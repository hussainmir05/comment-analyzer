from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAnalyzer, FakeCollector

from comment_analyzer import config
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.errors import UserError
from comment_analyzer.models import AnalysisResult, Comment, Platform, Post, Snapshot
from comment_analyzer.timeutil import utc_now
from comment_analyzer.tracking import startup
from comment_analyzer.tracking.scheduler import BROWSER_LIMIT, Tracker
from comment_analyzer.tracking.spikes import comment_spike, negative_spike
from comment_analyzer.ui.pages.tracking import TrackingPage
from comment_analyzer.workers import TaskRunner

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def test_comment_rate_spike():
    usual = [NOW - timedelta(hours=h) for h in range(2, 26)]  # one an hour
    burst = [NOW - timedelta(minutes=m) for m in range(0, 60, 5)]  # 12 in the last hour
    assert (
        comment_spike(usual + burst, NOW)
        == "12 comments in the last hour, about 12 times the usual rate."
    )
    assert comment_spike(usual, NOW) is None
    assert comment_spike(burst[:9], NOW) is None  # under 10: not worth an alert


def test_negative_share_spike():
    def analysed(n: int, sentiment: str, minutes_ago: int) -> tuple[Comment, AnalysisResult]:
        comment = Comment(f"c{n}", "t", created_at=NOW - timedelta(minutes=minutes_ago), id=n)
        return comment, AnalysisResult(n, "local", "m", NOW, sentiment=sentiment)

    old = [analysed(i, "positive", 600) for i in range(40)]
    recent = [analysed(100 + i, "negative", 10) for i in range(10)]
    rows = old + recent
    results = {r.comment_id: r for _, r in rows}
    message = negative_spike([c for c, _ in rows], results, NOW)
    assert message == "100% of the last hour's comments are negative, against 20% overall."
    calm = old + [analysed(200 + i, "positive", 10) for i in range(10)]
    assert negative_spike([c for c, _ in calm], {r.comment_id: r for _, r in calm}, NOW) is None


def test_launch_command_starts_in_the_tray():
    assert startup.launch_command().endswith(startup.MINIMIZED)


@pytest.fixture
def setup(qtbot):
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db)
    db.migrate()
    tasks = TaskRunner()
    yield db, config.settings_store(paths), tasks
    tasks.wait(5000)


