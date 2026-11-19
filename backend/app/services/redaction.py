"""Removes obvious secrets from text before it is sent to an AI provider.

This is a safety net rather than a guarantee. Files that look like secrets are excluded
earlier, and truly private code should use the project's local AI only setting.
"""

from __future__ import annotations

import re

_PLACEHOLDER = "[REDACTED]"

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL)
_ASSIGNED_SECRET = re.compile(
    r"""(?P<name>\b[\w.$-]*(?:password|passwd|pwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token|private[_-]?key)"""
    r"""[\w.-]*['"]?\s*(?:=>|=|:)\s*)(?P<quote>['"])(?P<value>[^'"\n]{4,})(?P=quote)""",
    re.IGNORECASE,
)
_TOKENS = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|gsk_[A-Za-z0-9]{20,}|"
    r"AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
)
_URL_PASSWORD = re.compile(r"""(?P<head>\b[a-z][a-z0-9+.-]*://[^\s:/@'"]+:)[^\s/@'"]+(?P<tail>@)""", re.IGNORECASE)


def _blank_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('name')}{quote}{_PLACEHOLDER}{quote}"


def redact_secrets(text: str) -> str:
    text = _PRIVATE_KEY.sub(_PLACEHOLDER, text)
    text = _ASSIGNED_SECRET.sub(_blank_assignment, text)
    text = _TOKENS.sub(_PLACEHOLDER, text)
    return _URL_PASSWORD.sub(lambda m: f"{m.group('head')}{_PLACEHOLDER}{m.group('tail')}", text)
