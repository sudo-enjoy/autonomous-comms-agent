"""Router agent — classifies incoming email into a track."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RouterDecision:
    track: str  # 'lead' | 'client' | 'internal' | 'ignore'
    confidence: float
    reasoning: str
    matched_client_id: int | None


def classify(message: dict) -> RouterDecision:
    """Run the router against a single incoming message.

    `message` is expected to have at least `sender`, `subject`, `body`, `id`.
    Returns a RouterDecision. Forces `track='ignore'` if confidence < 0.6.
    """
    raise NotImplementedError
