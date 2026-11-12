"""Test doubles: a fake YouTube API (an httpx2 MockTransport handler) and a fake collector."""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from PySide6.QtCore import QTimer

from comment_analyzer.models import Comment, Platform, Post, RunStatus, Snapshot
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo

FIXTURES = Path(__file__).parent / "fixtures" / "youtube"
VIDEO_ID = "vid00000001"
KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def error_body(name: str) -> Any:
    return fixture("errors.json")[name]


class FakeYouTube:
    """Answers YouTube API requests from the fixtures.

    fail() makes an endpoint answer with an error, optionally only after some successful calls.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.calls: Counter[str] = Counter()
        self._failures: dict[str, list[tuple[int, httpx2.Response]]] = {}

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)

    def fail(
        self,
        endpoint: str,
        status: int,
        error: str | None = None,
        *,
        after: int = 0,
        times: int = 1,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = error_body(error) if error else {}
        response = httpx2.Response(status, json=body, headers=headers)
        self._failures.setdefault(endpoint, []).extend([(after, response)] * times)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        done = self.calls[endpoint]
        self.calls[endpoint] += 1
        queue = self._failures.get(endpoint, [])
        if queue and done >= queue[0][0]:
            return queue.pop(0)[1]
        token = request.url.params.get("pageToken")
        match endpoint:
            case "videos":
                return httpx2.Response(200, json=fixture("video.json"))
            case "commentThreads":
                page = "comment_threads_page2.json" if token == "PAGE2" else "comment_threads.json"
                return httpx2.Response(200, json=fixture(page))
            case "comments":
                page = "comments_page2.json" if token == "R2" else "comments_page1.json"
                return httpx2.Response(200, json=fixture(page))
            case "videoCategories":
                return httpx2.Response(200, json={"items": []})
        return httpx2.Response(404, json={})


class FakeAnalyzer:
    """Analyzer stand-in: already "downloaded", judges by keywords."""

    download_size = 300 * 2**20

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.downloads = 0

    def download(self, progress: Any, token: Any) -> None:
        self.downloads += 1
        self.ready = True

    def classifier(self) -> Any:
        def classify(texts: list[str]) -> list[tuple[str, float]]:
            return [
                ("positive", 0.9) if "1" in text else ("negative", 0.8) if "2" in text
                else ("neutral", 0.7)
                for text in texts
            ]  # fmt: skip

        return classify

    def embedder(self) -> Any:
        return fake_embedder


def fake_embedder(texts: list[str]) -> list[Any]:
    """Comments that start with the same word get the same vector."""
    import numpy as np

    vectors = []
    for text in texts:
        vector = np.zeros(64, dtype=np.float32)
        vector[sum(map(ord, (text.split() or [""])[0])) % 64] = 1.0
        vectors.append(vector)
    return vectors


def sample_comments(count: int = 3) -> list[Comment]:
    when = datetime(2026, 11, 1, tzinfo=UTC)
    return [
        Comment(f"c{i}", f"comment number {i}", created_at=when, like_count=i, reply_count=0)
        for i in range(count)
    ]


class FakeCollector(Collector):
    """Emits a scripted run a moment after start(), like a real collector would."""

    platform = Platform.YOUTUBE

    def __init__(
        self,
        *,
        comments: list[Comment] | None = None,
        reported: int | None = 10,
        status: RunStatus = RunStatus.COMPLETED,
        message: str = "",
        fail_before_post: str | None = None,
        wait_for_stop: bool = False,
    ) -> None:
        super().__init__()
        self.comments = sample_comments() if comments is None else comments
        self.reported = reported
        self.status = status
        self.message = message
        self.fail_before_post = fail_before_post
        self.wait_for_stop = wait_for_stop
        self.started_with: tuple[LinkInfo, int, str] | None = None
        self.since: datetime | None = None
        self.stopped = False

    def start(
        self, link: LinkInfo, *, limit: int, sort: str, since: datetime | None = None
    ) -> None:
        self.started_with = (link, limit, sort)
        self.since = since
        QTimer.singleShot(0, lambda: self._run(link))

    def stop(self) -> None:
        self.stopped = True
        if self.wait_for_stop:
            QTimer.singleShot(0, lambda: self.finished.emit(RunStatus.STOPPED, "Stopped."))

    def _run(self, link: LinkInfo) -> None:
        if self.fail_before_post:
            self.finished.emit(RunStatus.FAILED, self.fail_before_post)
            return
        post = Post(
            Platform.YOUTUBE, link.post_id or "x", link.canonical_url or "", title="A video"
        )
        snapshot = Snapshot(datetime.now(UTC), views=100, comments_reported=self.reported)
        self.post_found.emit(post, snapshot)
        self.api_units.emit("2026-11-01", 2)
        self.batch.emit(list(self.comments))
        self.progress.emit(len(self.comments), self.reported or 0, "reading")
        if not self.wait_for_stop:
            self.finished.emit(self.status, self.message)
