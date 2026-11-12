"""Claude mode, with a fake Anthropic client: these tests never send anything."""

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from fakes import FakeAnalyzer
from PySide6.QtWidgets import QMessageBox

from comment_analyzer import config
from comment_analyzer.analysis import claude_mode, summarize
from comment_analyzer.analysis.claude_mode import MODELS, make_plan, model_by_id
from comment_analyzer.analysis.pipeline import analyse_post, store_claude
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, repository
from comment_analyzer.errors import UserError
from comment_analyzer.models import Comment, Platform, Post
from comment_analyzer.ui.pages.analyse import NO_CLAUDE_KEY, AnalysePage
from comment_analyzer.ui.pages.settings import SettingsPage
from comment_analyzer.workers import CancelToken, TaskRunner

WHEN = datetime(2026, 11, 1, tzinfo=UTC)
KEY = "sk-ant-api03-" + "x" * 40


class FakeClaude:
    """Answers like the Messages API with structured outputs: positive when a comment mentions
    "love", negative for "hate", and two topics."""

    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.stop_reason = stop_reason
        self.sent: list[dict[str, Any]] = []
        self.counted = 0

    def count_tokens(self, **params: Any) -> Any:
        self.counted += 1
        return SimpleNamespace(input_tokens=len(params["messages"][0]["content"]) // 4)

    def create(self, **params: Any) -> Any:
        self.sent.append(params)
        text = params["messages"][0]["content"]
        lines = [line for line in text.splitlines() if line[:1].isdigit()]
        numbers = [int(line.split(".")[0]) for line in lines]
        if "sentiment" in json.dumps(params["output_config"]["format"]["schema"]):
            answer = {
                "comments": [
                    {
                        "n": n,
                        "sentiment": "positive"
                        if "love" in line
                        else "negative"
                        if "hate" in line
                        else "neutral",
                        "sure": "?" not in line,
                    }
                    for n, line in zip(numbers, lines, strict=True)
                ]
            }
        else:
            loves = [n for n, line in zip(numbers, lines, strict=True) if "love" in line]
            hates = [n for n, line in zip(numbers, lines, strict=True) if "hate" in line]
            answer = {
                "topics": [
                    {"label": "Praise", "summary": "People like it.", "comments": loves},
                    {"label": "Complaints", "summary": "Some don't.", "comments": hates + loves},
                ]
            }
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(answer))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
        )


def _comments(count: int = 40) -> list[Comment]:
    texts = ["I love this, @someone", "I hate the ending https://spam.example", "what? ok"]
    return [
        Comment(f"c{i}", f"{texts[i % 3]} {i}", like_count=i, collected_at=WHEN, id=i + 1)
        for i in range(count)
    ]


