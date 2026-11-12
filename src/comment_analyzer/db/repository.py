"""Read and write records.

Every function takes an open connection; callers group writes with db.connection.transaction().
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from comment_analyzer.models import (
    Account,
    AccountStatus,
    AnalysisResult,
    CollectionRun,
    Comment,
    Platform,
    Post,
    RunStatus,
    Snapshot,
    Topic,
)
from comment_analyzer.timeutil import from_iso, parse_iso, to_iso, utc_now

# --- posts ------------------------------------------------------------------------------------


def upsert_post(conn: sqlite3.Connection, post: Post) -> int:
    """Insert the post, or fill in what's new about it. Returns its row ID (posts.id).

    Known details are never erased by a later run that lacks them, and first_seen_at and
    tracked keep their original values.
    """
    row = conn.execute(
        """
        INSERT INTO posts (platform, post_id, url, author, title, published_at,
                           first_seen_at, tracked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (platform, post_id) DO UPDATE SET
            url = excluded.url,
            author = COALESCE(excluded.author, author),
            title = COALESCE(excluded.title, title),
            published_at = COALESCE(excluded.published_at, published_at)
        RETURNING id
        """,
        (
            post.platform.value,
            post.post_id,
            post.url,
            post.author,
            post.title,
            to_iso(post.published_at),
            to_iso(post.first_seen_at or utc_now()),
            int(post.tracked),
        ),
    ).fetchone()
    return int(row[0])


def get_post(conn: sqlite3.Connection, platform: Platform, post_id: str) -> Post | None:
    row = conn.execute(
        "SELECT * FROM posts WHERE platform = ? AND post_id = ?", (platform.value, post_id)
    ).fetchone()
    return _post(row) if row else None


def get_post_by_id(conn: sqlite3.Connection, post_row_id: int) -> Post | None:
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_row_id,)).fetchone()
    return _post(row) if row else None


def list_posts(conn: sqlite3.Connection, limit: int = 50) -> list[Post]:
    """Most recently added first."""
    rows = conn.execute(
        "SELECT * FROM posts ORDER BY first_seen_at DESC, id DESC LIMIT ?", (limit,)
    )
    return [_post(row) for row in rows]


def set_tracked(conn: sqlite3.Connection, post_row_id: int, tracked: bool) -> None:
    conn.execute("UPDATE posts SET tracked = ? WHERE id = ?", (int(tracked), post_row_id))


def list_tracked(conn: sqlite3.Connection) -> list[Post]:
    rows = conn.execute("SELECT * FROM posts WHERE tracked = 1 ORDER BY id")
    return [_post(row) for row in rows]


def _post(row: sqlite3.Row) -> Post:
    return Post(
        platform=Platform(row["platform"]),
        post_id=row["post_id"],
        url=row["url"],
        author=row["author"],
        title=row["title"],
        published_at=from_iso(row["published_at"]),
        first_seen_at=from_iso(row["first_seen_at"]),
        tracked=bool(row["tracked"]),
        id=row["id"],
    )


# --- snapshots --------------------------------------------------------------------------------


def add_snapshot(conn: sqlite3.Connection, post_row_id: int, snapshot: Snapshot) -> int:
    row = conn.execute(
        """
        INSERT INTO snapshots (post_id, taken_at, views, likes, comments_reported, shares,
                               saves, reactions_json, extra_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            post_row_id,
            to_iso(snapshot.taken_at),
            snapshot.views,
            snapshot.likes,
            snapshot.comments_reported,
            snapshot.shares,
            snapshot.saves,
            _json(snapshot.reactions),
            _json(snapshot.extra),
        ),
    ).fetchone()
    return int(row[0])


def latest_snapshot(conn: sqlite3.Connection, post_row_id: int) -> Snapshot | None:
    row = conn.execute(
        "SELECT * FROM snapshots WHERE post_id = ? ORDER BY taken_at DESC, id DESC LIMIT 1",
        (post_row_id,),
    ).fetchone()
    return _snapshot(row) if row else None


def list_snapshots(conn: sqlite3.Connection, post_row_id: int) -> list[Snapshot]:
    """Oldest first."""
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE post_id = ? ORDER BY taken_at, id", (post_row_id,)
    )
    return [_snapshot(row) for row in rows]


