from collections.abc import Callable, Iterator

import pytest
from fakes import FakeAnalyzer, FakeCollector, sample_comments
from PySide6.QtWidgets import QMessageBox

from comment_analyzer import config
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database
from comment_analyzer.models import RunStatus
from comment_analyzer.platforms.base import Collector
from comment_analyzer.platforms.detect import LinkInfo
from comment_analyzer.ui.comments_table import TEXT_COLUMN
from comment_analyzer.ui.pages.analyse import NO_KEY, AnalysePage
from comment_analyzer.workers import TaskRunner

LINK = "https://youtu.be/dQw4w9WgXcQ"
PageFactory = Callable[..., AnalysePage]


@pytest.fixture
def make_page(qtbot) -> Iterator[PageFactory]:
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db)
    db.migrate()
    tasks = TaskRunner()

    def make(
        collector: Collector | None = None, analyzer: FakeAnalyzer | None = None
    ) -> AnalysePage:
        factory = (lambda link: collector) if collector else None
        page = AnalysePage(
            db, tasks, config.settings_store(paths), collector_factory=factory, analyzer=analyzer
        )
        qtbot.addWidget(page)
        return page

    yield make
    tasks.cancel_all()
    tasks.wait(5000)


def test_every_platform_can_be_analysed(make_page: PageFactory):
    page = make_page()
    page.link.setText(LINK)
    assert page.analyse_btn.isEnabled() and page.sort.isVisibleTo(page)
    page.link.setText("https://www.instagram.com/p/Chunk8-jurw/")
    assert "read in the app's own browser" in page.detected.text()
    assert page.analyse_btn.isEnabled()
    assert not page.sort.isVisibleTo(page)  # the site picks the order


def test_without_a_key_the_page_says_where_to_add_one(make_page: PageFactory):
    page = make_page()  # the real YouTube collector, and no key saved
    page.link.setText(LINK)
    page.analyse_btn.click()
    assert page.status.text() == NO_KEY
    assert page.analyse_btn.isEnabled()


def test_a_run_fills_the_list_summary_and_table(qtbot, make_page: PageFactory):
    collector = FakeCollector(comments=sample_comments(4), reported=10)
    page = make_page(collector)
    page.link.setText(LINK)
    with qtbot.waitSignal(page.run_finished, timeout=5000):
        page.analyse_btn.click()
    assert page.status.text() == "Done: read 4 comments."
    assert page.saved_posts.count() == 1
    assert "A video" in page.saved_posts.item(0).text()
    summary = page.summary.text()
    assert "Collected 4 comments of the 10 YouTube reports (40%), top comments first" in summary
    assert page.comments.model.rowCount() == 4
    assert page.comments.count.text() == "4 comments"
    assert collector.started_with is not None
    assert collector.started_with[0].post_id == "dQw4w9WgXcQ"


def test_the_order_and_limit_reach_the_collector(qtbot, make_page: PageFactory):
    store = config.settings_store(AppPaths.default())
    settings = config.load_settings(store)
    settings.comments_per_post = 300
    config.save_settings(store, settings)
    collector = FakeCollector()
    page = make_page(collector)
    page.sort.setCurrentIndex(page.sort.findData("time"))
    page.link.setText(LINK)
    with qtbot.waitSignal(page.run_finished, timeout=5000):
        page.analyse_btn.click()
    assert collector.started_with is not None
    link, limit, sort = collector.started_with
    assert isinstance(link, LinkInfo)
    assert (limit, sort) == (300, "time")


def test_stop(qtbot, make_page: PageFactory):
    page = make_page(FakeCollector(wait_for_stop=True))
    page.link.setText(LINK)
    page.analyse_btn.click()
    assert page.stop_btn.isEnabled() and not page.analyse_btn.isEnabled()
    with qtbot.waitSignal(page.run_finished, timeout=5000) as blocker:
        page.stop_btn.click()
    assert blocker.args[0].status is RunStatus.STOPPED
    assert page.analyse_btn.isEnabled() and not page.stop_btn.isEnabled()


def test_failures_are_shown_as_errors(qtbot, make_page: PageFactory):
    page = make_page(FakeCollector(fail_before_post="YouTube couldn't find this video."))
    page.link.setText(LINK)
    with qtbot.waitSignal(page.run_finished, timeout=5000):
        page.analyse_btn.click()
    assert page.status.text() == "YouTube couldn't find this video."
    assert page.status.property("role") == "error"


def test_collected_comments_are_analysed_and_summarised(qtbot, make_page: PageFactory):
    analyzer = FakeAnalyzer()
    page = make_page(FakeCollector(comments=sample_comments(3)), analyzer)
    page.link.setText(LINK)
    with qtbot.waitSignal(page.analysis_finished, timeout=10_000) as blocker:
        page.analyse_btn.click()
    assert blocker.args == [True]
    text = page.analysis.text()
    assert "Mood: mixed. 33% positive, 33% neutral, 33% negative (3 comments)." in text
    assert text.splitlines()[-1].endswith("Comments are not a poll.")
    shown = {
        page.comments.proxy.index(row, TEXT_COLUMN).data(): page.comments.proxy.index(row, 4).data()
        for row in range(3)
    }
    assert shown == {
        "comment number 0": "neutral",
        "comment number 1": "positive",
        "comment number 2": "negative",
    }
    page.comments.sentiment.setCurrentIndex(page.comments.sentiment.findData("negative"))
    assert page.comments.proxy.rowCount() == 1
    assert analyzer.downloads == 0


def test_the_model_download_is_asked_for_first(
    qtbot, make_page: PageFactory, monkeypatch: pytest.MonkeyPatch
):
    asked: list[str] = []

    def answer(parent: object, title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    analyzer = FakeAnalyzer(ready=False)
    page = make_page(FakeCollector(), analyzer)
    page.link.setText(LINK)
    with qtbot.waitSignal(page.analysis_finished, timeout=10_000):
        page.analyse_btn.click()
    assert "about 300 MB" in asked[0]
    assert analyzer.downloads == 1
    assert page.analysis.text().startswith("Mood:")


def test_declining_the_download_skips_analysis(
    qtbot, make_page: PageFactory, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    analyzer = FakeAnalyzer(ready=False)
    page = make_page(FakeCollector(), analyzer)
    page.link.setText(LINK)
    with qtbot.waitSignal(page.run_finished, timeout=5000):
        page.analyse_btn.click()
    assert "Analysis skipped" in page.status.text()
    assert analyzer.downloads == 0
    assert page.analysis.text() == 'Not analysed yet. Click "Analyse comments".'
    assert page.analyse_comments_btn.isEnabled()


def test_filtering_the_table(qtbot, make_page: PageFactory):
    page = make_page(FakeCollector(comments=sample_comments(12)))
    page.link.setText(LINK)
    with qtbot.waitSignal(page.run_finished, timeout=5000):
        page.analyse_btn.click()
    page.comments.filter.setText("number 1")  # matches 1, 10 and 11
    assert page.comments.proxy.rowCount() == 3
    assert page.comments.count.text() == "3 of 12 comments"