def test_plan_chunks_numbers_and_hides_usernames(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(claude_mode, "CHUNK", 16)
    plan = make_plan(_comments(40), spam={40}, model=MODELS[0])
    kinds = [r.kind for r in plan.requests]
    assert kinds == ["sentiment", "sentiment", "sentiment", "topics"]
    assert plan.comments == 40
    first = plan.requests[0].params("claude-opus-5-5")["messages"][0]["content"]
    assert "1. I love this, @user 0" in first and "2. I hate the ending http 1" in first
    assert "@someone" not in first and "spam.example" not in first
    assert 40 not in plan.requests[-1].comment_ids  # spam stays out of the topics
    assert plan.requests[-1].comment_ids[0] == 39  # most liked first


def test_small_posts_get_no_topic_request():
    plan = make_plan(_comments(10), spam=set(), model=MODELS[0])
    assert [r.kind for r in plan.requests] == ["sentiment"]


def test_costs_use_the_model_prices():
    opus, haiku = model_by_id("claude-opus-5-5"), model_by_id("claude-haiku-5-5")
    assert opus.cost(1_000_000, 100_000) == pytest.approx(4 + 2)
    assert haiku.cost(100_000, 0) == pytest.approx(0.01)
    assert haiku.cost(200_000, 0) == pytest.approx(0.10)  # long prompts cost more
    assert model_by_id("no-such-model") is MODELS[0]
    api = FakeClaude()
    plan = make_plan(_comments(40), spam=set(), model=opus)
    claude_mode.count_tokens(api, plan, CancelToken())
    assert api.counted == 2 and plan.input_tokens > 0
    assert 0 < plan.expected_cost() < plan.max_cost()


def test_run_reads_answers_and_counts_the_cost():
    api = FakeClaude()
    plan = make_plan(_comments(40), spam=set(), model=model_by_id("claude-haiku-5-5"))
    outcome = claude_mode.run(api, plan, lambda *a: None, CancelToken())
    assert outcome.sentiments[1] == ("positive", True)
    assert outcome.sentiments[2] == ("negative", True)
    assert outcome.sentiments[3] == ("neutral", False)  # "what?" was unclear
    assert outcome.skipped == 0
    praise, complaints = outcome.topics
    assert praise.label == "Praise" and len(praise.comment_ids) == 14
    assert set(complaints.comment_ids).isdisjoint(praise.comment_ids)  # one topic each
    assert outcome.cost == pytest.approx(2 * (1000 * 0.10 + 500 * 0.50) / 1e6)
    sent = api.sent[0]
    assert sent["max_tokens"] == claude_mode.MAX_TOKENS
    assert sent["output_config"]["effort"] == "low"


def test_declined_answers_are_skipped():
    plan = make_plan(_comments(5), spam=set(), model=MODELS[0])
    outcome = claude_mode.run(FakeClaude("refusal"), plan, lambda *a: None, CancelToken())
    assert outcome.sentiments == {} and outcome.skipped == 5


def _error(cls: type[anthropic.APIStatusError], status: int, message: str) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "refused the API key"),
        (_error(anthropic.RateLimitError, 429, "slow down"), "limiting requests"),
        (
            _error(anthropic.BadRequestError, 400, "Your credit balance is too low"),
            "run out of credit",
        ),
        (_error(anthropic.InternalServerError, 500, "oops"), "error 500"),
    ],
)
def test_api_errors_become_plain_messages(error: Exception, words: str):
    def fail() -> None:
        raise error

    with pytest.raises(UserError, match=words):
        claude_mode._call(fail)


# --- storage and the Analyse page -------------------------------------------------------------


def _post(db: Database) -> int:
    with closing(db.connect()) as conn:
        post_id = repository.upsert_post(
            conn, Post(Platform.YOUTUBE, "vid00000001", "https://youtu.be/vid00000001")
        )
        repository.upsert_comments(
            conn, post_id, [Comment(c.platform_comment_id, c.text) for c in _comments(40)]
        )
    return post_id


def test_claude_results_are_a_second_opinion(tmp_path: Path):
    db = Database(tmp_path / "data.db")
    db.migrate()
    post_id = _post(db)
    analyse_post(db, post_id, FakeAnalyzer().classifier, lambda *a: None, CancelToken())
    with closing(db.connect()) as conn:
        comments = repository.list_comments(conn, post_id)
    plan = make_plan(comments, set(), MODELS[0])
    store_claude(db, post_id, claude_mode.run(FakeClaude(), plan, lambda *a: None, CancelToken()))
    with closing(db.connect()) as conn:
        local = repository.analysis_for_post(conn, post_id)
        claude = repository.analysis_for_post(conn, post_id, "claude")
        topics = repository.list_topics(conn, post_id, "claude")
        assert repository.list_topics(conn, post_id) == []  # local topics untouched
    assert len(claude) == 40 and {r.model_version for r in claude.values()} == {"claude-opus-5-5"}
    assert [t.summary for t in topics] == ["People like it.", "Some don't."]
    lines = summarize.second_opinion(comments, local, claude, topics)
    assert lines[0].startswith("Second opinion from Claude (claude-opus-5-5")
    assert "agrees with the local model on" in lines[0]
    assert lines[1].startswith("  - Praise (") and lines[1].endswith("People like it.")


