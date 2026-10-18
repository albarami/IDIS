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


def test_verified_watermark_receipt_writes_registered_schema_ref(
    app_engine: Engine,
    store: ComplianceEnforcedStore,
    tmp_path: Path,
) -> None:
    """A watermark receipt persists the registered v1 ref around the v2 payload."""
    from idis.persistence.repositories.data_room_packages import PostgresDataRoomPackagesRepository
    from idis.persistence.repositories.task7_source_freeze import _SOURCE_SCHEMA_AUTHORITIES
    from idis.services.data_room.package_service import create_data_room_package
    from tests._cir04c3_exclusion_signer import (
        EXCLUSION_UPLOAD_NAME,
        build_bound_source_manifest,
        watermark_only_pdf,
        watermark_service_options,
        write_signed_watermark_authority,
    )

    tenant, deal = _scope(app_engine)
    pdf_bytes = watermark_only_pdf()
    fingerprint = hashlib.sha256(b"c3-provenance-watermark-tree").hexdigest()
    authority = write_signed_watermark_authority(
        tmp_path,
        tenant_id=tenant,
        safe_alias=EXCLUSION_UPLOAD_NAME,
        source_tree_fingerprint=fingerprint,
        pdf_bytes=pdf_bytes,
    )
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        service = IngestionService(
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            db_conn=conn,
            **watermark_service_options(authority),
        )
        result = _upload(
            service,
            tenant,
            deal,
            pdf_bytes,
            name=EXCLUSION_UPLOAD_NAME,
            metadata={
                "source_system": "c3-watermark-exclusion",
                "upload_filename": EXCLUSION_UPLOAD_NAME,
                "source_tree_fingerprint": fingerprint,
            },
        )
        assert result.success is False
        assert result.terminal_disposition == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
        document = PostgresDocumentsRepository(conn, tenant).get_document(str(result.document_id))
        assert document is not None
        manifest = build_bound_source_manifest(
            [document],
            source_tree_fingerprint=fingerprint,
        )
        create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(conn, tenant),
            tenant_id=tenant,
            deal_id=deal,
            created_by_actor_id="C3-test",
            created_by_actor_type="SERVICE",
            document_ids=[str(manifest.files[0].document_id)],
            documents=[document],
            source_manifest=manifest,
        )
        stored = conn.execute(
            text("SELECT metadata FROM documents WHERE document_id=:document"),
            {"document": str(result.document_id)},
        ).scalar_one()
        file_status = conn.execute(
            text(
                """
                SELECT files.file_status
                FROM data_room_package_files AS files
                JOIN documents
                  ON documents.deal_id = files.deal_id
                JOIN document_artifacts
                  ON document_artifacts.doc_id = documents.doc_id
                 AND document_artifacts.sha256 = files.sha256
                WHERE documents.document_id = :document
                """
            ),
            {"document": str(result.document_id)},
        ).scalar_one()
    receipt = stored["document_no_substantive_content_receipt"]
    schema_sha256 = _SOURCE_SCHEMA_AUTHORITIES[
        ("no-substantive-content-receipt", "document-no-substantive-content-receipt-v1")
    ]
    assert stored["applicable_schema_refs"] == [
        {
            "schema_id": "no-substantive-content-receipt",
            "schema_version": "document-no-substantive-content-receipt-v1",
            "schema_sha256": schema_sha256,
            "validated_payload": receipt,
        }
    ]
    assert receipt["schema_version"] == "document-no-substantive-content-receipt-v2"
    assert file_status == "blocked"


def test_receipt_schema_refs_sort_preexisting_later_ref() -> None:
    """A later schema id stays, and the registered receipt ref sorts first."""
    from idis.services.ingestion.service import _receipt_schema_refs

    later = {
        "schema_id": "ocr-terminal-evidence",
        "schema_version": "ocr-terminal-evidence-v5",
        "schema_sha256": "a" * 64,
        "validated_payload": {"marker": "preexisting-later-ref"},
    }
    receipt = {"schema_version": "document-no-substantive-content-receipt-v2", "marker": "new"}
    result = _receipt_schema_refs([later], receipt)
    assert [item["schema_id"] for item in result] == [
        "no-substantive-content-receipt",
        "ocr-terminal-evidence",
    ]
    assert result[1] == later
    assert result[0]["validated_payload"] == receipt


