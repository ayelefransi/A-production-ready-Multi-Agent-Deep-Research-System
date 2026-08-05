import pytest
from graph.nodes.critic import critic_node
from schemas.verifier import VerificationReport

@pytest.mark.asyncio
async def test_critic_node_mock():
    state = {
        "analyst_output": {},
        "researcher_output": {},
        "plan": {},
        "iteration_count": 1,
        "replan_count": 0
    }
    
    result = await critic_node(state)
    
    assert "verification_report" in result
    report_dict = result["verification_report"]
    
    report = VerificationReport(**report_dict)
    assert report.quality_score == 0.8
    assert report.should_replan == False
