"""Strict full-live classification for configured external integrations."""

from __future__ import annotations

from collections.abc import Mapping

from idis.services.runs.strict_full_live_env import (
    database_url_is_supabase,
    has_supabase_config,
    present_byol_provider_env_vars,
)
from idis.services.runs.strict_full_live_health import StrictHealthCheckResult
from idis.services.runs.strict_full_live_models import (
    StrictComponentReadiness,
    StrictComponentStatus,
)


def external_enrichment_apis(env: Mapping[str, str]) -> StrictComponentReadiness:
    """Classify BYOL enrichment config without pretending env keys are wired."""
    present_keys = present_byol_provider_env_vars(env)
    if present_keys:
        return StrictComponentReadiness(
            component_name="external_enrichment_apis",
            status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
            blocker_message=(
                "BYOL provider keys are present in config, but FULL enrichment uses the "
                "tenant BYOL credential repository and the default repository is not loaded "
                "from those env vars."
            ),
            required_env_vars=present_keys,
            required_services=["tenant BYOL credential store"],
            evidence="src/idis/services/enrichment/service.py:create_default_enrichment_service",
            may_proceed=False,
            mode="config-present-not-wired",
            provenance={"provider": "byol", "fallback": "blocked", "env_values": "redacted"},
        )
    return StrictComponentReadiness(
        component_name="external_enrichment_apis",
        status=StrictComponentStatus.MISSING_CREDENTIALS,
        blocker_message=(
            "FULL enrichment is wired, but BYOL providers use an empty in-memory credential "
            "repository and strict mode cannot allow silent provider blocking."
        ),
        required_env_vars=[],
        required_services=[
            "tenant BYOL credential store",
            "companies_house credentials",
            "github credentials",
            "fred credentials",
            "finnhub credentials",
            "fmp credentials",
        ],
        evidence="src/idis/services/enrichment/service.py:create_default_enrichment_service",
        may_proceed=False,
        mode="missing-credentials",
        provenance={"provider": "byol+public", "fallback": "blocked"},
    )


def supabase_components(
    env: Mapping[str, str],
    *,
    runtime_health: StrictHealthCheckResult | None,
) -> list[StrictComponentReadiness]:
    """Classify Supabase product wiring when Supabase config is present."""
    if not has_supabase_config(env):
        return []
    components = [_supabase_database(env, runtime_health=runtime_health)]
    components.extend(
        [
            _supabase_not_wired(
                "supabase_auth",
                "Supabase Auth config is present, but FULL uses IDIS API key/JWT auth paths.",
            ),
            _supabase_not_wired(
                "supabase_storage",
                "Supabase Storage config is present, but the product object store is filesystem.",
            ),
            _supabase_not_wired(
                "supabase_vectors_rag",
                (
                    "Supabase/pgvector config is not enough: embeddings, index, "
                    "query, and FULL RAG wiring are absent."
                ),
            ),
            _supabase_not_wired(
                "supabase_edge_realtime_cron_queues",
                "Supabase Edge, Realtime, Cron, and Queues are not wired into FULL run paths.",
            ),
        ]
    )
    return components


def _supabase_database(
    env: Mapping[str, str],
    *,
    runtime_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    if not database_url_is_supabase(env):
        return StrictComponentReadiness(
            component_name="supabase_database",
            status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
            blocker_message=(
                "Supabase config is present, but IDIS_DATABASE_URL is not classified as a "
                "Supabase Postgres runtime target."
            ),
            required_env_vars=["IDIS_DATABASE_URL"],
            required_services=["Supabase Postgres"],
            evidence="src/idis/persistence/db.py",
            may_proceed=False,
            mode="config-present-not-wired",
            provenance={"provider": "supabase", "product": "database", "fallback": "none"},
        )
    if runtime_health is not None and runtime_health.passed:
        return StrictComponentReadiness(
            component_name="supabase_database",
            status=StrictComponentStatus.LIVE_WIRED_AND_USED,
            blocker_message="",
            required_env_vars=[],
            required_services=[],
            evidence="src/idis/persistence/db.py",
            may_proceed=True,
            mode="managed-postgres-runtime",
            provenance={"provider": "supabase", "product": "database", "fallback": "none"},
        )
    return StrictComponentReadiness(
        component_name="supabase_database",
        status=StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK,
        blocker_message=(
            "IDIS_DATABASE_URL is classified as Supabase Postgres, but durable runtime "
            "connectivity has not passed."
        ),
        required_env_vars=["IDIS_DATABASE_URL"],
        required_services=["Supabase Postgres"],
        evidence="src/idis/persistence/db.py",
        may_proceed=False,
        mode="configured-health-check-required",
        provenance={"provider": "supabase", "product": "database", "fallback": "none"},
    )


def _supabase_not_wired(component_name: str, blocker_message: str) -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name=component_name,
        status=StrictComponentStatus.NOT_IMPLEMENTED,
        blocker_message=blocker_message,
        required_env_vars=[],
        required_services=[],
        evidence="repo-wide Supabase product wiring scan",
        may_proceed=False,
        mode="config-present-not-wired",
        provenance={"provider": "supabase", "fallback": "none"},
    )
