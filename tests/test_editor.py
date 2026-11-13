from graph.nodes.editor import editor_node
from schemas.editor import QACheckResult
from utils.mock_llm import _MOCK_RESPONSES


async def test_editor_passes_a_complete_cited_report():
    result = await editor_node({"final_report": dict(_MOCK_RESPONSES["writer"])})
    qa = QACheckResult(**result["editor_result"])
    assert qa.passed is True
    assert qa.citation_coverage == 1.0
    assert qa.word_count >= 100  # measured, not the model's estimate
    assert all(qa.section_completeness.values())


async def test_editor_fails_an_empty_report_even_if_the_model_approves():
    """The mock LLM says 'passed'; measured facts must override it."""
    result = await editor_node({"final_report": {}})
    qa = QACheckResult(**result["editor_result"])
    assert qa.passed is False
    assert qa.word_count == 0
    assert any("Missing or empty sections" in w for w in qa.quality_warnings)
