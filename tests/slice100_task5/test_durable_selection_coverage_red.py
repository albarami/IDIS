"""RED behavioral contracts for durable CDD+FDD selection and coverage init.

This suite intentionally specifies only a small constructor-injected seam.  It
does not inspect implementation source or prescribe SQL/repository internals.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    ReportMapping,
    RequiredEvidence,
)
from idis.models.methodology_coverage import MethodologyCoverageStatus
from idis.services.methodology.coverage import InMemoryMethodologyCoverageService

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
FOREIGN_TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
LEASE_FENCING_TOKEN = 7


@dataclass(frozen=True)
class _PersistedSelection:
    selection_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    immutable: bool
    selected_version_ids: tuple[str, ...]
    selected_source_hashes: tuple[str, ...]
    selected_registry_hashes: tuple[str, ...]


class _MethodologyRepository:
    """Transactional durable-repository double at the behavioral protocol seam."""

    is_durable = True
    backend = "postgresql"

    def __init__(
        self,
        registries: tuple[MethodologyRegistry, ...],
        *,
        has_selection: bool = True,
        fail_insert: bool = False,
        selection_tenant_id: str = TENANT_ID,
        selection_hash_drift: bool = False,
    ) -> None:
        self.registries = registries
        self.has_selection = has_selection
        self.fail_insert = fail_insert
        self.selection_tenant_id = selection_tenant_id
        self.selection_hash_drift = selection_hash_drift
        self.durable_rows: list[Any] = []

    def load_run_selection(
        self, *, tenant_id: str, deal_id: str, run_id: str
    ) -> _PersistedSelection | None:
        if not self.has_selection:
            return None
        return _PersistedSelection(
            selection_id="44444444-4444-4444-8444-444444444444",
            tenant_id=self.selection_tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            immutable=True,
            selected_version_ids=tuple(
                registry.current_version.methodology_version_id for registry in self.registries
            ),
            selected_source_hashes=tuple(
                (
                    "f" * 64
                    if self.selection_hash_drift and index == 0
                    else registry.current_version.source_hash
                )
                for index, registry in enumerate(self.registries)
            ),
            selected_registry_hashes=tuple(registry.registry_hash for registry in self.registries),
        )

    def load_selected_registries(
        self, *, selection: _PersistedSelection
    ) -> tuple[MethodologyRegistry, ...]:
        assert selection.selected_version_ids == tuple(
            registry.current_version.methodology_version_id for registry in self.registries
        )
        return self.registries

    def insert_coverage_records(self, *, records: list[Any]) -> list[Any]:
        if self.fail_insert:
            self.durable_rows.extend(records[:50])
            raise RuntimeError("injected durable insert failure")
        self.durable_rows.extend(records)
        return list(records)


class _RunsRepository:
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
        assert (lease_owner_id, lease_fencing_token) == (
            LEASE_OWNER_ID,
            LEASE_FENCING_TOKEN,
        )


class _UnitOfWork:
    def __init__(self, repository: _MethodologyRepository) -> None:
        self.repository = repository
        self.commit_count = 0
        self.rollback_count = 0

    @contextmanager
    def transaction(self):  # type: ignore[no-untyped-def]
        before = list(self.repository.durable_rows)
        try:
            yield
        except Exception:
            self.repository.durable_rows[:] = before
            self.rollback_count += 1
            raise
        else:
            self.commit_count += 1


def _question(
    methodology_type: MethodologyType,
    ordinal: int,
) -> MethodologyQuestion:
    methodology_id = methodology_type.value
    version_id = f"{methodology_id}:frozen-v1"
    is_fdd = methodology_type is MethodologyType.FINANCIAL_DD
    source_hash = ("a" if is_fdd else "b") * 64
    section = "P&L" if is_fdd else "Fictional commercial section"
    row_number = ordinal + 3 if is_fdd else None
    return MethodologyQuestion(
        methodology_id=methodology_id,
        methodology_version_id=version_id,
        methodology_question_id=f"mq_{methodology_id}_{ordinal:04d}",
        methodology_type=methodology_type,
        section=section,
        sheet_or_source_section=section,
        source_row_number=row_number,
        term=f"fictional-term-{ordinal:04d}",
        nature="fictional",
        line_item=f"fictional-line-{ordinal:04d}",
        question_text=f"Review fictional evidence {ordinal:04d}.",
        required_evidence=[
            RequiredEvidence(evidence_type="fictional_support", description="Fictional support")
        ],
        target_document_categories=["financial_statement" if is_fdd else "market_research"],
        assigned_agents=[
            AssignedAgent(
                role="financial_agent" if is_fdd else "market_agent",
                responsibility="Review fictional evidence",
            )
        ],
        report_mapping=ReportMapping(report_section=section),
        validation_requirements=["requires_claim_or_evidence"],
        source_trace=MethodologySourceTrace(
            source_type="operator_workbook" if is_fdd else "checked_in_template",
            source_name="configured-source",
            source_hash=source_hash,
            sheet_or_section=section,
            row_number=row_number,
        ),
    )


def _registry(methodology_type: MethodologyType, count: int) -> MethodologyRegistry:
    methodology_id = methodology_type.value
    source_hash = ("a" if methodology_type is MethodologyType.FINANCIAL_DD else "b") * 64
    version = MethodologyVersion(
        methodology_id=methodology_id,
        methodology_version_id=f"{methodology_id}:frozen-v1",
        methodology_type=methodology_type,
        version_label="frozen-v1",
        source_hash=source_hash,
        source_name="configured-source",
        questions=[_question(methodology_type, ordinal) for ordinal in range(1, count + 1)],
    )
    return MethodologyRegistry(
        methodology_id=methodology_id,
        methodology_type=methodology_type,
        versions=[version],
    )


def _service(
    repository: object,
    *,
    uow: _UnitOfWork | None = None,
) -> tuple[Any, _UnitOfWork | None]:
    try:
        from idis.services.runs.methodology_coverage_init import (
            DurableRunMethodologyCoverageInitService,
        )
    except ImportError:
        pytest.fail("Task 5 durable methodology coverage service is missing")
    actual_uow = uow
    if actual_uow is None and isinstance(repository, _MethodologyRepository):
        actual_uow = _UnitOfWork(repository)
    service = DurableRunMethodologyCoverageInitService(
        methodology_repository=repository,
        runs_repository=_RunsRepository(),
        unit_of_work=actual_uow,
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=LEASE_FENCING_TOKEN,
    )
    return service, actual_uow


def _initialize(service: Any) -> tuple[Any, list[Any]]:
    return service.initialize_for_run(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)


def _assert_incomplete(callable_: Any) -> None:
    with pytest.raises(Exception) as raised:
        callable_()
    assert getattr(raised.value, "code", None) == "METHODOLOGY_COVERAGE_INCOMPLETE"


def test_both_frozen_versions_atomically_create_exactly_100_durable_rows() -> None:
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, 79),
        )
    )
    service, uow = _service(repository)

    result, records = _initialize(service)

    assert uow is not None
    assert uow.commit_count == 1
    assert uow.rollback_count == 0
    assert len(records) == len(repository.durable_rows) == 100
    assert len({record.coverage_record_id for record in records}) == 100
    assert Counter(record.methodology_type for record in records) == {
        MethodologyType.COMMERCIAL_DD: 21,
        MethodologyType.FINANCIAL_DD: 79,
    }
    assert {record.status for record in records} == {MethodologyCoverageStatus.NOT_STARTED}
    assert {(record.tenant_id, record.deal_id, record.run_id) for record in records} == {
        (TENANT_ID, DEAL_ID, RUN_ID)
    }
    assert result.summary.total_questions == 100


def test_cdd_only_persisted_selection_fails_without_writes() -> None:
    repository = _MethodologyRepository((_registry(MethodologyType.COMMERCIAL_DD, 21),))
    service, uow = _service(repository)

    _assert_incomplete(lambda: _initialize(service))

    assert repository.durable_rows == []
    assert uow is not None and uow.commit_count == 0


def test_fdd_only_persisted_selection_fails_without_writes() -> None:
    repository = _MethodologyRepository((_registry(MethodologyType.FINANCIAL_DD, 79),))
    service, uow = _service(repository)

    _assert_incomplete(lambda: _initialize(service))

    assert repository.durable_rows == []
    assert uow is not None and uow.commit_count == 0


def test_absent_run_selection_fails_without_writes() -> None:
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, 79),
        ),
        has_selection=False,
    )
    service, uow = _service(repository)

    _assert_incomplete(lambda: _initialize(service))

    assert repository.durable_rows == []
    assert uow is not None and uow.commit_count == 0


@pytest.mark.parametrize("fdd_count", [78, 80])
def test_selected_fdd_version_must_have_exactly_79_rows(fdd_count: int) -> None:
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, fdd_count),
        )
    )
    service, _uow = _service(repository)

    _assert_incomplete(lambda: _initialize(service))

    assert repository.durable_rows == []


def test_strict_initialization_rejects_the_existing_in_memory_coverage_service() -> None:
    _assert_incomplete(lambda: _initialize(_service(InMemoryMethodologyCoverageService())[0]))


def test_selection_source_hash_drift_fails_without_writes() -> None:
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, 79),
        ),
        selection_hash_drift=True,
    )
    service, _uow = _service(repository)

    _assert_incomplete(lambda: _initialize(service))

    assert repository.durable_rows == []


def test_cross_tenant_selection_row_is_rejected_without_writes() -> None:
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, 79),
        ),
        selection_tenant_id=FOREIGN_TENANT_ID,
    )
    service, _uow = _service(repository)

    _assert_incomplete(lambda: _initialize(service))

    assert repository.durable_rows == []


def test_repository_failure_rolls_back_every_staged_coverage_row() -> None:
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, 79),
        ),
        fail_insert=True,
    )
    uow = _UnitOfWork(repository)
    service, _ = _service(repository, uow=uow)

    with pytest.raises(RuntimeError, match="injected durable insert failure"):
        _initialize(service)

    assert repository.durable_rows == []
    assert uow.commit_count == 0
    assert uow.rollback_count == 1
