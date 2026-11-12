"""What the sentiment and topic models share: a Hugging Face tokenizer and an ONNX model run on the
CPU, fed in batches of comments of similar length."""

import os
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from comment_analyzer.workers import CancelToken, ProgressFn

MAX_TOKENS = 128  # longer comments are judged on their beginning
BATCH_SIZE = 32


class OnnxTextModel:
    """Loads `onnx/model_int8.onnx` and `tokenizer.json` from a model folder (see model_store)."""

    def __init__(self, folder: Path, threads: int | None = None) -> None:
        # Imported here: ONNX Runtime takes a moment to load and only analysis needs it
        import onnxruntime
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        pad_id = tokenizer.token_to_id("<pad>")
        tokenizer.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
        options = onnxruntime.SessionOptions()
        # leave a core for the window, so the app stays responsive while it works
        options.intra_op_num_threads = threads or max(1, (os.cpu_count() or 2) - 1)
        self._session = onnxruntime.InferenceSession(
            str(folder / "onnx" / "model_int8.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = tokenizer

    def run(self, texts: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """The model's first output, and the attention mask (1 for words, 0 for padding)."""
        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)
        return np.asarray(self._session.run(None, feeds)[0], dtype=np.float64), mask


def in_batches[T](
    fn: Callable[[list[str]], Sequence[T]],
    texts: list[str],
    progress: ProgressFn,
    token: CancelToken,
    message: str,
) -> list[T]:
    """Run fn on batches of similar length (less padding, so faster). Results keep the input
    order. `message` is formatted with `done` and `total`."""
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    results: dict[int, T] = {}
    for start in range(0, len(order), BATCH_SIZE):
        token.check()
        chunk = order[start : start + BATCH_SIZE]
        for i, result in zip(chunk, fn([texts[i] for i in chunk]), strict=True):
            results[i] = result
        done = min(start + BATCH_SIZE, len(order))
        progress(done, len(order), message.format(done=done, total=len(order)))
    return [results[i] for i in range(len(texts))]
