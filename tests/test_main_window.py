import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from comment_analyzer import config
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database
from comment_analyzer.platforms import youtube
from comment_analyzer.ui.icon import ICON_SIZES, write_ico
from comment_analyzer.ui.main_window import MainWindow
from comment_analyzer.workers import TaskRunner

KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"


@pytest.fixture
def window(qtbot) -> Iterator[MainWindow]:
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db, backups=paths.backups)
    db.migrate()
    tasks = TaskRunner()
    main_window = MainWindow(db=db, paths=paths, store=config.settings_store(paths), tasks=tasks)
    qtbot.addWidget(main_window)
    yield main_window
    tasks.cancel_all()
    tasks.wait(5000)


def test_detection_follows_the_link_box(window: MainWindow):
    page = window.analyse_page
    page.link.setText("https://youtu.be/dQw4w9WgXcQ")
    assert page.detected.text() == "YouTube video dQw4w9WgXcQ"
    assert page.analyse_btn.isEnabled()
    page.link.setText("https://x.com/someone/status/1")
    assert "X (Twitter) isn't supported" in page.detected.text()
    assert not page.analyse_btn.isEnabled()
    page.link.setText("https://vm.tiktok.com/ZMabc123/")
    assert "opens it in its own browser" in page.detected.text()
    assert page.analyse_btn.isEnabled()
    page.link.setText("https://www.tiktok.com/@someone")
    assert "isn't a single video" in page.detected.text()
    assert not page.analyse_btn.isEnabled()


def test_browser_links_need_a_logged_in_account(window: MainWindow):
    page = window.analyse_page
    page.link.setText("https://www.instagram.com/p/Chunk8-jurw/")
    page.analyse_btn.click()
    assert page.status.text().startswith("Log in to Instagram on the Accounts page first")


def test_external_link_lands_in_the_link_box(window: MainWindow):
    window.show_page("settings")
    window.handle_external_link("  https://youtu.be/dQw4w9WgXcQ  ")
    assert window.stack.currentWidget() is window.analyse_page
    assert window.analyse_page.link.text() == "https://youtu.be/dQw4w9WgXcQ"


def test_api_key_goes_to_the_credential_store_not_the_settings_file(
    qtbot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
):
    checked: list[str] = []
    monkeypatch.setattr(youtube, "check_key", checked.append)
    page = window.settings_page
    page.key_edit.setText(KEY)
    with qtbot.waitSignal(page.key_checked) as blocker:  # saving also asks YouTube
        page.save_key_btn.click()
    assert blocker.args == [True]
    assert checked == [KEY]
    assert config.get_secret(config.YOUTUBE_API_KEY) == KEY
    assert page.key_edit.text() == ""
    assert "YouTube accepted the key" in page.key_status.text()
    page.save_btn.click()
    assert "AIza" not in AppPaths.default().settings_file.read_text()
    page.remove_key_btn.click()
    assert config.get_secret(config.YOUTUBE_API_KEY) is None


def test_a_refused_key_is_explained(qtbot, window: MainWindow, monkeypatch: pytest.MonkeyPatch):
    def refuse(key: str) -> None:
        raise youtube.YouTubeError("Google refused this key for YouTube.")

    monkeypatch.setattr(youtube, "check_key", refuse)
    config.set_secret(config.YOUTUBE_API_KEY, KEY)
    page = window.settings_page
    page.refresh_key_status()  # a saved key enables the Test key button
    with qtbot.waitSignal(page.key_checked) as blocker:
        page.test_key_btn.click()
    assert blocker.args == [False]
    assert page.key_status.text() == "Google refused this key for YouTube."
    assert page.key_status.property("role") == "error"


def test_bad_api_key_is_refused(window: MainWindow):
    page = window.settings_page
    page.key_edit.setText("not a key")
    page.save_key_btn.click()
    assert config.get_secret(config.YOUTUBE_API_KEY) is None
    assert "doesn't look like an API key" in page.key_status.text()


def test_settings_page_saves_limits(window: MainWindow):
    page = window.settings_page
    page.comments_per_post.setValue(500)
    page.show_names.setChecked(True)
    page.save_btn.click()
    saved = config.load_settings(config.settings_store(AppPaths.default()))
    assert (saved.comments_per_post, saved.show_names) == (500, True)


def test_icon_file_has_every_size(qapp, tmp_path: Path):
    path = tmp_path / "icon.ico"
    write_ico(path)
    data = path.read_bytes()
    assert struct.unpack("<HHH", data[:6]) == (0, 1, len(ICON_SIZES))
    first_offset = struct.unpack("<I", data[18:22])[0]
    assert data[first_offset : first_offset + 4] == b"\x89PNG"
