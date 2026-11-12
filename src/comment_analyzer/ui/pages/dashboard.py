"""Dashboard page: charts for one post, a comparison of several, and exports.

The built-in browser starts the first time the page is shown, since Chromium takes a moment.
"""

from contextlib import closing
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.reports import exports
from comment_analyzer.reports.data import load_post
from comment_analyzer.ui import dashboard_html
from comment_analyzer.ui.dashboard_html import Theme
from comment_analyzer.ui.theme import muted, set_error, title
from comment_analyzer.workers import CancelToken, ProgressFn, TaskRunner, Worker

MAX_COMPARED = 4


class DashboardPage(QWidget):
    web_ready = Signal(bool)  # the built-in browser loaded the first page (--smoke-test)
    exported = Signal(object)  # Path of a finished export

    def __init__(
        self, db: Database, paths: AppPaths, tasks: TaskRunner, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.tasks = tasks
        self.cache = paths.root / "cache"
        self.current_post: int | None = None
        self.open_exports = True  # open the folder in Explorer after exporting (off in tests)
        self._views: dict[str, QWidget] = {}
        self._pending: dict[str, Path] = {}

        self.posts = QComboBox()
        self.export_btn = QPushButton("Export report")
        self.export_btn.setEnabled(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.posts, stretch=1)
        top.addWidget(self.export_btn)
        self.post_tab = QWidget()
        self._post_layout = QVBoxLayout(self.post_tab)
        self._post_layout.addLayout(top)
        self._post_layout.addWidget(self.status)

        self.compare_list = QListWidget()
        self.compare_btn = QPushButton("Compare")
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(muted(f"Tick 2 to {MAX_COMPARED} posts."))
        left_layout.addWidget(self.compare_list, stretch=1)
        left_layout.addWidget(self.compare_btn)
        self.compare_area = QWidget()
        self._compare_layout = QVBoxLayout(self.compare_area)
        self._compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_split = QSplitter()
        compare_split.addWidget(left)
        compare_split.addWidget(self.compare_area)
        compare_split.setSizes([260, 780])

        self.tabs = QTabWidget()
        self.tabs.addTab(self.post_tab, "Post")
        self.tabs.addTab(compare_split, "Compare")
        layout = QVBoxLayout(self)
        layout.addWidget(title("Dashboard"))
        layout.addWidget(self.tabs, stretch=1)

        self.posts.currentIndexChanged.connect(self._on_post_chosen)
        self.export_btn.clicked.connect(self.export)
        self.compare_btn.clicked.connect(self.compare)
        self.refresh_posts()
        self._write("post", dashboard_html.empty_page("Pick a post above.", self.theme()))
        self._write(
            "compare",
            dashboard_html.empty_page("Tick some posts, then click Compare.", self.theme()),
        )

    # --- public -----------------------------------------------------------------------------

    def refresh_posts(self) -> None:
        with closing(self.db.connect()) as conn:
            posts = [p for p in repository.list_posts(conn, limit=200) if p.id is not None]
            counts = {p.id: repository.count_comments(conn, p.id) for p in posts if p.id}
        posts = [p for p in posts if counts.get(p.id or 0)]
        self.posts.blockSignals(True)
        self.posts.clear()
        self.posts.addItem("Pick a post", None)
        for post in posts:
            self.posts.addItem(f"{post.platform.label}: {post.title or post.post_id}", post.id)
        index = self.posts.findData(self.current_post)
        self.posts.setCurrentIndex(max(index, 0))
        self.posts.blockSignals(False)
        checked = self._checked()
        self.compare_list.clear()
        for post in posts:
            item = QListWidgetItem(f"{post.platform.label}: {post.title or post.post_id}")
            item.setData(Qt.ItemDataRole.UserRole, post.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if post.id in checked else Qt.CheckState.Unchecked
            )
            self.compare_list.addItem(item)

    def select_post(self, post_row_id: int) -> None:
        if self.posts.findData(post_row_id) < 0:
            self.refresh_posts()
        index = self.posts.findData(post_row_id)
        if index >= 0:
            self.posts.setCurrentIndex(index)  # renders through _on_post_chosen
            self.render_post(post_row_id)

    def render_post(self, post_row_id: int) -> None:
        report = load_post(self.db, post_row_id)
        if report is None:
            return
        self.current_post = post_row_id
        self.export_btn.setEnabled(bool(report.comments))
        self._write("post", dashboard_html.post_page(report, self.theme(), self._plotly_url()))

    def compare(self) -> None:
        chosen = self._checked()
        if not 2 <= len(chosen) <= MAX_COMPARED:
            self._show_status(f"Tick between 2 and {MAX_COMPARED} posts to compare.", error=True)
            return
        reports = [r for r in (load_post(self.db, i) for i in chosen) if r is not None]
        self._write(
            "compare", dashboard_html.compare_page(reports, self.theme(), self._plotly_url())
        )
        self._show_status("")

    def export(self) -> None:
        post_row_id = self.current_post
        if post_row_id is None:
            return
        db = self.db

        def job(progress: ProgressFn, token: CancelToken) -> Path:
            progress(0, 0, "Writing the report...")
            report = load_post(db, post_row_id)
            if report is None:
                raise ValueError("The post is gone")
            return exports.export_post(report)

        worker = Worker(job, name="Export")
        worker.signals.succeeded.connect(self._exported)
        worker.signals.failed.connect(lambda message: self._show_status(message, error=True))
        worker.signals.finished.connect(lambda: self.export_btn.setEnabled(True))
        self.export_btn.setEnabled(False)
        self._show_status("Writing the Word report, Excel workbook and CSV...")
        self.tasks.start(worker)

    def theme(self) -> Theme:
        palette = self.palette()
        return Theme(
            text=palette.color(QPalette.ColorRole.WindowText).name(),
            muted=palette.color(QPalette.ColorRole.PlaceholderText).name(),
            background=palette.color(QPalette.ColorRole.Window).name(),
            border=palette.color(QPalette.ColorRole.Mid).name(),
        )

    def page_file(self, name: str) -> Path:
        return self.cache / f"{name}.html"

    # --- internals --------------------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._ensure_view("post", self._post_layout)
        self._ensure_view("compare", self._compare_layout)

    def _ensure_view(self, name: str, layout: QVBoxLayout) -> None:
        if name in self._views:
            return
        # Imported here so Chromium only loads when needed
        from PySide6.QtWebEngineWidgets import QWebEngineView

        view = QWebEngineView(self)  # Qt 6's default profile keeps nothing on disk
        if name == "post":
            view.loadFinished.connect(self._first_load)
        layout.addWidget(view, stretch=1)
        self._views[name] = view
        self._load(name)

    def _first_load(self, ok: bool) -> None:
        self.web_ready.emit(ok)

    def _write(self, name: str, page: str) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        self.page_file(name).write_text(page, encoding="utf-8")
        self._load(name)

    def _load(self, name: str) -> None:
        view = self._views.get(name)
        if view is not None:
            view.load(QUrl.fromLocalFile(str(self.page_file(name))))  # type: ignore[attr-defined]

    def _plotly_url(self) -> str:
        return QUrl.fromLocalFile(str(dashboard_html.plotly_js())).toString()

    def _checked(self) -> list[int]:
        chosen = []
        for row in range(self.compare_list.count()):
            item = self.compare_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(item.data(Qt.ItemDataRole.UserRole))
        return chosen

    def _on_post_chosen(self, index: int) -> None:
        post_row_id = self.posts.itemData(index)
        if post_row_id is not None:
            self.render_post(post_row_id)

    def _exported(self, folder: object) -> None:
        assert isinstance(folder, Path)
        self._show_status(f"Saved to {folder}")
        if self.open_exports:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.exported.emit(folder)

    def _show_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        set_error(self.status, error)
