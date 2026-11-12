"""Analyse a post's comments and store the results. Runs in a worker: it can take a minute.

Language and spam are cheap, so they're redone for every comment each time. The sentiment model
only runs on comments it hasn't judged yet (or judged with another model version). Topics are
found again each time from all the comments, because new comments can change the groups.
"""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Protocol

from comment_analyzer.analysis import model_store
from comment_analyzer.analysis.claude_mode import MODE as CLAUDE_MODE
from comment_analyzer.analysis.claude_mode import Outcome
from comment_analyzer.analysis.clean import clean
from comment_analyzer.analysis.embeddings import MODEL_VERSION as EMBEDDINGS_VERSION
from comment_analyzer.analysis.embeddings import Embedder, EmbeddingModel, embed_all
from comment_analyzer.analysis.language import detect_language
from comment_analyzer.analysis.model_store import EMBEDDINGS, SENTIMENT
from comment_analyzer.analysis.sentiment import (
    MODEL_VERSION,
    Classifier,
    SentimentModel,
    classify_all,
    is_low_confidence,
)
from comment_analyzer.analysis.spam import spam_flags
from comment_analyzer.analysis.topics import MIN_COMMENTS, FoundTopic, find_topics
from comment_analyzer.db import Database, repository
from comment_analyzer.db.connection import transaction
from comment_analyzer.models import AnalysisResult, Comment, Topic
from comment_analyzer.timeutil import utc_now
from comment_analyzer.workers import CancelToken, ProgressFn

MODE = "local"
MODELS = (SENTIMENT, EMBEDDINGS)


class Analyzer(Protocol):
    """What the Analyse page needs from the local analysis."""

    @property
    def ready(self) -> bool: ...

    @property
    def download_size(self) -> int: ...

    def download(self, progress: ProgressFn, token: CancelToken) -> None: ...

    def classifier(self) -> Classifier: ...

    def embedder(self) -> Embedder: ...


class LocalAnalyzer:
    """The sentiment and topic models, downloaded into the data folder on first use."""

    def __init__(self, models_root: Path) -> None:
        self.models_root = models_root

    @property
    def ready(self) -> bool:
        return all(spec.installed(self.models_root) for spec in MODELS)

    @property
    def download_size(self) -> int:
        """Of the models still missing."""
        return sum(spec.size for spec in MODELS if not spec.installed(self.models_root))

    def download(self, progress: ProgressFn, token: CancelToken) -> None:
        for spec in MODELS:
            model_store.download(spec, self.models_root, progress, token)

    def classifier(self) -> Classifier:
        return SentimentModel(SENTIMENT.folder(self.models_root))

    def embedder(self) -> Embedder:
        return EmbeddingModel(EMBEDDINGS.folder(self.models_root))


def analyse_post(
    db: Database,
    post_row_id: int,
    make_classifier: Callable[[], Classifier],
    progress: ProgressFn,
    token: CancelToken,
    *,
    make_embedder: Callable[[], Embedder] | None = None,
    model_version: str = MODEL_VERSION,
) -> int:
    """Returns how many comments the sentiment model judged in this pass. Without
    make_embedder, the post keeps the topics it had."""
    with closing(db.connect()) as conn:
        comments = [c for c in repository.list_comments(conn, post_row_id) if c.text]
        previous = repository.analysis_for_post(conn, post_row_id, MODE)
    languages = {c.id: c.lang or detect_language(c.text or "")[0] for c in comments}
    todo = [
        c for c in comments if c.id not in previous or previous[c.id].model_version != model_version
    ]
    predictions = {}
    if todo:
        progress(0, len(todo), "Loading the sentiment model...")
        classify = make_classifier()
        texts = [clean(c.text) for c in todo]
        predictions = dict(
            zip((c.id for c in todo), classify_all(classify, texts, progress, token), strict=True)
        )
    spam = spam_flags(comments)
    grouped = [c for c in comments if not spam[c.platform_comment_id]]
    found: list[FoundTopic] | None = None
    if make_embedder is not None:
        found = _topics(grouped, make_embedder, progress, token)
    now = utc_now()
    results = []
    for comment in comments:
        assert comment.id is not None
        sentiment: str | None
        confidence: float | None
        if comment.id in predictions:
            sentiment, confidence = predictions[comment.id]
        else:
            sentiment = previous[comment.id].sentiment
            confidence = previous[comment.id].confidence
        old = previous.get(comment.id)
        results.append(
            AnalysisResult(
                comment_id=comment.id,
                mode=MODE,
                model_version=model_version,
                analyzed_at=now,
                sentiment=sentiment,
                confidence=confidence,
                low_confidence=is_low_confidence(languages[comment.id], confidence),
                topic_id=old.topic_id if old is not None and found is None else None,
                spam_flag=spam[comment.platform_comment_id],
            )
        )
    with closing(db.connect()) as conn, transaction(conn):
        repository.set_comment_languages(
            conn, ((c.id, languages[c.id]) for c in comments if c.id is not None)
        )
        if found is not None:
            topic_of = _store_topics(conn, post_row_id, grouped, found, now)
            for result in results:
                result.topic_id = topic_of.get(result.comment_id)
        repository.upsert_analysis(conn, results)
    return len(todo)


