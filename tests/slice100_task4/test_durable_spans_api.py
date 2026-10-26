"""Task 4 RED contract for restart-safe span reads and reconciliation evidence."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

import idis.evaluation.real_example_terminal_reconciliation as terminal_reconciliation
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation.real_example_source_package import (
    CompanySourceContract,
    SourceFileContract,
)
from idis.idempotency.store import SqliteIdempotencyStore
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
DEAL_A = "33333333-3333-3333-3333-333333333333"
ARTIFACT_ID = "44444444-4444-4444-4444-444444444444"
DOCUMENT_ID = "55555555-5555-5555-5555-555555555555"
SPAN_ID = "66666666-6666-6666-6666-666666666666"
DURABLE_BYTES = b"x" * 1024
CONTENT_HASH = hashlib.sha256(DURABLE_BYTES).hexdigest()
SPAN_HASH = hashlib.sha256(b"durable evidence").hexdigest()
API_KEY_A = "task4-durable-spans-a"
API_KEY_B = "task4-durable-spans-b"
EXPECTED_MEDIA_RUNTIME_PROVENANCE = {
    "schema_version": "faster-whisper-runtime-provenance-v2",
    "adapter": "faster-whisper",
    "adapter_contract_version": "idis-faster-whisper-adapter-v1",
    "runtime_package_version": "fictional-version-1",
    "model_source": "local",
    "model_files_sha256": hashlib.sha256(b"fictional-model").hexdigest(),
    "cpu_threads": 4,
    "num_workers": 1,
    "config_sha256": hashlib.sha256(b"fictional-config").hexdigest(),
}


def test_content_integrity_openapi_contract_is_safe_and_bounded() -> None:
    spec = yaml.safe_load(
        (Path(__file__).parents[2] / "openapi" / "IDIS_OpenAPI_v6_3.yaml").read_text(
            encoding="utf-8"
        )
    )
    operation = spec["paths"]["/v1/documents/{docId}/content-integrity"]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert operation["operationId"] == "getDocumentContentIntegrity"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "doc_id",
        "content_sha256",
        "size_bytes",
        "uri_sha256",
        "version_id",
        "backend",
        "streaming_verified",
    }
    assert not ({"uri", "path", "content", "content_b64", "body"} & set(schema["properties"]))
    assert schema["properties"]["streaming_verified"]["enum"] == [True]

    document_schema = spec["components"]["schemas"]["DocumentArtifact"]
    assert document_schema["properties"]["parsed_doc_type"] == {
        "type": "string",
        "enum": ["PDF", "PPTX", "XLSX", "DOCX", "AUDIO", "VIDEO", "IMAGE", "HTML", "TEXT"],
        "nullable": True,
        "description": (
            "Canonical durable parsed-document type, distinct from artifact classification."
        ),
    }


class _RowsResult:
    def __init__(self, rows: list[MagicMock]) -> None:
        self._rows = rows

    def fetchone(self) -> MagicMock | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[MagicMock]:
        return list(self._rows)


class _RestartedPostgresConnection:
    """Small RLS-aware SQL boundary containing only durable rows."""

    def __init__(self) -> None:
        self.tenant_id: str | None = None
        self.sql: list[str] = []
        self.document_row = MagicMock(
            _mapping={
                "document_id": DOCUMENT_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_A,
                "doc_id": ARTIFACT_ID,
                "doc_type": "PDF",
                "artifact_doc_type": "DATA_ROOM_FILE",
                "parse_status": "PARSED",
                "document_metadata": {
                    "detected_format": "PDF",
                    "parser_support_status": "supported",
                    "parser_triage_status": "ready",
                },
                "artifact_metadata": {
                    "upload_filename": "company-001-document-00001.pdf",
                    "size_bytes": 1024,
                },
                "created_at": "2026-07-16T00:00:00Z",
                "updated_at": "2026-07-16T00:00:00Z",
                "document_name": "company-001-document-00001.pdf",
                "sha256": CONTENT_HASH,
                "uri": "deals/safe/artifact",
            }
        )
        self.span_row = MagicMock(
            _mapping={
                "span_id": SPAN_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_A,
                "document_id": DOCUMENT_ID,
                "span_type": "PAGE_TEXT",
                "locator": {"page": 1, "line": 1},
                "text_excerpt": "durable evidence",
                "content_hash": SPAN_HASH,
                "created_at": "2026-07-16T00:00:00Z",
                "updated_at": "2026-07-16T00:00:00Z",
            }
        )

    def execute(self, statement: object, params: Mapping[str, Any] | None = None) -> _RowsResult:
        sql = str(statement)
        self.sql.append(sql)
        if "SET LOCAL idis.tenant_id" in sql:
            matched = re.search(r"idis\.tenant_id = '([0-9a-f-]+)'", sql)
            assert matched is not None
            self.tenant_id = matched.group(1)
            return _RowsResult([])
        if "WHERE documents.doc_id = :doc_id" in sql:
            visible = self.tenant_id == TENANT_A and (params or {}).get("doc_id") == ARTIFACT_ID
            return _RowsResult([self.document_row] if visible else [])
        if "WHERE documents.document_id = :document_id" in sql:
            visible = (
                self.tenant_id == TENANT_A and (params or {}).get("document_id") == DOCUMENT_ID
            )
            return _RowsResult([self.document_row] if visible else [])
        if "FROM document_spans" in sql:
            visible = (
                self.tenant_id == TENANT_A
                and (params or {}).get("deal_id") == DEAL_A
                and (params or {}).get("document_id") == DOCUMENT_ID
            )
            return _RowsResult([self.span_row] if visible else [])
        raise AssertionError(f"unexpected SQL: {sql}")


class _RestartedIngestionService:
    """A fresh process must never consult its empty in-memory span cache."""

    def __init__(self, compliant_store: ComplianceEnforcedStore) -> None:
        self._compliant_store = compliant_store

    def get_spans(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("restart-safe GET consulted IngestionService._spans")


@pytest.fixture
def restarted_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> tuple[TestClient, _RestartedPostgresConnection]:
    clear_document_store()
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY_A: {
                    "tenant_id": TENANT_A,
                    "actor_id": "task4-reader-a",
                    "name": "Task4 Reader A",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                },
                API_KEY_B: {
                    "tenant_id": TENANT_B,
                    "actor_id": "task4-reader-b",
                    "name": "Task4 Reader B",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                },
            }
        ),
    )
    connection = _RestartedPostgresConnection()
    inner_store = FilesystemObjectStore(base_dir=tmp_path / "persistent-objects")
    inner_store.put(TENANT_A, "deals/safe/artifact", DURABLE_BYTES)
    connection.inner_store = inner_store
    app = create_app(
        audit_sink=InMemoryAuditSink(),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=_RestartedIngestionService(
            ComplianceEnforcedStore(inner_store=inner_store)
        ),
        service_region="me-south-1",
    )

    @app.middleware("http")
    async def _attach_restarted_connection(request: Any, call_next: Any) -> Any:
        request.state.db_conn = connection
        return await call_next(request)

    yield TestClient(app, raise_server_exceptions=False), connection
    clear_document_store()


def test_authenticated_spans_read_survives_restart_from_postgres(
    restarted_client: tuple[TestClient, _RestartedPostgresConnection],
) -> None:
    client, connection = restarted_client

    response = client.get(
        f"/v1/documents/{ARTIFACT_ID}/spans",
        headers={"X-IDIS-API-Key": API_KEY_A},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "span_id": SPAN_ID,
                "document_id": DOCUMENT_ID,
                "span_type": "PAGE_TEXT",
                "locator": {"page": 1, "line": 1},
                "text_excerpt": "durable evidence",
                "content_hash": SPAN_HASH,
            }
        ],
        "total": 1,
    }
    assert any("FROM document_spans" in sql for sql in connection.sql)


def test_authenticated_durable_summary_exposes_canonical_parsed_type_after_restart(
    restarted_client: tuple[TestClient, _RestartedPostgresConnection],
) -> None:
    client, _connection = restarted_client

    response = client.get(
        f"/v1/deals/{DEAL_A}/documents/{DOCUMENT_ID}",
        headers={"X-IDIS-API-Key": API_KEY_A},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["doc_type"] == "DATA_ROOM_FILE"
    assert body["parsed_doc_type"] == "PDF"


def test_restart_safe_spans_read_keeps_cross_tenant_404_oracle(
    restarted_client: tuple[TestClient, _RestartedPostgresConnection],
) -> None:
    client, _connection = restarted_client

    response = client.get(
        f"/v1/documents/{ARTIFACT_ID}/spans",
        headers={"X-IDIS-API-Key": API_KEY_B},
    )

    assert response.status_code == 404


def test_authenticated_content_integrity_rehashes_persistent_bytes_without_emitting_them(
    restarted_client: tuple[TestClient, _RestartedPostgresConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, connection = restarted_client

    def forbidden_materializing_get(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("integrity endpoint materialized the durable object")

    monkeypatch.setattr(connection.inner_store, "get", forbidden_materializing_get)
    response = client.get(
        f"/v1/documents/{ARTIFACT_ID}/content-integrity",
        headers={"X-IDIS-API-Key": API_KEY_A},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "schema_version": "durable-object-integrity-v1",
        "doc_id": ARTIFACT_ID,
        "content_sha256": CONTENT_HASH,
        "size_bytes": len(DURABLE_BYTES),
        "uri_sha256": hashlib.sha256(b"deals/safe/artifact").hexdigest(),
        "version_id": connection.inner_store.head(
            TENANT_A,
            "deals/safe/artifact",
        ).version_id,
        "backend": "compliant:filesystem",
        "streaming_verified": True,
    }
    encoded = json.dumps(body)
    assert "deals/safe/artifact" not in encoded
    assert "eHh4" not in encoded
    assert "content_b64" not in encoded


def test_content_integrity_keeps_cross_tenant_not_found_oracle(
    restarted_client: tuple[TestClient, _RestartedPostgresConnection],
) -> None:
    client, _connection = restarted_client

    response = client.get(
        f"/v1/documents/{ARTIFACT_ID}/content-integrity",
        headers={"X-IDIS-API-Key": API_KEY_B},
    )

    assert response.status_code == 404


def test_content_integrity_fails_closed_on_published_byte_corruption(
    restarted_client: tuple[TestClient, _RestartedPostgresConnection],
) -> None:
    client, connection = restarted_client
    [content_path] = list(connection.inner_store.base_dir.rglob("*.data"))
    content_path.write_bytes(b"y" * len(DURABLE_BYTES))

    response = client.get(
        f"/v1/documents/{ARTIFACT_ID}/content-integrity",
        headers={"X-IDIS-API-Key": API_KEY_A},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "DOCUMENT_CONTENT_INTEGRITY_FAILED"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _company(number: int, *, extension: str, size_bytes: int) -> CompanySourceContract:
    alias = f"company-{number:03d}"
    source = SourceFileContract(
        sequence=0,
        generated_upload_name=f"{alias}-document-00001.{extension}",
        source_locator_sha256=_digest(f"locator:{alias}"),
        content_sha256=_digest(f"content:{alias}"),
        size_bytes=size_bytes,
        extension=extension,
    )
    return CompanySourceContract(
        company_alias=alias,
        deal_alias=f"deal-{number:03d}",
        document_set_alias=f"documents-{number:03d}",
        run_alias=f"run-{number:03d}",
        ledger_alias=f"ledger-{number:03d}",
        package_alias=f"package-{number:03d}",
        source_company_locator_sha256=_digest(f"company-locator:{alias}"),
        file_count=1,
        total_bytes=size_bytes,
        source_contract_sha256=_digest(f"company-contract:{alias}"),
        files=(source,),
    )


class _Response:
    def __init__(self, body: Mapping[str, Any], status_code: int = 200) -> None:
        self.status_code = status_code
        self._body = dict(body)

    def json(self) -> dict[str, Any]:
        return self._body


class _DurableEvidenceApi:
    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self.responses = {url: dict(body) for url, body in responses.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        body = self.responses.get(url)
        return _Response({} if body is None else body, 404 if body is None else 200)


def _collector_fixture() -> tuple[
    tuple[CompanySourceContract, CompanySourceContract],
    dict[str, Any],
    _DurableEvidenceApi,
]:
    threshold = 50 * 1024 * 1024
    companies = (
        _company(1, extension="pdf", size_bytes=2048),
        _company(2, extension="mp4", size_bytes=threshold + 1),
    )
    company_states: list[dict[str, Any]] = []
    responses: dict[str, dict[str, Any]] = {}
    for number, company in enumerate(companies, start=1):
        deal_id = str(uuid.UUID(int=100 + number))
        package_id = str(uuid.UUID(int=200 + number))
        document_id = str(uuid.UUID(int=300 + number))
        artifact_id = str(uuid.UUID(int=400 + number))
        span_id = str(uuid.UUID(int=500 + number))
        source = company.files[0]
        company_states.append(
            {
                **company.safe_header(),
                "deal_id": deal_id,
                "package_id": package_id,
                "manifest_sha256": _digest(f"manifest:{company.company_alias}"),
            }
        )
        responses[f"/v1/deals/{deal_id}/data-room-packages/{package_id}"] = {
            "deal_id": deal_id,
            "package_id": package_id,
            "files": [
                {
                    "sequence": 0,
                    "sha256": source.content_sha256,
                    "extension": source.extension,
                    "document_id": document_id,
                    "doc_id": artifact_id,
                }
            ],
        }
        is_media = source.extension == "mp4"
        media_content_hash = _digest("private excerpt must not enter reconciliation rows")
        media_stt_evidence = [
            {
                "source": "media_transcript",
                "start_ms": 0,
                "end_ms": 1_000,
                "content_hash": media_content_hash,
            }
        ]
        frame_schedule = [0, 500, 1_500, 2_500, 2_999]
        responses[f"/v1/deals/{deal_id}/documents/{document_id}"] = {
            "deal_id": deal_id,
            "document_id": document_id,
            "doc_id": artifact_id,
            "parsed_doc_type": "VIDEO" if is_media else "PDF",
            "sha256": source.content_sha256,
            "uri": f"idis://private-object-locator/{artifact_id}",
            "parse_status": "PARSED",
            "metadata": {
                "parser_support_status": "supported",
                "parser_triage_status": "ready",
                "parser_reason_codes": [
                    "media_evidence_completed" if is_media else "pdf_parse_completed"
                ],
                "parse_warning_codes": [],
                "parse_error_codes": [],
                "ocr_performed": False,
                "media_transcription_performed": is_media,
                **(
                    {
                        "media_frame_ocr_performed": True,
                        "media_runtime_provenance": EXPECTED_MEDIA_RUNTIME_PROVENANCE.copy(),
                        "media_stt_receipt": {
                            "schema_version": "media-stt-receipt-v1",
                            "status": "completed",
                            "segment_count": 1,
                            "evidence_span_count": 1,
                            "evidence_sha256": _canonical_digest(media_stt_evidence),
                            "runtime_provenance_sha256": _canonical_digest(
                                EXPECTED_MEDIA_RUNTIME_PROVENANCE
                            ),
                        },
                        "media_frame_ocr_receipt": {
                            "schema_version": "media-frame-ocr-receipt-v1",
                            "status": "completed",
                            "sampling_policy": "periodic-plus-scenes-v1",
                            "scene_threshold": 0.20,
                            "max_sample_count": 600,
                            "max_frame_width": 1_920,
                            "max_frame_height": 1_080,
                            "duration_ms": 3_000,
                            "scheduled_sample_count": len(frame_schedule),
                            "completed_sample_count": len(frame_schedule),
                            "nonempty_sample_count": 0,
                            "evidence_span_count": 0,
                            "schedule_sha256": _canonical_digest(frame_schedule),
                            "evidence_sha256": _canonical_digest([]),
                            "confidence_count": 0,
                            "mean_confidence": None,
                            "minimum_confidence": None,
                            "maximum_confidence": None,
                        },
                    }
                    if is_media
                    else {}
                ),
                **({"pdf_encryption_disposition": "not_encryption_marked"} if not is_media else {}),
            },
            "source_metadata": {
                "upload_filename": source.generated_upload_name,
                "size_bytes": source.size_bytes,
                "upload_intake": "single_document_upload",
                "streaming_contract_version": "bounded-streaming-v1",
                "streaming_size_verified": True,
                "streaming_sha256_verified": True,
            },
        }
        responses[f"/v1/documents/{artifact_id}/content-integrity"] = {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": artifact_id,
            "content_sha256": source.content_sha256,
            "size_bytes": source.size_bytes,
            "uri_sha256": _digest(f"idis://private-object-locator/{artifact_id}"),
            "version_id": str(uuid.UUID(int=600 + number)),
            "backend": "compliant:filesystem",
            "streaming_verified": True,
        }
        responses[f"/v1/documents/{artifact_id}/spans"] = {
            "items": [
                {
                    "span_id": span_id,
                    "document_id": document_id,
                    "span_type": "TIMECODE" if is_media else "PAGE_TEXT",
                    "locator": (
                        {"source": "media_transcript", "start_ms": 0, "end_ms": 1000}
                        if is_media
                        else {"page": 1, "private_marker": "do-not-emit"}
                    ),
                    "text_excerpt": "private excerpt must not enter reconciliation rows",
                    "content_hash": media_content_hash,
                }
            ],
            "total": 1,
        }
    summary = {
        "schema_version": "slice100-source-preparation-summary-v1",
        "total_file_count": 2,
        "companies": company_states,
    }
    return companies, summary, _DurableEvidenceApi(responses)


def _ocr_collector_fixture(
    *, extension: str
) -> tuple[tuple[CompanySourceContract, ...], dict[str, Any], _DurableEvidenceApi]:
    """Build one durable OCR document without exposing a real source name."""
    company = _company(1, extension=extension, size_bytes=2048)
    source = company.files[0]
    deal_id = str(uuid.UUID(int=701))
    package_id = str(uuid.UUID(int=702))
    document_id = str(uuid.UUID(int=703))
    artifact_id = str(uuid.UUID(int=704))
    if extension == "png":
        metadata = {
            "parser_support_status": "supported",
            "parser_triage_status": "ready",
            "parser_reason_codes": ["image_ocr_completed"],
            "parse_warning_codes": [],
            "parse_error_codes": [],
            "ocr_performed": True,
            "ocr_image_count": 1,
            "ocr_mean_confidence": 0.81,
            "media_transcription_performed": False,
        }
        span_specs = [
            ("fictional image line one", {"page": 1, "line": 1, "source": "ocr_image"}),
            ("fictional image line two", {"page": 1, "line": 2, "source": "ocr_image"}),
        ]
    elif extension == "pdf":
        metadata = {
            "parser_support_status": "supported",
            "parser_triage_status": "ready",
            "parser_reason_codes": ["pdf_ocr_completed"],
            "parse_warning_codes": [],
            "parse_error_codes": [],
            "ocr_performed": True,
            "ocr_page_count": 2,
            "ocr_required_page_count": 2,
            "ocr_mean_confidence": 0.75,
            "ocr_confidence_by_page": [0.8, 0.7],
            "ocr_rescue_page_count": 0,
            "ocr_rescue_span_count": 0,
            "ocr_rescue_receipt_sha256": hashlib.sha256(b"[]").hexdigest(),
            "media_transcription_performed": False,
            "pdf_encryption_disposition": "not_encryption_marked",
        }
        span_specs = [
            ("fictional OCR page one", {"page": 1, "line": 1, "source": "ocr"}),
            ("fictional native page two", {"page": 2, "line": 1}),
            ("fictional OCR page three", {"page": 3, "line": 1, "source": "ocr"}),
        ]
    else:  # pragma: no cover - test-helper misuse
        raise AssertionError(f"unsupported OCR fixture extension: {extension}")

    responses: dict[str, dict[str, Any]] = {
        f"/v1/deals/{deal_id}/data-room-packages/{package_id}": {
            "deal_id": deal_id,
            "package_id": package_id,
            "files": [
                {
                    "sequence": 0,
                    "sha256": source.content_sha256,
                    "extension": source.extension,
                    "document_id": document_id,
                    "doc_id": artifact_id,
                }
            ],
        },
        f"/v1/deals/{deal_id}/documents/{document_id}": {
            "deal_id": deal_id,
            "document_id": document_id,
            "doc_id": artifact_id,
            "parsed_doc_type": "IMAGE" if extension == "png" else "PDF",
            "sha256": source.content_sha256,
            "uri": f"idis://private-object-locator/{artifact_id}",
            "parse_status": "PARSED",
            "metadata": metadata,
            "source_metadata": {
                "upload_filename": source.generated_upload_name,
                "size_bytes": source.size_bytes,
                "upload_intake": "single_document_upload",
                "streaming_contract_version": "bounded-streaming-v1",
                "streaming_size_verified": True,
                "streaming_sha256_verified": True,
            },
        },
        f"/v1/documents/{artifact_id}/content-integrity": {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": artifact_id,
            "content_sha256": source.content_sha256,
            "size_bytes": source.size_bytes,
            "uri_sha256": _digest(f"idis://private-object-locator/{artifact_id}"),
            "version_id": str(uuid.UUID(int=705)),
            "backend": "compliant:filesystem",
            "streaming_verified": True,
        },
        f"/v1/documents/{artifact_id}/spans": {
            "items": [
                {
                    "span_id": str(uuid.UUID(int=710 + index)),
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": locator,
                    "text_excerpt": excerpt,
                    "content_hash": _digest(excerpt),
                }
                for index, (excerpt, locator) in enumerate(span_specs)
            ],
            "total": len(span_specs),
        },
    }
    summary = {
        "schema_version": "slice100-source-preparation-summary-v1",
        "total_file_count": 1,
        "companies": [
            {
                **company.safe_header(),
                "deal_id": deal_id,
                "package_id": package_id,
                "manifest_sha256": _digest("fictional-ocr-manifest"),
            }
        ],
    }
    return (company,), summary, _DurableEvidenceApi(responses)


def _collect_ocr_fixture(*, extension: str) -> tuple[list[dict[str, Any]], _DurableEvidenceApi]:
    companies, summary, api = _ocr_collector_fixture(extension=extension)
    rows = terminal_reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key="secret-acceptance-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
    )
    return rows, api


def test_authenticated_collector_feeds_reconciliation_without_private_text() -> None:
    companies, summary, api = _collector_fixture()
    collect = getattr(terminal_reconciliation, "collect_durable_terminal_rows", None)
    assert callable(collect), "Task 4 durable terminal-row collector is not implemented"

    rows = collect(
        api_client=api,
        api_key="secret-acceptance-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
    )

    result = terminal_reconciliation.reconcile_terminal_ingestion(
        source_companies=companies,
        terminal_rows=rows,
        expected_company_counts={"company-001": 1, "company-002": 1},
        expected_format_counts={"pdf": 1, "mp4": 1},
        expected_total_files=2,
        expected_ocr_files=0,
        expected_stt_files=1,
        expected_large_files=1,
        large_file_threshold_bytes=50 * 1024 * 1024,
        expected_pdf_encryption_dispositions={
            "not_encryption_marked": 1,
            "empty_credential_openable": 0,
            "configured_credential_openable": 0,
            "credential_required": 0,
            "credential_invalid": 0,
        },
        expected_media_runtime_provenance=EXPECTED_MEDIA_RUNTIME_PROVENANCE,
        expected_pdf_ocr_content_sha256s=frozenset(),
    )
    assert result["status"] == "ready"
    assert all(
        call[1]["headers"] == {"X-IDIS-API-Key": "secret-acceptance-key"} for call in api.calls
    )
    encoded = json.dumps(rows)
    assert "private excerpt" not in encoded
    assert "do-not-emit" not in encoded
    assert "secret-acceptance-key" not in encoded
    assert "private-object-locator" not in encoded
    assert rows[0]["durable_spans"][0]["locator"].keys() == {"locator_sha256"}
    assert rows[1]["durable_spans"][0]["locator"] == {
        "source": "media_transcript",
        "start_ms": 0,
        "end_ms": 1000,
    }
    assert all(
        set(row["durable_object_integrity"])
        == {
            "schema_version",
            "content_sha256",
            "size_bytes",
            "uri_sha256",
            "version_id",
            "backend",
            "streaming_verified",
        }
        for row in rows
    )


@pytest.mark.parametrize("replacement", [None, "HTML"])
def test_durable_collector_requires_source_bound_canonical_parsed_type(
    replacement: str | None,
) -> None:
    companies, summary, api = _collector_fixture()
    document_url = next(
        url for url in api.responses if "/documents/" in url and "/v1/deals/" in url
    )
    if replacement is None:
        api.responses[document_url].pop("parsed_doc_type")
    else:
        api.responses[document_url]["parsed_doc_type"] = replacement

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )

    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("content_sha256", _digest("forged-object")),
        ("size_bytes", 1),
        ("uri_sha256", _digest("forged-uri")),
        ("version_id", "not-a-version"),
        ("backend", "inmemory"),
        ("streaming_verified", False),
    ],
)
def test_durable_collector_rejects_drifted_persistent_object_integrity_proof(
    field: str,
    replacement: object,
) -> None:
    companies, summary, api = _collector_fixture()
    integrity_url = next(url for url in api.responses if url.endswith("/content-integrity"))
    api.responses[integrity_url][field] = replacement

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )

    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_durable_collector_requires_authenticated_persistent_object_integrity_proof() -> None:
    companies, summary, api = _collector_fixture()
    integrity_url = next(url for url in api.responses if url.endswith("/content-integrity"))
    del api.responses[integrity_url]

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )

    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize("extension", ["png", "pdf"])
def test_ocr_collector_proves_confidence_and_reduces_private_locators(extension: str) -> None:
    rows, _api = _collect_ocr_fixture(extension=extension)

    assert len(rows) == 1
    evidence = rows[0]["ocr_evidence"]
    assert evidence["schema_version"] == "ocr-terminal-evidence-v2"
    assert evidence["ocr_rescue_page_count"] == 0
    assert evidence["ocr_rescue_span_count"] == 0
    assert evidence["ocr_rescue_receipt_sha256"] == hashlib.sha256(b"[]").hexdigest()
    assert 0.0 <= evidence["minimum_confidence"] <= evidence["mean_confidence"] <= 1.0
    assert 0.0 <= evidence["mean_confidence"] <= evidence["maximum_confidence"] <= 1.0
    assert evidence["confidence_count"] == evidence["ocr_page_count"]
    assert evidence["ocr_span_count"] <= len(rows[0]["durable_spans"])
    encoded = json.dumps(rows)
    assert '"page"' not in encoded
    assert '"line"' not in encoded
    assert '"source"' not in encoded
    assert all(set(span["locator"]) == {"locator_sha256"} for span in rows[0]["durable_spans"])


@pytest.mark.parametrize("extension", ["png", "pdf"])
@pytest.mark.parametrize("confidence", [None, float("nan"), float("inf"), -0.01, 1.01])
def test_ocr_collector_rejects_missing_nonfinite_or_out_of_range_mean_confidence(
    extension: str,
    confidence: object,
) -> None:
    companies, summary, api = _ocr_collector_fixture(extension=extension)
    document_url = next(
        url for url in api.responses if "/documents/" in url and "/spans" not in url
    )
    api.responses[document_url]["metadata"]["ocr_mean_confidence"] = confidence

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize("confidence", [None, float("nan"), float("inf"), -0.01, 1.01])
def test_pdf_ocr_collector_rejects_invalid_page_confidence(confidence: object) -> None:
    companies, summary, api = _ocr_collector_fixture(extension="pdf")
    document_url = next(
        url for url in api.responses if "/documents/" in url and "/spans" not in url
    )
    api.responses[document_url]["metadata"]["ocr_confidence_by_page"][0] = confidence

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    "metadata_mutation",
    [
        {"ocr_confidence_by_page": [0.8]},
        {"ocr_page_count": 1},
        {"ocr_required_page_count": 3},
        {"ocr_mean_confidence": 0.74},
    ],
)
def test_pdf_ocr_collector_rejects_confidence_or_page_coverage_drift(
    metadata_mutation: dict[str, Any],
) -> None:
    companies, summary, api = _ocr_collector_fixture(extension="pdf")
    document_url = next(
        url for url in api.responses if "/documents/" in url and "/spans" not in url
    )
    api.responses[document_url]["metadata"].update(metadata_mutation)

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    ("extension", "replacement"),
    [
        ("png", {"line": 1, "source": "ocr_image"}),
        ("png", {"page": 0, "line": 1, "source": "ocr_image"}),
        ("png", {"page": 1.0, "line": 1, "source": "ocr_image"}),
        ("png", {"page": 1, "line": 0, "source": "ocr_image"}),
        ("png", {"page": 1, "line": 1, "source": "fictional_wrong_source"}),
        ("png", {"page": 1, "line": 1, "source": "ocr_image", "junk": True}),
        ("pdf", {"page": 1, "source": "ocr"}),
        ("pdf", {"page": 0, "line": 1, "source": "ocr"}),
        ("pdf", {"page": 1, "line": 1.0, "source": "ocr"}),
        ("pdf", {"page": 1, "line": 0, "source": "ocr"}),
        ("pdf", {"page": 1, "line": 1, "source": "fictional_wrong_source"}),
        ("pdf", {"page": 1, "line": 1, "source": "ocr", "junk": True}),
    ],
)
def test_ocr_collector_rejects_invalid_or_junk_page_line_locator(
    extension: str,
    replacement: dict[str, Any],
) -> None:
    companies, summary, api = _ocr_collector_fixture(extension=extension)
    spans_url = next(url for url in api.responses if url.endswith("/spans"))
    api.responses[spans_url]["items"][0]["locator"] = replacement

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_durable_collector_fails_closed_when_span_hash_is_missing() -> None:
    companies, summary, api = _collector_fixture()
    package_url = next(url for url in api.responses if "/data-room-packages/" in url)
    artifact_id = api.responses[package_url]["files"][0]["doc_id"]
    del api.responses[f"/v1/documents/{artifact_id}/spans"]["items"][0]["content_hash"]
    collect = getattr(terminal_reconciliation, "collect_durable_terminal_rows", None)
    assert callable(collect), "Task 4 durable terminal-row collector is not implemented"

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        collect(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_durable_collector_fails_closed_when_span_hash_does_not_match_excerpt() -> None:
    companies, summary, api = _collector_fixture()
    package_url = next(url for url in api.responses if "/data-room-packages/" in url)
    artifact_id = api.responses[package_url]["files"][0]["doc_id"]
    api.responses[f"/v1/documents/{artifact_id}/spans"]["items"][0]["content_hash"] = _digest(
        "forged-span-hash"
    )

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize("excerpt", [None, "", "   "])
def test_durable_collector_fails_closed_when_span_excerpt_is_empty(excerpt: object) -> None:
    companies, summary, api = _collector_fixture()
    package_url = next(url for url in api.responses if "/data-room-packages/" in url)
    artifact_id = api.responses[package_url]["files"][0]["doc_id"]
    api.responses[f"/v1/documents/{artifact_id}/spans"]["items"][0]["text_excerpt"] = excerpt

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_durable_collector_requires_explicit_parser_warning_proof() -> None:
    companies, summary, api = _collector_fixture()
    document_url = next(
        url for url in api.responses if "/documents/" in url and "/spans" not in url
    )
    api.responses[document_url]["metadata"].pop("parse_warning_codes")

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_durable_collector_does_not_promote_parser_warning_to_success() -> None:
    companies, summary, api = _collector_fixture()
    document_url = next(
        url for url in api.responses if "/documents/" in url and "/spans" not in url
    )
    api.responses[document_url]["metadata"]["parse_warning_codes"] = ["fictional_parser_warning"]

    rows = terminal_reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key="secret-acceptance-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
    )

    warned_row = next(row for row in rows if row["warning_codes"])
    assert warned_row["terminal_status"] == "PARTIAL"
    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.reconcile_terminal_ingestion(
            source_companies=companies,
            terminal_rows=rows,
            expected_company_counts={"company-001": 1, "company-002": 1},
            expected_format_counts={"pdf": 1, "mp4": 1},
            expected_total_files=2,
            expected_ocr_files=0,
            expected_stt_files=1,
            expected_large_files=1,
            large_file_threshold_bytes=50 * 1024 * 1024,
            expected_pdf_encryption_dispositions={
                "not_encryption_marked": 1,
                "empty_credential_openable": 0,
                "configured_credential_openable": 0,
                "credential_required": 0,
                "credential_invalid": 0,
            },
            expected_media_runtime_provenance=EXPECTED_MEDIA_RUNTIME_PROVENANCE,
            expected_pdf_ocr_content_sha256s=frozenset(),
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_collector_uses_durable_media_metadata_not_a_caller_success_boolean() -> None:
    companies, summary, api = _collector_fixture()
    media_document_url = next(
        url
        for url, body in api.responses.items()
        if "/documents/" in url
        and "/spans" not in url
        and body.get("metadata", {}).get("media_transcription_performed") is True
    )
    api.responses[media_document_url]["metadata"]["media_transcription_performed"] = False

    # Collector admission itself now requires both execution receipts; it does
    # not emit a completed-looking row for the later reconciler to reject.
    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError):
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )


def test_collector_rejects_missing_service_stamped_streaming_proof() -> None:
    companies, summary, api = _collector_fixture()
    large_document_url = next(
        url
        for url, body in api.responses.items()
        if "/documents/" in url
        and "/spans" not in url
        and body.get("source_metadata", {}).get("size_bytes", 0) > 50 * 1024 * 1024
    )
    api.responses[large_document_url]["source_metadata"].pop("streaming_sha256_verified")

    with pytest.raises(terminal_reconciliation.TerminalIngestionReconciliationError) as caught:
        terminal_reconciliation.collect_durable_terminal_rows(
            api_client=api,
            api_key="secret-acceptance-key",
            source_companies=companies,
            preparation_summary=summary,
            large_file_threshold_bytes=50 * 1024 * 1024,
        )
    assert caught.value.code == terminal_reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE
