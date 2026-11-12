import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fakes import FakeCollector, sample_comments

from comment_analyzer.collection import STORAGE_ERROR, CollectionSession, RunSummary
from comment_analyzer.db import Database, repository
from comment_analyzer.models import RunStatus
from comment_analyzer.platforms.detect import detect

LINK = detect("https://youtu.be/dQw4w9WgXcQ")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data.db")
    database.migrate()
    return database


def run(qtbot, db: Database, collector: FakeCollector, **kwargs: object) -> RunSummary:
    session = CollectionSession(db, collector, LINK, limit=500, sort="time", **kwargs)  # type: ignore[arg-type]
    with qtbot.waitSignal(session.done, timeout=5000) as blocker:
        session.start()
    return blocker.args[0]


def test_a_run_stores_post_snapshot_run_comments_and_quota(qtbot, db: Database):
    collector = FakeCollector(comments=sample_comments(3), reported=10)
    summary = run(qtbot, db, collector)
    assert collector.started_with is not None
    assert collector.started_with[1:] == (500, "time")
    assert (summary.status, summary.collected, summary.new) == (RunStatus.COMPLETED, 3, 3)
    assert summary.post is not None and summary.post.id is not None
    with closing(db.connect()) as conn:
        comments = repository.list_comments(conn, summary.post.id)
        assert len(comments) == 3
        assert {c.run_id for c in comments} == {summary.run_id}
        assert {c.sort_used for c in comments} == {"time"}
        stored_run = repository.latest_run(conn, summary.post.id)
        assert stored_run is not None
        assert (stored_run.status, stored_run.comments_collected, stored_run.comments_reported) == (
            RunStatus.COMPLETED,
            3,
            10,
        )
        assert (stored_run.sort_used, stored_run.comment_limit) == ("time", 500)
        assert repository.latest_snapshot(conn, summary.post.id) is not None
        assert repository.api_units_used(conn, "youtube", "2026-11-01") == 2


def test_running_again_counts_only_new_comments(qtbot, db: Database):
    run(qtbot, db, FakeCollector(comments=sample_comments(3)))
    summary = run(qtbot, db, FakeCollector(comments=sample_comments(5)))
    assert (summary.collected, summary.new) == (5, 2)


def test_failure_before_the_post_leaves_no_run(qtbot, db: Database):
    summary = run(qtbot, db, FakeCollector(fail_before_post="YouTube couldn't find this video."))
    assert summary.status is RunStatus.FAILED
    assert summary.message == "YouTube couldn't find this video."
    assert summary.run_id is None
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0


def test_stop_is_passed_to_the_collector(qtbot, db: Database):
    collector = FakeCollector(wait_for_stop=True)
    session = CollectionSession(db, collector, LINK, limit=500, sort="relevance")
    with qtbot.waitSignal(session.progress):
        session.start()
    with qtbot.waitSignal(session.done) as blocker:
        session.stop()
    assert collector.stopped
    assert blocker.args[0].status is RunStatus.STOPPED


def test_a_storage_error_stops_the_run(qtbot, db: Database, monkeypatch: pytest.MonkeyPatch):
    def broken(*args: object) -> int:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repository, "upsert_comments", broken)
    summary = run(qtbot, db, FakeCollector())
    assert (summary.status, summary.message) == (RunStatus.FAILED, STORAGE_ERROR)
