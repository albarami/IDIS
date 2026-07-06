"""Slice93 Task 9 — end-to-end Layer-2 IC challenge acceptance proof.

One acceptance path proves the master-plan contract: the Layer-2 IC challenge is
**distinct**, **durable**, **live-provider-proven**, and **visible in the IC memo / QA brief**,
plus the Task-6 category + scorecard-safe stage-emphasis behavior. Deferred scope (DEC-G/H)
stays honestly out: no advocate role, no dissent, no deep VEP consumption.

GREEN-on-arrival: this pins the shipped Tasks 2-6 behavior end-to-end (injected fakes only —
no real Anthropic, no database). Any RED here is a real acceptance gap.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any

from idis.analysis.scoring.models import Stage
from idis.analysis.scoring.stage_packs import get_stage_pack
from idis.api.routes.runs import _build_layer2_provenance
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
from idis.models.layer2_ic_challenge import Layer2ChallengeCategory
from idis.persistence.repositories.layer2_challenge import (
    InMemoryLayer2ChallengeRepository,
    clear_in_memory_layer2_challenge_store,
)
from idis.services.llm_model_health import LlmModelHealthCheck, LlmModelRole
from idis.services.runs.layer2_ic_challenge import (
    Layer2ICLLMRunner,
    RunLayer2ICChallengeService,
)
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
)
from tests.test_deliverables_generator import (
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice65_layer2_ic_challenge import _layer2_response

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "22222222-2222-2222-2222-222222222222"
_TIMESTAMP = "2026-01-01T00:00:00Z"

_CONFIGURED_ENV = {
    "IDIS_DEBATE_BACKEND": "anthropic",
    "ANTHROPIC_API_KEY": "configured-not-a-real-key",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-fake",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-fake",
}


class _FakeClient:
    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, json_mode: bool = False) -> str:
        return self._response


def _run_live_layer2() -> tuple[dict[str, Any], Any, Layer2ICLLMRunner, Layer2ICLLMRunner]:
    """Run the strict Layer-2 service with two real runners over a fake client."""
    response = _layer2_response(
        supported_claim_ids=["claim-a"],
        supported_calc_ids=["calc-a"],
        extra_content={"category": "market_risk"},
    )
    challenger = Layer2ICLLMRunner(
        role="ic_challenger", llm_client=_FakeClient(response), system_prompt="SECRET_PROMPT"
    )
    arbiter = Layer2ICLLMRunner(
        role="ic_arbiter", llm_client=_FakeClient(response), system_prompt="SECRET_PROMPT"
    )
    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=_CONFIGURED_ENV,
        challenger_runner=challenger,
        arbiter_runner=arbiter,
    )
    summary, record = service.run_with_record(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
        created_claim_ids=["claim-a"],
        calc_ids=["calc-a"],
        graph_evidence={"graph_retrieval": {"retrieval_ids": ["graph-ref-1"]}},
        rag_evidence={"rag_retrieval": {"match_ids": ["rag-ref-1"]}},
        enrichment_refs={"enrich-ref-1": {"provider_id": "companies_house"}},
    )
    return summary, record, challenger, arbiter


def _layer2_evidence_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Build the deliverables layer2_evidence block the way the orchestrator does."""
    return {
        "status": summary["status"],
        "layer2_challenge_ids": summary["layer2_challenge_ids"],
        "source_debate_ids": summary["source_debate_ids"],
        "claim_ids": summary["claim_ids"],
        "calc_ids": summary["calc_ids"],
        "finding_ids": summary["finding_ids"],
        "finding_count": summary["finding_count"],
        "unresolved_question_count": summary["unresolved_question_count"],
        "by_finding_type": summary["by_finding_type"],
        "by_severity": summary["by_severity"],
        "muhasabah_passed": summary["muhasabah_passed"],
    }


# --- The one acceptance path: distinct + durable + proven + visible ---


