"""Slice92 Task 1 — characterization pinning the CURRENT Layer-1 court/VEP durability truth.

The Evidence Trust Court and Validated Evidence Package already exist on main and are
FULL-wired (phase-3-0k/0l, merged 2026-05-09): court = FULL step 12, VEP = step 13, both
in-memory services whose only durable trace is the run_steps.result_summary JSONB. This pins
the as-built reality Slice92 builds on, under the locked decisions (DEC-A..DEC-G):

  1. (Reuse) Court + VEP steps are IMPLEMENTED and ordered 12/13 — before GRAPH_EVIDENCE (20),
     RAG_EVIDENCE (21), ENRICHMENT (22); LAYER2_IC_CHALLENGE runs at 24. Per DEC-B the court's
     position and inputs stay as-is; graph/RAG/enrichment join at Layer 2 (Slice 93 scope).
  2. (G1 closed, Tasks 2-3 / DEC-A) Durable twin repositories + safe-shape rows exist
     (`layer1_evidence` + `layer1_durability`) AND the court/VEP step path persists
     through them: the orchestrator carries the persistence seams and steps.py binds
     the repository selector. The runs.py route layer stays repository-free (the
     persistence is orchestrator-level).
  3. (G1/DEC-A closed, Task 2) Migration 0021 adds the three Layer-1 durability tables.
  4. (G2 partly closed, Task 2) muhasabah_records persistence exists at the repo layer;
     the debate_sessions table remains written only by the API debate route, never the
     FULL path.
  5. (G4/DEC-D closed, Task 4) ``_run_full_layer2_ic_challenge`` accepts
     ``vep_package_ids`` and surfaces safe ``vep_ref_ids`` in its result — the durable
     Layer-1 output is referenced by Layer 2.
  6. (Reuse pin) The step-16 readiness package service already consumes
     ``validated_evidence_packages`` in-memory — the reference concept exists, undurably.
  7. (G5/DEC-E closed, Task 5) The product bundle exports a safe ``vep_evidence``
     package (IDs/counts/status only) in evidence_index plus run_summary counts.

  8. (Task 7) The strict readiness doc carries a post-Slice92 banner recording the
     durable Layer-1 court/VEP reality (frozen Slice-53 census preserved).

GREEN-on-arrival expected (characterization pins current truth). Any RED → STOP + report.
No production changes. No database, no real LLM providers (DEC-G).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import idis.api.routes.runs as runs_mod
import idis.deliverables.product_bundle as product_bundle_mod
from idis.api.routes.runs import _run_full_layer2_ic_challenge
from idis.models.run_step import FULL_STEPS, IMPLEMENTED_STEPS, StepName
from idis.services.runs.methodology_evidence_trust_court import (
    InMemoryRunMethodologyEvidenceTrustCourtService,
)
from idis.services.runs.methodology_layer2_readiness_package import (
    InMemoryRunMethodologyLayer2ReadinessPackageService,
)
from idis.services.runs.methodology_validated_evidence_package import (
    InMemoryRunMethodologyValidatedEvidencePackageService,
)

_PERSISTENCE_DIR = Path("src/idis/persistence")
_MIGRATIONS_DIR = _PERSISTENCE_DIR / "migrations" / "versions"


def _persistence_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_PERSISTENCE_DIR.rglob("*.py"))
    )


# --- 1. Reuse + DEC-B ordering: court/VEP implemented, positioned before graph/RAG/enrichment ---


def test_court_and_vep_steps_implemented_and_ordered_before_evidence_sources() -> None:
    assert StepName.METHODOLOGY_EVIDENCE_TRUST_COURT in IMPLEMENTED_STEPS
    assert StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE in IMPLEMENTED_STEPS
    court = FULL_STEPS.index(StepName.METHODOLOGY_EVIDENCE_TRUST_COURT)
    vep = FULL_STEPS.index(StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE)
    assert (court, vep) == (12, 13)
    # DEC-B: the court runs before the operational evidence sources; they join at Layer 2.
    assert vep < FULL_STEPS.index(StepName.GRAPH_EVIDENCE)
    assert vep < FULL_STEPS.index(StepName.RAG_EVIDENCE)
    assert vep < FULL_STEPS.index(StepName.ENRICHMENT)
    assert FULL_STEPS.index(StepName.LAYER2_IC_CHALLENGE) == 24


# --- 2. G1 closed (Tasks 2-3): durable repos exist and the step path persists ---


def test_durable_repos_exist_and_step_path_is_wired() -> None:
    # The judging services remain the InMemory implementations (unchanged logic)...
    assert InMemoryRunMethodologyEvidenceTrustCourtService.__name__.startswith("InMemory")
    assert InMemoryRunMethodologyValidatedEvidencePackageService.__name__.startswith("InMemory")
    # ...the durable persistence boundary exists (migration 0021 + twin repos)...
    sources = _persistence_sources()
    for token in (
        "validated_evidence_packages",
        "evidence_trust_findings",
        "muhasabah_records",
        "InMemoryLayer1EvidenceRepository",
    ):
        assert token in sources
    # ...and the court/VEP step path persists through it (Task 3 wiring): the
    # orchestrator carries the seams and steps.py binds the selector, while the
    # runs.py route layer stays repository-free.
    orchestrator_src = Path("src/idis/services/runs/orchestrator.py").read_text(encoding="utf-8")
    assert "layer1_evidence_repository" in orchestrator_src
    assert "METHODOLOGY_LAYER1_PERSISTENCE_FAILED" in orchestrator_src
    steps_src = Path("src/idis/services/runs/steps.py").read_text(encoding="utf-8")
    assert "get_layer1_evidence_repository" in steps_src
    assert "layer1_evidence" not in Path(runs_mod.__file__).read_text(encoding="utf-8")


# --- 3. G1/DEC-A closed (Task 2): migration 0021 adds the Layer-1 durability tables ---


def test_latest_migration_is_0021_layer1_durability() -> None:
    numbers = sorted(
        path.name[:4] for path in _MIGRATIONS_DIR.glob("0*.py") if path.name[:4].isdigit()
    )
    assert numbers[-1] == "0021"
    assert list(_MIGRATIONS_DIR.glob("0021_layer1_evidence_durability.py"))


# --- 4. G2 partly closed (Task 2): muhasabah persistence exists; debate_sessions API-only ---


def test_muhasabah_persistence_exists_and_debate_sessions_stays_api_only() -> None:
    assert "muhasabah_records" in _persistence_sources()
    # The durable debate_sessions table (migration 0009) is an API-route surface only:
    # the FULL pipeline path never writes it.
    runs_src = Path(runs_mod.__file__).read_text(encoding="utf-8")
    assert "debate_sessions" not in runs_src
    debate_route_src = Path("src/idis/api/routes/debate.py").read_text(encoding="utf-8")
    assert "debate_sessions" in debate_route_src


# --- 8. Task 7: readiness doc reconciled to the durable Layer-1 reality ---


def test_readiness_doc_reconciled_slice92_layer1_durability() -> None:
    doc = Path("docs/architecture/strict_full_live_readiness.md").read_text(encoding="utf-8")
    assert "post-Slice92" in doc
    # Durable tables + fail-closed persistence + the Layer-2 reference + export block.
    assert (
        "`validated_evidence_packages`, `evidence_trust_findings`, and `muhasabah_records`" in doc
    )
    assert "METHODOLOGY_LAYER1_PERSISTENCE_FAILED" in doc
    assert "vep_ref_ids" in doc
    assert "vep_evidence" in doc
    # The frozen Slice-53 census stays preserved, and prior banners stay intact.
    assert "post-Slice91" in doc
    assert "Indexes `document_span`" in doc


# --- 5. G4/DEC-D closed (Task 4): Layer 2 references the durable VEP output ---


def test_layer2_ic_challenge_accepts_vep_reference() -> None:
    params = set(inspect.signature(_run_full_layer2_ic_challenge).parameters)
    assert params == {
        "run_id",
        "tenant_id",
        "deal_id",
        "debate_summary",
        "created_claim_ids",
        "calc_ids",
        "graph_evidence",
        "rag_evidence",
        "enrichment_refs",
        "vep_package_ids",
    }


# --- 6. Reuse pin: step-16 readiness package already references the VEP in-memory ---


def test_layer2_readiness_package_consumes_vep_in_memory() -> None:
    params = set(
        inspect.signature(InMemoryRunMethodologyLayer2ReadinessPackageService.run).parameters
    )
    assert "validated_evidence_packages" in params


# --- 7. G5/DEC-E closed (Task 5): safe VEP surface in the product bundle ---


def test_product_bundle_has_safe_vep_package() -> None:
    bundle_src = Path(product_bundle_mod.__file__).read_text(encoding="utf-8")
    assert "_vep_package" in bundle_src
    assert "vep_evidence" in bundle_src
    # The bundle still never touches the court/claim internals directly.
    assert "trust_court" not in bundle_src
    assert "claim_text" not in bundle_src
