import pytest
from graph.nodes.editor import editor_node
from schemas.editor import QACheckResult

@pytest.mark.asyncio
async def test_editor_node_mock():
    state = {
        "final_report": {}
    }
    
    result = await editor_node(state)
    
    assert "editor_result" in result
    editor_dict = result["editor_result"]
    
    editor = QACheckResult(**editor_dict)
    assert editor.passed == True
    assert editor.citation_coverage == 1.0
