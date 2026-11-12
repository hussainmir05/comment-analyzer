"""Start with Windows: a value under HKEY_CURRENT_USER\\...\\Run, which needs no admin rights."""

import contextlib
import logging
import sys

from comment_analyzer import APP_ID

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MINIMIZED = "--minimized"


def launch_command() -> str:
    """How Windows should start the app: the .exe when built, else Python with the module."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {MINIMIZED}'
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")  # no console window
    return f'"{pythonw}" -m comment_analyzer {MINIMIZED}'


def set_start_with_windows(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, launch_command())
        else:
            with contextlib.suppress(FileNotFoundError):  # it wasn't set
                winreg.DeleteValue(key, APP_ID)
    log.info("Start with Windows: %s", "on" if enabled else "off")
