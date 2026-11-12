"""A sortable, filterable table of collected comments, with their analysis.

Commenter names never appear: they aren't stored.
"""

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer.analysis.language import language_name
from comment_analyzer.models import AnalysisResult, Comment, Topic
from comment_analyzer.ui.theme import muted

SORT_ROLE = Qt.ItemDataRole.UserRole
SENTIMENT_ROLE = Qt.ItemDataRole.UserRole + 1
TEXT_GONE = "(text deleted after the retention period)"
HEADERS = ("Posted", "Likes", "Replies", "Type", "Sentiment", "Language", "Topic", "Comment")
SENTIMENT_COLUMN = 4
TOPIC_COLUMN = 6
TEXT_COLUMN = 7
_ORDER = {"negative": 0, "neutral": 1, "positive": 2, "spam": 3}
_ROOT = QModelIndex()
Index = QModelIndex | QPersistentModelIndex
Row = tuple[Comment, AnalysisResult | None]


def sentiment_label(result: AnalysisResult | None) -> str:
    if result is None or result.sentiment is None:
        return ""
    if result.spam_flag:
        return "spam"
    return result.sentiment + (" (unsure)" if result.low_confidence else "")


class CommentsModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[Row] = []
        self._topics: dict[int, str] = {}  # label by topic ID

    def set_rows(self, rows: list[Row], topics: dict[int, str] | None = None) -> None:
        self.beginResetModel()
        self._rows = rows
        self._topics = topics or {}
        self.endResetModel()

    def rowCount(self, parent: Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        comment, result = self._rows[index.row()]
        column = index.column()
        if column == TOPIC_COLUMN and role in (Qt.ItemDataRole.DisplayRole, SORT_ROLE):
            return self._topics.get(result.topic_id, "") if result and result.topic_id else ""
        if role == Qt.ItemDataRole.DisplayRole:
            return _display(comment, result, column)
        if role == SORT_ROLE:
            return _sort_key(comment, result, column)
        if role == SENTIMENT_ROLE:
            label = sentiment_label(result)
            return label.split(" ")[0] if label else ""
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == TEXT_COLUMN:
                return comment.text or TEXT_GONE
            if column == 5 and comment.lang:
                return language_name(comment.lang)
            if column == SENTIMENT_COLUMN and result and result.confidence is not None:
                return f"Model confidence {result.confidence:.0%}"
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 2):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


def _display(comment: Comment, result: AnalysisResult | None, column: int) -> str:
    match column:
        case 0:
            when = comment.created_at
            return when.astimezone().strftime("%Y-%m-%d %H:%M") if when else ""
        case 1:
            return "" if comment.like_count is None else f"{comment.like_count:,}"
        case 2:
            return "" if comment.reply_count is None else f"{comment.reply_count:,}"
        case 3:
            return "Reply" if comment.parent_id else "Comment"
        case 4:
            return sentiment_label(result)
        case 5:
            return comment.lang or ""
        case _:
            return " ".join((comment.text or TEXT_GONE).split())  # one line per comment


def _sort_key(comment: Comment, result: AnalysisResult | None, column: int) -> object:
    match column:
        case 0:
            return comment.created_at.timestamp() if comment.created_at else 0.0
        case 1:
            return comment.like_count or 0
        case 2:
            return comment.reply_count or 0
        case 3:
            return 1 if comment.parent_id else 0
        case 4:
            label = sentiment_label(result).split(" ")[0]
            return _ORDER.get(label, -1)
        case 5:
            return comment.lang or ""
        case _:
            return (comment.text or "").lower()


class _Filter(QSortFilterProxyModel):
    """Text search on the comment, plus an optional sentiment and topic."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sentiment = ""
        self.topic = ""

    def filterAcceptsRow(self, source_row: int, source_parent: Index) -> bool:
        if self.sentiment:
            index = self.sourceModel().index(source_row, SENTIMENT_COLUMN, source_parent)
            if index.data(SENTIMENT_ROLE) != self.sentiment:
                return False
        if self.topic:
            index = self.sourceModel().index(source_row, TOPIC_COLUMN, source_parent)
            if index.data() != self.topic:
                return False
        return super().filterAcceptsRow(source_row, source_parent)


class CommentsView(QWidget):
    """The table plus filters. Most-liked comments come first."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = CommentsModel(self)
        self.proxy = _Filter(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setFilterKeyColumn(TEXT_COLUMN)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter comments")
        self.filter.setClearButtonEnabled(True)
        self.sentiment = QComboBox()
        for label, value in (
            ("All comments", ""),
            ("Positive", "positive"),
            ("Neutral", "neutral"),
            ("Negative", "negative"),
            ("Spam", "spam"),
        ):
            self.sentiment.addItem(label, value)
        self.topic = QComboBox()
        self.topic.setToolTip("Comments that say much the same thing are grouped into topics")
        self.topic.setMaximumWidth(260)
        self.count = muted()
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((130, 60, 60, 70, 120, 70, 160)):  # fixed: sizing is slow
            self.table.setColumnWidth(column, width)

        top = QHBoxLayout()
        top.addWidget(self.filter, stretch=1)
        top.addWidget(self.sentiment)
        top.addWidget(self.topic)
        top.addWidget(self.count)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.table, stretch=1)
        self.filter.textChanged.connect(self._apply_filter)
        self.sentiment.currentIndexChanged.connect(self._apply_filter)
        self.topic.currentIndexChanged.connect(self._apply_filter)
        self._set_topic_choices([])
        self._update_count()

    def set_comments(
        self,
        comments: list[Comment],
        analysis: dict[int, AnalysisResult] | None = None,
        topics: Sequence[Topic] = (),
    ) -> None:
        results = analysis or {}
        labels = {t.id: t.label for t in topics if t.id is not None}
        self.model.set_rows([(c, results.get(c.id) if c.id else None) for c in comments], labels)
        self._set_topic_choices(list(labels.values()))
        self._apply_filter()

    def _set_topic_choices(self, labels: list[str]) -> None:
        self.topic.blockSignals(True)
        self.topic.clear()
        self.topic.addItem("All topics", "")
        for label in labels:
            self.topic.addItem(label, label)
        self.topic.setVisible(bool(labels))
        self.topic.blockSignals(False)

    def _apply_filter(self, *_: object) -> None:
        self.proxy.sentiment = self.sentiment.currentData() or ""
        self.proxy.topic = self.topic.currentData() or ""
        self.proxy.setFilterFixedString(self.filter.text())  # also re-runs the sentiment filter
        self.proxy.invalidate()
        self._update_count()

    def _update_count(self) -> None:
        total, shown = self.model.rowCount(), self.proxy.rowCount()
        text = f"{shown:,} of {total:,} comments" if shown != total else f"{total:,} comments"
        self.count.setText(text)
