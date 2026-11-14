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
    assert router.available_services() == ["gemini"]
    assert router.llm_ready() is True


async def test_runtime_failure_falls_back_to_the_next_provider(live):
    live.setenv("GROQ_API_KEY", "gsk_test_key_not_real")
    live.setenv("GEMINI_API_KEY", "AIzaSyTestKeyNotReal123456")
    calls = []
    plan = ResearchPlan(**_MOCK_RESPONSES["planner"])

    def rate_limited():
        raise RuntimeError("429 rate limit exceeded")

    behaviours = {"groq_fast": rate_limited, "groq": rate_limited, "gemini": lambda: plan}

    def build(name, _temperature, _max_retries=0):
        if not router._api_key_for(name):  # same contract as the real factory
            raise ValueError(f"No API key found for provider '{name}'")
        return _FakeProvider(name, behaviours[name], calls)

    live.setattr(router, "_build_provider", build)

    result = await router.get_llm("planner", structured_output=ResearchPlan).ainvoke("prompt")

    assert result == plan
    assert calls == ["groq_fast", "groq", "gemini"]  # primary first, then each fallback


async def test_single_provider_is_used_without_a_fallback_chain(live):
    live.setenv("GEMINI_API_KEY", "AIzaSyTestKeyNotReal123456")
    calls = []
    plan = ResearchPlan(**_MOCK_RESPONSES["planner"])

    def build(name, _temperature, _max_retries=0):
        if not router._api_key_for(name):  # same contract as the real factory
            raise ValueError(f"No API key found for provider '{name}'")
        return _FakeProvider(name, lambda: plan, calls)

    live.setattr(router, "_build_provider", build)
    assert await router.get_llm("planner", structured_output=ResearchPlan).ainvoke("p") == plan
    assert calls == ["gemini"]  # groq (primary) has no key and is skipped


class _FakeDDGS:
    """Records which engines were tried; only 'duckduckgo' has results."""

    tried = []

    def __init__(self, **_kwargs):
        pass

    def text(self, query, max_results, backend):
        _FakeDDGS.tried.append(backend)
        if backend == "auto":
            raise RuntimeError("No results found.")
        return [{"title": "T", "href": "https://example.org/a", "body": f"about {query}"}]


async def test_keyless_search_tries_the_next_engine(live):
    _FakeDDGS.tried = []
    live.setattr(search_tool, "_tavily_client", None)
    live.setattr(search_tool, "DDGS", _FakeDDGS)

    # Call the undecorated function so a failure is not retried for seconds.
    result = await search_tool.execute_search_async.__wrapped__("coffee", max_results=3)

    assert _FakeDDGS.tried == ["auto", "duckduckgo"]
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


@pytest.mark.parametrize("pasted", [
    "https://abc.supabase.co",
    "https://abc.supabase.co/",
    "https://abc.supabase.co/rest/v1",
    "https://abc.supabase.co/rest/v1/",
    " https://abc.supabase.co/auth/v1 ",
])
def test_supabase_url_tolerates_a_pasted_api_path(monkeypatch, pasted):
    from utils.supabase import project_url

    monkeypatch.setattr(settings, "supabase_url", pasted)
    assert project_url() == "https://abc.supabase.co"


def test_key_problems_name_the_wrong_keys_without_revealing_them(live):
    from utils.config_check import clean_key, key_problems

    live.setattr(settings, "groq_api_key", "definitely-not-a-groq-key-0123456789abcdef")
    live.setattr(settings, "tavily_api_key", "...")
    live.setattr(settings, "supabase_anon_key", "sb_secret_same_value_1234567890")
    live.setattr(settings, "supabase_service_role_key", "sb_secret_same_value_1234567890")

    problems = " | ".join(key_problems())
    assert "GROQ_API_KEY does not look like a valid key" in problems
    assert "TAVILY_API_KEY is a placeholder" in problems
    assert "SUPABASE_ANON_KEY does not look like" in problems
    assert "same value" in problems
    assert "0123456789abcdef" not in problems  # values are never echoed
    assert clean_key("...") == "" and clean_key(" 'gsk_realistic_key_value' ") == "gsk_realistic_key_value"


def test_correctly_formatted_keys_raise_no_problems(live):
    from utils.config_check import key_problems

    live.setattr(settings, "groq_api_key", "gsk_" + "a" * 52)
    live.setattr(settings, "tavily_api_key", "tvly-" + "b" * 30)
    live.setattr(settings, "supabase_anon_key", "sb_publishable_" + "c" * 31)
    live.setattr(settings, "supabase_service_role_key", "sb_secret_" + "d" * 31)
    assert key_problems() == []


@pytest.mark.parametrize("error, expected", [
    ("Error code: 404 - {'error': {'message': 'The model `x` does not exist', 'code': 'model_not_found'}}",
     "no longer offers the configured model"),
    ("Error code: 401 - {'error': {'message': 'Invalid API Key'}}", "rejected its API key"),
    ("429 RESOURCE_EXHAUSTED. You exceeded your current quota", "rate limited or out of quota"),
    ("503 UNAVAILABLE. This model is currently experiencing high demand", "temporarily unavailable"),
    ("something nobody anticipated", ""),
])
def test_provider_failures_are_explained_without_echoing_provider_text(error, expected):
    from utils.errors import describe_failure

    message = describe_failure(RuntimeError(error))
    assert expected in message
    assert "Error code" not in message and "{" not in message
