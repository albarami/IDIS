"""Slice65 tests for the real Layer 2 IC challenge step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from idis.models.run_step import FULL_STEPS, IMPLEMENTED_STEPS, STEP_ORDER, StepName

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
LIVE_ENV = {
    "IDIS_DEBATE_BACKEND": "anthropic",
    "ANTHROPIC_API_KEY": "fake-key",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-fake",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-fake",
}


class RecordingLayer2Runner:
    """Test runner that records strict live calls and returns JSON text."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def run(self, payload: dict[str, Any]) -> str:
        self.calls.append(payload)
        return self.response


def _layer2_response(
    *,
    supported_claim_ids: list[str] | None = None,
    supported_calc_ids: list[str] | None = None,
    muhasabah_claim_ids: list[str] | None = None,
    muhasabah_calc_ids: list[str] | None = None,
    extra_content: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "content": {
                "findings": [
                    {
                        "finding_type": "unresolved_risk",
                        "severity": "high",
                        "supported_claim_ids": supported_claim_ids or ["claim-a"],
                        "supported_calc_ids": supported_calc_ids or ["calc-a"],
                        "graph_ref_ids": ["graph-ref-1"],
                        "rag_ref_ids": ["rag-ref-1"],
                        "enrichment_ref_ids": ["enrich-ref-1"],
                        **(extra_content or {}),
                    }
                ],
                "unresolved_questions": ["Need IC review of cited evidence refs."],
            },
            "muhasabah": {
                "supported_claim_ids": muhasabah_claim_ids
                if muhasabah_claim_ids is not None
                else supported_claim_ids or ["claim-a"],
                "supported_calc_ids": muhasabah_calc_ids
                if muhasabah_calc_ids is not None
                else supported_calc_ids or ["calc-a"],
                "confidence": 0.82,
                "uncertainties": ["Limited to supplied refs."],
            },
        },
        sort_keys=True,
    )


def test_full_steps_include_layer2_after_debate_before_analysis() -> None:
    """Layer 2 must be a first-class FULL step, not hidden inside debate."""
    assert len(FULL_STEPS) == 28
    assert StepName.LAYER2_IC_CHALLENGE in FULL_STEPS
    assert StepName.LAYER2_IC_CHALLENGE in IMPLEMENTED_STEPS
    assert STEP_ORDER[StepName.DEBATE] < STEP_ORDER[StepName.LAYER2_IC_CHALLENGE]
    assert STEP_ORDER[StepName.LAYER2_IC_CHALLENGE] < STEP_ORDER[StepName.ANALYSIS]


def test_layer2_record_serializes_safe_ids_without_private_content() -> None:
    """Layer 2 summaries expose IDs/counts only, never paths, text, prompts, or vectors."""
    from idis.models.layer2_ic_challenge import (
        Layer2ICChallengeFinding,
        Layer2ICChallengeRecord,
        Layer2ICChallengeStatus,
        deterministic_layer2_ic_challenge_id,
    )

    challenge_id = deterministic_layer2_ic_challenge_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        debate_id="debate-001",
        claim_ids=["claim-b", "claim-a"],
        calc_ids=["calc-a"],
    )
    record = Layer2ICChallengeRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        layer2_challenge_id=challenge_id,
        source_debate_id="debate-001",
        status=Layer2ICChallengeStatus.COMPLETED,
        claim_ids=["claim-b", "claim-a"],
        calc_ids=["calc-a"],
        graph_ref_ids=["graph-ref-1"],
        rag_ref_ids=["rag-ref-1"],
        enrichment_ref_ids=["enrich-ref-1"],
        findings=[
            Layer2ICChallengeFinding(
                finding_id="finding-001",
                finding_type="unresolved_risk",
                severity="high",
                supported_claim_ids=["claim-a"],
                supported_calc_ids=["calc-a"],
                graph_ref_ids=["graph-ref-1"],
                rag_ref_ids=["rag-ref-1"],
                enrichment_ref_ids=["enrich-ref-1"],
            )
        ],
        unresolved_question_count=1,
        muhasabah_passed=True,
    )

    summary = record.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["layer2_challenge_ids"] == [challenge_id]
    assert summary["claim_ids"] == ["claim-a", "claim-b"]
    assert summary["finding_count"] == 1
    assert "C:\\Projects\\IDIS\\real_example\\secret.pdf" not in serialized
    assert ".local_reports" not in serialized
    assert "raw_text" not in serialized
    assert "prompt" not in serialized.lower()
    assert "transcript" not in serialized.lower()
    assert "embedding" not in serialized.lower()
    assert "object_key" not in serialized


