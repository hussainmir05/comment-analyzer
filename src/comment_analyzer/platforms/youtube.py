"""YouTube collector, using the official YouTube Data API v3 and the user's own API key.

Every request costs 1 quota unit, whether it's videos.list, commentThreads.list or comments.list.
The free quota is 10,000 units a day and resets at midnight Pacific Time, so units are counted by
Pacific day. The key travels in the X-Goog-Api-Key header, so it never appears in a URL.
"""

import functools
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx2
from PySide6.QtCore import QObject

from comment_analyzer.errors import UserError
from comment_analyzer.models import Comment, Platform, Post, RunStatus, Snapshot
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo
from comment_analyzer.timeutil import utc_now
from comment_analyzer.workers import Cancelled, CancelToken, ProgressFn, TaskRunner, Worker

log = logging.getLogger(__name__)

API_ROOT = "https://www.googleapis.com/youtube/v3/"
DAILY_QUOTA = 10_000
PACIFIC = ZoneInfo("America/Los_Angeles")
PAGE_SIZE = 100
MAX_ATTEMPTS = 5
SORTS = {"relevance": "Top comments first", "time": "Newest first"}

Json = dict[str, Any]
HashAuthor = Callable[[str | None], str | None]

VIDEO_NOT_FOUND = "YouTube couldn't find this video. It may be private, deleted or age-restricted."
QUOTA_USED_UP = (
    "Today's YouTube quota (10,000 units) is used up. It resets at midnight Pacific Time."
)
_KEY_INVALID = (
    "YouTube says the API key isn't valid. Copy it again from Google Cloud Console and save "
    "it in Settings."
)
_KEY_EXPIRED = (
    "This API key no longer works: it was deleted or has expired in Google Cloud Console. "
    "Save a current key in Settings."
)
_API_OFF = (
    "YouTube Data API v3 isn't turned on for this key's Google Cloud project. Turn it on in "
    "Google Cloud Console (APIs & Services > Library), wait a minute, then try again."
)
_API_BLOCKED = (
    "This API key isn't allowed to use YouTube Data API v3. In Google Cloud Console, add "
    "YouTube Data API v3 to the key's API restrictions."
)
_APP_BLOCKED = (
    "This API key only works for certain websites, apps or IP addresses. In Google Cloud "
    "Console, set the key's application restriction to None."
)
_ACCESS_DENIED = (
    'Google refused this key for YouTube ("Permission denied"), so the key\'s Google Cloud '
    "project isn't allowed to use YouTube Data API v3. Create the key in a project where that "
    "API is turned on, as a plain API key that isn't tied to a service account. If you used a "
    "school or work Google account, try a personal one: organizations can block YouTube."
)
_KEY_PROBLEMS = {
    "ACCESS_DENIED": _ACCESS_DENIED,
    "keyInvalid": _KEY_INVALID,
    "API_KEY_INVALID": _KEY_INVALID,
    "accessNotConfigured": _API_OFF,
    "SERVICE_DISABLED": _API_OFF,
    "API_KEY_SERVICE_BLOCKED": _API_BLOCKED,
    "API_KEY_HTTP_REFERRER_BLOCKED": _APP_BLOCKED,
    "API_KEY_IP_ADDRESS_BLOCKED": _APP_BLOCKED,
    "API_KEY_ANDROID_APP_BLOCKED": _APP_BLOCKED,
    "API_KEY_IOS_APP_BLOCKED": _APP_BLOCKED,
}
_RETRY_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "processingFailure",
    "backendError",
    "internalError",
}
_RETRY_STATUS = {429, 500, 502, 503, 504}


class YouTubeError(UserError):
    """A problem reported by YouTube, worded for the user."""


class QuotaExceeded(YouTubeError):
    pass


class CommentsDisabled(YouTubeError):
    pass


def quota_day(now: datetime | None = None) -> str:
    """The quota day (Pacific Time) that a request made now counts toward, e.g. "2026-11-12"."""
    return (now or utc_now()).astimezone(PACIFIC).date().isoformat()


# --- API client -------------------------------------------------------------------------------


