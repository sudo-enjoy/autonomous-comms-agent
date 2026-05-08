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

from agent import capacity, llm, memory
from agent.handlers import voice_violations
from agent.logging_setup import get_logger
from agent.router import RouterDecision
from agent.tools import gmail, slack
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
            "client_name": {
                "type": "string",
                "description": (
                    "The sender's company name. Used to seed our clients "
                    "table so future emails route correctly. For policy 5, "
                    "use the matched company name from CAPACITY context. "
                    "For other policies, use the company stated in the email "
                    "or inferred from the sender's domain. Required for all "
                    "policies except 3 (low fit / decline)."
                ),
            },
            "should_alert_slack": {
                "type": "boolean",
                "description": "True only for policy 1 (high-value urgent).",
            },
            "capacity_update": {
                "type": "object",
                "description": (
                    "What to do with the capacity sheet. Set action=add for "
                    "policy 2 (standard build) when adding to the waitlist. "
                    "Set action=none for policies 1, 3, 4, 5."
                ),
                "properties": {
                    "action": {"type": "string", "enum": ["add", "none"]},
                    "estimated_days": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Days of work if action=add; 0 otherwise.",
                    },
                    "project_name": {
                        "type": "string",
                        "description": (
                            "Short project label for the sheet (only when "
                            "action=add). 2-5 words, descriptive."
                        ),
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
            "client_name",
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


def _infer_client_from_email(sender: str) -> str:
    """Fallback when the LLM doesn't supply a client_name. Take the second-level
    domain as a title-cased best guess (e.g. `bob@northwind-corp.com` →
    `Northwind-Corp`). Returns 'Unknown' for malformed senders.
    """
    if "@" not in sender:
        return "Unknown"
    domain = sender.split("@", 1)[1]
    root = domain.split(".")[0]
    return root.title() or "Unknown"


def _capacity_context() -> str:
    """Real capacity summary from the Google Sheet.

    Includes total load (Active + Waitlist days) and every existing entry so
    the LLM can do company-name matching for policy 5 (already in queue).
    On any sheet read failure, falls back to a safe placeholder so the
    handler never crashes on transient Google API issues.
    """
    try:
        rows = capacity.read_capacity()
        load = capacity.current_load_days()
        eta = capacity.get_eta("any")
    except Exception as exc:
        log.warning(f"capacity read failed: {exc}; using fallback context")
        return (
            "CAPACITY: sheet read failed. Treat the current waitlist as "
            "roughly 6-8 weeks. Use phrasing like 'estimated 6-8 weeks from "
            "acceptance' and never commit to specific dates."
        )

    lines = [
        "CAPACITY:",
        f"  current load: {load} days across Active + Waitlist ({eta})",
        f"  entries in capacity sheet ({len(rows)}):",
    ]
    for r in rows:
        lines.append(
            f"    - project={r.get('Project')!r} client={r.get('Client')!r} "
            f"status={r.get('Status')} days={r.get('Estimated Days')} "
            f"queue_pos={r.get('Queue Position') or '-'}"
        )
    lines.append(
        "  If the sender's company name appears above, prefer policy 5 "
        "(already in queue) and surface their position + ETA."
    )
    return "\n".join(lines)


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

    violations = voice_violations(draft_body)
    if violations:
        log.warning(
            f"[LEAD] voice violations in policy={policy} draft: "
            f"{', '.join(violations)}"
        )

    # Resolve a single canonical client name for both seeding and capacity-add.
    client_name = (out.get("client_name") or "").strip()
    if not client_name:
        client_name = _infer_client_from_email(message.sender)

    # Seed the clients table for every non-decline policy. Policy 3 declines
    # don't need a client record. Doing this for policies 1/2/4/5 means a
    # follow-up email from the same company will route cleanly through the
    # Router's domain match — closes the lead/client no-man's-land gap that
    # otherwise affects policy 5 most acutely (no `add` to trigger seeding).
    if policy in {"1", "2", "4", "5"}:
        try:
            memory.find_or_create_client(
                email=message.sender, company=client_name
            )
        except Exception as exc:
            log.warning(f"[LEAD] client record seeding failed: {exc}; continuing")

    # Slack alert for policy 1 (high-value urgent).
    if should_alert:
        slack.send_alert(
            headline=f"High-value lead: {message.subject}",
            summary=(
                f"*From:* {message.sender}\n"
                f"*Why this is high-value:* {reasoning}"
            ),
            link=f"https://mail.google.com/mail/u/0/#inbox/{message.thread_id}",
        )

    # Capacity sheet — append a Waitlist row when the LLM says add.
    if cap_update["action"] == "add":
        days = int(cap_update.get("estimated_days") or 0)
        project_name = (cap_update.get("project_name") or "").strip()
        if not project_name:
            project_name = "AI build (TBD)"
        try:
            queue_pos = capacity.add_to_waitlist(
                client=client_name,
                project=project_name,
                estimated_days=days,
            )
            log.info(
                f"[LEAD] added to waitlist: client={client_name!r} "
                f"project={project_name!r} days={days} position={queue_pos}"
            )
        except Exception as exc:
            log.warning(f"[LEAD] capacity update failed: {exc}; continuing")

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
