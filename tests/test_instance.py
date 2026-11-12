from pathlib import Path

from comment_analyzer.instance import SingleInstance


def _folder(tmp_path: Path, name: str = "home") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    return folder


def test_second_copy_hands_its_link_to_the_first(qtbot, tmp_path: Path):
    folder = _folder(tmp_path)
    first = SingleInstance(folder)
    assert first.acquire()
    second = SingleInstance(folder)
    assert not second.acquire()
    with qtbot.waitSignal(first.message_received, timeout=5000) as blocker:
        assert second.send("https://youtu.be/dQw4w9WgXcQ")
    assert blocker.args == ["https://youtu.be/dQw4w9WgXcQ"]
    with qtbot.waitSignal(first.message_received, timeout=5000) as blocker:
        assert second.send("")  # no link: just bring the window forward
    assert blocker.args == [""]
    first.release()
    third = SingleInstance(folder)
    assert third.acquire()
    third.release()


def test_separate_data_folders_run_side_by_side(qapp, tmp_path: Path):
    a = SingleInstance(_folder(tmp_path, "a"))
    b = SingleInstance(_folder(tmp_path, "b"))
    assert a.acquire()
    assert b.acquire()
    a.release()
    b.release()
