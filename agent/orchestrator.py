"""Main loop — poll Gmail, route, dispatch to handlers."""
from __future__ import annotations

import argparse
import contextlib
import time

from dotenv import load_dotenv

from agent import memory, router
from agent.dry_run import dry_run
from agent.handlers import client as client_handler
from agent.handlers import lead as lead_handler
from agent.logging_setup import get_logger, init_root_logger
from agent.tools import gmail

POLL_SECONDS = 60

log = get_logger(__name__)


def _process_message(msg: gmail.Message) -> None:
    """Classify and dispatch one message. Per-message try/except is the caller's
    job — this function raises on any failure so the caller can log and skip."""
    if memory.is_processed(msg.id):
        return
    log.info(f"[INCOMING] from={msg.sender} subject={msg.subject!r}")
    decision = router.classify(msg)

    if decision.track == "lead":
        result = lead_handler.run(msg, decision)
    elif decision.track == "client":
        result = client_handler.run(msg, decision)
    elif decision.track == "internal":
        log.warning("[INTERNAL] handler not implemented in MVP, skipping")
        memory.mark_processed(msg.id)
        return
    elif decision.track == "ignore":
        log.info(f"[IGNORE] {decision.reasoning}")
        memory.mark_processed(msg.id)
        return
    else:
        log.warning(f"[ROUTER] unknown track {decision.track!r}; skipping")
        return

    gmail.mark_processed(msg.id)
    memory.mark_processed(msg.id)
    log.info(
        f"[DONE] draft_id={result.gmail_draft_id} policy={result.policy} "
        f"subject={result.draft_subject!r}"
    )


def run_once() -> None:
    """Single pass: fetch unread, route each, dispatch to the right handler.

    Per-message exceptions are caught and logged so one bad message doesn't
    block the rest of the pass.
    """
    new_messages = gmail.list_unread()
    log.info(f"[POLL] {len(new_messages)} unread message(s) to process")
    for msg in new_messages:
        try:
            _process_message(msg)
        except Exception as exc:
            log.exception(
                f"[ERROR] message id={msg.id} subject={msg.subject!r}: {exc}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent.orchestrator",
        description="Capacity Guardian — autonomous email triage agent.",
    )
    parser.add_argument("--once", action="store_true", help="Single pass, then exit.")
    parser.add_argument("--verbose", action="store_true", help="DEBUG-level logging.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Smoke-test mode. Real LLM calls and real Gmail/Sheet reads, but "
            "no drafts created, no labels applied, no Slack alerts, no DB writes."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()
    init_root_logger(verbose=args.verbose)
    memory.init_db()

    # Wrap `run_once` in `dry_run()` if the flag is set; otherwise pass through.
    runner = dry_run if args.dry_run else contextlib.nullcontext
    if args.dry_run:
        log.warning("[DRY-RUN] all side effects mocked; nothing will be persisted")

    if args.once:
        with runner():
            run_once()
        return

    log.info(f"[ORCH] entering poll loop (every {POLL_SECONDS}s); ctrl-c to stop")
    while True:
        try:
            with runner():
                run_once()
        except Exception:
            log.exception("[ERROR] loop-level failure, sleeping and continuing")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
