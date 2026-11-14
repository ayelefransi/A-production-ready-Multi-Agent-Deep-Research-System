"""
HTTP API for the Multi-Agent Deep Research System.

Routes stay thin: authentication lives in ``utils/auth.py``, data access in
``utils/repository.py``, background execution in ``utils/jobs.py`` and all
research logic in the LangGraph nodes.
"""

import os
import sys
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque, Dict, Literal, Optional

# Make the project root importable when the app is started from another directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from pydantic import BaseModel, EmailStr, Field, field_validator  # noqa: E402

from config.settings import settings  # noqa: E402
from utils import auth, repository  # noqa: E402
from utils.auth import CurrentUser, current_admin, current_user  # noqa: E402
from utils.config_check import clean_key, key_problems  # noqa: E402
from utils.jobs import jobs  # noqa: E402
from utils.llm_router import (  # noqa: E402
    NO_PROVIDER_MESSAGE,
    available_services,
    llm_ready,
)
from utils.logger import logger  # noqa: E402
from utils.report_export import report_to_html, report_to_markdown  # noqa: E402
from utils.sse import sse_event  # noqa: E402
from utils.supabase import (  # noqa: E402
    NOT_CONFIGURED_MESSAGE,
    SupabaseError,
    SupabaseNotConfigured,
    is_configured,
    supabase,
)
from utils.tracing import setup_tracing  # noqa: E402

setup_tracing()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for problem in key_problems():
        logger.warning("configuration_problem", problem=problem)
    if is_configured():
        try:
            closed = await repository.fail_orphaned_runs()
            if closed:
                logger.warning("orphaned_runs_closed", count=closed)
        except Exception:  # noqa: BLE001 - the app must still start and report it
            logger.exception("startup_recovery_failed")
    else:
        logger.warning("supabase_not_configured")
    yield
    await jobs.shutdown()
    await supabase.aclose()


app = FastAPI(
    title="Multi-Agent Deep Research API",
    description="Multi-user research agent: accounts, background research runs, "
                "live progress and persistent reports.",
    version=settings.api_version,
    lifespan=lifespan,
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
    if request.url.path.startswith(("/auth", "/runs", "/admin")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(SupabaseNotConfigured)
async def _not_configured(_request: Request, _exc: SupabaseNotConfigured):
    return JSONResponse(status_code=503, content={"detail": NOT_CONFIGURED_MESSAGE})


@app.exception_handler(SupabaseError)
async def _database_error(_request: Request, exc: SupabaseError):
    # Details stay in the log; table and column names are not for clients.
    logger.error("supabase_error", status=exc.status, code=exc.code, message=exc.message)
    return JSONResponse(status_code=502,
                        content={"detail": "The database request failed. Try again."})


# --- Request models ---------------------------------------------------------

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class Registration(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(default="", max_length=120)

    @field_validator("full_name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class EmailOnly(BaseModel):
    email: EmailStr


class SessionTokens(BaseModel):
    access_token: str = Field(min_length=10, max_length=4096)
    refresh_token: str = Field(min_length=1, max_length=4096)


class PasswordReset(BaseModel):
    access_token: str = Field(min_length=10, max_length=4096)
    password: str = Field(min_length=8, max_length=200)


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


class Decision(BaseModel):
    action: Literal["approve", "reject"]


class UserUpdate(BaseModel):
    role: Optional[Literal["user", "admin"]] = None
    is_active: Optional[bool] = None


# --- Rate limiting ----------------------------------------------------------

_RATE_WINDOW_SECONDS = 60.0
_request_log: Dict[str, Deque[float]] = defaultdict(deque)


def enforce_rate_limit(key: str, limit: int, message: str) -> None:
    """
    Sliding-window limit per key (a user id, or a client IP for sign-in).

    State is per process: adequate for the single-instance deployment this
    app is designed for, not a substitute for an edge limiter.
    """
    if limit <= 0:
        return
    now = time.monotonic()
    window = _request_log[key]
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - window[0])))
        raise HTTPException(status_code=429, detail=message,
                            headers={"Retry-After": str(retry_after)})
    window.append(now)


def _limit_auth(request: Request) -> None:
    auth.require_same_origin(request)
    client = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"auth:{client}", settings.auth_rate_limit_per_minute,
                       "Too many attempts. Wait a minute and try again.")


