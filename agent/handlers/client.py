"""Client handler — drafts replies for ongoing client threads."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HandlerResult:
    gmail_draft_id: str
    # `policy` is overloaded across handlers so the orchestrator log line stays
    # uniform: lead → '1'..'5'; client → status_request | scope_change |
    # blocker | approval | smalltalk | other.
    policy: str
    draft_subject: str
    draft_body: str


def run(message: dict, decision) -> HandlerResult:
    """Sub-classify the message, pull voice samples, draft a reply, optionally Slack."""
    raise NotImplementedError
