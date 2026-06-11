"""Slice86 Task 3 — per-provider enrichment ledger + cache visibility (RED-first).

Implements the master-plan "hit/miss/error/cache/blocked ledger" scope bullet (plan §3 G2,
decision D-D): an additive ``from_cache: bool = False`` on ``EnrichmentResult`` set ONLY on
cache-hit results, and an additive ``enrichment_ledger`` block in the ENRICHMENT step
``result_summary`` — per-provider rows of SAFE values only (provider id, status, from_cache,
rights class, optional flag, ref id when a HIT produced one) plus aggregate counts
{hit, miss, error, blocked_rights, blocked_missing_byol, cache_hits}. Existing summary fields
(provider_count, result_count, blocked_count, enrichment_refs) and the Task 2 strict policy
(mandatory fatal / optional recorded-and-continued) are unchanged. No source-grade mapping, no
deliverables/VC changes, no conflict checks, no hardening, no real provider calls, no DB, no
Slice87.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.enrichment_credentials import InMemoryCredentialRepository
from idis.services.enrichment.cache_policy import EnrichmentCacheStore
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from idis.services.enrichment.rights_gate import EnvironmentMode
from idis.services.enrichment.service import EnrichmentService
from tests.test_slice86_enrichment_execution_provenance_characterization import (
    TENANT_ID,
    _env_without,
    _mixed_registry,
    _StatusConnector,
)

_LEAK_MARKERS = ("sk-s86-ledger-LEAK-1", "/var/secret/s86-ledger", "C:\\secret\\s86l")

_ROW_FIELDS = {
    "provider_id",
    "status",
    "from_cache",
    "rights_class",
    "optional_in_strict",
    "ref_id",
    "source_grade",  # Task 4 drift: rows carry the D-E mapped grade
    "conflicts",  # Task 5 drift: narrow identifier-mismatch flags (never values)
}
_COUNT_FIELDS = {"hit", "miss", "error", "blocked_rights", "blocked_missing_byol", "cache_hits"}


class _CachingConnector(_StatusConnector):
    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=3600, no_store=False)


def _service(registry: EnrichmentProviderRegistry) -> EnrichmentService:
    return EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=InMemoryCredentialRepository(),
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )


def _request() -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id=TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name="safe-public-company"),
    )


def _run_step(*, strict: bool, registry_factory: Any = None, service: Any = None) -> dict[str, Any]:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    patches = [patch.dict(os.environ, env, clear=True)]
    if registry_factory is not None:
        patches.append(
            patch("idis.services.enrichment.service._build_default_registry", registry_factory)
        )
    if service is not None:
        patches.append(
            patch(
                "idis.services.enrichment.service.create_default_enrichment_service",
                lambda **_kw: service,
            )
        )
    if strict:
        patches.append(
            patch("idis.api.routes.runs.is_strict_full_live_required", return_value=True)
        )
    with patches[0], patches[1]:
        if len(patches) == 3:
            with patches[2]:
                return _run_full_enrichment(
                    run_id="run-1",
                    tenant_id=TENANT_ID,
                    deal_id="deal-1",
                    created_claim_ids=[],
                    calc_ids=[],
                    db_conn=None,
                )
        return _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )


# --- from_cache: additive field, set ONLY on cache-hit results ---


def test_enrichment_result_has_additive_from_cache_default_false() -> None:
    assert "from_cache" in EnrichmentResult.model_fields
    assert EnrichmentResult(status=EnrichmentStatus.MISS).from_cache is False


def test_cache_hit_sets_from_cache_true_only_on_cached_results() -> None:
    registry = EnrichmentProviderRegistry()
    registry.register(_CachingConnector("cached_provider", EnrichmentStatus.HIT))
    service = _service(registry)

    live = service.enrich(provider_id="cached_provider", request=_request())
    cached = service.enrich(provider_id="cached_provider", request=_request())
    again = service.enrich(provider_id="cached_provider", request=_request())
    assert live.from_cache is False  # first fetch is live
    assert cached.from_cache is True
    assert again.from_cache is True
    assert live.status == cached.status == EnrichmentStatus.HIT


def test_no_store_provider_never_reports_from_cache() -> None:
    registry = EnrichmentProviderRegistry()
    registry.register(_StatusConnector("no_store_provider", EnrichmentStatus.HIT))
    service = _service(registry)
    first = service.enrich(provider_id="no_store_provider", request=_request())
    second = service.enrich(provider_id="no_store_provider", request=_request())
    assert first.from_cache is False
    assert second.from_cache is False


# --- enrichment_ledger: per-provider rows + aggregate counts, additive ---


def test_ledger_records_hit_miss_error_blocked_rows() -> None:
    summary = _run_step(strict=False, registry_factory=_mixed_registry)
    # Existing fields intact (values unchanged from the Task 1 pins).
    assert summary["provider_count"] == 4
    assert summary["result_count"] == 1
    assert summary["blocked_count"] == 1
    assert set(summary["enrichment_refs"]) and "enrichment_ledger" in summary

    ledger = summary["enrichment_ledger"]
    rows = {row["provider_id"]: row for row in ledger["providers"]}
    assert set(rows) == {"fake_hit", "fake_miss", "fake_error", "fake_byol"}
    for row in rows.values():
        assert set(row) == _ROW_FIELDS
        assert row["from_cache"] is False
        assert row["rights_class"] == "GREEN"
        assert row["optional_in_strict"] is False
    assert rows["fake_hit"]["status"] == "HIT"
    assert rows["fake_hit"]["ref_id"]  # the HIT row links its enrichment ref
    assert rows["fake_hit"]["ref_id"] in summary["enrichment_refs"]
    assert rows["fake_miss"]["status"] == "MISS"
    assert rows["fake_miss"]["ref_id"] is None
    assert rows["fake_error"]["status"] == "ERROR"
    assert rows["fake_byol"]["status"] == "BLOCKED_MISSING_BYOL"

    counts = ledger["counts"]
    assert set(counts) == _COUNT_FIELDS
    assert counts == {
        "hit": 1,
        "miss": 1,
        "error": 1,
        "blocked_rights": 0,
        "blocked_missing_byol": 1,
        "cache_hits": 0,
    }


def test_ledger_counts_cache_hits_via_warm_service() -> None:
    from idis.services.enrichment.models import EnrichmentPurpose

    registry = EnrichmentProviderRegistry()
    registry.register(_CachingConnector("warm_provider", EnrichmentStatus.HIT))
    service = _service(registry)
    # Warm the cache out-of-band with the EXACT request shape the step builds (the cache key
    # includes tenant + query; the step uses deal_id as company_name), then run the step
    # against the SAME service instance.
    step_request = EnrichmentRequest(
        tenant_id=TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name="deal-1"),
        purpose=EnrichmentPurpose.DUE_DILIGENCE,
    )
    warmed = service.enrich(provider_id="warm_provider", request=step_request)
    assert warmed.from_cache is False

    summary = _run_step(strict=False, service=service)
    (row,) = summary["enrichment_ledger"]["providers"]
    assert row["provider_id"] == "warm_provider"
    assert row["status"] == "HIT"
    assert row["from_cache"] is True
    counts = summary["enrichment_ledger"]["counts"]
    assert counts["hit"] == 1
    assert counts["cache_hits"] == 1


# --- Task 2 policy compatibility ---


def test_strict_optional_failure_recorded_in_ledger_without_raise() -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_StatusConnector("opt_error", EnrichmentStatus.ERROR), optional_in_strict=True)
        reg.register(_StatusConnector("mand_hit", EnrichmentStatus.HIT))
        return reg

    summary = _run_step(strict=True, registry_factory=registry)
    rows = {row["provider_id"]: row for row in summary["enrichment_ledger"]["providers"]}
    assert rows["opt_error"]["status"] == "ERROR"
    assert rows["opt_error"]["optional_in_strict"] is True
    assert rows["mand_hit"]["status"] == "HIT"
    assert summary["enrichment_ledger"]["counts"]["error"] == 1


def test_strict_mandatory_fatality_unchanged_by_ledger() -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_StatusConnector("mand_error", EnrichmentStatus.ERROR))
        return reg

    with pytest.raises(RuntimeError) as exc_info:
        _run_step(strict=True, registry_factory=registry)
    assert "mand_error" in str(exc_info.value)


# --- leak safety over the FULL summary JSON ---


def test_ledger_summary_json_carries_no_planted_markers() -> None:
    confidential = " ".join(_LEAK_MARKERS)

    class _LeakyError(_StatusConnector):
        def fetch(self, request: Any, ctx: Any) -> Any:
            raise RuntimeError(confidential)  # service contains this as a fixed ERROR result

    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_LeakyError("leaky_error", EnrichmentStatus.HIT), optional_in_strict=True)
        reg.register(_StatusConnector("plain_hit", EnrichmentStatus.HIT))
        return reg

    env_markers = {"FRED_API_KEY": _LEAK_MARKERS[0]}
    with patch.dict(os.environ, env_markers, clear=False):
        summary = _run_step(strict=True, registry_factory=registry)
    blob = json.dumps(summary, sort_keys=True, default=str)
    for marker in _LEAK_MARKERS:
        assert marker not in blob
