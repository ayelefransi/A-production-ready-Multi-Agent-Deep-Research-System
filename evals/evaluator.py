import json
import asyncio
import os
import sys

# Ensure parent directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.workflow import app_graph
from schemas.report_schema import ResearchReport

async def run_evals():
    with open(os.path.join(os.path.dirname(__file__), "test_cases.json"), "r") as f:
        test_cases = json.load(f)
    
    total = len(test_cases)
    passed = 0
    results = []
    
    for case in test_cases:
        query = case["query"]
        print(f"Testing Query: {query}")
        
        initial_state = {"query": query, "messages": []}
        config = {"configurable": {"thread_id": str(case["id"])}}
        
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
            
            if len(report.sources) < 2:
                print("❌ Failed: Not enough sources in final report.")
                results.append({"query": query, "status": "failed", "reason": "Insufficient sources"})
                continue
                
            print("✅ Passed!")
            passed += 1
            results.append({"query": query, "status": "passed"})
            
        except Exception as e:
            print(f"❌ Failed: Exception -> {e}")
            results.append({"query": query, "status": "error", "reason": str(e)})

    score = (passed / total) * 100
    print("\n" + "="*40)
    print(f"EVALUATION COMPLETE: Accuracy Score = {score:.2f}% ({passed}/{total})")
    print("="*40)
    
    # Write results
    with open(os.path.join(os.path.dirname(__file__), "results.json"), "w") as f:
        json.dump({"score": score, "details": results}, f, indent=2)

if __name__ == "__main__":
    asyncio.run(run_evals())
