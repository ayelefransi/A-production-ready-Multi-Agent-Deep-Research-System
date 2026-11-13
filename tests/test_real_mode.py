"""
Behaviour of the live (non-mock) path, tested without network or API keys:
provider selection, runtime fallback, readiness, keyless search, and the
refusal to write a report that has no evidence behind it.
"""

import uuid

import pytest
from langchain_core.runnables import RunnableLambda

import tools.search_tool as search_tool
import utils.llm_router as router
from config.settings import settings
from graph.build_graph import app_graph
from schemas.planner import ResearchPlan
from utils.errors import LLMUnavailableError, NoEvidenceError
from utils.mock_llm import _MOCK_RESPONSES

PROVIDER_ENV = ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")


@pytest.fixture
def live(monkeypatch):
    """Mock mode off, with every provider key cleared."""
    monkeypatch.setattr(settings, "mock_mode", False)
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(settings, name.lower(), "")
    return monkeypatch


class _FakeProvider:
    """Stands in for a chat model; ``behaviour`` decides what a call does."""

    def __init__(self, name, behaviour, calls):
        self.name, self.behaviour, self.calls = name, behaviour, calls

    def with_structured_output(self, _schema):
        async def call(_prompt):
            self.calls.append(self.name)
            return self.behaviour()

        return RunnableLambda(call)


def test_no_keys_means_not_ready_and_a_clear_error(live):
    assert router.available_providers() == []
    assert router.llm_ready() is False
    with pytest.raises(LLMUnavailableError) as raised:
        router.get_llm("planner")
    assert "GROQ_API_KEY" in raised.value.user_message


def test_placeholder_keys_from_env_example_are_not_keys(live):
    live.setenv("GROQ_API_KEY", "your_groq_api_key_here")
    live.setenv("GEMINI_API_KEY", "  ")
    assert router.available_providers() == []
    assert router.llm_ready() is False


def test_a_real_looking_key_makes_the_provider_available(live):
    live.setenv("GEMINI_API_KEY", "AIzaSyTestKeyNotReal123456")
    assert router.available_providers() == ["gemini"]
    assert router.llm_ready() is True


async def test_runtime_failure_falls_back_to_the_next_provider(live):
    live.setenv("GROQ_API_KEY", "gsk_test_key_not_real")
    live.setenv("GEMINI_API_KEY", "AIzaSyTestKeyNotReal123456")
    calls = []
    plan = ResearchPlan(**_MOCK_RESPONSES["planner"])

    def rate_limited():
        raise RuntimeError("429 rate limit exceeded")

    behaviours = {"groq": rate_limited, "gemini": lambda: plan}
    live.setattr(
        router, "_build_provider",
        lambda name, _temperature: _FakeProvider(name, behaviours[name], calls),
    )

    result = await router.get_llm("planner", structured_output=ResearchPlan).ainvoke("prompt")

    assert result == plan
    assert calls == ["groq", "gemini"]  # primary tried first, then the fallback


async def test_single_provider_is_used_without_a_fallback_chain(live):
    live.setenv("GEMINI_API_KEY", "AIzaSyTestKeyNotReal123456")
    calls = []
    plan = ResearchPlan(**_MOCK_RESPONSES["planner"])

    def build(name, _temperature):
        if not router._api_key_for(name):  # same contract as the real factory
            raise ValueError(f"No API key found for provider '{name}'")
        return _FakeProvider(name, lambda: plan, calls)

    live.setattr(router, "_build_provider", build)
    assert await router.get_llm("planner", structured_output=ResearchPlan).ainvoke("p") == plan
    assert calls == ["gemini"]  # groq (primary) has no key and is skipped


def test_api_refuses_to_start_a_run_without_a_provider(client, live):
    for path in ("/research", "/research/stream"):
        response = client.post(path, json={"query": "What is X?"})
        assert response.status_code == 503
        assert "GROQ_API_KEY" in response.json()["detail"]
    assert client.get("/research/history").json()["total"] == 0  # nothing was started

    config = client.get("/health").json()["config"]
    assert config["llm_ready"] is False and config["llm_providers"] == []


def test_health_reports_ready_in_mock_mode(client):
    config = client.get("/health").json()["config"]
    assert config["llm_ready"] is True and config["mock_mode"] is True


class _FakeDDGS:
    """Records which engines were tried; only 'duckduckgo' has results."""

    tried = []

    def __init__(self, **_kwargs):
        pass

    def text(self, query, max_results, backend):
        _FakeDDGS.tried.append(backend)
        if backend == "bing":
            raise RuntimeError("No results found.")
        return [{"title": "T", "href": "https://example.org/a", "body": f"about {query}"}]


async def test_keyless_search_tries_the_next_engine(live):
    _FakeDDGS.tried = []
    live.setattr(search_tool, "_tavily_client", None)
    live.setattr(search_tool, "DDGS", _FakeDDGS)

    # Call the undecorated function so a failure is not retried for seconds.
    result = await search_tool.execute_search_async.__wrapped__("coffee", max_results=3)

    assert _FakeDDGS.tried == ["bing", "duckduckgo"]
    assert result["provider"] == "keyless_search"
    assert result["results"] == [
        {"title": "T", "url": "https://example.org/a", "content": "about coffee"}
    ]


async def test_keyless_search_returns_empty_when_every_engine_fails(live):
    class _Dead(_FakeDDGS):
        def text(self, *_a, **_k):
            raise RuntimeError("blocked")

    live.setattr(search_tool, "_tavily_client", None)
    live.setattr(search_tool, "DDGS", _Dead)
    result = await search_tool.execute_search_async.__wrapped__("coffee")
    assert result == {"results": [], "provider": "keyless_search"}


class _Boom:
    async def ainvoke(self, *_a, **_k):
        raise RuntimeError("provider down")


async def test_run_with_no_evidence_fails_instead_of_inventing_a_report(
    initial_state, monkeypatch
):
    """Every researcher fails -> no sources -> the Writer must refuse."""
    monkeypatch.setattr("graph.nodes.researcher.get_llm", lambda *a, **k: _Boom())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    with pytest.raises(NoEvidenceError):
        await app_graph.ainvoke(initial_state, config)

    assert not app_graph.get_state(config).values.get("final_report")


async def test_one_failing_researcher_does_not_abort_the_run(initial_state, monkeypatch):
    """A single failed sub-question is tolerated when the others return evidence."""
    from utils.llm_router import get_llm as real_get_llm

    seen = []

    def flaky(role, structured_output=None):
        seen.append(role)
        return _Boom() if len(seen) == 1 else real_get_llm(role, structured_output)

    monkeypatch.setattr("graph.nodes.researcher.get_llm", flaky)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    state = await app_graph.ainvoke(initial_state, config)

    results = state["sub_question_results"]
    assert len(results) == 3
    assert sorted(len(r["sources"]) for r in results) == [0, 2, 2]
    assert state["final_report"]["sources"]


def test_stream_shows_the_no_evidence_message_to_the_client(client, monkeypatch):
    import json

    monkeypatch.setattr("graph.nodes.researcher.get_llm", lambda *a, **k: _Boom())
    with client.stream("POST", "/research/stream", json={"query": "What is X?"}) as response:
        lines = [line for line in response.iter_lines() if line.startswith("data: ")]
    last = json.loads(lines[-1][6:])
    assert "No sources could be retrieved" in last["message"]
    assert client.get(f"/research/{last['thread_id']}").json()["status"] == "failed"
