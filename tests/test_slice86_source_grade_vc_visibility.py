"""Slice86 Task 4 — source-grade mapping + VC-package enrichment visibility (RED-first).

Implements the master-plan "source-grade mapping" bullet and acceptance (2) "enrichment
provenance is visible in VC package" (plan §3 G3+G4; decisions D-E/D-F locked):
  - pure mapping at summary/export time — GREEN→B, YELLOW→C, RED+BYOL→C, RED without BYOL→D;
    grade A is reserved for primary/audited documents and is NEVER emitted for enrichment;
    the grade is NOT persisted into EnrichmentProvenance;
  - ledger rows gain a safe ``source_grade`` value (computed where rights + BYOL truth live);
  - enrichment threads through ``_run_full_deliverables`` → ``ProductBundleExporter``:
    ``run_summary`` gains enrichment counts and ``evidence_index`` gains a sanitized
    ``enrichment_evidence`` package (mirroring the graph/rag/layer2 package style);
  - the strict matrix's registered ``provenance_output_status`` flips from
    ``provenance_ref_on_hit_not_final_output_visible`` to ``enrichment_package_output_visible``.
No conflict checks, no URL-key/httpx hardening, no DB migration, no real provider calls,
no Layer-1 debate changes, no Slice87.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from idis.persistence.repositories.enrichment_credentials import InMemoryCredentialRepository
from idis.services.enrichment.models import (
    EnrichmentProvenance,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    RecordingDeliverablesRepository,
    _make_context,
    _make_deliverables_bundle,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import (
    TENANT_ID as EXPORT_TENANT_ID,
)
from tests.test_slice86_enrichment_execution_provenance_characterization import (
    TENANT_ID,
    _env_without,
    _mixed_registry,
    _StatusConnector,
)
from tests.test_slice86_enrichment_ledger_cache_visibility import _service

_TIMESTAMP = "2026-06-11T00:00:00Z"
_LEAK_MARKERS = ("sk-s86-vc-LEAK-1", "/var/secret/s86-vc", "C:\\secret\\s86vc")

_ENRICHMENT_EVIDENCE = {
    "enrichment_ledger": {
        "providers": [
            {
                "provider_id": "fake_hit",
                "status": "HIT",
                "from_cache": False,
                "rights_class": "GREEN",
                "optional_in_strict": False,
                "ref_id": "enrich-fake_hit-run-1",
                "source_grade": "B",
            },
            {
                "provider_id": "fake_red",
                "status": "BLOCKED_MISSING_BYOL",
                "from_cache": False,
                "rights_class": "RED",
                "optional_in_strict": False,
                "ref_id": None,
                "source_grade": "D",
            },
        ],
        "counts": {
            "hit": 1,
            "miss": 0,
            "error": 0,
            "blocked_rights": 0,
            "blocked_missing_byol": 1,
            "cache_hits": 0,
        },
    },
    "enrichment_refs": {
        "enrich-fake_hit-run-1": {
            "ref_id": "enrich-fake_hit-run-1",
            "provider_id": "fake_hit",
            "source_id": "fake_hit",
        }
    },
}


class _RedConnector(_StatusConnector):
    @property
    def rights_class(self) -> RightsClass:
        return RightsClass.RED


def _exporter(tmp_path: Path) -> tuple[Any, Any]:
    from idis.deliverables.product_bundle import ProductBundleExporter
    from idis.storage.filesystem_store import FilesystemObjectStore

    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    return exporter, object_store


def _export(tmp_path: Path, enrichment_evidence: dict[str, Any] | None) -> tuple[Any, Any]:
    exporter, object_store = _exporter(tmp_path)
    summary = exporter.export_bundle(
        tenant_id=EXPORT_TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        enrichment_evidence=enrichment_evidence,
    )
    return summary, object_store


def _artifact_json(object_store: Any, name: str) -> dict[str, Any]:
    blob = object_store.get(
        tenant_id=EXPORT_TENANT_ID,
        key=f"runs/{RUN_ID}/product_bundle/{name}.json",
    )
    return json.loads(blob.body.decode("utf-8"))


# --- D-E: the pure mapping rule (never A; not persisted) ---


def test_source_grade_mapping_rule() -> None:
    from idis.models.evidence_item import SourceGrade
    from idis.services.enrichment.source_grade import map_rights_to_source_grade

    assert map_rights_to_source_grade(RightsClass.GREEN, has_byol=False) == SourceGrade.B
    assert map_rights_to_source_grade(RightsClass.GREEN, has_byol=True) == SourceGrade.B
    assert map_rights_to_source_grade(RightsClass.YELLOW, has_byol=False) == SourceGrade.C
    assert map_rights_to_source_grade(RightsClass.YELLOW, has_byol=True) == SourceGrade.C
    assert map_rights_to_source_grade(RightsClass.RED, has_byol=True) == SourceGrade.C
    assert map_rights_to_source_grade(RightsClass.RED, has_byol=False) == SourceGrade.D
    for rights in RightsClass:
        for has_byol in (False, True):
            assert map_rights_to_source_grade(rights, has_byol=has_byol) != SourceGrade.A


def test_grade_is_not_persisted_into_enrichment_provenance() -> None:
    assert not any("grade" in field.lower() for field in EnrichmentProvenance.model_fields)


# --- ledger rows carry source_grade (computed at step time) ---


def test_ledger_rows_carry_source_grade() -> None:
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
    grades = {
        row["provider_id"]: row["source_grade"] for row in summary["enrichment_ledger"]["providers"]
    }
    # All mixed-registry fakes are GREEN -> B, regardless of outcome status.
    assert grades == {"fake_hit": "B", "fake_miss": "B", "fake_error": "B", "fake_byol": "B"}


def test_red_provider_grades_c_with_byol_d_without() -> None:
    from idis.api.routes.runs import _run_full_enrichment

    # RED + BYOL credentials present -> C (warm service whose repo holds the credentials).
    registry = EnrichmentProviderRegistry()
    registry.register(_RedConnector("red_byol", EnrichmentStatus.HIT), requires_byol=True)
    service = _service(registry)
    service._credential_repo.store(  # type: ignore[attr-defined]
        tenant_id=TENANT_ID, connector_id="red_byol", credentials={"api_key": "k"}
    )

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch(
            "idis.services.enrichment.service.create_default_enrichment_service",
            lambda **_kw: service,
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
    (row,) = summary["enrichment_ledger"]["providers"]
    assert row["status"] == "HIT"
    assert row["source_grade"] == "C"  # RED + BYOL

    # RED requiring BYOL with NO credentials -> blocked -> D.
    def blocked_registry() -> EnrichmentProviderRegistry:
        reg = EnrichmentProviderRegistry()
        reg.register(_RedConnector("red_no_byol", EnrichmentStatus.HIT), requires_byol=True)
        return reg

    with (
        patch.dict(os.environ, env, clear=True),
        patch(
            "idis.services.enrichment.service._build_default_registry",
            blocked_registry,
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
    (row,) = summary["enrichment_ledger"]["providers"]
    assert row["status"] == "BLOCKED_MISSING_BYOL"
    assert row["source_grade"] == "D"  # RED without BYOL


# --- VC package: run_summary counts + evidence_index.enrichment_evidence ---


def test_export_run_summary_contains_enrichment_counts(tmp_path: Path) -> None:
    summary, object_store = _export(tmp_path, _ENRICHMENT_EVIDENCE)
    assert summary["artifact_count"] == 14  # no new artifacts; fields are additive
    run_summary = _artifact_json(object_store, "run_summary")
    assert run_summary["enrichment_status"] == "executed"
    assert run_summary["enrichment_provider_count"] == 2
    assert run_summary["enrichment_hit_count"] == 1
    assert run_summary["enrichment_miss_count"] == 0
    assert run_summary["enrichment_error_count"] == 0
    assert run_summary["enrichment_blocked_count"] == 1
    assert run_summary["enrichment_cache_hit_count"] == 0


def test_export_evidence_index_contains_enrichment_package(tmp_path: Path) -> None:
    _, object_store = _export(tmp_path, _ENRICHMENT_EVIDENCE)
    evidence_index = _artifact_json(object_store, "evidence_index")
    package = evidence_index["enrichment_evidence"]
    assert package["status"] == "executed"
    assert package["counts"]["hit"] == 1
    rows = {row["provider_id"]: row for row in package["providers"]}
    assert rows["fake_hit"]["source_grade"] == "B"
    assert rows["fake_hit"]["ref_id"] == "enrich-fake_hit-run-1"
    assert rows["fake_red"]["source_grade"] == "D"
    # graph/rag/layer2 packages remain intact alongside (regression).
    assert {"graph_evidence", "rag_evidence", "layer2_evidence"} <= set(evidence_index)


def test_export_without_enrichment_evidence_is_skipped_package(tmp_path: Path) -> None:
    summary, object_store = _export(tmp_path, None)
    assert summary["artifact_count"] == 14
    run_summary = _artifact_json(object_store, "run_summary")
    assert run_summary["enrichment_status"] == "skipped"
    assert run_summary["enrichment_provider_count"] == 0
    evidence_index = _artifact_json(object_store, "evidence_index")
    assert evidence_index["enrichment_evidence"]["status"] == "skipped"
    assert evidence_index["enrichment_evidence"]["providers"] == []


def test_export_sanitizes_bogus_evidence_fields_leak_free(tmp_path: Path) -> None:
    leaky = json.loads(json.dumps(_ENRICHMENT_EVIDENCE))
    leaky["enrichment_ledger"]["providers"][0]["bogus_payload"] = _LEAK_MARKERS[0]
    leaky["enrichment_ledger"]["providers"][0]["normalized"] = {"path": _LEAK_MARKERS[1]}
    leaky["enrichment_ledger"]["counts"]["bogus"] = _LEAK_MARKERS[2]
    leaky["bogus_top_level"] = _LEAK_MARKERS[0]

    _, object_store = _export(tmp_path, leaky)
    for name in ("run_summary", "evidence_index"):
        blob = json.dumps(_artifact_json(object_store, name), sort_keys=True)
        for marker in _LEAK_MARKERS:
            assert marker not in blob
    package = _artifact_json(object_store, "evidence_index")["enrichment_evidence"]
    assert set(package["providers"][0]) == {
        "provider_id",
        "status",
        "from_cache",
        "rights_class",
        "optional_in_strict",
        "ref_id",
        "source_grade",
        "conflicts",  # Task 5 drift: sanitized {code, field} flags only
    }


# --- threading: _run_full_deliverables + orchestrator ---


def test_deliverables_step_and_orchestrator_thread_enrichment_evidence() -> None:
    from idis.api.routes.runs import _run_full_deliverables
    from idis.services.runs import orchestrator

    assert "enrichment_evidence" in inspect.signature(_run_full_deliverables).parameters
    orchestrator_source = inspect.getsource(orchestrator)
    assert "enrichment_evidence=" in orchestrator_source
    assert 'accumulated.get("enrichment_ledger")' in orchestrator_source


# --- strict matrix flip: registered providers are now output-visible ---


def test_matrix_registered_providers_output_visible() -> None:
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
    registered = [m for m in report.enrichment_provider_matrix if m.registry_status == "registered"]
    unregistered = [
        m for m in report.enrichment_provider_matrix if m.registry_status == "not_registered"
    ]
    assert registered and unregistered
    assert {m.provenance_output_status for m in registered} == {"enrichment_package_output_visible"}
    assert {m.provenance_output_status for m in unregistered} == {"not_output_visible"}
