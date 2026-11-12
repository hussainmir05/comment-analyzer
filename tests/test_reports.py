import csv
import json
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook

from comment_analyzer.analysis.pipeline import analyse_post
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.models import CollectionRun, Comment, Platform, Post, RunStatus, Snapshot
from comment_analyzer.reports import exports
from comment_analyzer.reports.data import build_timeline, growth, load_post
from comment_analyzer.ui import dashboard_html
from comment_analyzer.ui.dashboard_html import Theme
from comment_analyzer.ui.pages.dashboard import DashboardPage
from comment_analyzer.workers import CancelToken, TaskRunner

START = datetime(2026, 10, 1, 12, tzinfo=UTC)
HOSTILE_TITLE = "Best </script><script>alert(1)</script> video"


def classify(texts: list[str]) -> list[tuple[str, float]]:
    return [
        ("negative", 0.9) if "slow" in t else ("positive", 0.9) if "love" in t else ("neutral", 0.6)
        for t in texts
    ]


def make_post(db: Database, video: str = "vid00000001", title: str = HOSTILE_TITLE) -> int:
    texts = [
        "love the editing",
        "the intro is slow and the audio is slow",
        "slow audio again",
        "first",
        "I love this channel",
    ]
    with closing(db.connect()) as conn:
        post_id = repository.upsert_post(
            conn,
            Post(Platform.YOUTUBE, video, f"https://youtu.be/{video}", title=title,
                 author="Test Channel", published_at=START),
        )  # fmt: skip
        repository.add_snapshot(
            conn, post_id, Snapshot(START, views=1000, likes=50, comments_reported=8)
        )
        run_id = repository.start_run(conn, CollectionRun(post_id, START, sort_used="relevance"))
        repository.finish_run(
            conn, run_id, RunStatus.COMPLETED, comments_collected=5, comments_reported=8
        )
        repository.upsert_comments(
            conn,
            post_id,
            [
                Comment(f"{video}-c{i}", text, created_at=START + timedelta(days=i), like_count=i,
                        author_hash=f"secret-hash-{i}", collected_at=START + timedelta(days=9))
                for i, text in enumerate(texts)
            ],
        )  # fmt: skip
    analyse_post(db, post_id, lambda: classify, lambda *a: None, CancelToken())
    return post_id


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data.db")
    database.migrate()
    return database


def test_timeline_picks_a_unit_and_fills_gaps():
    def at(**delta: float) -> Comment:
        return Comment("x", "t", created_at=START + timedelta(**delta))

    daily = build_timeline([at(days=0), at(days=3), at(days=3, hours=1)], lambda c: "positive")
    assert daily is not None and daily.unit == "day"
    assert len(daily.starts) == 4  # days 0..3, the empty ones included
    assert daily.counts["positive"] == [1, 0, 0, 2]
    hourly = build_timeline([at(hours=0), at(hours=5)], lambda c: "neutral")
    assert hourly is not None and hourly.unit == "hour" and len(hourly.starts) == 6
    monthly = build_timeline([at(days=0), at(days=400)], lambda c: "neutral")
    assert monthly is not None and monthly.unit == "month"
    assert build_timeline([at(days=0)], lambda c: "neutral") is None


def test_report_data(db: Database):
    report = load_post(db, make_post(db))
    assert report is not None
    assert report.coverage().startswith("Collected 5 comments of the 8 YouTube reports (62%)")
    assert report.analysis is not None
    assert report.analysis.issues[0].term in ("slow", "audio", "slow audio")
    assert report.footer().endswith("Comments are not a poll.")
    assert [n for _, n in growth(report)] == [1, 2, 3, 4, 5]
    assert growth(report)[1][0] == pytest.approx(24.0)  # hours since posting


