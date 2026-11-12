"""SQLite connections with the settings the app relies on."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database in autocommit mode; group writes with transaction()."""
    conn = sqlite3.connect(path, timeout=10, autocommit=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite leaves foreign keys off by default
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")  # readers never wait for the writer
        conn.execute("PRAGMA synchronous = NORMAL")  # safe with WAL, and much faster
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rolled back if the block raises."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
