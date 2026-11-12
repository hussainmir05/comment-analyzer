import os
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from fakes import FakeAnalyzer, fake_embedder

from comment_analyzer.analysis import summarize, topics
from comment_analyzer.analysis.model_store import EMBEDDINGS
from comment_analyzer.analysis.pipeline import analyse_post
from comment_analyzer.analysis.topics import NO_WORDS, communities, find_topics, name_groups
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.models import AnalysisResult, Comment, Platform, Post, Topic
from comment_analyzer.reports.data import load_post
from comment_analyzer.ui.comments_table import TEXT_COLUMN, TOPIC_COLUMN, CommentsView
from comment_analyzer.ui.dashboard_html import Theme, post_page
from comment_analyzer.workers import CancelToken

WHEN = datetime(2026, 11, 1, tzinfo=UTC)


def _unit(rows: np.ndarray) -> np.ndarray:
    return rows / np.linalg.norm(rows, axis=1, keepdims=True)


def test_communities_take_the_biggest_groups_and_leave_the_rest():
    rng = np.random.default_rng(0)
    centres = np.eye(8)[:3]
    vectors = [centres[0] + 0.05 * rng.standard_normal(8) for _ in range(6)]
    vectors += [centres[1] + 0.05 * rng.standard_normal(8) for _ in range(4)]
    vectors += [centres[2] + 0.05 * rng.standard_normal(8) for _ in range(2)]  # too small
    groups = communities(_unit(np.array(vectors)), threshold=0.9, min_size=3)
    assert [sorted(g) for g in groups] == [[0, 1, 2, 3, 4, 5], [6, 7, 8, 9]]


def test_small_posts_get_no_topics():
    texts = ["same words"] * (topics.MIN_COMMENTS - 1)
    assert find_topics(texts, _unit(np.ones((len(texts), 4)))) == []


def test_names_use_the_words_typical_of_each_group():
    texts = [
        "the audio is too quiet",
        "audio too quiet again",
        "can't hear the audio",
        "the price is too high",
        "price too high for me",
        "high price, no thanks",
        "😂😂",
        "🔥",
        "ok",  # too short to be a word
        "thanks a lot",
        "thanks so much",
        "thank you thanks",
    ]
    names = name_groups(texts, [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11]])
    assert names[0][0] == "audio"
    assert set(names[1][:2]) == {"high", "price"}  # equal scores
    assert names[2] == []  # no words: named NO_WORDS by find_topics
    assert names[3][0] == "thanks" and "thank" not in names[3]


def test_phrases_from_one_name_are_joined():
    texts = ["hello to dera ismail khan", "from dera ismail khan", "dera ismail khan rocks"]
    texts += ["something else entirely"] * 3
    names = name_groups(texts, [[0, 1, 2]])
    assert names[0][0] == "dera ismail khan"


def test_find_topics_labels_and_orders_groups():
    texts = [f"audio quiet {i}" for i in range(20)] + [f"price high {i}" for i in range(12)]
    texts += ["😂"] * 10
    vectors = _unit(np.array([[1, 0, 0]] * 20 + [[0, 1, 0]] * 12 + [[0, 0, 1]] * 10, float))
    found = find_topics(texts, vectors)
    assert [len(f.members) for f in found] == [20, 12, 10]
    assert found[0].label.startswith("audio")
    assert found[2].label == NO_WORDS


# --- storage and pipeline ---------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data.db")
    database.migrate()
    return database


def _post_with_comments(db: Database) -> int:
    comments = [Comment(f"a{i}", f"audio is too quiet {i}", collected_at=WHEN) for i in range(15)]
    comments += [Comment(f"p{i}", f"price is too high {i}", collected_at=WHEN) for i in range(12)]
    comments += [
        Comment(f"x{i}", f"x{i % 10} something else {i}", collected_at=WHEN) for i in range(13)
    ]
    with closing(db.connect()) as conn:
        post_id = repository.upsert_post(
            conn, Post(Platform.YOUTUBE, "vid00000001", "https://youtu.be/vid00000001")
        )
        repository.upsert_comments(conn, post_id, comments)
    return post_id


def _analyse(db: Database, post_id: int, embed: bool = True) -> None:
    analyzer = FakeAnalyzer()
    analyse_post(
        db,
        post_id,
        analyzer.classifier,
        lambda *a: None,
        CancelToken(),
        make_embedder=analyzer.embedder if embed else None,
    )


