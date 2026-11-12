"""The Facebook, Instagram and TikTok collector: the post opens in a visible window of the
account's own browser profile, and the app reads what the page receives.

- capture.js passes the page's own API answers to the bridge; the site's parser turns them into
  records.
- Every 2-5 seconds (random), one "load more" step clicks at most a few "View more comments" or
  "View replies" buttons named in the selectors file, then scrolls.
- It stops at the comment limit, when nothing new arrives for a few steps, at the step cap, or
  when the user presses Stop.
- At a login wall, checkpoint or captcha it stops, emits blocked, and leaves the window open for
  the user. It never solves anything, and never likes, comments or posts.
"""

import json
import logging
import random
from collections.abc import Callable, Collection
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from comment_analyzer import APP_NAME
from comment_analyzer.browser.bridge import CaptureBridge, install
from comment_analyzer.browser.login import SitePage
from comment_analyzer.browser.profiles import ProfileStore
from comment_analyzer.browser.sites import SITES
from comment_analyzer.db import Database, repository
from comment_analyzer.errors import UserError
from comment_analyzer.models import Account, AccountStatus, Comment, Platform, Post, RunStatus
from comment_analyzer.platforms import facebook, instagram, tiktok
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo, detect
from comment_analyzer.platforms.web_data import Hasher, Parser, PostInfo
from comment_analyzer.timeutil import utc_now

log = logging.getLogger(__name__)

PARSERS: dict[Platform, Parser] = {
    Platform.FACEBOOK: facebook.parse,
    Platform.INSTAGRAM: instagram.parse,
    Platform.TIKTOK: tiktok.parse,
}
PAUSE = (2.0, 5.0)  # seconds between steps, at random: human pace
QUIET_STEPS = 4  # steps in a row with no new comments and nothing to click: done
MAX_CLICKS = 3  # buttons per step
_BUNDLED = Path(__file__).with_name("selectors")


@dataclass(frozen=True, slots=True)
class Selectors:
    capture: tuple[str, ...]
    document_json: bool
    more_buttons: tuple[str, ...]
    scroll: bool
    wall_urls: tuple[str, ...]
    wall_texts: tuple[str, ...]
    max_steps: int

    def more_script(self) -> str:
        settings = {
            "buttons": list(self.more_buttons),
            "wallTexts": list(self.wall_texts),
            "scroll": self.scroll,
            "maxClicks": MAX_CLICKS,
        }
        source = (_BUNDLED / "more.js").read_text(encoding="utf-8")
        return source.replace("__SETTINGS__", json.dumps(settings))

    def wall_in(self, url: str) -> bool:
        path = QUrl(url).path()
        return any(path.startswith(w) or w in path for w in self.wall_urls)


def load_selectors(platform: Platform, override_dir: Path | None = None) -> Selectors:
    """The bundled selectors file, or a copy in the data folder's selectors/ that replaces it
    (so a site change can be fixed without a new release)."""
    name = f"{platform.value}.json"
    override = override_dir / name if override_dir is not None else None
    source = override if override is not None and override.is_file() else _BUNDLED / name
    data = json.loads(source.read_text(encoding="utf-8"))
    return Selectors(
        capture=tuple(data["capture"]),
        document_json=bool(data.get("document_json")),
        more_buttons=tuple(data.get("more_buttons", ())),
        scroll=bool(data.get("scroll", True)),
        wall_urls=tuple(data.get("wall_urls", ())),
        wall_texts=tuple(data.get("wall_texts", ())),
        max_steps=int(data.get("max_steps", 300)),
    )


class Surface(Protocol):
    """Where the page runs: the visible collection window (tests use a stand-in)."""

    loaded: Any  # Signal(bool)
    url_changed: Any  # Signal(str)
    captured: Any  # Signal(str, str): response URL, body
    stop_clicked: Any  # Signal()

    def open(self, url: str) -> None: ...
    def run_js(self, script: str, done: Callable[[Any], None]) -> None: ...
    def current_url(self) -> str: ...
    def set_status(self, text: str) -> None: ...
    def hand_over(self, message: str) -> None: ...
    def close(self) -> bool: ...


