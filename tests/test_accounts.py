"""Phase 5: accounts, browser profiles, the capture bridge. Nothing here loads a real website."""

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
import shiboken6
from PySide6.QtCore import QDateTime, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QMessageBox

from comment_analyzer import config
from comment_analyzer.browser import bridge as bridge_module
from comment_analyzer.browser.bridge import CaptureBridge, capture_source, install
from comment_analyzer.browser.profiles import PENDING_DELETES, ProfileStore, new_profile_dir
from comment_analyzer.browser.sites import SITES
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.models import Account, AccountStatus, Platform
from comment_analyzer.ui.pages import accounts as accounts_module
from comment_analyzer.ui.pages.accounts import AccountsPage

WHEN = datetime(2026, 11, 1, tzinfo=UTC)
FACEBOOK = SITES[Platform.FACEBOOK]


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data.db")
    database.migrate()
    return database


def _account(db: Database, platform: Platform = Platform.FACEBOOK, label: str = "Work") -> Account:
    account = Account(platform, label, new_profile_dir(platform), WHEN)
    with closing(db.connect()) as conn:
        account.id = repository.add_account(conn, account)
    return account


def test_accounts_are_stored_and_usage_adds_up(db: Database):
    work = _account(db)
    home = _account(db, Platform.FACEBOOK, "Home")
    _account(db, Platform.TIKTOK, "Tok")
    with closing(db.connect()) as conn:
        assert [a.label for a in repository.list_accounts(conn, Platform.FACEBOOK)] == [
            "Work",
            "Home",
        ]
        assert work.id is not None and home.id is not None
        repository.set_account_status(conn, work.id, AccountStatus.LOGGED_IN)
        repository.mark_account_used(conn, work.id, WHEN)
        repository.add_account_usage(conn, work.id, "2026-11-01", 1, 120)
        repository.add_account_usage(conn, work.id, "2026-11-01", 1, 30)
        assert repository.account_usage(conn, work.id, "2026-11-01") == (2, 150)
        stored = repository.get_account(conn, work.id)
        assert stored is not None and stored.status is AccountStatus.LOGGED_IN
        assert stored.last_used_at == WHEN
        repository.delete_account(conn, work.id)
        assert repository.get_account(conn, work.id) is None
        assert repository.account_usage(conn, work.id, "2026-11-01") == (0, 0)


def test_sites_own_their_domains_only():
    assert FACEBOOK.owns("www.facebook.com") and FACEBOOK.owns("facebook.com")
    assert FACEBOOK.owns("m.facebook.com.")
    assert not FACEBOOK.owns("notfacebook.com")
    assert not FACEBOOK.owns("facebook.com.evil.example")
    assert new_profile_dir(Platform.TIKTOK).startswith("tiktok-")


# --- profiles ---------------------------------------------------------------------------------


def test_login_cookie_sets_the_status_and_delete_removes_the_folder(
    qtbot, db: Database, tmp_path: Path
):
    profiles = ProfileStore(tmp_path / "profiles")
    account = _account(db)
    profile = profiles.profile(account)
    assert profiles.profile(account) is profile  # one profile per account per session
    assert profiles.folder(account).is_dir()
    page = _wake(profile)
    cookie = QNetworkCookie(b"c_user", b"1000123")
    cookie.setDomain(".facebook.com")
    with qtbot.waitSignal(profiles.status_changed, timeout=10_000) as signal:
        profile.cookieStore().setCookie(cookie, QUrl("https://www.facebook.com/"))
    assert signal.args == [account.id, AccountStatus.LOGGED_IN.value]
    assert profiles.is_logged_in(account.id or 0)

    other = QNetworkCookie(b"c_user", b"5")
    other.setDomain(".notfacebook.com")  # someone else's cookie of that name: ignored
    profile.cookieStore().setCookie(other, QUrl("https://www.notfacebook.com/"))

    shiboken6.delete(page)
    profiles.remove(account)
    qtbot.wait(200)
    assert not profiles.is_logged_in(account.id or 0)
    assert not profiles.folder(account).exists() or (profiles.root / PENDING_DELETES).is_file()


