"""Writer node - evidence-grounded report with verified inline citations."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from schemas.report import ReportMetadata, ResearchReport
from utils.errors import NoEvidenceError, is_retryable
from utils.llm_router import get_llm
from utils.logger import logger
from utils.report_quality import evidence_digest, ground_report, rank_sources


def _truncate_json(obj: Any, max_chars: int) -> str:
    dumped = json.dumps(obj, indent=2)
    if len(dumped) > max_chars:
        return dumped[:max_chars] + "\n... [truncated for brevity]"
    return dumped


def _editor_feedback(state: Dict[str, Any]) -> str:
    """On the retry pass, tell the Writer exactly what the Editor rejected."""
    if not state.get("scratchpad", {}).get("writer_retried"):
        return ""
    qa = state.get("editor_result") or {}
    issues = list(qa.get("quality_warnings", [])) + list(qa.get("improvement_suggestions", []))
    if not issues:
        return ""
    bullet_list = "\n".join(f"    - {issue}" for issue in issues)
    return f"""
    REVISION REQUIRED. The Editor rejected your previous draft. Fix every point:
{bullet_list}
    """


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception(is_retryable),
    reraise=True,
)
async def writer_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce the research report, then ground it: citations that do not map to
    a retrieved source are removed and the source list is rebuilt from the
    Researchers' evidence, so the report cannot reference an invented source.
    """
    start = time.time()
    query = state.get("query", "")
    researcher_output = state.get("researcher_output") or {}
    analyst_output = state.get("analyst_output") or {}
    plan = state.get("plan") or {}
    verification_report = state.get("verification_report") or {}
    iteration_count = state.get("iteration_count", 1)
    replan_count = state.get("replan_count", 0)

    logger.info("writer_started")

    llm = get_llm("writer", structured_output=ResearchReport)

    sources = rank_sources(researcher_output.get("sources", []))
    if not sources:
        # A report with nothing to cite would be the model's memory dressed up
        # as research. Fail honestly instead.
        raise NoEvidenceError(
            "No sources could be retrieved for this question, so no evidence-based "
            "report was written. Check the search configuration (a TAVILY_API_KEY "
            "makes search reliable) or rephrase the question."
        )
    total_searches = researcher_output.get("total_searches_performed", 1)

    prompt = f"""
    You are a senior research analyst writing for expert readers (domain specialists,
    policy makers, investors). Write the report a subject-matter expert would sign.

    RESEARCH QUESTION: {query}

    RESEARCH PLAN:
    {_truncate_json(plan, 1500)}

    ANALYST SYNTHESIS (insights, evidence strength, contradictions, gaps):
    {_truncate_json(analyst_output, 6000)}

    CRITIC VERIFICATION (per-claim verdicts; do not assert claims marked
    unsupported or contradicted):
    {_truncate_json(verification_report, 3000)}

    EVIDENCE. These are the ONLY sources you may cite. Cite with the bracketed
    number shown, e.g. [3]:
    {evidence_digest(sources)}
    {_editor_feedback(state)}
    STANDARDS (non-negotiable):
    - Every factual claim carries at least one citation [N]. Never cite a number that
      is not in the EVIDENCE list. Never invent a source, statistic, date or quote.
    - Be specific: name the organisations, figures, dates and mechanisms the evidence
      gives. If the evidence gives no number, do not supply one.
    - Calibrate language to evidence strength: state strong findings plainly; mark
      moderate ones ("the evidence suggests"); flag weak or single-source ones.
    - Where sources disagree, present both positions and say which is better supported.
    - Analyse, do not list: explain causes, trade-offs and implications.
    - No filler, no marketing tone, no restating the question, no first person.

    FIELDS:
    1. title: precise and descriptive, not clickbait.
    2. executive_summary: 3-4 sentences giving the answer, the strongest evidence
       for it, and the main caveat. Must stand alone.
    3. methodology: how the research was done: a multi-agent pipeline (planner,
       parallel researchers, analyst, critic, writer, editor), {total_searches} searches,
       {len(sources)} sources reviewed, {iteration_count} iteration(s), {replan_count}
       replanning cycle(s); note how sources were weighted by credibility.
    4. summary: the main analysis, 5-8 paragraphs (450-900 words) in markdown, organised
       under 3-5 "### " thematic headings that follow the research plan. Cited throughout.
    5. key_findings: 5-8 findings, one specific, cited sentence each, most important first.
    6. risks: concrete risks or downsides, each cited where evidence exists.
    7. contradictions: where sources disagree and how the disagreement was weighed.
    8. knowledge_gaps: what the evidence could not answer.
    9. recommendations: 3-5 actionable recommendations that follow from the findings.
    10. confidence_level: "high", "moderate" or "low" for the report as a whole.
    11. confidence_rationale: one or two sentences justifying that level (source quality,
        agreement between sources, coverage gaps).
    12. sources: the cited sources (index, title, url, credibility) from EVIDENCE.
    """

    try:
        result: ResearchReport = await llm.ainvoke(prompt)

        agent_timings = state.get("agent_timings", {})
        result.metadata = ReportMetadata(
            total_sources_scanned=researcher_output.get("total_raw_results", len(sources)),
            total_searches=total_searches,
            iterations_taken=iteration_count,
            replan_count=replan_count,
            agent_timings=agent_timings,
            total_duration_seconds=sum(
                v for v in agent_timings.values() if isinstance(v, (int, float))
            ),
        )

        report = ground_report(result.model_dump(), sources)

        duration = time.time() - start
        timings = dict(agent_timings)
        timings["writer"] = round(duration, 2)

        logger.info(
            "writer_success",
            title=result.title,
            cited_sources=len(report["sources"]),
            duration_seconds=round(duration, 2),
        )

        return {
            "final_report": report,
            "agent_timings": timings,
            "messages": [f"Writer generated final report: '{result.title}'."],
        }
    except Exception as e:
        logger.error("writer_failed", error=str(e))
        raise
