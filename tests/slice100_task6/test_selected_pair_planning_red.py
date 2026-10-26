"""RED contracts for durable CDD+FDD task planning and fallback removal."""

from __future__ import annotations

import hashlib
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from idis.methodology.category_mapping import (
    CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES,
    load_commercial_dd_question_category_map,
)
from idis.methodology.importers.cdd_json import import_cdd_template
from idis.methodology.models import MethodologyType
from idis.models.extraction_task import (
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.models.methodology_coverage import MethodologyCoverageRecord
from idis.services.extraction.task_planner import StrictDurableCategorySpanAdmissionPolicy
from idis.services.runs.methodology_coverage_init import DEFAULT_METHODOLOGY_REGISTRY_PATH
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError
from tests.slice100_task5.test_durable_selection_coverage_red import _registry
from tests.slice100_task6._support import (
    DEAL_ID,
    LEASE_OWNER_ID,
    RUN_ID,
    SELECTION_ID,
    TENANT_ID,
    require_symbol,
)

CDD_DOCUMENT_ID = "18181818-1818-4181-8181-181818181818"
FDD_DOCUMENT_ID = "19191919-1919-4191-8191-191919191919"
CDD_SPAN_ID = "20202020-2020-4202-8202-202020202020"
FDD_SPAN_ID = "21212121-2121-4212-8212-212121212121"
CDD_SOURCE_TEXT = "Fictional CDD span"
FDD_SOURCE_TEXT = "Fictional FDD span"
CDD_SOURCE_SHA256 = hashlib.sha256(CDD_SOURCE_TEXT.encode("utf-8")).hexdigest()
FDD_SOURCE_SHA256 = hashlib.sha256(FDD_SOURCE_TEXT.encode("utf-8")).hexdigest()


def _registries() -> tuple[Any, Any]:
    return (
        import_cdd_template(DEFAULT_METHODOLOGY_REGISTRY_PATH),
        _registry(MethodologyType.FINANCIAL_DD, 79),
    )


def _selected_coverage_records(
    registries: tuple[Any, ...] | None = None,
) -> list[MethodologyCoverageRecord]:
    selected = registries or _registries()
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
        for registry in selected
        for question in registry.current_version.questions
    ]


def _preflight_summary() -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "summary": {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "total_documents": 2,
            "usable_documents": 2,
            "excluded_confirmed_documents": 0,
            "blocked_documents": 0,
            "by_support_status": {"supported": 2},
            "by_triage_status": {"ready": 2},
            "by_reason": {"READY": 2},
        },
        "eligible_document_ids": [CDD_DOCUMENT_ID, FDD_DOCUMENT_ID],
        "excluded_confirmed_document_ids": [],
        "blocked_document_ids": [],
        "classifications": [
            {
                "classification_id": "dc_doc_cdd",
                "document_id": CDD_DOCUMENT_ID,
                "support_status": "supported",
                "triage_status": "ready",
                # READY is the exact producer enum for a supported, usable decision.
                "reason": "READY",
                "reason_codes": ["supported"],
                "warning_codes": [],
                "fdd_category": "unknown",
                "cdd_category": "market_research",
                "secondary_fdd_categories": [],
                "secondary_cdd_categories": sorted(
                    category.value
                    for category in CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES
                    if category.value != "market_research"
                ),
                "methodology_target_areas": ["Fictional commercial section"],
                "usable_for_methodology_extraction": True,
            },
            {
                "classification_id": "dc_doc_fdd",
                "document_id": FDD_DOCUMENT_ID,
                "support_status": "supported",
                "triage_status": "ready",
                "reason": "READY",
                "reason_codes": ["supported"],
                "warning_codes": [],
                "fdd_category": "financial_schedule_model",
                "cdd_category": "unknown",
                "secondary_fdd_categories": [],
                "secondary_cdd_categories": [],
                "methodology_target_areas": ["P&L"],
                "usable_for_methodology_extraction": True,
            },
        ],
        "source_spans_by_document_id": {
            CDD_DOCUMENT_ID: [
                {
                    "span_id": CDD_SPAN_ID,
                    "document_id": CDD_DOCUMENT_ID,
                    "locator": {"page": 1},
                    # Durable strict reload compares the canonical parser span
                    # identity; the seeded PDF row is PAGE_TEXT, never TEXT.
                    "span_type": "PAGE_TEXT",
                    "content_hash": CDD_SOURCE_SHA256,
                }
            ],
            FDD_DOCUMENT_ID: [
                {
                    "span_id": FDD_SPAN_ID,
                    "document_id": FDD_DOCUMENT_ID,
                    "locator": {"sheet": "P&L", "cell": "A1"},
                    "span_type": "CELL",
                    "content_hash": FDD_SOURCE_SHA256,
                }
            ],
        },
    }


