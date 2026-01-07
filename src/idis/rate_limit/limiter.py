"""Token bucket rate limiter for IDIS API.

Implements tenant-scoped rate limiting per v6.3 API contracts:
- User tier: 600 req/min (default), burst 2x
- Integration tier: 1200 req/min (default), burst 2x

Uses monotonic time and integer arithmetic to avoid float drift.
Thread-safe for concurrent access within a single process.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Final

logger = logging.getLogger(__name__)

ENV_RATE_LIMIT_USER_RPM: Final[str] = "IDIS_RATE_LIMIT_USER_RPM"
ENV_RATE_LIMIT_INTEGRATION_RPM: Final[str] = "IDIS_RATE_LIMIT_INTEGRATION_RPM"
ENV_RATE_LIMIT_BURST_MULTIPLIER: Final[str] = "IDIS_RATE_LIMIT_BURST_MULTIPLIER"

DEFAULT_USER_RPM: Final[int] = 600
DEFAULT_INTEGRATION_RPM: Final[int] = 1200
DEFAULT_BURST_MULTIPLIER: Final[int] = 2

NANOSECONDS_PER_SECOND: Final[int] = 1_000_000_000
SECONDS_PER_MINUTE: Final[int] = 60


class RateLimitTier(str, Enum):
    """Rate limit tier classification."""

    USER = "user"
    INTEGRATION = "integration"


class RateLimitConfigError(Exception):
    """Raised when rate limit configuration is invalid."""


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limiting configuration (immutable).

    Attributes:
        user_rpm: Requests per minute for user tier.
        integration_rpm: Requests per minute for integration tier.
        burst_multiplier: Multiplier for burst capacity (capacity = rpm * burst_multiplier).
    """

    user_rpm: int
    integration_rpm: int
    burst_multiplier: int

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.user_rpm <= 0:
            raise RateLimitConfigError(
                f"IDIS_RATE_LIMIT_USER_RPM must be a positive integer, got {self.user_rpm}"
            )
        if self.integration_rpm <= 0:
            raise RateLimitConfigError(
                f"IDIS_RATE_LIMIT_INTEGRATION_RPM must be a positive integer, "
                f"got {self.integration_rpm}"
            )
        if self.burst_multiplier <= 0:
            raise RateLimitConfigError(
                f"IDIS_RATE_LIMIT_BURST_MULTIPLIER must be a positive integer, "
                f"got {self.burst_multiplier}"
            )

    def get_rpm(self, tier: RateLimitTier) -> int:
        """Get RPM limit for the specified tier."""
        if tier == RateLimitTier.INTEGRATION:
            return self.integration_rpm
        return self.user_rpm

    def get_capacity(self, tier: RateLimitTier) -> int:
        """Get bucket capacity for the specified tier (rpm * burst_multiplier)."""
        return self.get_rpm(tier) * self.burst_multiplier


def _parse_positive_int(env_var: str, default: int) -> int:
    """Parse a positive integer from environment variable.

    Args:
        env_var: Environment variable name.
        default: Default value if env var is not set.

    Returns:
        Parsed positive integer.

    Raises:
        RateLimitConfigError: If value is set but not a positive integer.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return default

    raw = raw.strip()
    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError as e:
        raise RateLimitConfigError(f"{env_var} must be a positive integer, got '{raw}'") from e

    if value <= 0:
        raise RateLimitConfigError(f"{env_var} must be a positive integer, got {value}")

    return value


def load_rate_limit_config() -> RateLimitConfig:
    """Load rate limit configuration from environment variables.

    Environment variables:
        IDIS_RATE_LIMIT_USER_RPM: User tier requests per minute (default: 600)
        IDIS_RATE_LIMIT_INTEGRATION_RPM: Integration tier RPM (default: 1200)
        IDIS_RATE_LIMIT_BURST_MULTIPLIER: Burst multiplier (default: 2)

    Returns:
        RateLimitConfig with validated values.

    Raises:
        RateLimitConfigError: If any value is invalid (not a positive integer).
    """
    user_rpm = _parse_positive_int(ENV_RATE_LIMIT_USER_RPM, DEFAULT_USER_RPM)
    integration_rpm = _parse_positive_int(ENV_RATE_LIMIT_INTEGRATION_RPM, DEFAULT_INTEGRATION_RPM)
    burst_multiplier = _parse_positive_int(
        ENV_RATE_LIMIT_BURST_MULTIPLIER, DEFAULT_BURST_MULTIPLIER
    )

    return RateLimitConfig(
        user_rpm=user_rpm,
        integration_rpm=integration_rpm,
        burst_multiplier=burst_multiplier,
    )


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of a rate limit check.

    Attributes:
        allowed: Whether the request is allowed.
        retry_after_seconds: Seconds until a token becomes available (None if allowed).
        remaining_tokens: Approximate tokens remaining after this request.
        limit_rpm: The RPM limit for this tier.
        burst_multiplier: The burst multiplier applied.
        tier: The rate limit tier applied.
    """

    allowed: bool
    retry_after_seconds: int | None
    remaining_tokens: int
    limit_rpm: int
    burst_multiplier: int
    tier: RateLimitTier


