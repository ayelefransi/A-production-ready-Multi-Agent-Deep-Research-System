"""
Editor / QA Gate node - final automated quality check before returning the report.

Validates the Pydantic output schema, checks citation coverage,
word count, and section completeness.  If it fails, the graph
retries the Writer once; if it still fails, the report is returned
with ``quality_warnings``.
"""

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

from schemas.editor import QACheckResult
from utils.errors import is_retryable
from utils.llm_router import get_llm
from utils.logger import logger
from utils.report_quality import deterministic_warnings, measure_report


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception(is_retryable),
    reraise=True,
)
async def editor_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    QA gate: validate the final report for completeness and quality.
    """
    start = time.time()
    final_report = state.get("final_report", {})

    logger.info("editor_started")

    llm = get_llm("editor", structured_output=QACheckResult)

    # Truncate large payloads to stay within model limits
    def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
        dumped = json.dumps(obj, indent=2)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "\n... [truncated for brevity]"
        return dumped

    prompt = f"""
    You are an expert Editor and Quality Assurance Agent. Your task is to
    evaluate the final research report for completeness and quality.

    REPORT TO EVALUATE:
    {_truncate_json(final_report, 16000)}

    CHECK THE FOLLOWING:

    1. **Citation Coverage** (0.0-1.0):
       - What fraction of major claims in the summary and key_findings have inline citations [N]?
       - Score 1.0 means every claim is cited; 0.5 means half are uncited.

    2. **Word Count**:
       - Approximate word count of the summary section.
       - A good research report summary should be 200-800 words.

    3. **Section Completeness**:
       - Check each section exists and is non-empty: executive_summary, methodology,
         summary, key_findings, risks, sources
       - Return a dict mapping section name -> bool (true = present and non-empty)

    4. **Schema Valid**:
       - Does the report have all required fields?
       - Are sources properly structured with index, title, url?

    5. **Overall Pass/Fail**:
       - Set passed=true if: citation_coverage >= 0.6 AND word_count >= 100
         AND all required sections are present AND schema is valid.
       - Otherwise, set passed=false.

    6. **Quality Warnings**:
       - List any specific warnings (e.g., "Low citation coverage in key_findings",
         "Summary is too short", "Missing risks section").

    7. **Improvement Suggestions**:
       - If passed=false, provide specific suggestions for improvement.
    """

    try:
        result: QACheckResult = await llm.ainvoke(prompt)

        # The model judges prose quality; counts and completeness are measured.
        measured = measure_report(final_report)
        hard_failures = deterministic_warnings(measured)
        result.word_count = measured["word_count"]
        result.section_completeness = measured["section_completeness"]
        result.citation_coverage = min(result.citation_coverage, measured["citation_coverage"])
        if hard_failures:
            result.passed = False
            result.quality_warnings = hard_failures + [
                w for w in result.quality_warnings if w not in hard_failures
            ]

        duration = time.time() - start

        timings = dict(state.get("agent_timings", {}))
        timings["editor"] = round(duration, 2)

        logger.info(
            "editor_success",
            passed=result.passed,
            citation_coverage=result.citation_coverage,
            word_count=result.word_count,
            warnings=len(result.quality_warnings),
            duration_seconds=round(duration, 2),
        )

        return {
            "editor_result": result.model_dump(),
            "agent_timings": timings,
            "messages": [
                f"Editor QA: {'passed' if result.passed else 'failed'} "
                f"(citations: {result.citation_coverage:.0%}, words: {result.word_count})."
            ],
        }
    except Exception as e:
        logger.error("editor_failed", error=str(e))
        raise
