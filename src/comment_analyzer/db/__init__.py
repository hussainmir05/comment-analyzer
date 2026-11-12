"""SQLite storage: connections, migrations and repository functions."""

import sqlite3
from contextlib import closing
from pathlib import Path

from comment_analyzer.db.connection import connect
from comment_analyzer.db.migrate import migrate


class Database:
    """The app's database file. Connections can't be shared between threads: each worker
    opens its own with connect()."""

    def __init__(self, path: Path, backups: Path | None = None) -> None:
        self.path = path
        self.backups = backups

    def connect(self) -> sqlite3.Connection:
        return connect(self.path)

    def migrate(self) -> list[int]:
        """Bring the schema up to date, backing up an existing database first.

        Returns the migration versions applied.
        """
        with closing(self.connect()) as conn:
            return migrate(conn, backup_dir=self.backups)
