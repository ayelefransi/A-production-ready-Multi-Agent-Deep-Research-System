"""
Session handling on top of Supabase Auth.

The browser never sees a token: after sign-in the server stores the Supabase
access and refresh tokens in HttpOnly cookies. Each request resolves the
cookie to a user (cached briefly), refreshing the session transparently when
the access token has expired.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response

from config.settings import settings
from utils import repository
from utils.logger import logger
from utils.supabase import (
    NOT_CONFIGURED_MESSAGE,
    SupabaseError,
    SupabaseNotConfigured,
    supabase,
)

ACCESS_COOKIE = "da_access"
REFRESH_COOKIE = "da_refresh"
_REFRESH_MAX_AGE = 60 * 60 * 24 * 30
_CACHE_SECONDS = 30
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    full_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "full_name": self.full_name,
                "role": self.role}


# access-token hash -> (expires_at, user). Bounds Supabase calls from polling.
_cache: Dict[str, Tuple[float, CurrentUser]] = {}


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def forget_user(user_id: str) -> None:
    """Drop cached sessions for a user (after a role or active-flag change)."""
    for key in [k for k, (_, user) in _cache.items() if user.id == user_id]:
        _cache.pop(key, None)


def forget_token(access_token: str) -> None:
    _cache.pop(_cache_key(access_token), None)


def clear_cache() -> None:
    _cache.clear()


def admin_emails() -> set[str]:
    return {email.strip().lower() for email in settings.admin_emails.split(",") if email.strip()}


def _is_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


def set_session_cookies(request: Request, response: Response, session: dict) -> None:
    common = {"httponly": True, "samesite": "lax", "secure": _is_https(request), "path": "/"}
    response.set_cookie(ACCESS_COOKIE, session["access_token"],
                        max_age=int(session.get("expires_in") or 3600), **common)
    response.set_cookie(REFRESH_COOKIE, session["refresh_token"],
                        max_age=_REFRESH_MAX_AGE, **common)


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def require_same_origin(request: Request) -> None:
    """
    CSRF defence for cookie-authenticated, state-changing requests: a browser
    always sends Origin on cross-site POST/PUT/PATCH/DELETE, so reject any
    whose Origin is not this site. (SameSite=Lax cookies are the first layer.)
    """
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if not origin:
        return  # non-browser client; it cannot ride a victim's cookies
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if urlsplit(origin).netloc != host:
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")


async def resolve_user(access_token: str, supabase_user: Optional[dict] = None) -> CurrentUser:
    """Turn a valid access token into a CurrentUser, applying role and active rules."""
    key = _cache_key(access_token)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    account = supabase_user or await supabase.get_user(access_token)
    email = (account.get("email") or "").lower()
    metadata = account.get("user_metadata") or {}
    profile = await repository.ensure_profile(account["id"], email, metadata.get("full_name", ""))

    if not profile.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been deactivated.")

    role = profile.get("role", "user")
    if role != "admin" and email in admin_emails():
        await repository.update_profile(account["id"], {"role": "admin"})
        role = "admin"

    user = CurrentUser(id=account["id"], email=email,
                       full_name=profile.get("full_name") or "", role=role)
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = (time.monotonic() + _CACHE_SECONDS, user)
    return user


async def current_user(request: Request, response: Response) -> CurrentUser:
    """FastAPI dependency: the signed-in user, or 401."""
    require_same_origin(request)
    access = request.cookies.get(ACCESS_COOKIE)
    refresh = request.cookies.get(REFRESH_COOKIE)

    try:
        if access:
            try:
                return await resolve_user(access)
            except SupabaseError as exc:
                if exc.status not in (401, 403):
                    raise
        if refresh:
            try:
                session = await supabase.refresh_session(refresh)
            except SupabaseError as exc:
                if exc.status >= 500:
                    raise
                session = None
            if session and session.get("access_token"):
                set_session_cookies(request, response, session)
                return await resolve_user(session["access_token"], session.get("user"))
    except SupabaseNotConfigured:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MESSAGE)
    except SupabaseError as exc:
        logger.error("auth_backend_error", status=exc.status, message=exc.message)
        raise HTTPException(status_code=503, detail="The authentication service is unavailable.")

    raise HTTPException(status_code=401, detail="Sign in to continue.")


async def current_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user
