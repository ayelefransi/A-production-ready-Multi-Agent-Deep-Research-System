from langchain_google_genai import ChatGoogleGenerativeAI
from schemas.report_schema import ResearcherOutput
from tools.search_tool import execute_search_async
from utils.logger import logger
from config.settings import settings
from tenacity import retry, stop_after_attempt, wait_fixed
import os

# Ensure API key is set for langchain
if settings.gemini_api_key:
    os.environ["GOOGLE_API_KEY"] = settings.gemini_api_key

llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.2)
structured_llm = llm.with_structured_output(ResearcherOutput)

@retry(stop=stop_after_attempt(2), wait=wait_fixed(2))
async def researcher_node(state: dict) -> dict:
    query = state.get("query", "")
    logger.info("researcher_agent_started", query=query)
    
    # Fetch search context
    search_results = await execute_search_async(query, max_results=5)
    
    context = ""
    for r in search_results.get("results", []):
        context += f"URL: {r.get('url')}\nTitle: {r.get('title')}\nContent: {r.get('content')}\n\n"
    
    prompt = f"""
    You are an expert Research Agent. Your task is to analyze the provided search results for the user's query and extract key points.
    Make sure to avoid hallucinated URLs. Only use the exact sources provided in the context snippet below.
    Each source must have at least 2 key points.
    
    User Query: {query}
    
    Search Context:
    {context}
    """
    
    try:
        result: ResearcherOutput = await structured_llm.ainvoke(prompt)
        logger.info("researcher_agent_success", sources_found=len(result.sources))
        return {"researcher_output": result.model_dump(), "messages": [f"Researcher found {len(result.sources)} sources."]}
    except Exception as e:
        logger.error("researcher_agent_failed", error=str(e))
        raise e
