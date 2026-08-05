"""
Critic / Verifier node - cross-checks claims against source text.

This is the key agentic upgrade: it fact-checks the Analyst's claims
against the actual retrieved sources, flags unsupported or contradicted
claims, and decides whether the system should replan.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from schemas.verifier import VerificationReport
from utils.llm_router import get_llm
from utils.logger import logger


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=60))
async def critic_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cross-check Analyst claims against retrieved source text.
    Produces a structured VerificationReport with per-claim verdicts.
    """
    start = time.time()
    analyst_output = state.get("analyst_output", {})
    researcher_output = state.get("researcher_output", {})
    plan = state.get("plan", {})
    iteration_count = state.get("iteration_count", 1)
    replan_count = state.get("replan_count", 0)

    logger.info(
        "critic_started",
        iteration=iteration_count,
        replan_count=replan_count,
    )

    # Use a *different* model from the writer to reduce correlated errors
    llm = get_llm("critic", structured_output=VerificationReport)

    # Truncate large payloads to stay within model limits
    def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
        dumped = json.dumps(obj, indent=2)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "\n... [truncated for brevity]"
        return dumped

    prompt = f"""
    You are an expert Research Critic and Verification Agent. Your task is to
    rigorously verify the claims made in the analysis by cross-checking them
    against the actual source material.

    ORIGINAL PLAN:
    {_truncate_json(plan, 1000)}

    RESEARCHER OUTPUT (source evidence):
    {_truncate_json(researcher_output, 5000)}

    ANALYST OUTPUT (claims to verify):
    {_truncate_json(analyst_output, 4000)}

    CURRENT STATE: Iteration {iteration_count}, Replan #{replan_count}
    Max replans allowed: {settings.max_replan_iterations}

    FOR EACH CLAIM in the analyst's key_insights and summary:
    1. Check if the claim is directly supported by the source evidence
    2. Identify which sources support it (list URLs)
    3. Identify any sources that contradict it (list URLs)
    4. Assign a confidence score (0.0-1.0)
    5. Give a verdict: "supported", "unsupported", "contradicted", or "partially_supported"
    6. Briefly explain your reasoning

    THEN ASSESS OVERALL:
    - overall_source_adequacy: Are there enough quality sources? (0.0-1.0)
    - quality_score: Overall research quality (0.0-1.0)
    - gaps: What critical information is missing?
    - flags: Any warning signs (low diversity, unresolved contradictions, etc.)
    - should_replan: Set to true ONLY if:
      a) There are critical gaps that replanning could fix, AND
      b) We haven't exceeded max replans ({settings.max_replan_iterations}), AND
      c) quality_score < 0.7
    - If should_replan is true, provide specific replan_guidance and refined_queries

    Be pragmatic: minor gaps are acceptable. Only flag for replanning if
    significant coverage holes could materially be filled with better sub-questions.
    """

    try:
        result: VerificationReport = await llm.ainvoke(prompt)

        # Hard cap on replans
        if replan_count >= settings.max_replan_iterations:
            result.should_replan = False

        # Don't replan if quality is good enough
        if result.quality_score >= 0.7:
            result.should_replan = False

        duration = time.time() - start
        timings = dict(state.get("agent_timings", {}))
        timing_key = f"critic_iter{iteration_count}"
        timings[timing_key] = round(duration, 2)

        logger.info(
            "critic_success",
            quality_score=result.quality_score,
            source_adequacy=result.overall_source_adequacy,
            should_replan=result.should_replan,
            claims_verified=len(result.claims),
            gaps=len(result.gaps),
            duration_seconds=round(duration, 2),
        )

        return {
            "verification_report": result.model_dump(),
            "agent_timings": timings,
            "messages": [
                f"Critic verified {len(result.claims)} claims "
                f"(quality: {result.quality_score:.0%}, "
                f"adequacy: {result.overall_source_adequacy:.0%}). "
                f"{'Requesting replan.' if result.should_replan else 'Quality sufficient.'}"
            ],
        }
    except Exception as e:
        logger.error("critic_failed", error=str(e))
        raise
