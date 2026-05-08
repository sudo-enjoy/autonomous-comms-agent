"""Lead handler — drafts replies to new inbound inquiries."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HandlerResult:
    gmail_draft_id: str
    policy: str
    draft_subject: str
    draft_body: str


def run(message: dict, decision) -> HandlerResult:
    """Pick one of 5 policies, draft a reply, optionally update capacity / Slack."""
    raise NotImplementedError
