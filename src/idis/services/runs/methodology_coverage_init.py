"""Run-scoped methodology coverage initialization service."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Never

from idis.methodology.models import MethodologyRegistry, MethodologyType
from idis.methodology.registry import load_registry_from_json_file
from idis.models.methodology_coverage import (
    DurableMethodologyCoverageInitializationResult,
    MethodologyCoverageInitializationResult,
    MethodologyCoverageInitializationStatus,
    MethodologyCoverageRecord,
    MethodologyCoverageRecordSummary,
    MethodologyCoverageStatus,
    MethodologyCoverageSummary,
    SelectedMethodologyVersionSummary,
)
from idis.services.methodology.coverage import InMemoryMethodologyCoverageService

RegistryLoader = Callable[[], MethodologyRegistry]

DEFAULT_METHODOLOGY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "methodology" / "templates" / "commercial_dd_v1.json"
)
METHODOLOGY_COVERAGE_INCOMPLETE = "METHODOLOGY_COVERAGE_INCOMPLETE"
_EXPECTED_QUESTION_COUNTS = {
    MethodologyType.COMMERCIAL_DD: 21,
    MethodologyType.FINANCIAL_DD: 79,
}
_EXPECTED_SOURCE_TYPES = {
    MethodologyType.COMMERCIAL_DD: "checked_in_template",
    MethodologyType.FINANCIAL_DD: "operator_workbook",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class MethodologyCoverageInitializationError(RuntimeError):
    """Fail-closed durable methodology selection or coverage error."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = METHODOLOGY_COVERAGE_INCOMPLETE
        super().__init__(self.code)


def _coverage_incomplete() -> Never:
    raise MethodologyCoverageInitializationError


def load_default_methodology_registry() -> MethodologyRegistry:
    """Load the deterministic checked-in CDD methodology registry."""
    return load_registry_from_json_file(DEFAULT_METHODOLOGY_REGISTRY_PATH)


