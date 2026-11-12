import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from comment_analyzer.db import Database, repository
from comment_analyzer.models import CollectionRun, Comment, Platform, Post, RunStatus, Snapshot

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = Database(tmp_path / "data.db")
    db.migrate()
    connection = db.connect()
    yield connection
    connection.close()


def _post(**changes: object) -> Post:
    post = Post(Platform.YOUTUBE, "dQw4w9WgXcQ", VIDEO)
    for name, value in changes.items():
        setattr(post, name, value)
    return post


def test_upsert_post_fills_in_but_never_erases(conn: sqlite3.Connection):
    first_seen = datetime(2026, 1, 1, tzinfo=UTC)
    row_id = repository.upsert_post(conn, _post(first_seen_at=first_seen))
    assert (
        repository.upsert_post(conn, _post(title="A title", first_seen_at=datetime.now(UTC)))
        == row_id
    )
    assert repository.upsert_post(conn, _post(title=None)) == row_id
    post = repository.get_post(conn, Platform.YOUTUBE, "dQw4w9WgXcQ")
    assert post is not None
    assert post.title == "A title"
    assert post.first_seen_at == first_seen


def test_same_id_on_two_platforms_is_two_posts(conn: sqlite3.Connection):
    a = repository.upsert_post(
        conn, Post(Platform.FACEBOOK, "123", "https://facebook.com/x/posts/123")
    )
    b = repository.upsert_post(
        conn, Post(Platform.TIKTOK, "123", "https://tiktok.com/@x/video/123")
    )
    assert a != b
    assert len(repository.list_posts(conn)) == 2


def test_times_are_stored_in_utc(conn: sqlite3.Connection):
    seoul = timezone(timedelta(hours=9))
    repository.upsert_post(conn, _post(published_at=datetime(2026, 3, 1, 9, 0, tzinfo=seoul)))
    stored = conn.execute("SELECT published_at FROM posts").fetchone()[0]
    assert stored == "2026-03-01T00:00:00Z"


def test_comments_are_unique_per_post_and_get_fresh_counts(conn: sqlite3.Connection):
    post_id = repository.upsert_post(conn, _post())
    first = [Comment("c1", "Great video", like_count=1), Comment("c2", "Meh", like_count=0)]
    assert repository.upsert_comments(conn, post_id, first) == 2
    assert repository.upsert_comments(conn, post_id, [Comment("c1", None, like_count=5)]) == 0
    assert repository.count_comments(conn, post_id) == 2
    text, likes = conn.execute(
        "SELECT text, like_count FROM comments WHERE platform_comment_id = 'c1'"
    ).fetchone()
    assert (text, likes) == ("Great video", 5)  # a missing text doesn't erase the stored one


def test_same_comment_id_on_two_posts_is_two_comments(conn: sqlite3.Connection):
    a = repository.upsert_post(conn, _post())
    b = repository.upsert_post(conn, Post(Platform.YOUTUBE, "aBcDeFgHiJk", VIDEO))
    assert repository.upsert_comments(conn, a, [Comment("c1", "x")]) == 1
    assert repository.upsert_comments(conn, b, [Comment("c1", "y")]) == 1


def test_snapshots_keep_reactions_and_the_newest_wins(conn: sqlite3.Connection):
    post_id = repository.upsert_post(conn, _post())
    old = Snapshot(taken_at=datetime(2026, 1, 1, tzinfo=UTC), views=10)
    new = Snapshot(
        taken_at=datetime(2026, 1, 2, tzinfo=UTC),
        views=20,
        reactions={"Like": 5, "Love": 2},
        extra={"saves_estimate": True},
    )
    repository.add_snapshot(conn, post_id, old)
    repository.add_snapshot(conn, post_id, new)
    latest = repository.latest_snapshot(conn, post_id)
    assert latest is not None
    assert latest.views == 20
    assert latest.reactions == {"Like": 5, "Love": 2}
    assert latest.extra == {"saves_estimate": True}


def test_collection_run_lifecycle(conn: sqlite3.Connection):
    post_id = repository.upsert_post(conn, _post())
    run = CollectionRun(post_id, datetime.now(UTC), sort_used="time", comment_limit=500)
    run_id = repository.start_run(conn, run)
    repository.finish_run(
        conn, run_id, RunStatus.STOPPED, comments_collected=120, comments_reported=4000
    )
    stored = repository.get_run(conn, run_id)
    assert stored is not None
    assert stored.status is RunStatus.STOPPED
    assert (stored.comments_collected, stored.comments_reported) == (120, 4000)
    assert stored.finished_at is not None