def test_folders_still_in_use_are_deleted_at_the_next_start(tmp_path: Path):
    root = tmp_path / "profiles"
    (root / "facebook-0123456789ab" / "storage").mkdir(parents=True)
    (root / "keep").mkdir()
    lines = ["facebook-0123456789ab", "..", "keep", "C:\\Windows", ""]
    (root / PENDING_DELETES).write_text("\n".join(lines), encoding="utf-8")
    ProfileStore(root).delete_pending()
    assert not (root / "facebook-0123456789ab").exists()
    assert not (root / PENDING_DELETES).exists()
    assert (root / "keep").is_dir() and root.is_dir()  # only profile folder names count


def _wake(profile: Any) -> QWebEnginePage:
    """A page in the profile, as the login window has: Chromium starts the cookie store then."""
    page = QWebEnginePage(profile)
    page.setUrl(QUrl("about:blank"))
    return page


def test_an_account_with_no_login_cookie_is_logged_out(qtbot, db: Database, tmp_path: Path):
    profiles = ProfileStore(tmp_path / "profiles")
    account = _account(db)
    with qtbot.waitSignal(profiles.status_changed, timeout=5_000) as signal:
        profiles.check(account)
    assert signal.args == [account.id, AccountStatus.LOGGED_OUT.value]


# --- the capture bridge -----------------------------------------------------------------------


def _message(**fields: Any) -> str:
    data = {"kind": "response", "url": "https://www.facebook.com/api/graphql/", "status": 200}
    return json.dumps(data | {"body": '{"data": 1}'} | fields)


def _bridge(page: str = "https://www.facebook.com/somebody/posts/1", **kwargs: Any) -> Any:
    bridge = CaptureBridge(
        FACEBOOK, lambda: QUrl(page), [r"/api/graphql/", r"^document:"], **kwargs
    )
    got: list[tuple[str, str]] = []
    bridge.captured.connect(lambda url, body: got.append((url, body)))
    return bridge, got


def test_the_bridge_accepts_matching_responses_from_the_site():
    bridge, got = _bridge()
    bridge.capture(_message())
    assert got == [("https://www.facebook.com/api/graphql/", '{"data": 1}')]


@pytest.mark.parametrize(
    "message",
    [
        _message(kind="cookies"),
        _message(url="https://evil.example/api/graphql/"),
        _message(url="https://www.facebook.com/settings/"),  # not in the selectors
        _message(url="http://www.facebook.com/api/graphql/"),  # not https
        _message(body=["not", "text"]),
        _message(extra="field"),
        "not json",
        "x" * (bridge_module.MAX_MESSAGE + 1),
    ],
)
def test_the_bridge_rejects_everything_else(message: str):
    bridge, got = _bridge()
    bridge.capture(message)
    assert got == [] and bridge.rejected == 1


def test_the_bridge_ignores_pages_on_other_sites_and_caps_the_rate(
    monkeypatch: pytest.MonkeyPatch,
):
    bridge, got = _bridge(page="https://evil.example/facebook.com")
    bridge.capture(_message())
    assert got == []
    monkeypatch.setattr(bridge_module, "MAX_PER_MINUTE", 3)
    bridge, got = _bridge()
    for _ in range(5):
        bridge.capture(_message())
    assert len(got) == 3


def test_debug_capture_saves_responses_only(tmp_path: Path):
    bridge, _ = _bridge(debug_dir=tmp_path / "debug")
    bridge.capture(_message())
    [saved] = (tmp_path / "debug" / "facebook").iterdir()
    assert json.loads(saved.read_text(encoding="utf-8")) == {
        "url": "https://www.facebook.com/api/graphql/",
        "body": '{"data": 1}',
    }


def test_capture_js_runs_in_a_page_and_reaches_the_bridge(qtbot, tmp_path: Path, db: Database):
    """A real page, with no network: the Facebook-style JSON block in the page itself."""
    source = capture_source([r"^document:"], document_json=True)
    assert "QWebChannel" in source and '["^document:"]' in source
    profiles = ProfileStore(tmp_path / "profiles")
    page = QWebEnginePage(profiles.profile(_account(db)))
    bridge = CaptureBridge(FACEBOOK, page.url, [r"^document:"])
    install(page, bridge, [r"^document:"], document_json=True)
    with qtbot.waitSignal(bridge.captured, timeout=15_000) as signal:
        page.setHtml(
            '<html><body><script type="application/json">{"comment": "hi"}</script></body></html>',
            QUrl("https://www.facebook.com/somebody/posts/1"),
        )
    url, body = signal.args
    assert url == "document:https://www.facebook.com/somebody/posts/1"
    assert json.loads(body) == {"comment": "hi"}
    page.deleteLater()


