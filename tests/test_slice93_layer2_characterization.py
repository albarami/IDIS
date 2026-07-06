"""Slice93 Task 1 — characterization pinning the CURRENT distinct-Layer-2-IC-challenge truth.

A live Layer-2 IC challenge already exists and is FULL-wired (Slice65): step 24 runs a
strict-live challenger→arbiter pass with NFF/Muḥāsabah validation, fail-closed codes,
private-leak rejection, and safe product-bundle visibility. "Not readiness metadata" is
therefore already substantially true. This pins the as-built reality Slice93 builds on,
under the locked decisions (DEC-A acceptance-first spine; DEC-B durability via migration
0022 + twin repos with an id-shape gate; DEC-C memo/QA visibility, safe fields only; DEC-D/E
minimal categories + scorecard-safe stage weighting; DEC-F Layer-2 provenance; DEC-G/H defer
deep-VEP-consumption, dissent, and the advocate role):

  1. (Reuse) The Layer-2 service is live-wired: challenger + arbiter roles, FULL step 24,
     FULL_ONLY + IMPLEMENTED. Per DEC-H the advocate role is DEFERRED (challenger→arbiter only).
  2. (G1 closed, Tasks 2-3 / DEC-B) Durable Layer-2 tables + twin repositories exist
     (migration 0022 + `layer2_challenge`/`layer2_durability`) AND the LAYER2 step path
     persists through them: the route fn (`_run_full_layer2_ic_challenge`) selects the
     repository and fails closed with `LAYER2_PERSISTENCE_FAILED`, and steps.py binds
     db_conn to it. Persistence is route-level (unlike Slice92's orchestrator-level VEP).
  3. (G3 closed, Task 5 / DEC-F) A Layer-2 provenance builder now exists (mirroring
     debate/analysis) and is surfaced in the result; the strict component clears when the
     debate model health is runtime-call-proven, and still blocks otherwise.
  4. (G2 closed, Task 4 / DEC-C) Safe Layer-2 challenge visibility (ids/counts/category +
     severity histograms only) IS surfaced in the IC memo and QA brief via a structured
     ``layer2_challenge`` field, whitelisted by the generator (None unless completed).
  5. (G5 closed, Task 6 / DEC-D) Findings carry a bounded ``Layer2ChallengeCategory`` enum
     (mapped 1:1 to the scorecard dimensions + a ``GENERAL`` catch-all); ``finding_type``/
     ``severity`` stay sanitized free strings. A ``by_category`` histogram + a scorecard-safe
     stage-weighted emphasis (DEC-E) surface in the safe summary.
  6. (G8, DEC-G deferred) The VEP reference is RECORDED, not consumed: ``service.run`` has no
     vep parameter (``vep_ref_ids`` are appended after the run). Stays true this slice.
  7. (G9 closed, Task 8) The strict readiness doc carries a post-Slice93 banner reconciling
     the durable / visible / provenance-proven / distinct Layer-2 reality, while the frozen
     Slice-53 census row and prior banners stay preserved verbatim.
  8. (DEC-B id-shape gate) ``challenge_id`` is bare UUID5 but ``finding_id`` is a prefixed/
     LLM-supplied string (``layer2-finding-…``) — the durability schema must not assume UUID.

GREEN-on-arrival expected (characterization pins current truth). Any RED → STOP + report.
No production changes. No database, no real Anthropic (DEC-I).
"""

from __future__ import annotations

import inspect
import uuid as uuid_mod
from pathlib import Path

from idis.models.layer2_ic_challenge import (
    Layer2ICChallengeFinding,
    deterministic_layer2_ic_challenge_id,
)
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, IMPLEMENTED_STEPS, StepName
from idis.services.runs.layer2_ic_challenge import RunLayer2ICChallengeService

