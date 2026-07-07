"""Reviewer strict full-live readiness route.

Exposes ``GET /v1/strict-readiness`` — a safe, reviewer-facing projection of the internal
:class:`StrictFullLiveReadinessReport` (DEC-D). The report is otherwise internal-only
(built at run admission in ``routes/runs.py``); this endpoint lets a fund reviewer see the
strict full-live component **modes** and **blockers** without exposing any internal detail.

Safe-shape: surfaces component modes (status enum), ``may_proceed`` flags, blocker counts +
blocking component names, and required env-var **names** / service labels only. It deliberately
drops the report's internal fields — ``evidence`` (source file:line), ``env_sources``, the
free-text ``blocker_message`` (which the codebase's own ``build_strict_block_operator_safe_details``
flags as able to carry paths / internal strings), the ``component_inventory`` truth table
(``evidence_files``), and the BYOL / enrichment provider matrices.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.persistence.db import IDIS_DATABASE_URL_ENV
from idis.persistence.neo4j_driver import (
    NEO4J_PASSWORD_ENV,
    NEO4J_URI_ENV,
    NEO4J_USERNAME_ENV,
    Neo4jHealthCheck,
)
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import (
    DEFAULT_EMBEDDING_MODEL,
    IDIS_EMBEDDING_MODEL_ENV,
    EmbeddingHealthCheck,
    _required_embedding_env,
)
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    StrictFullLiveReadinessReport,
    build_strict_full_live_readiness_report,
)

router = APIRouter(prefix="/v1", tags=["Readiness"])


class StrictReadinessComponentReview(BaseModel):
    """Safe reviewer view of one strict full-live component (mode + what it needs)."""

    model_config = ConfigDict(extra="forbid")

    component_name: str
    status: StrictComponentStatus
    may_proceed: bool
    required_env_vars: list[str] = Field(default_factory=list)
    required_services: list[str] = Field(default_factory=list)


class StrictReadinessReview(BaseModel):
    """Safe reviewer strict full-live readiness report — modes + blockers + env-var names only."""

    model_config = ConfigDict(extra="forbid")

    required: bool
    may_proceed: bool
    blocker_count: int
    blocking_components: list[str] = Field(default_factory=list)
    components: list[StrictReadinessComponentReview] = Field(default_factory=list)


def _safe_env_var_names(required_env_vars: list[str]) -> list[str]:
    """Reduce requirement tokens (e.g. ``IDIS_EXTRACT_BACKEND=anthropic``) to bare var NAMES,
    deduped and stable (first-occurrence order), so a required VALUE never surfaces."""
    names: list[str] = []
    for token in required_env_vars:
        name = token.split("=", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def _to_review(report: StrictFullLiveReadinessReport) -> StrictReadinessReview:
    """Project the internal readiness report to the safe reviewer shape (whitelist only)."""
    return StrictReadinessReview(
        required=report.required,
        may_proceed=report.may_proceed,
        blocker_count=report.blocker_count,
        blocking_components=list(report.blocking_components),
        components=[
            StrictReadinessComponentReview(
                component_name=component.component_name,
                status=component.status,
                may_proceed=component.may_proceed,
                required_env_vars=_safe_env_var_names(component.required_env_vars),
                required_services=list(component.required_services),
            )
            for component in report.components
        ],
    )


def _config_only_neo4j_health(env: Mapping[str, str]) -> Neo4jHealthCheck:
    """Config-only Neo4j readiness: detect missing env, else report configured. Never connects."""
    missing = [
        name
        for name in (NEO4J_URI_ENV, NEO4J_USERNAME_ENV, NEO4J_PASSWORD_ENV)
        if not str(env.get(name, "")).strip()
    ]
    if missing:
        return Neo4jHealthCheck.missing(missing_env_vars=missing)
    return Neo4jHealthCheck.healthy()


def _config_only_pgvector_health(env: Mapping[str, str]) -> PgvectorHealthCheck:
    """Config-only pgvector readiness: detect missing DB URL, else report configured. No connect."""
    if not str(env.get(IDIS_DATABASE_URL_ENV, "")).strip():
        return PgvectorHealthCheck.missing(missing_env_vars=[IDIS_DATABASE_URL_ENV])
    return PgvectorHealthCheck.healthy()


def _config_only_embedding_health(env: Mapping[str, str]) -> EmbeddingHealthCheck:
    """Config-only embedding readiness: reuse the authoritative missing-env detector, else report
    configured. Never calls the embedding provider."""
    missing = _required_embedding_env(env)
    if missing:
        return EmbeddingHealthCheck.missing(missing_env_vars=missing)
    model = str(env.get(IDIS_EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL)).strip()
    return EmbeddingHealthCheck.healthy(
        model=model or DEFAULT_EMBEDDING_MODEL,
        dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )


@router.get("/strict-readiness", response_model=StrictReadinessReview)
def get_strict_readiness(tenant_ctx: RequireTenantContext) -> StrictReadinessReview:
    """Return the safe strict full-live readiness review (component modes + blockers).

    This endpoint is **config / read-model inspection only — not a live health proof.** It reports
    each component's wiring / credential / infrastructure MODE from the server environment using
    config-only health checkers, with the object-store probe disabled, so a reviewer GET never opens
    a live Neo4j / Postgres connection, calls the embedding provider, or writes to the object store.
    Missing-env detection is preserved (a missing credential still surfaces as a blocker); only the
    live network / API / filesystem probe is skipped. Strict run admission (``routes/runs.py``)
    still performs the real live checks — this reviewer view deliberately does not.
    """
    report = build_strict_full_live_readiness_report(
        tenant_id=tenant_ctx.tenant_id,
        neo4j_health_checker=_config_only_neo4j_health,
        embedding_health_checker=_config_only_embedding_health,
        pgvector_health_checker=_config_only_pgvector_health,
        probe_object_store=False,
    )
    return _to_review(report)
