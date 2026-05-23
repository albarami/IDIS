"""Strict full-live readiness model and preflight reporting."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

IDIS_REQUIRE_FULL_LIVE_ENV = "IDIS_REQUIRE_FULL_LIVE"
STRICT_FULL_LIVE_BLOCKED = "STRICT_FULL_LIVE_BLOCKED"


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


class StrictFullLiveReadinessReport(BaseModel):
    """Strict full-live preflight report."""

    model_config = ConfigDict(extra="forbid")

    required: bool
    may_proceed: bool
    blocker_count: int
    blocking_components: list[str]
    components: list[StrictComponentReadiness]

    def component(self, component_name: str) -> StrictComponentReadiness:
        """Return a named component readiness result."""
        for component in self.components:
            if component.component_name == component_name:
                return component
        msg = f"Unknown strict full-live component: {component_name}"
        raise KeyError(msg)


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
) -> StrictFullLiveReadinessReport:
    """Build a safe strict full-live readiness report without executing a run."""
    values = os.environ if env is None else env
    resolver = binary_resolver or shutil.which
    extensions = _safe_extensions(
        data_room_root_path=data_room_root_path,
        data_room_file_extensions=data_room_file_extensions,
    )
    has_media = any(
        extension == ".mp4" for extension in extensions
    ) or _preflight_has_media_document(preflight_corpus)
    components = [
        _supported_parsers_extraction(values),
        _durable_runtime(values),
        _ocr(preflight_corpus=preflight_corpus),
        _mp4_stt(has_media=has_media, env=values, binary_resolver=resolver),
        _live("deterministic_calculations", "src/idis/services/calc/runner.py"),
        _external_enrichment_apis(),
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
    blocking_components = [
        component.component_name for component in components if not component.may_proceed
    ]
    return StrictFullLiveReadinessReport(
        required=True,
        may_proceed=not blocking_components,
        blocker_count=len(blocking_components),
        blocking_components=blocking_components,
        components=components,
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
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
) -> StrictComponentReadiness:
    requires_ocr = _preflight_has_ocr_required_document(preflight_corpus)
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
    )


def _external_enrichment_apis() -> StrictComponentReadiness:
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
        capability = metadata.get("parser_capability")
        reason_codes = (
            _string_set(metadata.get("parser_reason_codes"))
            | _string_set(metadata.get("reason_codes"))
            | _string_set(metadata.get("parse_error_codes"))
        )
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
        if doc_type in {"media", "mp4"} or metadata_types & {"media", "mp4"}:
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