def _topics(
    comments: list[Comment],
    make_embedder: Callable[[], Embedder],
    progress: ProgressFn,
    token: CancelToken,
) -> list[FoundTopic]:
    if len(comments) < MIN_COMMENTS:
        return []
    progress(0, len(comments), "Loading the topic model...")
    texts = [clean(c.text or "") for c in comments]
    return find_topics(texts, embed_all(make_embedder(), texts, progress, token))


def _store_topics(
    conn: sqlite3.Connection,
    post_row_id: int,
    comments: list[Comment],
    found: list[FoundTopic],
    now: datetime,
) -> dict[int, int]:
    """Returns the topic ID of each grouped comment, by comment ID."""
    topics = [
        Topic(
            post_id=post_row_id,
            mode=MODE,
            label=f.label,
            keywords=f.keywords,
            share=len(f.members) / len(comments),
            model_version=EMBEDDINGS_VERSION,
            created_at=now,
        )
        for f in found
    ]
    ids = repository.replace_topics(conn, post_row_id, MODE, topics)
    topic_of = {}
    for topic_id, f in zip(ids, found, strict=True):
        for i in f.members:
            comment_id = comments[i].id
            assert comment_id is not None
            topic_of[comment_id] = topic_id
    return topic_of


def store_claude(db: Database, post_row_id: int, outcome: Outcome) -> None:
    """Save Claude's reading as the "claude" mode, replacing any earlier one. Spam flags come
    from the local rules, so both modes leave out the same comments."""
    with closing(db.connect()) as conn:
        comments = [c for c in repository.list_comments(conn, post_row_id) if c.text]
    spam = spam_flags(comments)
    now = utc_now()
    topics = [
        Topic(
            post_id=post_row_id,
            mode=CLAUDE_MODE,
            label=t.label,
            keywords=[],
            share=len(t.comment_ids) / max(outcome.topic_base, 1),
            model_version=outcome.model,
            created_at=now,
            summary=t.summary,
        )
        for t in outcome.topics
    ]
    with closing(db.connect()) as conn, transaction(conn):
        ids = repository.replace_topics(conn, post_row_id, CLAUDE_MODE, topics)
        topic_of = {
            i: tid for tid, t in zip(ids, outcome.topics, strict=True) for i in t.comment_ids
        }
        repository.upsert_analysis(
            conn,
            [
                AnalysisResult(
                    comment_id=c.id,
                    mode=CLAUDE_MODE,
                    model_version=outcome.model,
                    analyzed_at=now,
                    sentiment=outcome.sentiments[c.id][0],
                    low_confidence=not outcome.sentiments[c.id][1],
                    topic_id=topic_of.get(c.id),
                    spam_flag=spam[c.platform_comment_id],
                )
                for c in comments
                if c.id is not None and c.id in outcome.sentiments
            ],
        )
