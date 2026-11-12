import csv
import hashlib
import os
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from comment_analyzer.analysis import evaluate, model_store, summarize
from comment_analyzer.analysis.clean import (
    RIGHT_QUOTE,
    clean,
    fingerprint,
    strip_josa,
    words_of,
)
from comment_analyzer.analysis.issues import criticized_issues
from comment_analyzer.analysis.language import UNKNOWN, detect_language, language_name
from comment_analyzer.analysis.model_store import DownloadError, ModelFile, ModelSpec
from comment_analyzer.analysis.pipeline import LocalAnalyzer, analyse_post
from comment_analyzer.analysis.sentiment import classify_all, is_low_confidence, preprocess
from comment_analyzer.analysis.spam import spam_flags
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.models import AnalysisResult, Comment, Platform, Post
from comment_analyzer.workers import Cancelled, CancelToken

WHEN = datetime(2026, 11, 1, tzinfo=UTC)


def keyword_classifier(texts: list[str]) -> list[tuple[str, float]]:
    """A stand-in for the model: "love" is positive, "hate" negative, anything else neutral."""
    out = []
    for text in texts:
        lowered = text.lower()
        if "love" in lowered:
            out.append(("positive", 0.9))
        elif "hate" in lowered:
            out.append(("negative", 0.8))
        else:
            out.append(("neutral", 0.45))
    return out


# --- text -------------------------------------------------------------------------------------


def test_clean_decodes_entities_and_drops_control_characters():
    assert clean("it&#39;s &amp; done\x00\x07 ") == "it's & done"
    assert clean(None) == ""


def test_words_of_handles_links_mentions_and_korean_particles():
    text = f"@someone Check https://x.com the AUDIO quality, it{RIGHT_QUOTE}s bad"
    assert words_of(text) == [
        "check",
        "the",
        "audio",
        "quality",
        "it's",
        "bad",
    ]
    assert words_of("영상이 좋아요 영상을 봤어요") == ["영상", "좋아요", "영상", "봤어요"]
    assert strip_josa("아이") == "아이"  # too short to be word + particle


def test_fingerprint_ignores_spacing_and_punctuation():
    assert fingerprint("Check out MY channel!!!") == fingerprint("check out my channel")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("This is a perfectly ordinary English sentence.", "en"),
        ("정말 좋은 영상이네요 감사합니다", "ko"),
        ("😂😂😂", UNKNOWN),
        ("ok", UNKNOWN),
    ],
)
def test_detect_language(text: str, language: str):
    assert detect_language(text)[0] == language


def test_language_names():
    assert language_name("ko") == "Korean"
    assert language_name("xx") == "xx"


# --- sentiment helpers ------------------------------------------------------------------------


def test_preprocess_follows_the_model_card():
    assert preprocess("@bob look http://a.b now") == "@user look http now"


def test_classify_all_keeps_the_input_order_and_stops():
    texts = ["hate it", "a much longer comment that I love", "meh"]
    assert classify_all(keyword_classifier, texts, lambda *a: None, CancelToken()) == [
        ("negative", 0.8),
        ("positive", 0.9),
        ("neutral", 0.45),
    ]
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        classify_all(keyword_classifier, texts, lambda *a: None, token)


@pytest.mark.parametrize(
    ("language", "confidence", "unsure"),
    [("en", 0.9, False), ("ko", 0.9, True), (UNKNOWN, 0.9, False), ("en", 0.4, True)],
)
def test_low_confidence(language: str, confidence: float, unsure: bool):
    assert is_low_confidence(language, confidence) is unsure


# --- spam -------------------------------------------------------------------------------------


def test_copies_from_several_commenters_are_spam():
    pasted = "Amazing giveaway for every subscriber happening right now"
    comments = [
        Comment("a", pasted, author_hash="x"),
        Comment("b", pasted.upper() + "!!", author_hash="y"),
        Comment("c", "love this song", author_hash="z"),
        Comment("d", "love this song", author_hash="w"),  # short: a coincidence, not a copy
        Comment("e", "Subscribe to my channel www.example.com", author_hash="v"),
        Comment("f", "great explanation at www.example.com", author_hash="u"),
    ]
    assert spam_flags(comments) == {
        "a": True,
        "b": True,
        "c": False,
        "d": False,
        "e": True,
        "f": False,
    }


# --- issues -----------------------------------------------------------------------------------


