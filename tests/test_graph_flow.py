"""
Multi-agent orchestration tests.

These drive the real compiled graph (no node is stubbed out) and assert on
which agents ran, how often, and what the loops did to shared state.
"""

import uuid
from collections import Counter

import pytest

from config.settings import settings
from graph.build_graph import app_graph, should_replan, should_retry_writer
from graph.state import merge_sub_question_results
from schemas.editor import QACheckResult
from schemas.verifier import VerificationReport
from utils.mock_llm import mock_run_calls


async def run_graph(initial_state):
    """Run the graph and return (node execution counts, final state)."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    executed = Counter()
    async for update in app_graph.astream(initial_state, config, stream_mode="updates"):
        executed.update(update.keys())
    return executed, app_graph.get_state(config).values


class _Scripted:
    """Structured-output LLM stand-in that always returns one fixed object."""

    def __init__(self, result):
        self._result = result

    async def ainvoke(self, _prompt, **_kwargs):
        return self._result.model_copy(deep=True)


async def test_happy_path_runs_every_agent_once(initial_state):
    executed, state = await run_graph(initial_state)

    assert executed["planner"] == 1
    assert executed["researcher"] == 3  # one parallel researcher per sub-question
    for agent in ("collector", "analyst", "critic", "writer", "editor", "finalize"):
        assert executed[agent] == 1, agent
    assert executed["replan"] == 0
    assert executed["mark_retry"] == 0

    assert len(state["sub_question_results"]) == 3
    assert {r["sub_question"] for r in state["sub_question_results"]} == {
        "What is X?", "How does X relate to Y?", "What are the implications of X?",
    }
    report = state["final_report"]
    assert report["title"] == "Mock Research Report: Understanding X"
    assert report["metadata"]["replan_count"] == 0


async def test_collector_deduplicates_sources_across_researchers(initial_state):
    _, state = await run_graph(initial_state)
    output = state["researcher_output"]
    # 3 researchers x 2 identical mock sources -> 2 unique, 6 raw
    assert len(output["sources"]) == 2
    assert output["total_raw_results"] == 6


async def test_self_heal_scenario_replans_once_and_retries_writer_once(initial_state):
    settings.mock_scenario = "self_heal"
    mock_run_calls.set({})

    executed, state = await run_graph(initial_state)

    assert executed["replan"] == 1
    assert executed["planner"] == 2
    assert executed["researcher"] == 6  # a fresh fan-out after the replan
    assert executed["critic"] == 2
    assert executed["mark_retry"] == 1
    assert executed["writer"] == 2
    assert executed["editor"] == 2

    assert state["replan_count"] == 1
    assert state["final_report"]["metadata"]["replan_count"] == 1
    assert state["editor_result"]["passed"] is True
    assert not state["final_report"].get("quality_warnings")


async def test_replan_resets_researcher_results(initial_state):
    """Evidence from the discarded plan must not leak into the new round."""
    settings.mock_scenario = "self_heal"
    mock_run_calls.set({})
    _, state = await run_graph(initial_state)
    assert len(state["sub_question_results"]) == 3


async def test_replan_loop_is_capped(initial_state, monkeypatch):
    """A Critic that always demands a replan cannot loop forever."""
    always_replan = VerificationReport(
        claims=[], overall_source_adequacy=0.2, quality_score=0.2,
        gaps=["everything"], should_replan=True, replan_guidance="try again",
    )
    monkeypatch.setattr(
        "graph.nodes.critic.get_llm", lambda *a, **k: _Scripted(always_replan)
    )

    executed, state = await run_graph(initial_state)

    assert executed["replan"] == settings.max_replan_iterations
    assert executed["planner"] == settings.max_replan_iterations + 1
    assert executed["writer"] >= 1  # still produces a report
    assert state["verification_report"]["should_replan"] is False


async def test_good_quality_overrides_replan_request(initial_state, monkeypatch):
    contradictory = VerificationReport(
        claims=[], overall_source_adequacy=0.9, quality_score=0.9, should_replan=True,
    )
    monkeypatch.setattr(
        "graph.nodes.critic.get_llm", lambda *a, **k: _Scripted(contradictory)
    )
    executed, _ = await run_graph(initial_state)
    assert executed["replan"] == 0


async def test_editor_failure_retries_writer_exactly_once_then_attaches_warnings(
    initial_state, monkeypatch
):
    always_fail = QACheckResult(
        citation_coverage=0.1, word_count=10, passed=False,
        quality_warnings=["Low citation coverage"],
    )
    monkeypatch.setattr(
        "graph.nodes.editor.get_llm", lambda *a, **k: _Scripted(always_fail)
    )

    executed, state = await run_graph(initial_state)

    assert executed["writer"] == 2
    assert executed["editor"] == 2
    assert executed["mark_retry"] == 1
    assert state["final_report"]["quality_warnings"] == ["Low citation coverage"]


async def test_one_failing_researcher_does_not_abort_the_run(initial_state, monkeypatch):
    class _Boom:
        async def ainvoke(self, *_a, **_k):
            raise RuntimeError("provider down")

    monkeypatch.setattr("graph.nodes.researcher.get_llm", lambda *a, **k: _Boom())

    executed, state = await run_graph(initial_state)

    assert executed["researcher"] == 3
    assert all(r["sources"] == [] for r in state["sub_question_results"])
    assert state["final_report"]  # downstream agents still ran


def test_reducer_appends_and_resets():
    assert merge_sub_question_results([{"a": 1}], [{"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert merge_sub_question_results(None, [{"b": 2}]) == [{"b": 2}]
    assert merge_sub_question_results([{"a": 1}], None) == []


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"verification_report": {"should_replan": True}, "replan_count": 0}, "replan"),
        ({"verification_report": {"should_replan": True}, "replan_count": 99}, "writer"),
        ({"verification_report": {"should_replan": False}, "replan_count": 0}, "writer"),
        ({}, "writer"),
    ],
)
def test_should_replan_routing(state, expected):
    assert should_replan(state) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"editor_result": {"passed": False}, "scratchpad": {}}, "retry_writer"),
        ({"editor_result": {"passed": False}, "scratchpad": {"writer_retried": True}}, "finalize"),
        ({"editor_result": {"passed": True}, "scratchpad": {}}, "finalize"),
    ],
)
def test_should_retry_writer_routing(state, expected):
    assert should_retry_writer(state) == expected
