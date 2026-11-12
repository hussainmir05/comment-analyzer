"""Settings page: YouTube and Anthropic keys (in Windows Credential Manager), limits, privacy,
tracking, data folder."""

import re
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer import config
from comment_analyzer.analysis import claude_mode
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.db.connection import transaction
from comment_analyzer.errors import UserError
from comment_analyzer.platforms import youtube
from comment_analyzer.publish.meta import MetaClient, MetaTarget
from comment_analyzer.tracking import startup
from comment_analyzer.ui.theme import muted, set_error, title
from comment_analyzer.workers import CancelToken, ProgressFn, TaskRunner, Worker

_KEY_SHAPE = re.compile(r"[A-Za-z0-9_-]{20,100}")
_ANTHROPIC_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,200}")

KeyChecker = Callable[[str], None]


class SettingsPage(QWidget):
    saved = Signal()
    key_checked = Signal(bool)  # True if YouTube accepted the saved key

    def __init__(
        self,
        paths: AppPaths,
        store: QSettings,
        db: Database,
        tasks: TaskRunner,
        *,
        key_checker: KeyChecker | None = None,
        claude_key_checker: KeyChecker | None = None,
        meta_checker: Callable[[str], MetaTarget] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = paths
        self.store = store
        self.db = db
        self.tasks = tasks
        self._key_checker = key_checker
        self._claude_key_checker = claude_key_checker or claude_mode.check_key
        self._meta_checker = meta_checker or _check_meta_token
        self.apply_autostart = True  # write the Windows Run key on save (off in tests)

        # YouTube
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste a YouTube Data API v3 key")
        self.show_key = QCheckBox("Show")
        self.save_key_btn = QPushButton("Save key")
        self.test_key_btn = QPushButton("Test key")
        self.remove_key_btn = QPushButton("Remove key")
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, stretch=1)
        for widget in (self.show_key, self.save_key_btn, self.test_key_btn, self.remove_key_btn):
            key_row.addWidget(widget)
        self.dislike_estimate = QCheckBox(
            "Show a dislike estimate from the Return YouTube Dislike project (sends the video's "
            "ID to that project; YouTube itself hides dislikes)"
        )
        youtube_box = QGroupBox("YouTube")
        youtube_layout = QVBoxLayout(youtube_box)
        youtube_layout.addLayout(key_row)
        youtube_layout.addWidget(self.key_status)
        youtube_layout.addWidget(self.dislike_estimate)
        youtube_layout.addWidget(
            muted(
                "Get a free key in Google Cloud Console: create a project, enable YouTube Data "
                "API v3, then create an API key. The key is kept in Windows Credential Manager, "
                "never in a file."
            )
        )

        # Claude (optional, paid)
        self.claude_key_edit = QLineEdit()
        self.claude_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.claude_key_edit.setPlaceholderText("Paste an Anthropic API key (sk-ant-...)")
        self.save_claude_key_btn = QPushButton("Save key")
        self.remove_claude_key_btn = QPushButton("Remove key")
        self.claude_key_status = QLabel()
        self.claude_key_status.setWordWrap(True)
        self.claude_model = QComboBox()
        for model in claude_mode.MODELS:
            self.claude_model.addItem(
                f"{model.name}: ${model.input_price:g} / ${model.output_price:g} per million "
                "tokens in / out",
                model.id,
            )
        claude_row = QHBoxLayout()
        claude_row.addWidget(self.claude_key_edit, stretch=1)
        claude_row.addWidget(self.save_claude_key_btn)
        claude_row.addWidget(self.remove_claude_key_btn)
        claude_box = QGroupBox("Claude (optional, paid)")
        claude_form = QFormLayout(claude_box)
        claude_form.addRow(claude_row)
        claude_form.addRow(self.claude_key_status)
        claude_form.addRow("Model:", self.claude_model)
        claude_form.addRow(
            muted(
                "Claude can read a post's comments as a second opinion, with written topic "
                "summaries. It uses Anthropic's paid API on your account: the Analyse page shows "
                "the estimated cost and asks before anything is sent. Only comment texts are "
                "sent, with usernames and links replaced. Get a key at console.anthropic.com."
            )
        )

        # Meta (publishing)
        self.meta_token_edit = QLineEdit()
        self.meta_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.meta_token_edit.setPlaceholderText("Paste a Facebook Page access token")
        self.save_meta_btn = QPushButton("Save token")
        self.remove_meta_btn = QPushButton("Remove")
        self.meta_status = QLabel()
        self.meta_status.setWordWrap(True)
        meta_row = QHBoxLayout()
        meta_row.addWidget(self.meta_token_edit, stretch=1)
        meta_row.addWidget(self.save_meta_btn)
        meta_row.addWidget(self.remove_meta_btn)
        meta_box = QGroupBox("Meta: publishing to your Facebook Page and Instagram (optional)")
        meta_layout = QVBoxLayout(meta_box)
        meta_layout.addLayout(meta_row)
        meta_layout.addWidget(self.meta_status)
        meta_layout.addWidget(
            muted(
                "Needs your own Meta developer app and a Page access token with "
                "pages_manage_posts, pages_read_engagement, instagram_basic and "
                "instagram_content_publish (Graph API Explorer can make one). The token is kept "
                "in Windows Credential Manager. The Post maker posts only when you click Publish "
                "and confirm."
            )
        )

        # Collection
        self.comments_per_post = _spin_box("comments_per_post", step=100)
        self.posts_per_day = _spin_box("posts_per_day")
        collection = QGroupBox("Collection limits")
        collection_form = QFormLayout(collection)
        collection_form.addRow("Comments to read per post:", self.comments_per_post)
        collection_form.addRow("Posts per account per day:", self.posts_per_day)
        collection_form.addRow(
            muted(
                "Facebook, Instagram and TikTok are read at human pace, with a random "
                "2-5 second pause between steps."
            )
        )

        # Privacy
        self.show_names = QCheckBox(
            "Show commenter names in tables (kept in memory only, never saved)"
        )
        self.debug_capture = QCheckBox(
            "Save what the in-app browser collects, for troubleshooting (in the data folder's "
            "debug folder)"
        )
        self.purge_days = _spin_box("purge_text_days")
        self.purge_days.setSuffix(" days")
        privacy = QGroupBox("Privacy")
        privacy_form = QFormLayout(privacy)
        privacy_form.addRow(self.show_names)
        privacy_form.addRow("Delete comment text after:", self.purge_days)
        privacy_form.addRow(
            muted("Counts and analysis results are kept after the text is deleted.")
        )
        privacy_form.addRow(self.debug_capture)

        # Tracking
        self.track_every = _spin_box("track_every_minutes", step=15)
        self.track_every.setSuffix(" minutes")
        self.keep_in_tray = QCheckBox(
            "Keep running in the tray when the window is closed (tracking continues)"
        )
        self.start_with_windows = QCheckBox("Start with Windows (in the tray)")
        tracking = QGroupBox("Tracking")
        tracking_form = QFormLayout(tracking)
        tracking_form.addRow("Check tracked posts every:", self.track_every)
        tracking_form.addRow(self.keep_in_tray)
        tracking_form.addRow(self.start_with_windows)
        tracking_form.addRow(
            muted(
                "Each YouTube check costs 1 quota unit, plus 1 per 100 new comments. Facebook, "
                "Instagram and TikTok posts are checked at most every 6 hours, up to 300 "
                "comments, in a visible window, within each account's daily limit."
            )
        )

        # Data
        folder = QLabel(str(paths.root))
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_data = QPushButton("Open data folder")
        open_logs = QPushButton("Open log folder")
        folder_buttons = QHBoxLayout()
        folder_buttons.addWidget(open_data)
        folder_buttons.addWidget(open_logs)
        folder_buttons.addStretch()
        data = QGroupBox("Data")
        data_layout = QVBoxLayout(data)
        data_layout.addWidget(folder)
        data_layout.addLayout(folder_buttons)

        self.save_btn = QPushButton("Save settings")
        self.save_status = muted()
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_btn)
        save_row.addWidget(self.save_status, stretch=1)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(title("Settings"))
        for group in (youtube_box, claude_box, meta_box, collection, privacy, tracking, data):
            layout.addWidget(group)
        layout.addLayout(save_row)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        QVBoxLayout(self).addWidget(scroll)

        self.show_key.toggled.connect(self._toggle_key_visibility)
        self.save_key_btn.clicked.connect(self.save_key)
        self.test_key_btn.clicked.connect(self.test_key)
        self.remove_key_btn.clicked.connect(self.remove_key)
        self.save_claude_key_btn.clicked.connect(self.save_claude_key)
        self.remove_claude_key_btn.clicked.connect(self.remove_claude_key)
        self.save_meta_btn.clicked.connect(self.save_meta_token)
        self.remove_meta_btn.clicked.connect(self.remove_meta_token)
        self.save_btn.clicked.connect(self.save)
        open_data.clicked.connect(lambda: _open_folder(paths.root))
        open_logs.clicked.connect(lambda: _open_folder(paths.logs))
        self.load()
        self.refresh_key_status()
        self.refresh_claude_key_status()
        self.refresh_meta_status()

    def load(self) -> None:
        settings = config.load_settings(self.store)
        self.comments_per_post.setValue(settings.comments_per_post)
        self.dislike_estimate.setChecked(settings.youtube_dislike_estimate)
        self.posts_per_day.setValue(settings.posts_per_day)
        self.show_names.setChecked(settings.show_names)
        self.debug_capture.setChecked(settings.debug_capture)
        self.purge_days.setValue(settings.purge_text_days)
        self.track_every.setValue(settings.track_every_minutes)
        self.keep_in_tray.setChecked(settings.keep_in_tray)
        self.start_with_windows.setChecked(settings.start_with_windows)
        index = self.claude_model.findData(claude_mode.model_by_id(settings.claude_model).id)
        self.claude_model.setCurrentIndex(index)

    def save(self) -> None:
        settings = config.load_settings(self.store)  # keeps values this page doesn't show
        settings.comments_per_post = self.comments_per_post.value()
        settings.youtube_dislike_estimate = self.dislike_estimate.isChecked()
        settings.posts_per_day = self.posts_per_day.value()
        settings.show_names = self.show_names.isChecked()
        settings.debug_capture = self.debug_capture.isChecked()
        settings.purge_text_days = self.purge_days.value()
        settings.track_every_minutes = self.track_every.value()
        settings.keep_in_tray = self.keep_in_tray.isChecked()
        autostart_changed = settings.start_with_windows != self.start_with_windows.isChecked()
        settings.start_with_windows = self.start_with_windows.isChecked()
        settings.claude_model = self.claude_model.currentData()
        try:
            config.save_settings(self.store, settings)
            if autostart_changed and self.apply_autostart:
                startup.set_start_with_windows(settings.start_with_windows)
        except (UserError, OSError) as e:
            self.save_status.setText(str(e))
            set_error(self.save_status, True)
            return
        self.save_status.setText("Settings saved.")
        set_error(self.save_status, False)
        self.saved.emit()

    def save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not _KEY_SHAPE.fullmatch(key):
            self._key_message(
                "That doesn't look like an API key. Copy the whole key from Google Cloud Console.",
                error=True,
            )
            return
        try:
            config.set_secret(config.YOUTUBE_API_KEY, key)
        except UserError as e:
            self._key_message(str(e), error=True)
            return
        self.key_edit.clear()
        self.refresh_key_status()
        self.test_key()

    def test_key(self) -> None:
        """Ask YouTube whether it accepts the saved key (1 quota unit)."""
        key = config.get_secret(config.YOUTUBE_API_KEY)
        if not key:
            self.refresh_key_status()
            return
        check = self._key_checker or youtube.check_key
        db = self.db

        def job(progress: ProgressFn, token: CancelToken) -> None:
            check(key)
            with closing(db.connect()) as conn, transaction(conn):
                repository.add_api_units(conn, "youtube", youtube.quota_day(), 1)

        worker = Worker(job, name="YouTube key check")
        worker.signals.succeeded.connect(lambda _: self._key_check_done(None))
        worker.signals.failed.connect(self._key_check_done)
        self.test_key_btn.setEnabled(False)
        self._key_message("Checking the key with YouTube...")
        self.tasks.start(worker)

    def _key_check_done(self, error: str | None) -> None:
        self.test_key_btn.setEnabled(True)
        if error:
            self._key_message(error, error=True)
        else:
            self._key_message("YouTube accepted the key. It's saved in Windows Credential Manager.")
        self.key_checked.emit(error is None)

    def remove_key(self) -> None:
        config.delete_secret(config.YOUTUBE_API_KEY)
        self.refresh_key_status()

    def refresh_key_status(self) -> None:
        key = config.get_secret(config.YOUTUBE_API_KEY)
        if key:
            self._key_message(
                f"A key ending in ...{key[-4:]} is saved in Windows Credential Manager."
            )
        else:
            self._key_message("No key saved yet. Analysing YouTube videos needs one.")
        self.remove_key_btn.setEnabled(bool(key))
        self.test_key_btn.setEnabled(bool(key))

    def save_claude_key(self) -> None:
        key = self.claude_key_edit.text().strip()
        if not _ANTHROPIC_KEY_SHAPE.fullmatch(key):
            self._claude_message(
                "That doesn't look like an Anthropic API key. It starts with sk-ant-",
                error=True,
            )
            return
        check = self._claude_key_checker

        def job(progress: ProgressFn, token: CancelToken) -> None:
            check(key)  # free: it only counts tokens
            config.set_secret(config.ANTHROPIC_API_KEY, key)

        worker = Worker(job, name="Anthropic key check")
        worker.signals.succeeded.connect(lambda _: self._claude_key_saved(None))
        worker.signals.failed.connect(self._claude_key_saved)
        self.save_claude_key_btn.setEnabled(False)
        self._claude_message("Checking the key with Anthropic (free)...")
        self.tasks.start(worker)

    def _claude_key_saved(self, error: str | None) -> None:
        self.save_claude_key_btn.setEnabled(True)
        if error:
            self._claude_message(error, error=True)
            return
        self.claude_key_edit.clear()
        self.refresh_claude_key_status()

    def remove_claude_key(self) -> None:
        config.delete_secret(config.ANTHROPIC_API_KEY)
        self.refresh_claude_key_status()

    def refresh_claude_key_status(self) -> None:
        key = config.get_secret(config.ANTHROPIC_API_KEY)
        if key:
            self._claude_message(
                f"Anthropic accepted the key ending in ...{key[-4:]}. It's saved in Windows "
                "Credential Manager."
            )
        else:
            self._claude_message("No key saved. Claude mode is off.")
        self.remove_claude_key_btn.setEnabled(bool(key))

    def save_meta_token(self) -> None:
        token = self.meta_token_edit.text().strip()
        if len(token) < 40 or " " in token:
            self._meta_message("That doesn't look like a Page access token.", error=True)
            return
        check = self._meta_checker

        def job(progress: ProgressFn, cancel: CancelToken) -> MetaTarget:
            target = check(token)  # one read-only call: which Page is this?
            config.set_secret(config.META_PAGE_TOKEN, token)
            return target

        worker = Worker(job, name="Meta token check")
        worker.signals.succeeded.connect(self._meta_saved)
        worker.signals.failed.connect(lambda message: self._meta_message(message, error=True))
        worker.signals.finished.connect(lambda: self.save_meta_btn.setEnabled(True))
        self.save_meta_btn.setEnabled(False)
        self._meta_message("Asking Facebook which Page this token is for...")
        self.tasks.start(worker)

    def _meta_saved(self, target: MetaTarget) -> None:
        settings = config.load_settings(self.store)
        settings.meta_page = f"{target.page_id}|{target.page_name}"
        settings.meta_instagram = (
            f"{target.instagram_id}|{target.instagram_name or ''}" if target.instagram_id else ""
        )
        config.save_settings(self.store, settings)
        self.meta_token_edit.clear()
        self.refresh_meta_status()

    def remove_meta_token(self) -> None:
        config.delete_secret(config.META_PAGE_TOKEN)
        settings = config.load_settings(self.store)
        settings.meta_page = settings.meta_instagram = ""
        config.save_settings(self.store, settings)
        self.refresh_meta_status()

    def refresh_meta_status(self) -> None:
        target = meta_target(self.store)
        if target is None or not config.get_secret(config.META_PAGE_TOKEN):
            self._meta_message("Not set up. The Post maker saves kits for posting by hand.")
            self.remove_meta_btn.setEnabled(False)
            return
        text = f'Connected to the Facebook Page "{target.page_name}"'
        if target.instagram_id:
            text += f" and its Instagram account @{target.instagram_name or target.instagram_id}"
        self._meta_message(text + ".")
        self.remove_meta_btn.setEnabled(True)

    def _meta_message(self, text: str, *, error: bool = False) -> None:
        self.meta_status.setText(text)
        set_error(self.meta_status, error)

    def _claude_message(self, text: str, *, error: bool = False) -> None:
        self.claude_key_status.setText(text)
        set_error(self.claude_key_status, error)

    def _key_message(self, text: str, *, error: bool = False) -> None:
        self.key_status.setText(text)
        set_error(self.key_status, error)

    def _toggle_key_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.key_edit.setEchoMode(mode)


def meta_target(store: QSettings) -> MetaTarget | None:
    """The Page (and Instagram account) the saved Meta token posts to, from the settings."""
    settings = config.load_settings(store)
    if "|" not in settings.meta_page:
        return None
    page_id, page_name = settings.meta_page.split("|", 1)
    instagram_id, _, instagram_name = settings.meta_instagram.partition("|")
    return MetaTarget(page_id, page_name, instagram_id or None, instagram_name or None)


def _check_meta_token(token: str) -> MetaTarget:
    client = MetaClient(token)
    try:
        return client.check()
    finally:
        client.close()


def _spin_box(setting: str, step: int = 1) -> QSpinBox:
    low, high = config.RANGES[setting]
    box = QSpinBox()
    box.setRange(low, high)
    box.setSingleStep(step)
    box.setGroupSeparatorShown(True)
    return box


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