@pytest.fixture
def page(qtbot) -> Any:
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db)
    db.migrate()
    tasks = TaskRunner()
    api = FakeClaude()
    page = AnalysePage(db, tasks, config.settings_store(paths), claude_factory=lambda key: api)
    page.fake_api = api  # type: ignore[attr-defined]
    qtbot.addWidget(page)
    page.show_post(_post(db))
    yield page
    tasks.cancel_all()
    tasks.wait(5000)


def test_without_a_key_claude_mode_explains_itself(page: AnalysePage):
    assert page.claude_btn.isEnabled()
    page.claude_btn.click()
    assert page.status.text() == NO_CLAUDE_KEY
    assert page.fake_api.counted == 0  # type: ignore[attr-defined]


def test_nothing_is_sent_without_agreeing_to_the_cost(
    qtbot, page: AnalysePage, monkeypatch: pytest.MonkeyPatch
):
    config.set_secret(config.ANTHROPIC_API_KEY, KEY)
    asked: list[str] = []

    def no(parent: object, title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    page.claude_btn.click()
    qtbot.waitUntil(lambda: bool(asked), timeout=5000)
    assert "Estimated cost: about $" in asked[0] and "40 comments" in asked[0]
    assert page.status.text() == "Nothing was sent to Claude."
    assert page.fake_api.sent == []  # type: ignore[attr-defined]


def test_agreeing_sends_and_shows_the_second_opinion(
    qtbot, page: AnalysePage, monkeypatch: pytest.MonkeyPatch
):
    config.set_secret(config.ANTHROPIC_API_KEY, KEY)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(page.analysis_finished, timeout=5000):
        page.claude_btn.click()
    assert page.status.text().startswith("Claude read 40 comments for about $")
    assert "Second opinion from Claude" in page.analysis.text()


def test_settings_check_and_save_the_anthropic_key(qtbot):
    paths = AppPaths.default()
    paths.create()
    db = Database(paths.db)
    db.migrate()
    tasks = TaskRunner()
    checked: list[str] = []
    page = SettingsPage(
        paths, config.settings_store(paths), db, tasks, claude_key_checker=checked.append
    )
    qtbot.addWidget(page)
    page.claude_key_edit.setText("not a key")
    page.save_claude_key_btn.click()
    assert "sk-ant-" in page.claude_key_status.text()
    page.claude_key_edit.setText(KEY)
    page.save_claude_key_btn.click()
    qtbot.waitUntil(lambda: config.get_secret(config.ANTHROPIC_API_KEY) == KEY, timeout=5000)
    qtbot.waitUntil(lambda: KEY[-4:] in page.claude_key_status.text(), timeout=5000)
    assert checked == [KEY]
    page.claude_model.setCurrentIndex(2)
    page.apply_autostart = False
    page.save()
    assert config.load_settings(page.store).claude_model == "claude-haiku-5-5"
    page.remove_claude_key()
    assert config.get_secret(config.ANTHROPIC_API_KEY) is None
    tasks.wait(5000)


# --- the evaluation script --------------------------------------------------------------------


def test_evaluation_asks_before_sending_to_claude():
    from comment_analyzer.analysis import evaluate
    from comment_analyzer.analysis.evaluate import Labelled

    labelled = [
        Labelled("I love it", "en", "positive"),
        Labelled("I hate it", "en", "negative"),
        Labelled("what? ok", "en", "neutral"),
    ] * 12
    api = FakeClaude()
    questions: list[str] = []
    assert (
        evaluate.ask_claude(
            labelled, api, "claude-haiku-5-5", lambda q: questions.append(q) or False
        )
        is None
    )
    assert api.sent == [] and api.counted == 1  # counting is free; nothing was sent
    assert "Send 36 comments to Claude Haiku 5.5 for about $" in questions[0]

    answers = evaluate.ask_claude(labelled, api, "claude-haiku-5-5", lambda q: True)
    assert answers is not None and len(api.sent) == 1  # sentiment only, no topic request
    assert answers.predictions[:3] == ["positive", "negative", "neutral"]
    report = evaluate.score(labelled, lambda texts: [("neutral", 0.9)] * len(texts), claude=answers)
    assert "Claude (claude-haiku-5-5, $" in report
