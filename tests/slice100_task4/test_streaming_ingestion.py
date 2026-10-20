"""Task 4 RED tests for bounded, resumable, all-format ingestion.

These tests are deliberately offline and synchronous.  They exercise public HTTP,
parser, storage, and preparation behavior with fictional bytes only; no provider,
network, private data-room, or local speech-model call is made.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.middleware.upload_staging import UploadStagingMiddleware
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import AuditSink, AuditSinkError, InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.document_classification import DocumentSupportStatus
from idis.parsers.base import ParseLimits
from idis.parsers.media import MediaConfig, MediaSegmentText
from idis.services.documents.parser_capabilities import capability_for_document
from idis.services.ingestion.service import (
    IngestionContext,
    IngestionService,
    RouteValidatedSha256,
)
from idis.services.ingestion.upload_staging import StagedUpload, UploadStagingError
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.errors import StorageBackendError
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.abac_seed import seed_deal_access
from tests.slice100_task3._support import (
    FakePreparationApi,
    FakeResponse,
    build_source_fixture,
    contract_kwargs,
)

APPROVED_INGEST_MAX_BYTES = 536_870_912
SAFE_LARGE_MP4_SIZES = (56_082_224, 90_059_031, 247_671_244)
_TENANT_ID = "11111111-1111-4111-8111-111111111111"


class _RecordingMediaAdapter:
    """A fictional adapter recording whether handoff is bounded by path or bytes."""

    def __init__(self) -> None:
        self.seen_path: Path | None = None
        self.seen_size_bytes: int | None = None
        self.received_full_bytes = False

    def extract_text(
        self,
        source: object,
        *,
        timeout_seconds: float,
    ) -> list[MediaSegmentText]:
        assert timeout_seconds > 0
        if isinstance(source, (bytes, bytearray, memoryview)):
            self.received_full_bytes = True
            self.seen_size_bytes = len(source)
        else:
            assert isinstance(source, (str, os.PathLike))
            path = Path(source)
            assert path.is_file()
            self.seen_path = path
            self.seen_size_bytes = path.stat().st_size
        return [
            MediaSegmentText(
                start_ms=0,
                end_ms=1_000,
                text="fictional media transcript",
            )
        ]


class _OriginalNameAbaMediaAdapter(_RecordingMediaAdapter):
    """Swap only the caller-owned stage name while reading the parser handoff."""

    def __init__(
        self,
        *,
        original_path: Path,
        original: bytes,
        replacement: bytes,
    ) -> None:
        super().__init__()
        self._original_path = original_path
        self._original = original
        self._replacement = replacement

    def extract_text(
        self,
        source: object,
        *,
        timeout_seconds: float,
    ) -> list[MediaSegmentText]:
        assert timeout_seconds > 0
        assert isinstance(source, (str, os.PathLike))
        parser_path = Path(source)
        self.seen_path = parser_path
        backup = self._original_path.with_name(f".fictional-aba-{uuid.uuid4().hex}.bak")
        self._original_path.replace(backup)
        try:
            self._original_path.write_bytes(self._replacement)
            observed = parser_path.read_bytes()
        finally:
            self._original_path.unlink(missing_ok=True)
            backup.replace(self._original_path)
        assert self._original_path.read_bytes() == self._original
        transcript = (
            "fictional transcript from original a"
            if observed == self._original
            else "fictional transcript from replacement b"
        )
        return [MediaSegmentText(start_ms=0, end_ms=1_000, text=transcript)]


class _FailingAuditSink:
    """Durable-audit-shaped sink that fails every append."""

    def emit(self, _event: dict[str, Any]) -> None:
        raise AuditSinkError("fictional durable audit failure")


class _ChunkGuardedReader:
    """Reader that rejects read-all and exposes content in small pieces."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        assert size > 0, "stream persistence must never call read() without a bound"
        self.read_calls += 1
        if self._offset >= len(self._payload):
            return b""
        bounded_size = min(size, 64 * 1024)
        end = min(len(self._payload), self._offset + bounded_size)
        chunk = self._payload[self._offset : end]
        self._offset = end
        return chunk


class _ExplodingReader:
    """Reader simulating a disconnect after one accepted chunk."""

    def __init__(self) -> None:
        self._first = True

    def read(self, size: int = -1) -> bytes:
        assert size > 0
        if self._first:
            self._first = False
            return b"fictional-partial-media"
        raise OSError("fictional source disconnected")


