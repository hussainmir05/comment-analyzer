"""Analyse page: paste a link, collect the post's comments, analyse them, see the results."""

import functools
from collections.abc import Callable
from contextlib import closing

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from comment_analyzer import APP_NAME, config, privacy
from comment_analyzer.analysis import claude_mode, pipeline, summarize
from comment_analyzer.analysis.claude_mode import MessagesApi, Outcome, Plan
from comment_analyzer.analysis.pipeline import Analyzer
from comment_analyzer.analysis.spam import spam_flags
from comment_analyzer.collection import CollectionSession, RunSummary
from comment_analyzer.db import Database, repository
from comment_analyzer.errors import UserError
from comment_analyzer.models import CollectionRun, Platform, Post, RunStatus, Snapshot
from comment_analyzer.platforms import youtube
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo, LinkStatus, detect
from comment_analyzer.ui.comments_table import CommentsView
from comment_analyzer.ui.theme import muted, set_error, title
from comment_analyzer.workers import CancelToken, ProgressFn, TaskRunner, Worker

CollectorFactory = Callable[[LinkInfo], Collector]
ClaudeFactory = Callable[[str], MessagesApi]  # API key -> client

NO_KEY = "Save your YouTube API key in Settings first."
COUNT_NOTE = (
    "Counts are YouTube's own. Its comment count includes comments it doesn't hand out (for "
    "example, ones held for review), so coverage can stay under 100%."
)
NOT_ANALYSED = 'Not analysed yet. Click "Analyse comments".'
NO_CLAUDE_KEY = (
    "Claude mode is optional and uses Anthropic's paid API. To use it, save an Anthropic API key "
    "in Settings, under Claude."
)


