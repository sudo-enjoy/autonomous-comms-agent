"""Pre-seed the Capacity Google Sheet with example projects for the demo.

Run once after setting CAPACITY_SHEET_ID in .env and granting Editor access
to the service-account email. Idempotent: clears existing data rows first
(keeps the header) and rewrites a fresh demo state.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent import capacity, memory  # noqa: E402
from agent.capacity import HEADERS, WORKSHEET_NAME, _get_worksheet  # noqa: E402


# Project | Client | Status | Estimated Days | Queue Position | Last Update
SEED_ROWS: list[list] = [
    ["Pamphlet AI agent",        "Pamphlet Co",        "Active",   35, "", "2026-04-15"],
    ["Lighthouse Triage",        "Lighthouse Health",  "Active",   28, "", "2026-04-22"],
    ["Aurora Compliance Bot",    "Aurora Fintech",     "Waitlist", 25, 1,  "2026-05-01"],
    ["Northwind Support Agent",  "Northwind Corp",     "Waitlist", 20, 2,  "2026-05-03"],
    ["Greenfield Scoping",       "Greenfield Labs",    "Scoping",  0,  "", "2026-05-05"],
]

# One plausible primary contact per seeded company. Without these, the very
# first email from any seeded company hits the lead/client no-man's-land bug
# (Router can't see the Capacity sheet, only the SQLite clients table). The
# pairing matches `Client` column in SEED_ROWS by index.
SEED_CONTACTS: list[tuple[str, str]] = [
    ("pm@pamphlet.co",          "Pamphlet Co"),
    ("ops@lighthouse.health",   "Lighthouse Health"),
    ("cto@aurora-fintech.io",   "Aurora Fintech"),
    ("alice@northwind-corp.com", "Northwind Corp"),
    ("dev@greenfieldlabs.io",   "Greenfield Labs"),
]


def main() -> None:
    ws = _get_worksheet()

    # Ensure the header row matches HEADERS exactly.
    header_row = ws.row_values(1)
    if header_row != HEADERS:
        ws.update("A1:F1", [HEADERS])
        print(f"Wrote header: {HEADERS}")
    else:
        print("Header already correct.")

    # Clear any existing data rows below the header.
    existing = ws.get_all_values()
    if len(existing) > 1:
        ws.batch_clear([f"A2:F{len(existing)}"])
        print(f"Cleared {len(existing) - 1} pre-existing data rows.")

    # Append fresh seed rows.
    for row in SEED_ROWS:
        ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"Seeded {len(SEED_ROWS)} rows in `{WORKSHEET_NAME}` tab.")

    print()
    print("Current state:")
    for r in capacity.read_capacity():
        print(f"  {r}")
    print()
    print(f"current_load_days() = {capacity.current_load_days()}")
    print(f"get_eta('any') = {capacity.get_eta('any')!r}")
    print()

    # Seed the SQLite clients table so the Router can resolve future emails
    # from these companies on the first hit.
    memory.init_db()
    seeded = 0
    for email, company in SEED_CONTACTS:
        memory.find_or_create_client(email=email, company=company)
        seeded += 1
    print(f"Seeded {seeded} primary contacts in clients table:")
    for email, company in SEED_CONTACTS:
        print(f"  {email}  ({company})")


if __name__ == "__main__":
    main()
