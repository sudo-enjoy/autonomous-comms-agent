"""Google Sheets operations for the capacity tracker.

Sheet: a Google Sheet (id in CAPACITY_SHEET_ID) with a tab named `Capacity`
containing columns:
    Project | Client | Status | Estimated Days | Queue Position | Last Update

Status enum: Active | Waitlist | Scoping | Done.

Auth: service account JSON at GOOGLE_SERVICE_ACCOUNT_PATH. The service account
email must be granted Editor access on the sheet.

A single worksheet handle is cached at module level (lazy). gspread is
synchronous; the orchestrator is single-threaded; no thread-safety concern.
"""
from __future__ import annotations

import datetime
import os
from typing import Any

import gspread

from agent.logging_setup import get_logger

log = get_logger(__name__)

WORKSHEET_NAME = "Capacity"
HEADERS = [
    "Project",
    "Client",
    "Status",
    "Estimated Days",
    "Queue Position",
    "Last Update",
]
ACTIVE_STATUSES = {"Active", "Waitlist"}

_worksheet: Any = None  # gspread.Worksheet, but typed Any to avoid import-time strictness


def _get_worksheet() -> Any:
    global _worksheet
    if _worksheet is not None:
        return _worksheet
    sa_path = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_PATH", "data/service_account.json"
    )
    sheet_id = os.environ.get("CAPACITY_SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError(
            "CAPACITY_SHEET_ID is not set. Add it to .env (see .env.example)."
        )
    gc = gspread.service_account(filename=sa_path)
    sh = gc.open_by_key(sheet_id)
    try:
        _worksheet = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        _worksheet = sh.add_worksheet(
            title=WORKSHEET_NAME, rows=200, cols=len(HEADERS)
        )
        _worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")
        log.info(f"Created `{WORKSHEET_NAME}` tab with header row")
    return _worksheet


def _today() -> str:
    return datetime.date.today().isoformat()


def read_capacity() -> list[dict]:
    """Return all data rows as dicts keyed by header name."""
    return _get_worksheet().get_all_records()


def add_to_waitlist(client: str, project: str, estimated_days: int) -> int:
    """Append a Waitlist row, return its queue position (1-indexed)."""
    ws = _get_worksheet()
    rows = ws.get_all_records()
    waitlist_count = sum(1 for r in rows if r.get("Status") == "Waitlist")
    queue_position = waitlist_count + 1
    ws.append_row(
        [project, client, "Waitlist", int(estimated_days), queue_position, _today()],
        value_input_option="USER_ENTERED",
    )
    log.info(
        f"[CAPACITY] added to waitlist: client={client!r} project={project!r} "
        f"days={estimated_days} position={queue_position}"
    )
    return queue_position


def current_load_days() -> int:
    """Sum of `Estimated Days` across rows where Status in {Active, Waitlist}."""
    total = 0
    for r in read_capacity():
        if r.get("Status") in ACTIVE_STATUSES:
            try:
                total += int(r.get("Estimated Days") or 0)
            except (ValueError, TypeError):
                pass
    return total


def get_eta(client_or_project: str) -> str:
    """Return phrase like 'estimated N weeks from acceptance'.

    The phrase reflects total backlog (Active + Waitlist days, ceiling-divided
    by 7). Per spec we never commit to specific dates.
    """
    load = current_load_days()
    weeks = max(1, (load + 6) // 7)  # round up
    return f"estimated {weeks} weeks from acceptance"
