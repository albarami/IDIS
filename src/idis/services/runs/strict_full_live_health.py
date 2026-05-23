"""Safe strict full-live health checks for live model and runtime dependencies."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from idis.api.auth import IDIS_API_KEYS_ENV, ApiKeyRecord, _normalize_roles
from idis.api.errors import IdisHttpError
from idis.services.ingestion.defaults import FILESYSTEM_OBJECT_STORE_BACKEND


@dataclass(frozen=True, slots=True)
class StrictHealthCheckResult:
    """Path-free and secret-free strict health check result."""

    passed: bool
    service: str
    message: str
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def ok_result(
        cls,
        *,
        service: str,
        message: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StrictHealthCheckResult:
        """Build a successful health check result."""
        return cls(passed=True, service=service, message=message, metadata=dict(metadata or {}))

    @classmethod
    def ok(
        cls,
        *,
        service: str,
        message: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StrictHealthCheckResult:
        """Build a successful health check result."""
        return cls.ok_result(service=service, message=message, metadata=metadata)

    @classmethod
    def failed(
        cls,
        *,
        service: str,
        message: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StrictHealthCheckResult:
        """Build a failed health check result."""
        return cls(passed=False, service=service, message=message, metadata=dict(metadata or {}))


@dataclass(frozen=True, slots=True)
class StrictLLMHealthCheckRequest:
    """Inputs for the live model health check."""

    extract_model: str
    debate_default_model: str
    debate_arbiter_model: str

    @property
    def models(self) -> tuple[str, str, str]:
        """Return all configured strict-live model names."""
        return (self.extract_model, self.debate_default_model, self.debate_arbiter_model)


@dataclass(frozen=True, slots=True)
class StrictRuntimeHealthCheckRequest:
    """Inputs for durable runtime health checks."""

    database_url: str
    api_keys_json: str
    object_store_backend: str
    db_conn: Any = None


def run_anthropic_llm_health_check(
    request: StrictLLMHealthCheckRequest,
) -> StrictHealthCheckResult:
    """Run a non-private Anthropic health check using configured strict models."""
    try:
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        for model in dict.fromkeys(request.models):
            client = AnthropicLLMClient(model=model, max_tokens=4)
            response = client.call(
                "Health check only. Reply with exactly OK.",
                json_mode=False,
            )
            if response != "OK":
                return StrictHealthCheckResult.failed(
                    service="anthropic",
                    message="Anthropic live health check returned an unexpected response",
                    metadata={"provider": "anthropic"},
                )
    except Exception:
        return StrictHealthCheckResult.failed(
            service="anthropic",
            message="Anthropic live health check failed",
            metadata={"provider": "anthropic"},
        )
    return StrictHealthCheckResult.ok(
        service="anthropic",
        message="Anthropic live health check passed",
        metadata={"provider": "anthropic", "model_count": str(len(set(request.models)))},
    )


def run_durable_runtime_health_check(
    request: StrictRuntimeHealthCheckRequest,
) -> StrictHealthCheckResult:
    """Run safe DB/API-key/object-store readiness checks without exposing secrets."""
    api_key_error = _validate_api_keys_json(request.api_keys_json)
    if api_key_error is not None:
        return StrictHealthCheckResult.failed(
            service="durable_runtime",
            message=api_key_error,
            metadata={"backend": _runtime_backend_label(request.object_store_backend)},
        )
    if request.object_store_backend != FILESYSTEM_OBJECT_STORE_BACKEND:
        return StrictHealthCheckResult.failed(
            service="durable_runtime",
            message="Configured object store backend is unsupported",
            metadata={"backend": _runtime_backend_label(request.object_store_backend)},
        )
    object_store_error = _probe_object_store()
    if object_store_error is not None:
        return StrictHealthCheckResult.failed(
            service="durable_runtime",
            message=object_store_error,
            metadata={"backend": _runtime_backend_label(request.object_store_backend)},
        )

    try:
        if request.db_conn is not None:
            request.db_conn.execute(text("SELECT 1"))
        else:
            engine = create_engine(request.database_url, pool_pre_ping=True)
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            finally:
                engine.dispose()
    except SQLAlchemyError:
        return StrictHealthCheckResult.failed(
            service="durable_runtime",
            message="Postgres SELECT 1 failed",
            metadata={"backend": _runtime_backend_label(request.object_store_backend)},
        )

    return StrictHealthCheckResult.ok(
        service="durable_runtime",
        message="Durable runtime health check passed",
        metadata={"backend": _runtime_backend_label(request.object_store_backend)},
    )


def missing_model_env(
    *,
    env: Mapping[str, str],
    backend_key: str,
    model_keys: Sequence[str],
) -> list[str]:
    """Return strict model env requirements missing from a backend path."""
    required: list[str] = []
    if env.get(backend_key) != "anthropic":
        required.append(f"{backend_key}=anthropic")
    if not _has_value(env, "ANTHROPIC_API_KEY"):
        required.append("ANTHROPIC_API_KEY")
    required.extend(key for key in model_keys if not _has_value(env, key))
    return required


def llm_health_result(
    env: Mapping[str, str],
    *,
    llm_health_checker: Callable[[StrictLLMHealthCheckRequest], StrictHealthCheckResult] | None,
) -> StrictHealthCheckResult | None:
    """Run live LLM health only when all strict model env is configured."""
    missing = set(
        missing_model_env(
            env=env,
            backend_key="IDIS_EXTRACT_BACKEND",
            model_keys=["IDIS_ANTHROPIC_MODEL_EXTRACT"],
        )
        + missing_model_env(
            env=env,
            backend_key="IDIS_DEBATE_BACKEND",
            model_keys=[
                "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
                "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
            ],
        )
    )
    if missing:
        return None
    request = StrictLLMHealthCheckRequest(
        extract_model=str(env["IDIS_ANTHROPIC_MODEL_EXTRACT"]),
        debate_default_model=str(env["IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"]),
        debate_arbiter_model=str(env["IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER"]),
    )
    checker = llm_health_checker or run_anthropic_llm_health_check
    return checker(request)


def runtime_health_result(
    env: Mapping[str, str],
    *,
    runtime_health_checker: Callable[[StrictRuntimeHealthCheckRequest], StrictHealthCheckResult]
    | None,
    db_conn: Any,
) -> StrictHealthCheckResult | None:
    """Run durable runtime health only when all strict runtime env is configured."""
    required_env_vars = (
        "IDIS_DATABASE_URL",
        IDIS_API_KEYS_ENV,
        "IDIS_OBJECT_STORE_BACKEND",
    )
    if any(not _has_value(env, key) for key in required_env_vars):
        return None
    request = StrictRuntimeHealthCheckRequest(
        database_url=str(env["IDIS_DATABASE_URL"]),
        api_keys_json=str(env[IDIS_API_KEYS_ENV]),
        object_store_backend=str(env["IDIS_OBJECT_STORE_BACKEND"]),
        db_conn=db_conn,
    )
    checker = runtime_health_checker or run_durable_runtime_health_check
    return checker(request)


def anthropic_provenance() -> dict[str, str]:
    """Return strict provenance for Anthropic-backed model paths."""
    return {"provider": "anthropic", "fallback": "none"}


def runtime_provenance(
    env: Mapping[str, str],
    runtime_health: StrictHealthCheckResult | None,
) -> dict[str, str]:
    """Return strict provenance for durable runtime paths."""
    metadata = dict(runtime_health.metadata if runtime_health is not None else {})
    metadata.setdefault(
        "backend",
        _runtime_backend_label(str(env.get("IDIS_OBJECT_STORE_BACKEND", ""))),
    )
    metadata["fallback"] = "none"
    return metadata


def _validate_api_keys_json(value: str) -> str | None:
    env_name = IDIS_API_KEYS_ENV
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return f"{env_name} is not valid JSON"
    if not isinstance(parsed, dict) or not parsed:
        return f"{env_name} must define at least one API key"
    for key_id, config in parsed.items():
        if not str(key_id).strip():
            return f"{env_name} contains an empty API key id"
        if not isinstance(config, dict):
            return f"{env_name} entries must be objects"
        try:
            record = ApiKeyRecord.model_validate(config)
        except ValidationError as exc:
            missing_fields = _missing_api_key_record_fields(exc)
            if missing_fields:
                return f"{env_name} entry is missing required fields: {missing_fields}"
            return f"{env_name} entry is invalid"
        if not record.roles:
            return f"{env_name} entries must include at least one role"
        try:
            _normalize_roles(record.roles)
        except IdisHttpError:
            return f"{env_name} entry contains invalid role"
    return None


def _missing_api_key_record_fields(exc: ValidationError) -> str:
    fields = sorted(
        str(error["loc"][0])
        for error in exc.errors()
        if error.get("type") == "missing" and error.get("loc")
    )
    return ", ".join(fields)


def _probe_object_store() -> str | None:
    from idis.storage.errors import ObjectStorageError
    from idis.storage.filesystem_store import FilesystemObjectStore

    tenant_id = "00000000-0000-0000-0000-000000000001"
    key = f"strict-full-live-health/{uuid.uuid4().hex}"
    store = FilesystemObjectStore()
    try:
        store.put(tenant_id, key, b"ok", content_type="text/plain")
        stored = store.get(tenant_id, key)
        if stored.body != b"ok":
            return "Configured object store read-after-write check failed"
    except ObjectStorageError:
        return "Configured object store read/write health check failed"
    finally:
        with suppress(ObjectStorageError):
            store.delete(tenant_id, key)
    return None


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _runtime_backend_label(object_store_backend: str) -> str:
    if object_store_backend == FILESYSTEM_OBJECT_STORE_BACKEND:
        return "postgres+filesystem"
    if not object_store_backend:
        return "postgres+unconfigured-object-store"
    return "postgres+unsupported-object-store"
