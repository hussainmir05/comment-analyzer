"""Phase 6: the Facebook, Instagram and TikTok parsers and the browser collector's driver.

The fixtures are made up in the shapes the sites use (no real people); the driver runs against a
stand-in for the browser window. Nothing here loads a real website.
"""

import json
import random
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from comment_analyzer.collection import CollectionSession
from comment_analyzer.db import Database, repository
from comment_analyzer.errors import UserError
from comment_analyzer.models import Account, AccountStatus, Platform, RunStatus
from comment_analyzer.platforms import browser_collector, facebook, instagram, tiktok
from comment_analyzer.platforms.browser_collector import (
    BrowserCollector,
    Selectors,
    choose_account,
    load_selectors,
)
from comment_analyzer.platforms.detect import LinkStatus, detect
from comment_analyzer.platforms.web_data import json_documents, parse_count

FIXTURES = Path(__file__).parent / "fixtures"
WHEN = datetime(2026, 11, 1, tzinfo=UTC)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def hasher(user_id: str | None) -> str | None:
    return f"h:{user_id}" if user_id else None


# --- parsers ----------------------------------------------------------------------------------


def test_facebook_post_page():
    parsed = facebook.parse(
        "document:https://www.facebook.com/x", fixture("facebook/post_page.json"), hasher
    )
    assert parsed.post is not None
    assert parsed.post.author == "Example Bakery"
    assert parsed.post.title == "Our new bakery opens on Saturday!"
    assert parsed.post.published_at == datetime.fromtimestamp(1761994800, UTC)
    assert parsed.post.reactions == {"Like": 200, "Love": 40, "Haha": 5}
    assert parsed.post.comments_reported == 3 and parsed.post.shares == 12
    first, second = parsed.comments
    assert (first.platform_comment_id, first.like_count, first.reply_count) == ("9001", 1200, 1)
    assert first.author_hash == "h:200000000000001"
    assert second.text == "Parking there is terrible though." and second.like_count == 3
    flat = json.dumps([c.text for c in parsed.comments])
    assert "Person One" not in flat  # names are never kept


def test_facebook_graphql_lines_and_duplicates():
    parsed = facebook.parse(
        "https://www.facebook.com/api/graphql/", fixture("facebook/graphql_replies.txt"), hasher
    )
    by_id = {c.platform_comment_id: c for c in parsed.comments}
    assert by_id["9003"].parent_id == "9001" and by_id["9003"].text == "Same here!"
    assert by_id["9002"].like_count == 7


def test_instagram_comments_and_post():
    rest = instagram.parse(
        "https://www.instagram.com/api/v1/media/1/comments/",
        fixture("instagram/comments.json"),
        hasher,
    )
    assert rest.post is None
    assert [c.platform_comment_id for c in rest.comments] == [
        "17900000000000001",
        "17900000000000002",
    ]
    assert rest.comments[0].like_count == 14 and rest.comments[0].reply_count == 1
    assert rest.comments[0].author_hash == "h:300000001"
    graphql = instagram.parse(
        "https://www.instagram.com/graphql/query", fixture("instagram/graphql_post.json"), hasher
    )
    assert graphql.post is not None
    assert graphql.post.author == "example.photos" and graphql.post.likes == 512
    assert graphql.post.views == 9001 and graphql.post.title == "Sunset over the bay"
    [reply] = graphql.comments
    assert reply.parent_id == "17900000000000002" and reply.like_count == 2


def test_tiktok_comments_and_video():
    listed = tiktok.parse(
        "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json"), hasher
    )
    assert [c.like_count for c in listed.comments] == [88, 1]
    assert listed.comments[0].parent_id is None and listed.comments[0].reply_count == 2
    page = tiktok.parse(
        "document:https://www.tiktok.com/@x/video/1", fixture("tiktok/universal_data.json"), hasher
    )
    assert page.post is not None
    assert page.post.author == "example.kitchen" and page.post.views == 120345
    assert page.post.comments_reported == 341 and page.post.saves == 800


def test_counts_and_documents():
    assert [parse_count(v) for v in (12, "1,234", "1.2K", "3M", "", None, True)] == [
        12, 1234, 1200, 3_000_000, None, None, None,
    ]  # fmt: skip
    assert list(json_documents('for (;;);{"a": 1}\n{"b": 2}\nnot json')) == [{"a": 1}, {"b": 2}]


def test_junk_never_raises():
    for parse in (facebook.parse, instagram.parse, tiktok.parse):
        assert parse("https://x/", "<html>not json</html>", hasher).comments == []


