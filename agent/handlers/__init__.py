"""Shared utilities for the lead/client handlers."""
from __future__ import annotations


VOICE_GREETING_CLICHES = (
    "hope this finds you well",
    "hope you're well",
    "i hope this email finds",
)


def voice_violations(draft_body: str) -> list[str]:
    """Return a list of voice-rule violations found in `draft_body`.

    Detection-only — no enforcement, no rewrite. Used by both lead and client
    handlers to log a per-policy / per-subclass violation map. Step 9 prompt
    iteration uses this data to decide where rules need strengthening.
    """
    violations: list[str] = []
    if "—" in draft_body:
        violations.append("em-dash")
    if "!" in draft_body:
        violations.append("exclamation")
    body_lower = draft_body.lower()
    if any(p in body_lower for p in VOICE_GREETING_CLICHES):
        violations.append("greeting-cliche")
    return violations