# --- Frontend and health ----------------------------------------------------

@app.get("/", include_in_schema=False)
async def home(request: Request):
    response = templates.TemplateResponse(request=request, name="index.html")
    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    return response


_db_health = {"checked": 0.0, "ready": False}


async def _database_ready() -> bool:
    if not is_configured():
        return False
    if not _db_health["ready"] or time.monotonic() - _db_health["checked"] > 30:
        _db_health["ready"] = await supabase.ping()
        _db_health["checked"] = time.monotonic()
    return bool(_db_health["ready"])


@app.get("/health")
async def health_check():
    database_ready = await _database_ready()
    return {
        "status": "healthy" if database_ready and llm_ready() else "degraded",
        "version": settings.api_version,
        "active_runs": jobs.active_count,
        "config": {
            "database_configured": is_configured(),
            "database_ready": database_ready,
            "llm_ready": llm_ready(),
            "llm_providers": available_services(),
            "search_provider": "tavily" if clean_key(settings.tavily_api_key) else "keyless",
            "problems": key_problems(),
            "mock_mode": settings.mock_mode,
            "human_approval": settings.require_human_approval,
            "max_query_length": settings.max_query_length,
            "max_active_runs_per_user": settings.max_active_runs_per_user,
            "run_timeout_seconds": settings.run_timeout_seconds,
        },
    }


# --- Authentication ---------------------------------------------------------

@app.post("/auth/register", status_code=201)
async def register(body: Registration, request: Request, response: Response):
    _limit_auth(request)
    try:
        result = await supabase.sign_up(body.email, body.password, body.full_name)
    except SupabaseError as exc:
        if exc.status == 429:
            raise HTTPException(status_code=429,
                                detail="Too many sign-up attempts. Try again later.")
        if exc.status in (400, 422):
            raise HTTPException(status_code=400, detail=exc.message)
        raise

    if result.get("access_token"):  # email confirmation is disabled in Supabase
        user = await auth.resolve_user(result["access_token"], result.get("user"))
        auth.set_session_cookies(request, response, result)
        return {"status": "signed_in", "user": user.public()}
    return {"status": "confirmation_required",
            "message": "Check your inbox and confirm your email address, then sign in."}


@app.post("/auth/login")
async def login(body: Credentials, request: Request, response: Response):
    _limit_auth(request)
    try:
        session = await supabase.sign_in(body.email, body.password)
    except SupabaseError as exc:
        if exc.code == "email_not_confirmed" or "not confirmed" in exc.message.lower():
            raise HTTPException(status_code=403,
                                detail="Confirm your email address first. Check your inbox.")
        if exc.status in (400, 401, 422):
            raise HTTPException(status_code=401, detail="Incorrect email or password.")
        if exc.status == 429:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
        raise
    user = await auth.resolve_user(session["access_token"], session.get("user"))
    auth.set_session_cookies(request, response, session)
    return {"user": user.public()}


@app.post("/auth/session")
async def adopt_session(body: SessionTokens, request: Request, response: Response):
    """Complete an email-link sign-in (the confirmation link carries the tokens)."""
    _limit_auth(request)
    try:
        user = await auth.resolve_user(body.access_token)
    except SupabaseError:
        raise HTTPException(status_code=401, detail="That link is invalid or has expired.")
    auth.set_session_cookies(request, response, {
        "access_token": body.access_token, "refresh_token": body.refresh_token,
    })
    return {"user": user.public()}


