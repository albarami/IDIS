"""Slice86 Task 5 — narrow enrichment conflict checks (RED-first).

Implements the master-plan "conflict checks" bullet at the locked NARROW scope (D-G): per HIT,
compare the REQUEST's structured query identifiers against the provider's
``provenance.identifiers_used`` (same field names, case/whitespace-insensitive). A mismatch is
recorded on the provider's ledger row as a safe flag ``{"code": "identifier_mismatch",
"field": <identifier name>}`` — NEVER fatal, NEVER carrying the compared values or any provider
payload. Rows gain an additive ``conflicts`` list (empty for matches and non-HIT rows); the VC
bundle's enrichment package propagates the sanitized flags. No broad value comparison, no strict
fatality changes, no hardening, no DB, no real provider calls, no Slice87.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from idis.services.enrichment.models import (
    EnrichmentProvenance,
    EnrichmentResult,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from tests.test_slice86_enrichment_execution_provenance_characterization import (
    TENANT_ID,
    _env_without,
    _mixed_registry,
    _StatusConnector,
)
from tests.test_slice86_source_grade_vc_visibility import (
    _ENRICHMENT_EVIDENCE,
    _artifact_json,
    _export,
)

_LEAK_MARKERS = ("sk-s86-conf-LEAK-1", "/var/secret/s86-conf", "Other Corp Confidential Ltd")


class _IdentityConnector(_StatusConnector):
    """HIT connector whose provenance reports a configurable used identifier."""

    def __init__(self, provider_id: str, used_company_name: str) -> None:
        super().__init__(provider_id, EnrichmentStatus.HIT)
        self._used_company_name = used_company_name

    def fetch(self, request: Any, ctx: Any) -> EnrichmentResult:
        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized={"safe": "result"},
            provenance=EnrichmentProvenance(
                provider_id=self.provider_id,
                source_id=self.provider_id,
                retrieved_at=datetime.now(UTC),
                rights_class=RightsClass.GREEN,
                raw_ref_hash="safehash",
                identifiers_used={"company_name": self._used_company_name},
            ),
        )


def _run_step(registry_factory: Any, *, strict: bool = False) -> dict[str, Any]:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch("idis.services.enrichment.service._build_default_registry", registry_factory),
        patch(
            "idis.api.routes.runs.is_strict_full_live_required",
            return_value=strict,
        ),
    ):
        return _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )


# --- the pure comparator ---


def test_identifier_conflicts_comparator() -> None:
    from idis.services.enrichment.conflicts import identifier_conflicts

    # Exact match and case/whitespace-insensitive match -> no conflict.
    assert identifier_conflicts({"company_name": "deal-1"}, {"company_name": "deal-1"}) == []
    assert identifier_conflicts({"company_name": "Deal-1 "}, {"company_name": "deal-1"}) == []
    # Same field, different identity -> one safe flag (code + field only).
    assert identifier_conflicts({"company_name": "deal-1"}, {"company_name": "Other Corp"}) == [
        {"code": "identifier_mismatch", "field": "company_name"}
    ]
    # Disjoint fields are NOT compared (narrow scope; no inference across fields).
    assert identifier_conflicts({"company_name": "deal-1"}, {"ticker": "AAPL"}) == []
    # Multiple shared mismatches -> deterministic field order.
    flags = identifier_conflicts(
        {"company_name": "deal-1", "ticker": "AAPL"},
        {"company_name": "x", "ticker": "MSFT"},
    )
    assert [flag["field"] for flag in flags] == ["company_name", "ticker"]
    assert all(set(flag) == {"code", "field"} for flag in flags)


# --- ledger rows carry conflicts; match vs mismatch differential ---


def test_ledger_rows_record_identifier_mismatch_per_hit() -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_IdentityConnector("matching_hit", "deal-1"))  # step queries deal_id
        reg.register(_IdentityConnector("mismatched_hit", _LEAK_MARKERS[2]))
        reg.register(_StatusConnector("plain_miss", EnrichmentStatus.MISS))
        return reg

    summary = _run_step(registry)
    rows = {row["provider_id"]: row for row in summary["enrichment_ledger"]["providers"]}
    assert rows["matching_hit"]["conflicts"] == []
    assert rows["mismatched_hit"]["conflicts"] == [
        {"code": "identifier_mismatch", "field": "company_name"}
    ]
    assert rows["plain_miss"]["conflicts"] == []  # non-HIT rows carry the empty list


def test_conflicts_are_recorded_never_fatal_in_strict() -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_IdentityConnector("mismatched_hit", "Other Corp"))
        return reg

    summary = _run_step(registry, strict=True)  # must NOT raise (mandatory provider, HIT)
    (row,) = summary["enrichment_ledger"]["providers"]
    assert row["status"] == "HIT"
    assert row["conflicts"] == [{"code": "identifier_mismatch", "field": "company_name"}]


def test_existing_counts_and_legacy_fields_unchanged() -> None:
    summary = _run_step(_mixed_registry)
    assert set(summary["enrichment_ledger"]["counts"]) == {
        "hit",
        "miss",
        "error",
        "blocked_rights",
        "blocked_missing_byol",
        "cache_hits",
    }
    assert summary["result_count"] == 1
    assert summary["blocked_count"] == 1


# --- leak safety: compared values never surface ---


def test_conflict_flags_carry_no_compared_values() -> None:
    def registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_IdentityConnector("leaky_identity", _LEAK_MARKERS[2]))
        return reg

    env_markers = {"FRED_API_KEY": _LEAK_MARKERS[0]}
    with patch.dict(os.environ, env_markers, clear=False):
        summary = _run_step(registry)
    blob = json.dumps(summary, sort_keys=True, default=str)
    for marker in _LEAK_MARKERS:
        assert marker not in blob  # neither the provider identity nor any planted marker


# --- VC bundle propagation (package reuses ledger rows) ---


def test_enrichment_package_propagates_sanitized_conflicts(tmp_path: Any) -> None:
    evidence = json.loads(json.dumps(_ENRICHMENT_EVIDENCE))
    evidence["enrichment_ledger"]["providers"][0]["conflicts"] = [
        {"code": "identifier_mismatch", "field": "company_name"},
        {"code": "identifier_mismatch", "field": "ticker", "bogus_value": _LEAK_MARKERS[2]},
        "not-a-dict",
    ]
    _, object_store = _export(tmp_path, evidence)
    package = _artifact_json(object_store, "evidence_index")["enrichment_evidence"]
    rows = {row["provider_id"]: row for row in package["providers"]}
    assert rows["fake_hit"]["conflicts"] == [
        {"code": "identifier_mismatch", "field": "company_name"},
        {"code": "identifier_mismatch", "field": "ticker"},
    ]
    assert rows["fake_red"]["conflicts"] == []  # absent in evidence -> empty, never None
    blob = json.dumps(package, sort_keys=True)
    for marker in _LEAK_MARKERS:
        assert marker not in blob