_PERSISTENCE_DIR = Path("src/idis/persistence")
_MIGRATIONS_DIR = _PERSISTENCE_DIR / "migrations" / "versions"
_RUNS_ROUTE = Path("src/idis/api/routes/runs.py")
_STRICT = Path("src/idis/services/runs/strict_full_live.py")
_LAYER2_SERVICE = Path("src/idis/services/runs/layer2_ic_challenge.py")
_MEMO = Path("src/idis/deliverables/memo.py")
_QA_BRIEF = Path("src/idis/deliverables/qa_brief.py")
_READINESS = Path("docs/architecture/strict_full_live_readiness.md")


def _persistence_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_PERSISTENCE_DIR.rglob("*.py"))
    )


# --- 1. Reuse: live-wired challenger→arbiter at step 24; advocate DEFERRED (DEC-H) ---


def test_layer2_is_live_wired_challenger_arbiter_only_no_advocate() -> None:
    assert RunLayer2ICChallengeService.__name__ == "RunLayer2ICChallengeService"
    assert StepName.LAYER2_IC_CHALLENGE in IMPLEMENTED_STEPS
    assert StepName.LAYER2_IC_CHALLENGE in FULL_ONLY_STEPS
    assert FULL_STEPS.index(StepName.LAYER2_IC_CHALLENGE) == 24
    service_src = _LAYER2_SERVICE.read_text(encoding="utf-8")
    assert "ic_challenger" in service_src
    assert "ic_arbiter" in service_src
    # DEC-H: no advocate role this slice (deferred) — stays true.
    assert "ic_advocate" not in service_src


# --- 2. G1 closed (Tasks 2-3): durable tables + twin repos exist and the step persists ---


def test_durable_layer2_repos_exist_and_step_path_is_wired() -> None:
    sources = _persistence_sources()
    for token in (
        "layer2_ic_challenges",
        "layer2_ic_findings",
        "InMemoryLayer2ChallengeRepository",
    ):
        assert token in sources
    numbers = sorted(
        path.name[:4] for path in _MIGRATIONS_DIR.glob("0*.py") if path.name[:4].isdigit()
    )
    assert numbers[-1] == "0022"
    assert list(_MIGRATIONS_DIR.glob("0022_layer2_ic_challenge_durability.py"))
    # The LAYER2 step path persists through the repository (Task 3): the route fn selects
    # the repository and fails closed, and steps.py binds db_conn to it.
    runs_src = _RUNS_ROUTE.read_text(encoding="utf-8")
    assert "get_layer2_challenge_repository" in runs_src
    assert "LAYER2_PERSISTENCE_FAILED" in runs_src
    steps_src = Path("src/idis/services/runs/steps.py").read_text(encoding="utf-8")
    assert "partial(_run_full_layer2_ic_challenge, db_conn=db_conn)" in steps_src


# --- 3. G3 closed (Task 5): Layer-2 provenance exists; strict clears on proven execution ---


def test_layer2_provenance_exists_and_strict_clears_on_proven_execution() -> None:
    runs_src = _RUNS_ROUTE.read_text(encoding="utf-8")
    assert "_build_debate_provenance" in runs_src
    assert "_build_analysis_provenance" in runs_src
    # Task 5: a Layer-2 provenance builder now exists and is surfaced in the result.
    assert "_build_layer2_provenance" in runs_src
    assert "layer2_provenance" in runs_src
    strict_src = _STRICT.read_text(encoding="utf-8")
    # The component still blocks on runtime proof when configured-but-not-yet-proven...
    assert "runtime proof that challenger and arbiter model calls executed" in strict_src
    # ...but now clears to live when the debate model health is runtime-call-proven.
    assert "runtime_call_proven" in strict_src


# --- 4. G2 closed (Task 4): safe Layer-2 visibility IS surfaced in IC memo + QA brief ---


