"""Pure compiler for the Slice100 exemplar-watermark prescan receipt.

The module accepts only safe source-contract facts and already-computed page
observations.  It deliberately has no filesystem, API, database, cache,
object-store, audit, or network capability.  Acquisition and rendering remain
outside this boundary; this boundary validates completeness and emits the
deterministic, private-name-free receipt used for reviewer batch admission.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Never, TypeGuard

from idis.runtime.governance_envelopes import canonicalize_json

WATERMARK_PAGE_PRESCAN_SCHEMA_VERSION = "watermark-page-prescan-v1"
WATERMARK_CLASSIFIER_POLICY_ID = "exemplar-watermark-page-v1"
WATERMARK_PRESCAN_INCOMPLETE = "WATERMARK_PRESCAN_INCOMPLETE"
WATERMARK_PAGE_POLICY_INVALID = "WATERMARK_PAGE_POLICY_INVALID"
WATERMARK_PAGE_SANITY_CAP_EXCEEDED = "WATERMARK_PAGE_SANITY_CAP_EXCEEDED"

SOURCE_CONTRACT_SCHEMA_VERSION = "slice100-two-company-source-contract-v1"
EXPECTED_TOTAL_FILE_COUNT = 267
EXPECTED_TOTAL_BYTES = 858_207_454
EXPECTED_COMPANY_FILE_COUNTS = {
    "company-001": 58,
    "company-002": 209,
}
MANDATORY_REVIEW_ALIAS = "company-002-document-00054.pdf"
SIGNED_EXEMPLAR_ALIAS = "company-002-document-00053.pdf"
OCR_RESCUE_CANDIDATE_COUNT = 24
SIMILARITY_THRESHOLD_TEXT = "0.800000"
SANITY_CAP_PERCENT = 15
REVIEW_SAMPLE_LIMIT = 5

_SIMILARITY_THRESHOLD = Decimal(SIMILARITY_THRESHOLD_TEXT)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ALIAS_RE = re.compile(
    r"^(?P<company>company-00[12])-document-(?P<sequence>[0-9]{5})\."
    r"(?P<extension>[a-z0-9]{1,12})$"
)
_SCORE_RE = re.compile(r"^(?:0\.[0-9]{6}|1\.000000)$")
_CANDIDATE_FIELDS = frozenset(
    {
        "safe_alias",
        "content_sha256",
        "page_number",
        "canonical_render_sha256",
        "similarity_score",
    }
)
_FILE_RESULT_FIELDS = frozenset(
    {
        "company_alias",
        "safe_alias",
        "content_sha256",
        "format",
        "scan_status",
        "eligible_pages",
    }
)
_PAGE_RESULT_FIELDS = frozenset(
    {
        "page_number",
        "native_text_token_count",
        "primary_ocr_token_count",
        "rescue_candidate_attempt_count",
        "rescue_candidate_completed_count",
        "rescue_outcome",
        "faint_signature_matches",
        "similarity_score",
        "canonical_render_sha256",
        "exemplar_content_sha256",
        "classifier_policy_sha256",
    }
)
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "source_contract_schema_version",
        "source_tree_fingerprint",
        "total_file_count",
        "total_bytes",
        "company_file_counts",
        "scanned_file_count",
        "classifier_policy_id",
        "classifier_policy_sha256",
        "candidate_count",
        "unresolved_count",
        "auto_classified_count",
        "candidates",
        "unresolved_pages",
        "candidate_set_sha256",
        "unresolved_set_sha256",
        "reviewer_sample",
        "reviewer_sample_set_sha256",
        "status",
        "blocker_code",
    }
)


class WatermarkPagePrescanError(RuntimeError):
    """Fail-closed prescan error carrying only a stable public-safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str = WATERMARK_PRESCAN_INCOMPLETE) -> Never:
    raise WatermarkPagePrescanError(code) from None


