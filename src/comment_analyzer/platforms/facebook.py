"""Facebook: comments and reactions from the JSON a post page receives (GraphQL answers, and the
JSON blocks in the page itself). Ported from the previous version's Facebook reader.
"""

from typing import Any

from comment_analyzer.models import Comment
from comment_analyzer.platforms.web_data import (
    Hasher,
    Parsed,
    PostInfo,
    all_dicts,
    from_unix,
    json_documents,
    parse_count,
    path,
    text_of,
)

REACTIONS = ("Like", "Love", "Care", "Haha", "Wow", "Sad", "Angry")
REACTION_IDS = {
    "1635855486666999": "Like",
    "1678524932434102": "Love",
    "613557422527858": "Care",
    "115940658764963": "Haha",
    "478547315650144": "Wow",
    "908563459236466": "Sad",
    "444813342392137": "Angry",
}


def parse(url: str, body: str, hasher: Hasher) -> Parsed:
    dicts = [d for document in json_documents(body) for d in all_dicts(document)]
    comments: dict[str, Comment] = {}
    for d in dicts:
        comment = _comment(d, hasher)
        if comment is None:
            continue
        old = comments.setdefault(comment.platform_comment_id, comment)
        if old is not comment:  # the same comment twice: keep what each copy knows
            old.like_count = max(old.like_count or 0, comment.like_count or 0)
            old.reply_count = old.reply_count or comment.reply_count
            old.parent_id = old.parent_id or comment.parent_id
    info = _post_info(dicts)
    return Parsed(info, list(comments.values()))


def _comment(d: dict[str, Any], hasher: Hasher) -> Comment | None:
    if d.get("__typename") != "Comment":
        return None
    text = text_of(path(d, "body", "text"))
    comment_id = d.get("legacy_fbid") or d.get("id")
    if text is None or not comment_id:
        return None
    parent = d.get("comment_parent")
    parent_id = None
    if isinstance(parent, dict):
        parent_id = str(parent.get("legacy_fbid") or parent.get("id") or "") or None
    author: dict[str, Any] = d["author"] if isinstance(d.get("author"), dict) else {}
    return Comment(
        str(comment_id),
        text,
        created_at=from_unix(d.get("created_time")),
        like_count=_likes(d),
        reply_count=parse_count(path(d, "feedback", "replies_fields", "total_count")),
        parent_id=parent_id,
        author_hash=hasher(str(author.get("id")) if author.get("id") else None),
    )


def _likes(comment: dict[str, Any]) -> int:
    for d in all_dicts(comment.get("feedback") or {}):
        for key in ("reactors", "reaction_count"):
            value = d.get(key)
            if isinstance(value, dict):
                count = value.get("count")
                n = parse_count(count if count is not None else value.get("count_reduced"))
                if n is not None:
                    return n
    return 0


def _post_info(dicts: list[dict[str, Any]]) -> PostInfo | None:
    reactions = _reactions(dicts)
    message = next(
        (text_of(path(d, "message", "text")) for d in dicts if text_of(path(d, "message", "text"))),
        None,
    )
    author = next(
        (
            text_of(d["actors"][0].get("name"))
            for d in dicts
            if isinstance(d.get("actors"), list)
            and d["actors"]
            and isinstance(d["actors"][0], dict)
            and text_of(d["actors"][0].get("name"))
        ),
        None,
    )
    created = next(
        (from_unix(d["creation_time"]) for d in dicts if "creation_time" in d and "message" in d),
        None,
    )
    info = PostInfo(
        author=author,
        title=message,
        published_at=created,
        views=_biggest(dicts, ("video_view_count",), ("play_count",)),
        comments_reported=_biggest(
            dicts,
            ("comments", "total_count"),
            ("total_comment_count",),
            ("comment_count", "total_count"),
        ),
        shares=_biggest(dicts, ("share_count", "count")),
        reactions=reactions,
    )
    total = _biggest(dicts, ("reaction_count", "count"))
    if total is not None and total > sum(reactions.values()):
        info.likes = total
    known = (info.author, info.title, info.views, info.comments_reported, info.shares, info.likes)
    return info if reactions or any(v is not None for v in known) else None


def _reactions(dicts: list[dict[str, Any]]) -> dict[str, int]:
    best: dict[str, int] = {}
    for d in dicts:
        top = d.get("top_reactions")
        if not isinstance(top, dict):
            continue
        counts: dict[str, int] = {}
        for edge in top.get("edges") or []:
            node = edge.get("node") or {} if isinstance(edge, dict) else {}
            name = (
                REACTION_IDS.get(str(node.get("id")))
                or str(node.get("reaction_type") or node.get("localized_name") or "").title()
            )
            n = parse_count(edge.get("reaction_count"))
            if name and n is not None:
                counts[name] = counts.get(name, 0) + n
        if sum(counts.values()) > sum(best.values()):
            best = counts
    order = [*REACTIONS, *sorted(k for k in best if k not in REACTIONS)]
    return {k: best[k] for k in order if k in best}


def _biggest(dicts: list[dict[str, Any]], *paths: tuple[str, ...]) -> int | None:
    """The largest whole number at any of these key paths: the post's count beats a comment's."""
    found = [n for d in dicts for keys in paths if (n := _int(path(d, *keys))) is not None]
    return max(found) if found else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