def test_layer2_visibility_surfaced_in_memo_and_qa_brief() -> None:
    # Both builders now set the safe Layer-2 challenge visibility (ids/counts/categories).
    for path in (_MEMO, _QA_BRIEF):
        src = path.read_text(encoding="utf-8")
        assert "Layer2ChallengeVisibility" in src
        assert "set_layer2_challenge" in src
    # The deliverable models carry the optional structured field...
    from idis.models.deliverables import ICMemo, Layer2ChallengeVisibility, QABrief

    assert "layer2_challenge" in ICMemo.model_fields
    assert "layer2_challenge" in QABrief.model_fields
    # ...the generator whitelists it (safe fields only, None unless completed)...
    generator_src = Path("src/idis/deliverables/generator.py").read_text(encoding="utf-8")
    assert "_safe_layer2_challenge_visibility" in generator_src
    # ...and the visibility model exposes only safe ids/counts/categories (no raw text).
    assert set(Layer2ChallengeVisibility.model_fields) == {
        "status",
        "challenge_ids",
        "finding_ids",
        "finding_count",
        "unresolved_question_count",
        "by_finding_type",
        "by_severity",
    }


# --- 5. G5 closed (Task 6): a challenge-category enum now exists on findings ---


def test_finding_type_severity_free_but_category_is_enum() -> None:
    from idis.models.layer2_ic_challenge import Layer2ChallengeCategory

    fields = Layer2ICChallengeFinding.model_fields
    # finding_type/severity stay sanitized free strings; the taxonomy lives in `category`.
    assert fields["finding_type"].annotation is str
    assert fields["severity"].annotation is str
    assert fields["category"].annotation is Layer2ChallengeCategory


# --- 6. G8 (DEC-G deferred): VEP is recorded, not consumed by the challenge ---


def test_vep_recorded_not_consumed_by_service() -> None:
    params = set(inspect.signature(RunLayer2ICChallengeService.run).parameters)
    assert params == {
        "self",
        "tenant_id",
        "deal_id",
        "run_id",
        "debate_summary",
        "created_claim_ids",
        "calc_ids",
        "graph_evidence",
        "rag_evidence",
        "enrichment_refs",
    }
    assert not any("vep" in param for param in params)


# --- 7. G9 closed (Task 8): readiness doc reconciled to the post-Slice93 Layer-2 reality ---


def test_readiness_doc_reconciled_slice93_layer2() -> None:
    doc = _READINESS.read_text(encoding="utf-8")
    # A post-Slice93 banner documents the durable / visible / provenance / distinct reality.
    assert "post-Slice93" in doc
    assert "layer2_ic_challenges" in doc
    assert "layer2_ic_findings" in doc
    assert "LAYER2_PERSISTENCE_FAILED" in doc
    assert "layer2_challenge" in doc  # safe IC-memo / QA-brief visibility block
    assert "runtime_call_proven" in doc  # both challenger + arbiter live-call proof
    assert "Layer2ChallengeCategory" in doc  # bounded challenge categories
    assert "stage-weighted" in doc  # scorecard-safe stage emphasis
    # Deferred scope is honestly recorded.
    assert "advocate" in doc
    # The frozen Slice-53 census row + prior banners stay preserved verbatim.
    assert "Debate layer 2 / IC challenge | `not-implemented`" in doc
    assert "post-Slice92" in doc
    assert "post-Slice91" in doc
    assert "Indexes `document_span`" in doc


# --- 8. DEC-B id-shape gate: challenge_id is UUID5, finding_id is a prefixed string ---


def test_id_shapes_challenge_uuid_finding_prefixed() -> None:
    challenge_id = deterministic_layer2_ic_challenge_id(
        tenant_id="t",
        deal_id="d",
        run_id="r",
        debate_id="deb",
        claim_ids=["c1"],
        calc_ids=["calc1"],
    )
    # challenge_id is a bare UUID5 -> UUID column is safe.
    assert str(uuid_mod.UUID(challenge_id)) == challenge_id
    # finding_id is a prefixed / LLM-supplied string, NOT a bare UUID -> the durability
    # schema (Task 2) must store it as text and not assume a UUID.
    service_src = _LAYER2_SERVICE.read_text(encoding="utf-8")
    assert "layer2-finding-" in service_src
