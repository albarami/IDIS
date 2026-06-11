"""Slice86 acceptance — master-plan acceptance proof for enrichment execution + provenance.

Acceptance criteria (docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md:273-275, plan doc §1):
  - Provider errors are fatal in strict mode unless policy says optional.
  - Enrichment provenance is visible in VC package.

Composes Tasks 2-6 end-to-end with injected fakes and httpx.MockTransport only — NO real
provider call, no DB. It proves (A) mandatory ERROR/blocked/exception each abort strict while
an optional provider's failure is recorded-and-continued; (B) the ledger carries safe
hit/miss/error/cache/blocked rows + counts; (C) a REAL step summary exported through the REAL
ProductBundleExporter surfaces enrichment provenance in the VC package — run_summary counts and
evidence_index.enrichment_evidence rows with provider provenance, source grades, and safe
conflict flags; (D) no raw normalized provider payloads are copied into the bundle; and
(E) URL keys and planted secret markers never leak into summaries, exceptions, bundle JSON, or
the httpx log records this suite captures.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from idis.services.enrichment.models import (
    EnrichmentPurpose,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from tests.test_slice86_enrichment_conflict_checks import _IdentityConnector
from tests.test_slice86_enrichment_execution_provenance_characterization import (
    TENANT_ID,
    _env_without,
    _StatusConnector,
)
from tests.test_slice86_enrichment_ledger_cache_visibility import _CachingConnector, _service
from tests.test_slice86_source_grade_vc_visibility import (
    _artifact_json,
    _export,
    _RedConnector,
)

_KEY = "sk-s86-acc-LEAK-0001"
_PAYLOAD_MARKER = "NORMALIZED-PAYLOAD-LEAK-s86"
_MARKERS = (_KEY, "/var/secret/s86-acc", "C:\\secret\\s86acc", _PAYLOAD_MARKER)


def _run_step(*, strict: bool, registry_factory: Any = None, service: Any = None) -> dict[str, Any]:
    from contextlib import ExitStack

    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, env, clear=True))
        if registry_factory is not None:
            stack.enter_context(
                patch("idis.services.enrichment.service._build_default_registry", registry_factory)
            )
        if service is not None:
            stack.enter_context(
                patch(
                    "idis.services.enrichment.service.create_default_enrichment_service",
                    lambda **_kw: service,
                )
            )
        stack.enter_context(
            patch("idis.api.routes.runs.is_strict_full_live_required", return_value=strict)
        )
        return _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )


# === A. Bullet 1: fatal in strict unless policy says optional ===


@pytest.mark.parametrize(
    ("connector", "requires_byol"),
    [
        (_StatusConnector("mand_error", EnrichmentStatus.ERROR), False),
        (_StatusConnector("mand_blocked", EnrichmentStatus.HIT), True),  # BYOL block
        (_StatusConnector("mand_raise", EnrichmentStatus.HIT, raises=True), False),
    ],
)
def test_acceptance_mandatory_failures_are_fatal_in_strict(
    connector: Any, requires_byol: bool
) -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(connector, requires_byol=requires_byol)
        return reg

    with pytest.raises(RuntimeError) as exc_info:
        _run_step(strict=True, registry_factory=registry)
    assert connector.provider_id in str(exc_info.value)


def test_acceptance_optional_failure_is_non_fatal_and_recorded() -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_StatusConnector("opt_error", EnrichmentStatus.ERROR), optional_in_strict=True)
        reg.register(_StatusConnector("mand_hit", EnrichmentStatus.HIT))
        return reg

    summary = _run_step(strict=True, registry_factory=registry)  # must NOT raise
    rows = {row["provider_id"]: row for row in summary["enrichment_ledger"]["providers"]}
    assert rows["opt_error"]["status"] == "ERROR"
    assert rows["opt_error"]["optional_in_strict"] is True
    assert rows["mand_hit"]["status"] == "HIT"
    assert summary["enrichment_ledger"]["counts"]["error"] == 1
    assert summary["enrichment_ledger"]["counts"]["hit"] == 1


# === B. Ledger completeness: hit/miss/error/cache/blocked rows + counts ===


def test_acceptance_ledger_covers_all_outcomes_including_cache() -> None:
    registry = EnrichmentProviderRegistry()
    registry.register(_CachingConnector("cached_hit", EnrichmentStatus.HIT))
    registry.register(_StatusConnector("plain_miss", EnrichmentStatus.MISS))
    registry.register(_StatusConnector("plain_error", EnrichmentStatus.ERROR))
    registry.register(_StatusConnector("byol_blocked", EnrichmentStatus.HIT), requires_byol=True)
    service = _service(registry)
    # Warm the cache with the step's exact request shape so the step sees a cache hit.
    warmed = service.enrich(
        provider_id="cached_hit",
        request=EnrichmentRequest(
            tenant_id=TENANT_ID,
            entity_type=EntityType.COMPANY,
            query=EnrichmentQuery(company_name="deal-1"),
            purpose=EnrichmentPurpose.DUE_DILIGENCE,
        ),
    )
    assert warmed.from_cache is False

    summary = _run_step(strict=False, service=service)
    rows = {row["provider_id"]: row for row in summary["enrichment_ledger"]["providers"]}
    assert rows["cached_hit"]["status"] == "HIT" and rows["cached_hit"]["from_cache"] is True
    assert rows["plain_miss"]["status"] == "MISS"
    assert rows["plain_error"]["status"] == "ERROR"
    assert rows["byol_blocked"]["status"] == "BLOCKED_MISSING_BYOL"
    assert summary["enrichment_ledger"]["counts"] == {
        "hit": 1,
        "miss": 1,
        "error": 1,
        "blocked_rights": 0,
        "blocked_missing_byol": 1,
        "cache_hits": 1,
    }


# === C. Bullet 2: enrichment provenance visible in the VC package (end-to-end) ===


def _acceptance_registry() -> EnrichmentProviderRegistry:
    reg = EnrichmentProviderRegistry()
    # GREEN HIT whose provenance identity mismatches the deal -> conflict flag + grade B.
    reg.register(_IdentityConnector("green_mismatch_hit", "Some Other Co"))
    # RED provider blocked on missing BYOL -> grade D row.
    reg.register(_RedConnector("red_blocked", EnrichmentStatus.HIT), requires_byol=True)
    # Optional failing provider -> recorded, non-fatal even in strict.
    reg.register(_StatusConnector("opt_error", EnrichmentStatus.ERROR), optional_in_strict=True)
    return reg


def test_acceptance_vc_package_surfaces_enrichment_provenance(tmp_path: Any) -> None:
    # Real NON-STRICT step summary (strict fatality is bullet 1's concern above); the RED
    # provider blocks on missing BYOL and the optional provider errors — both recorded.
    def registry() -> EnrichmentProviderRegistry:
        reg = _acceptance_registry()
        return reg

    summary = _run_step(strict=False, registry_factory=registry)
    # Thread exactly like the orchestrator does.
    enrichment_evidence = {
        "enrichment_ledger": summary["enrichment_ledger"],
        "enrichment_refs": summary["enrichment_refs"],
    }
    _, object_store = _export(tmp_path, enrichment_evidence)

    run_summary = _artifact_json(object_store, "run_summary")
    assert run_summary["enrichment_status"] == "executed"
    assert run_summary["enrichment_provider_count"] == 3
    assert run_summary["enrichment_hit_count"] == 1
    assert run_summary["enrichment_error_count"] == 1
    assert run_summary["enrichment_blocked_count"] == 1

    package = _artifact_json(object_store, "evidence_index")["enrichment_evidence"]
    rows = {row["provider_id"]: row for row in package["providers"]}
    hit = rows["green_mismatch_hit"]
    assert hit["source_grade"] == "B"  # GREEN -> B
    assert hit["ref_id"] and hit["ref_id"] in summary["enrichment_refs"]  # provider provenance
    assert hit["conflicts"] == [{"code": "identifier_mismatch", "field": "company_name"}]
    assert rows["red_blocked"]["source_grade"] == "D"  # RED without BYOL -> D
    assert rows["red_blocked"]["status"] == "BLOCKED_MISSING_BYOL"
    assert rows["opt_error"]["optional_in_strict"] is True


def test_acceptance_no_normalized_payloads_copied_into_bundle(tmp_path: Any) -> None:
    class _PayloadConnector(_StatusConnector):
        def fetch(self, request: Any, ctx: Any) -> Any:
            result = super().fetch(request, ctx)
            return result.model_copy(update={"normalized": {"secret_blob": _PAYLOAD_MARKER}})

    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_PayloadConnector("payload_hit", EnrichmentStatus.HIT))
        return reg

    summary = _run_step(strict=False, registry_factory=registry)
    assert _PAYLOAD_MARKER not in json.dumps(summary, sort_keys=True, default=str)

    enrichment_evidence = {
        "enrichment_ledger": summary["enrichment_ledger"],
        "enrichment_refs": summary["enrichment_refs"],
    }
    _, object_store = _export(tmp_path, enrichment_evidence)
    for name in ("run_summary", "evidence_index"):
        assert _PAYLOAD_MARKER not in json.dumps(_artifact_json(object_store, name), sort_keys=True)


# === E. URL keys + planted markers never leak (step, exceptions, bundle, captured logs) ===


def test_acceptance_url_key_connectors_leak_free_end_to_end(
    caplog: pytest.LogCaptureFixture, tmp_path: Any
) -> None:
    from idis.services.enrichment.connectors.finnhub import FinnhubConnector
    from idis.services.enrichment.connectors.fmp import FmpConnector
    from idis.services.enrichment.connectors.fred import FredConnector

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, json={"error": "upstream"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ticker_request = EnrichmentRequest(
        tenant_id=TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(ticker="AAPL", company_name="safe-public-company"),
    )
    from idis.services.enrichment.models import EnrichmentContext

    ctx = EnrichmentContext(
        timeout_seconds=2.0,
        max_retries=0,
        request_id="slice86-acceptance",
        byol_credentials={"api_key": _KEY, "token": _KEY},
    )
    # Real URL-key connectors against a failing transport: contained ERRORs, key-free logs.
    with caplog.at_level(logging.INFO, logger="httpx"):
        for connector in (
            FredConnector(http_client=client),
            FinnhubConnector(http_client=client),
            FmpConnector(http_client=client),
        ):
            result = connector.fetch(ticker_request, ctx)
            assert result.status == EnrichmentStatus.ERROR
            assert _KEY not in result.model_dump_json()
    for record in caplog.records:
        assert _KEY not in record.getMessage()

    # Strict step with an optional leaky-error fake + planted env markers: marker-free summary
    # and marker-free exported bundle.
    confidential = " ".join(_MARKERS)

    class _LeakyError(_StatusConnector):
        def fetch(self, request: Any, ctx: Any) -> Any:
            raise RuntimeError(confidential)

    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_LeakyError("opt_leaky", EnrichmentStatus.HIT), optional_in_strict=True)
        reg.register(_StatusConnector("plain_hit", EnrichmentStatus.HIT))
        return reg

    with patch.dict(os.environ, {"FRED_API_KEY": _KEY, "FMP_API_KEY": _KEY}, clear=False):
        summary = _run_step(strict=True, registry_factory=registry)
    blob = json.dumps(summary, sort_keys=True, default=str)
    for marker in _MARKERS:
        assert marker not in blob

    enrichment_evidence = {
        "enrichment_ledger": summary["enrichment_ledger"],
        "enrichment_refs": summary["enrichment_refs"],
    }
    _, object_store = _export(tmp_path, enrichment_evidence)
    for name in ("run_summary", "evidence_index"):
        bundle_blob = json.dumps(_artifact_json(object_store, name), sort_keys=True)
        for marker in _MARKERS:
            assert marker not in bundle_blob
