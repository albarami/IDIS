"""Secret-safe BYOL credential loading for enrichment providers."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from idis.persistence.repositories.enrichment_credentials import (
    CredentialNotFoundError,
    EncryptionKeyMissingError,
)
from idis.services.enrichment.models import (
    EnrichmentConnector,
    EnrichmentContext,
    EnrichmentPurpose,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
)

PROCESS_ENV_SOURCE = "process"
DOTENV_ENV_SOURCE = "dotenv"
MISSING_ENV_SOURCE = "missing"


class ByolCredentialRepository(Protocol):
    """Repository contract used by Slice 57 BYOL credential wiring."""

    def store(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        credentials: dict[str, str],
    ) -> object:
        """Store credentials for a tenant and provider."""
        ...

    def load(self, *, tenant_id: str, connector_id: str) -> dict[str, str]:
        """Load credentials for a tenant and provider."""
        ...

    def exists(self, *, tenant_id: str, connector_id: str) -> bool:
        """Return whether active credentials exist for a tenant and provider."""
        ...

    @property
    def is_durable(self) -> bool:
        """Return whether the repository persists credentials durably."""
        ...


class ByolProviderHealthChecker(Protocol):
    """Injectable safe provider-health checker for strict readiness."""

    def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
        """Return whether the provider is healthy for the supplied credentials."""
        ...


class SafeByolProviderHealthChecker:
    """Safe live health checks for BYOL providers using public identifiers only."""

    def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
        """Return whether a provider accepts the supplied credentials."""
        connector = _build_byol_health_connector(provider_id)
        result = connector.fetch(
            _safe_health_request(provider_id=provider_id),
            EnrichmentContext(
                timeout_seconds=10.0,
                max_retries=0,
                request_id="strict-byol-health",
                byol_credentials=credentials,
            ),
        )
        return result.status not in {
            EnrichmentStatus.BLOCKED_MISSING_BYOL,
            EnrichmentStatus.BLOCKED_RIGHTS,
            EnrichmentStatus.ERROR,
        }


class ByolProviderStatus(StrEnum):
    """Secret-safe provider-level BYOL readiness statuses."""

    ENV_KEY_MISSING = "env_key_missing"
    ENV_KEY_PRESENT_NOT_LOADED = "env_key_present_not_loaded"
    TENANT_CREDENTIAL_MISSING = "tenant_credential_missing"
    TENANT_CREDENTIAL_LOADED = "tenant_credential_loaded"
    HEALTH_PASSED = "health_passed"
    HEALTH_FAILED = "health_failed"


class EnrichmentProviderRegistryStatus(StrEnum):
    """Whether a provider is registered in the enrichment registry."""

    REGISTERED = "registered"
    NOT_REGISTERED = "not_registered"


@dataclass(frozen=True)
class ByolProviderEnvSpec:
    """Mapping from one runtime env key to one tenant credential key."""

    provider_id: str
    env_var: str
    credential_key: str


class EnrichmentProviderDescriptorLike(Protocol):
    """Minimal descriptor contract needed for the strict provider matrix."""

    @property
    def provider_id(self) -> str:
        """Registered provider identifier."""
        ...

    @property
    def requires_byol(self) -> bool:
        """Whether the provider requires tenant BYOL credentials."""
        ...


BYOL_PROVIDER_ENV_SPECS: dict[str, ByolProviderEnvSpec] = {
    "companies_house": ByolProviderEnvSpec(
        provider_id="companies_house",
        env_var="COMPANIES_HOUSE_API_KEY",
        credential_key="api_key",
    ),
    "github": ByolProviderEnvSpec(
        provider_id="github",
        env_var="GITHUB_API_TOKEN",
        credential_key="token",
    ),
    "fred": ByolProviderEnvSpec(
        provider_id="fred",
        env_var="FRED_API_KEY",
        credential_key="api_key",
    ),
    "finnhub": ByolProviderEnvSpec(
        provider_id="finnhub",
        env_var="FINNHUB_API_KEY",
        credential_key="api_key",
    ),
    "fmp": ByolProviderEnvSpec(
        provider_id="fmp",
        env_var="FMP_API_KEY",
        credential_key="api_key",
    ),
}


class ByolProviderReadiness(BaseModel):
    """Secret-free readiness for one BYOL provider."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    env_var: str
    env_source: str
    status: ByolProviderStatus


