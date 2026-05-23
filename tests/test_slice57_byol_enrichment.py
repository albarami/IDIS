"""Slice 57 BYOL enrichment credential wiring tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.enrichment_credentials import (
    CredentialNotFoundError,
    EncryptionKeyMissingError,
    InMemoryCredentialRepository,
)
from idis.services.enrichment.cache_policy import EnrichmentCacheStore
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from idis.services.enrichment.rights_gate import EnvironmentMode
from idis.services.enrichment.service import (
    EnrichmentService,
    _build_default_registry,
    create_default_enrichment_service,
)
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

TENANT_ID = "tenant-slice57"
SECRET_PROCESS_VALUE = "PROCESS_TOKEN_VALUE_SHOULD_NOT_LEAK"
SECRET_DOTENV_VALUE = "DOTENV_TOKEN_VALUE_SHOULD_NOT_LEAK"


class _HealthyProvider:
    def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
        assert provider_id
        assert credentials
        return True


class _FailingProvider:
    def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
        assert provider_id
        assert credentials
        return False


class _DurableCredentialRepository(InMemoryCredentialRepository):
    is_durable = True


class _EncryptionKeyMissingRepository(InMemoryCredentialRepository):
    def store(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        credentials: dict[str, str],
    ) -> object:
        raise EncryptionKeyMissingError()


class _FakeConnector:
    def __init__(
        self,
        *,
        provider_id: str = "companies_house",
        result: EnrichmentResult | None = None,
        should_raise: bool = False,
        exception_message: str = "safe provider failure",
    ) -> None:
        self._provider_id = provider_id
        self._result = result
        self._should_raise = should_raise
        self._exception_message = exception_message

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def rights_class(self) -> RightsClass:
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=0, no_store=True)

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        if self._should_raise:
            raise RuntimeError(self._exception_message)
        assert ctx.byol_credentials is not None
        if self._result is not None:
            return self._result
        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized={"safe": "result"},
            provenance=EnrichmentProvenance(
                provider_id=self.provider_id,
                source_id=self.provider_id,
                retrieved_at=datetime.now(UTC),
                rights_class=RightsClass.GREEN,
                raw_ref_hash="safehash",
                identifiers_used={"company_name": "safe-public-company"},
            ),
        )


def test_byol_loader_loads_only_expected_provider_keys_and_redacts_values() -> None:
    from idis.services.enrichment.byol_credentials import (
        BYOL_PROVIDER_ENV_SPECS,
        load_byol_credentials_from_env,
    )

    repo = InMemoryCredentialRepository()
    loaded = load_byol_credentials_from_env(
        tenant_id=TENANT_ID,
        credential_repo=repo,
        env={
            "COMPANIES_HOUSE_API_KEY": "safe-companies-house-value",
            "GITHUB_API_TOKEN": "safe-github-value",
            "FRED_API_KEY": "safe-fred-value",
            "FINNHUB_API_KEY": "safe-finnhub-value",
            "FMP_API_KEY": "safe-fmp-value",
            "UNRELATED_PRIVATE_TOKEN": "must-not-load",
        },
    )

    assert set(BYOL_PROVIDER_ENV_SPECS) == {
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
    }
    assert BYOL_PROVIDER_ENV_SPECS["companies_house"].credential_key == "api_key"
    assert BYOL_PROVIDER_ENV_SPECS["github"].credential_key == "token"
    assert BYOL_PROVIDER_ENV_SPECS["fred"].credential_key == "api_key"
    assert BYOL_PROVIDER_ENV_SPECS["finnhub"].credential_key == "api_key"
    assert BYOL_PROVIDER_ENV_SPECS["fmp"].credential_key == "api_key"
    assert {item.provider_id for item in loaded.providers} == set(BYOL_PROVIDER_ENV_SPECS)
    assert repo.load(tenant_id=TENANT_ID, connector_id="github") == {"token": "safe-github-value"}
    with pytest.raises(CredentialNotFoundError):
        repo.load(tenant_id=TENANT_ID, connector_id="UNRELATED_PRIVATE_TOKEN")
    encoded = json.dumps(loaded.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "safe-companies-house-value",
        "safe-github-value",
        "safe-fred-value",
        "safe-finnhub-value",
        "safe-fmp-value",
        "must-not-load",
        "length",
    ):
        assert forbidden not in encoded


def test_byol_loader_uses_explicit_dotenv_with_process_precedence(tmp_path: Path) -> None:
    from idis.services.enrichment.byol_credentials import load_byol_credentials_from_env

    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text(
        "\n".join(
            [
                f"GITHUB_API_TOKEN={SECRET_DOTENV_VALUE}",
                "COMPANIES_HOUSE_API_KEY=dotenv-companies",
                "FRED_API_KEY=dotenv-fred",
                "FINNHUB_API_KEY=dotenv-finnhub",
                "FMP_API_KEY=dotenv-fmp",
            ]
        ),
        encoding="utf-8",
    )
    repo = InMemoryCredentialRepository()

    loaded = load_byol_credentials_from_env(
        tenant_id=TENANT_ID,
        credential_repo=repo,
        env={"GITHUB_API_TOKEN": SECRET_PROCESS_VALUE},
        dotenv_path=dotenv_path,
    )

    assert repo.load(tenant_id=TENANT_ID, connector_id="github") == {"token": SECRET_PROCESS_VALUE}
    github = loaded.provider("github")
    assert github.env_source == "process"
    assert loaded.provider("companies_house").env_source == "dotenv"
    encoded = json.dumps(loaded.model_dump(mode="json"), sort_keys=True)
    assert SECRET_PROCESS_VALUE not in encoded
    assert SECRET_DOTENV_VALUE not in encoded


def test_strict_readiness_preserves_process_and_dotenv_source_for_byol_keys(
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text(
        "\n".join(
            [
                "COMPANIES_HOUSE_API_KEY=dotenv-companies",
                "GITHUB_API_TOKEN=dotenv-github",
                "FRED_API_KEY=dotenv-fred",
                "FINNHUB_API_KEY=dotenv-finnhub",
                "FMP_API_KEY=dotenv-fmp",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_full_live_readiness_report(
        env={"GITHUB_API_TOKEN": "process-github"},
        dotenv_path=dotenv_path,
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
        byol_health_checker=_HealthyProvider(),
    )

    sources = {item.provider_id: item.env_source for item in report.byol_providers}
    assert sources["companies_house"] == "dotenv"
    assert sources["github"] == "process"


def test_default_enrichment_service_can_receive_injected_credential_repo() -> None:
    repo = InMemoryCredentialRepository()
    service = create_default_enrichment_service(
        audit_sink=InMemoryAuditSink(),
        credential_repo=repo,
    )

    provider_ids = {provider["provider_id"] for provider in service.list_providers()}
    assert "companies_house" in provider_ids


def test_provider_matrix_covers_registered_public_byol_and_not_registered_providers() -> None:
    registry = _build_default_registry()
    registered_provider_ids = [descriptor.provider_id for descriptor in registry.list_providers()]
    assert len(registered_provider_ids) == 15
    assert len(registered_provider_ids) == len(set(registered_provider_ids))
    assert set(registered_provider_ids) == {
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
    }

    report = build_strict_full_live_readiness_report(
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
        byol_health_checker=_HealthyProvider(),
    )
    matrix = {item.provider_id: item for item in report.enrichment_provider_matrix}
    registered_rows = [
        item for item in report.enrichment_provider_matrix if item.registry_status == "registered"
    ]
    assert len(registered_rows) == 15
    assert matrix["sec_edgar"].requires_byol is False
    assert matrix["sec_edgar"].credential_repo_status == "not_required"
    assert matrix["sec_edgar"].health_status == "not_checked"
    assert matrix["sec_edgar"].strict_behavior == "strict_fail_closed_on_error"
    assert matrix["companies_house"].env_var == "COMPANIES_HOUSE_API_KEY"
    assert matrix["github"].credential_repo_status == "tenant_credential_loaded"
    assert matrix["github"].health_status == "safe_provider_health_passed"
    assert matrix["patentsview"].requires_byol is False
    assert matrix["epo_open_patent"].registry_status == "not_registered"
    assert matrix["google_trends"].registry_status == "not_registered"
    assert matrix["epo_open_patent"].strict_behavior == "not_registered_not_wired"
    assert matrix["google_trends"].provenance_output_status == "not_output_visible"


def test_strict_readiness_byol_clears_component_when_loaded_and_health_passes() -> None:
    repo = _DurableCredentialRepository()
    report = build_strict_full_live_readiness_report(
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=repo,
        byol_health_checker=_HealthyProvider(),
    )

    provider_statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert provider_statuses == {
        "companies_house": "health_passed",
        "github": "health_passed",
        "fred": "health_passed",
        "finnhub": "health_passed",
        "fmp": "health_passed",
    }
    assert "enrichment BYOL providers" not in report.blocking_components
    assert report.may_proceed is False
    assert "OCR" not in report.blocking_components

    report_with_ocr_evidence = build_strict_full_live_readiness_report(
        data_room_file_extensions=[".png"],
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=repo,
        byol_health_checker=_HealthyProvider(),
        binary_resolver=lambda _binary: None,
    )
    assert "OCR" in report_with_ocr_evidence.blocking_components
    encoded = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    for forbidden in _full_byol_env().values():
        assert forbidden not in encoded


def test_strict_readiness_byol_does_not_clear_with_transient_credentials() -> None:
    report = build_strict_full_live_readiness_report(
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
        byol_health_checker=_HealthyProvider(),
    )

    provider_statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert provider_statuses["github"] == "health_passed"
    assert "enrichment BYOL providers" in report.blocking_components
    byol_inventory = next(
        item
        for item in report.component_inventory
        if item.component_name == "enrichment BYOL providers"
    )
    assert byol_inventory.full_wired is False
    assert byol_inventory.health_check_status == "configured_not_durable"
    assert "durable" in byol_inventory.blocker


def test_strict_readiness_byol_reports_missing_encryption_key_without_crashing() -> None:
    report = build_strict_full_live_readiness_report(
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=_EncryptionKeyMissingRepository(),
        byol_health_checker=_HealthyProvider(),
    )

    statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert statuses["companies_house"] == "env_key_present_not_loaded"
    assert statuses["github"] == "env_key_present_not_loaded"
    assert "enrichment BYOL providers" in report.blocking_components


def test_strict_readiness_byol_blocks_on_missing_key_and_failed_health() -> None:
    repo = InMemoryCredentialRepository()
    env = _full_byol_env()
    del env["FMP_API_KEY"]

    report = build_strict_full_live_readiness_report(
        env=env,
        tenant_id=TENANT_ID,
        byol_credential_repo=repo,
        byol_health_checker=_FailingProvider(),
    )

    statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert statuses["fmp"] == "env_key_missing"
    assert statuses["github"] == "health_failed"
    assert "enrichment BYOL providers" in report.blocking_components
    assert report.may_proceed is False
    encoded = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    assert "provider-health-host" not in encoded


def test_strict_readiness_distinguishes_env_present_not_loaded_and_tenant_missing() -> None:
    report = build_strict_full_live_readiness_report(
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
        load_byol_env_credentials=False,
        byol_health_checker=_HealthyProvider(),
    )

    statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert statuses["companies_house"] == "env_key_present_not_loaded"
    assert statuses["github"] == "env_key_present_not_loaded"
    assert "enrichment BYOL providers" in report.blocking_components

    report_without_env = build_strict_full_live_readiness_report(
        env={},
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
        byol_health_checker=_HealthyProvider(),
    )
    missing_statuses = {item.provider_id: item.status for item in report_without_env.byol_providers}
    assert missing_statuses["companies_house"] == "tenant_credential_missing"

    loaded_without_health = build_strict_full_live_readiness_report(
        env=_full_byol_env(),
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
    )
    loaded_statuses = {
        item.provider_id: item.status for item in loaded_without_health.byol_providers
    }
    assert loaded_statuses["companies_house"] == "tenant_credential_loaded"


def test_strict_full_enrichment_fails_closed_on_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.api.routes.runs as runs_module
    import idis.services.enrichment.service as enrichment_service_module

    registry = EnrichmentProviderRegistry()
    registry.register(_FakeConnector(should_raise=True), requires_byol=True)
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "safe-test-value"},
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=repo,
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setattr(
        enrichment_service_module,
        "create_default_enrichment_service",
        lambda **_kwargs: service,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="Strict enrichment provider failed: companies_house"):
        runs_module._run_full_enrichment(
            run_id="00000000-0000-4000-8000-000000000057",
            tenant_id=TENANT_ID,
            deal_id="safe-public-company",
            created_claim_ids=[],
            calc_ids=[],
        )


def test_service_provider_exception_details_are_redacted() -> None:
    audit_sink = InMemoryAuditSink()
    registry = EnrichmentProviderRegistry()
    registry.register(
        _FakeConnector(
            provider_id="public_safe",
            should_raise=True,
            exception_message="https://secret-host.example/path?token=LEAK_ME",
        ),
        requires_byol=False,
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=audit_sink,
        credential_repo=InMemoryCredentialRepository(),
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )

    result = service.enrich(
        provider_id="public_safe",
        request=EnrichmentRequest(
            tenant_id=TENANT_ID,
            entity_type="COMPANY",
            query={"company_name": "safe-public-company"},
        ),
    )

    assert result.status == EnrichmentStatus.ERROR
    encoded = json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "audit_events": audit_sink.events,
        },
        sort_keys=True,
    )
    for forbidden in ("secret-host", "LEAK_ME", "https://", "token="):
        assert forbidden not in encoded


def test_strict_full_enrichment_public_provider_error_uses_generic_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.api.routes.runs as runs_module
    import idis.services.enrichment.service as enrichment_service_module

    registry = EnrichmentProviderRegistry()
    registry.register(
        _FakeConnector(provider_id="sec_edgar", should_raise=True), requires_byol=False
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=InMemoryCredentialRepository(),
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setattr(
        enrichment_service_module,
        "create_default_enrichment_service",
        lambda **_kwargs: service,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="Strict enrichment provider failed: sec_edgar") as exc:
        runs_module._run_full_enrichment(
            run_id="00000000-0000-4000-8000-000000000061",
            tenant_id=TENANT_ID,
            deal_id="safe-public-company",
            created_claim_ids=[],
            calc_ids=[],
        )
    assert "BYOL" not in str(exc.value)


def test_strict_full_enrichment_fails_closed_on_blocked_missing_byol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.api.routes.runs as runs_module
    import idis.services.enrichment.service as enrichment_service_module

    registry = EnrichmentProviderRegistry()
    registry.register(
        _FakeConnector(
            result=EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"reason": "BYOL credentials not configured for this provider"},
            )
        ),
        requires_byol=True,
    )
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "safe-test-value"},
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=repo,
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setattr(
        enrichment_service_module,
        "create_default_enrichment_service",
        lambda **_kwargs: service,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="Strict enrichment provider blocked: companies_house"):
        runs_module._run_full_enrichment(
            run_id="00000000-0000-4000-8000-000000000060",
            tenant_id=TENANT_ID,
            deal_id="safe-public-company",
            created_claim_ids=[],
            calc_ids=[],
        )


def test_non_strict_full_enrichment_keeps_best_effort_on_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.api.routes.runs as runs_module
    import idis.services.enrichment.service as enrichment_service_module

    registry = EnrichmentProviderRegistry()
    registry.register(_FakeConnector(should_raise=True), requires_byol=True)
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "safe-test-value"},
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=repo,
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
    monkeypatch.setattr(
        enrichment_service_module,
        "create_default_enrichment_service",
        lambda **_kwargs: service,
        raising=False,
    )

    result = runs_module._run_full_enrichment(
        run_id="00000000-0000-4000-8000-000000000058",
        tenant_id=TENANT_ID,
        deal_id="safe-public-company",
        created_claim_ids=[],
        calc_ids=[],
    )

    assert result["provider_count"] == 1
    assert result["result_count"] == 0
    assert result["blocked_count"] == 0


def test_full_enrichment_preserves_provenance_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    import idis.api.routes.runs as runs_module
    import idis.services.enrichment.service as enrichment_service_module

    registry = EnrichmentProviderRegistry()
    registry.register(_FakeConnector(), requires_byol=True)
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "safe-test-value"},
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=repo,
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    monkeypatch.setattr(
        enrichment_service_module,
        "create_default_enrichment_service",
        lambda **_kwargs: service,
        raising=False,
    )

    result = runs_module._run_full_enrichment(
        run_id="00000000-0000-4000-8000-000000000059",
        tenant_id=TENANT_ID,
        deal_id="safe-public-company",
        created_claim_ids=[],
        calc_ids=[],
    )

    assert result["result_count"] == 1
    assert result["enrichment_refs"]
    ref = next(iter(result["enrichment_refs"].values()))
    assert ref["provider_id"] == "companies_house"
    assert ref["source_id"] == "companies_house"


def test_readiness_json_does_not_leak_secret_metadata_or_local_paths(tmp_path: Path) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text(
        "\n".join(
            [
                "COMPANIES_HOUSE_API_KEY=DOTENV_SECRET_WITH_UNIQUE_SUFFIX",
                "GITHUB_API_TOKEN=DOTENV_GITHUB_SECRET",
                "FRED_API_KEY=DOTENV_FRED_SECRET",
                "FINNHUB_API_KEY=DOTENV_FINNHUB_SECRET",
                "FMP_API_KEY=DOTENV_FMP_SECRET",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_full_live_readiness_report(
        env={"GITHUB_API_TOKEN": "PROCESS_SECRET_WITH_UNIQUE_SUFFIX"},
        dotenv_path=dotenv_path,
        tenant_id=TENANT_ID,
        byol_credential_repo=InMemoryCredentialRepository(),
        byol_health_checker=_HealthyProvider(),
    )

    encoded = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "DOTENV_SECRET_WITH_UNIQUE_SUFFIX",
        "PROCESS_SECRET_WITH_UNIQUE_SUFFIX",
        "DOTENV_GITHUB_SECRET",
        "DOTENV_FRED_SECRET",
        "DOTENV_FINNHUB_SECRET",
        "DOTENV_FMP_SECRET",
        "://",
        "supabase.co",
        "localhost",
        "strict-vars",
        str(tmp_path),
        "length",
    ):
        assert forbidden not in encoded


def test_registered_connectors_do_not_serialize_provider_exception_strings() -> None:
    connector_dir = Path("src/idis/services/enrichment/connectors")
    for path in connector_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert 'normalized={"error": str(exc)}' not in source
        assert 'normalized={"error": f"Provider fetch failed: {exc}"}' not in source
        assert ", exc)" not in source


def _full_byol_env() -> dict[str, str]:
    return {
        "COMPANIES_HOUSE_API_KEY": "safe-companies-house-token",
        "GITHUB_API_TOKEN": "safe-github-token",
        "FRED_API_KEY": "safe-fred-token",
        "FINNHUB_API_KEY": "safe-finnhub-token",
        "FMP_API_KEY": "safe-fmp-token",
    }