def test_acceptance_layer2_distinct_durable_proven_visible() -> None:
    clear_in_memory_layer2_challenge_store()
    summary, record, challenger, arbiter = _run_live_layer2()

    # (1) DISTINCT — a real second challenge layer (challenger + arbiter roles) that emits its
    # own categorized findings, not readiness metadata.
    assert summary["status"] == "completed"
    assert record.findings, "Layer-2 must emit at least one distinct finding"
    assert record.findings[0].category is Layer2ChallengeCategory.MARKET_RISK
    assert challenger.executed and arbiter.executed

    # (2) LIVE-PROVIDER-PROVEN — the provenance artifact proves both live calls executed,
    # carrying safe metadata only (ids/counts/booleans — no prompt body / model output).
    provenance = _build_layer2_provenance(
        strict_full_live=True,
        backend="anthropic",
        challenger_model="claude-sonnet-fake",
        arbiter_model="claude-opus-fake",
        challenger_runner=challenger,
        arbiter_runner=arbiter,
    )
    assert provenance["live_calls_executed"] is True
    assert "SECRET_PROMPT" not in json.dumps(provenance)

    # (3) DURABLE — the safe challenge + finding rows persist and roundtrip, carrying the
    # category taxonomy + scorecard-safe stage emphasis.
    repo = InMemoryLayer2ChallengeRepository(TENANT_ID)
    repo.upsert_challenge(Layer2ChallengeRow.from_record(record))
    for finding in record.findings:
        repo.upsert_finding(
            Layer2FindingRow.from_finding(
                finding,
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                challenge_id=record.layer2_challenge_id,
            )
        )
    challenges = repo.list_challenges(run_id=RUN_ID)
    findings = repo.list_findings(run_id=RUN_ID)
    assert [c["challenge_id"] for c in challenges] == [record.layer2_challenge_id]
    assert challenges[0]["safe_summary"]["by_category"] == {"market_risk": 1}
    assert challenges[0]["safe_summary"]["stage_emphasis"]["stage"] == "SEED"
    assert findings[0]["category"] == "market_risk"

    # (4) VISIBLE — the safe Layer-2 block surfaces in BOTH the IC memo and QA brief, with no
    # private content leaking.
    bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-accept",
        layer2_evidence=_layer2_evidence_from_summary(summary),
    )
    for deliverable in (bundle.ic_memo, bundle.qa_brief):
        vis = deliverable.layer2_challenge
        assert vis is not None
        assert vis.status == "completed"
        assert vis.challenge_ids == [record.layer2_challenge_id]
        assert vis.finding_ids == summary["finding_ids"]
        assert vis.finding_count == 1
        dumped = json.dumps(deliverable.model_dump(mode="json"))
        assert "SECRET_PROMPT" not in dumped
        assert "challenger_transcript" not in dumped


# --- Category + stage-emphasis behavior (Task 6) ---


def test_acceptance_category_and_stage_emphasis_behavior() -> None:
    summary, _record, _challenger, _arbiter = _run_live_layer2()

    assert summary["by_category"] == {"market_risk": 1}
    stage_emphasis = summary["stage_emphasis"]
    assert stage_emphasis["stage"] == "SEED"
    # The emphasis reuses the scoring stage-pack weights read-only; market_risk maps to the
    # MARKET_ATTRACTIVENESS dimension.
    from idis.analysis.scoring.models import ScoreDimension

    seed = get_stage_pack(Stage.SEED)
    assert stage_emphasis["weighted_by_category"]["market_risk"] == round(
        1 * seed.weights[ScoreDimension.MARKET_ATTRACTIVENESS], 6
    )
    # Scorecard is never touched by the emphasis (no scorecard fields ride along).
    for forbidden in ("composite_score", "score_band", "routing", "dimension_scores"):
        assert forbidden not in json.dumps(stage_emphasis)


# --- Live-provider proof gate: strict clears ONLY when both models are runtime-proven ---


def test_acceptance_strict_clears_only_when_both_models_proven() -> None:
    def _proven(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
        return LlmModelHealthCheck.healthy(
            role,
            backend="anthropic",
            provider="anthropic",
            models={"model": "m"},
            runtime_call_proven=True,
            provider_request_id="msg_safe",
        )

    def _only_challenger_proven(env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
        if role is LlmModelRole.DEBATE:
            return _proven(env, role)
        return LlmModelHealthCheck.healthy(
            role, backend="anthropic", provider="anthropic", models={"model": "m"}
        )

    def _component(checker: Any) -> Any:
        report = build_strict_full_live_readiness_report(
            env=_CONFIGURED_ENV,
            load_byol_env_credentials=False,
            binary_resolver=lambda _name: None,
            model_health_checker=checker,
        )
        return report.component("debate_layer_2_ic_challenge")

    # Both challenger and arbiter proven -> clears live.
    assert _component(_proven).status is StrictComponentStatus.LIVE_WIRED_AND_USED
    assert _component(_proven).may_proceed is True
    # Only the challenger proven -> still blocked.
    assert (
        _component(_only_challenger_proven).status
        is StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    )
    assert _component(_only_challenger_proven).may_proceed is False


# --- Deferred scope stays honest (DEC-G/H): no advocate, no dissent, no VEP consumption ---


def test_acceptance_t7_scope_stays_deferred() -> None:
    from pathlib import Path

    # No advocate role: challenger -> arbiter only.
    service_src = Path("src/idis/services/runs/layer2_ic_challenge.py").read_text(encoding="utf-8")
    assert "ic_challenger" in service_src
    assert "ic_arbiter" in service_src
    assert "ic_advocate" not in service_src

    # VEP recorded-not-consumed: the service run signature takes no vep parameter.
    run_params = set(inspect.signature(RunLayer2ICChallengeService.run).parameters)
    assert not any("vep" in param for param in run_params)

    # No Layer-2 dissent is fed into the deliverables: the memo dissent section is Layer-1 only,
    # so an absent Layer-2 challenge leaves the safe visibility field unset (None).
    bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-accept-none",
        layer2_evidence=None,
    )
    assert bundle.ic_memo.layer2_challenge is None
    assert bundle.qa_brief.layer2_challenge is None
