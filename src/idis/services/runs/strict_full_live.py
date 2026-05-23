from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.services.runs.strict_full_live_component_utils import (
    graph_evidence_layer,
    live,
    not_implemented,
    product_export_bundle,
)
from idis.services.runs.strict_full_live_document_scan import (
    preflight_has_media_document,
    preflight_has_ocr_required_document,
    safe_extensions,
)
from idis.services.runs.strict_full_live_env import (
    build_env_config_inventory,
    build_strict_env_source,
)
from idis.services.runs.strict_full_live_health import (
    StrictHealthCheckResult,
    StrictLLMHealthCheckRequest,
    StrictRuntimeHealthCheckRequest,
    anthropic_provenance,
    llm_health_result,
    missing_model_env,
    runtime_health_result,
    runtime_provenance,
)
from idis.services.runs.strict_full_live_integrations import (
    external_enrichment_apis,
    supabase_components,
)
from idis.services.runs.strict_full_live_models import (
    StrictComponentReadiness,
    StrictComponentStatus,
    StrictFullLiveReadinessReport,
)

IDIS_REQUIRE_FULL_LIVE_ENV = "IDIS_REQUIRE_FULL_LIVE"
STRICT_FULL_LIVE_BLOCKED = "STRICT_FULL_LIVE_BLOCKED"


