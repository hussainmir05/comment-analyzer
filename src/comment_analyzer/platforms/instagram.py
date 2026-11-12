"""Instagram: comments and post details from the JSON a post page receives (its web API and
GraphQL answers). Field names come from the previous version's Instagram reader.
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


def parse(url: str, body: str, hasher: Hasher) -> Parsed:
    dicts = [d for document in json_documents(body) for d in all_dicts(document)]
    comments: dict[str, Comment] = {}
    info = PostInfo()
    found_post = False
    for d in dicts:
        comment = _comment(d, hasher)
        if comment is not None:
            comments.setdefault(comment.platform_comment_id, comment)
        elif _is_media(d):
            info.merge(_media(d))
            found_post = True
    return Parsed(info if found_post else None, list(comments.values()))


def _comment(d: dict[str, Any], hasher: Hasher) -> Comment | None:
    if "comment_like_count" not in d or not isinstance(d.get("user"), dict):
        return None
    text = text_of(d.get("text"))
    comment_id = d.get("pk") or d.get("id")
    if text is None or not comment_id:
        return None
    user = d["user"]
    parent = d.get("parent_comment_id")
    return Comment(
        str(comment_id),
        text,
        created_at=from_unix(d.get("created_at")),
        like_count=parse_count(d.get("comment_like_count")) or 0,
        reply_count=parse_count(d.get("child_comment_count")),
        parent_id=str(parent) if parent else None,
        author_hash=hasher(str(user.get("pk") or user.get("id") or "") or None),
    )


def _is_media(d: dict[str, Any]) -> bool:
    return "taken_at" in d and ("code" in d or "caption" in d) and "like_count" in d


def _media(d: dict[str, Any]) -> PostInfo:
    views = next(
        (parse_count(d[k]) for k in ("play_count", "ig_play_count", "view_count") if d.get(k)),
        None,
    )
    return PostInfo(
        author=text_of(path(d, "user", "username")) or text_of(path(d, "owner", "username")),
        title=text_of(path(d, "caption", "text")),
        published_at=from_unix(d.get("taken_at")),
        views=views,
        likes=parse_count(d.get("like_count")),
        comments_reported=parse_count(d.get("comment_count")),
    )
