"""Task-4 replay admission for the signed no-substantive-content terminal path."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request, Response

from idis.api.document_upload_replay import (
    DocumentUploadReplayInvariantError,
    validate_upload_replay_invariant,
)
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.idempotency.store import IdempotencyRecord, ScopeKey
from idis.services.ingestion.no_substantive_content_attestation import (
    VerifiedDocumentNoSubstantiveContentAttestation,
)

TENANT_ID = "10000000-0000-4000-8000-000000000001"
ACTOR_ID = "task4-replay-actor"
DEAL_ID = "20000000-0000-4000-8000-000000000001"
DOC_ID = "30000000-0000-4000-8000-000000000001"
DOCUMENT_ID = "40000000-0000-4000-8000-000000000001"
CONTENT_SHA256 = "a" * 64
SOURCE_TREE_FINGERPRINT = "b" * 64
SAFE_ALIAS = "company-002-document-00053.pdf"
PAYLOAD_SHA256 = "sha256:" + "c" * 64
RAW_KEY = "task4-terminal-replay-key"
_DEFAULT_ATTESTATION = object()


def _terminal_body() -> bytes:
    return json.dumps(
        {
            "doc_id": DOC_ID,
            "document_id": DOCUMENT_ID,
            "deal_id": DEAL_ID,
            "doc_type": "DATA_ROOM_FILE",
            "title": SAFE_ALIAS,
            "source_system": "slice100",
            "version_id": CONTENT_SHA256[:12],
            "ingested_at": "2026-07-17T12:00:00Z",
            "parse_status": "FAILED",
            "terminal_disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
            "parsed_doc_type": "PDF",
            "sha256": CONTENT_SHA256,
            "uri": "tenant-safe/object.pdf",
            "metadata": {
                "document_no_substantive_content_receipt": {
                    "disposition": "fictional-initial-value"
                }
            },
            "source_metadata": {
                "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
                "upload_filename": SAFE_ALIAS,
            },
        },
        separators=(",", ":"),
    ).encode()


def _normal_body() -> bytes:
    return json.dumps(
        {
            "doc_id": DOC_ID,
            "document_id": DOCUMENT_ID,
            "parse_status": "PARSED",
            "terminal_disposition": None,
        },
        separators=(",", ":"),
    ).encode()


def _superseded_terminal_body(*, disposition: object, receipt_key: str | None = None) -> bytes:
    metadata: dict[str, object] = {}
    if receipt_key is not None:
        metadata[receipt_key] = {"disposition": disposition}
    return json.dumps(
        {
            "doc_id": DOC_ID,
            "document_id": DOCUMENT_ID,
            "parse_status": "FAILED",
            "terminal_disposition": disposition,
            "metadata": metadata,
        },
        separators=(",", ":"),
    ).encode()


def _record(body: bytes, *, media_type: str = "application/json") -> IdempotencyRecord:
    return IdempotencyRecord(
        payload_sha256=PAYLOAD_SHA256,
        status_code=201,
        media_type=media_type,
        body_bytes=body,
        created_at="2026-07-17T12:00:00Z",
    )


class _ReplayStore:
    def __init__(self, record: IdempotencyRecord) -> None:
        self.record = record

    def get(self, scope_key: ScopeKey, *, conn: object) -> IdempotencyRecord:
        del scope_key, conn
        return self.record

    def delete_expired(self, *, tenant_id: str, older_than: object) -> int:
        del tenant_id, older_than
        return 0


async def _noop_app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
    del scope, receive, send


def _request(*, attestation: object | None = _DEFAULT_ATTESTATION) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"upload", "more_body": False}

    if attestation is _DEFAULT_ATTESTATION:
        attestation = _attestation_and_receipt()[0]
    return Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    task4_ingestion_scope=True,
                    ingestion_service=SimpleNamespace(
                        _no_substantive_content_attestation=attestation,
                    ),
                )
            ),
            "method": "POST",
            "path": f"/v1/deals/{DEAL_ID}/documents/upload",
            "query_string": b"",
            "headers": [(b"idempotency-key", RAW_KEY.encode())],
            "client": ("127.0.0.1", 1234),
            "state": {
                "tenant_context": SimpleNamespace(tenant_id=TENANT_ID, actor_id=ACTOR_ID),
                "openapi_operation_id": "uploadDealDocument",
                "request_body_sha256": PAYLOAD_SHA256,
                "db_conn": object(),
                "request_id": "task4-replay-request",
            },
        },
        receive,
    )


def _durable_document() -> dict[str, Any]:
    return {
        "doc_id": DOC_ID,
        "document_id": DOCUMENT_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "parse_status": "FAILED",
        "document_name": SAFE_ALIAS,
        "sha256": CONTENT_SHA256,
        "metadata": {
            "document_no_substantive_content_receipt": {"disposition": "fictional-initial-value"}
        },
        "source_metadata": {
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
            "upload_filename": SAFE_ALIAS,
        },
    }


def _middleware(record: IdempotencyRecord) -> IdempotencyMiddleware:
    return IdempotencyMiddleware(
        _noop_app,
        postgres_store=_ReplayStore(record),  # type: ignore[arg-type]
        cleanup_interval_seconds=3600,
    )


def _dispatch(request: Request, middleware: IdempotencyMiddleware) -> Response:
    async def forbidden_route(_request: Request) -> Response:
        raise AssertionError("a durable replay must not execute the route")

    return asyncio.run(middleware.dispatch(request, forbidden_route))


def test_terminal_replay_rereads_rls_document_and_zero_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.api import document_upload_replay
    from idis.persistence.repositories import documents as documents_repository

    connection = object()
    request = _request()
    request.state.db_conn = connection
    durable = _durable_document()
    repository_calls: list[tuple[object, str, str, str]] = []
    validation_calls: list[dict[str, object]] = []

    class Repository:
        def __init__(self, conn: object, tenant_id: str) -> None:
            repository_calls.append((conn, tenant_id, "init", ""))

        def get_document(self, document_id: str) -> dict[str, Any]:
            repository_calls.append((connection, TENANT_ID, "get_document", document_id))
            return durable

        def list_spans_by_document(
            self, *, deal_id: str, document_id: str
        ) -> list[dict[str, object]]:
            repository_calls.append((connection, TENANT_ID, deal_id, document_id))
            return []

    def validate(**kwargs: object) -> None:
        validation_calls.append(kwargs)

    monkeypatch.setattr(documents_repository, "PostgresDocumentsRepository", Repository)
    monkeypatch.setattr(document_upload_replay, "validate_upload_replay_invariant", validate)

    response = _dispatch(request, _middleware(_record(_terminal_body())))

    assert response.status_code == 201
    assert response.headers["X-IDIS-Idempotency-Replay"] == "true"
    assert repository_calls == [
        (connection, TENANT_ID, "init", ""),
        (connection, TENANT_ID, "get_document", DOCUMENT_ID),
        (connection, TENANT_ID, DEAL_ID, DOCUMENT_ID),
    ]
    assert len(validation_calls) == 1
    call = validation_calls[0]
    assert (
        call["attestation"]
        is request.app.state.ingestion_service._no_substantive_content_attestation
    )
    assert call["tenant_id"] == TENANT_ID
    assert call["source_tree_fingerprint"] == SOURCE_TREE_FINGERPRINT
    assert call["safe_alias"] == SAFE_ALIAS
    assert call["content_sha256"] == CONTENT_SHA256
    assert call["now"].tzinfo is UTC  # type: ignore[union-attr]
    assert call["durable_document"] == {**durable, "span_count": 0, "spans": []}


@pytest.mark.parametrize("durable_mutation", ["missing", "wrong_status", "has_span"])
def test_terminal_replay_fails_closed_when_current_durable_truth_is_not_exact(
    monkeypatch: pytest.MonkeyPatch,
    durable_mutation: str,
) -> None:
    from idis.persistence.repositories import documents as documents_repository

    durable = _durable_document()
    if durable_mutation == "missing":
        durable_result = None
    else:
        if durable_mutation == "wrong_status":
            durable["parse_status"] = "PARSED"
        durable_result = durable

    class Repository:
        def __init__(self, conn: object, tenant_id: str) -> None:
            del conn, tenant_id

        def get_document(self, document_id: str) -> dict[str, Any] | None:
            del document_id
            return durable_result

        def list_spans_by_document(
            self, *, deal_id: str, document_id: str
        ) -> list[dict[str, object]]:
            del deal_id, document_id
            return [{"span_id": "must-fail"}] if durable_mutation == "has_span" else []

    monkeypatch.setattr(documents_repository, "PostgresDocumentsRepository", Repository)

    response = _dispatch(_request(), _middleware(_record(_terminal_body())))

    assert response.status_code == 500
    assert b'"code":"IDEMPOTENCY_REPLAY_INVARIANT_FAILED"' in response.body
    assert "X-IDIS-Idempotency-Replay" not in response.headers


def test_terminal_replay_requires_current_verified_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.persistence.repositories import documents as documents_repository

    class Repository:
        def __init__(self, conn: object, tenant_id: str) -> None:
            del conn, tenant_id

        def get_document(self, document_id: str) -> dict[str, Any]:
            del document_id
            return _durable_document()

        def list_spans_by_document(
            self, *, deal_id: str, document_id: str
        ) -> list[dict[str, object]]:
            del deal_id, document_id
            return []

    monkeypatch.setattr(documents_repository, "PostgresDocumentsRepository", Repository)
    response = _dispatch(_request(attestation=None), _middleware(_record(_terminal_body())))

    assert response.status_code == 500
    assert b'"code":"IDEMPOTENCY_REPLAY_INVARIANT_FAILED"' in response.body
    assert "X-IDIS-Idempotency-Replay" not in response.headers


def test_normal_cached_201_keeps_existing_fast_replay_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.persistence.repositories import documents as documents_repository

    class ForbiddenRepository:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("normal replay consulted terminal proof")

    monkeypatch.setattr(
        documents_repository,
        "PostgresDocumentsRepository",
        ForbiddenRepository,
    )
    response = _dispatch(_request(attestation=None), _middleware(_record(_normal_body())))

    assert response.status_code == 201
    assert response.body == _normal_body()
    assert response.headers["X-IDIS-Idempotency-Replay"] == "true"


@pytest.mark.parametrize(
    ("body", "media_type"),
    [
        (b"not-json", "application/json"),
        (json.dumps([{"parse_status": "PARSED"}]).encode(), "application/json"),
        (json.dumps({"document_id": DOCUMENT_ID}).encode(), "application/json"),
        (
            json.dumps(
                {
                    "document_id": DOCUMENT_ID,
                    "parse_status": "FAILED",
                    "terminal_disposition": None,
                    "metadata": {},
                }
            ).encode(),
            "application/json",
        ),
        (
            json.dumps(
                {
                    "document_id": DOCUMENT_ID,
                    "parse_status": "PARTIAL",
                    "terminal_disposition": None,
                    "metadata": {},
                }
            ).encode(),
            "application/json",
        ),
        (_normal_body(), "text/plain"),
    ],
    ids=(
        "malformed-json",
        "non-object-json",
        "missing-parse-status",
        "failed-without-terminal-proof",
        "partial-without-terminal-proof",
        "wrong-media-type",
    ),
)
def test_upload_replay_rejects_cached_201_outside_exact_admitted_states(
    body: bytes,
    media_type: str,
) -> None:
    response = _dispatch(
        _request(attestation=None),
        _middleware(_record(body, media_type=media_type)),
    )

    assert response.status_code == 500
    assert b'"code":"IDEMPOTENCY_REPLAY_INVARIANT_FAILED"' in response.body
    assert "X-IDIS-Idempotency-Replay" not in response.headers


@pytest.mark.parametrize(
    "body",
    [
        _superseded_terminal_body(
            disposition="DOCUMENT_ILLEGIBLE_CONFIRMED",
            receipt_key="document_illegibility_receipt",
        ),
        _superseded_terminal_body(disposition="UNKNOWN_TERMINAL_DISPOSITION"),
        _superseded_terminal_body(
            disposition=None,
            receipt_key="document_illegibility_receipt",
        ),
    ],
)
def test_upload_replay_rejects_superseded_or_unknown_terminal_contract(body: bytes) -> None:
    response = _dispatch(_request(attestation=None), _middleware(_record(body)))

    assert response.status_code == 500
    assert b'"code":"IDEMPOTENCY_REPLAY_INVARIANT_FAILED"' in response.body
    assert "X-IDIS-Idempotency-Replay" not in response.headers


def test_terminal_replay_failure_log_never_contains_raw_bindings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from idis.api import document_upload_replay
    from idis.persistence.repositories import documents as documents_repository

    class Repository:
        def __init__(self, conn: object, tenant_id: str) -> None:
            del conn, tenant_id

        def get_document(self, document_id: str) -> dict[str, Any]:
            del document_id
            return _durable_document()

        def list_spans_by_document(
            self, *, deal_id: str, document_id: str
        ) -> list[dict[str, object]]:
            del deal_id, document_id
            return []

    def reject(**kwargs: object) -> None:
        del kwargs
        raise document_upload_replay.DocumentUploadReplayInvariantError()

    monkeypatch.setattr(documents_repository, "PostgresDocumentsRepository", Repository)
    monkeypatch.setattr(document_upload_replay, "validate_upload_replay_invariant", reject)

    response = _dispatch(_request(), _middleware(_record(_terminal_body())))

    assert response.status_code == 500
    rendered = caplog.text
    for forbidden in (RAW_KEY, SAFE_ALIAS, CONTENT_SHA256, SOURCE_TREE_FINGERPRINT):
        assert forbidden not in rendered


def test_test_clock_is_timezone_aware() -> None:
    """Keep the attestation replay contract explicit about UTC instants."""

    assert datetime.now(UTC).tzinfo is UTC


def _attestation_and_receipt() -> tuple[
    VerifiedDocumentNoSubstantiveContentAttestation, dict[str, object]
]:
    approved_at = datetime(2026, 1, 1, tzinfo=UTC)
    expires_at = datetime(2027, 1, 1, tzinfo=UTC)
    attestation = VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version="document-no-substantive-content-attestation-v1",
        signer_id="slice100-reviewer",
        tenant_id=TENANT_ID,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=SAFE_ALIAS,
        content_sha256=CONTENT_SHA256,
        disposition="DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        variant="watermark_only",
        attestation_text_sha256="d" * 64,
        reviewer_id="operator-reviewer",
        approved_at_text="2026-01-01T00:00:00Z",
        expires_at_text="2027-01-01T00:00:00Z",
        approved_at=approved_at,
        expires_at=expires_at,
        payload_sha256="e" * 64,
        envelope_sha256="f" * 64,
        public_key_sha256="1" * 64,
    )
    receipt: dict[str, object] = {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "safe_alias": SAFE_ALIAS,
        "content_sha256": CONTENT_SHA256,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "attestation_text_sha256": "d" * 64,
        "reviewer_id": "operator-reviewer",
        "approved_at": "2026-01-01T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "signer_id": "slice100-reviewer",
        "payload_sha256": "e" * 64,
        "envelope_sha256": "f" * 64,
        "public_key_sha256": "1" * 64,
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": 1,
        "ocr_required_page_count": 1,
        "primary_completed_page_count": 1,
        "rescue_completed_page_count": 1,
        "accepted_text_page_count": 0,
        "span_count": 0,
        "ocr_result_sha256": "2" * 64,
    }
    return attestation, receipt


def _pure_replay_fixture() -> tuple[
    VerifiedDocumentNoSubstantiveContentAttestation, dict[str, object], dict[str, object]
]:
    attestation, receipt = _attestation_and_receipt()
    body: dict[str, object] = {
        "doc_id": DOC_ID,
        "document_id": DOCUMENT_ID,
        "deal_id": DEAL_ID,
        "title": SAFE_ALIAS,
        "parse_status": "FAILED",
        "terminal_disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "sha256": CONTENT_SHA256,
        "metadata": {"document_no_substantive_content_receipt": copy.deepcopy(receipt)},
        "source_metadata": {
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
            "upload_filename": SAFE_ALIAS,
        },
    }
    durable: dict[str, object] = {
        "doc_id": DOC_ID,
        "document_id": DOCUMENT_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "parse_status": "FAILED",
        "span_count": 0,
        "spans": [],
        "metadata": {"document_no_substantive_content_receipt": receipt},
    }
    return attestation, body, durable


def _validate_pure_fixture(
    attestation: VerifiedDocumentNoSubstantiveContentAttestation,
    body: dict[str, object],
    durable: dict[str, object],
) -> None:
    validate_upload_replay_invariant(
        cached_response={
            "status_code": 201,
            "media_type": "application/json",
            "body": body,
        },
        durable_document=durable,
        attestation=attestation,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=SAFE_ALIAS,
        content_sha256=CONTENT_SHA256,
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )


def test_pure_replay_validator_accepts_one_exact_current_binding() -> None:
    attestation, body, durable = _pure_replay_fixture()

    _validate_pure_fixture(attestation, body, durable)


def test_middleware_and_current_attestation_admit_one_exact_terminal_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.persistence.repositories import documents as documents_repository

    _attestation, body, pure_durable = _pure_replay_fixture()
    durable = {
        **_durable_document(),
        "metadata": pure_durable["metadata"],
    }

    class Repository:
        def __init__(self, conn: object, tenant_id: str) -> None:
            del conn, tenant_id

        def get_document(self, document_id: str) -> dict[str, Any]:
            assert document_id == DOCUMENT_ID
            return durable

        def list_spans_by_document(
            self, *, deal_id: str, document_id: str
        ) -> list[dict[str, object]]:
            assert (deal_id, document_id) == (DEAL_ID, DOCUMENT_ID)
            return []

    monkeypatch.setattr(documents_repository, "PostgresDocumentsRepository", Repository)
    response = _dispatch(
        _request(),
        _middleware(_record(json.dumps(body, separators=(",", ":")).encode())),
    )

    assert response.status_code == 201
    assert response.headers["X-IDIS-Idempotency-Replay"] == "true"


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("doc_id",), "50000000-0000-4000-8000-000000000001"),
        (("deal_id",), "60000000-0000-4000-8000-000000000001"),
        (("title",), "different-safe-alias.pdf"),
        (("parse_status",), "PARSED"),
        (("sha256",), "9" * 64),
        (("source_metadata", "source_tree_fingerprint"), "8" * 64),
        (("source_metadata", "upload_filename"), "different-safe-alias.pdf"),
        (("metadata", "document_no_substantive_content_receipt", "content_sha256"), "7" * 64),
        (("metadata", "document_no_substantive_content_receipt", "variant"), "blank"),
    ],
)
def test_pure_replay_validator_rejects_cached_public_binding_drift(
    path: tuple[str, ...],
    replacement: object,
) -> None:
    attestation, body, durable = _pure_replay_fixture()
    target: dict[str, object] = body
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = replacement

    with pytest.raises(DocumentUploadReplayInvariantError) as caught:
        _validate_pure_fixture(attestation, body, durable)

    assert caught.value.code == "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"


def test_pure_replay_validator_rejects_receipt_variant_drift_even_when_both_copies_match() -> None:
    attestation, body, durable = _pure_replay_fixture()
    body_metadata = body["metadata"]
    durable_metadata = durable["metadata"]
    assert isinstance(body_metadata, dict)
    assert isinstance(durable_metadata, dict)
    body_receipt = body_metadata["document_no_substantive_content_receipt"]
    durable_receipt = durable_metadata["document_no_substantive_content_receipt"]
    assert isinstance(body_receipt, dict)
    assert isinstance(durable_receipt, dict)
    body_receipt["variant"] = "blank"
    durable_receipt["variant"] = "blank"

    with pytest.raises(DocumentUploadReplayInvariantError) as caught:
        _validate_pure_fixture(attestation, body, durable)

    assert caught.value.code == "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"
