from langchain_google_genai import ChatGoogleGenerativeAI
from schemas.report_schema import AnalystOutput
from utils.logger import logger
from tenacity import retry, stop_after_attempt, wait_fixed
import json

llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.3)
structured_llm = llm.with_structured_output(AnalystOutput)

@retry(stop=stop_after_attempt(2), wait=wait_fixed(2))
async def analyst_node(state: dict) -> dict:
    researcher_output = state.get("researcher_output", {})
    logger.info("analyst_agent_started")
    
    prompt = f"""
    You are an expert Analyst Agent. Your task is to analyze the output from the Researcher Agent and produce overarching insights, identify risks, and spot any contradictions among the sources.
    
    Researcher Output Context:
    {json.dumps(researcher_output, indent=2)}
    """
    
    try:
        result: AnalystOutput = await structured_llm.ainvoke(prompt)
        logger.info("analyst_agent_success", confidence=result.confidence_score)
        return {"analyst_output": result.model_dump(), "messages": ["Analyst generated insights."]}
    except Exception as e:
        logger.error("analyst_agent_failed", error=str(e))
        raise e
