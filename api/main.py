import os
import sys
import uuid
import time
import json

# Add the project root to the Python path to fix Vercel import errors
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from config.settings import settings
from graph.build_graph import app_graph
from utils.logger import logger
from utils.storage import research_store
from utils.tracing import setup_tracing

# Initialize tracing at startup
setup_tracing()

app = FastAPI(
    title="Multi-Agent Deep Research API",
    description="A production-ready multi-agent research system with SSE streaming, "
                "iterative self-healing, and research history.",
    version=settings.api_version
)

# Setup frontend static and templates
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# --- Request/Response Models ---

class ResearchRequest(BaseModel):
    query: str


class ResumeRequest(BaseModel):
    thread_id: str
    action: str = "approve"


# --- Frontend Route ---

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# --- Standard Research Endpoint (backward compatible) ---

@app.post("/research")
async def start_research(request: ResearchRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "query": request.query,
        "messages": [],
        "iteration_count": 1,
        "replan_count": 0,
        "agent_timings": {},
        "scratchpad": {},
    }
    logger.info("starting_research", query=request.query, thread_id=thread_id)

    # Save initial state
    research_store.save(thread_id, request.query, "running")

    try:
        state = await app_graph.ainvoke(initial_state, config)

        # Check if we hit an interrupt
        current_state = app_graph.get_state(config)
        if current_state.next:
            research_store.save(
                thread_id, request.query, "paused_for_approval",
                preview=current_state.values.get("plan", {})
            )
            return {
                "status": "paused_for_approval",
                "thread_id": thread_id,
                "preview": current_state.values.get("plan", {}),
                "message": f"Human approval required at node: {current_state.next[0]}. Call POST /research/resume."
            }

        report = state.get("final_report")
        research_store.save(thread_id, request.query, "completed", report=report)

        return {
            "status": "completed",
            "thread_id": thread_id,
            "report": report
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error("research_workflow_failed", error=str(e), thread_id=thread_id)
        research_store.save(thread_id, request.query, "failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- SSE Streaming Research Endpoint ---

@app.post("/research/stream")
async def start_research_stream(request: ResearchRequest):
    """
    Streams research progress via Server-Sent Events (SSE).
    Emits events: agent_start, agent_complete, iteration, error, complete.
    """
    if not settings.enable_streaming:
        raise HTTPException(status_code=400, detail="Streaming is disabled in settings.")

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "query": request.query,
        "messages": [],
        "iteration_count": 1,
        "replan_count": 0,
        "agent_timings": {},
        "scratchpad": {},
    }

    logger.info("starting_research_stream", query=request.query, thread_id=thread_id)
    research_store.save(thread_id, request.query, "running")

    async def event_generator():
        """Generate SSE events as the graph executes."""
        try:
            # Send initial event
            yield _sse_event("research_start", {
                "thread_id": thread_id,
                "query": request.query,
                "timestamp": time.time()
            })

            current_agent = None
            agent_start_time = None
            last_iteration = 1
            last_replan = 0

            # Stream through graph execution
            async for event in app_graph.astream(initial_state, config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    
                    # Handle parallel nodes (like researchers)
                    if node_name == "researcher":
                        yield _sse_event("parallel_task_complete", {
                            "node": "researcher",
                            "messages": node_output.get("messages", [])
                        })
                        continue

                    # Detect agent transitions for sequential nodes
                    if node_name not in ["increment", "replan", "finalize", "mark_retry"] and node_name != current_agent:
                        # Send completion event for previous agent
                        if current_agent and agent_start_time:
                            duration_ms = int((time.time() - agent_start_time) * 1000)
                            yield _sse_event("agent_complete", {
                                "agent": current_agent,
                                "duration_ms": duration_ms,
                                "timestamp": time.time()
                            })

                        current_agent = node_name
                        agent_start_time = time.time()

                        # Send start event for new agent
                        yield _sse_event("agent_start", {
                            "agent": node_name,
                            "timestamp": time.time()
                        })

                    # Detect iteration loops
                    if node_name == "increment" or (node_name == "critic" and node_output.get("iteration_count")):
                        iteration_count = node_output.get("iteration_count", last_iteration)
                        if iteration_count > last_iteration:
                            last_iteration = iteration_count
                            yield _sse_event("iteration", {
                                "iteration": iteration_count,
                                "timestamp": time.time()
                            })

                    # Detect replan loops
                    if node_name == "replan":
                        replan_count = node_output.get("replan_count", last_replan + 1)
                        last_replan = replan_count
                        yield _sse_event("replan", {
                            "replan_count": replan_count,
                            "timestamp": time.time()
                        })

                    # Send preview data for key agents
                    if node_name == "planner" and node_output.get("plan"):
                        yield _sse_event("planner_preview", {
                            "sub_questions": node_output["plan"].get("sub_questions", []),
                            "strategy": node_output["plan"].get("research_strategy", "")
                        })

                    if node_name == "collector" and node_output.get("researcher_output"):
                        sources = node_output["researcher_output"].get("sources", [])
                        yield _sse_event("researcher_preview", {
                            "source_count": len(sources),
                            "sources": [{"title": s.get("title", ""), "url": s.get("url", "")} for s in sources[:5]]
                        })

                    if node_name == "critic" and node_output.get("verification_report"):
                        critic = node_output["verification_report"]
                        yield _sse_event("critic_preview", {
                            "quality_score": critic.get("quality_score", 0),
                            "should_replan": critic.get("should_replan", False),
                            "gaps": critic.get("gaps", [])
                        })
                        
                    if node_name == "editor" and node_output.get("editor_result"):
                        editor = node_output["editor_result"]
                        yield _sse_event("editor_preview", {
                            "passed": editor.get("passed", False),
                            "warnings": editor.get("quality_warnings", [])
                        })

            # Send final completion event for last agent
            if current_agent and agent_start_time:
                duration_ms = int((time.time() - agent_start_time) * 1000)
                yield _sse_event("agent_complete", {
                    "agent": current_agent,
                    "duration_ms": duration_ms,
                    "timestamp": time.time()
                })

            # Get final state
            final_state = app_graph.get_state(config)
            
            # Check for HIL interrupt
            if final_state.next:
                research_store.save(
                    thread_id, request.query, "paused_for_approval",
                    preview=final_state.values.get("plan", {})
                )
                yield _sse_event("human_approval_required", {
                    "thread_id": thread_id,
                    "node": final_state.next[0]
                })
                return

            report = final_state.values.get("final_report")

            if report:
                research_store.save(thread_id, request.query, "completed", report=report)
                yield _sse_event("complete", {
                    "thread_id": thread_id,
                    "report": report,
                    "timestamp": time.time()
                })
            else:
                yield _sse_event("error", {
                    "message": "No report generated",
                    "thread_id": thread_id
                })

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error("stream_research_failed", error=str(e), thread_id=thread_id)
            research_store.save(thread_id, request.query, "failed")
            yield _sse_event("error", {
                "message": str(e),
                "thread_id": thread_id
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


def _sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# --- Resume Endpoint ---

@app.post("/research/resume")
async def resume_research(request: ResumeRequest):
    config = {"configurable": {"thread_id": request.thread_id}}
    current_state = app_graph.get_state(config)

    if not current_state.next:
        raise HTTPException(status_code=400, detail="No pending action for this thread ID.")

    if request.action == "reject":
        research_store.save(request.thread_id, "", "rejected")
        return {"status": "rejected", "message": "Research workflow canceled by human."}

    try:
        # Pass None to resume
        state = await app_graph.ainvoke(None, config)
        report = state.get("final_report")
        research_store.save(request.thread_id, "", "completed", report=report)
        return {
            "status": "completed",
            "thread_id": request.thread_id,
            "report": report
        }
    except Exception as e:
        logger.error("resume_workflow_failed", error=str(e), thread_id=request.thread_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- Research History Endpoints ---

@app.get("/research/history")
async def get_history(limit: int = 50, offset: int = 0):
    """Get list of past research sessions."""
    sessions = research_store.list_all(limit=limit, offset=offset)
    # Return lightweight summaries (no full reports)
    summaries = []
    for s in sessions:
        summaries.append({
            "thread_id": s["thread_id"],
            "query": s["query"],
            "status": s["status"],
            "created_at": s["created_at"],
            "has_report": s.get("report") is not None,
        })
    return {"sessions": summaries, "total": research_store.count()}


@app.get("/research/{thread_id}")
async def get_research(thread_id: str):
    """Get a specific research session with full report."""
    session = research_store.get(thread_id)
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found.")
    return session


@app.delete("/research/{thread_id}")
async def delete_research(thread_id: str):
    """Delete a research session."""
    if research_store.delete(thread_id):
        return {"status": "deleted", "thread_id": thread_id}
    raise HTTPException(status_code=404, detail="Research session not found.")


# --- Export Endpoints ---

@app.get("/research/{thread_id}/export/markdown")
async def export_markdown(thread_id: str):
    """Export a research report as a Markdown file."""
    session = research_store.get(thread_id)
    if not session or not session.get("report"):
        raise HTTPException(status_code=404, detail="No completed report found for this thread.")

    report = session["report"]
    md = _report_to_markdown(report)

    return Response(
        content=md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="research_report_{thread_id[:8]}.md"'
        }
    )


def _report_to_markdown(report: dict) -> str:
    """Convert a report dict to a formatted Markdown string."""
    lines = []
    lines.append(f"# {report.get('title', 'Research Report')}\n")

    if report.get("executive_summary"):
        lines.append(f"> **Executive Summary:** {report['executive_summary']}\n")

    if report.get("methodology"):
        lines.append(f"## Methodology\n{report['methodology']}\n")

    if report.get("summary"):
        lines.append(f"## Summary\n{report['summary']}\n")

    if report.get("key_findings"):
        lines.append("## Key Findings")
        for finding in report["key_findings"]:
            lines.append(f"- {finding}")
        lines.append("")

    if report.get("risks"):
        lines.append("## Risks & Concerns")
        for risk in report["risks"]:
            lines.append(f"- {risk}")
        lines.append("")

    if report.get("contradictions"):
        lines.append("## Contradictions")
        for c in report["contradictions"]:
            lines.append(f"- {c}")
        lines.append("")

    if report.get("knowledge_gaps"):
        lines.append("## Areas for Further Research")
        for gap in report["knowledge_gaps"]:
            lines.append(f"- {gap}")
        lines.append("")

    if report.get("sources"):
        lines.append("## Sources")
        for src in report["sources"]:
            if isinstance(src, dict):
                idx = src.get("index", "")
                title = src.get("title", "Unknown")
                url = src.get("url", "")
                cred = src.get("credibility", 0)
                lines.append(f"{idx}. [{title}]({url}) - Credibility: {cred:.0%}")
            else:
                lines.append(f"- {src}")
        lines.append("")
        
    if report.get("quality_warnings"):
        lines.append("## QA Warnings")
        for w in report["quality_warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    if report.get("metadata"):
        meta = report["metadata"]
        lines.append("---")
        lines.append(f"*Generated by Multi-Agent Deep Research System | "
                     f"Iterations: {meta.get('iterations_taken', 1)} | "
                     f"Replans: {meta.get('replan_count', 0)} | "
                     f"Sources Scanned: {meta.get('total_sources_scanned', 0)} | "
                     f"Total Searches: {meta.get('total_searches', 0)}*")

    return "\n".join(lines)


# --- Health Check ---

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": settings.api_version,
        "sessions_stored": research_store.count(),
        "config": {
            "model": settings.model_name,
            "max_iterations": settings.max_iterations,
            "critic_enabled": settings.enable_critic,
            "streaming_enabled": settings.enable_streaming,
            "mock_mode": settings.mock_mode,
        }
    }
