"""
End-to-end application tests: accounts, background runs, isolation between
users, failure recovery and user management.

The app code is the production code. Only the outermost services are
replaced: Supabase by an in-memory implementation of its REST API, and the
LLM/search by the deterministic mock mode.
"""

import json
import uuid

import pytest

from config.settings import settings
from tests.conftest import PASSWORD

QUERY = "What is X and why does it matter?"


def start(client, query=QUERY):
    response = client.post("/runs", json={"query": query})
    assert response.status_code == 202, response.text
    return response.json()["run"]["id"]


def read_events(client, run_id):
    events, name = [], None
    with client.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    return events


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_everything_requires_a_session(client):
    run_id = uuid.uuid4()
    for method, path in [
        ("GET", "/auth/me"), ("GET", "/runs"), ("POST", "/runs"), ("GET", f"/runs/{run_id}"),
        ("DELETE", f"/runs/{run_id}"), ("GET", f"/runs/{run_id}/events"),
        ("GET", f"/runs/{run_id}/export/html"), ("GET", "/admin/users"),
    ]:
        response = client.request(method, path, json={"query": QUERY})
        assert response.status_code == 401, (method, path)


def test_register_confirm_login_logout(client, backend):
    registered = client.post("/auth/register", json={
        "email": "New.User@Example.com", "password": PASSWORD, "full_name": "  New   User ",
    })
    assert registered.status_code == 201
    assert registered.json()["status"] == "confirmation_required"
    assert "da_access" not in client.cookies  # no session before confirmation

    unconfirmed = client.post("/auth/login", json={"email": "new.user@example.com",
                                                   "password": PASSWORD})
    assert unconfirmed.status_code == 403 and "Confirm your email" in unconfirmed.json()["detail"]

    backend.confirm("new.user@example.com")
    login = client.post("/auth/login", json={"email": "new.user@example.com",
                                             "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["user"]["full_name"] == "New User"
    assert login.json()["user"]["role"] == "user"

    set_cookie = login.headers.get_list("set-cookie")
    assert all("HttpOnly" in c and "SameSite=lax" in c for c in set_cookie)
    assert "access_token" not in login.text  # tokens never reach page scripts

    assert client.get("/auth/me").json()["user"]["email"] == "new.user@example.com"

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_bad_credentials_and_weak_passwords(client, sign_in):
    sign_in("user@example.com")
    client.cookies.clear()
    wrong = client.post("/auth/login", json={"email": "user@example.com", "password": "nope"})
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]  # no account enumeration

    weak = client.post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert weak.status_code == 422
    assert client.post("/auth/register", json={"email": "not-an-email",
                                               "password": PASSWORD}).status_code == 422


def test_expired_access_token_is_refreshed_transparently(client, backend, sign_in):
    from utils import auth

    sign_in()
    old_access = client.cookies["da_access"]
    backend.expire_access_tokens()
    auth.clear_cache()

    response = client.get("/auth/me")
    assert response.status_code == 200
    assert client.cookies["da_access"] != old_access  # a new session cookie was issued


def test_session_dies_when_refresh_token_is_also_invalid(client, backend, sign_in):
    from utils import auth

    sign_in()
    backend.expire_access_tokens()
    backend.refresh_tokens.clear()
    auth.clear_cache()
    assert client.get("/auth/me").status_code == 401


def test_password_recovery_does_not_reveal_accounts_and_reset_works(client, backend, sign_in):
    sign_in("user@example.com")
    access = client.cookies["da_access"]
    client.cookies.clear()

    known = client.post("/auth/forgot", json={"email": "user@example.com"})
    unknown = client.post("/auth/forgot", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert backend.recovery_emails == ["user@example.com", "ghost@example.com"]

    assert client.post("/auth/reset", json={"access_token": "x" * 20,
                                            "password": "new-password-123"}).status_code == 401
    assert client.post("/auth/reset", json={"access_token": access,
                                            "password": "new-password-123"}).status_code == 200
    assert client.post("/auth/login", json={"email": "user@example.com",
                                            "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": "user@example.com",
                                            "password": "new-password-123"}).status_code == 200


def test_cross_site_requests_are_blocked(client, sign_in):
    sign_in()
    forged = client.post("/runs", json={"query": QUERY}, headers={"Origin": "https://evil.example"})
    assert forged.status_code == 403
    same_site = client.post("/runs", json={"query": QUERY}, headers={"Origin": "http://testserver"})
    assert same_site.status_code == 202


def test_unconfigured_database_gives_a_clear_503(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "x"})
    assert response.status_code == 503
    assert "SUPABASE_URL" in response.json()["detail"]
    config = client.get("/health").json()["config"]
    assert config["database_configured"] is False and config["database_ready"] is False


# ---------------------------------------------------------------------------
# Runs: lifecycle, persistence, repetition, concurrency
# ---------------------------------------------------------------------------

def test_run_completes_in_the_background_and_is_persisted(client, backend, sign_in, wait_for):
    user = sign_in()
    run_id = start(client)

    run = wait_for(run_id)
    assert run["status"] == "completed" and run["progress"] == 100 and run["error"] == ""
    assert run["report"]["title"] == "Mock Research Report: Understanding X"
    assert run["started_at"] and run["finished_at"]

    # The durable record is in the database, owned by this user.
    stored = backend.runs()[0]
    assert stored["user_id"] == user["id"] and stored["report"]["sources"]
    assert stored["title"] == run["report"]["title"] and stored["source_count"] == 2

    listing = client.get("/runs").json()["runs"]
    assert [r["id"] for r in listing] == [run_id]
    assert "report" not in listing[0]  # lists stay light

    markdown = client.get(f"/runs/{run_id}/export/markdown")
    assert markdown.status_code == 200 and markdown.text.startswith("# Mock Research Report")
    page = client.get(f"/runs/{run_id}/export/html")
    assert page.status_code == 200 and "<script" not in page.text.lower()
    assert client.get(f"/runs/{run_id}/export/pdf").status_code == 422


def test_results_survive_a_new_login(client, sign_in, wait_for):
    sign_in()
    run_id = start(client)
    wait_for(run_id)

    client.post("/auth/logout")
    assert client.get("/runs").status_code == 401

    sign_in()  # log back in
    assert client.get(f"/runs/{run_id}").json()["run"]["report"]["title"]


def test_event_stream_replays_the_whole_run_for_a_late_subscriber(client, sign_in, wait_for):
    sign_in()
    run_id = start(client)
    wait_for(run_id)

    events = read_events(client, run_id)  # attached after the run finished
    names = [name for name, _ in events]
    assert names[0] == "queued" and names[1] == "research_start" and names[-1] == "complete"

    completes = [d["agent"] for n, d in events if n == "agent_complete"]
    assert completes == ["planner", "researcher", "researcher", "researcher",
                         "collector", "analyst", "critic", "writer", "editor"]
    starts = [i for i, (n, d) in enumerate(events)
              if n == "agent_start" and d["agent"] == "researcher"]
    first_done = names.index("researcher_task_complete")
    assert len(starts) == 3 and max(starts) < first_done  # true parallel fan-out
    assert events[-1][1]["report"]["metadata"]["total_duration_seconds"] >= 0

    # Subscribing twice is safe and gives the same history.
    assert [n for n, _ in read_events(client, run_id)] == names


def test_finished_run_stream_is_served_from_the_database(client, sign_in, wait_for):
    from utils.jobs import jobs

    sign_in()
    run_id = start(client)
    wait_for(run_id)
    jobs._jobs.clear()  # as after the buffer expires, or after a restart

    events = read_events(client, run_id)
    assert [n for n, _ in events] == ["complete"]
    assert events[0][1]["report"]["title"]


def test_many_runs_one_after_another_without_restart(client, sign_in, wait_for):
    sign_in()
    monkey_limit = settings.rate_limit_per_minute
    settings.rate_limit_per_minute = 0
    try:
        ids = []
        for number in range(6):
            run_id = start(client, f"Research question number {number}")
            assert wait_for(run_id)["status"] == "completed"
            ids.append(run_id)
    finally:
        settings.rate_limit_per_minute = monkey_limit

    listing = client.get("/runs").json()["runs"]
    assert len(listing) == 6 and {r["status"] for r in listing} == {"completed"}
    assert {r["query"] for r in listing} == {f"Research question number {n}" for n in range(6)}


def test_several_runs_execute_concurrently_and_independently(client, sign_in, wait_for):
    sign_in()
    settings.mock_latency_ms = 150
    ids = [start(client, f"Concurrent question {n}") for n in range(3)]

    # All three are in flight at the same time.
    statuses = {r["id"]: r["status"] for r in client.get("/runs").json()["runs"]}
    assert all(statuses[i] in ("queued", "running") for i in ids)

    # A fourth is refused while three are active (per-user limit)...
    blocked = client.post("/runs", json={"query": "One too many"})
    assert blocked.status_code == 409 and "limit is 3" in blocked.json()["detail"]

    runs = [wait_for(i) for i in ids]
    assert [r["status"] for r in runs] == ["completed"] * 3
    assert [r["query"] for r in runs] == [f"Concurrent question {n}" for n in range(3)]

    # ...and accepted as soon as a slot is free.
    assert wait_for(start(client, "Now there is room"))["status"] == "completed"


def test_runs_queue_when_the_server_is_at_capacity(client, sign_in, wait_for, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_runs", 1)
    settings.mock_latency_ms = 100
    sign_in()
    first, second = start(client, "First in line"), start(client, "Second in line")

    assert wait_for(first, statuses=("running",))["status"] == "running"
    assert client.get(f"/runs/{second}").json()["run"]["status"] == "queued"

    assert wait_for(first)["status"] == "completed"
    assert wait_for(second)["status"] == "completed"


def test_replan_and_writer_retry_are_tracked(client, sign_in, wait_for):
    sign_in()
    settings.mock_scenario = "self_heal"
    run_id = start(client)
    run = wait_for(run_id)

    assert run["status"] == "completed" and run["round"] == 2
    names = [n for n, _ in read_events(client, run_id)]
    assert names.count("replan") == 1 and names.count("writer_retry") == 1


def test_query_validation_and_rate_limit(client, sign_in, monkeypatch):
    sign_in()
    for bad in ("", "  ", "ab", "x" * 5000):
        assert client.post("/runs", json={"query": bad}).status_code == 422
    assert client.get("/runs/not-a-uuid").status_code == 422

    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    assert client.post("/runs", json={"query": QUERY}).status_code == 202
    limited = client.post("/runs", json={"query": QUERY})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


# ---------------------------------------------------------------------------
# Runs: nothing gets stuck
# ---------------------------------------------------------------------------

def test_cancel_stops_a_run_and_retry_starts_a_fresh_one(client, sign_in, wait_for):
    sign_in()
    settings.mock_latency_ms = 300
    run_id = start(client)
    wait_for(run_id, statuses=("running",))

    assert client.post(f"/runs/{run_id}/cancel").status_code == 200
    cancelled = wait_for(run_id)
    assert cancelled["status"] == "cancelled" and cancelled["error"] == "Cancelled by the user."
    assert cancelled["report"] is None
    assert client.post(f"/runs/{run_id}/cancel").status_code == 409  # already finished
    assert read_events(client, run_id)[-1][0] == "cancelled"

    settings.mock_latency_ms = 0
    retried = client.post(f"/runs/{run_id}/retry")
    assert retried.status_code == 202
    new_run = wait_for(retried.json()["run"]["id"])
    assert new_run["status"] == "completed" and new_run["query"] == QUERY
    assert new_run["id"] != run_id


def test_run_that_exceeds_the_time_limit_is_failed_not_left_running(
    client, sign_in, wait_for, monkeypatch
):
    sign_in()
    settings.mock_latency_ms = 400
    monkeypatch.setattr(settings, "run_timeout_seconds", 0.3)
    run = wait_for(start(client))
    assert run["status"] == "failed" and "time limit" in run["error"]

    # The slot is released: the next run works.
    settings.mock_latency_ms = 0
    monkeypatch.setattr(settings, "run_timeout_seconds", 60)
    assert wait_for(start(client))["status"] == "completed"


def test_crash_is_recorded_without_leaking_internals_and_the_next_run_works(
    client, sign_in, wait_for, monkeypatch
):
    sign_in()

    def explode(*_a, **_k):
        raise RuntimeError("SECRET-KEY-abc123 connection string")

    with monkeypatch.context() as patch:
        patch.setattr("utils.jobs.app_graph.astream", explode)
        run_id = start(client)
        run = wait_for(run_id)
    assert run["status"] == "failed"
    assert "SECRET" not in json.dumps(run) and "Reference:" in run["error"]
    assert "SECRET" not in json.dumps(read_events(client, run_id))

    assert wait_for(start(client))["status"] == "completed"


def test_no_evidence_fails_with_an_explanation(client, sign_in, wait_for, monkeypatch):
    class _Boom:
        async def ainvoke(self, *_a, **_k):
            raise RuntimeError("provider down")

    sign_in()
    monkeypatch.setattr("graph.nodes.researcher.get_llm", lambda *a, **k: _Boom())
    run_id = start(client)
    run = wait_for(run_id)
    assert run["status"] == "failed" and "No sources could be retrieved" in run["error"]
    assert read_events(client, run_id)[-1][1]["message"] == run["error"]


def test_run_survives_a_database_outage_during_progress_updates(
    client, backend, sign_in, wait_for
):
    """Progress writes are best-effort; the final write is retried."""
    import threading

    sign_in()
    settings.mock_latency_ms = 150
    run_id = start(client)
    wait_for(run_id, statuses=("running",))
    backend.fail_database = True
    threading.Timer(0.6, lambda: setattr(backend, "fail_database", False)).start()

    run = wait_for(run_id, timeout=20)
    assert run["status"] == "completed" and run["report"]


def test_runs_left_active_by_a_previous_process_are_closed_at_startup(backend):
    from fastapi.testclient import TestClient

    import api.main as main

    owner = str(uuid.uuid4())
    backend.runs().extend([
        {"id": str(uuid.uuid4()), "user_id": owner, "query": "q", "status": status,
         "error": "", "stage": "writer"}
        for status in ("running", "queued", "awaiting_approval", "completed")
    ])
    with TestClient(main.app):
        pass  # startup hook runs

    assert [r["status"] for r in backend.runs()] == ["failed", "failed", "failed", "completed"]
    assert all("server restarted" in r["error"] for r in backend.runs()[:3])


def test_no_llm_provider_is_refused_up_front(client, sign_in, monkeypatch):
    sign_in()
    monkeypatch.setattr("api.main.llm_ready", lambda: False)
    response = client.post("/runs", json={"query": QUERY})
    assert response.status_code == 503 and "GROQ_API_KEY" in response.json()["detail"]
    assert client.get("/runs").json()["runs"] == []


# ---------------------------------------------------------------------------
# Human approval
# ---------------------------------------------------------------------------

@pytest.fixture
def approval_graph(monkeypatch):
    from graph.build_graph import build_graph

    monkeypatch.setattr(settings, "require_human_approval", True)
    monkeypatch.setattr("utils.jobs.app_graph", build_graph())


def test_approval_checkpoint_approve(client, sign_in, wait_for, approval_graph):
    sign_in()
    run_id = start(client)
    assert wait_for(run_id, statuses=("awaiting_approval",))["status"] == "awaiting_approval"

    assert client.post(f"/runs/{run_id}/decision", json={"action": "approve"}).status_code == 200
    assert wait_for(run_id)["status"] == "completed"
    names = [n for n, _ in read_events(client, run_id)]
    assert names.index("human_approval_required") < names.index("approved") < names.index("complete")
    assert client.post(f"/runs/{run_id}/decision", json={"action": "approve"}).status_code == 409


def test_approval_checkpoint_reject_and_timeout(client, sign_in, wait_for, approval_graph,
                                                monkeypatch):
    sign_in()
    run_id = start(client)
    wait_for(run_id, statuses=("awaiting_approval",))
    assert client.post(f"/runs/{run_id}/decision", json={"action": "reject"}).status_code == 200
    rejected = wait_for(run_id)
    assert rejected["status"] == "cancelled" and "Rejected" in rejected["error"]

    monkeypatch.setattr(settings, "approval_timeout_seconds", 0.2)
    abandoned = wait_for(start(client))
    assert abandoned["status"] == "cancelled" and "in time" in abandoned["error"]


# ---------------------------------------------------------------------------
# Isolation between users
# ---------------------------------------------------------------------------

def test_users_cannot_see_or_touch_each_others_runs(client, sign_in, wait_for):
    sign_in("alice@example.com")
    alice_run = start(client, "Alice's private question")
    wait_for(alice_run)

    sign_in("bob@example.com")
    assert client.get("/runs").json()["runs"] == []
    for method, path in [
        ("GET", f"/runs/{alice_run}"),
        ("GET", f"/runs/{alice_run}/events"),
        ("GET", f"/runs/{alice_run}/export/markdown"),
        ("POST", f"/runs/{alice_run}/cancel"),
        ("POST", f"/runs/{alice_run}/retry"),
        ("DELETE", f"/runs/{alice_run}"),
    ]:
        assert client.request(method, path).status_code == 404, (method, path)
    assert client.post(f"/runs/{alice_run}/decision",
                       json={"action": "approve"}).status_code == 404

    sign_in("alice@example.com")
    assert client.get(f"/runs/{alice_run}").status_code == 200  # untouched
    assert client.delete(f"/runs/{alice_run}").status_code == 200
    assert client.get(f"/runs/{alice_run}").status_code == 404


def test_one_users_limit_does_not_block_another(client, sign_in, wait_for):
    settings.mock_latency_ms = 150
    sign_in("alice@example.com")
    alice_runs = [start(client, f"Alice {n}") for n in range(3)]
    assert client.post("/runs", json={"query": "Alice 4"}).status_code == 409

    sign_in("bob@example.com")
    bob_run = start(client, "Bob is unaffected")
    assert wait_for(bob_run)["status"] == "completed"

    sign_in("alice@example.com")
    assert [wait_for(r)["status"] for r in alice_runs] == ["completed"] * 3


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def test_admin_access_and_user_listing(client, sign_in, wait_for):
    sign_in("user@example.com", "Regular User")
    wait_for(start(client))
    assert client.get("/admin/users").status_code == 403

    admin = sign_in("admin@example.com", "Site Admin")  # listed in ADMIN_EMAILS
    assert admin["role"] == "admin"
    users = {u["email"]: u for u in client.get("/admin/users").json()["users"]}
    assert set(users) == {"user@example.com", "admin@example.com"}
    assert users["user@example.com"]["run_count"] == 1
    assert users["user@example.com"]["role"] == "user"


def test_admin_can_deactivate_promote_and_delete_users(client, backend, sign_in, wait_for):
    target = sign_in("user@example.com")
    wait_for(start(client))
    admin = sign_in("admin@example.com")

    # Deactivate: the user's existing and new sessions stop working.
    assert client.patch(f"/admin/users/{target['id']}",
                        json={"is_active": False}).status_code == 200
    client.cookies.clear()
    blocked = client.post("/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert blocked.status_code == 403 and "deactivated" in blocked.json()["detail"]

    # Reactivate and promote.
    sign_in("admin@example.com")
    updated = client.patch(f"/admin/users/{target['id']}",
                           json={"is_active": True, "role": "admin"})
    assert updated.json()["user"]["role"] == "admin"
    assert sign_in("user@example.com")["role"] == "admin"

    # Guard rails.
    sign_in("admin@example.com")
    assert client.patch(f"/admin/users/{admin['id']}", json={"role": "user"}).status_code == 400
    assert client.delete(f"/admin/users/{admin['id']}").status_code == 400
    assert client.patch(f"/admin/users/{target['id']}", json={}).status_code == 400
    assert client.patch(f"/admin/users/{target['id']}", json={"role": "owner"}).status_code == 422
    assert client.delete(f"/admin/users/{uuid.uuid4()}").status_code == 404

    # Delete: account and its runs are gone.
    assert client.delete(f"/admin/users/{target['id']}").status_code == 200
    assert backend.runs() == []
    client.cookies.clear()
    assert client.post("/auth/login", json={"email": "user@example.com",
                                            "password": PASSWORD}).status_code == 401


def test_home_serves_ui_with_strict_csp(client):
    response = client.get("/")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY"
