from langchain_google_genai import ChatGoogleGenerativeAI
from schemas.report_schema import ResearchReport
from utils.logger import logger
from tenacity import retry, stop_after_attempt, wait_fixed
import json

llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.4)
structured_llm = llm.with_structured_output(ResearchReport)

@retry(stop=stop_after_attempt(2), wait=wait_fixed(2))
async def writer_node(state: dict) -> dict:
    query = state.get("query", "")
    researcher_output = state.get("researcher_output", {})
    analyst_output = state.get("analyst_output", {})
    
    logger.info("writer_agent_started")
    
    prompt = f"""
    You are an expert Writer Agent. Your task is to convert the analysis and research into a final cohesive research report.
    Ensure you return the exact JSON structure requested. Use detailed markdown formatting for the summaries and key findings where applicable within the JSON string fields.
    
    Original Query: {query}
    
    Analysis Context:
    {json.dumps(analyst_output, indent=2)}
    
    Source Context:
    {json.dumps(researcher_output, indent=2)}
    """
    
    try:
        result: ResearchReport = await structured_llm.ainvoke(prompt)
        logger.info("writer_agent_success", title=result.title)
        return {"final_report": result.model_dump(), "messages": ["Writer generated final report."]}
    except Exception as e:
        logger.error("writer_agent_failed", error=str(e))
        raise e