def test_issues_count_comments_not_repeats_and_prefer_phrases():
    negative = [
        ("The audio quality is awful. Audio audio audio.", 10),
        ("audio quality ruined it", 3),
        ("Terrible audio quality and too many ads", 1),
        ("ads everywhere, ads", 0),
        ("one-off complaint about lighting", 0),
    ]
    issues = criticized_issues(negative)
    assert [(i.term, i.mentions) for i in issues] == [("audio quality", 3), ("ads", 2)]
    assert issues[0].example.startswith("The audio quality")  # the most liked


def test_issue_phrases_from_one_name_are_joined():
    negative = [("They forgot Dera Ismail Khan again", 2), ("Dera Ismail Khan deserves better", 1)]
    assert criticized_issues(negative)[0].term == "dera ismail khan"


def test_phrases_never_cross_punctuation():
    negative = [("too loud, ruins it", 0), ("too loud, ruins it", 0)]
    assert "loud ruins" not in [i.term for i in criticized_issues(negative)]


# --- summary ----------------------------------------------------------------------------------


def _analysed(
    sentiment: str, likes: int = 0, *, spam: bool = False, unsure: bool = False, n: int = 0
):
    comment = Comment(f"c{n}", f"text {n}", like_count=likes, lang="en", collected_at=WHEN, id=n)
    result = AnalysisResult(
        n, "local", "m", WHEN, sentiment=sentiment, confidence=0.9, low_confidence=unsure,
        spam_flag=spam,
    )  # fmt: skip
    return comment, result


def test_summary_shares_weighting_and_honesty_lines():
    rows = [
        _analysed("positive", 0, n=1),
        _analysed("positive", 0, n=2),
        _analysed("negative", 98, n=3),
        _analysed("neutral", 0, n=4, unsure=True),
        _analysed("positive", 0, n=5, spam=True),
    ]
    analysis = summarize.build([c for c, _ in rows], {r.comment_id: r for _, r in rows})
    assert analysis is not None
    assert (analysis.analysed, analysis.counted, analysis.spam, analysis.low_confidence) == (
        5,
        4,
        1,
        1,
    )
    assert analysis.shares == {"positive": 50.0, "neutral": 25.0, "negative": 25.0}
    assert analysis.weighted["negative"] == pytest.approx(100 * 99 / 102)
    lines = summarize.summary_lines(analysis)
    assert (
        lines[0] == "Mood: mostly positive. 50% positive, 25% neutral, 25% negative (4 comments)."
    )
    assert "The most-liked comment alone carries 97% of that weight." in lines[1]
    assert any("1 comment look like spam" in line for line in lines)
    assert lines[-1].endswith("Comments are not a poll.")
    assert lines[-1].startswith("Based on 4 public comments collected ")


def test_summary_needs_analysed_comments():
    assert summarize.build([Comment("a", "x", id=1)], {}) is None


@pytest.mark.parametrize(
    ("positive", "negative", "word"),
    [(60, 20, "mostly positive"), (20, 60, "mostly negative"), (40, 30, "mixed")],
)
def test_mood_words(positive: float, negative: float, word: str):
    assert summarize.mood({"positive": positive, "neutral": 0, "negative": negative}) == word


# --- pipeline ---------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data.db")
    database.migrate()
    return database


def _store(db: Database, comments: list[Comment]) -> int:
    with closing(db.connect()) as conn:
        post_id = repository.upsert_post(
            conn, Post(Platform.YOUTUBE, "vid00000001", "https://youtu.be/vid00000001")
        )
        repository.upsert_comments(conn, post_id, comments)
    return post_id


def test_pipeline_stores_results_and_only_judges_new_comments(db: Database):
    post_id = _store(
        db,
        [
            Comment("a", "I love this", collected_at=WHEN),
            Comment("b", "I hate the ending of this film", collected_at=WHEN),
            Comment("c", "정말 좋은 영상이네요 감사합니다", collected_at=WHEN),
            Comment("d", None, collected_at=WHEN),  # text already purged: skipped
        ],
    )
    calls: list[int] = []

    def make_classifier():
        def classify(texts: list[str]) -> list[tuple[str, float]]:
            calls.append(len(texts))
            return keyword_classifier(texts)

        return classify

    assert analyse_post(db, post_id, make_classifier, lambda *a: None, CancelToken()) == 3
    with closing(db.connect()) as conn:
        results = repository.analysis_for_post(conn, post_id)
        comments = {c.platform_comment_id: c for c in repository.list_comments(conn, post_id)}
    by_id = {c.platform_comment_id: results.get(c.id or 0) for c in comments.values()}
    assert by_id["a"] is not None and by_id["a"].sentiment == "positive"
    assert by_id["b"] is not None and by_id["b"].sentiment == "negative"
    assert by_id["c"] is not None and by_id["c"].low_confidence  # Korean: not trained on it
    assert by_id["d"] is None
    assert comments["c"].lang == "ko"

    _store(db, [Comment("e", "love it", collected_at=WHEN)])
    assert analyse_post(db, post_id, make_classifier, lambda *a: None, CancelToken()) == 1
    assert calls == [3, 1]


