"""Current-schema storage compatibility, never native strict admission."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.claims import ClaimsRepository, SanadsRepository
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.services.ingestion import IngestionContext, IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from tests import test_ingestion_persists_documents_postgres as ingestion_fixtures
from tests._cir04c2_pg_helpers import create_sanad_evidence_parents

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

TENANT = "11111111-1111-1111-1111-111111111111"


def _ids() -> dict[str, str]:
    return {
        key: str(uuid.uuid4())
        for key in ("deal", "artifact", "document", "span", "claim", "evidence", "sanad")
    }


def _seed_storage(conn: Connection, ids: dict[str, str], *, through: str) -> None:
    """Seed real UNBOUND parents; these rows are not native source receipts."""
    set_tenant_local(conn, TENANT)
    params = ids | {"tenant": TENANT}
    conn.execute(
        text(
            "INSERT INTO tenants (tenant_id,name) VALUES (CAST(:tenant AS uuid),'C2 Tenant') "
            "ON CONFLICT (tenant_id) DO NOTHING; "
            "INSERT INTO deals (deal_id,tenant_id,name,created_at) VALUES "
            "(CAST(:deal AS uuid),CAST(:tenant AS uuid),'C2 Storage Deal',now())"
        ),
        params,
    )
    if through == "deal":
        return
    conn.execute(
        text(
            "INSERT INTO document_artifacts "
            "(doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,"
            "source_binding_state,source_lineage_object_id,source_corpus_receipt_id) "
            "VALUES (CAST(:artifact AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
            "'DATA_ROOM_FILE','C2 Storage','synthetic-c2','v1','UNBOUND',NULL,NULL)"
        ),
        params,
    )
    if through == "artifact":
        return
    conn.execute(
        text(
            "INSERT INTO documents "
            "(document_id,tenant_id,deal_id,doc_id,doc_type,parse_status,"
            "source_binding_state,source_lineage_object_id,source_corpus_receipt_id) "
            "VALUES (CAST(:document AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
            "CAST(:artifact AS uuid),'PDF','PARSED','UNBOUND',NULL,NULL)"
        ),
        params,
    )
    if through == "document":
        return
    assert through == "span"
    conn.execute(
        text(
            "INSERT INTO document_spans "
            "(span_id,tenant_id,deal_id,document_id,span_type,locator,content_hash,"
            "source_binding_state,source_lineage_object_id,source_corpus_receipt_id,"
            "parse_attempt_id) VALUES (CAST(:span AS uuid),CAST(:tenant AS uuid),"
            "CAST(:deal AS uuid),CAST(:document AS uuid),'PAGE_TEXT',jsonb_build_object('page',1),"
            ":content_hash,'UNBOUND',NULL,NULL,NULL)"
        ),
        params | {"content_hash": "a" * 64},
    )


@pytest.mark.parametrize("kind", ["artifact", "document", "span"])
def test_repository_creates_explicit_unbound_storage_rows(
    app_engine: Engine, clean_tables: None, kind: str
) -> None:
    ids = _ids()
    with app_engine.begin() as conn:
        _seed_storage(
            conn,
            ids,
            through={"artifact": "deal", "document": "artifact", "span": "document"}[kind],
        )
        repo = PostgresDocumentsRepository(conn, TENANT)
        if kind == "artifact":
            repo.create_artifact(
                doc_id=ids["artifact"],
                deal_id=ids["deal"],
                doc_type="DATA_ROOM_FILE",
                title="C2 Durable Artifact",
                source_system="synthetic-c2",
                version_id="v1",
                ingested_at=datetime.now(UTC),
                sha256="a" * 64,
                uri=None,
            )
            table, key, identity = "document_artifacts", "doc_id", ids["artifact"]
        elif kind == "document":
            repo.create_document(
                document_id=ids["document"],
                deal_id=ids["deal"],
                doc_id=ids["artifact"],
                doc_type="PDF",
                parse_status="PARSED",
            )
            table, key, identity = "documents", "document_id", ids["document"]
        else:
            repo.create_document_span(
                span_id=ids["span"],
                deal_id=ids["deal"],
                document_id=ids["document"],
                span_type="PAGE_TEXT",
                locator={"page": 1},
                text_excerpt="Synthetic C2 text",
                content_hash="a" * 64,
            )
            table, key, identity = "document_spans", "span_id", ids["span"]
        row = conn.execute(
            text(
                f"SELECT source_binding_state,source_lineage_object_id,source_corpus_receipt_id "
                f"FROM {table} WHERE {key}=CAST(:identity AS uuid)"
            ),
            {"identity": identity},
        ).one()
        assert tuple(row) == ("UNBOUND", None, None)
        if kind == "span":
            assert (
                conn.execute(
                    text(
                        "SELECT parse_attempt_id FROM document_spans WHERE span_id=CAST(:s AS uuid)"
                    ),
                    {"s": identity},
                ).scalar_one()
                is None
            )


def test_sanad_uuid_primary_evidence_projection_is_public_string(
    app_engine: Engine, clean_tables: None
) -> None:
    ids = _ids()
    with app_engine.begin() as conn:
        _seed_storage(conn, ids, through="span")
        ClaimsRepository(conn, TENANT).create(
            claim_id=ids["claim"],
            deal_id=ids["deal"],
            claim_class="FINANCIAL",
            claim_text="Synthetic C2 claim",
        )
        conn.execute(
            text(
                "INSERT INTO evidence_items "
                "(evidence_id,tenant_id,deal_id,claim_id,source_span_id,source_grade) "
                "VALUES (CAST(:evidence AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "CAST(:claim AS uuid),CAST(:span AS uuid),'B')"
            ),
            ids | {"tenant": TENANT},
        )
        repo = SanadsRepository(conn, TENANT)
        repo.create(
            sanad_id=ids["sanad"],
            claim_id=ids["claim"],
            deal_id=ids["deal"],
            primary_evidence_id=ids["evidence"],
        )
        result = repo.get(ids["sanad"])
        assert result is not None
        assert isinstance(result["primary_evidence_id"], str)
        assert result["primary_evidence_id"] == ids["evidence"]


@pytest.mark.parametrize("parent_scope", ["missing", "other_deal", "other_tenant"])
def test_sanad_primary_evidence_rejects_missing_or_cross_scope_parent(
    app_engine: Engine, clean_tables: None, parent_scope: str
) -> None:
    ids = _ids()
    with app_engine.begin() as conn:
        _seed_storage(conn, ids, through="deal")
        ClaimsRepository(conn, TENANT).create(
            claim_id=ids["claim"],
            deal_id=ids["deal"],
            claim_class="FINANCIAL",
            claim_text="C2 target claim",
        )
        primary_id = str(uuid.uuid4())
        if parent_scope != "missing":
            parent_tenant = (
                "22222222-2222-2222-2222-222222222222" if parent_scope == "other_tenant" else TENANT
            )
            parent_deal, parent_claim = str(uuid.uuid4()), str(uuid.uuid4())
            set_tenant_local(conn, parent_tenant)
            conn.execute(
                text(
                    "INSERT INTO tenants (tenant_id,name) VALUES (CAST(:t AS uuid),'C2 Parent') "
                    "ON CONFLICT (tenant_id) DO NOTHING; "
                    "INSERT INTO deals (deal_id,tenant_id,name,created_at) "
                    "VALUES (CAST(:d AS uuid),CAST(:t AS uuid),'C2 parent deal',now())"
                ),
                {"t": parent_tenant, "d": parent_deal},
            )
            ClaimsRepository(conn, parent_tenant).create(
                claim_id=parent_claim,
                deal_id=parent_deal,
                claim_class="FINANCIAL",
                claim_text="C2 genuine parent claim",
            )
            primary_id, _ = create_sanad_evidence_parents(
                conn, tenant_id=parent_tenant, deal_id=parent_deal, claim_id=parent_claim
            )
        repo = SanadsRepository(conn, TENANT)
        with pytest.raises(IntegrityError) as caught, conn.begin_nested():
            repo.create(
                sanad_id=ids["sanad"],
                claim_id=ids["claim"],
                deal_id=ids["deal"],
                primary_evidence_id=primary_id,
            )
        assert caught.value.orig.pgcode == "23503"
        assert caught.value.orig.diag.constraint_name == "fk_sanads_task6_primary_evidence_scope"


class _WriteThenFailAuditSink(PostgresAuditSink):
    def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
        super().emit_in_tx(conn, event)
        raise AuditSinkError("C2_INJECTED_AUDIT_FAILURE")


def _durable_counts(conn: Connection) -> tuple[int, ...]:
    set_tenant_local(conn, TENANT)
    return tuple(
        conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=CAST(:t AS uuid)"),
            {"t": TENANT},
        ).scalar_one()
        for table in ("document_artifacts", "documents", "document_spans", "audit_events")
    )


@pytest.mark.parametrize("failure", ["audit", "persistence"])
def test_ingestion_failure_rolls_back_corpus_and_transactional_audit(
    app_engine: Engine,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    ids = _ids()
    with app_engine.begin() as conn:
        _seed_storage(conn, ids, through="deal")
        before = _durable_counts(conn)
    sink = _WriteThenFailAuditSink() if failure == "audit" else PostgresAuditSink()
    if failure == "persistence":
        original = PostgresDocumentsRepository.create_document

        def invalid_parent(self: PostgresDocumentsRepository, **values: Any) -> dict[str, Any]:
            return original(self, **(values | {"doc_id": str(uuid.uuid4())}))

        monkeypatch.setattr(PostgresDocumentsRepository, "create_document", invalid_parent)

    def ingest(conn: Connection) -> Any:
        set_tenant_local(conn, TENANT)
        return IngestionService(
            compliant_store=compliant_store,
            audit_sink=sink,
            db_conn=conn,
        ).ingest_bytes(
            ctx=IngestionContext(
                tenant_id=UUID(TENANT), actor_id="c2-test", request_id="c2-rollback"
            ),
            deal_id=UUID(ids["deal"]),
            filename="synthetic.pdf",
            media_type="application/pdf",
            data=ingestion_fixtures._pdf_bytes(),
        )

    if failure == "audit":
        with (
            pytest.raises(AuditSinkError, match="C2_INJECTED_AUDIT_FAILURE"),
            app_engine.begin() as conn,
        ):
            ingest(conn)
    else:
        with app_engine.begin() as conn:
            result = ingest(conn)
            assert result.success is False
            assert result.artifact_id is None
            assert result.document_id is None
    with app_engine.begin() as conn:
        assert _durable_counts(conn) == before
