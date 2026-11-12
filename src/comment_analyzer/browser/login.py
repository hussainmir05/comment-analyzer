"""The login window: the site's own login page in the account's profile.

The user types their password into the site itself; the app never reads the page (no capture.js
here) and never stores passwords. Logged in or not comes from the site's login cookie.
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from comment_analyzer import APP_NAME
from comment_analyzer.browser.profiles import ProfileStore
from comment_analyzer.browser.sites import SITES
from comment_analyzer.models import Account, AccountStatus


class SitePage(QWebEnginePage):
    """A page that keeps pop-ups (such as "Continue with Google") in the same window and turns
    down camera, microphone, location and notification requests."""

    def __init__(self, profile: QWebEngineProfile, parent: QWidget | None = None) -> None:
        super().__init__(profile, parent)
        self.permissionRequested.connect(lambda permission: permission.deny())

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        return self


class LoginWindow(QWidget):
    def __init__(self, account: Account, profiles: ProfileStore, *, url: str | None = None) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        assert account.id is not None
        self.account = account
        site = SITES[account.platform]
        self.setWindowTitle(f"{APP_NAME}: {account.platform.label} ({account.label})")
        self.resize(1000, 760)

        self.view = QWebEngineView(self)
        # the page is the view's child, so closing the window deletes it before the profile
        self.page = SitePage(profiles.profile(account), self.view)
        self.view.setPage(self.page)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        back = QPushButton("Back")
        reload = QPushButton("Reload")
        self.status = QLabel()
        self.done = QPushButton("Done")
        top = QHBoxLayout()
        top.addWidget(back)
        top.addWidget(reload)
        top.addWidget(self.address, stretch=1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, stretch=1)
        bottom.addWidget(self.done)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view, stretch=1)
        layout.addLayout(bottom)

        back.clicked.connect(self.view.back)
        reload.clicked.connect(self.view.reload)
        self.done.clicked.connect(self.close)
        self.view.urlChanged.connect(lambda u: self.address.setText(u.toString()))
        profiles.status_changed.connect(self._status_changed)
        self._show_status(profiles.is_logged_in(account.id))
        self.view.load(QUrl(url or site.login_url))

    def _status_changed(self, account_id: int, status: str) -> None:
        if account_id == self.account.id:
            self._show_status(status == AccountStatus.LOGGED_IN)

    def _show_status(self, logged_in: bool) -> None:
        name = self.account.platform.label
        self.status.setText(
            f"Logged in to {name}. You can close this window."
            if logged_in
            else f"Log in to {name} here. Your password goes to {name} only; the app never sees it."
        )