def _tracked_post(db: Database, *, checked_minutes_ago: int | None = None) -> int:
    with closing(db.connect()) as conn:
        post_id = repository.upsert_post(
            conn,
            Post(Platform.YOUTUBE, "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        )
        repository.set_tracked(conn, post_id, True)
        repository.upsert_comments(
            conn, post_id, [Comment("old", "an old comment", created_at=NOW - timedelta(days=1))]
        )
        if checked_minutes_ago is not None:
            taken = utc_now() - timedelta(minutes=checked_minutes_ago)
            repository.add_snapshot(conn, post_id, Snapshot(taken, views=10))
    return post_id


def test_due_posts_are_checked_for_new_comments_only(qtbot, setup):
    db, store, tasks = setup
    post_id = _tracked_post(db, checked_minutes_ago=90)
    collectors: list[FakeCollector] = []

    def factory(link: object) -> FakeCollector:
        collectors.append(FakeCollector())
        return collectors[-1]

    tracker = Tracker(db, store, tasks, factory, FakeAnalyzer())
    with qtbot.waitSignal(tracker.checked, timeout=10_000) as blocker:
        tracker.check_due()
    assert blocker.args == [post_id]
    [collector] = collectors
    assert collector.started_with is not None and collector.started_with[2] == "time"
    assert collector.since == NOW - timedelta(days=1) - timedelta(minutes=5)
    with closing(db.connect()) as conn:
        assert len(repository.list_snapshots(conn, post_id)) == 2
        assert repository.analysis_for_post(conn, post_id)  # new comments were analysed
    tracker.check_due()  # just checked: nothing is due
    assert not tracker.busy and not tracker.queue


def test_recently_checked_posts_wait(setup):
    db, store, tasks = setup
    _tracked_post(db, checked_minutes_ago=10)
    tracker = Tracker(db, store, tasks, lambda link: FakeCollector())
    tracker.check_due()
    assert not tracker.busy


def test_tracking_pauses_without_a_key(qtbot, setup):
    db, store, tasks = setup
    _tracked_post(db)

    def no_key(link: object) -> FakeCollector:
        raise UserError("Save your YouTube API key in Settings first.")

    tracker = Tracker(db, store, tasks, no_key)
    with qtbot.waitSignal(tracker.status) as blocker:
        tracker.check_now()
    assert blocker.args == ["Tracking paused: Save your YouTube API key in Settings first."]
    assert not tracker.queue


def test_tracking_page_lists_and_untracks(qtbot, setup):
    db, store, tasks = setup
    _tracked_post(db, checked_minutes_ago=5)
    tracker = Tracker(db, store, tasks, lambda link: FakeCollector())
    page = TrackingPage(db, tracker)
    qtbot.addWidget(page)
    assert page.table.rowCount() == 1
    page.table.selectRow(0)
    page.untrack_selected()
    assert page.table.rowCount() == 0
    with closing(db.connect()) as conn:
        assert repository.list_tracked(conn) == []


def _tracked_instagram(db: Database, checked_hours_ago: float, code: str = "Chunk8-jurw") -> int:
    with closing(db.connect()) as conn:
        post_id = repository.upsert_post(
            conn,
            Post(Platform.INSTAGRAM, code, f"https://www.instagram.com/p/{code}/"),
        )
        repository.set_tracked(conn, post_id, True)
        taken = utc_now() - timedelta(hours=checked_hours_ago)
        repository.add_snapshot(conn, post_id, Snapshot(taken, likes=5))
    return post_id


def test_browser_posts_are_checked_rarely_and_capped(qtbot, setup):
    db, store, tasks = setup
    _tracked_instagram(db, checked_hours_ago=2)  # hourly for YouTube, but not for the sites
    collectors: list[FakeCollector] = []

    def factory(link: object) -> FakeCollector:
        collectors.append(FakeCollector())
        collectors[-1].account_id = None  # type: ignore[attr-defined]
        return collectors[-1]

    tracker = Tracker(db, store, tasks, factory)
    tracker.check_due()
    assert collectors == []

    post_id = _tracked_instagram(db, checked_hours_ago=7, code="CxYz123abcD")
    with qtbot.waitSignal(tracker.checked, timeout=10_000) as blocker:
        tracker.check_due()
    assert blocker.args == [post_id]
    [collector] = collectors
    assert collector.started_with is not None
    _, limit, sort = collector.started_with
    assert (limit, sort, collector.since) == (BROWSER_LIMIT, "relevance", None)


def test_a_browser_post_that_cant_start_waits_without_stopping_youtube(qtbot, setup):
    db, store, tasks = setup
    instagram = _tracked_instagram(db, checked_hours_ago=7)
    youtube = _tracked_post(db, checked_minutes_ago=90)
    started: list[str] = []

    def factory(link: object) -> FakeCollector:
        platform = getattr(link, "platform", None)
        if platform is Platform.INSTAGRAM:
            raise UserError("Log in to Instagram on the Accounts page first.")
        started.append("youtube")
        return FakeCollector()

    tracker = Tracker(db, store, tasks, factory, FakeAnalyzer())
    with qtbot.waitSignal(tracker.checked, timeout=10_000) as blocker:
        tracker.check_due()
    assert blocker.args == [youtube] and started == ["youtube"]
    assert instagram not in tracker.queue
    tracker.check_due()  # the Instagram post waits half an hour before trying again
    assert instagram not in tracker.queue and not tracker.busy
