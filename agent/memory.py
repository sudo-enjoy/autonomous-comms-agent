"""SQLite operations for clients, threads, drafts, and voice samples.

Single module-level connection (the orchestrator is synchronous per spec).
DB lives at `data/memory.db`. Schema is created lazily by `init_db()`.
"""
from __future__ import annotations

import difflib
import sqlite3
from pathlib import Path

DB_PATH = Path("data/memory.db")

_conn: sqlite3.Connection | None = None


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
    return _conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    company TEXT,
    domain TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_thread_id TEXT UNIQUE NOT NULL,
    client_id INTEGER REFERENCES clients(id),
    subject TEXT,
    summary TEXT,
    last_seen_message_id TEXT,
    last_seen_at TIMESTAMP,
    track TEXT
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER REFERENCES threads(id),
    gmail_draft_id TEXT,
    original_message TEXT,
    agent_draft TEXT,
    final_sent TEXT,
    edit_diff TEXT,
    policy TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS voice_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES clients(id),
    sample_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db() -> None:
    """Create all tables if they do not exist."""
    conn = _connection()
    conn.executescript(SCHEMA)
    conn.commit()


def _domain_from_email(email: str) -> str | None:
    if "@" not in email:
        return None
    domain = email.split("@", 1)[1].strip().lower()
    return domain or None


def find_or_create_client(
    email: str,
    name: str | None = None,
    company: str | None = None,
) -> int:
    conn = _connection()
    email = email.strip().lower()
    row = conn.execute("SELECT id FROM clients WHERE email = ?", (email,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO clients (email, name, company, domain) VALUES (?, ?, ?, ?)",
        (email, name, company, _domain_from_email(email)),
    )
    conn.commit()
    return cur.lastrowid


def find_thread(gmail_thread_id: str) -> dict | None:
    conn = _connection()
    row = conn.execute(
        "SELECT * FROM threads WHERE gmail_thread_id = ?",
        (gmail_thread_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def record_thread(
    gmail_thread_id: str,
    client_id: int | None,
    subject: str | None,
    summary: str | None,
    last_seen_message_id: str | None,
    track: str | None,
) -> int:
    """Upsert a thread row keyed by `gmail_thread_id`. Returns the row id."""
    conn = _connection()
    cur = conn.execute(
        """
        INSERT INTO threads (
            gmail_thread_id, client_id, subject, summary,
            last_seen_message_id, last_seen_at, track
        )
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(gmail_thread_id) DO UPDATE SET
            client_id = COALESCE(excluded.client_id, threads.client_id),
            subject = COALESCE(excluded.subject, threads.subject),
            summary = COALESCE(excluded.summary, threads.summary),
            last_seen_message_id = COALESCE(
                excluded.last_seen_message_id, threads.last_seen_message_id
            ),
            last_seen_at = CURRENT_TIMESTAMP,
            track = COALESCE(excluded.track, threads.track)
        RETURNING id
        """,
        (gmail_thread_id, client_id, subject, summary, last_seen_message_id, track),
    )
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def is_processed(message_id: str) -> bool:
    conn = _connection()
    return (
        conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        is not None
    )


def mark_processed(message_id: str) -> None:
    conn = _connection()
    conn.execute(
        "INSERT OR IGNORE INTO processed_messages (message_id) VALUES (?)",
        (message_id,),
    )
    conn.commit()


def record_draft(
    thread_id: int,
    original: str,
    draft: str,
    gmail_draft_id: str,
    policy: str | None = None,
) -> int:
    conn = _connection()
    cur = conn.execute(
        """
        INSERT INTO drafts (
            thread_id, gmail_draft_id, original_message, agent_draft, policy
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (thread_id, gmail_draft_id, original, draft, policy),
    )
    conn.commit()
    return cur.lastrowid


def record_sent(draft_id: int, final_text: str) -> None:
    """Diff `final_text` against the saved agent_draft and store the diff.

    If `final_text` differs from the agent draft, also save it as a voice
    sample for the client on this draft's thread.
    """
    conn = _connection()
    row = conn.execute(
        """
        SELECT d.agent_draft, t.client_id
        FROM drafts d
        LEFT JOIN threads t ON t.id = d.thread_id
        WHERE d.id = ?
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        return
    agent_draft = row["agent_draft"] or ""
    client_id = row["client_id"]
    diff = "".join(
        difflib.unified_diff(
            agent_draft.splitlines(keepends=True),
            final_text.splitlines(keepends=True),
            fromfile="agent_draft",
            tofile="final_sent",
            n=1,
        )
    )
    conn.execute(
        "UPDATE drafts SET final_sent = ?, edit_diff = ? WHERE id = ?",
        (final_text, diff, draft_id),
    )
    if final_text != agent_draft and client_id is not None:
        conn.execute(
            "INSERT INTO voice_samples (client_id, sample_text) VALUES (?, ?)",
            (client_id, final_text),
        )
    conn.commit()


def get_voice_samples(client_id: int, limit: int = 2) -> list[str]:
    """Return the most recent voice samples for the client (newest first)."""
    conn = _connection()
    rows = conn.execute(
        """
        SELECT sample_text FROM voice_samples
        WHERE client_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()
    return [r["sample_text"] for r in rows]
