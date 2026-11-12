"""Optional analysis by Claude, Anthropic's paid API, as a second opinion next to the local models.

Nothing is sent until the user has seen the estimated cost and agreed (the Analyse page asks).
Only comment texts go out, numbered, with @mentions turned into "@user" and links into "http".
The app doesn't store commenters' names, so none can be sent.

Requests go one at a time from a worker. The Message Batches API would cost half as much, but its
results can take up to 24 hours, and the app would have to follow a batch across restarts.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from comment_analyzer.analysis.clean import clean
from comment_analyzer.analysis.sentiment import preprocess
from comment_analyzer.analysis.summarize import SENTIMENTS
from comment_analyzer.errors import UserError
from comment_analyzer.models import Comment
from comment_analyzer.workers import CancelToken, ProgressFn

log = logging.getLogger(__name__)

MODE = "claude"
CHUNK = 200  # comments per sentiment request
MAX_TOKENS = 16_000  # per request, thinking included: the most one answer can cost
EFFORT = "low"  # reading the mood of comments needs little thinking
TEXT_LIMIT = 1_000  # characters per comment
TOPIC_COMMENTS = 1_500  # the topic request reads at most this many, most liked first
MIN_TOPIC_COMMENTS = 30
PRICES_AS_OF = "November 2026"


@dataclass(frozen=True, slots=True)
class ClaudeModel:
    id: str
    name: str
    input_price: float  # US dollars per million tokens
    output_price: float
    long_prompt: int | None = None  # prompts longer than this are priced higher
    long_input_price: float = 0.0
    long_output_price: float = 0.0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        long = self.long_prompt is not None and input_tokens > self.long_prompt
        input_price = self.long_input_price if long else self.input_price
        output_price = self.long_output_price if long else self.output_price
        return (input_tokens * input_price + output_tokens * output_price) / 1e6


MODELS = (
    ClaudeModel("claude-opus-5-5", "Claude Opus 5.5 (most capable)", 4.0, 20.0),
    ClaudeModel("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.0, 10.0),
    ClaudeModel("claude-haiku-5-5", "Claude Haiku 5.5 (cheapest)", 0.10, 0.50, 100_000, 0.50, 2.50),
)
DEFAULT_MODEL = MODELS[0].id


def model_by_id(model_id: str) -> ClaudeModel:
    return next((m for m in MODELS if m.id == model_id), MODELS[0])


SYSTEM = (
    "You read the public comments on a social media post for its creator, who wants an honest "
    "picture of how people reacted. Comments can be in any language, or mix languages; read "
    'each in its own language. Each comment is numbered. Usernames were replaced by "@user" '
    'and links by "http".'
)
SENTIMENT_TASK = (
    "For every numbered comment, give its sentiment towards the post or its subject: "
    '"positive", "neutral" or "negative". Sarcasm counts as what it means. Questions and plain '
    'information are "neutral". Set "sure" to false when a comment is too short, unclear or '
    "mixed to judge. Answer for every number, once."
)
TOPIC_TASK = (
    "Group the comments by what they talk about. Give at most 8 topics, biggest first, each "
    'with a "label" of 2 to 5 words in English, a one-sentence "summary" of what those comments '
    'say (without quoting or naming anyone), and the "comments" numbers in it. A comment '
    "belongs to one topic at most; leave out comments that fit none, and topics with fewer "
    "than 3 comments."
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SENTIMENT_SCHEMA = _object(
    {
        "comments": {
            "type": "array",
            "items": _object(
                {
                    "n": {"type": "integer"},
                    "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
                    "sure": {"type": "boolean"},
                }
            ),
        }
    }
)
TOPIC_SCHEMA = _object(
    {
        "topics": {
            "type": "array",
            "items": _object(
                {
                    "label": {"type": "string"},
                    "summary": {"type": "string"},
                    "comments": {"type": "array", "items": {"type": "integer"}},
                }
            ),
        }
    }
)


class MessagesApi(Protocol):
    """The part of anthropic.Anthropic().messages this module uses."""

    def create(self, **params: Any) -> Any: ...

    def count_tokens(self, **params: Any) -> Any: ...


def make_client(api_key: str) -> MessagesApi:
    import anthropic  # only Claude mode needs it

    client = anthropic.Anthropic(
        api_key=api_key, max_retries=3, timeout=anthropic.Timeout(300.0, connect=15.0)
    )
    return cast(MessagesApi, client.messages)  # its overloads are stricter than needed


@dataclass(slots=True)
class Request:
    kind: str  # "sentiment" or "topics"
    comment_ids: list[int]  # comment row IDs, numbered from 1 in this order
    task: str
    text: str
    schema: dict[str, Any]
    input_tokens: int = 0  # counted by Anthropic before asking the user

    def params(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": f"{self.task}\n\n{self.text}"}],
            "output_config": {
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": self.schema},
            },
        }

    @property
    def expected_output(self) -> int:
        """A rough guess: the JSON answer plus some thinking."""
        per_comment = 20 if self.kind == "sentiment" else 5
        return 1_000 + per_comment * len(self.comment_ids)


@dataclass(slots=True)
class Plan:
    model: ClaudeModel
    requests: list[Request]

    @property
    def comments(self) -> int:
        return sum(len(r.comment_ids) for r in self.requests if r.kind == "sentiment")

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.requests)

    def expected_cost(self) -> float:
        return sum(self.model.cost(r.input_tokens, r.expected_output) for r in self.requests)

    def max_cost(self) -> float:
        return sum(self.model.cost(r.input_tokens, MAX_TOKENS) for r in self.requests)


def make_plan(
    comments: list[Comment], spam: set[int], model: ClaudeModel, *, topics: bool = True
) -> Plan:
    """spam: comment IDs the local rules flagged; they're left out of the topics."""
    usable = [c for c in comments if c.text and c.id is not None]
    requests = [
        _request("sentiment", usable[start : start + CHUNK], SENTIMENT_TASK, SENTIMENT_SCHEMA)
        for start in range(0, len(usable), CHUNK)
    ]
    grouped = [c for c in usable if c.id not in spam]
    if topics and len(grouped) >= MIN_TOPIC_COMMENTS:
        liked = sorted(grouped, key=lambda c: -(c.like_count or 0))[:TOPIC_COMMENTS]
        requests.append(_request("topics", liked, TOPIC_TASK, TOPIC_SCHEMA))
    return Plan(model, requests)


