"""Start the app: logging, single instance, database, settings, main window.

    python -m comment_analyzer [link] [--smoke-test] [--minimized]
    python -m comment_analyzer analyse <link> [--limit N] [--newest] [--out FOLDER]

The second form is the command line (cli.py), with no window. --smoke-test opens the window and
the built-in browser, then exits with 0 if both work (the build script runs it on the built .exe).
"""

import ctypes
import logging
import os
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QMessageLogContext,
    QSettings,
    Qt,
    QTimer,
    QtMsgType,
    qInstallMessageHandler,
)
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from comment_analyzer import APP_ID, APP_NAME, __version__, config, logs
from comment_analyzer.analysis import claude_mode
from comment_analyzer.analysis.language import detect_language
from comment_analyzer.browser.bridge import capture_source
from comment_analyzer.config import AppPaths, LegacyImport
from comment_analyzer.db import Database, retention
from comment_analyzer.errors import UserError
from comment_analyzer.instance import SingleInstance
from comment_analyzer.models import Platform
from comment_analyzer.platforms import youtube
from comment_analyzer.platforms.browser_collector import load_selectors
from comment_analyzer.reports.exports import export_check
from comment_analyzer.tracking.startup import MINIMIZED
from comment_analyzer.ui.dashboard_html import plotly_js
from comment_analyzer.ui.icon import app_icon
from comment_analyzer.ui.main_window import MainWindow
from comment_analyzer.ui.theme import apply_theme
from comment_analyzer.ui.tray import create_tray
from comment_analyzer.workers import CancelToken, ProgressFn, TaskRunner, Worker

log = logging.getLogger(__name__)

SMOKE_TEST_TIMEOUT_MS = 60_000
MAINTENANCE_INTERVAL_MS = 6 * 60 * 60 * 1000  # retention rules, while the app stays open


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["analyse"]:
        from comment_analyzer import cli

        return cli.main(args[1:])
    smoke_test = "--smoke-test" in args
    link = next((a for a in args if not a.startswith("-")), "")

    _ensure_std_streams()
    paths = AppPaths.default()
    paths.create()
    _set_taskbar_id()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)  # QtWebEngine
    app = QApplication(sys.argv[:1])

    instance = SingleInstance(paths.root)
    if not instance.acquire():
        # Hand over and exit without opening the log: on Windows, two processes appending to
        # one file overwrite each other's lines, and rotation fails while the file is open.
        instance.send(link)
        return 0

    logs.setup_logging(paths.logs)
    logs.install_crash_handlers(paths.logs)
    qInstallMessageHandler(_qt_message)
    log.info("Starting %s %s on Python %s", APP_NAME, __version__, sys.version.split()[0])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    apply_theme(app)

    config.use_system_keyring()
    config.get_secret(config.YOUTUBE_API_KEY)  # registers the key for log masking

    db = Database(paths.db, backups=paths.backups)
    try:
        db.migrate()
    except UserError as e:
        QMessageBox.critical(None, APP_NAME, str(e))
        return 1
    except Exception:
        log.exception("Couldn't open the database")
        QMessageBox.critical(
            None,
            APP_NAME,
            f"Couldn't open the app's data file. The details are in the log in {paths.logs}.",
        )
        return 1

    with closing(db.connect()) as conn:
        retention.close_interrupted_runs(conn)

    store = config.settings_store(paths)
    tasks = TaskRunner()
    window = MainWindow(db=db, paths=paths, store=store, tasks=tasks)
    instance.message_received.connect(window.handle_external_link)
    maintenance = QTimer(window)
    maintenance.timeout.connect(lambda: _start_maintenance(tasks, db, store))
    maintenance.start(MAINTENANCE_INTERVAL_MS)
    QTimer.singleShot(5_000, lambda: _start_maintenance(tasks, db, store))
    logs.on_crash(lambda exc: QTimer.singleShot(0, lambda: _show_crash(window, paths)))
    tray = create_tray(window)
    if not (MINIMIZED in args and tray is not None):  # "start with Windows" opens in the tray
        window.show()
    if link:
        window.handle_external_link(link)

    if smoke_test:
        _start_smoke_test(app, window)
    else:
        window.tracker.start()
        QTimer.singleShot(0, lambda: _offer_legacy_import(window, store))

    code = app.exec()
    tasks.cancel_all()
    tasks.wait(3000)
    window.profiles.shutdown()  # browser pages, then profiles: cookies reach the disk
    instance.release()
    logs.on_crash(None)
    log.info("Exited with code %s", code)
    return code