def test_layer2_service_fails_closed_without_layer1_debate() -> None:
    """Layer 2 must not manufacture a challenge without a completed Layer 1 debate."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService()

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER1_DEBATE_MISSING"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            debate_summary={},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            graph_evidence={"graph_retrieval": {"retrieval_ids": ["graph-ref-1"]}},
            rag_evidence={"rag_retrieval": {"match_ids": ["rag-ref-1"]}},
            enrichment_refs={"enrich-ref-1": {"provider_id": "companies_house"}},
        )


def test_layer2_service_fails_closed_without_claim_or_calc_refs() -> None:
    """Layer 2 challenge findings must stay No-Free-Facts bound."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService()

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_NO_REFERENCED_EVIDENCE"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=[],
            calc_ids=[],
            graph_evidence={},
            rag_evidence={},
            enrichment_refs={},
        )


def test_layer2_service_strict_mode_requires_live_model_config() -> None:
    """Strict mode cannot pass through a deterministic or unconfigured Layer 2 path."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(strict_full_live=True, env={})

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_MISSING_LIVE_MODEL_CONFIG"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            graph_evidence={},
            rag_evidence={},
            enrichment_refs={},
        )


def test_layer2_service_strict_mode_calls_injected_challenger_and_arbiter() -> None:
    """Strict Layer 2 must be backed by live injected runners, not a local record."""
    from idis.services.runs.layer2_ic_challenge import RunLayer2ICChallengeService

    challenger = RecordingLayer2Runner(_layer2_response())
    arbiter = RecordingLayer2Runner(_layer2_response())
    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=challenger,
        arbiter_runner=arbiter,
    )

    summary = service.run(
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

    assert challenger.calls
    assert arbiter.calls
    assert challenger.calls[0]["role"] == "ic_challenger"
    assert arbiter.calls[0]["role"] == "ic_arbiter"
    assert summary["status"] == "completed"
    assert summary["finding_count"] == 1


def test_layer2_service_strict_mode_rejects_fake_env_without_live_runners() -> None:
    """Present env labels alone cannot clear strict Layer 2."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(strict_full_live=True, env=LIVE_ENV)

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_LIVE_RUNNER_MISSING"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            graph_evidence={},
            rag_evidence={},
            enrichment_refs={},
        )


def test_layer2_service_strict_mode_rejects_unsupported_refs() -> None:
    """Prompt outputs cannot introduce unsupported claim or calc refs."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=RecordingLayer2Runner(_layer2_response(supported_claim_ids=["claim-x"])),
        arbiter_runner=RecordingLayer2Runner(_layer2_response(supported_claim_ids=["claim-x"])),
    )

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_UNSUPPORTED_REFS"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            graph_evidence={},
            rag_evidence={},
            enrichment_refs={},
        )


def test_layer2_service_strict_mode_rejects_challenger_unsupported_finding_refs() -> None:
    """The challenger cannot smuggle unsupported finding refs past a valid arbiter."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=RecordingLayer2Runner(_layer2_response(supported_claim_ids=["claim-x"])),
        arbiter_runner=RecordingLayer2Runner(_layer2_response(supported_claim_ids=["claim-a"])),
    )

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_UNSUPPORTED_REFS"):
        service.run(
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


def test_layer2_service_strict_mode_rejects_challenger_unsupported_muhasabah_refs() -> None:
    """The challenger muhasabah refs must be inside the supplied claim/calc registry."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=RecordingLayer2Runner(
            _layer2_response(muhasabah_claim_ids=["claim-x"], muhasabah_calc_ids=["calc-x"])
        ),
        arbiter_runner=RecordingLayer2Runner(_layer2_response()),
    )

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_UNSUPPORTED_REFS"):
        service.run(
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


def test_layer2_service_strict_mode_rejects_arbiter_unsupported_muhasabah_refs() -> None:
    """The arbiter muhasabah refs must also be checked, not only its findings."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=RecordingLayer2Runner(_layer2_response()),
        arbiter_runner=RecordingLayer2Runner(_layer2_response(muhasabah_claim_ids=["claim-x"])),
    )

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_UNSUPPORTED_REFS"):
        service.run(
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


def test_layer2_service_strict_mode_accepts_valid_challenger_and_arbiter_refs() -> None:
    """Valid finding and muhasabah refs from both roles still complete."""
    from idis.services.runs.layer2_ic_challenge import RunLayer2ICChallengeService

    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=RecordingLayer2Runner(_layer2_response()),
        arbiter_runner=RecordingLayer2Runner(_layer2_response()),
    )

    summary = service.run(
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

    assert summary["status"] == "completed"
    assert summary["finding_count"] == 1


def test_layer2_service_strict_mode_rejects_raw_text_path_and_object_key_leakage() -> None:
    """Layer 2 prompt outputs fail closed on private data or storage-key leakage."""
    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
    )

    service = RunLayer2ICChallengeService(
        strict_full_live=True,
        env=LIVE_ENV,
        challenger_runner=RecordingLayer2Runner(
            _layer2_response(
                extra_content={
                    "raw_text": "PRIVATE",
                    "local_path": "C:\\Projects\\IDIS\\real_example\\secret.pdf",
                    "object_key": "runs/private/secret.json",
                }
            )
        ),
        arbiter_runner=RecordingLayer2Runner(_layer2_response()),
    )

    with pytest.raises(Layer2ICChallengeBlockedError, match="LAYER2_PRIVATE_DATA_LEAK"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            graph_evidence={},
            rag_evidence={},
            enrichment_refs={},
        )


