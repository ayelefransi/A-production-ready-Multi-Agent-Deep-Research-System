"""
Provider-agnostic LLM router with role-based provider fallback.

Usage:
    from utils.llm_router import get_llm
    llm = get_llm("planner")                                  # plain chat model
    llm = get_llm("planner", structured_output=ResearchPlan)  # with Pydantic output

Fallback happens at two levels:

* configuration: a provider without a usable API key is skipped;
* runtime: if the chosen provider fails a call (rate limit, outage, a
  structured-output parse error), the same call is retried on the next
  configured provider via LangChain's ``with_fallbacks``.
"""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional, Type

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from config.settings import settings
from utils.errors import LLMUnavailableError
from utils.logger import logger

# Values copied from .env.example ("your_groq_api_key_here") are not keys.
_PLACEHOLDER_KEY = re.compile(r"^(your[_-].*|.*[_-]here|changeme|x{3,}|<.*>)$", re.IGNORECASE)

NO_PROVIDER_MESSAGE = (
    "No LLM provider is configured. Add GROQ_API_KEY, GEMINI_API_KEY or "
    "OPENROUTER_API_KEY to the .env file and restart the server."
)


def _api_key_for(provider_name: str) -> str:
    """Resolve a provider's API key from the environment or settings ('' if unset)."""
    api_key_env = settings.llm_providers.get(provider_name, {}).get("api_key_env", "")
    if not api_key_env:
        return ""
    api_key = os.environ.get(api_key_env, "") or getattr(settings, api_key_env.lower(), "")
    api_key = (api_key or "").strip()
    return "" if _PLACEHOLDER_KEY.match(api_key) else api_key


def available_providers() -> List[str]:
    """Names of configured providers that have a usable API key."""
    return [name for name in settings.llm_providers if _api_key_for(name)]


def llm_ready() -> bool:
    """True when a research run can reach at least one model."""
    return settings.mock_mode or bool(available_providers())


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _build_provider(provider_name: str, temperature: float) -> BaseChatModel:
    """
    Return a LangChain ChatModel for the given provider name.
    Raises ``ValueError`` if the provider is unknown or unconfigured.
    """
    provider_cfg = settings.llm_providers.get(provider_name, {})
    model_name = provider_cfg.get("model", "")
    api_key = _api_key_for(provider_name)

    if not api_key:
        raise ValueError(
            f"No API key found for provider '{provider_name}' "
            f"(expected env var '{provider_cfg.get('api_key_env', '')}')"
        )

    if provider_name == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=api_key,
        )

    if provider_name == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model_name,
            temperature=temperature,
            api_key=api_key,
        )

    if provider_name == "openrouter":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=api_key,
            openai_api_base="https://openrouter.ai/api/v1",
        )

    raise ValueError(f"Unknown LLM provider: {provider_name}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_llm(
    role: str,
    structured_output: Optional[Type[BaseModel]] = None,
) -> Any:
    """
    Return a runnable model for the given agent *role*.

    When ``settings.mock_mode`` is ``True``, returns a deterministic
    ``MockChatModel`` (tests, CI, UI demos). Otherwise returns the role's
    primary provider with the remaining configured providers as runtime
    fallbacks.

    Raises ``LLMUnavailableError`` when no provider has a usable key.
    """
    if settings.mock_mode:
        from utils.mock_llm import MockChatModel

        mock = MockChatModel(role=role)
        logger.info("llm_router_mock", role=role, provider="mock", model="mock")
        if structured_output is not None:
            return mock.with_structured_output(structured_output)
        return mock

    role_cfg = settings.llm_roles.get(role, {})
    chain: List[str] = [role_cfg.get("primary", "gemini")] + list(role_cfg.get("fallback", []))
    temperature = role_cfg.get("temperature", 0.2)

    runnables = []
    selected: List[str] = []
    for provider_name in dict.fromkeys(chain):  # de-duplicate, keep order
        try:
            llm = _build_provider(provider_name, temperature)
        except ValueError as exc:
            logger.warning(
                "llm_router_provider_skipped", role=role, provider=provider_name, reason=str(exc)
            )
            continue
        runnables.append(
            llm.with_structured_output(structured_output) if structured_output is not None else llm
        )
        selected.append(provider_name)

    if not runnables:
        raise LLMUnavailableError(NO_PROVIDER_MESSAGE)

    logger.info(
        "llm_router_selected",
        role=role,
        provider=selected[0],
        model=settings.llm_providers.get(selected[0], {}).get("model", "?"),
        runtime_fallbacks=selected[1:],
    )
    primary, fallbacks = runnables[0], runnables[1:]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary
