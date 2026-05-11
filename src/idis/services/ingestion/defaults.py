"""Default ingestion service wiring for production API startup."""

from __future__ import annotations

import os

from idis.audit.sink import AuditSink
from idis.services.ingestion.service import IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

IDIS_OBJECT_STORE_BACKEND_ENV = "IDIS_OBJECT_STORE_BACKEND"
FILESYSTEM_OBJECT_STORE_BACKEND = "filesystem"


def build_default_compliance_store() -> ComplianceEnforcedStore:
    """Build the configured compliance-enforced object store.

    The current production-safe backend is the existing filesystem object store.
    Unsupported backend names fail closed instead of silently bypassing storage
    compliance controls.
    """
    backend = os.environ.get(IDIS_OBJECT_STORE_BACKEND_ENV, FILESYSTEM_OBJECT_STORE_BACKEND)
    if backend != FILESYSTEM_OBJECT_STORE_BACKEND:
        msg = f"Unsupported object store backend for ingestion: {backend}"
        raise ValueError(msg)

    return ComplianceEnforcedStore(inner_store=FilesystemObjectStore())


def build_default_ingestion_service(audit_sink: AuditSink | None = None) -> IngestionService:
    """Build the default production ingestion service for public API upload."""
    return IngestionService(
        compliant_store=build_default_compliance_store(),
        audit_sink=audit_sink,
    )
