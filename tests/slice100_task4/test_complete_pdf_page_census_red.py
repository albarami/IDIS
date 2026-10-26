"""RED contracts for the complete, disjoint PDF page-disposition census.

The valid fixtures enter through the production ingestion composition root and
the durable API collector.  The operator-backed case binds the real acceptance
artifact by safe alias and content digest without emitting its private path or
text.  Mutations happen only after that production pipeline has produced a
coherent baseline, so hand-assembled parser output cannot mask composition
drift.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import idis.evaluation.real_example_terminal_reconciliation as reconciliation
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation.real_example_source_package import (
    CompanySourceContract,
    SourceFileContract,
    SourcePackageContractError,
    build_two_company_source_contract,
)
from idis.idempotency.store import SqliteIdempotencyStore
from idis.parsers.image_only_page import evaluate_image_only_page_receipts
from idis.parsers.poppler_runtime import PopplerRuntimeBindingError
from idis.persistence.repositories.data_room_packages import clear_data_room_packages_store
from idis.persistence.repositories.runs import clear_in_memory_runs_store
from idis.runtime.profile import StrictProfileError, load_strict_runtime_profile
from idis.services.ingestion import defaults
from idis.services.ingestion.no_substantive_content_attestation import (
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    load_document_no_substantive_content_attestation,
)
from idis.services.ingestion.service import IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests._canonical_poppler_pin import requires_canonical_poppler_pin
from tests._linux_poppler_gate import (
    linux_governed_poppler_absent,
    requires_linux_governed_poppler,
)
from tests.abac_seed import seed_deal_access
from tests.slice100_task4.test_ocr_image_only_composition_root_red import (
    TENANT_ID as SYNTHETIC_TENANT_ID,
)
from tests.slice100_task4.test_ocr_image_only_composition_root_red import (
    _review_records,
    _reviewed_image_pdf,
    _verified_review_manifest,
)

API_KEY = "slice100-page-census-red-key"
ACTOR_ID = "slice100-page-census-red-actor"
REAL_SAFE_ALIAS = "company-002-document-00105.pdf"
REAL_CONTENT_SHA256 = "904e3d52c36d8d006ee0cc0d087891777a52fdd8de0e8856312efba69c61e440"
REAL_SOURCE_SEQUENCE = 104
REAL_PAGE_COUNT = 8
REAL_NATIVE_PAGES = [1, 2, 3, 5, 6, 8]
REAL_IMAGE_ONLY_PAGES = [4, 7]
PAGE_CENSUS_SCHEMA_VERSION = "pdf-page-disposition-census-v1"
IMAGE_ONLY_RECEIPT_SCHEMA_VERSION = "image-only-page-receipt-v2"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class _CollectorFixture:
    company: CompanySourceContract
    preparation_summary: Mapping[str, Any]
    responses: Mapping[str, Mapping[str, Any]]
    expected_pdf_page_count: int


class _Response:
    def __init__(self, status_code: int, body: Mapping[str, Any]) -> None:
        self.status_code = status_code
        self._body = copy.deepcopy(dict(body))

    def json(self) -> dict[str, Any]:
        return copy.deepcopy(self._body)


class _SnapshotApi:
    """Authenticated API-shaped read boundary over production route snapshots."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self.responses = copy.deepcopy(dict(responses))
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        headers = cast(Mapping[str, str], kwargs.get("headers") or {})
        self.calls.append((url, dict(headers)))
        body = self.responses.get(url)
        if body is None:
            return _Response(404, {"code": "NOT_FOUND"})
        return _Response(200, body)


def _company_contract(
    *,
    source: SourceFileContract,
) -> CompanySourceContract:
    return CompanySourceContract(
        company_alias="company-002",
        deal_alias="deal-002",
        document_set_alias="documents-002",
        run_alias="run-002",
        ledger_alias="ledger-002",
        package_alias="package-002",
        source_company_locator_sha256=_digest("page-census-company-locator"),
        file_count=1,
        total_bytes=source.size_bytes,
        source_contract_sha256=_digest("page-census-company-contract"),
        files=(source,),
    )