class _FailedParsePreparationApi(FakePreparationApi):
    """External-API-shaped fixture returning a durable FAILED parse receipt."""

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        response = super().post(url, **kwargs)
        if "/documents/upload" in url and response.status_code == 201:
            response._body["parse_status"] = "FAILED"  # noqa: SLF001 - deliberate API fixture
            document_id = response._body.get("document_id")  # noqa: SLF001
            if isinstance(document_id, str) and document_id in self.state.documents:
                self.state.documents[document_id]["parse_status"] = "FAILED"
        return response


@dataclass(frozen=True)
class _RawHttpResponse:
    status_code: int
    body: bytes
    request_message_count: int

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body)
        assert isinstance(value, dict)
        return value


def _configured_service(
    *,
    tmp_path: Path,
    max_bytes: int,
    adapter: _RecordingMediaAdapter,
    audit_sink: AuditSink,
) -> IngestionService:
    return IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "fictional-object-store")
        ),
        audit_sink=audit_sink,
        max_bytes=max_bytes,
        parse_limits=ParseLimits(max_bytes=max_bytes),
        media_config=MediaConfig(
            enabled=True,
            adapter=adapter,
            timeout_seconds=5.0,
        ),
    )


def _configured_app(
    *,
    monkeypatch: pytest.MonkeyPatch,
    ingestion_service: IngestionService,
    audit_sink: InMemoryAuditSink,
) -> tuple[Any, str, dict[str, str]]:
    clear_deals_store()
    clear_document_store()
    deal_id = str(uuid.uuid4())
    actor_id = "fictional-task4-operator"
    api_key = "fictional-task4-api-key"
    seed_deal_access(_TENANT_ID, deal_id, actor_id)
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                api_key: {
                    "tenant_id": _TENANT_ID,
                    "actor_id": actor_id,
                    "name": "Fictional Task4 Operator",
                    "timezone": "Asia/Qatar",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                }
            }
        ),
    )
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        service_region="me-south-1",
        ingestion_service=ingestion_service,
    )
    headers = {
        "content-type": "application/octet-stream",
        "idempotency-key": f"task4-{uuid.uuid4().hex}",
        "x-idis-api-key": api_key,
    }
    return app, deal_id, headers


def _drive_asgi_upload(
    app: Any,
    *,
    deal_id: str,
    headers: dict[str, str],
    chunks: Iterable[bytes],
    filename: str = "fictional-media-001.mp4",
    sha256: str | None = None,
) -> _RawHttpResponse:
    return asyncio.run(
        _drive_asgi_upload_async(
            app,
            deal_id=deal_id,
            headers=headers,
            chunks=list(chunks),
            filename=filename,
            sha256=sha256,
        )
    )


async def _drive_asgi_upload_async(
    app: Any,
    *,
    deal_id: str,
    headers: dict[str, str],
    chunks: list[bytes],
    filename: str,
    sha256: str | None,
) -> _RawHttpResponse:
    query: dict[str, str] = {
        "filename": filename,
        "doc_type": "DATA_ROOM_FILE",
        "source_system": "fictional-task4-source",
    }
    if sha256 is not None:
        query["sha256"] = sha256
    request_messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    received = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received < len(request_messages):
            message = request_messages[received]
            received += 1
            return message
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    path = f"/v1/deals/{deal_id}/documents/upload"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": urlencode(query).encode("ascii"),
        "root_path": "",
        "headers": [(key.encode("ascii"), value.encode("ascii")) for key, value in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "extensions": {},
    }
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return _RawHttpResponse(
        status_code=int(start["status"]),
        body=body,
        request_message_count=received,
    )


