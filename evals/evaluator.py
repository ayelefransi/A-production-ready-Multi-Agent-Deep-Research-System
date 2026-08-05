import json
import asyncio
import os
import sys
import time
import argparse

# Ensure parent directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.build_graph import app_graph
from schemas.report import ResearchReport
from config.settings import settings

async def run_evals(mock_mode: bool = False):
    if mock_mode:
        settings.mock_mode = True
        print("Running in MOCK MODE - no API quota will be used.")

    with open(os.path.join(os.path.dirname(__file__), "test_cases.json"), "r") as f:
        test_cases = json.load(f)
    
    total = len(test_cases)
    passed = 0
    results = []
    
    start_time = time.time()
    
    for case in test_cases:
        query = case["query"]
        case_type = case.get("type", "standard")
        print(f"Testing Query [{case_type}]: {query}")
        
        initial_state = {
            "query": query, 
            "messages": [],
            "iteration_count": 1,
            "replan_count": 0,
            "agent_timings": {},
            "scratchpad": {}
        }
        config = {"configurable": {"thread_id": f"eval_{case['id']}"}}
        
        try:
            state = await app_graph.ainvoke(initial_state, config)
            final_report_raw = state.get("final_report")
            
            # check completeness and schema validity
            if not final_report_raw:
                print("❌ Failed: No final report generated.")
                results.append({"query": query, "status": "failed", "reason": "No report"})
                continue
            
            # Pydantic validation
            report = ResearchReport(**final_report_raw)
            
            if len(report.sources) < 2 and not mock_mode:
                print("❌ Failed: Not enough sources in final report.")
                results.append({"query": query, "status": "failed", "reason": "Insufficient sources"})
                continue
                
            # If it's a replan test case, ensure replanning occurred (skip in mock mode)
            if case_type == "replan" and not mock_mode:
                if report.metadata and report.metadata.replan_count == 0:
                    print("❌ Failed: Expected a replan loop but none occurred.")
                    results.append({"query": query, "status": "failed", "reason": "Failed to trigger replan"})
                    continue
                    
            print(f"✅ Passed! (Replans: {report.metadata.replan_count if report.metadata else 0})")
            passed += 1
            results.append({
                "query": query, 
                "status": "passed",
                "timings": report.metadata.agent_timings if report.metadata else {}
            })
            
        except Exception as e:
            print(f"❌ Failed: Exception -> {e}")
            results.append({"query": query, "status": "error", "reason": str(e)})

    total_time = time.time() - start_time
    score = (passed / total) * 100
    
    print("\n" + "="*50)
    print(f"EVALUATION COMPLETE: Accuracy Score = {score:.2f}% ({passed}/{total})")
    print(f"Total Time: {total_time:.2f}s")
    print("="*50)
    
    # Write results
    with open(os.path.join(os.path.dirname(__file__), "results.json"), "w") as f:
        json.dump({"score": score, "total_time_seconds": total_time, "details": results}, f, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Run with mock LLM responses")
    args = parser.parse_args()
    
    asyncio.run(run_evals(mock_mode=args.mock))