# --- the Accounts page ------------------------------------------------------------------------


class FakeLoginWindow:
    opened: ClassVar[list[Account]] = []

    def __init__(self, account: Account, profiles: ProfileStore) -> None:
        FakeLoginWindow.opened.append(account)
        self.destroyed = _Signal()
        self.closed = False

    def show(self) -> None: ...
    def raise_(self) -> None: ...
    def activateWindow(self) -> None: ...

    def close(self) -> None:
        self.closed = True


class _Signal:
    def connect(self, slot: Any) -> None: ...


@pytest.fixture
def page(qtbot, monkeypatch: pytest.MonkeyPatch) -> AccountsPage:
    FakeLoginWindow.opened = []
    monkeypatch.setattr(accounts_module, "LoginWindow", FakeLoginWindow)
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db)
    db.migrate()
    page = AccountsPage(db, config.settings_store(paths), ProfileStore(paths.profiles))
    qtbot.addWidget(page)
    return page


def test_adding_an_account_asks_about_the_terms_once(
    page: AccountsPage, monkeypatch: pytest.MonkeyPatch
):
    asked: list[str] = []

    def yes(parent: object, title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", yes)
    page.name.setText("Work")
    first = page.add_account()
    page.name.setText("Home")
    second = page.add_account()
    assert first is not None and second is not None
    assert len(asked) == 1 and "Facebook's terms" in asked[0]
    assert page.table.rowCount() == 2
    assert page.table.item(1, 2).text() == "not logged in yet"
    assert [a.label for a in FakeLoginWindow.opened] == ["Work", "Home"]
    assert config.load_settings(page.store).accepted_sites == "facebook"


def test_declining_the_terms_adds_nothing(page: AccountsPage, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    page.site.setCurrentIndex(page.site.findData("tiktok"))
    page.name.setText("Tok")
    assert page.add_account() is None
    assert page.table.rowCount() == 0 and FakeLoginWindow.opened == []


def test_a_name_is_needed(page: AccountsPage):
    assert page.add_account() is None
    assert "name" in page.message.text()


def test_log_out_and_delete(page: AccountsPage, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page.name.setText("Work")
    account = page.add_account()
    assert account is not None and account.id is not None
    window = page.windows[account.id]
    folder = page.profiles.folder(account)
    folder.mkdir(parents=True, exist_ok=True)
    page.table.selectRow(0)
    page.delete_btn.click()
    assert page.table.rowCount() == 0
    assert window.closed  # type: ignore[attr-defined]
    assert not folder.exists()
    assert page.message.text().startswith("Logged out and deleted")


def test_status_changes_reach_the_table(page: AccountsPage, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page.name.setText("Work")
    account = page.add_account()
    assert account is not None
    page.profiles.status_changed.emit(account.id, AccountStatus.LOGGED_IN.value)
    assert page.table.item(0, 2).text() == "logged in"


def test_a_login_survives_a_restart(qtbot, db: Database, tmp_path: Path):
    """The cookie store reaches the disk when the app shuts profiles down in order."""
    account = _account(db)
    first = ProfileStore(tmp_path / "profiles")
    profile = first.profile(account)
    page = _wake(profile)
    cookie = QNetworkCookie(b"c_user", b"1000123")
    cookie.setDomain(".facebook.com")
    cookie.setExpirationDate(QDateTime.currentDateTime().addDays(30))
    with qtbot.waitSignal(first.status_changed, timeout=10_000):
        profile.cookieStore().setCookie(cookie, QUrl("https://www.facebook.com/"))
    shiboken6.delete(page)
    first.shutdown()  # the cookie file is written as the profile closes

    second = ProfileStore(tmp_path / "profiles")
    with qtbot.waitSignal(second.status_changed, timeout=10_000) as signal:
        second.check(account)
    assert signal.args == [account.id, AccountStatus.LOGGED_IN.value]
    second.shutdown()
