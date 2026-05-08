"""SQLite operations for clients, threads, drafts, and voice samples."""
from __future__ import annotations


def init_db() -> None:
    """Create all tables if they do not exist."""
    raise NotImplementedError


def find_or_create_client(
    email: str,
    name: str | None = None,
    company: str | None = None,
) -> int:
    raise NotImplementedError


def find_thread(gmail_thread_id: str) -> dict | None:
    raise NotImplementedError


def record_thread(
    gmail_thread_id: str,
    client_id: int | None,
    subject: str | None,
    summary: str | None,
    last_seen_message_id: str | None,
    track: str | None,
) -> int:
    raise NotImplementedError


def is_processed(message_id: str) -> bool:
    raise NotImplementedError


def mark_processed(message_id: str) -> None:
    raise NotImplementedError


def record_draft(
    thread_id: int,
    original: str,
    draft: str,
    gmail_draft_id: str,
    policy: str | None = None,
) -> int:
    raise NotImplementedError


def record_sent(draft_id: int, final_text: str) -> None:
    """Diff `final_text` against the saved agent_draft and store the diff.

    If `final_text` differs from the agent draft, also save it as a voice sample
    for the associated client.
    """
    raise NotImplementedError


def get_voice_samples(client_id: int, limit: int = 2) -> list[str]:
    """Return the most recent `final_sent` values for the client."""
    raise NotImplementedError
