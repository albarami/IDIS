from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ._postgres_rls_fixtures import TENANT_A_ID, TENANT_B_ID

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine


class TestDocumentsRLS:
    """Tests for documents table RLS tenant isolation (Phase 3.1)."""

    def _create_deal_and_artifact(
        self, conn: Connection, tenant_id: str, deal_id: str, doc_id: str
    ) -> None:
        """Helper to create deal and document_artifact for FK constraints."""
        now = datetime.now(UTC)
        conn.execute(
            text(
                """
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (:deal_id, :tenant_id, :name, :created_at)
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "name": "Test Deal",
                "created_at": now,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO document_artifacts
                (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                 version_id, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                        :version_id, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                """
            ),
            {
                "doc_id": doc_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "doc_type": "PITCH_DECK",
                "title": "Test Document",
                "source_system": "DocSend",
                "version_id": "v1",
                "created_at": now,
                "updated_at": now,
            },
        )

    def test_documents_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert document under tenant A, read under tenant A => 1 row."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text(
                    "SELECT document_id, doc_type FROM documents WHERE document_id = :document_id"
                ),
                {"document_id": document_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant document reads"
        assert rows[0].doc_type == "PDF"

    def test_documents_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert document under tenant A, read under tenant B => 0 rows."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT document_id FROM documents WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant document reads"

    def test_documents_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT document_id FROM documents"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_documents_insert_blocked_without_tenant_context(
        self, app_engine: Engine, admin_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Test Deal",
                    "created_at": now,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_documents_rls_blocks_mismatched_tenant_insert(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT with tenant context A but tenant_id=B must be blocked by WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at, source_binding_state,
                     source_lineage_object_id, source_corpus_receipt_id)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at, 'UNBOUND', NULL, NULL)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_B_ID,  # Mismatched: context=A, value=B
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"Mismatched tenant INSERT should be blocked by RLS WITH CHECK: {exc_info.value}"