def _snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        taken_at=parse_iso(row["taken_at"]),
        views=row["views"],
        likes=row["likes"],
        comments_reported=row["comments_reported"],
        shares=row["shares"],
        saves=row["saves"],
        reactions=json.loads(row["reactions_json"] or "{}"),
        extra=json.loads(row["extra_json"] or "{}"),
        post_id=row["post_id"],
        id=row["id"],
    )


def _json(value: dict[str, Any]) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None


# --- collection runs --------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, run: CollectionRun) -> int:
    row = conn.execute(
        """
        INSERT INTO collection_runs (post_id, account_id, started_at, status, sort_used,
                                     comment_limit)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            run.post_id,
            run.account_id,
            to_iso(run.started_at),
            run.status.value,
            run.sort_used,
            run.comment_limit,
        ),
    ).fetchone()
    return int(row[0])


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: RunStatus,
    *,
    comments_collected: int,
    comments_reported: int | None = None,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE collection_runs
        SET status = ?, finished_at = ?, comments_collected = ?, comments_reported = ?, note = ?
        WHERE id = ?
        """,
        (status.value, to_iso(utc_now()), comments_collected, comments_reported, note, run_id),
    )


def get_run(conn: sqlite3.Connection, run_id: int) -> CollectionRun | None:
    row = conn.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def latest_run(conn: sqlite3.Connection, post_row_id: int) -> CollectionRun | None:
    row = conn.execute(
        "SELECT * FROM collection_runs WHERE post_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
        (post_row_id,),
    ).fetchone()
    return _run(row) if row else None


def _run(row: sqlite3.Row) -> CollectionRun:
    return CollectionRun(
        post_id=row["post_id"],
        started_at=parse_iso(row["started_at"]),
        status=RunStatus(row["status"]),
        account_id=row["account_id"],
        finished_at=from_iso(row["finished_at"]),
        sort_used=row["sort_used"],
        comment_limit=row["comment_limit"],
        comments_collected=row["comments_collected"],
        comments_reported=row["comments_reported"],
        note=row["note"],
        id=row["id"],
    )


# --- comments ---------------------------------------------------------------------------------

_UPSERT_COMMENT = """
    INSERT INTO comments (post_id, platform_comment_id, parent_id, author_hash, text, lang,
                          created_at, like_count, reply_count, collected_at, sort_used, run_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (post_id, platform_comment_id) DO UPDATE SET
        text = COALESCE(excluded.text, text),
        like_count = COALESCE(excluded.like_count, like_count),
        reply_count = COALESCE(excluded.reply_count, reply_count),
        collected_at = excluded.collected_at,
        sort_used = COALESCE(excluded.sort_used, sort_used),
        run_id = COALESCE(excluded.run_id, run_id)
"""


def upsert_comments(conn: sqlite3.Connection, post_row_id: int, comments: Iterable[Comment]) -> int:
    """Store comments. Ones already stored get fresh text and counts. Returns how many were new."""
    now = utc_now()
    before = count_comments(conn, post_row_id)
    conn.executemany(
        _UPSERT_COMMENT,
        (
            (
                post_row_id,
                c.platform_comment_id,
                c.parent_id,
                c.author_hash,
                c.text,
                c.lang,
                to_iso(c.created_at),
                c.like_count,
                c.reply_count,
                to_iso(c.collected_at or now),
                c.sort_used,
                c.run_id,
            )
            for c in comments
        ),
    )
    return count_comments(conn, post_row_id) - before


def newest_comment_time(conn: sqlite3.Connection, post_row_id: int) -> datetime | None:
    row = conn.execute(
        "SELECT MAX(created_at) FROM comments WHERE post_id = ?", (post_row_id,)
    ).fetchone()
    return from_iso(row[0]) if row and row[0] else None


def count_comments(conn: sqlite3.Connection, post_row_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_row_id,))
    return int(row.fetchone()[0])


def list_comments(conn: sqlite3.Connection, post_row_id: int) -> list[Comment]:
    """Newest first."""
    rows = conn.execute(
        "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at DESC, id DESC",
        (post_row_id,),
    )
    return [_comment(row) for row in rows]


