"""Rich-based logger setup.

Step-1 stub: returns a plain stdlib logger so any module that does
`log = get_logger(__name__)` at import time loads cleanly. The `rich`
handler + formatting is wired in step 10 (terminal polish).
"""
from __future__ import annotations

import logging


def get_logger(name: str = "capacity_guardian", verbose: bool = False) -> logging.Logger:
    """Return a stdlib logger at INFO (or DEBUG if verbose).

    Safe to call at import time. Replaced with a rich-formatted logger in step 10.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger
