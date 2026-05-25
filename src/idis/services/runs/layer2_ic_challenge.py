"""Layer 2 IC challenge service for FULL runs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from idis.models.layer2_ic_challenge import (
    Layer2ICChallengeFinding,
    Layer2ICChallengeRecord,
    Layer2ICChallengeStatus,
    deterministic_layer2_ic_challenge_id,
)

WINDOWS_PATH_PATTERN = re.compile(r"(?i)(^|[^a-z0-9])[a-z]:[\\/]")


class Layer2ICChallengeBlockedError(RuntimeError):
    """Raised when Layer 2 cannot execute honestly."""


class Layer2ICRunner(Protocol):
    """Injected live runner for one Layer 2 role."""

    def run(self, payload: dict[str, Any]) -> str:
        """Run the role and return raw JSON text."""
        ...


class Layer2ICLLMRunner:
    """Live LLM-backed Layer 2 runner using the shared LLMClient style."""

    def __init__(self, *, role: str, llm_client: Any, system_prompt: str) -> None:
        """Initialize a live Layer 2 LLM runner."""
        self._role = role
        self._llm_client = llm_client
        self._system_prompt = system_prompt

    def run(self, payload: dict[str, Any]) -> str:
        """Call the configured LLM client with a safe JSON payload."""
        role_payload = {"role": self._role, **payload}
        prompt = (
            f"{self._system_prompt}\n\n"
            "LAYER2_SAFE_CONTEXT_JSON:\n"
            f"{json.dumps(role_payload, sort_keys=True, separators=(',', ':'))}"
        )
        return str(self._llm_client.call(prompt, json_mode=True))


class RunLayer2ICChallengeService:
    """Build a safe, reference-bound Layer 2 IC challenge summary."""

    def __init__(
        self,
        *,
        strict_full_live: bool = False,
        env: Mapping[str, str] | None = None,
        challenger_runner: Layer2ICRunner | None = None,
        arbiter_runner: Layer2ICRunner | None = None,
    ) -> None:
        """Initialize the Layer 2 IC challenge service."""
        self._strict_full_live = strict_full_live
        self._env = env or {}
        self._challenger_runner = challenger_runner
        self._arbiter_runner = arbiter_runner

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        debate_summary: dict[str, Any],
        created_claim_ids: list[str],
        calc_ids: list[str],
        graph_evidence: dict[str, Any] | None,
        rag_evidence: dict[str, Any] | None,
        enrichment_refs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Execute the minimal Layer 2 IC challenge contract."""
        debate_id = str(debate_summary.get("debate_id") or "").strip()
        if not debate_id or debate_summary.get("muhasabah_passed") is not True:
            raise Layer2ICChallengeBlockedError("LAYER1_DEBATE_MISSING")

        safe_claim_ids = _safe_ids(created_claim_ids)
        safe_calc_ids = _safe_ids(calc_ids)
        if not safe_claim_ids and not safe_calc_ids:
            raise Layer2ICChallengeBlockedError("LAYER2_NO_REFERENCED_EVIDENCE")

        if self._strict_full_live and _missing_layer2_model_env(self._env):
            raise Layer2ICChallengeBlockedError("LAYER2_MISSING_LIVE_MODEL_CONFIG")

        graph_ref_ids = _graph_ref_ids(graph_evidence)
        rag_ref_ids = _rag_ref_ids(rag_evidence)
        enrichment_ref_ids = sorted((enrichment_refs or {}).keys())
        if self._strict_full_live:
            return self._run_strict_live(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                debate_id=debate_id,
                claim_ids=safe_claim_ids,
                calc_ids=safe_calc_ids,
                graph_ref_ids=graph_ref_ids,
                rag_ref_ids=rag_ref_ids,
                enrichment_ref_ids=enrichment_ref_ids,
                debate_summary=debate_summary,
            )

        challenge_id = deterministic_layer2_ic_challenge_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            debate_id=debate_id,
            claim_ids=safe_claim_ids,
            calc_ids=safe_calc_ids,
        )
        finding = Layer2ICChallengeFinding(
            finding_id=f"layer2-finding-{challenge_id[:8]}",
            finding_type="ic_challenge",
            severity="medium",
            supported_claim_ids=safe_claim_ids[:1],
            supported_calc_ids=safe_calc_ids[:1],
            graph_ref_ids=graph_ref_ids[:5],
            rag_ref_ids=rag_ref_ids[:5],
            enrichment_ref_ids=enrichment_ref_ids[:5],
        )
        record = Layer2ICChallengeRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            layer2_challenge_id=challenge_id,
            source_debate_id=debate_id,
            status=Layer2ICChallengeStatus.COMPLETED,
            claim_ids=safe_claim_ids,
            calc_ids=safe_calc_ids,
            graph_ref_ids=graph_ref_ids,
            rag_ref_ids=rag_ref_ids,
            enrichment_ref_ids=enrichment_ref_ids,
            findings=[finding],
            unresolved_question_count=1,
            muhasabah_passed=True,
        )
        return record.to_run_step_summary()

    def _run_strict_live(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        debate_id: str,
        claim_ids: list[str],
        calc_ids: list[str],
        graph_ref_ids: list[str],
        rag_ref_ids: list[str],
        enrichment_ref_ids: list[str],
        debate_summary: dict[str, Any],
    ) -> dict[str, Any]:
        if self._challenger_runner is None or self._arbiter_runner is None:
            raise Layer2ICChallengeBlockedError("LAYER2_LIVE_RUNNER_MISSING")

        safe_payload = {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "source_debate_id": debate_id,
            "claim_ids": claim_ids,
            "calc_ids": calc_ids,
            "graph_ref_ids": graph_ref_ids,
            "rag_ref_ids": rag_ref_ids,
            "enrichment_ref_ids": enrichment_ref_ids,
            "debate_summary": _safe_debate_summary(debate_summary),
        }
        challenger_output = _parse_layer2_runner_output(
            self._challenger_runner.run({"role": "ic_challenger", **safe_payload})
        )
        _validate_runner_refs(
            challenger_output,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
            graph_ref_ids=graph_ref_ids,
            rag_ref_ids=rag_ref_ids,
            enrichment_ref_ids=enrichment_ref_ids,
        )
        arbiter_output = _parse_layer2_runner_output(
            self._arbiter_runner.run(
                {
                    "role": "ic_arbiter",
                    **safe_payload,
                    "challenger_findings": challenger_output["findings"],
                }
            )
        )
        findings = _validate_runner_refs(
            arbiter_output,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
            graph_ref_ids=graph_ref_ids,
            rag_ref_ids=rag_ref_ids,
            enrichment_ref_ids=enrichment_ref_ids,
        )
        challenge_id = deterministic_layer2_ic_challenge_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            debate_id=debate_id,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
        )
        record = Layer2ICChallengeRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            layer2_challenge_id=challenge_id,
            source_debate_id=debate_id,
            status=Layer2ICChallengeStatus.COMPLETED,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
            graph_ref_ids=graph_ref_ids,
            rag_ref_ids=rag_ref_ids,
            enrichment_ref_ids=enrichment_ref_ids,
            findings=findings,
            unresolved_question_count=max(
                len(challenger_output["unresolved_questions"]),
                len(arbiter_output["unresolved_questions"]),
            ),
            muhasabah_passed=True,
        )
        return record.to_run_step_summary()


