"""
Centralized settings loader.

Reads config/settings.yaml for all non-secret configuration and
environment variables (via .env) for API keys / secrets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Locate project root & load YAML
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_YAML_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def _load_yaml() -> Dict[str, Any]:
    """Load settings.yaml, returning empty dict if missing."""
    if _YAML_PATH.exists():
        with open(_YAML_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


_yaml_cfg = _load_yaml()

# Convenience helpers to reach into nested YAML safely
_llm = _yaml_cfg.get("llm", {})
_search = _yaml_cfg.get("search", {})
_research = _yaml_cfg.get("research", {})
_cache = _yaml_cfg.get("cache", {})
_memory = _yaml_cfg.get("memory", {})
_tracing = _yaml_cfg.get("tracing", {})
_logging = _yaml_cfg.get("logging", {})
_api = _yaml_cfg.get("api", {})
_embeddings = _yaml_cfg.get("embeddings", {})


# ---------------------------------------------------------------------------
# Pydantic Settings (env vars supply secrets; YAML supplies the rest)
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """
    Unified configuration.

    * API keys come from environment variables / .env
    * Everything else defaults from settings.yaml
    """

    # --- API Keys (secrets — env only) ---
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    tavily_api_key: str = ""
    langsmith_api_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""

    # --- LLM ---
    model_name: str = Field(
        default=_llm.get("providers", {}).get("gemini", {}).get("model", "gemini-2.5-flash")
    )
    llm_providers: Dict[str, Any] = Field(default_factory=lambda: _llm.get("providers", {}))
    llm_roles: Dict[str, Any] = Field(default_factory=lambda: _llm.get("roles", {}))
    mock_mode: bool = Field(default=_llm.get("mock_mode", False))

    # --- Embeddings ---
    embeddings_provider: str = Field(default=_embeddings.get("provider", "local"))
    embeddings_model: str = Field(default=_embeddings.get("model", "all-MiniLM-L6-v2"))

    # --- Search ---
    tavily_enabled: bool = Field(default=_search.get("tavily_enabled", True))
    duckduckgo_enabled: bool = Field(default=_search.get("duckduckgo_enabled", True))
    max_search_results: int = Field(default=_search.get("max_results_per_query", 5))

    # --- Research pipeline ---
    max_iterations: int = Field(default=_research.get("max_iterations", 3))
    max_replan_iterations: int = Field(default=_research.get("max_replan_iterations", 2))
    enable_critic: bool = Field(default=_research.get("enable_critic", True))
    enable_editor: bool = Field(default=_research.get("enable_editor", True))
    require_human_approval: bool = Field(default=_research.get("require_human_approval", False))
    hil_checkpoints: List[str] = Field(
        default_factory=lambda: _research.get("hil_checkpoints", ["after_planning"])
    )

    # --- Cache ---
    cache_enabled: bool = Field(default=_cache.get("enabled", True))
    cache_backend: str = Field(default=_cache.get("backend", "diskcache"))
    cache_ttl_seconds: int = Field(default=_cache.get("ttl_seconds", 3600))
    cache_directory: str = Field(default=_cache.get("directory", ".cache/research"))
    cache_redis_url: str = Field(default=_cache.get("redis_url", "redis://localhost:6379/0"))

    # --- Memory ---
    memory_enabled: bool = Field(default=_memory.get("enabled", True))
    memory_backend: str = Field(default=_memory.get("backend", "sqlite"))
    memory_db_path: str = Field(default=_memory.get("db_path", "data/research_memory.db"))

    # --- Tracing ---
    tracing_enabled: bool = Field(default=_tracing.get("enabled", False))
    tracing_provider: str = Field(default=_tracing.get("provider", "langsmith"))

    # --- Logging ---
    log_level: str = Field(default=_logging.get("level", "INFO"))

    # --- API ---
    enable_streaming: bool = Field(default=_api.get("enable_streaming", True))
    api_version: str = Field(default=_api.get("version", "3.0.0"))

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
