"""
Web fetch/scrape tool to extract full article text from URLs.

URLs come from third-party search results, so every hop is validated before
it is requested: only http(s), no credentials, and no address that resolves
to a private, loopback, link-local or otherwise non-public network (SSRF).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from config.settings import settings
from utils.logger import logger

MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2_000_000

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


class UnsafeURLError(ValueError):
    """Raised when a URL must not be fetched."""


def _is_public_ip(address: str) -> bool:
    return ipaddress.ip_address(address).is_global


async def validate_public_url(url: str) -> None:
    """Raise ``UnsafeURLError`` unless *url* is an http(s) URL on a public host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Unsupported URL scheme: {parts.scheme or 'none'}")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    addresses = {host} if _looks_like_ip(host) else None

    if addresses is None:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Host does not resolve: {host}") from exc
        addresses = {info[4][0] for info in infos}

    for address in addresses:
        if not _is_public_ip(address):
            raise UnsafeURLError(f"Host resolves to a non-public address: {host}")


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def web_fetch_async(url: str, timeout: int = 10) -> str:
    """
    Fetch a web page and extract its main text content using trafilatura.

    Returns the extracted text, or an empty string when the page is unsafe,
    unreachable, too large or has no extractable text. Never raises: full
    text is an optional enrichment for the Researcher.
    """
    if settings.mock_mode:
        return ""

    try:
        import trafilatura
    except ImportError:
        logger.warning("trafilatura_unavailable_web_fetch_disabled")
        return ""

    logger.info("fetching_web_content", url=url)

    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers=_HEADERS, follow_redirects=False
        ) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                await validate_public_url(current)
                response = await client.get(current)
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                break
            else:
                raise UnsafeURLError("Too many redirects")

            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                logger.debug("web_fetch_response_too_large", url=url)
                return ""

            extracted = await asyncio.to_thread(
                trafilatura.extract,
                response.text,
                include_links=False,
                include_images=False,
                include_tables=False,
            )
            return extracted or ""

    except UnsafeURLError as exc:
        logger.warning("web_fetch_blocked", url=url, reason=str(exc))
        return ""
    except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
        logger.warning("web_fetch_failed", url=url, error=str(exc))
        return ""
