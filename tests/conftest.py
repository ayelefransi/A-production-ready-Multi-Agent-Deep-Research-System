"""Shared fixtures: every test runs offline against the mock LLM and mock search."""

import os

os.environ["MOCK_MODE"] = "true"

import pytest  # noqa: E402

from config.settings import settings  # noqa: E402


@pytest.fixture(autouse=True)
def offline_defaults():
    """Reset settings that individual tests mutate."""
    settings.mock_mode = True
    settings.mock_scenario = "default"
    settings.mock_latency_ms = 0
    yield
    settings.mock_mode = True
    settings.mock_scenario = "default"


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
def client():
    """API client with a clean rate-limit window and an empty session store."""
    from fastapi.testclient import TestClient

    import api.main as main
    from utils.storage import research_store

    main._request_log.clear()
    research_store._store.clear()
    with TestClient(main.app) as test_client:
        yield test_client