def test_exports_have_everything_but_names(db: Database, tmp_path: Path):
    report = load_post(db, make_post(db))
    assert report is not None
    folder = exports.export_post(report, base=tmp_path)
    assert folder.parent == tmp_path and "YouTube - Best" in folder.name
    names = {p.name for p in folder.iterdir()}
    assert {
        "Report.docx",
        "Comments.xlsx",
        "Comments.csv",
        "sentiment.png",
        "timeline.png",
    } <= names

    rows = list(csv.reader((folder / "Comments.csv").open(encoding="utf-8-sig")))
    assert rows[0] == list(exports.COMMENT_COLUMNS)
    assert len(rows) == 6

    book = load_workbook(folder / "Comments.xlsx")
    assert book.sheetnames == ["Summary", "Comments", "Issues", "Topics"]
    summary = {row[0]: row[1] for row in book["Summary"].iter_rows(values_only=True)}
    assert summary["Views"] == 1000
    assert str(summary["Note"]).endswith("Comments are not a poll.")

    with zipfile.ZipFile(folder / "Report.docx") as docx:
        document = docx.read("word/document.xml").decode("utf-8")
    assert "Comments are not a poll." in document
    assert "How these numbers were made" in document

    for path in folder.iterdir():  # commenter IDs (even hashed) never leave the app
        if path.suffix in (".csv", ".xlsx", ".docx"):
            data = path.read_bytes()
            if path.suffix != ".csv":
                with zipfile.ZipFile(path) as archive:
                    data = b"".join(archive.read(n) for n in archive.namelist())
            assert b"secret-hash" not in data


def test_dashboard_page_html(db: Database):
    report = load_post(db, make_post(db))
    assert report is not None
    page = dashboard_html.post_page(report, Theme(), "file:///C:/plotly.min.js")
    assert '<script src="file:///C:/plotly.min.js">' in page
    assert "&lt;/script&gt;&lt;script&gt;alert(1)" in page  # the title is escaped in the HTML
    assert "alert(1)</script>" not in page.split("const figures")[1]  # and inside the JSON
    for chart in ("sentiment", "timeline", "issues", "languages"):
        assert f'id="{chart}"' in page
    figures = json.loads(page.split("const figures = ")[1].split(";\nfor")[0].replace("<\\/", "</"))
    assert figures["sentiment"]["layout"]["barmode"] == "stack"
    assert "Comments are not a poll." in page


def test_compare_page(db: Database):
    reports = [
        load_post(db, make_post(db, v, f"Video {v}")) for v in ("vid00000001", "vid00000002")
    ]
    page = dashboard_html.compare_page([r for r in reports if r], Theme(), "")
    assert 'id="shares"' in page and 'id="growth"' in page
    assert "Video vid00000002" in page


def test_plotly_js_is_available():
    assert dashboard_html.plotly_js().is_file()


def test_dashboard_page_lists_renders_compares_and_exports(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db)
    db.migrate()
    first = make_post(db, "vid00000001", "First video")
    make_post(db, "vid00000002", "Second video")
    with closing(db.connect()) as conn:  # a post without comments isn't offered
        repository.upsert_post(conn, Post(Platform.YOUTUBE, "vid00000003", "https://youtu.be/x"))
    tasks = TaskRunner()
    page = DashboardPage(db, paths, tasks)
    qtbot.addWidget(page)
    page.open_exports = False
    assert page.posts.count() == 3  # "Pick a post" + 2
    assert page.compare_list.count() == 2

    page.select_post(first)
    assert "First video" in page.page_file("post").read_text(encoding="utf-8")
    assert page.export_btn.isEnabled()

    page.compare()
    assert "Tick between 2" in page.status.text()
    for row in range(2):
        page.compare_list.item(row).setCheckState(page.compare_list.item(row).checkState().Checked)
    page.compare()
    assert "Second video" in page.page_file("compare").read_text(encoding="utf-8")

    monkeypatch.setattr(exports, "reports_folder", lambda: tmp_path / "reports")
    with qtbot.waitSignal(page.exported, timeout=20_000) as blocker:
        page.export()
    folder = blocker.args[0]
    assert (folder / "Report.docx").is_file()
    assert "Saved to" in page.status.text()
    tasks.wait(5000)
