"""Shared test setup. No test touches the network, the real data folder, Documents or
Credential Manager."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no windows pop up during tests

from collections.abc import Iterator
from pathlib import Path

import httpx2
import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from comment_analyzer import config


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.passwords.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    # the app pins Credential Manager at startup; in tests that would reach the real one
    monkeypatch.setattr(config, "use_system_keyring", lambda: None)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reports go to a throwaway Documents folder, never the real one."""
    folder = tmp_path / "Documents"
    monkeypatch.setattr("platformdirs.user_documents_dir", lambda: str(folder))
    return folder


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real HTTP fails loudly; tests talk to fakes through httpx2.MockTransport."""

    def refuse(self: object, request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError(f"Tests must not use the network ({request.url.host})")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway data folder, and a throwaway %APPDATA% for the old app's settings."""
    home = tmp_path / "home"
    monkeypatch.setenv(config.HOME_ENV, str(home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return home
