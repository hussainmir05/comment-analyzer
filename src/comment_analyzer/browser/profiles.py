"""One Chromium profile per browser account, in profiles/<profile_dir>/ in the data folder.

A profile keeps the account's cookies, so it stays logged in across restarts. The app never sees
or stores passwords: the user types them into the site's own login page. Two accounts on the same
site are two profiles, logged in at once.

Whether an account is logged in comes from the site's login cookie (sites.py): the profile's
cookie file for earlier sessions, and the cookie store's signals for this one.
"""

import logging
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineProfileBuilder
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from comment_analyzer.browser.sites import SITES
from comment_analyzer.models import Account, AccountStatus, Platform

log = logging.getLogger(__name__)

PENDING_DELETES = ".delete-at-start"  # folders Chromium still held when their account went
_CHROME_EPOCH = 11_644_473_600  # seconds from 1601-01-01, where Chromium's times start
_PROFILE_DIR = re.compile(r"(?:facebook|instagram|tiktok)-[0-9a-f]{12}")


def new_profile_dir(platform: Platform) -> str:
    return f"{platform.value}-{secrets.token_hex(6)}"


class ProfileStore(QObject):
    """Creates each account's profile on first use and keeps it for the session (Chromium allows
    one profile object per folder)."""

    status_changed = Signal(int, str)  # account ID, AccountStatus value

    def __init__(self, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = root
        self._profiles: dict[int, QWebEngineProfile] = {}
        self._accounts: dict[int, Account] = {}
        self._live: dict[int, dict[str, bool]] = {}  # login cookies set or deleted this session
        self._status: dict[int, AccountStatus] = {}
        self.in_use: set[int] = set()  # accounts reading a post now: one post at a time each

    def folder(self, account: Account) -> Path:
        return self.root / account.profile_dir

    def profile(self, account: Account) -> QWebEngineProfile:
        assert account.id is not None
        existing = self._profiles.get(account.id)
        if existing is not None:
            return existing
        folder = self.folder(account)
        folder.mkdir(parents=True, exist_ok=True)
        builder = QWebEngineProfileBuilder()
        builder.setPersistentStoragePath(str(folder / "storage"))
        builder.setCachePath(str(folder / "cache"))
        # keep session cookies too, so a login survives a restart
        builder.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        profile = builder.createProfile(account.profile_dir, self)
        account_id = account.id
        cookies = profile.cookieStore()
        cookies.cookieAdded.connect(lambda c: self._cookie(account_id, c, added=True))
        cookies.cookieRemoved.connect(lambda c: self._cookie(account_id, c, added=False))
        self._profiles[account_id] = profile
        self._accounts.setdefault(account_id, account)
        self._status.setdefault(account_id, account.status)
        return profile

    def check(self, account: Account) -> None:
        """Work out the login state; status_changed follows if it changed."""
        assert account.id is not None
        self._accounts.setdefault(account.id, account)
        self._status.setdefault(account.id, account.status)
        self._update(account.id)

    def is_logged_in(self, account_id: int) -> bool:
        account = self._accounts.get(account_id)
        if account is None:
            return False
        live = self._live.get(account_id, {})
        saved = {name for name in self._saved_login_cookies(account) if live.get(name) is not False}
        return bool(saved or any(live.values()))

    def _saved_login_cookies(self, account: Account) -> set[str]:
        """Names of the site's unexpired login cookies in the profile's cookie file. Only names,
        sites and expiry dates are read, never values (Chromium encrypts those anyway).

        Chromium doesn't report the cookies it loads from disk, so this file is the only way to
        see a login from an earlier session.
        """
        path = self.folder(account) / "storage" / "Cookies"
        if not path.is_file():
            return set()
        site = SITES[account.platform]
        marks = ",".join("?" * len(site.login_cookies))
        now = (time.time() + _CHROME_EPOCH) * 1_000_000
        try:
            # immutable: never lock or change the file, even while the profile has it open
            uri = f"{path.as_uri()}?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                rows = conn.execute(
                    "SELECT host_key, name, has_expires, expires_utc FROM cookies "
                    f"WHERE name IN ({marks})",
                    site.login_cookies,
                ).fetchall()
        except sqlite3.Error:
            log.warning("Couldn't read the cookie file of %s", account.profile_dir)
            return set()
        return {
            name
            for host, name, has_expires, expires in rows
            if site.owns(host.lstrip(".")) and (not has_expires or expires > now)
        }

    def _update(self, account_id: int) -> None:
        logged_in = self.is_logged_in(account_id)
        self._set_status(
            account_id, AccountStatus.LOGGED_IN if logged_in else AccountStatus.LOGGED_OUT
        )

    def remove(self, account: Account) -> bool:
        """Log out (delete every cookie) and delete the account's folder. Call only once its
        windows are closed. Returns False when Chromium still holds files: they're deleted at the
        next start."""
        assert account.id is not None
        profile = self._profiles.pop(account.id, None)
        self._accounts.pop(account.id, None)
        self._live.pop(account.id, None)
        self._status.pop(account.id, None)
        if profile is not None:
            profile.cookieStore().deleteAllCookies()
            profile.clearHttpCache()
            profile.deleteLater()
        folder = self.folder(account)
        try:
            shutil.rmtree(folder)
        except FileNotFoundError:
            pass
        except OSError:
            log.info("Profile folder %s is still in use; deleting it at the next start", folder)
            with (self.root / PENDING_DELETES).open("a", encoding="utf-8") as f:
                f.write(account.profile_dir + "\n")
            return False
        return True

    def shutdown(self) -> None:
        """At exit: delete every page that uses one of these profiles, then the profiles. A
        profile writes its cookies to disk as it closes, and must outlive its pages."""
        ours = list(self._profiles.values())
        for widget in QApplication.allWidgets():
            if (
                isinstance(widget, QWebEngineView)
                and shiboken6.isValid(widget)
                and widget.page().profile() in ours
            ):
                shiboken6.delete(widget.window())  # a login or collection window
        for profile in ours:
            if shiboken6.isValid(profile):
                shiboken6.delete(profile)
        self._profiles.clear()

    def delete_pending(self) -> None:
        """At startup, before any profile is opened: finish deletions that had to wait."""
        marker = self.root / PENDING_DELETES
        if not marker.is_file():
            return
        left = []
        for name in marker.read_text(encoding="utf-8").split():
            if not _PROFILE_DIR.fullmatch(name):  # never "..", a path or anything else
                continue
            folder = self.root / name
            try:
                shutil.rmtree(folder)
            except FileNotFoundError:
                pass
            except OSError:
                left.append(name)
        if left:
            marker.write_text("\n".join(left) + "\n", encoding="utf-8")
        else:
            marker.unlink()

    def _cookie(self, account_id: int, cookie: QNetworkCookie, *, added: bool) -> None:
        account = self._accounts.get(account_id)
        if account is None:
            return
        site = SITES[account.platform]
        name = bytes(cookie.name().data()).decode("latin-1")
        if name not in site.login_cookies or not site.owns(cookie.domain().lstrip(".")):
            return
        live = self._live.setdefault(account_id, {})
        live[name] = added and bool(bytes(cookie.value().data()))
        self._update(account_id)

    def _set_status(self, account_id: int, status: AccountStatus) -> None:
        if self._status.get(account_id) != status:
            self._status[account_id] = status
            self.status_changed.emit(account_id, status.value)
