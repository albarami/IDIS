"""Strict full-live readiness model and preflight reporting."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from idis.parsers.media import (
    FASTER_WHISPER_ADAPTER_NAME,
    FasterWhisperMediaConfig,
    probe_faster_whisper_model,
)
from idis.services.enrichment.byol_credentials import (
    ByolCredentialRepository,
    ByolProviderHealthChecker,
    ByolProviderReadiness,
    ByolProviderStatus,
    EnrichmentProviderMatrixEntry,
    assess_byol_provider_readiness,
    build_enrichment_provider_matrix,
    byol_all_health_passed,
)

IDIS_REQUIRE_FULL_LIVE_ENV = "IDIS_REQUIRE_FULL_LIVE"
IDIS_STRICT_DOTENV_PATH_ENV = "IDIS_STRICT_DOTENV_PATH"
STRICT_FULL_LIVE_BLOCKED = "STRICT_FULL_LIVE_BLOCKED"
OCR_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"})

PROCESS_ENV_SOURCE = "process"
DOTENV_ENV_SOURCE = "dotenv"
MISSING_ENV_SOURCE = "missing"

REQUIRED_STRICT_COMPONENTS: tuple[str, ...] = (
    "API FULL path",
    "worker path",
    "private harness path",
    "parsers",
    "OCR",
    "MP4/STT",
    "Anthropic extraction",
    "Anthropic debate",
    "Anthropic analysis",
    "Anthropic scoring",
    "enrichment public providers",
    "enrichment BYOL providers",
    "Supabase database",
    "Supabase Auth",
    "Supabase Storage",
    "Supabase Vectors/RAG",
    "Postgres/RLS",
    "object storage",
    "audit sink",
    "calculation engine",
    "CalcSanad",
    "Neo4j graph projection",
    "graph retrieval",
    "pgvector/RAG",
    "Layer 1 debate",
    "Layer 2 IC challenge",
    "deliverable generation",
    "product export",
    "UI/API download",
    "real_example gate",
)

TRACKED_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "IDIS_EXTRACT_BACKEND",
    "IDIS_DEBATE_BACKEND",
    "IDIS_ANTHROPIC_MODEL_EXTRACT",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
    "IDIS_DATABASE_URL",
    "IDIS_DATABASE_ADMIN_URL",
    "IDIS_API_KEYS_JSON",
    "IDIS_API_KEYS",
    "IDIS_OBJECT_STORE_BACKEND",
    "IDIS_OBJECT_STORE_BASE_DIR",
    "IDIS_OCR_ENABLED",
    "IDIS_OCR_ADAPTER",
    "IDIS_MEDIA_STT_MODEL_PATH",
    "IDIS_MEDIA_STT_MODEL_NAME",
    "IDIS_MEDIA_ADAPTER",
    "IDIS_ENRICHMENT_ENCRYPTION_KEY",
    "COMPANIES_HOUSE_API_KEY",
    "GITHUB_API_TOKEN",
    "FRED_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "SUPABASE_SECRET_KEY",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
)


class StrictComponentStatus(StrEnum):
    """Allowed strict full-live readiness states."""

    LIVE_WIRED_AND_USED = "live-wired-and-used"
    CODE_EXISTS_BUT_NOT_WIRED = "code-exists-but-not-wired"
    CONFIGURED_BUT_FAILED_HEALTH_CHECK = "configured-but-failed-health-check"
    MISSING_CREDENTIALS = "missing-credentials"
    MISSING_INFRASTRUCTURE = "missing-infrastructure"
    NOT_IMPLEMENTED = "not-implemented"


class StrictComponentReadiness(BaseModel):
    """Readiness result for one required full-live component."""

    model_config = ConfigDict(extra="forbid")

    component_name: str
    status: StrictComponentStatus
    blocker_message: str
    required_env_vars: list[str] = Field(default_factory=list)
    required_services: list[str] = Field(default_factory=list)
    evidence: str
    may_proceed: bool


class StrictComponentInventory(BaseModel):
    """Truth-table inventory for one strict full-live component."""

    model_config = ConfigDict(extra="forbid")

    component_name: str
    exists_in_code: bool
    full_wired: bool
    config_present: bool
    health_check_status: str
    output_visible: bool
    blocker: str
    implementation_slice: str
    evidence_files: list[str]


class StrictFullLiveReadinessReport(BaseModel):
    """Strict full-live preflight report."""

    model_config = ConfigDict(extra="forbid")

    required: bool
    may_proceed: bool
    blocker_count: int
    blocking_components: list[str]
    components: list[StrictComponentReadiness]
    component_inventory: list[StrictComponentInventory] = Field(default_factory=list)
    env_sources: dict[str, str] = Field(default_factory=dict)
    byol_providers: list[ByolProviderReadiness] = Field(default_factory=list)
    enrichment_provider_matrix: list[EnrichmentProviderMatrixEntry] = Field(default_factory=list)

    def component(self, component_name: str) -> StrictComponentReadiness:
        """Return a named component readiness result."""
        for component in self.components:
            if component.component_name == component_name:
                return component
        msg = f"Unknown strict full-live component: {component_name}"
        raise KeyError(msg)


def is_strict_full_live_required(
    env: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path | None = None,
) -> bool:
    """Return whether strict full-live mode is enabled by environment."""
    env_source = _build_strict_env_source(
        process_env=os.environ if env is None else env,
        dotenv_path=dotenv_path,
    )
    value = str(env_source.effective_env.get(IDIS_REQUIRE_FULL_LIVE_ENV, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def build_strict_full_live_readiness_report(
    *,
    preflight_corpus: Sequence[Mapping[str, Any]] | None = None,
    data_room_root_path: str | Path | None = None,
    data_room_file_extensions: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
    tenant_id: str | None = None,
    byol_credential_repo: ByolCredentialRepository | None = None,
    load_byol_env_credentials: bool = True,
    byol_health_checker: ByolProviderHealthChecker | None = None,
) -> StrictFullLiveReadinessReport:
    """Build a safe strict full-live readiness report without executing a run."""
    env_source = _build_strict_env_source(
        process_env=os.environ if env is None else env,
        dotenv_path=dotenv_path,
    )
    values = env_source.effective_env
    resolver = binary_resolver or shutil.which
    extensions = _safe_extensions(
        data_room_root_path=data_room_root_path,
        data_room_file_extensions=data_room_file_extensions,
    )
    has_media = any(
        extension == ".mp4" for extension in extensions
    ) or _preflight_has_media_document(preflight_corpus)
    has_ocr_evidence = any(extension in OCR_IMAGE_EXTENSIONS for extension in extensions) or (
        _preflight_has_ocr_required_document(preflight_corpus)
    )
    byol_providers = assess_byol_provider_readiness(
        tenant_id=tenant_id,
        credential_repo=byol_credential_repo,
        env=values,
        env_sources=_env_source_map(env_source),
        load_env_credentials=load_byol_env_credentials,
        health_checker=byol_health_checker,
    )
    byol_credentials_durable = _byol_credentials_durable(byol_credential_repo)
    enrichment_provider_matrix = _build_enrichment_provider_matrix(byol_providers)
    components = [
        _supported_parsers_extraction(values),
        _durable_runtime(values),
        _ocr(has_ocr_evidence=has_ocr_evidence, env=values, binary_resolver=resolver),
        _mp4_stt(has_media=has_media, env=values, binary_resolver=resolver),
        _live("deterministic_calculations", "src/idis/services/calc/runner.py"),
        _external_enrichment_apis(
            byol_providers=byol_providers,
            byol_credentials_durable=byol_credentials_durable,
        ),
        _live_llm_model_clients(values),
        _analysis(values),
        _debate_layer_1(values),
        _not_implemented(
            "debate_layer_2_ic_challenge",
            "Distinct Layer 2 / IC challenge debate is not implemented.",
            "docs/architecture/strict_full_live_readiness.md; src/idis/api/routes/runs.py",
        ),
        _live("muhasabah_nff", "src/idis/debate/orchestrator.py; src/idis/deliverables/"),
        _scoring(values),
        _not_implemented(
            "rag_evidence_retrieval",
            "RAG/vector retrieval has no production embedding, index, query, or FULL wiring.",
            "migrations/*pgvector*; src/idis/debate/graph.py",
        ),
        _graph_evidence_layer(),
        _live("deliverable_generation", "src/idis/deliverables/generator.py"),
        _product_export_bundle(),
    ]
    component_inventory = _build_component_inventory(
        env_source=env_source,
        preflight_corpus=preflight_corpus,
        has_ocr_evidence=has_ocr_evidence,
        has_media=has_media,
        binary_resolver=resolver,
        byol_providers=byol_providers,
        byol_credentials_durable=byol_credentials_durable,
    )
    blocking_components = [
        component.component_name for component in components if not component.may_proceed
    ]
    blocking_components.extend(
        item.component_name for item in component_inventory if _inventory_item_blocks(item)
    )
    deduped_blocking_components = list(dict.fromkeys(blocking_components))
    return StrictFullLiveReadinessReport(
        required=True,
        may_proceed=not deduped_blocking_components,
        blocker_count=len(deduped_blocking_components),
        blocking_components=deduped_blocking_components,
        components=components,
        component_inventory=component_inventory,
        env_sources=_env_source_map(env_source),
        byol_providers=byol_providers,
        enrichment_provider_matrix=enrichment_provider_matrix,
    )


def _supported_parsers_extraction(env: Mapping[str, str]) -> StrictComponentReadiness:
    missing = _missing_model_env(
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
        )
    return _live(
        "supported_parsers_extraction",
        "src/idis/parsers/registry.py; src/idis/api/routes/runs.py:_build_extraction_llm_client",
    )


def _ocr(
    *,
    has_ocr_evidence: bool,
    env: Mapping[str, str],
    binary_resolver: Callable[[str], str | None],
) -> StrictComponentReadiness:
    evidence = (
        "src/idis/parsers/pdf.py; src/idis/parsers/image.py; "
        "src/idis/services/ingestion/defaults.py"
    )
    if not has_ocr_evidence:
        return StrictComponentReadiness(
            component_name="ocr",
            status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
            blocker_message="",
            required_env_vars=[],
            required_services=[],
            evidence=evidence,
            may_proceed=True,
        )
    if _ocr_runtime_ready(env=env, binary_resolver=binary_resolver):
        return _live("ocr", evidence)
    missing_services = []
    if binary_resolver("tesseract") is None:
        missing_services.append("tesseract")
    missing_env = []
    if not _truthy(env.get("IDIS_OCR_ENABLED")):
        missing_env.append("IDIS_OCR_ENABLED=1")
    return StrictComponentReadiness(
        component_name="ocr",
        status=StrictComponentStatus.MISSING_INFRASTRUCTURE,
        blocker_message=(
            "OCR-required documents are present and OCR is not full-live ready: "
            "default ingestion requires enabled OCR config, Tesseract runtime, "
            "and persisted PAGE_TEXT spans."
        ),
        required_env_vars=missing_env or ["IDIS_OCR_ENABLED=1"],
        required_services=missing_services or ["Tesseract OCR runtime"],
        evidence=evidence,
        may_proceed=False,
    )


def _mp4_stt(
    *,
    has_media: bool,
    env: Mapping[str, str],
    binary_resolver: Callable[[str], str | None],
) -> StrictComponentReadiness:
    evidence = "src/idis/parsers/media.py; src/idis/services/ingestion/defaults.py"
    if not has_media:
        return StrictComponentReadiness(
            component_name="mp4_stt",
            status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
            blocker_message="",
            required_env_vars=[],
            required_services=[],
            evidence=evidence,
            may_proceed=True,
        )
    if _media_runtime_ready(env=env, binary_resolver=binary_resolver):
        return _live("mp4_stt", evidence)
    missing_services = [
        service for service in ("ffmpeg", "ffprobe") if binary_resolver(service) is None
    ]
    missing_env = _missing_media_env(env)
    if not _media_model_probe_ready(env):
        missing_services.append("faster-whisper model")
    blocker_prefix = "MP4 files are present and " if has_media else ""
    return StrictComponentReadiness(
        component_name="mp4_stt",
        status=StrictComponentStatus.MISSING_INFRASTRUCTURE,
        blocker_message=(
            f"{blocker_prefix}STT is not full-live ready: media transcription requires "
            "ffmpeg, ffprobe, a provisioned faster-whisper model, and FULL ingestion wiring."
        ),
        required_env_vars=missing_env
        or ["IDIS_MEDIA_ADAPTER=faster-whisper", "IDIS_MEDIA_STT_MODEL_PATH"],
        required_services=missing_services or ["ffmpeg", "ffprobe", "faster-whisper model"],
        evidence=evidence,
        may_proceed=False,
    )


def _durable_runtime(env: Mapping[str, str]) -> StrictComponentReadiness:
    required_env_vars = [
        "IDIS_DATABASE_URL",
        "IDIS_API_KEYS",
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
        )
    return _live(
        "durable_runtime",
        "src/idis/api/main.py; src/idis/services/ingestion/defaults.py",
    )


def _external_enrichment_apis(
    *,
    byol_providers: list[ByolProviderReadiness],
    byol_credentials_durable: bool,
) -> StrictComponentReadiness:
    if byol_all_health_passed(byol_providers) and byol_credentials_durable:
        return _live(
            "external_enrichment_apis",
            "src/idis/services/enrichment/service.py; "
            "src/idis/services/enrichment/byol_credentials.py",
        )
    return StrictComponentReadiness(
        component_name="external_enrichment_apis",
        status=StrictComponentStatus.MISSING_CREDENTIALS,
        blocker_message=(
            "FULL enrichment is wired, but BYOL providers are not loaded into durable "
            "tenant credential storage with passing health checks."
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
    )


def _live_llm_model_clients(env: Mapping[str, str]) -> StrictComponentReadiness:
    missing = sorted(
        set(
            _missing_model_env(
                env=env,
                backend_key="IDIS_EXTRACT_BACKEND",
                model_keys=["IDIS_ANTHROPIC_MODEL_EXTRACT"],
            )
            + _missing_model_env(
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
        )
    return _live(
        "live_llm_model_clients",
        "src/idis/services/extraction/extractors/anthropic_client.py",
    )


def _analysis(env: Mapping[str, str]) -> StrictComponentReadiness:
    missing = _missing_model_env(
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
        )
    return _live("agent_analysis", "src/idis/analysis/runner.py")


def _debate_layer_1(env: Mapping[str, str]) -> StrictComponentReadiness:
    missing = _missing_model_env(
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
        )
    return _live("debate_layer_1", "src/idis/debate/orchestrator.py")


def _scoring(env: Mapping[str, str]) -> StrictComponentReadiness:
    missing = _missing_model_env(
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
        )
    return _live("scoring", "src/idis/scoring/engine.py")


def _graph_evidence_layer() -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name="graph_evidence_layer",
        status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
        blocker_message=(
            "GraphProjectionService and Neo4j repository code exist, but FULL does not call "
            "the graph projection or graph retrieval paths."
        ),
        required_env_vars=["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"],
        required_services=["Neo4j"],
        evidence="src/idis/persistence/graph_consistency.py:GraphProjectionService",
        may_proceed=False,
    )


def _product_export_bundle() -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name="product_export_bundle",
        status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
        blocker_message=(
            "Product export primitives exist, but strict VC export is not product-wired from "
            "strict-live run outputs."
        ),
        required_env_vars=[],
        required_services=["product deliverable export storage/path"],
        evidence=(
            "src/idis/deliverables/exporter.py; docs/architecture/strict_full_live_readiness.md"
        ),
        may_proceed=False,
    )


@dataclass(frozen=True)
class _StrictEnvSource:
    effective_env: dict[str, str]
    process_keys: frozenset[str]
    dotenv_keys: frozenset[str]


def _build_strict_env_source(
    *,
    process_env: Mapping[str, str],
    dotenv_path: str | Path | None,
) -> _StrictEnvSource:
    dotenv_values = _parse_dotenv_values(dotenv_path)
    effective_env = dict(dotenv_values)
    effective_env.update({key: str(value) for key, value in process_env.items()})
    return _StrictEnvSource(
        effective_env=effective_env,
        process_keys=frozenset(process_env.keys()),
        dotenv_keys=frozenset(dotenv_values.keys()),
    )


def _parse_dotenv_values(dotenv_path: str | Path | None) -> dict[str, str]:
    if dotenv_path is None:
        return {}
    path = Path(dotenv_path)
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key.removeprefix("export ").strip()
    if not key:
        return None
    return key, _strip_dotenv_value(value.strip())


def _strip_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


def _env_source_map(env_source: _StrictEnvSource) -> dict[str, str]:
    return {key: _env_source_for_key(env_source, key) for key in TRACKED_ENV_VARS}


def _env_source_for_key(env_source: _StrictEnvSource, key: str) -> str:
    if key in env_source.process_keys:
        return PROCESS_ENV_SOURCE
    if key in env_source.dotenv_keys:
        return DOTENV_ENV_SOURCE
    return MISSING_ENV_SOURCE


def _build_component_inventory(
    *,
    env_source: _StrictEnvSource,
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
    has_ocr_evidence: bool,
    has_media: bool,
    binary_resolver: Callable[[str], str | None],
    byol_providers: list[ByolProviderReadiness],
    byol_credentials_durable: bool,
) -> list[StrictComponentInventory]:
    env = env_source.effective_env
    ocr_ready = _ocr_runtime_ready(env=env, binary_resolver=binary_resolver)
    media_ready = _media_runtime_ready(env=env, binary_resolver=binary_resolver)
    inventory = [
        _inventory_item(
            "API FULL path",
            exists=True,
            full_wired=True,
            config_present=True,
            health="passed",
            output_visible=True,
            blocker="",
            slice_name="Slice 56",
            evidence=["src/idis/models/run_step.py", "src/idis/api/routes/runs.py"],
        ),
        _inventory_item(
            "worker path",
            exists=True,
            full_wired=True,
            config_present=_has_value(env, "IDIS_WORKER_TENANT_IDS"),
            health="passed",
            output_visible=True,
            blocker=(
                "Classified as canonical only through RunExecutionService; "
                "legacy PipelineExecutor is non-authoritative."
            ),
            slice_name="Slice 56",
            evidence=["src/idis/pipeline/worker.py", "src/idis/services/runs/execution.py"],
        ),
        _inventory_item(
            "private harness path",
            exists=True,
            full_wired=False,
            config_present=True,
            health="non_authoritative",
            output_visible=False,
            blocker=(
                "Private harness is aggregate-only and cannot be authoritative "
                "for strict success claims."
            ),
            slice_name="Slice 56",
            evidence=[
                "src/idis/evaluation/real_example_run_harness.py",
                "scripts/run_real_example_gate.py",
            ],
        ),
        _inventory_item(
            "parsers",
            exists=True,
            full_wired=True,
            config_present=True,
            health="contract_only",
            output_visible=True,
            blocker=(
                "Parser support remains partial; strict readiness must expose "
                "unsupported/deferred classes."
            ),
            slice_name="Slice 56",
            evidence=[
                "src/idis/parsers/registry.py",
                "src/idis/services/documents/parser_capabilities.py",
            ],
        ),
        _inventory_item(
            "OCR",
            exists=True,
            full_wired=ocr_ready or not has_ocr_evidence,
            config_present=_truthy(env.get("IDIS_OCR_ENABLED")) or not has_ocr_evidence,
            health=(
                "healthy"
                if ocr_ready
                else "not_applicable"
                if not has_ocr_evidence
                else "missing_config"
            ),
            output_visible=ocr_ready or not has_ocr_evidence,
            blocker=(
                "OCR-required documents are present and OCR runtime/config is missing or unhealthy."
                if has_ocr_evidence
                else "OCR runtime/config is optional until OCR-required evidence is present."
            ),
            slice_name="Slice 58",
            evidence=[
                "src/idis/parsers/ocr.py",
                "src/idis/parsers/image.py",
                "src/idis/parsers/pdf.py",
            ],
        ),
        _inventory_item(
            "MP4/STT",
            exists=True,
            full_wired=media_ready or not has_media,
            config_present=_media_model_config_present(env) or not has_media,
            health=(
                _media_health_status(env=env, binary_resolver=binary_resolver)
                if has_media
                else "not_applicable"
            ),
            output_visible=media_ready or not has_media,
            blocker=(
                "MP4 files are present and STT runtime/config is missing or unhealthy."
                if has_media
                else "Media STT runtime/config is optional until media evidence is present."
            ),
            slice_name="Slice 58",
            evidence=[
                "src/idis/parsers/media.py",
                "src/idis/services/documents/parser_capabilities.py",
            ],
        ),
        _inventory_llm_item(
            "Anthropic extraction",
            backend_key="IDIS_EXTRACT_BACKEND",
            model_keys=["IDIS_ANTHROPIC_MODEL_EXTRACT"],
            evidence=[
                "src/idis/api/routes/runs.py",
                "src/idis/services/extraction/extractors/anthropic_client.py",
            ],
            env=env,
        ),
        _inventory_llm_item(
            "Anthropic debate",
            backend_key="IDIS_DEBATE_BACKEND",
            model_keys=[
                "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
                "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
            ],
            evidence=["src/idis/api/routes/runs.py", "src/idis/debate/roles/llm_role_runner.py"],
            env=env,
        ),
        _inventory_llm_item(
            "Anthropic analysis",
            backend_key="IDIS_DEBATE_BACKEND",
            model_keys=["IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"],
            evidence=["src/idis/api/routes/runs.py", "src/idis/analysis/agents/__init__.py"],
            env=env,
        ),
        _inventory_llm_item(
            "Anthropic scoring",
            backend_key="IDIS_DEBATE_BACKEND",
            model_keys=["IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"],
            evidence=[
                "src/idis/api/routes/runs.py",
                "src/idis/analysis/scoring/llm_scorecard_runner.py",
            ],
            env=env,
        ),
        _inventory_item(
            "enrichment public providers",
            exists=True,
            full_wired=True,
            config_present=True,
            health="contract_only",
            output_visible=False,
            blocker="Public enrichment results are not yet strict provenance/output-visible.",
            slice_name="Slice 57",
            evidence=[
                "src/idis/services/enrichment/service.py",
                "src/idis/services/enrichment/connectors",
            ],
        ),
        _inventory_byol_item(
            byol_providers=byol_providers,
            env=env,
            byol_credentials_durable=byol_credentials_durable,
        ),
        _inventory_item(
            "Supabase database",
            exists=True,
            full_wired=False,
            config_present=_any_env_present(
                env, ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SECRET_KEY")
            )
            or "supabase" in str(env.get("IDIS_DATABASE_URL", "")).lower(),
            health="not_implemented",
            output_visible=False,
            blocker="Supabase is not separately health-checked; DB path is generic Postgres only.",
            slice_name="Slice 56",
            evidence=["src/idis/persistence/db.py"],
        ),
        _not_implemented_inventory("Supabase Auth", "Slice TBD"),
        _not_implemented_inventory("Supabase Storage", "Slice 59"),
        _not_implemented_inventory("Supabase Vectors/RAG", "Slice 62"),
        _inventory_item(
            "Postgres/RLS",
            exists=True,
            full_wired=True,
            config_present=_has_value(env, "IDIS_DATABASE_URL"),
            health="contract_only" if _has_value(env, "IDIS_DATABASE_URL") else "missing_config",
            output_visible=True,
            blocker="" if _has_value(env, "IDIS_DATABASE_URL") else "IDIS_DATABASE_URL missing.",
            slice_name="Slice 56",
            evidence=["src/idis/persistence/db.py", "src/idis/persistence/migrations/versions"],
        ),
        _inventory_item(
            "object storage",
            exists=True,
            full_wired=True,
            config_present=_has_value(env, "IDIS_OBJECT_STORE_BACKEND"),
            health="contract_only"
            if _has_value(env, "IDIS_OBJECT_STORE_BACKEND")
            else "missing_config",
            output_visible=False,
            blocker=(
                "Object storage exists for ingestion, but final bundle output "
                "visibility is not wired."
            ),
            slice_name="Slice 59",
            evidence=[
                "src/idis/storage/filesystem_store.py",
                "src/idis/services/ingestion/defaults.py",
            ],
        ),
        _inventory_item(
            "audit sink",
            exists=True,
            full_wired=False,
            config_present=_has_value(env, "IDIS_AUDIT_LOG_PATH")
            or _has_value(env, "IDIS_DATABASE_URL"),
            health="contract_only",
            output_visible=True,
            blocker="Some run helpers still instantiate InMemoryAuditSink.",
            slice_name="Slice 56",
            evidence=["src/idis/audit", "src/idis/api/middleware/audit.py"],
        ),
        _inventory_item(
            "calculation engine",
            exists=True,
            full_wired=True,
            config_present=True,
            health="contract_only",
            output_visible=False,
            blocker="Calculation outputs are not yet final-package-visible.",
            slice_name="Slice 60",
            evidence=["src/idis/services/calc/runner.py", "src/idis/calc/engine.py"],
        ),
        _inventory_item(
            "CalcSanad",
            exists=True,
            full_wired=True,
            config_present=True,
            health="contract_only",
            output_visible=False,
            blocker="CalcSanad persistence exists, but final-package visibility is not proven.",
            slice_name="Slice 60",
            evidence=[
                "src/idis/models/calc_sanad.py",
                "src/idis/persistence/repositories/calculations.py",
            ],
        ),
        _inventory_item(
            "Neo4j graph projection",
            exists=True,
            full_wired=False,
            config_present=_any_env_present(env, ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")),
            health="not_wired",
            output_visible=False,
            blocker="GraphProjectionService exists, but FULL does not call projection.",
            slice_name="Slice 61",
            evidence=[
                "src/idis/persistence/graph_consistency.py",
                "src/idis/persistence/graph_repo.py",
            ],
        ),
        _inventory_item(
            "graph retrieval",
            exists=True,
            full_wired=False,
            config_present=_any_env_present(env, ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")),
            health="not_wired",
            output_visible=False,
            blocker="Graph retrieval methods exist, but no FULL consumer/API path uses them.",
            slice_name="Slice 61",
            evidence=["src/idis/persistence/graph_repo.py", "src/idis/persistence/cypher"],
        ),
        _inventory_item(
            "pgvector/RAG",
            exists=False,
            full_wired=False,
            config_present=_has_value(env, "IDIS_ENABLE_VECTOR_SEARCH"),
            health="not_implemented",
            output_visible=False,
            blocker="No production embedding/index/query/retriever path exists.",
            slice_name="Slice 62",
            evidence=["scripts/pg_init.sql", "docker-compose.yml"],
        ),
        _inventory_item(
            "Layer 1 debate",
            exists=True,
            full_wired=True,
            config_present=_has_value(env, "IDIS_DEBATE_BACKEND"),
            health="contract_only",
            output_visible=True,
            blocker="Live Anthropic provenance is not fully health-checked in strict inventory.",
            slice_name="Slice 56",
            evidence=["src/idis/debate/orchestrator.py", "src/idis/api/routes/runs.py"],
        ),
        _inventory_item(
            "Layer 2 IC challenge",
            exists=False,
            full_wired=False,
            config_present=False,
            health="not_implemented",
            output_visible=False,
            blocker="Only Layer 2 readiness package exists; no distinct IC challenge debate loop.",
            slice_name="Slice 63",
            evidence=["src/idis/services/runs/methodology_layer2_readiness_package.py"],
        ),
        _inventory_item(
            "deliverable generation",
            exists=True,
            full_wired=True,
            config_present=True,
            health="contract_only",
            output_visible=True,
            blocker="Generation is in-run; durable product export is separate.",
            slice_name="Slice 59",
            evidence=["src/idis/deliverables/generator.py", "src/idis/api/routes/runs.py"],
        ),
        _inventory_item(
            "product export",
            exists=True,
            full_wired=False,
            config_present=False,
            health="not_wired",
            output_visible=False,
            blocker=(
                "Export primitives exist, but strict VC bundle persistence/API access is not wired."
            ),
            slice_name="Slice 59",
            evidence=["src/idis/deliverables/export.py", "src/idis/api/routes/deliverables.py"],
        ),
        _inventory_item(
            "UI/API download",
            exists=True,
            full_wired=False,
            config_present=True,
            health="not_wired",
            output_visible=False,
            blocker=(
                "Deliverables API/UI can list metadata, but strict bundle download "
                "URIs are not produced."
            ),
            slice_name="Slice 64",
            evidence=[
                "src/idis/api/routes/deliverables.py",
                "ui/src/app/deals/[dealId]/deliverables/page.tsx",
            ],
        ),
        _inventory_item(
            "real_example gate",
            exists=True,
            full_wired=False,
            config_present=True,
            health="contract_only",
            output_visible=False,
            blocker="Gate is aggregate/private; strict VC package run is not implemented.",
            slice_name="Slice 65",
            evidence=[
                "src/idis/evaluation/real_example_gate.py",
                "src/idis/evaluation/real_example_run_harness.py",
            ],
        ),
    ]
    return _ordered_inventory(inventory)


_BYOL_ENV_KEYS = (
    "COMPANIES_HOUSE_API_KEY",
    "GITHUB_API_TOKEN",
    "FRED_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
)

_DOC_MENTIONED_NOT_REGISTERED_ENRICHMENT_PROVIDERS = [
    "epo_open_patent",
    "google_trends",
]


def _build_enrichment_provider_matrix(
    byol_providers: list[ByolProviderReadiness],
) -> list[EnrichmentProviderMatrixEntry]:
    from idis.services.enrichment.service import _build_default_registry

    registry = _build_default_registry()
    return build_enrichment_provider_matrix(
        provider_descriptors=registry.list_providers(),
        byol_providers=byol_providers,
        not_registered_provider_ids=_DOC_MENTIONED_NOT_REGISTERED_ENRICHMENT_PROVIDERS,
    )


def _inventory_byol_item(
    *,
    byol_providers: list[ByolProviderReadiness],
    env: Mapping[str, str],
    byol_credentials_durable: bool,
) -> StrictComponentInventory:
    health_passed = byol_all_health_passed(byol_providers)
    full_wired = health_passed and byol_credentials_durable
    return _inventory_item(
        "enrichment BYOL providers",
        exists=True,
        full_wired=full_wired,
        config_present=health_passed or _any_env_present(env, _BYOL_ENV_KEYS),
        health="passed"
        if full_wired
        else _byol_inventory_health(
            byol_providers=byol_providers,
            byol_credentials_durable=byol_credentials_durable,
        ),
        output_visible=full_wired,
        blocker=""
        if full_wired
        else _byol_inventory_blocker(
            byol_providers=byol_providers,
            byol_credentials_durable=byol_credentials_durable,
        ),
        slice_name="Slice 57",
        evidence=[
            "src/idis/services/enrichment/byol_credentials.py",
            "src/idis/services/enrichment/service.py",
            "src/idis/persistence/repositories/enrichment_credentials.py",
        ],
    )


def _byol_inventory_health(
    *,
    byol_providers: list[ByolProviderReadiness],
    byol_credentials_durable: bool,
) -> str:
    if byol_all_health_passed(byol_providers) and not byol_credentials_durable:
        return "configured_not_durable"
    statuses = {provider.status for provider in byol_providers}
    if ByolProviderStatus.HEALTH_FAILED in statuses:
        return "configured_failed"
    if ByolProviderStatus.TENANT_CREDENTIAL_LOADED in statuses:
        return "contract_only"
    if ByolProviderStatus.ENV_KEY_PRESENT_NOT_LOADED in statuses:
        return "not_wired"
    return "missing_config"


def _byol_inventory_blocker(
    *,
    byol_providers: list[ByolProviderReadiness],
    byol_credentials_durable: bool,
) -> str:
    if not byol_providers:
        return "BYOL provider readiness has not been evaluated."
    if byol_all_health_passed(byol_providers) and not byol_credentials_durable:
        return "BYOL provider credentials are health-checked but not using durable tenant storage."
    failed = [
        provider.provider_id
        for provider in byol_providers
        if provider.status != ByolProviderStatus.HEALTH_PASSED
    ]
    return (
        "BYOL providers are not strict-ready; provider statuses are reported "
        f"without credential values for: {', '.join(failed)}."
    )


def _byol_credentials_durable(
    credential_repo: ByolCredentialRepository | None,
) -> bool:
    return bool(getattr(credential_repo, "is_durable", False))


def _inventory_llm_item(
    component_name: str,
    *,
    backend_key: str,
    model_keys: Sequence[str],
    evidence: list[str],
    env: Mapping[str, str],
) -> StrictComponentInventory:
    configured = (
        env.get(backend_key) == "anthropic"
        and _has_value(env, "ANTHROPIC_API_KEY")
        and all(_has_value(env, key) for key in model_keys)
    )
    return _inventory_item(
        component_name,
        exists=True,
        full_wired=True,
        config_present=configured,
        health="not_implemented" if configured else "missing_config",
        output_visible=False,
        blocker=(
            "Live Anthropic config is present, but Slice 56 has no real live model health check."
            if configured
            else "Live Anthropic backend/API key/model config is incomplete."
        ),
        slice_name="Slice 56",
        evidence=evidence,
    )


def _not_implemented_inventory(component_name: str, slice_name: str) -> StrictComponentInventory:
    return _inventory_item(
        component_name,
        exists=False,
        full_wired=False,
        config_present=False,
        health="not_implemented",
        output_visible=False,
        blocker=f"{component_name} is not implemented in the current strict FULL path.",
        slice_name=slice_name,
        evidence=[],
    )


def _inventory_item(
    component_name: str,
    *,
    exists: bool,
    full_wired: bool,
    config_present: bool,
    health: str,
    output_visible: bool,
    blocker: str,
    slice_name: str,
    evidence: list[str],
) -> StrictComponentInventory:
    return StrictComponentInventory(
        component_name=component_name,
        exists_in_code=exists,
        full_wired=full_wired,
        config_present=config_present,
        health_check_status=health,
        output_visible=output_visible,
        blocker=blocker,
        implementation_slice=slice_name,
        evidence_files=evidence,
    )


def _ordered_inventory(
    inventory: list[StrictComponentInventory],
) -> list[StrictComponentInventory]:
    by_name = {item.component_name: item for item in inventory}
    return [by_name[name] for name in REQUIRED_STRICT_COMPONENTS]


def _inventory_item_blocks(item: StrictComponentInventory) -> bool:
    if item.health_check_status == "non_authoritative":
        return True
    return (
        not item.exists_in_code
        or not item.full_wired
        or not item.config_present
        or not item.output_visible
        or item.health_check_status
        in {
            "contract_only",
            "missing_config",
            "not_implemented",
            "not_wired",
            "configured_failed",
        }
    )


def _any_env_present(env: Mapping[str, str], keys: Sequence[str]) -> bool:
    return any(_has_value(env, key) for key in keys)


def _media_model_config_present(env: Mapping[str, str]) -> bool:
    return _has_value(env, "IDIS_MEDIA_STT_MODEL_PATH") or _has_value(
        env,
        "IDIS_MEDIA_STT_MODEL_NAME",
    )


def _ocr_runtime_ready(
    *,
    env: Mapping[str, str],
    binary_resolver: Callable[[str], str | None],
) -> bool:
    adapter_name = str(env.get("IDIS_OCR_ADAPTER", "tesseract")).strip().lower()
    return (
        _truthy(env.get("IDIS_OCR_ENABLED"))
        and adapter_name == "tesseract"
        and binary_resolver("tesseract") is not None
    )


def _media_runtime_ready(
    *,
    env: Mapping[str, str],
    binary_resolver: Callable[[str], str | None],
) -> bool:
    return (
        str(env.get("IDIS_MEDIA_ADAPTER", "")).strip().lower() == FASTER_WHISPER_ADAPTER_NAME
        and all(binary_resolver(binary) is not None for binary in ("ffmpeg", "ffprobe"))
        and _media_model_probe_ready(env)
    )


def _media_model_probe_ready(env: Mapping[str, str]) -> bool:
    if not _media_model_config_present(env):
        return False
    return probe_faster_whisper_model(
        FasterWhisperMediaConfig(
            model_path=_optional_env(env, "IDIS_MEDIA_STT_MODEL_PATH"),
            model_name=_optional_env(env, "IDIS_MEDIA_STT_MODEL_NAME"),
            allow_model_download=_truthy(env.get("IDIS_MEDIA_STT_ALLOW_DOWNLOAD")),
        )
    ).can_attempt


def _missing_media_env(env: Mapping[str, str]) -> list[str]:
    missing: list[str] = []
    if str(env.get("IDIS_MEDIA_ADAPTER", "")).strip().lower() != FASTER_WHISPER_ADAPTER_NAME:
        missing.append("IDIS_MEDIA_ADAPTER=faster-whisper")
    if not _media_model_config_present(env):
        missing.append("IDIS_MEDIA_STT_MODEL_PATH or IDIS_MEDIA_STT_MODEL_NAME")
    return missing


def _optional_env(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _media_health_status(
    *,
    env: Mapping[str, str],
    binary_resolver: Callable[[str], str | None],
) -> str:
    if _media_runtime_ready(env=env, binary_resolver=binary_resolver):
        return "healthy"
    return "missing_config"


def _not_implemented(
    component_name: str,
    blocker_message: str,
    evidence: str,
) -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name=component_name,
        status=StrictComponentStatus.NOT_IMPLEMENTED,
        blocker_message=blocker_message,
        required_env_vars=[],
        required_services=[],
        evidence=evidence,
        may_proceed=False,
    )


def _live(component_name: str, evidence: str) -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name=component_name,
        status=StrictComponentStatus.LIVE_WIRED_AND_USED,
        blocker_message="",
        required_env_vars=[],
        required_services=[],
        evidence=evidence,
        may_proceed=True,
    )


def _missing_model_env(
    *,
    env: Mapping[str, str],
    backend_key: str,
    model_keys: Sequence[str],
) -> list[str]:
    required: list[str] = []
    if env.get(backend_key) != "anthropic":
        required.append(f"{backend_key}=anthropic")
    if not _has_value(env, "ANTHROPIC_API_KEY"):
        required.append("ANTHROPIC_API_KEY")
    required.extend(key for key in model_keys if not _has_value(env, key))
    return required


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_extensions(
    *,
    data_room_root_path: str | Path | None,
    data_room_file_extensions: Sequence[str] | None,
) -> list[str]:
    extensions = [str(extension).lower() for extension in data_room_file_extensions or []]
    if data_room_root_path is None:
        return extensions
    root = Path(data_room_root_path)
    if not root.exists() or not root.is_dir():
        return extensions
    return sorted(
        {path.suffix.lower() for path in root.rglob("*") if path.is_file()} | set(extensions)
    )


def _preflight_has_ocr_required_document(
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
) -> bool:
    for document in preflight_corpus or []:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        document_name = str(document.get("document_name") or "")
        doc_type = str(document.get("doc_type") or "").lower()
        capability = metadata.get("parser_capability")
        metadata_types = {
            str(metadata.get(key) or "").lower()
            for key in ("detected_format", "parser_doc_type", "file_type")
        }
        reason_codes = (
            _string_set(metadata.get("parser_reason_codes"))
            | _string_set(metadata.get("reason_codes"))
            | _string_set(metadata.get("parse_error_codes"))
        )
        if Path(document_name).suffix.lower() in OCR_IMAGE_EXTENSIONS:
            return True
        if doc_type == "image" or metadata_types & {"image"}:
            return True
        if metadata.get("parser_requires_ocr") is True or metadata.get("requires_ocr") is True:
            return True
        if isinstance(capability, Mapping) and capability.get("requires_ocr") is True:
            return True
        if "ocr_required" in reason_codes:
            return True
    return False


def _preflight_has_media_document(
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
) -> bool:
    for document in preflight_corpus or []:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        document_name = str(document.get("document_name") or "")
        doc_type = str(document.get("doc_type") or "").lower()
        metadata_types = {
            str(metadata.get(key) or "").lower()
            for key in ("detected_format", "parser_doc_type", "file_type")
        }
        reason_codes = (
            _string_set(metadata.get("parser_reason_codes"))
            | _string_set(metadata.get("reason_codes"))
            | _string_set(metadata.get("parse_error_codes"))
        )
        if Path(document_name).suffix.lower() == ".mp4":
            return True
        if doc_type in {"media", "mp4", "video", "audio"} or metadata_types & {
            "media",
            "mp4",
            "video",
            "audio",
        }:
            return True
        if (
            "media_transcription_unavailable" in reason_codes
            or "conversion_required" in reason_codes
        ):
            return True
    return False


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return set()
    return {str(item) for item in value}
