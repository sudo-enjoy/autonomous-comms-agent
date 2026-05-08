"""Main loop — poll Gmail, route, dispatch to handlers."""
from __future__ import annotations

import argparse


def run_once(dry_run: bool = False) -> None:
    """Single pass: fetch unread, route each, dispatch to the right handler."""
    raise NotImplementedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capacity Guardian orchestrator")
    parser.add_argument("--once", action="store_true", help="Single pass, then exit.")
    parser.add_argument("--verbose", action="store_true", help="DEBUG-level logging.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Gmail draft creation and Slack alerts.",
    )
    return parser.parse_args()


def main() -> None:
    # Parse first so argparse can handle --help before any stubbed work runs.
    args = parse_args()
    del args  # full loop wired up in later steps
    raise NotImplementedError


if __name__ == "__main__":
    main()