def _comment(row: sqlite3.Row) -> Comment:
    return Comment(
        platform_comment_id=row["platform_comment_id"],
        text=row["text"],
        created_at=from_iso(row["created_at"]),
        like_count=row["like_count"],
        reply_count=row["reply_count"],
        parent_id=row["parent_id"],
        author_hash=row["author_hash"],
        lang=row["lang"],
        collected_at=from_iso(row["collected_at"]),
        sort_used=row["sort_used"],
        run_id=row["run_id"],
        post_id=row["post_id"],
        id=row["id"],
    )


def set_comment_languages(conn: sqlite3.Connection, languages: Iterable[tuple[int, str]]) -> None:
    """(comment row ID, language code) pairs."""
    conn.executemany(
        "UPDATE comments SET lang = ? WHERE id = ?", ((lang, row_id) for row_id, lang in languages)
    )


# --- analysis ---------------------------------------------------------------------------------


def upsert_analysis(conn: sqlite3.Connection, results: Iterable[AnalysisResult]) -> None:
    conn.executemany(
        """
        INSERT INTO analysis (comment_id, mode, sentiment, confidence, low_confidence, topic_id,
                              spam_flag, model_version, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (comment_id, mode) DO UPDATE SET
            sentiment = excluded.sentiment,
            confidence = excluded.confidence,
            low_confidence = excluded.low_confidence,
            topic_id = excluded.topic_id,
            spam_flag = excluded.spam_flag,
            model_version = excluded.model_version,
            analyzed_at = excluded.analyzed_at
        """,
        (
            (
                r.comment_id,
                r.mode,
                r.sentiment,
                r.confidence,
                int(r.low_confidence),
                r.topic_id,
                int(r.spam_flag),
                r.model_version,
                to_iso(r.analyzed_at),
            )
            for r in results
        ),
    )


def analysis_for_post(
    conn: sqlite3.Connection, post_row_id: int, mode: str = "local"
) -> dict[int, AnalysisResult]:
    """Analysis results of a post's comments, by comment row ID."""
    rows = conn.execute(
        """
        SELECT a.* FROM analysis a JOIN comments c ON c.id = a.comment_id
        WHERE c.post_id = ? AND a.mode = ?
        """,
        (post_row_id, mode),
    )
    return {
        row["comment_id"]: AnalysisResult(
            comment_id=row["comment_id"],
            mode=row["mode"],
            model_version=row["model_version"],
            analyzed_at=parse_iso(row["analyzed_at"]),
            sentiment=row["sentiment"],
            confidence=row["confidence"],
            low_confidence=bool(row["low_confidence"]),
            topic_id=row["topic_id"],
            spam_flag=bool(row["spam_flag"]),
        )
        for row in rows
    }


