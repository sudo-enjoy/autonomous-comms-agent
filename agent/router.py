"""Router agent — classifies incoming email into one of four tracks.

Pre-resolves client matches Python-side (exact email, then domain) and includes
the result as structured context for the LLM. The LLM still owns the final call
because the right track also depends on the body (e.g. a former client could
write about a brand-new project — that's a `lead`, not a `client`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent import llm, memory
from agent.logging_setup import get_logger
from agent.tools.gmail import Message

log = get_logger(__name__)

# build.md spec: claude-opus-4-7 (Anthropic native dash format).
# ppq.ai exposes the same model as `claude-opus-4.7` (dot format).
ROUTER_MODEL = "claude-opus-4.7"
PROMPT_PATH = Path(__file__).parent / "prompts" / "router.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text()

# OpenAI-compatible tool schema: `parameters` (not `input_schema`). The llm
# wrapper handles the `{"type": "function", "function": {...}}` envelope.
DISPATCH_EMAIL_TOOL = {
    "name": "dispatch_email",
    "description": (
        "Classify the incoming email into one of four tracks and dispatch. "
        "Always called exactly once per message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "track": {
                "type": "string",
                "enum": ["lead", "client", "internal", "ignore"],
                "description": "Which downstream handler should process this email.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in this classification, 0.0 to 1.0.",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the decision.",
            },
            "matched_client_id": {
                "type": ["integer", "null"],
                "description": (
                    "If track == 'client', the id of the matched client row "
                    "from the provided CLIENT MATCH context. Null otherwise."
                ),
            },
        },
        "required": ["track", "confidence", "reasoning", "matched_client_id"],
    },
}


@dataclass
class RouterDecision:
    track: str  # 'lead' | 'client' | 'internal' | 'ignore'
    confidence: float
    reasoning: str
    matched_client_id: int | None


def _domain(email: str) -> str | None:
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[1].strip().lower() or None


def _is_internal(sender_email: str) -> bool:
    sender_domain = _domain(sender_email)
    if not sender_domain:
        return False
    internal = os.environ.get("AGENCY_INTERNAL_DOMAIN", "").strip().lower()
    return bool(internal) and sender_domain == internal


def _find_matching_clients(sender_email: str) -> list[dict]:
    """Return clients matching by exact email first; fall back to domain match."""
    if not sender_email or "@" not in sender_email:
        return []
    sender_email = sender_email.strip().lower()
    sender_domain = sender_email.split("@", 1)[1]

    conn = memory._connection()
    rows = conn.execute(
        "SELECT id, email, name, company, domain FROM clients WHERE email = ?",
        (sender_email,),
    ).fetchall()
    if rows:
        return [dict(r) for r in rows]
    rows = conn.execute(
        "SELECT id, email, name, company, domain FROM clients WHERE domain = ?",
        (sender_domain,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_user_message(
    message: Message, matched: list[dict], internal: bool
) -> str:
    parts = [
        f"FROM: {message.sender}",
        f"SUBJECT: {message.subject}",
        f"BODY:\n{message.body}",
        "",
    ]
    if internal:
        parts.append(
            "CLIENT MATCH: sender's domain matches our internal team domain "
            "(treat as `internal` unless the body clearly indicates otherwise)."
        )
    elif matched:
        parts.append("CLIENT MATCH (one or more known clients):")
        for c in matched:
            parts.append(
                f"  - id={c['id']} email={c['email']} "
                f"company={c['company']!r} domain={c['domain']}"
            )
    else:
        parts.append("CLIENT MATCH: none — sender is not a known client.")
    return "\n".join(parts)


def classify(message: Message) -> RouterDecision:
    """Run the router against a single incoming message.

    Forces `track='ignore'` if the model emits confidence < 0.6.
    On any LLM error, returns a safe ignore decision so the loop continues.
    """
    matched = _find_matching_clients(message.sender)
    internal = _is_internal(message.sender)
    user_msg = _build_user_message(message, matched, internal)

    try:
        out = llm.call_with_tool(
            model=ROUTER_MODEL,
            system=SYSTEM_PROMPT,
            user_message=user_msg,
            tool_schema=DISPATCH_EMAIL_TOOL,
        )
    except Exception as exc:
        log.warning(f"router LLM call failed: {exc}; defaulting to ignore")
        return RouterDecision(
            track="ignore",
            confidence=0.0,
            reasoning=f"router error: {exc}",
            matched_client_id=None,
        )

    track = out["track"]
    confidence = float(out["confidence"])
    reasoning = out["reasoning"]
    matched_client_id = out.get("matched_client_id")

    # Apply the confidence floor.
    if confidence < 0.6 and track != "ignore":
        log.info(
            f"forcing ignore: confidence {confidence:.2f} < 0.6 "
            f"(model said track={track})"
        )
        track = "ignore"

    # Defensive: if the model emitted track='client' but supplied no id (or an
    # id that's not in our match set), fall back to lead. This catches the
    # model hallucinating a client match.
    if track == "client":
        match_ids = {c["id"] for c in matched}
        if matched_client_id is None or matched_client_id not in match_ids:
            log.warning(
                f"model said track=client but matched_client_id={matched_client_id} "
                f"not in {match_ids}; downgrading to lead"
            )
            track = "lead"
            matched_client_id = None

    log.info(
        f"[ROUTER] track={track} confidence={confidence:.2f} | {reasoning}"
    )
    return RouterDecision(
        track=track,
        confidence=confidence,
        reasoning=reasoning,
        matched_client_id=matched_client_id,
    )
