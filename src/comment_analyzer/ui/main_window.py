"""Main window: navigation on the left, one page per area."""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from comment_analyzer import APP_NAME, config, privacy
from comment_analyzer.analysis.pipeline import LocalAnalyzer
from comment_analyzer.browser.profiles import ProfileStore
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.browser_collector import (
    BrowserCollector,
    choose_account,
    load_selectors,
)
from comment_analyzer.platforms.detect import LinkInfo
from comment_analyzer.tracking.scheduler import Tracker
from comment_analyzer.ui.pages.accounts import AccountsPage
from comment_analyzer.ui.pages.analyse import AnalysePage
from comment_analyzer.ui.pages.dashboard import DashboardPage
from comment_analyzer.ui.pages.post_maker import PostMakerPage
from comment_analyzer.ui.pages.settings import SettingsPage
from comment_analyzer.ui.pages.tracking import TrackingPage
from comment_analyzer.workers import TaskRunner


class MainWindow(QMainWindow):
    def __init__(
        self, *, db: Database, paths: AppPaths, store: QSettings, tasks: TaskRunner
    ) -> None:
        super().__init__()
        self.tasks = tasks
        self.store = store
        self.tray: QSystemTrayIcon | None = None  # set by ui.tray when Windows has a tray
        self.quitting = False
        self._told_about_tray = False
        self.setWindowTitle(APP_NAME)
        self.resize(1040, 700)
        self.setMinimumSize(760, 520)

        analyzer = LocalAnalyzer(paths.models)
        self.paths = paths
        self.db = db
        self.profiles = ProfileStore(paths.profiles, parent=self)
        self.profiles.delete_pending()
        self.analyse_page = AnalysePage(
            db, tasks, store, analyzer=analyzer, browser_factory=self._browser_collector
        )
        self.dashboard_page = DashboardPage(db, paths, tasks)
        self.settings_page = SettingsPage(paths, store, db, tasks)
        self.tracker = Tracker(
            db, store, tasks, self.analyse_page.collector_factory, analyzer, parent=self
        )
        self.tracking_page = TrackingPage(db, self.tracker)
        self.post_maker_page = PostMakerPage(db, store, tasks)
        self.accounts_page = AccountsPage(db, store, self.profiles)
        self.analyse_page.analysis_finished.connect(lambda _: self.post_maker_page.refresh_posts())
        # the dashboard follows the post picked on the Analyse page, and new results
        self.analyse_page.post_shown.connect(self.dashboard_page.select_post)
        self.analyse_page.run_finished.connect(lambda _: self.dashboard_page.refresh_posts())
        self.analyse_page.tracking_changed.connect(self.tracking_page.refresh)
        self.tracker.checked.connect(self._tracked_post_checked)
        pages: list[tuple[str, str, QWidget]] = [
            ("analyse", "Analyse", self.analyse_page),
            ("dashboard", "Dashboard", self.dashboard_page),
            ("accounts", "Accounts", self.accounts_page),
            ("tracking", "Tracking", self.tracking_page),
            ("post_maker", "Post maker", self.post_maker_page),
            ("settings", "Settings", self.settings_page),
        ]
        self._page_names = [name for name, _, _ in pages]

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(180)
        self.stack = QStackedWidget()
        for _, label, page in pages:
            self.nav.addItem(label)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, stretch=1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Data folder: {paths.root}")

    def show_page(self, name: str) -> None:
        self.nav.setCurrentRow(self._page_names.index(name))

    def bring_forward(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def handle_external_link(self, link: str) -> None:
        """A link from the command line, or from a second launch of the app."""
        if link:
            self.show_page("analyse")
            self.analyse_page.set_link(link)
        self.bring_forward()

    def closeEvent(self, event: QCloseEvent) -> None:
        settings = config.load_settings(self.store)
        to_tray = self.tray is not None and settings.keep_in_tray and not self.quitting
        if to_tray and self.tracker.tracked_count():
            event.ignore()
            self.hide()
            if self.tray is not None and not self._told_about_tray:
                self._told_about_tray = True
                self.tray.showMessage(
                    APP_NAME, "Still tracking your posts in the background. Right-click to quit."
                )
            return
        self.tasks.cancel_all()
        for window in list(self.accounts_page.windows.values()):
            window.close()
        super().closeEvent(event)

    def _browser_collector(self, link: LinkInfo) -> Collector:
        """Facebook, Instagram and TikTok: the least-used logged-in account, within the daily
        limit in Settings."""
        assert link.platform is not None
        settings = config.load_settings(self.store)
        account = choose_account(
            self.db, link.platform, settings.posts_per_day, busy=self.profiles.in_use
        )
        return BrowserCollector(
            account,
            self.profiles,
            privacy.author_hasher(),
            selectors=load_selectors(link.platform, self.paths.root / "selectors"),
            debug_dir=self.paths.debug if settings.debug_capture else None,
        )

    def _tracked_post_checked(self, post_row_id: int) -> None:
        self.dashboard_page.refresh_posts()
        if self.analyse_page.current_post == post_row_id and not self.analyse_page.busy:
            self.analyse_page.show_post(post_row_id)
