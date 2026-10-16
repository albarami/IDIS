"""PostgreSQL RLS and Audit Immutability Integration Tests.

Tests for:
- Row-Level Security (RLS) tenant isolation
- Audit table immutability (UPDATE/DELETE blocked by trigger)
- Cross-tenant read/write blocking

These tests require a real PostgreSQL instance and use:
- IDIS_DATABASE_ADMIN_URL for migrations and admin operations
- IDIS_DATABASE_URL for app-role operations

Run with: pytest -q tests/test_postgres_rls_and_audit_immutability.py
"""

from __future__ import annotations

from ._postgres_rls_artifacts import (
    TestDocumentArtifactsRLS,
)
from ._postgres_rls_calc_sanads import (
    TestCalcSanadsRLS,
)
from ._postgres_rls_calculations import (
    TestDeterministicCalculationsRLS,
)
from ._postgres_rls_documents import (
    TestDocumentsRLS,
)
from ._postgres_rls_fixtures import (
    admin_engine,
    app_engine,
    clean_tables,
    migrated_db,
)
from ._postgres_rls_security import (
    TestAppRoleSecurity,
    TestAuditImmutability,
    TestRLSTenantIsolation,
)
from ._postgres_rls_spans import (
    TestDocumentSpansRLS,
)
from ._postgres_rls_stores import (
    TestIdempotencyRecordsRLS,
    TestPostgresAuditSink,
    TestPostgresIdempotencyStore,
    TestWebhooksRLS,
)
from ._postgres_rls_webhook_delivery import (
    TestWebhookDeliveryAttemptsRLS,
)

__all__ = [
    "admin_engine",
    "app_engine",
    "clean_tables",
    "migrated_db",
    "TestAppRoleSecurity",
    "TestRLSTenantIsolation",
    "TestAuditImmutability",
    "TestIdempotencyRecordsRLS",
    "TestPostgresAuditSink",
    "TestPostgresIdempotencyStore",
    "TestWebhooksRLS",
    "TestWebhookDeliveryAttemptsRLS",
    "TestDocumentArtifactsRLS",
    "TestDocumentsRLS",
    "TestDocumentSpansRLS",
    "TestDeterministicCalculationsRLS",
    "TestCalcSanadsRLS",
]
