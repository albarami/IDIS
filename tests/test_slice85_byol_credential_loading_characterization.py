"""Slice85 Task 1 — characterization pinning the CURRENT BYOL credential-loading truth.

GREEN-on-arrival expected: most of the master-plan Slice85 text was already delivered by
Slice57 (see docs/plans/2026-06-10-slice85-byol-credential-loading.md §2). This suite pins the
acceptance-relevant truths Slice85 builds on — without duplicating the Slice57 suite — plus the
encryption/dependency truths Task 2 delivered (AES-256-GCM via a declared, bounded
``cryptography`` dependency). Originally authored pre-Task 2; items 7 and 10 were drift-flipped
to the as-built truth. No real provider call
(fake connectors/health checkers only; planted env values are markers, never real keys),
no DB migration, no new dependency.

Pins:
  1. The 5 BYOL connectors never read ambient env (planted env key + no ctx credentials ->
     BLOCKED_MISSING_BYOL; marker never surfaces).
  2. EnrichmentService reads tenant credentials from the repository, not env (acceptance 1).
  3. Strict preflight blocks when credentials are missing entirely (acceptance 2, preflight).
  4. Execution backstop: _run_full_enrichment raises in strict on BLOCKED_MISSING_BYOL and
     swallows/blocks-counts in non-strict (acceptance 2, execution; no network via fake registry).
  5. Preflight and execution both build the repo via get_enrichment_credentials_repository.
  6. Repository factory truths: no conn -> in-memory (is_durable False); Postgres repo class is
     durable; strict readiness requires durability (pinned by Slice57 tests, referenced here).
  7. Encryption truths: round-trip, tamper fail-closed, missing-key fail-closed — the cipher
     is AES-256-GCM in a versioned ``v2:`` ciphertext format (Task 2 closed the XOR+HMAC gap;
     full coverage in test_slice85_aes_gcm_credential_encryption.py).
  8. Planted secret markers never appear in the strict readiness report JSON.
  9. Migration 0011 already provides the durable table (no new migration needed).
 10. ``cryptography`` is lazily imported by auth_sso.py and is now an explicit, bounded
     dependency in pyproject.toml (Task 2 formalized the previously implicit dependency).
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
from base64 import b64decode, b64encode
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from idis.persistence.repositories.enrichment_credentials import (
    EncryptionKeyMissingError,
    InMemoryCredentialRepository,
    PostgresCredentialRepository,
    decrypt_credentials,
    encrypt_credentials,
    get_enrichment_credentials_repository,
)
from idis.services.enrichment.byol_credentials import (
    BYOL_PROVIDER_ENV_SPECS,
    ByolProviderStatus,
    _build_byol_health_connector,
    _safe_health_request,
    assess_byol_provider_readiness,
    byol_all_health_passed,
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
from idis.services.enrichment.service import EnrichmentService
from idis.services.runs.strict_full_live import (
    build_strict_full_live_admission_report,
    build_strict_full_live_readiness_report,
)

TENANT_ID = "tenant-slice85"

# Planted markers (never real credentials) — must never surface in any report/exception.
_ENV_MARKERS = {
    "COMPANIES_HOUSE_API_KEY": "sk-byol-ch-LEAK-1111",
    "GITHUB_API_TOKEN": "sk-byol-gh-LEAK-2222",
    "FRED_API_KEY": "sk-byol-fred-LEAK-3333",
    "FINNHUB_API_KEY": "sk-byol-fin-LEAK-4444",
    "FMP_API_KEY": "sk-byol-fmp-LEAK-5555",
}
_ENC_KEY_MARKER = "enc-key-LEAK-/var/secret/byol"


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _HealthyChecker:
    def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
        assert provider_id and credentials
        return True


class _DurableRepo(InMemoryCredentialRepository):
    is_durable = True


class _CapturingByolConnector:
    """Fake BYOL connector capturing the ctx credentials it receives (no network)."""

    def __init__(self, provider_id: str = "companies_house") -> None:
        self._provider_id = provider_id
        self.seen_credentials: dict[str, str] | None = None

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
        self.seen_credentials = dict(ctx.byol_credentials or {})
        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized={"safe": "result"},
            provenance=EnrichmentProvenance(
                provider_id=self._provider_id,
                source_id=self._provider_id,
                retrieved_at=datetime.now(UTC),
                rights_class=RightsClass.GREEN,
                raw_ref_hash="safehash",
                identifiers_used={"company_name": "safe-public-company"},
            ),
        )


def _service_with(
    connector: Any, *, requires_byol: bool, repo: InMemoryCredentialRepository
) -> EnrichmentService:
    from idis.audit.sink import InMemoryAuditSink

    registry = EnrichmentProviderRegistry()
    registry.register(connector, requires_byol=requires_byol)
    return EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=repo,
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )


# --- 1. connectors never read ambient env ---


def test_connectors_never_read_ambient_env() -> None:
    # Planted provider env keys + NO ctx credentials -> every connector blocks; the connector
    # never falls back to os.environ and the marker never surfaces in the result.
    with patch.dict(os.environ, _ENV_MARKERS, clear=False):
        for spec in BYOL_PROVIDER_ENV_SPECS.values():
            connector = _build_byol_health_connector(spec.provider_id)
            result = connector.fetch(
                _safe_health_request(provider_id=spec.provider_id),
                EnrichmentContext(
                    timeout_seconds=1.0,
                    max_retries=0,
                    request_id="slice85-characterization",
                    byol_credentials=None,
                ),
            )
            assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL, spec.provider_id
            blob = result.model_dump_json()
            for marker in _ENV_MARKERS.values():
                assert marker not in blob


# --- 2. acceptance (1): service reads tenant credentials from the repository, not env ---


def _tenant_request() -> EnrichmentRequest:
    # Credential loads are scoped by request.tenant_id (see EnrichmentService.enrich), so the
    # request must carry the tenant that stored the credentials.
    from idis.services.enrichment.models import EnrichmentPurpose, EnrichmentQuery, EntityType

    return EnrichmentRequest(
        tenant_id=TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name="safe-public-company"),
        purpose=EnrichmentPurpose.DUE_DILIGENCE,
    )


def test_service_reads_repo_credentials_not_env() -> None:
    connector = _CapturingByolConnector()
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "from-tenant-repo"},
    )
    service = _service_with(connector, requires_byol=True, repo=repo)

    with patch.dict(os.environ, _ENV_MARKERS, clear=False):
        result = service.enrich(provider_id="companies_house", request=_tenant_request())
    assert result.status == EnrichmentStatus.HIT
    assert connector.seen_credentials == {"api_key": "from-tenant-repo"}  # repo, not env


def test_service_credentials_are_tenant_scoped_by_request() -> None:
    # Same repo, DIFFERENT request tenant -> no credential visible (RLS-style scoping at the
    # repository seam); the planted env var is never used as a fallback.
    connector = _CapturingByolConnector()
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "from-tenant-repo"},
    )
    service = _service_with(connector, requires_byol=True, repo=repo)
    request = _safe_health_request(provider_id="companies_house")  # tenant "strict-health-check"

    with patch.dict(os.environ, _ENV_MARKERS, clear=False):
        result = service.enrich(provider_id="companies_house", request=request)
    assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
    assert connector.seen_credentials is None


def test_service_blocks_when_repo_empty_even_with_env_present() -> None:
    connector = _CapturingByolConnector()
    service = _service_with(connector, requires_byol=True, repo=InMemoryCredentialRepository())

    with patch.dict(os.environ, _ENV_MARKERS, clear=False):
        result = service.enrich(provider_id="companies_house", request=_tenant_request())
    assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
    assert connector.seen_credentials is None  # connector never invoked with env fallback


# --- 3. acceptance (2) at preflight: missing credentials block strict before run ---


def test_preflight_blocks_when_credentials_missing_entirely() -> None:
    # With ZERO BYOL env keys, every provider reports TENANT_CREDENTIAL_MISSING (the
    # ENV_KEY_MISSING status is reserved for the some-keys-present case via any_env_present);
    # either way the component blocks strict before the run. Hermetic: env={} keeps every
    # conditional default probe (Neo4j/pgvector/embedding/object-store/OCR/media) structurally
    # dormant — no triggering env var, no live check.
    report = build_strict_full_live_readiness_report(
        env={},
        tenant_id=TENANT_ID,
        byol_credential_repo=_DurableRepo(),
        byol_health_checker=_HealthyChecker(),
    )
    statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert set(statuses) == set(BYOL_PROVIDER_ENV_SPECS)
    assert all(
        status == ByolProviderStatus.TENANT_CREDENTIAL_MISSING for status in statuses.values()
    )
    assert "enrichment BYOL providers" in report.blocking_components
    assert report.may_proceed is False


# --- 4. acceptance (2) at execution: strict backstop raises; non-strict counts blocked ---


def _fake_byol_only_registry() -> EnrichmentProviderRegistry:
    registry = EnrichmentProviderRegistry()
    registry.register(_CapturingByolConnector(provider_id="fake_byol"), requires_byol=True)
    return registry


def test_run_full_enrichment_strict_raises_on_missing_byol_without_leak() -> None:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without(*_ENV_MARKERS, "IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch.dict(os.environ, _ENV_MARKERS, clear=False),
        patch(
            "idis.services.enrichment.service._build_default_registry",
            _fake_byol_only_registry,
        ),
        patch("idis.api.routes.runs.is_strict_full_live_required", return_value=True),
        pytest.raises(RuntimeError) as exc_info,
    ):
        _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    # Note: with env markers present, the strict execution path bootstraps them into the
    # (in-memory) repo, so the block is the rights/credential outcome for the fake provider;
    # the exception must carry only the provider id, never a credential value.
    message = f"{exc_info.value!s}|{exc_info.value!r}"
    assert "fake_byol" in message
    for marker in (*_ENV_MARKERS.values(), _ENC_KEY_MARKER):
        assert marker not in message


def test_run_full_enrichment_non_strict_counts_blocked_without_raise() -> None:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without(*_ENV_MARKERS, "IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch(
            "idis.services.enrichment.service._build_default_registry",
            _fake_byol_only_registry,
        ),
    ):
        summary = _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    assert summary["provider_count"] == 1
    assert summary["blocked_count"] == 1  # BLOCKED_MISSING_BYOL swallowed, not raised
    assert summary["result_count"] == 0


# --- 5. preflight/execution repo parity ---


def test_preflight_and_execution_share_repo_factory() -> None:
    from idis.api.routes.runs import _run_full_enrichment

    execution_src = inspect.getsource(_run_full_enrichment)
    preflight_src = inspect.getsource(build_strict_full_live_admission_report)
    assert "get_enrichment_credentials_repository(" in execution_src
    assert "get_enrichment_credentials_repository(" in preflight_src
    assert "db_conn" in preflight_src  # same conn-driven selection on both paths


# --- 6. repository factory + durability truths ---


def test_repo_factory_without_conn_is_in_memory_non_durable() -> None:
    repo = get_enrichment_credentials_repository(None, TENANT_ID)
    assert isinstance(repo, InMemoryCredentialRepository)
    assert repo.is_durable is False
    assert PostgresCredentialRepository.is_durable is True  # durable class contract


def test_bootstrap_then_assess_reaches_health_passed_on_durable_repo() -> None:
    repo = _DurableRepo()
    env = dict(_ENV_MARKERS)
    readiness = assess_byol_provider_readiness(
        tenant_id=TENANT_ID,
        credential_repo=repo,
        env=env,
        load_env_credentials=True,
        health_checker=_HealthyChecker(),
    )
    statuses = {item.provider_id: item.status for item in readiness}
    assert all(status == ByolProviderStatus.HEALTH_PASSED for status in statuses.values())
    assert byol_all_health_passed(readiness) is True
    for spec in BYOL_PROVIDER_ENV_SPECS.values():
        assert repo.exists(tenant_id=TENANT_ID, connector_id=spec.provider_id)
    blob = json.dumps([item.model_dump(mode="json") for item in readiness], sort_keys=True)
    for marker in _ENV_MARKERS.values():
        assert marker not in blob


# --- 7. encryption truths (current cipher + fail-closed contracts + the Task 2 gap) ---


def test_encryption_round_trip_tamper_and_missing_key_fail_closed() -> None:
    with patch.dict(os.environ, {"IDIS_ENRICHMENT_ENCRYPTION_KEY": _ENC_KEY_MARKER}, clear=False):
        ciphertext = encrypt_credentials({"api_key": "round-trip-secret"})
        assert ciphertext.startswith("v2:")  # versioned AES-256-GCM format (Task 2)
        assert "round-trip-secret" not in ciphertext  # never plaintext at rest
        assert decrypt_credentials(ciphertext) == {"api_key": "round-trip-secret"}

        raw = bytearray(b64decode(ciphertext.removeprefix("v2:")))
        raw[20] ^= 0xFF  # corrupt one sealed byte -> GCM authentication must fail
        with pytest.raises(ValueError):
            decrypt_credentials("v2:" + b64encode(bytes(raw)).decode("ascii"))

    env = _env_without("IDIS_ENRICHMENT_ENCRYPTION_KEY")
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(EncryptionKeyMissingError),
    ):
        encrypt_credentials({"api_key": "x"})


def test_current_cipher_is_aes_gcm_via_cryptography() -> None:
    # Task 2 closed the gap: AES-256-GCM via the cryptography library; the XOR stream cipher
    # and its in-code production note are gone.
    source = inspect.getsource(encrypt_credentials)
    assert "AESGCM" in source
    assert "XOR" not in source
    module_source = inspect.getsource(
        importlib.import_module("idis.persistence.repositories.enrichment_credentials")
    )
    assert "from cryptography" in module_source


# --- 8. strict readiness report never leaks planted markers ---


def test_readiness_report_json_carries_no_planted_markers() -> None:
    env = dict(_ENV_MARKERS)
    env["IDIS_ENRICHMENT_ENCRYPTION_KEY"] = _ENC_KEY_MARKER
    report = build_strict_full_live_readiness_report(
        env=env,
        tenant_id=TENANT_ID,
        byol_credential_repo=_DurableRepo(),
        byol_health_checker=_HealthyChecker(),
    )
    encoded = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    for marker in (*_ENV_MARKERS.values(), _ENC_KEY_MARKER):
        assert marker not in encoded


# --- 9. migration 0011 already provides the durable table (no new migration) ---


def test_migration_0011_provides_enrichment_credentials_table() -> None:
    migration = Path("src/idis/persistence/migrations/versions/0011_enrichment_credentials.py")
    assert migration.exists()
    text = migration.read_text(encoding="utf-8")
    assert "enrichment_credentials" in text
    assert "ciphertext" in text  # encrypted at rest, never plaintext columns
    assert "tenant_id" in text
    assert "POLICY" in text.upper()  # RLS tenant isolation


# --- 10. cryptography: implicit transitive dependency (Task 2 may formalize) ---


def test_cryptography_declared_and_used_by_auth_sso_and_credentials() -> None:
    assert importlib.import_module("cryptography") is not None
    auth_sso = Path("src/idis/api/auth_sso.py").read_text(encoding="utf-8")
    assert "from cryptography." in auth_sso  # lazy in-function imports
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"cryptography>=' in pyproject  # explicit, declared dependency (Task 2)
    spec = next(line for line in pyproject.splitlines() if '"cryptography>=' in line)
    assert "<" in spec  # bounded ceiling — no open upper bound