def test_default_ingestion_wires_the_single_validated_limit_into_parser_and_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One reviewed env value must govern intake and every parser limit."""
    from idis.services.ingestion import defaults

    sentinel_store = object()
    monkeypatch.setattr(
        defaults,
        "build_default_compliance_store",
        lambda *args, **kwargs: sentinel_store,
    )

    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env={"IDIS_INGEST_MAX_BYTES": str(APPROVED_INGEST_MAX_BYTES)},
        strict=True,
    )

    assert service._max_bytes == APPROVED_INGEST_MAX_BYTES  # noqa: SLF001
    assert service._parse_limits.max_bytes == APPROVED_INGEST_MAX_BYTES  # noqa: SLF001


@pytest.mark.parametrize("invalid", [None, "0", "not-an-integer", "536870913"])
def test_strict_ingestion_rejects_missing_or_unreviewed_limit(
    monkeypatch: pytest.MonkeyPatch,
    invalid: str | None,
) -> None:
    """Strict startup may not fall back to the historical 50 MiB default."""
    from idis.services.ingestion import defaults

    monkeypatch.setattr(
        defaults,
        "build_default_compliance_store",
        lambda *args, **kwargs: object(),
    )
    env = {} if invalid is None else {"IDIS_INGEST_MAX_BYTES": invalid}

    with pytest.raises(ValueError):
        defaults.build_default_ingestion_service(
            audit_sink=InMemoryAuditSink(),
            env=env,
            strict=True,
        )


@pytest.mark.parametrize("size_bytes", SAFE_LARGE_MP4_SIZES)
def test_all_three_safe_large_mp4_sizes_are_admitted_by_the_reviewed_limit(
    monkeypatch: pytest.MonkeyPatch,
    size_bytes: int,
) -> None:
    """The three private-room size aggregates must no longer classify as too large."""
    from idis.services.ingestion import defaults

    monkeypatch.setattr(
        defaults,
        "build_default_compliance_store",
        lambda *args, **kwargs: object(),
    )
    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env={"IDIS_INGEST_MAX_BYTES": str(APPROVED_INGEST_MAX_BYTES)},
        strict=True,
    )

    capability = capability_for_document(
        filename="fictional-large-media.mp4",
        file_size_bytes=size_bytes,
        max_bytes=service._parse_limits.max_bytes,  # noqa: SLF001
    )
    assert capability.support_status != DocumentSupportStatus.TOO_LARGE
    assert size_bytes <= service._max_bytes  # noqa: SLF001


def test_chunked_upload_never_calls_request_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotency, hashing, storage, and media share a bounded streamed intake."""
    adapter = _RecordingMediaAdapter()
    audit_sink = InMemoryAuditSink()
    payload = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media" * 4_096)
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=audit_sink,
    )
    app, deal_id, headers = _configured_app(
        monkeypatch=monkeypatch,
        ingestion_service=service,
        audit_sink=audit_sink,
    )
    original_body = Request.body

    upload_body_calls = 0

    async def record_upload_body(self: Request) -> bytes:
        nonlocal upload_body_calls
        if self.url.path.endswith("/documents/upload"):
            upload_body_calls += 1
        return await original_body(self)

    monkeypatch.setattr(Request, "body", record_upload_body)
    response = _drive_asgi_upload(
        app,
        deal_id=deal_id,
        headers=headers,
        chunks=(payload[:17], payload[17:8_193], payload[8_193:]),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert response.status_code == 201, response.body
    assert response.json()["parse_status"] == "PARSED"
    assert upload_body_calls == 0, "streaming upload called Request.body()"


def test_upload_middleware_never_returns_success_when_spool_cleanup_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a replay/non-route success must fail closed when its private spool remains."""
    from idis.api.middleware import upload_staging

    private_canary = "C:/fictional-private/spool-secret.bin"

    class CleanupFailingStage:
        size_bytes = 17
        sha256 = "a" * 64

        def cleanup(self) -> None:
            raise UploadStagingError(
                f"unable to delete {private_canary}",
                code="INGEST_STAGING_CLEANUP_FAILED",
            )

    async def fake_stage(*_args: Any, **_kwargs: Any) -> CleanupFailingStage:
        return CleanupFailingStage()

    monkeypatch.setattr(upload_staging, "stage_request_stream", fake_stage)
    app = SimpleNamespace(state=SimpleNamespace(ingestion_service=SimpleNamespace(_max_bytes=100)))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/deals/11111111-1111-4111-8111-111111111111/documents/upload",
            "query_string": b"",
            "headers": [],
            "app": app,
        }
    )

    async def successful_downstream(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "fictional-success"}, status_code=201)

    response = asyncio.run(UploadStagingMiddleware(app).dispatch(request, successful_downstream))

    assert response.status_code == 500
    assert json.loads(response.body)["code"] == "INGEST_STAGING_CLEANUP_FAILED"
    assert private_canary.encode() not in response.body


def test_route_cleans_spool_before_idempotency_can_persist_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup failure must reach idempotency before it snapshots a 201 response."""
    adapter = _RecordingMediaAdapter()
    audit_sink = InMemoryAuditSink()
    payload = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media" * 512)
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=audit_sink,
    )
    app, deal_id, headers = _configured_app(
        monkeypatch=monkeypatch,
        ingestion_service=service,
        audit_sink=audit_sink,
    )
    private_canary = "C:/fictional-private/route-spool-secret.bin"
    real_cleanup = StagedUpload.cleanup
    cleanup_calls = 0

    def fail_route_cleanup_once(staged: StagedUpload) -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise UploadStagingError(
                f"unable to delete {private_canary}",
                code="INGEST_STAGING_CLEANUP_FAILED",
            )
        real_cleanup(staged)

    put_calls = 0
    real_put = SqliteIdempotencyStore.put

    def record_put(self: SqliteIdempotencyStore, *args: Any, **kwargs: Any) -> None:
        nonlocal put_calls
        put_calls += 1
        real_put(self, *args, **kwargs)

    monkeypatch.setattr(StagedUpload, "cleanup", fail_route_cleanup_once)
    monkeypatch.setattr(SqliteIdempotencyStore, "put", record_put)

    response = _drive_asgi_upload(
        app,
        deal_id=deal_id,
        headers=headers,
        chunks=(payload,),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert response.status_code == 500
    assert response.json()["code"] == "INGEST_STAGING_CLEANUP_FAILED"
    assert private_canary.encode() not in response.body
    assert put_calls == 0, "idempotency persisted a success before cleanup was proven"
    assert cleanup_calls == 2, "middleware did not retry cleanup after the route failed closed"


def test_media_receives_a_disk_path_instead_of_full_file_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Media inference must reuse the bounded spool rather than copy full bytes."""
    adapter = _RecordingMediaAdapter()
    audit_sink = InMemoryAuditSink()
    payload = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media" * 4_096)
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=audit_sink,
    )
    app, deal_id, headers = _configured_app(
        monkeypatch=monkeypatch,
        ingestion_service=service,
        audit_sink=audit_sink,
    )

    response = _drive_asgi_upload(
        app,
        deal_id=deal_id,
        headers=headers,
        chunks=(payload[:17], payload[17:8_193], payload[8_193:]),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert response.status_code == 201, response.body
    assert response.json()["parse_status"] == "PARSED"
    assert not adapter.received_full_bytes
    assert adapter.seen_path is not None
    assert adapter.seen_size_bytes == len(payload)


def test_only_successful_put_stream_can_stamp_durable_streaming_proof(tmp_path: Path) -> None:
    """Caller-authored proof keys are overwritten only after streamed storage succeeds."""
    adapter = _RecordingMediaAdapter()
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=InMemoryAuditSink(),
    )
    payload = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media" * 512)
    staged_path = tmp_path / "fictional-staged-media.mp4"
    staged_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    result = service.ingest_file(
        ctx=IngestionContext(
            tenant_id=uuid.UUID(_TENANT_ID),
            actor_id="fictional-task4-operator",
            request_id="task4-stream-proof",
        ),
        deal_id=uuid.uuid4(),
        filename="fictional-media-001.mp4",
        media_type="video/mp4",
        source_path=staged_path,
        size_bytes=len(payload),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={
            "streaming_contract_version": "caller-forged-v0",
            "streaming_size_verified": False,
            "streaming_sha256_verified": False,
        },
    )

    assert result.success is True
    assert result.artifact_id is not None
    artifact = service.get_artifact(uuid.UUID(_TENANT_ID), result.artifact_id)
    assert artifact is not None
    assert artifact.metadata["streaming_contract_version"] == "bounded-streaming-v1"
    assert artifact.metadata["streaming_size_verified"] is True
    assert artifact.metadata["streaming_sha256_verified"] is True


def test_streamed_ingestion_propagates_audit_failure_and_discards_materialized_state(
    tmp_path: Path,
) -> None:
    """A failed durable audit must reach HTTP transaction rollback as an exception."""
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=_RecordingMediaAdapter(),
        audit_sink=_FailingAuditSink(),
    )
    payload = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-audit-media" * 64)
    staged_path = tmp_path / "fictional-audit-failure.mp4"
    staged_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    with pytest.raises(AuditSinkError, match="fictional durable audit failure"):
        service.ingest_file(
            ctx=IngestionContext(
                tenant_id=uuid.UUID(_TENANT_ID),
                actor_id="fictional-task4-operator",
                request_id="task4-audit-failure",
            ),
            deal_id=uuid.uuid4(),
            filename="fictional-media-001.mp4",
            media_type="video/mp4",
            source_path=staged_path,
            size_bytes=len(payload),
            validated_sha256=RouteValidatedSha256.from_digest(
                actual_sha256=digest,
                expected_sha256=digest,
            ),
            metadata={},
        )

    assert service._artifacts == {}  # noqa: SLF001 - no orphan after rollback signal
    assert service._documents == {}  # noqa: SLF001 - no orphan after rollback signal
    assert service._spans == {}  # noqa: SLF001 - no orphan after rollback signal


def test_byte_ingestion_shares_the_same_durable_audit_rollback_boundary(tmp_path: Path) -> None:
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=_RecordingMediaAdapter(),
        audit_sink=_FailingAuditSink(),
    )

    with pytest.raises(AuditSinkError, match="fictional durable audit failure"):
        service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=uuid.UUID(_TENANT_ID),
                actor_id="fictional-task4-operator",
                request_id="task4-byte-audit-failure",
            ),
            deal_id=uuid.uuid4(),
            filename="fictional-evidence.txt",
            media_type="text/plain",
            data=b"fictional durable evidence",
        )

    assert service._artifacts == {}  # noqa: SLF001 - no orphan after rollback signal
    assert service._documents == {}  # noqa: SLF001 - no orphan after rollback signal
    assert service._spans == {}  # noqa: SLF001 - no orphan after rollback signal


def test_same_size_stage_replacement_after_storage_fails_before_parse_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parser evidence must remain bound to the route-validated stored bytes."""
    adapter = _RecordingMediaAdapter()
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=InMemoryAuditSink(),
    )
    original = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media-a" * 128)
    replacement = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media-b" * 128)
    assert len(original) == len(replacement)
    staged_path = tmp_path / "fictional-stage-replaced.mp4"
    staged_path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    real_put_stream = service._compliant_store.put_stream  # noqa: SLF001

    def store_then_replace(*args: Any, **kwargs: Any) -> Any:
        receipt = real_put_stream(*args, **kwargs)
        staged_path.write_bytes(replacement)
        return receipt

    monkeypatch.setattr(service._compliant_store, "put_stream", store_then_replace)  # noqa: SLF001

    result = service.ingest_file(
        ctx=IngestionContext(
            tenant_id=uuid.UUID(_TENANT_ID),
            actor_id="fictional-task4-operator",
            request_id="task4-stage-replacement",
        ),
        deal_id=uuid.uuid4(),
        filename="fictional-media-001.mp4",
        media_type="video/mp4",
        source_path=staged_path,
        size_bytes=len(original),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={},
    )

    assert result.success is False
    assert result.parse_status is not None and result.parse_status.value == "FAILED"
    assert [error.code.value for error in result.errors] == ["parse_failed"]
    assert result.errors[0].details == {"parse_error_code": "corrupted_file"}
    assert adapter.seen_path is None


