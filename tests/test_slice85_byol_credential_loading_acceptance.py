"""Slice85 acceptance — master-plan acceptance proof for BYOL credential loading.

Acceptance criteria (docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md:260-261, plan doc §1):
  - Enrichment services read tenant credentials, not raw ambient env.
  - Missing BYOL credentials block strict enrichment before run.

This suite composes the behaviors verified in Tasks 1-2 into an end-to-end acceptance proof,
mirroring the Slice83/84 seam-level style: injected fakes only, NO real provider call — the
real-funnel test runs against a hermetic EMPTY environment so every conditional default probe
is structurally dormant, and the BYOL health checker fires only for loaded credentials (none
are ever loaded with real connectors). No DB migration, no lifecycle API. It proves
(A) tenant-credential reads, never ambient env;
(B) missing credentials block strict at the REAL preflight admission funnel before
any run; (C) the durability requirement is enforced (transient storage blocks, durable clears);
(D) the secret-safe bootstrap loads whitelist-only env credentials into durable ENCRYPTED
storage (versioned v2 AES-256-GCM at rest, no plaintext); (E) the execution backstop stays
fail-closed in strict and unchanged in non-strict; (F) planted secret markers never appear in
readiness JSON, exceptions, step summaries, or reprs.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.persistence.repositories.enrichment_credentials import (
    InMemoryCredentialRepository,
    decrypt_credentials,
    encrypt_credentials,
)
from idis.services.enrichment.byol_credentials import (
    BYOL_PROVIDER_ENV_SPECS,
    ByolProviderStatus,
    assess_byol_provider_readiness,
    byol_all_health_passed,
)
from idis.services.enrichment.models import EnrichmentStatus
from idis.services.runs.strict_full_live import (
    build_strict_full_live_admission_report,
    build_strict_full_live_readiness_report,
)
from tests.test_slice85_byol_credential_loading_characterization import (
    TENANT_ID,
    _CapturingByolConnector,
    _service_with,
    _tenant_request,
)

_ENV_MARKERS = {
    "COMPANIES_HOUSE_API_KEY": "sk-acc-ch-LEAK-1111",
    "GITHUB_API_TOKEN": "sk-acc-gh-LEAK-2222",
    "FRED_API_KEY": "sk-acc-fred-LEAK-3333",
    "FINNHUB_API_KEY": "sk-acc-fin-LEAK-4444",
    "FMP_API_KEY": "sk-acc-fmp-LEAK-5555",
}
_ENC_KEY_MARKER = "acc-enc-key-LEAK-/var/secret/byol"
_ALL_MARKERS = (*_ENV_MARKERS.values(), _ENC_KEY_MARKER)


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _HealthyChecker:
    def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
        assert provider_id and credentials
        return True


class _DurableEncryptingRepository(InMemoryCredentialRepository):
    """Durable-flagged fake that ENCRYPTS at rest with the real v2 AES-256-GCM functions.

    Composes loader + encryption + readiness without faking SQLAlchemy: stored values are
    ciphertext only (inspectable via ``ciphertexts``), decrypted on load — the same
    encrypt-before-persist / decrypt-on-load contract as PostgresCredentialRepository.
    """

    is_durable = True

    def __init__(self) -> None:
        super().__init__()
        self.ciphertexts: dict[tuple[str, str], str] = {}

    def store(self, *, tenant_id: str, connector_id: str, credentials: dict[str, str]) -> Any:
        ciphertext = encrypt_credentials(credentials)
        self.ciphertexts[(tenant_id, connector_id)] = ciphertext
        return super().store(
            tenant_id=tenant_id,
            connector_id=connector_id,
            credentials={"__ciphertext__": ciphertext},
        )

    def load(self, *, tenant_id: str, connector_id: str) -> dict[str, str]:
        stored = super().load(tenant_id=tenant_id, connector_id=connector_id)
        return decrypt_credentials(stored["__ciphertext__"])


# === A. Enrichment services read tenant credentials, not raw ambient env ===


def test_acceptance_service_uses_tenant_credentials_never_ambient_env() -> None:
    connector = _CapturingByolConnector()
    repo = InMemoryCredentialRepository()
    repo.store(
        tenant_id=TENANT_ID,
        connector_id="companies_house",
        credentials={"api_key": "tenant-credential-value"},
    )
    service = _service_with(connector, requires_byol=True, repo=repo)
    with patch.dict(os.environ, _ENV_MARKERS, clear=False):
        result = service.enrich(provider_id="companies_house", request=_tenant_request())
    assert result.status == EnrichmentStatus.HIT
    assert connector.seen_credentials == {"api_key": "tenant-credential-value"}

    # Empty repo + the same planted ambient env -> blocked; env is never a fallback.
    blocked_connector = _CapturingByolConnector()
    blocked = _service_with(
        blocked_connector, requires_byol=True, repo=InMemoryCredentialRepository()
    )
    with patch.dict(os.environ, _ENV_MARKERS, clear=False):
        blocked_result = blocked.enrich(provider_id="companies_house", request=_tenant_request())
    assert blocked_result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
    assert blocked_connector.seen_credentials is None


# === B. Missing BYOL credentials block strict enrichment BEFORE run ===


def test_acceptance_real_preflight_admission_funnel_blocks_without_credentials() -> None:
    # The REAL API/worker admission funnel (no fakes injected), run HERMETICALLY with an empty
    # environment: every conditional default probe (Neo4j connectivity, pgvector/embedding,
    # object-store put/delete, OCR/media subprocesses) is structurally dormant because its
    # triggering env vars are absent, and the BYOL health checker never fires because
    # db_conn=None resolves an empty in-memory repo (no credentials -> no check, no network).
    # The report still blocks before any run starts — the acceptance truth under test.
    with patch.dict(os.environ, {}, clear=True):
        report = build_strict_full_live_admission_report(
            db_conn=None,
            tenant_id=TENANT_ID,
            preflight_corpus=None,
            strict_dotenv_path=None,
        )
    assert report.may_proceed is False
    assert "enrichment BYOL providers" in report.blocking_components
    component = next(
        item
        for item in report.component_inventory
        if item.component_name == "enrichment BYOL providers"
    )
    assert component.full_wired is False


# === C. Durability requirement is enforced ===


def test_acceptance_transient_storage_blocks_durable_clears() -> None:
    env_markers = dict(_ENV_MARKERS)
    env_markers["IDIS_ENRICHMENT_ENCRYPTION_KEY"] = _ENC_KEY_MARKER

    # The encryption layer reads IDIS_ENRICHMENT_ENCRYPTION_KEY from PROCESS env (same as the
    # real PostgresCredentialRepository), while the report's env= mapping governs the provider
    # keys — so the key must be patched into process env for the encrypting repo to load.
    with patch.dict(os.environ, {"IDIS_ENRICHMENT_ENCRYPTION_KEY": _ENC_KEY_MARKER}, clear=False):
        transient = build_strict_full_live_readiness_report(
            env=env_markers,
            tenant_id=TENANT_ID,
            byol_credential_repo=InMemoryCredentialRepository(),  # is_durable False
            byol_health_checker=_HealthyChecker(),
        )
        assert "enrichment BYOL providers" in transient.blocking_components

        durable = build_strict_full_live_readiness_report(
            env=env_markers,
            tenant_id=TENANT_ID,
            byol_credential_repo=_DurableEncryptingRepository(),
            byol_health_checker=_HealthyChecker(),
        )
        assert "enrichment BYOL providers" not in durable.blocking_components


# === D. Secret-safe bootstrap into durable ENCRYPTED storage ===


def test_acceptance_bootstrap_loads_whitelist_into_encrypted_durable_storage() -> None:
    repo = _DurableEncryptingRepository()
    env = dict(_ENV_MARKERS)
    env["UNRELATED_SECRET"] = "sk-unrelated-LEAK-9999"
    with patch.dict(os.environ, {"IDIS_ENRICHMENT_ENCRYPTION_KEY": _ENC_KEY_MARKER}, clear=False):
        readiness = assess_byol_provider_readiness(
            tenant_id=TENANT_ID,
            credential_repo=repo,
            env=env,
            load_env_credentials=True,
            health_checker=_HealthyChecker(),
        )
        # All five providers bootstrapped to HEALTH_PASSED; whitelist-only (no sixth entry).
        statuses = {item.provider_id: item.status for item in readiness}
        assert set(statuses) == set(BYOL_PROVIDER_ENV_SPECS)
        assert all(status == ByolProviderStatus.HEALTH_PASSED for status in statuses.values())
        assert byol_all_health_passed(readiness) is True
        assert len(repo.ciphertexts) == len(BYOL_PROVIDER_ENV_SPECS)

        # At rest: versioned v2 AES-256-GCM ciphertext only — no plaintext marker, no key.
        for ciphertext in repo.ciphertexts.values():
            assert ciphertext.startswith("v2:")
            for marker in (*_ALL_MARKERS, "sk-unrelated-LEAK-9999"):
                assert marker not in ciphertext

        # Round trip back through the repository decrypts to the original env values.
        loaded = repo.load(tenant_id=TENANT_ID, connector_id="github")
        assert loaded == {"token": _ENV_MARKERS["GITHUB_API_TOKEN"]}

    # The readiness JSON is secret-free.
    blob = json.dumps([item.model_dump(mode="json") for item in readiness], sort_keys=True)
    for marker in (*_ALL_MARKERS, "sk-unrelated-LEAK-9999"):
        assert marker not in blob


# === E. Execution backstop remains fail-closed (strict) and unchanged (non-strict) ===


def _fake_byol_only_registry() -> Any:
    from idis.services.enrichment.registry import EnrichmentProviderRegistry

    registry = EnrichmentProviderRegistry()
    registry.register(_CapturingByolConnector(provider_id="fake_byol"), requires_byol=True)
    return registry


def test_acceptance_execution_backstop_fails_closed_in_strict_without_leak() -> None:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without(*_ENV_MARKERS, "IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    env["IDIS_ENRICHMENT_ENCRYPTION_KEY"] = _ENC_KEY_MARKER
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
    surfaced = f"{exc_info.value!s}|{exc_info.value!r}"
    assert "fake_byol" in surfaced  # provider id only
    for marker in _ALL_MARKERS:
        assert marker not in surfaced


def test_acceptance_non_strict_summary_is_unchanged_and_leak_free() -> None:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without(*_ENV_MARKERS, "IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch.dict(os.environ, _ENV_MARKERS, clear=False),
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
    assert summary["blocked_count"] == 1
    assert summary["result_count"] == 0
    blob = repr(summary)
    for marker in _ALL_MARKERS:
        assert marker not in blob


# === F. No planted marker in the full strict readiness JSON ===


def test_acceptance_full_readiness_json_is_marker_free() -> None:
    env_markers = dict(_ENV_MARKERS)
    env_markers["IDIS_ENRICHMENT_ENCRYPTION_KEY"] = _ENC_KEY_MARKER
    with patch.dict(os.environ, {"IDIS_ENRICHMENT_ENCRYPTION_KEY": _ENC_KEY_MARKER}, clear=False):
        report = build_strict_full_live_readiness_report(
            env=env_markers,
            tenant_id=TENANT_ID,
            byol_credential_repo=_DurableEncryptingRepository(),
            byol_health_checker=_HealthyChecker(),
        )
    # Richest report shape: credentials loaded + health passed, then leak-checked.
    statuses = {item.provider_id: item.status for item in report.byol_providers}
    assert all(status == ByolProviderStatus.HEALTH_PASSED for status in statuses.values())
    encoded = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    for marker in _ALL_MARKERS:
        assert marker not in encoded
