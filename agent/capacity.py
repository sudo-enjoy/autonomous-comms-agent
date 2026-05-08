"""Google Sheets operations for the capacity tracker."""
from __future__ import annotations


def read_capacity() -> list[dict]:
    """Return all rows from the Capacity sheet as dicts."""
    raise NotImplementedError


def add_to_waitlist(client: str, project: str, estimated_days: int) -> int:
    """Append a row in `Waitlist` status. Returns its queue position."""
    raise NotImplementedError


def get_eta(client_or_project: str) -> str:
    """Return a phrase like 'estimated 5 weeks from acceptance'."""
    raise NotImplementedError


def current_load_days() -> int:
    """Sum of `Estimated Days` across rows in `Active` or `Waitlist` status."""
    raise NotImplementedError