def test_pipeline_stores_topics_and_links_comments(db: Database):
    post_id = _post_with_comments(db)
    _analyse(db, post_id)
    with closing(db.connect()) as conn:
        stored = repository.list_topics(conn, post_id)
        results = repository.analysis_for_post(conn, post_id)
        comments = repository.list_comments(conn, post_id)
    assert "audio" in stored[0].label and "price" in stored[1].label
    assert stored[0].share == pytest.approx(15 / 40)
    assert stored[0].keywords and stored[0].mode == "local"
    topic_of = {c.platform_comment_id: results[c.id or 0].topic_id for c in comments}
    assert topic_of["a0"] == stored[0].id and topic_of["p3"] == stored[1].id
    assert topic_of["x5"] is None

    # analysing again replaces the topics; without the topic model, the post keeps them
    _analyse(db, post_id)
    _analyse(db, post_id, embed=False)
    with closing(db.connect()) as conn:
        again = repository.list_topics(conn, post_id)
        results = repository.analysis_for_post(conn, post_id)
    assert [t.label for t in again] == [t.label for t in stored]
    assert sum(r.topic_id == again[0].id for r in results.values()) == 15


def test_replacing_topics_unlinks_old_ones(db: Database):
    post_id = _post_with_comments(db)
    _analyse(db, post_id)
    with closing(db.connect()) as conn:
        repository.replace_topics(conn, post_id, "local", [])
        assert repository.list_topics(conn, post_id) == []
        results = repository.analysis_for_post(conn, post_id)
    assert all(r.topic_id is None for r in results.values())


def test_topics_reach_the_summary_dashboard_and_table(db: Database, qtbot):
    post_id = _post_with_comments(db)
    _analyse(db, post_id)
    report = load_post(db, post_id)
    assert report is not None and report.analysis is not None
    first = report.analysis.topics[0]
    assert first.comments == 15 and first.example is not None
    assert sum(first.sentiments.values()) == pytest.approx(100)
    lines = summarize.summary_lines(report.analysis)
    assert any(line.startswith("Comments talk most about: audio") for line in lines)
    assert '<div id="topics"' in post_page(report, Theme(), "")

    view = CommentsView()
    qtbot.addWidget(view)
    view.set_comments(report.comments, report.results, report.topics)
    assert view.topic.count() == 3  # "All topics" and two topics
    view.topic.setCurrentIndex(2)
    shown = {view.proxy.index(r, TEXT_COLUMN).data() for r in range(view.proxy.rowCount())}
    assert len(shown) == 12 and all(text.startswith("price") for text in shown)
    assert view.proxy.index(0, TOPIC_COLUMN).data() == report.topics[1].label


def test_topic_shares_skip_spam_and_empty_topics():
    comments = [Comment(str(i), f"text {i}", like_count=i, id=i + 1) for i in range(4)]
    results = {
        c.id or 0: AnalysisResult(
            c.id or 0, "local", "v", WHEN, sentiment, 0.9, topic_id=7, spam_flag=spam
        )
        for c, sentiment, spam in zip(
            comments,
            ["positive", "negative", "negative", "neutral"],
            [False, False, False, True],
            strict=True,
        )
    }
    stored = [
        Topic(1, "local", "seven", ["seven"], 0.75, "v", WHEN, id=7),
        Topic(1, "local", "empty", ["empty"], 0.1, "v", WHEN, id=8),
    ]
    analysis = summarize.build(comments, results, stored)
    assert analysis is not None
    [share] = analysis.topics
    assert share.comments == 3 and share.example == "text 2"
    assert share.sentiments["negative"] == pytest.approx(200 / 3)


def test_real_topic_model_if_installed():
    """Runs only on a machine where the app has downloaded the model already."""
    models = AppPaths(Path(os.environ.get("LOCALAPPDATA", "")) / "CommentAnalyzer").models
    if not EMBEDDINGS.installed(models):
        pytest.skip("the topic model isn't downloaded on this machine")
    from comment_analyzer.analysis.embeddings import EmbeddingModel

    quiet, leise, price = EmbeddingModel(EMBEDDINGS.folder(models))(
        ["The sound is too quiet", "Der Ton ist zu leise", "The price is too high"]
    )
    assert float(np.linalg.norm(quiet)) == pytest.approx(1, abs=1e-5)
    assert float(quiet @ leise) > 0.7 > 0.3 > float(quiet @ price)


def test_fake_embedder_groups_by_first_word():
    a, b, c = fake_embedder(["audio one", "audio two", "price"])
    assert float(a @ b) == 1 and float(a @ c) == 0


def test_roman_urdu_function_words_never_name_anything():
    texts = [
        "yeh chip bhi chinese hai bhai",
        "chip ka case hai, bohot bura",
        "bhai yeh chip wala case kya hai",
    ]
    names = name_groups(texts * 2, [[0, 1, 2, 3, 4, 5]])
    assert names[0][0] == "chip"
    assert not {"hai", "bhai", "bhi", "yeh", "kya", "wala"} & {
        w for n in names[0] for w in n.split()
    }
