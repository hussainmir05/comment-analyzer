import threading
from collections.abc import Iterator

import pytest

from comment_analyzer.errors import UserError
from comment_analyzer.workers import GENERIC_ERROR, CancelToken, ProgressFn, TaskRunner, Worker


@pytest.fixture
def runner(qapp) -> Iterator[TaskRunner]:
    tasks = TaskRunner()
    yield tasks
    tasks.cancel_all()
    assert tasks.wait(5000)


def test_progress_then_result(qtbot, runner: TaskRunner):
    def job(progress: ProgressFn, token: CancelToken) -> int:
        progress(1, 2, "half")
        progress(2, 2, "done")
        return 42

    worker = Worker(job)
    seen: list[tuple[int, int, str]] = []
    results: list[object] = []
    worker.signals.progress.connect(lambda *args: seen.append(args))
    worker.signals.succeeded.connect(results.append)
    with qtbot.waitSignal(worker.signals.finished):  # always emitted last
        runner.start(worker)
    assert seen == [(1, 2, "half"), (2, 2, "done")]
    assert results == [42]


def test_stop_ends_the_job_quickly(qtbot, runner: TaskRunner):
    started = threading.Event()

    def job(progress: ProgressFn, token: CancelToken) -> str:
        started.set()
        token.wait(30)
        return "never"

    worker = Worker(job)
    runner.start(worker)
    assert started.wait(5)
    with qtbot.waitSignal(worker.signals.cancelled, timeout=2000):
        worker.cancel()


def test_user_errors_are_shown_as_they_are(qtbot, runner: TaskRunner):
    def job(progress: ProgressFn, token: CancelToken) -> None:
        raise UserError("Today's YouTube quota is used up.")

    worker = Worker(job)
    with qtbot.waitSignal(worker.signals.failed) as blocker:
        runner.start(worker)
    assert blocker.args == ["Today's YouTube quota is used up."]


def test_unexpected_errors_are_logged_not_shown(qtbot, runner: TaskRunner, caplog):
    def job(progress: ProgressFn, token: CancelToken) -> None:
        raise RuntimeError("internal detail")

    worker = Worker(job, name="demo")
    with qtbot.waitSignal(worker.signals.failed) as blocker:
        runner.start(worker)
    assert blocker.args == [GENERIC_ERROR]
    assert "internal detail" in caplog.text
