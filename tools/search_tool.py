"""
Web search tool: Tavily when a key is configured, otherwise a keyless
metasearch fallback (ddgs) that tries several engines in turn.
"""

from __future__ import annotations

import asyncio

from ddgs import DDGS
from tavily import AsyncTavilyClient
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from utils.config_check import clean_key
from utils.logger import logger

# Engines tried, in order, when no Tavily key is configured.
KEYLESS_BACKENDS = ("auto", "duckduckgo", "yahoo", "mojeek")

# Initialize client if key is provided
_tavily_client = None
if settings.tavily_enabled and clean_key(settings.tavily_api_key):
    try:
        _tavily_client = AsyncTavilyClient(api_key=clean_key(settings.tavily_api_key))
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
    if settings.mock_mode:
        return {
            "results": [
                {
                    "title": f"Mock Source {i}",
                    "url": f"https://example.com/source{i}",
                    "content": f"Deterministic mock search content {i} for: {query}",
                }
                for i in (1, 2)
            ],
            "provider": "mock_search",
        }

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
            logger.warning("tavily_search_failed_falling_back_to_keyless", error=str(e))

    if not settings.duckduckgo_enabled:
        logger.error("search_failed_no_providers_available")
        return {"results": [], "provider": "none"}

    logger.info("executing_keyless_search", query=query)

    def _keyless_search() -> list:
        # Public engines throttle automated traffic unpredictably, so try a
        # few in turn. ddgs raises when an engine returns nothing.
        for backend in KEYLESS_BACKENDS:
            try:
                found = list(DDGS(timeout=10).text(query, max_results=max_results, backend=backend))
            except Exception as exc:  # noqa: BLE001 - try the next engine
                logger.debug("keyless_search_backend_failed", backend=backend, error=str(exc))
                continue
            if found:
                return found
        return []

    # ddgs is synchronous; keep it off the event loop so parallel researchers
    # and the SSE stream are not blocked.
    raw_results = await asyncio.to_thread(_keyless_search)
    if not raw_results:
        logger.warning("keyless_search_returned_nothing", query=query)
    results = [
        {
            "title": res.get("title", ""),
            "url": res.get("href", ""),
            "content": res.get("body", ""),
        }
        for res in raw_results
    ]
    return {"results": results, "provider": "keyless_search"}
