"""Look and feel: the native Windows style plus a few sizes.

No hard-coded colours except the error red, so the app follows Windows' light and dark modes.
"""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel

ERROR_COLOR = "#d13438"  # readable on light and dark backgrounds

STYLE_SHEET = f"""
QLabel[role="title"] {{ font-size: 16pt; font-weight: 600; }}
QLabel[role="error"] {{ color: {ERROR_COLOR}; }}
QListWidget#nav {{ font-size: 11pt; }}
QListWidget#nav::item {{ padding: 8px 10px; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyleSheet(STYLE_SHEET)


def title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "title")
    return label


def muted(text: str = "") -> QLabel:
    """Secondary text, in the palette's placeholder colour."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def set_error(label: QLabel, is_error: bool) -> None:
    label.setProperty("role", "error" if is_error else "")
    label.style().unpolish(label)  # re-apply the style sheet for the new property
    label.style().polish(label)
