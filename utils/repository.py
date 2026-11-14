"""
Data access for profiles and research runs.

Every function that reads or changes a user's run takes ``user_id`` and puts
it in the query filter, so ownership is enforced here regardless of which
route calls it. Functions without ``user_id`` are for the job runner and
admin routes only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from utils.supabase import supabase

RUNS = "research_runs"
PROFILES = "profiles"

ACTIVE_STATUSES = ("queued", "running", "awaiting_approval")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

# Everything except the (large) report document.
RUN_SUMMARY_COLUMNS = (
    "id,user_id,query,status,stage,progress,round,title,source_count,error,"
    "created_at,started_at,finished_at,updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _in(values) -> str:
    return f"in.({','.join(values)})"


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

async def get_profile(user_id: str) -> Optional[dict]:
    rows = await supabase.select(PROFILES, filters={"id": f"eq.{user_id}"}, limit=1)
    return rows[0] if rows else None


async def ensure_profile(user_id: str, email: str, full_name: str = "") -> dict:
    """Return the user's profile, creating it if the signup trigger did not."""
    profile = await get_profile(user_id)
    if profile:
        return profile
    return await supabase.insert(
        PROFILES, {"id": user_id, "email": email, "full_name": full_name}, upsert=True
    )


async def update_profile(user_id: str, values: Dict[str, Any]) -> Optional[dict]:
    rows = await supabase.update(PROFILES, {"id": f"eq.{user_id}"}, values)
    return rows[0] if rows else None


async def list_profiles() -> List[dict]:
    return await supabase.select(PROFILES, order="created_at.desc", limit=1000)


async def run_counts_by_user() -> Dict[str, int]:
    rows = await supabase.select(RUNS, columns="user_id", limit=10000)
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["user_id"]] = counts.get(row["user_id"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Runs: user-scoped
# ---------------------------------------------------------------------------

async def create_run(user_id: str, query: str) -> dict:
    return await supabase.insert(RUNS, {"user_id": user_id, "query": query, "status": "queued"})


async def list_runs(user_id: str, limit: int = 100, offset: int = 0) -> List[dict]:
    return await supabase.select(
        RUNS, columns=RUN_SUMMARY_COLUMNS, filters={"user_id": f"eq.{user_id}"},
        order="created_at.desc", limit=limit, offset=offset,
    )


async def get_run(user_id: str, run_id: str, with_report: bool = True) -> Optional[dict]:
    rows = await supabase.select(
        RUNS, columns="*" if with_report else RUN_SUMMARY_COLUMNS,
        filters={"id": f"eq.{run_id}", "user_id": f"eq.{user_id}"}, limit=1,
    )
    return rows[0] if rows else None


async def count_active_runs(user_id: str) -> int:
    rows = await supabase.select(
        RUNS, columns="id",
        filters={"user_id": f"eq.{user_id}", "status": _in(ACTIVE_STATUSES)},
    )
    return len(rows)


async def delete_run(user_id: str, run_id: str) -> bool:
    rows = await supabase.delete(RUNS, {"id": f"eq.{run_id}", "user_id": f"eq.{user_id}"})
    return bool(rows)


# ---------------------------------------------------------------------------
# Runs: job runner (the runner only touches runs it was handed)
# ---------------------------------------------------------------------------

async def update_run(run_id: str, **values: Any) -> None:
    values["updated_at"] = _now()
    await supabase.update(RUNS, {"id": f"eq.{run_id}"}, values)


async def finish_run(run_id: str, status: str, **values: Any) -> None:
    await update_run(run_id, status=status, finished_at=_now(), stage="", **values)


async def fail_orphaned_runs() -> int:
    """
    Close runs left active by a previous server process.

    A run executes inside the server process, so anything still marked active
    at startup can never finish. Marking it failed keeps the dashboard honest
    and frees the user's concurrency slots.
    """
    rows = await supabase.update(
        RUNS, {"status": _in(ACTIVE_STATUSES)},
        {
            "status": "failed", "stage": "", "finished_at": _now(), "updated_at": _now(),
            "error": "The server restarted while this run was in progress. Start it again.",
        },
    )
    return len(rows)