def _request(kind: str, comments: list[Comment], task: str, schema: dict[str, Any]) -> Request:
    lines = []
    for n, comment in enumerate(comments, start=1):
        text = " ".join(preprocess(clean(comment.text or "")).split())[:TEXT_LIMIT]
        lines.append(f"{n}. {text}")
    ids = [c.id for c in comments if c.id is not None]
    return Request(kind, ids, task, "\n".join(lines), schema)


def count_tokens(api: MessagesApi, plan: Plan, token: CancelToken) -> None:
    """Fill in each request's input tokens. Counting is free."""
    for request in plan.requests:
        token.check()
        result = _call(lambda r=request: api.count_tokens(**r.params(plan.model.id)))
        request.input_tokens = int(result.input_tokens)


@dataclass(slots=True)
class ClaudeTopic:
    label: str
    summary: str
    comment_ids: list[int]


@dataclass(slots=True)
class Outcome:
    model: str
    sentiments: dict[int, tuple[str, bool]] = field(default_factory=dict)  # by comment ID
    topics: list[ClaudeTopic] = field(default_factory=list)
    topic_base: int = 0  # comments the topic request read
    skipped: int = 0  # comments Claude gave no answer for
    cost: float = 0.0


def run(api: MessagesApi, plan: Plan, progress: ProgressFn, token: CancelToken) -> Outcome:
    outcome = Outcome(plan.model.id)
    total = len(plan.requests)
    for done, request in enumerate(plan.requests):
        token.check()
        progress(done, total, f"Claude is reading the comments ({done + 1} of {total})...")
        params = request.params(plan.model.id) | {"max_tokens": MAX_TOKENS}
        response = _call(lambda p=params: api.create(**p))
        usage = response.usage
        outcome.cost += plan.model.cost(usage.input_tokens, usage.output_tokens)
        answer = _answer(response)
        if answer is None:
            if request.kind == "sentiment":
                outcome.skipped += len(request.comment_ids)
            continue
        number = dict(enumerate(request.comment_ids, start=1))
        if request.kind == "sentiment":
            for item in answer.get("comments", []):
                if item.get("n") in number and item.get("sentiment") in SENTIMENTS:
                    outcome.sentiments[number[item["n"]]] = (item["sentiment"], bool(item["sure"]))
            answered = {i for i in request.comment_ids if i in outcome.sentiments}
            outcome.skipped += len(request.comment_ids) - len(answered)
        else:
            outcome.topic_base = len(request.comment_ids)
            outcome.topics = _topics(answer, number)
    progress(total, total, "Claude has finished.")
    return outcome


def _topics(answer: dict[str, Any], number: dict[int, int]) -> list[ClaudeTopic]:
    topics, used = [], set()
    for item in answer.get("topics", [])[:8]:
        ids = [number[n] for n in item.get("comments", []) if n in number and number[n] not in used]
        used.update(ids)
        if ids:
            topics.append(ClaudeTopic(str(item["label"])[:80], str(item["summary"])[:400], ids))
    return sorted(topics, key=lambda t: -len(t.comment_ids))


def _answer(response: Any) -> dict[str, Any] | None:
    """The JSON answer, or None when Claude declined or ran out of room."""
    if response.stop_reason != "end_turn":
        log.warning("Claude stopped early: %s", response.stop_reason)
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        answer = json.loads(text)
    except ValueError:
        log.warning("Claude's answer wasn't valid JSON")
        return None
    return answer if isinstance(answer, dict) else None


def _call(send: Any) -> Any:
    """Send one request; turn the SDK's errors into messages for the user."""
    import anthropic

    try:
        return send()
    except anthropic.AuthenticationError as e:
        raise UserError("Anthropic refused the API key. Check it in Settings, under Claude.") from e
    except anthropic.PermissionDeniedError as e:
        raise UserError("This Anthropic API key isn't allowed to use that model.") from e
    except anthropic.RateLimitError as e:
        raise UserError("Anthropic is limiting requests right now. Try again in a minute.") from e
    except anthropic.BadRequestError as e:
        if "credit" in str(e).lower():
            raise UserError(
                "Your Anthropic account has run out of credit. Add credit at "
                "console.anthropic.com, then try again."
            ) from e
        log.exception("Claude request rejected")
        raise UserError("Anthropic rejected the request. The details are in the log.") from e
    except anthropic.APIConnectionError as e:
        raise UserError("Couldn't reach Anthropic. Check your internet connection.") from e
    except anthropic.APIStatusError as e:
        log.exception("Claude request failed")
        raise UserError(f"Anthropic's service had a problem (error {e.status_code}).") from e


def check_key(api_key: str) -> None:
    """Counting tokens is free, so it's a no-cost way to test a key."""
    api = make_client(api_key)
    _call(
        lambda: api.count_tokens(
            model=DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}]
        )
    )
