"""API contract tests: validation, the SSE event stream, history, export, limits."""

import json
import uuid

import pytest

from config.settings import settings

QUERY = "What is X and why does it matter?"


def read_events(response):
    """Parse an SSE response into a list of (event, data) tuples."""
    events, name = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
    return events


def stream(client, query=QUERY):
    with client.stream("POST", "/research/stream", json={"query": query}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return read_events(response)


def index_of(events, name, agent=None):
    return next(
        i for i, (n, d) in enumerate(events)
        if n == name and (agent is None or d.get("agent") == agent)
    )


def test_health_reports_real_configuration(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["config"]["mock_mode"] is True
    assert body["config"]["max_query_length"] == settings.max_query_length


def test_home_serves_ui_with_strict_csp(client):
    response = client.get("/")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("query", ["", "  ", "ab", "x" * 5000])
def test_invalid_queries_are_rejected(client, query):
    assert client.post("/research/stream", json={"query": query}).status_code == 422
    assert client.post("/research", json={"query": query}).status_code == 422


def test_stream_event_contract(client):
    events = stream(client)
    names = [name for name, _ in events]

    assert names[0] == "research_start"
    assert names[-1] == "complete"
    assert "error" not in names

    starts = [d for n, d in events if n == "agent_start"]
    completes = [d for n, d in events if n == "agent_complete"]
    assert len(starts) == len(completes) == 9  # 6 single agents + 3 researchers
    assert {d["task_id"] for d in starts} == {d["task_id"] for d in completes}

    # Every agent starts before it completes, and agents finish in pipeline order.
    for agent in ("planner", "collector", "analyst", "critic", "writer", "editor"):
        assert index_of(events, "agent_start", agent) < index_of(events, "agent_complete", agent)
    assert [d["agent"] for d in completes] == [
        "planner", "researcher", "researcher", "researcher",
        "collector", "analyst", "critic", "writer", "editor",
    ]

    # All three researchers are started before any of them finishes (fan-out).
    researcher_starts = [
        i for i, (n, d) in enumerate(events)
        if n == "agent_start" and d["agent"] == "researcher"
    ]
    assert len(researcher_starts) == 3
    assert max(researcher_starts) < index_of(events, "researcher_task_complete")
    assert all(events[i][1]["sub_question"] for i in researcher_starts)

    previews = {n: d for n, d in events if n.endswith("_preview")}
    assert len(previews["planner_preview"]["sub_questions"]) == 3
    assert previews["researcher_preview"]["source_count"] == 2
    assert previews["critic_preview"]["should_replan"] is False
    assert previews["editor_preview"]["passed"] is True
    assert "citation_coverage" in previews["editor_preview"]

    report = events[-1][1]["report"]
    assert report["title"] and report["metadata"]["total_duration_seconds"] >= 0


def test_stream_reports_replan_and_writer_retry(client):
    settings.mock_scenario = "self_heal"
    events = stream(client)
    names = [name for name, _ in events]

    assert names.count("replan") == 1
    assert names.count("writer_retry") == 1
    assert names[-1] == "complete"

    # Loop events are ordered: critic verdict -> replan -> planner restarts.
    replan_at = names.index("replan")
    assert events[replan_at - 1][0] == "agent_complete"
    assert events[replan_at - 1][1]["agent"] == "critic"
    assert events[replan_at + 1][1]["agent"] == "planner"

    critic_previews = [d for n, d in events if n == "critic_preview"]
    assert [d["should_replan"] for d in critic_previews] == [True, False]
    assert events[-1][1]["report"]["metadata"]["replan_count"] == 1


def test_history_get_export_delete_lifecycle(client):
    thread_id = stream(client)[-1][1]["thread_id"]

    history = client.get("/research/history").json()
    assert history["total"] == 1
    summary = history["sessions"][0]
    assert summary["thread_id"] == thread_id
    assert summary["query"] == QUERY
    assert summary["status"] == "completed" and summary["has_report"] is True

    session = client.get(f"/research/{thread_id}").json()
    assert session["report"]["title"] == "Mock Research Report: Understanding X"

    export = client.get(f"/research/{thread_id}/export/markdown")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    assert export.text.startswith("# Mock Research Report: Understanding X")
    assert "## Sources" in export.text and "Credibility: 90%" in export.text

    assert client.delete(f"/research/{thread_id}").status_code == 200
    assert client.get(f"/research/{thread_id}").status_code == 404
    assert client.delete(f"/research/{thread_id}").status_code == 404
    assert client.get("/research/history").json()["total"] == 0


def test_unknown_and_malformed_thread_ids(client):
    missing = uuid.uuid4()
    assert client.get(f"/research/{missing}").status_code == 404
    assert client.get(f"/research/{missing}/export/markdown").status_code == 404
    assert client.get("/research/not-a-uuid").status_code == 422
    assert client.delete("/research/not-a-uuid").status_code == 422


def test_history_pagination_bounds(client):
    assert client.get("/research/history?limit=0").status_code == 422
    assert client.get("/research/history?limit=100000").status_code == 422
    assert client.get("/research/history?offset=-1").status_code == 422


def test_blocking_endpoint_returns_report(client):
    body = client.post("/research", json={"query": "What is X?"}).json()
    assert body["status"] == "completed"
    assert body["report"]["sources"]


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    assert client.post("/research", json={"query": "What is X?"}).status_code == 200
    assert client.post("/research", json={"query": "What is X?"}).status_code == 200
    blocked = client.post("/research", json={"query": "What is X?"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    # Reads are not rate limited.
    assert client.get("/research/history").status_code == 200


def test_pipeline_failure_does_not_leak_internals(client, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("SECRET-KEY-abc123 connection string")

    monkeypatch.setattr("utils.sse.app_graph.astream", explode)
    events = stream(client)
    assert events[-1][0] == "error"
    assert "SECRET" not in json.dumps(events)
    thread_id = events[0][1]["thread_id"]
    assert client.get(f"/research/{thread_id}").json()["status"] == "failed"


def test_resume_without_pending_run_is_rejected(client):
    assert client.post(
        "/research/resume", json={"thread_id": str(uuid.uuid4())}
    ).status_code == 400
    assert client.post("/research/resume", json={"thread_id": "nope"}).status_code == 422
    assert client.post(
        "/research/resume",
        json={"thread_id": str(uuid.uuid4()), "action": "delete-everything"},
    ).status_code == 422


@pytest.fixture
def approval_graph(monkeypatch):
    """Rebuild the graph with the human-approval checkpoint enabled."""
    from graph.build_graph import build_graph

    monkeypatch.setattr(settings, "require_human_approval", True)
    graph = build_graph()
    monkeypatch.setattr("api.main.app_graph", graph)
    monkeypatch.setattr("utils.sse.app_graph", graph)
    return graph


def test_human_approval_pause_then_approve(client, approval_graph):
    events = stream(client)
    assert events[-1][0] == "human_approval_required"
    pause = events[-1][1]
    assert pause["node"] == "collector" and pause["plan"]["sub_questions"]

    # Researchers ran; nothing after the checkpoint did.
    agents_done = {d["agent"] for n, d in events if n == "agent_complete"}
    assert agents_done == {"planner", "researcher"}
    paused = client.get(f"/research/{pause['thread_id']}").json()
    assert paused["status"] == "paused_for_approval"

    resumed = client.post("/research/resume", json={"thread_id": pause["thread_id"]}).json()
    assert resumed["status"] == "completed"
    assert resumed["report"]["title"]
    stored = client.get(f"/research/{pause['thread_id']}").json()
    assert stored["status"] == "completed" and stored["query"] == QUERY


def test_human_approval_reject(client, approval_graph):
    pause = stream(client)[-1][1]
    rejected = client.post(
        "/research/resume", json={"thread_id": pause["thread_id"], "action": "reject"}
    ).json()
    assert rejected["status"] == "rejected"
    assert client.get(f"/research/{pause['thread_id']}").json()["status"] == "rejected"
