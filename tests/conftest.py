"""Shared fixtures: tests run offline (mock LLM, mock search, in-memory Supabase)."""

import os
import time

os.environ["MOCK_MODE"] = "true"

import pytest  # noqa: E402

from config.settings import settings  # noqa: E402
from tests.fake_supabase import ANON_KEY, SERVICE_KEY, URL, FakeSupabase  # noqa: E402

PASSWORD = "correct-horse-battery"
TERMINAL = ("completed", "failed", "cancelled")


@pytest.fixture(autouse=True)
def offline_defaults():
    """Reset settings that individual tests mutate."""
    settings.mock_mode = True
    settings.mock_scenario = "default"
    settings.mock_latency_ms = 0
    yield
    settings.mock_mode = True
    settings.mock_scenario = "default"
    settings.mock_latency_ms = 0


@pytest.fixture
def initial_state():
    return {
        "query": "What is X?",
        "messages": [],
        "iteration_count": 1,
        "replan_count": 0,
        "agent_timings": {},
        "scratchpad": {},
    }


@pytest.fixture
def backend(monkeypatch):
    """Point the real Supabase client at an in-memory Supabase."""
    import api.main as main
    from utils import auth
    from utils.jobs import jobs
    from utils.supabase import supabase

    fake = FakeSupabase()
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(settings, "supabase_anon_key", ANON_KEY)
    monkeypatch.setattr(settings, "supabase_service_role_key", SERVICE_KEY)
    monkeypatch.setattr(settings, "admin_emails", "admin@example.com")
    monkeypatch.setattr(supabase, "_transport", fake.transport)
    monkeypatch.setattr(supabase, "_http", None)

    main._request_log.clear()
    main._db_health.update(checked=0.0, ready=False)
    auth.clear_cache()
    jobs._jobs.clear()
    jobs._slots = None  # the semaphore belongs to the previous test's event loop
    yield fake
    auth.clear_cache()
    jobs._jobs.clear()
    jobs._slots = None


@pytest.fixture
def client(backend):
    from fastapi.testclient import TestClient

    import api.main as main

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def sign_in(client, backend):
    """Register (if needed), confirm and sign in as *email*; returns the user dict."""

    def _sign_in(email="user@example.com", name="Test User"):
        client.cookies.clear()
        if not backend._user_by_email(email):
            created = client.post("/auth/register", json={
                "email": email, "password": PASSWORD, "full_name": name,
            })
            assert created.status_code == 201, created.text
            backend.confirm(email)
        response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return response.json()["user"]

    return _sign_in


@pytest.fixture
def wait_for(client):
    """Poll a run until it reaches one of *statuses* (default: any terminal one)."""

    def _wait_for(run_id, statuses=TERMINAL, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = client.get(f"/runs/{run_id}")
            if response.status_code == 200:  # a simulated outage answers 502
                run = response.json()["run"]
                if run["status"] in statuses:
                    return run
            time.sleep(0.02)
        raise AssertionError(f"run {run_id} did not reach {statuses}: {response.text[:200]}")

    return _wait_for