class InMemoryRunMethodologyCoverageInitService:
    """Initialize run-scoped methodology coverage records in memory."""

    def __init__(
        self,
        *,
        coverage_service: InMemoryMethodologyCoverageService | None = None,
        registry_loader_fn: RegistryLoader | None = None,
    ) -> None:
        """Initialize the service with injectable dependencies."""
        self._coverage_service = coverage_service or InMemoryMethodologyCoverageService()
        self._registry_loader_fn = registry_loader_fn or load_default_methodology_registry

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        registry: MethodologyRegistry | None = None,
    ) -> tuple[MethodologyCoverageInitializationResult, list[MethodologyCoverageRecord]]:
        """Initialize coverage records for every question in the selected registry."""
        selected_registry = self._validated_registry(registry or self._registry_loader_fn())
        records = self._coverage_service.initialize_coverage(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            registry=selected_registry,
        )
        summary = self._coverage_service.summarize(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
        version = selected_registry.current_version
        result = MethodologyCoverageInitializationResult(
            status=MethodologyCoverageInitializationStatus.COMPLETED,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_id=selected_registry.methodology_id,
            methodology_version_id=version.methodology_version_id,
            methodology_type=selected_registry.methodology_type,
            registry_hash=selected_registry.registry_hash,
            coverage_record_ids=[record.coverage_record_id for record in records],
            methodology_question_ids=[record.methodology_question_id for record in records],
            coverage_records=[
                MethodologyCoverageRecordSummary.from_record(record) for record in records
            ],
            summary=summary,
        )
        return result, records

    @staticmethod
    def _validated_registry(registry: MethodologyRegistry) -> MethodologyRegistry:
        """Re-validate injected/loaded registries at the run boundary."""
        return MethodologyRegistry.model_validate(registry.model_dump(mode="json"))


class DurableRunMethodologyCoverageInitService:
    """Initialize the immutable, persisted CDD+FDD selection in one transaction."""

    def __init__(
        self,
        *,
        methodology_repository: object,
        runs_repository: object,
        unit_of_work: object | None,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        """Bind only durable repository seams; no in-memory fallback is permitted."""
        if not _is_durable_postgres_methodology_repository(methodology_repository):
            _coverage_incomplete()
        if not _is_durable_runs_repository(runs_repository):
            _coverage_incomplete()
        if unit_of_work is None or not callable(getattr(unit_of_work, "transaction", None)):
            _coverage_incomplete()
        if (
            not isinstance(lease_owner_id, str)
            or not lease_owner_id.strip()
            or not isinstance(lease_fencing_token, int)
            or isinstance(lease_fencing_token, bool)
            or lease_fencing_token <= 0
        ):
            _coverage_incomplete()
        self._methodology_repository: Any = methodology_repository
        self._runs_repository: Any = runs_repository
        self._unit_of_work: Any = unit_of_work
        self._lease_owner_id = lease_owner_id.strip()
        self._lease_fencing_token = lease_fencing_token

    def initialize_for_run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
    ) -> tuple[
        DurableMethodologyCoverageInitializationResult,
        list[MethodologyCoverageRecord],
    ]:
        """Validate the persisted selection and atomically insert all 100 coverage rows."""
        scope = _validated_scope(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
        try:
            self._runs_repository.require_run_scope(
                tenant_id=scope[0],
                deal_id=scope[1],
                run_id=scope[2],
            )
        except LookupError as exc:
            raise MethodologyCoverageInitializationError from exc
        selection = self._methodology_repository.load_run_selection(
            tenant_id=scope[0],
            deal_id=scope[1],
            run_id=scope[2],
        )
        _validate_selection_scope(selection=selection, scope=scope)
        registries = _validate_selected_registries(
            selection=selection,
            registries=self._methodology_repository.load_selected_registries(selection=selection),
        )
        records = _coverage_records_for_registries(scope=scope, registries=registries)

        with self._unit_of_work.transaction():
            # This locks the run row in the same transaction as the 100 output
            # writes.  A reclaim cannot overtake the write, and a zombie owner
            # or expired token fails with WORKER_LEASE_FENCED before mutation.
            self._runs_repository.require_active_worker_lease(
                deal_id=scope[1],
                run_id=scope[2],
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
            )
            persisted = self._methodology_repository.insert_coverage_records(records=records)
            persisted_records = _validated_persisted_records(
                persisted=persisted,
                expected=records,
                scope=scope,
            )
            # Recheck against statement time immediately before commit so an
            # operation that outlives its lease rolls the complete write back.
            self._runs_repository.require_active_worker_lease(
                deal_id=scope[1],
                run_id=scope[2],
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
            )

        result = _durable_result(
            selection=selection,
            scope=scope,
            registries=registries,
            records=persisted_records,
        )
        return result, persisted_records


def validate_durable_methodology_coverage_records(
    *,
    records: object,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> list[MethodologyCoverageRecord]:
    """Validate the exact persisted CDD+FDD row set used during strict resume."""
    scope = _validated_scope(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
    if isinstance(records, (str, bytes)) or not isinstance(records, Iterable):
        _coverage_incomplete()
    try:
        validated = [
            MethodologyCoverageRecord.model_validate(record, from_attributes=True)
            for record in records
        ]
    except (TypeError, ValueError):
        _coverage_incomplete()
    if len(validated) != 100:
        _coverage_incomplete()
    if len({record.coverage_record_id for record in validated}) != 100:
        _coverage_incomplete()
    if len({record.methodology_question_id for record in validated}) != 100:
        _coverage_incomplete()
    if any((record.tenant_id, record.deal_id, record.run_id) != scope for record in validated):
        _coverage_incomplete()
    if any(record.methodology_id != record.methodology_type.value for record in validated):
        _coverage_incomplete()
    if Counter(record.methodology_type for record in validated) != Counter(
        _EXPECTED_QUESTION_COUNTS
    ):
        _coverage_incomplete()
    version_ids_by_type = {
        methodology_type: {
            record.methodology_version_id
            for record in validated
            if record.methodology_type is methodology_type
        }
        for methodology_type in _EXPECTED_QUESTION_COUNTS
    }
    if any(len(version_ids) != 1 for version_ids in version_ids_by_type.values()):
        _coverage_incomplete()
    return sorted(validated, key=lambda record: record.coverage_record_id)


def _is_durable_postgres_methodology_repository(repository: object) -> bool:
    if getattr(repository, "is_durable", None) is not True:
        return False
    backend = getattr(repository, "backend", None)
    if not isinstance(backend, str) or not backend.strip().lower().startswith("postgres"):
        return False
    return all(
        callable(getattr(repository, method, None))
        for method in (
            "load_run_selection",
            "load_selected_registries",
            "insert_coverage_records",
        )
    )


def _is_durable_runs_repository(repository: object) -> bool:
    return getattr(repository, "is_durable", None) is True and all(
        callable(getattr(repository, method, None))
        for method in ("require_run_scope", "require_active_worker_lease")
    )


def _validated_scope(*, tenant_id: str, deal_id: str, run_id: str) -> tuple[str, str, str]:
    values = (tenant_id.strip(), deal_id.strip(), run_id.strip())
    if not all(values):
        _coverage_incomplete()
    return values


def _validate_selection_scope(*, selection: Any, scope: tuple[str, str, str]) -> None:
    if selection is None or getattr(selection, "immutable", None) is not True:
        _coverage_incomplete()
    if (
        getattr(selection, "tenant_id", None),
        getattr(selection, "deal_id", None),
        getattr(selection, "run_id", None),
    ) != scope:
        _coverage_incomplete()
    selection_id = getattr(selection, "selection_id", None)
    if not isinstance(selection_id, str) or not selection_id.strip():
        _coverage_incomplete()
    state = getattr(selection, "state", None)
    if state is not None and str(getattr(state, "value", state)).upper() != "FROZEN":
        _coverage_incomplete()

    version_ids = _two_string_values(getattr(selection, "selected_version_ids", None))
    source_hashes = _two_string_values(getattr(selection, "selected_source_hashes", None))
    registry_hashes = _two_string_values(getattr(selection, "selected_registry_hashes", None))
    if version_ids is None or source_hashes is None or registry_hashes is None:
        _coverage_incomplete()
    if len(set(version_ids)) != 2:
        _coverage_incomplete()
    if not all(_SHA256_RE.fullmatch(value) for value in (*source_hashes, *registry_hashes)):
        _coverage_incomplete()


def _two_string_values(value: object) -> tuple[str, str] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None
    items = tuple(value)
    if len(items) != 2 or any(not isinstance(item, str) or not item.strip() for item in items):
        return None
    return items[0].strip(), items[1].strip()


def _validate_selected_registries(
    *,
    selection: Any,
    registries: object,
) -> tuple[MethodologyRegistry, MethodologyRegistry]:
    if isinstance(registries, (str, bytes)) or not isinstance(registries, Iterable):
        _coverage_incomplete()
    try:
        validated = tuple(
            MethodologyRegistry.model_validate(registry) for registry in tuple(registries)
        )
    except (TypeError, ValueError):
        _coverage_incomplete()
    if len(validated) != 2:
        _coverage_incomplete()

    by_type = {registry.methodology_type: registry for registry in validated}
    if set(by_type) != set(_EXPECTED_QUESTION_COUNTS) or len(by_type) != 2:
        _coverage_incomplete()

    version_ids = _two_string_values(selection.selected_version_ids)
    source_hashes = _two_string_values(selection.selected_source_hashes)
    registry_hashes = _two_string_values(selection.selected_registry_hashes)
    if version_ids is None or source_hashes is None or registry_hashes is None:
        _coverage_incomplete()
    selected_integrity = {
        version_id: (source_hash, registry_hash)
        for version_id, source_hash, registry_hash in zip(
            version_ids, source_hashes, registry_hashes, strict=True
        )
    }
    if len(selected_integrity) != 2:
        _coverage_incomplete()

    global_question_ids: set[str] = set()
    fdd_source_rows: set[tuple[str, int]] = set()
    ordered: list[MethodologyRegistry] = []
    for methodology_type in (MethodologyType.COMMERCIAL_DD, MethodologyType.FINANCIAL_DD):
        registry = by_type[methodology_type]
        version = registry.current_version
        expected_count = _EXPECTED_QUESTION_COUNTS[methodology_type]
        if (
            registry.methodology_id != methodology_type.value
            or len(version.questions) != expected_count
        ):
            _coverage_incomplete()
        version_state = getattr(version, "state", None)
        if (
            version_state is not None
            and str(getattr(version_state, "value", version_state)).upper() != "FROZEN"
        ):
            _coverage_incomplete()
        integrity = selected_integrity.get(version.methodology_version_id)
        if integrity != (version.source_hash, registry.registry_hash):
            _coverage_incomplete()
        if _SHA256_RE.fullmatch(version.source_hash) is None:
            _coverage_incomplete()

        for question in version.questions:
            if question.methodology_question_id in global_question_ids:
                _coverage_incomplete()
            global_question_ids.add(question.methodology_question_id)
            if question.source_trace.source_hash != version.source_hash:
                _coverage_incomplete()
            if question.source_trace.source_type != _EXPECTED_SOURCE_TYPES[methodology_type]:
                _coverage_incomplete()
            if question.sheet_or_source_section != question.source_trace.sheet_or_section:
                _coverage_incomplete()
            if methodology_type is MethodologyType.FINANCIAL_DD:
                row_number = question.source_row_number
                if (
                    row_number is None
                    or row_number <= 0
                    or question.source_trace.row_number != row_number
                ):
                    _coverage_incomplete()
                source_row = (question.source_trace.sheet_or_section, row_number)
                if source_row in fdd_source_rows:
                    _coverage_incomplete()
                fdd_source_rows.add(source_row)
        ordered.append(registry)

    if set(selected_integrity) != {
        registry.current_version.methodology_version_id for registry in ordered
    }:
        _coverage_incomplete()
    return ordered[0], ordered[1]


def _coverage_records_for_registries(
    *,
    scope: tuple[str, str, str],
    registries: tuple[MethodologyRegistry, MethodologyRegistry],
) -> list[MethodologyCoverageRecord]:
    records = [
        MethodologyCoverageRecord(
            tenant_id=scope[0],
            deal_id=scope[1],
            run_id=scope[2],
            methodology_id=registry.methodology_id,
            methodology_version_id=registry.current_version.methodology_version_id,
            methodology_question_id=question.methodology_question_id,
            methodology_type=question.methodology_type,
            section=question.section,
            status=MethodologyCoverageStatus.NOT_STARTED,
        )
        for registry in registries
        for question in sorted(
            registry.current_version.questions,
            key=lambda candidate: candidate.methodology_question_id,
        )
    ]
    if len(records) != 100 or len({record.coverage_record_id for record in records}) != 100:
        _coverage_incomplete()
    return records


def _validated_persisted_records(
    *,
    persisted: object,
    expected: list[MethodologyCoverageRecord],
    scope: tuple[str, str, str],
) -> list[MethodologyCoverageRecord]:
    if isinstance(persisted, (str, bytes)) or not isinstance(persisted, Iterable):
        _coverage_incomplete()
    try:
        records = [
            MethodologyCoverageRecord.model_validate(record, from_attributes=True)
            for record in persisted
        ]
    except (TypeError, ValueError):
        _coverage_incomplete()
    records.sort(key=lambda record: record.coverage_record_id)
    if len(records) != 100:
        _coverage_incomplete()
    expected_by_id = {record.coverage_record_id: record for record in expected}
    if {record.coverage_record_id for record in records} != set(expected_by_id):
        _coverage_incomplete()
    for record in records:
        expected_record = expected_by_id[record.coverage_record_id]
        if record != expected_record or (record.tenant_id, record.deal_id, record.run_id) != scope:
            _coverage_incomplete()
        if record.status is not MethodologyCoverageStatus.NOT_STARTED:
            _coverage_incomplete()
    return records


def _durable_result(
    *,
    selection: Any,
    scope: tuple[str, str, str],
    registries: tuple[MethodologyRegistry, MethodologyRegistry],
    records: list[MethodologyCoverageRecord],
) -> DurableMethodologyCoverageInitializationResult:
    by_status = Counter(record.status.value for record in records)
    by_type = Counter(record.methodology_type.value for record in records)
    by_section = Counter(record.section for record in records)
    summary = MethodologyCoverageSummary(
        tenant_id=scope[0],
        deal_id=scope[1],
        run_id=scope[2],
        total_questions=len(records),
        by_status=dict(sorted(by_status.items())),
        by_methodology_type=dict(sorted(by_type.items())),
        by_section=dict(sorted(by_section.items())),
    )
    selected = [
        SelectedMethodologyVersionSummary(
            methodology_id=registry.methodology_id,
            methodology_version_id=registry.current_version.methodology_version_id,
            methodology_type=registry.methodology_type,
            source_hash=registry.current_version.source_hash,
            registry_hash=registry.registry_hash,
            question_count=len(registry.current_version.questions),
        )
        for registry in registries
    ]
    return DurableMethodologyCoverageInitializationResult(
        status=MethodologyCoverageInitializationStatus.COMPLETED,
        tenant_id=scope[0],
        deal_id=scope[1],
        run_id=scope[2],
        selection_id=str(selection.selection_id),
        selected_methodologies=selected,
        coverage_record_ids=[record.coverage_record_id for record in records],
        methodology_question_ids=[record.methodology_question_id for record in records],
        coverage_records=[
            MethodologyCoverageRecordSummary.from_record(record) for record in records
        ],
        summary=summary,
    )
