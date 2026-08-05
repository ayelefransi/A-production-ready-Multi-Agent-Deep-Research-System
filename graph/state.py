"""
Graph state definition for the supervisor-orchestrated research pipeline.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict


class ResearchState(TypedDict):
    """
    Shared state flowing through the LangGraph research pipeline.

    Key additions over the old state:
    - ``sub_question_results``: per-sub-question research outputs (parallel fan-out)
    - ``verification_report``: Critic/Verifier structured output
    - ``editor_result``: Editor/QA gate result
    - ``replan_count``: how many times we looped back to the Planner
    - ``scratchpad``: orchestrator-level in-run memory
    """

    # ── Core ──────────────────────────────────────────────────────────────
    query: str
    plan: Optional[Dict[str, Any]]

    # Parallel researcher outputs (one dict per sub-question)
    sub_question_results: Annotated[List[Dict[str, Any]], operator.add]

    # Aggregated/merged researcher output (produced by the collector)
    researcher_output: Optional[Dict[str, Any]]

    # Agent outputs
    verification_report: Optional[Dict[str, Any]]
    analyst_output: Optional[Dict[str, Any]]
    final_report: Optional[Dict[str, Any]]
    editor_result: Optional[Dict[str, Any]]

    # ── Iteration tracking ────────────────────────────────────────────────
    iteration_count: int
    replan_count: int

    # ── Observability ─────────────────────────────────────────────────────
    agent_timings: Dict[str, float]
    messages: Annotated[List[str], operator.add]

    # ── In-run scratchpad ─────────────────────────────────────────────────
    scratchpad: Dict[str, Any]
