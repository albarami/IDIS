"""Real upload/parser/storage provenance, never synthetic native acceptance."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError
from idis.compliance.byok import BYOKKeyState, BYOKPolicy, BYOKPolicyRegistry
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.parsers.ocr import (
    OcrConfig,
    OcrRescueNoConsensusError,
    OcrTimeoutError,
    OcrUnavailableError,
    TesseractOcrAdapter,
)
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.ingestion.service import RouteValidatedSha256
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task4.test_watermark_runtime_bounds_red import _oversized_mediabox_pdf
from tests.test_pdf_parser import create_test_pdf
from tests.test_tesseract_ocr_adapter import _create_image_text_pdf

pytest_plugins = ("tests.test_cir04c3_inline_claim_postgres",)


@pytest.fixture
def store(tmp_path: Path) -> ComplianceEnforcedStore:
    return ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))


def _scope(engine: Engine) -> tuple[str, str]:
    tenant, deal = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        set_tenant_local(conn, tenant)
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) VALUES (CAST(:tenant AS uuid),'C3 synthetic')"
            ),
            {"tenant": tenant},
        )
        conn.execute(
            text(
                "INSERT INTO deals (deal_id,tenant_id,name,created_at) VALUES "
                "(CAST(:deal AS uuid),CAST(:tenant AS uuid),'C3 provenance',now())"
            ),
            {"tenant": tenant, "deal": deal},
        )
    return tenant, deal


def _upload(
    service: IngestionService,
    tenant: str,
    deal: str,
    data: bytes,
    *,
    name: str = "synthetic.pdf",
    metadata: dict[str, Any] | None = None,
    media_type: str = "application/pdf",
) -> Any:
    return service.ingest_bytes(
        IngestionContext(tenant_id=UUID(tenant), actor_id="C3-test", request_id=str(uuid.uuid4())),
        UUID(deal),
        filename=name,
        media_type=media_type,
        data=data,
        metadata=metadata or {"source_system": "synthetic-c3-provenance"},
    )


@pytest.mark.parametrize("code", [code for code in ParseErrorCode if code.name.startswith("OCR_")])
def test_every_ocr_family_error_is_ocr_participation(
    store: ComplianceEnforcedStore, code: ParseErrorCode
) -> None:
    service = IngestionService(compliant_store=store)
    result = ParseResult(
        doc_type="PDF", success=False, errors=[ParseError(code=code, message="Synthetic terminal")]
    )
    assert service._parser_mode(result) == "ocr"
    assert service._parser_source_type(result) == "pdf_ocr_fallback"
    assert service._parser_runtime(result) == "unconfigured_ocr"


class _ActualFailureAdapter:
    """Inject only the existing adapter failure, not the parser outcome."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def extract_pdf_text(self, _data: bytes, **_kwargs: Any) -> list[Any]:
        self.calls += 1
        raise self.error


@pytest.mark.parametrize(
    "terminal",
    ["unavailable", "timeout", "too-large", "proof-less-no-consensus", "required-no-config"],
)
def test_real_pdf_ocr_terminals_never_record_false_native_attempt(
    app_engine: Engine, store: ComplianceEnforcedStore, terminal: str
) -> None:
    tenant, deal = _scope(app_engine)
    data = _create_image_text_pdf("PUBLIC SYNTHETIC OCR PARTICIPATION")
    adapter: _ActualFailureAdapter | None = None
    if terminal == "required-no-config":
        config = None
        expected = ParseErrorCode.OCR_UNAVAILABLE
    elif terminal == "too-large":
        data = _oversized_mediabox_pdf(width_points=5000.0, height_points=5000.0)
        config = OcrConfig(enabled=True, adapter=TesseractOcrAdapter(), max_pages=1)
        expected = ParseErrorCode.OCR_IMAGE_TOO_LARGE
    else:
        error, expected = {
            "unavailable": (
                OcrUnavailableError("synthetic unavailable"),
                ParseErrorCode.OCR_UNAVAILABLE,
            ),
            "timeout": (OcrTimeoutError("synthetic timeout"), ParseErrorCode.OCR_TIMEOUT),
            "proof-less-no-consensus": (
                OcrRescueNoConsensusError("synthetic no proof"),
                ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
            ),
        }[terminal]
        adapter = _ActualFailureAdapter(error)
        config = OcrConfig(enabled=True, adapter=adapter, max_pages=1)
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        service = IngestionService(compliant_store=store, ocr_config=config, db_conn=conn)
        result = _upload(service, tenant, deal, data)
    assert result.success is False
    assert result.document_id is not None
    assert [error.details.get("parse_error_code") for error in result.errors] == [expected.value]
    if adapter is not None:
        assert adapter.calls == 1
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        row = conn.execute(
            text("SELECT metadata,source_binding_state FROM documents WHERE document_id=:document"),
            {"document": str(result.document_id)},
        ).one()
        assert row.metadata["parser_mode"] == "ocr"
        assert row.metadata["parser_source_type"] == "pdf_ocr_fallback"
        assert (
            row.metadata["source_preparation_prerequisite"] == "PARSER_NATIVE_IDENTITY_UNAVAILABLE"
        )
        assert row.source_binding_state == "UNBOUND"
        for table in ("document_parse_attempts", "data_room_packages", "runs", "run_steps"):
            assert (
                conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=CAST(:tenant AS uuid)"),
                    {"tenant": tenant},
                ).scalar_one()
                == 0
            )


