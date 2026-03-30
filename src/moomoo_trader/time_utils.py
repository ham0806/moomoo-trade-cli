from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

_KNOWN_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
)


def now_local() -> datetime:
    return datetime.now()


def today_text(now: Optional[datetime] = None) -> str:
    return (now or now_local()).strftime("%Y-%m-%d")


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text or text == "N/A":
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in _KNOWN_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def to_iso(value: Any) -> Optional[str]:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat()