def _ensure_std_streams() -> None:
    """A windowed .exe has no console; give libraries somewhere harmless to print."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _set_taskbar_id() -> None:
    """Group the window under the app's own taskbar icon instead of Python's."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.App")
        except OSError:
            log.warning("Couldn't set the taskbar ID", exc_info=True)


_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def _qt_message(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
    logging.getLogger("qt").log(_QT_LEVELS.get(mode, logging.WARNING), "%s", message)


def _show_crash(parent: QWidget, paths: AppPaths) -> None:
    QMessageBox.warning(
        parent,
        APP_NAME,
        f"Something went wrong. The details were saved to the log in {paths.logs}.",
    )


def _offer_legacy_import(window: MainWindow, store: QSettings) -> None:
    """Once: offer to move the key the previous version kept in plain text."""
    settings = config.load_settings(store)
    if settings.legacy_import_offered or config.find_legacy_api_key() is None:
        return
    answer = QMessageBox.question(
        window,
        APP_NAME,
        "The previous version of Comment Analyzer saved your YouTube API key in a plain-text "
        "file.\n\nMove it to Windows Credential Manager and delete the plain-text copy?",
    )
    settings.legacy_import_offered = True
    config.save_settings(store, settings)
    if answer != QMessageBox.StandardButton.Yes:
        return
    result = config.import_legacy_api_key()
    window.settings_page.refresh_key_status()
    if result is LegacyImport.KEPT_EXISTING:
        QMessageBox.information(
            window,
            APP_NAME,
            "You already had a key saved, so it was kept. The plain-text copy was deleted.",
        )


def _start_maintenance(tasks: TaskRunner, db: Database, store: QSettings) -> None:
    days = config.load_settings(store).purge_text_days

    def job(progress: ProgressFn, token: CancelToken) -> None:
        retention.run_maintenance(db, days)

    tasks.start(Worker(job, name="Maintenance"))


def _start_smoke_test(app: QApplication, window: MainWindow) -> None:
    # Load what collection needs (HTTP client with the Windows certificate store, time zones):
    # a packaging gap shows up here rather than on the user's first run.
    try:
        youtube.YouTubeApi("smoke-test").close()
        log.info("Smoke test: YouTube quota day is %s", youtube.quota_day())
        language = detect_language("This is a perfectly ordinary English sentence.")[0]
        import onnxruntime  # loads its DLLs, as analysis will

        providers = onnxruntime.get_available_providers()
        log.info("Smoke test: language %s, ONNX Runtime providers %s", language, providers)
        if language != "en" or "CPUExecutionProvider" not in providers:
            raise RuntimeError("analysis libraries didn't load properly")
        if not plotly_js().is_file():
            raise RuntimeError(f"plotly.js is missing: {plotly_js()}")
        claude_mode.make_client("sk-ant-smoke-test")  # the SDK and pydantic load; nothing sent
        for platform in (Platform.FACEBOOK, Platform.INSTAGRAM, Platform.TIKTOK):
            selectors = load_selectors(platform)  # bundled selectors, more.js and capture.js
            selectors.more_script()
            capture_source(selectors.capture, selectors.document_json)
        with tempfile.TemporaryDirectory() as folder:  # Word, Excel and chart writers load
            export_check(Path(folder))
    except Exception:
        log.exception("Smoke test failed: collection code didn't load")
        QTimer.singleShot(0, lambda: app.exit(3))
        return

    def finish(ok: bool) -> None:
        window.dashboard_page.web_ready.disconnect(finish)  # the page can report "loaded" twice
        log.info("Smoke test %s", "passed" if ok else "failed: the built-in browser didn't load")
        app.exit(0 if ok else 1)

    def time_out() -> None:
        log.error("Smoke test timed out")
        app.exit(2)

    window.dashboard_page.web_ready.connect(finish)
    window.show_page("dashboard")
    QTimer.singleShot(SMOKE_TEST_TIMEOUT_MS, time_out)
