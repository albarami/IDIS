"""Run-scoped methodology coverage initialization service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from idis.methodology.models import MethodologyRegistry
from idis.methodology.registry import load_registry_from_json_file
from idis.models.methodology_coverage import (
    MethodologyCoverageInitializationResult,
    MethodologyCoverageInitializationStatus,
    MethodologyCoverageRecord,
    MethodologyCoverageRecordSummary,
)
from idis.services.methodology.coverage import InMemoryMethodologyCoverageService

RegistryLoader = Callable[[], MethodologyRegistry]

DEFAULT_METHODOLOGY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "methodology" / "templates" / "commercial_dd_v1.json"
)


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
