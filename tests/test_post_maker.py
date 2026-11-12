from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from test_reports import HOSTILE_TITLE, make_post

from comment_analyzer import config
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database
from comment_analyzer.reports import cards, exports
from comment_analyzer.reports.captions import VARIANTS, caption
from comment_analyzer.reports.data import load_post
from comment_analyzer.ui.pages.post_maker import PostMakerPage
from comment_analyzer.workers import TaskRunner


@pytest.fixture
def db() -> Database:
    paths = AppPaths.default()
    paths.create()
    database = Database(paths.db)
    database.migrate()
    return database


def test_card_is_portrait_jpeg_and_png(qapp, db: Database, tmp_path: Path):
    report = load_post(db, make_post(db))
    assert report is not None
    image = cards.render_card(report)
    assert (image.width(), image.height()) == (1080, 1350)
    colours = {
        image.pixelColor(x, y).name() for x in range(0, 1080, 40) for y in range(0, 1350, 40)
    }
    assert QColor(cards.MOOD_COLORS["mostly positive"]).name() in colours or len(colours) > 3
    jpeg, png = cards.save_card(image, tmp_path)
    assert jpeg.read_bytes()[:3] == b"\xff\xd8\xff"  # Instagram's API takes JPEG only
    assert QImage(str(png)).size() == image.size()


def test_captions_vary_and_stay_honest(db: Database):
    report = load_post(db, make_post(db))
    assert report is not None
    texts = [caption(report, v) for v in range(VARIANTS)]
    assert len(set(texts)) == VARIANTS  # a different wording for each account
    for text in texts:
        assert text.endswith("Comments are not a poll.")
        assert "secret-hash" not in text
    assert caption(report, VARIANTS) == texts[0]


def test_post_maker_page(qtbot, db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    post_id = make_post(db)
    page = PostMakerPage(db, config.settings_store(AppPaths.default()), TaskRunner())
    qtbot.addWidget(page)
    page.open_saved = False
    assert page.posts.count() == 2  # "Pick an analysed post" + 1
    assert not page.save_btn.isEnabled()
    page.posts.setCurrentIndex(page.posts.findData(post_id))
    first = page.caption.toPlainText()
    assert HOSTILE_TITLE in first
    page.other_btn.click()
    assert page.caption.toPlainText() != first

    monkeypatch.setattr(exports, "reports_folder", lambda: tmp_path)
    with qtbot.waitSignal(page.saved) as blocker:
        page.save_btn.click()
    folder = blocker.args[0]
    assert {p.name for p in folder.iterdir()} == {"card.jpg", "card.png", "caption.txt"}
    assert folder.name.endswith("post kit")
