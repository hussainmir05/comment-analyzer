import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from comment_analyzer.db import Database, repository, retention
from comment_analyzer.models import CollectionRun, Comment, Platform, Post, RunStatus, Snapshot

NOW = datetime(2026, 11, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=31)
RECENT = NOW - timedelta(days=2)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = Database(tmp_path / "data.db")
    db.migrate()
    connection = db.connect()
    yield connection
    connection.close()


def _post(conn: sqlite3.Connection, platform: Platform, post_id: str) -> int:
    return repository.upsert_post(
        conn, Post(platform, post_id, f"https://example.com/{post_id}", title="Title", author="A")
    )


def test_youtube_api_data_expires_after_30_days(conn: sqlite3.Connection):
    video = _post(conn, Platform.YOUTUBE, "vid00000001")
    repository.add_snapshot(conn, video, Snapshot(OLD, views=1, comments_reported=9))
    repository.start_run(conn, CollectionRun(video, OLD))
    repository.upsert_comments(
        conn,
        video,
        [Comment("old", "old text", collected_at=OLD), Comment("new", "x", collected_at=RECENT)],
    )
    facebook = _post(conn, Platform.FACEBOOK, "123")
    repository.upsert_comments(conn, facebook, [Comment("fb", "kept", collected_at=OLD)])

    assert retention.expire_youtube_data(conn, NOW) == 1
    assert [c.platform_comment_id for c in repository.list_comments(conn, video)] == ["new"]
    assert repository.latest_snapshot(conn, video) is None
    stale = repository.get_post_by_id(conn, video)
    assert stale is not None
    assert (stale.title, stale.author) == (None, None)  # video details came from the API too
    run = repository.latest_run(conn, video)
    assert run is not None and run.comments_reported is None
    assert repository.count_comments(conn, facebook) == 1  # other platforms aren't affected


def test_fresh_youtube_data_is_kept(conn: sqlite3.Connection):
    video = _post(conn, Platform.YOUTUBE, "vid00000001")
    repository.add_snapshot(conn, video, Snapshot(RECENT, views=1))
    assert retention.expire_youtube_data(conn, NOW) == 0
    kept = repository.get_post_by_id(conn, video)
    assert kept is not None and kept.title == "Title"


def test_comment_text_is_blanked_after_the_set_number_of_days(conn: sqlite3.Connection):
    post = _post(conn, Platform.TIKTOK, "7301234567890123456")
    repository.upsert_comments(
        conn,
        post,
        [
            Comment("a", "old", collected_at=NOW - timedelta(days=8)),
            Comment("b", "new", collected_at=RECENT),
        ],
    )
    assert retention.purge_comment_text(conn, 7, NOW) == 1
    texts = {c.platform_comment_id: c.text for c in repository.list_comments(conn, post)}
    assert texts == {"a": None, "b": "new"}


def test_runs_left_running_by_a_crash_are_closed(conn: sqlite3.Connection):
    post = _post(conn, Platform.YOUTUBE, "vid00000001")
    run_id = repository.start_run(conn, CollectionRun(post, RECENT))
    assert retention.close_interrupted_runs(conn) == 1
    run = repository.get_run(conn, run_id)
    assert run is not None
    assert run.status is RunStatus.STOPPED
    assert run.note == "The app closed during this run."
