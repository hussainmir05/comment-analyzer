"""Checks tracked posts on a timer while the app runs.

Each check takes a snapshot (views, likes, comment count) and collects the comments posted since
the newest one stored ("newest first", so it stops early). New comments are analysed if the model
is installed, then the spike rules run. Posts are checked one at a time.

For YouTube a check costs 1 quota unit plus 1 per 100 new comments. YouTube's 30-day rule means
its snapshot history is a rolling 30 days (see db/retention.py).

Facebook, Instagram and TikTok posts are checked rarely and with caps: at most every
BROWSER_EVERY, up to BROWSER_LIMIT comments, in a visible window, counted against the account's
daily limit like any other read. A post whose check can't start (no free account, or the daily
limit reached) waits RETRY_LATER; it never stops tracking for the other posts.
"""

import functools
import logging
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from comment_analyzer import config
from comment_analyzer.analysis import pipeline
from comment_analyzer.analysis.pipeline import Analyzer
from comment_analyzer.collection import CollectionSession, RunSummary
from comment_analyzer.db import Database, repository
from comment_analyzer.errors import UserError
from comment_analyzer.models import Platform, RunStatus
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo, detect
from comment_analyzer.timeutil import utc_now
from comment_analyzer.tracking.spikes import comment_spike, negative_spike
from comment_analyzer.workers import CancelToken, ProgressFn, TaskRunner, Worker

log = logging.getLogger(__name__)

TICK_MS = 60_000  # how often to look for posts that are due
OVERLAP = timedelta(minutes=5)  # re-read a little before the newest comment, in case of delays
BROWSER_EVERY = timedelta(hours=6)
BROWSER_LIMIT = 300
RETRY_LATER = timedelta(minutes=30)


class Tracker(QObject):
    checked = Signal(int)  # posts.id, after each check
    spike = Signal(int, str)  # posts.id, the alert in plain words
    status = Signal(str)

    def __init__(
        self,
        db: Database,
        store: QSettings,
        tasks: TaskRunner,
        collector_factory: Callable[[LinkInfo], Collector],
        analyzer: Analyzer | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.store = store
        self.tasks = tasks
        self.collector_factory = collector_factory
        self.analyzer = analyzer
        self.queue: list[int] = []
        self._busy = False
        self._wait_until: dict[int, datetime] = {}  # posts.id -> when to try a skipped post again
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_due)

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self) -> None:
        self._timer.start(TICK_MS)
        QTimer.singleShot(0, self.check_due)

    def stop(self) -> None:
        self._timer.stop()

    def tracked_count(self) -> int:
        with closing(self.db.connect()) as conn:
            return len(repository.list_tracked(conn))

    def check_due(self) -> None:
        """Queue the tracked posts whose last snapshot is older than the interval."""
        every = timedelta(minutes=config.load_settings(self.store).track_every_minutes)
        now = utc_now()
        with closing(self.db.connect()) as conn:
            for post in repository.list_tracked(conn):
                assert post.id is not None
                if post.id in self.queue or self._wait_until.get(post.id, now) > now:
                    continue
                interval = every if post.platform is Platform.YOUTUBE else max(every, BROWSER_EVERY)
                last = repository.latest_snapshot(conn, post.id)
                if last is None or now - last.taken_at >= interval:
                    self.queue.append(post.id)
        self._next()

    def check_now(self) -> None:
        with closing(self.db.connect()) as conn:
            for post in repository.list_tracked(conn):
                if post.id is not None and post.id not in self.queue:
                    self.queue.append(post.id)
        self._next()

    def _next(self) -> None:
        if self._busy or not self.queue:
            return
        post_row_id = self.queue.pop(0)
        with closing(self.db.connect()) as conn:
            post = repository.get_post_by_id(conn, post_row_id)
            newest = repository.newest_comment_time(conn, post_row_id)
        if post is None:
            self._next()
            return
        youtube = post.platform is Platform.YOUTUBE
        link = detect(post.url)
        try:
            collector = self.collector_factory(link)
        except UserError as e:
            if youtube:  # a missing key stops every YouTube check
                self.queue.clear()
                self.status.emit(f"Tracking paused: {e}")
                return
            self._wait_until[post_row_id] = utc_now() + RETRY_LATER
            self.status.emit(f"{post.platform.label} tracking waits: {e}")
            self._next()
            return
        limit = config.load_settings(self.store).comments_per_post
        if youtube:
            since = newest - OVERLAP if newest else None
            sort = "time"
        else:  # the sites show the most relevant comments; read a capped number of them
            since, sort, limit = None, "relevance", min(limit, BROWSER_LIMIT)
        session = CollectionSession(
            self.db,
            collector,
            link,
            limit=limit,
            sort=sort,
            since=since,
            account_id=getattr(collector, "account_id", None),
        )
        session.done.connect(functools.partial(self._collected, post_row_id, session))
        self._busy = True
        self.status.emit(f"Checking {post.title or post.post_id}...")
        session.start()

    def _collected(
        self, post_row_id: int, _session: CollectionSession, summary: RunSummary
    ) -> None:  # _session: kept referenced (by the partial) until it's done
        if summary.status is RunStatus.FAILED:
            log.warning("Tracking check failed: %s", summary.message)
            self.status.emit(f"Tracking check failed: {summary.message}")
            self._done(post_row_id)
            return
        analyzer = self.analyzer
        if analyzer is None or not analyzer.ready or not summary.new:
            self._done(post_row_id)
            return
        db = self.db

        def job(progress: ProgressFn, token: CancelToken) -> int:
            return pipeline.analyse_post(
                db,
                post_row_id,
                analyzer.classifier,
                progress,
                token,
                make_embedder=analyzer.embedder,
            )

        worker = Worker(job, name="Tracking analysis")
        worker.signals.finished.connect(lambda: self._done(post_row_id))
        self.tasks.start(worker)

    def _done(self, post_row_id: int) -> None:
        self._check_spikes(post_row_id)
        self._busy = False
        self.checked.emit(post_row_id)
        self.status.emit("")
        self._next()

    def _check_spikes(self, post_row_id: int) -> None:
        with closing(self.db.connect()) as conn:
            comments = repository.list_comments(conn, post_row_id)
            results = repository.analysis_for_post(conn, post_row_id)
        now = utc_now()
        times = [c.created_at for c in comments if c.created_at]
        for message in (comment_spike(times, now), negative_spike(comments, results, now)):
            if message:
                self.spike.emit(post_row_id, message)
