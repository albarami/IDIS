"""Private local gate for the fund-supplied real_example data room."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import stat
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from idis.evaluation.real_example_gate_ledger import (
    RETRYABLE_REASON_CODES,
    ledger_entry_count,
    load_ledger,
    media_policy_key,
    ocr_policy_key,
    record_ledger_entry,
    save_ledger,
    terminal_ledger_entry,
)
from idis.evaluation.real_example_gate_runtime import (
    ParseAttempt,
    ParseAttemptFn,
    memory_exceeded,
    run_injected_parse_with_timeout,
    run_parse_subprocess,
)
from idis.evaluation.real_example_source_package import (
    DEFAULT_EXPECTED_COMPANY_BYTES,
    DEFAULT_EXPECTED_FILE_COUNTS,
    DEFAULT_EXPECTED_TOTAL_BYTES,
    DEFAULT_EXPECTED_TOTAL_FILES,
    DEFAULT_SOURCE_ROOT,
    TwoCompanySourceContract,
)
from idis.evaluation.real_example_source_package import (
    SourcePackageContractError as _SourcePackageContractError,
)
from idis.evaluation.real_example_source_package import (
    build_two_company_source_contract as _build_two_company_source_contract,
)
from idis.evaluation.real_example_source_package import (
    prepare_two_company_source_packages as _prepare_two_company_source_packages,
)
from idis.evaluation.real_example_terminal_reconciliation import (
    DEFAULT_PDF_FILE_COUNT,
    DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT,
    DEFAULT_PDF_OCR_REQUIRED_PAGE_COUNT,
    DEFAULT_PDF_PAGE_COUNT,
    MEDIA_RUNTIME_PROVENANCE_KEY,
    OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION,
    PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION,
    TERMINAL_INGESTION_PRIOR_RECONCILIATION_SCHEMA_VERSION,
    TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION,
    PdfOcrRequirementsCensus,
    TerminalIngestionReconciliationError,
    collect_durable_terminal_rows,
    inspect_pdf_ocr_requirements,
    reconcile_terminal_ingestion,
)
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.parsers.media import (
    FASTER_WHISPER_ADAPTER_NAME,
    MAX_MEDIA_DURATION_SECONDS,
    FasterWhisperMediaConfig,
    probe_faster_whisper_model,
)
from idis.parsers.video_frame_ocr import (
    FRAME_OCR_RECEIPT_SCHEMA_VERSION,
    FRAME_SAMPLING_POLICY,
    MAX_FRAME_SAMPLES,
)
from idis.runtime.profile import (
    StrictProfileError,
    StrictRuntimeProfile,
    require_runtime_profile,
)
from idis.runtime.task4_ingestion_scope import (
    TASK4_BUFFERED_TRANSPORT,
    TASK4_CANARY_SCOPE,
    TASK4_OWNER_SCOPE,
    TASK4_STREAM_TRANSPORT,
    is_task4_owner_upload_call,
    task4_client_call_allowed,
)
from idis.services.documents.parser_capabilities import capability_for_document
from idis.services.ingestion.no_substantive_content_attestation import (
    NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT,
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestation,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    load_document_no_substantive_content_attestation,
)
from idis.services.media_health import (
    PINNED_FRAME_OCR_FIXTURE_SHA256,
    PINNED_FRAME_OCR_MANIFEST_SHA256,
    PINNED_STT_FIXTURE_SHA256,
    PINNED_STT_MANIFEST_SHA256,
    MediaHealthStatus,
    check_media_frame_ocr_health,
    check_media_health,
    check_media_inference_health,
)
from idis.services.ocr_health import OcrHealthStatus, check_ocr_health

DEFAULT_REAL_EXAMPLE_ROOT = Path("real_example")
DEFAULT_LEDGER_PATH = Path(".local_reports") / "real_example_gate_ledger.json"
DEFAULT_PER_FILE_TIMEOUT_SECONDS = 30.0
HASH_CHUNK_SIZE_BYTES = 1024 * 1024
MAX_OCR_PAGES = 10
MAX_OCR_TIMEOUT_SECONDS = 120.0
MIN_OCR_DPI = 72
MAX_OCR_DPI = 300
MAX_MEDIA_TIMEOUT_SECONDS = 120.0
MEDIA_ADAPTER_NONE = "none"
MEDIA_ADAPTER_CHOICES = frozenset({MEDIA_ADAPTER_NONE, FASTER_WHISPER_ADAPTER_NAME})
DEFAULT_MEDIA_LANGUAGE = "en"
DEFAULT_MEDIA_COMPUTE_TYPE = "int8"
_TASK4_DURABLE_PROOF_SCHEMA_VERSION_V3 = "slice100-task4-durable-ingestion-proof-v3"
_TASK4_DURABLE_PROOF_SCHEMA_VERSION_V4 = "slice100-task4-durable-ingestion-proof-v4"
_TASK4_DURABLE_PROOF_SCHEMA_VERSION_V5 = "slice100-task4-durable-ingestion-proof-v5"
TASK4_DURABLE_PROOF_SCHEMA_VERSION = "slice100-task4-durable-ingestion-proof-v6"
_TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION_V3 = "safe-terminal-reconciliation-v3"
TASK4_DURABLE_PROOF_REPORT_NAME = "task4-durable-ingestion-proof.json"
TASK4_TERMINAL_SNAPSHOT_MISMATCH_REPORT_NAME = "task4-terminal-snapshot-mismatch.json"
TASK4_PREPARATION_LEDGER_NAME = "prepare-ledger.jsonl"
TASK4_TERMINAL_SNAPSHOT_SCHEMA_VERSION = "task4-terminal-snapshot-digests-v1"
TASK4_TERMINAL_SNAPSHOT_MISMATCH_SCHEMA_VERSION = "task4-terminal-snapshot-mismatch-v1"
_TASK4_TERMINAL_SNAPSHOT_COMPONENTS = (
    "documents",
    "artifacts",
    "spans",
    "censuses",
    "audit_counts",
)
_TERMINAL_RECONCILIATION_V4_REQUIRED_KEYS = frozenset(
    {
        "manual_v1_confirmed_document_count",
        "watermark_classifier_document_count",
        "watermark_manifest_bound_receipt_count",
        "watermark_manifest_runtime_summary_sha256",
        "watermark_classified_page_count",
        "watermark_batch_attested_page_count",
        "watermark_runtime_unseen_page_count",
        "watermark_classified_pages_by_company",
        "watermark_classification_receipts_sha256",
        "runtime_review_required",
    }
)
_TERMINAL_RECONCILIATION_V5_REQUIRED_KEYS = frozenset(
    {
        "pdf_page_disposition_census_schema_version",
        "pdf_page_disposition_class_counts",
        "pdf_page_disposition_censuses_sha256",
    }
)
_TASK4_ACCEPTANCE_NAMESPACE_RE = re.compile(r"^acceptance-[0-9]{3}$")
TASK4_PLAN_RELATIVE_PATH = Path(
    "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md"
)
TASK4_CODE_STATIC_RELATIVE_PATHS = (
    Path("contracts/contract_lock.json"),
    Path("openapi/IDIS_OpenAPI_v6_3.yaml"),
    Path("scripts/invoke_strict_native_process.ps1"),
    Path("scripts/strict_process_identity.psm1"),
)
TASK4_CODE_RUNTIME_ROOT = Path("src/idis")
TASK4_LARGE_FILE_THRESHOLD_BYTES = 50 * 1024 * 1024
TASK4_EXPECTED_FORMAT_COUNTS = {
    "pdf": 223,
    "xlsx": 27,
    "mp4": 8,
    "docx": 3,
    "png": 2,
    "pptx": 2,
    "html": 1,
    "txt": 1,
}
TASK4_EXPECTED_PDF_ENCRYPTION_DISPOSITIONS = {
    "not_encryption_marked": 81,
    "empty_credential_openable": 142,
    "configured_credential_openable": 0,
    "credential_required": 0,
    "credential_invalid": 0,
}
_APPROVED_ACCEPTANCE_API_KEY_ENV = "IDIS_REAL_EXAMPLE_API_KEY"
_APPROVED_CANARY_API_KEY_ENV = "IDIS_ISOLATION_CANARY_API_KEY"
_DURABLE_DOWNLOAD_IDIS_HEADERS = frozenset(
    {
        "x-idis-content-sha256",
        "x-idis-content-size",
        "x-idis-object-version",
        "x-idis-stream-contract",
        "x-idis-ratelimit-limit",
        "x-idis-ratelimit-remaining",
    }
)
_DURABLE_DOWNLOAD_SINGLETON_HEADERS = frozenset(
    {
        *_DURABLE_DOWNLOAD_IDIS_HEADERS,
        "cache-control",
        "content-length",
        "content-type",
        "x-content-type-options",
    }
)
_CANONICAL_NONNEGATIVE_DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z", re.ASCII)
_LOCAL_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0
_LOCAL_HTTP_TRANSACTION_GRACE_SECONDS = 30.0
_LOCAL_HTTP_MIN_WRITE_TIMEOUT_SECONDS = 30.0
_LOCAL_HTTP_ASSUMED_WRITE_BYTES_PER_SECOND = 8 * 1024 * 1024
_SAFE_PROOF_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "phase",
        "total_file_count",
        "source_tree_fingerprint",
        "preparation_ledger_sha256",
        "terminal_rows_sha256",
        "plan_sha256",
        "code_sha256",
        "reconciliation_sha256",
        "profile_hmac_sha256",
        "local_runtime_readiness_sha256",
        "no_substantive_content_attestation",
        "no_substantive_content_attestation_sha256",
        "confirmed_no_substantive_content_receipts_sha256",
        "confirmed_no_substantive_content_variant_counts",
        "api_boot_sha256",
        "previous_api_boot_sha256",
        "authenticated_download_company_count",
        "authenticated_download_receipts_sha256",
        "total_bytes",
        "bytes_by_company",
        "parser_counts",
        "upload_post_count",
        "tenant_isolation",
        "tenant_isolation_receipt_sha256",
        "restart_survival_verified",
    }
)
_SAFE_PROOF_OPTIONAL_KEYS = frozenset({"counts_by_company", "counts_by_extension"})
_SAFE_PROOF_V4_KEYS = frozenset(
    {
        "watermark_classified_pages_by_company",
        "watermark_classification_receipts_sha256",
        "watermark_manifest_bound_receipt_count",
        "watermark_manifest_runtime_summary_sha256",
        "watermark_runtime_review_required",
    }
)
_SAFE_PROOF_V5_KEYS = frozenset(
    {
        *_SAFE_PROOF_V4_KEYS,
        "pdf_page_disposition_census_schema_version",
        "pdf_page_disposition_class_counts",
        "pdf_page_disposition_censuses_sha256",
    }
)
_SAFE_PROOF_V6_KEYS = frozenset({*_SAFE_PROOF_V5_KEYS, "terminal_snapshot_digests"})
_RESTART_FROZEN_DIGEST_KEYS = (
    "source_tree_fingerprint",
    "preparation_ledger_sha256",
    "terminal_rows_sha256",
    "plan_sha256",
    "code_sha256",
    "reconciliation_sha256",
    "profile_hmac_sha256",
    "local_runtime_readiness_sha256",
    "no_substantive_content_attestation_sha256",
    "confirmed_no_substantive_content_receipts_sha256",
    "authenticated_download_receipts_sha256",
    "tenant_isolation_receipt_sha256",
)
_FORBIDDEN_SAFE_PROOF_KEYS = frozenset(
    {
        "deal_id",
        "document_id",
        "doc_id",
        "package_id",
        "span_id",
        "version_id",
        "generated_upload_name",
        "text_excerpt",
        "source_path",
        "private_filename",
        "raw_text",
        "attestation_text",
        "signature_base64",
        "exemplar_canonical_bitmap_base64",
        "stamp_email",
        "stamp_timestamp",
        "private_path",
        "path",
        "uri",
        "api_key",
        "canary_api_key",
    }
)
SUPPORTED_PARSE_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
SUPPORTED_PARSER_NAMES = frozenset({"pdf", "xlsx", "docx", "pptx"})
OCR_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"})
TEXT_PARSE_EXTENSIONS = frozenset({".html", ".htm", ".txt"})
MEDIA_EXTENSIONS = frozenset({".mp4"})

# Task 3 extends the existing gate contract rather than introducing a parallel
# runner.  These aliases are intentionally public from this established module.
SourcePackageContractError = _SourcePackageContractError
build_two_company_source_contract = _build_two_company_source_contract
prepare_two_company_source_packages = _prepare_two_company_source_packages


class DurableIngestionGateError(RuntimeError):
    """Secret-safe Task 4 external-gate failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class _DurableApiSettings:
    api_base_url: str
    api_key: str
    canary_api_key: str
    connect_timeout_seconds: float
    write_timeout_seconds: float
    read_timeout_seconds: float
    pool_timeout_seconds: float
    request_interval_seconds: float


@dataclass(frozen=True, slots=True, repr=False)
class _PdfOcrExecutionCensus:
    """Separate source admission from classifier-first OCR execution."""

    admission_content_sha256s: frozenset[str]
    execution_content_sha256s: frozenset[str]
    classifier_only_content_sha256s: frozenset[str]
    required_pages_by_content_sha256: Mapping[str, tuple[int, ...]]
    page_count_by_content_sha256: Mapping[str, int]
    render_admissions_by_content_sha256: Mapping[str, tuple[Mapping[str, object], ...]]
    admission_file_count: int
    admission_page_count: int
    execution_file_count: int
    execution_page_count: int
    classifier_first_file_count: int
    classifier_only_file_count: int
    classifier_only_page_count: int
    classifier_first_page_count: int
    classifier_terminal_file_count: int

    @property
    def total_ocr_execution_file_count(self) -> int:
        return self.execution_file_count + TASK4_EXPECTED_FORMAT_COUNTS["png"]

    def safe_count_fields(self) -> dict[str, int]:
        return {
            "pdf_ocr_admission_file_count": self.admission_file_count,
            "pdf_ocr_admission_page_count": self.admission_page_count,
            "pdf_ocr_execution_page_count": self.execution_page_count,
            "pdf_classifier_first_file_count": self.classifier_first_file_count,
            "pdf_classifier_only_file_count": self.classifier_only_file_count,
            "pdf_classifier_only_page_count": self.classifier_only_page_count,
            "pdf_classifier_first_page_count": self.classifier_first_page_count,
            "pdf_classifier_terminal_file_count": self.classifier_terminal_file_count,
        }


