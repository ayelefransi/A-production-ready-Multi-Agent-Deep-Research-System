"""Writer node - structured report with inline citations."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential

from schemas.report import ReportMetadata, ResearchReport
from utils.llm_router import get_llm
from utils.logger import logger


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=60))
async def writer_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce a comprehensive, publication-quality research report with
    executive summary, methodology, inline citations, and structured sections.
    """
    start = time.time()
    query = state.get("query", "")
    researcher_output = state.get("researcher_output", {})
    analyst_output = state.get("analyst_output", {})
    plan = state.get("plan", {})
    verification_report = state.get("verification_report", {})
    iteration_count = state.get("iteration_count", 1)
    replan_count = state.get("replan_count", 0)

    logger.info("writer_started")

    llm = get_llm("writer", structured_output=ResearchReport)

    # Build numbered source reference
    sources = researcher_output.get("sources", [])
    source_reference = ""
    for i, src in enumerate(sources, 1):
        source_reference += f"[{i}] {src.get('title', 'Unknown')} - {src.get('url', '')}\n"

    # Truncate large payloads to stay within model context limits
    def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
        dumped = json.dumps(obj, indent=2)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "\n... [truncated for brevity]"
        return dumped

    prompt = f"""
    You are an expert Writer Agent. Your task is to produce a comprehensive, publication-quality
    research report from the analysis and research data provided.

    ORIGINAL QUERY: {query}

    RESEARCH PLAN:
    {_truncate_json(plan, 1000)}

    ANALYSIS:
    {_truncate_json(analyst_output, 4000)}

    SOURCE DATA (summarized):
    {_truncate_json(researcher_output, 4000)}

    VERIFICATION REPORT:
    {_truncate_json(verification_report, 1000)}

    SOURCE REFERENCE (use these citation numbers):
    {source_reference}

    REPORT REQUIREMENTS:

    1. **title**: A compelling, descriptive title for the report.

    2. **executive_summary**: A concise 2-3 sentence executive summary that captures the
       most important takeaway. Should stand alone and be useful for busy readers.

    3. **methodology**: Describe how this research was conducted. Mention:
       - The multi-agent research pipeline used
       - Number of searches performed ({researcher_output.get('total_searches_performed', 1)})
       - Number of sources analyzed ({len(sources)})
       - Number of research iterations ({iteration_count})
       - Number of replanning cycles ({replan_count})

    4. **summary**: A comprehensive, detailed summary (4-8 paragraphs) with inline citations
       using [1], [2], etc. corresponding to the source reference numbers above.
       Use rich markdown formatting (bold, headers, etc.) within the text.

    5. **key_findings**: 5-8 major findings, each with inline citations [N].

    6. **risks**: Identified risks and concerns about the topic.

    7. **contradictions**: Areas where sources disagree.

    8. **knowledge_gaps**: Areas that need further research.

    9. **sources**: A structured list of all cited sources with their index, title, URL,
       and credibility score. Include ALL sources from the source reference.

    IMPORTANT:
    - Write at a professional level suitable for publication.
    - Ensure every claim is backed by at least one citation.
    - Use varied sentence structure and avoid repetition.
    - The report should be informative, balanced, and actionable.
    """

    try:
        result: ResearchReport = await llm.ainvoke(prompt)

        # Attach metadata
        agent_timings = state.get("agent_timings", {})
        result.metadata = ReportMetadata(
            total_sources_scanned=researcher_output.get("total_raw_results", len(sources)),
            total_searches=researcher_output.get("total_searches_performed", 1),
            iterations_taken=iteration_count,
            replan_count=replan_count,
            agent_timings=agent_timings,
            total_duration_seconds=sum(
                v for v in agent_timings.values() if isinstance(v, (int, float))
            ),
        )

        duration = time.time() - start
        timings = dict(agent_timings)
        timings["writer"] = round(duration, 2)

        logger.info(
            "writer_success",
            title=result.title,
            duration_seconds=round(duration, 2),
        )

        return {
            "final_report": result.model_dump(),
            "agent_timings": timings,
            "messages": [f"Writer generated final report: '{result.title}'."],
        }
    except Exception as e:
        logger.error("writer_failed", error=str(e))
        raise
