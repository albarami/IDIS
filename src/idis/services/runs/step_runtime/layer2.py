"""S9 Layer 2 IC challenge step moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from idis.providers.role_config import LlmRole, resolve_role_config
from idis.services.runs.step_runtime.common import (
    _STAGE_UNKNOWN_MESSAGE_LIMIT,
    _deal_metadata_stage_text,
)
from idis.services.runs.step_runtime.roles import (
    _build_layer2_provenance,
    _new_budgeted_llm_client,
)
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
)


def _run_full_layer2_ic_challenge(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    debate_summary: dict[str, Any],
    created_claim_ids: list[str],
    calc_ids: list[str],
    graph_evidence: dict[str, Any] | None = None,
    rag_evidence: dict[str, Any] | None = None,
    enrichment_refs: dict[str, Any] | None = None,
    deal_metadata: Mapping[str, Any] | None = None,
    vep_package_ids: list[str] | None = None,
    db_conn: Any = None,
    challenge_repository: Any = None,
    defer_persistence: bool = False,
) -> dict[str, Any]:
    """Run the distinct Layer 2 IC challenge for a FULL pipeline run.

    ``deal_metadata`` is the admitted-deal snapshot the analysis step copies onto
    the analysis context. An unmapped or blank stage blocks this step before any
    challenger or arbiter runner is built.

    ``vep_package_ids`` (Slice92) carries the durable Layer-1 VEP candidate ids
    persisted by the METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE step; they are surfaced
    as safe sorted ``vep_ref_ids`` in the result so the Layer-2 ledger entry
    references the durable Layer-1 output.

    After the service returns a completed record, the safe challenge + finding rows
    are persisted (Slice93) via ``challenge_repository`` when provided, else the
    Postgres/InMemory twin selector; a write failure fails the step closed with a
    static ``LAYER2_PERSISTENCE_FAILED`` reason.
    """
    from idis.analysis.scoring.models import resolve_stage
    from idis.services.runs.orchestrator import RunStepBlockedError

    raw_stage = _deal_metadata_stage_text(deal_metadata)
    resolved_stage = resolve_stage(raw_stage)
    if resolved_stage is None:
        visible = raw_stage if raw_stage is not None else ""
        raise RunStepBlockedError(
            "STAGE_UNKNOWN",
            f"layer2 stage did not map: {visible[:_STAGE_UNKNOWN_MESSAGE_LIMIT]}",
            result_summary={"outcome": "STAGE_UNKNOWN", "stage_text": visible},
        )

    from idis.services.runs.layer2_ic_challenge import (
        Layer2ICChallengeBlockedError,
        RunLayer2ICChallengeService,
        build_live_layer2_ic_runners,
    )

    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    strict_full_live = is_strict_full_live_required(dotenv_path=strict_dotenv_path)
    backend = str(os.environ.get("IDIS_DEBATE_BACKEND", "")).strip().lower()
    challenger_config = resolve_role_config(LlmRole.IC_CHALLENGER, backend=backend)
    arbiter_config = resolve_role_config(LlmRole.IC_ARBITER, backend=backend)
    challenger_runner = None
    arbiter_runner = None
    if strict_full_live:
        challenger_runner, arbiter_runner = build_live_layer2_ic_runners(
            challenger_client=_new_budgeted_llm_client(
                model=challenger_config.model,
                max_tokens=challenger_config.max_output_tokens,
                tenant_id=tenant_id,
            ),
            arbiter_client=_new_budgeted_llm_client(
                model=arbiter_config.model,
                max_tokens=arbiter_config.max_output_tokens,
                tenant_id=tenant_id,
            ),
        )
    service = RunLayer2ICChallengeService(
        strict_full_live=strict_full_live,
        env=os.environ,
        challenger_runner=challenger_runner,
        arbiter_runner=arbiter_runner,
    )
    result, record = service.run_with_record(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        debate_summary=debate_summary,
        created_claim_ids=created_claim_ids,
        calc_ids=calc_ids,
        graph_evidence=graph_evidence,
        rag_evidence=rag_evidence,
        enrichment_refs=enrichment_refs,
    )

    # LAYER2_IC_CHALLENGE runs before SCORING, so the scorecard does not exist yet.
    # resolved_stage was taken from deal_metadata above, the same snapshot the
    # analysis step copies onto the analysis context.
    if resolved_stage is not None:
        record = record.model_copy(update={"stage": resolved_stage})
        result["stage_emphasis"] = record.to_run_step_summary(stage=resolved_stage)[
            "stage_emphasis"
        ]

    # Persist the durable safe rows (Slice93). Deterministic ids -> idempotent upserts;
    # the InMemory twin keeps non-database runs green. Fail closed with a static,
    # ledger-safe reason (no raw exception text; cause chained for logs).
    if defer_persistence:
        result["_layer2_ic_challenge_record"] = record
        result["layer2_persistence"] = {
            "status": "staged",
            "challenge_ids": [record.layer2_challenge_id],
            "finding_row_count": len(record.findings),
        }
    else:
        from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
        from idis.persistence.repositories.layer2_challenge import (
            get_layer2_challenge_repository,
        )

        repository = challenge_repository or get_layer2_challenge_repository(db_conn, tenant_id)
        try:
            repository.upsert_challenge(
                Layer2ChallengeRow.from_record(record, stage=resolved_stage)
            )
            for finding in record.findings:
                repository.upsert_finding(
                    Layer2FindingRow.from_finding(
                        finding,
                        tenant_id=record.tenant_id,
                        deal_id=record.deal_id,
                        run_id=record.run_id,
                        challenge_id=record.layer2_challenge_id,
                    )
                )
        except Exception as exc:
            raise Layer2ICChallengeBlockedError("LAYER2_PERSISTENCE_FAILED") from exc
        result["layer2_persistence"] = {
            "status": "persisted",
            "challenge_ids": [record.layer2_challenge_id],
            "finding_row_count": len(record.findings),
        }

    # Safe durable Layer-1 reference: sorted, deduped, non-empty strings only.
    result["vep_ref_ids"] = sorted(
        {item for item in (vep_package_ids or []) if isinstance(item, str) and item}
    )

    # Safe live-provider provenance (Slice93 Task 5): model/prompt ids + a runtime-executed
    # signal proving the challenger + arbiter live calls ran. Non-strict runs report
    # live_calls_executed=False (no live runners were constructed).
    result["layer2_provenance"] = _build_layer2_provenance(
        strict_full_live=strict_full_live,
        backend=backend,
        challenger_model=challenger_config.model if strict_full_live else None,
        arbiter_model=arbiter_config.model if strict_full_live else None,
        challenger_runner=challenger_runner,
        arbiter_runner=arbiter_runner,
    )
    return result
