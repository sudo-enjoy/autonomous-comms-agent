"""Shared utilities for the lead/client handlers."""
from __future__ import annotations

import re


VOICE_GREETING_CLICHES = (
    "hope this finds you well",
    "hope you're well",
    "i hope this email finds",
)


def voice_violations(draft_body: str) -> list[str]:
    """Return a list of voice-rule violations found in `draft_body`.

    Detection-only. Run BEFORE `normalize_voice` so the raw drift signal is
    still observable in logs even after sanitization is applied.
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


def normalize_voice(text: str) -> str:
    """Strip residual em-dash drift the prompt didn't fully suppress.

    Belt-and-suspenders for the prompt fix landed in step 9. Voice violations
    are logged from the raw text BEFORE this runs (so the drift signal isn't
    lost), but the user-visible output (Gmail draft + persisted agent_draft)
    goes through this filter.

    - " — " (spaced, used as clause connector) → ". " then re-capitalize.
    - "—" (no spaces, used as hyphen replacement) → " - ".
    """
    text = text.replace(" — ", ". ").replace("—", " - ")
    text = re.sub(
        r"(\.\s+)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )
    return text
