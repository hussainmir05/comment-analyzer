"""Post maker: a card image and a caption for a post's findings.

The kit can be saved for posting by hand anywhere. With Meta set up in Settings, it can also be
published to the user's Facebook Page or its Instagram account through the Graph API. Every post
needs the user's click and confirmation, and the same text never goes out twice (CLAUDE.md).
"""

import re
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer import APP_NAME, config
from comment_analyzer.db import Database, repository
from comment_analyzer.publish.meta import MetaClient, MetaTarget
from comment_analyzer.reports import cards, exports
from comment_analyzer.reports.captions import caption
from comment_analyzer.reports.data import PostReport, load_post
from comment_analyzer.timeutil import utc_now
from comment_analyzer.ui.pages.settings import meta_target
from comment_analyzer.ui.theme import muted, set_error, title
from comment_analyzer.workers import CancelToken, ProgressFn, TaskRunner, Worker

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PostMakerPage(QWidget):
    saved = Signal(object)  # Path of the saved kit
    published = Signal(str, str)  # platform, remote ID

    def __init__(
        self,
        db: Database,
        store: QSettings,
        tasks: TaskRunner,
        *,
        meta_factory: Callable[[str], MetaClient] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.store = store
        self.tasks = tasks
        self.meta_factory = meta_factory or MetaClient
        self.report: PostReport | None = None
        self.variant = 0
        self.open_saved = True  # open the folder in Explorer after saving (off in tests)

        self.posts = QComboBox()
        self.preview = QLabel()
        self.preview.setFixedSize(360, 450)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption = QPlainTextEdit()
        self.other_btn = QPushButton("Other wording")
        self.copy_btn = QPushButton("Copy caption")
        self.save_btn = QPushButton("Save post kit")
        self.facebook_btn = QPushButton("Publish to Facebook Page...")
        self.instagram_btn = QPushButton("Publish to Instagram...")
        self.status = muted()
        self.status.setWordWrap(True)
        buttons = QHBoxLayout()
        for button in (self.other_btn, self.copy_btn, self.save_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        publish_buttons = QHBoxLayout()
        publish_buttons.addWidget(self.facebook_btn)
        publish_buttons.addWidget(self.instagram_btn)
        publish_buttons.addStretch()
        right = QVBoxLayout()
        right.addWidget(QLabel("Caption (you can edit it):"))
        right.addWidget(self.caption, stretch=1)
        right.addLayout(buttons)
        right.addLayout(publish_buttons)
        right.addWidget(self.status)
        body = QHBoxLayout()
        body.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignTop)
        body.addLayout(right, stretch=1)
        layout = QVBoxLayout(self)
        layout.addWidget(title("Post maker"))
        layout.addWidget(
            muted(
                "A card and a caption from a post's analysis, without commenters' names. Use a "
                'different wording ("Other wording") for each account you post to. Save the kit '
                "to post it yourself, or publish to your Facebook Page or Instagram once Meta is "
                "set up in Settings."
            )
        )
        layout.addWidget(self.posts)
        layout.addLayout(body, stretch=1)

        self.posts.currentIndexChanged.connect(self._on_post_chosen)
        self.other_btn.clicked.connect(self.other_wording)
        self.copy_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.caption.toPlainText())
        )
        self.save_btn.clicked.connect(lambda: self.save())
        self.facebook_btn.clicked.connect(lambda: self.publish("facebook"))
        self.instagram_btn.clicked.connect(lambda: self.publish("instagram"))
        self.refresh_posts()

    def refresh_posts(self) -> None:
        with closing(self.db.connect()) as conn:
            posts = [
                p
                for p in repository.list_posts(conn, limit=200)
                if p.id is not None and repository.analysis_for_post(conn, p.id)
            ]
        current = self.report.post.id if self.report else None
        self.posts.blockSignals(True)
        self.posts.clear()
        self.posts.addItem("Pick an analysed post", None)
        for post in posts:
            self.posts.addItem(f"{post.platform.label}: {post.title or post.post_id}", post.id)
        self.posts.setCurrentIndex(max(self.posts.findData(current), 0))
        self.posts.blockSignals(False)
        self._set_enabled(self.report is not None)

    def show_post(self, post_row_id: int) -> None:
        self.report = load_post(self.db, post_row_id)
        self.variant = 0
        self._render()

    def other_wording(self) -> None:
        self.variant += 1
        self._render(card=False)

    def save(self, *, reveal: bool = True) -> Path | None:
        if self.report is None:
            return None
        name = _UNSAFE.sub("", self.report.title)[:40].strip(" .") or "post"
        stamp = f"{datetime.now():%Y-%m-%d %H%M}"
        folder = (
            exports.reports_folder()
            / f"{stamp} {self.report.post.platform.label} - {name} post kit"
        )
        cards.save_card(cards.render_card(self.report), folder)
        (folder / "caption.txt").write_text(self.caption.toPlainText() + "\n", encoding="utf-8")
        self._show(f"Saved to {folder}")
        if self.open_saved and reveal:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.saved.emit(folder)
        return folder

    def publish(self, platform: str) -> None:
        """platform: "facebook" (the Page) or "instagram" (its linked account)."""
        if self.report is None:
            return
        target = meta_target(self.store)
        token = config.get_secret(config.META_PAGE_TOKEN)
        if target is None or not token:
            self._show("Set up Meta in Settings first (it needs your own Meta app).", error=True)
            return
        if platform == "instagram" and not target.instagram_id:
            self._show("No Instagram account is linked to your Facebook Page.", error=True)
            return
        text = self.caption.toPlainText().strip()
        if not text:
            self._show("Write a caption first.", error=True)
            return
        with closing(self.db.connect()) as conn:
            already = repository.published_with_text(conn, text)
        if already:
            self._show(
                "This caption was already posted. Click Other wording or edit it: the same "
                "text never goes out twice.",
                error=True,
            )
            return
        where = (
            f'the Facebook Page "{target.page_name}"'
            if platform == "facebook"
            else f"Instagram as @{target.instagram_name or target.instagram_id}"
        )
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Post this card and caption publicly to {where}?\n\n{text[:400]}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._show("Nothing was posted.")
            return
        folder = self.save(reveal=False)
        assert folder is not None
        image = folder / "card.jpg"
        make_client = self.meta_factory
        post_row_id = self.report.post.id

        def job(progress: ProgressFn, cancel: CancelToken) -> str:
            client = make_client(token)
            try:
                if platform == "facebook":
                    return client.publish_page_photo(target.page_id, image, text)
                return client.publish_instagram(target, image, text)
            finally:
                client.close()

        worker = Worker(job, name="Publishing")
        worker.signals.succeeded.connect(
            lambda remote_id: self._published(
                platform, target, text, image, post_row_id, str(remote_id)
            )
        )
        worker.signals.failed.connect(lambda message: self._show(message, error=True))
        worker.signals.finished.connect(lambda: self._set_enabled(self.report is not None))
        self._set_enabled(False)
        self._show(f"Posting to {where}...")
        self.tasks.start(worker)

    def _published(
        self,
        platform: str,
        target: MetaTarget,
        text: str,
        image: Path,
        post_row_id: int | None,
        remote_id: str,
    ) -> None:
        account = target.page_id if platform == "facebook" else target.instagram_id or ""
        with closing(self.db.connect()) as conn:
            repository.add_published(
                conn,
                post_row_id=post_row_id,
                platform=platform,
                target=account,
                text=text,
                media_path=str(image),
                remote_id=remote_id,
                when=utc_now(),
            )
        name = "your Facebook Page" if platform == "facebook" else "Instagram"
        self._show(f"Posted to {name}.")
        self.published.emit(platform, remote_id)

    def _show(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        set_error(self.status, error)

    def _render(self, card: bool = True) -> None:
        if self.report is None:
            self._set_enabled(False)
            return
        if card:
            image = cards.render_card(self.report)
            self.preview.setPixmap(
                QPixmap.fromImage(image).scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.caption.setPlainText(caption(self.report, self.variant))
        self._set_enabled(True)

    def _on_post_chosen(self, index: int) -> None:
        post_row_id = self.posts.itemData(index)
        if post_row_id is None:
            self.report = None
            self.preview.clear()
            self.caption.clear()
            self._set_enabled(False)
        else:
            self.show_post(post_row_id)

    def _set_enabled(self, enabled: bool) -> None:
        for button in (
            self.other_btn,
            self.copy_btn,
            self.save_btn,
            self.facebook_btn,
            self.instagram_btn,
        ):
            button.setEnabled(enabled)