@app.post("/auth/logout")
async def logout(request: Request, response: Response):
    auth.require_same_origin(request)
    access = request.cookies.get(auth.ACCESS_COOKIE)
    if access:
        try:
            await supabase.sign_out(access)
        except (SupabaseError, SupabaseNotConfigured):
            pass  # the cookies are cleared either way
        auth.forget_token(access)
    auth.clear_session_cookies(response)
    return {"status": "signed_out"}


@app.get("/auth/me")
async def me(user: CurrentUser = Depends(current_user)):
    return {"user": user.public()}


@app.post("/auth/forgot")
async def forgot_password(body: EmailOnly, request: Request):
    _limit_auth(request)
    try:
        await supabase.send_recovery(body.email)
    except SupabaseError as exc:
        logger.warning("password_recovery_failed", status=exc.status, code=exc.code)
    # Same answer whether or not the address exists.
    return {"message": "If that address has an account, a reset link is on its way."}


@app.post("/auth/reset")
async def reset_password(body: PasswordReset, request: Request):
    _limit_auth(request)
    try:
        await supabase.update_password(body.access_token, body.password)
    except SupabaseError as exc:
        if exc.status in (401, 403):
            raise HTTPException(status_code=401,
                                detail="That reset link is invalid or has expired.")
        if exc.status in (400, 422):
            raise HTTPException(status_code=400, detail=exc.message)
        raise
    return {"message": "Password updated. Sign in with the new password."}


# --- Research runs ----------------------------------------------------------

async def _run_or_404(user: CurrentUser, run_id: uuid.UUID, with_report: bool = True) -> dict:
    run = await repository.get_run(user.id, str(run_id), with_report=with_report)
    if not run:
        raise HTTPException(status_code=404, detail="Research run not found.")
    return run


async def _start_run(user: CurrentUser, query: str) -> dict:
    if not llm_ready():
        raise HTTPException(status_code=503, detail=NO_PROVIDER_MESSAGE)
    enforce_rate_limit(f"runs:{user.id}", settings.rate_limit_per_minute,
                       "Too many research requests. Wait a moment and try again.")
    active = await repository.count_active_runs(user.id)
    if active >= settings.max_active_runs_per_user:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {active} runs in progress (the limit is "
                   f"{settings.max_active_runs_per_user}). Wait for one to finish or cancel it.",
        )
    run = await repository.create_run(user.id, query)
    jobs.start(run["id"], user.id, query)
    logger.info("run_started", run_id=run["id"], user_id=user.id)
    return run


@app.post("/runs", status_code=202)
async def create_run(body: ResearchRequest, user: CurrentUser = Depends(current_user)):
    """Start a research run in the background and return immediately."""
    return {"run": await _start_run(user, body.query)}


@app.get("/runs")
async def list_runs(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(current_user),
):
    """The signed-in user's runs, newest first (without report bodies)."""
    return {"runs": await repository.list_runs(user.id, limit=limit, offset=offset)}


@app.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser = Depends(current_user)):
    return {"run": await _run_or_404(user, run_id)}


@app.delete("/runs/{run_id}")
async def delete_run(run_id: uuid.UUID, user: CurrentUser = Depends(current_user)):
    jobs.cancel(str(run_id), user.id)
    if not await repository.delete_run(user.id, str(run_id)):
        raise HTTPException(status_code=404, detail="Research run not found.")
    return {"status": "deleted", "id": str(run_id)}


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, user: CurrentUser = Depends(current_user)):
    run = await _run_or_404(user, run_id, with_report=False)
    if run["status"] not in repository.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="This run has already finished.")
    if not jobs.cancel(str(run_id), user.id):
        # Active in the database but not in this process: close it directly.
        await repository.finish_run(str(run_id), "cancelled", error="Cancelled by the user.")
    return {"status": "cancelling", "id": str(run_id)}


