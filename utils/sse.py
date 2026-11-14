"""
Server-Sent Events contract for a research run.

``GraphEventTranslator`` translates LangGraph's ``debug`` stream (one ``task`` event when a node starts,
one ``task_result`` when it finishes) into the UI-facing event vocabulary.
Because start and finish are separate graph events, every agent duration is a
real measurement, and each parallel Researcher is tracked by its own task id.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple


# Nodes that are agents (shown in the UI). Bookkeeping nodes are translated
# into loop events instead.
AGENT_NODES = {"planner", "researcher", "collector", "analyst", "critic", "writer", "editor"}

_MAX_PREVIEW_SOURCES = 12


def sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


def _as_dict(value: Any) -> Dict[str, Any]:
    """Task results arrive as a dict or as a list of (channel, value) writes."""
    if isinstance(value, dict):
        return value
    try:
        return dict(value or [])
    except (TypeError, ValueError):
        return {}


def _preview_events(node: str, task_id: str, task_input: Dict[str, Any],
                    result: Dict[str, Any]) -> list[tuple[str, dict]]:
    """Build the node-specific preview events for a finished task."""
    if node == "planner" and result.get("plan"):
        plan = result["plan"]
        return [("planner_preview", {
            "sub_questions": plan.get("sub_questions", []),
            "strategy": plan.get("research_strategy", ""),
            "domains": plan.get("expected_domains", []),
        })]

    if node == "researcher":
        sub_results = result.get("sub_question_results") or [{}]
        sources = sub_results[0].get("sources", [])
        return [("researcher_task_complete", {
            "task_id": task_id,
            "sub_question": task_input.get("sub_question", ""),
            "source_count": len(sources),
            "tools_used": sub_results[0].get("tools_used", []),
        })]

    if node == "collector" and result.get("researcher_output"):
        output = result["researcher_output"]
        sources = output.get("sources", [])
        return [("researcher_preview", {
            "source_count": len(sources),
            "total_searches": output.get("total_searches_performed", 0),
            "sources": [
                {
                    "title": s.get("title", ""),
                    "url": s.get("url", ""),
                    "credibility": s.get("credibility", 0),
                    "domain": s.get("domain", ""),
                }
                for s in sources[:_MAX_PREVIEW_SOURCES]
            ],
        })]

    if node == "analyst" and result.get("analyst_output"):
        analysis = result["analyst_output"]
        insights = analysis.get("key_insights", [])
        strengths: Dict[str, int] = {}
        for insight in insights:
            key = str(insight.get("evidence_strength", "unknown")).lower()
            strengths[key] = strengths.get(key, 0) + 1
        return [("analyst_preview", {
            "insight_count": len(insights),
            "confidence": analysis.get("confidence_score", 0),
            "evidence_strength": strengths,
            "contradictions": len(analysis.get("contradictions", [])),
            "knowledge_gaps": len(analysis.get("knowledge_gaps", [])),
        })]

    if node == "critic" and result.get("verification_report"):
        report = result["verification_report"]
        verdicts: Dict[str, int] = {}
        for claim in report.get("claims", []):
            verdict = claim.get("verdict", "unknown")
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
        return [("critic_preview", {
            "quality_score": report.get("quality_score", 0),
            "source_adequacy": report.get("overall_source_adequacy", 0),
            "should_replan": report.get("should_replan", False),
            "gaps": report.get("gaps", []),
            "flags": report.get("flags", []),
            "verdicts": verdicts,
            "replan_guidance": report.get("replan_guidance", ""),
        })]

    if node == "writer" and result.get("final_report"):
        report = result["final_report"]
        return [("writer_preview", {
            "title": report.get("title", ""),
            "findings": len(report.get("key_findings", [])),
            "sources": len(report.get("sources", [])),
        })]

    if node == "editor" and result.get("editor_result"):
        qa = result["editor_result"]
        return [("editor_preview", {
            "passed": qa.get("passed", False),
            "citation_coverage": qa.get("citation_coverage", 0),
            "word_count": qa.get("word_count", 0),
            "schema_valid": qa.get("schema_valid", True),
            "section_completeness": qa.get("section_completeness", {}),
            "warnings": qa.get("quality_warnings", []),
            "suggestions": qa.get("improvement_suggestions", []),
        })]

    return []


class GraphEventTranslator:
    """Turns raw LangGraph ``debug`` chunks into (event name, data) pairs."""

    def __init__(self) -> None:
        self._started: Dict[str, float] = {}
        self._inputs: Dict[str, Dict[str, Any]] = {}

    def translate(self, chunk: Dict[str, Any]) -> List[Tuple[str, dict]]:
        kind = chunk.get("type")
        payload = chunk.get("payload") or {}
        node: Optional[str] = payload.get("name")
        task_id: str = payload.get("id", "")

        if kind == "task" and node in AGENT_NODES:
            self._started[task_id] = time.monotonic()
            task_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
            self._inputs[task_id] = task_input
            event = {"agent": node, "task_id": task_id}
            if node == "researcher":
                event["sub_question"] = task_input.get("sub_question", "")
            return [("agent_start", event)]

        if kind != "task_result":
            return []

        result = _as_dict(payload.get("result"))
        if node in AGENT_NODES:
            started = self._started.pop(task_id, None)
            duration_ms = int((time.monotonic() - started) * 1000) if started else 0
            events = _preview_events(node, task_id, self._inputs.pop(task_id, {}), result)
            messages = result.get("messages") or []
            events.append(("agent_complete", {
                "agent": node,
                "task_id": task_id,
                "duration_ms": duration_ms,
                "message": messages[-1] if messages else "",
            }))
            return events
        if node == "replan":
            return [("replan", {"replan_count": result.get("replan_count", 1)})]
        if node == "mark_retry":
            return [("writer_retry", {
                "reason": "Editor QA gate failed; the Writer gets one more attempt.",
            })]
        return []
