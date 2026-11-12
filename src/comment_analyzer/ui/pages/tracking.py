"""Tracking page: the posts being followed, how they've grown, and controls."""

from contextlib import closing

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer.db import Database, repository
from comment_analyzer.tracking.scheduler import Tracker
from comment_analyzer.ui.theme import muted, title

HEADERS = ("Post", "Last check", "Views", "Comments (reported)", "Change since tracking began")


class TrackingPage(QWidget):
    def __init__(self, db: Database, tracker: Tracker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.tracker = tracker
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 320)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.check_btn = QPushButton("Check now")
        self.untrack_btn = QPushButton("Stop tracking")
        self.status = muted()
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.untrack_btn)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(title("Tracking"))
        layout.addWidget(
            muted(
                'Tick "Track this post" on the Analyse page to follow a YouTube video. While the '
                "app runs, it checks tracked posts on the interval set in Settings, collects new "
                "comments and alerts you to spikes. YouTube history covers the last 30 days, as "
                "YouTube's rules require."
            )
        )
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(self.table, stretch=1)
        self.check_btn.clicked.connect(tracker.check_now)
        self.untrack_btn.clicked.connect(self.untrack_selected)
        tracker.checked.connect(lambda _: self.refresh())
        tracker.status.connect(self.status.setText)
        self.refresh()

    def refresh(self) -> None:
        with closing(self.db.connect()) as conn:
            rows = []
            for post in repository.list_tracked(conn):
                assert post.id is not None
                rows.append((post, repository.list_snapshots(conn, post.id)))
        self.table.setRowCount(len(rows))
        for row, (post, snapshots) in enumerate(rows):
            last = snapshots[-1] if snapshots else None
            first = snapshots[0] if snapshots else None
            change = ""
            if first and last and first is not last:
                parts = []
                for label, a, b in (
                    ("views", first.views, last.views),
                    ("comments", first.comments_reported, last.comments_reported),
                ):
                    if a is not None and b is not None:
                        parts.append(f"{b - a:+,} {label}")
                change = ", ".join(parts)
            values = (
                post.title or post.post_id,
                last.taken_at.astimezone().strftime("%Y-%m-%d %H:%M") if last else "not yet",
                f"{last.views:,}" if last and last.views is not None else "",
                f"{last.comments_reported:,}"
                if last and last.comments_reported is not None
                else "",
                change,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, post.id)
                self.table.setItem(row, column, item)
        self.untrack_btn.setEnabled(bool(rows))
        self.check_btn.setEnabled(bool(rows))

    def untrack_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        with closing(self.db.connect()) as conn:
            for row in rows:
                item = self.table.item(row, 0)
                if item is not None:
                    repository.set_tracked(conn, item.data(Qt.ItemDataRole.UserRole), False)
        self.refresh()
