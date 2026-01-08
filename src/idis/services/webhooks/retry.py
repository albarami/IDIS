"""Webhook retry/backoff primitives for IDIS.

Implements deterministic exponential backoff per IDIS v6.3:
- API Contracts §6.1: 10 attempts over 24 hours with exponential backoff
- Traceability Matrix WH-001: test_exponential_backoff

Design:
- 10 total attempts (1 initial + 9 retries)
- Exponential backoff: base * 2^attempt_index
- Cap ensures total window ≤ 24 hours
- No jitter by default (deterministic for testing)
- Optional jitter flag for production use

Backoff schedule (default base=60s, cap=14400s/4h):
  Attempt 0:    0s (immediate)
  Attempt 1:   60s
  Attempt 2:  120s
  Attempt 3:  240s
  Attempt 4:  480s
  Attempt 5:  960s
  Attempt 6: 1920s
  Attempt 7: 3840s
  Attempt 8: 7680s
  Attempt 9: 14400s (capped)

Total window: ~8.5 hours with default settings, well under 24h limit.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

MAX_ATTEMPTS: Final[int] = 10
DEFAULT_BASE_SECONDS: Final[int] = 60
DEFAULT_CAP_SECONDS: Final[int] = 14400
MAX_WINDOW_SECONDS: Final[int] = 86400


@dataclass(frozen=True)
class RetryState:
    """State for tracking webhook delivery retry attempts.

    Attributes:
        webhook_id: UUID of the webhook subscription.
        event_id: UUID of the event being delivered.
        attempt_count: Number of attempts made (0 = not yet attempted).
        next_attempt_at: Scheduled time for next attempt (None if exhausted).
        last_attempt_at: Time of last attempt (None if not yet attempted).
        last_error: Error message from last failed attempt.
        exhausted: True if all retry attempts have been used.
    """

    webhook_id: str
    event_id: str
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    exhausted: bool = False


def compute_backoff_seconds(
    attempt_index: int,
    base_seconds: int = DEFAULT_BASE_SECONDS,
    cap_seconds: int = DEFAULT_CAP_SECONDS,
    jitter: bool = False,
) -> int:
    """Compute backoff delay in seconds for a given attempt index.

    Uses exponential backoff: base * 2^attempt_index, capped at cap_seconds.

    Args:
        attempt_index: Zero-based attempt index (0 = first retry after initial).
        base_seconds: Base delay in seconds (default 60).
        cap_seconds: Maximum delay cap in seconds (default 14400 = 4 hours).
        jitter: If True, add random jitter up to 10% of delay.

    Returns:
        Backoff delay in seconds for this attempt.

    Example:
        >>> compute_backoff_seconds(0)  # First retry
        60
        >>> compute_backoff_seconds(3)  # Fourth retry
        480
        >>> compute_backoff_seconds(10)  # Capped
        14400
    """
    if attempt_index < 0:
        return 0

    delay: int = base_seconds * (2**attempt_index)
    delay = min(delay, cap_seconds)

    if jitter:
        jitter_amount = int(delay * 0.1 * random.random())
        delay += jitter_amount

    return int(delay)


def next_attempt_at(
    now: datetime,
    attempt_count: int,
    base_seconds: int = DEFAULT_BASE_SECONDS,
    cap_seconds: int = DEFAULT_CAP_SECONDS,
    jitter: bool = False,
) -> datetime | None:
    """Compute the datetime for the next retry attempt.

    Args:
        now: Current datetime.
        attempt_count: Number of attempts already made (1 = initial attempt done).
        base_seconds: Base delay in seconds.
        cap_seconds: Maximum delay cap in seconds.
        jitter: If True, add random jitter.

    Returns:
        Datetime for next attempt, or None if attempts exhausted (>= MAX_ATTEMPTS).

    Example:
        >>> from datetime import datetime, UTC
        >>> now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        >>> next_attempt_at(now, 1)  # After first attempt
        datetime.datetime(2026, 1, 1, 12, 1, 0, tzinfo=datetime.timezone.utc)
    """
    if attempt_count >= MAX_ATTEMPTS:
        return None

    retry_index = attempt_count - 1 if attempt_count > 0 else 0
    delay_seconds = compute_backoff_seconds(
        attempt_index=retry_index,
        base_seconds=base_seconds,
        cap_seconds=cap_seconds,
        jitter=jitter,
    )

    return now + timedelta(seconds=delay_seconds)


def is_retry_exhausted(attempt_count: int) -> bool:
    """Check if retry attempts are exhausted.

    Args:
        attempt_count: Number of attempts made.

    Returns:
        True if attempt_count >= MAX_ATTEMPTS.
    """
    return attempt_count >= MAX_ATTEMPTS


def get_retry_schedule(
    base_seconds: int = DEFAULT_BASE_SECONDS,
    cap_seconds: int = DEFAULT_CAP_SECONDS,
) -> list[int]:
    """Get the full retry schedule as a list of delays.

    Useful for documentation and testing.

    Args:
        base_seconds: Base delay in seconds.
        cap_seconds: Maximum delay cap in seconds.

    Returns:
        List of delays for attempts 0 through MAX_ATTEMPTS-1.
    """
    return [
        compute_backoff_seconds(i, base_seconds, cap_seconds, jitter=False)
        for i in range(MAX_ATTEMPTS)
    ]


def total_retry_window_seconds(
    base_seconds: int = DEFAULT_BASE_SECONDS,
    cap_seconds: int = DEFAULT_CAP_SECONDS,
) -> int:
    """Compute total time window for all retry attempts.

    Args:
        base_seconds: Base delay in seconds.
        cap_seconds: Maximum delay cap in seconds.

    Returns:
        Total seconds from first attempt to last retry.

    Raises:
        ValueError: If total window exceeds 24 hours (design constraint).
    """
    schedule = get_retry_schedule(base_seconds, cap_seconds)
    total = sum(schedule)

    if total > MAX_WINDOW_SECONDS:
        raise ValueError(f"Total retry window {total}s exceeds 24h limit ({MAX_WINDOW_SECONDS}s)")

    return total