def test_native_upload_records_actual_object_uuid_integrity_and_runtime_attempt(
    app_engine: Engine, store: ComplianceEnforcedStore
) -> None:
    tenant, deal = _scope(app_engine)
    data = create_test_pdf(["PUBLIC SYNTHETIC REVENUE 100"])
    with app_engine.begin() as conn:
        service = IngestionService(compliant_store=store, db_conn=conn)
        result = _upload(service, tenant, deal, data)
    assert result.success
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        artifact = (
            conn.execute(
                text("SELECT * FROM document_artifacts WHERE doc_id=:artifact"),
                {"artifact": str(result.artifact_id)},
            )
            .mappings()
            .one()
        )
        actual = store.verify_integrity(
            tenant_ctx=TenantContext(
                tenant_id=tenant,
                actor_id="C3-test",
                name="test",
                timezone="UTC",
                data_region="me-south-1",
            ),
            key=result.storage_uri,
        )
        assert str(UUID(artifact["version_id"])) == actual.version_id
        assert artifact["metadata"]["size_bytes"] == actual.size_bytes == len(data)
        assert artifact["metadata"]["durable_object_integrity"] == {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": str(result.artifact_id),
            "content_sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "uri_sha256": hashlib.sha256(result.storage_uri.encode()).hexdigest(),
            "version_id": actual.version_id,
            "backend": store.backend_name,
            "streaming_verified": True,
        }
        attempt = (
            conn.execute(
                text("SELECT * FROM document_parse_attempts WHERE document_id=:document"),
                {"document": str(result.document_id)},
            )
            .mappings()
            .one()
        )
        assert attempt["attempt_ordinal"] == 1 and attempt["write_origin"] == "RUNTIME"
        assert attempt["status"] == "PARSED" and attempt["source_binding_state"] == "UNBOUND"
        assert attempt["parser_name"] == "idis-ingestion/built_in/native_parser/pdf"
        assert attempt["parser_version"].startswith("idis-parser-runtime-v1:")
        assert attempt["input_sha256"] == hashlib.sha256(data).hexdigest()
        assert attempt["started_at"] <= attempt["finished_at"]
        span_attempts = list(
            conn.execute(
                text("SELECT parse_attempt_id FROM document_spans WHERE document_id=:document"),
                {"document": str(result.document_id)},
            ).scalars()
        )
        # Installed0038 permits this pointer only at the atomic BOUND freeze.
        assert span_attempts and set(span_attempts) == {None}


@pytest.mark.parametrize("streamed", [False, True])
def test_actual_late_byok_revocation_preserves_403_and_materializes_no_source_rows(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, streamed: bool
) -> None:
    tenant, deal = _scope(app_engine)
    registry = BYOKPolicyRegistry()
    registry.set(BYOKPolicy(tenant_id=tenant, key_alias="synthetic-c3-key"))
    store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects"), byok_registry=registry
    )
    method = "put_stream" if streamed else "put"
    actual_put = getattr(store, method)
    stored = []

    def put_then_revoke(**kwargs: Any) -> Any:
        metadata = actual_put(**kwargs)
        stored.append(metadata)
        registry.set(
            BYOKPolicy(
                tenant_id=tenant, key_alias="synthetic-c3-key", key_state=BYOKKeyState.REVOKED
            )
        )
        return metadata

    monkeypatch.setattr(store, method, put_then_revoke)
    data = create_test_pdf(["PUBLIC SYNTHETIC LATE REVOCATION"])
    source = tmp_path / "source.pdf"
    source.write_bytes(data)
    with pytest.raises(IdisHttpError) as caught, app_engine.begin() as conn:
        service = IngestionService(compliant_store=store, db_conn=conn)
        if streamed:
            service.ingest_file(
                IngestionContext(tenant_id=UUID(tenant), actor_id="C3-test", request_id="late-key"),
                UUID(deal),
                filename="synthetic.pdf",
                media_type="application/pdf",
                source_path=source,
                size_bytes=len(data),
                validated_sha256=RouteValidatedSha256.from_bytes(data=data),
            )
        else:
            _upload(service, tenant, deal, data)
    assert caught.value.status_code == 403 and caught.value.code == "BYOK_KEY_REVOKED"
    assert len(stored) == 1 and stored[0].sha256 == hashlib.sha256(data).hexdigest()
    # The actual put happened before denial; do not claim object bytes were
    # rolled back by SQL. No source/runtime/audit row may be materialized.
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        for relation in (
            "document_artifacts",
            "documents",
            "document_spans",
            "document_parse_attempts",
            "data_room_packages",
            "run_input_bindings",
            "runs",
            "run_steps",
            "audit_events",
        ):
            assert (
                conn.execute(
                    text(f"SELECT count(*) FROM {relation} WHERE tenant_id=CAST(:tenant AS uuid)"),
                    {"tenant": tenant},
                ).scalar_one()
                == 0
            )


def test_late_ingestion_audit_failure_rolls_back_actual_attempt_and_source_rows(
    app_engine: Engine, store: ComplianceEnforcedStore
) -> None:
    tenant, deal = _scope(app_engine)

    class LateFailure(PostgresAuditSink):
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            super().emit_in_tx(conn, event)
            assert (
                conn.execute(text("SELECT count(*) FROM document_parse_attempts")).scalar_one() == 1
            )
            raise AuditSinkError("C3_ACTUAL_ATTEMPT_LATE_AUDIT_FAILURE")

    with (
        pytest.raises(AuditSinkError, match="C3_ACTUAL_ATTEMPT_LATE_AUDIT_FAILURE"),
        app_engine.begin() as conn,
    ):
        service = IngestionService(compliant_store=store, audit_sink=LateFailure(), db_conn=conn)
        _upload(service, tenant, deal, create_test_pdf(["PUBLIC SYNTHETIC ROLLBACK"]))
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        for table in (
            "document_artifacts",
            "documents",
            "document_parse_attempts",
            "document_spans",
            "audit_events",
        ):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize("changed_replay", [False, True])
def test_runtime_attempt_replay_is_exact_and_never_appends_an_execution(
    app_engine: Engine, store: ComplianceEnforcedStore, changed_replay: bool
) -> None:
    tenant, deal = _scope(app_engine)
    with app_engine.begin() as conn:
        result = _upload(
            IngestionService(compliant_store=store, db_conn=conn),
            tenant,
            deal,
            create_test_pdf(["PUBLIC SYNTHETIC SINGLE EXECUTION"]),
        )
    assert result.success
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        before = dict(
            conn.execute(
                text("SELECT * FROM document_parse_attempts WHERE document_id=:document"),
                {"document": str(result.document_id)},
            )
            .mappings()
            .one()
        )
        values = {
            key: str(before[key]) if key.endswith("_id") else before[key]
            for key in (
                "parse_attempt_id",
                "deal_id",
                "document_id",
                "parser_name",
                "parser_version",
                "input_sha256",
                "receipt_sha256",
                "status",
                "started_at",
                "finished_at",
            )
        }
        if changed_replay:
            values["parser_version"] += ":changed"
            with pytest.raises(ValueError, match="runtime parse attempt replay does not match"):
                PostgresDocumentsRepository(conn, tenant).create_runtime_parse_attempt(**values)
        else:
            for _ in range(2):
                PostgresDocumentsRepository(conn, tenant).create_runtime_parse_attempt(**values)
    # Fresh app-role read, including timestamps and binding pointers: no new attempt,
    # no replacement execution, and a rejected replay does not rewrite the original.
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        rows = (
            conn.execute(
                text("SELECT * FROM document_parse_attempts WHERE document_id=:document"),
                {"document": str(result.document_id)},
            )
            .mappings()
            .all()
        )
        assert [dict(row) for row in rows] == [before]


def test_package_bound_failed_native_upload_records_failed_attempt(
    app_engine: Engine, store: ComplianceEnforcedStore
) -> None:
    """A manifest-backed encrypted PDF persists one FAILED native parse attempt.

    The attempt write is existing behavior: it runs when the prerequisite is
    absent and a native parser identity is present. It does not store a schema ref.
    """
    from idis.tools.preview_source_preparation import create_preview_preparation
    from tests.test_pdf_parser import create_encrypted_test_pdf

    tenant, deal = _scope(app_engine)
    data = create_encrypted_test_pdf(
        ["PUBLIC SYNTHETIC ENCRYPTED"],
        user_password=secrets.token_hex(8),
    )
    preparation = create_preview_preparation(tenant_id=tenant, deal_id=deal, files=[("pdf", data)])
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        service = IngestionService(
            compliant_store=store, audit_sink=PostgresAuditSink(), db_conn=conn
        )
        result = _upload(
            service,
            tenant,
            deal,
            data,
            name=preparation.files[0].generated_upload_name,
            metadata=preparation.upload_metadata(0),
        )
    assert result.success is False
    assert result.document_id is not None
    assert [error.details.get("parse_error_code") for error in result.errors] == ["encrypted_pdf"]
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        document = conn.execute(
            text("SELECT metadata FROM documents WHERE document_id=:document"),
            {"document": str(result.document_id)},
        ).one()
        assert "applicable_schema_refs" not in document.metadata
        assert "PARSER_NATIVE_IDENTITY_UNAVAILABLE" not in document.metadata.values()
        attempts = (
            conn.execute(
                text("SELECT * FROM document_parse_attempts WHERE document_id=:document"),
                {"document": str(result.document_id)},
            )
            .mappings()
            .all()
        )
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["attempt_ordinal"] == 1
    assert attempt["status"] == "FAILED"
    assert attempt["write_origin"] == "RUNTIME"
    assert attempt["parser_name"] == "idis-ingestion/built_in/native_parser/pdf"
    assert attempt["parser_version"].startswith("idis-parser-runtime-v1:")
