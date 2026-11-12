"""Data the app must not keep.

- YouTube API data older than 30 days (YouTube API Services Developer Policies): comments,
  snapshots and the video's details. Collecting the video again refreshes them.
- Comment text older than the number of days set in Settings (privacy). Counts and analysis
  results stay.
"""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from comment_analyzer.db import Database
from comment_analyzer.db.connection import transaction
from comment_analyzer.timeutil import to_iso, utc_now

log = logging.getLogger(__name__)

YOUTUBE_MAX_AGE = timedelta(days=30)
_YOUTUBE_POSTS = "SELECT id FROM posts WHERE platform = 'youtube'"


def expire_youtube_data(conn: sqlite3.Connection, now: datetime) -> int:
    """Delete YouTube API data older than 30 days. Returns how many comments were deleted."""
    cutoff = to_iso(now - YOUTUBE_MAX_AGE)
    with transaction(conn):
        deleted = conn.execute(
            f"DELETE FROM comments WHERE collected_at < ? AND post_id IN ({_YOUTUBE_POSTS})",
            (cutoff,),
        ).rowcount
        conn.execute(
            f"DELETE FROM snapshots WHERE taken_at < ? AND post_id IN ({_YOUTUBE_POSTS})",
            (cutoff,),
        )
        conn.execute(
            "UPDATE collection_runs SET comments_reported = NULL "
            f"WHERE started_at < ? AND post_id IN ({_YOUTUBE_POSTS})",
            (cutoff,),
        )
        # title, channel and date come from the API too; keep them only while a snapshot is fresh
        conn.execute(
            "UPDATE posts SET title = NULL, author = NULL, published_at = NULL "
            "WHERE platform = 'youtube' "
            "AND NOT EXISTS (SELECT 1 FROM snapshots WHERE snapshots.post_id = posts.id)"
        )
    return deleted


def purge_comment_text(conn: sqlite3.Connection, days: int, now: datetime) -> int:
    """Blank comment text collected more than `days` ago. Returns how many comments changed."""
    cutoff = to_iso(now - timedelta(days=days))
    with transaction(conn):
        return conn.execute(
            "UPDATE comments SET text = NULL WHERE text IS NOT NULL AND collected_at < ?",
            (cutoff,),
        ).rowcount


def close_interrupted_runs(conn: sqlite3.Connection) -> int:
    """Mark runs left 'running' by an app that closed or crashed mid-run. Call at startup only."""
    with transaction(conn):
        return conn.execute(
            "UPDATE collection_runs SET status = 'stopped', finished_at = ?, "
            "note = 'The app closed during this run.' WHERE status = 'running'",
            (to_iso(utc_now()),),
        ).rowcount


def run_maintenance(db: Database, purge_text_days: int) -> None:
    now = utc_now()
    with closing(db.connect()) as conn:
        expired = expire_youtube_data(conn, now)
        purged = purge_comment_text(conn, purge_text_days, now)
    if expired or purged:
        log.info("Maintenance: deleted %d old YouTube comments, blanked %d texts", expired, purged)
