"""Eval harness — runs all fixtures through Router (and Lead/Client handlers
where applicable) in dry-run mode and prints a results table.

Dry-run = mock all side effects (Gmail draft creation, Slack post, capacity
sheet writes, SQLite writes). The LLM calls are real and exercise the same
prompts + tool schemas the orchestrator uses.

Exit code: 0 if all fixtures pass, 1 otherwise.
"""
from __future__ import annotations

import contextlib
import json
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from agent import memory, router  # noqa: E402
from agent.handlers import client, lead  # noqa: E402
from agent.tools.gmail import Message  # noqa: E402


@contextlib.contextmanager
def dry_run():
    """Mock every side-effect path so evals don't write to Gmail/Sheet/Slack/DB.

    Memory reads (find clients by domain, etc.) still hit the real DB so the
    Router's domain matching works against seeded contacts. Memory *writes*
    (record_thread, record_draft, find_or_create_client) are mocked.
    """
    with mock.patch("agent.tools.gmail.create_draft", return_value="eval-draft-id"), \
         mock.patch("agent.tools.gmail.get_thread", return_value=[]), \
         mock.patch("agent.tools.slack.send_alert"), \
         mock.patch("agent.capacity.add_to_waitlist", return_value=99), \
         mock.patch("agent.memory.find_or_create_client", return_value=999), \
         mock.patch("agent.memory.record_thread", return_value=999), \
         mock.patch("agent.memory.record_draft", return_value=999), \
         mock.patch("agent.memory.get_voice_samples", return_value=[]):
        yield


def run_fixture(fix: dict) -> dict:
    """Run one fixture. Returns a dict ready for the results table."""
    msg = Message(
        id=f"eval-{fix['name']}",
        thread_id=f"eval-thread-{fix['name']}",
        sender=fix["from"],
        subject=fix["subject"],
        body=fix["body"],
        received_at="",
    )
    expected_track = fix["expected_track"]
    expected_label = fix.get("expected_policy") or fix.get("expected_subclass") or "-"

    actual_track = "?"
    actual_label = "-"
    error: str | None = None

    try:
        decision = router.classify(msg)
        actual_track = decision.track
        if decision.track == "lead":
            with dry_run():
                result = lead.run(msg, decision)
            actual_label = result.policy
        elif decision.track == "client":
            with dry_run():
                result = client.run(msg, decision)
            actual_label = result.policy
        # internal / ignore: no handler in MVP, just track classification
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"

    track_ok = actual_track == expected_track
    label_ok = (
        expected_label == "-" or actual_label == expected_label
    )
    return {
        "name": fix["name"],
        "expected_track": expected_track,
        "actual_track": actual_track,
        "expected_label": expected_label,
        "actual_label": actual_label,
        "passed": track_ok and label_ok and error is None,
        "error": error,
    }


def main() -> int:
    memory.init_db()
    fixtures = json.loads(
        Path(__file__).parent.joinpath("fixtures.json").read_text()
    )

    console = Console()
    console.print(f"[bold]Running {len(fixtures)} fixtures...[/bold]\n")

    results = []
    for fix in fixtures:
        r = run_fixture(fix)
        results.append(r)

    table = Table(title="Eval Results", show_lines=False)
    table.add_column("fixture", style="cyan", no_wrap=False)
    table.add_column("expected", style="white")
    table.add_column("actual", style="white")
    table.add_column("pass", style="bold", justify="center")

    for r in results:
        expected = f"{r['expected_track']}/{r['expected_label']}"
        actual = (
            f"[red]{r['error']}[/red]"
            if r["error"]
            else f"{r['actual_track']}/{r['actual_label']}"
        )
        passed = "[bold green]✓[/bold green]" if r["passed"] else "[bold red]✗[/bold red]"
        table.add_row(r["name"], expected, actual, passed)

    console.print(table)

    n_pass = sum(1 for r in results if r["passed"])
    n_total = len(results)
    color = "green" if n_pass == n_total else "red"
    console.print(f"\n[bold {color}]{n_pass}/{n_total} passed[/bold {color}]")

    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
