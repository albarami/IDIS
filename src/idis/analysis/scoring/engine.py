"""Scoring engine — Phase 9.

Deterministic aggregator that:
1. Loads stage pack (fail-closed)
2. Runs LLM scorecard runner for raw dimension scores
3. Validates NFF (claim/calc/enrichment refs against context)
4. Validates Muḥāsabah via existing validator
5. Computes composite_score = 100 * sum(score_i * weight_i)
6. Derives score_band + routing from thresholds
7. Emits audit events (fail-closed on sink failure)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from idis.analysis.models import AnalysisBundle, AnalysisContext, EnrichmentRef
from idis.analysis.scoring.llm_scorecard_runner import LLMScorecardRunner
from idis.analysis.scoring.models import (
    DimensionScore,
    RoutingAction,
    ScoreBand,
    Scorecard,
    ScoreDimension,
    Stage,
)
from idis.analysis.scoring.stage_packs import StagePack, get_stage_pack
from idis.audit.sink import AuditSink, AuditSinkError
from idis.validators.muhasabah import validate_muhasabah
from idis.validators.schema_validator import ValidationError, ValidationResult

logger = logging.getLogger(__name__)


class ScoringEngineError(Exception):
    """Raised when the scoring engine encounters a fatal error."""


class _ScoringNFFValidator:
    """Validates NFF for dimension scores against analysis context.

    Every claim_id, calc_id, and enrichment ref must trace to
    known registries. Fail-closed on any ungrounded reference.
    """

    def validate(
        self,
        scores: dict[ScoreDimension, DimensionScore],
        ctx: AnalysisContext,
    ) -> ValidationResult:
        """Validate all references in dimension scores are grounded.

        Args:
            scores: Dimension scores to validate.
            ctx: Analysis context with known registries.

        Returns:
            ValidationResult — fails if any reference is ungrounded.
        """
        errors: list[ValidationError] = []

        for dim, ds in sorted(scores.items(), key=lambda x: x[0].value):
            prefix = f"$.dimension_scores.{dim.value}"

            errors.extend(
                self._validate_claim_ids(
                    ds.supported_claim_ids, ctx, f"{prefix}.supported_claim_ids"
                )
            )
            errors.extend(
                self._validate_calc_ids(ds.supported_calc_ids, ctx, f"{prefix}.supported_calc_ids")
            )
            errors.extend(
                self._validate_enrichment_refs(ds.enrichment_refs, ctx, f"{prefix}.enrichment_refs")
            )
            errors.extend(
                self._validate_claim_ids(
                    ds.muhasabah.supported_claim_ids,
                    ctx,
                    f"{prefix}.muhasabah.supported_claim_ids",
                )
            )
            errors.extend(
                self._validate_calc_ids(
                    ds.muhasabah.supported_calc_ids,
                    ctx,
                    f"{prefix}.muhasabah.supported_calc_ids",
                )
            )

        if errors:
            return ValidationResult.fail(errors)
        return ValidationResult.success()

    def _validate_claim_ids(
        self,
        claim_ids: list[str],
        ctx: AnalysisContext,
        path: str,
    ) -> list[ValidationError]:
        """Check all claim IDs exist in context."""
        errors: list[ValidationError] = []
        for i, cid in enumerate(claim_ids):
            if cid not in ctx.claim_ids:
                errors.append(
                    ValidationError(
                        code="NFF_UNKNOWN_CLAIM_ID",
                        message=f"Claim ID '{cid}' not found in claim registry",
                        path=f"{path}[{i}]",
                    )
                )
        return errors

    def _validate_calc_ids(
        self,
        calc_ids: list[str],
        ctx: AnalysisContext,
        path: str,
    ) -> list[ValidationError]:
        """Check all calc IDs exist in context."""
        errors: list[ValidationError] = []
        for i, cid in enumerate(calc_ids):
            if cid not in ctx.calc_ids:
                errors.append(
                    ValidationError(
                        code="NFF_UNKNOWN_CALC_ID",
                        message=f"Calc ID '{cid}' not found in calc registry",
                        path=f"{path}[{i}]",
                    )
                )
        return errors

    def _validate_enrichment_refs(
        self,
        refs: list[EnrichmentRef],
        ctx: AnalysisContext,
        path: str,
    ) -> list[ValidationError]:
        """Check all enrichment refs exist with complete provenance."""
        errors: list[ValidationError] = []
        for i, ref in enumerate(refs):
            ref_path = f"{path}[{i}]"
            if ref.ref_id not in ctx.enrichment_refs:
                errors.append(
                    ValidationError(
                        code="NFF_UNKNOWN_ENRICHMENT_REF",
                        message=f"Enrichment ref '{ref.ref_id}' not found in context",
                        path=ref_path,
                    )
                )
                continue
            if not ref.provider_id:
                errors.append(
                    ValidationError(
                        code="NFF_ENRICHMENT_MISSING_PROVIDER_ID",
                        message=f"Enrichment ref '{ref.ref_id}' missing required provider_id",
                        path=f"{ref_path}.provider_id",
                    )
                )
            if not ref.source_id:
                errors.append(
                    ValidationError(
                        code="NFF_ENRICHMENT_MISSING_SOURCE_ID",
                        message=f"Enrichment ref '{ref.ref_id}' missing required source_id",
                        path=f"{ref_path}.source_id",
                    )
                )
        return errors


def _compute_composite(
    scores: dict[ScoreDimension, DimensionScore],
    pack: StagePack,
) -> float:
    """Compute stage-weighted composite score.

    composite_score = 100 * sum(score_i * weight_i)

    Args:
        scores: Validated dimension scores.
        pack: Stage pack with weights.

    Returns:
        Composite score in range [0, 100].
    """
    total = 0.0
    for dim, weight in pack.weights.items():
        total += scores[dim].score * weight
    return 100.0 * total


def _resolve_band(composite: float, pack: StagePack) -> ScoreBand:
    """Resolve score band from composite score using thresholds.

    Args:
        composite: Composite score 0-100.
        pack: Stage pack with band thresholds.

    Returns:
        ScoreBand (HIGH, MEDIUM, or LOW).
    """
    high_threshold = pack.band_thresholds["HIGH"]
    medium_threshold = pack.band_thresholds["MEDIUM"]
    if composite >= high_threshold:
        return ScoreBand.HIGH
    if composite >= medium_threshold:
        return ScoreBand.MEDIUM
    return ScoreBand.LOW


def _resolve_routing(band: ScoreBand, pack: StagePack) -> RoutingAction:
    """Resolve routing action from score band.

    Args:
        band: Score band.
        pack: Stage pack with routing map.

    Returns:
        RoutingAction (INVEST, HOLD, or DECLINE).
    """
    return pack.routing_by_band[band]


class ScoringEngine:
    """Deterministic scoring engine for VC Investment Scorecard.

    Orchestrates LLM scoring, NFF + Muḥāsabah validation,
    composite score computation, and routing determination.
    Emits audit events (fail-closed on sink failure).
    """

    def __init__(
        self,
        *,
        runner: LLMScorecardRunner,
        audit_sink: AuditSink,
    ) -> None:
        """Initialize the scoring engine.

        Args:
            runner: LLM scorecard runner for raw dimension scores.
            audit_sink: Audit event sink (fail-closed on failure).
        """
        self._runner = runner
        self._audit_sink = audit_sink
        self._nff_validator = _ScoringNFFValidator()

    def score(
        self,
        ctx: AnalysisContext,
        bundle: AnalysisBundle,
        stage: Stage,
    ) -> Scorecard:
        """Execute scoring pipeline and return a complete Scorecard.

        Args:
            ctx: Analysis context with registries.
            bundle: Specialist agent reports.
            stage: Deal stage for weight selection.

        Returns:
            Validated Scorecard with composite score, band, and routing.

        Raises:
            ScoringEngineError: On validation or computation failure.
            AuditSinkError: On audit sink failure (fatal).
        """
        self._emit_audit(
            "analysis.scoring.started",
            {
                "deal_id": ctx.deal_id,
                "tenant_id": ctx.tenant_id,
                "run_id": ctx.run_id,
                "stage": stage.value,
            },
        )

        try:
            pack = get_stage_pack(stage)

            dimension_scores = self._runner.run(ctx, bundle, stage)

            self._validate_nff(dimension_scores, ctx)
            self._validate_muhasabah(dimension_scores)

            composite = _compute_composite(dimension_scores, pack)
            band = _resolve_band(composite, pack)
            routing = _resolve_routing(band, pack)

            scorecard = Scorecard(
                stage=stage,
                dimension_scores=dimension_scores,
                composite_score=composite,
                score_band=band,
                routing=routing,
            )

            self._emit_audit(
                "analysis.scoring.completed",
                {
                    "deal_id": ctx.deal_id,
                    "tenant_id": ctx.tenant_id,
                    "run_id": ctx.run_id,
                    "stage": stage.value,
                    "composite_score": composite,
                    "score_band": band.value,
                    "routing": routing.value,
                },
            )

            return scorecard

        except AuditSinkError:
            raise
        except ScoringEngineError:
            self._emit_audit(
                "analysis.scoring.failed",
                {
                    "deal_id": ctx.deal_id,
                    "tenant_id": ctx.tenant_id,
                    "run_id": ctx.run_id,
                    "stage": stage.value,
                    "error_type": "validation_failure",
                },
            )
            raise
        except Exception as exc:
            self._emit_audit(
                "analysis.scoring.failed",
                {
                    "deal_id": ctx.deal_id,
                    "tenant_id": ctx.tenant_id,
                    "run_id": ctx.run_id,
                    "stage": stage.value,
                    "error": str(exc),
                },
            )
            raise ScoringEngineError(f"Scoring failed: {exc}") from exc

    def _validate_nff(
        self,
        scores: dict[ScoreDimension, DimensionScore],
        ctx: AnalysisContext,
    ) -> None:
        """Validate NFF for all dimension scores. Fail-closed.

        Args:
            scores: Dimension scores to validate.
            ctx: Analysis context with registries.

        Raises:
            ScoringEngineError: If NFF validation fails.
        """
        result = self._nff_validator.validate(scores, ctx)
        if not result.passed:
            error_details = [f"{e.code}: {e.message}" for e in result.errors]
            raise ScoringEngineError(
                f"No-Free-Facts validation failed for scoring: {error_details}"
            )

    def _validate_muhasabah(
        self,
        scores: dict[ScoreDimension, DimensionScore],
    ) -> None:
        """Validate Muḥāsabah for all dimension scores. Fail-closed.

        Args:
            scores: Dimension scores to validate.

        Raises:
            ScoringEngineError: If Muḥāsabah validation fails.
        """
        for dim, ds in sorted(scores.items(), key=lambda x: x[0].value):
            muhasabah_dict = ds.muhasabah.to_validator_dict()
            result = validate_muhasabah(muhasabah_dict)
            if not result.passed:
                error_details = [f"{e.code}: {e.message}" for e in result.errors]
                raise ScoringEngineError(
                    f"Muhasabah validation failed for dimension '{dim.value}': {error_details}"
                )

    def _emit_audit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an audit event. Fail-closed on sink failure.

        Args:
            event_type: Audit event type identifier.
            data: Event payload.

        Raises:
            AuditSinkError: If the audit sink fails.
        """
        event = {
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **data,
        }
        try:
            self._audit_sink.emit(event)
        except AuditSinkError:
            raise
        except Exception as exc:
            raise AuditSinkError(f"Audit sink failure for event '{event_type}': {exc}") from exc
