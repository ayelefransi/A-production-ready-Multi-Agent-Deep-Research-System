"""
Planner node - decomposes user query into 3-7 sub-questions.

Supports replanning: when the Critic flags gaps, the Planner receives
the gap description and adjusts sub-questions accordingly.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential

from schemas.planner import ResearchPlan
from utils.llm_router import get_llm
from utils.logger import logger


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=60))
async def planner_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decomposes a complex user query into 3-7 targeted sub-questions
    with explicit research goals.  On a replan, incorporates gap feedback.
    """
    start = time.time()
    query = state.get("query", "")
    replan_count = state.get("replan_count", 0)
    verification_report = state.get("verification_report")

    logger.info("planner_started", query=query, replan_count=replan_count)

    llm = get_llm("planner", structured_output=ResearchPlan)

    # Build replan context if this is a replanning pass
    replan_context = ""
    if replan_count > 0 and verification_report:
        gaps = verification_report.get("gaps", [])
        guidance = verification_report.get("replan_guidance", "")
        replan_context = f"""
    REPLANNING CONTEXT (iteration {replan_count + 1}):
    The Critic/Verifier found these gaps in the previous research:
    - Gaps: {gaps}
    - Guidance: {guidance}

    You MUST adjust your sub-questions to specifically address these gaps.
    Keep any sub-questions from the previous plan that were adequately covered,
    but add or replace sub-questions to fill the identified gaps.
    """

    prompt = f"""
    You are an expert Research Planner Agent. Your task is to decompose a complex user query
    into 3-7 specific, targeted sub-questions that together would comprehensively answer the
    original query. Each sub-question should explore a different angle or dimension of the topic.

    For each sub-question, provide:
    - question: The specific sub-question to research
    - research_goal: What this sub-question aims to discover or validate
    - priority: 1 (highest) to 5 (lowest)

    Also define:
    - A research strategy describing how to approach this topic
    - The expected domain areas that should be covered

    Guidelines:
    - Sub-questions should be specific enough to yield focused search results
    - Cover different aspects: causes, effects, current state, future outlook, key players
    - Avoid overlapping sub-questions
    - Each sub-question should be searchable on the web
    - Prioritize by importance to the original query
    {replan_context}

    User Query: {query}
    """

    try:
        result: ResearchPlan = await llm.ainvoke(prompt)
        duration = time.time() - start

        logger.info(
            "planner_success",
            sub_questions=len(result.sub_questions),
            domains=result.expected_domains,
            replan_count=replan_count,
            duration_seconds=round(duration, 2),
        )

        timings = dict(state.get("agent_timings", {}))
        timing_key = f"planner_replan{replan_count}" if replan_count > 0 else "planner"
        timings[timing_key] = round(duration, 2)

        return {
            "plan": result.model_dump(),
            "agent_timings": timings,
            "messages": [
                f"Planner decomposed query into {len(result.sub_questions)} sub-questions"
                f"{f' (replan #{replan_count})' if replan_count > 0 else ''}."
            ],
        }
    except Exception as e:
        logger.error("planner_failed", error=str(e))
        raise
