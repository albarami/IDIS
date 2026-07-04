"""Slice92 Task 6 — acceptance proof for "Durable Layer 1 Evidence Trust Court".

Master-plan acceptance: **"Layer 1 output is durable and referenced by Layer 2."**

Proven end-to-end in ONE orchestrated FULL run (injected fakes, in-memory twins, no
real LLM, no database): the methodology chain materializes a REAL claim, the existing
Evidence Trust Court judges it, and then — the Slice92 substance —

  1. DURABLE: the VEP candidate row and the court-scoped Muhasabah rows (with their
     structured uncertainty triples) exist in the Layer-1 evidence repository, and
     the court findings row count matches the court record exactly.
  2. REFERENCED BY LAYER 2: the real ``_run_full_layer2_ic_challenge`` runs inside
     the same orchestrated run and its LAYER2 ledger row carries ``vep_ref_ids``
     equal to the persisted package id.
  3. OUTPUT-VISIBLE (DEC-E): the deliverables step receives the persisted block and
     the real exporter lists the same package id in ``evidence_index.vep_evidence``
     and the run_summary counts — with no claim text or transcripts anywhere.

Plus the degrade half: a no-claims run persists nothing, references nothing, and
exports the empty shape. GREEN-on-arrival expected (composes Tasks 2-5).
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from pathlib import Path
from typing import Any

from idis.api.routes.runs import _run_full_layer2_ic_challenge
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.models.run_step import StepName, StepStatus
from idis.persistence.repositories.layer1_evidence import (
    InMemoryLayer1EvidenceRepository,
    clear_in_memory_layer1_evidence_store,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_run_orchestrator_methodology_claim_materialization import (
    DEAL_ID,
    TENANT_ID,
    _ctx,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository


def setup_function() -> None:
    clear_run_steps_store()
    clear_in_memory_layer1_evidence_store()


def test_full_run_produces_durable_vep_referenced_by_layer2(tmp_path: Path) -> None:
    run_id = str(uuid_mod.uuid4())
    captured_deliverables: list[dict[str, Any]] = []

    def capturing_deliverables(**kwargs: Any) -> dict[str, Any]:
        captured_deliverables.append(kwargs)
        return {"deliverable_count": 1}

    ctx = _ctx(run_id)
    # The harness's operational stubs return empty refs; the real Layer-2 service
    # fails closed without claim/calc references, so supply production-shaped ones.
    ctx.extract_fn = lambda **_kwargs: {
        "status": "COMPLETED",
        "created_claim_ids": ["claim_mth_0123456789abcdef01234567"],
    }
    ctx.calc_fn = lambda **_kwargs: {"calc_ids": ["calc-1"], "reproducibility_hashes": []}
    ctx.debate_fn = lambda **_kwargs: {
        "debate_id": run_id,
        "stop_reason": "consensus",
        "round_number": 1,
        "muhasabah_passed": True,
        "agent_output_count": 2,
    }
    ctx.layer2_ic_challenge_fn = _run_full_layer2_ic_challenge  # the REAL Layer 2 fn
    ctx.deliverables_fn = capturing_deliverables

    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    result = orchestrator.execute(ctx)
    assert result.status == "SUCCEEDED"

    # The methodology chain judged a REAL claim (not the empty path).
    court = ctx.methodology_evidence_trust_court
    package = ctx.methodology_validated_evidence_package
    assert court is not None and package is not None
    assert len(ctx.methodology_materialized_claims) == 1

    # --- Acceptance 1: Layer 1 output is DURABLE ---
    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)
    persisted_packages = twin.list_validated_evidence_packages(run_id=run_id)
    assert [row["package_id"] for row in persisted_packages] == [package.package_id]
    assert persisted_packages[0]["court_id"] == court.court_id
    assert persisted_packages[0]["status"] == "completed"

    persisted_findings = twin.list_evidence_trust_findings(run_id=run_id)
    assert len(persisted_findings) == len(court.findings)

    persisted_muhasabah = twin.list_muhasabah_records(run_id=run_id)
    assert len(persisted_muhasabah) >= 1
    for record in persisted_muhasabah:
        assert record["source_step"] == "METHODOLOGY_EVIDENCE_TRUST_COURT"
        assert isinstance(record["uncertainties"], list)
        assert record["record_timestamp"]

    # --- Acceptance 2: the durable output is REFERENCED BY LAYER 2 ---
    layer2_step = repo.get_step(run_id, StepName.LAYER2_IC_CHALLENGE)
    assert layer2_step is not None and layer2_step.status == StepStatus.COMPLETED
    assert layer2_step.result_summary["vep_ref_ids"] == [package.package_id]

    # --- DEC-E: the same package id is output-visible in the product bundle ---
    vep_evidence = captured_deliverables[0]["vep_evidence"]
    assert vep_evidence["package_ids"] == [package.package_id]

    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice92-acceptance",
    )
    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=run_id,
        bundle=deliverables_bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        vep_evidence=vep_evidence,
    )
    evidence_index = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{run_id}/product_bundle/evidence_index.json",
        ).body.decode("utf-8")
    )
    run_summary = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{run_id}/product_bundle/run_summary.json",
        ).body.decode("utf-8")
    )
    assert evidence_index["vep_evidence"]["package_ids"] == [package.package_id]
    assert run_summary["vep_package_ids"] == [package.package_id]
    assert run_summary["vep_status"] == "persisted"

    encoded = json.dumps({"evidence_index": evidence_index, "run_summary": run_summary})
    assert "claim_text" not in encoded
    assert "transcript" not in encoded


def test_no_claims_run_degrades_with_empty_durable_and_reference_surfaces() -> None:
    run_id = str(uuid_mod.uuid4())
    captured_layer2: list[dict[str, Any]] = []
    captured_deliverables: list[dict[str, Any]] = []

    def capturing_layer2(**kwargs: Any) -> dict[str, Any]:
        captured_layer2.append(kwargs)
        return {"status": "completed", "layer2_challenge_ids": []}

    def capturing_deliverables(**kwargs: Any) -> dict[str, Any]:
        captured_deliverables.append(kwargs)
        return {"deliverable_count": 1}

    ctx = _ctx(run_id)
    # No planned extraction tasks -> no materialized claims -> the court takes the
    # empty path and persists nothing.
    ctx.methodology_extraction_task_planning_fn = None
    ctx.methodology_extraction_task_execution_fn = None
    ctx.layer2_ic_challenge_fn = capturing_layer2
    ctx.deliverables_fn = capturing_deliverables

    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    result = orchestrator.execute(ctx)
    assert result.status == "SUCCEEDED"

    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)
    assert twin.list_validated_evidence_packages(run_id=run_id) == []
    assert twin.list_evidence_trust_findings(run_id=run_id) == []
    assert twin.list_muhasabah_records(run_id=run_id) == []
    assert captured_layer2[0]["vep_package_ids"] is None
    assert captured_deliverables[0]["vep_evidence"] is None