def replace_topics(
    conn: sqlite3.Connection, post_row_id: int, mode: str, topics: list[Topic]
) -> list[int]:
    """Replace a post's topics for one mode. Returns the new topic IDs, in order. Analysis rows
    that pointed at the old topics lose their topic (ON DELETE SET NULL)."""
    conn.execute("DELETE FROM topics WHERE post_id = ? AND mode = ?", (post_row_id, mode))
    return [
        conn.execute(
            """
            INSERT INTO topics (post_id, mode, label, keywords, share, summary, model_version,
                                created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                post_row_id,
                mode,
                t.label,
                json.dumps(t.keywords, ensure_ascii=False),
                t.share,
                t.summary,
                t.model_version,
                to_iso(t.created_at),
            ),
        ).fetchone()[0]
        for t in topics
    ]


def list_topics(conn: sqlite3.Connection, post_row_id: int, mode: str = "local") -> list[Topic]:
    """Biggest first."""
    rows = conn.execute(
        "SELECT * FROM topics WHERE post_id = ? AND mode = ? ORDER BY share DESC, id",
        (post_row_id, mode),
    )
    return [
        Topic(
            post_id=row["post_id"],
            mode=row["mode"],
            label=row["label"],
            keywords=json.loads(row["keywords"] or "[]"),
            share=row["share"],
            model_version=row["model_version"],
            created_at=parse_iso(row["created_at"]),
            summary=row["summary"],
            id=row["id"],
        )
        for row in rows
    ]


# --- official-API quota -----------------------------------------------------------------------


def add_api_units(conn: sqlite3.Connection, platform: str, day: str, units: int) -> None:
    conn.execute(
        """
        INSERT INTO api_usage (platform, day, units) VALUES (?, ?, ?)
        ON CONFLICT (platform, day) DO UPDATE SET units = units + excluded.units
        """,
        (platform, day, units),
    )


def api_units_used(conn: sqlite3.Connection, platform: str, day: str) -> int:
    row = conn.execute(
        "SELECT units FROM api_usage WHERE platform = ? AND day = ?", (platform, day)
    ).fetchone()
    return int(row[0]) if row else 0


# --- browser accounts -------------------------------------------------------------------------


def add_account(conn: sqlite3.Connection, account: Account) -> int:
    return conn.execute(
        """
        INSERT INTO accounts (platform, label, profile_dir, status, created_at)
        VALUES (?, ?, ?, ?, ?) RETURNING id
        """,
        (
            account.platform.value,
            account.label,
            account.profile_dir,
            account.status.value,
            to_iso(account.created_at),
        ),
    ).fetchone()[0]


def get_account(conn: sqlite3.Connection, account_id: int) -> Account | None:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return _account(row) if row else None


def list_accounts(conn: sqlite3.Connection, platform: Platform | None = None) -> list[Account]:
    if platform is None:
        rows = conn.execute("SELECT * FROM accounts ORDER BY platform, id")
    else:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE platform = ? ORDER BY id", (platform.value,)
        )
    return [_account(row) for row in rows]


def set_account_status(conn: sqlite3.Connection, account_id: int, status: AccountStatus) -> None:
    conn.execute("UPDATE accounts SET status = ? WHERE id = ?", (status.value, account_id))


def mark_account_used(conn: sqlite3.Connection, account_id: int, when: datetime) -> None:
    conn.execute("UPDATE accounts SET last_used_at = ? WHERE id = ?", (to_iso(when), account_id))


def delete_account(conn: sqlite3.Connection, account_id: int) -> None:
    """Its runs keep their data with no account (ON DELETE SET NULL); its usage goes."""
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def _account(row: sqlite3.Row) -> Account:
    return Account(
        platform=Platform(row["platform"]),
        label=row["label"],
        profile_dir=row["profile_dir"],
        created_at=parse_iso(row["created_at"]),
        status=AccountStatus(row["status"]),
        last_used_at=from_iso(row["last_used_at"]),
        id=row["id"],
    )


def add_account_usage(
    conn: sqlite3.Connection, account_id: int, day: str, posts: int, comments: int
) -> None:
    conn.execute(
        """
        INSERT INTO account_usage (account_id, day, posts, comments) VALUES (?, ?, ?, ?)
        ON CONFLICT (account_id, day) DO UPDATE SET
            posts = posts + excluded.posts, comments = comments + excluded.comments
        """,
        (account_id, day, posts, comments),
    )


def account_usage(conn: sqlite3.Connection, account_id: int, day: str) -> tuple[int, int]:
    """(posts, comments) read with the account on that day."""
    row = conn.execute(
        "SELECT posts, comments FROM account_usage WHERE account_id = ? AND day = ?",
        (account_id, day),
    ).fetchone()
    return (row["posts"], row["comments"]) if row else (0, 0)


# --- publishing -------------------------------------------------------------------------------


def add_published(
    conn: sqlite3.Connection,
    *,
    post_row_id: int | None,
    platform: str,
    target: str,
    text: str,
    media_path: str | None,
    remote_id: str | None,
    when: datetime,
) -> int:
    return conn.execute(
        """
        INSERT INTO published (post_id, platform, target, text, media_path, published_at,
                               remote_id)
        VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id
        """,
        (post_row_id, platform, target, text, media_path, to_iso(when), remote_id),
    ).fetchone()[0]


def published_with_text(conn: sqlite3.Connection, text: str) -> list[tuple[str, str]]:
    """(platform, target) of everything already published with this text (spacing ignored)."""
    wanted = " ".join(text.split())
    rows = conn.execute("SELECT platform, target, text FROM published")
    return [(r["platform"], r["target"]) for r in rows if " ".join(r["text"].split()) == wanted]
