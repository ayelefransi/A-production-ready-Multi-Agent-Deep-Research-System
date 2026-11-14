"""
Thin async client for Supabase: Auth (GoTrue) and the database (PostgREST).

Talks to the documented REST endpoints with httpx, so the server needs no
extra SDK. Database calls use the service-role key, which must never leave
the server; every query that touches user data is filtered by the caller's
user id in ``utils/repository.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from config.settings import settings
from utils.config_check import clean_key
from utils.logger import logger

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

NOT_CONFIGURED_MESSAGE = (
    "The database is not configured. Set SUPABASE_URL, SUPABASE_ANON_KEY and "
    "SUPABASE_SERVICE_ROLE_KEY in the .env file, run supabase/schema.sql in the "
    "Supabase SQL editor, and restart the server."
)


class SupabaseError(RuntimeError):
    """A Supabase call failed. ``code`` is Supabase's error code when present."""

    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(f"Supabase {status}: {message}")
        self.status = status
        self.message = message
        self.code = code


class SupabaseNotConfigured(RuntimeError):
    """Supabase credentials are missing."""


def project_url() -> str:
    """
    The project's base URL.

    The dashboard shows several URLs (project, REST, auth); pasting the REST
    one ("https://x.supabase.co/rest/v1/") is a common mistake, so a trailing
    API path is tolerated and removed.
    """
    url = settings.supabase_url.strip().rstrip("/")
    for suffix in ("/rest/v1", "/auth/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


def is_configured() -> bool:
    return bool(
        project_url()
        and clean_key(settings.supabase_anon_key)
        and clean_key(settings.supabase_service_role_key)
    )


class SupabaseClient:
    """One shared HTTP connection pool for Auth and database calls."""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport  # tests inject an in-memory Supabase here
        self._http: Optional[httpx.AsyncClient] = None

    def _client(self) -> httpx.AsyncClient:
        if not is_configured():
            raise SupabaseNotConfigured(NOT_CONFIGURED_MESSAGE)
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=project_url(),
                timeout=_TIMEOUT,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @staticmethod
    def _headers(key: str, bearer: Optional[str] = None) -> Dict[str, str]:
        return {"apikey": key, "Authorization": f"Bearer {bearer or key}"}

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = (
            body.get("msg") or body.get("message") or body.get("error_description")
            or body.get("error") or response.text[:200] or "request failed"
        )
        code = str(body.get("error_code") or body.get("code") or "")
        raise SupabaseError(response.status_code, str(message), code)

    # ------------------------------------------------------------------
    # Auth (anon key, or the user's own access token)
    # ------------------------------------------------------------------
    async def _auth(self, method: str, path: str, *, bearer: Optional[str] = None,
                    json: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        response = await self._client().request(
            method, f"/auth/v1{path}", json=json, params=params,
            headers=self._headers(settings.supabase_anon_key, bearer),
        )
        self._raise_for(response)
        return response.json() if response.content else {}

    async def sign_up(self, email: str, password: str, full_name: str) -> dict:
        params = {"redirect_to": settings.site_url} if settings.site_url else None
        return await self._auth("POST", "/signup", params=params, json={
            "email": email, "password": password, "data": {"full_name": full_name},
        })

    async def sign_in(self, email: str, password: str) -> dict:
        return await self._auth("POST", "/token", params={"grant_type": "password"},
                                json={"email": email, "password": password})

    async def refresh_session(self, refresh_token: str) -> dict:
        return await self._auth("POST", "/token", params={"grant_type": "refresh_token"},
                                json={"refresh_token": refresh_token})

    async def get_user(self, access_token: str) -> dict:
        return await self._auth("GET", "/user", bearer=access_token)

    async def sign_out(self, access_token: str) -> None:
        await self._auth("POST", "/logout", bearer=access_token)

    async def send_recovery(self, email: str) -> None:
        params = {"redirect_to": settings.site_url} if settings.site_url else None
        await self._auth("POST", "/recover", params=params, json={"email": email})

    async def update_password(self, access_token: str, password: str) -> dict:
        return await self._auth("PUT", "/user", bearer=access_token, json={"password": password})

    async def admin_delete_user(self, user_id: str) -> None:
        response = await self._client().delete(
            f"/auth/v1/admin/users/{user_id}",
            headers=self._headers(settings.supabase_service_role_key),
        )
        self._raise_for(response)

    # ------------------------------------------------------------------
    # Database (service-role key; callers filter by user id)
    # ------------------------------------------------------------------
    async def _rest(self, method: str, table: str, *, params: Optional[dict] = None,
                    json: Any = None, prefer: str = "") -> List[dict]:
        headers = self._headers(settings.supabase_service_role_key)
        if prefer:
            headers["Prefer"] = prefer
        response = await self._client().request(
            method, f"/rest/v1/{table}", params=params, json=json, headers=headers,
        )
        self._raise_for(response)
        return response.json() if response.content else []

    async def select(self, table: str, *, columns: str = "*", filters: Optional[dict] = None,
                     order: str = "", limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[dict]:
        params: Dict[str, Any] = {"select": columns, **(filters or {})}
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return await self._rest("GET", table, params=params)

    async def insert(self, table: str, row: dict, *, upsert: bool = False) -> dict:
        prefer = "return=representation"
        if upsert:
            prefer += ",resolution=merge-duplicates"
        rows = await self._rest("POST", table, json=row, prefer=prefer)
        return rows[0] if rows else {}

    async def update(self, table: str, filters: dict, values: dict) -> List[dict]:
        return await self._rest("PATCH", table, params=filters, json=values,
                                prefer="return=representation")

    async def delete(self, table: str, filters: dict) -> List[dict]:
        return await self._rest("DELETE", table, params=filters, prefer="return=representation")

    async def ping(self) -> bool:
        """True when the database answers and the schema is installed."""
        try:
            await self.select("research_runs", columns="id", limit=1)
            return True
        except (SupabaseError, SupabaseNotConfigured, httpx.HTTPError) as exc:
            logger.warning("supabase_ping_failed", error=str(exc))
            return False


supabase = SupabaseClient()
