"""Context manager that mocks every side-effecting call in the project.

Used by:
- The orchestrator's `--dry-run` CLI flag — smoke-test against a real inbox
  without writing drafts, applying labels, posting to Slack, updating the
  Capacity sheet, or persisting to SQLite.
- The eval harness in `evals/run_evals.py` — run fixtures through real LLM
  calls without polluting any external state.

Read paths stay live: SQLite client lookups, capacity sheet reads, Gmail
list/get. So handlers see the same context they would in production.
"""
from __future__ import annotations

import contextlib
import unittest.mock as mock


@contextlib.contextmanager
def dry_run():
    with mock.patch("agent.tools.gmail.create_draft", return_value="dry-run-draft-id"), \
         mock.patch("agent.tools.gmail.mark_processed"), \
         mock.patch("agent.tools.gmail.get_thread", return_value=[]), \
         mock.patch("agent.tools.slack.send_alert"), \
         mock.patch("agent.capacity.add_to_waitlist", return_value=99), \
         mock.patch("agent.memory.find_or_create_client", return_value=999), \
         mock.patch("agent.memory.record_thread", return_value=999), \
         mock.patch("agent.memory.record_draft", return_value=999), \
         mock.patch("agent.memory.mark_processed"), \
         mock.patch("agent.memory.get_voice_samples", return_value=[]):
        yield
