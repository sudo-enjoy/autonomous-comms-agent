"""Rich-based logger setup.

Module-level `get_logger(name)` returns a stdlib logger so any module can call
it at import time without triggering any setup. The orchestrator's `main()`
calls `init_root_logger(verbose)` once at startup, which installs a
`RichHandler` on the root logger; every child logger then renders through it.

This separation keeps imports cheap and the demo's terminal output pretty.
"""
from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

_console = Console()
_initialized = False


def init_root_logger(verbose: bool = False) -> None:
    """Install a RichHandler on the root logger. Idempotent."""
    global _initialized
    if _initialized:
        return
    level = logging.DEBUG if verbose else logging.INFO
    handler = RichHandler(
        console=_console,
        show_time=True,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
        log_time_format="[%H:%M:%S]",
    )
    handler.setLevel(level)
    root = logging.getLogger()
    root.setLevel(level)
    # Replace any pre-existing handlers so we don't double-print.
    root.handlers = [handler]
    # Quiet noisy third-party libs at INFO; they're DEBUG-only spam in normal use.
    for noisy in ("httpx", "googleapiclient", "google.auth", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _initialized = True


def get_logger(name: str = "capacity_guardian", verbose: bool = False) -> logging.Logger:
    """Return a stdlib logger. Output is formatted by the root RichHandler
    once `init_root_logger` has been called."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger
