"""Analysis No-Free-Facts validator — Phase 8.A.

Deterministic validator enforcing grounding per TDD §10.3:
- All claim_ids must exist in the claim registry
- All calc_ids must exist in the calc registry
- All enrichment refs must exist with complete provenance (provider_id + source_id)

Fail-closed: any ungrounded reference causes rejection.
"""

from __future__ import annotations

from idis.analysis.models import AgentReport, AnalysisContext, EnrichmentRef
from idis.validators.schema_validator import ValidationError, ValidationResult


class AnalysisNoFreeFactsValidator:
    """Validates that all references in an AgentReport are grounded.

    Per TDD §10.3, every factual reference must trace to:
    - A valid claim_id in the claim registry
    - A valid calc_id from the calc engine
    - An enrichment ref with complete provenance (provider_id + source_id)
    """

    def validate(self, report: AgentReport, ctx: AnalysisContext) -> ValidationResult:
        """Validate all references in the report are grounded.

        Args:
            report: Agent report to validate.
            ctx: Analysis context with known registries.

        Returns:
            ValidationResult — fails if any reference is ungrounded.
        """
        errors: list[ValidationError] = []

        errors.extend(
            self._validate_claim_ids(report.supported_claim_ids, ctx, "$.supported_claim_ids")
        )
        errors.extend(
            self._validate_calc_ids(report.supported_calc_ids, ctx, "$.supported_calc_ids")
        )
        errors.extend(self._validate_enrichment_refs(report.enrichment_ref_ids, ctx))

        for i, risk in enumerate(report.risks):
            errors.extend(self._validate_claim_ids(risk.claim_ids, ctx, f"$.risks[{i}].claim_ids"))
            errors.extend(self._validate_calc_ids(risk.calc_ids, ctx, f"$.risks[{i}].calc_ids"))
            errors.extend(
                self._validate_enrichment_refs(
                    risk.enrichment_ref_ids,
                    ctx,
                    path_prefix=f"$.risks[{i}]",
                )
            )

        errors.extend(
            self._validate_claim_ids(
                report.muhasabah.supported_claim_ids,
                ctx,
                "$.muhasabah.supported_claim_ids",
            )
        )
        errors.extend(
            self._validate_calc_ids(
                report.muhasabah.supported_calc_ids,
                ctx,
                "$.muhasabah.supported_calc_ids",
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
        ref_ids: list[str],
        ctx: AnalysisContext,
        path_prefix: str = "$",
    ) -> list[ValidationError]:
        """Check all enrichment refs exist with complete provenance."""
        errors: list[ValidationError] = []
        for i, ref_id in enumerate(ref_ids):
            path = f"{path_prefix}.enrichment_ref_ids[{i}]"
            if ref_id not in ctx.enrichment_refs:
                errors.append(
                    ValidationError(
                        code="NFF_UNKNOWN_ENRICHMENT_REF",
                        message=f"Enrichment ref '{ref_id}' not found in context",
                        path=path,
                    )
                )
                continue
            ref = ctx.enrichment_refs[ref_id]
            errors.extend(self._validate_enrichment_provenance(ref, path))
        return errors

    def _validate_enrichment_provenance(
        self, ref: EnrichmentRef, path: str
    ) -> list[ValidationError]:
        """Validate enrichment provenance completeness (defense-in-depth)."""
        errors: list[ValidationError] = []
        if not ref.provider_id:
            errors.append(
                ValidationError(
                    code="NFF_ENRICHMENT_MISSING_PROVIDER_ID",
                    message=(
                        f"Enrichment ref '{ref.ref_id}' missing required provider_id in provenance"
                    ),
                    path=f"{path}.provider_id",
                )
            )
        if not ref.source_id:
            errors.append(
                ValidationError(
                    code="NFF_ENRICHMENT_MISSING_SOURCE_ID",
                    message=(
                        f"Enrichment ref '{ref.ref_id}' missing required source_id in provenance"
                    ),
                    path=f"{path}.source_id",
                )
            )
        return errors
