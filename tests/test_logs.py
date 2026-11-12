import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from comment_analyzer import logs


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.parametrize(
    ("line", "leak"),
    [
        (
            "GET https://www.googleapis.com/youtube/v3/videos?id=x&key=AIzaSyA1234567890abcdefghijklmnopqrstu",
            "AIzaSy",
        ),
        ("x-goog-api-key: AIzaSyA1234567890abcdefghijklmnopqrstu", "AIzaSy"),
        ("Authorization: Bearer abc.def.ghi", "abc.def"),
        ("Cookie: c_user=123; xs=abc", "c_user=123"),
        ("graph call failed: access_token=EAABwzLixnjYBO1234567890abcdef", "EAABwz"),
        ("anthropic key sk-ant-api03-abcdef123456", "api03"),
        ("password=hunter2&next=1", "hunter2"),
    ],
)
def test_known_secret_shapes_are_masked(line: str, leak: str):
    assert leak not in logs.redact(line)


def test_ordinary_text_is_left_alone():
    line = "pageToken=QURBQUFBQU and 412 comments read; Cookie consent dialog found"
    assert logs.redact(line) == line


def test_registered_secret_is_masked_whatever_its_shape():
    logs.register_secret("my-very-own-secret-value")
    assert logs.redact("value my-very-own-secret-value here") == "value *** here"


def test_log_file_is_masked_including_tracebacks(tmp_path: Path, restore_logging: None):
    path = logs.setup_logging(tmp_path, console=False)
    secret = "AIzaTESTSECRETabcdefghijklmnopqrstuvwxy"
    logs.register_secret(secret)
    try:
        raise ValueError(f"YouTube rejected key {secret}")
    except ValueError:
        logging.getLogger("test").exception("call failed with key=%s", secret)
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = path.read_text(encoding="utf-8")
    assert "ValueError" in content
    assert "AIzaTEST" not in content
