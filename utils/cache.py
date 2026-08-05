"""
Caching layer for search results and LLM calls.
Uses diskcache for zero-dependency file-based caching.
"""

from __future__ import annotations

import functools
import hashlib
from typing import Any, Callable

from config.settings import settings
from utils.logger import logger

_cache = None

if settings.cache_enabled:
    if settings.cache_backend == "diskcache":
        try:
            import diskcache

            _cache = diskcache.Cache(settings.cache_directory)
            logger.info("diskcache_initialized", directory=settings.cache_directory)
        except ImportError:
            logger.warning("diskcache_not_installed_caching_disabled")
    elif settings.cache_backend == "redis":
        logger.warning("redis_cache_not_yet_implemented")


def _generate_cache_key(func_name: str, *args: Any, **kwargs: Any) -> str:
    """Generate a stable string key from function arguments."""
    key_parts = [func_name]
    for arg in args:
        key_parts.append(str(arg))
    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}={v}")

    raw_key = "|".join(key_parts)
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def with_cache(ttl: int = settings.cache_ttl_seconds) -> Callable:
    """
    Decorator to cache the results of an async function.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _cache:
                return await func(*args, **kwargs)

            cache_key = _generate_cache_key(func.__name__, *args, **kwargs)
            cached_val = _cache.get(cache_key)

            if cached_val is not None:
                logger.debug("cache_hit", func=func.__name__, key=cache_key)
                return cached_val

            logger.debug("cache_miss", func=func.__name__, key=cache_key)
            result = await func(*args, **kwargs)

            # Don't cache exceptions or empty results if we can help it
            if result:
                _cache.set(cache_key, result, expire=ttl)

            return result

        return wrapper

    return decorator