class CollectionWindow(QWidget):
    """The visible browser window a collection runs in."""

    loaded = Signal(bool)
    url_changed = Signal(str)
    captured = Signal(str, str)
    stop_clicked = Signal()

    def __init__(
        self,
        account: Account,
        profiles: ProfileStore,
        selectors: Selectors,
        debug_dir: Path | None,
    ) -> None:
        super().__init__(None)
        self.setWindowTitle(f"{APP_NAME}: reading {account.platform.label} as {account.label}")
        self.resize(1000, 800)
        self.view = QWebEngineView(self)
        self.page = SitePage(profiles.profile(account), self.view)
        self.view.setPage(self.page)
        site = SITES[account.platform]
        self.bridge = CaptureBridge(
            site, self.page.url, selectors.capture, debug_dir=debug_dir, parent=self
        )
        install(self.page, self.bridge, selectors.capture, selectors.document_json)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.stop_btn = QPushButton("Stop")
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, stretch=1)
        bottom.addWidget(self.stop_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, stretch=1)
        layout.addLayout(bottom)
        self.page.loadFinished.connect(self.loaded)
        self.page.urlChanged.connect(lambda u: self.url_changed.emit(u.toString()))
        self.bridge.captured.connect(self.captured)
        self.stop_btn.clicked.connect(self.stop_clicked)

    def open(self, url: str) -> None:
        self.show()
        self.page.load(QUrl(url))

    def run_js(self, script: str, done: Callable[[Any], None]) -> None:
        self.page.runJavaScript(script, 0, done)

    def current_url(self) -> str:
        return self.page.url().toString()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def hand_over(self, message: str) -> None:
        self.status.setText(message)
        self.stop_btn.setEnabled(False)
        self.show()
        self.raise_()
        self.activateWindow()


SurfaceFactory = Callable[[Account, ProfileStore, Selectors, Path | None], Surface]


class BrowserCollector(Collector):
    def __init__(
        self,
        account: Account,
        profiles: ProfileStore,
        hasher: Hasher,
        *,
        selectors: Selectors | None = None,
        debug_dir: Path | None = None,
        surface_factory: SurfaceFactory | None = None,
        pause: tuple[float, float] = PAUSE,
        rng: random.Random | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.platform = account.platform
        self.account = account
        self.account_id = account.id  # CollectionSession records it with the run
        self.profiles = profiles
        self.hasher = hasher
        self.selectors = selectors or load_selectors(account.platform)
        self.debug_dir = debug_dir
        self.surface_factory: SurfaceFactory = surface_factory or CollectionWindow
        self.pause = pause
        self.rng = rng or random.Random()
        self.parser = PARSERS[account.platform]
        self.surface: Surface | None = None
        self.base: Post | None = None
        self.info = PostInfo()
        self.seen: set[str] = set()
        self.waiting: list[Comment] = []  # read before the post was announced
        self.limit = 0
        self.steps = 0
        self.quiet = 0
        self.announced = False
        self.done = False
        self._stopping = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)
        self._seen_at_last_step = 0

    # --- Collector ------------------------------------------------------------------------

    def start(self, link: LinkInfo, *, limit: int, sort: str, since: Any = None) -> None:
        self.limit = limit
        if link.ok and link.post_id and link.canonical_url:
            self.base = Post(self.platform, link.post_id, link.canonical_url)
        surface = self.surface_factory(self.account, self.profiles, self.selectors, self.debug_dir)
        self.surface = surface
        if self.profiles is not None and self.account_id is not None:
            self.profiles.in_use.add(self.account_id)
        surface.loaded.connect(self._loaded)
        surface.url_changed.connect(self._url_changed)
        surface.captured.connect(self._captured)
        surface.stop_clicked.connect(self.stop)
        self._say(f"Opening the {self.platform.label} post...")
        if not link.canonical_url:
            self._finish(RunStatus.FAILED, "That link can't be opened.")
            return
        surface.open(link.canonical_url)  # a share link opens as pasted, then leads to the post

    def stop(self) -> None:
        if not self.done:
            self._stopping = True
            self._finish(RunStatus.STOPPED, "Stopped. The comments read so far are kept.")

    # --- page events ----------------------------------------------------------------------

    def _loaded(self, ok: bool) -> None:
        if self.done:
            return
        if not ok and not self.seen and self.info == PostInfo():
            self._finish(
                RunStatus.FAILED,
                f"Couldn't open the {self.platform.label} post. Check the link and your "
                "internet connection.",
            )
            return
        if self.base is None:  # a share link: the page shows where it led
            found = detect(self._current_url())
            if not found.ok or found.platform is not self.platform or not found.post_id:
                self._finish(RunStatus.FAILED, "That link didn't lead to a single post.")
                return
            assert found.canonical_url is not None
            self.base = Post(self.platform, found.post_id, found.canonical_url)
        if not self._timer.isActive():
            self._schedule()

    def _url_changed(self, url: str) -> None:
        if not self.done and self.selectors.wall_in(url):
            self._blocked(f"{self.platform.label} asked you to log in or check something.")

    def _captured(self, url: str, body: str) -> None:
        if self.done:
            return
        try:
            parsed = self.parser(url, body, self.hasher)
        except Exception:  # a site change must not crash the app
            log.exception("Couldn't read a %s response", self.platform.label)
            return
        if parsed.post is not None:
            self.info.merge(parsed.post)
        fresh = [c for c in parsed.comments if c.platform_comment_id not in self.seen]
        room = self.limit - len(self.seen) if self.limit else len(fresh)
        fresh = fresh[: max(room, 0)]
        if not fresh:
            return
        now = utc_now()
        for comment in fresh:
            comment.collected_at = now
            self.seen.add(comment.platform_comment_id)
        if self.announced:
            self.batch.emit(fresh)
        else:
            self.waiting.extend(fresh)
        self._progress()
        if self.limit and len(self.seen) >= self.limit:
            self._finish(RunStatus.COMPLETED, "")

    # --- the driver -----------------------------------------------------------------------

    def _schedule(self) -> None:
        seconds = self.rng.uniform(*self.pause)
        self._timer.start(round(seconds * 1000))

    def _step(self) -> None:
        if self.done or self.surface is None:
            return
        if self.seen or self.info != PostInfo():
            self._announce()
        if self.steps >= self.selectors.max_steps:
            self._finish(RunStatus.COMPLETED, "Stopped after the most steps allowed per post.")
            return
        self.steps += 1
        self.surface.run_js(self.selectors.more_script(), self._step_done)

    def _step_done(self, result: Any) -> None:
        if self.done:
            return
        if isinstance(result, str):  # more.js answers with JSON text
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        result = result if isinstance(result, dict) else {}
        if result.get("wall"):
            self._blocked(f'{self.platform.label} showed "{result["wall"]}".')
            return
        new = len(self.seen) - self._seen_at_last_step
        self._seen_at_last_step = len(self.seen)
        self.quiet = 0 if new or result.get("clicked") else self.quiet + 1
        if self.quiet >= QUIET_STEPS:
            self._finish(RunStatus.COMPLETED, "")
            return
        self._schedule()

    # --- endings --------------------------------------------------------------------------

    def _announce(self) -> None:
        """post_found once, with what's known by the first step, then the comments so far."""
        if self.announced or self.base is None:
            return
        self.announced = True
        post, snapshot = self.info.records(self.base, utc_now())
        self.post_found.emit(post, snapshot)
        if self.waiting:
            self.batch.emit(self.waiting)
            self.waiting = []

    def _blocked(self, reason: str) -> None:
        self.blocked.emit(reason)
        message = (
            f"{reason} Finish it in the browser window if you want to, then try again. "
            "The comments read so far are kept."
        )
        self._finish(RunStatus.BLOCKED, message, hand_over=True)

    def _finish(self, status: RunStatus, message: str, *, hand_over: bool = False) -> None:
        if self.done:
            return
        self.done = True
        self._timer.stop()
        if self.profiles is not None and self.account_id is not None:
            self.profiles.in_use.discard(self.account_id)
        if self.seen or self.info != PostInfo():
            self._announce()
        elif status is RunStatus.COMPLETED:
            status = RunStatus.FAILED
            message = (
                f"{self.platform.label} showed no comments. The post may be private, or the "
                "page may have changed (the selectors file may need an update)."
            )
        surface = self.surface
        if surface is not None:
            if hand_over:
                surface.hand_over(message)
            else:
                surface.set_status(message or "Done.")
                QTimer.singleShot(1500, surface.close)
        self.finished.emit(status, message)

    def _progress(self) -> None:
        reported = self.info.comments_reported or 0
        total = max(reported, len(self.seen)) if reported else 0
        text = f"Read {len(self.seen):,} {self.platform.label} comments"
        text += f" of about {reported:,}" if reported else ""
        self._say(text + "...", len(self.seen), total)

    def _say(self, text: str, done: int = 0, total: int = 0) -> None:
        self.progress.emit(done, total, text)
        if self.surface is not None:
            self.surface.set_status(text)

    def _current_url(self) -> str:
        return self.surface.current_url() if self.surface is not None else ""


