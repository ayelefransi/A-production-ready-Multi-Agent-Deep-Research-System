"""
Observability and tracing setup (LangSmith / LangFuse).
"""

import os
from config.settings import settings
from utils.logger import logger


def setup_tracing() -> None:
    """
    Initialize tracing if enabled in settings.
    Should be called once at application startup.
    """
    if not settings.tracing_enabled:
        return

    if settings.tracing_provider == "langsmith":
        if not settings.langsmith_api_key:
            logger.warning("langsmith_enabled_but_no_api_key_found")
            return
            
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGCHAIN_PROJECT"] = "Multi-Agent Deep Research"
        
        logger.info("langsmith_tracing_enabled")
        
    elif settings.tracing_provider == "langfuse":
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            logger.warning("langfuse_enabled_but_missing_keys")
            return
            
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        # If deploying to EU or custom host:
        # os.environ["LANGFUSE_HOST"] = "https://cloud.langfuse.com"
        
        # LangChain native callback integration
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        logger.info("langfuse_tracing_enabled")
    else:
        logger.warning("unknown_tracing_provider", provider=settings.tracing_provider)
