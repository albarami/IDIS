"""Slice 12 in-memory Layer 1 Validated Evidence Package service."""

from __future__ import annotations

from idis.models.evidence_trust_court_materialization import (
    RunScopedEvidenceTrustCourtRecord,
    RunScopedEvidenceTrustCourtShell,
)
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageReason,
    MethodologyValidatedEvidencePackageRejection,
    MethodologyValidatedEvidencePackageRunResult,
    MethodologyValidatedEvidencePackageStatus,
    RunScopedValidatedEvidencePackageRecord,
    RunScopedValidatedEvidencePackageSummary,
    counter,
    deterministic_validated_evidence_package_id,
)


class InMemoryRunMethodologyValidatedEvidencePackageService:
    """Build run-scoped Layer 1 VEP records from full Evidence Trust Court records."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        evidence_trust_courts: list[
            RunScopedEvidenceTrustCourtRecord | RunScopedEvidenceTrustCourtShell
        ],
    ) -> tuple[
        MethodologyValidatedEvidencePackageRunResult,
        list[RunScopedValidatedEvidencePackageRecord],
    ]:
        """Run VEP construction and return safe run-scoped records."""
        early_rejection = self._early_rejection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            evidence_trust_courts=evidence_trust_courts,
        )
        if early_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                rejection=early_rejection,
            ), []

        court = evidence_trust_courts[0]
        if not isinstance(court, RunScopedEvidenceTrustCourtRecord):
            rejection = _rejection(
                MethodologyValidatedEvidencePackageReason.EVIDENCE_TRUST_COURT_SHELL_ONLY,
                "Evidence Trust Court shell cannot support VEP construction",
                court_id=court.court_id,
            )
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                rejection=rejection,
            ), []

        reference_rejection = self._reference_rejection(court)
        if reference_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                rejection=reference_rejection,
            ), []

        package = self._build_package(court)
        result = MethodologyValidatedEvidencePackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=MethodologyValidatedEvidencePackageStatus.COMPLETED,
            package_shells=[package.to_shell()],
            rejections=[],
            summary=package.to_summary(),
        )
        return result, [package]

    def _early_rejection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        evidence_trust_courts: list[
            RunScopedEvidenceTrustCourtRecord | RunScopedEvidenceTrustCourtShell
        ],
    ) -> MethodologyValidatedEvidencePackageRejection | None:
        if not evidence_trust_courts:
            return _rejection(
                MethodologyValidatedEvidencePackageReason.MISSING_EVIDENCE_TRUST_COURT,
                "Validated Evidence Package requires a full Evidence Trust Court record",
            )
        if len(evidence_trust_courts) > 1:
            return _rejection(
                MethodologyValidatedEvidencePackageReason.DUPLICATE_COURT_INPUT,
                "Validated Evidence Package accepts one Evidence Trust Court input",
            )
        court = evidence_trust_courts[0]
        if _scope_mismatch(court, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id):
            return _rejection(
                MethodologyValidatedEvidencePackageReason.TENANT_OR_RUN_MISMATCH,
                "Validated Evidence Package input scope mismatch",
                court_id=court.court_id,
            )
        return None

    def _reference_rejection(
        self, court: RunScopedEvidenceTrustCourtRecord
    ) -> MethodologyValidatedEvidencePackageRejection | None:
        claim_ids = {assessment.claim_id for assessment in court.claim_assessments}
        evidence_ids = {
            evidence_id
            for assessment in court.claim_assessments
            for evidence_id in assessment.evidence_ids
        }
        sanad_ids = {assessment.sanad_id for assessment in court.claim_assessments}
        calc_ids = {
            calc_id for assessment in court.claim_assessments for calc_id in assessment.calc_ids
        }
        defect_ids = {
            defect_id
            for assessment in court.claim_assessments
            for defect_id in assessment.defect_ids
        }

        for finding in court.findings:
            if finding.claim_id not in claim_ids:
                return _missing_reference(court.court_id)
            if not set(finding.evidence_ids).issubset(evidence_ids):
                return _missing_reference(court.court_id)
            if finding.sanad_id is not None and finding.sanad_id not in sanad_ids:
                return _missing_reference(court.court_id)
            if not set(finding.calc_ids).issubset(calc_ids):
                return _missing_reference(court.court_id)
            if not set(finding.defect_ids).issubset(defect_ids):
                return _missing_reference(court.court_id)

        for role_summary in court.role_summaries:
            if not set(role_summary.supported_claim_ids).issubset(claim_ids):
                return _missing_reference(court.court_id)
        return None

    def _build_package(
        self, court: RunScopedEvidenceTrustCourtRecord
    ) -> RunScopedValidatedEvidencePackageRecord:
        claim_ids = [assessment.claim_id for assessment in court.claim_assessments]
        finding_ids = [finding.finding_id for finding in court.findings]
        package_id = deterministic_validated_evidence_package_id(
            tenant_id=court.tenant_id,
            deal_id=court.deal_id,
            run_id=court.run_id,
            court_id=court.court_id,
            claim_ids=claim_ids,
            finding_ids=finding_ids,
        )
        claim_ids_by_disposition: dict[str, list[str]] = {}
        for assessment in court.claim_assessments:
            claim_ids_by_disposition.setdefault(assessment.disposition.value, []).append(
                assessment.claim_id
            )

        reason_codes = [
            reason_code
            for assessment in court.claim_assessments
            for reason_code in assessment.reason_codes
        ]
        reason_codes.extend(
            reason_code for finding in court.findings for reason_code in finding.reason_codes
        )
        reason_codes.extend(
            reason_code
            for role_summary in court.role_summaries
            for reason_code in role_summary.reason_codes
        )

        return RunScopedValidatedEvidencePackageRecord(
            tenant_id=court.tenant_id,
            deal_id=court.deal_id,
            run_id=court.run_id,
            package_id=package_id,
            court_id=court.court_id,
            dashboard_id=court.dashboard_id,
            claim_ids_by_disposition=claim_ids_by_disposition,
            evidence_ids=[
                evidence_id
                for assessment in court.claim_assessments
                for evidence_id in assessment.evidence_ids
            ],
            source_span_ids=[
                source_span_id
                for assessment in court.claim_assessments
                for source_span_id in assessment.source_span_ids
            ],
            sanad_ids=[assessment.sanad_id for assessment in court.claim_assessments],
            defect_ids=[
                defect_id
                for assessment in court.claim_assessments
                for defect_id in assessment.defect_ids
            ],
            calc_ids=[
                calc_id for assessment in court.claim_assessments for calc_id in assessment.calc_ids
            ],
            finding_ids=finding_ids,
            finding_types=[finding.finding_type.value for finding in court.findings],
            role_names=[role_summary.role.value for role_summary in court.role_summaries],
            reason_codes=reason_codes,
            by_disposition=counter(
                assessment.disposition.value for assessment in court.claim_assessments
            ),
            by_grade=counter(
                assessment.sanad_grade.value for assessment in court.claim_assessments
            ),
            by_dashboard_verdict=counter(
                assessment.dashboard_verdict.value for assessment in court.claim_assessments
            ),
            by_finding_type=counter(finding.finding_type.value for finding in court.findings),
            by_reason=counter(reason_codes),
            status=MethodologyValidatedEvidencePackageStatus.COMPLETED,
        )

    def _failed_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        rejection: MethodologyValidatedEvidencePackageRejection,
    ) -> MethodologyValidatedEvidencePackageRunResult:
        return MethodologyValidatedEvidencePackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=MethodologyValidatedEvidencePackageStatus.FAILED,
            package_shells=[],
            rejections=[rejection],
            summary=RunScopedValidatedEvidencePackageSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                package_count=0,
                packaged_claim_count=0,
                finding_count=0,
                by_disposition={},
                by_grade={},
                by_dashboard_verdict={},
                by_finding_type={},
                by_reason={rejection.reason.value: 1},
            ),
        )


def _scope_mismatch(
    item: RunScopedEvidenceTrustCourtRecord | RunScopedEvidenceTrustCourtShell,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    return item.tenant_id != tenant_id or item.deal_id != deal_id or item.run_id != run_id


def _missing_reference(court_id: str) -> MethodologyValidatedEvidencePackageRejection:
    return _rejection(
        MethodologyValidatedEvidencePackageReason.MISSING_COURT_REFERENCE,
        "Validated Evidence Package court reference is missing from court record",
        court_id=court_id,
    )


def _rejection(
    reason: MethodologyValidatedEvidencePackageReason,
    message: str,
    *,
    court_id: str | None = None,
) -> MethodologyValidatedEvidencePackageRejection:
    return MethodologyValidatedEvidencePackageRejection(
        court_id=court_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
