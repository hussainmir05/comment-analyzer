from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from fakes import KEY, VIDEO_ID, FakeYouTube, fixture

from comment_analyzer.collection import CollectionSession
from comment_analyzer.db import Database, repository
from comment_analyzer.models import Comment, Post, RunStatus, Snapshot
from comment_analyzer.platforms import youtube
from comment_analyzer.platforms.detect import detect
from comment_analyzer.platforms.youtube import (
    CollectRequest,
    QuotaExceeded,
    YouTubeApi,
    YouTubeError,
    collect,
    quota_day,
)
from comment_analyzer.workers import CancelToken, TaskRunner


class Run:
    """One collect() call against the fake API, with everything it emitted."""

    def __init__(
        self,
        fake: FakeYouTube,
        *,
        limit: int = 1000,
        order: str = "relevance",
        since: datetime | None = None,
        on_batch: Callable[[list[Comment]], None] | None = None,
        token: CancelToken | None = None,
    ) -> None:
        self.posts: list[tuple[Post, Snapshot]] = []
        self.comments: list[Comment] = []
        self.units = 0
        self.sleeps: list[float] = []

        def count_unit() -> None:
            self.units += 1

        def batch(comments: list[Comment]) -> None:
            self.comments.extend(comments)
            if on_batch:
                on_batch(comments)

        api = YouTubeApi(
            KEY, on_unit=count_unit, transport=fake.transport(), sleep=self.sleeps.append
        )
        self.status, self.message = collect(
            api,
            CollectRequest(VIDEO_ID, limit, order, since),
            hash_author=lambda user: f"hash({user})" if user else None,
            emit_post=lambda post, snapshot: self.posts.append((post, snapshot)),
            emit_batch=batch,
            progress=lambda done, total, text: None,
            token=token or CancelToken(),
        )

    def ids(self) -> list[str]:
        return [c.platform_comment_id for c in self.comments]


def test_reads_every_comment_and_every_reply():
    fake = FakeYouTube()
    run = Run(fake)
    assert (run.status, run.message) == (RunStatus.COMPLETED, "")
    assert len(run.comments) == 12
    assert len(set(run.ids())) == 12  # included replies aren't read twice
    assert [c.platform_comment_id for c in run.comments if c.parent_id == "Ugw_T2"] == [
        f"Ugw_T2.r{i}" for i in range(1, 8)
    ]
    # 1 video + 2 thread pages + 2 reply pages, each 1 quota unit
    assert run.units == 5
    assert fake.calls == {"videos": 1, "commentThreads": 2, "comments": 2}


def test_post_and_snapshot_come_from_the_video():
    [(post, snapshot)] = Run(FakeYouTube()).posts
    assert (post.post_id, post.title, post.author) == (
        VIDEO_ID,
        "A made-up test video",
        "Test Channel",
    )
    assert post.url == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert post.published_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert (snapshot.views, snapshot.likes, snapshot.comments_reported) == (12345, 678, 12)


def test_comments_carry_hashes_never_names():
    run = Run(FakeYouTube())
    first = run.comments[0]
    assert first.author_hash == "hash(UCuser1)"
    assert first.text == "Great video, thanks!"
    assert (first.like_count, first.reply_count, first.parent_id) == (15, 2, None)
    assert first.created_at == datetime(2026, 10, 2, 8, tzinfo=UTC)
    no_channel = next(c for c in run.comments if c.platform_comment_id == "Ugw_T3")
    assert no_channel.author_hash is None
    assert not any("made-up-user" in repr(c) for c in run.comments)


def test_key_goes_in_a_header_never_the_url():
    fake = FakeYouTube()
    Run(fake)
    assert fake.requests
    for request in fake.requests:
        assert request.headers["X-Goog-Api-Key"] == KEY
        assert KEY not in str(request.url)


def test_limit_stops_early():
    run = Run(FakeYouTube(), limit=4)
    assert run.status is RunStatus.COMPLETED
    assert "limit of 4" in run.message
    assert len(run.comments) == 4