def is_strict_full_live_required(env: Mapping[str, str] | None = None) -> bool:
    """Return whether strict full-live mode is enabled by environment."""
    values = os.environ if env is None else env
    value = str(values.get(IDIS_REQUIRE_FULL_LIVE_ENV, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def build_strict_full_live_readiness_report(
    *,
    preflight_corpus: Sequence[Mapping[str, Any]] | None = None,
    data_room_root_path: str | Path | None = None,
    data_room_file_extensions: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
    llm_health_checker: Callable[[StrictLLMHealthCheckRequest], StrictHealthCheckResult]
    | None = None,
    runtime_health_checker: Callable[[StrictRuntimeHealthCheckRequest], StrictHealthCheckResult]
    | None = None,
    db_conn: Any = None,
    dotenv_path: str | Path | None = None,
) -> StrictFullLiveReadinessReport:
    """Build a safe strict full-live readiness report without executing a run."""
    process_values = os.environ if env is None else env
    env_source = build_strict_env_source(
        process_env=process_values,
        dotenv_path=dotenv_path,
    )
    values = env_source.effective_env
    resolver = binary_resolver or shutil.which
    extensions = safe_extensions(
        data_room_root_path=data_room_root_path,
        data_room_file_extensions=data_room_file_extensions,
    )
    has_media = any(
        extension == ".mp4" for extension in extensions
    ) or preflight_has_media_document(preflight_corpus)
    llm_health = llm_health_result(values, llm_health_checker=llm_health_checker)
    runtime_health = runtime_health_result(
        values,
        runtime_health_checker=runtime_health_checker,
        db_conn=db_conn,
    )
    components = [
        _supported_parsers_extraction(values, llm_health=llm_health),
        _durable_runtime(values, runtime_health=runtime_health),
        *supabase_components(values, runtime_health=runtime_health),
        _ocr(preflight_corpus=preflight_corpus),
        _mp4_stt(has_media=has_media, env=values, binary_resolver=resolver),
        live("deterministic_calculations", "src/idis/services/calc/runner.py"),
        external_enrichment_apis(values),
        _live_llm_model_clients(values, llm_health=llm_health),
        _analysis(values, llm_health=llm_health),
        _debate_layer_1(values, llm_health=llm_health),
        not_implemented(
            "debate_layer_2_ic_challenge",
            "Distinct Layer 2 / IC challenge debate is not implemented.",
            "docs/architecture/strict_full_live_readiness.md; src/idis/api/routes/runs.py",
        ),
        live("muhasabah_nff", "src/idis/debate/orchestrator.py; src/idis/deliverables/"),
        _scoring(values, llm_health=llm_health),
        not_implemented(
            "rag_evidence_retrieval",
            "RAG/vector retrieval has no production embedding, index, query, or FULL wiring.",
            "migrations/*pgvector*; src/idis/debate/graph.py",
        ),
        graph_evidence_layer(),
        live("deliverable_generation", "src/idis/deliverables/generator.py"),
        product_export_bundle(),
    ]
    blocking_components = [
        component.component_name for component in components if not component.may_proceed
    ]
    return StrictFullLiveReadinessReport(
        required=True,
        may_proceed=not blocking_components,
        blocker_count=len(blocking_components),
        blocking_components=blocking_components,
        components=components,
        env_config_inventory=build_env_config_inventory(
            env_source=env_source,
            llm_health=llm_health,
            runtime_health=runtime_health,
        ),
    )


def _supported_parsers_extraction(
    env: Mapping[str, str],
    *,
    llm_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    missing = missing_model_env(
        env=env,
        backend_key="IDIS_EXTRACT_BACKEND",
        model_keys=["IDIS_ANTHROPIC_MODEL_EXTRACT"],
    )
    if missing:
        return StrictComponentReadiness(
            component_name="supported_parsers_extraction",
            status=StrictComponentStatus.MISSING_CREDENTIALS,
            blocker_message=(
                "Supported parsers are wired, but strict extraction requires live Anthropic "
                "claim extraction instead of deterministic fallback."
            ),
            required_env_vars=missing,
            evidence=(
                "src/idis/parsers/registry.py; "
                "src/idis/api/routes/runs.py:_build_extraction_llm_client"
            ),
            may_proceed=False,
            mode="deterministic-fallback",
            provenance={"provider": "deterministic", "fallback": "deterministic"},
        )
    if llm_health is not None and not llm_health.passed:
        return _failed_llm_health_component(
            "supported_parsers_extraction",
            llm_health,
            (
                "Supported parsers are wired, but strict extraction live model health failed: "
                f"{llm_health.message}"
            ),
            (
                "src/idis/parsers/registry.py; "
                "src/idis/api/routes/runs.py:_build_extraction_llm_client"
            ),
        )
    return live(
        "supported_parsers_extraction",
        "src/idis/parsers/registry.py; src/idis/api/routes/runs.py:_build_extraction_llm_client",
        provenance=anthropic_provenance(),
    )


def _ocr(
    *,
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
) -> StrictComponentReadiness:
    requires_ocr = preflight_has_ocr_required_document(preflight_corpus)
    blocker = (
        "OCR-required documents are present and OCR is not wired into default FULL ingestion."
        if requires_ocr
        else "OCR adapter code exists, but default FULL ingestion does not enable OCR."
    )
    return StrictComponentReadiness(
        component_name="ocr",
        status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
        blocker_message=blocker,
        required_env_vars=[],
        required_services=["Tesseract OCR runtime"],
        evidence=(
            "src/idis/parsers/pdf.py; src/idis/parsers/image.py; "
            "src/idis/services/ingestion/defaults.py"
        ),
        may_proceed=False,
        mode="code-exists-but-not-wired",
        provenance={"provider": "tesseract", "fallback": "none"},
    )


def _durable_runtime(
    env: Mapping[str, str],
    *,
    runtime_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    required_env_vars = [
        "IDIS_DATABASE_URL",
        IDIS_API_KEYS_ENV,
        "IDIS_OBJECT_STORE_BACKEND",
    ]
    missing = [key for key in required_env_vars if not _has_value(env, key)]
    if missing:
        return StrictComponentReadiness(
            component_name="durable_runtime",
            status=StrictComponentStatus.MISSING_INFRASTRUCTURE,
            blocker_message=(
                "Strict full-live requires durable runtime configuration; in-memory "
                "or implicit local defaults cannot be claimed as enterprise full-live."
            ),
            required_env_vars=required_env_vars,
            required_services=["Postgres", "configured object store", "API key configuration"],
            evidence="src/idis/api/main.py; src/idis/services/ingestion/defaults.py",
            may_proceed=False,
            mode="missing-runtime",
            provenance={"backend": "missing", "fallback": "in-memory"},
        )
    if runtime_health is not None and not runtime_health.passed:
        return StrictComponentReadiness(
            component_name="durable_runtime",
            status=StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK,
            blocker_message=runtime_health.message,
            required_env_vars=required_env_vars,
            required_services=["Postgres", "configured object store", "API key configuration"],
            evidence="src/idis/api/main.py; src/idis/services/ingestion/defaults.py",
            may_proceed=False,
            mode="configured-health-check-failed",
            provenance=runtime_provenance(env, runtime_health),
        )
    return live(
        "durable_runtime",
        "src/idis/api/main.py; src/idis/services/ingestion/defaults.py",
        provenance=runtime_provenance(env, runtime_health),
    )


def _mp4_stt(
    *,
    has_media: bool,
    env: Mapping[str, str],
    binary_resolver: Callable[[str], str | None],
) -> StrictComponentReadiness:
    missing_services = [
        service for service in ("ffmpeg", "ffprobe") if binary_resolver(service) is None
    ]
    missing_env = [
        key
        for key in ("IDIS_MEDIA_STT_MODEL_PATH", "IDIS_MEDIA_STT_MODEL_NAME")
        if not _has_value(env, key)
    ]
    blocker_prefix = "MP4 files are present and " if has_media else ""
    return StrictComponentReadiness(
        component_name="mp4_stt",
        status=StrictComponentStatus.MISSING_INFRASTRUCTURE,
        blocker_message=(
            f"{blocker_prefix}STT is not full-live ready: media transcription requires "
            "ffmpeg, ffprobe, a provisioned faster-whisper model, and FULL ingestion wiring."
        ),
        required_env_vars=missing_env or ["IDIS_MEDIA_STT_MODEL_PATH", "IDIS_MEDIA_STT_MODEL_NAME"],
        required_services=missing_services or ["ffmpeg", "ffprobe", "faster-whisper model"],
        evidence="src/idis/parsers/media.py; src/idis/services/documents/parser_capabilities.py",
        may_proceed=False,
        mode="missing-infrastructure",
        provenance={"provider": "faster-whisper", "fallback": "none"},
    )


def _live_llm_model_clients(
    env: Mapping[str, str],
    *,
    llm_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    missing = sorted(
        set(
            missing_model_env(
                env=env,
                backend_key="IDIS_EXTRACT_BACKEND",
                model_keys=["IDIS_ANTHROPIC_MODEL_EXTRACT"],
            )
            + missing_model_env(
                env=env,
                backend_key="IDIS_DEBATE_BACKEND",
                model_keys=[
                    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
                    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
                ],
            )
        )
    )
    if missing:
        return StrictComponentReadiness(
            component_name="live_llm_model_clients",
            status=StrictComponentStatus.MISSING_CREDENTIALS,
            blocker_message=(
                "Live model clients are not fully configured; deterministic model clients "
                "would be selected for at least one strict-required path."
            ),
            required_env_vars=missing,
            evidence="src/idis/api/routes/runs.py:_build_extraction_llm_client,_build_debate_role_runners",
            may_proceed=False,
            mode="deterministic-fallback",
            provenance={"provider": "deterministic", "fallback": "deterministic"},
        )
    if llm_health is not None and not llm_health.passed:
        return _failed_llm_health_component(
            "live_llm_model_clients",
            llm_health,
            f"Live model clients are configured, but health check failed: {llm_health.message}",
            "src/idis/api/routes/runs.py:_build_extraction_llm_client,_build_debate_role_runners",
        )
    return live(
        "live_llm_model_clients",
        "src/idis/services/extraction/extractors/anthropic_client.py",
        provenance=anthropic_provenance(),
    )


def _analysis(
    env: Mapping[str, str],
    *,
    llm_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    missing = missing_model_env(
        env=env,
        backend_key="IDIS_DEBATE_BACKEND",
        model_keys=["IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"],
    )
    if missing:
        return StrictComponentReadiness(
            component_name="agent_analysis",
            status=StrictComponentStatus.MISSING_CREDENTIALS,
            blocker_message=(
                "Agent analysis is wired, but strict mode requires live analysis LLM calls "
                "instead of DeterministicAnalysisLLMClient."
            ),
            required_env_vars=missing,
            evidence="src/idis/api/routes/runs.py:_build_analysis_llm_client",
            may_proceed=False,
            mode="deterministic-fallback",
            provenance={"provider": "deterministic", "fallback": "deterministic"},
        )
    if llm_health is not None and not llm_health.passed:
        return _failed_llm_health_component(
            "agent_analysis",
            llm_health,
            f"Agent analysis live model health failed: {llm_health.message}",
            "src/idis/api/routes/runs.py:_build_analysis_llm_client",
        )
    return live(
        "agent_analysis",
        "src/idis/analysis/runner.py",
        provenance=anthropic_provenance(),
    )


def _debate_layer_1(
    env: Mapping[str, str],
    *,
    llm_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    missing = missing_model_env(
        env=env,
        backend_key="IDIS_DEBATE_BACKEND",
        model_keys=[
            "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
            "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
        ],
    )
    if missing:
        return StrictComponentReadiness(
            component_name="debate_layer_1",
            status=StrictComponentStatus.MISSING_CREDENTIALS,
            blocker_message=(
                "Debate layer 1 is wired, but default role runners are deterministic; "
                "strict mode requires Anthropic-backed role runners."
            ),
            required_env_vars=missing,
            evidence="src/idis/api/routes/runs.py:_build_debate_role_runners",
            may_proceed=False,
            mode="deterministic-fallback",
            provenance={"provider": "deterministic", "fallback": "deterministic"},
        )
    if llm_health is not None and not llm_health.passed:
        return _failed_llm_health_component(
            "debate_layer_1",
            llm_health,
            f"Debate layer 1 live model health failed: {llm_health.message}",
            "src/idis/api/routes/runs.py:_build_debate_role_runners",
        )
    return live(
        "debate_layer_1",
        "src/idis/debate/orchestrator.py",
        provenance=anthropic_provenance(),
    )


def _scoring(
    env: Mapping[str, str],
    *,
    llm_health: StrictHealthCheckResult | None,
) -> StrictComponentReadiness:
    missing = missing_model_env(
        env=env,
        backend_key="IDIS_DEBATE_BACKEND",
        model_keys=["IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"],
    )
    if missing:
        return StrictComponentReadiness(
            component_name="scoring",
            status=StrictComponentStatus.MISSING_CREDENTIALS,
            blocker_message=(
                "Scoring is wired, but strict mode requires a live scoring LLM instead of "
                "DeterministicScoringLLMClient."
            ),
            required_env_vars=missing,
            evidence="src/idis/api/routes/runs.py:_build_scoring_llm_client",
            may_proceed=False,
            mode="deterministic-fallback",
            provenance={"provider": "deterministic", "fallback": "deterministic"},
        )
    if llm_health is not None and not llm_health.passed:
        return _failed_llm_health_component(
            "scoring",
            llm_health,
            f"Scoring live model health failed: {llm_health.message}",
            "src/idis/api/routes/runs.py:_build_scoring_llm_client",
        )
    return live(
        "scoring",
        "src/idis/scoring/engine.py",
        provenance=anthropic_provenance(),
    )


def _failed_llm_health_component(
    component_name: str,
    llm_health: StrictHealthCheckResult,
    blocker_message: str,
    evidence: str,
) -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name=component_name,
        status=StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK,
        blocker_message=blocker_message,
        required_env_vars=[],
        required_services=["Anthropic API"],
        evidence=evidence,
        may_proceed=False,
        mode="configured-health-check-failed",
        provenance=anthropic_provenance() | llm_health.metadata,
    )


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())
