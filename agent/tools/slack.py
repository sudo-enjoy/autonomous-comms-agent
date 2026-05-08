"""Slack webhook alerts.

POSTs a minimal block-kit message (header + section) to SLACK_WEBHOOK_URL.
Fails silently — a Slack outage or missing webhook should never crash the
orchestrator loop.
"""
from __future__ import annotations

import os

import requests

from agent.logging_setup import get_logger

log = get_logger(__name__)

HTTP_TIMEOUT_SECONDS = 10


def send_alert(headline: str, summary: str, link: str | None = None) -> None:
    """POST a header + section block-kit message to SLACK_WEBHOOK_URL.

    No-ops with a log warning if the env var is missing. Catches all request
    exceptions so the calling handler always returns successfully.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        log.warning(
            f"[SLACK] SLACK_WEBHOOK_URL not set; skipping alert: {headline!r}"
        )
        return

    body_text = summary
    if link:
        body_text = f"{summary}\n<{link}|Open in Gmail>"

    payload = {
        # `text` is the fallback shown in mobile push / notification preview.
        "text": headline,
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": headline, "emoji": False},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body_text},
            },
        ],
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except Exception as exc:
        log.warning(f"[SLACK] post failed: {exc}; continuing")
        return

    if resp.status_code >= 400:
        log.warning(
            f"[SLACK] webhook returned {resp.status_code}: {resp.text[:200]}"
        )
        return

    log.info(f"[SLACK] alert posted: {headline!r}")
