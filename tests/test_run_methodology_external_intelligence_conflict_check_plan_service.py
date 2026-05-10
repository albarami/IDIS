"""Tests for Slice 13 external intelligence conflict-check plan service."""

from __future__ import annotations

from uuid import UUID

from idis.models.external_intelligence_conflict_check_plan_materialization import (
    ExternalIntelligencePlanCheckStatus,
    MethodologyExternalIntelligenceConflictCheckPlanReason,
    MethodologyExternalIntelligenceConflictCheckPlanStatus,
)
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageStatus,
)
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentRequest,
    EnrichmentResult,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from idis.services.runs.methodology_external_intelligence_conflict_check_plan import (
    InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService,
)
from idis.services.runs.methodology_validated_evidence_package import (
    InMemoryRunMethodologyValidatedEvidencePackageService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)
from tests.test_run_methodology_validated_evidence_package_service import _court_record


class _ExplodingConnector:
    """Connector that proves Slice 13 never calls provider fetch."""

    def __init__(self, provider_id: str, rights_class: RightsClass = RightsClass.GREEN) -> None:
        self._provider_id = provider_id
        self._rights_class = rights_class
        self.fetch_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def rights_class(self) -> RightsClass:
        return self._rights_class

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=3600)

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        self.fetch_count += 1
        raise AssertionError("Slice 13 plan construction must not fetch provider data")


def test_builds_provider_plan_from_vep_without_live_connector_calls() -> None:
    sec = _ExplodingConnector("sec_edgar")
    companies_house = _ExplodingConnector("companies_house")
    result, plans = _service(sec, companies_house, requires_byol={"companies_house"}).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[_vep_record()],
    )

    plan = plans[0]

    assert result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    assert plan.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    assert {check.provider_id for check in plan.checks} == {"sec_edgar", "companies_house"}
    assert plan.to_summary().by_status == {"blocked": 1, "deferred": 1}
    assert any(
        check.status == ExternalIntelligencePlanCheckStatus.DEFERRED
        and "missing_query_identifiers" in check.reason_codes
        for check in plan.checks
        if check.provider_id == "sec_edgar"
    )
    assert any(
        check.status == ExternalIntelligencePlanCheckStatus.BLOCKED
        and "provider_requires_byol" in check.reason_codes
        for check in plan.checks
        if check.provider_id == "companies_house"
    )
    assert sec.fetch_count == 0
    assert companies_house.fetch_count == 0
    assert "external conflict checks executed" not in str(result.to_run_step_summary())


def test_safe_vep_shell_is_sufficient_for_plan_construction() -> None:
    package = _vep_record()
    result, plans = _service(_ExplodingConnector("sec_edgar")).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[package.to_shell()],
    )

    assert result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    assert plans[0].package_id == package.package_id
    assert plans[0].checks[0].provider_id == "sec_edgar"


def test_provider_check_ids_are_deterministically_scoped_to_vep_context() -> None:
    service = _service(_ExplodingConnector("sec_edgar"))
    package = _vep_record()
    other_run_id = "22222222-2222-4222-8222-222222222222"
    other_package = package.model_copy(
        update={"run_id": other_run_id, "package_id": "package-other"}
    )

    first_result, first_plans = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[package],
    )
    repeated_result, repeated_plans = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[package],
    )
    other_result, other_plans = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=other_run_id,
        validated_evidence_packages=[other_package],
    )

    first_check_id = first_plans[0].checks[0].check_id
    repeated_check_id = repeated_plans[0].checks[0].check_id
    other_check_id = other_plans[0].checks[0].check_id

    assert first_result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    assert (
        repeated_result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    )
    assert other_result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    assert UUID(first_check_id)
    assert first_check_id == repeated_check_id
    assert first_check_id != other_check_id
    assert first_check_id != "provider-check-sec_edgar"


def test_default_registry_path_uses_provider_metadata_only() -> None:
    result, plans = InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[_vep_record()],
    )

    summary = result.to_run_step_summary()

    assert result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    assert plans
    assert result.summary.check_count > 0
    assert "live_provider_calls_deferred" in summary["reason_codes"]
    assert "external conflict checks executed" not in str(summary)


def test_missing_vep_fails_closed_without_plan() -> None:
    result, plans = _service(_ExplodingConnector("sec_edgar")).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[],
    )

    assert result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.FAILED
    assert plans == []
    assert result.plan_shells == []
    assert result.rejections[0].reason == (
        MethodologyExternalIntelligenceConflictCheckPlanReason.MISSING_VALIDATED_EVIDENCE_PACKAGE
    )


def test_cross_scope_vep_fails_closed_without_plan() -> None:
    package = _vep_record().model_copy(update={"tenant_id": "tenant-other"})

    result, plans = _service(_ExplodingConnector("sec_edgar")).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[package],
    )

    assert result.status == MethodologyExternalIntelligenceConflictCheckPlanStatus.FAILED
    assert plans == []
    assert result.rejections[0].reason == (
        MethodologyExternalIntelligenceConflictCheckPlanReason.TENANT_OR_RUN_MISMATCH
    )


def _service(
    *connectors: _ExplodingConnector,
    requires_byol: set[str] | None = None,
) -> InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService:
    registry = EnrichmentProviderRegistry()
    byol_ids = requires_byol or set()
    for connector in connectors:
        registry.register(connector, requires_byol=connector.provider_id in byol_ids)
    return InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService(
        provider_registry=registry,
    )


def _vep_record():
    run_result, packages = InMemoryRunMethodologyValidatedEvidencePackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[
            _court_record().model_copy(
                update={"status": MethodologyValidatedEvidencePackageStatus.COMPLETED.value}
            )
        ],
    )
    assert run_result.status == MethodologyValidatedEvidencePackageStatus.COMPLETED
    return packages[0]