def test_share_links_keep_the_address_to_open():
    info = detect("https://vm.tiktok.com/ZMabc123/")
    assert info.status is LinkStatus.NEEDS_BROWSER
    assert info.canonical_url == "https://vm.tiktok.com/ZMabc123/"


def test_selectors_load_and_a_data_folder_copy_wins(tmp_path: Path):
    for platform in (Platform.FACEBOOK, Platform.INSTAGRAM, Platform.TIKTOK):
        selectors = load_selectors(platform)
        assert selectors.capture and selectors.more_buttons and selectors.max_steps > 0
    (tmp_path / "tiktok.json").write_text(
        json.dumps({"capture": ["^x"], "max_steps": 5}), encoding="utf-8"
    )
    custom = load_selectors(Platform.TIKTOK, tmp_path)
    assert custom.capture == ("^x",) and custom.max_steps == 5
    facebook_selectors = load_selectors(Platform.FACEBOOK)
    assert facebook_selectors.wall_in("https://www.facebook.com/checkpoint/123/")
    assert not facebook_selectors.wall_in("https://www.facebook.com/somebody/posts/1")


# --- the driver -------------------------------------------------------------------------------


class FakeSurface(QObject):
    loaded = Signal(bool)
    url_changed = Signal(str)
    captured = Signal(str, str)
    stop_clicked = Signal()

    def __init__(self, *args: Any) -> None:
        super().__init__()
        self.opened: list[str] = []
        self.steps: list[str] = []
        self.answers: list[Any] = []
        self.status = ""
        self.handed_over = ""
        self.url = ""
        self.closed = False

    def open(self, url: str) -> None:
        self.opened.append(url)
        self.url = url

    def run_js(self, script: str, done: Any) -> None:
        self.steps.append(script)
        done(self.answers.pop(0) if self.answers else {"clicked": 0, "wall": None})

    def current_url(self) -> str:
        return self.url

    def set_status(self, text: str) -> None:
        self.status = text

    def hand_over(self, message: str) -> None:
        self.handed_over = message

    def close(self) -> bool:
        self.closed = True
        return True


def _collector(
    platform: Platform = Platform.TIKTOK, **kwargs: Any
) -> tuple[BrowserCollector, FakeSurface]:
    surfaces: list[FakeSurface] = []

    def factory(*args: Any) -> FakeSurface:
        surfaces.append(FakeSurface())
        return surfaces[0]

    account = Account(
        platform, "Work", f"{platform.value}-0123456789ab", WHEN, AccountStatus.LOGGED_IN, id=1
    )
    collector = BrowserCollector(
        account,
        None,
        hasher,
        surface_factory=factory,
        pause=(0, 0),
        rng=random.Random(1),
        **kwargs,  # type: ignore[arg-type]
    )
    collector.surface_factory = factory
    return collector, surfaces  # type: ignore[return-value]


class Recorder:
    def __init__(self, collector: BrowserCollector) -> None:
        self.events: list[tuple[str, Any]] = []
        collector.post_found.connect(lambda post, snap: self.events.append(("post", (post, snap))))
        collector.batch.connect(lambda comments: self.events.append(("batch", comments)))
        collector.blocked.connect(lambda reason: self.events.append(("blocked", reason)))
        collector.finished.connect(
            lambda status, message: self.events.append(("finished", (status, message)))
        )

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]

    def last(self, kind: str) -> Any:
        return [value for k, value in self.events if k == kind][-1]


LINK = detect("https://www.tiktok.com/@example.kitchen/video/7300000000000000001")


def test_a_run_reads_until_nothing_new_comes(qtbot):
    collector, surfaces = _collector()
    recorder = Recorder(collector)
    collector.start(LINK, limit=100, sort="relevance")
    [surface] = surfaces
    assert surface.opened == ["https://www.tiktok.com/@example.kitchen/video/7300000000000000001"]
    surface.captured.emit(
        "document:https://www.tiktok.com/@x/video/1", fixture("tiktok/universal_data.json")
    )
    surface.captured.emit(
        "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json")
    )
    surface.captured.emit(
        "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json")
    )  # again
    surface.loaded.emit(True)
    qtbot.waitUntil(lambda: "finished" in recorder.kinds(), timeout=5000)
    assert recorder.kinds() == ["post", "batch", "finished"]
    post, snapshot = recorder.last("post")
    assert post.post_id == "7300000000000000001" and post.author == "example.kitchen"
    assert snapshot.views == 120345 and snapshot.comments_reported == 341
    assert len(recorder.last("batch")) == 2  # the repeat added nothing
    assert recorder.last("finished") == (RunStatus.COMPLETED, "")
    assert len(surface.steps) == 1 + browser_collector.QUIET_STEPS  # the first step had news
    assert '"maxClicks": 3' in surface.steps[0]
    qtbot.waitUntil(lambda: surface.closed, timeout=5000)


