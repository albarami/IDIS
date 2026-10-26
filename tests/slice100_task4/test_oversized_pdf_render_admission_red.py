"""RED contracts for bounded oversized-PDF OCR render admission.

The production blocker is a valid one-page PDF whose declared page geometry is
too large for the configured 200-DPI OCR render, even though a deterministic
lower DPI fits the existing 20-megapixel resource bound.  These tests require
geometry admission before OCR, durable per-span/page provenance, and a distinct
fail-closed code when even the approved 72-DPI floor cannot fit.

The operator-backed cases are skipped only when no strict profile is configured.
They never serialize or print the private source path, profile values, or company
names.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation.real_example_source_package import (
    SourcePackageContractError,
    build_two_company_source_contract,
)
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.document import ParseStatus
from idis.parsers import ocr
from idis.parsers.poppler_runtime import PinnedPopplerRuntime
from idis.runtime.governance_envelopes import canonicalize_json
from idis.runtime.profile import StrictProfileError, load_strict_runtime_profile
from idis.services.ingestion import defaults
from idis.services.ingestion.no_substantive_content_attestation import (
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    load_document_no_substantive_content_attestation,
)
from idis.services.ingestion.service import (
    IngestionContext,
    IngestionResult,
    IngestionService,
    RouteValidatedSha256,
)
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.abac_seed import seed_deal_access

CONFIGURED_DPI = 200
MINIMUM_DPI = 72
MAX_PIXEL_COUNT = 20_000_000
EXPECTED_ALIAS = "company-002-document-00053.pdf"
EXPECTED_CONTENT_SHA256_PREFIX = "660891ceb200f7ea"
TENANT_ID = UUID("a1000000-0000-4000-8000-000000000001")
DEAL_ID = UUID("a2000000-0000-4000-8000-000000000001")
ACTOR_ID = "fictional-oversized-pdf-worker"
API_KEY = "fictional-oversized-pdf-api-key"


@dataclass(frozen=True, slots=True, repr=False)
class _OperatorMaterial:
    """Private operator material that cannot leak through pytest representations."""

    verified_manifest: VerifiedDocumentNoSubstantiveContentAttestationManifest
    tenant_id: UUID
    source_tree_fingerprint: str
    safe_alias: str
    content_sha256: str
    source_path: Path
    size_bytes: int
    pdf_bytes: bytes


@dataclass(slots=True)
class _OcrAttemptLedger:
    attempted_dpis: list[int]


_AdapterBehavior = Literal["text", "forbid"]


class _RecordingOcrAdapter:
    """Deterministic OCR seam that records the production-selected render DPI."""

    def __init__(
        self,
        *,
        dpi: int,
        language: str,
        ledger: _OcrAttemptLedger,
        behavior: _AdapterBehavior,
        poppler_runtime: PinnedPopplerRuntime | None = None,
    ) -> None:
        self._dpi = dpi
        self._language = language
        self._ledger = ledger
        self._behavior = behavior
        self._poppler_runtime = poppler_runtime

    @property
    def dpi(self) -> int:
        return self._dpi

    @property
    def language(self) -> str:
        return self._language

    @property
    def poppler_runtime(self) -> PinnedPopplerRuntime | None:
        return self._poppler_runtime

    def with_dpi(self, dpi: int) -> _RecordingOcrAdapter:
        """Support a clone-based implementation without prescribing it."""

        return type(self)(
            dpi=dpi,
            language=self._language,
            ledger=self._ledger,
            behavior=self._behavior,
            poppler_runtime=self._poppler_runtime,
        )

    def for_dpi(self, dpi: int) -> _RecordingOcrAdapter:
        return self.with_dpi(dpi)

    def extract_pdf_file(
        self,
        source_path: str | Path,
        *,
        max_pages: int,
        timeout_seconds: float,
        **kwargs: object,
    ) -> list[ocr.OcrPageText]:
        del source_path, max_pages, timeout_seconds
        return self._extract(**kwargs)

    def extract_pdf_file_with_partial_results(
        self,
        source_path: str | Path,
        *,
        max_pages: int,
        timeout_seconds: float,
        **kwargs: object,
    ) -> list[ocr.OcrPageText]:
        del source_path, max_pages, timeout_seconds
        return self._extract(**kwargs)

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
        **kwargs: object,
    ) -> list[ocr.OcrPageText]:
        del data, max_pages, timeout_seconds
        return self._extract(**kwargs)

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
        **kwargs: object,
    ) -> list[ocr.OcrPageText]:
        del data, timeout_seconds
        return self._extract(**kwargs)

    def extract_pdf_file_with_render_admissions(
        self,
        source_path: str | Path,
        *,
        render_admissions: tuple[ocr.OcrPageRenderAdmission, ...],
        deadline: float,
        retain_partial_results: bool,
    ) -> list[ocr.OcrPageText]:
        del source_path, deadline, retain_partial_results
        pages: list[ocr.OcrPageText] = []
        for admission in render_admissions:
            pages.extend(self._extract(render_admission=admission))
        return pages

    def _extract(self, **kwargs: object) -> list[ocr.OcrPageText]:
        chosen_dpi = self._chosen_dpi(kwargs)
        self._ledger.attempted_dpis.append(chosen_dpi)
        if self._behavior == "forbid":
            raise AssertionError("OCR must not execute before classifier-first admission")
        if chosen_dpi >= CONFIGURED_DPI:
            raise AssertionError("oversized PDF reached full-resolution OCR")
        render_admission = kwargs.get("render_admission")
        if isinstance(render_admission, ocr.OcrPageRenderAdmission):
            observed = render_admission.with_observed_render(
                width=int(render_admission.expected_render_width or 0),
                height=int(render_admission.expected_render_height or 0),
            )
            page_number = render_admission.page_number
        else:
            observed = None
            page_number = 1
        return [
            ocr.OcrPageText(
                page_number=page_number,
                text="fictional poster evidence alpha beta",
                confidence=0.99,
                render_admission=observed,
            )
        ]

    def _chosen_dpi(self, kwargs: Mapping[str, object]) -> int:
        for admission_name in ("ocr_render_admission", "render_admission"):
            admission = kwargs.get(admission_name)
            if isinstance(admission, ocr.OcrPageRenderAdmission):
                return admission.chosen_dpi
            if isinstance(admission, Mapping):
                value = admission.get("chosen_dpi")
                if type(value) is int:
                    return cast(int, value)
        for name in ("chosen_dpi", "render_dpi", "dpi"):
            value = kwargs.get(name)
            if type(value) is int:
                return cast(int, value)
        return self._dpi


def _strict_ocr_env() -> dict[str, str]:
    return {
        "IDIS_INGEST_MAX_BYTES": str(512 * 1024 * 1024),
        "IDIS_OCR_ENABLED": "true",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_DPI": str(CONFIGURED_DPI),
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "60",
    }


def _install_recording_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ledger: _OcrAttemptLedger,
    behavior: _AdapterBehavior,
) -> None:
    class BoundRecordingOcrAdapter(_RecordingOcrAdapter):
        def __init__(
            self,
            *,
            dpi: int = CONFIGURED_DPI,
            language: str = "eng",
            poppler_runtime: PinnedPopplerRuntime | None = None,
            **_ignored: object,
        ) -> None:
            super().__init__(
                dpi=dpi,
                language=language,
                ledger=ledger,
                behavior=behavior,
                poppler_runtime=poppler_runtime,
            )

    monkeypatch.setattr(defaults, "TesseractOcrAdapter", BoundRecordingOcrAdapter)
    # A future selector may instantiate through the adapter module instead of the
    # composition-root import.  Patching both keeps this a behavior contract.
    monkeypatch.setattr(ocr, "TesseractOcrAdapter", BoundRecordingOcrAdapter)


def _build_service(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger: _OcrAttemptLedger,
    behavior: _AdapterBehavior,
    verified_manifest: VerifiedDocumentNoSubstantiveContentAttestationManifest | None = None,
) -> IngestionService:
    _install_recording_adapter(monkeypatch, ledger=ledger, behavior=behavior)
    store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
    )
    monkeypatch.setattr(defaults, "build_default_compliance_store", lambda: store)
    if verified_manifest is not None:
        monkeypatch.setattr(
            defaults,
            "load_document_no_substantive_content_attestation",
            lambda *, env: verified_manifest,
        )
    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env=_strict_ocr_env(),
        strict=True,
    )
    assert isinstance(service, IngestionService)
    return service


def _ingest_file(
    service: IngestionService,
    *,
    tenant_id: UUID,
    source_tree_fingerprint: str,
    source_path: Path,
    safe_alias: str,
    content_sha256: str,
    size_bytes: int,
) -> IngestionResult:
    return service.ingest_file(
        ctx=IngestionContext(
            tenant_id=tenant_id,
            actor_id=ACTOR_ID,
            request_id=f"oversized-pdf-red:{safe_alias}",
        ),
        deal_id=DEAL_ID,
        filename=safe_alias,
        media_type="application/pdf",
        source_path=source_path,
        size_bytes=size_bytes,
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=content_sha256,
            expected_sha256=content_sha256,
        ),
        metadata={
            "upload_filename": safe_alias,
            "source_tree_fingerprint": source_tree_fingerprint,
        },
    )


def _document_receipt(
    service: IngestionService,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> dict[str, object]:
    document = service.get_document(tenant_id, document_id)
    assert document is not None
    receipt = document.metadata.get("document_no_substantive_content_receipt")
    assert isinstance(receipt, dict)
    return receipt


def _assert_classifier_first_watermark_receipt(receipt: Mapping[str, object]) -> None:
    assert receipt["variant"] == "watermark_only"
    classifications = receipt["watermark_page_classifications"]
    assert isinstance(classifications, list)
    assert len(classifications) == 1
    classification = classifications[0]
    assert isinstance(classification, dict)
    assert classification["classification_phase"] == "pre_ocr"
    assert classification["full_resolution_ocr_attempted"] is False


@pytest.fixture(scope="module")
def operator_material() -> _OperatorMaterial:
    strict_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not strict_path:
        pytest.skip("strict operator profile is not configured")

    private_env = {"IDIS_STRICT_DOTENV_PATH": strict_path}
    try:
        profile = load_strict_runtime_profile(command="api", process_env=private_env)
    except StrictProfileError as exc:
        pytest.fail(
            f"configured strict operator profile failed admission: {exc.code}",
            pytrace=False,
        )
    try:
        verified = load_document_no_substantive_content_attestation(env=profile.values)
    except DocumentNoSubstantiveContentAttestationError as exc:
        pytest.fail(
            f"configured operator manifest failed verification: {exc.code}",
            pytrace=False,
        )
    except Exception:
        pytest.fail("configured operator manifest could not be securely loaded", pytrace=False)
    if not isinstance(verified, VerifiedDocumentNoSubstantiveContentAttestationManifest):
        pytest.fail("configured operator artifact is not the signed production manifest v2")
    if verified.safe_summary().get("signature_verified") is not True:
        pytest.fail("configured operator manifest lacks verified governance provenance")

    try:
        contract = build_two_company_source_contract()
    except SourcePackageContractError as exc:
        pytest.fail(f"operator source contract failed admission: {exc.code}", pytrace=False)
    except Exception:
        pytest.fail("operator source contract could not be loaded read-only", pytrace=False)

    company = contract.companies[1]
    source_file = company.files[52]
    if (
        company.company_alias != "company-002"
        or source_file.sequence != 52
        or source_file.generated_upload_name != EXPECTED_ALIAS
        or not source_file.content_sha256.startswith(EXPECTED_CONTENT_SHA256_PREFIX)
        or source_file.generated_upload_name != verified.exemplar.safe_alias
        or source_file.content_sha256 != verified.exemplar.content_sha256
        or contract.source_tree_fingerprint != verified.exemplar.source_tree_fingerprint
    ):
        pytest.fail("operator exemplar does not match the approved safe source binding")
    try:
        source_path = contract.source_path(company.company_alias, source_file.sequence)
        pdf_bytes = source_path.read_bytes()
        observed_size = source_path.stat().st_size
    except OSError:
        pytest.fail("operator exemplar bytes are unavailable", pytrace=False)
    if observed_size != source_file.size_bytes:
        pytest.fail("operator exemplar size changed after source fingerprinting")
    if hashlib.sha256(pdf_bytes).hexdigest() != source_file.content_sha256:
        pytest.fail("operator exemplar content changed after source fingerprinting")

    try:
        tenant_id = UUID(verified.exemplar.tenant_id)
    except ValueError:
        pytest.fail("operator manifest tenant binding is malformed", pytrace=False)
    return _OperatorMaterial(
        verified_manifest=verified,
        tenant_id=tenant_id,
        source_tree_fingerprint=contract.source_tree_fingerprint,
        safe_alias=source_file.generated_upload_name,
        content_sha256=source_file.content_sha256,
        source_path=source_path,
        size_bytes=source_file.size_bytes,
        pdf_bytes=pdf_bytes,
    )


def test_operator_exemplar_is_classified_before_full_resolution_ocr_in_ingestion_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_material: _OperatorMaterial,
) -> None:
    ledger = _OcrAttemptLedger(attempted_dpis=[])
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        ledger=ledger,
        behavior="forbid",
        verified_manifest=operator_material.verified_manifest,
    )

    result = _ingest_file(
        service,
        tenant_id=operator_material.tenant_id,
        source_tree_fingerprint=operator_material.source_tree_fingerprint,
        source_path=operator_material.source_path,
        safe_alias=operator_material.safe_alias,
        content_sha256=operator_material.content_sha256,
        size_bytes=operator_material.size_bytes,
    )

    assert result.document_id is not None
    assert result.parse_status == ParseStatus.FAILED
    assert result.terminal_disposition == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    receipt = _document_receipt(
        service,
        tenant_id=operator_material.tenant_id,
        document_id=result.document_id,
    )
    _assert_classifier_first_watermark_receipt(receipt)
    assert CONFIGURED_DPI not in ledger.attempted_dpis


def test_second_signed_watermark_candidate_uses_same_classifier_first_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_material: _OperatorMaterial,
) -> None:
    contract = build_two_company_source_contract()
    source_file = contract.companies[1].files[53]
    assert source_file.generated_upload_name == "company-002-document-00054.pdf"
    source_path = contract.source_path("company-002", 53)
    ledger = _OcrAttemptLedger(attempted_dpis=[])
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        ledger=ledger,
        behavior="forbid",
        verified_manifest=operator_material.verified_manifest,
    )

    result = _ingest_file(
        service,
        tenant_id=operator_material.tenant_id,
        source_tree_fingerprint=contract.source_tree_fingerprint,
        source_path=source_path,
        safe_alias=source_file.generated_upload_name,
        content_sha256=source_file.content_sha256,
        size_bytes=source_file.size_bytes,
    )

    assert result.document_id is not None
    assert result.terminal_disposition == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    receipt = _document_receipt(
        service,
        tenant_id=operator_material.tenant_id,
        document_id=result.document_id,
    )
    _assert_classifier_first_watermark_receipt(receipt)
    classification = cast(list[dict[str, object]], receipt["watermark_page_classifications"])[0]
    assert classification["similarity_score"] == "0.820542"
    assert ledger.attempted_dpis == []


def test_operator_exemplar_is_classified_before_full_resolution_ocr_through_api_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_material: _OperatorMaterial,
) -> None:
    ledger = _OcrAttemptLedger(attempted_dpis=[])
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        ledger=ledger,
        behavior="forbid",
        verified_manifest=operator_material.verified_manifest,
    )
    clear_deals_store()
    clear_document_store()
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_DATABASE_ADMIN_URL", raising=False)
    monkeypatch.delenv("IDIS_REDIS_URL", raising=False)
    monkeypatch.setenv("IDIS_SERVICE_REGION", "me-south-1")
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": str(operator_material.tenant_id),
                    "actor_id": ACTOR_ID,
                    "name": "Fictional Oversized PDF Tenant",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    seed_deal_access(str(operator_material.tenant_id), str(DEAL_ID), ACTOR_ID)
    app = create_app(
        audit_sink=InMemoryAuditSink(),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=service,
        service_region="me-south-1",
    )
    persisted_receipt: dict[str, object] | None = None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/v1/deals/{DEAL_ID}/documents/upload",
                headers={
                    "X-IDIS-API-Key": API_KEY,
                    "Content-Type": "application/octet-stream",
                    "X-IDIS-Source-Tree-Fingerprint": (operator_material.source_tree_fingerprint),
                },
                params={
                    "filename": operator_material.safe_alias,
                    "doc_type": "DATA_ROOM_FILE",
                    "sha256": operator_material.content_sha256,
                    "source_system": "slice100-oversized-pdf-red",
                },
                content=operator_material.pdf_bytes,
            )
            body = response.json()
            if response.status_code == 201:
                persisted_receipt = _document_receipt(
                    service,
                    tenant_id=operator_material.tenant_id,
                    document_id=UUID(body["document_id"]),
                )
    finally:
        clear_deals_store()
        clear_document_store()

    assert response.status_code == 201, {
        "observed_status": response.status_code,
        "observed_code": body.get("code"),
    }
    assert body["parse_status"] == "FAILED"
    assert body["terminal_disposition"] == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    assert persisted_receipt is not None
    _assert_classifier_first_watermark_receipt(persisted_receipt)
    assert CONFIGURED_DPI not in ledger.attempted_dpis


def _synthetic_poster_pdf(path: Path) -> tuple[float, float]:
    width_points = 1536.0
    height_points = 2048.0
    image_bytes = io.BytesIO()
    with Image.new("RGB", (1536, 2048), color="white") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 1495, 2007), outline="black", width=8)
        draw.text(
            (120, 860),
            "FICTIONAL POSTER EVIDENCE ALPHA BETA",
            font=ImageFont.load_default(size=92),
            fill="black",
        )
        image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    document = canvas.Canvas(
        str(path),
        pagesize=(width_points, height_points),
        pageCompression=0,
        invariant=1,
    )
    document.drawImage(
        ImageReader(image_bytes),
        0,
        0,
        width=width_points,
        height=height_points,
    )
    document.showPage()
    document.save()
    return width_points, height_points


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


@requires_linux_governed_poppler
def test_synthetic_oversized_poster_steps_down_dpi_and_persists_exact_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_alias = "company-002-document-00190.pdf"
    source_path = tmp_path / safe_alias
    width_points, height_points = _synthetic_poster_pdf(source_path)
    configured_width = math.ceil(width_points * CONFIGURED_DPI / 72)
    configured_height = math.ceil(height_points * CONFIGURED_DPI / 72)
    assert configured_width * configured_height > MAX_PIXEL_COUNT

    ledger = _OcrAttemptLedger(attempted_dpis=[])
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        ledger=ledger,
        behavior="text",
    )
    content_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    result = _ingest_file(
        service,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=hashlib.sha256(b"fictional-poster-tree").hexdigest(),
        source_path=source_path,
        safe_alias=safe_alias,
        content_sha256=content_sha256,
        size_bytes=source_path.stat().st_size,
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    assert result.document_id is not None
    spans = service.get_spans(TENANT_ID, result.document_id)
    stepped_spans = [span for span in spans if "ocr_render_admission" in span.locator]
    assert stepped_spans
    admissions: list[dict[str, object]] = []
    for span in stepped_spans:
        admission = span.locator["ocr_render_admission"]
        assert isinstance(admission, dict)
        assert set(admission) == {
            "schema_version",
            "ladder_version",
            "configured_dpi",
            "chosen_dpi",
            "projected_width",
            "projected_height",
            "projected_pixel_count",
            "expected_render_width",
            "expected_render_height",
            "expected_render_pixel_count",
            "actual_render_width",
            "actual_render_height",
            "actual_render_pixel_count",
            "poppler_version",
            "poppler_runtime_identity_sha256",
            "expected_dimension_policy_id",
            "expected_dimension_policy_sha256",
            "comparison_mode",
            "width_delta_pixels",
            "height_delta_pixels",
            "tolerance_pixels",
            "tolerance_used",
            "max_pixel_count",
        }
        assert admission["schema_version"] == "ocr-render-admission-v2"
        assert admission["ladder_version"] == "ocr-dpi-ladder-v1"
        assert admission["configured_dpi"] == CONFIGURED_DPI
        assert type(admission["chosen_dpi"]) is int
        chosen_dpi = cast(int, admission["chosen_dpi"])
        assert MINIMUM_DPI <= chosen_dpi < CONFIGURED_DPI
        projected_width = math.ceil(width_points * chosen_dpi / 72)
        projected_height = math.ceil(height_points * chosen_dpi / 72)
        projected = projected_width * projected_height
        assert admission["projected_width"] == projected_width
        assert admission["projected_height"] == projected_height
        assert admission["projected_pixel_count"] == projected
        assert projected <= MAX_PIXEL_COUNT
        assert admission["expected_render_width"] == projected_width
        assert admission["expected_render_height"] == projected_height
        assert admission["expected_render_pixel_count"] == projected
        assert admission["actual_render_width"] == projected_width
        assert admission["actual_render_height"] == projected_height
        assert admission["actual_render_pixel_count"] == projected
        assert admission["poppler_version"] == "25.12.0"
        assert isinstance(admission["poppler_runtime_identity_sha256"], str)
        assert len(cast(str, admission["poppler_runtime_identity_sha256"])) == 64
        assert admission["expected_dimension_policy_id"] == (
            "poppler-pdftoppm-25.12.0-dimensions-v1"
        )
        assert admission["expected_dimension_policy_sha256"] == (
            "483ee7542fbcb6a69dc3949072ef2897471130e409cc41318395e72fec3e0253"
        )
        assert admission["comparison_mode"] == "exact-poppler-25.12.0"
        assert admission["width_delta_pixels"] == 0
        assert admission["height_delta_pixels"] == 0
        assert admission["tolerance_pixels"] == 0
        assert admission["tolerance_used"] is False
        assert admission["max_pixel_count"] == MAX_PIXEL_COUNT
        admissions.append(admission)

    assert all(admission == admissions[0] for admission in admissions)
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    # Metadata binds the complete observed v2 receipt to its page so a sparse
    # plan is independently replayable without inferring page identity.
    metadata_admission = {"page": 1, **admissions[0]}
    assert document.metadata["ocr_render_admissions"] == [metadata_admission]
    assert document.metadata["ocr_render_admissions_sha256"] == _canonical_sha256(
        [metadata_admission]
    )
    assert CONFIGURED_DPI not in ledger.attempted_dpis
    assert admissions[0]["chosen_dpi"] in ledger.attempted_dpis


@requires_linux_governed_poppler
def test_synthetic_oversized_poster_uses_real_poppler_and_tesseract_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_alias = "company-002-document-00192.pdf"
    source_path = tmp_path / safe_alias
    _synthetic_poster_pdf(source_path)
    store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=tmp_path / "real-ocr-objects")
    )
    monkeypatch.setattr(defaults, "build_default_compliance_store", lambda: store)
    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env={**_strict_ocr_env(), "IDIS_OCR_TIMEOUT_SECONDS": "120"},
        strict=True,
    )
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()

    result = _ingest_file(
        service,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=hashlib.sha256(b"real-poster-ocr-tree").hexdigest(),
        source_path=source_path,
        safe_alias=safe_alias,
        content_sha256=digest,
        size_bytes=source_path.stat().st_size,
    )

    assert result.success is True
    assert result.document_id is not None
    spans = service.get_spans(TENANT_ID, result.document_id)
    assert spans
    admissions = {
        cast(dict[str, object], span.locator["ocr_render_admission"])["chosen_dpi"]
        for span in spans
        if "ocr_render_admission" in span.locator
    }
    assert admissions == {150}
    assert "POSTER" in " ".join(span.text_excerpt for span in spans).upper()


def _pdf_above_pixel_cap_at_floor(path: Path) -> tuple[float, float]:
    width_points = 5000.0
    height_points = 5000.0
    writer = PdfWriter()
    writer.add_blank_page(width=width_points, height=height_points)
    with path.open("wb") as output:
        writer.write(output)
    return width_points, height_points


@requires_linux_governed_poppler
def test_pdf_above_pixel_cap_at_72_dpi_fails_before_ocr_with_stable_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_alias = "company-002-document-00191.pdf"
    source_path = tmp_path / safe_alias
    width_points, height_points = _pdf_above_pixel_cap_at_floor(source_path)
    floor_pixels = math.ceil(width_points * MINIMUM_DPI / 72) * math.ceil(
        height_points * MINIMUM_DPI / 72
    )
    assert floor_pixels > MAX_PIXEL_COUNT

    ledger = _OcrAttemptLedger(attempted_dpis=[])
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        ledger=ledger,
        behavior="forbid",
    )
    content_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    result = _ingest_file(
        service,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=hashlib.sha256(b"fictional-floor-tree").hexdigest(),
        source_path=source_path,
        safe_alias=safe_alias,
        content_sha256=content_sha256,
        size_bytes=source_path.stat().st_size,
    )

    assert result.success is False
    assert result.parse_status == ParseStatus.FAILED
    parse_codes = [
        error.details.get("parse_error_code")
        for error in result.errors
        if error.details.get("parse_error_code") is not None
    ]
    assert parse_codes == ["ocr_image_too_large"]
    assert ledger.attempted_dpis == []
