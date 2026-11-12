"""Versioned schema migrations: db/migrations/NNNN_name.sql, tracked with PRAGMA user_version.

Each migration runs in one transaction together with the version bump, so a failed migration
leaves the database exactly as it was. Before upgrading an existing database, a copy goes to the
backups folder.
"""

import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from comment_analyzer.errors import UserError
from comment_analyzer.timeutil import utc_now

log = logging.getLogger(__name__)

KEEP_BACKUPS = 5
_FILE_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


def bundled_migrations() -> list[Migration]:
    folder = resources.files("comment_analyzer.db") / "migrations"
    found = [
        Migration(int(m.group(1)), entry.name, entry.read_text(encoding="utf-8"))
        for entry in folder.iterdir()
        if (m := _FILE_NAME.match(entry.name))
    ]
    found.sort(key=lambda migration: migration.version)
    versions = [migration.version for migration in found]
    if versions != list(range(1, len(found) + 1)):
        raise RuntimeError(f"Migrations must be numbered 1, 2, 3... without gaps, got {versions}")
    return found


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(
    conn: sqlite3.Connection,
    *,
    backup_dir: Path | None = None,
    migrations: list[Migration] | None = None,
) -> list[int]:
    """Apply the migrations the database hasn't had yet. Returns their versions."""
    migrations = bundled_migrations() if migrations is None else migrations
    current = schema_version(conn)
    latest = migrations[-1].version if migrations else 0
    if current > latest:
        raise UserError(
            "This data was saved by a newer version of Comment Analyzer. "
            "Install the newer version to open it."
        )
    pending = [m for m in migrations if m.version > current]
    if not pending:
        return []
    if current > 0 and backup_dir is not None:
        backup(conn, backup_dir, current)
    for migration in pending:
        log.info("Applying database migration %s", migration.name)
        bump = f"PRAGMA user_version = {migration.version};"
        script = f"BEGIN IMMEDIATE;\n{migration.sql}\n{bump}\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return [m.version for m in pending]


def backup(conn: sqlite3.Connection, backup_dir: Path, version: int) -> Path:
    """Copy the database (consistently, while open) and keep the newest few copies."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"data-v{version}-{utc_now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(target)) as copy:
        conn.backup(copy)
    copies = sorted(backup_dir.glob("data-v*.db"), key=lambda p: p.stat().st_mtime)
    for old in copies[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    log.info("Backed up the database to %s", target)
    return target
