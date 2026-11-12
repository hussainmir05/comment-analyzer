"""The one door from web pages into the app: capture.js hands over API responses that the page
itself received.

Page scripts can reach this object too (it lives in the page's own JavaScript world), so it
trusts nothing: it accepts one kind of message, only while the page is on the account's own site,
only for addresses the selectors file names, with size and rate caps. Nothing it accepts can do
more than add data to the collection in progress.
"""

import itertools
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from comment_analyzer.browser.sites import Site
from comment_analyzer.timeutil import utc_now

log = logging.getLogger(__name__)

MAX_MESSAGE = 9 * 2**20  # capture.js caps bodies at 8 MB; JSON escaping adds a little
MAX_PER_MINUTE = 600
DOCUMENT = "document:"  # capture.js marks JSON blocks from the page itself this way
_CAPTURE_JS = Path(__file__).with_name("capture.js")
_counter = itertools.count(1)


class CaptureBridge(QObject):
    captured = Signal(str, str)  # url, body

    def __init__(
        self,
        site: Site,
        page_url: Callable[[], QUrl],
        patterns: Sequence[str],
        *,
        debug_dir: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self._page_url = page_url
        self._patterns = [re.compile(p) for p in patterns]
        self._debug_dir = debug_dir
        self._recent: deque[float] = deque()
        self.rejected = 0

    @Slot(str)
    def capture(self, message: str) -> None:
        accepted = self._accept(message)
        if accepted is None:
            self.rejected += 1
            return
        url, body = accepted
        if self._debug_dir is not None:
            self._save_debug(url, body)
        self.captured.emit(url, body)

    def _accept(self, message: str) -> tuple[str, str] | None:
        """The response's address and body, if every check passes."""
        if not isinstance(message, str) or len(message) > MAX_MESSAGE:
            return None
        page = self._page_url()
        if page.scheme() != "https" or not self.site.owns(page.host()):
            return None
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if len(self._recent) >= MAX_PER_MINUTE:
            return None
        try:
            data = json.loads(message)
        except ValueError:
            return None
        if not isinstance(data, dict) or set(data) != {"kind", "url", "status", "body"}:
            return None
        url, body = data["url"], data["body"]
        if data["kind"] != "response" or not isinstance(url, str) or not isinstance(body, str):
            return None
        target = QUrl(url.removeprefix(DOCUMENT))
        if target.scheme() != "https" or not self.site.owns(target.host()):
            return None
        if not any(p.search(url) for p in self._patterns):
            return None
        self._recent.append(now)
        return url, body

    def _save_debug(self, url: str, body: str) -> None:
        """Debug capture (off by default): the response alone, never cookies or headers."""
        assert self._debug_dir is not None
        folder = self._debug_dir / self.site.platform.value
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{utc_now():%Y%m%d-%H%M%S}-{next(_counter):04d}.json"
        record = {"url": url, "body": body}
        (folder / name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def install(
    page: QWebEnginePage, bridge: CaptureBridge, patterns: Sequence[str], document_json: bool
) -> None:
    """Give a collection page the bridge and capture.js, before its first script runs."""
    channel = QWebChannel(page)
    channel.registerObject("capture", bridge)
    page.setWebChannel(channel, QWebEngineScript.ScriptWorldId.MainWorld)
    script = QWebEngineScript()
    script.setName("comment-analyzer-capture")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    script.setSourceCode(capture_source(patterns, document_json))
    page.scripts().insert(script)


def capture_source(patterns: Sequence[str], document_json: bool) -> str:
    """qwebchannel.js (from Qt's resources) followed by capture.js with its settings filled in."""
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Qt's qwebchannel.js resource is missing")
    channel_js = bytes(resource.readAll().data()).decode("utf-8")
    resource.close()
    capture_js = (
        _CAPTURE_JS.read_text(encoding="utf-8")
        .replace("__PATTERNS__", json.dumps(list(patterns)))
        .replace("__DOCUMENT_JSON__", "true" if document_json else "false")
    )
    return channel_js + "\n" + capture_js