class AnalysePage(QWidget):
    run_finished = Signal(object)  # RunSummary
    analysis_finished = Signal(bool)  # True if it completed
    post_shown = Signal(int)  # posts.id of the post now shown
    tracking_changed = Signal()  # a post was ticked or unticked for tracking

    def __init__(
        self,
        db: Database,
        tasks: TaskRunner,
        store: QSettings,
        *,
        collector_factory: CollectorFactory | None = None,
        browser_factory: CollectorFactory | None = None,
        analyzer: Analyzer | None = None,
        claude_factory: ClaudeFactory | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.claude_factory = claude_factory or claude_mode.make_client
        self.db = db
        self.tasks = tasks
        self.store = store
        self.browser_factory = browser_factory
        self.collector_factory = collector_factory or self._collector_for
        self.analyzer = analyzer
        self.current_post: int | None = None
        self._session: CollectionSession | None = None
        self._analysis: Worker | None = None
        self._download_declined = False

        self.link = QLineEdit()
        self.link.setPlaceholderText("https://...")
        self.link.setClearButtonEnabled(True)
        self.paste_btn = QPushButton("Paste")
        self.sort = QComboBox()
        for value, label in youtube.SORTS.items():
            self.sort.addItem(label, value)
        self.analyse_btn = QPushButton("Analyse")
        self.analyse_btn.setEnabled(False)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.detected = muted()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.quota = muted()

        self.saved_posts = QListWidget()
        self.summary = QLabel("Pick a post to see what was collected.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.analysis = QLabel()
        self.analysis.setWordWrap(True)
        self.analysis.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.analyse_comments_btn = QPushButton("Analyse comments")
        self.analyse_comments_btn.setEnabled(False)
        self.claude_btn = QPushButton("Ask Claude...")
        self.claude_btn.setToolTip(
            "A second opinion from Claude (Anthropic's paid API). Shows the cost first."
        )
        self.claude_btn.setEnabled(False)
        self.track = QCheckBox("Track this post (checked on the interval set in Settings)")
        self.track.setEnabled(False)
        self.comments = CommentsView()

        row = QHBoxLayout()
        row.addWidget(self.link, stretch=1)
        for widget in (self.paste_btn, self.sort, self.analyse_btn, self.stop_btn):
            row.addWidget(widget)

        saved = QGroupBox("Saved posts")
        QVBoxLayout(saved).addWidget(self.saved_posts)
        results = QGroupBox("Comments")
        results_layout = QVBoxLayout(results)
        results_layout.addWidget(self.summary)
        analysis_row = QHBoxLayout()
        analysis_row.addWidget(self.analysis, stretch=1)
        analysis_buttons = QVBoxLayout()
        analysis_buttons.addWidget(self.analyse_comments_btn)
        analysis_buttons.addWidget(self.claude_btn)
        analysis_buttons.addStretch()
        analysis_row.addLayout(analysis_buttons)
        results_layout.addWidget(self.track)
        results_layout.addLayout(analysis_row)
        results_layout.addWidget(self.comments, stretch=1)
        results_layout.addWidget(muted(COUNT_NOTE))
        splitter = QSplitter()
        splitter.addWidget(saved)
        splitter.addWidget(results)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 780])

        layout = QVBoxLayout(self)
        layout.addWidget(title("Analyse a post"))
        layout.addWidget(muted("Paste a link to a YouTube, Facebook, Instagram or TikTok post."))
        layout.addLayout(row)
        layout.addWidget(self.detected)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.quota)
        layout.addWidget(splitter, stretch=1)

        self.link.textChanged.connect(self._on_link_changed)
        self.link.returnPressed.connect(self.start)
        self.paste_btn.clicked.connect(self.paste)
        self.analyse_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.analyse_comments_btn.clicked.connect(self._analyse_current)
        self.claude_btn.clicked.connect(self._ask_claude_current)
        self.track.toggled.connect(self._on_track_toggled)
        self.saved_posts.currentItemChanged.connect(self._on_post_selected)
        self.refresh_saved_posts()
        self.refresh_quota()

    # --- actions --------------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._session is not None or self._analysis is not None

    def set_link(self, text: str) -> None:
        self.link.setText(text.strip())

    def paste(self) -> None:
        self.set_link(QGuiApplication.clipboard().text())

    def start(self) -> None:
        info = detect(self.link.text())
        if not _collectable(info) or self.busy:
            return
        try:
            collector = self.collector_factory(info)
        except UserError as e:
            self._show_status(str(e), error=True)
            return
        settings = config.load_settings(self.store)
        browser = info.platform is not Platform.YOUTUBE
        session = CollectionSession(
            self.db,
            collector,
            info,
            limit=settings.comments_per_post,
            sort="relevance" if browser else self.sort.currentData(),  # sites show "most relevant"
            account_id=getattr(collector, "account_id", None),
        )
        session.progress.connect(self._on_progress)
        session.done.connect(self._on_collected)
        self._session = session
        self._set_running(True)
        self._show_status(f"Reading {info.message}...")
        session.start()

    def stop(self) -> None:
        if self._session is not None:
            self._session.stop()
        if self._analysis is not None:
            self._analysis.cancel()
        self.stop_btn.setEnabled(False)
        self._show_status("Stopping...")

    def analyse(self, post_row_id: int) -> None:
        """Analyse a post's comments, downloading the model first if needed (asks once)."""
        if self.analyzer is None or self.busy:
            return
        analyzer = self.analyzer
        if not analyzer.ready and not self._agree_to_download(analyzer):
            self._show_status(
                "Analysis skipped: the analysis models aren't downloaded. Click "
                '"Analyse comments" when you want them.'
            )
            return
        db = self.db

        def job(progress: ProgressFn, token: CancelToken) -> int:
            if not analyzer.ready:
                analyzer.download(progress, token)
            return pipeline.analyse_post(
                db,
                post_row_id,
                analyzer.classifier,
                progress,
                token,
                make_embedder=analyzer.embedder,
            )

        worker = Worker(job, name="Analysis")
        worker.signals.progress.connect(self._on_progress)
        worker.signals.succeeded.connect(functools.partial(self._on_analysed, post_row_id))
        worker.signals.failed.connect(lambda message: self._on_analysis_ended(message, True))
        worker.signals.cancelled.connect(
            lambda: self._on_analysis_ended("Analysis stopped.", False)
        )
        self._analysis = worker
        self._set_running(True)
        self._show_status("Analysing the comments...")
        self.tasks.start(worker)

    def ask_claude(self, post_row_id: int) -> None:
        """Count the tokens (free), show the estimated cost, and only then send the comments."""
        if self.busy:
            return
        key = config.get_secret(config.ANTHROPIC_API_KEY)
        if not key:
            self._show_status(NO_CLAUDE_KEY, error=True)
            return
        model = claude_mode.model_by_id(config.load_settings(self.store).claude_model)
        with closing(self.db.connect()) as conn:
            comments = [c for c in repository.list_comments(conn, post_row_id) if c.text]
        flags = spam_flags(comments)
        spam = {c.id for c in comments if c.id is not None and flags[c.platform_comment_id]}
        plan = claude_mode.make_plan(comments, spam, model)
        if not plan.requests:
            self._show_status("There's no comment text to send.")
            return
        api = self.claude_factory(key)

        def job(progress: ProgressFn, token: CancelToken) -> Plan:
            claude_mode.count_tokens(api, plan, token)
            return plan

        worker = Worker(job, name="Claude cost estimate")
        worker.signals.succeeded.connect(
            lambda counted: self._confirm_claude(post_row_id, api, counted)
        )
        worker.signals.failed.connect(lambda message: self._on_analysis_ended(message, True))
        worker.signals.cancelled.connect(
            lambda: self._on_analysis_ended("Stopped. Nothing was sent to Claude.", False)
        )
        self._analysis = worker
        self._set_running(True)
        self._show_status("Asking Anthropic how many tokens this is (free)...")
        self.tasks.start(worker)

    def _confirm_claude(self, post_row_id: int, api: MessagesApi, plan: Plan) -> None:
        self._analysis = None
        self._set_running(False)
        if not self._agree_to_cost(plan):
            self._show_status("Nothing was sent to Claude.")
            return
        db = self.db

        def job(progress: ProgressFn, token: CancelToken) -> Outcome:
            outcome = claude_mode.run(api, plan, progress, token)
            pipeline.store_claude(db, post_row_id, outcome)
            return outcome

        worker = Worker(job, name="Claude analysis")
        worker.signals.progress.connect(self._on_progress)
        worker.signals.succeeded.connect(functools.partial(self._on_claude_done, post_row_id))
        worker.signals.failed.connect(lambda message: self._on_analysis_ended(message, True))
        worker.signals.cancelled.connect(
            lambda: self._on_analysis_ended("Claude analysis stopped. Nothing was saved.", False)
        )
        self._analysis = worker
        self._set_running(True)
        self._show_status("Sending the comments to Claude...")
        self.tasks.start(worker)

    def _agree_to_cost(self, plan: Plan) -> bool:
        name = plan.model.name.split(" (")[0]
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"{name} will read {plan.comments:,} comments in {len(plan.requests)} requests "
            f"({plan.input_tokens:,} tokens sent).\n\n"
            f"Estimated cost: about ${plan.expected_cost():.2f}, and at most "
            f"${plan.max_cost():.2f}. Anthropic bills your account (prices as of "
            f"{claude_mode.PRICES_AS_OF}).\n\n"
            "Only the comment texts are sent, with usernames and links replaced.\n\n"
            "Send them to Claude?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_claude_done(self, post_row_id: int, outcome: Outcome) -> None:
        self._analysis = None
        self._set_running(False)
        text = f"Claude read {len(outcome.sentiments):,} comments for about ${outcome.cost:.2f}."
        if outcome.skipped:
            text += f" It gave no answer for {outcome.skipped:,}."
        self._show_status(text)
        self.show_post(post_row_id)
        self.analysis_finished.emit(True)

    def refresh_saved_posts(self, select: int | None = None) -> None:
        """Reload the list (a small indexed read; with WAL it never waits for a writer)."""
        with closing(self.db.connect()) as conn:
            posts = repository.list_posts(conn)
        self.saved_posts.blockSignals(True)
        self.saved_posts.clear()
        for post in posts:
            item = QListWidgetItem(f"{post.platform.label}: {post.title or post.post_id}")
            item.setToolTip(post.url)
            item.setData(Qt.ItemDataRole.UserRole, post.id)
            self.saved_posts.addItem(item)
            if post.id == select:
                self.saved_posts.setCurrentItem(item)
        self.saved_posts.blockSignals(False)
        if select is not None:
            self.show_post(select)

    def refresh_quota(self) -> None:
        with closing(self.db.connect()) as conn:
            used = repository.api_units_used(conn, "youtube", youtube.quota_day())
        self.quota.setText(
            f"YouTube quota used by this app today: {used:,} of {youtube.DAILY_QUOTA:,} units "
            "(it resets at midnight Pacific Time)."
        )

    def show_post(self, post_row_id: int) -> None:
        with closing(self.db.connect()) as conn:
            post = repository.get_post_by_id(conn, post_row_id)
            snapshot = repository.latest_snapshot(conn, post_row_id)
            run = repository.latest_run(conn, post_row_id)
            comments = repository.list_comments(conn, post_row_id)
            results = repository.analysis_for_post(conn, post_row_id)
            topics = repository.list_topics(conn, post_row_id)
            claude = repository.analysis_for_post(conn, post_row_id, claude_mode.MODE)
            claude_topics = repository.list_topics(conn, post_row_id, claude_mode.MODE)
        self.current_post = post_row_id
        self.summary.setText(describe(post, snapshot, run))
        analysis = summarize.build(comments, results, topics)
        lines = summarize.summary_lines(analysis) if analysis else []
        if not lines and comments:
            lines = [NOT_ANALYSED]
        lines += summarize.second_opinion(comments, results, claude, claude_topics)
        self.analysis.setText("\n".join(lines))
        self.comments.set_comments(comments, results, topics)
        self.analyse_comments_btn.setEnabled(
            bool(comments) and self.analyzer is not None and not self.busy
        )
        self.claude_btn.setEnabled(bool(comments) and not self.busy)
        self.track.blockSignals(True)
        self.track.setChecked(bool(post and post.tracked))
        self.track.setEnabled(post is not None)
        self.track.blockSignals(False)
        self.post_shown.emit(post_row_id)

    # --- signals --------------------------------------------------------------------------

    def _collector_for(self, link: LinkInfo) -> Collector:
        if link.platform is Platform.YOUTUBE:
            return self._youtube_collector(link)
        if self.browser_factory is None:
            raise UserError("Reading this site needs the app's own browser, which isn't ready.")
        return self.browser_factory(link)

    def _youtube_collector(self, link: LinkInfo) -> Collector:
        key = config.get_secret(config.YOUTUBE_API_KEY)
        if not key:
            raise UserError(NO_KEY)
        settings = config.load_settings(self.store)
        return youtube.YouTubeCollector(
            key,
            self.tasks,
            privacy.author_hasher(),
            dislike_estimate=settings.youtube_dislike_estimate,
        )

    def _agree_to_download(self, analyzer: Analyzer) -> bool:
        if self._download_declined:
            return False
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Analysis needs two models: one reads the mood of comments, one groups them by "
            "topic. Both run on this computer. What's missing is a one-time download of about "
            f"{analyzer.download_size // 2**20} MB from Hugging Face, kept in the app's data "
            "folder.\n\nDownload now?",
        )
        self._download_declined = answer != QMessageBox.StandardButton.Yes
        return not self._download_declined

    def _on_track_toggled(self, tracked: bool) -> None:
        if self.current_post is None:
            return
        with closing(self.db.connect()) as conn:
            repository.set_tracked(conn, self.current_post, tracked)
        self.tracking_changed.emit()

    def _ask_claude_current(self) -> None:
        if self.current_post is not None:
            self.ask_claude(self.current_post)

    def _analyse_current(self) -> None:
        if self.current_post is not None:
            self._download_declined = False  # an explicit click asks again
            self.analyse(self.current_post)

    def _on_link_changed(self, text: str) -> None:
        info = detect(text)
        note = ""
        if info.ok and info.platform not in (Platform.YOUTUBE, None):
            note = " It's read in the app's own browser, with one of your accounts."
        self.detected.setText(info.message + note)
        self.sort.setVisible(info.platform in (Platform.YOUTUBE, None))  # sites pick the order
        self.analyse_btn.setEnabled(_collectable(info) and not self.busy)

    def _on_post_selected(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.show_post(item.data(Qt.ItemDataRole.UserRole))

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)  # total 0 shows a busy bar
        self.progress.setValue(done)
        self.status.setText(message)

    def _on_collected(self, summary: RunSummary) -> None:
        self._session = None
        self._set_running(False)
        failed = summary.status is RunStatus.FAILED
        text = summary.message or f"Done: read {summary.collected:,} comments."
        self._show_status(text, error=failed)
        post_id = summary.post.id if summary.post else None
        self.refresh_saved_posts(select=post_id)
        self.refresh_quota()
        self.run_finished.emit(summary)
        if post_id is not None and not failed and summary.collected:
            self.analyse(post_id)

    def _on_analysed(self, post_row_id: int, judged: object) -> None:
        self._analysis = None
        self._set_running(False)
        self._show_status(f"Analysis done ({judged} comments read by the model).")
        self.show_post(post_row_id)
        self.analysis_finished.emit(True)

    def _on_analysis_ended(self, message: str, error: bool) -> None:
        self._analysis = None
        self._set_running(False)
        self._show_status(message, error=error)
        if self.current_post is not None:
            self.show_post(self.current_post)
        self.analysis_finished.emit(False)

    def _set_running(self, running: bool) -> None:
        if running:
            self.progress.setRange(0, 0)
        self.progress.setVisible(running)
        self.stop_btn.setEnabled(running)
        for widget in (self.paste_btn, self.sort):
            widget.setEnabled(not running)
        self.link.setReadOnly(running)
        self.analyse_btn.setEnabled(not running and _collectable(detect(self.link.text())))
        self.analyse_comments_btn.setEnabled(
            not running and self.current_post is not None and self.analyzer is not None
        )
        self.claude_btn.setEnabled(not running and self.current_post is not None)

    def _show_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        set_error(self.status, error)


