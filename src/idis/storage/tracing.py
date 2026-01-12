"""IDIS Object Storage OpenTelemetry tracing integration.

Provides tracing decorators and utilities for storage operations.

Security (per docs/IDIS_Security_Threat_Model_v6_3.md):
    - Never export absolute filesystem paths in span attributes
    - Only logical keys and safe identifiers in attributes
    - No secrets or credentials in any span attribute
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from idis.storage.models import StoredObjectMetadata

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean from environment variable."""
    val = os.environ.get(key, "").strip().lower()
    is_truthy = val in ("1", "true", "yes")
    is_falsy = val in ("0", "false", "no", "")
    if is_truthy:
        return is_truthy
    if is_falsy:
        return default
    return default


def _is_otel_enabled() -> bool:
    """Check if OpenTelemetry tracing is enabled."""
    return _get_env_bool("IDIS_OTEL_ENABLED", False)


def traced_storage_operation(operation: str) -> Callable[[F], F]:
    """Decorator to trace storage operations with OpenTelemetry.

    Emits spans with safe attributes (no absolute paths, no secrets).

    Args:
        operation: Operation name (e.g., "put", "get", "head", "delete").

    Returns:
        Decorated function that emits OTel spans when tracing is enabled.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(self: Any, tenant_id: str, key: str, *args: Any, **kwargs: Any) -> Any:
            if not _is_otel_enabled():
                return func(self, tenant_id, key, *args, **kwargs)

            try:
                from opentelemetry import trace

                tracer = trace.get_tracer("idis.object_store")
                span_name = f"idis.object_store.{operation}"

                with tracer.start_as_current_span(span_name) as span:
                    span.set_attribute("idis.tenant_id", tenant_id)
                    # SECURITY: Never export raw keys - they may contain secrets.
                    # Use SHA256 hash for correlation without exposing key content.
                    key_sha256 = hashlib.sha256(key.encode("utf-8")).hexdigest()
                    span.set_attribute("idis.object_key_sha256", key_sha256)
                    span.set_attribute("storage.backend", getattr(self, "backend_name", "unknown"))

                    if "version_id" in kwargs and kwargs["version_id"] is not None:
                        span.set_attribute("idis.object_version_id", kwargs["version_id"])

                    try:
                        result = func(self, tenant_id, key, *args, **kwargs)

                        if result is not None:
                            _add_result_attributes(span, result, operation)

                        return result

                    except Exception as e:
                        span.set_attribute("error", True)
                        span.set_attribute("error.type", type(e).__name__)
                        raise

            except ImportError:
                return func(self, tenant_id, key, *args, **kwargs)

        return cast(F, wrapper)

    return decorator


def _add_result_attributes(span: Any, result: Any, operation: str) -> None:
    """Add result-based attributes to span safely.

    Only adds safe attributes (sha256, version_id, size).
    Never adds filesystem paths.
    """
    try:
        from idis.storage.models import StoredObject, StoredObjectMetadata

        metadata: StoredObjectMetadata | None = None

        if isinstance(result, StoredObjectMetadata):
            metadata = result
        elif isinstance(result, StoredObject):
            metadata = result.metadata

        if metadata is not None:
            span.set_attribute("idis.object_sha256", metadata.sha256)
            span.set_attribute("idis.object_version_id", metadata.version_id)
            span.set_attribute("idis.object_size_bytes", metadata.size_bytes)
            if metadata.content_type:
                span.set_attribute("idis.object_content_type", metadata.content_type)

        if operation == "list_versions" and isinstance(result, list):
            span.set_attribute("idis.object_version_count", len(result))

    except Exception as e:
        logger.debug("Failed to add result attributes to span: %s", e)
