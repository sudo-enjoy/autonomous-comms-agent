"""Gmail OAuth2 + read/draft helpers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Message:
    id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received_at: str
    # Gmail's Reply-To header may differ from From (mailing lists, support
    # aliases, personal-address-behind-forwarder). Replies must prefer this.
    reply_to: str | None = None


def list_unread(since_seconds: int = 3600) -> list[Message]:
    """Return unread messages from the last `since_seconds`, oldest first."""
    raise NotImplementedError


def get_thread(thread_id: str) -> list[Message]:
    raise NotImplementedError


def create_draft(thread_id: str, to: str, subject: str, body: str) -> str:
    """Create a reply draft on the given thread. Returns the Gmail draft id.

    Callers should pass `message.reply_to or message.sender` as `to`.
    """
    raise NotImplementedError


def mark_processed(message_id: str) -> None:
    """Apply the `capacity-guardian-handled` label, creating it if necessary."""
    raise NotImplementedError