def _is_int(value: object, *, minimum: int = 0) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_sha256(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _score(value: object) -> Decimal:
    if not isinstance(value, str) or _SCORE_RE.fullmatch(value) is None:
        _fail(WATERMARK_PAGE_POLICY_INVALID)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail(WATERMARK_PAGE_POLICY_INVALID)
    if parsed < 0 or parsed > 1:
        _fail(WATERMARK_PAGE_POLICY_INVALID)
    return parsed


def _canonical_sha256(value: object) -> str:
    try:
        return hashlib.sha256(canonicalize_json(value)).hexdigest()
    except (TypeError, ValueError, RecursionError):
        _fail(WATERMARK_PRESCAN_INCOMPLETE)


def _candidate_key(row: Mapping[str, object]) -> tuple[str, int]:
    alias = row.get("safe_alias")
    page_number = row.get("page_number")
    if not isinstance(alias, str) or not _is_int(page_number, minimum=1):
        _fail()
    return alias, page_number


def _validate_candidate(row: object) -> dict[str, object]:
    if not isinstance(row, Mapping) or frozenset(row) != _CANDIDATE_FIELDS:
        _fail()
    alias = row.get("safe_alias")
    match = _SAFE_ALIAS_RE.fullmatch(alias) if isinstance(alias, str) else None
    if (
        match is None
        or not _is_sha256(row.get("content_sha256"))
        or not _is_int(row.get("page_number"), minimum=1)
        or not _is_sha256(row.get("canonical_render_sha256"))
    ):
        _fail()
    _score(row.get("similarity_score"))
    return {
        field: row[field]
        for field in (
            "safe_alias",
            "content_sha256",
            "page_number",
            "canonical_render_sha256",
            "similarity_score",
        )
    }


def _validate_candidate_sequence(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail()
    rows = [_validate_candidate(row) for row in value]
    keys = [_candidate_key(row) for row in rows]
    if len(keys) != len(set(keys)) or keys != sorted(keys):
        _fail()
    return rows


def _sample_rank(
    row: Mapping[str, object],
    *,
    source_tree_fingerprint: str,
    classifier_policy_sha256: str,
) -> str:
    return _canonical_sha256(
        {
            "classifier_policy_sha256": classifier_policy_sha256,
            "source_tree_fingerprint": source_tree_fingerprint,
            "safe_alias": row["safe_alias"],
            "content_sha256": row["content_sha256"],
            "page_number": row["page_number"],
        }
    )


def select_watermark_review_sample(
    *,
    candidates: Sequence[Mapping[str, object]],
    source_tree_fingerprint: str,
    classifier_policy_sha256: str,
) -> list[dict[str, object]]:
    """Select all candidates up to five, else mandatory 00054 plus four ranks."""

    if not _is_sha256(source_tree_fingerprint) or not _is_sha256(classifier_policy_sha256):
        _fail(WATERMARK_PAGE_POLICY_INVALID)
    rows = _validate_candidate_sequence(candidates)
    mandatory = [row for row in rows if row["safe_alias"] == MANDATORY_REVIEW_ALIAS]
    if len(mandatory) != 1:
        _fail()
    if len(rows) <= REVIEW_SAMPLE_LIMIT:
        return rows
    mandatory_row = mandatory[0]
    tail = sorted(
        (row for row in rows if row is not mandatory_row),
        key=lambda row: _sample_rank(
            row,
            source_tree_fingerprint=source_tree_fingerprint,
            classifier_policy_sha256=classifier_policy_sha256,
        ),
    )[: REVIEW_SAMPLE_LIMIT - 1]
    return [mandatory_row, *tail]


def watermark_page_sanity_cap_allows(
    *,
    auto_classified_page_count: int,
    company_file_count: int,
) -> bool:
    """Apply the pinned 15% cap with integer arithmetic and no rounding."""

    if not _is_int(auto_classified_page_count) or not _is_int(company_file_count, minimum=1):
        _fail(WATERMARK_PAGE_POLICY_INVALID)
    return 100 * auto_classified_page_count <= SANITY_CAP_PERCENT * company_file_count


def _source_contract_facts(
    source_contract: Mapping[str, object],
) -> tuple[str, int, dict[str, int]]:
    if source_contract.get("schema_version") != SOURCE_CONTRACT_SCHEMA_VERSION:
        _fail()
    fingerprint = source_contract.get("source_tree_fingerprint")
    total_file_count = source_contract.get("total_file_count")
    total_bytes = source_contract.get("total_bytes")
    companies = source_contract.get("companies")
    if (
        not _is_sha256(fingerprint)
        or total_file_count != EXPECTED_TOTAL_FILE_COUNT
        or not _is_int(total_bytes)
        or total_bytes != EXPECTED_TOTAL_BYTES
        or not isinstance(companies, Sequence)
        or isinstance(companies, (str, bytes, bytearray))
        or len(companies) != 2
    ):
        _fail()

    counts: dict[str, int] = {}
    for company in companies:
        if not isinstance(company, Mapping):
            _fail()
        alias = company.get("company_alias")
        file_count = company.get("file_count")
        if not isinstance(alias, str) or not _is_int(file_count, minimum=1):
            _fail()
        if alias in counts:
            _fail()
        counts[alias] = file_count
    if counts != EXPECTED_COMPANY_FILE_COUNTS:
        _fail()
    return fingerprint, total_bytes, counts


def _page_candidate(
    *,
    page: object,
    safe_alias: str,
    content_sha256: str,
    classifier_policy_sha256: str,
) -> tuple[dict[str, object], bool]:
    if not isinstance(page, Mapping) or frozenset(page) != _PAGE_RESULT_FIELDS:
        _fail()
    if (
        not _is_int(page.get("page_number"), minimum=1)
        or page.get("native_text_token_count") != 0
        or page.get("primary_ocr_token_count") != 0
        or page.get("rescue_candidate_attempt_count") != OCR_RESCUE_CANDIDATE_COUNT
        or page.get("rescue_candidate_completed_count") != OCR_RESCUE_CANDIDATE_COUNT
        or page.get("rescue_outcome") != "no_consensus"
        or not isinstance(page.get("faint_signature_matches"), bool)
        or not _is_sha256(page.get("canonical_render_sha256"))
        or not _is_sha256(page.get("exemplar_content_sha256"))
        or not hmac.compare_digest(
            str(page.get("classifier_policy_sha256")), classifier_policy_sha256
        )
    ):
        _fail()
    score = _score(page.get("similarity_score"))
    faint_signature_matches = page["faint_signature_matches"]
    if faint_signature_matches is not (score >= _SIMILARITY_THRESHOLD):
        _fail(WATERMARK_PAGE_POLICY_INVALID)
    candidate = {
        "safe_alias": safe_alias,
        "content_sha256": content_sha256,
        "page_number": page["page_number"],
        "canonical_render_sha256": page["canonical_render_sha256"],
        "similarity_score": page["similarity_score"],
    }
    return candidate, faint_signature_matches


def _compile_file_results(
    *,
    file_results: Sequence[Mapping[str, object]],
    classifier_policy_sha256: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, int]]:
    if len(file_results) != EXPECTED_TOTAL_FILE_COUNT:
        _fail()
    candidates: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    company_file_counts = dict.fromkeys(EXPECTED_COMPANY_FILE_COUNTS, 0)
    seen_aliases: set[str] = set()
    previous_alias_key: tuple[int, int] | None = None

    for result in file_results:
        if not isinstance(result, Mapping) or frozenset(result) != _FILE_RESULT_FIELDS:
            _fail()
        company_alias = result.get("company_alias")
        safe_alias = result.get("safe_alias")
        content_sha256 = result.get("content_sha256")
        file_format = result.get("format")
        eligible_pages = result.get("eligible_pages")
        match = _SAFE_ALIAS_RE.fullmatch(safe_alias) if isinstance(safe_alias, str) else None
        if (
            match is None
            or not isinstance(company_alias, str)
            or not isinstance(safe_alias, str)
            or company_alias != match.group("company")
            or company_alias not in company_file_counts
            or not _is_sha256(content_sha256)
            or not isinstance(file_format, str)
            or file_format != match.group("extension")
            or result.get("scan_status") != "complete"
            or not isinstance(eligible_pages, Sequence)
            or isinstance(eligible_pages, (str, bytes, bytearray))
            or safe_alias in seen_aliases
        ):
            _fail()
        sequence = int(match.group("sequence"))
        alias_key = (1 if company_alias == "company-001" else 2, sequence)
        if previous_alias_key is not None and alias_key <= previous_alias_key:
            _fail()
        previous_alias_key = alias_key
        seen_aliases.add(safe_alias)
        company_file_counts[company_alias] += 1
        if eligible_pages and file_format != "pdf":
            _fail()

        page_numbers: list[int] = []
        for page in eligible_pages:
            candidate, matched = _page_candidate(
                page=page,
                safe_alias=safe_alias,
                content_sha256=content_sha256,
                classifier_policy_sha256=classifier_policy_sha256,
            )
            page_number = candidate["page_number"]
            assert isinstance(page_number, int)
            page_numbers.append(page_number)
            (candidates if matched else unresolved).append(candidate)
        if page_numbers != sorted(set(page_numbers)):
            _fail()

    if company_file_counts != EXPECTED_COMPANY_FILE_COUNTS:
        _fail()
    if not any(row["safe_alias"] == SIGNED_EXEMPLAR_ALIAS for row in candidates):
        _fail()
    return candidates, unresolved, company_file_counts


def compile_watermark_page_prescan(
    *,
    source_contract: Mapping[str, object],
    file_results: Sequence[Mapping[str, object]],
    source_tree_fingerprint_after: str,
    classifier_policy_id: str,
    classifier_policy_sha256: str,
    mandatory_candidate_alias: str,
) -> dict[str, object]:
    """Validate complete safe observations and compile one deterministic receipt."""

    if not isinstance(source_contract, Mapping):
        _fail()
    source_tree_fingerprint, total_bytes, contract_company_counts = _source_contract_facts(
        source_contract
    )
    if (
        not _is_sha256(source_tree_fingerprint_after)
        or not hmac.compare_digest(source_tree_fingerprint, source_tree_fingerprint_after)
        or classifier_policy_id != WATERMARK_CLASSIFIER_POLICY_ID
        or not _is_sha256(classifier_policy_sha256)
        or mandatory_candidate_alias != MANDATORY_REVIEW_ALIAS
    ):
        _fail()
    if not isinstance(file_results, Sequence) or isinstance(file_results, (str, bytes, bytearray)):
        _fail()

    candidates, unresolved, observed_company_counts = _compile_file_results(
        file_results=file_results,
        classifier_policy_sha256=classifier_policy_sha256,
    )
    if observed_company_counts != contract_company_counts:
        _fail()
    mandatory_count = sum(row["safe_alias"] == mandatory_candidate_alias for row in candidates)
    if mandatory_count != 1:
        _fail()

    for company_alias, file_count in contract_company_counts.items():
        page_count = sum(
            str(row["safe_alias"]).startswith(f"{company_alias}-") for row in candidates
        )
        if not watermark_page_sanity_cap_allows(
            auto_classified_page_count=page_count,
            company_file_count=file_count,
        ):
            _fail(WATERMARK_PAGE_SANITY_CAP_EXCEEDED)

    candidate_set_sha256 = _canonical_sha256(candidates)
    unresolved_set_sha256 = _canonical_sha256(unresolved)
    reviewer_sample = select_watermark_review_sample(
        candidates=candidates,
        source_tree_fingerprint=source_tree_fingerprint,
        classifier_policy_sha256=classifier_policy_sha256,
    )
    blocked = bool(unresolved)
    report: dict[str, object] = {
        "schema_version": WATERMARK_PAGE_PRESCAN_SCHEMA_VERSION,
        "source_contract_schema_version": SOURCE_CONTRACT_SCHEMA_VERSION,
        "source_tree_fingerprint": source_tree_fingerprint,
        "total_file_count": EXPECTED_TOTAL_FILE_COUNT,
        "total_bytes": total_bytes,
        "company_file_counts": dict(contract_company_counts),
        "scanned_file_count": len(file_results),
        "classifier_policy_id": classifier_policy_id,
        "classifier_policy_sha256": classifier_policy_sha256,
        "candidate_count": len(candidates),
        "unresolved_count": len(unresolved),
        "auto_classified_count": len(candidates),
        "candidates": candidates,
        "unresolved_pages": unresolved,
        "candidate_set_sha256": candidate_set_sha256,
        "unresolved_set_sha256": unresolved_set_sha256,
        "reviewer_sample": reviewer_sample,
        "reviewer_sample_set_sha256": _canonical_sha256(reviewer_sample),
        "status": "blocked_pending_reviewer" if blocked else "ready_for_batch_review",
        "blocker_code": WATERMARK_PRESCAN_INCOMPLETE if blocked else None,
    }
    return validate_watermark_page_prescan(report=report)


def validate_watermark_page_prescan(*, report: Mapping[str, object]) -> dict[str, object]:
    """Validate and copy a safe deterministic prescan receipt."""

    if not isinstance(report, Mapping) or frozenset(report) != _REPORT_FIELDS:
        _fail()
    if (
        report.get("schema_version") != WATERMARK_PAGE_PRESCAN_SCHEMA_VERSION
        or report.get("source_contract_schema_version") != SOURCE_CONTRACT_SCHEMA_VERSION
        or not _is_sha256(report.get("source_tree_fingerprint"))
        or report.get("total_file_count") != EXPECTED_TOTAL_FILE_COUNT
        or report.get("total_bytes") != EXPECTED_TOTAL_BYTES
        or report.get("company_file_counts") != EXPECTED_COMPANY_FILE_COUNTS
        or report.get("scanned_file_count") != EXPECTED_TOTAL_FILE_COUNT
        or report.get("classifier_policy_id") != WATERMARK_CLASSIFIER_POLICY_ID
        or not _is_sha256(report.get("classifier_policy_sha256"))
    ):
        _fail()

    candidates = _validate_candidate_sequence(report.get("candidates"))
    unresolved = _validate_candidate_sequence(report.get("unresolved_pages"))
    if (
        report.get("candidate_count") != len(candidates)
        or report.get("auto_classified_count") != len(candidates)
        or report.get("unresolved_count") != len(unresolved)
        or report.get("candidate_set_sha256") != _canonical_sha256(candidates)
        or report.get("unresolved_set_sha256") != _canonical_sha256(unresolved)
    ):
        _fail()
    if not any(row["safe_alias"] == MANDATORY_REVIEW_ALIAS for row in candidates):
        _fail()
    if {_candidate_key(row) for row in candidates}.intersection(
        _candidate_key(row) for row in unresolved
    ):
        _fail()
    if any(_score(row["similarity_score"]) < _SIMILARITY_THRESHOLD for row in candidates):
        _fail()
    if any(_score(row["similarity_score"]) >= _SIMILARITY_THRESHOLD for row in unresolved):
        _fail()

    for company_alias, file_count in EXPECTED_COMPANY_FILE_COUNTS.items():
        page_count = sum(
            str(row["safe_alias"]).startswith(f"{company_alias}-") for row in candidates
        )
        if not watermark_page_sanity_cap_allows(
            auto_classified_page_count=page_count,
            company_file_count=file_count,
        ):
            _fail(WATERMARK_PAGE_SANITY_CAP_EXCEEDED)

    source_tree_fingerprint = report["source_tree_fingerprint"]
    classifier_policy_sha256 = report["classifier_policy_sha256"]
    assert isinstance(source_tree_fingerprint, str)
    assert isinstance(classifier_policy_sha256, str)
    expected_sample = select_watermark_review_sample(
        candidates=candidates,
        source_tree_fingerprint=source_tree_fingerprint,
        classifier_policy_sha256=classifier_policy_sha256,
    )
    if report.get("reviewer_sample") != expected_sample or report.get(
        "reviewer_sample_set_sha256"
    ) != _canonical_sha256(expected_sample):
        _fail()

    blocked = bool(unresolved)
    if report.get("status") != (
        "blocked_pending_reviewer" if blocked else "ready_for_batch_review"
    ) or report.get("blocker_code") != (WATERMARK_PRESCAN_INCOMPLETE if blocked else None):
        _fail()
    return copy.deepcopy(dict(report))


__all__ = [
    "MANDATORY_REVIEW_ALIAS",
    "SANITY_CAP_PERCENT",
    "WATERMARK_CLASSIFIER_POLICY_ID",
    "WATERMARK_PAGE_POLICY_INVALID",
    "WATERMARK_PAGE_PRESCAN_SCHEMA_VERSION",
    "WATERMARK_PAGE_SANITY_CAP_EXCEEDED",
    "WATERMARK_PRESCAN_INCOMPLETE",
    "WatermarkPagePrescanError",
    "compile_watermark_page_prescan",
    "select_watermark_review_sample",
    "validate_watermark_page_prescan",
    "watermark_page_sanity_cap_allows",
]