class YouTubeApi:
    """The list calls the collector needs, with retries.

    on_unit is called for every request YouTube answers, failed ones included: they cost quota
    too.
    """

    def __init__(
        self,
        key: str,
        *,
        on_unit: Callable[[], None] | None = None,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx2.Client(
            base_url=API_ROOT,
            headers={"X-Goog-Api-Key": key},
            timeout=30.0,
            transport=transport,
        )
        self._on_unit = on_unit or (lambda: None)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def video(self, video_id: str) -> Json | None:
        items = self._get("videos", {"part": "snippet,statistics", "id": video_id}).get("items")
        return items[0] if items else None

    def comment_threads(self, video_id: str, *, order: str, page_token: str | None = None) -> Json:
        params = {
            "part": "snippet,replies",
            "videoId": video_id,
            "order": order,
            "maxResults": PAGE_SIZE,
            "textFormat": "plainText",
        }
        return self._get("commentThreads", _with_page(params, page_token))

    def replies(self, parent_id: str, *, page_token: str | None = None) -> Json:
        params = {
            "part": "snippet",
            "parentId": parent_id,
            "maxResults": PAGE_SIZE,
            "textFormat": "plainText",
        }
        return self._get("comments", _with_page(params, page_token))

    def check_key(self) -> None:
        """Raise YouTubeError unless YouTube accepts the key."""
        self._get("videoCategories", {"part": "snippet", "regionCode": "US"})

    def _get(self, endpoint: str, params: dict[str, Any]) -> Json:
        for attempt in range(MAX_ATTEMPTS):
            wait = float(min(2**attempt, 30))
            try:
                response = self._client.get(endpoint, params=params)
            except httpx2.TransportError as e:  # no connection, timeout...
                log.warning(
                    "YouTube request failed (%s), attempt %d", type(e).__name__, attempt + 1
                )
                self._sleep(wait)
                continue
            self._on_unit()
            if response.status_code == 200:
                return cast(Json, response.json())
            retry, error = _problem(response)
            if not retry:
                raise error
            self._sleep(_retry_after(response) or wait)
        raise YouTubeError(
            "YouTube isn't responding. Check your internet connection and try again."
        )


def check_key(key: str, *, transport: httpx2.BaseTransport | None = None) -> None:
    """Raise YouTubeError unless YouTube accepts the key. Costs 1 quota unit."""
    api = YouTubeApi(key, transport=transport)
    try:
        api.check_key()
    finally:
        api.close()


def _with_page(params: dict[str, Any], page_token: str | None) -> dict[str, Any]:
    return {**params, "pageToken": page_token} if page_token else params


def _problem(response: httpx2.Response) -> tuple[bool, YouTubeError]:
    """(retry?, error) for a failed response."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    found = [e.get("reason", "") for e in error.get("errors") or [] if isinstance(e, dict)]
    found += [d.get("reason", "") for d in error.get("details") or [] if isinstance(d, dict)]
    reasons = set(found)
    message = str(error.get("message") or "")
    log.warning("YouTube answered %s %s: %s", response.status_code, sorted(reasons), message)

    if "commentsDisabled" in reasons:
        return False, CommentsDisabled("Comments are turned off for this video.")
    if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
        return False, QuotaExceeded(QUOTA_USED_UP)
    if "API_KEY_INVALID" in reasons and "expired" in message.lower():
        return False, YouTubeError(_KEY_EXPIRED)  # also what Google says about deleted keys
    for reason in found:
        if reason in _KEY_PROBLEMS:
            return False, YouTubeError(_KEY_PROBLEMS[reason])
    if "API key not valid" in message:
        return False, YouTubeError(_KEY_INVALID)
    if response.status_code in _RETRY_STATUS or reasons & _RETRY_REASONS:
        return True, YouTubeError("YouTube is busy.")
    if "videoNotFound" in reasons or response.status_code == 404:
        return False, YouTubeError(VIDEO_NOT_FOUND)
    if "forbidden" in reasons:
        return False, YouTubeError("YouTube won't share this video's comments.")
    return False, YouTubeError(
        f"YouTube returned an error ({response.status_code}). The details are in the log."
    )


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        return min(float(response.headers["Retry-After"]), 60.0)
    except (KeyError, ValueError):
        return None


# --- the dislike estimate (opt-in) ------------------------------------------------------------

DISLIKES_API = "https://returnyoutubedislikeapi.com/votes"
DISLIKES_KEY = "dislikes_estimate"  # in Snapshot.extra


def dislike_estimate(video_id: str, *, transport: httpx2.BaseTransport | None = None) -> int | None:
    """YouTube hid dislike counts in 2021. The Return YouTube Dislike project estimates them
    from its browser extension's users. Off by default (Settings): when on, the video's ID goes
    to that project, and nothing else (never the API key). None when it has no answer."""
    try:
        with httpx2.Client(timeout=10.0, transport=transport) as client:
            response = client.get(DISLIKES_API, params={"videoId": video_id})
        value = response.json().get("dislikes") if response.status_code == 200 else None
    except (httpx2.HTTPError, ValueError, AttributeError):
        log.info("No dislike estimate for %s", video_id)
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


# --- parsing ----------------------------------------------------------------------------------


def parse_video(item: Json, now: datetime) -> tuple[Post, Snapshot]:
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    video_id = str(item["id"])
    post = Post(
        platform=Platform.YOUTUBE,
        post_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        author=snippet.get("channelTitle") or None,
        title=snippet.get("title") or None,
        published_at=_time(snippet.get("publishedAt")),
    )
    snapshot = Snapshot(
        taken_at=now,
        views=_int(stats.get("viewCount")),
        likes=_int(stats.get("likeCount")),
        comments_reported=_int(stats.get("commentCount")),
    )
    return post, snapshot


def parse_comment(
    item: Json,
    *,
    parent_id: str | None,
    hash_author: HashAuthor,
    now: datetime,
    reply_count: int | None = None,
) -> Comment:
    snippet = item.get("snippet") or {}
    author_id = (snippet.get("authorChannelId") or {}).get("value")
    return Comment(
        platform_comment_id=str(item["id"]),
        text=snippet.get("textDisplay"),
        created_at=_time(snippet.get("publishedAt")),
        like_count=_int(snippet.get("likeCount")),
        reply_count=reply_count,
        parent_id=parent_id,
        author_hash=hash_author(author_id),
        collected_at=now,
    )


def parse_thread(
    item: Json, *, hash_author: HashAuthor, now: datetime
) -> tuple[Comment, list[Comment], int]:
    """The top-level comment, the replies included with it, and how many replies it has in all."""
    snippet = item.get("snippet") or {}
    total = _int(snippet.get("totalReplyCount")) or 0
    top = parse_comment(
        snippet["topLevelComment"],
        parent_id=None,
        hash_author=hash_author,
        now=now,
        reply_count=total,
    )
    included = [
        parse_comment(r, parent_id=top.platform_comment_id, hash_author=hash_author, now=now)
        for r in (item.get("replies") or {}).get("comments") or []
    ]
    return top, included, total


def _int(value: object) -> int | None:
    try:
        return None if value is None else int(cast(str, value))
    except (TypeError, ValueError):
        return None


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- collecting -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CollectRequest:
    video_id: str
    limit: int
    order: str = "relevance"  # or "time" (newest first)
    since: datetime | None = None  # with order="time": stop at comments older than this


class _LimitReached(Exception):
    pass


class _Reader:
    """Counts comments against the limit and passes them on."""

    def __init__(
        self,
        limit: int,
        reported: int | None,
        emit_batch: Callable[[list[Comment]], None],
        progress: ProgressFn,
    ) -> None:
        self.limit = limit
        self.expected = min(limit, reported) if reported is not None else limit
        self.count = 0
        self._emit_batch = emit_batch
        self._progress = progress

    def add(self, comments: list[Comment]) -> None:
        kept = comments[: self.limit - self.count]
        if kept:
            self._emit_batch(kept)
            self.count += len(kept)
        total = max(self.expected, self.count)
        self._progress(self.count, total, f"Read {self.count:,} of about {total:,} comments")
        if self.count >= self.limit:
            raise _LimitReached


def collect(
    api: YouTubeApi,
    request: CollectRequest,
    *,
    hash_author: HashAuthor,
    emit_post: Callable[[Post, Snapshot], None],
    emit_batch: Callable[[list[Comment]], None],
    progress: ProgressFn,
    token: CancelToken,
    dislikes: Callable[[str], int | None] | None = None,
) -> tuple[RunStatus, str]:
    """Read the video's details and comments, passing them on as they arrive.

    Returns (status, message). Stop, the comment limit and running out of quota keep what was
    read; other problems raise YouTubeError.
    """
    now = utc_now()
    item = api.video(request.video_id)
    if item is None:
        raise YouTubeError(VIDEO_NOT_FOUND)
    post, snapshot = parse_video(item, now)
    if dislikes is not None and (estimate := dislikes(request.video_id)) is not None:
        snapshot.extra[DISLIKES_KEY] = estimate
    emit_post(post, snapshot)
    reader = _Reader(request.limit, snapshot.comments_reported, emit_batch, progress)
    try:
        for thread in _thread_items(api, request, token):
            top, included, total = parse_thread(thread, hash_author=hash_author, now=now)
            if request.since and top.created_at and top.created_at < request.since:
                return RunStatus.COMPLETED, "Read the comments posted since the last run."
            if total <= len(included):
                reader.add([top, *included])
                continue
            # commentThreads includes only a few replies, so fetch the whole thread's replies
            reader.add([top])
            for page in _reply_pages(api, top.platform_comment_id, token):
                reader.add(
                    [
                        parse_comment(
                            r, parent_id=top.platform_comment_id, hash_author=hash_author, now=now
                        )
                        for r in page
                    ]
                )
    except _LimitReached:
        return (
            RunStatus.COMPLETED,
            f"Stopped at your limit of {request.limit:,} comments. You can change it in Settings.",
        )
    except CommentsDisabled as e:
        return RunStatus.COMPLETED, str(e)
    except Cancelled:
        return RunStatus.STOPPED, f"Stopped. Kept the {reader.count:,} comments read so far."
    except QuotaExceeded as e:
        if reader.count == 0:
            raise
        return RunStatus.STOPPED, f"{e} Kept the {reader.count:,} comments read so far."
    return RunStatus.COMPLETED, ""


def _thread_items(api: YouTubeApi, request: CollectRequest, token: CancelToken) -> Iterator[Json]:
    order = "time" if request.since else request.order
    page_token: str | None = None
    while True:
        token.check()
        page = api.comment_threads(request.video_id, order=order, page_token=page_token)
        yield from page.get("items") or []
        page_token = page.get("nextPageToken")
        if not page_token:
            return


def _reply_pages(api: YouTubeApi, parent_id: str, token: CancelToken) -> Iterator[list[Json]]:
    page_token: str | None = None
    while True:
        token.check()
        page = api.replies(parent_id, page_token=page_token)
        yield page.get("items") or []
        page_token = page.get("nextPageToken")
        if not page_token:
            return


class YouTubeCollector(Collector):
    """Runs collect() in a QThreadPool worker and passes its results on as signals."""

    platform = Platform.YOUTUBE

    def __init__(
        self,
        api_key: str,
        tasks: TaskRunner,
        hash_author: HashAuthor,
        *,
        transport: httpx2.BaseTransport | None = None,
        dislike_estimate: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._dislikes = dislike_estimate
        self._key = api_key
        self._tasks = tasks
        self._hash_author = hash_author
        self._transport = transport
        self._worker: Worker | None = None

    def start(
        self, link: LinkInfo, *, limit: int, sort: str, since: datetime | None = None
    ) -> None:
        if link.post_id is None:
            raise ValueError(f"No video ID in {link}")
        request = CollectRequest(link.post_id, limit, sort, since)
        worker = Worker(functools.partial(self._run, request), name=f"YouTube {link.post_id}")
        worker.signals.progress.connect(self.progress)
        worker.signals.succeeded.connect(self._succeeded)
        worker.signals.failed.connect(lambda message: self.finished.emit(RunStatus.FAILED, message))
        worker.signals.cancelled.connect(lambda: self.finished.emit(RunStatus.STOPPED, "Stopped."))
        self._worker = self._tasks.start(worker)

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _run(
        self, request: CollectRequest, progress: ProgressFn, token: CancelToken
    ) -> tuple[RunStatus, str]:
        api = YouTubeApi(
            self._key,
            on_unit=lambda: self.api_units.emit(quota_day(), 1),
            transport=self._transport,
        )
        try:
            return collect(
                api,
                request,
                hash_author=self._hash_author,
                emit_post=self.post_found.emit,
                emit_batch=self.batch.emit,
                progress=progress,
                token=token,
                dislikes=(
                    functools.partial(dislike_estimate, transport=self._transport)
                    if self._dislikes
                    else None
                ),
            )
        finally:
            api.close()

    def _succeeded(self, result: object) -> None:
        status, message = cast(tuple[RunStatus, str], result)
        self.finished.emit(status, message)