class _DeterministicRequestPacer:
    """Apply a deterministic interval derived from the admitted rate-limit profile."""

    __slots__ = ("_clock", "_interval_seconds", "_next_request_at", "_sleeper")

    def __init__(
        self,
        *,
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._next_request_at: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next_request_at is not None and now < self._next_request_at:
            self._sleeper(self._next_request_at - now)
            now = self._clock()
        self._next_request_at = max(now, self._next_request_at or now) + self._interval_seconds


def _import_httpx() -> Any:
    import httpx

    return httpx


class _PacedExternalApiClient:
    """Real external HTTP transport with finite bounds and admitted local pacing."""

    __slots__ = (
        "_api_key",
        "_auth_error_code",
        "_client",
        "_client_scope",
        "_pacer",
        "_upload_post_count",
    )

    def __init__(
        self,
        *,
        settings: _DurableApiSettings,
        api_key: str,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        client_scope, reviewed_api_key, auth_error_code = self._derive_client_scope(
            settings=settings,
            api_key=api_key,
        )
        httpx = _import_httpx()
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            write=settings.write_timeout_seconds,
            read=settings.read_timeout_seconds,
            pool=settings.pool_timeout_seconds,
        )
        self._client = httpx.Client(
            base_url=settings.api_base_url,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        self._api_key = reviewed_api_key
        self._auth_error_code = auth_error_code
        self._client_scope = client_scope
        self._pacer = _DeterministicRequestPacer(
            interval_seconds=settings.request_interval_seconds,
            clock=clock,
            sleeper=sleeper,
        )
        self._upload_post_count = 0

    @property
    def upload_post_count(self) -> int:
        return self._upload_post_count

    @property
    def client_scope(self) -> str:
        """Return the non-secret owner/canary purpose derived from the reviewed key."""
        return self._client_scope

    def __enter__(self) -> _PacedExternalApiClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, **kwargs: Any) -> Any:
        self._admit_call(
            transport=TASK4_BUFFERED_TRANSPORT,
            method="GET",
            url=url,
            kwargs=kwargs,
        )
        self._pacer.wait()
        return self._client.get(url, **kwargs)

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        """Open one admitted real HTTP response without materializing its body."""
        self._admit_call(
            transport=TASK4_STREAM_TRANSPORT,
            method=method,
            url=url,
            kwargs=kwargs,
        )
        self._pacer.wait()
        return self._client.stream(method, url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        canonical_path = self._admit_call(
            transport=TASK4_BUFFERED_TRANSPORT,
            method="POST",
            url=url,
            kwargs=kwargs,
        )
        self._pacer.wait()
        if is_task4_owner_upload_call(
            client_scope=self._client_scope,
            transport=TASK4_BUFFERED_TRANSPORT,
            method="POST",
            path=canonical_path,
        ):
            self._upload_post_count += 1
        return self._client.post(url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        self._admit_call(
            transport=TASK4_BUFFERED_TRANSPORT,
            method="DELETE",
            url=url,
            kwargs=kwargs,
        )
        self._pacer.wait()
        return self._client.delete(url, **kwargs)

    @staticmethod
    def _derive_client_scope(
        *,
        settings: _DurableApiSettings,
        api_key: str,
    ) -> tuple[str, str, str]:
        owner_api_key = settings.api_key
        canary_api_key = settings.canary_api_key
        if (
            not isinstance(api_key, str)
            or not api_key
            or not isinstance(owner_api_key, str)
            or not owner_api_key
            or not isinstance(canary_api_key, str)
            or not canary_api_key
            or hmac.compare_digest(owner_api_key, canary_api_key)
        ):
            raise DurableIngestionGateError("AUTH_ACCEPTANCE_KEY_MISMATCH")
        if hmac.compare_digest(api_key, owner_api_key):
            return TASK4_OWNER_SCOPE, owner_api_key, "AUTH_ACCEPTANCE_KEY_MISMATCH"
        if hmac.compare_digest(api_key, canary_api_key):
            return TASK4_CANARY_SCOPE, canary_api_key, "AUTH_CANARY_KEY_MISMATCH"
        raise DurableIngestionGateError("AUTH_ACCEPTANCE_KEY_MISMATCH")

    def _admit_call(
        self,
        *,
        transport: str,
        method: str,
        url: str,
        kwargs: Mapping[str, Any],
    ) -> str:
        canonical_path = self._admit_url(url)
        if not task4_client_call_allowed(
            client_scope=self._client_scope,
            transport=transport,
            method=method,
            path=canonical_path,
        ):
            raise DurableIngestionGateError("API_ACCEPTANCE_PATH_NONCANONICAL")
        self._admit_request(kwargs)
        return canonical_path

    def _admit_request(self, kwargs: Mapping[str, Any]) -> None:
        headers = kwargs.get("headers")
        supplied_api_key = headers.get("X-IDIS-API-Key") if isinstance(headers, Mapping) else None
        if not isinstance(supplied_api_key, str) or not hmac.compare_digest(
            supplied_api_key,
            self._api_key,
        ):
            raise DurableIngestionGateError(self._auth_error_code)

    @staticmethod
    def _admit_url(url: str) -> str:
        try:
            parsed = urlsplit(url)
        except (TypeError, ValueError):
            raise DurableIngestionGateError("API_ACCEPTANCE_PATH_NONCANONICAL") from None
        if (
            not isinstance(url, str)
            or not url.startswith("/")
            or url.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise DurableIngestionGateError("API_ACCEPTANCE_PATH_NONCANONICAL")
        return parsed.path


def _build_durable_api_settings(
    *,
    env: Mapping[str, str],
    api_base_url: str | None,
    api_key_env: str | None,
    canary_api_key_env: str | None,
) -> _DurableApiSettings:
    """Bind external transport only to the admitted strict-profile names and values."""
    if api_key_env != _APPROVED_ACCEPTANCE_API_KEY_ENV:
        raise DurableIngestionGateError("AUTH_ACCEPTANCE_KEY_MISMATCH")
    if canary_api_key_env != _APPROVED_CANARY_API_KEY_ENV:
        raise DurableIngestionGateError("AUTH_CANARY_KEY_MISMATCH")
    api_key = str(env.get(_APPROVED_ACCEPTANCE_API_KEY_ENV, "")).strip()
    canary_api_key = str(env.get(_APPROVED_CANARY_API_KEY_ENV, "")).strip()
    if not api_key:
        raise DurableIngestionGateError("AUTH_ACCEPTANCE_KEY_MISMATCH")
    if not canary_api_key or canary_api_key == api_key:
        raise DurableIngestionGateError("AUTH_CANARY_KEY_MISMATCH")

    configured_base_url = str(env.get("IDIS_ACCEPTANCE_API_BASE_URL", "")).strip().rstrip("/")
    requested_base_url = str(api_base_url or "").strip().rstrip("/")
    if not configured_base_url or requested_base_url != configured_base_url:
        raise DurableIngestionGateError("API_ACCEPTANCE_PATH_NONCANONICAL")
    try:
        parsed = urlsplit(requested_base_url)
        port = parsed.port
    except ValueError:
        raise DurableIngestionGateError("API_ACCEPTANCE_PATH_NONCANONICAL") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 0 < port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DurableIngestionGateError("API_ACCEPTANCE_PATH_NONCANONICAL")

    ocr_timeout = _strict_positive_float(env, "IDIS_OCR_TIMEOUT_SECONDS")
    media_timeout = _strict_positive_float(env, "IDIS_MEDIA_TIMEOUT_SECONDS")
    ingest_max_bytes = _strict_positive_int(env, "IDIS_INGEST_MAX_BYTES")
    integration_rpm = _strict_positive_int(env, "IDIS_RATE_LIMIT_INTEGRATION_RPM")
    _strict_positive_int(env, "IDIS_RATE_LIMIT_BURST_MULTIPLIER")
    if ingest_max_bytes != 536_870_912:
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")

    write_timeout = max(
        _LOCAL_HTTP_MIN_WRITE_TIMEOUT_SECONDS,
        ingest_max_bytes / _LOCAL_HTTP_ASSUMED_WRITE_BYTES_PER_SECOND,
    )
    read_timeout = max(ocr_timeout, media_timeout) + _LOCAL_HTTP_TRANSACTION_GRACE_SECONDS
    # Burst expands bucket capacity only; the sustained refill rate remains ``rpm / 60``.
    # Pace at that refill rate so the long 267-row proof cannot exhaust its initial burst.
    request_interval = 60.0 / integration_rpm
    bounds = (
        _LOCAL_HTTP_CONNECT_TIMEOUT_SECONDS,
        write_timeout,
        read_timeout,
        _LOCAL_HTTP_CONNECT_TIMEOUT_SECONDS,
        request_interval,
    )
    if any(not math.isfinite(value) or value <= 0 for value in bounds):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    return _DurableApiSettings(
        api_base_url=requested_base_url,
        api_key=api_key,
        canary_api_key=canary_api_key,
        connect_timeout_seconds=_LOCAL_HTTP_CONNECT_TIMEOUT_SECONDS,
        write_timeout_seconds=write_timeout,
        read_timeout_seconds=read_timeout,
        pool_timeout_seconds=_LOCAL_HTTP_CONNECT_TIMEOUT_SECONDS,
        request_interval_seconds=request_interval,
    )


def _strict_positive_float(env: Mapping[str, str], name: str) -> float:
    try:
        value = float(str(env.get(name, "")).strip())
    except ValueError:
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED") from None
    if not math.isfinite(value) or value <= 0:
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    return value


def _strict_positive_int(env: Mapping[str, str], name: str) -> int:
    raw = str(env.get(name, "")).strip()
    if not raw.isascii() or not raw.isdecimal():
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    value = int(raw)
    if value <= 0:
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_task4_gate_profile(profile: object) -> str:
    """Require the reviewed unpromoted Task-4 gate profile and return its hash."""
    try:
        require_runtime_profile(profile, command="gate")  # type: ignore[arg-type]
    except StrictProfileError:
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED") from None
    values = getattr(profile, "values", None)
    base_profile_hmac = getattr(profile, "base_profile_hmac", None)
    if (
        getattr(profile, "command", None) != "gate"
        or getattr(profile, "generation", None) != "bootstrap-v1"
        or getattr(profile, "process_role", None) is not None
        or not isinstance(values, Mapping)
        or values.get("IDIS_REQUIRE_PROMOTED_PROMPTS") != "0"
        or not _is_sha256(base_profile_hmac)
    ):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    return hashlib.sha256(str(base_profile_hmac).encode("ascii")).hexdigest()


def _require_task4_local_runtime_readiness(*, env: Mapping[str, str]) -> str:
    """Run presence checks plus real pinned-fixture STT and frame-OCR probes."""
    from idis.parsers.poppler_linux_runtime import PinnedLinuxPopplerRuntime
    from idis.parsers.poppler_runtime import (
        PopplerRuntimeBindingError,
        resolve_pinned_poppler_runtime,
    )

    try:
        governed_poppler = resolve_pinned_poppler_runtime()
    except PopplerRuntimeBindingError:
        governed_poppler = None
    if type(governed_poppler) is PinnedLinuxPopplerRuntime:
        raise DurableIngestionGateError("POPPLER_RUNTIME_NONCANONICAL")
    ocr = check_ocr_health(env=env)
    media = check_media_health(env=env)
    inference = check_media_inference_health(env=env)
    frame_ocr = check_media_frame_ocr_health(env=env)
    if (
        getattr(ocr, "status", None) != OcrHealthStatus.HEALTHY
        or getattr(ocr, "enabled", None) is not True
        or getattr(media, "status", None) != MediaHealthStatus.HEALTHY
        or getattr(media, "enabled", None) is not True
        or getattr(inference, "status", None) != MediaHealthStatus.HEALTHY
        or getattr(inference, "may_proceed", None) is not True
        or getattr(frame_ocr, "status", None) != MediaHealthStatus.HEALTHY
        or getattr(frame_ocr, "may_proceed", None) is not True
    ):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    safe_hashes = {
        "fixture_sha256": getattr(inference, "fixture_sha256", None),
        "manifest_sha256": getattr(inference, "manifest_sha256", None),
        "model_path_sha256": getattr(inference, "model_path_sha256", None),
        "model_files_sha256": getattr(inference, "model_files_sha256", None),
    }
    segment_count = getattr(inference, "segment_count", None)
    first_start_ms = getattr(inference, "first_start_ms", None)
    final_end_ms = getattr(inference, "final_end_ms", None)
    frame_hashes = {
        "fixture_sha256": getattr(frame_ocr, "fixture_sha256", None),
        "manifest_sha256": getattr(frame_ocr, "manifest_sha256", None),
        "config_sha256": getattr(frame_ocr, "config_sha256", None),
        "schedule_sha256": getattr(frame_ocr, "schedule_sha256", None),
    }
    duration_ms = getattr(frame_ocr, "duration_ms", None)
    scheduled_sample_count = getattr(frame_ocr, "scheduled_sample_count", None)
    completed_sample_count = getattr(frame_ocr, "completed_sample_count", None)
    nonempty_sample_count = getattr(frame_ocr, "nonempty_sample_count", None)
    evidence_span_count = getattr(frame_ocr, "evidence_span_count", None)
    if (
        safe_hashes["fixture_sha256"] != PINNED_STT_FIXTURE_SHA256
        or safe_hashes["manifest_sha256"] != PINNED_STT_MANIFEST_SHA256
        or any(not _is_sha256(value) for value in safe_hashes.values())
        or type(segment_count) is not int
        or segment_count <= 0
        or type(first_start_ms) is not int
        or first_start_ms < 0
        or type(final_end_ms) is not int
        or final_end_ms <= first_start_ms
        or frame_hashes["fixture_sha256"] != PINNED_FRAME_OCR_FIXTURE_SHA256
        or frame_hashes["manifest_sha256"] != PINNED_FRAME_OCR_MANIFEST_SHA256
        or any(not _is_sha256(value) for value in frame_hashes.values())
        or getattr(frame_ocr, "receipt_schema_version", None) != FRAME_OCR_RECEIPT_SCHEMA_VERSION
        or getattr(frame_ocr, "sampling_policy", None) != FRAME_SAMPLING_POLICY
        or duration_ms != 3000
        or type(scheduled_sample_count) is not int
        or not 0 < scheduled_sample_count <= MAX_FRAME_SAMPLES
        or completed_sample_count != scheduled_sample_count
        or type(nonempty_sample_count) is not int
        or not 0 <= nonempty_sample_count <= completed_sample_count
        or type(evidence_span_count) is not int
        or evidence_span_count < nonempty_sample_count
    ):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    return _canonical_json_sha256(
        {
            "ocr_status": "healthy",
            "media_status": "healthy",
            "inference": {
                **safe_hashes,
                "segment_count": segment_count,
                "first_start_ms": first_start_ms,
                "final_end_ms": final_end_ms,
            },
            "frame_ocr": {
                **frame_hashes,
                "receipt_schema_version": FRAME_OCR_RECEIPT_SCHEMA_VERSION,
                "sampling_policy": FRAME_SAMPLING_POLICY,
                "duration_ms": duration_ms,
                "scheduled_sample_count": scheduled_sample_count,
                "completed_sample_count": completed_sample_count,
                "nonempty_sample_count": nonempty_sample_count,
                "evidence_span_count": evidence_span_count,
            },
        }
    )


def _canonical_json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
    return hashlib.sha256(encoded).hexdigest()


_CANONICAL_TIMESTAMP_RE = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z",
    re.ASCII,
)
_CANONICAL_SHA256_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z", re.ASCII)


def _canonical_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if value == 0:
        return "0"
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int) or not digits:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        rendered = coefficient + ("0" * exponent)
    else:
        decimal_position = len(coefficient) + exponent
        if decimal_position <= 0:
            rendered = f"0.{('0' * -decimal_position)}{coefficient}"
        else:
            rendered = f"{coefficient[:decimal_position]}.{coefficient[decimal_position:]}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    integer, separator, fraction = rendered.partition(".")
    integer = integer.lstrip("0") or "0"
    rendered = integer + (f".{fraction}" if separator and fraction else "")
    return f"-{rendered}" if sign else rendered


def _canonical_snapshot_value(value: object) -> object:
    """Normalize one snapshot value without erasing ordered evidence sequences."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, StrEnum):
        return _canonical_snapshot_value(value.value)
    if isinstance(value, UUID):
        return {"$snapshot_scalar": "uuid", "value": str(value).lower()}
    if isinstance(value, datetime):
        return {"$snapshot_scalar": "timestamp", "value": _canonical_utc_timestamp(value)}
    if isinstance(value, datetime_time):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        anchored = datetime.combine(date(1970, 1, 1), value)
        return {"$snapshot_scalar": "time", "value": _canonical_utc_timestamp(anchored)[11:]}
    if isinstance(value, date):
        return {"$snapshot_scalar": "date", "value": value.isoformat()}
    if isinstance(value, int):
        return {"$snapshot_scalar": "number", "value": _canonical_decimal(Decimal(value))}
    if isinstance(value, Decimal):
        return {"$snapshot_scalar": "number", "value": _canonical_decimal(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        return {
            "$snapshot_scalar": "number",
            "value": _canonical_decimal(Decimal(str(value))),
        }
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        if _CANONICAL_SHA256_RE.fullmatch(normalized) is not None:
            return normalized.lower()
        try:
            if len(normalized) == 36:
                return {
                    "$snapshot_scalar": "uuid",
                    "value": str(UUID(normalized)).lower(),
                }
        except (AttributeError, TypeError, ValueError):
            pass
        if _CANONICAL_TIMESTAMP_RE.fullmatch(normalized) is not None:
            try:
                parsed_timestamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except ValueError:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
            return {
                "$snapshot_scalar": "timestamp",
                "value": _canonical_utc_timestamp(parsed_timestamp),
            }
        return normalized
    if isinstance(value, Mapping):
        normalized_mapping: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized_mapping:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            normalized_mapping[key] = _canonical_snapshot_value(raw_value)
        return normalized_mapping
    if isinstance(value, (set, frozenset)):
        normalized_items = [_canonical_snapshot_value(item) for item in value]
        return sorted(normalized_items, key=_canonical_snapshot_sort_key)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_snapshot_value(item) for item in value]
    raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")


def _canonical_snapshot_sort_key(value: object) -> str:
    normalized = _canonical_snapshot_value(value)
    try:
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None


def _canonical_snapshot_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_snapshot_sort_key(value).encode("utf-8")).hexdigest()


def _api_boot_sha256(*, api_client: Any, api_key: str) -> str:
    """Read and hash the current API boot UUID without persisting the raw identity."""
    try:
        response = api_client.get(
            "/health",
            headers={"X-IDIS-API-Key": api_key},
        )
        payload = response.json()
        if response.status_code != 200 or not isinstance(payload, Mapping):
            raise ValueError
        if set(payload) != {"status", "time", "version", "boot_id"}:
            raise ValueError
        raw_boot_id = payload.get("boot_id")
        boot_id = UUID(str(raw_boot_id))
        if payload.get("status") != "ok" or payload.get("version") != "6.3" or boot_id.version != 4:
            raise ValueError
    except DurableIngestionGateError:
        raise
    except Exception:
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
    return hashlib.sha256(str(boot_id).encode("ascii")).hexdigest()


def _require_api_profile_match(
    *,
    api_client: Any,
    api_key: str,
    expected_profile_hmac_sha256: str,
) -> None:
    """Bind the external API process to the gate's reviewed base profile."""
    if not _is_sha256(expected_profile_hmac_sha256):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    try:
        response = api_client.get(
            "/v1/strict-readiness",
            headers={"X-IDIS-API-Key": api_key},
        )
        payload = response.json()
        server_profile_hmac_sha256 = (
            payload.get("profile_hmac_sha256") if isinstance(payload, Mapping) else None
        )
    except DurableIngestionGateError:
        raise
    except Exception:
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED") from None
    if (
        getattr(response, "status_code", None) != 200
        or not isinstance(server_profile_hmac_sha256, str)
        or not _is_sha256(server_profile_hmac_sha256)
        or not hmac.compare_digest(server_profile_hmac_sha256, expected_profile_hmac_sha256)
    ):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")


def _validated_durable_download_headers(response_headers_raw: object) -> dict[str, str]:
    """Return exact singleton download headers with case-insensitive names."""
    if not isinstance(response_headers_raw, Mapping):
        raise ValueError
    multi_items = getattr(response_headers_raw, "multi_items", None)
    if not callable(multi_items):
        raise ValueError
    try:
        raw_items = list(multi_items())
    except Exception:
        raise ValueError from None

    values_by_name: dict[str, list[str]] = {}
    for raw_name, raw_value in raw_items:
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError
        try:
            raw_name.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError from None
        name = raw_name.lower()
        values_by_name.setdefault(name, []).append(raw_value)

    observed_idis_headers = {name for name in values_by_name if name.startswith("x-idis-")}
    if observed_idis_headers != _DURABLE_DOWNLOAD_IDIS_HEADERS:
        raise ValueError
    if "content-disposition" in values_by_name:
        raise ValueError
    if any(len(values_by_name.get(name, ())) != 1 for name in _DURABLE_DOWNLOAD_SINGLETON_HEADERS):
        raise ValueError

    headers = {name: values_by_name[name][0] for name in _DURABLE_DOWNLOAD_SINGLETON_HEADERS}
    limit_text = headers["x-idis-ratelimit-limit"]
    remaining_text = headers["x-idis-ratelimit-remaining"]
    if (
        _CANONICAL_NONNEGATIVE_DECIMAL_RE.fullmatch(limit_text) is None
        or _CANONICAL_NONNEGATIVE_DECIMAL_RE.fullmatch(remaining_text) is None
    ):
        raise ValueError
    try:
        limit = int(limit_text)
        remaining = int(remaining_text)
    except (ValueError, OverflowError):
        raise ValueError from None
    if remaining > limit:
        raise ValueError
    return headers


def _verify_authenticated_downloads(
    *,
    api_client: Any,
    api_key: str,
    acceptance_tenant_id: str,
    source_companies: Sequence[Any],
    terminal_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Stream one deterministic durable object per company and hash exact bytes."""
    try:
        str(UUID(acceptance_tenant_id))
    except (TypeError, ValueError, AttributeError):
        raise DurableIngestionGateError("AUTH_ACCEPTANCE_KEY_MISMATCH") from None
    rows_by_key: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in terminal_rows:
        alias = row.get("company_alias")
        sequence = row.get("source_sequence")
        if not isinstance(alias, str) or type(sequence) is not int:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        key = (alias, sequence)
        if key in rows_by_key:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        rows_by_key[key] = row

    receipts: list[dict[str, Any]] = []
    headers = {"X-IDIS-API-Key": api_key}
    for company in source_companies:
        files = tuple(getattr(company, "files", ()))
        alias = getattr(company, "company_alias", None)
        if not isinstance(alias, str) or not files:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        source = min(files, key=lambda item: (item.size_bytes, item.sequence))
        terminal_row = rows_by_key.get((alias, source.sequence))
        if terminal_row is None:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        artifact_id = terminal_row.get("doc_id")
        deal_id = terminal_row.get("deal_id")
        object_integrity = terminal_row.get("durable_object_integrity")
        expected_version_id = (
            object_integrity.get("version_id") if isinstance(object_integrity, Mapping) else None
        )
        if (
            not isinstance(artifact_id, str)
            or not isinstance(deal_id, str)
            or not isinstance(object_integrity, Mapping)
            or object_integrity.get("schema_version") != "durable-object-integrity-v1"
            or object_integrity.get("content_sha256") != source.content_sha256
            or object_integrity.get("size_bytes") != source.size_bytes
            or object_integrity.get("streaming_verified") is not True
            or not isinstance(expected_version_id, str)
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        try:
            if str(UUID(expected_version_id)) != expected_version_id:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
        try:
            with api_client.stream(
                "GET",
                f"/v1/documents/{artifact_id}/content",
                headers=headers,
            ) as response:
                response_headers_raw = getattr(response, "headers", None)
                if response.status_code != 200:
                    raise ValueError
                response_headers = _validated_durable_download_headers(response_headers_raw)
                if (
                    response_headers.get("content-type") != "application/octet-stream"
                    or response_headers.get("cache-control") != "no-store"
                    or response_headers.get("x-content-type-options") != "nosniff"
                    or response_headers.get("x-idis-stream-contract") != "durable-object-stream-v1"
                    or response_headers.get("x-idis-content-sha256") != source.content_sha256
                    or int(response_headers.get("content-length", "-1")) != source.size_bytes
                    or int(response_headers.get("x-idis-content-size", "-1")) != source.size_bytes
                ):
                    raise ValueError
                version_id = response_headers.get("x-idis-object-version")
                if version_id != expected_version_id:
                    raise ValueError

                digest = hashlib.sha256()
                observed_size = 0
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    if not isinstance(chunk, bytes) or len(chunk) > 1024 * 1024:
                        raise ValueError
                    digest.update(chunk)
                    observed_size += len(chunk)
                    if observed_size > source.size_bytes:
                        raise ValueError
        except DurableIngestionGateError:
            raise
        except Exception:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
        if observed_size != source.size_bytes or digest.hexdigest() != source.content_sha256:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        receipts.append(
            {
                "company_alias": alias,
                "source_sequence": source.sequence,
                "content_sha256": source.content_sha256,
                "size_bytes": source.size_bytes,
                "version_id_sha256": hashlib.sha256(version_id.encode("utf-8")).hexdigest(),
            }
        )
    receipts.sort(key=lambda item: str(item["company_alias"]))
    if len(receipts) != 2 or len({row["company_alias"] for row in receipts}) != 2:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return {
        "company_count": len(receipts),
        "receipts_sha256": _canonical_json_sha256(receipts),
    }


def _identifier_sha256(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _snapshot_code_set(raw: object) -> list[str]:
    if not isinstance(raw, list) or any(not isinstance(value, str) or not value for value in raw):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    normalized = [unicodedata.normalize("NFC", value) for value in raw]
    if len(normalized) != len(set(normalized)):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return sorted(normalized)


def _snapshot_row_key(raw: Mapping[str, Any]) -> dict[str, object]:
    company_alias = raw.get("company_alias")
    source_sequence = raw.get("source_sequence")
    content_sha256 = raw.get("content_sha256")
    if (
        not isinstance(company_alias, str)
        or not company_alias
        or type(source_sequence) is not int
        or source_sequence < 0
        or not _is_sha256(content_sha256)
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return {
        "company_alias": unicodedata.normalize("NFC", company_alias),
        "source_sequence": source_sequence,
        "content_sha256": str(content_sha256).lower(),
    }


def _snapshot_row_sort_key(row_key: Mapping[str, object]) -> tuple[str, int, str]:
    company_alias = row_key.get("company_alias")
    source_sequence = row_key.get("source_sequence")
    content_sha256 = row_key.get("content_sha256")
    if (
        not isinstance(company_alias, str)
        or type(source_sequence) is not int
        or not isinstance(content_sha256, str)
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return company_alias, source_sequence, content_sha256


def _snapshot_section_row_sort_key(row: Mapping[str, object]) -> tuple[str, int, str]:
    row_key = row.get("row_key")
    if not isinstance(row_key, Mapping):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return _snapshot_row_sort_key(row_key)


def _canonical_terminal_snapshot_digests(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Return section-addressable hashes of one normalized terminal snapshot."""
    documents: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    spans: list[dict[str, object]] = []
    censuses: list[dict[str, object]] = []
    seen_rows: set[tuple[str, int, str]] = set()
    company_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    census_row_count = 0

    for raw in rows:
        if not isinstance(raw, Mapping):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        row_key = _snapshot_row_key(raw)
        row_identity = _snapshot_row_sort_key(row_key)
        if row_identity in seen_rows:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        seen_rows.add(row_identity)
        object_integrity = raw.get("durable_object_integrity")
        raw_spans = raw.get("durable_spans")
        if not isinstance(object_integrity, Mapping) or not isinstance(raw_spans, list):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")

        reason_codes = _snapshot_code_set(raw.get("reason_codes"))
        warning_codes = _snapshot_code_set(raw.get("warning_codes"))
        error_codes = _snapshot_code_set(raw.get("error_codes"))
        reason_counts.update(reason_codes)
        warning_counts.update(warning_codes)
        error_counts.update(error_codes)
        company_counts[str(row_key["company_alias"])] += 1
        extension = raw.get("extension")
        if not isinstance(extension, str) or not extension:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        extension_counts[extension] += 1

        documents.append(
            {
                "row_key": row_key,
                "deal_id_sha256": _identifier_sha256(raw.get("deal_id")),
                "document_id_sha256": _identifier_sha256(raw.get("document_id")),
                "parsed_doc_type": raw.get("parsed_doc_type"),
                "terminal_status": raw.get("terminal_status"),
                "file_status": raw.get("file_status"),
                "support_status": raw.get("support_status"),
                "triage_status": raw.get("triage_status"),
                "parse_status": raw.get("parse_status"),
                "reason_codes": reason_codes,
                "warning_codes": warning_codes,
                "error_codes": error_codes,
                "ocr_performed": raw.get("ocr_performed"),
                "media_transcription_performed": raw.get("media_transcription_performed"),
                "media_frame_ocr_performed": raw.get("media_frame_ocr_performed"),
            }
        )
        artifacts.append(
            {
                "row_key": row_key,
                "doc_id_sha256": _identifier_sha256(raw.get("doc_id")),
                "size_bytes": raw.get("size_bytes"),
                "extension": extension,
                "bounded_streaming_verified": raw.get("bounded_streaming_verified"),
                "pdf_encryption_disposition": raw.get("pdf_encryption_disposition"),
                "durable_object_integrity": {
                    "schema_version": object_integrity.get("schema_version"),
                    "content_sha256": object_integrity.get("content_sha256"),
                    "size_bytes": object_integrity.get("size_bytes"),
                    "uri_sha256": object_integrity.get("uri_sha256"),
                    "version_id_sha256": _identifier_sha256(object_integrity.get("version_id")),
                    "backend": object_integrity.get("backend"),
                    "streaming_verified": object_integrity.get("streaming_verified"),
                },
            }
        )

        reduced_spans: list[dict[str, object]] = []
        seen_span_ids: set[str] = set()
        for span in raw_spans:
            if not isinstance(span, Mapping):
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            span_id_sha256 = _identifier_sha256(span.get("span_id"))
            if span_id_sha256 in seen_span_ids:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            seen_span_ids.add(span_id_sha256)
            reduced_spans.append(
                {
                    "span_id_sha256": span_id_sha256,
                    "document_id_sha256": _identifier_sha256(span.get("document_id")),
                    "span_type": span.get("span_type"),
                    "locator": span.get("locator"),
                    "content_hash": span.get("content_hash"),
                }
            )
        reduced_spans.sort(key=_canonical_snapshot_sort_key)
        spans.append({"row_key": row_key, "spans": reduced_spans})

        census: dict[str, object] = {"row_key": row_key}
        for optional_key in (
            "ocr_evidence",
            "document_no_substantive_content_receipt",
            MEDIA_RUNTIME_PROVENANCE_KEY,
            "media_stt_receipt",
            "media_frame_ocr_receipt",
        ):
            if optional_key in raw:
                census[optional_key] = raw[optional_key]
        if len(census) > 1:
            census_row_count += 1
            censuses.append(census)

    for section in (documents, artifacts, spans, censuses):
        section.sort(key=_snapshot_section_row_sort_key)
    span_count = 0
    for row in spans:
        row_spans = row.get("spans")
        if not isinstance(row_spans, list):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        span_count += len(row_spans)
    audit_counts = {
        "schema_version": "task4-terminal-snapshot-audit-counts-v1",
        "terminal_row_count": len(documents),
        "artifact_count": len(artifacts),
        "span_count": span_count,
        "census_row_count": census_row_count,
        "counts_by_company": dict(sorted(company_counts.items())),
        "counts_by_extension": dict(sorted(extension_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "warning_code_counts": dict(sorted(warning_counts.items())),
        "error_code_counts": dict(sorted(error_counts.items())),
    }
    component_payloads: dict[str, object] = {
        "documents": {
            "schema_version": "task4-terminal-snapshot-documents-v1",
            "rows": documents,
        },
        "artifacts": {
            "schema_version": "task4-terminal-snapshot-artifacts-v1",
            "rows": artifacts,
        },
        "spans": {
            "schema_version": "task4-terminal-snapshot-spans-v1",
            "rows": spans,
        },
        "censuses": {
            "schema_version": "task4-terminal-snapshot-censuses-v1",
            "rows": censuses,
        },
        "audit_counts": audit_counts,
    }
    component_sha256 = {
        section: _canonical_snapshot_sha256(component_payloads[section])
        for section in _TASK4_TERMINAL_SNAPSHOT_COMPONENTS
    }
    terminal_rows_sha256 = _canonical_snapshot_sha256(
        {
            "schema_version": TASK4_TERMINAL_SNAPSHOT_SCHEMA_VERSION,
            "component_sha256": component_sha256,
        }
    )
    return {
        "schema_version": TASK4_TERMINAL_SNAPSHOT_SCHEMA_VERSION,
        "terminal_rows_sha256": terminal_rows_sha256,
        "component_sha256": component_sha256,
    }


def _validate_terminal_snapshot_digests(
    value: object,
    *,
    expected_top: object | None = None,
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "terminal_rows_sha256",
        "component_sha256",
    }:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    components = value.get("component_sha256")
    if (
        value.get("schema_version") != TASK4_TERMINAL_SNAPSHOT_SCHEMA_VERSION
        or not _is_sha256(value.get("terminal_rows_sha256"))
        or not isinstance(components, Mapping)
        or set(components) != set(_TASK4_TERMINAL_SNAPSHOT_COMPONENTS)
        or any(not _is_sha256(components.get(section)) for section in components)
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    component_sha256 = {
        section: str(components[section]) for section in _TASK4_TERMINAL_SNAPSHOT_COMPONENTS
    }
    expected_bound_top = _canonical_snapshot_sha256(
        {
            "schema_version": TASK4_TERMINAL_SNAPSHOT_SCHEMA_VERSION,
            "component_sha256": component_sha256,
        }
    )
    if value.get("terminal_rows_sha256") != expected_bound_top or (
        expected_top is not None and expected_top != expected_bound_top
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return {
        "schema_version": TASK4_TERMINAL_SNAPSHOT_SCHEMA_VERSION,
        "terminal_rows_sha256": expected_bound_top,
        "component_sha256": component_sha256,
    }


def _write_terminal_snapshot_mismatch_receipt(
    path: Path,
    *,
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> None:
    before_snapshot = _validate_terminal_snapshot_digests(before)
    after_snapshot = _validate_terminal_snapshot_digests(after)
    before_components = before_snapshot["component_sha256"]
    after_components = after_snapshot["component_sha256"]
    if not isinstance(before_components, Mapping) or not isinstance(after_components, Mapping):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    differing_components = sorted(
        section
        for section in _TASK4_TERMINAL_SNAPSHOT_COMPONENTS
        if before_components.get(section) != after_components.get(section)
    )
    if not differing_components or before_snapshot == after_snapshot:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    receipt = {
        "schema_version": TASK4_TERMINAL_SNAPSHOT_MISMATCH_SCHEMA_VERSION,
        "status": "failed",
        "reason_code": "DATA_ROOM_TERMINAL_SNAPSHOT_DRIFT",
        "before": before_snapshot,
        "after": after_snapshot,
        "differing_components": differing_components,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        if path.exists() or temporary.exists():
            raise OSError
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None


def _require_terminal_snapshot_stable(
    *,
    before: Mapping[str, object],
    after_rows: Sequence[Mapping[str, Any]],
    mismatch_path: Path,
) -> dict[str, object]:
    after = _canonical_terminal_snapshot_digests(after_rows)
    if after != before:
        _write_terminal_snapshot_mismatch_receipt(
            mismatch_path,
            before=before,
            after=after,
        )
        raise DurableIngestionGateError("DATA_ROOM_TERMINAL_SNAPSHOT_DRIFT")
    return after


def _canonical_reduced_terminal_rows_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    """Compatibility wrapper returning the deterministic snapshot top digest."""
    return str(_canonical_terminal_snapshot_digests(rows)["terminal_rows_sha256"])


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_SIZE_BYTES):
                digest.update(chunk)
    except OSError:
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
    return digest.hexdigest()


def _task4_code_sha256() -> str:
    repository_root = Path(__file__).resolve().parents[3]
    runtime_root = repository_root / TASK4_CODE_RUNTIME_ROOT
    try:
        resolved_runtime_root = runtime_root.resolve(strict=True)
        runtime_paths = sorted(
            (
                target.relative_to(repository_root)
                for target in runtime_root.rglob("*.py")
                if target.is_file()
            ),
            key=lambda path: path.as_posix(),
        )
    except (OSError, ValueError):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
    if not runtime_paths or resolved_runtime_root != runtime_root.absolute():
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    relative_paths = (*TASK4_CODE_STATIC_RELATIVE_PATHS, *runtime_paths)
    if len(set(relative_paths)) != len(relative_paths):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    bindings: list[dict[str, str]] = []
    for relative_path in relative_paths:
        target = repository_root / relative_path
        try:
            resolved_target = target.resolve(strict=True)
        except OSError:
            raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
        if (
            not target.is_file()
            or target.is_symlink()
            or resolved_target != target.absolute()
            or relative_path.suffix == ".py"
            and not resolved_target.is_relative_to(resolved_runtime_root)
        ):
            raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
        bindings.append(
            {
                "component": relative_path.as_posix(),
                "sha256": _file_sha256(target),
            }
        )
    return _canonical_json_sha256(bindings)


def _task4_plan_sha256() -> str:
    repository_root = Path(__file__).resolve().parents[3]
    return _file_sha256(repository_root / TASK4_PLAN_RELATIVE_PATH)


def _verify_restart_equivalence(
    *,
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    upload_post_count: int,
) -> None:
    for proof in (baseline, current):
        if proof.get("schema_version") == TASK4_DURABLE_PROOF_SCHEMA_VERSION:
            _validate_terminal_snapshot_digests(
                proof.get("terminal_snapshot_digests"),
                expected_top=proof.get("terminal_rows_sha256"),
            )
    baseline_boot = baseline.get("api_boot_sha256")
    current_boot = current.get("api_boot_sha256")
    frozen_keys: tuple[str, ...] = _RESTART_FROZEN_DIGEST_KEYS
    if baseline.get("schema_version") in {
        _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V5,
        TASK4_DURABLE_PROOF_SCHEMA_VERSION,
    } or current.get("schema_version") in {
        _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V5,
        TASK4_DURABLE_PROOF_SCHEMA_VERSION,
    }:
        frozen_keys = (
            *_RESTART_FROZEN_DIGEST_KEYS,
            "pdf_page_disposition_censuses_sha256",
        )
    if (
        baseline.get("phase") != "prepared"
        or baseline.get("restart_survival_verified") is not False
        or baseline.get("previous_api_boot_sha256") is not None
        or current.get("phase") != "prepared"
        or current.get("restart_survival_verified") is not False
        or current.get("previous_api_boot_sha256") is not None
        or not _is_sha256(baseline_boot)
        or not _is_sha256(current_boot)
        or baseline_boot == current_boot
        or upload_post_count != 0
        or any(baseline.get(key) != current.get(key) for key in frozen_keys)
        or (
            baseline.get("schema_version") == TASK4_DURABLE_PROOF_SCHEMA_VERSION
            or current.get("schema_version") == TASK4_DURABLE_PROOF_SCHEMA_VERSION
        )
        and baseline.get("terminal_snapshot_digests") != current.get("terminal_snapshot_digests")
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")


def _verify_tenant_isolation(
    *,
    api_client: Any,
    canary_api_key: str,
    preparation_summary: Mapping[str, Any],
    terminal_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require non-enumerating cross-tenant denial on ingestion read/write surfaces."""
    raw_companies = preparation_summary.get("companies")
    if not isinstance(raw_companies, list) or not canary_api_key:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    first_rows: dict[str, Mapping[str, Any]] = {}
    for row in terminal_rows:
        alias = row.get("company_alias") if isinstance(row, Mapping) else None
        if isinstance(alias, str) and alias not in first_rows:
            first_rows[alias] = row
    headers = {"X-IDIS-API-Key": canary_api_key}
    denial_count = 0
    aliases: set[str] = set()
    for company in raw_companies:
        if not isinstance(company, Mapping):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        alias = company.get("company_alias")
        deal_id = company.get("deal_id")
        package_id = company.get("package_id")
        candidate_row = first_rows.get(str(alias))
        if (
            not isinstance(alias, str)
            or alias in aliases
            or not isinstance(deal_id, str)
            or not isinstance(package_id, str)
            or candidate_row is None
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        aliases.add(alias)
        document_id = candidate_row.get("document_id")
        artifact_id = candidate_row.get("doc_id")
        if not isinstance(document_id, str) or not isinstance(artifact_id, str):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        paths = (
            f"/v1/deals/{deal_id}/data-room-packages/{package_id}",
            f"/v1/deals/{deal_id}/documents/{document_id}",
            f"/v1/documents/{artifact_id}/content-integrity",
            f"/v1/documents/{artifact_id}/spans",
            f"/v1/documents/{artifact_id}",
            f"/v1/documents/{artifact_id}/content",
        )
        for path in paths:
            try:
                if path.endswith("/content"):
                    with api_client.stream("GET", path, headers=headers) as response:
                        status_code = getattr(response, "status_code", None)
                else:
                    response = api_client.get(path, headers=headers)
                    status_code = getattr(response, "status_code", None)
            except DurableIngestionGateError:
                raise
            except Exception:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
            if status_code != 404:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            denial_count += 1

        canary_payload = b"isolation canary"
        write_probes = (
            (
                f"/v1/deals/{deal_id}/data-room-packages",
                {"headers": headers, "json": {"document_ids": [document_id]}},
            ),
            (
                f"/v1/deals/{deal_id}/documents/upload",
                {
                    "headers": {**headers, "Content-Type": "application/octet-stream"},
                    "params": {
                        "filename": f"{alias}-isolation-canary.txt",
                        "doc_type": "DATA_ROOM_FILE",
                        "source_system": "slice100-isolation-canary",
                        "sha256": hashlib.sha256(canary_payload).hexdigest(),
                    },
                    "content": canary_payload,
                },
            ),
        )
        for path, kwargs in write_probes:
            try:
                response = api_client.post(path, **kwargs)
            except DurableIngestionGateError:
                raise
            except Exception:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
            if getattr(response, "status_code", None) != 404:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            denial_count += 1
        try:
            response = api_client.delete(
                f"/v1/documents/{artifact_id}",
                headers=headers,
            )
        except DurableIngestionGateError:
            raise
        except Exception:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
        if getattr(response, "status_code", None) != 404:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        denial_count += 1
    if len(aliases) != 2 or denial_count != len(aliases) * 9:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return {
        "status": "passed",
        "company_count": len(aliases),
        "denial_count": denial_count,
        "surface_count": 9,
    }


def _walk_mapping_keys(value: Any) -> Sequence[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                keys.append(key)
            keys.extend(_walk_mapping_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_mapping_keys(item))
    return keys


def _safe_no_substantive_content_attestation_summary(
    attestation: (
        VerifiedDocumentNoSubstantiveContentAttestation
        | VerifiedDocumentNoSubstantiveContentAttestationManifest
        | None
    ),
) -> dict[str, object]:
    if attestation is None:
        return {"status": "not_configured", "entry_count": 0}
    return attestation.safe_summary()


def _derive_pdf_ocr_execution_census(
    *,
    contract: TwoCompanySourceContract,
    admission: PdfOcrRequirementsCensus,
    attestation: (
        VerifiedDocumentNoSubstantiveContentAttestation
        | VerifiedDocumentNoSubstantiveContentAttestationManifest
        | None
    ),
) -> _PdfOcrExecutionCensus:
    """Derive classifier-first exclusions from the verified signed candidate set."""

    admission_hashes = frozenset(admission.required_content_sha256s)
    raw_required_page_numbers_by_hash = getattr(
        admission,
        "required_pages_by_content_sha256",
        None,
    )
    raw_page_count_by_hash = getattr(admission, "page_count_by_content_sha256", None)
    raw_render_admissions_by_hash = getattr(
        admission,
        "render_admissions_by_content_sha256",
        None,
    )
    strict_source_page_binding = isinstance(contract, TwoCompanySourceContract)
    source_pdf_hashes = (
        frozenset(
            source.content_sha256
            for company in contract.companies
            for source in company.files
            if source.extension == "pdf"
        )
        if strict_source_page_binding
        else frozenset(raw_page_count_by_hash or {})
    )
    if (
        len(admission_hashes) != DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT
        or admission.required_page_count != DEFAULT_PDF_OCR_REQUIRED_PAGE_COUNT
        or not isinstance(raw_required_page_numbers_by_hash, Mapping)
        or set(raw_required_page_numbers_by_hash) != set(admission_hashes)
        or any(
            not _is_sha256(content_sha256)
            or not isinstance(required_page_numbers, tuple)
            or not required_page_numbers
            or any(
                type(page_number) is not int or page_number < 1
                for page_number in required_page_numbers
            )
            or tuple(sorted(set(required_page_numbers))) != required_page_numbers
            for content_sha256, required_page_numbers in raw_required_page_numbers_by_hash.items()
        )
        or sum(len(value) for value in raw_required_page_numbers_by_hash.values())
        != admission.required_page_count
        or not isinstance(raw_page_count_by_hash, Mapping)
        or strict_source_page_binding
        and len(source_pdf_hashes) != DEFAULT_PDF_FILE_COUNT
        or set(raw_page_count_by_hash) != set(source_pdf_hashes)
        or any(
            not _is_sha256(content_sha256)
            or type(page_count) is not int
            or page_count < 1
            or (
                content_sha256 in admission_hashes
                and page_count < max(raw_required_page_numbers_by_hash[content_sha256])
            )
            for content_sha256, page_count in raw_page_count_by_hash.items()
        )
        or not isinstance(raw_render_admissions_by_hash, Mapping)
        or set(raw_render_admissions_by_hash) != set(admission_hashes)
        or any(
            not isinstance(admissions, tuple)
            or len(admissions) != len(raw_required_page_numbers_by_hash[content_sha256])
            or any(
                not isinstance(render_admission, Mapping)
                or render_admission.get("page") != required_page_number
                for render_admission, required_page_number in zip(
                    admissions,
                    raw_required_page_numbers_by_hash[content_sha256],
                    strict=True,
                )
            )
            for content_sha256, admissions in raw_render_admissions_by_hash.items()
        )
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    required_page_numbers_by_hash = {
        str(content_sha256): frozenset(required_page_numbers)
        for content_sha256, required_page_numbers in raw_required_page_numbers_by_hash.items()
    }
    page_count_by_hash = {
        str(content_sha256): int(page_count)
        for content_sha256, page_count in raw_page_count_by_hash.items()
    }

    candidates: Sequence[Mapping[str, object]] = ()
    if isinstance(attestation, VerifiedDocumentNoSubstantiveContentAttestationManifest):
        candidates = attestation.batch.candidates

    source_by_alias: dict[str, Any] = {}
    if candidates:
        source_by_alias = {
            source.generated_upload_name: source
            for company in contract.companies
            for source in company.files
        }
        if len(source_by_alias) != contract.total_file_count:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")

    candidate_identities: set[tuple[str, str, int]] = set()
    classified_page_numbers_by_hash: dict[str, set[int]] = {}
    for candidate in candidates:
        safe_alias = candidate.get("safe_alias")
        content_sha256 = candidate.get("content_sha256")
        page_number = candidate.get("page_number")
        if (
            not isinstance(safe_alias, str)
            or not safe_alias.endswith(".pdf")
            or not _is_sha256(content_sha256)
            or type(page_number) is not int
            or page_number < 1
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        source = source_by_alias.get(safe_alias)
        identity = (safe_alias, str(content_sha256), page_number)
        if (
            source is None
            or source.extension != "pdf"
            or source.content_sha256 != content_sha256
            or content_sha256 not in admission_hashes
            or page_number not in required_page_numbers_by_hash[str(content_sha256)]
            or identity in candidate_identities
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        candidate_identities.add(identity)
        classified_page_numbers_by_hash.setdefault(str(content_sha256), set()).add(page_number)

    classifier_only_hashes = {
        content_sha256
        for content_sha256, classified_page_numbers in classified_page_numbers_by_hash.items()
        if classified_page_numbers == required_page_numbers_by_hash[content_sha256]
    }
    classifier_first_page_count = len(candidate_identities)
    classifier_first_file_count = len(classified_page_numbers_by_hash)
    classifier_only_file_count = len(classifier_only_hashes)
    classifier_only_page_count = sum(
        len(required_page_numbers_by_hash[content_sha256])
        for content_sha256 in classifier_only_hashes
    )
    execution_hashes = admission_hashes - classifier_only_hashes
    classifier_terminal_hashes = {
        content_sha256
        for content_sha256, classified_page_numbers in classified_page_numbers_by_hash.items()
        if classified_page_numbers == set(range(1, page_count_by_hash[content_sha256] + 1))
    }
    execution_page_count = sum(
        len(required_page_numbers - classified_page_numbers_by_hash.get(content_sha256, set()))
        for content_sha256, required_page_numbers in required_page_numbers_by_hash.items()
    )
    if execution_page_count < 0:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return _PdfOcrExecutionCensus(
        admission_content_sha256s=admission_hashes,
        execution_content_sha256s=frozenset(execution_hashes),
        classifier_only_content_sha256s=frozenset(classifier_only_hashes),
        required_pages_by_content_sha256={
            content_sha256: tuple(required_page_numbers)
            for content_sha256, required_page_numbers in raw_required_page_numbers_by_hash.items()
        },
        page_count_by_content_sha256=dict(page_count_by_hash),
        render_admissions_by_content_sha256={
            content_sha256: tuple(dict(admission) for admission in admissions)
            for content_sha256, admissions in raw_render_admissions_by_hash.items()
        },
        admission_file_count=len(admission_hashes),
        admission_page_count=admission.required_page_count,
        execution_file_count=len(execution_hashes),
        execution_page_count=execution_page_count,
        classifier_first_file_count=classifier_first_file_count,
        classifier_only_file_count=classifier_only_file_count,
        classifier_only_page_count=classifier_only_page_count,
        classifier_first_page_count=classifier_first_page_count,
        classifier_terminal_file_count=len(classifier_terminal_hashes),
    )


def _observed_pdf_ocr_execution_page_count(
    terminal_rows: Sequence[Mapping[str, Any]],
) -> int:
    total = 0
    for row in terminal_rows:
        if row.get("extension") != "pdf" or row.get("ocr_performed") is not True:
            continue
        evidence = row.get("ocr_evidence")
        if (
            isinstance(evidence, Mapping)
            and evidence.get("schema_version") == OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION
        ):
            census = evidence.get("pdf_page_census")
            if (
                isinstance(census, Mapping)
                and census.get("schema_version") == PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION
            ):
                class_counts = census.get("class_counts")
                ocr_text_count = (
                    class_counts.get("ocr_text") if isinstance(class_counts, Mapping) else None
                )
                image_only_count = (
                    class_counts.get("image_only_page")
                    if isinstance(class_counts, Mapping)
                    else None
                )
                page_count = (
                    ocr_text_count + image_only_count
                    if type(ocr_text_count) is int and type(image_only_count) is int
                    else None
                )
            else:
                page_count = None
        else:
            page_count = evidence.get("ocr_page_count") if isinstance(evidence, Mapping) else None
        if type(page_count) is not int or page_count < 1:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        total += page_count
    return total


def _validate_safe_no_substantive_content_attestation_summary(value: object) -> None:
    if not isinstance(value, Mapping):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if dict(value) == {"status": "not_configured", "entry_count": 0}:
        return
    v1_fields = {
        "schema_version",
        "status",
        "entry_count",
        "disposition",
        "variant",
        "payload_sha256",
        "envelope_sha256",
        "public_key_sha256",
        "signature_verified",
    }
    if value.get("schema_version") == "document-no-substantive-content-attestation-v1":
        if set(value) != v1_fields or any(
            not _is_sha256(value.get(key))
            for key in ("payload_sha256", "envelope_sha256", "public_key_sha256")
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        if (
            value.get("status") != "verified"
            or value.get("entry_count") != 1
            or value.get("disposition") != "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
            or value.get("variant") not in {"blank", "illegible", "watermark_only"}
            or value.get("signature_verified") is not True
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        return

    if not NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT.is_valid(value):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")


def _validate_safe_durable_ingestion_receipt(receipt: Mapping[str, Any]) -> None:
    keys = set(receipt)
    schema_version = receipt.get("schema_version")
    is_v6 = schema_version == TASK4_DURABLE_PROOF_SCHEMA_VERSION
    is_v5 = schema_version == _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V5
    is_v4 = schema_version == _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V4
    is_governed = is_v4 or is_v5 or is_v6
    has_page_census = is_v5 or is_v6
    versioned_keys = (
        _SAFE_PROOF_V6_KEYS
        if is_v6
        else _SAFE_PROOF_V5_KEYS
        if is_v5
        else _SAFE_PROOF_V4_KEYS
        if is_v4
        else frozenset()
    )
    required_keys = _SAFE_PROOF_REQUIRED_KEYS | versioned_keys
    if (
        schema_version
        not in {
            _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V3,
            _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V4,
            _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V5,
            TASK4_DURABLE_PROOF_SCHEMA_VERSION,
        }
        or not keys >= required_keys
        or not keys <= required_keys | _SAFE_PROOF_OPTIONAL_KEYS
        or receipt.get("status") != "ready"
        or receipt.get("phase") not in {"prepared", "restart_verified"}
        or receipt.get("restart_survival_verified")
        is not (receipt.get("phase") == "restart_verified")
        or any(key in _FORBIDDEN_SAFE_PROOF_KEYS for key in _walk_mapping_keys(receipt))
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    company_counts = receipt.get("counts_by_company")
    company_bytes = receipt.get("bytes_by_company")
    format_counts = receipt.get("counts_by_extension")
    parser_counts = receipt.get("parser_counts")
    variant_counts = receipt.get("confirmed_no_substantive_content_variant_counts")
    isolation = receipt.get("tenant_isolation")
    attestation = receipt.get("no_substantive_content_attestation")
    _validate_safe_no_substantive_content_attestation_summary(attestation)
    if not isinstance(attestation, Mapping):  # keeps static and runtime narrowing aligned
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if (
        receipt.get("total_file_count") != DEFAULT_EXPECTED_TOTAL_FILES
        or receipt.get("total_bytes") != DEFAULT_EXPECTED_TOTAL_BYTES
        or company_bytes
        != {
            "company-001": DEFAULT_EXPECTED_COMPANY_BYTES[0],
            "company-002": DEFAULT_EXPECTED_COMPANY_BYTES[1],
        }
        or receipt.get("authenticated_download_company_count") != 2
        or type(receipt.get("upload_post_count")) is not int
        or int(receipt["upload_post_count"]) < 0
        or company_counts is not None
        and company_counts != {"company-001": 58, "company-002": 209}
        or format_counts is not None
        and format_counts != dict(sorted(TASK4_EXPECTED_FORMAT_COUNTS.items()))
        or isolation
        != {
            "status": "passed",
            "company_count": 2,
            "denial_count": 18,
            "surface_count": 9,
            "post_mutation_state_verified": True,
        }
        or receipt.get("tenant_isolation_receipt_sha256") != _canonical_json_sha256(isolation)
        or receipt.get("no_substantive_content_attestation_sha256")
        != _canonical_json_sha256(attestation)
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if not isinstance(parser_counts, Mapping):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    expected_parser_keys = _SAFE_PARSER_COUNT_KEYS_V4 if is_governed else _SAFE_PARSER_COUNT_KEYS_V3
    if set(parser_counts) != set(expected_parser_keys):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    validated_parser_counts = _safe_parser_counts(
        parser_counts,
        schema_version=str(schema_version),
    )
    if (
        not isinstance(variant_counts, Mapping)
        or set(variant_counts) != {"blank", "illegible", "watermark_only"}
        or any(type(value) is not int or value < 0 for value in variant_counts.values())
        or sum(int(value) for value in variant_counts.values())
        != validated_parser_counts["confirmed_no_substantive_content_file_count"]
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    confirmed_count = validated_parser_counts["confirmed_no_substantive_content_file_count"]
    if dict(attestation) == {"status": "not_configured", "entry_count": 0}:
        variant_census_matches_attestation = confirmed_count == 0 and all(
            int(value) == 0 for value in variant_counts.values()
        )
    elif (
        attestation.get("schema_version")
        == "document-no-substantive-content-attestation-manifest-v2"
    ):
        terminal_file_count = validated_parser_counts["pdf_classifier_terminal_file_count"]
        variant_census_matches_attestation = (
            confirmed_count
            == validated_parser_counts["manual_v1_confirmed_document_count"] + terminal_file_count
            and int(variant_counts["watermark_only"]) == terminal_file_count
            and int(variant_counts["blank"]) == 0
            and int(variant_counts["illegible"]) == 0
        )
    else:
        attested_variant = attestation.get("variant")
        variant_census_matches_attestation = (
            confirmed_count == 1
            and isinstance(attested_variant, str)
            and all(
                int(count) == (1 if variant == attested_variant else 0)
                for variant, count in variant_counts.items()
            )
        )
    if not variant_census_matches_attestation:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if is_governed:
        pages_by_company = receipt.get("watermark_classified_pages_by_company")
        classification_receipts_sha256 = receipt.get("watermark_classification_receipts_sha256")
        manifest_bound_receipt_count = receipt.get("watermark_manifest_bound_receipt_count")
        manifest_runtime_summary_sha256 = receipt.get("watermark_manifest_runtime_summary_sha256")
        classified_page_count = validated_parser_counts["watermark_classified_page_count"]
        batch_attested_page_count = validated_parser_counts["watermark_batch_attested_page_count"]
        runtime_unseen_page_count = validated_parser_counts["watermark_runtime_unseen_page_count"]
        if (
            not isinstance(pages_by_company, Mapping)
            or set(pages_by_company) != {"company-001", "company-002"}
            or any(type(value) is not int or value < 0 for value in pages_by_company.values())
            or sum(int(value) for value in pages_by_company.values()) != classified_page_count
            or 100 * int(pages_by_company["company-001"]) > 15 * 58
            or 100 * int(pages_by_company["company-002"]) > 15 * 209
            or not _is_sha256(classification_receipts_sha256)
            or type(manifest_bound_receipt_count) is not int
            or manifest_bound_receipt_count
            != validated_parser_counts["watermark_classifier_document_count"]
            or not _is_sha256(manifest_runtime_summary_sha256)
            or (
                attestation.get("schema_version")
                != "document-no-substantive-content-attestation-manifest-v2"
                and manifest_runtime_summary_sha256 != _canonical_json_sha256({})
            )
            or receipt.get("watermark_runtime_review_required")
            is not (runtime_unseen_page_count > 0)
            or batch_attested_page_count + runtime_unseen_page_count != classified_page_count
            or runtime_unseen_page_count != 0
            or receipt.get("watermark_runtime_review_required") is not False
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        every_classifier_document_is_terminal = (
            validated_parser_counts["pdf_classifier_first_file_count"]
            == validated_parser_counts["pdf_classifier_terminal_file_count"]
        )
        if (
            validated_parser_counts["manual_v1_confirmed_document_count"] == 0
            and every_classifier_document_is_terminal
            and classification_receipts_sha256
            != receipt.get("confirmed_no_substantive_content_receipts_sha256")
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        if (
            isinstance(attestation, Mapping)
            and attestation.get("schema_version")
            == "document-no-substantive-content-attestation-manifest-v2"
            and batch_attested_page_count != attestation.get("watermark_batch_candidate_count")
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    frozen_digest_keys: tuple[str, ...] = (*_RESTART_FROZEN_DIGEST_KEYS, "api_boot_sha256")
    if is_governed:
        frozen_digest_keys = (
            *_RESTART_FROZEN_DIGEST_KEYS,
            "watermark_classification_receipts_sha256",
            "watermark_manifest_runtime_summary_sha256",
            "api_boot_sha256",
        )
    if has_page_census:
        census_counts = receipt.get("pdf_page_disposition_class_counts")
        if (
            receipt.get("pdf_page_disposition_census_schema_version")
            != PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION
            or not isinstance(census_counts, Mapping)
            or set(census_counts)
            != {"native_text", "ocr_text", "image_only_page", "watermark_only"}
            or any(type(value) is not int or value < 0 for value in census_counts.values())
            or sum(int(value) for value in census_counts.values()) != DEFAULT_PDF_PAGE_COUNT
            or not _is_sha256(receipt.get("pdf_page_disposition_censuses_sha256"))
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        frozen_digest_keys = (
            *frozen_digest_keys[:-1],
            "pdf_page_disposition_censuses_sha256",
            "api_boot_sha256",
        )
    if is_v6:
        _validate_terminal_snapshot_digests(
            receipt.get("terminal_snapshot_digests"),
            expected_top=receipt.get("terminal_rows_sha256"),
        )
    if any(
        not isinstance(receipt.get(key), str)
        or len(str(receipt[key])) != 64
        or any(character not in "0123456789abcdef" for character in str(receipt[key]))
        for key in frozen_digest_keys
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    previous_boot = receipt.get("previous_api_boot_sha256")
    restart_verified = receipt.get("phase") == "restart_verified"
    if (
        (not restart_verified and previous_boot is not None)
        or (restart_verified and not _is_sha256(previous_boot))
        or (restart_verified and previous_boot == receipt.get("api_boot_sha256"))
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")


def _write_safe_durable_ingestion_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    _validate_safe_durable_ingestion_receipt(receipt)
    payload = json.dumps(
        dict(receipt),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
    finally:
        temporary.unlink(missing_ok=True)


def _read_safe_durable_ingestion_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE") from None
    if not isinstance(payload, dict):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    _validate_safe_durable_ingestion_receipt(payload)
    return payload


class GateMode(StrEnum):
    """Supported private gate execution modes."""

    INVENTORY_ONLY = "inventory_only"
    PARSE_SUPPORTED = "parse_supported"


@dataclass(frozen=True, slots=True)
class _InventoryFile:
    path: Path
    extension: str
    size_bytes: int
    sha256: str
    read_error_reason: str | None = None


ProgressFn = Callable[[dict[str, object]], None]


def run_real_example_gate(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    mode: GateMode = GateMode.INVENTORY_ONLY,
    safe_summary: bool = True,
    per_file_timeout_seconds: float = DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    max_runtime_seconds: float | None = None,
    max_memory_mb: int | None = None,
    emit_progress: bool = False,
    parse_attempt_fn: ParseAttemptFn | None = None,
    progress_fn: ProgressFn | None = None,
    ocr_enabled: bool = False,
    ocr_max_pages: int = 10,
    ocr_timeout_seconds: float = 30.0,
    ocr_dpi: int = 200,
    media_enabled: bool = False,
    media_timeout_seconds: float = 30.0,
    media_adapter: str = MEDIA_ADAPTER_NONE,
    media_model_name: str | None = None,
    media_model_path: str | None = None,
    media_allow_model_download: bool = False,
    media_language: str = DEFAULT_MEDIA_LANGUAGE,
    media_compute_type: str = DEFAULT_MEDIA_COMPUTE_TYPE,
    media_max_duration_seconds: float = MAX_MEDIA_DURATION_SECONDS,
) -> dict[str, object]:
    """Run the local private real_example gate and return a safe aggregate summary.

    Args:
        root: Local data room root. The returned summary never includes it.
        ledger_path: Local resumable ledger path. The returned summary never includes it.
        mode: Inventory-only or supported-file parsing mode.
        safe_summary: Must remain true for parse-supported runs.
        per_file_timeout_seconds: Maximum seconds for one parse attempt.
        max_runtime_seconds: Optional total runtime budget.
        max_memory_mb: Optional process memory budget checked before each file.
        emit_progress: Whether to emit safe per-file progress to stderr. Defaults off.
        parse_attempt_fn: Optional test seam for parse attempts.
        progress_fn: Optional test seam for progress emission.
        ocr_enabled: Whether to run the opt-in OCR adapter for OCR-required PDFs.
        ocr_max_pages: Maximum leading PDF pages the OCR adapter may process.
        ocr_timeout_seconds: Maximum OCR runtime inside the parser subprocess.
        ocr_dpi: PDF rasterization DPI for OCR.
        media_enabled: Whether to run the opt-in private media adapter boundary.
        media_timeout_seconds: Maximum media transcription runtime inside the parser subprocess.
        media_adapter: Private media adapter name. Defaults to disabled.
        media_model_name: faster-whisper model name, used only when downloads are explicit.
        media_model_path: Local faster-whisper model path.
        media_allow_model_download: Whether faster-whisper may download a named model.
        media_language: Transcription language hint.
        media_compute_type: faster-whisper compute type.
        media_max_duration_seconds: Maximum media duration eligible for transcription.

    Returns:
        Aggregate counts by extension, status, parser outcome, and reason code only.

    Raises:
        ValueError: If arguments would allow unsafe output.
        FileNotFoundError: If the root does not exist.
        NotADirectoryError: If the root is not a directory.
    """
    resolved_mode = GateMode(mode)
    if resolved_mode == GateMode.PARSE_SUPPORTED and not safe_summary:
        raise ValueError("--parse-supported requires --safe-summary")
    _validate_ocr_options(
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
    )
    _validate_media_options(
        media_enabled=media_enabled,
        media_timeout_seconds=media_timeout_seconds,
        media_adapter=media_adapter,
        media_max_duration_seconds=media_max_duration_seconds,
    )

    root_path = Path(root)
    _validate_root(root_path)
    files = _inventory_files(root_path)
    ledger_file = Path(ledger_path)
    ledger = load_ledger(ledger_file)
    resume_entries = dict(ledger["entries"])
    current_ocr_policy_key = ocr_policy_key(
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
    )
    current_media_policy_key = media_policy_key(
        media_enabled=media_enabled,
        media_adapter_available=_media_adapter_attemptable(
            media_adapter=media_adapter,
            media_model_name=media_model_name,
            media_model_path=media_model_path,
            media_allow_model_download=media_allow_model_download,
        ),
        media_adapter_name=media_adapter,
        media_timeout_seconds=media_timeout_seconds,
        media_model_key=_media_model_policy_key(
            media_model_name=media_model_name,
            media_model_path=media_model_path,
        ),
        media_allow_model_download=media_allow_model_download,
        media_language=media_language,
        media_compute_type=media_compute_type,
        media_max_duration_seconds=media_max_duration_seconds,
    )
    progress = progress_fn or _emit_progress
    started_at = time.monotonic()
    records: list[dict[str, object]] = []

    for index, file in enumerate(files, start=1):
        attempt = _attempt_for_file(
            file=file,
            mode=resolved_mode,
            resume_entries=resume_entries,
            started_at=started_at,
            max_runtime_seconds=max_runtime_seconds,
            max_memory_mb=max_memory_mb,
            per_file_timeout_seconds=per_file_timeout_seconds,
            parse_attempt_fn=parse_attempt_fn,
            ocr_enabled=ocr_enabled,
            ocr_max_pages=ocr_max_pages,
            ocr_timeout_seconds=ocr_timeout_seconds,
            ocr_dpi=ocr_dpi,
            ocr_policy_key=current_ocr_policy_key,
            media_enabled=media_enabled,
            media_timeout_seconds=media_timeout_seconds,
            media_adapter=media_adapter,
            media_model_name=media_model_name,
            media_model_path=media_model_path,
            media_allow_model_download=media_allow_model_download,
            media_language=media_language,
            media_compute_type=media_compute_type,
            media_max_duration_seconds=media_max_duration_seconds,
            media_policy_key=current_media_policy_key,
        )
        records.append(
            {
                "extension": file.extension,
                "status": attempt.status,
                "parser_outcome": attempt.parser_outcome,
                "reason_code": attempt.reason_code,
            }
        )
        _record_ledger_entry(
            ledger=ledger,
            file=file,
            attempt=attempt,
            mode=resolved_mode,
            ocr_policy_key=current_ocr_policy_key,
            media_policy_key=current_media_policy_key,
        )
        save_ledger(ledger_file, ledger)
        if emit_progress:
            progress(
                {
                    "index": index,
                    "total": len(files),
                    "extension": file.extension,
                    "status": attempt.status,
                    "reason_code": attempt.reason_code,
                }
            )

    save_ledger(ledger_file, ledger)
    summary = _safe_summary(mode=resolved_mode, records=records, ledger=ledger)
    _append_gate_reconciliation_entry(
        summary=summary,
        mode=resolved_mode,
        records=records,
        ledger_file=ledger_file,
    )
    return summary


def _append_gate_reconciliation_entry(
    *,
    summary: dict[str, object],
    mode: GateMode,
    records: list[dict[str, object]],
    ledger_file: Path,
) -> None:
    """Record the completed gate run in the append-only reconciliation log (Slice99 Task 4).

    Carries only the safe summary's sha256, aggregate counts, and status codes - never the
    data-room root, ledger path, or any private filename. The log lives next to the ledger
    (``.local_reports/reconciliation_log.jsonl`` under the default layout). Fail-closed: a
    reconciliation failure fails the gate run.
    """
    from idis.evaluation.local_reports_log import (
        RECONCILIATION_LOG_FILENAME,
        append_reconciliation_entry,
    )

    summary_sha256 = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    counts: dict[str, int] = {"files_total": len(records)}
    for status, count in sorted(Counter(str(r["status"]) for r in records).items()):
        counts[f"status_{status.lower()}"] = count

    append_reconciliation_entry(
        artifact_type="real_example_gate_summary",
        artifact_id=f"real_example_gate:{mode.value}",
        sha256=summary_sha256,
        counts=counts,
        status_code="GATE_COMPLETED",
        log_path=ledger_file.parent / RECONCILIATION_LOG_FILENAME,
    )


def build_data_room_package_inventory_summary(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> dict[str, object]:
    """Safe data-room package inventory over a private tree (Slice77 thin hook).

    Runs the private real_example gate in INVENTORY_ONLY mode -- no parsing, OCR,
    media, provider/network calls, FULL run, or readiness change -- and projects the
    gate's safe aggregate onto the durable data-room package summary shape. Emits safe
    aggregates only (``file_count`` + ``counts_by_*`` + ``ledger_entry_count``); never
    raw paths, filenames, content, object keys, or manifest/storage URIs.
    """
    gate_summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.INVENTORY_ONLY,
        safe_summary=True,
        emit_progress=False,
    )
    return {
        "safe_summary": gate_summary["safe_summary"],
        "source": "real_example_private_inventory",
        "mode": gate_summary["mode"],
        "file_count": gate_summary["total_files"],
        "ledger_entry_count": gate_summary["ledger_entry_count"],
        "counts_by_extension": gate_summary["counts_by_extension"],
        "counts_by_status": gate_summary["counts_by_status"],
        "counts_by_reason_code": gate_summary["counts_by_reason_code"],
    }


def build_real_example_parse_readiness_summary(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    per_file_timeout_seconds: float = DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    max_runtime_seconds: float | None = None,
    max_memory_mb: int | None = None,
    parse_attempt_fn: ParseAttemptFn | None = None,
    ocr_enabled: bool = False,
    ocr_max_pages: int = MAX_OCR_PAGES,
    ocr_timeout_seconds: float = 30.0,
    ocr_dpi: int = 200,
    media_enabled: bool = False,
    media_timeout_seconds: float = 30.0,
    media_adapter: str = MEDIA_ADAPTER_NONE,
    media_model_name: str | None = None,
    media_model_path: str | None = None,
    media_allow_model_download: bool = False,
    media_language: str = DEFAULT_MEDIA_LANGUAGE,
    media_compute_type: str = DEFAULT_MEDIA_COMPUTE_TYPE,
    media_max_duration_seconds: float = MAX_MEDIA_DURATION_SECONDS,
) -> dict[str, object]:
    """Safe PARSE_SUPPORTED parse-readiness projection over a private tree (Slice81).

    Wraps ``run_real_example_gate`` in PARSE_SUPPORTED mode (never INVENTORY_ONLY) and
    re-projects its safe aggregate, adding ``counts_by_evidence_class`` (extension-derived),
    ``counts_by_deferral_class`` (intended vs unintended), the unintended reason-code
    breakdown, and a ``parse_ready`` verdict (True only when zero unintended deferrals).
    Does NOT mutate or extend ``_safe_summary``. Emits safe aggregates only -- never raw
    paths, filenames, content, object keys, model paths, env values, command output, or
    secrets; media bytes are never read (the gate's media-no-read behavior is preserved).
    """
    gate_summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        per_file_timeout_seconds=per_file_timeout_seconds,
        max_runtime_seconds=max_runtime_seconds,
        max_memory_mb=max_memory_mb,
        emit_progress=False,
        parse_attempt_fn=parse_attempt_fn,
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
        media_enabled=media_enabled,
        media_timeout_seconds=media_timeout_seconds,
        media_adapter=media_adapter,
        media_model_name=media_model_name,
        media_model_path=media_model_path,
        media_allow_model_download=media_allow_model_download,
        media_language=media_language,
        media_compute_type=media_compute_type,
        media_max_duration_seconds=media_max_duration_seconds,
    )
    counts_by_extension = gate_summary["counts_by_extension"]
    counts_by_reason_code = gate_summary["counts_by_reason_code"]
    assert isinstance(counts_by_extension, dict)
    assert isinstance(counts_by_reason_code, dict)
    counts_by_evidence_class = _aggregate_counts_by(
        counts_by_extension, _evidence_class_for_extension
    )
    counts_by_deferral_class = _aggregate_counts_by(
        counts_by_reason_code, _deferral_class_for_reason_code
    )
    unintended_deferral_reason_codes = {
        reason_code: count
        for reason_code, count in sorted(counts_by_reason_code.items())
        if _deferral_class_for_reason_code(reason_code) == _DEFERRAL_CLASS_UNINTENDED
    }
    parse_ready = counts_by_deferral_class.get(_DEFERRAL_CLASS_UNINTENDED, 0) == 0
    return {
        "safe_summary": gate_summary["safe_summary"],
        "source": "real_example_private_parse_readiness",
        "mode": gate_summary["mode"],
        "total_files": gate_summary["total_files"],
        "processed_files": gate_summary["processed_files"],
        "ledger_entry_count": gate_summary["ledger_entry_count"],
        "counts_by_extension": counts_by_extension,
        "counts_by_status": gate_summary["counts_by_status"],
        "counts_by_parser_outcome": gate_summary["counts_by_parser_outcome"],
        "counts_by_reason_code": counts_by_reason_code,
        "counts_by_evidence_class": counts_by_evidence_class,
        "counts_by_deferral_class": counts_by_deferral_class,
        "unintended_deferral_reason_codes": unintended_deferral_reason_codes,
        "parse_ready": parse_ready,
    }


def _run_parse_readiness_cli(args: argparse.Namespace) -> int:
    """Run the Slice81 PARSE_SUPPORTED parse-readiness projection and print safe JSON."""
    try:
        summary = build_real_example_parse_readiness_summary(
            root=args.root,
            ledger_path=args.ledger,
            per_file_timeout_seconds=args.per_file_timeout_seconds,
            max_runtime_seconds=args.max_runtime_seconds,
            max_memory_mb=args.max_memory_mb,
            ocr_enabled=args.ocr_enabled,
            ocr_max_pages=args.ocr_max_pages,
            ocr_timeout_seconds=args.ocr_timeout_seconds,
            ocr_dpi=args.ocr_dpi,
            media_enabled=args.media_enabled,
            media_timeout_seconds=args.media_timeout_seconds,
            media_adapter=args.media_adapter,
            media_model_name=args.media_model_name,
            media_model_path=args.media_model_path,
            media_allow_model_download=args.media_allow_model_download,
            media_language=args.media_language,
            media_compute_type=args.media_compute_type,
            media_max_duration_seconds=args.media_max_duration_seconds,
        )
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "gate": "real_example_private_v1",
                    "safe_summary": True,
                    "status": "failed",
                    "reason_code": _safe_cli_reason(exc),
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


def _parse_expected_file_counts(value: str | None) -> tuple[int, int]:
    if value is None:
        return tuple(DEFAULT_EXPECTED_FILE_COUNTS)  # type: ignore[return-value]
    parts = [part.strip() for part in value.split(",")]
    if (
        len(parts) != 2
        or any(not part.isascii() or not part.isdecimal() for part in parts)
        or any(int(part) <= 0 for part in parts)
    ):
        raise DurableIngestionGateError("DATA_ROOM_INVENTORY_PACKAGE_FAILED")
    return int(parts[0]), int(parts[1])


def _durable_source_root(args: argparse.Namespace) -> Path:
    raw = str(args.root)
    if args.verify_only and raw == str(DEFAULT_REAL_EXAMPLE_ROOT):
        return DEFAULT_SOURCE_ROOT
    return Path(raw)


def _durable_path_has_reparse_component(path: Path) -> bool:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & reparse_flag
        ):
            return True
    return False


def _canonical_durable_path(raw: object) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    if any(part in {".", ".."} for part in raw.replace("/", "\\").split("\\")):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    path = Path(raw)
    if not path.is_absolute() or _durable_path_has_reparse_component(path):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    try:
        if path.resolve(strict=False) != path:
            raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    except OSError:
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
    return path


def _durable_namespace_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    report_dir = _canonical_durable_path(args.safe_report_dir)
    selected_ledger = args.prepare_ledger if args.prepare_data_rooms else args.resume_ledger
    ledger_path = _canonical_durable_path(selected_ledger)
    if (
        not _TASK4_ACCEPTANCE_NAMESPACE_RE.fullmatch(report_dir.name)
        or report_dir.parent.name != "slice100"
        or report_dir.parent.parent.name != ".local_reports"
        or ledger_path.name != TASK4_PREPARATION_LEDGER_NAME
        or ledger_path.parent != report_dir
    ):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    return report_dir, ledger_path


def _durable_ledger_path(args: argparse.Namespace) -> Path:
    _report_dir, ledger_path = _durable_namespace_paths(args)
    return ledger_path


def _durable_report_path(args: argparse.Namespace, source_root: Path) -> Path:
    report_dir, _ledger_path = _durable_namespace_paths(args)
    if (
        not source_root.is_absolute()
        or report_dir.parent != source_root.parent / ".local_reports" / "slice100"
    ):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    try:
        resolved_source = source_root.resolve(strict=False)
    except OSError:
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE") from None
    if (
        resolved_source != source_root
        or report_dir == resolved_source
        or report_dir.is_relative_to(resolved_source)
    ):
        raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    return report_dir / TASK4_DURABLE_PROOF_REPORT_NAME


def _validate_durable_cli_mode(args: argparse.Namespace) -> None:
    initial = args.prepare_data_rooms and args.preflight_only and not args.verify_only
    verification = (
        args.verify_only
        and args.verify_restart_survival
        and not args.prepare_data_rooms
        and not args.preflight_only
    )
    has_prepare_ledger = isinstance(args.prepare_ledger, str) and bool(args.prepare_ledger.strip())
    has_resume_ledger = isinstance(args.resume_ledger, str) and bool(args.resume_ledger.strip())
    if (
        not args.require_full_live
        or not args.no_secret_output
        or args.company_mode != "isolated"
        or not (initial or verification)
        or (args.verify_restart_survival is True and not verification)
        or args.verify_tenant_isolation is not True
        or (initial and (not has_prepare_ledger or has_resume_ledger))
        or (verification and (not has_resume_ledger or has_prepare_ledger))
    ):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")


def _media_runtime_provenance_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    values = [
        row.get(MEDIA_RUNTIME_PROVENANCE_KEY) for row in rows if row.get("extension") == "mp4"
    ]
    if not values or any(not isinstance(value, Mapping) for value in values):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    mapping_values = [value for value in values if isinstance(value, Mapping)]
    first = dict(mapping_values[0])
    if any(dict(value) != first for value in mapping_values):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return first


_SAFE_PARSER_COUNT_KEYS_V3 = (
    "parsed_file_count",
    "parsed_span_bearing_file_count",
    "confirmed_no_substantive_content_file_count",
    "durable_span_file_count",
    "durable_object_file_count",
    "ocr_file_count",
    "pdf_ocr_file_count",
    "stt_file_count",
    "media_dual_receipt_file_count",
    "stt_positive_file_count",
    "frame_ocr_positive_file_count",
    "large_streamed_file_count",
)
_SAFE_PARSER_COUNT_KEYS_V4 = (
    *_SAFE_PARSER_COUNT_KEYS_V3,
    "pdf_ocr_admission_file_count",
    "pdf_ocr_admission_page_count",
    "pdf_ocr_execution_page_count",
    "pdf_classifier_first_file_count",
    "pdf_classifier_only_file_count",
    "pdf_classifier_only_page_count",
    "pdf_classifier_first_page_count",
    "pdf_classifier_terminal_file_count",
    "manual_v1_confirmed_document_count",
    "watermark_classifier_document_count",
    "watermark_classified_page_count",
    "watermark_batch_attested_page_count",
    "watermark_runtime_unseen_page_count",
)


def _proof_schema_version_for_terminal_reconciliation(
    reconciliation: Mapping[str, Any],
) -> str:
    schema_version = reconciliation.get("schema_version")
    keys = set(reconciliation)
    if schema_version == TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION:
        if not keys >= (
            _TERMINAL_RECONCILIATION_V4_REQUIRED_KEYS | _TERMINAL_RECONCILIATION_V5_REQUIRED_KEYS
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        return TASK4_DURABLE_PROOF_SCHEMA_VERSION
    if schema_version == TERMINAL_INGESTION_PRIOR_RECONCILIATION_SCHEMA_VERSION:
        if not keys >= _TERMINAL_RECONCILIATION_V4_REQUIRED_KEYS or (
            _TERMINAL_RECONCILIATION_V5_REQUIRED_KEYS & keys
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        return _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V4
    if schema_version == _TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION_V3:
        if (
            _TERMINAL_RECONCILIATION_V4_REQUIRED_KEYS | _TERMINAL_RECONCILIATION_V5_REQUIRED_KEYS
        ) & keys:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        return _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V3
    raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")


def _safe_parser_counts(
    reconciliation: Mapping[str, Any],
    *,
    schema_version: str = TASK4_DURABLE_PROOF_SCHEMA_VERSION,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    is_governed = schema_version in {
        _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V4,
        _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V5,
        TASK4_DURABLE_PROOF_SCHEMA_VERSION,
    }
    keys = _SAFE_PARSER_COUNT_KEYS_V4 if is_governed else _SAFE_PARSER_COUNT_KEYS_V3
    for key in keys:
        value = reconciliation.get(key)
        if type(value) is not int or value < 0:
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        counts[key] = value
    confirmed_count = counts["confirmed_no_substantive_content_file_count"]
    if (
        counts["parsed_file_count"]
        != DEFAULT_EXPECTED_TOTAL_FILES - counts["confirmed_no_substantive_content_file_count"]
        or counts["parsed_span_bearing_file_count"] != counts["parsed_file_count"]
        or counts["durable_span_file_count"] != counts["parsed_file_count"]
        or counts["durable_object_file_count"] != DEFAULT_EXPECTED_TOTAL_FILES
        or counts["stt_file_count"] != TASK4_EXPECTED_FORMAT_COUNTS["mp4"]
        or counts["media_dual_receipt_file_count"] != TASK4_EXPECTED_FORMAT_COUNTS["mp4"]
        or counts["large_streamed_file_count"] != 3
        or not 0 <= counts["stt_positive_file_count"] <= TASK4_EXPECTED_FORMAT_COUNTS["mp4"]
        or not 0 <= counts["frame_ocr_positive_file_count"] <= TASK4_EXPECTED_FORMAT_COUNTS["mp4"]
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if not is_governed:
        if (
            counts["ocr_file_count"]
            != DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT + TASK4_EXPECTED_FORMAT_COUNTS["png"]
            or counts["pdf_ocr_file_count"] != DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT
            or confirmed_count not in {0, 1}
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        return counts
    if (
        counts["pdf_ocr_admission_file_count"] != DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT
        or counts["pdf_ocr_admission_page_count"] != DEFAULT_PDF_OCR_REQUIRED_PAGE_COUNT
        or counts["ocr_file_count"]
        != counts["pdf_ocr_file_count"] + TASK4_EXPECTED_FORMAT_COUNTS["png"]
        or counts["pdf_ocr_admission_file_count"]
        != counts["pdf_ocr_file_count"] + counts["pdf_classifier_only_file_count"]
        or counts["pdf_ocr_admission_page_count"]
        != counts["pdf_ocr_execution_page_count"] + counts["pdf_classifier_first_page_count"]
        or counts["pdf_classifier_only_file_count"] > counts["pdf_classifier_first_file_count"]
        or counts["pdf_classifier_terminal_file_count"] > counts["pdf_classifier_only_file_count"]
        or counts["pdf_classifier_only_page_count"] > counts["pdf_classifier_first_page_count"]
        or counts["pdf_classifier_only_page_count"] < counts["pdf_classifier_only_file_count"]
        or counts["pdf_classifier_first_file_count"]
        != counts["watermark_classifier_document_count"]
        or counts["pdf_classifier_first_page_count"] != counts["watermark_classified_page_count"]
        or counts["manual_v1_confirmed_document_count"] > 1
        or confirmed_count
        != counts["manual_v1_confirmed_document_count"]
        + counts["pdf_classifier_terminal_file_count"]
        or counts["watermark_classifier_document_count"] > counts["watermark_classified_page_count"]
        or counts["watermark_batch_attested_page_count"]
        + counts["watermark_runtime_unseen_page_count"]
        != counts["watermark_classified_page_count"]
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    return counts


def _execute_durable_ingestion_proof(
    *,
    root: Path,
    ledger_path: Path,
    expected_company_count: int,
    expected_file_counts: tuple[int, int],
    expected_total_files: int,
    ingest_max_bytes: int,
    api_client: _PacedExternalApiClient,
    api_key: str,
    canary_client: _PacedExternalApiClient | None,
    canary_api_key: str,
    verify_tenant_isolation: bool,
    profile_hmac_sha256: str,
    local_runtime_readiness_sha256: str,
    acceptance_tenant_id: str,
    no_substantive_content_attestation: (
        VerifiedDocumentNoSubstantiveContentAttestation
        | VerifiedDocumentNoSubstantiveContentAttestationManifest
        | None
    ) = None,
) -> dict[str, Any]:
    if not _is_sha256(profile_hmac_sha256) or not _is_sha256(local_runtime_readiness_sha256):
        raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
    if not verify_tenant_isolation or canary_client is None:
        raise DurableIngestionGateError("AUTH_CANARY_KEY_MISMATCH")
    api_boot_sha256 = _api_boot_sha256(api_client=api_client, api_key=api_key)
    _require_api_profile_match(
        api_client=api_client,
        api_key=api_key,
        expected_profile_hmac_sha256=profile_hmac_sha256,
    )
    contract = build_two_company_source_contract(
        root=root,
        expected_company_count=expected_company_count,
        expected_file_counts=expected_file_counts,
        expected_total_files=expected_total_files,
        expected_company_bytes=DEFAULT_EXPECTED_COMPANY_BYTES,
        expected_total_bytes=DEFAULT_EXPECTED_TOTAL_BYTES,
    )
    if any(
        source.size_bytes > ingest_max_bytes
        for company in contract.companies
        for source in company.files
    ):
        raise DurableIngestionGateError("INGEST_ADMISSION_MAX_SIZE_EXCEEDED")
    pdf_ocr = inspect_pdf_ocr_requirements(contract)
    pdf_ocr_execution = _derive_pdf_ocr_execution_census(
        contract=contract,
        admission=pdf_ocr,
        attestation=no_substantive_content_attestation,
    )

    before_uploads = api_client.upload_post_count
    preparation_summary = prepare_two_company_source_packages(
        api_client=api_client,
        api_key=api_key,
        ledger_path=ledger_path,
        root=root,
        expected_company_count=expected_company_count,
        expected_file_counts=expected_file_counts,
        expected_total_files=expected_total_files,
        expected_company_bytes=DEFAULT_EXPECTED_COMPANY_BYTES,
        expected_total_bytes=DEFAULT_EXPECTED_TOTAL_BYTES,
        no_substantive_content_attestation=no_substantive_content_attestation,
    )
    upload_post_count = api_client.upload_post_count - before_uploads
    if preparation_summary.get("source_tree_fingerprint") != contract.source_tree_fingerprint:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")

    terminal_rows = collect_durable_terminal_rows(
        api_client=api_client,
        api_key=api_key,
        source_companies=contract.companies,
        preparation_summary=preparation_summary,
        large_file_threshold_bytes=TASK4_LARGE_FILE_THRESHOLD_BYTES,
        expected_pdf_page_counts_by_content_sha256=(pdf_ocr.page_count_by_content_sha256),
    )
    format_counts = Counter(
        source.extension for company in contract.companies for source in company.files
    )
    if dict(format_counts) != TASK4_EXPECTED_FORMAT_COUNTS:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    company_counts = {company.company_alias: company.file_count for company in contract.companies}
    expected_large_files = sum(
        source.size_bytes > TASK4_LARGE_FILE_THRESHOLD_BYTES
        for company in contract.companies
        for source in company.files
    )
    if expected_large_files != 3:
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    watermark_manifest_runtime_summary = (
        no_substantive_content_attestation.runtime_summary()
        if isinstance(
            no_substantive_content_attestation,
            VerifiedDocumentNoSubstantiveContentAttestationManifest,
        )
        else None
    )
    media_runtime = _media_runtime_provenance_from_rows(terminal_rows)
    reconciliation = reconcile_terminal_ingestion(
        source_companies=contract.companies,
        terminal_rows=terminal_rows,
        expected_company_counts=company_counts,
        expected_format_counts=TASK4_EXPECTED_FORMAT_COUNTS,
        expected_total_files=expected_total_files,
        expected_ocr_files=pdf_ocr_execution.total_ocr_execution_file_count,
        expected_stt_files=TASK4_EXPECTED_FORMAT_COUNTS["mp4"],
        expected_large_files=expected_large_files,
        large_file_threshold_bytes=TASK4_LARGE_FILE_THRESHOLD_BYTES,
        expected_pdf_encryption_dispositions=TASK4_EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
        expected_media_runtime_provenance=media_runtime,
        expected_pdf_ocr_content_sha256s=pdf_ocr_execution.execution_content_sha256s,
        expected_pdf_ocr_required_pages_by_content_sha256=(
            pdf_ocr_execution.required_pages_by_content_sha256
        ),
        expected_pdf_ocr_render_admissions_by_content_sha256=(
            pdf_ocr_execution.render_admissions_by_content_sha256
        ),
        expected_pdf_page_counts_by_content_sha256=(pdf_ocr_execution.page_count_by_content_sha256),
        verified_watermark_manifest_runtime_summary=(watermark_manifest_runtime_summary),
    )
    proof_schema_version = _proof_schema_version_for_terminal_reconciliation(reconciliation)
    is_governed_reconciliation = proof_schema_version != _TASK4_DURABLE_PROOF_SCHEMA_VERSION_V3
    is_v5_reconciliation = proof_schema_version == TASK4_DURABLE_PROOF_SCHEMA_VERSION
    if is_governed_reconciliation:
        if (
            reconciliation.get("pdf_ocr_file_count") != pdf_ocr_execution.execution_file_count
            or reconciliation.get("ocr_file_count")
            != pdf_ocr_execution.total_ocr_execution_file_count
            or _observed_pdf_ocr_execution_page_count(terminal_rows)
            != pdf_ocr_execution.execution_page_count
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        reconciliation = {
            **reconciliation,
            **pdf_ocr_execution.safe_count_fields(),
        }
    parser_counts = _safe_parser_counts(
        reconciliation,
        schema_version=proof_schema_version,
    )
    watermark_pages_by_company = reconciliation.get("watermark_classified_pages_by_company")
    watermark_classification_receipts_sha256 = reconciliation.get(
        "watermark_classification_receipts_sha256"
    )
    watermark_runtime_review_required = reconciliation.get("runtime_review_required")
    watermark_manifest_bound_receipt_count = reconciliation.get(
        "watermark_manifest_bound_receipt_count"
    )
    watermark_manifest_runtime_summary_sha256 = reconciliation.get(
        "watermark_manifest_runtime_summary_sha256"
    )
    expected_watermark_manifest_runtime_summary_sha256 = _canonical_json_sha256(
        watermark_manifest_runtime_summary or {}
    )
    if is_governed_reconciliation and (
        not isinstance(watermark_pages_by_company, Mapping)
        or set(watermark_pages_by_company) != {"company-001", "company-002"}
        or any(type(value) is not int or value < 0 for value in watermark_pages_by_company.values())
        or not _is_sha256(watermark_classification_receipts_sha256)
        or type(watermark_manifest_bound_receipt_count) is not int
        or watermark_manifest_bound_receipt_count
        != reconciliation.get("watermark_classifier_document_count")
        or watermark_manifest_runtime_summary_sha256
        != expected_watermark_manifest_runtime_summary_sha256
        or watermark_runtime_review_required is not False
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    if isinstance(
        no_substantive_content_attestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    ):
        expected_confirmed_no_substantive_content = pdf_ocr_execution.classifier_terminal_file_count
    else:
        expected_confirmed_no_substantive_content = int(
            isinstance(
                no_substantive_content_attestation,
                VerifiedDocumentNoSubstantiveContentAttestation,
            )
        )
    if (
        parser_counts["confirmed_no_substantive_content_file_count"]
        != expected_confirmed_no_substantive_content
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    confirmed_receipts_sha256 = reconciliation.get(
        "confirmed_no_substantive_content_receipts_sha256"
    )
    if not _is_sha256(confirmed_receipts_sha256):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    confirmed_variant_counts = reconciliation.get("confirmed_no_substantive_content_variant_counts")
    if (
        not isinstance(confirmed_variant_counts, Mapping)
        or set(confirmed_variant_counts) != {"blank", "illegible", "watermark_only"}
        or confirmed_variant_counts.get("watermark_only")
        != expected_confirmed_no_substantive_content
        or confirmed_variant_counts.get("blank") != 0
        or confirmed_variant_counts.get("illegible") != 0
    ):
        raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
    attestation_summary = _safe_no_substantive_content_attestation_summary(
        no_substantive_content_attestation
    )
    authenticated_downloads = _verify_authenticated_downloads(
        api_client=api_client,
        api_key=api_key,
        acceptance_tenant_id=acceptance_tenant_id,
        source_companies=contract.companies,
        terminal_rows=terminal_rows,
    )
    isolation_receipt = _verify_tenant_isolation(
        api_client=canary_client,
        canary_api_key=canary_api_key,
        preparation_summary=preparation_summary,
        terminal_rows=terminal_rows,
    )
    terminal_snapshot_digests = _canonical_terminal_snapshot_digests(terminal_rows)
    terminal_rows_sha256 = str(terminal_snapshot_digests["terminal_rows_sha256"])
    post_isolation_rows = collect_durable_terminal_rows(
        api_client=api_client,
        api_key=api_key,
        source_companies=contract.companies,
        preparation_summary=preparation_summary,
        large_file_threshold_bytes=TASK4_LARGE_FILE_THRESHOLD_BYTES,
        expected_pdf_page_counts_by_content_sha256=(pdf_ocr_execution.page_count_by_content_sha256),
    )
    _require_terminal_snapshot_stable(
        before=terminal_snapshot_digests,
        after_rows=post_isolation_rows,
        mismatch_path=ledger_path.parent / TASK4_TERMINAL_SNAPSHOT_MISMATCH_REPORT_NAME,
    )
    isolation_receipt = {**isolation_receipt, "post_mutation_state_verified": True}

    receipt: dict[str, Any] = {
        "schema_version": proof_schema_version,
        "status": "ready",
        "phase": "prepared",
        "total_file_count": expected_total_files,
        "total_bytes": contract.total_bytes,
        "counts_by_company": dict(sorted(company_counts.items())),
        "bytes_by_company": {
            company.company_alias: company.total_bytes for company in contract.companies
        },
        "counts_by_extension": dict(sorted(format_counts.items())),
        "source_tree_fingerprint": contract.source_tree_fingerprint,
        "preparation_ledger_sha256": _file_sha256(ledger_path),
        "terminal_rows_sha256": terminal_rows_sha256,
        "plan_sha256": _task4_plan_sha256(),
        "code_sha256": _task4_code_sha256(),
        "reconciliation_sha256": _canonical_json_sha256(reconciliation),
        "profile_hmac_sha256": profile_hmac_sha256,
        "local_runtime_readiness_sha256": local_runtime_readiness_sha256,
        "no_substantive_content_attestation": attestation_summary,
        "no_substantive_content_attestation_sha256": _canonical_json_sha256(attestation_summary),
        "confirmed_no_substantive_content_receipts_sha256": confirmed_receipts_sha256,
        "confirmed_no_substantive_content_variant_counts": dict(confirmed_variant_counts),
        "api_boot_sha256": api_boot_sha256,
        "previous_api_boot_sha256": None,
        "authenticated_download_company_count": authenticated_downloads["company_count"],
        "authenticated_download_receipts_sha256": authenticated_downloads["receipts_sha256"],
        "parser_counts": parser_counts,
        "upload_post_count": upload_post_count,
        "tenant_isolation": isolation_receipt,
        "tenant_isolation_receipt_sha256": _canonical_json_sha256(isolation_receipt),
        "restart_survival_verified": False,
    }
    if is_governed_reconciliation:
        assert isinstance(watermark_pages_by_company, Mapping)
        assert isinstance(watermark_classification_receipts_sha256, str)
        receipt.update(
            {
                "watermark_classified_pages_by_company": dict(watermark_pages_by_company),
                "watermark_classification_receipts_sha256": (
                    watermark_classification_receipts_sha256
                ),
                "watermark_manifest_bound_receipt_count": (watermark_manifest_bound_receipt_count),
                "watermark_manifest_runtime_summary_sha256": (
                    watermark_manifest_runtime_summary_sha256
                ),
                "watermark_runtime_review_required": watermark_runtime_review_required,
            }
        )
    if is_v5_reconciliation:
        census_schema_version = reconciliation.get("pdf_page_disposition_census_schema_version")
        census_class_counts = reconciliation.get("pdf_page_disposition_class_counts")
        census_digest = reconciliation.get("pdf_page_disposition_censuses_sha256")
        if (
            census_schema_version != PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION
            or not isinstance(census_class_counts, Mapping)
            or set(census_class_counts)
            != {"native_text", "ocr_text", "image_only_page", "watermark_only"}
            or any(type(value) is not int or value < 0 for value in census_class_counts.values())
            or sum(int(value) for value in census_class_counts.values()) != DEFAULT_PDF_PAGE_COUNT
            or not _is_sha256(census_digest)
        ):
            raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
        receipt.update(
            {
                "pdf_page_disposition_census_schema_version": census_schema_version,
                "pdf_page_disposition_class_counts": dict(census_class_counts),
                "pdf_page_disposition_censuses_sha256": census_digest,
                "terminal_snapshot_digests": terminal_snapshot_digests,
            }
        )
    return receipt


def _durable_failure_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code and code.isascii():
        return code
    return "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE"


def _run_durable_ingestion_cli(
    args: argparse.Namespace,
    *,
    strict_profile: StrictRuntimeProfile | None,
) -> int:
    try:
        profile_hmac_sha256 = _require_task4_gate_profile(strict_profile)
        if strict_profile is None:  # pragma: no cover - rejected by the helper above.
            raise DurableIngestionGateError("STRICT_FULL_LIVE_BLOCKED")
        strict_env = strict_profile.values
        _validate_durable_cli_mode(args)
        _report_dir, ledger_path = _durable_namespace_paths(args)
        local_runtime_readiness_sha256 = _require_task4_local_runtime_readiness(env=strict_env)
        no_substantive_content_attestation = load_document_no_substantive_content_attestation(
            env=strict_env
        )
        settings = _build_durable_api_settings(
            env=strict_env,
            api_base_url=args.api_base_url,
            api_key_env=args.api_key_env,
            canary_api_key_env=args.canary_api_key_env,
        )
        expected_company_count = args.expected_company_count or 2
        expected_file_counts = _parse_expected_file_counts(args.expected_file_counts)
        expected_total_files = args.expected_total_files or DEFAULT_EXPECTED_TOTAL_FILES
        if (
            expected_company_count != 2
            or expected_file_counts != tuple(DEFAULT_EXPECTED_FILE_COUNTS)
            or expected_total_files != DEFAULT_EXPECTED_TOTAL_FILES
        ):
            raise DurableIngestionGateError("DATA_ROOM_INVENTORY_PACKAGE_FAILED")
        source_root = _durable_source_root(args)
        report_path = _durable_report_path(args, source_root)
        if ledger_path.resolve(strict=False) == report_path:
            raise DurableIngestionGateError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
        baseline = _read_safe_durable_ingestion_receipt(report_path) if args.verify_only else None
        with _PacedExternalApiClient(
            settings=settings,
            api_key=settings.api_key,
        ) as api_client:
            canary_client: _PacedExternalApiClient | None = None
            try:
                if args.verify_tenant_isolation:
                    canary_client = _PacedExternalApiClient(
                        settings=settings,
                        api_key=settings.canary_api_key,
                    )
                receipt = _execute_durable_ingestion_proof(
                    root=source_root,
                    ledger_path=ledger_path,
                    expected_company_count=expected_company_count,
                    expected_file_counts=expected_file_counts,
                    expected_total_files=expected_total_files,
                    ingest_max_bytes=_strict_positive_int(
                        strict_env,
                        "IDIS_INGEST_MAX_BYTES",
                    ),
                    api_client=api_client,
                    api_key=settings.api_key,
                    canary_client=canary_client,
                    canary_api_key=settings.canary_api_key,
                    verify_tenant_isolation=args.verify_tenant_isolation,
                    profile_hmac_sha256=profile_hmac_sha256,
                    local_runtime_readiness_sha256=local_runtime_readiness_sha256,
                    acceptance_tenant_id=strict_env["IDIS_ACCEPTANCE_TENANT_ID"],
                    no_substantive_content_attestation=no_substantive_content_attestation,
                )
            finally:
                if canary_client is not None:
                    canary_client.close()
        if args.verify_restart_survival:
            if baseline is None:
                raise DurableIngestionGateError("DATA_ROOM_RECONCILIATION_INCOMPLETE")
            _verify_restart_equivalence(
                baseline=baseline,
                current=receipt,
                upload_post_count=int(receipt["upload_post_count"]),
            )
            receipt["previous_api_boot_sha256"] = baseline["api_boot_sha256"]
            receipt["phase"] = "restart_verified"
            receipt["restart_survival_verified"] = True
        _write_safe_durable_ingestion_receipt(report_path, receipt)
    except (
        DurableIngestionGateError,
        DocumentNoSubstantiveContentAttestationError,
        SourcePackageContractError,
        TerminalIngestionReconciliationError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "gate": "slice100_task4_durable_ingestion",
                    "safe_summary": True,
                    "status": "failed",
                    "reason_code": _durable_failure_code(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    strict_profile: StrictRuntimeProfile | None = None,
) -> int:
    """CLI entrypoint for the private real_example gate."""
    parser = argparse.ArgumentParser(
        description="Run the local-only private real_example gate.",
        epilog=(
            "Examples:\n"
            "  python scripts/run_real_example_gate.py --inventory-only\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary\n"
            "  python scripts/run_real_example_gate.py --root real_example --parse-readiness\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary "
            "--per-file-timeout-seconds 20 --max-runtime-seconds 900"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modes = parser.add_mutually_exclusive_group(required=False)
    modes.add_argument("--inventory-only", action="store_true")
    modes.add_argument("--parse-supported", action="store_true")
    modes.add_argument(
        "--parse-readiness",
        action="store_true",
        help="Run the PARSE_SUPPORTED parse-readiness projection (evidence/deferral class + "
        "parse_ready). Always emits the safe projection.",
    )
    parser.add_argument("--safe-summary", action="store_true")
    parser.add_argument("--root", default=str(DEFAULT_REAL_EXAMPLE_ROOT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--api-base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--canary-api-key-env")
    parser.add_argument("--safe-report-dir")
    parser.add_argument("--prepare-ledger")
    parser.add_argument("--resume-ledger")
    parser.add_argument("--company-mode", choices=("isolated",), default="isolated")
    parser.add_argument("--expected-company-count", type=int)
    parser.add_argument("--expected-file-counts")
    parser.add_argument("--expected-total-files", type=int)
    parser.add_argument("--require-full-live", action="store_true")
    parser.add_argument("--prepare-data-rooms", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--verify-restart-survival", action="store_true")
    parser.add_argument("--verify-tenant-isolation", action="store_true")
    parser.add_argument("--no-secret-output", action="store_true")
    parser.add_argument(
        "--per-file-timeout-seconds",
        type=float,
        default=DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    )
    parser.add_argument("--max-runtime-seconds", type=float)
    parser.add_argument("--max-memory-mb", type=int)
    parser.add_argument("--ocr-enabled", action="store_true")
    parser.add_argument("--ocr-max-pages", type=int, default=MAX_OCR_PAGES)
    parser.add_argument("--ocr-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--ocr-dpi", type=int, default=200)
    parser.add_argument("--media-enabled", action="store_true")
    parser.add_argument("--media-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--media-adapter",
        choices=sorted(MEDIA_ADAPTER_CHOICES),
        default=os.environ.get("IDIS_MEDIA_ADAPTER", MEDIA_ADAPTER_NONE),
    )
    parser.add_argument("--media-model-name", default=os.environ.get("IDIS_MEDIA_STT_MODEL_NAME"))
    parser.add_argument("--media-model-path", default=os.environ.get("IDIS_MEDIA_STT_MODEL_PATH"))
    parser.add_argument(
        "--media-allow-model-download",
        action="store_true",
        default=_media_allow_model_download_default(),
    )
    parser.add_argument(
        "--media-language",
        default=os.environ.get("IDIS_MEDIA_LANGUAGE", DEFAULT_MEDIA_LANGUAGE),
    )
    parser.add_argument(
        "--media-compute-type",
        default=os.environ.get("IDIS_MEDIA_COMPUTE_TYPE", DEFAULT_MEDIA_COMPUTE_TYPE),
    )
    parser.add_argument(
        "--media-max-duration-seconds",
        type=float,
        default=_env_float(
            "IDIS_MEDIA_MAX_DURATION_SECONDS",
            default=MAX_MEDIA_DURATION_SECONDS,
        ),
    )
    parser.add_argument("--emit-progress", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)

    durable_requested = any(
        (
            args.prepare_data_rooms,
            args.preflight_only,
            args.verify_only,
            args.verify_restart_survival,
            args.verify_tenant_isolation,
        )
    )
    if durable_requested:
        if args.inventory_only or args.parse_supported or args.parse_readiness:
            parser.error("durable ingestion proof cannot be combined with a local parse mode")
        return _run_durable_ingestion_cli(args, strict_profile=strict_profile)
    if not (args.inventory_only or args.parse_supported or args.parse_readiness):
        parser.error(
            "one of --inventory-only, --parse-supported, --parse-readiness, "
            "--prepare-data-rooms, or --verify-only is required"
        )

    if args.parse_readiness:
        return _run_parse_readiness_cli(args)

    mode = GateMode.PARSE_SUPPORTED if args.parse_supported else GateMode.INVENTORY_ONLY
    if mode == GateMode.PARSE_SUPPORTED and not args.safe_summary:
        parser.error("--parse-supported requires --safe-summary")

    try:
        summary = run_real_example_gate(
            root=args.root,
            ledger_path=args.ledger,
            mode=mode,
            safe_summary=True,
            per_file_timeout_seconds=args.per_file_timeout_seconds,
            max_runtime_seconds=args.max_runtime_seconds,
            max_memory_mb=args.max_memory_mb,
            emit_progress=args.emit_progress and not args.no_progress,
            ocr_enabled=args.ocr_enabled,
            ocr_max_pages=args.ocr_max_pages,
            ocr_timeout_seconds=args.ocr_timeout_seconds,
            ocr_dpi=args.ocr_dpi,
            media_enabled=args.media_enabled,
            media_timeout_seconds=args.media_timeout_seconds,
            media_adapter=args.media_adapter,
            media_model_name=args.media_model_name,
            media_model_path=args.media_model_path,
            media_allow_model_download=args.media_allow_model_download,
            media_language=args.media_language,
            media_compute_type=args.media_compute_type,
            media_max_duration_seconds=args.media_max_duration_seconds,
        )
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "gate": "real_example_private_v1",
                    "safe_summary": True,
                    "status": "failed",
                    "reason_code": _safe_cli_reason(exc),
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


def _attempt_for_file(
    *,
    file: _InventoryFile,
    mode: GateMode,
    resume_entries: dict[str, object],
    started_at: float,
    max_runtime_seconds: float | None,
    max_memory_mb: int | None,
    per_file_timeout_seconds: float,
    parse_attempt_fn: ParseAttemptFn | None,
    ocr_enabled: bool,
    ocr_max_pages: int,
    ocr_timeout_seconds: float,
    ocr_dpi: int,
    ocr_policy_key: str | None,
    media_enabled: bool,
    media_timeout_seconds: float,
    media_adapter: str,
    media_model_name: str | None,
    media_model_path: str | None,
    media_allow_model_download: bool,
    media_language: str,
    media_compute_type: str,
    media_max_duration_seconds: float,
    media_policy_key: str | None,
) -> ParseAttempt:
    if mode == GateMode.INVENTORY_ONLY:
        return ParseAttempt(
            status="inventoried",
            parser_outcome="not_attempted",
            reason_code="inventory_only",
        )
    if file.read_error_reason is not None:
        return ParseAttempt(
            status="failed",
            parser_outcome="not_attempted",
            reason_code=file.read_error_reason,
        )

    existing = terminal_ledger_entry(
        entries=resume_entries,
        sha256=file.sha256,
        extension=file.extension,
        ocr_enabled=ocr_enabled,
        ocr_policy_key=ocr_policy_key,
        media_enabled=media_enabled,
        media_policy_key=media_policy_key,
    )
    if existing is not None:
        return ParseAttempt(
            status=str(existing["status"]),
            parser_outcome="resumed",
            reason_code=str(existing["reason_code"]),
        )

    capability = capability_for_document(
        filename=f"file{file.extension}",
        file_size_bytes=file.size_bytes,
    )
    if capability.support_status == DocumentSupportStatus.TOO_LARGE:
        return ParseAttempt.deferred(reason_code="file_too_large")

    should_parse_image_ocr = _should_parse_image_with_ocr(
        extension=file.extension,
        support_status=capability.support_status,
        ocr_enabled=ocr_enabled,
    )
    should_parse_text = file.extension in TEXT_PARSE_EXTENSIONS
    should_parse_media = _should_parse_media(
        extension=file.extension,
        support_status=capability.support_status,
        media_enabled=media_enabled,
    )
    if (
        file.extension not in SUPPORTED_PARSE_EXTENSIONS
        and not should_parse_image_ocr
        and not should_parse_text
        and not should_parse_media
    ):
        reason_code = (
            "unsupported_in_slice_29"
            if file.extension == ".xlsm"
            else _capability_reason_code(
                support_status=capability.support_status,
                triage_status=capability.triage_status,
                reason_codes=capability.reason_codes,
            )
        )
        if capability.support_status == DocumentSupportStatus.UNSUPPORTED:
            return ParseAttempt.unsupported(reason_code=reason_code)
        return ParseAttempt.deferred(reason_code=reason_code)

    if (
        not should_parse_image_ocr
        and not should_parse_text
        and not should_parse_media
        and not _should_parse(
            capability.support_status,
            capability.parser_name,
        )
    ):
        reason_code = _capability_reason_code(
            support_status=capability.support_status,
            triage_status=capability.triage_status,
            reason_codes=capability.reason_codes,
        )
        if capability.support_status == DocumentSupportStatus.UNSUPPORTED:
            return ParseAttempt.unsupported(reason_code=reason_code)
        return ParseAttempt.deferred(reason_code=reason_code)

    if _runtime_exceeded(started_at, max_runtime_seconds):
        return ParseAttempt.deferred(reason_code="max_runtime_exceeded")
    if memory_exceeded(max_memory_mb):
        return ParseAttempt.deferred(reason_code="max_memory_exceeded")

    if should_parse_media and not _media_adapter_attemptable(
        media_adapter=media_adapter,
        media_model_name=media_model_name,
        media_model_path=media_model_path,
        media_allow_model_download=media_allow_model_download,
    ):
        return ParseAttempt.media_required(reason_code="media_transcription_unavailable")

    if parse_attempt_fn is not None:
        return run_injected_parse_with_timeout(
            file.path,
            parse_attempt_fn=parse_attempt_fn,
            timeout_seconds=per_file_timeout_seconds,
        )
    return run_parse_subprocess(
        file.path,
        timeout_seconds=per_file_timeout_seconds,
        max_memory_mb=max_memory_mb,
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
        media_enabled=media_enabled,
        media_timeout_seconds=media_timeout_seconds,
        media_adapter=media_adapter,
        media_model_name=media_model_name,
        media_model_path=media_model_path,
        media_allow_model_download=media_allow_model_download,
        media_language=media_language,
        media_compute_type=media_compute_type,
        media_max_duration_seconds=media_max_duration_seconds,
    )


def _inventory_files(root: Path) -> list[_InventoryFile]:
    records: list[_InventoryFile] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=_sort_key(root)):
        extension = path.suffix.lower() or ".unknown"
        try:
            if extension in MEDIA_EXTENSIONS:
                file_stat = path.stat()
                size_bytes = file_stat.st_size
                sha256 = _media_no_read_file_key(
                    extension=extension,
                    size_bytes=size_bytes,
                    modified_ns=file_stat.st_mtime_ns,
                )
            else:
                sha256, size_bytes = _hash_file_streaming(path)
            records.append(
                _InventoryFile(
                    path=path,
                    extension=extension,
                    size_bytes=size_bytes,
                    sha256=sha256,
                )
            )
        except OSError:
            records.append(
                _InventoryFile(
                    path=path,
                    extension=extension,
                    size_bytes=0,
                    sha256=_unreadable_file_key(root=root, path=path),
                    read_error_reason="file_scan_failed",
                )
            )
    return records


def _validate_ocr_options(
    *,
    ocr_enabled: bool,
    ocr_max_pages: int,
    ocr_timeout_seconds: float,
    ocr_dpi: int,
) -> None:
    if not ocr_enabled:
        return
    if not 1 <= ocr_max_pages <= MAX_OCR_PAGES:
        raise ValueError("OCR max pages is outside the allowed range")
    if not 0 < ocr_timeout_seconds <= MAX_OCR_TIMEOUT_SECONDS:
        raise ValueError("OCR timeout is outside the allowed range")
    if not MIN_OCR_DPI <= ocr_dpi <= MAX_OCR_DPI:
        raise ValueError("OCR DPI is outside the allowed range")


def _validate_media_options(
    *,
    media_enabled: bool,
    media_timeout_seconds: float,
    media_adapter: str,
    media_max_duration_seconds: float,
) -> None:
    if media_adapter not in MEDIA_ADAPTER_CHOICES:
        raise ValueError("Media adapter is outside the allowed choices")
    if not media_enabled:
        return
    if not 0 < media_timeout_seconds <= MAX_MEDIA_TIMEOUT_SECONDS:
        raise ValueError("Media timeout is outside the allowed range")
    if not 0 < media_max_duration_seconds <= MAX_MEDIA_DURATION_SECONDS:
        raise ValueError("Media max duration is outside the allowed range")


def _media_adapter_attemptable(
    *,
    media_adapter: str,
    media_model_name: str | None,
    media_model_path: str | None,
    media_allow_model_download: bool,
) -> bool:
    if media_adapter != FASTER_WHISPER_ADAPTER_NAME:
        return False
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return False
    if media_model_path:
        return probe_faster_whisper_model(
            FasterWhisperMediaConfig(
                model_path=media_model_path,
                allow_model_download=False,
            )
        ).can_attempt
    return probe_faster_whisper_model(
        FasterWhisperMediaConfig(
            model_name=media_model_name,
            allow_model_download=media_allow_model_download,
        )
    ).can_attempt


def _media_model_policy_key(
    *,
    media_model_name: str | None,
    media_model_path: str | None,
) -> str:
    if media_model_path:
        model_path = Path(media_model_path).expanduser()
        model_digest = hashlib.sha256()
        try:
            model_stat = model_path.stat()
        except OSError:
            model_digest.update(str(model_path).encode())
            return f"path-missing-sha256:{model_digest.hexdigest()[:16]}"
        _update_model_signature(
            digest=model_digest,
            label="root",
            stat_payload=f"{model_stat.st_mode}:{model_stat.st_size}:{model_stat.st_mtime_ns}",
        )
        if model_path.is_dir():
            for child_path in sorted(
                model_path.rglob("*"),
                key=lambda path: path.relative_to(model_path).as_posix().lower(),
            ):
                try:
                    child_stat = child_path.stat()
                except OSError:
                    continue
                relative_hash = hashlib.sha256(
                    child_path.relative_to(model_path).as_posix().encode()
                ).hexdigest()
                _update_model_signature(
                    digest=model_digest,
                    label=relative_hash,
                    stat_payload=(
                        f"{child_stat.st_mode}:{child_stat.st_size}:{child_stat.st_mtime_ns}"
                    ),
                )
            return f"dir-sha256:{model_digest.hexdigest()[:16]}"
        return f"file-sha256:{model_digest.hexdigest()[:16]}"
    if media_model_name:
        return f"name:{media_model_name}"
    return "none"


def _update_model_signature(
    *,
    digest: Any,
    label: str,
    stat_payload: str,
) -> None:
    digest.update(f"{label}:{stat_payload};".encode())


def _hash_file_streaming(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as file:
        while chunk := file.read(HASH_CHUNK_SIZE_BYTES):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _media_no_read_file_key(*, extension: str, size_bytes: int, modified_ns: int) -> str:
    payload = f"{extension}:{size_bytes}:{modified_ns}".encode()
    return f"media-no-read:{hashlib.sha256(payload).hexdigest()}"


def _sort_key(root: Path) -> Callable[[Path], str]:
    def key(path: Path) -> str:
        return path.relative_to(root).as_posix().lower()

    return key


def _should_parse(support_status: DocumentSupportStatus, parser_name: str | None) -> bool:
    return (
        support_status
        in {
            DocumentSupportStatus.SUPPORTED,
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
        }
        and parser_name in SUPPORTED_PARSER_NAMES
    )


def _should_parse_image_with_ocr(
    *,
    extension: str,
    support_status: DocumentSupportStatus,
    ocr_enabled: bool,
) -> bool:
    return (
        ocr_enabled
        and extension in OCR_IMAGE_EXTENSIONS
        and support_status == DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY
    )


def _should_parse_media(
    *,
    extension: str,
    support_status: DocumentSupportStatus,
    media_enabled: bool,
) -> bool:
    return (
        media_enabled
        and extension in MEDIA_EXTENSIONS
        and support_status == DocumentSupportStatus.CONVERSION_REQUIRED
    )


def _capability_reason_code(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    reason_codes: list[str],
) -> str:
    if support_status == DocumentSupportStatus.CONVERSION_REQUIRED:
        return "conversion_required"
    if triage_status == DocumentTriageStatus.OCR_REQUIRED:
        return "ocr_required"
    if support_status == DocumentSupportStatus.UNSUPPORTED:
        return "unsupported_format"
    if support_status == DocumentSupportStatus.TOO_LARGE:
        return "file_too_large"
    return _first_reason_code(reason_codes, default="unknown_format")


def _first_reason_code(reason_codes: list[str], *, default: str) -> str:
    if not reason_codes:
        return default
    return sorted(reason_codes)[0]


# --- Slice81 parse-readiness classifiers (pure; extension/reason-code only; no IO) ---

_EVIDENCE_CLASS_BY_EXTENSION: dict[str, str] = {
    "pdf": "PDF",
    "xlsx": "SPREADSHEET",
    "xlsm": "SPREADSHEET",
    "docx": "DOCUMENT",
    "pptx": "PRESENTATION",
    "html": "WEB_TEXT",
    "htm": "WEB_TEXT",
    "txt": "WEB_TEXT",
    "png": "IMAGE",
    "jpg": "IMAGE",
    "jpeg": "IMAGE",
    "tif": "IMAGE",
    "tiff": "IMAGE",
    "bmp": "IMAGE",
    "mp4": "MEDIA",
}
_EVIDENCE_CLASS_OTHER = "OTHER"

# Locked intended terminal blockers (safe, expected). Everything else - retryable/transient
# codes, ``unknown_format``, empty/missing, or any unrecognized future code - is an UNINTENDED
# deferral (fail-safe: parse readiness must never silently accept an unknown blocker).
_INTENDED_REASON_CODES: frozenset[str] = frozenset(
    {
        "parsed",
        "conversion_required",
        "ocr_required",
        "ocr_no_text_extracted",
        "media_transcription_unavailable",
        "media_no_text_extracted",
        "media_duration_exceeded",
        "unsupported_format",
        "unsupported_in_slice_29",
        "file_too_large",
        "encrypted_pdf",
        "no_text_extracted",
        "corrupted_file",
        "inventory_only",
    }
)
_UNINTENDED_REASON_CODES: frozenset[str] = frozenset(
    RETRYABLE_REASON_CODES
    | {"unknown_format", "media_transcription_failed", "media_transcription_timeout"}
)
_DEFERRAL_CLASS_INTENDED = "intended"
_DEFERRAL_CLASS_UNINTENDED = "unintended"


def _evidence_class_for_extension(extension: str) -> str:
    """Map a file extension to a safe evidence class.

    Derived from the extension token only - never a filename or content. Case-insensitive;
    a leading dot is optional. Unknown/empty tokens map to ``OTHER``.
    """
    token = str(extension).strip().lower().lstrip(".")
    return _EVIDENCE_CLASS_BY_EXTENSION.get(token, _EVIDENCE_CLASS_OTHER)


def _deferral_class_for_reason_code(reason_code: str | None) -> str:
    """Classify a parse reason code as an intended blocker or an unintended deferral.

    Fail-safe: only the locked intended set is ``intended``; retryable/transient codes,
    ``unknown_format``, empty/missing, and any unrecognized future code are ``unintended``
    so parse readiness never silently accepts an unknown blocker.
    """
    code = (reason_code or "").strip()
    if code in _UNINTENDED_REASON_CODES:
        return _DEFERRAL_CLASS_UNINTENDED
    if code in _INTENDED_REASON_CODES:
        return _DEFERRAL_CLASS_INTENDED
    return _DEFERRAL_CLASS_UNINTENDED


def _aggregate_counts_by(
    counts: dict[str, int], classifier: Callable[[str], str]
) -> dict[str, int]:
    """Re-bucket an existing ``{key: count}`` aggregate by a classifier, count-weighted.

    Emits only classifier labels (evidence class / deferral class) - never the original
    keys - so reason/extension strings are not echoed beyond the gate's already-safe counts.
    """
    aggregated: Counter[str] = Counter()
    for key, count in counts.items():
        aggregated[classifier(key)] += count
    return dict(sorted(aggregated.items()))


def _runtime_exceeded(started_at: float, max_runtime_seconds: float | None) -> bool:
    return max_runtime_seconds is not None and time.monotonic() - started_at >= max_runtime_seconds


def _record_ledger_entry(
    *,
    ledger: dict[str, Any],
    file: _InventoryFile,
    attempt: ParseAttempt,
    mode: GateMode,
    ocr_policy_key: str | None,
    media_policy_key: str | None,
) -> None:
    if mode == GateMode.PARSE_SUPPORTED and attempt.parser_outcome == "resumed":
        return
    record_ledger_entry(
        ledger=ledger,
        sha256=file.sha256,
        extension=file.extension,
        size_bytes=file.size_bytes,
        status=attempt.status,
        parser_outcome=attempt.parser_outcome,
        reason_code=attempt.reason_code,
        ocr_policy_key=ocr_policy_key if file.extension == ".pdf" else None,
        media_policy_key=media_policy_key if file.extension in MEDIA_EXTENSIONS else None,
    )


def _safe_summary(
    *,
    mode: GateMode,
    records: list[dict[str, object]],
    ledger: dict[str, Any],
) -> dict[str, object]:
    return {
        "gate": "real_example_private_v1",
        "safe_summary": True,
        "mode": mode.value,
        "total_files": len(records),
        "processed_files": len(records),
        "ledger_entry_count": ledger_entry_count(ledger),
        "counts_by_extension": _count(records, "extension"),
        "counts_by_status": _count(records, "status"),
        "counts_by_parser_outcome": _count(records, "parser_outcome"),
        "counts_by_reason_code": _count(records, "reason_code"),
    }


def _count(records: list[dict[str, object]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(record[key]) for record in records).items()))


def _validate_root(root: Path) -> None:
    if not root.exists():
        raise FileNotFoundError("real_example root does not exist")
    if not root.is_dir():
        raise NotADirectoryError("real_example root is not a directory")


def _safe_cli_reason(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "root_not_found"
    if isinstance(exc, NotADirectoryError):
        return "root_not_directory"
    if isinstance(exc, RuntimeError):
        return "ledger_write_failed"
    if isinstance(exc, ValueError):
        return "invalid_arguments"
    return "internal_error"


def _unreadable_file_key(*, root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix().encode("utf-8", errors="replace")
    return f"unreadable:{hashlib.sha256(relative).hexdigest()}"


def _emit_progress(event: dict[str, object]) -> None:
    print(json.dumps({"progress": event}, sort_keys=True), file=sys.stderr)


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _media_allow_model_download_default() -> bool:
    return _env_bool("IDIS_MEDIA_STT_ALLOW_DOWNLOAD", default=False)


__all__ = [
    "GateMode",
    "ParseAttempt",
    "build_data_room_package_inventory_summary",
    "main",
    "run_real_example_gate",
]
