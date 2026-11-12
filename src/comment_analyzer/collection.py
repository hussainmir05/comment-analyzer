"""Stores what a collector finds: the post, a snapshot, the collection run and its comments.

Every write happens here, on the GUI thread, as the collector's signals arrive. Batches are at
most a few hundred rows and take milliseconds. Collectors themselves never touch the database.
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from comment_analyzer.db import Database, repository
from comment_analyzer.db.connection import transaction
from comment_analyzer.models import (
    AccountStatus,
    CollectionRun,
    Comment,
    Post,
    RunStatus,
    Snapshot,
)
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo
from comment_analyzer.timeutil import utc_now

log = logging.getLogger(__name__)

STORAGE_ERROR = "Couldn't save the comments. The details are in the log."


@dataclass(slots=True)
class RunSummary:
    status: RunStatus
    message: str = ""
    post: Post | None = None
    snapshot: Snapshot | None = None
    run_id: int | None = None
    collected: int = 0  # comments read in this run
    new: int = 0  # of those, comments that weren't stored before
    sort_used: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CollectionSession(QObject):
    progress = Signal(int, int, str)  # done, total (0 if unknown), message
    done = Signal(object)  # RunSummary

    def __init__(
        self,
        db: Database,
        collector: Collector,
        link: LinkInfo,
        *,
        limit: int,
        sort: str,
        since: datetime | None = None,
        account_id: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.collector = collector
        self.link = link
        self.limit = limit
        self.sort = sort
        self.since = since
        self.account_id = account_id
        self.summary = RunSummary(RunStatus.RUNNING, sort_used=sort, started_at=utc_now())
        self._conn = db.connect()
        self._storage_failed = False
        collector.post_found.connect(self._on_post)
        collector.batch.connect(self._on_batch)
        collector.api_units.connect(self._on_units)
        collector.progress.connect(self.progress)
        collector.finished.connect(self._on_finished)

    def start(self) -> None:
        self.collector.start(self.link, limit=self.limit, sort=self.sort, since=self.since)

    def stop(self) -> None:
        self.collector.stop()

    def _on_post(self, post: Post, snapshot: Snapshot) -> None:
        def write() -> None:
            with transaction(self._conn):
                post.id = repository.upsert_post(self._conn, post)
                snapshot.post_id = post.id
                repository.add_snapshot(self._conn, post.id, snapshot)
                run = CollectionRun(
                    post.id,
                    self.summary.started_at or utc_now(),
                    account_id=self.account_id,
                    sort_used=self.sort,
                    comment_limit=self.limit,
                )
                self.summary.run_id = repository.start_run(self._conn, run)
            self.summary.post, self.summary.snapshot = post, snapshot

        self._store(write)

    def _on_batch(self, comments: list[Comment]) -> None:
        post_row_id = self.summary.post.id if self.summary.post else None
        if post_row_id is None:
            log.warning("%d comments arrived before their post; ignored", len(comments))
            return
        for comment in comments:
            comment.run_id = self.summary.run_id
            comment.sort_used = self.sort

        def write() -> None:
            with transaction(self._conn):
                self.summary.new += repository.upsert_comments(self._conn, post_row_id, comments)
            self.summary.collected += len(comments)

        self._store(write)

    def _on_units(self, day: str, units: int) -> None:
        def write() -> None:
            with transaction(self._conn):
                repository.add_api_units(self._conn, self.collector.platform.value, day, units)

        self._store(write)

    def _store(self, write: Callable[[], None]) -> None:
        if self._storage_failed:
            return
        try:
            write()
        except sqlite3.Error:
            log.exception("Couldn't store collected data")
            self._storage_failed = True
            self.collector.stop()

    def _count_account_use(self, summary: RunSummary) -> None:
        """For the per-account daily limits in Settings (local day)."""
        assert self.account_id is not None
        today = datetime.now().date().isoformat()
        try:
            with transaction(self._conn):
                repository.add_account_usage(
                    self._conn, self.account_id, today, 1, summary.collected
                )
                repository.mark_account_used(self._conn, self.account_id, utc_now())
                if summary.status is RunStatus.BLOCKED:  # the Accounts page shows it
                    repository.set_account_status(
                        self._conn, self.account_id, AccountStatus.NEEDS_ATTENTION
                    )
        except sqlite3.Error:
            log.exception("Couldn't count the account's use")

    def _on_finished(self, status: RunStatus, message: str) -> None:
        if self._storage_failed:
            status, message = RunStatus.FAILED, STORAGE_ERROR
        summary = self.summary
        summary.status, summary.message, summary.finished_at = status, message, utc_now()
        if summary.run_id is not None:
            reported = summary.snapshot.comments_reported if summary.snapshot else None
            try:
                with transaction(self._conn):
                    repository.finish_run(
                        self._conn,
                        summary.run_id,
                        status,
                        comments_collected=summary.collected,
                        comments_reported=reported,
                        note=message or None,
                    )
            except sqlite3.Error:
                log.exception("Couldn't record the end of the run")
        if self.account_id is not None and summary.run_id is not None:
            self._count_account_use(summary)
        self._conn.close()
        log.info(
            "%s run ended: %s, %d comments (%d new). %s",
            self.collector.platform.label,
            status,
            summary.collected,
            summary.new,
            message,
        )
        self.done.emit(summary)