# --- model downloads --------------------------------------------------------------------------


def _spec(content: bytes) -> ModelSpec:
    return ModelSpec(
        name="tiny",
        repo="someone/tiny",
        revision="0123456789abcdef",
        files=(ModelFile("onnx/model.onnx", len(content), hashlib.sha256(content).hexdigest()),),
        credit="test",
        title="the test model",
    )


def test_download_checks_the_file_and_reports_progress(tmp_path: Path):
    content = b"model bytes" * 1000
    requests: list[str] = []

    def serve(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(200, content=content)

    spec = _spec(content)
    seen: list[str] = []
    folder = model_store.download(
        spec,
        tmp_path,
        lambda d, t, m: seen.append(m),
        CancelToken(),
        transport=httpx2.MockTransport(serve),
    )
    assert (folder / "onnx" / "model.onnx").read_bytes() == content
    assert spec.installed(tmp_path)
    assert requests == [
        "https://huggingface.co/someone/tiny/resolve/0123456789abcdef/onnx/model.onnx"
    ]
    assert seen
    # already there: nothing is fetched again
    model_store.download(
        spec, tmp_path, lambda *a: None, CancelToken(), transport=httpx2.MockTransport(serve)
    )
    assert len(requests) == 1


def test_a_damaged_download_is_thrown_away(tmp_path: Path):
    spec = _spec(b"the real model")
    tampered = httpx2.MockTransport(lambda r: httpx2.Response(200, content=b"something else"))
    with pytest.raises(DownloadError, match="damaged"):
        model_store.download(spec, tmp_path, lambda *a: None, CancelToken(), transport=tampered)
    assert not spec.installed(tmp_path)
    assert not list(tmp_path.rglob("*.part"))


def test_stopping_a_download_leaves_nothing_behind(tmp_path: Path):
    token = CancelToken()
    token.cancel()
    serve = httpx2.MockTransport(lambda r: httpx2.Response(200, content=b"x" * 10))
    with pytest.raises(Cancelled):
        model_store.download(_spec(b"x" * 10), tmp_path, lambda *a: None, token, transport=serve)
    assert not list(tmp_path.rglob("*.part"))


# --- evaluation -------------------------------------------------------------------------------


def test_metrics():
    pairs = [("positive", "positive"), ("negative", "positive"), ("neutral", "neutral")]
    assert evaluate.accuracy(pairs) == pytest.approx(2 / 3)
    # positive F1 = 2/3, neutral F1 = 1, negative F1 = 0 -> mean 5/9
    assert evaluate.macro_f1(pairs) == pytest.approx(5 / 9)
    bands = evaluate.calibration([("positive", "positive", 0.95), ("negative", "positive", 0.6)])
    assert ("0.9-1.0", 1, 1.0) in bands and ("0.5-0.7", 1, 0.0) in bands


def test_sample_and_score(db: Database, tmp_path: Path):
    _store(db, [Comment(f"c{i}", f"I love part {i}", collected_at=WHEN) for i in range(5)])
    out = tmp_path / "labels.csv"
    assert evaluate.sample(db, out, count=3) == 3
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert list(rows[0]) == list(evaluate.COLUMNS)
    rows[0]["label"], rows[1]["label"], rows[2]["label"] = "positive", "neg", ""
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=evaluate.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    labelled, skipped = evaluate.read_labels(out)
    assert (len(labelled), skipped) == (2, 1)
    report = evaluate.score(labelled, keyword_classifier, lambda text: "positive", skipped)
    assert "Labelled comments: 2 (skipped 1" in report
    assert "Confusion matrix" in report and "VADER" in report


def test_real_model_if_installed():
    """Runs only on a machine where the app has downloaded the model already."""
    real_home = Path(os.environ.get("LOCALAPPDATA", "")) / "CommentAnalyzer"
    analyzer = LocalAnalyzer(AppPaths(real_home).models)
    if not analyzer.ready:
        pytest.skip("the sentiment model isn't downloaded on this machine")
    labels = [label for label, _ in analyzer.classifier()(["I love this!", "This is awful."])]
    assert labels == ["positive", "negative"]