class _TokenBucket:
    """Token bucket for a single (tenant_id, tier) pair.

    Uses integer nanoseconds for time tracking to avoid float precision issues.
    Thread-safe via lock.
    """

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        """Initialize bucket.

        Args:
            capacity: Maximum tokens (rpm * burst_multiplier).
            refill_rate_per_sec: Tokens added per second (rpm / 60).
        """
        self._capacity = capacity
        self._refill_rate_ns = int(refill_rate_per_sec * NANOSECONDS_PER_SECOND)
        self._tokens_ns = capacity * NANOSECONDS_PER_SECOND
        self._last_refill_ns = time.monotonic_ns()
        self._lock = threading.Lock()

    def try_consume(self, cost: int = 1) -> tuple[bool, int, int]:
        """Try to consume tokens from the bucket.

        Args:
            cost: Number of tokens to consume (default 1).

        Returns:
            Tuple of (allowed, retry_after_seconds, remaining_tokens).
            retry_after_seconds is 0 if allowed, >= 1 if denied.
        """
        cost_ns = cost * NANOSECONDS_PER_SECOND

        with self._lock:
            now_ns = time.monotonic_ns()
            elapsed_ns = now_ns - self._last_refill_ns

            if elapsed_ns > 0 and self._refill_rate_ns > 0:
                refill_ns = (elapsed_ns * self._refill_rate_ns) // NANOSECONDS_PER_SECOND
                self._tokens_ns = min(
                    self._capacity * NANOSECONDS_PER_SECOND,
                    self._tokens_ns + refill_ns,
                )
                self._last_refill_ns = now_ns

            if self._tokens_ns >= cost_ns:
                self._tokens_ns -= cost_ns
                remaining = self._tokens_ns // NANOSECONDS_PER_SECOND
                return (True, 0, remaining)

            deficit_ns = cost_ns - self._tokens_ns
            if self._refill_rate_ns > 0:
                wait_ns = (deficit_ns * NANOSECONDS_PER_SECOND) // self._refill_rate_ns
                retry_after_sec = max(
                    1, (wait_ns + NANOSECONDS_PER_SECOND - 1) // NANOSECONDS_PER_SECOND
                )
            else:
                retry_after_sec = 60

            remaining = self._tokens_ns // NANOSECONDS_PER_SECOND
            return (False, int(retry_after_sec), remaining)


class TenantRateLimiter:
    """Tenant-scoped rate limiter using token buckets.

    Creates separate buckets for each (tenant_id, tier) combination.
    Thread-safe for concurrent access.
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        """Initialize the rate limiter.

        Args:
            config: Rate limit configuration. If None, loads from environment.

        Raises:
            RateLimitConfigError: If configuration is invalid.
        """
        if config is None:
            config = load_rate_limit_config()
        self._config = config
        self._buckets: dict[tuple[str, RateLimitTier], _TokenBucket] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> RateLimitConfig:
        """Get the rate limit configuration."""
        return self._config

    def _get_or_create_bucket(self, tenant_id: str, tier: RateLimitTier) -> _TokenBucket:
        """Get or create a token bucket for the given tenant and tier."""
        key = (tenant_id, tier)

        with self._lock:
            if key not in self._buckets:
                rpm = self._config.get_rpm(tier)
                capacity = self._config.get_capacity(tier)
                refill_rate = rpm / SECONDS_PER_MINUTE
                self._buckets[key] = _TokenBucket(capacity, refill_rate)
            return self._buckets[key]

    def check(self, tenant_id: str, tier: RateLimitTier) -> RateLimitDecision:
        """Check and consume a rate limit token for the given tenant and tier.

        Args:
            tenant_id: The tenant identifier.
            tier: The rate limit tier (user or integration).

        Returns:
            RateLimitDecision with the result.
        """
        bucket = self._get_or_create_bucket(tenant_id, tier)
        allowed, retry_after, remaining = bucket.try_consume(1)

        return RateLimitDecision(
            allowed=allowed,
            retry_after_seconds=retry_after if not allowed else None,
            remaining_tokens=remaining,
            limit_rpm=self._config.get_rpm(tier),
            burst_multiplier=self._config.burst_multiplier,
            tier=tier,
        )

    def reset(self) -> None:
        """Reset all buckets (useful for testing)."""
        with self._lock:
            self._buckets.clear()


def classify_tier(roles: frozenset[str]) -> RateLimitTier:
    """Classify rate limit tier based on actor roles.

    Args:
        roles: The actor's roles.

    Returns:
        RateLimitTier.INTEGRATION if INTEGRATION_SERVICE role present,
        otherwise RateLimitTier.USER.
    """
    if "INTEGRATION_SERVICE" in roles:
        return RateLimitTier.INTEGRATION
    return RateLimitTier.USER