def _reset_api_stores() -> None:
    clear_deals_store()
    clear_document_store()
    clear_data_room_packages_store()
    clear_in_memory_runs_store()


def _build_service(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: Mapping[str, str],
    verified_manifest: VerifiedDocumentNoSubstantiveContentAttestationManifest,
) -> IngestionService:
    store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
    )
    monkeypatch.setattr(defaults, "build_default_compliance_store", lambda: store)
    monkeypatch.setattr(
        defaults,
        "load_document_no_substantive_content_attestation",
        lambda *, env: verified_manifest,
    )
    return defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env=env,
        strict=True,
    )


def _upload_and_snapshot(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: IngestionService,
    tenant_id: UUID,
    source_tree_fingerprint: str,
    source_path: Path,
    source: SourceFileContract,
) -> _CollectorFixture:
    """Upload through the real API route, then snapshot every collector GET route."""

    source_bytes = source_path.read_bytes()
    expected_pdf_page_count, _required_pages = reconciliation._inspect_pdf_page_requirements(
        source_bytes
    )
    _reset_api_stores()
    for name in (
        "IDIS_STRICT_DOTENV_PATH",
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_REDIS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IDIS_SERVICE_REGION", "me-south-1")
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": str(tenant_id),
                    "actor_id": ACTOR_ID,
                    "name": "Fictional Page Census Tenant",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    app = create_app(
        audit_sink=InMemoryAuditSink(),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=service,
        service_region="me-south-1",
    )
    headers = {"X-IDIS-API-Key": API_KEY}
    responses: dict[str, Mapping[str, Any]] = {}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            created = client.post(
                "/v1/deals",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "name": "Fictional Page Census Deal",
                    "company_name": "Fictional Census Company",
                },
            )
            assert created.status_code == 201, created.json()
            deal_id = created.json()["deal_id"]
            seed_deal_access(str(tenant_id), deal_id, ACTOR_ID)
            uploaded = client.post(
                f"/v1/deals/{deal_id}/documents/upload",
                headers={
                    **headers,
                    "Content-Type": "application/octet-stream",
                    "X-IDIS-Source-Tree-Fingerprint": source_tree_fingerprint,
                },
                params={
                    "filename": source.generated_upload_name,
                    "doc_type": "DATA_ROOM_FILE",
                    "sha256": source.content_sha256,
                    "source_system": "slice100-page-census-red",
                },
                content=source_bytes,
            )
            assert uploaded.status_code == 201, uploaded.json()
            upload_body = uploaded.json()
            document_id = upload_body["document_id"]
            artifact_id = upload_body["doc_id"]
            package_id = str(uuid.uuid4())
            package_url = f"/v1/deals/{deal_id}/data-room-packages/{package_id}"
            responses[package_url] = {
                "package_id": package_id,
                "deal_id": deal_id,
                "files": [
                    {
                        "sequence": source.sequence,
                        "sha256": source.content_sha256,
                        "extension": source.extension,
                        "document_id": document_id,
                        "doc_id": artifact_id,
                    }
                ],
            }
            for url in (
                f"/v1/deals/{deal_id}/documents/{document_id}",
                f"/v1/documents/{artifact_id}/content-integrity",
                f"/v1/documents/{artifact_id}/spans",
            ):
                response = client.get(url, headers=headers)
                assert response.status_code == 200, response.json()
                responses[url] = response.json()
            # The no-DB public summary store intentionally exposes only the
            # artifact projection.  Bind the same authenticated response
            # envelope to the production ingestion service's durable parsed
            # representation, matching the joined Postgres route used by the
            # acceptance server.
            durable_document = service.get_document(tenant_id, UUID(document_id))
            durable_artifact = service.get_artifact(tenant_id, UUID(artifact_id))
            durable_spans = service.get_spans(tenant_id, UUID(document_id))
            assert durable_document is not None
            assert durable_artifact is not None
            assert durable_spans
            document_url = f"/v1/deals/{deal_id}/documents/{document_id}"
            responses[document_url] = {
                **responses[document_url],
                "deal_id": deal_id,
                "document_id": document_id,
                "doc_id": artifact_id,
                "parsed_doc_type": durable_document.doc_type.value,
                "sha256": durable_artifact.sha256,
                "uri": durable_artifact.uri,
                "parse_status": durable_document.parse_status.value,
                "metadata": copy.deepcopy(durable_document.metadata),
                "source_metadata": copy.deepcopy(durable_artifact.metadata),
            }
            responses[f"/v1/documents/{artifact_id}/spans"] = {
                "items": [span.model_dump(mode="json") for span in durable_spans],
                "total": len(durable_spans),
            }
    finally:
        _reset_api_stores()

    company = _company_contract(source=source)
    preparation_summary = {
        "schema_version": "slice100-source-preparation-summary-v1",
        "total_file_count": 1,
        "companies": [
            {
                **company.safe_header(),
                "deal_id": deal_id,
                "package_id": package_id,
                "manifest_sha256": _digest("page-census-package-manifest"),
            }
        ],
    }
    return _CollectorFixture(
        company=company,
        preparation_summary=MappingProxyType(preparation_summary),
        responses=MappingProxyType(responses),
        expected_pdf_page_count=expected_pdf_page_count,
    )


