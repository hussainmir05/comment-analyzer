"""Sentence vectors from paraphrase-multilingual-MiniLM-L12-v2 (50+ languages), run with ONNX
Runtime on the CPU. Comments that say similar things get nearby vectors, in any language; topics.py
groups them.
"""

from collections.abc import Callable
from pathlib import Path

import numpy as np

from comment_analyzer.analysis.model_store import EMBEDDINGS
from comment_analyzer.analysis.onnx_text import OnnxTextModel, in_batches
from comment_analyzer.workers import CancelToken, ProgressFn

MODEL_VERSION = EMBEDDINGS.version

Embedder = Callable[[list[str]], list[np.ndarray]]  # one unit-length vector per text


class EmbeddingModel:
    """The MiniLM model in its folder (see model_store.EMBEDDINGS)."""

    def __init__(self, folder: Path, threads: int | None = None) -> None:
        self._model = OnnxTextModel(folder, threads)

    def __call__(self, texts: list[str]) -> list[np.ndarray]:
        tokens, mask = self._model.run(texts)  # tokens: (texts, tokens, 384)
        # the model was trained with the mean of its token vectors, padding left out
        weights = mask[:, :, None]
        vectors = (tokens * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        return list(vectors.astype(np.float32))


def embed_all(
    embed: Embedder, texts: list[str], progress: ProgressFn, token: CancelToken
) -> np.ndarray:
    """One row per text."""
    vectors = in_batches(
        embed, texts, progress, token, "Finding the topics of {done:,} of {total:,} comments"
    )
    return np.stack(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)
