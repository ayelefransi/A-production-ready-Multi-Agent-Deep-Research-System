"""
Collector node - fan-in aggregation of parallel researcher results.

Merges all ``SubQuestionResult``s into a single ``ResearcherOutput``,
deduplicating sources by URL.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from schemas.researcher import ResearcherOutput, SourceItem, SubQuestionResult
from utils.logger import logger


async def collector_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregate parallel sub-question results into a unified ResearcherOutput.
    """
    start = time.time()
    query = state.get("query", "")
    sub_results: List[Dict[str, Any]] = state.get("sub_question_results", [])

    logger.info("collector_started", sub_results_count=len(sub_results))

    # Flatten all sources, dedup by URL
    seen_urls: set[str] = set()
    all_sources: List[SourceItem] = []
    parsed_results: List[SubQuestionResult] = []
    total_searches = 0

    for sr_dict in sub_results:
        sr = SubQuestionResult(**sr_dict)
        parsed_results.append(sr)
        total_searches += len(sr.search_queries_tried)

        for source in sr.sources:
            if source.url not in seen_urls:
                seen_urls.add(source.url)
                all_sources.append(source)

    total_raw = sum(len(sr.sources) for sr in parsed_results)

    output = ResearcherOutput(
        query=query,
        sources=all_sources,
        sub_question_results=[sr.model_dump() for sr in parsed_results],
        total_searches_performed=total_searches,
        total_raw_results=total_raw,
    )

    duration = time.time() - start
    timings = dict(state.get("agent_timings", {}))
    timings["collector"] = round(duration, 2)

    logger.info(
        "collector_success",
        total_sources=len(all_sources),
        total_raw=total_raw,
        total_searches=total_searches,
        duration_seconds=round(duration, 2),
    )

    return {
        "researcher_output": output.model_dump(),
        "agent_timings": timings,
        "messages": [
            f"Collector aggregated {len(all_sources)} unique sources "
            f"from {len(parsed_results)} sub-question researchers."
        ],
    }