def _missing_layer2_model_env(env: Mapping[str, str]) -> list[str]:
    required = []
    if env.get("IDIS_DEBATE_BACKEND") != "anthropic":
        required.append("IDIS_DEBATE_BACKEND=anthropic")
    for key in (
        "ANTHROPIC_API_KEY",
        "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
        "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
    ):
        if not str(env.get(key, "")).strip():
            required.append(key)
    return required


def build_live_layer2_ic_runners(
    *,
    challenger_client: Any,
    arbiter_client: Any,
    prompts: dict[str, str] | None = None,
) -> tuple[Layer2ICRunner, Layer2ICRunner]:
    """Construct the live Layer 2 challenger and arbiter runners."""
    resolved_prompts = prompts or load_layer2_prompts()
    return (
        Layer2ICLLMRunner(
            role="ic_challenger",
            llm_client=challenger_client,
            system_prompt=resolved_prompts["ic_challenger"],
        ),
        Layer2ICLLMRunner(
            role="ic_arbiter",
            llm_client=arbiter_client,
            system_prompt=resolved_prompts["ic_arbiter"],
        ),
    )


def load_layer2_prompts() -> dict[str, str]:
    """Load Layer 2 IC challenge prompts from disk."""
    root = _find_project_root()
    prompt_paths = {
        "ic_challenger": root / "prompts" / "layer2_ic_challenger" / "1.0.0" / "prompt.md",
        "ic_arbiter": root / "prompts" / "layer2_ic_arbiter" / "1.0.0" / "prompt.md",
    }
    prompts: dict[str, str] = {}
    for role, path in prompt_paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Layer 2 prompt file not found: {path}")
        prompts[role] = path.read_text(encoding="utf-8")
    return prompts


