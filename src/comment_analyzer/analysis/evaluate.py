r"""Check the sentiment model against comments you label yourself.

    python -m comment_analyzer.analysis.evaluate sample [--count 300]
        Writes a CSV of collected comments to the data folder's exports\ folder. Fill in the
        "label" column with positive, neutral or negative; leave it empty to skip a comment.
    python -m comment_analyzer.analysis.evaluate score <labels.csv>
        Compares your labels with the model and with VADER (the previous version's method). It
        reports accuracy and macro-F1, overall and per language, plus a confusion matrix and
        calibration. The report is saved next to the CSV.
    python -m comment_analyzer.analysis.evaluate score <labels.csv> --claude
        Also asks Claude (paid): it shows the estimated cost and sends nothing until you say yes.

The CSV holds real comments, so it stays in the data folder. Never commit it.
"""

import argparse
import csv
import random
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from comment_analyzer import config
from comment_analyzer.analysis import claude_mode
from comment_analyzer.analysis.claude_mode import MessagesApi, make_client
from comment_analyzer.analysis.clean import clean
from comment_analyzer.analysis.language import detect_language, language_name
from comment_analyzer.analysis.sentiment import (
    LABELS,
    Classifier,
    classify_all,
    is_low_confidence,
)
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database
from comment_analyzer.models import Comment
from comment_analyzer.workers import CancelToken

COLUMNS = ("comment_id", "language", "likes", "label", "text")
_ALIASES = {"pos": "positive", "neu": "neutral", "neg": "negative"}
BUCKETS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))


@dataclass(frozen=True, slots=True)
class Labelled:
    text: str
    language: str
    label: str


# --- metrics ----------------------------------------------------------------------------------


def accuracy(pairs: list[tuple[str, str]]) -> float:
    """pairs: (true label, predicted label)."""
    return sum(t == p for t, p in pairs) / len(pairs) if pairs else 0.0


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    """The mean of each label's F1, so a rare label counts as much as a common one."""
    scores = []
    for label in LABELS:
        tp = sum(t == label and p == label for t, p in pairs)
        fp = sum(t != label and p == label for t, p in pairs)
        fn = sum(t == label and p != label for t, p in pairs)
        if tp + fp + fn == 0:
            continue  # the label appears nowhere: leave it out
        scores.append(2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores) if scores else 0.0


def confusion(pairs: list[tuple[str, str]]) -> Counter[tuple[str, str]]:
    return Counter(pairs)


def calibration(rows: list[tuple[str, str, float]]) -> list[tuple[str, int, float]]:
    """rows: (true, predicted, confidence). For each confidence band: (band, count, accuracy)."""
    bands = []
    for low, high in BUCKETS:
        inside = [(t, p) for t, p, c in rows if low <= c < high]
        bands.append((f"{low:.1f}-{min(high, 1.0):.1f}", len(inside), accuracy(inside)))
    return bands


# --- sample -----------------------------------------------------------------------------------


def sample(db: Database, out: Path, count: int, seed: int = 7) -> int:
    """Write up to `count` random collected comments to a CSV for labelling."""
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT id, lang, like_count, text FROM comments WHERE text IS NOT NULL"
        ).fetchall()
    chosen = random.Random(seed).sample(rows, min(count, len(rows)))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:  # Excel reads utf-8-sig
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for row in chosen:
            language = row["lang"] or detect_language(row["text"])[0]
            writer.writerow([row["id"], language, row["like_count"] or 0, "", clean(row["text"])])
    return len(chosen)


# --- score ------------------------------------------------------------------------------------


def read_labels(path: Path) -> tuple[list[Labelled], int]:
    """(labelled rows, rows skipped for a missing or unknown label)."""
    labelled, skipped = [], 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            label = (row.get("label") or "").strip().lower()
            label = _ALIASES.get(label, label)
            if label not in LABELS:
                skipped += 1
                continue
            text = row.get("text") or ""
            language = row.get("language") or detect_language(text)[0]
            labelled.append(Labelled(text, language, label))
    return labelled, skipped


@dataclass(slots=True)
class ClaudeAnswers:
    model: str
    predictions: list[str | None]  # one per labelled comment, in order
    cost: float


