"""Shared by the Facebook, Instagram and TikTok parsers: reading the JSON the sites send their
own pages, and the records the parsers produce.

The parsers search the JSON for objects with a comment's fields rather than following fixed paths,
so a site that moves its data around keeps working as long as the field names stay.
"""

import json
import re
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from comment_analyzer.models import Comment, Post, Snapshot

Hasher = Callable[[str | None], str | None]  # privacy.author_hasher()
_COUNT = re.compile(r"\s*([\d.,]+)\s*([KkMm]?)\s*")
_GUARD = "for (;;);"  # Facebook puts this before some JSON answers


def json_documents(body: str) -> Iterator[Any]:
    """The JSON value(s) in a response: one document, or one per line."""
    text = body.strip().removeprefix(_GUARD)
    try:
        yield json.loads(text)
        return
    except ValueError:
        pass
    for line in text.splitlines():
        line = line.strip().removeprefix(_GUARD)
        if line.startswith(("{", "[")):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def all_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Every object inside a JSON tree, outermost first."""
    todo = deque([node])
    while todo:
        item = todo.popleft()
        if isinstance(item, dict):
            yield item
            todo.extend(item.values())
        elif isinstance(item, list):
            todo.extend(item)


def parse_count(value: Any) -> int | None:
    """Counts arrive as 1234, "1,234", "1.2K" or "3M"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = _COUNT.fullmatch(str(value if value is not None else ""))
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(number * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()])


def from_unix(value: Any) -> datetime | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def text_of(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def path(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


@dataclass(slots=True)
class PostInfo:
    """What a page says about the post. Several responses can each add a part."""

    author: str | None = None  # the post's author (a public account), never a commenter
    title: str | None = None
    published_at: datetime | None = None
    views: int | None = None
    likes: int | None = None
    comments_reported: int | None = None
    shares: int | None = None
    saves: int | None = None
    reactions: dict[str, int] = field(default_factory=dict)

    def merge(self, other: "PostInfo") -> None:
        """Keep what's known; counts take the bigger value (the post's own beats a comment's)."""
        for name in ("author", "title", "published_at"):
            if getattr(self, name) is None:
                setattr(self, name, getattr(other, name))
        for name in ("views", "likes", "comments_reported", "shares", "saves"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if theirs is not None and (mine is None or theirs > mine):
                setattr(self, name, theirs)
        if sum(other.reactions.values()) > sum(self.reactions.values()):
            self.reactions = dict(other.reactions)

    def records(self, base: Post, taken_at: datetime) -> tuple[Post, Snapshot]:
        post = Post(
            base.platform,
            base.post_id,
            base.url,
            author=self.author,
            title=self.title,
            published_at=self.published_at,
        )
        snapshot = Snapshot(
            taken_at,
            views=self.views,
            likes=self.likes if self.likes is not None else sum(self.reactions.values()) or None,
            comments_reported=self.comments_reported,
            shares=self.shares,
            saves=self.saves,
            reactions=dict(self.reactions),
        )
        return post, snapshot


@dataclass(slots=True)
class Parsed:
    post: PostInfo | None = None
    comments: list[Comment] = field(default_factory=list)


Parser = Callable[[str, str, Hasher], Parsed]  # (response URL, body, hasher)
