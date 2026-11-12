"""Sentiment from XLM-T (Cardiff NLP): a multilingual RoBERTa trained on tweets, run with ONNX
Runtime on the CPU.

It was trained on Arabic, English, French, German, Hindi, Italian, Portuguese and Spanish
tweets. It still answers for other languages, but those answers are marked low-confidence.
"""

from collections.abc import Callable
from pathlib import Path

import numpy as np

from comment_analyzer.analysis.language import UNKNOWN
from comment_analyzer.analysis.model_store import SENTIMENT
from comment_analyzer.analysis.onnx_text import OnnxTextModel, in_batches
from comment_analyzer.workers import CancelToken, ProgressFn

LABELS = ("negative", "neutral", "positive")
TRAINED_LANGUAGES = frozenset({"ar", "en", "fr", "de", "hi", "it", "pt", "es"})
MIN_CONFIDENCE = 0.5  # with three labels, the top probability is never below 1/3
MODEL_VERSION = SENTIMENT.version

Prediction = tuple[str, float]  # label, probability of that label
Classifier = Callable[[list[str]], list[Prediction]]


def preprocess(text: str) -> str:
    """As the model card asks: @mentions become "@user" and links become "http"."""
    words = []
    for word in text.split():
        if word.startswith("@") and len(word) > 1:
            word = "@user"
        elif word.startswith("http"):
            word = "http"
        words.append(word)
    return " ".join(words)


def is_low_confidence(language: str, confidence: float | None) -> bool:
    untrained = language != UNKNOWN and language not in TRAINED_LANGUAGES
    return untrained or confidence is None or confidence < MIN_CONFIDENCE


class SentimentModel:
    """The XLM-T model in its folder (see model_store.SENTIMENT)."""

    def __init__(self, folder: Path, threads: int | None = None) -> None:
        self._model = OnnxTextModel(folder, threads)

    def __call__(self, texts: list[str]) -> list[Prediction]:
        logits, _mask = self._model.run([preprocess(t) for t in texts])
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        return [(LABELS[int(p.argmax())], float(p.max())) for p in probabilities]


def classify_all(
    classify: Classifier, texts: list[str], progress: ProgressFn, token: CancelToken
) -> list[Prediction]:
    return in_batches(
        classify, texts, progress, token, "Reading the mood of {done:,} of {total:,} comments"
    )