def choose_account(
    db: Database, platform: Platform, posts_per_day: int, busy: Collection[int] = ()
) -> Account:
    """The logged-in account that has read the fewest posts today, within the daily limit.
    busy: accounts reading another post right now (one post at a time per account)."""
    today = datetime.now().date().isoformat()
    with closing(db.connect()) as conn:
        accounts = [
            a
            for a in repository.list_accounts(conn, platform)
            if a.status is AccountStatus.LOGGED_IN and a.id is not None
        ]
        used = {a.id: repository.account_usage(conn, a.id or 0, today)[0] for a in accounts}
    if not accounts:
        raise UserError(
            f"Log in to {platform.label} on the Accounts page first (add an account there). "
            "If you already did, click Check login there."
        )
    open_accounts = [a for a in accounts if used[a.id] < posts_per_day]
    if not open_accounts:
        raise UserError(
            f"Your {platform.label} accounts have read {posts_per_day} posts each today, the "
            "daily limit in Settings. Try again tomorrow, or change the limit."
        )
    free = [a for a in open_accounts if a.id not in busy]
    if not free:
        raise UserError(
            f"Your {platform.label} accounts are reading another post right now. Try again when "
            "it has finished."
        )
    return min(free, key=lambda a: (used[a.id], a.id or 0))
