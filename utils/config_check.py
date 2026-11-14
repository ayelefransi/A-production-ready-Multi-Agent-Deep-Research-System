"""
Configuration diagnostics.

Checks the shape of each configured credential against the format its
provider issues, so a mis-pasted key is reported by name at start-up and on
the setup banner instead of surfacing later as an opaque 401. Only formats
and lengths are inspected; key values are never logged or returned.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from config.settings import settings

# Values that are clearly not credentials ("...", "your_key_here", "<key>").
_PLACEHOLDER = re.compile(r"^(\.+|your[_-].*|.*[_-]here|changeme|x{3,}|<.*>)$", re.IGNORECASE)

# setting name -> (label, accepted prefixes, where to find it)
_KEY_FORMATS: dict[str, Tuple[str, Tuple[str, ...], str]] = {
    "groq_api_key": ("GROQ_API_KEY", ("gsk_",), "console.groq.com, API Keys (starts with gsk_)"),
    # Google issues more than one key format, so only placeholders are flagged.
    "gemini_api_key": ("GEMINI_API_KEY", ("",), "aistudio.google.com, Get API key"),
    "openrouter_api_key": ("OPENROUTER_API_KEY", ("sk-or-",), "openrouter.ai, Keys (starts with sk-or-)"),
    "tavily_api_key": ("TAVILY_API_KEY", ("tvly-",), "app.tavily.com (starts with tvly-)"),
    "supabase_anon_key": (
        "SUPABASE_ANON_KEY", ("sb_publishable_", "eyJ"),
        "Supabase, Project Settings, API Keys: the publishable key (sb_publishable_...) "
        "or the legacy anon key (eyJ...)",
    ),
    "supabase_service_role_key": (
        "SUPABASE_SERVICE_ROLE_KEY", ("sb_secret_", "eyJ"),
        "Supabase, Project Settings, API Keys: the secret key (sb_secret_...) "
        "or the legacy service_role key (eyJ...)",
    ),
}


def clean_key(value: str) -> str:
    """Return a usable credential, or '' for empty and placeholder values."""
    value = (value or "").strip().strip("\"'")
    if len(value) < 8 or _PLACEHOLDER.match(value):
        return ""
    return value


def key_problems() -> List[str]:
    """Human-readable descriptions of credentials that look wrong."""
    problems = []
    for setting, (label, prefixes, where) in _KEY_FORMATS.items():
        raw = (getattr(settings, setting, "") or "").strip()
        if not raw:
            continue
        if not clean_key(raw):
            problems.append(f"{label} is a placeholder, not a key. Find it at {where}.")
        elif not raw.strip("\"'").startswith(prefixes):
            problems.append(
                f"{label} does not look like a valid key for that service "
                f"(wrong format). Find it at {where}."
            )
    anon, service = settings.supabase_anon_key.strip(), settings.supabase_service_role_key.strip()
    if anon and anon == service:
        problems.append("SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY are the same value; "
                        "they must be two different keys.")
    return problems
