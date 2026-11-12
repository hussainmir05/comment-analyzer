"""TikTok: comments from the comment-list answers a video page receives, and the video's details
from the JSON in the page itself. Field names come from the previous version's TikTok reader.
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
    info: PostInfo | None = None
    for d in dicts:
        comment = _comment(d, hasher)
        if comment is not None:
            comments.setdefault(comment.platform_comment_id, comment)
        elif _is_video(d):
            video = _video(d)
            if info is None:
                info = video
            else:
                info.merge(video)
    return Parsed(info, list(comments.values()))


def _comment(d: dict[str, Any], hasher: Hasher) -> Comment | None:
    if "cid" not in d or "create_time" not in d or not isinstance(d.get("user"), dict):
        return None
    text = text_of(d.get("text"))
    if text is None or not d["cid"]:
        return None
    user = d["user"]
    parent = str(d.get("reply_id") or "")
    return Comment(
        str(d["cid"]),
        text,
        created_at=from_unix(d.get("create_time")),
        like_count=parse_count(d.get("digg_count")) or 0,
        reply_count=parse_count(d.get("reply_comment_total")),
        parent_id=parent if parent not in ("", "0") else None,
        author_hash=hasher(str(user.get("uid") or user.get("sec_uid") or "") or None),
    )


def _is_video(d: dict[str, Any]) -> bool:
    return "createTime" in d and isinstance(d.get("stats"), dict) and "desc" in d


def _video(d: dict[str, Any]) -> PostInfo:
    stats = d["stats"]
    stats_v2: dict[str, Any] = d["statsV2"] if isinstance(d.get("statsV2"), dict) else {}

    def count(key: str) -> int | None:
        return parse_count(stats_v2.get(key)) or parse_count(stats.get(key))

    return PostInfo(
        author=text_of(path(d, "author", "uniqueId")) or text_of(path(d, "author", "nickname")),
        title=text_of(d.get("desc")),
        published_at=from_unix(d.get("createTime")),
        views=count("playCount"),
        likes=count("diggCount"),
        comments_reported=count("commentCount"),
        shares=count("shareCount"),
        saves=count("collectCount"),
    )
