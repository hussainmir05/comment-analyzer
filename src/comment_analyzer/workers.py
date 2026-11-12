"""Run slow work off the UI thread, with progress, Stop and plain-language errors.

A job is a function (progress, token) -> result that runs in the global QThreadPool. It reports
with progress(done, total, message) (total 0 = unknown), and checks token.check() or sleeps
with token.wait() so that Stop takes effect quickly.
"""

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from comment_analyzer.errors import UserError

log = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong. The details were saved to the log."

ProgressFn = Callable[[int, int, str], None]


class Cancelled(Exception):
    """Raised inside a job when the user pressed Stop."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled

    def wait(self, seconds: float) -> None:
        """Sleep, but raise Cancelled as soon as Stop is pressed."""
        if self._event.wait(seconds):
            raise Cancelled


Job = Callable[[ProgressFn, CancelToken], object]


class WorkerSignals(QObject):
    progress = Signal(int, int, str)  # done, total (0 if unknown), message
    succeeded = Signal(object)
    failed = Signal(str)  # message for the user; details are in the log
    cancelled = Signal()
    finished = Signal()  # always last, after one of the three above


class Worker(QRunnable):
    def __init__(self, job: Job, name: str = "job") -> None:
        super().__init__()
        self.setAutoDelete(False)  # Python owns it; TaskRunner keeps it alive while it runs
        self.job = job
        self.name = name
        self.token = CancelToken()
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        self.token.cancel()

    def run(self) -> None:
        try:
            result = self.job(self.signals.progress.emit, self.token)
        except Cancelled:
            log.info("%s: stopped by the user", self.name)
            self.signals.cancelled.emit()
        except UserError as e:
            log.info("%s: %s", self.name, e)
            self.signals.failed.emit(str(e))
        except Exception:
            log.exception("%s failed", self.name)
            self.signals.failed.emit(GENERIC_ERROR)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class TaskRunner(QObject):
    """Starts workers on the global thread pool and keeps them alive until they finish."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running: set[Worker] = set()

    def start(self, worker: Worker) -> Worker:
        self._running.add(worker)
        worker.signals.finished.connect(lambda: self._running.discard(worker))
        QThreadPool.globalInstance().start(worker)
        return worker

    def cancel_all(self) -> None:
        for worker in list(self._running):
            worker.cancel()

    def wait(self, msecs: int = 5000) -> bool:
        """Block until running jobs end (used when the app quits)."""
        return QThreadPool.globalInstance().waitForDone(msecs)
