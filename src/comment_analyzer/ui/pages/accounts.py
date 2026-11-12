"""Accounts page: Facebook, Instagram and TikTok accounts, each with its own browser profile.

Several accounts can be logged in at once, including two on the same site. Each has "Log out and
delete", which removes its cookies and its whole profile folder.
"""

from collections.abc import Callable
from contextlib import closing

from PySide6.QtCore import QSettings
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer import APP_NAME, config
from comment_analyzer.browser.login import LoginWindow
from comment_analyzer.browser.profiles import ProfileStore, new_profile_dir
from comment_analyzer.browser.sites import SITES
from comment_analyzer.db import Database, repository
from comment_analyzer.models import Account, AccountStatus, Platform
from comment_analyzer.timeutil import utc_now
from comment_analyzer.ui.theme import muted, set_error, title

HEADERS = ("Site", "Name", "Status", "Last used")
STATUS_WORDS = {
    AccountStatus.NEW: "not logged in yet",
    AccountStatus.LOGGED_IN: "logged in",
    AccountStatus.LOGGED_OUT: "logged out",
    AccountStatus.NEEDS_ATTENTION: "needs you: open it and check",
}


class AccountsPage(QWidget):
    def __init__(
        self,
        db: Database,
        store: QSettings,
        profiles: ProfileStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.store = store
        self.profiles = profiles
        self.windows: dict[int, LoginWindow] = {}
        self._accounts: list[Account] = []
        self._checked = False

        self.site = QComboBox()
        for platform in SITES:
            self.site.addItem(platform.label, platform.value)
        self.name = QLineEdit()
        self.name.setPlaceholderText('A name you\'ll recognise, such as "Work page"')
        self.add_btn = QPushButton("Add and log in")
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.open_btn = QPushButton("Open browser")
        self.check_btn = QPushButton("Check login")
        self.delete_btn = QPushButton("Log out and delete")
        self.message = muted()
        self.message.setWordWrap(True)

        add_row = QHBoxLayout()
        add_row.addWidget(self.site)
        add_row.addWidget(self.name, stretch=1)
        add_row.addWidget(self.add_btn)
        buttons = QHBoxLayout()
        for button in (self.open_btn, self.check_btn, self.delete_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(title("Accounts"))
        layout.addWidget(
            muted(
                "Facebook, Instagram and TikTok are read through your own accounts, logged in "
                "inside the app's browser. Each account has its own browser profile, so several "
                "can be logged in at once. Passwords go to the site only: the app never sees or "
                "stores them."
            )
        )
        layout.addLayout(add_row)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(buttons)
        layout.addWidget(self.message)

        self.add_btn.clicked.connect(self.add_account)
        self.name.returnPressed.connect(self.add_account)
        self.open_btn.clicked.connect(lambda: self._with_selected(self.open_browser))
        self.check_btn.clicked.connect(lambda: self._with_selected(self.profiles.check))
        self.delete_btn.clicked.connect(lambda: self._with_selected(self.delete_account))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _: self._with_selected(self.open_browser))
        profiles.status_changed.connect(self._status_changed)
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:
        """The first time the page shows, read every account's login cookie."""
        super().showEvent(event)
        if not self._checked:
            self._checked = True
            for account in self._accounts:
                self.profiles.check(account)

    # --- actions --------------------------------------------------------------------------

    def refresh(self, select: int | None = None) -> None:
        with closing(self.db.connect()) as conn:
            self._accounts = repository.list_accounts(conn)
        self.table.setRowCount(len(self._accounts))
        for row, account in enumerate(self._accounts):
            used = account.last_used_at
            cells = (
                account.platform.label,
                account.label,
                STATUS_WORDS[account.status],
                used.astimezone().strftime("%Y-%m-%d %H:%M") if used else "never",
            )
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))
            if account.id == select:
                self.table.selectRow(row)
        self._update_buttons()

    def add_account(self) -> Account | None:
        platform = Platform(self.site.currentData())
        label = " ".join(self.name.text().split())
        if not label:
            self._show("Give the account a name first.", error=True)
            return None
        if not self._accept_terms(platform):
            self._show(f"No {platform.label} account was added.")
            return None
        account = Account(platform, label[:60], new_profile_dir(platform), utc_now())
        with closing(self.db.connect()) as conn:
            account.id = repository.add_account(conn, account)
        self.name.clear()
        self.refresh(select=account.id)
        self._show(f"Log in to {platform.label} in the window that opened.")
        self.open_browser(account)
        return account

    def open_browser(self, account: Account) -> None:
        assert account.id is not None
        window = self.windows.get(account.id)
        if window is None:
            window = LoginWindow(account, self.profiles)
            window.destroyed.connect(lambda _=None, i=account.id: self.windows.pop(i, None))
            self.windows[account.id] = window
        window.show()
        window.raise_()
        window.activateWindow()

    def delete_account(self, account: Account) -> None:
        assert account.id is not None
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f'Log out of {account.platform.label} as "{account.label}" and delete this '
            "account's browser data from this computer?\n\nCollected comments stay.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        window = self.windows.pop(account.id, None)
        if window is not None:
            window.close()
        complete = self.profiles.remove(account)
        with closing(self.db.connect()) as conn:
            repository.delete_account(conn, account.id)
        self.refresh()
        self._show(
            "Logged out and deleted."
            if complete
            else "Logged out and deleted. A few files still in use go when the app next starts."
        )

    # --- signals --------------------------------------------------------------------------

    def _status_changed(self, account_id: int, status: str) -> None:
        with closing(self.db.connect()) as conn:
            repository.set_account_status(conn, account_id, AccountStatus(status))
        self.refresh(select=self._selected_id())

    def _accept_terms(self, platform: Platform) -> bool:
        settings = config.load_settings(self.store)
        accepted = set(filter(None, settings.accepted_sites.split(",")))
        if platform.value in accepted:
            return True
        answer = QMessageBox.question(
            self, APP_NAME, SITES[platform].terms_note + "\n\nAdd the account?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        settings.accepted_sites = ",".join(sorted(accepted | {platform.value}))
        config.save_settings(self.store, settings)
        return True

    def _selected_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self._accounts[rows[0].row()].id if rows else None

    def _with_selected(self, action: Callable[[Account], None]) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            action(self._accounts[rows[0].row()])

    def _update_buttons(self) -> None:
        selected = bool(self.table.selectionModel().selectedRows())
        for button in (self.open_btn, self.check_btn, self.delete_btn):
            button.setEnabled(selected)

    def _show(self, text: str, *, error: bool = False) -> None:
        self.message.setText(text)
        set_error(self.message, error)
