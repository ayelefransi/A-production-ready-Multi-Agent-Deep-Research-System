"""Tool tests: SSRF guard on web fetch, offline behaviour in mock mode."""

import pytest

from config.settings import settings
from tools.search_tool import execute_search_async
from tools.web_fetch import UnsafeURLError, validate_public_url, web_fetch_async


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://127.0.0.1/admin",
        "http://localhost:8000/health",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://[::1]/",
        "http://user:pass@93.184.216.34/",
        "http:///no-host",
    ],
)
async def test_non_public_urls_are_blocked(url):
    with pytest.raises(UnsafeURLError):
        await validate_public_url(url)


async def test_public_ip_literal_is_allowed():
    await validate_public_url("https://93.184.216.34/page")


async def test_web_fetch_returns_empty_instead_of_raising_for_blocked_url(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    assert await web_fetch_async("http://127.0.0.1:9/secret") == ""


async def test_mock_mode_search_is_offline_and_deterministic():
    first = await execute_search_async("anything")
    second = await execute_search_async("anything")
    assert first == second
    assert first["provider"] == "mock_search" and len(first["results"]) == 2
