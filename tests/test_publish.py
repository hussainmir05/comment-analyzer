"""Phase 8: publishing through Meta's Graph API, against a mock server (no real requests)."""

from contextlib import closing
from pathlib import Path
from typing import Any

import httpx2
import pytest
from PySide6.QtWidgets import QMessageBox
from test_reports import make_post

from comment_analyzer import config
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.publish import meta
from comment_analyzer.publish.meta import MetaClient, MetaError, MetaTarget
from comment_analyzer.reports import exports
from comment_analyzer.ui.pages.post_maker import PostMakerPage
from comment_analyzer.ui.pages.settings import SettingsPage, meta_target
from comment_analyzer.workers import TaskRunner

TOKEN = "EAAB" + "x" * 60
TARGET = MetaTarget("111", "Example Page", "222", "example.ig")
PUBLISHED_TRUE = 'name="published"\r\n\r\ntrue'
PUBLISHED_FALSE = 'name="published"\r\n\r\nfalse'


class FakeGraph:
    """Answers like the Graph API for the calls the publisher makes, and records them."""

    def __init__(
        self, *, fail: dict[str, Any] | None = None, statuses: list[str] | None = None
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail = fail
        self.statuses = statuses or ["IN_PROGRESS", "FINISHED"]

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        assert params.pop("access_token") == TOKEN
        path = request.url.path.removeprefix("/v23.0")
        body = request.content.decode("latin-1") if request.method == "POST" else ""
        self.calls.append((request.method, path, params | ({"body": body} if body else {})))
        if self.fail is not None:
            return httpx2.Response(400, json={"error": self.fail})
        if request.method == "GET" and path == "/me":
            account = {"id": "222", "username": "example.ig"}
            return httpx2.Response(
                200,
                json={"id": "111", "name": "Example Page", "instagram_business_account": account},
            )
        if request.method == "POST" and path == "/111/photos":
            if PUBLISHED_TRUE in body:
                return httpx2.Response(200, json={"id": "333", "post_id": "111_444"})
            return httpx2.Response(200, json={"id": "555"})
        if request.method == "GET" and path == "/555":
            images = [
                {"width": 600, "source": "https://cdn.example/s.jpg"},
                {"width": 1080, "source": "https://cdn.example/l.jpg"},
            ]
            return httpx2.Response(200, json={"images": images})
        if request.method == "POST" and path == "/222/media":
            return httpx2.Response(200, json={"id": "666"})
        if request.method == "GET" and path == "/666":
            return httpx2.Response(200, json={"status_code": self.statuses.pop(0)})
        if request.method == "POST" and path == "/222/media_publish":
            return httpx2.Response(200, json={"id": "777"})
        if request.method == "DELETE" and path == "/555":
            return httpx2.Response(200, json={"success": True})
        return httpx2.Response(404, json={"error": {"code": 100, "message": "unknown"}})


def _client(graph: FakeGraph) -> MetaClient:
    return MetaClient(TOKEN, transport=httpx2.MockTransport(graph.handle), sleep=lambda s: None)


@pytest.fixture
def card(tmp_path: Path) -> Path:
    path = tmp_path / "card.jpg"
    path.write_bytes(b"\xff\xd8\xff fake jpeg")
    return path


def test_check_finds_the_page_and_its_instagram():
    assert _client(FakeGraph()).check() == TARGET


def test_page_photo(card: Path):
    graph = FakeGraph()
    assert _client(graph).publish_page_photo("111", card, "Mostly positive!") == "111_444"
    [(method, path, sent)] = graph.calls
    assert (method, path) == ("POST", "/111/photos")
    assert "Mostly positive!" in sent["body"] and "fake jpeg" in sent["body"]


def test_instagram_goes_through_an_unpublished_page_photo(card: Path):
    graph = FakeGraph()
    assert _client(graph).publish_instagram(TARGET, card, "Caption") == "777"
    steps = [(m, p) for m, p, _ in graph.calls]
    assert steps == [
        ("POST", "/111/photos"),
        ("GET", "/555"),
        ("POST", "/222/media"),
        ("GET", "/666"),
        ("GET", "/666"),
        ("POST", "/222/media_publish"),
        ("DELETE", "/555"),  # the helper photo is removed
    ]
    assert PUBLISHED_FALSE in graph.calls[0][2]["body"]
    assert "cdn.example%2Fl.jpg" in graph.calls[2][2]["body"]  # the biggest image


def test_instagram_processing_errors_still_clean_up(card: Path):
    graph = FakeGraph(statuses=["ERROR"])
    with pytest.raises(MetaError, match="couldn't process"):
        _client(graph).publish_instagram(TARGET, card, "Caption")
    assert graph.calls[-1][:2] == ("DELETE", "/555")


@pytest.mark.parametrize(
    ("error", "words"),
    [
        ({"code": 190, "message": "expired"}, "expired or was revoked"),
        ({"code": 200, "message": "permission"}, "lacks a permission"),
        ({"code": 4, "message": "too many"}, "limiting requests"),
        ({"code": 1, "message": "unknown"}, "error 1"),
    ],
)
def test_graph_errors_are_plain(error: dict[str, Any], words: str):
    with pytest.raises(MetaError, match=words):
        _client(FakeGraph(fail=error)).check()


def test_the_token_never_reaches_the_log(caplog: pytest.LogCaptureFixture):
    with pytest.raises(MetaError):
        _client(FakeGraph(fail={"code": 190, "message": "expired"})).check()
    assert TOKEN not in caplog.text


def test_graph_version_is_pinned():
    assert meta.GRAPH == "https://graph.facebook.com/v23.0"


# --- Settings and the Post maker --------------------------------------------------------------


@pytest.fixture
def db() -> Database:
    paths = AppPaths.default()
    paths.create()
    database = Database(paths.db)
    database.migrate()
    return database


def test_settings_save_the_token_and_the_page(qtbot, db: Database):
    paths = AppPaths.default()
    tasks = TaskRunner()
    page = SettingsPage(
        paths, config.settings_store(paths), db, tasks, meta_checker=lambda token: TARGET
    )
    qtbot.addWidget(page)
    page.meta_token_edit.setText("too short")
    page.save_meta_btn.click()
    assert "doesn't look like" in page.meta_status.text()
    page.meta_token_edit.setText(TOKEN)
    page.save_meta_btn.click()
    qtbot.waitUntil(lambda: "Connected to" in page.meta_status.text(), timeout=5000)
    assert "Example Page" in page.meta_status.text()
    assert "@example.ig" in page.meta_status.text()
    assert config.get_secret(config.META_PAGE_TOKEN) == TOKEN
    assert meta_target(page.store) == TARGET
    page.remove_meta_token()
    assert meta_target(page.store) is None
    assert config.get_secret(config.META_PAGE_TOKEN) is None
    tasks.wait(5000)


def _post_maker(qtbot, db: Database, graph: FakeGraph) -> PostMakerPage:
    store = config.settings_store(AppPaths.default())
    settings = config.load_settings(store)
    settings.meta_page, settings.meta_instagram = "111|Example Page", "222|example.ig"
    config.save_settings(store, settings)
    config.set_secret(config.META_PAGE_TOKEN, TOKEN)
    page = PostMakerPage(db, store, TaskRunner(), meta_factory=lambda token: _client(graph))
    qtbot.addWidget(page)
    page.open_saved = False
    return page


def test_publishing_asks_first_and_never_repeats_a_text(
    qtbot, db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(exports, "reports_folder", lambda: tmp_path)
    graph = FakeGraph()
    post_id = make_post(db)
    page = _post_maker(qtbot, db, graph)
    page.posts.setCurrentIndex(page.posts.findData(post_id))

    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    page.facebook_btn.click()
    assert page.status.text() == "Nothing was posted." and graph.calls == []

    asked: list[str] = []

    def yes(parent: object, title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", yes)
    with qtbot.waitSignal(page.published, timeout=5000) as signal:
        page.facebook_btn.click()
    assert signal.args == ["facebook", "111_444"]
    assert 'the Facebook Page "Example Page"' in asked[0]
    caption = page.caption.toPlainText()
    with closing(db.connect()) as conn:
        spaced = "  " + caption.replace(" ", "  ")
        assert repository.published_with_text(conn, spaced) == [("facebook", "111")]

    page.instagram_btn.click()  # the same caption: refused before asking
    assert "already posted" in page.status.text() and len(asked) == 1
    page.other_btn.click()
    with qtbot.waitSignal(page.published, timeout=5000) as signal:
        page.instagram_btn.click()
    assert signal.args == ["instagram", "777"]
    assert "Instagram as @example.ig" in asked[1]


def test_publishing_without_meta_explains(qtbot, db: Database):
    post_id = make_post(db)
    page = PostMakerPage(db, config.settings_store(AppPaths.default()), TaskRunner())
    qtbot.addWidget(page)
    page.posts.setCurrentIndex(page.posts.findData(post_id))
    page.facebook_btn.click()
    assert "Set up Meta in Settings first" in page.status.text()