class _Methodologies:
    is_durable = True
    backend = "postgresql"

    def __init__(self, *, registries: tuple[Any, ...] | None = None, count: int = 100) -> None:
        self.registries = registries or _registries()
        self.coverage = _selected_coverage_records(self.registries)[:count]

    def load_run_selection(self, *, tenant_id: str, deal_id: str, run_id: str) -> Any:
        assert (tenant_id, deal_id, run_id) == (TENANT_ID, DEAL_ID, RUN_ID)
        return SimpleNamespace(
            selection_id=SELECTION_ID,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            selected_question_count=sum(
                len(registry.current_version.questions) for registry in self.registries
            ),
        )

    def load_selected_registries(self, *, selection: object) -> tuple[Any, ...]:
        _ = selection
        return self.registries

    def list_coverage_records(self, *, deal_id: str, run_id: str) -> list[Any]:
        assert (deal_id, run_id) == (DEAL_ID, RUN_ID)
        return list(self.coverage)


class _Tasks:
    is_durable = True
    backend = "postgresql"

    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def load_strict_planning_source_spans(
        self,
        *,
        deal_id: str,
        source_span_references_by_document_id: dict[str, list[SourceSpanReference]],
    ) -> dict[str, list[SourceSpanReference]]:
        assert deal_id == DEAL_ID
        source_text = {
            CDD_DOCUMENT_ID: CDD_SOURCE_TEXT,
            FDD_DOCUMENT_ID: FDD_SOURCE_TEXT,
        }
        return {
            document_id: [
                span.model_copy(update={"text_excerpt": source_text[document_id]}) for span in spans
            ]
            for document_id, spans in source_span_references_by_document_id.items()
        }

    def persist_planned_tasks(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str,
        tasks: list[Any],
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> list[Any]:
        assert (deal_id, run_id, selection_id) == (DEAL_ID, RUN_ID, SELECTION_ID)
        assert (lease_owner_id, lease_fencing_token) == (LEASE_OWNER_ID, 7)
        self.tasks.extend(tasks)
        return list(tasks)


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


def _service(methodologies: object, tasks: object) -> Any:
    service_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_planning",
        "DurableRunMethodologyExtractionTaskPlanningService",
    )
    return service_cls(
        methodology_repository=methodologies,
        extraction_repository=tasks,
        extraction_repository_factory=lambda _db_conn: tasks,
        runs_repository=_Runs(),
        unit_of_work=_UnitOfWork(),
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=7,
        admission_policy=StrictDurableCategorySpanAdmissionPolicy(
            cdd_category_map_loader=load_commercial_dd_question_category_map,
        ),
    )


def test_selected_cdd_and_fdd_plan_stages_exactly_100_ready_tasks() -> None:
    methodologies = _Methodologies()
    tasks = _Tasks()

    result, planned, staged_write = _service(methodologies, tasks).plan_for_run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_preflight_summary=_preflight_summary(),
    )

    # Immediate persistence was an obsolete assertion: §5.1 requires Step 6 writes
    # to join the ordinary fenced completion transaction.
    assert len(planned) == 100
    assert tasks.tasks == []
    assert all(task.status is ExtractionTaskStatus.READY for task in planned)
    assert Counter(task.methodology_type for task in planned) == {
        MethodologyType.COMMERCIAL_DD: 21,
        MethodologyType.FINANCIAL_DD: 79,
    }
    assert result.summary.total_tasks == 100
    assert result.summary.by_status == {"ready": 100}

    staged_write.persist(
        db_conn=object(),
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=7,
    )
    assert len(tasks.tasks) == 100


@pytest.mark.parametrize(
    ("methodologies", "reason"),
    [
        (_Methodologies(registries=(_registries()[0],), count=21), "CDD-only selection"),
        (_Methodologies(count=99), "missing applicable coverage row"),
    ],
)
def test_incomplete_selected_pair_or_coverage_fails_before_persisting_any_task(
    methodologies: _Methodologies,
    reason: str,
) -> None:
    tasks = _Tasks()
    error_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_planning",
        "MethodologyTaskCoverageError",
    )

    with pytest.raises(error_cls) as raised:
        _service(methodologies, tasks).plan_for_run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            document_preflight_summary=_preflight_summary(),
        )

    assert raised.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE", reason
    assert tasks.tasks == []


def test_strict_orchestrator_rejects_the_checked_in_cdd_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_fallback() -> object:
        raise AssertionError("strict planning reached checked-in CDD-only fallback")

    monkeypatch.setattr(
        "idis.services.runs.methodology_coverage_init.load_default_methodology_registry",
        forbidden_fallback,
    )
    context = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        strict_resume_validation_required=True,
        methodology_coverage_records=_selected_coverage_records(),
    )

    orchestrator = object.__new__(RunOrchestrator)
    with pytest.raises(RunStepBlockedError) as raised:
        orchestrator._execute_methodology_extraction_task_planning(context, _preflight_summary())

    assert raised.value.code == "STRICT_METHODOLOGY_COMPOSITION_REQUIRED"
