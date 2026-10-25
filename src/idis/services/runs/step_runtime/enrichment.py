"""S5 enrichment step moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

import logging
import os
from typing import Any

from idis.audit.sink import AuditSink
from idis.services.runs.step_runtime.common import _resolve_run_audit_sink
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
)

logger = logging.getLogger(__name__)


def _run_full_enrichment(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    db_conn: Any = None,
    audit_sink: AuditSink | None = None,
    deal_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run enrichment for all configured providers in a FULL pipeline run.

    Iterates over registered providers, calls EnrichmentService.enrich() for each,
    and aggregates results. Gracefully handles zero results (step COMPLETED with
    result_count=0).

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction/grading.
        calc_ids: Calc IDs from calculation step.
        db_conn: Optional database connection for durable BYOL credential repository.
        deal_metadata: Deal record fields. company_name is the enrichment query
            identity. A blank or missing label blocks the step before any provider call.

    Returns:
        Dict with provider_count, result_count, blocked_count, enrichment_refs.
    """
    raw_label = (deal_metadata or {}).get("company_name")
    label = raw_label.strip() if isinstance(raw_label, str) else ""
    if not label:
        from idis.services.runs.orchestrator import RunStepBlockedError

        raise RunStepBlockedError(
            "ENRICHMENT_DEAL_LABEL_BLANK",
            "enrichment requires a nonblank explicit deal company label",
        )

    from idis.persistence.repositories.enrichment_credentials import (
        get_enrichment_credentials_repository,
    )
    from idis.services.enrichment.models import (
        EnrichmentPurpose,
        EnrichmentQuery,
        EnrichmentRequest,
        EnrichmentStatus,
        EntityType,
    )
    from idis.services.enrichment.service import create_default_enrichment_service

    effective_audit_sink = _resolve_run_audit_sink(audit_sink)
    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    strict_full_live = is_strict_full_live_required(dotenv_path=strict_dotenv_path)
    service = create_default_enrichment_service(
        audit_sink=effective_audit_sink,
        credential_repo=get_enrichment_credentials_repository(db_conn, tenant_id),
        strict_full_live=strict_full_live,
        tenant_id=tenant_id,
        strict_dotenv_path=strict_dotenv_path,
    )
    providers = service.list_providers()

    result_count = 0
    blocked_count = 0
    enrichment_refs: dict[str, dict[str, str]] = {}
    # Slice86 ledger: per-provider rows of SAFE values only (ids/enums/bools — never payloads,
    # credentials, or raw errors) plus aggregate counts, additive in the open result_summary.
    ledger_rows: list[dict[str, Any]] = []
    ledger_counts = {
        "hit": 0,
        "miss": 0,
        "error": 0,
        "blocked_rights": 0,
        "blocked_missing_byol": 0,
        "cache_hits": 0,
    }
    _LEDGER_COUNT_KEYS = {
        EnrichmentStatus.HIT: "hit",
        EnrichmentStatus.MISS: "miss",
        EnrichmentStatus.ERROR: "error",
        EnrichmentStatus.BLOCKED_RIGHTS: "blocked_rights",
        EnrichmentStatus.BLOCKED_MISSING_BYOL: "blocked_missing_byol",
    }

    request = EnrichmentRequest(
        tenant_id=tenant_id,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name=label),
        purpose=EnrichmentPurpose.DUE_DILIGENCE,
    )

    def _record(
        *,
        provider_id: str,
        status: EnrichmentStatus,
        from_cache: bool,
        rights_class: str,
        optional_in_strict: bool,
        requires_byol: bool,
        ref_id: str | None,
        conflicts: list[dict[str, str]] | None = None,
    ) -> None:
        from idis.services.enrichment.source_grade import map_rights_to_source_grade

        # Slice86 D-E: grade computed at summary time; BYOL credentials demonstrably backed
        # the call when a BYOL-requiring provider got past the missing-credential block.
        has_byol = requires_byol and status != EnrichmentStatus.BLOCKED_MISSING_BYOL
        source_grade = map_rights_to_source_grade(rights_class, has_byol=has_byol)
        ledger_rows.append(
            {
                "provider_id": provider_id,
                "status": status.value,
                "from_cache": from_cache,
                "rights_class": rights_class,
                "optional_in_strict": optional_in_strict,
                "ref_id": ref_id,
                "source_grade": source_grade.value,
                "conflicts": conflicts or [],
            }
        )
        ledger_counts[_LEDGER_COUNT_KEYS[status]] += 1
        if from_cache:
            ledger_counts["cache_hits"] += 1

    for provider_info in providers:
        provider_id = provider_info["provider_id"]
        optional_in_strict = bool(provider_info.get("optional_in_strict", False))
        rights_class = str(provider_info.get("rights_class", ""))
        requires_byol = bool(provider_info.get("requires_byol", False))
        # Slice86 policy: provider errors are fatal in strict mode UNLESS the provider is
        # registered optional_in_strict — optional failures take the non-strict handling
        # (recorded and continued) instead of aborting the step.
        strict_fatal = strict_full_live and not optional_in_strict

        try:
            result = service.enrich(provider_id=provider_id, request=request)
        except Exception as exc:
            if strict_fatal:
                raise RuntimeError(f"Strict enrichment provider failed: {provider_id}") from exc
            logger.warning(
                "Enrichment provider %s failed for deal %s in run %s",
                provider_id,
                deal_id,
                run_id,
                exc_info=True,
            )
            _record(
                provider_id=provider_id,
                status=EnrichmentStatus.ERROR,
                from_cache=False,
                rights_class=rights_class,
                optional_in_strict=optional_in_strict,
                requires_byol=requires_byol,
                ref_id=None,
            )
            continue

        if result.status == EnrichmentStatus.HIT:
            result_count += 1
            hit_ref_id: str | None = None
            hit_conflicts: list[dict[str, str]] = []
            if result.provenance:
                hit_ref_id = f"enrich-{provider_id}-{run_id[:8]}"
                enrichment_refs[hit_ref_id] = {
                    "ref_id": hit_ref_id,
                    "provider_id": result.provenance.provider_id,
                    "source_id": result.provenance.source_id,
                }
                # Slice86 D-G: narrow, never-fatal identifier conflict check — flags
                # carry only a fixed code + field name, never the compared values.
                from idis.services.enrichment.conflicts import identifier_conflicts

                hit_conflicts = identifier_conflicts(
                    request.query.model_dump(exclude_none=True),
                    result.provenance.identifiers_used,
                )
            _record(
                provider_id=provider_id,
                status=EnrichmentStatus.HIT,
                from_cache=result.from_cache,
                rights_class=rights_class,
                optional_in_strict=optional_in_strict,
                requires_byol=requires_byol,
                ref_id=hit_ref_id,
                conflicts=hit_conflicts,
            )
        elif result.status in (
            EnrichmentStatus.BLOCKED_RIGHTS,
            EnrichmentStatus.BLOCKED_MISSING_BYOL,
        ):
            if strict_fatal:
                raise RuntimeError(f"Strict enrichment provider blocked: {provider_id}")
            blocked_count += 1
            _record(
                provider_id=provider_id,
                status=result.status,
                from_cache=result.from_cache,
                rights_class=rights_class,
                optional_in_strict=optional_in_strict,
                requires_byol=requires_byol,
                ref_id=None,
            )
        elif result.status == EnrichmentStatus.ERROR and strict_fatal:
            raise RuntimeError(f"Strict enrichment provider failed: {provider_id}")
        else:
            _record(
                provider_id=provider_id,
                status=result.status,
                from_cache=result.from_cache,
                rights_class=rights_class,
                optional_in_strict=optional_in_strict,
                requires_byol=requires_byol,
                ref_id=None,
            )

    return {
        "provider_count": len(providers),
        "result_count": result_count,
        "blocked_count": blocked_count,
        "enrichment_refs": enrichment_refs,
        "enrichment_ledger": {"providers": ledger_rows, "counts": ledger_counts},
    }
