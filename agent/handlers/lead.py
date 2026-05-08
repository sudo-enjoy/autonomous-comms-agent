"""Lead handler — drafts replies for new inbound inquiries.

Picks one of the 5 policies in `prompts/lead.md`, then creates a real Gmail
draft and records it in memory.db. Capacity sheet (step 6) and Slack (step 8)
are not wired here — the handler still produces the right structured output
(`should_alert_slack`, `capacity_update`) so wiring them in later steps is
purely additive.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent import llm, memory
from agent.logging_setup import get_logger
from agent.router import RouterDecision
from agent.tools import gmail
from agent.tools.gmail import Message

log = get_logger(__name__)

LEAD_MODEL = "claude-sonnet-4.6"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "lead.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text()

DRAFT_LEAD_REPLY_TOOL = {
    "name": "draft_lead_reply",
    "description": (
        "Emit the lead-handler decision: chosen policy, reasoning, draft "
        "subject + body in agency voice, and side-effect intents. Always "
        "called exactly once per message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "policy": {
                "type": "string",
                "enum": ["1", "2", "3", "4", "5"],
                "description": (
                    "1=high-value urgent, 2=standard build, 3=low fit, "
                    "4=needs more info, 5=already in queue."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining why this policy.",
            },
            "draft_subject": {
                "type": "string",
                "description": "Subject for the reply, sentence case.",
            },
            "draft_body": {
                "type": "string",
                "description": "Body of the reply, in agency voice.",
            },
            "should_alert_slack": {
                "type": "boolean",
                "description": "True only for policy 1 (high-value urgent).",
            },
            "capacity_update": {
                "type": "object",
                "description": (
                    "What to do with the capacity sheet. Wired in step 6; "
                    "the handler still records intent today."
                ),
                "properties": {
                    "action": {"type": "string", "enum": ["add", "none"]},
                    "estimated_days": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Days of work if action=add; 0 otherwise.",
                    },
                },
                "required": ["action", "estimated_days"],
            },
        },
        "required": [
            "policy",
            "reasoning",
            "draft_subject",
            "draft_body",
            "should_alert_slack",
            "capacity_update",
        ],
    },
}


@dataclass
class HandlerResult:
    gmail_draft_id: str
    policy: str
    draft_subject: str
    draft_body: str


# Step 5: capacity is not wired yet. Step 6 replaces this with a real summary
# from `agent/capacity.py`. Keeping the function tells the LLM what to expect
# and means the prompt's "estimated X weeks" line still produces sensible text.
def _capacity_context() -> str:
    return (
        "CAPACITY CHECK: not yet wired in this build. For drafting, treat "
        "the current waitlist as roughly 6-8 weeks. Use phrasing like "
        "'estimated 6-8 weeks from acceptance' — never commit to specific dates."
    )


def _build_user_message(message: Message, decision: RouterDecision) -> str:
    parts = [
        "INCOMING EMAIL:",
        f"FROM: {message.sender}",
        f"SUBJECT: {message.subject}",
        f"BODY:",
        message.body,
        "",
        f"ROUTER DECISION: track={decision.track} confidence={decision.confidence:.2f}",
        f"  reasoning: {decision.reasoning}",
        "",
        _capacity_context(),
    ]
    return "\n".join(parts)


def run(message: Message, decision: RouterDecision) -> HandlerResult:
    """Pick a policy, draft a reply, create a Gmail draft, record it.

    Raises on failure — the orchestrator wraps per-message exceptions.
    """
    user_msg = _build_user_message(message, decision)
    out = llm.call_with_tool(
        model=LEAD_MODEL,
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tool_schema=DRAFT_LEAD_REPLY_TOOL,
    )

    policy = out["policy"]
    reasoning = out["reasoning"]
    draft_subject = out["draft_subject"]
    draft_body = out["draft_body"]
    should_alert = bool(out["should_alert_slack"])
    cap_update = out["capacity_update"]

    log.info(
        f"[LEAD] policy={policy} alert_slack={should_alert} "
        f"capacity_action={cap_update['action']} | {reasoning}"
    )

    # Voice rule violations recorded as data, not enforcement. By step 9 we
    # want a per-policy violation map to know which policies leak which
    # patterns most often. Fixing the prompt is step-9 work; observing now
    # is free.
    violations: list[str] = []
    if "—" in draft_body:
        violations.append("em-dash")
    if "!" in draft_body:
        violations.append("exclamation")
    body_lower = draft_body.lower()
    if any(
        p in body_lower
        for p in (
            "hope this finds you well",
            "hope you're well",
            "i hope this email finds",
        )
    ):
        violations.append("greeting-cliche")
    if violations:
        log.warning(
            f"[LEAD] voice violations in policy={policy} draft: "
            f"{', '.join(violations)}"
        )

    # Step 5: Slack and capacity sheet are not wired. Log the intents so the
    # demo viewer can see they're being decided correctly.
    if should_alert:
        log.info("[LEAD] would send Slack alert (wiring lands in step 8)")
    if cap_update["action"] == "add":
        log.info(
            f"[LEAD] would add to capacity sheet ({cap_update['estimated_days']} days; "
            f"wiring lands in step 6)"
        )

    # Real Gmail draft.
    to_addr = message.reply_to or message.sender
    draft_id = gmail.create_draft(
        thread_id=message.thread_id,
        to=to_addr,
        subject=draft_subject,
        body=draft_body,
    )

    # Persist for the voice-sample loop (step 7) and downstream eval/audit.
    thread_db_id = memory.record_thread(
        gmail_thread_id=message.thread_id,
        client_id=None,
        subject=message.subject,
        summary=None,
        last_seen_message_id=message.id,
        track="lead",
    )
    memory.record_draft(
        thread_id=thread_db_id,
        original=message.body,
        draft=draft_body,
        gmail_draft_id=draft_id,
        policy=policy,
    )

    return HandlerResult(
        gmail_draft_id=draft_id,
        policy=policy,
        draft_subject=draft_subject,
        draft_body=draft_body,
    )
