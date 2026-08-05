import pytest
from graph.nodes.planner import planner_node
from schemas.planner import ResearchPlan

@pytest.mark.asyncio
async def test_planner_node_mock():
    # Because we're in mock mode, it will return the canned response
    state = {"query": "What is X?", "replan_count": 0}
    result = await planner_node(state)
    
    assert "plan" in result
    plan_dict = result["plan"]
    
    # Validate schema
    plan = ResearchPlan(**plan_dict)
    assert len(plan.sub_questions) == 3
    assert plan.original_query == "mock query"
