"""
Provider-agnostic LLM router with role-based provider fallback.

Usage:
    from utils.llm_router import get_llm
    llm = get_llm("planner")                              # plain chat model
    llm = get_llm("planner", structured_output=ResearchPlan)  # with Pydantic output
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, Type

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from utils.logger import logger


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
    api_key_env = provider_cfg.get("api_key_env", "")

    # Resolve API key from env / settings
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        # Try the settings attribute directly (e.g. settings.gemini_api_key)
        attr = api_key_env.lower()
        api_key = getattr(settings, attr, "")

    if not api_key:
        raise ValueError(
            f"No API key found for provider '{provider_name}' "
            f"(expected env var '{api_key_env}')"
        )

    if provider_name == "gemini":
        # Set the env var that langchain-google-genai expects
        os.environ["GOOGLE_API_KEY"] = api_key
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
        )

    if provider_name == "groq":
        os.environ["GROQ_API_KEY"] = api_key
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
) -> BaseChatModel:
    """
    Return a configured LangChain chat model for the given agent *role*.

    When ``settings.mock_mode`` is ``True``, returns a deterministic
    ``MockChatModel`` that produces valid structured output.

    Parameters
    ----------
    role : str
        One of the role keys defined in ``settings.yaml`` → ``llm.roles``
        (e.g. ``"planner"``, ``"researcher"``, ``"critic"``).
    structured_output : Type[BaseModel], optional
        If provided, wraps the model via ``.with_structured_output()``.
    """

    # ── Mock mode (tests / CI) ────────────────────────────────────────────
    if settings.mock_mode:
        from utils.mock_llm import MockChatModel

        mock = MockChatModel(role=role)
        logger.info(
            "llm_router_mock",
            role=role,
            provider="mock",
            model="mock",
        )
        if structured_output is not None:
            return mock.with_structured_output(structured_output)
        return mock

    # ── Real provider chain ───────────────────────────────────────────────
    role_cfg = settings.llm_roles.get(role, {})
    primary = role_cfg.get("primary", "gemini")
    fallbacks: List[str] = role_cfg.get("fallback", [])
    temperature = role_cfg.get("temperature", 0.2)

    providers_to_try = [primary] + fallbacks

    last_error: Optional[Exception] = None

    for provider_name in providers_to_try:
        try:
            llm = _build_provider(provider_name, temperature)
            logger.info(
                "llm_router_selected",
                role=role,
                provider=provider_name,
                model=settings.llm_providers.get(provider_name, {}).get("model", "?"),
            )
            if structured_output is not None:
                return llm.with_structured_output(structured_output)
            return llm
        except ValueError as exc:
            # Missing API key → skip to fallback
            logger.warning(
                "llm_router_provider_skipped",
                role=role,
                provider=provider_name,
                reason=str(exc),
            )
            last_error = exc
            continue

    raise RuntimeError(
        f"No LLM provider available for role '{role}'. "
        f"Tried: {providers_to_try}. Last error: {last_error}"
    )


def get_llm_with_retry(
    role: str,
    structured_output: Optional[Type[BaseModel]] = None,
    max_attempts: int = 3,
) -> BaseChatModel:
    """
    Same as ``get_llm`` but wraps the *invocation* in a Tenacity retry that
    will try fallback providers on rate-limit / server errors.

    This is useful when you want automatic retry-with-fallback at call time,
    not just at initialization time.
    """
    # For now, delegation — real per-call retry is handled inside the nodes
    # via their own Tenacity decorators.
    return get_llm(role, structured_output)
