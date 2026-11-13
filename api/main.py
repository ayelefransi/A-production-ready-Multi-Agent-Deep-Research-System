"""
HTTP API for the Multi-Agent Deep Research System.

Routes stay thin: request validation, rate limiting and response shaping live
here; the SSE event contract lives in ``utils/sse.py`` and all research logic
lives in the LangGraph nodes.
"""

import os
import sys
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Literal

# Add the project root to the Python path to fix Vercel import errors
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402

from utils.sse import sse_event, stream_research_events  # noqa: E402
from config.settings import settings  # noqa: E402
from graph.build_graph import app_graph  # noqa: E402
from utils.logger import logger  # noqa: E402
from utils.storage import research_store  # noqa: E402
from utils.tracing import setup_tracing  # noqa: E402

setup_tracing()

app = FastAPI(
    title="Multi-Agent Deep Research API",
    description="Multi-agent research pipeline with SSE streaming, a critic-driven "
                "replan loop, an editor QA gate and in-memory research history.",
    version=settings.api_version,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# The UI ships no third-party scripts, so scripts are locked to this origin.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


# --- Request models ---------------------------------------------------------

class ResearchRequest(BaseModel):
    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("Query must be at least 3 characters long.")
        if len(value) > settings.max_query_length:
            raise ValueError(
                f"Query must be at most {settings.max_query_length} characters long."
            )
        return value


class ResumeRequest(BaseModel):
    thread_id: uuid.UUID
    action: Literal["approve", "reject"] = "approve"


# --- Rate limiting ----------------------------------------------------------

_RATE_WINDOW_SECONDS = 60.0
_request_log: Dict[str, Deque[float]] = defaultdict(deque)


def enforce_rate_limit(request: Request) -> None:
    """
    Sliding-window limit on research starts per client IP.

    State is per process: adequate for a single instance, not a substitute
    for an edge/gateway limiter when running several replicas.
    """
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _request_log[client]
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - window[0])))
        raise HTTPException(
            status_code=429,
            detail="Too many research requests. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)


def _initial_state(query: str) -> Dict[str, Any]:
    return {
        "query": query,
        "messages": [],
        "iteration_count": 1,
        "replan_count": 0,
        "agent_timings": {},
        "scratchpad": {},
    }


def _internal_error(thread_id: str) -> HTTPException:
    """Client-safe 500: details go to the log, not the response."""
    return HTTPException(
        status_code=500,
        detail=f"The research run failed. Reference: {thread_id[:8]}",
    )


def _session_or_404(thread_id: uuid.UUID) -> dict:
    session = research_store.get(str(thread_id))
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found.")
    return session


# --- Frontend ---------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def home(request: Request):
    response = templates.TemplateResponse(request=request, name="index.html")
    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    return response


# --- Research: blocking -----------------------------------------------------

@app.post("/research")
async def start_research(body: ResearchRequest, request: Request):
    """Run the full pipeline and return the report (or a pending approval)."""
    enforce_rate_limit(request)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    logger.info("starting_research", query=body.query, thread_id=thread_id)
    research_store.save(thread_id, body.query, "running")

    try:
        state = await app_graph.ainvoke(_initial_state(body.query), config)
        current_state = app_graph.get_state(config)
    except Exception:
        logger.exception("research_workflow_failed", thread_id=thread_id)
        research_store.save(thread_id, body.query, "failed")
        raise _internal_error(thread_id)

    if current_state.next:
        plan = current_state.values.get("plan") or {}
        research_store.save(thread_id, body.query, "paused_for_approval", preview=plan)
        return {
            "status": "paused_for_approval",
            "thread_id": thread_id,
            "preview": plan,
            "message": f"Human approval required before node: {current_state.next[0]}. "
                       "Call POST /research/resume.",
        }

    report = state.get("final_report")
    research_store.save(thread_id, body.query, "completed", report=report)
    return {"status": "completed", "thread_id": thread_id, "report": report}


# --- Research: SSE stream ---------------------------------------------------

@app.post("/research/stream")
async def start_research_stream(body: ResearchRequest, request: Request):
    """
    Run the pipeline and stream progress as Server-Sent Events.

    Events: research_start, agent_start, agent_complete, planner_preview,
    researcher_task_complete, researcher_preview, analyst_preview,
    critic_preview, replan, writer_preview, editor_preview, writer_retry,
    human_approval_required, complete, error.
    """
    if not settings.enable_streaming:
        raise HTTPException(status_code=400, detail="Streaming is disabled in settings.")
    enforce_rate_limit(request)

    thread_id = str(uuid.uuid4())
    logger.info("starting_research_stream", query=body.query, thread_id=thread_id)
    research_store.save(thread_id, body.query, "running")

    return StreamingResponse(
        stream_research_events(thread_id, body.query, _initial_state(body.query)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Human-in-the-loop resume -----------------------------------------------

@app.post("/research/resume")
async def resume_research(body: ResumeRequest):
    thread_id = str(body.thread_id)
    config = {"configurable": {"thread_id": thread_id}}
    current_state = app_graph.get_state(config)

    if not current_state.next:
        raise HTTPException(status_code=400, detail="No pending action for this thread ID.")

    query = current_state.values.get("query", "")

    if body.action == "reject":
        research_store.save(thread_id, query, "rejected")
        return {"status": "rejected", "thread_id": thread_id,
                "message": "Research workflow canceled by human."}

    try:
        state = await app_graph.ainvoke(None, config)
        pending = app_graph.get_state(config).next
    except Exception:
        logger.exception("resume_workflow_failed", thread_id=thread_id)
        research_store.save(thread_id, query, "failed")
        raise _internal_error(thread_id)

    if pending:
        research_store.save(thread_id, query, "paused_for_approval")
        return {"status": "paused_for_approval", "thread_id": thread_id,
                "message": f"Human approval required before node: {pending[0]}."}

    report = state.get("final_report")
    research_store.save(thread_id, query, "completed", report=report)
    return {"status": "completed", "thread_id": thread_id, "report": report}


# --- History ----------------------------------------------------------------

@app.get("/research/history")
async def get_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List past research sessions (summaries only, newest first)."""
    sessions = research_store.list_all(limit=limit, offset=offset)
    summaries = [
        {
            "thread_id": s["thread_id"],
            "query": s["query"],
            "status": s["status"],
            "created_at": s["created_at"],
            "has_report": s.get("report") is not None,
        }
        for s in sessions
    ]
    return {"sessions": summaries, "total": research_store.count()}


@app.get("/research/{thread_id}")
async def get_research(thread_id: uuid.UUID):
    """Get a specific research session with its full report."""
    return _session_or_404(thread_id)


@app.delete("/research/{thread_id}")
async def delete_research(thread_id: uuid.UUID):
    """Delete a research session."""
    if research_store.delete(str(thread_id)):
        return {"status": "deleted", "thread_id": str(thread_id)}
    raise HTTPException(status_code=404, detail="Research session not found.")


# --- Export -----------------------------------------------------------------

@app.get("/research/{thread_id}/export/markdown")
async def export_markdown(thread_id: uuid.UUID):
    """Export a research report as a Markdown file."""
    session = research_store.get(str(thread_id))
    if not session or not session.get("report"):
        raise HTTPException(status_code=404, detail="No completed report found for this thread.")

    return Response(
        content=_report_to_markdown(session["report"]),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="research_report_{str(thread_id)[:8]}.md"'
        },
    )


def _report_to_markdown(report: dict) -> str:
    """Convert a report dict to a formatted Markdown string."""
    lines = [f"# {report.get('title', 'Research Report')}\n"]

    if report.get("executive_summary"):
        lines.append(f"> **Executive Summary:** {report['executive_summary']}\n")
    if report.get("methodology"):
        lines.append(f"## Methodology\n{report['methodology']}\n")
    if report.get("summary"):
        lines.append(f"## Summary\n{report['summary']}\n")

    for key, heading in (
        ("key_findings", "Key Findings"),
        ("risks", "Risks & Concerns"),
        ("contradictions", "Contradictions"),
        ("knowledge_gaps", "Areas for Further Research"),
    ):
        if report.get(key):
            lines.append(f"## {heading}")
            lines.extend(f"- {item}" for item in report[key])
            lines.append("")

    if report.get("sources"):
        lines.append("## Sources")
        for src in report["sources"]:
            if isinstance(src, dict):
                cred = src.get("credibility") or 0
                lines.append(
                    f"{src.get('index', '')}. [{src.get('title', 'Unknown')}]"
                    f"({src.get('url', '')}) - Credibility: {cred:.0%}"
                )
            else:
                lines.append(f"- {src}")
        lines.append("")

    if report.get("quality_warnings"):
        lines.append("## QA Warnings")
        lines.extend(f"- {w}" for w in report["quality_warnings"])
        lines.append("")

    meta = report.get("metadata")
    if meta:
        lines.append("---")
        lines.append(
            "*Generated by Multi-Agent Deep Research System | "
            f"Iterations: {meta.get('iterations_taken', 1)} | "
            f"Replans: {meta.get('replan_count', 0)} | "
            f"Sources Scanned: {meta.get('total_sources_scanned', 0)} | "
            f"Total Searches: {meta.get('total_searches', 0)}*"
        )

    return "\n".join(lines)


# --- Health -----------------------------------------------------------------

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": settings.api_version,
        "sessions_stored": research_store.count(),
        "config": {
            "model": settings.model_name,
            "max_replan_iterations": settings.max_replan_iterations,
            "critic_enabled": settings.enable_critic,
            "editor_enabled": settings.enable_editor,
            "human_approval": settings.require_human_approval,
            "streaming_enabled": settings.enable_streaming,
            "mock_mode": settings.mock_mode,
            "max_query_length": settings.max_query_length,
        },
    }


__all__ = ["app", "sse_event"]
