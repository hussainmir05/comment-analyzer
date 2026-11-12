"""Records shared by every part of the app.

Collectors for all platforms produce these same records, so analysis, dashboard, tracking and
publishing never depend on the platform.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Platform(StrEnum):
    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Platform.YOUTUBE: "YouTube",
    Platform.FACEBOOK: "Facebook",
    Platform.INSTAGRAM: "Instagram",
    Platform.TIKTOK: "TikTok",
}


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"  # the user pressed Stop; what was read so far is kept
    BLOCKED = "blocked"  # login wall, checkpoint or captcha; the browser went to the user
    FAILED = "failed"


class AccountStatus(StrEnum):
    NEW = "new"
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(slots=True)
class Account:
    platform: Platform
    label: str
    profile_dir: str
    created_at: datetime
    status: AccountStatus = AccountStatus.NEW
    last_used_at: datetime | None = None
    id: int | None = None


@dataclass(slots=True)
class Post:
    platform: Platform
    post_id: str  # the platform's ID for the post
    url: str
    author: str | None = None  # the post's author (a public account), never a commenter
    title: str | None = None
    published_at: datetime | None = None
    first_seen_at: datetime | None = None
    tracked: bool = False
    id: int | None = None  # posts.id


@dataclass(slots=True)
class Snapshot:
    taken_at: datetime
    views: int | None = None
    likes: int | None = None
    comments_reported: int | None = None  # what the platform says, not what we collected
    shares: int | None = None
    saves: int | None = None
    reactions: dict[str, int] = field(default_factory=dict)  # Facebook: {"Like": 120, ...}
    extra: dict[str, Any] = field(default_factory=dict)
    post_id: int | None = None  # posts.id
    id: int | None = None


@dataclass(slots=True)
class Comment:
    platform_comment_id: str
    text: str | None
    created_at: datetime | None = None
    like_count: int | None = None
    reply_count: int | None = None
    parent_id: str | None = None  # platform_comment_id of the parent, for replies
    author_hash: str | None = None  # keyed hash of the commenter's ID, never the name
    lang: str | None = None
    collected_at: datetime | None = None
    sort_used: str | None = None
    run_id: int | None = None
    post_id: int | None = None  # posts.id
    id: int | None = None


@dataclass(slots=True)
class CollectionRun:
    post_id: int  # posts.id
    started_at: datetime
    status: RunStatus = RunStatus.RUNNING
    account_id: int | None = None
    finished_at: datetime | None = None
    sort_used: str | None = None
    comment_limit: int | None = None
    comments_collected: int = 0
    comments_reported: int | None = None
    note: str | None = None
    id: int | None = None


@dataclass(slots=True)
class AnalysisResult:
    comment_id: int
    mode: str  # "local" or "claude"
    model_version: str
    analyzed_at: datetime
    sentiment: str | None = None
    confidence: float | None = None
    low_confidence: bool = False  # e.g. a language the model wasn't trained on
    topic_id: int | None = None
    spam_flag: bool = False


@dataclass(slots=True)
class Topic:
    post_id: int  # posts.id
    mode: str  # "local" or "claude"
    label: str
    keywords: list[str]
    share: float  # fraction of the comments that were grouped (spam left out)
    model_version: str
    created_at: datetime
    summary: str | None = None
    id: int | None = None
