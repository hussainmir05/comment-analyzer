"""Commenter IDs are stored only as keyed hashes (HMAC-SHA256), never as names.

The key is random, made once per install and kept in Windows Credential Manager. The same
commenter gets the same hash on every run, so repeat and unique commenters can be counted, but
the hash can't be turned back into an ID. It is pseudonymous rather than anonymous: whoever
holds the key can check whether a known ID matches a hash.
"""

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable

from comment_analyzer import config

log = logging.getLogger(__name__)

AUTHOR_KEY = "author_hash_key"


def author_hasher() -> Callable[[str | None], str | None]:
    """A function that hashes a commenter's platform ID (None stays None)."""
    secret = _load_key()

    def hash_author(platform_user_id: str | None) -> str | None:
        if not platform_user_id:
            return None
        return hmac.new(secret, platform_user_id.encode("utf-8"), hashlib.sha256).hexdigest()

    return hash_author


def _load_key() -> bytes:
    stored = config.get_secret(AUTHOR_KEY)
    if stored:
        try:
            return bytes.fromhex(stored)
        except ValueError:
            log.warning("The stored commenter-hash key is damaged; making a new one")
    key = secrets.token_hex(32)
    config.set_secret(AUTHOR_KEY, key)
    return bytes.fromhex(key)
