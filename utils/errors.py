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
