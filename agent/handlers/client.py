"""Client handler — drafts replies for ongoing client threads.

Sub-classifies the incoming message into one of six categories and produces a
voice-matched reply. Always pulls the last 2 voice samples for the matched
client (populated by `memory.record_sent` when the user edits a draft) and
includes them in the prompt as few-shot style guides. Always creates a real
Gmail draft. Slack alerts fire on `scope_change` and `blocker` (wired in step 8).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent import capacity, llm, memory
from agent.handlers import voice_violations
from agent.logging_setup import get_logger
from agent.router import RouterDecision
from agent.tools import gmail
from agent.tools.gmail import Message

log = get_logger(__name__)

CLIENT_MODEL = "claude-sonnet-4.6"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "client.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text()

SUBCLASSES = (
    "status_request",
    "scope_change",
    "blocker",
    "approval",
    "smalltalk",
    "other",
)
ALERT_SUBCLASSES = {"scope_change", "blocker"}

DRAFT_CLIENT_REPLY_TOOL = {
    "name": "draft_client_reply",
    "description": (
        "Emit the client-handler decision: sub-classification, reasoning, "
        "draft subject + body matching the client's voice samples, and "
        "Slack alert intent. Always called exactly once per message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subclass": {
                "type": "string",
                "enum": list(SUBCLASSES),
                "description": (
                    "status_request: they want a project update. "
                    "scope_change: they're requesting something new/different. "
                    "blocker: something is preventing progress. "
                    "approval: they're signing off on something. "
                    "smalltalk: scheduling, pleasantries, logistics. "
                    "other: best judgment, conservative."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the sub-classification.",
            },
            "draft_subject": {
                "type": "string",
                "description": "Subject for the reply, sentence case unless voice samples say otherwise.",
            },
            "draft_body": {
                "type": "string",
                "description": (
                    "Body of the reply. Match the voice samples' tone, length, "
                    "and conventions. Per agency style: no em-dashes, no "
                    "exclamation marks, no 'hope this finds you well'."
                ),
            },
            "should_alert_slack": {
                "type": "boolean",
                "description": "True only for scope_change or blocker; false otherwise.",
            },
        },
        "required": [
            "subclass",
            "reasoning",
            "draft_subject",
            "draft_body",
            "should_alert_slack",
        ],
    },
}


@dataclass
class HandlerResult:
    gmail_draft_id: str
    # `policy` overload across handlers: lead uses '1'..'5', client uses one
    # of SUBCLASSES. Same field name keeps the orchestrator log line uniform.
    policy: str
    draft_subject: str
    draft_body: str


def _client_capacity_context(client_company: str) -> str:
    """Pull capacity rows matching this client only — keeps the prompt focused."""
    try:
        rows = capacity.read_capacity()
    except Exception as exc:
        log.warning(f"capacity read failed: {exc}; using fallback context")
        return "CAPACITY: sheet unavailable; do not promise specific dates."

    target = client_company.lower().strip()
    matches = [
        r
        for r in rows
        if target and target in (r.get("Client") or "").lower()
    ]
    if not matches:
        return f"CAPACITY: no project on the sheet for {client_company!r}."

    lines = [f"CAPACITY ({len(matches)} project row(s) for {client_company!r}):"]
    for r in matches:
        lines.append(
            f"  - project={r.get('Project')!r} status={r.get('Status')} "
            f"days={r.get('Estimated Days')} queue={r.get('Queue Position') or '-'} "
            f"updated={r.get('Last Update')}"
        )
    return "\n".join(lines)


def _voice_context(samples: list[str]) -> str:
    """Format voice samples as few-shot style examples, or note their absence."""
    if not samples:
        return (
            "VOICE SAMPLES: none on file yet. Match the original message's "
            "tone, length, and casualness. Default to direct and short."
        )
    lines = ["VOICE SAMPLES (most-recent first; match this style precisely):"]
    for i, s in enumerate(samples, 1):
        snippet = s if len(s) <= 600 else s[:600] + "  [...truncated]"
        lines.append(f"---SAMPLE {i}---")
        lines.append(snippet)
        lines.append("---END SAMPLE---")
    return "\n".join(lines)


def _thread_context(thread_id: str, current_message_id: str) -> str:
    """Recent thread history (last 3 prior messages), excluding the current one."""
    try:
        msgs = gmail.get_thread(thread_id)
    except Exception as exc:
        log.warning(f"thread fetch failed: {exc}; continuing without thread context")
        return "THREAD CONTEXT: unavailable."
    prior = [m for m in msgs if m.id != current_message_id][-3:]
    if not prior:
        return "THREAD CONTEXT: this is the first message in the thread."
    lines = [f"THREAD CONTEXT (last {len(prior)} prior message(s), oldest first):"]
    for m in prior:
        body_preview = m.body if len(m.body) <= 400 else m.body[:400] + "  [...truncated]"
        lines.append(f"--- from={m.sender} subject={m.subject!r} ---")
        lines.append(body_preview)
    return "\n".join(lines)


def _build_user_message(
    message: Message,
    decision: RouterDecision,
    voice_samples: list[str],
    capacity_text: str,
    thread_text: str,
) -> str:
    parts = [
        "INCOMING EMAIL:",
        f"FROM: {message.sender}",
        f"SUBJECT: {message.subject}",
        "BODY:",
        message.body,
        "",
        f"ROUTER DECISION: track=client confidence={decision.confidence:.2f}",
        f"  matched_client_id={decision.matched_client_id}",
        f"  reasoning: {decision.reasoning}",
        "",
        capacity_text,
        "",
        thread_text,
        "",
        _voice_context(voice_samples),
    ]
    return "\n".join(parts)


def run(message: Message, decision: RouterDecision) -> HandlerResult:
    """Sub-classify, draft a voice-matched reply, create the Gmail draft, persist."""
    if decision.matched_client_id is None:
        raise RuntimeError("client handler invoked without matched_client_id")

    conn = memory._connection()
    client_row = conn.execute(
        "SELECT email, company FROM clients WHERE id = ?",
        (decision.matched_client_id,),
    ).fetchone()
    if client_row is None:
        raise RuntimeError(
            f"matched_client_id={decision.matched_client_id} not found in clients"
        )
    client_company = client_row["company"] or "Unknown"

    voice_samples = memory.get_voice_samples(decision.matched_client_id, limit=2)
    capacity_text = _client_capacity_context(client_company)
    thread_text = _thread_context(message.thread_id, message.id)
    user_msg = _build_user_message(
        message, decision, voice_samples, capacity_text, thread_text
    )

    out = llm.call_with_tool(
        model=CLIENT_MODEL,
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tool_schema=DRAFT_CLIENT_REPLY_TOOL,
    )

    subclass = out["subclass"]
    reasoning = out["reasoning"]
    draft_subject = out["draft_subject"]
    draft_body = out["draft_body"]
    should_alert = bool(out["should_alert_slack"])

    log.info(
        f"[CLIENT] subclass={subclass} alert_slack={should_alert} "
        f"voice_samples={len(voice_samples)} | {reasoning}"
    )

    violations = voice_violations(draft_body)
    if violations:
        log.warning(
            f"[CLIENT] voice violations in subclass={subclass} draft: "
            f"{', '.join(violations)}"
        )

    # Sanity: alert flag must align with subclass per spec.
    expected_alert = subclass in ALERT_SUBCLASSES
    if should_alert != expected_alert:
        log.warning(
            f"[CLIENT] should_alert_slack={should_alert} disagrees with "
            f"subclass={subclass} (expected {expected_alert}); using subclass"
        )
        should_alert = expected_alert

    if should_alert:
        log.info("[CLIENT] would send Slack alert (wiring lands in step 8)")

    to_addr = message.reply_to or message.sender
    draft_id = gmail.create_draft(
        thread_id=message.thread_id,
        to=to_addr,
        subject=draft_subject,
        body=draft_body,
    )

    thread_db_id = memory.record_thread(
        gmail_thread_id=message.thread_id,
        client_id=decision.matched_client_id,
        subject=message.subject,
        summary=None,
        last_seen_message_id=message.id,
        track="client",
    )
    memory.record_draft(
        thread_id=thread_db_id,
        original=message.body,
        draft=draft_body,
        gmail_draft_id=draft_id,
        policy=subclass,
    )

    return HandlerResult(
        gmail_draft_id=draft_id,
        policy=subclass,
        draft_subject=draft_subject,
        draft_body=draft_body,
    )
