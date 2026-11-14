"""
In-memory stand-in for Supabase, used only by the test suite.

It is plugged in as the HTTP transport of the real ``SupabaseClient``, so the
application code under test is exactly the production code: it builds real
requests for the GoTrue and PostgREST endpoints, and this module answers them
with the same status codes and shapes Supabase uses for the cases we rely on.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

URL = "https://fake-project.supabase.co"
ANON_KEY = "anon-key-for-tests"
SERVICE_KEY = "service-role-key-for-tests"

_RUN_DEFAULTS = {
    "status": "queued", "stage": "", "progress": 0, "round": 1, "title": "",
    "source_count": 0, "error": "", "report": None, "started_at": None, "finished_at": None,
}
_PROFILE_DEFAULTS = {"full_name": "", "role": "user", "is_active": True}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


class FakeSupabase:
    def __init__(self, autoconfirm: bool = False):
        self.autoconfirm = autoconfirm
        self.users: Dict[str, dict] = {}
        self.access_tokens: Dict[str, str] = {}
        self.refresh_tokens: Dict[str, str] = {}
        self.tables: Dict[str, List[dict]] = {"profiles": [], "research_runs": []}
        self.recovery_emails: List[str] = []
        self.fail_database = False  # flip to simulate an outage

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ---- test helpers -------------------------------------------------
    def confirm(self, email: str) -> None:
        self._user_by_email(email)["confirmed"] = True

    def expire_access_tokens(self) -> None:
        self.access_tokens.clear()

    def runs(self) -> List[dict]:
        return self.tables["research_runs"]

    # ---- internals ----------------------------------------------------
    def _user_by_email(self, email: str) -> Optional[dict]:
        return next((u for u in self.users.values() if u["email"] == email.lower()), None)

    def _public_user(self, user: dict) -> dict:
        return {"id": user["id"], "email": user["email"],
                "user_metadata": user["user_metadata"]}

    def _session(self, user: dict) -> dict:
        access, refresh = f"access-{uuid.uuid4()}", f"refresh-{uuid.uuid4()}"
        self.access_tokens[access] = user["id"]
        self.refresh_tokens[refresh] = user["id"]
        return {"access_token": access, "refresh_token": refresh, "expires_in": 3600,
                "token_type": "bearer", "user": self._public_user(user)}

    def _bearer_user(self, request: httpx.Request) -> Optional[dict]:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        return self.users.get(self.access_tokens.get(token, ""))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/auth/v1/"):
            return self._auth(request, path[len("/auth/v1"):])
        if path.startswith("/rest/v1/"):
            return self._rest(request, path[len("/rest/v1/"):])
        return _json(404, {"message": "not found"})

    # ---- GoTrue -------------------------------------------------------
    def _auth(self, request: httpx.Request, path: str) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        method = request.method

        if path.startswith("/admin/users/") and method == "DELETE":
            if request.headers.get("apikey") != SERVICE_KEY:
                return _json(401, {"msg": "service role required"})
            user = self.users.pop(path.rsplit("/", 1)[1], None)
            if not user:
                return _json(404, {"msg": "User not found"})
            for table, key in (("profiles", "id"), ("research_runs", "user_id")):  # cascade
                self.tables[table] = [r for r in self.tables[table] if r[key] != user["id"]]
            self.access_tokens = {t: u for t, u in self.access_tokens.items() if u != user["id"]}
            self.refresh_tokens = {t: u for t, u in self.refresh_tokens.items() if u != user["id"]}
            return _json(200, {})

        if request.headers.get("apikey") != ANON_KEY:
            return _json(401, {"msg": "Invalid API key"})

        if path == "/signup" and method == "POST":
            if len(body.get("password", "")) < 6:
                return _json(422, {"error_code": "weak_password",
                                   "msg": "Password should be at least 6 characters."})
            if self._user_by_email(body["email"]):
                # Supabase does not reveal that the address is taken.
                return _json(200, {"id": str(uuid.uuid4()), "email": body["email"]})
            user = {"id": str(uuid.uuid4()), "email": body["email"].lower(),
                    "password": body["password"], "confirmed": self.autoconfirm,
                    "user_metadata": body.get("data") or {}}
            self.users[user["id"]] = user
            # The database trigger from supabase/schema.sql
            self.tables["profiles"].append({
                **_PROFILE_DEFAULTS, "id": user["id"], "email": user["email"],
                "full_name": user["user_metadata"].get("full_name", ""), "created_at": _now(),
            })
            return _json(200, self._session(user) if self.autoconfirm else self._public_user(user))

        if path == "/token" and method == "POST":
            grant = request.url.params.get("grant_type")
            if grant == "password":
                user = self._user_by_email(body.get("email", ""))
                if not user or user["password"] != body.get("password"):
                    return _json(400, {"error_code": "invalid_credentials",
                                       "msg": "Invalid login credentials"})
                if not user["confirmed"]:
                    return _json(400, {"error_code": "email_not_confirmed",
                                       "msg": "Email not confirmed"})
                return _json(200, self._session(user))
            if grant == "refresh_token":
                user = self.users.get(self.refresh_tokens.pop(body.get("refresh_token", ""), ""))
                if not user:
                    return _json(400, {"error_code": "refresh_token_not_found",
                                       "msg": "Invalid Refresh Token"})
                return _json(200, self._session(user))

        if path == "/recover" and method == "POST":
            self.recovery_emails.append(body.get("email", ""))
            return _json(200, {})

        user = self._bearer_user(request)
        if path == "/user" and method == "GET":
            return _json(200, self._public_user(user)) if user else _json(
                401, {"error_code": "bad_jwt", "msg": "invalid JWT"})
        if path == "/user" and method == "PUT":
            if not user:
                return _json(401, {"error_code": "bad_jwt", "msg": "invalid JWT"})
            user["password"] = body["password"]
            return _json(200, self._public_user(user))
        if path == "/logout" and method == "POST":
            if user:
                self.access_tokens = {t: u for t, u in self.access_tokens.items()
                                      if u != user["id"]}
            return httpx.Response(204)
        return _json(404, {"msg": "not found"})

    # ---- PostgREST ----------------------------------------------------
    @staticmethod
    def _matches(row: dict, filters: Dict[str, str]) -> bool:
        for column, condition in filters.items():
            value = str(row.get(column))
            if condition.startswith("eq."):
                if value != condition[3:]:
                    return False
            elif condition.startswith("in.("):
                if value not in condition[4:-1].split(","):
                    return False
            else:
                raise AssertionError(f"filter not supported by the fake: {condition}")
        return True

    def _rest(self, request: httpx.Request, table: str) -> httpx.Response:
        if self.fail_database:
            return _json(503, {"message": "database unavailable"})
        if request.headers.get("apikey") != SERVICE_KEY:
            return _json(401, {"message": "permission denied (service role required)"})
        if table not in self.tables:
            return _json(404, {"code": "42P01", "message": f"relation {table} does not exist"})

        params = dict(request.url.params)
        select = params.pop("select", "*")
        order = params.pop("order", "")
        limit = params.pop("limit", None)
        offset = int(params.pop("offset", 0))
        rows = self.tables[table]
        body = json.loads(request.content) if request.content else None

        if request.method == "GET":
            found = [r for r in rows if self._matches(r, params)]
            if order:
                column, _, direction = order.partition(".")
                found.sort(key=lambda r: r.get(column) or "", reverse=direction == "desc")
            found = found[offset:offset + int(limit)] if limit else found[offset:]
            if select != "*":
                columns = select.split(",")
                found = [{c: r.get(c) for c in columns} for r in found]
            return _json(200, found)

        if request.method == "POST":
            defaults = _RUN_DEFAULTS if table == "research_runs" else _PROFILE_DEFAULTS
            row = {**defaults, "id": str(uuid.uuid4()), "created_at": _now(),
                   "updated_at": _now(), **body}
            existing = next((r for r in rows if r["id"] == row["id"]), None)
            if existing:
                if "merge-duplicates" not in request.headers.get("prefer", ""):
                    return _json(409, {"code": "23505", "message": "duplicate key"})
                existing.update(body)
                row = existing
            else:
                rows.append(row)
            return _json(201, [row])

        if request.method == "PATCH":
            changed = [r for r in rows if self._matches(r, params)]
            for row in changed:
                row.update(body)
            return _json(200, changed)

        if request.method == "DELETE":
            removed = [r for r in rows if self._matches(r, params)]
            self.tables[table] = [r for r in rows if r not in removed]
            return _json(200, removed)

        return _json(405, {"message": "method not allowed"})
