"""Per-tenant compliance export bundle (Slice98 Task 8).

Builds a manifest-first export of a TENANT's compliance-relevant inventory (deals, documents,
claims, sanads, deliverables) and writes it through the hold-aware ``ComplianceEnforcedStore``.
The manifest carries METADATA ONLY: every item is sanitized through a sensitive-key blocklist
(the product-bundle discipline - ``raw_text``, ``text_excerpt``, ``local_path``, ``embedding``
and friends are dropped recursively), so raw content, paths, or vectors can never ride along.
The ``export.created`` audit event (HIGH) is fatal and emitted BEFORE the bundle is written.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from idis.api.errors import IdisHttpError
from idis.validators.audit_event_validator import validate_audit_event

if TYPE_CHECKING:
    from idis.api.auth import TenantContext
    from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)

EXPORT_CREATED = "export.created"

# Keys that must never appear in an export manifest (recursively dropped).
_SENSITIVE_KEYS = frozenset(
    {
        "raw_text",
        "text_excerpt",
        "local_path",
        "embedding",
        "query_text",
        "body",
        "content",
        "secret",
        "token",
        "api_key",
        "password",
    }
)

_EXPORT_SECTIONS = ("deals", "documents", "claims", "sanads", "deliverables")


@runtime_checkable
class ComplianceExportCollector(Protocol):
    """Yields the tenant's export inventory (tenant-scoped reads only)."""

    def collect(self, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return {section: [entries]} for the export sections."""
        ...


class InMemoryExportCollector:
    """Hermetic twin over the in-memory route stores (deals + documents).

    Claims/sanads/deliverables sections are empty here; the Postgres collector reads the
    durable tables and is proven in the env-gated tests.
    """

    def collect(self, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        from idis.api.routes.documents import _document_store
        from idis.persistence.repositories.deals import _in_memory_store as deals_store

        deals = [
            {"deal_id": deal_id, "name": row.get("name", "")}
            for deal_id, row in deals_store.items()
            if row.get("tenant_id") == tenant_id
        ]
        documents = [
            {
                "doc_id": doc.get("doc_id"),
                "deal_id": doc.get("deal_id"),
                "title": doc.get("title"),
                "sha256": doc.get("sha256"),
            }
            for doc in _document_store._artifacts.values()
            if doc.get("tenant_id") == tenant_id
        ]
        return {
            "deals": deals,
            "documents": documents,
            "claims": [],
            "sanads": [],
            "deliverables": [],
        }


_collector: ComplianceExportCollector | None = None


def build_default_export_collector() -> ComplianceExportCollector:
    """Select the durable Postgres collector when configured, else the in-memory twin."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        from idis.compliance.erasure_postgres import PostgresExportCollector

        return PostgresExportCollector()
    return InMemoryExportCollector()


def get_export_collector() -> ComplianceExportCollector:
    """Return the process-wide export collector, building the default on first use."""
    global _collector
    if _collector is None:
        _collector = build_default_export_collector()
    return _collector


def set_export_collector(collector: ComplianceExportCollector) -> None:
    """Override the process-wide collector (tests / explicit wiring)."""
    global _collector
    _collector = collector


def reset_export_collector() -> None:
    """Clear the process-wide collector so the next access rebuilds the default."""
    global _collector
    _collector = None


def _sanitize(value: Any) -> Any:
    """Recursively drop sensitive keys; keep only metadata-safe structure."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key.lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _emit_audit_or_fail(audit_sink: AuditSink | None, event: dict[str, Any]) -> None:
    """Validate THEN emit; audit is fatal for the export (fail-closed before the bundle write)."""
    if audit_sink is None:
        logger.error("Export audit sink not configured; export BLOCKED (fail-closed)")
        raise IdisHttpError(
            status_code=500,
            code="EXPORT_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        )
    validation = validate_audit_event(event)
    if not validation.passed:
        logger.error(
            "Export audit event failed validation (fail-closed): %s",
            [error.code for error in validation.errors],
        )
        raise IdisHttpError(
            status_code=500,
            code="EXPORT_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        )
    try:
        audit_sink.emit(event)
    except Exception:
        logger.error("Export audit emission failed (fail-closed)", exc_info=True)
        raise IdisHttpError(
            status_code=500,
            code="EXPORT_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        ) from None


def build_compliance_export(
    tenant_ctx: TenantContext,
    audit_sink: AuditSink | None = None,
    collector: ComplianceExportCollector | None = None,
) -> dict[str, Any]:
    """Build and store the tenant's sanitized export bundle; return its safe descriptor."""
    from idis.services.ingestion.defaults import build_default_compliance_store

    effective_collector = collector or get_export_collector()
    inventory = effective_collector.collect(tenant_ctx.tenant_id)

    export_id = str(uuid.uuid4())
    items = {section: _sanitize(list(inventory.get(section, []))) for section in _EXPORT_SECTIONS}
    counts = {section: len(items[section]) for section in _EXPORT_SECTIONS}
    manifest = {
        "export_id": export_id,
        "tenant_id": tenant_ctx.tenant_id,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "counts": counts,
        "items": items,
        "manifest_version": "1.0",
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    object_key = f"compliance-exports/{export_id}/manifest.json"

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_ctx.tenant_id,
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": tenant_ctx.actor_id,
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": export_id,
            "method": "POST",
            "path": "/internal/compliance/export",
            "status_code": 200,
        },
        "resource": {"resource_type": "compliance_export", "resource_id": export_id},
        "event_type": EXPORT_CREATED,
        "severity": "HIGH",
        "summary": f"compliance export created for tenant {tenant_ctx.tenant_id}",
        "payload": {
            "safe": {"export_id": export_id, **counts},
            "hashes": [f"manifest_sha256:{manifest_sha256}"],
            "refs": [f"object_key:{object_key}"],
        },
    }
    _emit_audit_or_fail(audit_sink, event)

    build_default_compliance_store().put(
        tenant_ctx, object_key, manifest_bytes, content_type="application/json"
    )
    logger.info(
        "Compliance export created: tenant_id=%s export_id=%s", tenant_ctx.tenant_id, export_id
    )
    return {
        "export_id": export_id,
        "object_key": object_key,
        "manifest_sha256": manifest_sha256,
        "counts": counts,
    }
