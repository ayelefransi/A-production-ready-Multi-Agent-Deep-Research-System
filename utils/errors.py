"""
Pipeline errors that are final and safe to show to the user.

Agent nodes retry transient failures (rate limits, timeouts). These errors
are different: retrying cannot fix them, and their message is written by us,
so it is shown to the client as-is instead of a generic failure.
"""

from __future__ import annotations


class PipelineError(RuntimeError):
    """A non-retryable failure with a user-facing explanation."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class LLMUnavailableError(PipelineError):
    """No LLM provider has a usable API key."""


class NoEvidenceError(PipelineError):
    """The researchers retrieved no sources, so no grounded report can be written."""


def is_retryable(exc: BaseException) -> bool:
    """
    Whether an agent node should retry after *exc*.

    Only ordinary errors are retried. A PipelineError is final, and anything
    that is not an ``Exception`` (notably ``asyncio.CancelledError``, raised
    when a run is cancelled or times out) must propagate immediately:
    retrying it would make the run impossible to stop.
    """
    return isinstance(exc, Exception) and not isinstance(exc, PipelineError)


def describe_failure(exc: BaseException) -> str:
    """
    A user-facing reason for an unexpected failure, or '' if none is known.

    Provider errors are recognised by their status code or wording so the
    dashboard can say what to fix. The provider's own message is never
    passed through: it can contain request details.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    text = str(exc).lower()
    if "model_not_found" in text or ("model" in text and "does not exist" in text) or (
        status == 404 and "model" in text
    ):
        return ("An AI provider no longer offers the configured model. Update the model id "
                "under llm.providers in config/settings.yaml and restart the server.")
    if status in (401, 403) or "invalid api key" in text or "invalid_api_key" in text             or "api key not valid" in text or "permission_denied" in text:
        return ("An AI provider rejected its API key. Check the key in .env and restart "
                "the server.")
    if status == 429 or "rate limit" in text or "resource_exhausted" in text or "quota" in text:
        return ("The AI providers are rate limited or out of quota right now. Wait a few "
                "minutes and run it again, or add another provider key.")
    if status == 413 or "request too large" in text or "context length" in text:
        return ("The research material was too large for the configured model. Try a "
                "narrower question.")
    if status in (500, 502, 503, 504) or "unavailable" in text or "overloaded" in text:
        return "An AI provider is temporarily unavailable. Run it again in a few minutes."
    if "timeout" in text or "timed out" in text:
        return "An AI provider did not answer in time. Run it again."
    return ""
