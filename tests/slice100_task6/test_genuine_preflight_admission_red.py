"""RED contracts for genuine category-and-span methodology admission."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import idis.methodology.category_mapping as category_mapping_module
import idis.pipeline.worker as worker_module
from idis.methodology.category_mapping import (
    METHODOLOGY_CDD_CATEGORY_MAP_DRIFT,
    METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE,
    CommercialDDCategoryMapError,
    load_commercial_dd_question_category_map,
)
from idis.methodology.importers.cdd_json import import_cdd_template
from idis.methodology.models import MethodologyRegistry, MethodologyType, MethodologyVersion
from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)
from idis.models.document_preflight import (
    DocumentPreflightReason,
    DocumentPreflightResult,
    RunDocumentPreflightDecision,
)
from idis.models.extraction_task import ExtractionTaskStatus, SourceSpanReference
from idis.models.methodology_coverage import MethodologyCoverageRecord
from idis.services.extraction.task_planner import SafePreflightClassificationInput
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService
from idis.services.runs.methodology_coverage_init import DEFAULT_METHODOLOGY_REGISTRY_PATH
from idis.services.runs.methodology_extraction_task_planning import (
    MethodologyExtractionTaskPlanningInputError,
    MethodologyTaskCoverageError,
    _classifications_from_preflight_summary,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.slice100_task5.test_durable_selection_coverage_red import _registry
from tests.slice100_task6._support import (
    DEAL_ID,
    LEASE_OWNER_ID,
    RUN_ID,
    SELECTION_ID,
    TENANT_ID,
    require_symbol,
)

_CUSTOMER_DOCUMENT_ID = "31313131-3131-4313-8313-313131313131"
_FINANCIAL_DOCUMENT_ID = "32323232-3232-4323-8323-323232323232"
_CUSTOMER_TEXT = "Fictional customer contract renewal schedule."
_FINANCIAL_TEXT = "Fictional financial model revenue schedule."
_MEDIA_DOCUMENT_ID = "37373737-3737-4373-8373-373737373737"
_MALFORMED_SECONDARY_SHAPES: tuple[tuple[str, object], ...] = (
    ("secondary_cdd_categories", ["customer_evidence", "customer_evidence"]),
    ("secondary_cdd_categories", ["sales_pipeline", "customer_evidence"]),
    ("secondary_cdd_categories", ["not_a_cdd_category"]),
    ("secondary_fdd_categories", ["pl_support", "pl_support"]),
    ("secondary_fdd_categories", ["pl_support", "cash_flow_support"]),
    ("secondary_fdd_categories", ["not_an_fdd_category"]),
)
_INVALID_SECONDARY_SHAPES: tuple[tuple[str, object], ...] = (
    ("secondary_cdd_categories", None),
    ("secondary_fdd_categories", None),
    *_MALFORMED_SECONDARY_SHAPES,
)


def _span(*, document_id: str, text: str) -> dict[str, Any]:
    return {
        "span_id": (
            "33333333-3333-4333-8333-333333333333"
            if document_id == _CUSTOMER_DOCUMENT_ID
            else "34343434-3434-4343-8343-343434343434"
        ),
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "document_id": document_id,
        "span_type": "PAGE_TEXT",
        "locator": {"page": 1},
        "text_excerpt": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _document(*, document_id: str, filename: str, text: str) -> dict[str, Any]:
    suffix = filename.rsplit(".", 1)[-1].upper()
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "document_id": document_id,
        "doc_id": f"artifact-{document_id}",
        "doc_type": suffix,
        "parse_status": "PARSED",
        "document_name": filename,
        "sha256": "a" * 64,
        "uri": f"safe/{filename}",
        "metadata": {
            "parser_doc_type": suffix,
            "detected_format": suffix,
            "parser_runtime_status": "completed",
            "span_count": 1,
            "parse_error_codes": [],
            "parse_warning_codes": [],
        },
        "source_metadata": {},
        "spans": [_span(document_id=document_id, text=text)],
    }


def _skipped_document_id(state: str) -> str:
    return (
        "35353535-3535-4353-8353-353535353535"
        if state == "blocked"
        else "36363636-3636-4363-8363-363636363636"
    )


def _real_preflight_result(*, skipped_state: str | None = None) -> DocumentPreflightResult:
    corpus = [
        _document(
            document_id=_CUSTOMER_DOCUMENT_ID,
            filename="fictional customer contract.docx",
            text=_CUSTOMER_TEXT,
        ),
        _document(
            document_id=_FINANCIAL_DOCUMENT_ID,
            filename="fictional financial model.xlsx",
            text=_FINANCIAL_TEXT,
        ),
    ]
    verifier: Any = None
    if skipped_state is not None:
        assert skipped_state in {"blocked", "excluded"}
        skipped_document = _document(
            document_id=_skipped_document_id(skipped_state),
            filename=f"fictional {skipped_state} source.pdf",
            text="Fictional skipped source.",
        )
        skipped_document["spans"] = []
        skipped_document["metadata"]["span_count"] = 0
        if skipped_state == "excluded":
            skipped_document["metadata"]["document_no_substantive_content_receipt"] = {
                "schema_version": "fixture-v1"
            }

            def verified_no_substantive_content(_document: object) -> bool:
                return True

            verifier = verified_no_substantive_content
        corpus.append(skipped_document)

    result, eligible = InMemoryRunDocumentPreflightService(
        no_substantive_content_receipt_verifier=verifier
    ).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=corpus,
    )
    assert len(eligible) == 2
    return result


def _real_preflight_summary(*, skipped_state: str | None = None) -> dict[str, Any]:
    return _real_preflight_result(skipped_state=skipped_state).to_run_step_summary()


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timed_media_document_with_opposed_span_ids() -> dict[str, Any]:
    timed_evidence: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    for span_id, start_ms, text in (
        ("ffffffff-ffff-4fff-8fff-ffffffffffff", 0, "Fictional first segment."),
        ("00000000-0000-4000-8000-000000000001", 1_000, "Fictional second segment."),
    ):
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        locator = {
            "source": "media_transcript",
            "start_ms": start_ms,
            "end_ms": start_ms + 900,
        }
        spans.append(
            {
                "span_id": span_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "document_id": _MEDIA_DOCUMENT_ID,
                "span_type": "TIMECODE",
                "locator": locator,
                "text_excerpt": text,
                "content_hash": content_hash,
            }
        )
        timed_evidence.append({**locator, "content_hash": content_hash})
    schedule = [0, 500, 1_500, 2_500, 2_999]
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "document_id": _MEDIA_DOCUMENT_ID,
        "doc_type": "VIDEO",
        "parse_status": "PARSED",
        "document_name": "fictional-timed-evidence.mp4",
        "metadata": {
            "parser_doc_type": "MEDIA",
            "detected_format": "MEDIA",
            "parser_runtime_status": "completed",
            "span_count": len(spans),
            "parse_error_codes": [],
            "parse_warning_codes": [],
            "media_transcription_performed": True,
            "media_frame_ocr_performed": True,
            "media_stt_receipt": {
                "schema_version": "media-stt-receipt-v1",
                "status": "completed",
                "segment_count": len(timed_evidence),
                "evidence_span_count": len(timed_evidence),
                "evidence_sha256": _digest(timed_evidence),
            },
            "media_frame_ocr_receipt": {
                "schema_version": "media-frame-ocr-receipt-v1",
                "status": "completed",
                "sampling_policy": "periodic-plus-scenes-v1",
                "scene_threshold": 0.20,
                "max_sample_count": 600,
                "max_frame_width": 1_920,
                "max_frame_height": 1_080,
                "duration_ms": 3_000,
                "scheduled_sample_count": len(schedule),
                "completed_sample_count": len(schedule),
                "nonempty_sample_count": 0,
                "evidence_span_count": 0,
                "schedule_sha256": _digest(schedule),
                "evidence_sha256": _digest([]),
                "confidence_count": 0,
                "mean_confidence": None,
                "minimum_confidence": None,
                "maximum_confidence": None,
            },
        },
        "spans": spans,
    }


def _classification(summary: dict[str, Any], document_id: str) -> dict[str, Any]:
    return next(item for item in summary["classifications"] if item["document_id"] == document_id)


def _decision_payload() -> dict[str, Any]:
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "document_id": _CUSTOMER_DOCUMENT_ID,
        "classification_id": "dc_genuine_preflight_fixture",
        "support_status": DocumentSupportStatus.SUPPORTED,
        "triage_status": DocumentTriageStatus.READY,
        "usable_for_methodology_extraction": True,
        "reason": DocumentPreflightReason.READY,
        "reason_codes": ["supported"],
        "warning_codes": [],
        "source_spans": [
            {
                "span_id": "33333333-3333-4333-8333-333333333333",
                "document_id": _CUSTOMER_DOCUMENT_ID,
                "locator": {"page": 1},
                "span_type": "PAGE_TEXT",
                "content_hash": hashlib.sha256(_CUSTOMER_TEXT.encode("utf-8")).hexdigest(),
            }
        ],
        "fdd_category": FddDocumentCategory.CUSTOMER_CONTRACT,
        "cdd_category": CddDocumentCategory.COMMERCIAL_CONTRACTS,
        "methodology_target_areas": ["customer contracts"],
    }


def test_real_customer_contract_preflight_preserves_secondary_categories_in_safe_json() -> None:
    summary = _real_preflight_summary()
    classification = _classification(summary, _CUSTOMER_DOCUMENT_ID)

    assert classification["secondary_fdd_categories"] == []
    assert classification["secondary_cdd_categories"] == ["customer_evidence"]
    assert "evidence_tags" not in str(summary)
    assert "text_excerpt" not in str(summary)


def test_real_financial_model_preflight_preserves_secondary_categories_in_safe_json() -> None:
    summary = _real_preflight_summary()
    classification = _classification(summary, _FINANCIAL_DOCUMENT_ID)

    assert classification["secondary_fdd_categories"] == [
        "cash_flow_support",
        "pl_support",
    ]
    assert classification["secondary_cdd_categories"] == ["pricing_unit_economics"]
    assert "evidence_tags" not in str(summary)
    assert "text_excerpt" not in str(summary)


def test_run_preflight_decision_requires_typed_secondary_arrays() -> None:
    payload = _decision_payload()

    with pytest.raises(ValidationError):
        RunDocumentPreflightDecision.model_validate(payload)

    payload["secondary_fdd_categories"] = []
    payload["secondary_cdd_categories"] = [CddDocumentCategory.CUSTOMER_EVIDENCE]
    decision = RunDocumentPreflightDecision.model_validate(payload)
    assert decision.secondary_fdd_categories == []
    assert decision.secondary_cdd_categories == [CddDocumentCategory.CUSTOMER_EVIDENCE]


def test_safe_preflight_classification_input_requires_typed_secondary_arrays() -> None:
    classification = SafePreflightClassificationInput(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_id=_CUSTOMER_DOCUMENT_ID,
        classification_id="dc_genuine_preflight_fixture",
        fdd_category=FddDocumentCategory.CUSTOMER_CONTRACT,
        cdd_category=CddDocumentCategory.COMMERCIAL_CONTRACTS,
        secondary_fdd_categories=(),
        secondary_cdd_categories=(CddDocumentCategory.CUSTOMER_EVIDENCE,),
        support_status=DocumentSupportStatus.SUPPORTED,
        triage_status=DocumentTriageStatus.READY,
        usable_for_methodology_extraction=True,
        methodology_target_areas=["customer contracts"],
        reason_codes=["supported"],
    )

    assert classification.secondary_fdd_categories == ()
    assert classification.secondary_cdd_categories == (CddDocumentCategory.CUSTOMER_EVIDENCE,)


@pytest.mark.parametrize(
    ("field", "value"),
    _INVALID_SECONDARY_SHAPES,
)
def test_strict_safe_summary_rejects_missing_duplicate_noncanonical_or_invalid_secondaries(
    field: str,
    value: object,
) -> None:
    summary = _real_preflight_summary()
    classification = _classification(summary, _CUSTOMER_DOCUMENT_ID)
    if value is None:
        classification.pop(field, None)
    else:
        classification[field] = value

    with pytest.raises(MethodologyExtractionTaskPlanningInputError):
        _classifications_from_preflight_summary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            document_preflight_summary=summary,
        )


@pytest.mark.parametrize("state", ["blocked", "excluded"])
@pytest.mark.parametrize(("field", "value"), _INVALID_SECONDARY_SHAPES)
def test_strict_decoder_validates_secondaries_before_skipping_classification(
    state: str,
    field: str,
    value: object,
) -> None:
    summary = _real_preflight_summary(skipped_state=state)
    raw = _classification(summary, _skipped_document_id(state))
    if value is None:
        raw.pop(field)
    else:
        raw[field] = value

    with pytest.raises(MethodologyExtractionTaskPlanningInputError):
        _classifications_from_preflight_summary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            document_preflight_summary=summary,
        )


@pytest.mark.parametrize("state", ["blocked", "excluded"])
def test_legacy_decoder_allows_missing_secondaries_on_skipped_classification(
    state: str,
) -> None:
    planning_module = __import__(
        "idis.services.runs.methodology_extraction_task_planning",
        fromlist=["_legacy_classifications_from_preflight_summary"],
    )
    summary = _real_preflight_summary(skipped_state=state)
    raw = _classification(summary, _skipped_document_id(state))
    raw.pop("secondary_fdd_categories")
    raw.pop("secondary_cdd_categories")

    reconstructed = planning_module._legacy_classifications_from_preflight_summary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_preflight_summary=summary,
    )

    assert {item.document_id for item in reconstructed} == {
        _CUSTOMER_DOCUMENT_ID,
        _FINANCIAL_DOCUMENT_ID,
    }


@pytest.mark.parametrize("state", ["blocked", "excluded"])
@pytest.mark.parametrize(("field", "value"), _MALFORMED_SECONDARY_SHAPES)
def test_legacy_decoder_rejects_present_malformed_secondaries_before_skip(
    state: str,
    field: str,
    value: object,
) -> None:
    planning_module = __import__(
        "idis.services.runs.methodology_extraction_task_planning",
        fromlist=["_legacy_classifications_from_preflight_summary"],
    )
    summary = _real_preflight_summary(skipped_state=state)
    raw = _classification(summary, _skipped_document_id(state))
    raw[field] = value

    with pytest.raises(MethodologyExtractionTaskPlanningInputError):
        planning_module._legacy_classifications_from_preflight_summary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            document_preflight_summary=summary,
        )


def test_strict_safe_summary_round_trip_preserves_real_secondary_categories() -> None:
    summary = copy.deepcopy(_real_preflight_summary())

    reconstructed = _classifications_from_preflight_summary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_preflight_summary=summary,
    )

    customer = next(item for item in reconstructed if item.document_id == _CUSTOMER_DOCUMENT_ID)
    financial = next(item for item in reconstructed if item.document_id == _FINANCIAL_DOCUMENT_ID)
    assert customer.secondary_cdd_categories == (CddDocumentCategory.CUSTOMER_EVIDENCE,)
    assert financial.secondary_fdd_categories == (
        FddDocumentCategory.CASH_FLOW_SUPPORT,
        FddDocumentCategory.PL_SUPPORT,
    )
    assert financial.secondary_cdd_categories == (CddDocumentCategory.PRICING_UNIT_ECONOMICS,)


def test_legacy_decoder_is_distinct_and_strict_decoder_has_no_permissive_switch() -> None:
    planning_module = __import__(
        "idis.services.runs.methodology_extraction_task_planning",
        fromlist=["_legacy_classifications_from_preflight_summary"],
    )
    strict_decoder = planning_module._classifications_from_preflight_summary
    legacy_decoder = planning_module._legacy_classifications_from_preflight_summary
    assert "require_secondary_categories" not in inspect.signature(strict_decoder).parameters

    summary = _real_preflight_summary()
    for raw in summary["classifications"]:
        raw.pop("secondary_fdd_categories")
        raw.pop("secondary_cdd_categories")
    with pytest.raises(MethodologyExtractionTaskPlanningInputError):
        strict_decoder(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            document_preflight_summary=summary,
        )
    reconstructed = legacy_decoder(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_preflight_summary=summary,
    )
    assert all(not item.secondary_fdd_categories for item in reconstructed)
    assert all(not item.secondary_cdd_categories for item in reconstructed)


def _selected_registries() -> tuple[MethodologyRegistry, MethodologyRegistry]:
    full_cdd = import_cdd_template(DEFAULT_METHODOLOGY_REGISTRY_PATH)
    cdd_question = next(
        question
        for question in full_cdd.current_version.questions
        if question.methodology_question_id == "mq_commercial_dd_customer_quality_0001"
    )
    cdd_version = MethodologyVersion.model_validate(
        {
            **full_cdd.current_version.model_dump(mode="json"),
            "questions": [cdd_question.model_dump(mode="json")],
        }
    )
    cdd_registry = MethodologyRegistry.model_validate(
        {
            "methodology_id": full_cdd.methodology_id,
            "methodology_type": full_cdd.methodology_type,
            "versions": [cdd_version.model_dump(mode="json")],
        }
    )
    return cdd_registry, _registry(MethodologyType.FINANCIAL_DD, 1)


def _coverage_records(
    registries: tuple[MethodologyRegistry, MethodologyRegistry],
) -> list[MethodologyCoverageRecord]:
    return [
        MethodologyCoverageRecord(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_id=question.methodology_id,
            methodology_version_id=question.methodology_version_id,
            methodology_question_id=question.methodology_question_id,
            methodology_type=question.methodology_type,
            section=question.section,
        )
        for registry in registries
        for question in registry.current_version.questions
    ]


class _Methodologies:
    is_durable = True
    backend = "postgresql"

    def __init__(self) -> None:
        self.registries = _selected_registries()
        self.coverage = _coverage_records(self.registries)

    def load_run_selection(self, *, tenant_id: str, deal_id: str, run_id: str) -> Any:
        assert (tenant_id, deal_id, run_id) == (TENANT_ID, DEAL_ID, RUN_ID)
        return SimpleNamespace(
            selection_id=SELECTION_ID,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            selected_question_count=2,
        )

    def load_selected_registries(self, *, selection: object) -> tuple[MethodologyRegistry, ...]:
        _ = selection
        return self.registries

    def list_coverage_records(self, *, deal_id: str, run_id: str) -> list[Any]:
        assert (deal_id, run_id) == (DEAL_ID, RUN_ID)
        return list(self.coverage)


def _live_spans() -> dict[str, list[SourceSpanReference]]:
    return {
        _CUSTOMER_DOCUMENT_ID: [
            SourceSpanReference(
                document_id=_CUSTOMER_DOCUMENT_ID,
                span_id="33333333-3333-4333-8333-333333333333",
                evidence_tags=[],
                locator={"page": 1},
                span_type="PAGE_TEXT",
                content_hash=hashlib.sha256(_CUSTOMER_TEXT.encode("utf-8")).hexdigest(),
                text_excerpt=_CUSTOMER_TEXT,
            )
        ],
        _FINANCIAL_DOCUMENT_ID: [
            SourceSpanReference(
                document_id=_FINANCIAL_DOCUMENT_ID,
                span_id="34343434-3434-4343-8343-343434343434",
                evidence_tags=[],
                locator={"page": 1},
                span_type="PAGE_TEXT",
                content_hash=hashlib.sha256(_FINANCIAL_TEXT.encode("utf-8")).hexdigest(),
                text_excerpt=_FINANCIAL_TEXT,
            )
        ],
    }


class _DurableTasks:
    is_durable = True
    backend = "postgresql"

    def __init__(
        self,
        *,
        live_spans: dict[str, list[SourceSpanReference]] | None = None,
    ) -> None:
        self.live_spans = _live_spans() if live_spans is None else live_spans

    def load_strict_planning_source_spans(
        self,
        *,
        deal_id: str,
        source_span_references_by_document_id: dict[str, list[SourceSpanReference]],
    ) -> dict[str, list[SourceSpanReference]]:
        assert deal_id == DEAL_ID
        assert set(source_span_references_by_document_id) == {
            _CUSTOMER_DOCUMENT_ID,
            _FINANCIAL_DOCUMENT_ID,
        }
        return self.live_spans


class _Runs:
    is_durable = True

    def require_run_scope(self, *, tenant_id: str, deal_id: str, run_id: str) -> None:
        assert (tenant_id, deal_id, run_id) == (TENANT_ID, DEAL_ID, RUN_ID)

    def require_active_worker_lease(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        assert (deal_id, run_id) == (DEAL_ID, RUN_ID)
        assert (lease_owner_id, lease_fencing_token) == (LEASE_OWNER_ID, 7)


class _UnitOfWork:
    @contextmanager
    def transaction(self):  # type: ignore[no-untyped-def]
        yield


def _strict_policy(*, loader: Any = load_commercial_dd_question_category_map) -> Any:
    policy_cls = require_symbol(
        "idis.services.extraction.task_planner",
        "StrictDurableCategorySpanAdmissionPolicy",
    )
    return policy_cls(cdd_category_map_loader=loader)


def _durable_service(*, tasks: _DurableTasks | None = None, policy: object | None = None) -> Any:
    service_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_planning",
        "DurableRunMethodologyExtractionTaskPlanningService",
    )
    repository = tasks or _DurableTasks()
    return service_cls(
        methodology_repository=_Methodologies(),
        extraction_repository=repository,
        extraction_repository_factory=lambda _db_conn: repository,
        runs_repository=_Runs(),
        unit_of_work=_UnitOfWork(),
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=7,
        admission_policy=policy or _strict_policy(),
    )


@pytest.mark.parametrize(
    ("document_id", "mutation"),
    [
        (_CUSTOMER_DOCUMENT_ID, "string_false_boolean"),
        (_CUSTOMER_DOCUMENT_ID, "missing_fdd_category"),
        (_FINANCIAL_DOCUMENT_ID, "missing_cdd_category"),
        (_CUSTOMER_DOCUMENT_ID, "missing_reason"),
        (_CUSTOMER_DOCUMENT_ID, "missing_reason_codes"),
        (_CUSTOMER_DOCUMENT_ID, "missing_warning_codes"),
        (_CUSTOMER_DOCUMENT_ID, "missing_methodology_target_areas"),
        (_CUSTOMER_DOCUMENT_ID, "noncanonical_reason_code"),
        (_CUSTOMER_DOCUMENT_ID, "unknown_classification_field"),
    ],
)
def test_durable_composition_rejects_malformed_persisted_classification_before_ready_tasks(
    document_id: str,
    mutation: str,
) -> None:
    summary = _real_preflight_summary()
    raw = _classification(summary, document_id)
    if mutation == "string_false_boolean":
        raw["usable_for_methodology_extraction"] = "false"
    elif mutation == "missing_fdd_category":
        raw.pop("fdd_category")
    elif mutation == "missing_cdd_category":
        raw.pop("cdd_category")
    elif mutation == "noncanonical_reason_code":
        raw["reason_codes"] = [" supported "]
    elif mutation == "unknown_classification_field":
        raw["private_runtime_note"] = "must not cross the safe-summary boundary"
    else:
        raw.pop(mutation.removeprefix("missing_"))

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)


@pytest.mark.parametrize(
    ("field", "coerced_value"),
    [
        ("support_status", b"supported"),
        ("triage_status", b"ready"),
        ("reason", b"READY"),
        ("fdd_category", b"customer_contract"),
        ("cdd_category", b"commercial_contracts"),
        ("secondary_fdd_categories", [b"customer_contract"]),
        ("secondary_cdd_categories", [b"customer_evidence"]),
    ],
)
def test_durable_composition_rejects_enum_values_that_require_coercion(
    field: str,
    coerced_value: object,
) -> None:
    summary = _real_preflight_summary()
    _classification(summary, _CUSTOMER_DOCUMENT_ID)[field] = coerced_value

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)


@pytest.mark.parametrize("field", ["reason_codes", "methodology_target_areas"])
def test_durable_composition_rejects_empty_required_lists_on_usable_classification(
    field: str,
) -> None:
    summary = _real_preflight_summary()
    _classification(summary, _CUSTOMER_DOCUMENT_ID)[field] = []

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)


def test_durable_composition_rejects_null_primaries_on_normal_blocked_classification() -> None:
    summary = _real_preflight_summary(skipped_state="blocked")
    raw = _classification(summary, _skipped_document_id("blocked"))
    raw["fdd_category"] = None
    raw["cdd_category"] = None

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)


def test_durable_composition_accepts_producer_excluded_null_and_empty_shape() -> None:
    summary = _real_preflight_summary(skipped_state="excluded")
    excluded = _classification(summary, _skipped_document_id("excluded"))
    assert excluded["fdd_category"] is None
    assert excluded["cdd_category"] is None
    assert excluded["methodology_target_areas"] == []

    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=summary,
    )

    assert len(planned) == 2
    assert all(task.status is ExtractionTaskStatus.READY for task in planned)


@pytest.mark.parametrize("invalid_reason_codes", [["supported"], ["ZZZ"]])
def test_durable_composition_rejects_confirmed_exclusion_with_wrong_reason_lineage(
    invalid_reason_codes: list[str],
) -> None:
    summary = _real_preflight_summary(skipped_state="excluded")
    excluded = _classification(summary, _skipped_document_id("excluded"))
    excluded["reason_codes"] = invalid_reason_codes

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)
    validation_error = raised.value.__cause__.__cause__
    assert isinstance(validation_error, ValidationError)
    assert "confirmed exclusion reason codes must be exact" in str(validation_error)


def test_durable_composition_rejects_confirmed_exclusion_with_warning_lineage() -> None:
    summary = _real_preflight_summary(skipped_state="excluded")
    excluded = _classification(summary, _skipped_document_id("excluded"))
    excluded["warning_codes"] = ["unexpected_warning"]

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)
    validation_error = raised.value.__cause__.__cause__
    assert isinstance(validation_error, ValidationError)
    assert "confirmed exclusion warning codes must be empty" in str(validation_error)


@pytest.mark.parametrize("state", ["blocked", "excluded"])
def test_durable_composition_validates_extra_fields_before_skipping_classification(
    state: str,
) -> None:
    summary = _real_preflight_summary(skipped_state=state)
    raw = _classification(summary, _skipped_document_id(state))
    raw["private_runtime_note"] = "must not be ignored"

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid preflight classification" in str(raised.value.__cause__)


def test_real_media_preflight_emits_strict_canonical_timed_span_order() -> None:
    result, eligible = InMemoryRunDocumentPreflightService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[_timed_media_document_with_opposed_span_ids()],
    )
    assert [item["document_id"] for item in eligible] == [_MEDIA_DOCUMENT_ID]
    summary = result.to_run_step_summary()

    planning_module = __import__(
        "idis.services.runs.methodology_extraction_task_planning",
        fromlist=["_strict_source_span_references_from_preflight_summary"],
    )
    reconstructed = planning_module._strict_source_span_references_from_preflight_summary(
        document_preflight_summary=summary,
    )

    assert [span.span_id for span in reconstructed[_MEDIA_DOCUMENT_ID]] == [
        "00000000-0000-4000-8000-000000000001",
        "ffffffff-ffff-4fff-8fff-ffffffffffff",
    ]


def test_strict_source_map_rejects_document_key_not_present_in_classification_set() -> None:
    summary = _real_preflight_summary()
    summary["source_spans_by_document_id"]["38383838-3838-4383-8383-383838383838"] = []

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "source-map keys" in str(raised.value.__cause__)


@pytest.mark.parametrize("state", ["blocked", "excluded"])
def test_strict_source_map_requires_empty_entry_for_every_skipped_classification(
    state: str,
) -> None:
    summary = _real_preflight_summary(skipped_state=state)
    raw = _classification(summary, _skipped_document_id(state))
    summary["source_spans_by_document_id"].pop(raw["document_id"])

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "source-map keys" in str(raised.value.__cause__)


@pytest.mark.parametrize("state", ["blocked", "excluded"])
@pytest.mark.parametrize("private_field", ["text_excerpt", "evidence_tags", "unknown_field"])
def test_strict_source_map_validates_every_span_before_eligibility_projection(
    state: str,
    private_field: str,
) -> None:
    summary = _real_preflight_summary(skipped_state=state)
    raw = _classification(summary, _skipped_document_id(state))
    poisoned_span = copy.deepcopy(summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID][0])
    poisoned_span["document_id"] = raw["document_id"]
    poisoned_span[private_field] = ["private"] if private_field == "evidence_tags" else "private"
    summary["source_spans_by_document_id"][raw["document_id"]] = [poisoned_span]

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )
    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid strict preflight source span" in str(raised.value.__cause__)


def test_strict_source_map_rejects_unknown_span_field_on_eligible_document() -> None:
    summary = _real_preflight_summary()
    summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID][0]["unknown_field"] = "private"

    with pytest.raises(MethodologyTaskCoverageError):
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )


@pytest.mark.parametrize("shape", ["duplicate", "noncanonical"])
def test_strict_source_map_rejects_duplicate_or_noncanonical_span_ids(shape: str) -> None:
    summary = _real_preflight_summary()
    spans = summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID]
    duplicate = copy.deepcopy(spans[0])
    if shape == "noncanonical":
        spans[0]["span_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        duplicate["span_id"] = "00000000-0000-4000-8000-000000000001"
    spans.append(duplicate)

    with pytest.raises(MethodologyTaskCoverageError):
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )


def test_real_preflight_to_strict_durable_planner_never_requires_fabricated_tags() -> None:
    summary = _real_preflight_summary()

    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=summary,
    )

    assert len(planned) == 2
    assert all(task.status is ExtractionTaskStatus.READY for task in planned)
    assert all(not span.evidence_tags for task in planned for span in task.source_spans)


def test_secondary_only_cdd_match_records_deterministic_governed_admission() -> None:
    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_real_preflight_summary(),
    )

    task = next(item for item in planned if item.methodology_type is MethodologyType.COMMERCIAL_DD)
    governed = load_commercial_dd_question_category_map()
    assert task.target_cdd_category is CddDocumentCategory.CUSTOMER_EVIDENCE
    assert task.strict_admission is not None
    assert task.strict_admission.matched_category == "customer_evidence"
    assert task.strict_admission.classified_primary_cdd_category == "commercial_contracts"
    assert task.strict_admission.classified_secondary_cdd_categories == ["customer_evidence"]
    assert task.strict_admission.cdd_category_mapping_version == governed.mapping_version
    assert task.strict_admission.cdd_category_mapping_sha256 == governed.canonical_payload_sha256
    assert task.strict_admission.classifier_contract_version == governed.classifier_version
    assert (
        task.strict_admission.classifier_reachable_categories_sha256
        == governed.classifier_reachable_categories_sha256
    )


def test_required_evidence_is_preserved_exactly_for_strict_extractor_instruction() -> None:
    methodologies = _Methodologies()
    expected_question = methodologies.registries[0].current_version.questions[0]
    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_real_preflight_summary(),
    )

    task = next(item for item in planned if item.methodology_type is MethodologyType.COMMERCIAL_DD)
    assert tuple(task.required_evidence) == expected_question.required_evidence
    schema_required_evidence = tuple(task.expected_answer_schema.required_evidence)
    assert schema_required_evidence == expected_question.required_evidence


def test_injected_evidence_tags_cannot_replace_a_real_durable_span() -> None:
    summary = _real_preflight_summary()
    summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID][0]["evidence_tags"] = [
        "customer_interview"
    ]
    tasks = _DurableTasks(live_spans={_CUSTOMER_DOCUMENT_ID: [], _FINANCIAL_DOCUMENT_ID: []})

    with pytest.raises(MethodologyTaskCoverageError):
        _durable_service(tasks=tasks).plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )


def test_strict_durable_service_has_no_implicit_legacy_policy_fallback() -> None:
    service_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_planning",
        "DurableRunMethodologyExtractionTaskPlanningService",
    )
    repository = _DurableTasks()

    with pytest.raises(TypeError):
        service_cls(
            methodology_repository=_Methodologies(),
            extraction_repository=repository,
            extraction_repository_factory=lambda _db_conn: repository,
            runs_repository=_Runs(),
            unit_of_work=_UnitOfWork(),
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        )


@pytest.mark.parametrize(
    ("loader", "expected_code"),
    [
        (
            lambda: (_ for _ in ()).throw(
                CommercialDDCategoryMapError(METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE)
            ),
            METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE,
        ),
        (
            lambda: replace(
                load_commercial_dd_question_category_map(),
                mapping_version="1.0.1",
            ),
            METHODOLOGY_CDD_CATEGORY_MAP_DRIFT,
        ),
    ],
)
def test_strict_policy_fails_closed_when_governed_map_is_unavailable_or_drifted(
    loader: Any,
    expected_code: str,
) -> None:
    with pytest.raises(CommercialDDCategoryMapError) as raised:
        _strict_policy(loader=loader)

    assert raised.value.code == expected_code


def test_strict_task_contract_versions_are_v2_and_rehydration_rejects_drift() -> None:
    repository_module = __import__(
        "idis.persistence.repositories.methodology_extraction",
        fromlist=["METHODOLOGY_PLANNER_CONTRACT_VERSION"],
    )
    assert repository_module.METHODOLOGY_PLANNER_CONTRACT_VERSION == "selected-pair-planner-v2"
    assert repository_module.METHODOLOGY_TASK_CONTRACT_VERSION == "methodology-extraction-task-v2"

    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_real_preflight_summary(),
    )
    policy = _strict_policy()
    policy.validate_rehydrated_tasks(planned)
    drifted = planned[0].model_copy(update={"strict_admission": None})
    with pytest.raises(ValueError):
        policy.validate_rehydrated_tasks([drifted, *planned[1:]])


def test_restart_rejects_secondary_category_drift_in_canonical_task_payload() -> None:
    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_real_preflight_summary(),
    )
    task = next(item for item in planned if item.methodology_type is MethodologyType.COMMERCIAL_DD)
    assert task.strict_admission is not None
    drifted_admission = task.strict_admission.model_copy(
        update={"classified_secondary_cdd_categories": []}
    )
    drifted = task.model_copy(update={"strict_admission": drifted_admission})

    with pytest.raises(ValueError, match="strict admission task payload drift"):
        _strict_policy().validate_rehydrated_tasks([drifted])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cdd_category_mapping_sha256", "9" * 64),
        ("classifier_contract_version", "stale-classifier-v1"),
        ("classifier_reachable_categories_sha256", "8" * 64),
    ],
)
def test_restart_rejects_map_or_classifier_contract_drift(
    field: str,
    value: str,
) -> None:
    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_real_preflight_summary(),
    )
    task = planned[0]
    assert task.strict_admission is not None
    drifted = task.model_copy(
        update={
            "strict_admission": task.strict_admission.model_copy(update={field: value}),
        }
    )

    with pytest.raises(ValueError, match="strict admission task payload drift"):
        _strict_policy().validate_rehydrated_tasks([drifted])


def test_strict_task_canonical_payload_carries_complete_admission_proof() -> None:
    _result, planned, _staged = _durable_service().plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_real_preflight_summary(),
    )
    task = next(item for item in planned if item.methodology_type is MethodologyType.COMMERCIAL_DD)
    payload = task.model_dump(mode="json")

    assert payload["strict_admission"] == {
        "contract_version": "strict-category-span-admission-v2",
        "matched_category": "customer_evidence",
        "classified_primary_fdd_category": "customer_contract",
        "classified_primary_cdd_category": "commercial_contracts",
        "classified_secondary_fdd_categories": [],
        "classified_secondary_cdd_categories": ["customer_evidence"],
        "cdd_category_mapping_version": "1.0.0",
        "cdd_category_mapping_sha256": (
            load_commercial_dd_question_category_map().canonical_payload_sha256
        ),
        "classifier_contract_version": "cdd-document-classifier-v2",
        "classifier_reachable_categories_sha256": (
            load_commercial_dd_question_category_map().classifier_reachable_categories_sha256
        ),
    }


def test_worker_callback_builder_loads_and_injects_the_fixed_governed_cdd_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[str] = []
    governed = load_commercial_dd_question_category_map()

    def recording_fixed_loader():  # type: ignore[no-untyped-def]
        loaded.append("checked-in-fixed-map")
        return governed

    @contextmanager
    def connection_factory():  # type: ignore[no-untyped-def]
        raise AssertionError("composition must not access PostgreSQL")
        yield

    monkeypatch.setattr(
        category_mapping_module,
        "load_commercial_dd_question_category_map",
        recording_fixed_loader,
    )

    worker_module.build_postgres_methodology_extraction_callbacks(
        connection_factory=connection_factory,
        tenant_id=TENANT_ID,
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=7,
        audit_sink=object(),
        strict_profile=None,
    )

    assert loaded == ["checked-in-fixed-map"]


def test_worker_restart_rejects_a_non_postgres_fake_composition_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def connection_factory():  # type: ignore[no-untyped-def]
        yield object()

    callbacks = worker_module.build_postgres_methodology_extraction_callbacks(
        connection_factory=connection_factory,
        tenant_id=TENANT_ID,
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=7,
        audit_sink=object(),
        strict_profile=None,
    )

    # Restart now rederives frozen selection, coverage, preflight, and task graph
    # in one PostgreSQL snapshot; a fake repository seam is no longer evidence.
    with pytest.raises(RuntimeError, match="METHODOLOGY_TASK_COVERAGE_INCOMPLETE"):
        callbacks["methodology_extraction_task_planning_fn"].rehydrate(  # type: ignore[attr-defined]
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )


@pytest.mark.parametrize("scope_field", ["tenant_id", "deal_id", "run_id"])
def test_durable_composition_rejects_cross_scope_nested_preflight_summary(
    scope_field: str,
) -> None:
    summary = _real_preflight_summary()
    summary["summary"][scope_field] = "99999999-9999-4999-8999-999999999999"

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "document preflight run-step summary scope mismatch" in str(raised.value.__cause__)


@pytest.mark.parametrize(
    "mutation",
    [
        "status_failed",
        "total_count_drift",
        "usable_count_drift",
        "support_count_drift",
        "triage_count_drift",
        "reason_count_drift",
        "blocked_overlap",
        "excluded_overlap",
    ],
)
def test_durable_composition_rejects_contradictory_full_preflight_envelope(
    mutation: str,
) -> None:
    summary = _real_preflight_summary()
    if mutation == "status_failed":
        summary["status"] = "FAILED"
    elif mutation == "total_count_drift":
        summary["summary"]["total_documents"] += 1
    elif mutation == "usable_count_drift":
        summary["summary"]["usable_documents"] -= 1
    elif mutation == "support_count_drift":
        summary["summary"]["by_support_status"]["supported"] += 1
    elif mutation == "triage_count_drift":
        summary["summary"]["by_triage_status"]["ready"] += 1
    elif mutation == "reason_count_drift":
        summary["summary"]["by_reason"]["READY"] += 1
    elif mutation == "blocked_overlap":
        summary["blocked_document_ids"] = [_CUSTOMER_DOCUMENT_ID]
    else:
        summary["excluded_confirmed_document_ids"] = [_CUSTOMER_DOCUMENT_ID]

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid document preflight run-step summary" in str(raised.value.__cause__)


@pytest.mark.parametrize(
    "mutation",
    ["status_bytes", "total_bool", "reason_counts_list", "blocked_tuple"],
)
def test_durable_composition_requires_exact_full_envelope_scalar_list_and_map_types(
    mutation: str,
) -> None:
    summary = _real_preflight_summary()
    if mutation == "status_bytes":
        summary["status"] = b"COMPLETED"
    elif mutation == "total_bool":
        summary["summary"]["total_documents"] = True
    elif mutation == "reason_counts_list":
        summary["summary"]["by_reason"] = [["READY", 2]]
    else:
        summary["blocked_document_ids"] = ()

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "invalid document preflight run-step summary" in str(raised.value.__cause__)


def test_producer_rejects_inconsistent_caller_supplied_aggregate_summary() -> None:
    result = _real_preflight_result()
    assert result.summary is not None
    inconsistent = result.model_copy(
        update={
            "summary": result.summary.model_copy(
                update={"total_documents": result.summary.total_documents + 1}
            )
        }
    )

    with pytest.raises(ValueError, match="caller-supplied preflight summary is inconsistent"):
        inconsistent.to_run_step_summary()


@pytest.mark.parametrize(
    ("mutation", "expected_cause"),
    [
        ("corrupted_ready", "invalid preflight classification"),
        ("supported_blocked", "invalid preflight classification"),
        (
            "blocked_disguised_as_exclusion",
            "invalid document preflight run-step summary",
        ),
    ],
)
def test_durable_composition_rejects_impossible_producer_state_machine(
    mutation: str,
    expected_cause: str,
) -> None:
    summary = _real_preflight_summary(skipped_state="blocked")
    blocked_id = _skipped_document_id("blocked")
    raw = _classification(summary, blocked_id)
    if mutation == "corrupted_ready":
        raw["support_status"] = "corrupted"
        raw["triage_status"] = "blocked"
        raw["reason"] = "READY"
    elif mutation == "supported_blocked":
        raw["support_status"] = "supported"
        raw["triage_status"] = "blocked"
        raw["reason"] = "READY"
    else:
        raw.update(
            {
                "support_status": "excluded_confirmed",
                "triage_status": "excluded_confirmed",
                "reason": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
                "reason_codes": ["DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"],
                "usable_for_methodology_extraction": False,
                "fdd_category": None,
                "cdd_category": None,
                "secondary_fdd_categories": [],
                "secondary_cdd_categories": [],
                "methodology_target_areas": [],
            }
        )

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert expected_cause in str(raised.value.__cause__)


def test_durable_composition_rejects_nonempty_span_on_confirmed_exclusion() -> None:
    summary = _real_preflight_summary(skipped_state="excluded")
    excluded_id = _skipped_document_id("excluded")
    poisoned_span = copy.deepcopy(summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID][0])
    poisoned_span["document_id"] = excluded_id
    summary["source_spans_by_document_id"][excluded_id] = [poisoned_span]

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "confirmed exclusion requires zero source spans" in str(raised.value.__cause__)


def test_durable_composition_rejects_null_hash_span_on_blocked_document() -> None:
    summary = _real_preflight_summary(skipped_state="blocked")
    blocked_id = _skipped_document_id("blocked")
    poisoned_span = copy.deepcopy(summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID][0])
    poisoned_span["document_id"] = blocked_id
    poisoned_span["content_hash"] = None
    summary["source_spans_by_document_id"][blocked_id] = [poisoned_span]

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "nonexcluded source span requires a content hash" in str(raised.value.__cause__)


@pytest.mark.parametrize("mutation", ["null_hash", "empty_locator"])
def test_strict_durable_planning_rejects_incomplete_eligible_span_before_source_load(
    mutation: str,
) -> None:
    summary = _real_preflight_summary()
    poisoned_span = summary["source_spans_by_document_id"][_CUSTOMER_DOCUMENT_ID][0]
    if mutation == "null_hash":
        poisoned_span["content_hash"] = None
        expected = "nonexcluded source span requires a content hash"
    else:
        poisoned_span["locator"] = {}
        expected = "locator must not be empty"

    class _NoSourceLoadTasks(_DurableTasks):
        def load_strict_planning_source_spans(self, **_kwargs: Any) -> Any:
            raise AssertionError("strict source loading must not run for an invalid envelope")

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service(tasks=_NoSourceLoadTasks()).plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert expected in str(raised.value.__cause__)


def test_strict_durable_planning_requires_completed_preflight_status() -> None:
    summary = _real_preflight_summary(skipped_state="blocked")
    assert summary["status"] == "PARTIAL"

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "strict durable planning requires COMPLETED document preflight" in str(
        raised.value.__cause__
    )


def test_strict_durable_planning_rejects_a_semantically_valid_failed_preflight() -> None:
    blocked = _document(
        document_id=_skipped_document_id("blocked"),
        filename="fictional blocked source.pdf",
        text="Fictional blocked source.",
    )
    blocked["spans"] = []
    blocked["metadata"]["span_count"] = 0
    result, eligible = InMemoryRunDocumentPreflightService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[blocked],
    )
    summary = result.to_run_step_summary()
    assert eligible == []
    assert summary["status"] == "FAILED"

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "strict durable planning requires COMPLETED document preflight" in str(
        raised.value.__cause__
    )


def test_strict_durable_planning_rejects_a_mapping_subclass_envelope() -> None:
    summary = OrderedDict(_real_preflight_summary())

    with pytest.raises(MethodologyTaskCoverageError) as raised:
        _durable_service().plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=summary,  # type: ignore[arg-type]
        )

    assert isinstance(raised.value.__cause__, MethodologyExtractionTaskPlanningInputError)
    assert "run-step summary must be an exact dictionary" in str(raised.value.__cause__)


def test_orchestrator_preserves_the_exact_document_preflight_envelope_on_context() -> None:
    result = _real_preflight_result()
    corpus = [
        _document(
            document_id=_CUSTOMER_DOCUMENT_ID,
            filename="fictional customer contract.docx",
            text=_CUSTOMER_TEXT,
        )
    ]
    context = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=corpus,
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        document_preflight_fn=lambda **_kwargs: (result, corpus),
    )

    summary = object.__new__(RunOrchestrator)._execute_document_preflight(context)

    assert context.document_preflight_summary == summary


def test_strict_orchestrator_planning_receives_unmerged_preflight_envelope() -> None:
    preflight = _real_preflight_summary()
    captured: dict[str, Any] = {}
    durable = _durable_service()

    def planning_callback(**kwargs: Any) -> Any:
        captured.update(kwargs)
        kwargs.pop("run_step_id")
        return durable.plan_for_run(**kwargs)

    # Strict composition admits callbacks only with explicit production metadata.
    planning_callback.persistence_backend = "postgresql"  # type: ignore[attr-defined]
    planning_callback.external_calls_enabled = False  # type: ignore[attr-defined]

    context = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        strict_resume_validation_required=True,
        active_run_step_id="rs-preflight-envelope",
        document_preflight_summary=preflight,
        methodology_extraction_task_planning_fn=planning_callback,
    )
    polluted_accumulator = {
        **preflight,
        "status": "coverage_completed",
        "summary": {"coverage_rows": 2},
        "coverage_record_ids": ["not-a-preflight-field"],
    }

    object.__new__(RunOrchestrator)._execute_methodology_extraction_task_planning(
        context,
        polluted_accumulator,
    )

    assert captured["document_preflight_summary"] is preflight


def test_strict_orchestrator_resume_restores_exact_document_preflight_envelope() -> None:
    preflight = _real_preflight_summary()
    context = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        strict_resume_validation_required=True,
    )

    object.__new__(RunOrchestrator)._rehydrate_document_preflight_summary(context, preflight)

    assert context.document_preflight_summary == preflight
    assert context.document_preflight_summary is not preflight