def test_media_parser_binds_to_verified_inode_during_original_name_aba(
    tmp_path: Path,
) -> None:
    """A rename/swap/restore cannot bind replacement-B spans to stored bytes A."""
    original = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media-a" * 128)
    replacement = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media-b" * 128)
    assert len(original) == len(replacement)
    staged_path = tmp_path / "fictional-stage-aba.mp4"
    staged_path.write_bytes(original)
    adapter = _OriginalNameAbaMediaAdapter(
        original_path=staged_path,
        original=original,
        replacement=replacement,
    )
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=InMemoryAuditSink(),
    )
    digest = hashlib.sha256(original).hexdigest()

    result = service.ingest_file(
        ctx=IngestionContext(
            tenant_id=uuid.UUID(_TENANT_ID),
            actor_id="fictional-task4-operator",
            request_id="task4-stage-aba",
        ),
        deal_id=uuid.uuid4(),
        filename="fictional-media-001.mp4",
        media_type="video/mp4",
        source_path=staged_path,
        size_bytes=len(original),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={},
    )

    assert result.success is True
    assert result.document_id is not None
    spans = service.get_spans(uuid.UUID(_TENANT_ID), result.document_id)
    assert [span.text_excerpt for span in spans] == ["fictional transcript from original a"]
    assert staged_path.read_bytes() == original