class EnrichmentProviderMatrixEntry(BaseModel):
    """Secret-free provider matrix row for strict enrichment readiness."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    registry_status: EnrichmentProviderRegistryStatus
    requires_byol: bool
    env_var: str | None = None
    credential_repo_status: str
    health_status: str
    strict_behavior: str
    provenance_output_status: str


class ByolCredentialLoadReport(BaseModel):
    """Secret-free report for env-to-repository credential loading."""

    model_config = ConfigDict(extra="forbid")

    providers: list[ByolProviderReadiness] = Field(default_factory=list)

    def provider(self, provider_id: str) -> ByolProviderReadiness:
        """Return a provider loading result by provider id."""
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider
        raise KeyError(provider_id)


@dataclass(frozen=True)
class _ByolEnvSource:
    effective_env: dict[str, str]
    process_keys: frozenset[str]
    dotenv_keys: frozenset[str]


def load_byol_credentials_from_env(
    *,
    tenant_id: str,
    credential_repo: ByolCredentialRepository,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> ByolCredentialLoadReport:
    """Load only approved BYOL provider keys into tenant credential storage.

    Args:
        tenant_id: Tenant scope for stored credentials.
        credential_repo: Repository abstraction receiving the credentials.
        env: Process environment mapping. Defaults to ``os.environ``.
        dotenv_path: Optional explicit strict-runtime dotenv path.

    Returns:
        Secret-free loading report with provider-level statuses.
    """
    env_source = _build_byol_env_source(
        process_env=os.environ if env is None else env,
        dotenv_path=dotenv_path,
    )
    providers: list[ByolProviderReadiness] = []
    for spec in BYOL_PROVIDER_ENV_SPECS.values():
        value = str(env_source.effective_env.get(spec.env_var, "")).strip()
        if not value:
            providers.append(
                _provider_readiness(
                    spec=spec,
                    env_source=env_source,
                    status=ByolProviderStatus.ENV_KEY_MISSING,
                )
            )
            continue

        try:
            credential_repo.store(
                tenant_id=tenant_id,
                connector_id=spec.provider_id,
                credentials={spec.credential_key: value},
            )
        except EncryptionKeyMissingError:
            providers.append(
                _provider_readiness(
                    spec=spec,
                    env_source=env_source,
                    status=ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED,
                )
            )
            continue
        providers.append(
            _provider_readiness(
                spec=spec,
                env_source=env_source,
                status=ByolProviderStatus.TENANT_CREDENTIAL_LOADED,
            )
        )

    return ByolCredentialLoadReport(providers=providers)


def assess_byol_provider_readiness(
    *,
    tenant_id: str | None,
    credential_repo: ByolCredentialRepository | None,
    env: Mapping[str, str],
    env_sources: Mapping[str, str] | None = None,
    load_env_credentials: bool,
    health_checker: ByolProviderHealthChecker | None,
) -> list[ByolProviderReadiness]:
    """Return secret-safe provider readiness for strict full-live inventory."""
    if tenant_id is None or credential_repo is None:
        return [
            ByolProviderReadiness(
                provider_id=spec.provider_id,
                env_var=spec.env_var,
                env_source=_readiness_env_source(
                    env=env,
                    env_sources=env_sources,
                    spec=spec,
                ),
                status=ByolProviderStatus.ENV_KEY_MISSING
                if not _has_value(env, spec.env_var)
                else ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED,
            )
            for spec in BYOL_PROVIDER_ENV_SPECS.values()
        ]

    load_report: ByolCredentialLoadReport | None = None
    if load_env_credentials:
        load_report = load_byol_credentials_from_env(
            tenant_id=tenant_id,
            credential_repo=credential_repo,
            env=env,
        )
    load_status_by_provider = (
        {provider.provider_id: provider.status for provider in load_report.providers}
        if load_report is not None
        else {}
    )

    any_env_present = any(
        _has_value(env, spec.env_var) for spec in BYOL_PROVIDER_ENV_SPECS.values()
    )
    readiness: list[ByolProviderReadiness] = []
    for spec in BYOL_PROVIDER_ENV_SPECS.values():
        env_present = _has_value(env, spec.env_var)
        load_status = load_status_by_provider.get(spec.provider_id)
        if load_status == ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED:
            readiness.append(
                ByolProviderReadiness(
                    provider_id=spec.provider_id,
                    env_var=spec.env_var,
                    env_source=_readiness_env_source(
                        env=env,
                        env_sources=env_sources,
                        spec=spec,
                    ),
                    status=load_status,
                )
            )
            continue
        status = _credential_status(
            tenant_id=tenant_id,
            credential_repo=credential_repo,
            spec=spec,
            env_present=env_present,
            any_env_present=any_env_present,
            load_env_credentials=load_env_credentials,
            health_checker=health_checker,
        )
        readiness.append(
            ByolProviderReadiness(
                provider_id=spec.provider_id,
                env_var=spec.env_var,
                env_source=_readiness_env_source(
                    env=env,
                    env_sources=env_sources,
                    spec=spec,
                ),
                status=status,
            )
        )
    return readiness


def byol_all_health_passed(providers: list[ByolProviderReadiness]) -> bool:
    """Return whether every BYOL provider passed strict health."""
    return bool(providers) and all(
        provider.status == ByolProviderStatus.HEALTH_PASSED for provider in providers
    )


def build_enrichment_provider_matrix(
    *,
    provider_descriptors: Sequence[EnrichmentProviderDescriptorLike],
    byol_providers: list[ByolProviderReadiness],
    not_registered_provider_ids: list[str] | None = None,
) -> list[EnrichmentProviderMatrixEntry]:
    """Build a secret-free provider matrix for all enrichment providers.

    Args:
        provider_descriptors: Registered provider descriptors from the existing registry.
        byol_providers: Provider-level BYOL readiness rows.
        not_registered_provider_ids: Documentation-mentioned providers not registered in code.

    Returns:
        Matrix rows for registered providers plus explicit not-registered entries.
    """
    byol_by_id = {provider.provider_id: provider for provider in byol_providers}
    matrix = [
        _registered_matrix_entry(
            provider_id=descriptor.provider_id,
            requires_byol=descriptor.requires_byol,
            byol_readiness=byol_by_id.get(descriptor.provider_id),
        )
        for descriptor in provider_descriptors
    ]
    matrix.extend(
        _not_registered_matrix_entry(provider_id=provider_id)
        for provider_id in not_registered_provider_ids or []
    )
    return matrix


def _credential_status(
    *,
    tenant_id: str,
    credential_repo: ByolCredentialRepository,
    spec: ByolProviderEnvSpec,
    env_present: bool,
    any_env_present: bool,
    load_env_credentials: bool,
    health_checker: ByolProviderHealthChecker | None,
) -> ByolProviderStatus:
    if not env_present and any_env_present and load_env_credentials:
        return ByolProviderStatus.ENV_KEY_MISSING
    if env_present and not load_env_credentials:
        return ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED

    try:
        credentials = credential_repo.load(tenant_id=tenant_id, connector_id=spec.provider_id)
    except CredentialNotFoundError:
        return ByolProviderStatus.TENANT_CREDENTIAL_MISSING
    except EncryptionKeyMissingError:
        return ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED

    if health_checker is None:
        return ByolProviderStatus.TENANT_CREDENTIAL_LOADED

    try:
        healthy = health_checker.check(provider_id=spec.provider_id, credentials=credentials)
    except Exception:
        healthy = False
    return ByolProviderStatus.HEALTH_PASSED if healthy else ByolProviderStatus.HEALTH_FAILED


def _registered_matrix_entry(
    *,
    provider_id: str,
    requires_byol: bool,
    byol_readiness: ByolProviderReadiness | None,
) -> EnrichmentProviderMatrixEntry:
    if not requires_byol:
        return EnrichmentProviderMatrixEntry(
            provider_id=provider_id,
            registry_status=EnrichmentProviderRegistryStatus.REGISTERED,
            requires_byol=False,
            env_var=None,
            credential_repo_status="not_required",
            health_status="not_checked",
            strict_behavior="strict_fail_closed_on_error",
            provenance_output_status="provenance_ref_on_hit_not_final_output_visible",
        )

    if byol_readiness is None:
        return EnrichmentProviderMatrixEntry(
            provider_id=provider_id,
            registry_status=EnrichmentProviderRegistryStatus.REGISTERED,
            requires_byol=True,
            env_var=BYOL_PROVIDER_ENV_SPECS[provider_id].env_var,
            credential_repo_status="tenant_credential_missing",
            health_status="not_checked_missing_credential",
            strict_behavior="strict_blocks_until_byol_ready",
            provenance_output_status="provenance_ref_on_hit_not_final_output_visible",
        )

    return EnrichmentProviderMatrixEntry(
        provider_id=provider_id,
        registry_status=EnrichmentProviderRegistryStatus.REGISTERED,
        requires_byol=True,
        env_var=byol_readiness.env_var,
        credential_repo_status=_matrix_credential_repo_status(byol_readiness.status),
        health_status=_matrix_health_status(byol_readiness.status),
        strict_behavior=_matrix_strict_behavior(byol_readiness.status),
        provenance_output_status="provenance_ref_on_hit_not_final_output_visible",
    )


def _not_registered_matrix_entry(provider_id: str) -> EnrichmentProviderMatrixEntry:
    return EnrichmentProviderMatrixEntry(
        provider_id=provider_id,
        registry_status=EnrichmentProviderRegistryStatus.NOT_REGISTERED,
        requires_byol=False,
        env_var=None,
        credential_repo_status="not_applicable",
        health_status="not_implemented",
        strict_behavior="not_registered_not_wired",
        provenance_output_status="not_output_visible",
    )


def _matrix_credential_repo_status(status: ByolProviderStatus) -> str:
    if status == ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED:
        return "env_key_present_not_loaded"
    if status == ByolProviderStatus.TENANT_CREDENTIAL_MISSING:
        return "tenant_credential_missing"
    if status in {
        ByolProviderStatus.TENANT_CREDENTIAL_LOADED,
        ByolProviderStatus.HEALTH_PASSED,
        ByolProviderStatus.HEALTH_FAILED,
    }:
        return "tenant_credential_loaded"
    return "not_loaded"


def _matrix_health_status(status: ByolProviderStatus) -> str:
    if status == ByolProviderStatus.HEALTH_PASSED:
        return "safe_provider_health_passed"
    if status == ByolProviderStatus.HEALTH_FAILED:
        return "safe_provider_health_failed"
    if status == ByolProviderStatus.TENANT_CREDENTIAL_LOADED:
        return "credential_loaded_health_not_run"
    if status == ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED:
        return "not_checked_not_loaded"
    return "not_checked_missing_credential"


def _matrix_strict_behavior(status: ByolProviderStatus) -> str:
    if status == ByolProviderStatus.HEALTH_PASSED:
        return "strict_fail_closed_on_error"
    return "strict_blocks_until_byol_ready"


def _provider_readiness(
    *,
    spec: ByolProviderEnvSpec,
    env_source: _ByolEnvSource,
    status: ByolProviderStatus,
) -> ByolProviderReadiness:
    return ByolProviderReadiness(
        provider_id=spec.provider_id,
        env_var=spec.env_var,
        env_source=_source_for_key(env_source=env_source, key=spec.env_var),
        status=status,
    )


def _build_byol_env_source(
    *,
    process_env: Mapping[str, str],
    dotenv_path: str | Path | None,
) -> _ByolEnvSource:
    dotenv_values = _parse_dotenv_values(dotenv_path)
    effective_env = dict(dotenv_values)
    effective_env.update({key: str(value) for key, value in process_env.items()})
    return _ByolEnvSource(
        effective_env=effective_env,
        process_keys=frozenset(process_env.keys()),
        dotenv_keys=frozenset(dotenv_values.keys()),
    )


def _source_for_key(*, env_source: _ByolEnvSource, key: str) -> str:
    if key in env_source.process_keys:
        return PROCESS_ENV_SOURCE
    if key in env_source.dotenv_keys:
        return DOTENV_ENV_SOURCE
    return MISSING_ENV_SOURCE


def _parse_dotenv_values(dotenv_path: str | Path | None) -> dict[str, str]:
    if dotenv_path is None:
        return {}
    path = Path(dotenv_path)
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key.removeprefix("export ").strip()
    if not key:
        return None
    return key, _strip_dotenv_value(value.strip())


def _strip_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _readiness_env_source(
    *,
    env: Mapping[str, str],
    env_sources: Mapping[str, str] | None,
    spec: ByolProviderEnvSpec,
) -> str:
    if env_sources is not None:
        return str(env_sources.get(spec.env_var, MISSING_ENV_SOURCE))
    return PROCESS_ENV_SOURCE if _has_value(env, spec.env_var) else MISSING_ENV_SOURCE


def _safe_health_request(*, provider_id: str) -> EnrichmentRequest:
    query = _safe_health_query(provider_id=provider_id)
    return EnrichmentRequest(
        tenant_id="strict-health-check",
        entity_type=EntityType.COMPANY,
        query=query,
        purpose=EnrichmentPurpose.DUE_DILIGENCE,
    )


def _safe_health_query(*, provider_id: str) -> EnrichmentQuery:
    if provider_id == "github":
        return EnrichmentQuery(company_name="github")
    if provider_id == "fred":
        return EnrichmentQuery(ticker="GDP")
    if provider_id in {"finnhub", "fmp"}:
        return EnrichmentQuery(ticker="AAPL")
    return EnrichmentQuery(company_name="TESCO PLC")


def _build_byol_health_connector(provider_id: str) -> EnrichmentConnector:
    if provider_id == "companies_house":
        from idis.services.enrichment.connectors.companies_house import CompaniesHouseConnector

        return CompaniesHouseConnector()
    if provider_id == "github":
        from idis.services.enrichment.connectors.github import GitHubConnector

        return GitHubConnector()
    if provider_id == "fred":
        from idis.services.enrichment.connectors.fred import FredConnector

        return FredConnector()
    if provider_id == "finnhub":
        from idis.services.enrichment.connectors.finnhub import FinnhubConnector

        return FinnhubConnector()
    if provider_id == "fmp":
        from idis.services.enrichment.connectors.fmp import FmpConnector

        return FmpConnector()
    raise ValueError("Unsupported BYOL provider health check")
