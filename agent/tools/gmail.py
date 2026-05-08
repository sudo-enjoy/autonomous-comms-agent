"""Gmail OAuth2 + read/draft helpers.

Single Gmail service instance per process (lazy). OAuth desktop flow:

1. Download an OAuth client (Desktop app) from Google Cloud Console and save
   it at `data/credentials.json` (path overridable via `GMAIL_CREDENTIALS_PATH`).
2. Run `python scripts/gmail_auth.py` once. A browser window opens; granting
   `gmail.modify` writes a refresh token to `data/gmail_token.json`.
3. Subsequent runs read the saved token and silently refresh as needed.

`mark_processed` applies the `capacity-guardian-handled` label, creating it
on first use. `list_unread` excludes already-handled messages.
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from agent.logging_setup import get_logger

log = get_logger(__name__)

# `gmail.modify` is the smallest single scope that covers list/read + drafts +
# labels. Using one scope avoids re-consent if we add features later.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

HANDLED_LABEL = "capacity-guardian-handled"


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


_service: Any = None
_handled_label_id: str | None = None


def _creds_path() -> Path:
    return Path(os.environ.get("GMAIL_CREDENTIALS_PATH", "data/credentials.json"))


def _token_path() -> Path:
    return Path(os.environ.get("GMAIL_TOKEN_PATH", "data/gmail_token.json"))


def _get_credentials() -> Credentials:
    """Load saved creds, refresh if expired, or run the OAuth desktop flow."""
    token_path = _token_path()
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
        return creds
    creds_path = _creds_path()
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Missing OAuth client at {creds_path}. Download it from Google "
            f"Cloud Console (Desktop app) and save it there, then run "
            f"`python scripts/gmail_auth.py` once to mint a token."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return creds


def _get_service() -> Any:
    global _service
    if _service is None:
        creds = _get_credentials()
        _service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _service


def _get_handled_label_id() -> str:
    """Return the label id, creating `capacity-guardian-handled` if needed."""
    global _handled_label_id
    if _handled_label_id:
        return _handled_label_id
    svc = _get_service()
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"] == HANDLED_LABEL:
            _handled_label_id = lbl["id"]
            return _handled_label_id
    new_label = (
        svc.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": HANDLED_LABEL,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    _handled_label_id = new_label["id"]
    log.info(f"Created Gmail label `{HANDLED_LABEL}` (id={_handled_label_id})")
    return _handled_label_id


def _decode_body(payload: dict) -> str:
    """Recursively walk a Gmail payload, prefer text/plain, fall back to text/html."""
    mime = payload.get("mimeType", "")
    if mime.startswith("text/plain"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    if mime.startswith("text/html"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def _parse_message(raw: dict) -> Message:
    headers_list = raw.get("payload", {}).get("headers", [])
    headers = {h["name"].lower(): h["value"] for h in headers_list}
    _, sender_email = parseaddr(headers.get("from", ""))
    reply_to_email: str | None = None
    if headers.get("reply-to"):
        _, parsed = parseaddr(headers["reply-to"])
        reply_to_email = parsed or None
    return Message(
        id=raw["id"],
        thread_id=raw["threadId"],
        sender=sender_email,
        subject=headers.get("subject", ""),
        body=_decode_body(raw.get("payload", {})),
        received_at=headers.get("date", ""),
        reply_to=reply_to_email,
    )


def list_unread(since_seconds: int = 3600) -> list[Message]:
    """Return unread messages from the last `since_seconds`, oldest first.

    Filters out anything already labeled `capacity-guardian-handled`.
    """
    svc = _get_service()
    handled_id = _get_handled_label_id()
    after_epoch = int(time.time()) - max(since_seconds, 1)
    query = f"is:unread after:{after_epoch} -label:{HANDLED_LABEL}"
    resp = svc.users().messages().list(userId="me", q=query, maxResults=50).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    raws: list[dict] = []
    for mid in ids:
        raw = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        if handled_id in raw.get("labelIds", []):
            continue
        raws.append(raw)
    raws.sort(key=lambda r: int(r.get("internalDate", "0")))
    return [_parse_message(r) for r in raws]


def get_thread(thread_id: str) -> list[Message]:
    """Return all messages on a thread, oldest first."""
    svc = _get_service()
    resp = (
        svc.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    msgs = resp.get("messages", [])
    msgs.sort(key=lambda r: int(r.get("internalDate", "0")))
    return [_parse_message(m) for m in msgs]


def create_draft(thread_id: str, to: str, subject: str, body: str) -> str:
    """Create a reply draft on the given thread. Returns the Gmail draft id.

    Callers should pass `message.reply_to or message.sender` as `to`.
    """
    svc = _get_service()
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = to
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
    draft = (
        svc.users()
        .drafts()
        .create(
            userId="me",
            body={"message": {"raw": raw, "threadId": thread_id}},
        )
        .execute()
    )
    return draft["id"]


def mark_processed(message_id: str) -> None:
    """Apply the `capacity-guardian-handled` label, creating it if necessary."""
    svc = _get_service()
    label_id = _get_handled_label_id()
    svc.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id]},
    ).execute()