def test_the_limit_ends_the_run(qtbot):
    collector, surfaces = _collector()
    recorder = Recorder(collector)
    collector.start(LINK, limit=1, sort="relevance")
    surfaces[0].captured.emit(
        "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json")
    )
    assert recorder.kinds() == ["post", "batch", "finished"]
    assert len(recorder.last("batch")) == 1


def test_a_wall_stops_and_hands_the_window_over(qtbot):
    collector, surfaces = _collector(Platform.FACEBOOK)
    recorder = Recorder(collector)
    collector.start(
        detect("https://www.facebook.com/somebody/posts/123456"), limit=100, sort="relevance"
    )
    surfaces[0].captured.emit(
        "document:https://www.facebook.com/x", fixture("facebook/post_page.json")
    )
    surfaces[0].url_changed.emit("https://www.facebook.com/checkpoint/1501092823525282/")
    assert recorder.kinds() == ["blocked", "post", "batch", "finished"]
    status, message = recorder.last("finished")
    assert status is RunStatus.BLOCKED and "comments read so far are kept" in message
    assert surfaces[0].handed_over == message and not surfaces[0].closed


def test_a_wall_found_by_the_step_script(qtbot):
    collector, surfaces = _collector()
    recorder = Recorder(collector)
    collector.start(LINK, limit=100, sort="relevance")
    surfaces[0].answers = [{"clicked": 0, "wall": "Drag the slider"}]
    surfaces[0].loaded.emit(True)
    qtbot.waitUntil(lambda: "finished" in recorder.kinds(), timeout=5000)
    assert recorder.last("blocked") == 'TikTok showed "Drag the slider".'
    assert recorder.last("finished")[0] is RunStatus.BLOCKED


def test_stop_keeps_what_was_read_and_nothing_found_is_a_failure(qtbot):
    collector, surfaces = _collector()
    recorder = Recorder(collector)
    collector.start(LINK, limit=100, sort="relevance")
    surfaces[0].captured.emit(
        "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json")
    )
    surfaces[0].stop_clicked.emit()
    assert recorder.kinds() == ["post", "batch", "finished"]
    assert recorder.last("finished")[0] is RunStatus.STOPPED

    empty, surfaces = _collector()
    recorder = Recorder(empty)
    empty.start(LINK, limit=100, sort="relevance")
    surfaces[0].loaded.emit(True)
    qtbot.waitUntil(lambda: "finished" in recorder.kinds(), timeout=5000)
    status, message = recorder.last("finished")
    assert status is RunStatus.FAILED and "showed no comments" in message
    assert recorder.kinds() == ["finished"]


def test_a_share_link_finds_its_post_after_loading(qtbot):
    collector, surfaces = _collector()
    recorder = Recorder(collector)
    collector.start(detect("https://vm.tiktok.com/ZMabc123/"), limit=100, sort="relevance")
    surface = surfaces[0]
    assert surface.opened == ["https://vm.tiktok.com/ZMabc123/"]
    surface.url = "https://www.tiktok.com/@example.kitchen/video/7300000000000000001?lang=en"
    surface.captured.emit(
        "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json")
    )
    surface.loaded.emit(True)
    qtbot.waitUntil(lambda: "finished" in recorder.kinds(), timeout=5000)
    post, _ = recorder.last("post")
    assert post.post_id == "7300000000000000001"


def test_runs_are_stored_and_counted_against_the_account(qtbot, tmp_path: Path):
    db = Database(tmp_path / "data.db")
    db.migrate()
    with closing(db.connect()) as conn:
        account_id = repository.add_account(
            conn,
            Account(Platform.TIKTOK, "Work", "tiktok-0123456789ab", WHEN, AccountStatus.LOGGED_IN),
        )
    collector, surfaces = _collector()
    collector.account_id = account_id
    session = CollectionSession(
        db, collector, LINK, limit=100, sort="relevance", account_id=account_id
    )
    with qtbot.waitSignal(session.done, timeout=5000) as done:
        session.start()
        surfaces[0].captured.emit(
            "https://www.tiktok.com/api/comment/list/", fixture("tiktok/comment_list.json")
        )
        surfaces[0].loaded.emit(True)
    summary = done.args[0]
    assert summary.status is RunStatus.COMPLETED and summary.collected == 2
    today = datetime.now().date().isoformat()
    with closing(db.connect()) as conn:
        assert repository.account_usage(conn, account_id, today) == (1, 2)
        run = repository.latest_run(conn, summary.post.id)
        assert run is not None and run.account_id == account_id


