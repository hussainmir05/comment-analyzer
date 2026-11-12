"""Turn a debug capture into a test fixture with no real people in it.

    python scripts/sanitize_fixture.py <capture.json> <fixture.json>

Debug captures are saved in the data folder's debug/<site>/ when "Save what the in-app browser
collects" is on in Settings. This script keeps the structure, counts and times the parsers need,
and replaces:
- names, usernames and nicknames with "person 1", "person 2", ...;
- user IDs with stable fake numbers;
- comment and caption texts with "text 1", "text 2", ... (--keep-text keeps them, for captures
  of your own test comments);
- profile pictures and other links with "https://example.invalid/".

Read the result before committing it.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comment_analyzer.platforms.web_data import json_documents

NAME_KEYS = {"name", "username", "full_name", "nickname", "unique_id", "uniqueId", "short_name"}
ID_KEYS = {"id", "pk", "uid", "sec_uid", "secUid", "strong_id__", "fbid"}
USER_KEYS = {"user", "author", "owner", "actors", "commenter", "from"}
TEXT_KEYS = {"text", "desc", "message"}
LINK_HINTS = ("http://", "https://")


class Sanitizer:
    def __init__(self, keep_text: bool) -> None:
        self.keep_text = keep_text
        self.names: dict[str, str] = {}
        self.ids: dict[str, str] = {}
        self.texts = 0

    def clean(self, node: Any, in_user: bool = False) -> Any:
        if isinstance(node, list):
            return [self.clean(item, in_user) for item in node]
        if not isinstance(node, dict):
            return self._value(node)
        result = {}
        for key, value in node.items():
            if key in NAME_KEYS and isinstance(value, str):
                result[key] = self.names.setdefault(value, f"person {len(self.names) + 1}")
            elif in_user and key in ID_KEYS and isinstance(value, (str, int)):
                fake = self.ids.setdefault(str(value), str(900_000_000 + len(self.ids)))
                result[key] = fake if isinstance(value, str) else int(fake)
            elif key in TEXT_KEYS and isinstance(value, str) and not self.keep_text:
                self.texts += 1
                result[key] = f"text {self.texts}"
            else:
                result[key] = self.clean(value, in_user or key in USER_KEYS)
        return result

    def _value(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith(LINK_HINTS):
            return "https://example.invalid/"
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--keep-text", action="store_true")
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    sanitizer = Sanitizer(args.keep_text)
    documents = [sanitizer.clean(d) for d in json_documents(capture["body"])]
    lines = [json.dumps(d, ensure_ascii=False) for d in documents]
    args.fixture.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.fixture}: {len(documents)} document(s), {len(sanitizer.names)} names and "
        f"{len(sanitizer.ids)} user IDs replaced, {sanitizer.texts} texts replaced."
    )


if __name__ == "__main__":
    main()