def _collect(fixture: _CollectorFixture) -> tuple[list[dict[str, Any]], _SnapshotApi]:
    api = _SnapshotApi(fixture.responses)
    rows = reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key=API_KEY,
        source_companies=(fixture.company,),
        preparation_summary=fixture.preparation_summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
        expected_pdf_page_counts_by_content_sha256={
            fixture.company.files[0].content_sha256: fixture.expected_pdf_page_count
        },
    )
    return rows, api


def _reconcile_one(
    fixture: _CollectorFixture,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    encryption_dispositions = {
        "not_encryption_marked": 0,
        "empty_credential_openable": 0,
        "configured_credential_openable": 0,
        "credential_required": 0,
        "credential_invalid": 0,
    }
    observed_encryption_disposition = rows[0]["pdf_encryption_disposition"]
    assert observed_encryption_disposition in encryption_dispositions
    encryption_dispositions[observed_encryption_disposition] = 1
    return reconciliation.reconcile_terminal_ingestion(
        source_companies=(fixture.company,),
        terminal_rows=rows,
        expected_company_counts={"company-002": 1},
        expected_format_counts={"pdf": 1},
        expected_total_files=1,
        expected_ocr_files=1,
        expected_stt_files=0,
        expected_large_files=0,
        large_file_threshold_bytes=50 * 1024 * 1024,
        expected_pdf_encryption_dispositions=encryption_dispositions,
        expected_media_runtime_provenance={
            "schema_version": "faster-whisper-runtime-provenance-v2",
            "adapter": "faster-whisper",
            "adapter_contract_version": "idis-faster-whisper-adapter-v1",
            "runtime_package_version": "not-executed-for-pdf-only-census",
            "model_source": "local",
            "model_files_sha256": _digest("unused-media-model"),
            "cpu_threads": 4,
            "num_workers": 1,
            "config_sha256": _digest("unused-media-config"),
        },
        expected_pdf_ocr_content_sha256s={fixture.company.files[0].content_sha256},
        expected_pdf_page_counts_by_content_sha256={
            fixture.company.files[0].content_sha256: rows[0]["ocr_evidence"]["pdf_page_census"][
                "page_count"
            ]
        },
    )


def _assert_image_only_receipt_v2(receipt: Mapping[str, Any]) -> None:
    assert receipt["schema_version"] == IMAGE_ONLY_RECEIPT_SCHEMA_VERSION
    assert receipt["classification"] == "image_only_page"
    page_number = receipt["page_number"]
    render = receipt["ocr_render_admission"]
    execution = receipt["ocr_page_execution_receipt"]
    assert isinstance(render, Mapping)
    assert isinstance(execution, Mapping)
    assert render["schema_version"] == "ocr-render-admission-v2"
    assert render["actual_render_width"] > 0
    assert render["actual_render_height"] > 0
    assert render["actual_render_pixel_count"] == (
        render["actual_render_width"] * render["actual_render_height"]
    )
    assert execution["page_number"] == page_number
    assert execution["outcome"] == "no_consensus"
    assert execution["primary_completed"] is True
    assert execution["primary_token_count"] == 0
    assert execution["accepted_text"] is False
    assert execution["accepted_text_token_count"] == 0
    canonical = json.dumps(
        dict(execution),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == receipt["ocr_terminal_proof_sha256"]


def _assert_pdf_page_census(
    value: object,
    *,
    page_count: int,
    native_text_pages: list[int],
    ocr_text_pages: list[int],
    image_only_pages: list[int],
    watermark_only_pages: list[int],
) -> None:
    assert isinstance(value, Mapping)
    census = dict(value)
    assert set(census) == {
        "schema_version",
        "page_count",
        "page_dispositions",
        "native_text_page_numbers",
        "ocr_text_page_numbers",
        "image_only_page_numbers",
        "watermark_only_page_numbers",
        "class_counts",
        "census_sha256",
    }
    assert census["schema_version"] == PAGE_CENSUS_SCHEMA_VERSION
    assert census["page_count"] == page_count
    assert census["native_text_page_numbers"] == native_text_pages
    assert census["ocr_text_page_numbers"] == ocr_text_pages
    assert census["image_only_page_numbers"] == image_only_pages
    assert census["watermark_only_page_numbers"] == watermark_only_pages
    expected_classes = {
        **dict.fromkeys(native_text_pages, "native_text"),
        **dict.fromkeys(ocr_text_pages, "ocr_text"),
        **dict.fromkeys(image_only_pages, "image_only_page"),
        **dict.fromkeys(watermark_only_pages, "watermark_only"),
    }
    dispositions = census["page_dispositions"]
    assert isinstance(dispositions, list)
    assert [row["page_number"] for row in dispositions] == list(range(1, page_count + 1))
    assert [row["disposition"] for row in dispositions] == [
        expected_classes[page_number] for page_number in range(1, page_count + 1)
    ]
    assert all(
        set(row) == {"page_number", "disposition", "evidence_binding_sha256"}
        and isinstance(row["evidence_binding_sha256"], str)
        and len(row["evidence_binding_sha256"]) == 64
        and set(row["evidence_binding_sha256"]) <= set("0123456789abcdef")
        for row in dispositions
    )
    assert census["class_counts"] == {
        "native_text": len(native_text_pages),
        "ocr_text": len(ocr_text_pages),
        "image_only_page": len(image_only_pages),
        "watermark_only": len(watermark_only_pages),
    }
    unsigned = {key: item for key, item in census.items() if key != "census_sha256"}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    assert census["census_sha256"] == hashlib.sha256(canonical).hexdigest()


@pytest.fixture(scope="module")
def synthetic_mixed_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[[], _CollectorFixture]:
    """Return a cached factory. Rendering starts on the first call from a test body."""

    cached: dict[str, _CollectorFixture] = {}

    def build() -> _CollectorFixture:
        existing = cached.get("value")
        if existing is not None:
            return existing
        if linux_governed_poppler_absent():
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")
        tmp_path = tmp_path_factory.mktemp("page-census-production-fixture")
        source_path = tmp_path / "company-002-document-00001.pdf"
        _reviewed_image_pdf(source_path, "native", "reviewed_image")
        content_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
        records = _review_records(
            source_path=source_path,
            safe_alias=source_path.name,
            reviewed_pages=(2,),
        )
        verified_manifest = _verified_review_manifest(records)
        source = SourceFileContract(
            sequence=0,
            generated_upload_name=source_path.name,
            source_locator_sha256=_digest("synthetic-page-census-source-locator"),
            content_sha256=content_sha256,
            size_bytes=source_path.stat().st_size,
            extension="pdf",
        )
        patcher = pytest.MonkeyPatch()
        try:
            service = _build_service(
                tmp_path=tmp_path,
                monkeypatch=patcher,
                env={
                    "IDIS_INGEST_MAX_BYTES": str(512 * 1024 * 1024),
                    "IDIS_OCR_ENABLED": "true",
                    "IDIS_OCR_ADAPTER": "tesseract",
                    "IDIS_OCR_DPI": "200",
                    "IDIS_OCR_MAX_PAGES": "72",
                    "IDIS_OCR_TIMEOUT_SECONDS": "60",
                },
                verified_manifest=verified_manifest,
            )
            built = _upload_and_snapshot(
                tmp_path=tmp_path,
                monkeypatch=patcher,
                service=service,
                tenant_id=SYNTHETIC_TENANT_ID,
                source_tree_fingerprint=_digest("synthetic-page-census-source-tree"),
                source_path=source_path,
                source=source,
            )
        finally:
            patcher.undo()
        cached["value"] = built
        return built

    return build


@pytest.fixture(scope="module")
def operator_mixed_fixture(tmp_path_factory: pytest.TempPathFactory) -> _CollectorFixture:
    strict_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not strict_path:
        pytest.skip("strict operator profile is not configured")
    try:
        profile = load_strict_runtime_profile(
            command="api",
            process_env={"IDIS_STRICT_DOTENV_PATH": strict_path},
        )
    except StrictProfileError as exc:
        pytest.fail(
            f"configured strict operator profile failed admission: {exc.code}",
            pytrace=False,
        )
    try:
        verified_manifest = load_document_no_substantive_content_attestation(env=profile.values)
    except DocumentNoSubstantiveContentAttestationError as exc:
        pytest.fail(
            f"configured operator manifest failed verification: {exc.code}",
            pytrace=False,
        )
    if not isinstance(
        verified_manifest,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    ):
        pytest.fail("configured operator manifest is not the signed production manifest v2")
    try:
        contract = build_two_company_source_contract()
    except SourcePackageContractError as exc:
        pytest.fail(f"operator source contract failed admission: {exc.code}", pytrace=False)
    source = contract.companies[1].files[REAL_SOURCE_SEQUENCE]
    if (
        source.sequence != REAL_SOURCE_SEQUENCE
        or source.generated_upload_name != REAL_SAFE_ALIAS
        or source.content_sha256 != REAL_CONTENT_SHA256
        or source.extension != "pdf"
    ):
        pytest.fail("operator mixed-page artifact drifted from its approved safe binding")
    source_path = contract.source_path("company-002", REAL_SOURCE_SEQUENCE)
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != REAL_CONTENT_SHA256:
        pytest.fail("operator mixed-page artifact changed after source fingerprinting")
    # The production company contract keeps this artifact at sequence 104.  This
    # one-file gate fixture is intentionally isolated, so normalize only its
    # local source sequence while preserving the real alias, digest, size, and
    # immutable bytes.  Full acceptance keeps the original 0..208 company set.
    isolated_source = replace(source, sequence=0)
    try:
        tenant_id = UUID(verified_manifest.exemplar.tenant_id)
    except ValueError:
        pytest.fail("operator manifest tenant binding is malformed", pytrace=False)

    tmp_path = tmp_path_factory.mktemp("page-census-operator-fixture")
    patcher = pytest.MonkeyPatch()
    try:
        service = _build_service(
            tmp_path=tmp_path,
            monkeypatch=patcher,
            env=profile.values,
            verified_manifest=verified_manifest,
        )
        return _upload_and_snapshot(
            tmp_path=tmp_path,
            monkeypatch=patcher,
            service=service,
            tenant_id=tenant_id,
            source_tree_fingerprint=contract.source_tree_fingerprint,
            source_path=source_path,
            source=isolated_source,
        )
    finally:
        patcher.undo()


def test_real_document_00105_reconciles_through_durable_collector_and_gate(
    operator_mixed_fixture: _CollectorFixture,
) -> None:
    rows, api = _collect(operator_mixed_fixture)

    assert len(rows) == 1
    row = rows[0]
    evidence = row["ocr_evidence"]
    assert evidence["schema_version"] == "ocr-terminal-evidence-v5"
    _assert_pdf_page_census(
        evidence["pdf_page_census"],
        page_count=REAL_PAGE_COUNT,
        native_text_pages=REAL_NATIVE_PAGES,
        ocr_text_pages=[],
        image_only_pages=REAL_IMAGE_ONLY_PAGES,
        watermark_only_pages=[],
    )
    receipts = _durable_image_only_receipts(operator_mixed_fixture)
    assert [receipt["page_number"] for receipt in receipts] == REAL_IMAGE_ONLY_PAGES
    for receipt in receipts:
        _assert_image_only_receipt_v2(receipt)
    gate_receipt = _reconcile_one(operator_mixed_fixture, rows)
    assert gate_receipt["status"] == "ready"
    assert gate_receipt["pdf_page_disposition_census_schema_version"] == (
        PAGE_CENSUS_SCHEMA_VERSION
    )
    assert gate_receipt["pdf_page_disposition_class_counts"] == {
        "native_text": 6,
        "ocr_text": 0,
        "image_only_page": 2,
        "watermark_only": 0,
    }
    assert len(gate_receipt["pdf_page_disposition_censuses_sha256"]) == 64
    assert all(headers == {"X-IDIS-API-Key": API_KEY} for _, headers in api.calls)


@requires_canonical_poppler_pin
def test_production_mixed_fixture_persists_complete_disjoint_census(
    synthetic_mixed_fixture: Callable[[], _CollectorFixture],
) -> None:
    synthetic_mixed_fixture = synthetic_mixed_fixture()
    rows, _api = _collect(synthetic_mixed_fixture)

    evidence = rows[0]["ocr_evidence"]
    assert evidence["schema_version"] == "ocr-terminal-evidence-v5"
    _assert_pdf_page_census(
        evidence["pdf_page_census"],
        page_count=2,
        native_text_pages=[1],
        ocr_text_pages=[],
        image_only_pages=[2],
        watermark_only_pages=[],
    )
    receipt = _durable_image_only_receipts(synthetic_mixed_fixture)[0]
    _assert_image_only_receipt_v2(receipt)
    assert _reconcile_one(synthetic_mixed_fixture, rows)["status"] == "ready"


def _document_url(fixture: _CollectorFixture) -> str:
    return next(
        url for url in fixture.responses if url.startswith("/v1/deals/") and "/documents/" in url
    )


def _durable_image_only_receipts(fixture: _CollectorFixture) -> list[dict[str, Any]]:
    document = fixture.responses[_document_url(fixture)]
    metadata = document["metadata"]
    assert isinstance(metadata, Mapping)
    receipts = metadata["image_only_page_receipts"]
    assert isinstance(receipts, list)
    return cast(list[dict[str, Any]], receipts)


def _mutated_fixture(
    fixture: _CollectorFixture,
    mutate: Any,
) -> _CollectorFixture:
    responses = copy.deepcopy(dict(fixture.responses))
    mutate(cast(dict[str, Any], responses[_document_url(fixture)]))
    return _CollectorFixture(
        company=fixture.company,
        preparation_summary=fixture.preparation_summary,
        responses=MappingProxyType(responses),
        expected_pdf_page_count=fixture.expected_pdf_page_count,
    )


def _assert_collector_blocked(
    fixture: _CollectorFixture,
    *,
    expected_message: str,
) -> None:
    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _collect(fixture)
    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE
    assert str(caught.value) == expected_message


@requires_canonical_poppler_pin
def test_page_claimed_by_native_text_and_image_only_receipt_fails_closed(
    synthetic_mixed_fixture: Callable[[], _CollectorFixture],
) -> None:
    synthetic_mixed_fixture = synthetic_mixed_fixture()
    responses = copy.deepcopy(dict(synthetic_mixed_fixture.responses))
    spans_url = next(url for url in responses if url.endswith("/spans"))
    span_page = cast(dict[str, Any], responses[spans_url])
    for span in span_page["items"]:
        span["locator"]["page"] = 2
    fixture = _CollectorFixture(
        company=synthetic_mixed_fixture.company,
        preparation_summary=synthetic_mixed_fixture.preparation_summary,
        responses=MappingProxyType(responses),
        expected_pdf_page_count=synthetic_mixed_fixture.expected_pdf_page_count,
    )
    _assert_collector_blocked(
        fixture,
        expected_message="A PDF page disposition census is incomplete or overlapping",
    )


@requires_linux_governed_poppler
def test_page_in_no_disposition_fails_closed(
    synthetic_mixed_fixture: Callable[[], _CollectorFixture],
) -> None:
    synthetic_mixed_fixture = synthetic_mixed_fixture()

    def omit(document: dict[str, Any]) -> None:
        metadata = cast(dict[str, Any], document["metadata"])
        metadata.pop("image_only_page_receipts")
        metadata.pop("image_only_page_evaluation")
        metadata["ocr_performed"] = False
        metadata["ocr_page_count"] = 0
        metadata["ocr_required_page_count"] = 0
        metadata["ocr_render_admissions"] = []
        metadata["ocr_render_admissions_sha256"] = _canonical_sha256([])

    _assert_collector_blocked(
        _mutated_fixture(synthetic_mixed_fixture, omit),
        expected_message="A PDF page disposition census is incomplete or overlapping",
    )


@pytest.mark.parametrize(
    "missing_field",
    ["ocr_render_admission", "ocr_page_execution_receipt"],
)
@requires_linux_governed_poppler
def test_image_only_receipt_without_render_or_no_consensus_provenance_fails_closed(
    synthetic_mixed_fixture: Callable[[], _CollectorFixture],
    missing_field: str,
) -> None:
    synthetic_mixed_fixture = synthetic_mixed_fixture()

    def strip_provenance(document: dict[str, Any]) -> None:
        receipt = cast(
            dict[str, Any],
            document["metadata"]["image_only_page_receipts"][0],
        )
        receipt.pop(missing_field, None)

    _assert_collector_blocked(
        _mutated_fixture(synthetic_mixed_fixture, strip_provenance),
        expected_message="An image-only PDF page omitted its reviewed terminal receipt",
    )


@requires_canonical_poppler_pin
def test_all_image_only_pdf_remains_reviewer_disposition_blocker(
    synthetic_mixed_fixture: Callable[[], _CollectorFixture],
) -> None:
    synthetic_mixed_fixture = synthetic_mixed_fixture()
    document = synthetic_mixed_fixture.responses[_document_url(synthetic_mixed_fixture)]
    metadata = cast(dict[str, Any], document["metadata"])
    receipt = copy.deepcopy(metadata["image_only_page_receipts"][0])
    receipt["page_number"] = 1
    receipt["safe_page_locator"] = {
        "scheme": "safe-pdf-page-v1",
        "page_number": 1,
        "locator_sha256": hashlib.sha256(
            f"{receipt['content_sha256']}\x00safe-pdf-page-v1\x001".encode("ascii")
        ).hexdigest(),
    }
    execution = cast(dict[str, Any], receipt["ocr_page_execution_receipt"])
    execution["page_number"] = 1
    receipt["ocr_terminal_proof_sha256"] = _canonical_sha256(execution)
    all_image_metadata = copy.deepcopy(metadata)
    all_image_metadata["page_count"] = 1
    all_image_metadata["image_only_page_receipts"] = [receipt]
    evaluation = evaluate_image_only_page_receipts(
        receipts=[receipt],
        content_sha256=receipt["content_sha256"],
        page_count=1,
        durable_substantive_spans=[],
    )
    assert evaluation["parse_status"] == "FAILED"
    assert evaluation["may_proceed"] is False
    assert evaluation["terminal_code"] == "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
    all_image_metadata["image_only_page_evaluation"] = evaluation
    all_image_metadata["ocr_required_page_count"] = 1
    all_image_metadata["ocr_page_count"] = 1
    all_image_metadata["ocr_performed"] = True
    admission = {"page": 1, **receipt["ocr_render_admission"]}
    all_image_metadata["ocr_render_admissions"] = [admission]
    all_image_metadata["ocr_render_admissions_sha256"] = _canonical_sha256([admission])

    # This private seam is deliberate: the durable collector rejects an empty
    # span page first, while this test proves the complete page census cannot
    # turn the real reviewer-disposition blocker into a parsed document.
    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        reconciliation._safe_pdf_ocr_evidence_v5(
            all_image_metadata,
            content_sha256=receipt["content_sha256"],
            parse_status="FAILED",
            locator_proof=reconciliation._empty_pdf_locator_proof(),
            watermark_receipt=None,
            expected_page_count=1,
            whole_document_terminal=False,
        )
    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE
    assert str(caught.value) == "A non-terminal PDF did not reach a parsed state"
