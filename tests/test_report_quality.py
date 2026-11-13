"""Guardrails that keep a report grounded in the evidence actually retrieved."""

from graph.nodes.writer import writer_node
from schemas.report import ResearchReport
from utils.mock_llm import _MOCK_RESPONSES
from utils.report_quality import (
    evidence_digest,
    ground_report,
    measure_report,
    rank_sources,
    strip_invalid_citations,
)

SOURCES = [
    {"title": "Low", "url": "https://example.com/low", "credibility": 0.3, "key_points": ["a", "b"]},
    {"title": "High", "url": "https://example.com/high", "credibility": 0.9, "key_points": ["c", "d"]},
]


def test_sources_are_ranked_by_credibility():
    assert [s["title"] for s in rank_sources(SOURCES)] == ["High", "Low"]


def test_evidence_digest_keeps_every_source_within_budget():
    many = [dict(SOURCES[0], title=f"S{i}", key_points=["x" * 5000]) for i in range(10)]
    digest = evidence_digest(many, max_chars=5000)
    assert all(f"[{i}] S{i - 1}" in digest for i in range(1, 11))  # none dropped
    assert len(digest) < 6000


def test_invalid_citations_are_removed_and_valid_ones_kept():
    assert strip_invalid_citations("Fact [1] and fiction [7].", {1, 2}) == "Fact [1] and fiction."


def test_ground_report_rebuilds_sources_from_real_evidence():
    report = {
        "executive_summary": "Claim [1]. Invented [9].",
        "summary": "Body [2].",
        "methodology": "",
        "key_findings": ["Finding [2]", "Hallucinated [42]"],
        "risks": [], "contradictions": [], "knowledge_gaps": [], "recommendations": [],
        "sources": [{"index": 9, "title": "Fake", "url": "https://fake.example", "credibility": 1.0}],
    }
    grounded = ground_report(report, rank_sources(SOURCES))

    assert "[9]" not in grounded["executive_summary"]
    assert grounded["key_findings"] == ["Finding [2]", "Hallucinated"]
    assert [s["url"] for s in grounded["sources"]] == [
        "https://example.com/high", "https://example.com/low",
    ]


def test_measure_report_counts_cited_findings():
    measured = measure_report({"summary": "one two three", "key_findings": ["a [1]", "b"]})
    assert measured["word_count"] == 3
    assert measured["citation_coverage"] == 0.5
    assert measured["section_completeness"]["risks"] is False


async def test_writer_output_is_schema_valid_and_grounded():
    state = {
        "query": "What is X?",
        "researcher_output": {"sources": SOURCES, "total_searches_performed": 2},
        "analyst_output": {}, "plan": {}, "verification_report": {},
        "agent_timings": {}, "scratchpad": {},
    }
    report = (await writer_node(state))["final_report"]
    ResearchReport(**report)
    # The mock cites [1] and [2]; they must resolve to the ranked real sources.
    assert [s["title"] for s in report["sources"]] == ["High", "Low"]
    assert report["recommendations"] and report["confidence_level"] == "moderate"


async def test_writer_retry_prompt_includes_editor_feedback(monkeypatch):
    captured = {}

    class _Capture:
        async def ainvoke(self, prompt, **_kwargs):
            captured["prompt"] = prompt
            return ResearchReport(**_MOCK_RESPONSES["writer"])

    monkeypatch.setattr("graph.nodes.writer.get_llm", lambda *a, **k: _Capture())
    state = {
        "query": "q",
        "researcher_output": {"sources": SOURCES},
        "scratchpad": {"writer_retried": True},
        "editor_result": {
            "quality_warnings": ["Summary is too short"],
            "improvement_suggestions": ["Cite every key finding"],
        },
    }
    await writer_node(state)
    assert "REVISION REQUIRED" in captured["prompt"]
    assert "Summary is too short" in captured["prompt"]
    assert "Cite every key finding" in captured["prompt"]
