"""Tests for webhook retry/backoff primitives.

Per Traceability Matrix WH-001: tests/test_webhook_retry.py::test_exponential_backoff
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from idis.services.webhooks.retry import (
    DEFAULT_BASE_SECONDS,
    DEFAULT_CAP_SECONDS,
    MAX_ATTEMPTS,
    MAX_WINDOW_SECONDS,
    RetryState,
    compute_backoff_seconds,
    get_retry_schedule,
    is_retry_exhausted,
    next_attempt_at,
    total_retry_window_seconds,
)


class TestComputeBackoffSeconds:
    """Tests for compute_backoff_seconds function."""

    def test_exponential_backoff(self) -> None:
        """Required test per Traceability Matrix WH-001.

        Verifies exponential backoff: base * 2^attempt_index.
        """
        base = DEFAULT_BASE_SECONDS

        assert compute_backoff_seconds(0) == base * 1
        assert compute_backoff_seconds(1) == base * 2
        assert compute_backoff_seconds(2) == base * 4
        assert compute_backoff_seconds(3) == base * 8
        assert compute_backoff_seconds(4) == base * 16

    def test_first_retry_is_base_delay(self) -> None:
        """First retry (attempt_index=0) uses base delay."""
        assert compute_backoff_seconds(0) == DEFAULT_BASE_SECONDS

    def test_exponential_growth(self) -> None:
        """Each attempt doubles the delay."""
        prev = compute_backoff_seconds(0)
        for i in range(1, 5):
            curr = compute_backoff_seconds(i)
            assert curr == prev * 2 or curr == DEFAULT_CAP_SECONDS
            prev = curr

    def test_cap_applied(self) -> None:
        """Delay is capped at cap_seconds."""
        large_index = 20
        result = compute_backoff_seconds(large_index)

        assert result == DEFAULT_CAP_SECONDS

    def test_custom_base_and_cap(self) -> None:
        """Custom base and cap values work correctly."""
        base = 30
        cap = 120

        assert compute_backoff_seconds(0, base_seconds=base, cap_seconds=cap) == 30
        assert compute_backoff_seconds(1, base_seconds=base, cap_seconds=cap) == 60
        assert compute_backoff_seconds(2, base_seconds=base, cap_seconds=cap) == 120
        assert compute_backoff_seconds(3, base_seconds=base, cap_seconds=cap) == 120

    def test_negative_attempt_returns_zero(self) -> None:
        """Negative attempt index returns 0."""
        assert compute_backoff_seconds(-1) == 0
        assert compute_backoff_seconds(-100) == 0

    def test_deterministic_without_jitter(self) -> None:
        """Without jitter, results are deterministic."""
        results = [compute_backoff_seconds(3, jitter=False) for _ in range(100)]

        assert all(r == results[0] for r in results)

    def test_jitter_adds_variability(self) -> None:
        """With jitter enabled, results vary."""
        results = {compute_backoff_seconds(5, jitter=True) for _ in range(100)}

        assert len(results) > 1

    def test_jitter_within_bounds(self) -> None:
        """Jitter stays within 10% of base delay."""
        base_delay = compute_backoff_seconds(3, jitter=False)

        for _ in range(100):
            jittered = compute_backoff_seconds(3, jitter=True)
            assert base_delay <= jittered <= base_delay * 1.1


class TestNextAttemptAt:
    """Tests for next_attempt_at function."""

    def test_first_retry_scheduled(self) -> None:
        """After first attempt, next retry is scheduled."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = next_attempt_at(now, attempt_count=1)

        assert result is not None
        assert result == now + timedelta(seconds=DEFAULT_BASE_SECONDS)

    def test_exhausted_returns_none(self) -> None:
        """When attempts exhausted, returns None."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = next_attempt_at(now, attempt_count=MAX_ATTEMPTS)

        assert result is None

    def test_beyond_max_returns_none(self) -> None:
        """Beyond max attempts also returns None."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = next_attempt_at(now, attempt_count=MAX_ATTEMPTS + 5)

        assert result is None

    def test_increasing_delays(self) -> None:
        """Subsequent retries have increasing delays."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        delays = []

        for attempt in range(1, MAX_ATTEMPTS):
            result = next_attempt_at(now, attempt)
            if result:
                delays.append((result - now).total_seconds())

        for i in range(1, len(delays) - 1):
            assert delays[i] >= delays[i - 1]


class TestIsRetryExhausted:
    """Tests for is_retry_exhausted function."""

    def test_not_exhausted_at_zero(self) -> None:
        """Zero attempts is not exhausted."""
        assert not is_retry_exhausted(0)

    def test_not_exhausted_below_max(self) -> None:
        """Below max attempts is not exhausted."""
        assert not is_retry_exhausted(MAX_ATTEMPTS - 1)

    def test_exhausted_at_max(self) -> None:
        """At max attempts is exhausted."""
        assert is_retry_exhausted(MAX_ATTEMPTS)

    def test_exhausted_above_max(self) -> None:
        """Above max attempts is exhausted."""
        assert is_retry_exhausted(MAX_ATTEMPTS + 1)


class TestGetRetrySchedule:
    """Tests for get_retry_schedule function."""

    def test_returns_list_of_correct_length(self) -> None:
        """Schedule has MAX_ATTEMPTS entries."""
        schedule = get_retry_schedule()

        assert len(schedule) == MAX_ATTEMPTS

    def test_schedule_matches_compute_backoff(self) -> None:
        """Schedule entries match compute_backoff_seconds."""
        schedule = get_retry_schedule()

        for i, delay in enumerate(schedule):
            assert delay == compute_backoff_seconds(i)

    def test_custom_params_applied(self) -> None:
        """Custom base and cap are applied to schedule."""
        schedule = get_retry_schedule(base_seconds=10, cap_seconds=100)

        assert schedule[0] == 10
        assert schedule[-1] <= 100


class TestTotalRetryWindowSeconds:
    """Tests for total_retry_window_seconds function."""

    def test_total_within_24_hours(self) -> None:
        """Default total window is under 24 hours."""
        total = total_retry_window_seconds()

        assert total < MAX_WINDOW_SECONDS

    def test_raises_if_exceeds_24_hours(self) -> None:
        """Raises ValueError if window exceeds 24 hours."""
        with pytest.raises(ValueError, match="exceeds 24h"):
            total_retry_window_seconds(base_seconds=10000, cap_seconds=50000)


class TestMaxAttemptsConstant:
    """Tests for MAX_ATTEMPTS constant."""

    def test_max_attempts_is_ten(self) -> None:
        """MAX_ATTEMPTS is 10 per API Contracts §6.1."""
        assert MAX_ATTEMPTS == 10


class TestRetryState:
    """Tests for RetryState dataclass."""

    def test_default_values(self) -> None:
        """RetryState has sensible defaults."""
        state = RetryState(webhook_id="wh-1", event_id="evt-1")

        assert state.webhook_id == "wh-1"
        assert state.event_id == "evt-1"
        assert state.attempt_count == 0
        assert state.next_attempt_at is None
        assert state.last_attempt_at is None
        assert state.last_error is None
        assert state.exhausted is False

    def test_frozen(self) -> None:
        """RetryState is immutable."""
        state = RetryState(webhook_id="wh-1", event_id="evt-1")

        with pytest.raises(AttributeError):
            state.attempt_count = 5  # type: ignore[misc]


class TestBackoffScheduleDocumentation:
    """Tests verifying documented backoff schedule."""

    def test_documented_schedule_matches_implementation(self) -> None:
        """Verify the schedule documented in module docstring."""
        schedule = get_retry_schedule()

        assert schedule[0] == 60
        assert schedule[1] == 120
        assert schedule[2] == 240
        assert schedule[3] == 480
        assert schedule[4] == 960
        assert schedule[5] == 1920
        assert schedule[6] == 3840
        assert schedule[7] == 7680
        assert schedule[8] == 14400
        assert schedule[9] == 14400
