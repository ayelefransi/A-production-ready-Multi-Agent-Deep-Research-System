"""
Web search tool with Tavily and DuckDuckGo fallback.
"""

from __future__ import annotations

import asyncio

from duckduckgo_search import DDGS
from tavily import AsyncTavilyClient
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from utils.logger import logger

# Initialize client if key is provided
_tavily_client = None
if settings.tavily_enabled and settings.tavily_api_key:
    try:
        _tavily_client = AsyncTavilyClient(api_key=settings.tavily_api_key)
    except Exception as e:
        logger.error("failed_to_initialize_tavily", error=str(e))


@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
async def execute_search_async(
    query: str, search_depth: str = "advanced", max_results: int = 5
) -> dict:
    """
    Executes a search query using Tavily API. Includes retry and timeout handling.
    Falls back to DuckDuckGo search if Tavily API key is missing or fails.

    Returns:
        dict: {"results": [...], "provider": "tavily" | "duckduckgo"}
    """
    if _tavily_client:
        logger.info("executing_search_with_tavily", query=query, depth=search_depth)
        try:
            response = await _tavily_client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_images=False,
            )
            # Standardize Tavily results output
            results = []
            for r in response.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                })
            return {"results": results, "provider": "tavily_search"}
        except Exception as e:
            logger.warning("tavily_search_failed_falling_back_to_ddg", error=str(e))
            # Fall through and let DuckDuckGo handle it

    if not settings.duckduckgo_enabled:
        logger.error("search_failed_no_providers_available")
        return {"results": [], "provider": "none"}

    logger.info("executing_search_with_ddg", query=query)
    try:
        results = []
        with DDGS() as ddgs:
            # DDGS is synchronous; we can wrap in run_in_executor for full async if needed,
            # but for simplicity we run it here as it blocks very briefly.
            ddg_results = list(ddgs.text(query, max_results=max_results))
            for res in ddg_results:
                results.append({
                    "title": res.get("title", ""),
                    "url": res.get("href", ""),
                    "content": res.get("body", ""),
                })
        return {"results": results, "provider": "duckduckgo_search"}
    except Exception as e:
        logger.error("failed_ddg_search", error=str(e))
        raise e  # Let tenacity retry if DDG fails
