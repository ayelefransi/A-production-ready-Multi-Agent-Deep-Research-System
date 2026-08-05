"""Analyst node - synthesis, cross-source reasoning, contradiction resolution."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential

from schemas.analyst import AnalystOutput
from utils.llm_router import get_llm
from utils.logger import logger


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=60))
async def analyst_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Perform goal-aware analysis: cross-reference sources, rate evidence
    strength, and identify knowledge gaps.
    """
    start = time.time()
    researcher_output = state.get("researcher_output", {})
    plan = state.get("plan", {})
    iteration_count = state.get("iteration_count", 1)

    logger.info("analyst_started", iteration=iteration_count)

    llm = get_llm("analyst", structured_output=AnalystOutput)

    # Truncate large payloads to stay within model limits
    def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
        dumped = json.dumps(obj, indent=2)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "\n... [truncated for brevity]"
        return dumped

    prompt = f"""
    You are an expert Analyst Agent. Your task is to perform a rigorous, goal-aware analysis
    of the research output.

    ORIGINAL RESEARCH PLAN:
    - Strategy: {plan.get('research_strategy', 'General research')}
    - Sub-questions to answer: {_truncate_json(plan.get('sub_questions', []), 1000)}
    - Expected domains: {_truncate_json(plan.get('expected_domains', []), 500)}

    RESEARCHER OUTPUT:
    {_truncate_json(researcher_output, 6000)}

    YOUR ANALYSIS MUST:
    1. **Synthesize insights**: Extract overarching insights that connect information across
       multiple sources. Don't just summarize individual sources.

    2. **Rate evidence strength**: For each insight, classify as:
       - "strong": Supported by 3+ credible sources with consistent findings
       - "moderate": Supported by 1-2 credible sources or partially corroborated
       - "weak": Based on single or low-credibility sources, or speculative

    3. **List supporting sources**: For each insight, include the URLs of sources that support it.

    4. **Cross-reference sources**: Explicitly compare what different sources say about the
       same sub-topic. Identify agreements and disagreements.

    5. **Identify contradictions**: Note where sources directly contradict each other.

    6. **Find knowledge gaps**: Identify which sub-questions from the plan are NOT adequately
       answered by the current sources. This helps the Critic decide if replanning is needed.

    7. **Assess risks**: Identify potential risks, downsides, or concerns related to the topic.

    8. **Confidence score**: Rate your overall confidence (0.0-1.0) considering source quality,
       coverage completeness, and consistency.

    This is iteration {iteration_count}. Be thorough but pragmatic.
    """

    try:
        result: AnalystOutput = await llm.ainvoke(prompt)
        duration = time.time() - start

        timings = dict(state.get("agent_timings", {}))
        timings[f"analyst_iter{iteration_count}"] = round(duration, 2)

        logger.info(
            "analyst_success",
            confidence=result.confidence_score,
            insights=len(result.key_insights),
            gaps=len(result.knowledge_gaps),
            iteration=iteration_count,
            duration_seconds=round(duration, 2),
        )

        return {
            "analyst_output": result.model_dump(),
            "agent_timings": timings,
            "messages": [
                f"Analyst generated {len(result.key_insights)} insights "
                f"(confidence: {result.confidence_score:.0%}, iteration {iteration_count})."
            ],
        }
    except Exception as e:
        logger.error("analyst_failed", error=str(e))
        raise
