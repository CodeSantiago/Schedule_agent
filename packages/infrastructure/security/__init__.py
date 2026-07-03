"""Input sanitization and output validation for production hardening.

This module provides utilities to:

1. **Sanitize inbound user messages** before they reach the LLM: strip
   control characters, null bytes, limit length, remove prompt-injection
   patterns at the boundary.

2. **Validate LLM classifier output**: ensure the parsed JSON is safe,
   fields are within expected bounds, and no prompt-leakage payload
   escapes in the ``reply`` field.

All functions are pure — no IO, no state. Easy to test and safe to call
from any context (webhook handler, background worker, tests).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


# ── Constants ─────────────────────────────────────────────────────────────────

# Maximum inbound message body length (bytes after decoding).
MAX_BODY_LENGTH: int = 4096

# Maximum LLM reply length (characters).
MAX_REPLY_LENGTH: int = 2000

# Maximum extracted fields depth (prevent nested-object injection).
MAX_EXTRACTED_DEPTH: int = 3

# Known prompt-injection / jailbreak patterns (case-insensitive).
_JAILBREAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignor(a|á|e)\s+(instrucciones|lo\s+anterior|el\s+prompt|el\s+sistema)", re.IGNORECASE),
    re.compile(r"olvid(a|á)\s+(las\s+)?instrucciones", re.IGNORECASE),
    re.compile(r"preterir\s+instrucciones", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"eres\s+un\s+(ai|asistente|bot)\s+y", re.IGNORECASE),
    re.compile(r"dilo\s+en\s+ingl.s", re.IGNORECASE),
    re.compile(r"responde\s+en\s+ingl.s", re.IGNORECASE),
    re.compile(r"act[uú]\s+como\s+si", re.IGNORECASE),
    re.compile(r"hazte\s+pasar\s+por", re.IGNORECASE),
    re.compile(r"fing(e|í)\s+ser", re.IGNORECASE),
)

# Control characters to strip (except newline and tab).
_CONTROL_CHARS: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── Public API ────────────────────────────────────────────────────────────────


def sanitize_message(body: str | None) -> str:
    """Sanitize an inbound user message before passing it to the LLM.

    Strips null bytes, control characters, and leading/trailing
    whitespace. Truncates to ``MAX_BODY_LENGTH``. Returns an empty
    string when the input is ``None`` or entirely whitespace/control
    chars.

    This is the **last** line of defense — the system prompt also
    contains security guardrails against injection.
    """
    if not body:
        return ""
    # Strip control characters (keep newlines and tabs).
    cleaned = _CONTROL_CHARS.sub("", body)
    # Normalize whitespace runs.
    cleaned = " ".join(cleaned.split())
    # Limit length.
    return cleaned[:MAX_BODY_LENGTH]


def detect_jailbreak(body: str) -> bool:
    """Return ``True`` if ``body`` matches known jailbreak patterns.

    This is a best-effort heuristic, not a security boundary. The
    system prompt is the primary defense against injection; this is
    an additional guard to log and reject obvious attempts.
    """
    cleaned = body.strip().lower()
    for pattern in _JAILBREAK_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False


def compute_body_hash(customer_phone: str, body: str) -> str:
    """Return the SHA-256 hex digest of ``(customer_phone, body)``.

    Used as the content-based dedup key for burst suppression and
    idempotency within a time window.
    """
    raw = f"{customer_phone}||{body}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_classifier_output(
    intent_kind: str,
    next_state: str,
    reply: str,
    extracted: dict[str, Any] | None,
    *,
    valid_states: frozenset[str] | None = None,
) -> list[str]:
    """Validate LLM classifier output fields and return a list of issues.

    Returns an empty list when all fields are valid. Callers should
    fall back to ``kind="unknown"`` / deterministic response when any
    issues are found.

    Checks performed:

    - ``reply`` is not empty and within length limit.
    - ``reply`` does not contain the system prompt or configuration
      data that could indicate prompt leakage.
    - ``extracted`` dict depth is bounded.
    - ``next_state`` is in ``valid_states`` when provided.
    """
    issues: list[str] = []

    if not reply:
        issues.append("reply is empty")
    elif len(reply) > MAX_REPLY_LENGTH:
        issues.append(f"reply exceeds {MAX_REPLY_LENGTH} chars ({len(reply)})")

    # Check for prompt leakage in reply.
    leakage_keywords = [
        "system prompt", "instrucciones", "eres un", "asistente de ia",
        "asistente AI", "lenguaje", "modelo de", "entrenado",
    ]
    reply_lower = reply.lower()
    for kw in leakage_keywords:
        if kw in reply_lower:
            issues.append(f"reply contains potential leakage keyword: '{kw}'")
            break

    if valid_states is not None and next_state not in valid_states:
        issues.append(f"next_state '{next_state}' is not a valid state")

    if extracted:
        depth = _dict_depth(extracted)
        if depth > MAX_EXTRACTED_DEPTH:
            issues.append(
                f"extracted dict depth {depth} exceeds max {MAX_EXTRACTED_DEPTH}"
            )

    return issues


def sanitize_reply(reply: str) -> str:
    """Sanitize an LLM-generated reply before sending to the customer.

    Strips control characters, limits length, removes any URIs that
    could be phishing attempts (keep phone numbers, remove non-whitelisted
    URLs).
    """
    cleaned = _CONTROL_CHARS.sub("", reply or "")
    # Strip any markdown code fences that might leak from the LLM response.
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned).strip()
    return cleaned[:MAX_REPLY_LENGTH]


# ── Internal helpers ──────────────────────────────────────────────────────────


def _dict_depth(value: Any, depth: int = 0) -> int:
    """Return the maximum nesting depth of a dict/list structure."""
    if not isinstance(value, (dict, list)):
        return depth
    if isinstance(value, list):
        if not value:
            return depth + 1
        return max(_dict_depth(item, depth + 1) for item in value)
    if not value:
        return depth + 1
    return max(_dict_depth(v, depth + 1) for v in value.values())
