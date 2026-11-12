"""What every collector provides.

Collectors only produce records; CollectionSession (collection.py) stores them. QtWebEngine
lives on the GUI thread, so collectors report through signals instead of returning iterators.
The signals arrive on the GUI thread in this order: post_found once (unless the post can't be
read at all), then any number of batch, progress and api_units, then finished exactly once.
"""

from datetime import datetime

from PySide6.QtCore import QObject, Signal

from comment_analyzer.models import Platform
from comment_analyzer.platforms.detect import LinkInfo


class Collector(QObject):
    platform: Platform

    post_found = Signal(object, object)  # Post, Snapshot
    batch = Signal(list)  # list[Comment]
    progress = Signal(int, int, str)  # done, total (0 if unknown), message
    api_units = Signal(str, int)  # quota day, official-API units used
    blocked = Signal(str)  # login wall, checkpoint or captcha: the browser goes to the user
    finished = Signal(object, str)  # RunStatus, message for the user ("" if none)

    def start(
        self, link: LinkInfo, *, limit: int, sort: str, since: datetime | None = None
    ) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        """Stop soon and keep what was read; finished still follows."""
        raise NotImplementedError
