"""Strip credential-shaped text before it reaches SQLite or the UI.

This is a backstop, not a security boundary: it runs at the persistence
edge so that *every* stored preview, event payload, and error message goes
through it regardless of which caller produced the text. Callers are still
expected not to deliberately log secrets.

The patterns deliberately favour precision over recall. A generic
"high-entropy string" rule would redact git SHAs, artifact hashes, and
UUIDs — all of which operators need to read — so only shapes that are
unambiguously credentials are matched. Known-provider prefixes come first,
then key/value forms where the *name* declares the value secret.
"""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[redacted]"

# Cap on any single stored string. Keeps one runaway agent response from
# bloating the database; the full text lives in the artifact store.
MAX_PERSISTED_CHARS = 32_768

# Key names whose value is secret regardless of what the value looks like.
_SECRET_KEY_NAMES = (
    "api[_-]?key",
    "secret[_-]?key",
    "secret",
    "access[_-]?key",
    "private[_-]?key",
    "session[_-]?token",
    "refresh[_-]?token",
    "access[_-]?token",
    "auth[_-]?token",
    "token",
    "password",
    "passwd",
    "pwd",
    "authorization",
    "cookie",
    "set-cookie",
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM blocks: replace the whole armoured body, not just the header.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # Provider-prefixed tokens. Anchored on the prefix so ordinary prose
    # containing "sk-" or "ghp_" without a token body is left alone.
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    # `Authorization: Bearer <token>` and bare `Bearer <token>`.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{12,}=*"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{12,}=*"),
)

# `key = value`, `key: value`, `key="value"`, `--key value`, `KEY=value`.
# The name group is kept so the operator still sees *which* setting was
# redacted; only the value is replaced.
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)(?P<name>\b(?:" + "|".join(_SECRET_KEY_NAMES) + r")\b)"
    r"(?P<sep>\s*[:=]\s*|\s+)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s\"',;}\]]{4,})"
    r"(?P=quote)"
)


def redact_text(value: str) -> str:
    """Return `value` with credential-shaped substrings replaced."""
    if not value:
        return value
    redacted = value
    for pattern in _PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    redacted = _KEY_VALUE_PATTERN.sub(
        lambda m: f"{m.group('name')}{m.group('sep')}{REDACTED}", redacted
    )
    return redacted


def redact_and_cap(value: str | None, *, limit: int = MAX_PERSISTED_CHARS) -> str | None:
    """Redact, then truncate with an explicit marker so nothing looks whole."""
    if value is None:
        return None
    redacted = redact_text(value)
    if len(redacted) <= limit:
        return redacted
    return redacted[:limit] + f"\n… [truncated, {len(redacted) - limit} more chars]"


def redact_payload(payload: Any) -> Any:
    """Recursively redact a JSON-shaped structure.

    Dict keys matching a secret name have their value replaced outright —
    a structured `{"api_key": "..."}` never reaches the value patterns as
    text, so it needs its own check.
    """
    if isinstance(payload, str):
        return redact_text(payload)
    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and _is_secret_key(key):
                result[key] = REDACTED
            else:
                result[key] = redact_payload(value)
        return result
    if isinstance(payload, (list, tuple)):
        return [redact_payload(item) for item in payload]
    return payload


_SECRET_KEY_RE = re.compile(r"(?i)^(?:" + "|".join(_SECRET_KEY_NAMES) + r")$")


def _is_secret_key(key: str) -> bool:
    return _SECRET_KEY_RE.match(key.strip()) is not None