def test_media_parser_snapshot_cleanup_failure_cannot_return_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained private parser alias must invalidate an otherwise successful parse."""
    payload = b"\x00\x00\x00\x18ftypmp42" + (b"fictional-media-cleanup" * 64)
    staged_path = tmp_path / "fictional-stage-cleanup.mp4"
    staged_path.write_bytes(payload)
    adapter = _RecordingMediaAdapter()
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=adapter,
        audit_sink=InMemoryAuditSink(),
    )
    real_unlink = Path.unlink

    def reject_private_alias_cleanup(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path.parent.name.startswith(".idis-media-parse-"):
            raise PermissionError("fictional parser alias cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", reject_private_alias_cleanup)
    digest = hashlib.sha256(payload).hexdigest()
    result = service.ingest_file(
        ctx=IngestionContext(
            tenant_id=uuid.UUID(_TENANT_ID),
            actor_id="fictional-task4-operator",
            request_id="task4-stage-cleanup-failure",
        ),
        deal_id=uuid.uuid4(),
        filename="fictional-media-001.mp4",
        media_type="video/mp4",
        source_path=staged_path,
        size_bytes=len(payload),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={},
    )

    assert result.success is False
    assert result.parse_status is not None and result.parse_status.value == "FAILED"
    assert [error.code.value for error in result.errors] == ["parse_failed"]
    assert result.errors[0].details == {"parse_error_code": "corrupted_file"}

    # The production path has already failed closed. Remove the deliberately
    # retained fictional alias with the unpatched primitive so the test is clean.
    if adapter.seen_path is not None and adapter.seen_path != staged_path:
        os.unlink(adapter.seen_path)
        os.rmdir(adapter.seen_path.parent)


def test_byte_ingestion_strips_caller_forged_streaming_proof(tmp_path: Path) -> None:
    """A non-streamed ingestion entry point cannot mint the durable streaming receipt."""
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=_RecordingMediaAdapter(),
        audit_sink=InMemoryAuditSink(),
    )
    tenant_id = uuid.UUID(_TENANT_ID)
    result = service.ingest_bytes(
        ctx=IngestionContext(
            tenant_id=tenant_id,
            actor_id="fictional-task4-operator",
            request_id="task4-forged-stream-proof",
        ),
        deal_id=uuid.uuid4(),
        filename="fictional-note.txt",
        media_type="text/plain",
        data=b"fictional terminal evidence",
        metadata={
            "streaming_contract_version": "bounded-streaming-v1",
            "streaming_size_verified": True,
            "streaming_sha256_verified": True,
        },
    )

    assert result.success is True
    assert result.artifact_id is not None
    artifact = service.get_artifact(tenant_id, result.artifact_id)
    assert artifact is not None
    assert "streaming_contract_version" not in artifact.metadata
    assert "streaming_size_verified" not in artifact.metadata
    assert "streaming_sha256_verified" not in artifact.metadata


def test_failed_put_stream_cannot_persist_caller_forged_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A storage failure returns before any artifact or proof metadata is persisted."""
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=APPROVED_INGEST_MAX_BYTES,
        adapter=_RecordingMediaAdapter(),
        audit_sink=InMemoryAuditSink(),
    )
    payload = b"\x00\x00\x00\x18ftypmp42" + b"fictional-media"
    staged_path = tmp_path / "fictional-failed-stage.mp4"
    staged_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    def fail_stream(*_args: Any, **_kwargs: Any) -> None:
        raise StorageBackendError("fictional stream failure")

    monkeypatch.setattr(service._compliant_store, "put_stream", fail_stream)  # noqa: SLF001
    result = service.ingest_file(
        ctx=IngestionContext(
            tenant_id=uuid.UUID(_TENANT_ID),
            actor_id="fictional-task4-operator",
            request_id="task4-failed-stream-proof",
        ),
        deal_id=uuid.uuid4(),
        filename="fictional-media-001.mp4",
        media_type="video/mp4",
        source_path=staged_path,
        size_bytes=len(payload),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={
            "streaming_contract_version": "bounded-streaming-v1",
            "streaming_size_verified": True,
            "streaming_sha256_verified": True,
        },
    )

    assert result.success is False
    assert result.artifact_id is None
    assert service._artifacts == {}  # noqa: SLF001 - verify no forged durable receipt


