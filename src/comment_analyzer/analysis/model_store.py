"""Model files: downloaded once into the data folder (models/), never bundled with the app.

Each file is pinned to a Hugging Face revision and checked against its size and SHA-256 before
use, so a changed or damaged file is never loaded.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx2

from comment_analyzer.errors import UserError
from comment_analyzer.workers import CancelToken, ProgressFn

log = logging.getLogger(__name__)

MB = 2**20


@dataclass(frozen=True, slots=True)
class ModelFile:
    path: str  # inside the Hugging Face repository
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    repo: str
    revision: str
    files: tuple[ModelFile, ...]
    credit: str  # who made it, and its licence
    title: str  # in messages: "the sentiment model"

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def version(self) -> str:
        return f"{self.name}@{self.revision[:12]}"

    def folder(self, models_root: Path) -> Path:
        return models_root / self.name / self.revision[:12]

    def installed(self, models_root: Path) -> bool:
        folder = self.folder(models_root)
        return all(
            (folder / f.path).is_file() and (folder / f.path).stat().st_size == f.size
            for f in self.files
        )


SENTIMENT = ModelSpec(
    name="twitter-xlm-roberta-base-sentiment",
    repo="onnx-community/twitter-xlm-roberta-base-sentiment-ONNX",
    revision="3112e85dea04f0019bf01282916588e3c9dd9bc8",
    files=(
        ModelFile(
            "onnx/model_int8.onnx",
            279_270_447,
            "6c7c4e804129149d58eb1d11a576129f4270f46134dd074315b8aea05d178e20",
        ),
        ModelFile(
            "tokenizer.json",
            17_098_251,
            "b74659c780d49afad7a7b9799868f75cbd3014fb6c34956e85a793028d38094a",
        ),
    ),
    credit="XLM-T sentiment by Cardiff NLP (Apache-2.0), ONNX export by onnx-community",
    title="the sentiment model",
)

EMBEDDINGS = ModelSpec(
    name="paraphrase-multilingual-MiniLM-L12-v2",
    repo="onnx-community/paraphrase-multilingual-MiniLM-L12-v2-ONNX",
    revision="d4c06bf0d7680171ac30042a1387e1fdb7a90021",
    files=(
        ModelFile(
            "onnx/model_int8.onnx",
            118_049_319,
            "0029fce9c82365d8a2bf20e03a476e84785a872d8d423c8bac0fd0f350df88dc",
        ),
        ModelFile(
            "tokenizer.json",
            17_082_987,
            "cad551d5600a84242d0973327029452a1e3672ba6313c2a3c3d69c4310e12719",
        ),
    ),
    credit="paraphrase-multilingual-MiniLM-L12-v2 by sentence-transformers (Apache-2.0), "
    "ONNX export by onnx-community",
    title="the topic model",
)


class DownloadError(UserError):
    pass


def download(
    spec: ModelSpec,
    models_root: Path,
    progress: ProgressFn,
    token: CancelToken,
    *,
    transport: httpx2.BaseTransport | None = None,
) -> Path:
    """Fetch whatever files are missing; returns the model's folder. Stop leaves no partial file."""
    folder = spec.folder(models_root)
    total, done = spec.size, 0
    timeout = httpx2.Timeout(30.0, read=120.0)
    with httpx2.Client(follow_redirects=True, timeout=timeout, transport=transport) as client:
        for file in spec.files:
            target = folder / file.path
            if target.is_file() and target.stat().st_size == file.size:
                done += file.size
                continue
            done = _fetch(client, spec, file, target, done, total, progress, token)
    log.info("Model %s is ready in %s", spec.version, folder)
    return folder


def _fetch(
    client: httpx2.Client,
    spec: ModelSpec,
    file: ModelFile,
    target: Path,
    done: int,
    total: int,
    progress: ProgressFn,
    token: CancelToken,
) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    url = f"https://huggingface.co/{spec.repo}/resolve/{spec.revision}/{file.path}"
    try:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise DownloadError(
                    f"Couldn't download the model (error {response.status_code}). Try again later."
                )
            with part.open("wb") as out:
                for chunk in response.iter_bytes(MB):
                    token.check()
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    progress(
                        done // MB,
                        total // MB,
                        f"Downloading {spec.title}: {done // MB} of {total // MB} MB",
                    )
    except httpx2.TransportError as e:
        part.unlink(missing_ok=True)
        raise DownloadError(
            "The model download was interrupted. Check your internet connection and try again."
        ) from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if part.stat().st_size != file.size or digest.hexdigest() != file.sha256:
        part.unlink(missing_ok=True)
        log.error("Checksum mismatch for %s", url)
        raise DownloadError("The downloaded model file was damaged. Try again.")
    part.replace(target)
    return done
