"""When a failed request is tried again, and after how long."""

from __future__ import annotations

import math
import random
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

__all__ = ["backoff", "retry_after_seconds", "retryable"]

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
# 429s that waiting a few seconds does not fix
NO_RETRY_429 = frozenset({"monthly_quota_exceeded", "daily_quota_exceeded", "thread_reply_limit"})
MAX_RETRY_AFTER = 60.0
# a decimal number of seconds, as JavaScript's Number() reads it (no 1_000, no inf)
_NUMBER = re.compile(r"\s*[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\s*")


def retryable(status: int, code: str, safe: bool) -> bool:
    """Whether an error answer is worth another attempt.

    ``safe`` says whether repeating the request cannot do the work twice: GET,
    PUT and DELETE, and operations that carry an Idempotency-Key.
    """
    if status == 429:
        return code not in NO_RETRY_429  # refused before any work: always safe
    if status == 409:
        return code == "idempotency_request_in_progress"
    return safe and status in RETRY_STATUSES


def backoff(attempt: int, retry_after: Optional[float]) -> float:
    """Seconds to wait before attempt number ``attempt + 1``.

    ``Retry-After`` wins, capped at 60 s; without it the wait doubles from
    0.5 s up to 8 s, with jitter over its upper half.
    """
    if retry_after is not None:
        return min(retry_after, MAX_RETRY_AFTER)
    base = min(0.5 * (1 << min(attempt, 16)), 8.0)
    return base / 2 + random.random() * (base / 2)


def retry_after_seconds(headers: Optional[httpx.Headers]) -> Optional[float]:
    """The Retry-After header in seconds (a number or an HTTP date), or None."""
    value = headers.get("retry-after") if headers is not None else None
    if not value:
        return None
    # never raises, whatever the header says: an unusable value is None
    seconds = float(value) if _NUMBER.fullmatch(value) else math.nan
    if math.isfinite(seconds):
        return max(0.0, seconds)
    try:
        at = parsedate_to_datetime(value)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(0.0, at.timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
