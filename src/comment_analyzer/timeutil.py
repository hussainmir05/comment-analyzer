"""Times are stored in UTC as ISO-8601 text, e.g. "2026-11-12T08:30:00Z"."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Now, in UTC, to the second."""
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Times must be timezone-aware; use UTC.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def from_iso(value: str | None) -> datetime | None:
    return None if value is None else parse_iso(value)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