def _safe_ids(values: list[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _parse_layer2_runner_output(raw_response: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise Layer2ICChallengeBlockedError("LAYER2_INVALID_JSON") from exc
    _reject_private_leakage(parsed)
    if not isinstance(parsed, dict):
        raise Layer2ICChallengeBlockedError("LAYER2_INVALID_OUTPUT")

    content = parsed.get("content")
    if not isinstance(content, dict):
        raise Layer2ICChallengeBlockedError("LAYER2_INVALID_OUTPUT")
    findings = content.get("validated_findings") or content.get("findings")
    if not isinstance(findings, list) or not findings:
        raise Layer2ICChallengeBlockedError("LAYER2_NO_REFERENCED_EVIDENCE")

    muhasabah = parsed.get("muhasabah")
    if not isinstance(muhasabah, dict):
        raise Layer2ICChallengeBlockedError("LAYER2_INVALID_MUHASABAH")
    muhasabah_claim_ids = muhasabah.get("supported_claim_ids")
    muhasabah_calc_ids = muhasabah.get("supported_calc_ids")
    if not isinstance(muhasabah_claim_ids, list) or not isinstance(muhasabah_calc_ids, list):
        raise Layer2ICChallengeBlockedError("LAYER2_INVALID_MUHASABAH")
    safe_muhasabah_claim_ids = _safe_ids(muhasabah_claim_ids)
    safe_muhasabah_calc_ids = _safe_ids(muhasabah_calc_ids)
    if not safe_muhasabah_claim_ids and not safe_muhasabah_calc_ids:
        raise Layer2ICChallengeBlockedError("LAYER2_NO_REFERENCED_EVIDENCE")

    unresolved = content.get("unresolved_questions") or []
    if not isinstance(unresolved, list):
        raise Layer2ICChallengeBlockedError("LAYER2_INVALID_OUTPUT")
    return {
        "findings": findings,
        "muhasabah_claim_ids": safe_muhasabah_claim_ids,
        "muhasabah_calc_ids": safe_muhasabah_calc_ids,
        "unresolved_questions": unresolved,
    }


def _validate_runner_refs(
    output: dict[str, Any],
    *,
    claim_ids: list[str],
    calc_ids: list[str],
    graph_ref_ids: list[str],
    rag_ref_ids: list[str],
    enrichment_ref_ids: list[str],
) -> list[Layer2ICChallengeFinding]:
    allowed_claims = set(claim_ids)
    allowed_calcs = set(calc_ids)
    if not set(output["muhasabah_claim_ids"]).issubset(allowed_claims):
        raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
    if not set(output["muhasabah_calc_ids"]).issubset(allowed_calcs):
        raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
    return _validated_findings(
        output["findings"],
        claim_ids=claim_ids,
        calc_ids=calc_ids,
        graph_ref_ids=graph_ref_ids,
        rag_ref_ids=rag_ref_ids,
        enrichment_ref_ids=enrichment_ref_ids,
    )


def _validated_findings(
    findings: list[Any],
    *,
    claim_ids: list[str],
    calc_ids: list[str],
    graph_ref_ids: list[str],
    rag_ref_ids: list[str],
    enrichment_ref_ids: list[str],
) -> list[Layer2ICChallengeFinding]:
    validated: list[Layer2ICChallengeFinding] = []
    allowed_claims = set(claim_ids)
    allowed_calcs = set(calc_ids)
    allowed_graph = set(graph_ref_ids)
    allowed_rag = set(rag_ref_ids)
    allowed_enrichment = set(enrichment_ref_ids)
    for index, raw in enumerate(findings, start=1):
        if not isinstance(raw, dict):
            raise Layer2ICChallengeBlockedError("LAYER2_INVALID_OUTPUT")
        finding = Layer2ICChallengeFinding(
            finding_id=str(raw.get("finding_id") or f"layer2-finding-{index:03d}"),
            finding_type=str(raw.get("finding_type") or "ic_challenge"),
            severity=str(raw.get("severity") or "medium"),
            supported_claim_ids=_safe_ids(raw.get("supported_claim_ids") or []),
            supported_calc_ids=_safe_ids(raw.get("supported_calc_ids") or []),
            graph_ref_ids=_safe_ids(raw.get("graph_ref_ids") or []),
            rag_ref_ids=_safe_ids(raw.get("rag_ref_ids") or []),
            enrichment_ref_ids=_safe_ids(raw.get("enrichment_ref_ids") or []),
        )
        if not set(finding.supported_claim_ids).issubset(allowed_claims):
            raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
        if not set(finding.supported_calc_ids).issubset(allowed_calcs):
            raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
        if not set(finding.graph_ref_ids).issubset(allowed_graph):
            raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
        if not set(finding.rag_ref_ids).issubset(allowed_rag):
            raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
        if not set(finding.enrichment_ref_ids).issubset(allowed_enrichment):
            raise Layer2ICChallengeBlockedError("LAYER2_UNSUPPORTED_REFS")
        validated.append(finding)
    return validated


def _safe_debate_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "debate_id": str(value.get("debate_id") or ""),
        "stop_reason": value.get("stop_reason"),
        "round_number": value.get("round_number"),
        "muhasabah_passed": value.get("muhasabah_passed") is True,
        "agent_output_count": value.get("agent_output_count"),
    }


def _reject_private_leakage(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(
                part in normalized
                for part in (
                    "raw_text",
                    "local_path",
                    "object_key",
                    "prompt_transcript",
                    "embedding",
                    "vector",
                )
            ):
                raise Layer2ICChallengeBlockedError("LAYER2_PRIVATE_DATA_LEAK")
            _reject_private_leakage(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_private_leakage(item)
        return
    if isinstance(value, str) and (
        WINDOWS_PATH_PATTERN.search(value) is not None or ".local_reports" in value.lower()
    ):
        raise Layer2ICChallengeBlockedError("LAYER2_PRIVATE_DATA_LEAK")


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "prompts").exists() and (parent / "src").exists():
            return parent
    raise FileNotFoundError("Project root with prompts/ and src/ not found")


def _graph_ref_ids(graph_evidence: dict[str, Any] | None) -> list[str]:
    if not isinstance(graph_evidence, dict):
        return []
    retrieval = graph_evidence.get("graph_retrieval")
    if isinstance(retrieval, dict):
        raw = retrieval.get("retrieval_ids") or retrieval.get("graph_ref_ids") or []
        if isinstance(raw, list):
            return _safe_ids([str(item) for item in raw])
        query_summaries = retrieval.get("query_summaries")
        if isinstance(query_summaries, list):
            return _safe_ids(
                [
                    str(item.get("claim_id"))
                    for item in query_summaries
                    if isinstance(item, dict) and item.get("claim_id")
                ]
            )
    return []


def _rag_ref_ids(rag_evidence: dict[str, Any] | None) -> list[str]:
    if not isinstance(rag_evidence, dict):
        return []
    retrieval = rag_evidence.get("rag_retrieval")
    if isinstance(retrieval, dict):
        raw = retrieval.get("match_ids") or retrieval.get("rag_ref_ids") or []
        if isinstance(raw, list):
            return _safe_ids([str(item) for item in raw])
        matches = retrieval.get("matches")
        if isinstance(matches, list):
            return _safe_ids(
                [
                    str(item.get("source_id"))
                    for item in matches
                    if isinstance(item, dict) and item.get("source_id")
                ]
            )
    return []
