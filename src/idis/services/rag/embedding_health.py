"""Live embedding provider configuration and health checks."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from idis.services.rag.constants import (
    ALLOWED_EMBEDDING_BACKENDS,
    VECTOR_EMBEDDING_DIMENSIONS,
)

IDIS_ENABLE_VECTOR_SEARCH_ENV = "IDIS_ENABLE_VECTOR_SEARCH"
IDIS_EMBEDDING_BACKEND_ENV = "IDIS_EMBEDDING_BACKEND"
IDIS_EMBEDDING_MODEL_ENV = "IDIS_EMBEDDING_MODEL"
IDIS_EMBEDDING_DIMENSIONS_ENV = "IDIS_EMBEDDING_DIMENSIONS"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

DEFAULT_EMBEDDING_BACKEND = "openai"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_HEALTH_PROBE_INPUT = "idis-embedding-health-check"

_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s]+")


class EmbeddingClientFactory(Protocol):
    """Factory for injectable OpenAI-compatible embedding clients."""

    def __call__(self, api_key: str) -> Any:
        """Return an embedding client for health checks."""


class EmbeddingHealthStatus(StrEnum):
    """Safe embedding health status values."""

    HEALTHY = "healthy"
    MISSING_CREDENTIALS = "missing_credentials"
    FAILED = "failed"


class EmbeddingHealthCheck(BaseModel):
    """Sanitized embedding provider health-check result."""

    model_config = ConfigDict(extra="forbid")

    status: EmbeddingHealthStatus
    config_present: bool
    backend: str | None = None
    model: str | None = None
    dimensions: int | None = None
    missing_env_vars: list[str] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def healthy(cls, *, model: str, dimensions: int) -> EmbeddingHealthCheck:
        """Return a successful health result."""
        return cls(
            status=EmbeddingHealthStatus.HEALTHY,
            config_present=True,
            backend=DEFAULT_EMBEDDING_BACKEND,
            model=model,
            dimensions=dimensions,
            missing_env_vars=[],
        )

    @classmethod
    def missing(cls, *, missing_env_vars: list[str]) -> EmbeddingHealthCheck:
        """Return a missing/partial configuration result."""
        return cls(
            status=EmbeddingHealthStatus.MISSING_CREDENTIALS,
            config_present=False,
            missing_env_vars=missing_env_vars,
            error="Embedding configuration is incomplete.",
        )

    @classmethod
    def failed(cls, *, error: str | None = None) -> EmbeddingHealthCheck:
        """Return a sanitized failed health result."""
        return cls(
            status=EmbeddingHealthStatus.FAILED,
            config_present=True,
            error=_sanitize_error(error) or "Embedding health check failed.",
        )


def is_vector_search_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether vector search is enabled via environment flag."""
    values = os.environ if env is None else env
    raw = str(values.get(IDIS_ENABLE_VECTOR_SEARCH_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _sanitize_error(error: str | None) -> str | None:
    if error is None:
        return None
    sanitized = _SECRET_PATTERN.sub("[redacted]", error)
    sanitized = _WINDOWS_PATH_PATTERN.sub("[redacted]", sanitized)
    return sanitized[:240]


def _truthy_env(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _required_embedding_env(env: Mapping[str, str]) -> list[str]:
    missing: list[str] = []
    if not _truthy_env(str(env.get(IDIS_ENABLE_VECTOR_SEARCH_ENV, ""))):
        missing.append(IDIS_ENABLE_VECTOR_SEARCH_ENV)
    backend = str(env.get(IDIS_EMBEDDING_BACKEND_ENV, DEFAULT_EMBEDDING_BACKEND)).strip().lower()
    if not backend:
        missing.append(IDIS_EMBEDDING_BACKEND_ENV)
    if not str(env.get(OPENAI_API_KEY_ENV, "")).strip():
        missing.append(OPENAI_API_KEY_ENV)
    if not str(env.get(IDIS_EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL)).strip():
        missing.append(IDIS_EMBEDDING_MODEL_ENV)
    if not str(env.get(IDIS_EMBEDDING_DIMENSIONS_ENV, str(VECTOR_EMBEDDING_DIMENSIONS))).strip():
        missing.append(IDIS_EMBEDDING_DIMENSIONS_ENV)
    return missing


def _parse_dimensions(env: Mapping[str, str]) -> int | None:
    raw = str(env.get(IDIS_EMBEDDING_DIMENSIONS_ENV, str(VECTOR_EMBEDDING_DIMENSIONS))).strip()
    try:
        dimensions = int(raw)
    except ValueError:
        return None
    if dimensions <= 0:
        return None
    return dimensions


def _default_openai_client_factory(api_key: str) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=api_key)


def check_embedding_health(
    *,
    env: Mapping[str, str] | None = None,
    client_factory: EmbeddingClientFactory | None = None,
) -> EmbeddingHealthCheck:
    """Check live embedding provider readiness without exposing secrets.

    Args:
        env: Environment mapping to inspect. Defaults to ``os.environ``.
        client_factory: Injectable OpenAI client constructor for unit tests.

    Returns:
        Sanitized embedding health-check result.
    """
    values = os.environ if env is None else env
    missing = _required_embedding_env(values)
    if missing:
        return EmbeddingHealthCheck.missing(missing_env_vars=missing)

    backend = str(values.get(IDIS_EMBEDDING_BACKEND_ENV, DEFAULT_EMBEDDING_BACKEND)).strip().lower()
    if backend not in ALLOWED_EMBEDDING_BACKENDS:
        if backend == "deterministic":
            return EmbeddingHealthCheck.failed(
                error="Deterministic embedding backend is not allowed for vector search.",
            )
        return EmbeddingHealthCheck.failed(
            error=(f"Unsupported embedding backend. Allowed backend: {DEFAULT_EMBEDDING_BACKEND}."),
        )

    dimensions = _parse_dimensions(values)
    if dimensions is None:
        return EmbeddingHealthCheck.failed(
            error=(
                f"{IDIS_EMBEDDING_DIMENSIONS_ENV} must be a positive integer matching "
                f"pgvector schema dimension {VECTOR_EMBEDDING_DIMENSIONS}."
            ),
        )
    if dimensions != VECTOR_EMBEDDING_DIMENSIONS:
        return EmbeddingHealthCheck.failed(
            error=(
                f"{IDIS_EMBEDDING_DIMENSIONS_ENV} must match pgvector schema dimension "
                f"{VECTOR_EMBEDDING_DIMENSIONS}."
            ),
        )

    model = str(values.get(IDIS_EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL)).strip()
    api_key = str(values[OPENAI_API_KEY_ENV]).strip()
    make_client = client_factory or _default_openai_client_factory

    try:
        client = make_client(api_key)
        response = client.embeddings.create(
            input=EMBEDDING_HEALTH_PROBE_INPUT,
            model=model,
            dimensions=dimensions,
        )
        vector = response.data[0].embedding
        if len(vector) != VECTOR_EMBEDDING_DIMENSIONS:
            return EmbeddingHealthCheck.failed(
                error="Embedding provider returned unexpected vector dimensions.",
            )
    except Exception as exc:
        return EmbeddingHealthCheck.failed(error=str(exc))

    return EmbeddingHealthCheck.healthy(model=model, dimensions=dimensions)
