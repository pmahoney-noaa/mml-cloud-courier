"""SQLite connection factory with the pragmas this application depends on."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from mml_cloud_transfer.store.schema import apply_migrations


def connect(path: str | os.PathLike[str], *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the job database in WAL mode."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30.0, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    apply_migrations(conn)
    return conn
