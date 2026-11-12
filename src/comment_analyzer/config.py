"""App folders, settings and secrets.

Settings live in settings.ini in the data folder (easy to inspect or reset, unlike the
registry). Secrets live only in Windows Credential Manager, through keyring.
"""

import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Self, cast

import keyring
import keyring.errors
import platformdirs
from PySide6.QtCore import QSettings

from comment_analyzer import APP_ID, APP_NAME, logs
from comment_analyzer.errors import UserError

log = logging.getLogger(__name__)

HOME_ENV = "COMMENT_ANALYZER_HOME"  # overrides the data folder (tests, smoke test)


# --- folders ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls) -> Self:
        override = os.environ.get(HOME_ENV)
        if override:
            return cls(Path(override))
        # appauthor=False: otherwise platformdirs nests the folder (CommentAnalyzer\CommentAnalyzer)
        return cls(Path(platformdirs.user_data_dir(APP_ID, appauthor=False, roaming=False)))

    @property
    def db(self) -> Path:
        return self.root / "data.db"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.ini"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def profiles(self) -> Path:
        return self.root / "profiles"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def debug(self) -> Path:
        """Debug captures (off by default; see Settings)."""
        return self.root / "debug"

    def create(self) -> None:
        for folder in (
            self.root,
            self.logs,
            self.backups,
            self.profiles,
            self.exports,
            self.models,
        ):
            folder.mkdir(parents=True, exist_ok=True)


# --- settings ---------------------------------------------------------------------------------


@dataclass(slots=True)
class Settings:
    comments_per_post: int = 2000
    youtube_dislike_estimate: bool = False  # ask Return YouTube Dislike (sends the video's ID)
    posts_per_day: int = 20  # per browser account
    show_names: bool = False  # names are only ever held in memory
    purge_text_days: int = 30
    track_every_minutes: int = 60
    keep_in_tray: bool = True  # closing the window keeps tracking running
    start_with_windows: bool = False
    claude_model: str = "claude-opus-5-5"  # Claude mode (optional, paid); see claude_mode.MODELS
    accepted_sites: str = ""  # browser sites whose terms notice was accepted, comma-separated
    debug_capture: bool = False  # save captured responses in debug/ for troubleshooting
    meta_page: str = ""  # "id|name" of the Facebook Page the Meta token is for
    meta_instagram: str = ""  # "id|username" of its linked Instagram account, if any
    legacy_import_offered: bool = False


_KEYS = {
    "comments_per_post": "collection/comments_per_post",
    "youtube_dislike_estimate": "youtube/dislike_estimate",
    "posts_per_day": "collection/posts_per_day_per_account",
    "show_names": "privacy/show_names",
    "purge_text_days": "privacy/purge_text_after_days",
    "track_every_minutes": "tracking/every_minutes",
    "keep_in_tray": "tracking/keep_in_tray",
    "start_with_windows": "tracking/start_with_windows",
    "claude_model": "claude/model",
    "accepted_sites": "browser/accepted_sites",
    "debug_capture": "browser/debug_capture",
    "meta_page": "meta/page",
    "meta_instagram": "meta/instagram",
    "legacy_import_offered": "app/legacy_import_offered",
}

RANGES = {
    "comments_per_post": (50, 1_000_000),
    "posts_per_day": (1, 500),
    "purge_text_days": (1, 3650),
    "track_every_minutes": (15, 24 * 60),
}


def settings_store(paths: AppPaths) -> QSettings:
    return QSettings(str(paths.settings_file), QSettings.Format.IniFormat)


def load_settings(store: QSettings) -> Settings:
    """Read settings; missing or nonsense values fall back to defaults or are clamped."""
    settings = Settings()
    for f in fields(Settings):
        default = getattr(settings, f.name)
        try:
            value = store.value(_KEYS[f.name], default, type=type(default))
        except (TypeError, ValueError):
            value = default
        if f.name in RANGES:
            low, high = RANGES[f.name]
            value = min(max(cast(int, value), low), high)  # type=int made it an int
        setattr(settings, f.name, value)
    return settings


def save_settings(store: QSettings, settings: Settings) -> None:
    for f in fields(Settings):
        store.setValue(_KEYS[f.name], getattr(settings, f.name))
    store.sync()
    if store.status() != QSettings.Status.NoError:
        raise UserError(f"Couldn't save the settings file {store.fileName()}.")


# --- secrets ----------------------------------------------------------------------------------

YOUTUBE_API_KEY = "youtube_api_key"
ANTHROPIC_API_KEY = "anthropic_api_key"
META_PAGE_TOKEN = "meta_page_token"


def _service(name: str) -> str:
    return (
        f"{APP_ID}:{name}"  # shows up in Credential Manager as e.g. CommentAnalyzer:youtube_api_key
    )


def use_system_keyring() -> None:
    """Pin Windows Credential Manager; keyring's backend discovery can fail in a frozen app."""
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        keyring.set_keyring(WinVaultKeyring())


def get_secret(name: str) -> str | None:
    try:
        value = keyring.get_password(_service(name), APP_ID)
    except keyring.errors.KeyringError:
        log.exception("Couldn't read %s from the credential store", name)
        return None
    logs.register_secret(value)
    return value


def set_secret(name: str, value: str) -> None:
    logs.register_secret(value)
    try:
        keyring.set_password(_service(name), APP_ID, value)
    except keyring.errors.KeyringError as e:
        log.exception("Couldn't save %s to the credential store", name)
        raise UserError("Windows Credential Manager didn't accept the key. Try again.") from e


def delete_secret(name: str) -> None:
    with contextlib.suppress(keyring.errors.PasswordDeleteError):  # nothing was stored
        keyring.delete_password(_service(name), APP_ID)


# --- the previous version's settings ----------------------------------------------------------


class LegacyImport(StrEnum):
    NOTHING = "nothing"  # no old settings file, or no key in it
    IMPORTED = "imported"
    KEPT_EXISTING = "kept_existing"  # a key was already saved; the old copy was just removed


def legacy_settings_file() -> Path:
    """Where the previous one-file app kept its settings, including the key in plain text."""
    return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME / "settings.json"


def find_legacy_api_key(path: Path | None = None) -> str | None:
    path = path or legacy_settings_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = data.get("youtube_api_key") if isinstance(data, dict) else None
    return key.strip() if isinstance(key, str) and key.strip() else None


def import_legacy_api_key(path: Path | None = None) -> LegacyImport:
    """Move the old app's plain-text YouTube key into Credential Manager and blank it there.

    The old file keeps its other settings.
    """
    path = path or legacy_settings_file()
    key = find_legacy_api_key(path)
    if key is None:
        return LegacyImport.NOTHING
    result = LegacyImport.KEPT_EXISTING
    if not get_secret(YOUTUBE_API_KEY):
        set_secret(YOUTUBE_API_KEY, key)
        result = LegacyImport.IMPORTED
    data = json.loads(path.read_text(encoding="utf-8"))
    data["youtube_api_key"] = ""
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("Moved the previous version's YouTube key out of %s (%s)", path, result)
    return result
