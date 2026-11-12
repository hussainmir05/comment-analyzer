"""The command line, against the fake YouTube API (no real requests)."""

import functools
from pathlib import Path

import pytest
from fakes import KEY, VIDEO_ID, FakeYouTube

from comment_analyzer import app, cli, config
from comment_analyzer.platforms import youtube


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeYouTube:
    fake = FakeYouTube()
    collector = functools.partial(youtube.YouTubeCollector, transport=fake.transport())
    monkeypatch.setattr(cli.youtube, "YouTubeCollector", collector)
    return fake


def test_analyse_collects_and_writes_a_report(
    qapp, fake: FakeYouTube, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    config.set_secret(config.YOUTUBE_API_KEY, KEY)
    code = app.main(
        ["analyse", f"https://youtu.be/{VIDEO_ID}", "--limit", "5", "--out", str(tmp_path / "out")]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Read 5 of about 5 comments" in out
    [folder] = (tmp_path / "out").iterdir()
    assert {"Report.docx", "Comments.xlsx", "Comments.csv"} <= {p.name for p in folder.iterdir()}
    assert f"Report: {folder}" in out


def test_the_sites_and_a_missing_key_are_explained(
    qapp, fake: FakeYouTube, capsys: pytest.CaptureFixture[str]
):
    assert app.main(["analyse", "https://www.instagram.com/p/Chunk8-jurw/"]) == 2
    assert "read in the app's own browser" in capsys.readouterr().err
    assert app.main(["analyse", f"https://youtu.be/{VIDEO_ID}"]) == 2
    assert "No YouTube API key" in capsys.readouterr().err
    assert fake.requests == []
