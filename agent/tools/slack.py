"""Slack webhook alerts."""
from __future__ import annotations


def send_alert(headline: str, summary: str, link: str | None = None) -> None:
    """POST a minimal block-kit message to SLACK_WEBHOOK_URL.

    Fail silently with a log warning if the env var is missing.
    """
    raise NotImplementedError
