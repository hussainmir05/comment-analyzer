# PyInstaller spec for the one-folder Windows build. Run it through scripts\build_exe.ps1.
# One folder, not one file: a one-file app unpacks itself on every launch, which is slow
# (and slower still while antivirus scans the unpacked files).
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
NAME = "Comment Analyzer"
PLOTLY_JS = (
    Path(importlib.util.find_spec("plotly").submodule_search_locations[0])
    / "package_data"
    / "plotly.min.js"
)

a = Analysis(
    [str(ROOT / "src" / "comment_analyzer" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        *collect_data_files("comment_analyzer"),  # SQL migrations and other non-code files
        *collect_data_files("fast_langdetect"),  # its compact language-ID model
        *collect_data_files("docx"),  # the default Word template
        (str(PLOTLY_JS), "plotly/package_data"),  # charts load it locally; no Python plotly
    ],
    hiddenimports=["keyring.backends.Windows"],
    excludes=["tkinter"],
)



def _needed(entry):
    """Leave out Chromium's developer tools (only used for debugging web pages; 83 MB) and the
    browser's UI translations other than English (the app's UI is English)."""
    name = entry[0].replace("\\", "/")
    if "qtwebengine_devtools_resources" in name:
        return False
    return not ("qtwebengine_locales/" in name and not name.endswith("/en-US.pak"))


a.datas = [entry for entry in a.datas if _needed(entry)]
a.binaries = [entry for entry in a.binaries if _needed(entry)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(ROOT / "build" / "icon.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
