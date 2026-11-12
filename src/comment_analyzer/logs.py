"""Logging to a rotating file in the data folder, with secrets masked, plus crash capture."""

import faulthandler
import logging
import logging.handlers
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import TextIO

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(message)s"

# Secrets that can turn up in URLs, headers or error messages, whatever their exact value.
_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "AIza***"),  # Google API keys
    (re.compile(r"sk-ant-[0-9A-Za-z_\-]+"), "sk-ant-***"),  # Anthropic API keys
    (re.compile(r"\bEAA[0-9A-Za-z]{20,}"), "EAA***"),  # Meta access tokens
    (
        re.compile(
            r"(?i)\b((?:api_?)?key|access_token|token|password|secret|sessionid|c_user|"
            r"csrftoken|mstoken)=([^&\s\"']+)"
        ),
        r"\1=***",
    ),
    (
        re.compile(
            r"(?i)\b(authorization|cookie|set-cookie|x-goog-api-key|x-api-key)(\s*[:=]\s*)\S.*"
        ),
        r"\1\2***",
    ),
]

_secrets: set[str] = set()
_secrets_lock = threading.Lock()
_crash_file: TextIO | None = None
_crash_callback: Callable[[BaseException], None] | None = None


def register_secret(value: str | None) -> None:
    """Mask this exact value (e.g. the saved YouTube key) in every log line from now on."""
    if value and len(value) >= 6:
        with _secrets_lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _secrets_lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, "***")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Masks secrets in the whole formatted record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(
    logs_dir: Path, *, level: int = logging.INFO, console: bool | None = None
) -> Path:
    """Log to logs_dir/app.log (rotated at 2 MB, 5 old files kept). Returns the log file path.

    console defaults to True when running from source with a terminal.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "app.log"
    root = logging.getLogger()
    for handler in list(root.handlers):  # calling again replaces our handlers
        if getattr(handler, "comment_analyzer", False):
            root.removeHandler(handler)
            handler.close()

    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
    ]
    if console is None:
        console = sys.stderr is not None and not getattr(sys, "frozen", False)
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(RedactingFormatter(LOG_FORMAT))
        handler.comment_analyzer = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx2", "anthropic"):  # otherwise a line per request
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    return path


def install_crash_handlers(logs_dir: Path) -> None:
    """Log every uncaught exception, in any thread, and native crashes (faulthandler)."""
    global _crash_file
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    if _crash_file is None:
        # faulthandler writes straight to a file descriptor, so the file must stay open
        _crash_file = open(logs_dir / "crash.log", "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_crash_file)


def on_crash(callback: Callable[[BaseException], None] | None) -> None:
    """Call this (e.g. to show a dialog) after an uncaught exception has been logged."""
    global _crash_callback
    _crash_callback = callback


def _excepthook(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger("comment_analyzer.crash").critical(
        "Unhandled error", exc_info=(exc_type, exc, tb)
    )
    if _crash_callback is not None:
        _crash_callback(exc)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    name = args.thread.name if args.thread else "?"
    exc_info = (args.exc_type, args.exc_value, args.exc_traceback)
    logging.getLogger("comment_analyzer.crash").critical(
        "Unhandled error in thread %s",
        name,
        exc_info=exc_info,  # type: ignore[arg-type]
    )
