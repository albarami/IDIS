"""RED contracts for the hermetic, all-267 watermark-page prescan.

The fixtures contain only generated aliases and fictional hashes.  The future
compiler consumes safe, already-read observations; it has no API, database,
Redis, object-store, audit, or network authority.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import socket
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from idis.runtime.governance_envelopes import canonicalize_json

PRESCAN_SCHEMA_VERSION = "watermark-page-prescan-v1"
POLICY_VERSION = "exemplar-watermark-page-v1"
POLICY_SHA256 = "1" * 64
SOURCE_TREE_FINGERPRINT = "2" * 64
MANDATORY_ALIAS = "company-002-document-00054.pdf"
EXEMPLAR_ALIAS = "company-002-document-00053.pdf"
PRIVATE_PATH_CANARY = r"C:\fictional-private-room\private-watermark-page.pdf"
PRIVATE_NAME_CANARY = "fictional-private-watermark-page.pdf"
PRIVATE_TEXT_CANARY = "fictional recipient address and confidential timestamp"


def _target() -> ModuleType:
    name = "idis.evaluation.watermark_page_prescan"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        missing = str(exc)
    pytest.fail(f"TASK4_WATERMARK_PRESCAN_API_MISSING: {name}: {missing}", pytrace=False)


def _required_attr(name: str) -> Any:
    module = _target()
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(
            f"TASK4_WATERMARK_PRESCAN_API_MISSING: {module.__name__}.{name}",
            pytrace=False,
        )
    return value


def _error_type() -> type[Exception]:
    value = _required_attr("WatermarkPagePrescanError")
    assert isinstance(value, type) and issubclass(value, Exception)
    return value


def _code(error: BaseException) -> str | None:
    value = getattr(error, "code", None)
    return str(value) if value is not None else None


def _sha256(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


def _alias(company: int, sequence: int, extension: str = "pdf") -> str:
    return f"company-{company:03d}-document-{sequence:05d}.{extension}"


def _source_contract() -> dict[str, object]:
    return {
        "schema_version": "slice100-two-company-source-contract-v1",
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "total_file_count": 267,
        "total_bytes": 858_207_454,
        "companies": [
            {"company_alias": "company-001", "file_count": 58},
            {"company_alias": "company-002", "file_count": 209},
        ],
    }


def _page(
    *,
    page_number: int,
    score: str,
    render_digit: str,
    faint_signature_matches: bool,
    rescue_completed_count: int = 24,
) -> dict[str, object]:
    return {
        "page_number": page_number,
        "native_text_token_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_candidate_attempt_count": 24,
        "rescue_candidate_completed_count": rescue_completed_count,
        "rescue_outcome": "no_consensus",
        "faint_signature_matches": faint_signature_matches,
        "similarity_score": score,
        "canonical_render_sha256": render_digit * 64,
        "exemplar_content_sha256": "3" * 64,
        "classifier_policy_sha256": POLICY_SHA256,
    }


def _result(
    *,
    company: int,
    sequence: int,
    content_digit: str,
    pages: Sequence[Mapping[str, object]] = (),
    extension: str = "pdf",
) -> dict[str, object]:
    return {
        "company_alias": f"company-{company:03d}",
        "safe_alias": _alias(company, sequence, extension),
        "content_sha256": content_digit * 64,
        "format": extension,
        "scan_status": "complete",
        "eligible_pages": [dict(page) for page in pages],
    }


def _file_results() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sequence in range(1, 59):
        rows.append(
            _result(
                company=1,
                sequence=sequence,
                content_digit=f"{sequence % 16:x}",
                extension="xlsx" if sequence % 11 == 0 else "pdf",
            )
        )
    for sequence in range(1, 210):
        pages: tuple[Mapping[str, object], ...] = ()
        if sequence == 53:
            pages = (
                _page(
                    page_number=1,
                    score="1.000000",
                    render_digit="a",
                    faint_signature_matches=True,
                ),
            )
        elif sequence == 54:
            pages = (
                _page(
                    page_number=1,
                    score="0.819404",
                    render_digit="b",
                    faint_signature_matches=True,
                ),
            )
        elif sequence == 105:
            pages = (
                _page(
                    page_number=4,
                    score="0.134971",
                    render_digit="c",
                    faint_signature_matches=False,
                ),
                _page(
                    page_number=7,
                    score="0.239372",
                    render_digit="d",
                    faint_signature_matches=False,
                ),
            )
        rows.append(
            _result(
                company=2,
                sequence=sequence,
                content_digit=f"{(sequence + 1) % 16:x}",
                pages=pages,
                extension="mp4" if sequence % 31 == 0 else "pdf",
            )
        )
    return rows


def _compile(
    *,
    source_contract: Mapping[str, object] | None = None,
    file_results: Sequence[Mapping[str, object]] | None = None,
    source_tree_fingerprint_after: str = SOURCE_TREE_FINGERPRINT,
) -> dict[str, object]:
    _required_attr("WATERMARK_PAGE_PRESCAN_SCHEMA_VERSION")
    return _required_attr("compile_watermark_page_prescan")(
        source_contract=source_contract or _source_contract(),
        file_results=file_results or _file_results(),
        source_tree_fingerprint_after=source_tree_fingerprint_after,
        classifier_policy_id=POLICY_VERSION,
        classifier_policy_sha256=POLICY_SHA256,
        mandatory_candidate_alias=MANDATORY_ALIAS,
    )


def _candidate_identity(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "safe_alias": row["safe_alias"],
        "content_sha256": row["content_sha256"],
        "page_number": row["page_number"],
        "canonical_render_sha256": row["canonical_render_sha256"],
        "similarity_score": row["similarity_score"],
    }


def _rank(
    row: Mapping[str, object],
    *,
    source_tree_fingerprint: str = SOURCE_TREE_FINGERPRINT,
    policy_sha256: str = POLICY_SHA256,
) -> str:
    return _sha256(
        {
            "classifier_policy_sha256": policy_sha256,
            "source_tree_fingerprint": source_tree_fingerprint,
            "safe_alias": row["safe_alias"],
            "content_sha256": row["content_sha256"],
            "page_number": row["page_number"],
        }
    )


def test_prescan_contract_is_exact_all_267_and_reports_complete_ordered_sets() -> None:
    assert _required_attr("WATERMARK_PAGE_PRESCAN_SCHEMA_VERSION") == PRESCAN_SCHEMA_VERSION
    report = _compile()

    assert report["schema_version"] == PRESCAN_SCHEMA_VERSION
    assert report["source_tree_fingerprint"] == SOURCE_TREE_FINGERPRINT
    assert report["total_file_count"] == 267
    assert report["company_file_counts"] == {
        "company-001": 58,
        "company-002": 209,
    }
    assert report["scanned_file_count"] == 267
    assert report["candidate_count"] == 2
    assert report["unresolved_count"] == 2
    assert [row["safe_alias"] for row in report["candidates"]] == [
        EXEMPLAR_ALIAS,
        MANDATORY_ALIAS,
    ]
    assert [(row["safe_alias"], row["page_number"]) for row in report["unresolved_pages"]] == [
        ("company-002-document-00105.pdf", 4),
        ("company-002-document-00105.pdf", 7),
    ]
    assert report["status"] == "blocked_pending_reviewer"
    assert report["blocker_code"] == "WATERMARK_PRESCAN_INCOMPLETE"
    assert report["candidate_set_sha256"] == _sha256(
        [_candidate_identity(row) for row in report["candidates"]]
    )
    assert report["unresolved_set_sha256"] == _sha256(
        [_candidate_identity(row) for row in report["unresolved_pages"]]
    )


def test_prescan_compiler_has_no_external_or_mutating_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline prescan attempted forbidden external or write access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for method in ("write_text", "write_bytes", "unlink", "rename", "replace", "touch"):
        monkeypatch.setattr(Path, method, forbidden)

    report = _compile()
    assert report["scanned_file_count"] == 267
    assert "api" not in report
    assert "database" not in report
    assert "redis" not in report
    assert "object_store" not in report
    assert "audit" not in report


def test_prescan_sample_rule_uses_all_at_most_five() -> None:
    candidates = [_candidate_identity(row) for row in _compile()["candidates"]]
    selected = _required_attr("select_watermark_review_sample")(
        candidates=candidates,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        classifier_policy_sha256=POLICY_SHA256,
    )
    assert selected == candidates
    assert any(row["safe_alias"] == MANDATORY_ALIAS for row in selected)


def test_prescan_sample_rule_uses_mandatory_00054_plus_four_lowest_hash_ranks() -> None:
    candidates = [
        {
            "safe_alias": _alias(2, sequence),
            "content_sha256": f"{sequence % 16:x}" * 64,
            "page_number": 1,
            "canonical_render_sha256": f"{(sequence + 1) % 16:x}" * 64,
            "similarity_score": "0.900000",
        }
        for sequence in range(49, 57)
    ]
    mandatory = next(row for row in candidates if row["safe_alias"] == MANDATORY_ALIAS)
    expected_tail = sorted((row for row in candidates if row is not mandatory), key=_rank)[:4]

    selected = _required_attr("select_watermark_review_sample")(
        candidates=candidates,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        classifier_policy_sha256=POLICY_SHA256,
    )

    assert selected == [mandatory, *expected_tail]
    assert len({_rank(row) for row in selected}) == 5


@pytest.mark.parametrize(
    ("auto_classified", "company_file_count", "allowed"),
    [(8, 58, True), (9, 58, False), (31, 209, True), (32, 209, False)],
)
def test_prescan_sanity_cap_is_exact_integer_15_percent(
    auto_classified: int,
    company_file_count: int,
    allowed: bool,
) -> None:
    assert (
        _required_attr("watermark_page_sanity_cap_allows")(
            auto_classified_page_count=auto_classified,
            company_file_count=company_file_count,
        )
        is allowed
    )
    assert allowed is (100 * auto_classified <= 15 * company_file_count)


@pytest.mark.parametrize("failure", ["count", "mutation", "ocr_incomplete", "missing_00054"])
def test_prescan_fails_closed_on_incomplete_or_mutated_source_proof(failure: str) -> None:
    source_contract = _source_contract()
    rows = _file_results()
    after = SOURCE_TREE_FINGERPRINT
    if failure == "count":
        rows.pop()
    elif failure == "mutation":
        after = "f" * 64
    elif failure == "ocr_incomplete":
        eligible = rows[58 + 52]["eligible_pages"]
        assert isinstance(eligible, list)
        eligible[0]["rescue_candidate_completed_count"] = 23
    else:
        rows[58 + 53]["eligible_pages"] = []

    with pytest.raises(_error_type()) as caught:
        _compile(
            source_contract=source_contract,
            file_results=rows,
            source_tree_fingerprint_after=after,
        )
    assert _code(caught.value) == "WATERMARK_PRESCAN_INCOMPLETE"


def test_below_threshold_pages_remain_complete_safe_blockers_not_auto_classified() -> None:
    report = _compile()
    assert report["status"] == "blocked_pending_reviewer"
    assert report["blocker_code"] == "WATERMARK_PRESCAN_INCOMPLETE"
    assert report["auto_classified_count"] == 2
    assert all(row["similarity_score"] < "0.800000" for row in report["unresolved_pages"])
    assert not {
        (row["safe_alias"], row["page_number"]) for row in report["candidates"]
    }.intersection((row["safe_alias"], row["page_number"]) for row in report["unresolved_pages"])


def test_prescan_report_is_safe_only_and_deterministic() -> None:
    first = _compile()
    second = _compile()
    assert first == second
    serialized = json.dumps(first, sort_keys=True)
    for canary in (
        PRIVATE_PATH_CANARY,
        PRIVATE_NAME_CANARY,
        PRIVATE_TEXT_CANARY,
        "source_path",
        "absolute_path",
        "native_text",
        "ocr_text",
        "bitmap",
        "signature_base64",
    ):
        assert canary not in serialized

    validator: Callable[..., object] = _required_attr("validate_watermark_page_prescan")
    assert validator(report=copy.deepcopy(first)) == first
