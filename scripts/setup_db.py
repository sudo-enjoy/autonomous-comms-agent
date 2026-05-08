"""Create the SQLite schema at data/memory.db."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import memory  # noqa: E402


def main() -> None:
    memory.init_db()
    print(f"Schema initialized at {memory.DB_PATH}")
    conn = memory._connection()
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    print(f"Tables: {tables}")


if __name__ == "__main__":
    main()
