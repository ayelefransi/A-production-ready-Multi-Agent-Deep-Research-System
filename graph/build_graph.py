"""
Supervisor-orchestrated research graph with parallel fan-out, replanning,
and Editor/QA gate.

Architecture:
    START → Planner → [HIL] → parallel Researchers (Send API) → Collector
    → Analyst → Critic/Verifier ─┬→ Writer → Editor ─┬→ END
                                 │                    │
                                 └── replan → Planner └── retry Writer (once)
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from config.settings import settings
from graph.nodes.analyst import analyst_node
from graph.nodes.collector import collector_node
from graph.nodes.critic import critic_node
from graph.nodes.editor import editor_node
from graph.nodes.planner import planner_node
from graph.nodes.researcher import researcher_node
from graph.nodes.writer import writer_node
from graph.state import ResearchState
from utils.logger import logger


# ---------------------------------------------------------------------------
# Helper nodes (iteration / replan bookkeeping)
# ---------------------------------------------------------------------------

async def increment_iteration(state: Dict[str, Any]) -> Dict[str, Any]:
    """Increment the iteration counter on a Critic→Researcher loop."""
    new_count = state.get("iteration_count", 1) + 1
    logger.info("iteration_loop", new_iteration=new_count)
    return {
        "iteration_count": new_count,
        "messages": [f"Starting research iteration {new_count}."],
    }


async def increment_replan(state: Dict[str, Any]) -> Dict[str, Any]:
    """Increment the replan counter when looping back to Planner."""
    new_count = state.get("replan_count", 0) + 1
    logger.info("replan_loop", replan_count=new_count)
    return {
        "replan_count": new_count,
        "sub_question_results": [],  # Clear old results for fresh research
        "messages": [f"Replanning (attempt #{new_count})."],
    }


async def attach_warnings(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attach Editor quality warnings to the final report before returning.
    """
    final_report = dict(state.get("final_report", {}))
    editor_result = state.get("editor_result", {})
    warnings = editor_result.get("quality_warnings", [])
    if warnings:
        final_report["quality_warnings"] = warnings
    return {
        "final_report": final_report,
        "messages": [
            f"Attached {len(warnings)} quality warnings to report."
            if warnings
            else "Report passed QA — no warnings."
        ],
    }


# ---------------------------------------------------------------------------
# Conditional edge: Planner → dynamic fan-out of Researcher nodes
# ---------------------------------------------------------------------------

def fan_out_researchers(state: Dict[str, Any]) -> List[Send]:
    """
    Use LangGraph's Send API to spawn one Researcher per sub-question.
    Each researcher gets a scoped state slice.
    """
    plan = state.get("plan", {})
    sub_questions = plan.get("sub_questions", [])
    query = state.get("query", "")

    sends = []
    for sq in sub_questions:
        # Support both dict (from Pydantic model_dump) and plain string
        if isinstance(sq, dict):
            question = sq.get("question", "")
            goal = sq.get("research_goal", "")
        else:
            question = str(sq)
            goal = ""

        sends.append(
            Send(
                "researcher",
                {
                    "query": query,
                    "sub_question": question,
                    "research_goal": goal,
                },
            )
        )

    logger.info("fan_out_researchers", count=len(sends))
    return sends


# ---------------------------------------------------------------------------
# Conditional edge: Critic → replan or proceed to Writer
# ---------------------------------------------------------------------------

def should_replan(state: Dict[str, Any]) -> Literal["replan", "writer"]:
    """
    After the Critic runs, decide whether to loop back to Planner.
    """
    if not settings.enable_critic:
        return "writer"

    verification = state.get("verification_report", {})
    if verification.get("should_replan", False):
        replan_count = state.get("replan_count", 0)
        if replan_count < settings.max_replan_iterations:
            logger.info("critic_requesting_replan", replan_count=replan_count)
            return "replan"

    return "writer"


# ---------------------------------------------------------------------------
# Conditional edge: Editor → END or retry Writer (once)
# ---------------------------------------------------------------------------

def should_retry_writer(state: Dict[str, Any]) -> Literal["retry_writer", "finalize"]:
    """
    If the Editor QA fails and we haven't retried yet, retry the Writer.
    """
    if not settings.enable_editor:
        return "finalize"

    editor_result = state.get("editor_result", {})
    scratchpad = state.get("scratchpad", {})

    if not editor_result.get("passed", True):
        if not scratchpad.get("writer_retried", False):
            logger.info("editor_requesting_writer_retry")
            return "retry_writer"

    return "finalize"


async def mark_writer_retried(state: Dict[str, Any]) -> Dict[str, Any]:
    """Mark that the Writer has been retried once."""
    scratchpad = dict(state.get("scratchpad", {}))
    scratchpad["writer_retried"] = True
    return {
        "scratchpad": scratchpad,
        "messages": ["Retrying Writer after Editor QA failure."],
    }


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    """
    Wire the supervisor-orchestrated research graph.

    Flow:
        START → planner → [HIL?] → researcher (parallel via Send)
        → collector → analyst → critic
        → [replan → planner OR writer]
        → writer → editor → [retry_writer → writer OR finalize → END]
    """
    builder = StateGraph(ResearchState)

    # ── Add nodes ─────────────────────────────────────────────────────────
    builder.add_node("planner", planner_node)
    builder.add_node("researcher", researcher_node)
    builder.add_node("collector", collector_node)
    builder.add_node("analyst", analyst_node)
    builder.add_node("critic", critic_node)
    builder.add_node("writer", writer_node)
    builder.add_node("editor", editor_node)
    builder.add_node("replan", increment_replan)
    builder.add_node("finalize", attach_warnings)
    builder.add_node("mark_retry", mark_writer_retried)

    # ── Edges ─────────────────────────────────────────────────────────────

    # START → Planner
    builder.add_edge(START, "planner")

    # Planner → dynamic fan-out of Researcher nodes
    builder.add_conditional_edges("planner", fan_out_researchers, ["researcher"])

    # Each Researcher → Collector (fan-in)
    builder.add_edge("researcher", "collector")

    # Collector → Analyst
    builder.add_edge("collector", "analyst")

    # Analyst → Critic
    builder.add_edge("analyst", "critic")

    # Critic → replan OR writer
    builder.add_conditional_edges(
        "critic",
        should_replan,
        {"replan": "replan", "writer": "writer"},
    )

    # Replan → Planner (loop back)
    builder.add_edge("replan", "planner")

    # Writer → Editor (if enabled) or finalize
    if settings.enable_editor:
        builder.add_edge("writer", "editor")

        # Editor → finalize or retry
        builder.add_conditional_edges(
            "editor",
            should_retry_writer,
            {"retry_writer": "mark_retry", "finalize": "finalize"},
        )

        # mark_retry → writer (second attempt)
        builder.add_edge("mark_retry", "writer")
    else:
        builder.add_edge("writer", "finalize")

    # Finalize → END
    builder.add_edge("finalize", END)

    # ── HIL checkpoints ───────────────────────────────────────────────────
    interrupt_before = []
    if settings.require_human_approval:
        hil = settings.hil_checkpoints
        if "after_planning" in hil:
            # Can't interrupt before fan-out easily; interrupt before collector
            # which is the first node after all researchers finish.
            interrupt_before.append("collector")
        if "after_critique" in hil:
            interrupt_before.append("writer")

    memory = MemorySaver()
    return builder.compile(checkpointer=memory, interrupt_before=interrupt_before)


# ---------------------------------------------------------------------------
# Module-level graph instance (backward compat with old workflow.py)
# ---------------------------------------------------------------------------

app_graph = build_graph()