def test_comments_turned_off():
    fake = FakeYouTube()
    fake.fail("commentThreads", 403, "comments_disabled")
    run = Run(fake)
    assert (run.status, run.message) == (
        RunStatus.COMPLETED,
        "Comments are turned off for this video.",
    )
    assert run.posts and not run.comments


def test_quota_running_out_keeps_what_was_read():
    fake = FakeYouTube()
    fake.fail("commentThreads", 403, "quota_exceeded", after=1)  # the second page fails
    run = Run(fake)
    assert run.status is RunStatus.STOPPED
    assert "quota" in run.message
    assert "Kept the 11 comments" in run.message
    assert len(run.comments) == 11


def test_quota_used_up_before_anything_was_read():
    fake = FakeYouTube()
    fake.fail("commentThreads", 403, "quota_exceeded")
    with pytest.raises(QuotaExceeded, match="resets at midnight Pacific Time"):
        Run(fake)


def test_missing_video():
    fake = FakeYouTube()
    fake.fail("videos", 200)  # an empty list of items
    with pytest.raises(YouTubeError, match="couldn't find this video"):
        Run(fake)


@pytest.mark.parametrize(
    ("error", "status", "wording"),
    [
        ("key_invalid", 400, "isn't valid"),
        ("key_expired", 400, "deleted or has expired"),
        ("service_disabled", 403, "isn't turned on"),
        ("access_denied", 403, "Permission denied"),
        ("video_not_found", 404, "couldn't find this video"),
    ],
)
def test_key_and_project_problems_are_explained(error: str, status: int, wording: str):
    fake = FakeYouTube()
    fake.fail("videoCategories", status, error)
    with pytest.raises(YouTubeError, match=wording):
        youtube.check_key(KEY, transport=fake.transport())


def test_a_good_key_passes_the_check():
    fake = FakeYouTube()
    youtube.check_key(KEY, transport=fake.transport())
    assert fake.calls["videoCategories"] == 1


def test_busy_answers_are_retried_with_backoff():
    fake = FakeYouTube()
    fake.fail("videos", 503, "backend_error", times=2)
    run = Run(fake)
    assert run.status is RunStatus.COMPLETED
    assert run.sleeps == [1.0, 2.0]
    assert run.units == 7  # failed answers cost quota too


def test_retry_after_is_honoured():
    fake = FakeYouTube()
    fake.fail("videos", 429, headers={"Retry-After": "7"})
    run = Run(fake)
    assert run.sleeps == [7.0]


def test_connection_problems_are_retried():
    fake = FakeYouTube()
    answered = fake.__call__
    failures = iter([True, False])

    def flaky(request: httpx2.Request) -> httpx2.Response:
        if next(failures, False):
            raise httpx2.ConnectError("offline")
        return answered(request)

    sleeps: list[float] = []
    api = YouTubeApi(KEY, transport=httpx2.MockTransport(flaky), sleep=sleeps.append)
    assert api.video(VIDEO_ID) is not None
    assert sleeps == [1.0]


def test_gives_up_when_youtube_keeps_failing():
    fake = FakeYouTube()
    fake.fail("videos", 503, "backend_error", times=youtube.MAX_ATTEMPTS)
    with pytest.raises(YouTubeError, match="isn't responding"):
        Run(fake)


def test_stop_keeps_what_was_read():
    token = CancelToken()
    run = Run(FakeYouTube(), token=token, on_batch=lambda comments: token.cancel())
    assert run.status is RunStatus.STOPPED
    assert f"Kept the {len(run.comments)} comments" in run.message
    assert 0 < len(run.comments) < 12


def test_since_reads_newest_first_and_stops_at_older_comments():
    fake = FakeYouTube()
    run = Run(fake, since=datetime(2026, 10, 2, 12, tzinfo=UTC))
    assert run.status is RunStatus.COMPLETED
    assert "since the last run" in run.message
    thread_request = next(r for r in fake.requests if r.url.path.endswith("commentThreads"))
    assert thread_request.url.params["order"] == "time"