def _collectable(info: LinkInfo) -> bool:
    if info.platform is Platform.YOUTUBE:
        return info.ok
    share = info.status is LinkStatus.NEEDS_BROWSER and info.canonical_url is not None
    return info.platform is not None and (info.ok or share)


def describe(post: Post | None, snapshot: Snapshot | None, run: CollectionRun | None) -> str:
    """What was collected for a post, in plain words: coverage, order and when."""
    if post is None:
        return ""
    heading = post.title or f"{post.platform.label} {post.post_id}"
    lines = [heading + (f"  (by {post.author})" if post.author else "")]
    if run is None:
        lines.append("Not collected yet.")
    else:
        when = (run.finished_at or run.started_at).astimezone().strftime("%Y-%m-%d %H:%M")
        order = youtube.SORTS.get(run.sort_used or "", "unknown order").lower()
        coverage = f"{run.comments_collected:,} comments"
        if run.comments_reported:
            share = min(100, round(100 * run.comments_collected / run.comments_reported))
            coverage += (
                f" of the {run.comments_reported:,} {post.platform.label} reports ({share}%)"
            )
        lines.append(f"Collected {coverage}, {order}, on {when}.")
        if run.note:
            lines.append(run.note)
    if snapshot is not None:
        numbers = [
            f"{label}: {value:,}"
            for label, value in (
                ("Views", snapshot.views),
                ("Likes", snapshot.likes),
                ("Dislikes (estimate)", snapshot.extra.get(youtube.DISLIKES_KEY)),
                ("Comments", snapshot.comments_reported),
            )
            if value is not None
        ]
        if numbers:
            lines.append(", ".join(numbers))
    return "\n".join(lines)
