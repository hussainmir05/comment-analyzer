import json
from pathlib import Path

import pytest
from keyring.errors import PasswordSetError
from PySide6.QtCore import QSettings

from comment_analyzer import config
from comment_analyzer.config import AppPaths, LegacyImport, Settings
from comment_analyzer.errors import UserError

KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"


def test_data_folder_follows_the_override(app_home: Path):
    assert AppPaths.default().root == app_home


def test_default_data_folder_is_not_nested(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(config.HOME_ENV)
    root = AppPaths.default().root
    assert root.name == "CommentAnalyzer"
    assert root.parent.name != "CommentAnalyzer"


def test_settings_round_trip(qapp, tmp_path: Path):
    path = tmp_path / "settings.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    assert config.load_settings(store) == Settings()
    changed = Settings(comments_per_post=500, posts_per_day=3, show_names=True, purge_text_days=7)
    config.save_settings(store, changed)
    reread = config.load_settings(QSettings(str(path), QSettings.Format.IniFormat))
    assert reread == changed
    assert "[collection]" in path.read_text()


def test_nonsense_settings_are_clamped(qapp, tmp_path: Path):
    path = tmp_path / "settings.ini"
    path.write_text("[collection]\ncomments_per_post=5\nposts_per_day_per_account=100000\n")
    settings = config.load_settings(QSettings(str(path), QSettings.Format.IniFormat))
    assert settings.comments_per_post == config.RANGES["comments_per_post"][0]
    assert settings.posts_per_day == config.RANGES["posts_per_day"][1]


def test_secret_round_trip():
    assert config.get_secret(config.YOUTUBE_API_KEY) is None
    config.set_secret(config.YOUTUBE_API_KEY, KEY)
    assert config.get_secret(config.YOUTUBE_API_KEY) == KEY
    config.delete_secret(config.YOUTUBE_API_KEY)
    assert config.get_secret(config.YOUTUBE_API_KEY) is None
    config.delete_secret(config.YOUTUBE_API_KEY)  # deleting nothing is fine


def test_credential_store_failure_is_a_plain_message(
    monkeypatch: pytest.MonkeyPatch, memory_keyring
):
    def refuse(*args: object) -> None:
        raise PasswordSetError("denied")

    monkeypatch.setattr(memory_keyring, "set_password", refuse)
    with pytest.raises(UserError, match="Credential Manager"):
        config.set_secret(config.YOUTUBE_API_KEY, KEY)


def _legacy_file(content: object) -> Path:
    path = config.legacy_settings_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(content) if not isinstance(content, str) else content)
    return path


def test_legacy_key_moves_to_the_credential_store():
    path = _legacy_file({"limit": "2,000", "firefox": False, "youtube_api_key": KEY})
    assert config.import_legacy_api_key() is LegacyImport.IMPORTED
    assert config.get_secret(config.YOUTUBE_API_KEY) == KEY
    left = json.loads(path.read_text())
    assert left == {"limit": "2,000", "firefox": False, "youtube_api_key": ""}


def test_legacy_import_keeps_an_existing_key():
    path = _legacy_file({"youtube_api_key": "AIzaOLD" + "x" * 32})
    config.set_secret(config.YOUTUBE_API_KEY, KEY)
    assert config.import_legacy_api_key() is LegacyImport.KEPT_EXISTING
    assert config.get_secret(config.YOUTUBE_API_KEY) == KEY
    assert json.loads(path.read_text())["youtube_api_key"] == ""


@pytest.mark.parametrize("content", [None, "{not json", {"youtube_api_key": "  "}, ["a list"]])
def test_legacy_import_without_a_usable_key(content: object):
    if content is not None:
        _legacy_file(content)
    assert config.import_legacy_api_key() is LegacyImport.NOTHING
    assert config.get_secret(config.YOUTUBE_API_KEY) is None