def test_layer2_service_returns_safe_completed_summary() -> None:
    """A valid non-strict synthetic run yields an output-visible safe summary."""
    from idis.services.runs.layer2_ic_challenge import RunLayer2ICChallengeService

    service = RunLayer2ICChallengeService()

    summary = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
        created_claim_ids=["claim-a", "claim-b"],
        calc_ids=["calc-a"],
        graph_evidence={"graph_retrieval": {"retrieval_ids": ["graph-ref-1"]}},
        rag_evidence={"rag_retrieval": {"match_ids": ["rag-ref-1"]}},
        enrichment_refs={"enrich-ref-1": {"provider_id": "companies_house"}},
    )

    assert summary["status"] == "completed"
    assert summary["source_debate_ids"] == ["debate-001"]
    assert summary["claim_ids"] == ["claim-a", "claim-b"]
    assert summary["calc_ids"] == ["calc-a"]
    assert summary["muhasabah_passed"] is True


def test_layer2_prompt_contracts_exist_and_require_safe_json_refs() -> None:
    """IC challenger and arbiter prompts must require ref-bound JSON outputs."""
    repo_root = Path(__file__).resolve().parents[1]
    prompt_paths = [
        repo_root / "prompts" / "layer2_ic_challenger" / "1.0.0" / "prompt.md",
        repo_root / "prompts" / "layer2_ic_arbiter" / "1.0.0" / "prompt.md",
    ]
    required_tokens = [
        "```json",
        "supported_claim_ids",
        "supported_calc_ids",
        "muhasabah",
        "unresolved_questions",
        "No-Free-Facts",
        "raw private text",
        "prompt transcripts",
        "vectors",
    ]

    for prompt_path in prompt_paths:
        assert prompt_path.exists(), f"Missing prompt: {prompt_path}"
        text = prompt_path.read_text(encoding="utf-8")
        for token in required_tokens:
            assert token in text


def test_strict_readiness_replaces_layer2_not_implemented_with_config_blocker() -> None:
    """Layer 2 readiness should become a real code-path/config check."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    report = build_strict_full_live_readiness_report(env={})
    component = report.component("debate_layer_2_ic_challenge")
    inventory = next(
        item for item in report.component_inventory if item.component_name == "Layer 2 IC challenge"
    )

    assert component.status != StrictComponentStatus.NOT_IMPLEMENTED
    assert component.status == StrictComponentStatus.MISSING_CREDENTIALS
    assert component.may_proceed is False
    assert inventory.exists_in_code is True
    assert inventory.full_wired is True
    assert inventory.output_visible is True
    assert inventory.health_check_status == "missing_config"


def test_strict_readiness_fake_env_does_not_mark_layer2_live_without_runner_construction() -> None:
    """Strict readiness requires a real live runner construction path, not labels only."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    report = build_strict_full_live_readiness_report(env=LIVE_ENV)
    component = report.component("debate_layer_2_ic_challenge")

    assert component.status != StrictComponentStatus.LIVE_WIRED_AND_USED
    assert component.status == StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert component.may_proceed is False


def test_product_bundle_exports_safe_layer2_visibility(tmp_path: Path) -> None:
    """Product bundle should include sanitized Layer 2 refs in JSON artifacts."""
    from idis.deliverables.product_bundle import ProductBundleExporter
    from idis.storage.filesystem_store import FilesystemObjectStore
    from tests.test_slice59_product_export_bundle import (
        _TIMESTAMP,
        RecordingDeliverablesRepository,
        _make_context,
        _make_deliverables_bundle,
        _make_scorecard,
    )

    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )
    layer2_evidence: dict[str, Any] = {
        "status": "completed",
        "layer2_challenge_ids": ["layer2-001"],
        "source_debate_ids": ["debate-001"],
        "claim_ids": ["claim-a"],
        "calc_ids": ["calc-a"],
        "finding_count": 1,
        "unresolved_question_count": 1,
        "raw_text": "SHOULD_NOT_APPEAR",
        "prompt_transcript": "SHOULD_NOT_APPEAR",
        "embedding_vector": [0.1, 0.2],
        "object_key": "runs/private/object.json",
        "local_path": "C:\\Projects\\IDIS\\real_example\\secret.pdf",
    }

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        layer2_evidence=layer2_evidence,
    )

    run_summary = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/run_summary.json",
        ).body.decode("utf-8")
    )
    evidence_index = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/evidence_index.json",
        ).body.decode("utf-8")
    )
    serialized = json.dumps({"run_summary": run_summary, "evidence_index": evidence_index})

    assert run_summary["layer2_status"] == "completed"
    assert run_summary["layer2_challenge_ids"] == ["layer2-001"]
    assert evidence_index["layer2_evidence"]["finding_count"] == 1
    assert "SHOULD_NOT_APPEAR" not in serialized
    assert "object_key" not in serialized
    assert "C:\\Projects\\IDIS" not in serialized