def test_chunked_oversize_stops_at_crossing_and_returns_exact_admission_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunked requests fail at max+1 without draining or creating durable state."""
    adapter = _RecordingMediaAdapter()
    audit_sink = InMemoryAuditSink()
    service = _configured_service(
        tmp_path=tmp_path,
        max_bytes=12,
        adapter=adapter,
        audit_sink=audit_sink,
    )
    app, deal_id, headers = _configured_app(
        monkeypatch=monkeypatch,
        ingestion_service=service,
        audit_sink=audit_sink,
    )

    response = _drive_asgi_upload(
        app,
        deal_id=deal_id,
        headers=headers,
        chunks=(b"a" * 8, b"b" * 5, b"must-not-be-consumed"),
    )

    assert response.status_code == 413, response.body
    assert response.json()["code"] == "INGEST_ADMISSION_MAX_SIZE_EXCEEDED"
    assert response.request_message_count == 2
    assert adapter.seen_path is None
    assert list((tmp_path / "fictional-object-store").rglob("*.data")) == []


def test_filesystem_stream_put_is_chunked_and_same_hash_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    """A whole-file retry must reuse one published object version."""
    store = FilesystemObjectStore(base_dir=tmp_path / "object-store")
    payload = b"fictional-stream-content" * 20_000
    expected_hash = hashlib.sha256(payload).hexdigest()
    key = "task4/fictional-stream.mp4"
    first_reader = _ChunkGuardedReader(payload)
    second_reader = _ChunkGuardedReader(payload)

    first = store.put_stream(
        _TENANT_ID,
        key,
        first_reader,
        content_type="video/mp4",
        expected_sha256=expected_hash,
        expected_size_bytes=len(payload),
    )
    second = store.put_stream(
        _TENANT_ID,
        key,
        second_reader,
        content_type="video/mp4",
        expected_sha256=expected_hash,
        expected_size_bytes=len(payload),
    )

    assert first_reader.read_calls > 1
    assert second_reader.read_calls > 1
    assert first.version_id == second.version_id
    assert first.sha256 == second.sha256 == expected_hash
    assert first.size_bytes == second.size_bytes == len(payload)
    assert len(store.list_versions(_TENANT_ID, key)) == 1


def test_filesystem_stream_failure_removes_partial_content_and_temp_files(tmp_path: Path) -> None:
    """A disconnect cannot publish a partial object or leave a private temp file."""
    base_dir = tmp_path / "object-store"
    store = FilesystemObjectStore(base_dir=base_dir)
    key = "task4/fictional-disconnect.mp4"

    with pytest.raises(StorageBackendError):
        store.put_stream(
            _TENANT_ID,
            key,
            _ExplodingReader(),
            content_type="video/mp4",
            expected_sha256="0" * 64,
            expected_size_bytes=100,
        )

    assert store.list_versions(_TENANT_ID, key) == []
    assert [path for path in base_dir.rglob("*") if ".tmp" in path.name] == []
    assert list(base_dir.rglob("*.data")) == []


def test_failed_parse_receipt_cannot_advance_terminal_success_ledger(tmp_path: Path) -> None:
    """A durable artifact with parse_status FAILED is accounted but never successful."""
    from idis.evaluation.real_example_source_package import (
        SourcePackageContractError,
        prepare_two_company_source_packages,
    )

    fixture = build_source_fixture(
        tmp_path,
        files_by_sorted_company=(
            (("fictional-company-a.pdf", b"fictional-a"),),
            (("fictional-company-b.pdf", b"fictional-b"),),
        ),
    )

    with pytest.raises(SourcePackageContractError) as caught:
        prepare_two_company_source_packages(
            deal_stage="SERIES_B",
            api_client=_FailedParsePreparationApi(),
            api_key="fictional-task4-api-key",
            ledger_path=tmp_path / "safe-preparation-ledger.jsonl",
            **contract_kwargs(fixture),
        )

    assert caught.value.code in {
        "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
        "DOCUMENT_UNREADABLE",
    }