def score(
    labelled: list[Labelled],
    classify: Classifier,
    vader: Callable[[str], str] | None = None,
    skipped: int = 0,
    claude: ClaudeAnswers | None = None,
) -> str:
    """A plain-text report comparing the labels with the model, and with VADER and Claude when
    given."""
    predictions = classify_all(classify, [x.text for x in labelled], lambda *_: None, CancelToken())
    rows = [(x.label, p, c) for x, (p, c) in zip(labelled, predictions, strict=True)]
    pairs = [(t, p) for t, p, _ in rows]
    lines = [f"Labelled comments: {len(rows)} (skipped {skipped} without a valid label)", ""]
    lines.append(f"{'':28}{'n':>5}{'accuracy':>10}{'macro-F1':>10}")
    lines.append(_metric_row("All", pairs))
    by_language: dict[str, list[tuple[str, str]]] = {}
    for x, pair in zip(labelled, pairs, strict=True):
        by_language.setdefault(x.language, []).append(pair)
    for language, group in sorted(by_language.items(), key=lambda item: -len(item[1])):
        lines.append(_metric_row(f"  {language_name(language)}", group))
    flagged = [
        is_low_confidence(x.language, c) for x, (_, _, c) in zip(labelled, rows, strict=True)
    ]
    lines.append(
        _metric_row("Marked unsure", [p for p, f in zip(pairs, flagged, strict=True) if f])
    )
    lines.append(
        _metric_row("Not marked", [p for p, f in zip(pairs, flagged, strict=True) if not f])
    )

    lines += ["", "Confusion matrix (rows: your label, columns: the model)"]
    lines.append(f"{'':12}" + "".join(f"{label:>10}" for label in LABELS))
    matrix = confusion(pairs)
    for true in LABELS:
        lines.append(f"{true:12}" + "".join(f"{matrix[(true, p)]:>10}" for p in LABELS))

    lines += ["", "Calibration (does the model's confidence match how often it's right?)"]
    lines.append(f"{'confidence':12}{'n':>5}{'accuracy':>10}")
    for band, n, acc in calibration(rows):
        lines.append(f"{band:12}{n:>5}{acc:>10.2f}")

    if vader is not None:
        baseline = [(x.label, vader(x.text)) for x in labelled]
        lines += ["", "VADER (the previous version's method), for comparison"]
        lines.append(_metric_row("All", baseline))
    if claude is not None:
        answered = [
            (x, p) for x, p in zip(labelled, claude.predictions, strict=True) if p is not None
        ]
        lines += ["", f"Claude ({claude.model}, ${claude.cost:.2f}), for comparison"]
        lines.append(_metric_row("All", [(x.label, p) for x, p in answered]))
        by_lang: dict[str, list[tuple[str, str]]] = {}
        for x, p in answered:
            by_lang.setdefault(x.language, []).append((x.label, p))
        for language, group in sorted(by_lang.items(), key=lambda item: -len(item[1])):
            lines.append(_metric_row(f"  {language_name(language)}", group))
        if len(answered) < len(labelled):
            lines.append(f"(Claude gave no answer for {len(labelled) - len(answered)} comments.)")
    return "\n".join(lines)


def ask_claude(
    labelled: list[Labelled],
    api: MessagesApi,
    model_id: str,
    agree: Callable[[str], bool],
) -> ClaudeAnswers | None:
    """Count the tokens (free), show the cost, and send only if agree() says yes."""
    comments = [Comment(str(i), x.text, id=i + 1) for i, x in enumerate(labelled)]
    plan = claude_mode.make_plan(comments, set(), claude_mode.model_by_id(model_id), topics=False)
    claude_mode.count_tokens(api, plan, CancelToken())
    question = (
        f"Send {plan.comments} comments to {plan.model.name.split(' (')[0]} for about "
        f"${plan.expected_cost():.2f} (at most ${plan.max_cost():.2f}), billed to your Anthropic "
        "account?"
    )
    if not agree(question):
        return None
    outcome = claude_mode.run(api, plan, lambda *_: None, CancelToken())
    predictions = [
        outcome.sentiments[c.id][0] if c.id in outcome.sentiments else None for c in comments
    ]
    return ClaudeAnswers(plan.model.id, predictions, outcome.cost)


def _metric_row(name: str, pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return f"{name:28}{0:>5}{'-':>10}{'-':>10}"
    return f"{name:28}{len(pairs):>5}{accuracy(pairs):>10.2f}{macro_f1(pairs):>10.2f}"


def vader_classifier() -> Callable[[str], str] | None:
    """VADER with the previous version's thresholds, if it's installed (a dev dependency)."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError:
        return None
    analyzer = SentimentIntensityAnalyzer()

    def judge(text: str) -> str:
        compound = analyzer.polarity_scores(text)["compound"]
        return "positive" if compound >= 0.05 else "negative" if compound <= -0.05 else "neutral"

    return judge


def _yes(question: str) -> bool:
    return input(question + " [y/N] ").strip().lower() in ("y", "yes")


def main(argv: Iterable[str] | None = None, ask: Callable[[str], bool] = _yes) -> int:
    parser = argparse.ArgumentParser(description="Check the sentiment model against your labels.")
    commands = parser.add_subparsers(dest="command", required=True)
    sample_cmd = commands.add_parser("sample", help="write a CSV of comments to label")
    sample_cmd.add_argument("--count", type=int, default=300)
    score_cmd = commands.add_parser("score", help="compare your labels with the model")
    score_cmd.add_argument("csv", type=Path)
    score_cmd.add_argument(
        "--claude",
        action="store_true",
        help="also ask Claude (paid: shows the estimated cost and asks first)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    paths = AppPaths.default()
    if args.command == "sample":
        out = paths.exports / "sentiment-labels.csv"
        written = sample(Database(paths.db), out, args.count)
        print(f"Wrote {written} comments to {out}")
        print('Fill the "label" column with positive, neutral or negative, then run "score".')
        return 0

    from comment_analyzer.analysis.pipeline import LocalAnalyzer

    analyzer = LocalAnalyzer(paths.models)
    if not analyzer.ready:
        print("The sentiment model isn't downloaded yet. Analyse a post in the app first.")
        return 1
    labelled, skipped = read_labels(args.csv)
    if not labelled:
        print("No labelled rows found.")
        return 1
    claude = None
    if args.claude:
        key = config.get_secret(config.ANTHROPIC_API_KEY)
        if not key:
            print("Claude needs an Anthropic API key: save one in the app's Settings.")
            return 1
        model = config.load_settings(config.settings_store(paths)).claude_model
        claude = ask_claude(labelled, make_client(key), model, ask)
    report = score(labelled, analyzer.classifier(), vader_classifier(), skipped, claude)
    report_path = args.csv.with_name(args.csv.stem + "-report.txt")
    report_path.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nSaved to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
