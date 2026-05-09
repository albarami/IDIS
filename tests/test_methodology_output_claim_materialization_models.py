"""Tests for Slice 6 neutral-output claim materialization models."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from idis.models.claim import ClaimType, Materiality
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    MaterializedClaimType,
    MaterializedClaimValueStruct,
    RunScopedMaterializedClaim,
    generate_methodology_materialized_claim_id,
)
from idis.models.value_structs import ValueStructType, parse_value_struct

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _source_ref() -> MaterializedClaimSourceRef:
    return MaterializedClaimSourceRef(
        document_id="doc-financial-model",
        source_span_id="span-001",
        locator={"sheet": "P&L", "cell": "B12"},
    )


def _value_struct() -> MaterializedClaimValueStruct:
    return MaterializedClaimValueStruct(
        type=ValueStructType.MONETARY,
        value=Decimal("10000000"),
        unit="USD",
        currency="USD",
        time_window="FY2024",
        source_answer_type="numeric",
    )


def _claim() -> RunScopedMaterializedClaim:
    return RunScopedMaterializedClaim(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_text="revenue: 10000000 USD",
        claim_type=MaterializedClaimType.FINANCIAL_METRIC,
        value_struct=_value_struct(),
        materiality=Materiality.MEDIUM,
        source_refs=[_source_ref()],
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        coverage_record_id="mcr_revenue_quality",
        extraction_task_id="et_revenue_quality",
        extraction_output_id="meo_revenue_quality",
        status="materialized_unverified",
    )


def test_slice6_inventory_documents_reuse_decisions() -> None:
    """Slice 6 reuses safe concepts and avoids semantically wrong ones."""
    assert ClaimType.PRIMARY.value == "primary"
    assert ClaimType.DERIVED.value == "derived"
    assert "primary" not in {claim_type.value for claim_type in MaterializedClaimType}
    assert Materiality.MEDIUM.value == "MEDIUM"

    parsed = parse_value_struct(
        {
            "type": "monetary",
            "amount": "10000000",
            "currency": "USD",
            "time_window": {"label": "FY2024"},
        }
    )

    assert parsed.model_dump(mode="json")["type"] == "monetary"


def test_run_scoped_materialized_claim_accepts_semantic_claim_type() -> None:
    claim = _claim()

    assert claim.claim_id.startswith("claim_mth_")
    assert claim.claim_type == MaterializedClaimType.FINANCIAL_METRIC
    assert claim.value_struct.source_answer_type == "numeric"
    assert claim.materiality == Materiality.MEDIUM


def test_run_scoped_materialized_claim_rejects_lifecycle_claim_type_primary() -> None:
    payload = _claim().model_dump(mode="python")
    payload["claim_id"] = None
    payload["claim_type"] = "primary"

    with pytest.raises(ValidationError):
        RunScopedMaterializedClaim.model_validate(payload)


def test_materialized_claim_requires_safe_source_refs_without_raw_text() -> None:
    payload = _claim().model_dump(mode="python")
    payload["claim_id"] = None
    payload["source_refs"] = [
        {
            "document_id": "doc-financial-model",
            "source_span_id": "span-001",
            "locator": {"sheet": "P&L", "cell": "B12"},
            "text_excerpt": "Revenue was $10M in FY2024.",
        }
    ]

    with pytest.raises(ValidationError):
        RunScopedMaterializedClaim.model_validate(payload)


def test_source_ref_rejects_path_or_uri_like_ids() -> None:
    unsafe_values = [
        "C:\\secret\\file.pdf",
        "C:/secret/file.pdf",
        "/mnt/secret/file.pdf",
        "\\\\server\\share\\file.pdf",
        "file://secret/file.pdf",
        "s3://bucket/file.pdf",
        "http://example.com/file.pdf",
        "https://example.com/file.pdf",
    ]

    for unsafe_value in unsafe_values:
        with pytest.raises(ValidationError):
            MaterializedClaimSourceRef(
                document_id=unsafe_value,
                source_span_id="span-001",
                locator={"sheet": "P&L", "cell": "B12"},
            )
        with pytest.raises(ValidationError):
            MaterializedClaimSourceRef(
                document_id="doc-financial-model",
                source_span_id=unsafe_value,
                locator={"sheet": "P&L", "cell": "B12"},
            )


def test_source_ref_rejects_raw_or_location_metadata_inside_locator() -> None:
    unsafe_locators = [
        {"text": "Revenue was $10M in FY2024."},
        {"raw_text": "Revenue was $10M in FY2024."},
        {"text_excerpt": "Revenue was $10M in FY2024."},
        {"document_name": "financial Due Diligence.xlsx"},
        {"path": "C:/secret/file.pdf"},
        {"uri": "s3://bucket/file.pdf"},
        {"nested": {"uri": "https://example.com/file.pdf"}},
    ]

    for locator in unsafe_locators:
        with pytest.raises(ValidationError):
            MaterializedClaimSourceRef(
                document_id="doc-financial-model",
                source_span_id="span-001",
                locator=locator,
            )


def test_materialized_value_struct_reuses_typed_value_validation() -> None:
    value_struct = _value_struct()

    assert value_struct.to_value_struct().model_dump(mode="json") == {
        "type": "monetary",
        "amount": "10000000",
        "currency": "USD",
        "as_of": None,
        "time_window": {"label": "FY2024", "start_date": None, "end_date": None},
    }


def test_numeric_financial_value_struct_requires_currency_unit_and_window() -> None:
    with pytest.raises(ValidationError):
        MaterializedClaimValueStruct(
            type=ValueStructType.MONETARY,
            value=Decimal("10000000"),
            source_answer_type="numeric",
        )


def test_deterministic_claim_id_is_stable_and_changes_with_value() -> None:
    claim = _claim()
    same_id = generate_methodology_materialized_claim_id(
        tenant_id=claim.tenant_id,
        deal_id=claim.deal_id,
        run_id=claim.run_id,
        extraction_output_id=claim.extraction_output_id,
        extraction_task_id=claim.extraction_task_id,
        methodology_question_id=claim.methodology_question_id,
        coverage_record_id=claim.coverage_record_id,
        source_refs=claim.source_refs,
        value_struct=claim.value_struct,
    )
    changed_id = generate_methodology_materialized_claim_id(
        tenant_id=claim.tenant_id,
        deal_id=claim.deal_id,
        run_id=claim.run_id,
        extraction_output_id=claim.extraction_output_id,
        extraction_task_id=claim.extraction_task_id,
        methodology_question_id=claim.methodology_question_id,
        coverage_record_id=claim.coverage_record_id,
        source_refs=claim.source_refs,
        value_struct=claim.value_struct.model_copy(update={"value": Decimal("11000000")}),
    )

    assert claim.claim_id == same_id
    assert changed_id != same_id
