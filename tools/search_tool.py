from tavily import TavilyClient
from duckduckgo_search import DDGS
import asyncio
from config.settings import settings
from utils.logger import logger
from tenacity import retry, wait_exponential, stop_after_attempt

# Initialize client if key is provided
tavily_client = None
if settings.tavily_api_key:
    try:
        tavily_client = TavilyClient(api_key=settings.tavily_api_key)
    except Exception as e:
        logger.error("failed_to_initialize_tavily", error=str(e))

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
def execute_search(query: str, search_depth: str = "advanced", max_results: int = 5) -> dict:
    """
    Executes a search query using Tavily API. Includes retry and timeout handling via tenacity.
    Falls back to DuckDuckGo search if Tavily API key is missing or fails.
    """
    if tavily_client:
        logger.info("executing_search_with_tavily", query=query, depth=search_depth)
        try:
            response = tavily_client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_images=False
            )
            return response
        except Exception as e:
            logger.warning("tavily_search_failed_falling_back_to_ddg", error=str(e))
            # Fall through and let DuckDuckGo handle it
    
    logger.info("executing_search_with_ddg", query=query)
    try:
        results = []
        with DDGS() as ddgs:
            ddg_results = list(ddgs.text(query, max_results=max_results))
            for res in ddg_results:
                results.append({
                    "title": res.get("title", ""),
                    "url": res.get("href", ""),
                    "content": res.get("body", "")
                })
        return {"results": results}
    except Exception as e:
        logger.error("failed_ddg_search", error=str(e))
        raise e  # Let tenacity retry if DDG fails

async def execute_search_async(query: str, search_depth: str = "advanced", max_results: int = 5) -> dict:
    """
    Asynchronous wrapper for Tavily search logic.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, execute_search, query, search_depth, max_results)