def test_choose_account_spreads_use_and_keeps_the_daily_limit(tmp_path: Path):
    db = Database(tmp_path / "data.db")
    db.migrate()
    today = datetime.now().date().isoformat()
    with pytest.raises(UserError, match="Log in to Facebook on the Accounts page"):
        choose_account(db, Platform.FACEBOOK, 5)
    with closing(db.connect()) as conn:
        ids = [
            repository.add_account(
                conn, Account(Platform.FACEBOOK, name, f"facebook-00000000000{i}", WHEN, status)
            )
            for i, (name, status) in enumerate(
                [
                    ("A", AccountStatus.LOGGED_IN),
                    ("B", AccountStatus.LOGGED_IN),
                    ("C", AccountStatus.LOGGED_OUT),
                ]
            )
        ]
        repository.add_account_usage(conn, ids[0], today, 2, 10)
    assert choose_account(db, Platform.FACEBOOK, 5).label == "B"
    with closing(db.connect()) as conn:
        repository.add_account_usage(conn, ids[1], today, 3, 10)
    assert choose_account(db, Platform.FACEBOOK, 5).label == "A"  # 2 posts today, B has 3
    with pytest.raises(UserError, match="daily limit"):
        choose_account(db, Platform.FACEBOOK, 2)
    with pytest.raises(UserError, match="reading another post"):
        choose_account(db, Platform.FACEBOOK, 5, busy={ids[0], ids[1]})
    assert choose_account(db, Platform.FACEBOOK, 5, busy={ids[0]}).label == "B"


# --- the "load more" script in a real page ----------------------------------------------------

PAGE = """<html><body>
<div role="button" onclick="window.hits.push('more')">View more comments</div>
<div role="button" onclick="window.hits.push('replies')"><span dir="auto">View all 4 replies</span></div>
<div role="button" onclick="window.hits.push('like')">Like</div>
<div role="button" onclick="window.hits.push('reply')">Reply</div>
<form><button type="button" onclick="window.hits.push('form')">View more comments</button></form>
<div role="button" style="display:none" onclick="window.hits.push('hidden')">View more comments</div>
<script>window.hits = [];</script>
</body></html>"""


def _run(qtbot, page: QWebEnginePage, script: str) -> Any:
    results: list[Any] = []
    page.runJavaScript(script, 0, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=10_000)
    return results[0]


def test_more_script_clicks_only_load_more_buttons(qtbot):
    view = QWebEngineView()  # a page without a view has no size, so nothing would be visible
    qtbot.addWidget(view)
    view.resize(800, 600)
    page = QWebEnginePage(view)
    view.setPage(page)
    view.show()
    with qtbot.waitSignal(page.loadFinished, timeout=15_000):
        page.setHtml(PAGE, QUrl("https://www.facebook.com/somebody/posts/1"))
    selectors = load_selectors(Platform.FACEBOOK)
    result = _run(qtbot, page, selectors.more_script())
    assert json.loads(result) == {"clicked": 2, "wall": None}
    assert json.loads(_run(qtbot, page, "JSON.stringify(window.hits)")) == ["more", "replies"]

    walled = Selectors(("x",), False, (), False, (), ("Log in to continue",), 1)
    page.runJavaScript("document.body.insertAdjacentText('beforeend', 'Log in to continue')")
    assert json.loads(_run(qtbot, page, walled.more_script())) == {
        "clicked": 0,
        "wall": "Log in to continue",
    }


def test_the_sanitizer_removes_people(tmp_path: Path):
    import subprocess
    import sys

    body = fixture("tiktok/comment_list.json")
    capture = tmp_path / "capture.json"
    capture.write_text(
        json.dumps({"url": "https://www.tiktok.com/api/comment/list/", "body": body}),
        encoding="utf-8",
    )
    out = tmp_path / "fixture.json"
    script = Path(__file__).parents[1] / "scripts" / "sanitize_fixture.py"
    subprocess.run(
        [sys.executable, str(script), str(capture), str(out)], check=True, capture_output=True
    )
    cleaned = out.read_text(encoding="utf-8")
    for private in ("cook_fan", "Cook Fan", "6800000000000000001", "saved my dinner"):
        assert private not in cleaned
    parsed = tiktok.parse("https://www.tiktok.com/api/comment/list/", cleaned, hasher)
    assert [c.like_count for c in parsed.comments] == [88, 1]  # what the parser needs stays
    assert parsed.comments[0].text == "text 1"