def test_receipt_schema_refs_fail_closed_on_non_list_or_non_dict() -> None:
    """Corrupt applicable_schema_refs are refused instead of replaced or dropped."""
    from idis.services.ingestion.service import ReceiptSchemaRefError, _receipt_schema_refs

    receipt = {"schema_version": "document-no-substantive-content-receipt-v2"}
    later = {
        "schema_id": "ocr-terminal-evidence",
        "schema_version": "ocr-terminal-evidence-v5",
        "schema_sha256": "b" * 64,
        "validated_payload": {"marker": "kept"},
    }
    with pytest.raises(ReceiptSchemaRefError):
        _receipt_schema_refs({"schema_id": "not-a-list"}, receipt)
    with pytest.raises(ReceiptSchemaRefError):
        _receipt_schema_refs(None, receipt)
    with pytest.raises(ReceiptSchemaRefError):
        _receipt_schema_refs([later, "not-a-dict"], receipt)


def test_receipt_schema_refs_replace_identical_payload() -> None:
    """An identical same-schema payload is replaced and every other ref stays."""
    from idis.services.ingestion.service import _receipt_schema_refs

    receipt = {
        "marker": "same-receipt",
        "schema_version": "document-no-substantive-content-receipt-v2",
    }
    identical = {
        "schema_version": receipt["schema_version"],
        "marker": receipt["marker"],
    }
    existing = {
        "schema_id": "no-substantive-content-receipt",
        "schema_version": "stale-version",
        "schema_sha256": "c" * 64,
        "validated_payload": identical,
    }
    later = {
        "schema_id": "ocr-terminal-evidence",
        "schema_version": "ocr-terminal-evidence-v5",
        "schema_sha256": "d" * 64,
        "validated_payload": {"marker": "kept"},
    }
    result = _receipt_schema_refs([existing, later], receipt)
    assert [item["schema_id"] for item in result] == [
        "no-substantive-content-receipt",
        "ocr-terminal-evidence",
    ]
    assert result[0]["validated_payload"] == receipt
    assert result[0]["schema_version"] == "document-no-substantive-content-receipt-v1"
    assert result[1] == later


def test_receipt_schema_refs_reject_different_payload() -> None:
    """A same-schema ref whose payload differs is refused, not replaced."""
    from idis.services.ingestion.service import ReceiptSchemaRefError, _receipt_schema_refs

    receipt = {"schema_version": "document-no-substantive-content-receipt-v2", "marker": "new"}
    conflicting = {
        "schema_id": "no-substantive-content-receipt",
        "schema_version": "document-no-substantive-content-receipt-v1",
        "schema_sha256": "e" * 64,
        "validated_payload": {"schema_version": receipt["schema_version"], "marker": "old"},
    }
    later = {
        "schema_id": "ocr-terminal-evidence",
        "schema_version": "ocr-terminal-evidence-v5",
        "schema_sha256": "f" * 64,
        "validated_payload": {"marker": "must-not-be-returned"},
    }
    with pytest.raises(ReceiptSchemaRefError):
        _receipt_schema_refs([conflicting, later], receipt)
    with pytest.raises(ReceiptSchemaRefError):
        _receipt_schema_refs([{"schema_id": "no-substantive-content-receipt"}], receipt)


def _attempt_rows(conn: Connection, document_id: str) -> list[Any]:
    return list(
        conn.execute(
            text("SELECT * FROM document_parse_attempts WHERE document_id=:document"),
            {"document": document_id},
        )
        .mappings()
        .all()
    )


def test_image_pdf_without_watermark_classification_gains_no_attempt(
    app_engine: Engine, store: ComplianceEnforcedStore
) -> None:
    """OCR participation without a watermark classification writes no attempt."""
    tenant, deal = _scope(app_engine)
    data = _create_image_text_pdf("PUBLIC SYNTHETIC UNCLASSIFIED PAGE")
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        service = IngestionService(
            compliant_store=store,
            ocr_config=OcrConfig(enabled=True, adapter=TesseractOcrAdapter(), max_pages=1),
            db_conn=conn,
        )
        result = _upload(service, tenant, deal, data)
    assert result.document_id is not None
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        row = conn.execute(
            text("SELECT metadata FROM documents WHERE document_id=:document"),
            {"document": str(result.document_id)},
        ).one()
        assert row.metadata["parser_mode"] == "ocr"
        assert (
            row.metadata["source_preparation_prerequisite"] == "PARSER_NATIVE_IDENTITY_UNAVAILABLE"
        )
        assert "watermark_page_classifications" not in row.metadata
        assert _attempt_rows(conn, str(result.document_id)) == []


