"""
Web fetch/scrape tool to extract full article text from URLs.
"""

from __future__ import annotations

import httpx
import trafilatura
from tenacity import retry, stop_after_attempt, wait_exponential

from utils.logger import logger


@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(2))
async def web_fetch_async(url: str, timeout: int = 10) -> str:
    """
    Fetches a web page and extracts its main text content using trafilatura.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        Extracted text content, or empty string if extraction fails.
    """
    logger.info("fetching_web_content", url=url)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            html_content = response.text

            # Extract main text using trafilatura
            extracted = trafilatura.extract(
                html_content,
                include_links=False,
                include_images=False,
                include_tables=False,
            )

            if extracted:
                return extracted
            
            logger.debug("trafilatura_extraction_empty", url=url)
            return ""

    except Exception as e:
        logger.warning("web_fetch_failed", url=url, error=str(e))
        raise e
