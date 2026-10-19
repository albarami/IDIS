"""RED contracts for exact Poppler PDF render-dimension provenance.

The acceptance-007 blocker proved that the conservative resource-admission
ceiling is not an expected renderer dimension.  These tests keep those values
separate, exercise the real file-backed Poppler path, and require the resulting
v2 proof at production composition roots.

Operator-backed tests discover the immutable source through the safe source
contract.  They never serialize the private source path, strict-profile values,
or company names.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pdf2image import convert_from_path
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
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
from idis.parsers.pdf import build_pdf_ocr_render_admissions
from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
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
from tests._canonical_poppler_pin import requires_canonical_poppler_pin
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.abac_seed import seed_deal_access

CONFIGURED_DPI = 200
MAX_PIXEL_COUNT = 20_000_000
POPPLER_VERSION = "25.12.0"
DIMENSION_POLICY_VERSION = "poppler-pdftoppm-25.12.0-dimensions-v1"
_DIMENSION_POLICY_CANONICAL_JSON = (
    '{"backend":"splash","numeric_parser":"poppler-lexer-decimal-digit-accumulation-'
    'binary64-v1","page_box":"MediaBox","poppler_version":"25.12.0","renderer":'
    '"pdftoppm","rotation":"swap-axes-for-90-or-270","rounding":"ceil",'
    '"scale_order":"extent-times-dpi-then-divide-72-binary64","schema_version":'
    '"poppler-expected-dimensions-policy-v1","user_unit_handling":'
    '"ignored-by-pinned-renderer"}'
)
DIMENSION_POLICY_SHA256 = hashlib.sha256(
    _DIMENSION_POLICY_CANONICAL_JSON.encode("utf-8")
).hexdigest()
POPPLER_RUNTIME_IDENTITY_SHA256 = "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"
REAL_SAFE_ALIAS = "company-001-document-00004.pdf"
REAL_CONTENT_SHA256 = "732a18b4543c5c7db3838f0a87d14816e896b41f9d09b3c9177c177561655b5e"
TENANT_ID = UUID("a8100000-0000-4000-8000-000000000001")
DEAL_ID = UUID("a8200000-0000-4000-8000-000000000001")
ACTOR_ID = "fictional-render-rounding-worker"
API_KEY = "fictional-render-rounding-api-key"

_V2_PROVENANCE_FIELDS = frozenset(
    {
        "schema_version",
        "ladder_version",
        "expected_dimension_policy_id",
        "expected_dimension_policy_sha256",
        "poppler_version",
        "poppler_runtime_identity_sha256",
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
        "comparison_mode",
        "width_delta_pixels",
        "height_delta_pixels",
        "tolerance_pixels",
        "tolerance_used",
        "max_pixel_count",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class _OperatorArtifact:
    """Private operator material whose repr cannot expose secret paths or values."""

    profile_values: Mapping[str, str]
    source_tree_fingerprint: str
    safe_alias: str
    content_sha256: str
    source_path: Path
    size_bytes: int
    pdf_bytes: bytes


@dataclass(frozen=True, slots=True)
class _BoundaryCase:
    chosen_dpi: int
    page_points: float
    half_pixel_floor: int
    position: str
    fractional_pixel: float

    @property
    def expected_pixels_per_axis(self) -> int:
        return self.half_pixel_floor + 1


_BOUNDARY_CASES = tuple(
    _BoundaryCase(
        chosen_dpi=dpi,
        page_points=(pixel_floor + fractional_pixel) * 72 / dpi,
        half_pixel_floor=pixel_floor,
        position=position,
        fractional_pixel=fractional_pixel,
    )
    for dpi, pixel_floor in (
        (200, 1_000),
        (150, 3_500),
        (120, 3_650),
        (96, 3_650),
        (72, 3_500),
    )
    for position, fractional_pixel in (
        ("below-half", 0.499),
        ("half", 0.5),
        ("above-half", 0.501),
    )
)


class _Output:
    DICT = "dict"


class _FakeImage:
    def __init__(self, *, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _strict_ocr_env() -> dict[str, str]:
    return {
        "IDIS_INGEST_MAX_BYTES": str(512 * 1024 * 1024),
        "IDIS_OCR_ENABLED": "true",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_LANGUAGE": "eng",
        "IDIS_OCR_DPI": str(CONFIGURED_DPI),
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
    }


def _build_service(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: Mapping[str, str],
) -> IngestionService:
    store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
    )
    monkeypatch.setattr(defaults, "build_default_compliance_store", lambda: store)
    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env=env,
        strict=True,
    )
    assert isinstance(service, IngestionService)
    return service


def _ingest_file(
    service: IngestionService,
    *,
    source_tree_fingerprint: str,
    source_path: Path,
    safe_alias: str,
    content_sha256: str,
    size_bytes: int,
) -> IngestionResult:
    return service.ingest_file(
        ctx=IngestionContext(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            request_id=f"render-rounding-red:{safe_alias}",
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


def _write_production_raster_pdf(path: Path, *, page_points: float) -> None:
    """Construct a zero-native-text PDF through the production-style image pipeline."""

    raster = io.BytesIO()
    with Image.new("RGB", (900, 240), color="white") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 894, 234), outline="black", width=5)
        draw.text(
            (35, 75),
            "FICTIONAL ROUNDING BOUNDARY EVIDENCE",
            font=ImageFont.load_default(size=44),
            fill="black",
        )
        image.save(raster, format="PNG")
    raster.seek(0)
    document = canvas.Canvas(
        str(path),
        pagesize=(page_points, page_points),
        invariant=1,
        pageCompression=0,
    )
    document.drawImage(
        ImageReader(raster),
        page_points * 0.10,
        page_points * 0.40,
        width=page_points * 0.80,
        height=page_points * 0.20,
    )
    document.showPage()
    document.save()


def _tesseract_payload(_image: object, **_kwargs: object) -> dict[str, object]:
    return {
        "text": ["fictional", "rounding", "boundary", "evidence"],
        "conf": ["96", "95", "94", "93"],
        "block_num": [1, 1, 1, 1],
        "par_num": [1, 1, 1, 1],
        "line_num": [1, 1, 1, 1],
        "word_num": [1, 2, 3, 4],
    }


def _assert_exact_v2_provenance(
    value: object,
    *,
    chosen_dpi: int,
    projected: tuple[int, int],
    expected: tuple[int, int],
    actual: tuple[int, int],
    page_number: int | None = None,
    worker_payload: bool = False,
    require_operator_runtime_identity: bool = False,
) -> None:
    assert isinstance(value, Mapping)
    payload = dict(value)
    if worker_payload:
        assert payload.pop("page_number") == page_number
        assert "page" not in payload
    elif page_number is None:
        assert "page" not in payload
        assert "page_number" not in payload
    else:
        assert payload.pop("page") == page_number
        assert "page_number" not in payload
    assert set(payload) == _V2_PROVENANCE_FIELDS
    runtime_identity_sha256 = payload["poppler_runtime_identity_sha256"]
    assert isinstance(runtime_identity_sha256, str)
    assert len(runtime_identity_sha256) == 64
    assert runtime_identity_sha256 == runtime_identity_sha256.lower()
    int(runtime_identity_sha256, 16)
    if require_operator_runtime_identity:
        assert runtime_identity_sha256 == POPPLER_RUNTIME_IDENTITY_SHA256
    assert payload == {
        "schema_version": "ocr-render-admission-v2",
        "ladder_version": "ocr-dpi-ladder-v1",
        "expected_dimension_policy_id": DIMENSION_POLICY_VERSION,
        "expected_dimension_policy_sha256": DIMENSION_POLICY_SHA256,
        "poppler_version": POPPLER_VERSION,
        "poppler_runtime_identity_sha256": runtime_identity_sha256,
        "configured_dpi": CONFIGURED_DPI,
        "chosen_dpi": chosen_dpi,
        "projected_width": projected[0],
        "projected_height": projected[1],
        "projected_pixel_count": projected[0] * projected[1],
        "expected_render_width": expected[0],
        "expected_render_height": expected[1],
        "expected_render_pixel_count": expected[0] * expected[1],
        "actual_render_width": actual[0],
        "actual_render_height": actual[1],
        "actual_render_pixel_count": actual[0] * actual[1],
        "comparison_mode": "exact-poppler-25.12.0",
        "width_delta_pixels": actual[0] - expected[0],
        "height_delta_pixels": actual[1] - expected[1],
        "tolerance_pixels": 0,
        "tolerance_used": False,
        "max_pixel_count": MAX_PIXEL_COUNT,
    }
    assert cast(int, payload["actual_render_pixel_count"]) <= MAX_PIXEL_COUNT


def _assert_document_span_provenance(
    service: IngestionService,
    *,
    document_id: UUID,
    chosen_dpi: int,
    projected: tuple[int, int],
    expected: tuple[int, int],
    actual: tuple[int, int],
    require_operator_runtime_identity: bool = False,
) -> None:
    document = service.get_document(TENANT_ID, document_id)
    assert document is not None
    metadata_rows = document.metadata.get("ocr_render_admissions")
    assert isinstance(metadata_rows, list)
    assert len(metadata_rows) == 1
    _assert_exact_v2_provenance(
        metadata_rows[0],
        chosen_dpi=chosen_dpi,
        projected=projected,
        expected=expected,
        actual=actual,
        page_number=1,
        require_operator_runtime_identity=require_operator_runtime_identity,
    )
    spans = service.get_spans(TENANT_ID, document_id)
    assert spans
    span_rows = [span.locator.get("ocr_render_admission") for span in spans]
    assert span_rows
    for row in span_rows:
        _assert_exact_v2_provenance(
            row,
            chosen_dpi=chosen_dpi,
            projected=projected,
            expected=expected,
            actual=actual,
            require_operator_runtime_identity=require_operator_runtime_identity,
        )


@pytest.fixture(scope="module")
def operator_artifact() -> _OperatorArtifact:
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
    if (
        profile.values.get("IDIS_OCR_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}
        or profile.values.get("IDIS_OCR_ADAPTER") != "tesseract"
        or profile.values.get("IDIS_OCR_DPI") != str(CONFIGURED_DPI)
    ):
        pytest.fail("configured operator OCR profile drifted from the approved contract")
    try:
        verified_manifest = load_document_no_substantive_content_attestation(env=profile.values)
    except DocumentNoSubstantiveContentAttestationError as exc:
        pytest.fail(
            f"configured operator manifest failed verification: {exc.code}",
            pytrace=False,
        )
    except Exception:
        pytest.fail("configured operator manifest could not be securely loaded", pytrace=False)
    if (
        not isinstance(
            verified_manifest,
            VerifiedDocumentNoSubstantiveContentAttestationManifest,
        )
        or verified_manifest.safe_summary().get("signature_verified") is not True
    ):
        pytest.fail("configured operator manifest lacks verified governance provenance")

    try:
        contract = build_two_company_source_contract()
    except SourcePackageContractError as exc:
        pytest.fail(f"operator source contract failed admission: {exc.code}", pytrace=False)
    except Exception:
        pytest.fail("operator source contract could not be loaded read-only", pytrace=False)
    source_file = contract.companies[0].files[3]
    if (
        contract.companies[0].company_alias != "company-001"
        or source_file.sequence != 3
        or source_file.generated_upload_name != REAL_SAFE_ALIAS
        or source_file.content_sha256 != REAL_CONTENT_SHA256
    ):
        pytest.fail("operator artifact does not match the approved safe source binding")
    try:
        source_path = contract.source_path("company-001", 3)
        pdf_bytes = source_path.read_bytes()
        observed_size = source_path.stat().st_size
    except OSError:
        pytest.fail("operator artifact bytes are unavailable", pytrace=False)
    if (
        observed_size != source_file.size_bytes
        or hashlib.sha256(pdf_bytes).hexdigest() != REAL_CONTENT_SHA256
    ):
        pytest.fail("operator artifact changed after source fingerprinting")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) != 1 or (reader.pages[0].extract_text() or "").strip():
        pytest.fail("operator artifact OCR-page contract drifted")

    return _OperatorArtifact(
        profile_values=MappingProxyType(dict(profile.values)),
        source_tree_fingerprint=contract.source_tree_fingerprint,
        safe_alias=source_file.generated_upload_name,
        content_sha256=source_file.content_sha256,
        source_path=source_path,
        size_bytes=source_file.size_bytes,
        pdf_bytes=pdf_bytes,
    )


@requires_canonical_poppler_pin
def test_real_rounding_artifact_succeeds_through_file_backed_worker(
    operator_artifact: _OperatorArtifact,
) -> None:
    from pytesseract import Output, image_to_data

    poppler_runtime = resolve_pinned_poppler_runtime()
    admission = build_pdf_ocr_render_admissions(
        data=operator_artifact.pdf_bytes,
        page_numbers=(1,),
        poppler_runtime=poppler_runtime,
    )[0]
    assert admission.chosen_dpi == 200
    assert (admission.projected_width, admission.projected_height) == (1_655, 2_339)

    pages = ocr._ocr_pdf_file_render_admission_batches(
        source_path=operator_artifact.source_path,
        render_admissions=(admission,),
        language="eng",
        deadline=time.monotonic() + 120,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
        output=Output,
        compute_page_content_sha256s=False,
        poppler_runtime=poppler_runtime,
    )

    assert len(pages) == 1
    assert cast(str, pages[0]["text"]).strip()
    _assert_exact_v2_provenance(
        pages[0]["render_admission"],
        chosen_dpi=200,
        projected=(1_655, 2_339),
        expected=(1_654, 2_339),
        actual=(1_654, 2_339),
        page_number=1,
        worker_payload=True,
        require_operator_runtime_identity=True,
    )


def test_real_rounding_artifact_succeeds_through_api_upload_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_artifact: _OperatorArtifact,
) -> None:
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        env=operator_artifact.profile_values,
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
                    "tenant_id": str(TENANT_ID),
                    "actor_id": ACTOR_ID,
                    "name": "Fictional Render Rounding Tenant",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    seed_deal_access(str(TENANT_ID), str(DEAL_ID), ACTOR_ID)
    app = create_app(
        audit_sink=InMemoryAuditSink(),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=service,
        service_region="me-south-1",
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/v1/deals/{DEAL_ID}/documents/upload",
                headers={
                    "X-IDIS-API-Key": API_KEY,
                    "Content-Type": "application/octet-stream",
                    "X-IDIS-Source-Tree-Fingerprint": (operator_artifact.source_tree_fingerprint),
                },
                params={
                    "filename": operator_artifact.safe_alias,
                    "doc_type": "DATA_ROOM_FILE",
                    "sha256": operator_artifact.content_sha256,
                    "source_system": "slice100-render-rounding-red",
                },
                content=operator_artifact.pdf_bytes,
            )
            body = response.json()
    finally:
        clear_deals_store()
        clear_document_store()

    assert response.status_code == 201, {
        "observed_status": response.status_code,
        "observed_code": body.get("code"),
    }
    assert body["parse_status"] == "PARSED"
    document_id = UUID(body["document_id"])
    _assert_document_span_provenance(
        service,
        document_id=document_id,
        chosen_dpi=200,
        projected=(1_655, 2_339),
        expected=(1_654, 2_339),
        actual=(1_654, 2_339),
        require_operator_runtime_identity=True,
    )


@pytest.mark.parametrize(
    "boundary",
    _BOUNDARY_CASES,
    ids=lambda value: f"{value.position}-{value.chosen_dpi}dpi",
)
@requires_canonical_poppler_pin
def test_real_poppler_half_pixel_matrix_emits_exact_v2_render_provenance(
    tmp_path: Path,
    boundary: _BoundaryCase,
) -> None:
    source_path = tmp_path / f"fictional-half-pixel-{boundary.chosen_dpi}.pdf"
    _write_production_raster_pdf(source_path, page_points=boundary.page_points)
    pdf_bytes = source_path.read_bytes()
    page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    assert not (page.extract_text() or "").strip()
    exact_axis = boundary.page_points * boundary.chosen_dpi / 72
    assert math.isclose(
        exact_axis,
        boundary.half_pixel_floor + boundary.fractional_pixel,
        abs_tol=1e-9,
    )

    poppler_runtime = resolve_pinned_poppler_runtime()
    admission = build_pdf_ocr_render_admissions(
        data=pdf_bytes,
        page_numbers=(1,),
        poppler_runtime=poppler_runtime,
    )[0]
    expected_axis = boundary.expected_pixels_per_axis
    assert admission.chosen_dpi == boundary.chosen_dpi
    assert admission.projected_width == expected_axis
    assert admission.projected_height == expected_axis

    pages = ocr._ocr_pdf_file_render_admission_batches(
        source_path=source_path,
        render_admissions=(admission,),
        language="eng",
        deadline=time.monotonic() + 60,
        convert_from_path=convert_from_path,
        image_to_data=_tesseract_payload,
        output=_Output,
        compute_page_content_sha256s=False,
        poppler_runtime=poppler_runtime,
    )

    assert len(pages) == 1
    _assert_exact_v2_provenance(
        pages[0]["render_admission"],
        chosen_dpi=boundary.chosen_dpi,
        projected=(expected_axis, expected_axis),
        expected=(expected_axis, expected_axis),
        actual=(expected_axis, expected_axis),
        page_number=1,
        worker_payload=True,
    )


@requires_linux_governed_poppler
def test_half_pixel_fixture_persists_exact_v2_provenance_at_ingestion_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = next(
        value for value in _BOUNDARY_CASES if value.chosen_dpi == 200 and value.position == "half"
    )
    source_path = tmp_path / "company-002-document-00193.pdf"
    _write_production_raster_pdf(source_path, page_points=boundary.page_points)
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    service = _build_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        env=_strict_ocr_env(),
    )

    result = _ingest_file(
        service,
        source_tree_fingerprint=hashlib.sha256(b"fictional-rounding-tree").hexdigest(),
        source_path=source_path,
        safe_alias=source_path.name,
        content_sha256=digest,
        size_bytes=source_path.stat().st_size,
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    assert result.document_id is not None
    expected_axis = boundary.expected_pixels_per_axis
    _assert_document_span_provenance(
        service,
        document_id=result.document_id,
        chosen_dpi=boundary.chosen_dpi,
        projected=(expected_axis, expected_axis),
        expected=(expected_axis, expected_axis),
        actual=(expected_axis, expected_axis),
    )


@pytest.mark.parametrize(
    ("actual_width", "actual_height"),
    [
        pytest.param(1_002, 1_001, id="exact-mode-one-pixel-mismatch"),
        pytest.param(20_000_001, 1, id="actual-resource-overrun"),
    ],
)
@requires_canonical_poppler_pin
def test_worker_rejects_expected_dimension_mismatch_and_actual_resource_overrun(
    tmp_path: Path,
    actual_width: int,
    actual_height: int,
) -> None:
    source_path = tmp_path / "fictional-render-rejection.pdf"
    boundary = next(
        value for value in _BOUNDARY_CASES if value.chosen_dpi == 200 and value.position == "half"
    )
    _write_production_raster_pdf(source_path, page_points=boundary.page_points)
    poppler_runtime = resolve_pinned_poppler_runtime()
    admission = build_pdf_ocr_render_admissions(
        data=source_path.read_bytes(),
        page_numbers=(1,),
        poppler_runtime=poppler_runtime,
    )[0]
    image = _FakeImage(width=actual_width, height=actual_height)

    def forbidden_image_to_data(_image: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("Tesseract must not execute after render verification fails")

    with pytest.raises(ocr.OcrError):
        ocr._ocr_pdf_file_render_admission_batches(
            source_path=source_path,
            render_admissions=(admission,),
            language="eng",
            deadline=time.monotonic() + 5,
            convert_from_path=lambda *_args, **_kwargs: [image],
            image_to_data=forbidden_image_to_data,
            output=_Output,
            compute_page_content_sha256s=False,
            poppler_runtime=poppler_runtime,
        )
    assert image.closed is True
