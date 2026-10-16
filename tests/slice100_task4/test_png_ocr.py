"""Task 4 RED contract for authenticated PNG OCR ingestion.

The tests inject a deterministic OCR boundary; they never invoke Tesseract or
read a private data-room file.  They prove the production upload/service path,
durable span metadata, language wiring, and fail-closed HTTP dispositions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.document import ParseStatus
from idis.models.document_classification import (
    DocumentSupportStatus,
    DocumentTriageStatus,
)
from idis.parsers.ocr import (
    OcrConfig,
    OcrPageText,
    OcrTimeoutError,
    OcrUnavailableError,
)
from idis.services.ingestion import IngestionService
from idis.services.ingestion.defaults import build_default_ocr_config
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.abac_seed import seed_deal_access

_TENANT_ID = "a1000004-0000-4000-8000-000000000001"
_DEAL_ID = "d1000004-0000-4000-8000-000000000001"
_ACTOR_ID = "fictional-task4-operator"
_API_KEY = "fictional-task4-api-key"
_PNG_BYTES = b"\x89PNG\r\n\x1a\nfictional-png-ocr-fixture"
_OCR_TEXT = "FICTIONAL OCR LINE"


class _RecordingDocumentsRepo:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []

    def create_artifact(self, **kwargs: Any) -> None:
        self.artifacts.append(kwargs)

    def create_document(self, **kwargs: Any) -> None:
        self.documents.append(kwargs)

    def create_document_span(self, **kwargs: Any) -> None:
        self.spans.append(kwargs)


class _RecordingIngestionService(IngestionService):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.recording_repo = _RecordingDocumentsRepo()

    def _documents_repo(self, tenant_id: object, *, db_conn: object | None = None) -> Any:
        del tenant_id, db_conn
        return self.recording_repo


class _SuccessOcrAdapter:
    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        assert data == _PNG_BYTES
        assert timeout_seconds > 0
        return [OcrPageText(page_number=1, text=_OCR_TEXT, confidence=0.91)]


class _UnavailableOcrAdapter:
    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, timeout_seconds
        raise OcrUnavailableError("fictional OCR dependency is unavailable")


class _TimeoutOcrAdapter:
    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, timeout_seconds
        raise OcrTimeoutError("fictional OCR timeout")


class _NoTextOcrAdapter:
    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, timeout_seconds
        return []


def _service(
    tmp_path: Path,
    *,
    adapter: object,
) -> tuple[_RecordingIngestionService, InMemoryAuditSink]:
    audit = InMemoryAuditSink()
    service = _RecordingIngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
        ),
        audit_sink=audit,
        ocr_config=OcrConfig(enabled=True, adapter=adapter, timeout_seconds=3),  # type: ignore[arg-type]
    )
    return service, audit


@pytest.fixture
def png_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[tuple[TestClient, _RecordingIngestionService, InMemoryAuditSink]]:
    clear_deals_store()
    clear_document_store()
    adapter = request.param
    service, audit = _service(tmp_path, adapter=adapter)
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                _API_KEY: {
                    "tenant_id": _TENANT_ID,
                    "actor_id": _ACTOR_ID,
                    "name": "Fictional Task 4 Tenant",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                }
            }
        ),
    )
    seed_deal_access(_TENANT_ID, _DEAL_ID, _ACTOR_ID)
    client = TestClient(
        create_app(
            audit_sink=audit,
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            ingestion_service=service,
            service_region="me-south-1",
        ),
        raise_server_exceptions=False,
    )
    try:
        yield client, service, audit
    finally:
        client.close()
        clear_deals_store()
        clear_document_store()


def _upload_png(client: TestClient) -> Any:
    return client.post(
        f"/v1/deals/{_DEAL_ID}/documents/upload",
        headers={
            "X-IDIS-API-Key": _API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": "company-01-file-0001.png",
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(_PNG_BYTES).hexdigest(),
            "source_system": "slice100-strict-upload",
        },
        content=_PNG_BYTES,
    )


@pytest.mark.parametrize("png_api", [_SuccessOcrAdapter()], indirect=True)
def test_authenticated_png_upload_persists_ready_ocr_spans(
    png_api: tuple[TestClient, _RecordingIngestionService, InMemoryAuditSink],
) -> None:
    client, service, audit = png_api
    response = _upload_png(client)

    assert response.status_code == 201
    assert response.json()["parse_status"] == ParseStatus.PARSED.value
    assert len(service.recording_repo.documents) == 1
    document = service.recording_repo.documents[0]
    metadata = document["metadata"]
    assert document["parse_status"] == ParseStatus.PARSED.value
    assert metadata["ocr_performed"] is True
    assert metadata["ocr_mean_confidence"] == 0.91
    assert metadata["parser_mode"] == "ocr"
    assert metadata["parser_support_status"] == DocumentSupportStatus.SUPPORTED.value
    assert metadata["parser_triage_status"] == DocumentTriageStatus.READY.value
    assert metadata["parser_reason_codes"] == ["image_ocr_completed"]

    assert len(service.recording_repo.spans) == 1
    span = service.recording_repo.spans[0]
    assert span["span_type"] == "PAGE_TEXT"
    assert span["locator"] == {"page": 1, "line": 1, "source": "ocr_image"}
    assert span["text_excerpt"] == _OCR_TEXT

    # OCR text is durable evidence in its span only, never in public/audit summaries.
    assert _OCR_TEXT not in response.text
    assert _OCR_TEXT not in json.dumps(service.recording_repo.documents, default=str)
    assert _OCR_TEXT not in json.dumps(audit.events, default=str)


def test_default_ocr_config_wires_explicit_runtime_language() -> None:
    config = build_default_ocr_config(
        env={
            "IDIS_OCR_ENABLED": "1",
            "IDIS_OCR_ADAPTER": "tesseract",
            "IDIS_OCR_LANGUAGE": "ara+eng",
            "IDIS_OCR_DPI": "240",
        }
    )

    assert config is not None
    assert config.enabled is True
    assert getattr(config.adapter, "_language", None) == "ara+eng"
    assert getattr(config.adapter, "_dpi", None) == 240


@pytest.mark.parametrize(
    ("png_api", "expected_reason"),
    [
        (_UnavailableOcrAdapter(), "ocr_unavailable"),
        (_TimeoutOcrAdapter(), "ocr_timeout"),
        (_NoTextOcrAdapter(), "ocr_no_text_extracted"),
    ],
    indirect=["png_api"],
)
def test_png_ocr_failure_is_terminal_not_completed_looking(
    png_api: tuple[TestClient, _RecordingIngestionService, InMemoryAuditSink],
    expected_reason: str,
) -> None:
    client, service, audit = png_api
    response = _upload_png(client)

    assert response.status_code == 422
    encoded = response.text.lower()
    assert expected_reason in encoded
    assert "fictional ocr dependency" not in encoded
    assert "fictional ocr timeout" not in encoded
    assert _OCR_TEXT.lower() not in encoded

    assert len(service.recording_repo.documents) == 1
    document = service.recording_repo.documents[0]
    assert document["parse_status"] == ParseStatus.FAILED.value
    assert expected_reason in document["metadata"]["parse_error_codes"]
    assert service.recording_repo.spans == []
    assert _OCR_TEXT not in json.dumps(audit.events, default=str)