def test_verified_watermark_exclusion_persists_one_pre_ocr_attempt(
    app_engine: Engine,
    store: ComplianceEnforcedStore,
    tmp_path: Path,
) -> None:
    """An attested pre-OCR exclusion stores one attempt over the stored projection."""
    from idis.persistence.repositories.task7_source_freeze import (
        _sha256_json,
        _terminal_document_projection,
    )
    from tests._cir04c3_exclusion_signer import (
        EXCLUSION_UPLOAD_NAME,
        watermark_only_pdf,
        watermark_service_options,
        write_signed_watermark_authority,
    )

    tenant, deal = _scope(app_engine)
    pdf_bytes = watermark_only_pdf()
    fingerprint = hashlib.sha256(b"c3-provenance-watermark-attempt").hexdigest()
    authority = write_signed_watermark_authority(
        tmp_path,
        tenant_id=tenant,
        safe_alias=EXCLUSION_UPLOAD_NAME,
        source_tree_fingerprint=fingerprint,
        pdf_bytes=pdf_bytes,
    )
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        service = IngestionService(
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            db_conn=conn,
            **watermark_service_options(authority),
        )
        result = _upload(
            service,
            tenant,
            deal,
            pdf_bytes,
            name=EXCLUSION_UPLOAD_NAME,
            metadata={
                "source_system": "c3-watermark-exclusion",
                "upload_filename": EXCLUSION_UPLOAD_NAME,
                "source_tree_fingerprint": fingerprint,
            },
        )
    assert result.success is False
    assert result.document_id is not None
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        document = (
            conn.execute(
                text(
                    "SELECT doc_type, parse_status, metadata FROM documents "
                    "WHERE document_id=:document"
                ),
                {"document": str(result.document_id)},
            )
            .mappings()
            .one()
        )
        artifact_metadata = conn.execute(
            text(
                """
                SELECT artifacts.metadata
                FROM document_artifacts AS artifacts
                JOIN documents ON documents.doc_id = artifacts.doc_id
                WHERE documents.document_id = :document
                """
            ),
            {"document": str(result.document_id)},
        ).scalar_one()
        attempts = _attempt_rows(conn, str(result.document_id))
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["write_origin"] == "RUNTIME"
    assert attempt["status"] == "FAILED"
    assert attempt["attempt_ordinal"] == 1
    assert attempt["input_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert attempt["parser_name"] == "idis-ingestion/pre-ocr-watermark/pdf"
    assert str(attempt["parser_version"]).startswith("idis-watermark-pre-ocr-runtime-v1:")
    projection = _terminal_document_projection(
        document["metadata"],
        doc_type=document["doc_type"],
        parse_status=document["parse_status"],
        spans=[],
    )
    assert attempt["receipt_sha256"] == _sha256_json(
        [
            "terminal-document-metadata-v2",
            document["doc_type"],
            document["parse_status"],
            projection,
        ]
    )
    assert "source_preparation_prerequisite" not in document["metadata"]
    assert "source_preparation_prerequisite" not in artifact_metadata
    assert document["metadata"]["parser_mode"] == "ocr"
    values = {
        key: str(attempt[key]) if key.endswith("_id") else attempt[key]
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
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        repository = PostgresDocumentsRepository(conn, tenant)
        repository.create_runtime_parse_attempt(**values)
        changed = dict(values)
        changed["parser_version"] = str(values["parser_version"]) + ":changed"
        with pytest.raises(ValueError, match="runtime parse attempt replay does not match"):
            repository.create_runtime_parse_attempt(**changed)
        assert len(_attempt_rows(conn, str(result.document_id))) == 1


def test_rejected_watermark_receipts_persist_no_attempt(
    app_engine: Engine,
    store: ComplianceEnforcedStore,
    tmp_path: Path,
) -> None:
    """Wrong signer, expiry, and alias mismatch leave no runtime attempt."""
    from datetime import UTC, datetime

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import idis.services.ingestion.service as ingestion_service
    from idis.services.ingestion.no_substantive_content_attestation import (
        DocumentNoSubstantiveContentAttestationError,
    )
    from tests._cir04c3_exclusion_signer import (
        EXCLUSION_UPLOAD_NAME,
        watermark_only_pdf,
        watermark_service_options,
        write_signed_watermark_authority,
    )

    tenant, deal = _scope(app_engine)
    pdf_bytes = watermark_only_pdf()
    fingerprint = hashlib.sha256(b"c3-provenance-watermark-reject").hexdigest()
    authority = write_signed_watermark_authority(
        tmp_path,
        tenant_id=tenant,
        safe_alias=EXCLUSION_UPLOAD_NAME,
        source_tree_fingerprint=fingerprint,
        pdf_bytes=pdf_bytes,
    )
    other_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    other_path = tmp_path / "other-public.pem"
    other_path.write_bytes(other_key)
    wrong_signer = {
        **authority,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(other_path),
    }
    with pytest.raises(DocumentNoSubstantiveContentAttestationError):
        watermark_service_options(wrong_signer)

    class _ExpiredClock(datetime):
        """Service clock past the signed batch expiry."""

        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return datetime(2100, 1, 2, tzinfo=UTC)

    original_clock = ingestion_service.datetime
    ingestion_service.datetime = _ExpiredClock
    try:
        with app_engine.begin() as conn:
            set_tenant_local(conn, tenant)
            service = IngestionService(
                compliant_store=store,
                audit_sink=PostgresAuditSink(),
                db_conn=conn,
                **watermark_service_options(authority),
            )
            expired = _upload(
                service,
                tenant,
                deal,
                pdf_bytes,
                name=EXCLUSION_UPLOAD_NAME,
                metadata={
                    "source_system": "c3-watermark-exclusion",
                    "upload_filename": EXCLUSION_UPLOAD_NAME,
                    "source_tree_fingerprint": fingerprint,
                },
            )
    finally:
        ingestion_service.datetime = original_clock
    assert expired.success is False
    assert expired.document_id is None
    assert expired.errors[0].details["attestation_error_code"] == (
        "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE"
    )

    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        service = IngestionService(
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            db_conn=conn,
            **watermark_service_options(authority),
        )
        mismatched = _upload(
            service,
            tenant,
            deal,
            pdf_bytes,
            name="company-001-document-00099.pdf",
            metadata={
                "source_system": "c3-watermark-exclusion",
                "upload_filename": "company-001-document-00099.pdf",
                "source_tree_fingerprint": fingerprint,
            },
        )
        attempts = conn.execute(
            text("SELECT count(*) FROM document_parse_attempts WHERE tenant_id=:tenant"),
            {"tenant": tenant},
        ).scalar_one()
    assert attempts == 0
    if mismatched.document_id is not None:
        with app_engine.begin() as conn:
            set_tenant_local(conn, tenant)
            stored = conn.execute(
                text("SELECT metadata FROM documents WHERE document_id=:document"),
                {"document": str(mismatched.document_id)},
            ).scalar_one()
        receipt = stored.get("document_no_substantive_content_receipt")
        if isinstance(receipt, dict):
            pages = receipt.get("watermark_page_classifications")
            assert isinstance(pages, list)
            assert all(page.get("batch_membership") != "ATTESTED" for page in pages)
        assert stored.get("source_preparation_prerequisite") == (
            "PARSER_NATIVE_IDENTITY_UNAVAILABLE"
        )


def test_run_route_loads_verified_v2_exclusion_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run route's methodology env is the verified v2 manifest, not ingestion options."""
    from types import SimpleNamespace

    from idis.api.routes.runs import _methodology_configuration
    from idis.services.ingestion.no_substantive_content_attestation import (
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
        load_document_no_substantive_content_attestation,
    )
    from tests._cir04c3_exclusion_signer import (
        EXCLUSION_UPLOAD_NAME,
        watermark_only_pdf,
        write_signed_watermark_authority,
    )

    pdf_bytes = watermark_only_pdf()
    authority = write_signed_watermark_authority(
        tmp_path,
        tenant_id=str(uuid.uuid4()),
        safe_alias=EXCLUSION_UPLOAD_NAME,
        source_tree_fingerprint=hashlib.sha256(b"c3-route-authority").hexdigest(),
        pdf_bytes=pdf_bytes,
    )
    for key, value in authority.items():
        monkeypatch.setenv(key, value)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(strict_runtime_profile=None))
    )
    loaded = load_document_no_substantive_content_attestation(
        env=_methodology_configuration(request)
    )
    assert isinstance(loaded, VerifiedDocumentNoSubstantiveContentAttestationManifest)