@app.post("/runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: uuid.UUID, user: CurrentUser = Depends(current_user)):
    """Start a fresh run with the same question."""
    run = await _run_or_404(user, run_id, with_report=False)
    return {"run": await _start_run(user, run["query"])}


@app.post("/runs/{run_id}/decision")
async def decide_run(run_id: uuid.UUID, body: Decision,
                     user: CurrentUser = Depends(current_user)):
    """Approve or reject a run paused at the human-approval checkpoint."""
    await _run_or_404(user, run_id, with_report=False)
    if not jobs.decide(str(run_id), user.id, approve=body.action == "approve"):
        raise HTTPException(status_code=409, detail="This run is not waiting for approval.")
    return {"status": "approved" if body.action == "approve" else "rejected"}


@app.get("/runs/{run_id}/events")
async def run_events(run_id: uuid.UUID, user: CurrentUser = Depends(current_user)):
    """
    Server-Sent Events for one run: everything so far, then live updates.

    Safe to open at any time and any number of times; a finished run answers
    with a single terminal event built from the database.
    """
    job = jobs.get(str(run_id), user.id)
    if job:
        stream = jobs.subscribe(job)
    else:
        run = await _run_or_404(user, run_id)

        async def snapshot():
            if run["status"] == "completed" and run.get("report"):
                yield sse_event("complete", {"run_id": run["id"], "report": run["report"]})
            elif run["status"] == "cancelled":
                yield sse_event("cancelled", {"run_id": run["id"],
                                              "message": run.get("error") or "Cancelled."})
            else:
                yield sse_event("error", {
                    "run_id": run["id"],
                    "message": run.get("error") or "This run is no longer in progress.",
                })

        stream = snapshot()

    return StreamingResponse(stream, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no",
    })


@app.get("/runs/{run_id}/export/{export_format}")
async def export_run(run_id: uuid.UUID, export_format: Literal["markdown", "html"],
                     user: CurrentUser = Depends(current_user)):
    """Download a finished report as Markdown or as a standalone HTML document."""
    run = await _run_or_404(user, run_id)
    if not run.get("report"):
        raise HTTPException(status_code=404, detail="This run has no report.")
    if export_format == "markdown":
        content = report_to_markdown(run["report"], run["query"], run["created_at"])
        media_type, extension = "text/markdown; charset=utf-8", "md"
    else:
        content = report_to_html(run["report"], run["query"], run["created_at"])
        media_type, extension = "text/html; charset=utf-8", "html"
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition":
            f'attachment; filename="research_report_{str(run_id)[:8]}.{extension}"',
        # A downloaded report is opened as a local file; it must never run script.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


# --- User management (admin) ------------------------------------------------

@app.get("/admin/users")
async def list_users(_admin: CurrentUser = Depends(current_admin)):
    profiles = await repository.list_profiles()
    counts = await repository.run_counts_by_user()
    return {"users": [{**p, "run_count": counts.get(p["id"], 0)} for p in profiles]}


def _not_self(admin: CurrentUser, user_id: uuid.UUID) -> None:
    if str(user_id) == admin.id:
        raise HTTPException(status_code=400,
                            detail="You cannot change or delete your own admin account here.")


@app.patch("/admin/users/{user_id}")
async def update_user(user_id: uuid.UUID, body: UserUpdate,
                      admin: CurrentUser = Depends(current_admin)):
    _not_self(admin, user_id)
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    profile = await repository.update_profile(str(user_id), values)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found.")
    auth.forget_user(str(user_id))
    logger.info("user_updated", target=str(user_id), by=admin.id, changes=values)
    return {"user": profile}


@app.delete("/admin/users/{user_id}")
async def delete_user(user_id: uuid.UUID, admin: CurrentUser = Depends(current_admin)):
    """Delete an account. Its runs are removed by the database cascade."""
    _not_self(admin, user_id)
    try:
        await supabase.admin_delete_user(str(user_id))
    except SupabaseError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail="User not found.")
        raise
    auth.forget_user(str(user_id))
    logger.info("user_deleted", target=str(user_id), by=admin.id)
    return {"status": "deleted", "id": str(user_id)}