@pytest.mark.parametrize(
    ("utc", "day"),
    [
        (datetime(2026, 11, 12, 7, 59, tzinfo=UTC), "2026-11-11"),  # 23:59 PST
        (datetime(2026, 11, 12, 8, 0, tzinfo=UTC), "2026-11-12"),
        (datetime(2026, 7, 1, 6, 59, tzinfo=UTC), "2026-06-30"),  # 23:59 PDT
        (datetime(2026, 7, 1, 7, 0, tzinfo=UTC), "2026-07-01"),
    ],
)
def test_quota_day_follows_pacific_time(utc: datetime, day: str):
    assert quota_day(utc) == day


def test_missing_statistics_stay_unknown():
    item = fixture("video.json")["items"][0]
    item["statistics"] = {"viewCount": "5"}
    _, snapshot = youtube.parse_video(item, datetime.now(UTC))
    assert (snapshot.views, snapshot.likes, snapshot.comments_reported) == (5, None, None)


def test_collector_and_session_store_a_whole_run(qtbot, tmp_path: Path):
    db = Database(tmp_path / "data.db")
    db.migrate()
    tasks = TaskRunner()
    fake = FakeYouTube()
    collector = youtube.YouTubeCollector(
        KEY, tasks, lambda user: f"h({user})" if user else None, transport=fake.transport()
    )
    link = detect(f"https://youtu.be/{VIDEO_ID}")
    session = CollectionSession(db, collector, link, limit=1000, sort="relevance")
    with qtbot.waitSignal(session.done, timeout=10_000) as blocker:
        session.start()
    summary = blocker.args[0]
    assert (summary.status, summary.collected, summary.new) == (RunStatus.COMPLETED, 12, 12)
    with closing(db.connect()) as conn:
        assert summary.post is not None and summary.post.id is not None
        assert repository.count_comments(conn, summary.post.id) == 12
        run = repository.latest_run(conn, summary.post.id)
        assert run is not None
        assert (run.status, run.comments_collected, run.comments_reported) == (
            RunStatus.COMPLETED,
            12,
            12,
        )
        assert repository.api_units_used(conn, "youtube", quota_day()) == 5
    assert tasks.wait(5000)


# --- the dislike estimate (opt-in) ------------------------------------------------------------


def _ryd(status: int = 200, body: object = None) -> httpx2.MockTransport:
    def answer(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "returnyoutubedislikeapi.com"
        assert dict(request.url.params) == {"videoId": VIDEO_ID}  # the video ID and nothing else
        assert KEY not in str(request.url) and KEY not in str(request.headers)
        return httpx2.Response(status, json=body if body is not None else {"dislikes": 321})

    return httpx2.MockTransport(answer)


def test_dislike_estimate_is_asked_for_by_video_id_only():
    assert youtube.dislike_estimate(VIDEO_ID, transport=_ryd()) == 321
    assert youtube.dislike_estimate(VIDEO_ID, transport=_ryd(404, {"error": "x"})) is None
    assert youtube.dislike_estimate(VIDEO_ID, transport=_ryd(body={"dislikes": "lots"})) is None
    assert youtube.dislike_estimate(VIDEO_ID, transport=_ryd(body=[1, 2])) is None


def test_the_estimate_lands_in_the_snapshot_only_when_asked():
    fake = FakeYouTube()
    api = YouTubeApi(KEY, transport=fake.transport())
    posts: list[tuple[Post, Snapshot]] = []
    collect(
        api,
        CollectRequest(VIDEO_ID, 5),
        hash_author=lambda user: None,
        emit_post=lambda post, snapshot: posts.append((post, snapshot)),
        emit_batch=lambda comments: None,
        progress=lambda *a: None,
        token=CancelToken(),
        dislikes=lambda video_id: 42,
    )
    assert posts[0][1].extra == {youtube.DISLIKES_KEY: 42}
    assert Run(FakeYouTube()).posts[0][1].extra == {}  # off by default
