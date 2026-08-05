import pytest
from graph.build_graph import app_graph

@pytest.mark.asyncio
async def test_e2e_mock():
    """Run the entire graph end-to-end using Mock LLMs."""
    initial_state = {
        "query": "What is X?",
        "messages": [],
        "iteration_count": 1,
        "replan_count": 0,
        "agent_timings": {},
        "scratchpad": {},
    }
    
    config = {"configurable": {"thread_id": "test_e2e"}}
    
    # Run the graph
    final_state = await app_graph.ainvoke(initial_state, config)
    
    assert "final_report" in final_state
    report = final_state["final_report"]
    
    # Mock writer produces this title
    assert report["title"] == "Mock Research Report: Understanding X"
    assert len(report["sources"]) == 2
