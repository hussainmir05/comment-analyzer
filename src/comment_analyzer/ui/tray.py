"""The tray icon: open the window, check tracked posts, quit, and spike alerts."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from comment_analyzer import APP_NAME
from comment_analyzer.ui.icon import app_icon
from comment_analyzer.ui.main_window import MainWindow


def create_tray(window: MainWindow) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(app_icon(), window)
    tray.setToolTip(APP_NAME)
    menu = QMenu(window)
    open_action = QAction("Open", menu)
    check_action = QAction("Check tracked posts now", menu)
    quit_action = QAction("Quit", menu)
    open_action.triggered.connect(window.bring_forward)
    check_action.triggered.connect(window.tracker.check_now)
    quit_action.triggered.connect(lambda: _quit(window))
    menu.addAction(open_action)
    menu.addAction(check_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            window.bring_forward() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
    )
    window.tracker.spike.connect(
        lambda _post, message: tray.showMessage(
            f"{APP_NAME}: spike on a tracked post", message, QSystemTrayIcon.MessageIcon.Warning
        )
    )
    window.tray = tray
    tray.show()
    return tray


def _quit(window: MainWindow) -> None:
    window.quitting = True
    window.close()
    QApplication.quit()
