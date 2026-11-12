import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from comment_analyzer.db import Database
from comment_analyzer.db.connection import connect, transaction
from comment_analyzer.db.migrate import Migration, bundled_migrations, migrate, schema_version
from comment_analyzer.errors import UserError

CORE_TABLES = {
    "accounts",
    "posts",
    "snapshots",
    "collection_runs",
    "comments",
    "api_usage",
    "account_usage",
}
V1 = Migration(1, "0001_a.sql", "CREATE TABLE a (x INTEGER) STRICT;")
V2 = Migration(2, "0002_b.sql", "CREATE TABLE b (y INTEGER) STRICT;")


def test_fresh_database_gets_every_migration(tmp_path: Path):
    db = Database(tmp_path / "data.db", backups=tmp_path / "backups")
    latest = bundled_migrations()[-1].version
    assert db.migrate() == list(range(1, latest + 1))
    with closing(db.connect()) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert tables >= CORE_TABLES
        assert schema_version(conn) == latest
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.migrate() == []  # nothing left to do
    assert not (tmp_path / "backups").exists()  # nothing worth backing up in a new database


def test_upgrade_backs_up_the_old_database_first(tmp_path: Path):
    backups = tmp_path / "backups"
    with closing(connect(tmp_path / "data.db")) as conn:
        migrate(conn, migrations=[V1])
        conn.execute("INSERT INTO a VALUES (1)")
        assert migrate(conn, backup_dir=backups, migrations=[V1, V2]) == [2]
    [copy] = backups.glob("data-v1-*.db")
    with closing(sqlite3.connect(copy)) as old:
        assert old.execute("SELECT x FROM a").fetchall() == [(1,)]
        assert schema_version(old) == 1


def test_failed_migration_changes_nothing(tmp_path: Path):
    bad = Migration(
        2, "0002_bad.sql", "CREATE TABLE c (z INTEGER); INSERT INTO missing VALUES (1);"
    )
    with closing(connect(tmp_path / "data.db")) as conn:
        migrate(conn, migrations=[V1])
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, migrations=[V1, bad])
        assert schema_version(conn) == 1
        assert conn.execute("SELECT name FROM sqlite_schema WHERE name = 'c'").fetchone() is None
        assert not conn.in_transaction


def test_database_from_a_newer_version_is_refused(tmp_path: Path):
    with closing(connect(tmp_path / "data.db")) as conn:
        conn.execute("PRAGMA user_version = 99")
        with pytest.raises(UserError, match="newer version"):
            migrate(conn)


def test_foreign_keys_are_enforced(tmp_path: Path):
    db = Database(tmp_path / "data.db")
    db.migrate()
    with closing(db.connect()) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO snapshots (post_id, taken_at) VALUES (12345, '2026-01-01T00:00:00Z')"
        )


def test_transaction_rolls_back_on_error(tmp_path: Path):
    with closing(connect(tmp_path / "data.db")) as conn:
        migrate(conn, migrations=[V1])
        with pytest.raises(RuntimeError), transaction(conn):
            conn.execute("INSERT INTO a VALUES (1)")
            raise RuntimeError("boom")
        assert conn.execute("SELECT COUNT(*) FROM a").fetchone()[0] == 0


def test_bundled_migrations_are_numbered_without_gaps():
    versions = [m.version for m in bundled_migrations()]
    assert versions == list(range(1, len(versions) + 1))
