"""
Background research jobs.

A run is an asyncio task owned by the server, not by an HTTP request, so it
keeps going when the browser tab is closed or refreshed, and any number of
runs can be in flight at once.

Guarantees that keep a run from getting stuck:

* every run has a hard wall-clock timeout (``run_timeout_seconds``);
* every exit path (success, pipeline error, crash, timeout, cancel) writes a
  terminal status to the database;
* a global semaphore bounds concurrency, and waiting runs are visibly "queued";
* runs left active by a previous process are failed at startup
  (``repository.fail_orphaned_runs``).

Progress events are buffered in memory per run so a client can attach at any
time and replay what it missed; the durable record (status, stage, progress,
report) is written to the database as the run advances.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from config.settings import settings
from graph.build_graph import app_graph
from utils import repository
from utils.errors import PipelineError, describe_failure
from utils.logger import logger
from utils.mock_llm import mock_run_calls
from utils.sse import GraphEventTranslator, sse_event

# Progress shown on the dashboard once an agent has finished.
_PROGRESS_AFTER = {
    "planner": 10, "researcher": 40, "collector": 46, "analyst": 60,
    "critic": 72, "writer": 88, "editor": 96,
}
_NEXT_STAGE = {
    "planner": "researcher", "researcher": "collector", "collector": "analyst",
    "analyst": "critic", "critic": "writer", "writer": "editor", "editor": "",
}
_BUFFER_TTL_SECONDS = 120
_KEEPALIVE_SECONDS = 15

Event = Tuple[str, dict]


@dataclass
class Job:
    run_id: str
    user_id: str
    query: str
    events: List[Event] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: bool = False
    task: Optional[asyncio.Task] = None
    decision: Optional[asyncio.Future] = None  # set while awaiting human approval
    cancel_requested: bool = False


def initial_state(query: str) -> Dict[str, Any]:
    return {
        "query": query,
        "messages": [],
        "iteration_count": 1,
        "replan_count": 0,
        "agent_timings": {},
        "scratchpad": {},
    }


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._slots: Optional[asyncio.Semaphore] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self, run_id: str, user_id: str, query: str) -> None:
        """Schedule a run. Returns immediately."""
        if self._slots is None:
            self._slots = asyncio.Semaphore(settings.max_concurrent_runs)
        job = Job(run_id=run_id, user_id=user_id, query=query)
        self._jobs[run_id] = job
        job.task = asyncio.create_task(self._supervise(job), name=f"run-{run_id}")

    def get(self, run_id: str, user_id: str) -> Optional[Job]:
        job = self._jobs.get(run_id)
        return job if job and job.user_id == user_id else None

    def cancel(self, run_id: str, user_id: str) -> bool:
        job = self.get(run_id, user_id)
        if not job or job.done or not job.task:
            return False
        job.cancel_requested = True
        job.task.cancel()
        return True

    def decide(self, run_id: str, user_id: str, approve: bool) -> bool:
        """Resolve a pending human-approval checkpoint."""
        job = self.get(run_id, user_id)
        if not job or not job.decision or job.decision.done():
            return False
        job.decision.set_result(approve)
        return True

    async def subscribe(self, job: Job) -> AsyncIterator[str]:
        """Replay a job's events from the start, then follow it live."""
        index = 0
        while True:
            while index < len(job.events):
                name, data = job.events[index]
                index += 1
                yield sse_event(name, data)
            if job.done:
                return
            async with job.changed:
                try:
                    await asyncio.wait_for(job.changed.wait(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # keeps proxies from closing an idle stream

    async def shutdown(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if not job.done)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _emit(self, job: Job, name: str, data: dict) -> None:
        job.events.append((name, data))
        async with job.changed:
            job.changed.notify_all()

    async def _save(self, run_id: str, **values: Any) -> None:
        """Best-effort progress write; a database blip must not kill the run."""
        try:
            await repository.update_run(run_id, **values)
        except Exception:  # noqa: BLE001
            logger.exception("run_progress_write_failed", run_id=run_id)

    async def _supervise(self, job: Job) -> None:
        """Run the job and guarantee a terminal status whatever happens."""
        status, extra, terminal = "failed", {}, ("error", {"message": "The research run failed."})
        try:
            assert self._slots is not None
            await self._emit(job, "queued", {"run_id": job.run_id})
            async with self._slots:
                async with asyncio.timeout(settings.run_timeout_seconds):
                    status, extra, terminal = await self._execute(job)
        except TimeoutError:
            minutes = settings.run_timeout_seconds // 60
            message = f"The run was stopped after exceeding the {minutes}-minute time limit."
            logger.warning("run_timed_out", run_id=job.run_id)
            extra, terminal = {"error": message}, ("error", {"message": message})
        except asyncio.CancelledError:
            status = "cancelled"
            reason = "Cancelled by the user." if job.cancel_requested else "The server shut down."
            extra, terminal = {"error": reason}, ("cancelled", {"message": reason})
        except PipelineError as exc:
            logger.warning("run_stopped", run_id=job.run_id, reason=exc.user_message)
            extra, terminal = {"error": exc.user_message}, ("error", {"message": exc.user_message})
        except Exception as exc:  # noqa: BLE001 - details go to the log, not the client
            logger.exception("run_crashed", run_id=job.run_id)
            message = describe_failure(exc) or (
                f"The research run failed unexpectedly. Reference: {job.run_id[:8]}"
            )
            extra, terminal = {"error": message}, ("error", {"message": message})
        finally:
            await self._finalize(job, status, extra, terminal)

    async def _finalize(self, job: Job, status: str, extra: dict, terminal: Event) -> None:
        # Shielded so a cancellation cannot interrupt the terminal write.
        async def write() -> None:
            for attempt in range(3):
                try:
                    await repository.finish_run(job.run_id, status, **extra)
                    return
                except Exception:  # noqa: BLE001
                    logger.exception("run_final_write_failed", run_id=job.run_id, attempt=attempt)
                    await asyncio.sleep(1 + attempt)

        try:
            await asyncio.shield(write())
        finally:
            job.done = True
            name, data = terminal
            await asyncio.shield(self._emit(job, name, {**data, "run_id": job.run_id}))
            asyncio.get_running_loop().call_later(
                _BUFFER_TTL_SECONDS, self._jobs.pop, job.run_id, None
            )

    async def _execute(self, job: Job) -> Tuple[str, dict, Event]:
        config = {"configurable": {"thread_id": job.run_id}}
        started = time.monotonic()
        translator = GraphEventTranslator()
        mock_run_calls.set({})  # per-run counters for the scripted mock scenario
        researchers_pending = 0

        await self._save(job.run_id, status="running", stage="planner", progress=2,
                         started_at=repository._now())
        await self._emit(job, "research_start", {
            "run_id": job.run_id, "query": job.query, "mock_mode": settings.mock_mode,
        })

        graph_input: Optional[Dict[str, Any]] = initial_state(job.query)
        while True:
            async for chunk in app_graph.astream(graph_input, config, stream_mode="debug"):
                for name, data in translator.translate(chunk):
                    await self._emit(job, name, data)

                    if name == "agent_start" and data["agent"] == "researcher":
                        researchers_pending += 1
                    elif name == "agent_complete":
                        agent = data["agent"]
                        if agent == "researcher":
                            researchers_pending -= 1
                            if researchers_pending > 0:
                                continue
                        await self._save(job.run_id, stage=_NEXT_STAGE[agent],
                                         progress=_PROGRESS_AFTER[agent])
                    elif name == "replan":
                        await self._save(job.run_id, stage="planner", progress=12,
                                         round=data["replan_count"] + 1)
                    elif name == "writer_retry":
                        await self._save(job.run_id, stage="writer", progress=74)

            state = app_graph.get_state(config)
            if not state.next:
                break

            # Human-in-the-loop checkpoint: wait for a decision, bounded in time.
            job.decision = asyncio.get_running_loop().create_future()
            await self._save(job.run_id, status="awaiting_approval")
            await self._emit(job, "human_approval_required", {
                "run_id": job.run_id, "node": state.next[0],
                "plan": state.values.get("plan") or {},
            })
            try:
                approved = await asyncio.wait_for(
                    job.decision, timeout=settings.approval_timeout_seconds
                )
            except asyncio.TimeoutError:
                message = "No approval decision was made in time, so the run was cancelled."
                return "cancelled", {"error": message}, ("cancelled", {"message": message})
            finally:
                job.decision = None
            if not approved:
                message = "Rejected at the approval checkpoint."
                return "cancelled", {"error": message}, ("cancelled", {"message": message})
            await self._save(job.run_id, status="running")
            await self._emit(job, "approved", {"run_id": job.run_id})
            graph_input = None  # resume from the checkpoint

        report = state.values.get("final_report")
        if not report:
            raise PipelineError("The pipeline finished without producing a report.")

        # Wall-clock duration of the whole run (agents ran in parallel, so
        # summing per-agent timings would overstate it).
        metadata = dict(report.get("metadata") or {})
        metadata["total_duration_seconds"] = round(time.monotonic() - started, 2)
        report = {**report, "metadata": metadata}

        extra = {
            "report": report, "progress": 100, "error": "",
            "title": report.get("title", ""), "source_count": len(report.get("sources") or []),
        }
        return "completed", extra, ("complete", {"report": report})


jobs = JobManager()
