"""Postgres pgvector extension health checks."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from idis.persistence.db import IDIS_DATABASE_URL_ENV

PGVECTOR_EXTENSION_NAME = "vector"


class PgvectorExtensionProbe(Protocol):
    """Callable that checks whether pgvector is available for a database URL."""

    def __call__(self, database_url: str) -> bool:
        """Return True when the pgvector extension is available."""


class PgvectorHealthStatus(StrEnum):
    """Safe pgvector health status values."""

    HEALTHY = "healthy"
    MISSING_CREDENTIALS = "missing_credentials"
    FAILED = "failed"


class PgvectorHealthCheck(BaseModel):
    """Sanitized pgvector health-check result."""

    model_config = ConfigDict(extra="forbid")

    status: PgvectorHealthStatus
    config_present: bool
    missing_env_vars: list[str] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def healthy(cls) -> PgvectorHealthCheck:
        """Return a successful health result."""
        return cls(
            status=PgvectorHealthStatus.HEALTHY,
            config_present=True,
            missing_env_vars=[],
        )

    @classmethod
    def missing(cls, *, missing_env_vars: list[str]) -> PgvectorHealthCheck:
        """Return a missing/partial configuration result."""
        return cls(
            status=PgvectorHealthStatus.MISSING_CREDENTIALS,
            config_present=False,
            missing_env_vars=missing_env_vars,
            error="Postgres database URL is not configured.",
        )

    @classmethod
    def failed(cls) -> PgvectorHealthCheck:
        """Return a sanitized failed health result."""
        return cls(
            status=PgvectorHealthStatus.FAILED,
            config_present=True,
            missing_env_vars=[],
            error="pgvector extension is not available on the configured Postgres database.",
        )


def _default_extension_probe(database_url: str) -> bool:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_extension
                    WHERE extname = :extension_name
                    """
                ),
                {"extension_name": PGVECTOR_EXTENSION_NAME},
            ).scalar_one_or_none()
            return row == 1
    except Exception:
        return False
    finally:
        engine.dispose()


def check_pgvector_health(
    *,
    env: Mapping[str, str] | None = None,
    extension_probe: Callable[[str], bool] | None = None,
) -> PgvectorHealthCheck:
    """Check pgvector availability on the configured Postgres database.

    Args:
        env: Environment mapping to inspect. Defaults to ``os.environ``.
        extension_probe: Injectable probe for unit tests.

    Returns:
        Sanitized pgvector health-check result.
    """
    values = os.environ if env is None else env
    database_url = str(values.get(IDIS_DATABASE_URL_ENV, "")).strip()
    if not database_url:
        return PgvectorHealthCheck.missing(missing_env_vars=[IDIS_DATABASE_URL_ENV])

    probe = extension_probe or _default_extension_probe
    if not probe(database_url):
        return PgvectorHealthCheck.failed()
    return PgvectorHealthCheck.healthy()
