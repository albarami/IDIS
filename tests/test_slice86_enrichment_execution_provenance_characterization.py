"""Slice86 Task 1 — characterization pinning the CURRENT enrichment-execution truth.

GREEN-on-arrival expected: this pins what Slice57/85 already built and the exact gaps the
Slice86 plan (docs/plans/2026-06-11-slice86-enrichment-execution-provenance.md §3) will close,
so later tasks change behavior deliberately (controlled drift). No production change, no real
provider call (fake connectors / httpx.MockTransport only), no DB, no Slice87.

Pins (per the locked decisions D-A..D-I):
  1. ENRICHMENT step summary keeps the four legacy fields (legacy count semantics unchanged)
     and gains the additive enrichment_ledger (Task 3) — MISS/ERROR are now ledger-visible.
  2. Strict fatality for MANDATORY providers — ERROR status, BLOCKED_*, and raised exceptions
     all abort the strict step (Task 2 added the optional_in_strict escape; defaults stay
     mandatory, so these pins hold; see test_slice86_optional_provider_strict_policy.py).
  3. The optionality policy exists with mandatory defaults (drift-flipped after Task 2).
  4. Cache hits emit enrichment.cache_hit AND mark the returned copy from_cache=True
     (Task 3 drift-flipped D-D); live results stay from_cache=False.
  5. Strict matrix strings: registered providers now report the output-visible
     'enrichment_package_output_visible' (Task 4 drift-flipped G3); strict_behavior values for
     the all-mandatory default registry remain the three known tokens.
  6. Deliverables/product bundle thread enrichment_evidence into the VC package (Task 4).
  7. enrichment_refs feed analysis, scoring, and Layer 2 only — Layer-1 debate consumes none
     (D-I) — and the refs carry exactly {ref_id, provider_id, source_id}.
  8. The RightsClass→SourceGrade mapping exists (Task 4); never persisted into provenance.
  9. FRED/Finnhub/FMP embed the credential in the request URL (their APIs are query-param
     only; Finnhub header auth was NOT verifiable from primary docs), errors are CONTAINED to
     fixed strings, and Task 6 added FetchError + httpx-log redaction (drift-flipped G6).
 10. EnrichmentStatus has exactly five values — no CACHED status.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.enrichment_credentials import InMemoryCredentialRepository
from idis.services.enrichment.cache_policy import EnrichmentCacheStore
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry, ProviderDescriptor
from idis.services.enrichment.rights_gate import EnvironmentMode
from idis.services.enrichment.service import EnrichmentService

TENANT_ID = "tenant-slice86"
_KEY_MARKER = "sk-s86-LEAK-0001"

_ENRICHMENT_DIR = Path("src/idis/services/enrichment")


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


def _provenance(provider_id: str) -> EnrichmentProvenance:
    return EnrichmentProvenance(
        provider_id=provider_id,
        source_id=provider_id,
        retrieved_at=datetime.now(UTC),
        rights_class=RightsClass.GREEN,
        raw_ref_hash="safehash",
        identifiers_used={"company_name": "safe-public-company"},
    )


class _StatusConnector:
    """Fake connector returning a fixed status (or raising); never touches the network."""

    def __init__(self, provider_id: str, status: EnrichmentStatus, *, raises: bool = False) -> None:
        self._provider_id = provider_id
        self._status = status
        self._raises = raises

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
        if self._raises:
            raise RuntimeError("safe provider failure")
        if self._status == EnrichmentStatus.HIT:
            return EnrichmentResult(
                status=EnrichmentStatus.HIT,
                normalized={"safe": "result"},
                provenance=_provenance(self._provider_id),
            )
        return EnrichmentResult(status=self._status)


def _registry(*connectors: tuple[Any, bool]) -> EnrichmentProviderRegistry:
    registry = EnrichmentProviderRegistry()
    for connector, requires_byol in connectors:
        registry.register(connector, requires_byol=requires_byol)
    return registry


def _mixed_registry() -> EnrichmentProviderRegistry:
    # hit + miss + error + byol-blocked (requires_byol with empty repo) — one of each outcome.
    return _registry(
        (_StatusConnector("fake_hit", EnrichmentStatus.HIT), False),
        (_StatusConnector("fake_miss", EnrichmentStatus.MISS), False),
        (_StatusConnector("fake_error", EnrichmentStatus.ERROR), False),
        (_StatusConnector("fake_byol", EnrichmentStatus.HIT), True),
    )


# --- 1. summary shape: 3 counts + refs; MISS/ERROR silently dropped (G2) ---


def test_summary_keeps_legacy_fields_and_gains_ledger() -> None:
    # Task 3 drift-flip: the four legacy fields are unchanged (result_count still counts only
    # HITs; blocked_count only BLOCKED_*), and MISS/ERROR are no longer invisible — they are
    # recorded in the additive enrichment_ledger (full coverage in
    # test_slice86_enrichment_ledger_cache_visibility.py).
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch("idis.services.enrichment.service._build_default_registry", _mixed_registry),
    ):
        summary = _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    assert set(summary) == {
        "provider_count",
        "result_count",
        "blocked_count",
        "enrichment_refs",
        "enrichment_ledger",
    }
    assert summary["provider_count"] == 4
    assert summary["result_count"] == 1  # legacy semantics: only the HIT
    assert summary["blocked_count"] == 1  # legacy semantics: only the BYOL block
    (ref,) = summary["enrichment_refs"].values()
    assert set(ref) == {"ref_id", "provider_id", "source_id"}
    assert ref["provider_id"] == "fake_hit"
    statuses = {
        row["provider_id"]: row["status"] for row in summary["enrichment_ledger"]["providers"]
    }
    assert statuses["fake_miss"] == "MISS" and statuses["fake_error"] == "ERROR"  # now visible


# --- 2. strict fatality is uniform: ERROR / BLOCKED / exception all abort (G1) ---


@pytest.mark.parametrize(
    ("connector", "requires_byol", "expected_fragment"),
    [
        (_StatusConnector("fatal_error", EnrichmentStatus.ERROR), False, "fatal_error"),
        (_StatusConnector("fatal_byol", EnrichmentStatus.HIT), True, "fatal_byol"),
        (
            _StatusConnector("fatal_raise", EnrichmentStatus.HIT, raises=True),
            False,
            "fatal_raise",
        ),
    ],
)
def test_strict_fatality_is_uniform(
    connector: Any, requires_byol: bool, expected_fragment: str
) -> None:
    from idis.api.routes.runs import _run_full_enrichment

    def registry() -> EnrichmentProviderRegistry:
        return _registry((connector, requires_byol))

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch("idis.services.enrichment.service._build_default_registry", registry),
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
    assert expected_fragment in str(exc_info.value)


# --- 3. no optionality flag anywhere (G1) ---


def test_optionality_policy_exists_and_defaults_to_mandatory() -> None:
    # Slice86 Task 2 added the optional-vs-fatal policy: descriptor + register() carry
    # optional_in_strict (default False), and all 15 default providers stay mandatory (D-C).
    field_names = {f.name for f in dataclasses.fields(ProviderDescriptor)}
    assert field_names == {
        "provider_id",
        "rights_class",
        "cache_policy",
        "requires_byol",
        "connector",
        "optional_in_strict",
    }
    register_params = set(inspect.signature(EnrichmentProviderRegistry.register).parameters)
    assert register_params == {"self", "connector", "requires_byol", "optional_in_strict"}
    from idis.services.enrichment.service import _build_default_registry

    assert all(d.optional_in_strict is False for d in _build_default_registry().list_providers())


# --- 4. cache hits: audit event + from_cache marker (Task 3 drift-flipped D-D) ---


def test_cache_hit_emits_audit_and_marks_from_cache() -> None:
    sink = InMemoryAuditSink()
    connector = _StatusConnector("cached_provider", EnrichmentStatus.HIT)

    class _CachingConnector(_StatusConnector):
        @property
        def cache_policy(self) -> CachePolicyConfig:
            return CachePolicyConfig(ttl_seconds=3600, no_store=False)

    caching = _CachingConnector("cached_provider", EnrichmentStatus.HIT)
    registry = _registry((caching, False))
    service = EnrichmentService(
        registry=registry,
        audit_sink=sink,
        credential_repo=InMemoryCredentialRepository(),
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    request = EnrichmentRequest(
        tenant_id=TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name="safe-public-company"),
    )
    first = service.enrich(provider_id="cached_provider", request=request)
    second = service.enrich(provider_id="cached_provider", request=request)
    assert first.status == second.status == EnrichmentStatus.HIT
    cache_events = [e for e in sink.events if e.get("event_type") == "enrichment.cache_hit"]
    assert len(cache_events) == 1
    # Task 3 drift-flip: the result model now carries the additive from_cache marker, set only
    # on cache-served copies (full coverage in test_slice86_enrichment_ledger_cache_visibility).
    assert "from_cache" in EnrichmentResult.model_fields
    assert first.from_cache is False
    assert second.from_cache is True
    assert connector  # silence unused (kept for symmetry with the caching subclass)


# --- 5. strict matrix strings (hermetic; G3 self-documented gap) ---


def test_matrix_strict_behavior_and_provenance_output_status_strings() -> None:
    from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

    class _Durable(InMemoryCredentialRepository):
        is_durable = True

    class _Healthy:
        def check(self, *, provider_id: str, credentials: dict[str, str]) -> bool:
            return True

    report = build_strict_full_live_readiness_report(
        env={},
        tenant_id=TENANT_ID,
        byol_credential_repo=_Durable(),
        byol_health_checker=_Healthy(),
    )
    matrix = report.enrichment_provider_matrix
    assert matrix  # populated
    registered = [m for m in matrix if m.registry_status == "registered"]
    unregistered = [m for m in matrix if m.registry_status == "not_registered"]
    assert registered and unregistered
    # Task 4 drift-flip: registered providers are now output-visible via the VC bundle's
    # enrichment package.
    assert {m.provenance_output_status for m in registered} == {"enrichment_package_output_visible"}
    assert {m.provenance_output_status for m in unregistered} == {"not_output_visible"}
    assert {m.strict_behavior for m in matrix} <= {
        "strict_fail_closed_on_error",
        "strict_blocks_until_byol_ready",
        "not_registered_not_wired",
    }


# --- 6. deliverables / product bundle are blind to enrichment (G3) ---


def test_deliverables_and_product_bundle_carry_enrichment_evidence() -> None:
    # Task 4 drift-flip: deliverables/product bundle now thread enrichment_evidence and the
    # bundle exposes the enrichment package (full coverage in
    # test_slice86_source_grade_vc_visibility.py).
    from idis.api.routes.runs import _run_full_deliverables
    from idis.deliverables.product_bundle import ProductBundleExporter

    deliverables_params = set(inspect.signature(_run_full_deliverables).parameters)
    assert {"graph_evidence", "rag_evidence", "layer2_evidence"} <= deliverables_params
    assert "enrichment_evidence" in deliverables_params

    export_params = set(inspect.signature(ProductBundleExporter.export_bundle).parameters)
    assert "enrichment_evidence" in export_params

    bundle_source = Path("src/idis/deliverables/product_bundle.py").read_text(encoding="utf-8")
    assert "_enrichment_package" in bundle_source


# --- 7. refs feed analysis/scoring/Layer 2 only; Layer-1 debate consumes none (D-I) ---


def test_refs_feed_analysis_scoring_layer2_not_debate() -> None:
    from idis.analysis.models import AnalysisContext
    from idis.analysis.scoring.engine import _ScoringNFFValidator
    from idis.api.routes.runs import _run_full_analysis, _run_full_layer2_ic_challenge

    assert "enrichment_refs" in inspect.signature(_run_full_analysis).parameters
    assert "enrichment_refs" in inspect.signature(_run_full_layer2_ic_challenge).parameters
    assert "enrichment_refs" in AnalysisContext.model_fields
    assert callable(_ScoringNFFValidator._validate_enrichment_refs)  # scoring-side NFF check

    debate_sources = "".join(
        path.read_text(encoding="utf-8") for path in Path("src/idis/debate").rglob("*.py")
    )
    assert "enrichment" not in debate_sources.lower()


# --- 8. no RightsClass -> SourceGrade mapping anywhere in enrichment code (G4) ---


def test_source_grade_mapping_exists_but_is_not_persisted() -> None:
    # Task 4 drift-flip: the RightsClass->SourceGrade mapping now lives in
    # idis.services.enrichment.source_grade (rule coverage in
    # test_slice86_source_grade_vc_visibility.py); the grade is computed at summary/export
    # time and never persisted into EnrichmentProvenance.
    from idis.models.evidence_item import SourceGrade
    from idis.services.enrichment.source_grade import map_rights_to_source_grade

    assert {g.value for g in SourceGrade} == {"A", "B", "C", "D"}
    assert {r.value for r in RightsClass} == {"GREEN", "YELLOW", "RED"}
    assert callable(map_rights_to_source_grade)
    assert not any("grade" in field.lower() for field in EnrichmentProvenance.model_fields)


# --- 9. URL-key risk + containment + no central redaction (G6) ---


def test_url_key_connectors_embed_credential_in_url() -> None:
    fred = (_ENRICHMENT_DIR / "connectors" / "fred.py").read_text(encoding="utf-8")
    finnhub = (_ENRICHMENT_DIR / "connectors" / "finnhub.py").read_text(encoding="utf-8")
    fmp = (_ENRICHMENT_DIR / "connectors" / "fmp.py").read_text(encoding="utf-8")
    assert "&api_key={api_key}" in fred  # credential in URL (documented Slice86 G6 risk)
    assert "&token={api_key}" in finnhub
    assert "?apikey={api_key}" in fmp


def test_url_key_provider_errors_are_contained_to_fixed_strings() -> None:
    from idis.services.enrichment.connectors.finnhub import FinnhubConnector
    from idis.services.enrichment.connectors.fmp import FmpConnector
    from idis.services.enrichment.connectors.fred import FredConnector

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, json={"error": "upstream"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ctx = EnrichmentContext(
        timeout_seconds=2.0,
        max_retries=0,
        request_id="slice86-characterization",
        byol_credentials={"api_key": _KEY_MARKER, "token": _KEY_MARKER},
    )
    request = EnrichmentRequest(
        tenant_id=TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(ticker="AAPL", company_name="safe-public-company"),
    )
    for connector in (
        FredConnector(http_client=client),
        FinnhubConnector(http_client=client),
        FmpConnector(http_client=client),
    ):
        result = connector.fetch(request, ctx)
        assert result.status == EnrichmentStatus.ERROR, connector.provider_id
        blob = result.model_dump_json()
        assert _KEY_MARKER not in blob  # contained: fixed error strings only


def test_central_httpx_redaction_layer_exists() -> None:
    # Task 6 drift-flip: a central redaction layer now exists (logging filter on the httpx
    # logger + FetchError message scrubbing via redact_secret_params); the per-connector
    # httpx.Client construction is retained (full coverage in
    # test_slice86_url_key_httpx_hardening.py).
    assert (_ENRICHMENT_DIR / "redaction.py").exists()
    for name in ("fred.py", "finnhub.py", "fmp.py"):
        source = (_ENRICHMENT_DIR / "connectors" / name).read_text(encoding="utf-8")
        assert "redact_secret_params" in source
        assert "install_httpx_redaction_filter" in source
        assert "httpx.Client(" in source  # per-connector client construction retained


# --- 10. status enum: exactly five values, no CACHED ---


def test_enrichment_status_has_no_cached_value() -> None:
    assert {s.value for s in EnrichmentStatus} == {
        "HIT",
        "MISS",
        "ERROR",
        "BLOCKED_RIGHTS",
        "BLOCKED_MISSING_BYOL",
    }
