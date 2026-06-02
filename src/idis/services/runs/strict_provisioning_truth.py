"""Redacted strict provisioning truth report projection."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from idis.persistence.neo4j_driver import Neo4jHealthCheck, Neo4jHealthStatus
from idis.services.media_health import MediaHealthCheck, MediaHealthStatus, check_media_health
from idis.services.ocr_health import OcrHealthCheck, OcrHealthStatus, check_ocr_health
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.pgvector_health import PgvectorHealthCheck, PgvectorHealthStatus
from idis.services.runs.strict_full_live import (
    StrictComponentInventory,
    StrictComponentReadiness,
    build_strict_full_live_readiness_report,
    build_strict_runtime_profile_report,
)

SLICE72_CLAIM_LANGUAGE = (
    "Slice72 produces a redacted strict provisioning truth report that reconciles "
    "strict readiness inventory, canonical env/service requirements, and "
    "runtime-proof status without clearing strict readiness."
)
SLICE73_CLAIM_LANGUAGE = (
    "Slice73 adds opt-in redacted local health probes for safe strict provisioning "
    "dependencies only. It does not run live providers, external enrichment, "
    "real_example, strict FULL, or clear strict readiness."
)

_STATIC_NOT_PROBED_COMPONENTS = frozenset(
    {
        "Anthropic extraction",
        "Anthropic debate",
        "Anthropic analysis",
        "Anthropic scoring",
        "enrichment public providers",
        "enrichment BYOL providers",
        "object storage",
        "product export",
        "UI/API download",
        "Neo4j graph projection",
        "graph retrieval",
        "pgvector/RAG",
        "Layer 1 debate",
        "Layer 2 IC challenge",
    }
)


@dataclass(frozen=True)
class _LocalProbeStatus:
    label: str
    claim: str
    attempted: bool
    passed: bool
    health_check_status: str
    blocker: str | None = None


def build_strict_provisioning_truth_report(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    preflight_corpus: Sequence[Mapping[str, Any]] | None = None,
    data_room_file_extensions: Sequence[str] | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
    allow_local_strict_health_probes: bool = False,
    neo4j_health_checker: Callable[[Mapping[str, str]], Neo4jHealthCheck] | None = None,
    pgvector_health_checker: Callable[[Mapping[str, str]], PgvectorHealthCheck] | None = None,
    ocr_health_checker: Callable[[Mapping[str, str]], OcrHealthCheck] | None = None,
    media_health_checker: Callable[[Mapping[str, str]], MediaHealthCheck] | None = None,
    object_store_probe_base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build a redacted inventory/provisioning report without live runtime probes."""
    values = os.environ if env is None else env
    readiness = build_strict_full_live_readiness_report(
        env=values,
        dotenv_path=dotenv_path,
        preflight_corpus=preflight_corpus,
        data_room_file_extensions=data_room_file_extensions,
        binary_resolver=binary_resolver,
        load_byol_env_credentials=False,
        neo4j_health_checker=lambda checked_env: _static_neo4j_health(checked_env),
        embedding_health_checker=lambda checked_env: _static_embedding_health(checked_env),
        pgvector_health_checker=lambda checked_env: _static_pgvector_health(checked_env),
        ocr_health_checker=lambda checked_env: _static_ocr_health(checked_env),
        media_health_checker=lambda checked_env: _static_media_health(checked_env),
        probe_object_store=False,
    )
    local_probe_statuses = _build_local_probe_statuses(
        env=values,
        allow_local_strict_health_probes=allow_local_strict_health_probes,
        neo4j_health_checker=neo4j_health_checker,
        pgvector_health_checker=pgvector_health_checker,
        ocr_health_checker=ocr_health_checker,
        media_health_checker=media_health_checker,
        object_store_probe_base_dir=object_store_probe_base_dir,
    )
    readiness_by_inventory_name = _readiness_by_inventory_name(readiness.components)
    components = [
        _strict_provisioning_component(
            inventory_item=inventory_item,
            readiness_component=readiness_by_inventory_name.get(inventory_item.component_name),
            local_probe_status=local_probe_statuses.get(inventory_item.component_name),
        )
        for inventory_item in readiness.component_inventory
    ]
    report = {
        "claim_language": (
            SLICE73_CLAIM_LANGUAGE if allow_local_strict_health_probes else SLICE72_CLAIM_LANGUAGE
        ),
        "source_of_truth": "strict_full_live.REQUIRED_STRICT_COMPONENTS",
        "component_count": len(components),
        "strict_global_may_proceed": False,
        "readiness_may_proceed": readiness.may_proceed,
        "local_strict_health_probes_allowed": allow_local_strict_health_probes,
        "env_canonicalization": _strict_env_canonicalization(),
        "components": components,
        "real_example_not_run": True,
        "live_provider_calls_made": False,
        "external_enrichment_calls_made": False,
        "rag_vector_runtime_proven": False,
        "graph_runtime_proven": False,
        "layer2_live_challenge_proven": False,
        "vc_ready_claim": False,
    }
    _assert_strict_provisioning_truth_safe(report, env=values)
    return report


def _readiness_by_inventory_name(
    components: list[StrictComponentReadiness],
) -> dict[str, StrictComponentReadiness]:
    mapping = {
        "parsers": "supported_parsers_extraction",
        "OCR": "ocr",
        "MP4/STT": "mp4_stt",
        "Anthropic extraction": "supported_parsers_extraction",
        "Anthropic debate": "debate_layer_1",
        "Anthropic analysis": "agent_analysis",
        "Anthropic scoring": "scoring",
        "enrichment public providers": "external_enrichment_apis",
        "enrichment BYOL providers": "external_enrichment_apis",
        "Postgres/RLS": "durable_runtime",
        "object storage": "durable_runtime",
        "Neo4j graph projection": "graph_evidence_layer",
        "graph retrieval": "graph_evidence_layer",
        "pgvector/RAG": "rag_evidence_retrieval",
        "Layer 1 debate": "debate_layer_1",
        "Layer 2 IC challenge": "debate_layer_2_ic_challenge",
        "deliverable generation": "deliverable_generation",
        "product export": "product_export_bundle",
        "UI/API download": "product_export_bundle",
    }
    by_name = {component.component_name: component for component in components}
    return {
        inventory_name: by_name[readiness_name]
        for inventory_name, readiness_name in mapping.items()
        if readiness_name in by_name
    }


def _strict_provisioning_component(
    *,
    inventory_item: StrictComponentInventory,
    readiness_component: StrictComponentReadiness | None,
    local_probe_status: _LocalProbeStatus | None,
) -> dict[str, Any]:
    required_env_names = (
        _canonical_env_names(readiness_component.required_env_vars)
        if readiness_component is not None
        else []
    )
    required_service_names = (
        sorted(set(readiness_component.required_services))
        if readiness_component is not None
        else []
    )
    if inventory_item.component_name == "Postgres/RLS":
        required_service_names = sorted({*required_service_names, "Postgres"})
    health_check_status = _slice73_health_check_status(
        inventory_item=inventory_item,
        local_probe_status=local_probe_status,
    )
    return {
        "component_name": inventory_item.component_name,
        "declared": True,
        "configured": inventory_item.config_present,
        "health_checked": bool(local_probe_status and local_probe_status.attempted)
        and health_check_status in {"healthy", "configured_failed"},
        "runtime_call_proven": False,
        "full_run_used": False,
        "required_env_names": required_env_names,
        "required_service_names": required_service_names,
        "health_check_status": health_check_status,
        "local_probe_label": local_probe_status.label if local_probe_status else None,
        "local_probe_claim": local_probe_status.claim if local_probe_status else None,
        "local_probe_attempted": bool(local_probe_status and local_probe_status.attempted),
        "local_probe_passed": bool(local_probe_status and local_probe_status.passed),
        "local_probe_blocker": local_probe_status.blocker if local_probe_status else None,
        "blocker": _slice73_component_blocker(
            inventory_item=inventory_item,
            local_probe_status=local_probe_status,
        ),
        "evidence_files": inventory_item.evidence_files,
    }


def _canonical_env_names(values: list[str]) -> list[str]:
    names: list[str] = []
    for value in values:
        name = str(value).split("=", maxsplit=1)[0]
        if name == "IDIS_API_KEYS":
            name = "IDIS_API_KEYS_JSON"
        names.append(name)
    return sorted(set(names))


def _slice73_health_check_status(
    *,
    inventory_item: StrictComponentInventory,
    local_probe_status: _LocalProbeStatus | None,
) -> str:
    if local_probe_status is not None:
        return local_probe_status.health_check_status
    if inventory_item.component_name in _STATIC_NOT_PROBED_COMPONENTS:
        return "configured_not_checked" if inventory_item.config_present else "not_run"
    if inventory_item.health_check_status == "healthy":
        return "healthy"
    return "not_run"


def _slice73_component_blocker(
    *,
    inventory_item: StrictComponentInventory,
    local_probe_status: _LocalProbeStatus | None,
) -> str:
    if inventory_item.component_name in {"product export", "UI/API download"}:
        return (
            f"{inventory_item.component_name} is not runtime-proven because Slice73 does not run "
            "product export, download APIs, package review, or strict FULL."
        )
    if local_probe_status is None or not local_probe_status.passed:
        return inventory_item.blocker
    if inventory_item.component_name in {"Neo4j graph projection", "graph retrieval"}:
        return (
            "Local Neo4j health passed, but graph runtime is not proven because Slice73 "
            "does not run graph projection, graph retrieval, or strict FULL."
        )
    if inventory_item.component_name == "pgvector/RAG":
        return (
            "Local pgvector extension/connectivity passed, but RAG runtime is not proven "
            "because Slice73 does not run embedding providers, indexing, retrieval, or strict FULL."
        )
    if inventory_item.component_name == "object storage":
        return (
            "Local filesystem object-store temp write/delete passed, but product export and "
            "download runtime are not proven because Slice73 does not run strict FULL."
        )
    return inventory_item.blocker


def _build_local_probe_statuses(
    *,
    env: Mapping[str, str],
    allow_local_strict_health_probes: bool,
    neo4j_health_checker: Callable[[Mapping[str, str]], Neo4jHealthCheck] | None,
    pgvector_health_checker: Callable[[Mapping[str, str]], PgvectorHealthCheck] | None,
    ocr_health_checker: Callable[[Mapping[str, str]], OcrHealthCheck] | None,
    media_health_checker: Callable[[Mapping[str, str]], MediaHealthCheck] | None,
    object_store_probe_base_dir: str | Path | None,
) -> dict[str, _LocalProbeStatus]:
    if not allow_local_strict_health_probes:
        return {
            "pgvector/RAG": _not_attempted_probe(
                label="pgvector_extension_connectivity",
                claim="pgvector extension/connectivity only",
                blocker="explicit_opt_in_required",
            ),
            "Neo4j graph projection": _not_attempted_probe(
                label="neo4j_local_health",
                claim="Neo4j local health only",
                blocker="explicit_opt_in_required",
            ),
            "graph retrieval": _not_attempted_probe(
                label="neo4j_local_health",
                claim="Neo4j local health only",
                blocker="explicit_opt_in_required",
            ),
            "object storage": _not_attempted_probe(
                label="filesystem_object_store_temp_write_delete",
                claim="filesystem object-store temp write/delete only",
                blocker="explicit_opt_in_required",
            ),
            "OCR": _not_attempted_probe(
                label="ocr_local_health",
                claim="OCR runtime health only",
                blocker="explicit_opt_in_required",
            ),
            "MP4/STT": _not_attempted_probe(
                label="media_local_health",
                claim="media STT runtime health only",
                blocker="explicit_opt_in_required",
            ),
        }

    statuses: dict[str, _LocalProbeStatus] = {}
    neo4j_status = _neo4j_local_probe_status(env, neo4j_health_checker)
    statuses["Neo4j graph projection"] = neo4j_status
    statuses["graph retrieval"] = neo4j_status
    pgvector_status = _pgvector_local_probe_status(env, pgvector_health_checker)
    statuses["pgvector/RAG"] = pgvector_status
    statuses["object storage"] = _object_store_local_probe_status(
        env=env,
        object_store_probe_base_dir=object_store_probe_base_dir,
    )
    statuses["OCR"] = _ocr_local_probe_status(env, ocr_health_checker)
    statuses["MP4/STT"] = _media_local_probe_status(env, media_health_checker)
    return statuses


def _pgvector_local_probe_status(
    env: Mapping[str, str],
    health_checker: Callable[[Mapping[str, str]], PgvectorHealthCheck] | None,
) -> _LocalProbeStatus:
    if not _is_local_url(str(env.get("IDIS_DATABASE_URL", ""))):
        return _not_attempted_probe(
            label="pgvector_extension_connectivity",
            claim="pgvector extension/connectivity only",
            blocker="not_local_probe_target",
        )
    if health_checker is not None:
        result = health_checker(env)
    else:
        from idis.services.rag.pgvector_health import check_pgvector_health

        result = check_pgvector_health(env=env)
    return _probe_status_from_bool(
        label="pgvector_extension_connectivity",
        claim="pgvector extension/connectivity only",
        passed=result.status == PgvectorHealthStatus.HEALTHY,
    )


def _ocr_local_probe_status(
    env: Mapping[str, str],
    health_checker: Callable[[Mapping[str, str]], OcrHealthCheck] | None,
) -> _LocalProbeStatus:
    label = "ocr_local_health"
    claim = "OCR runtime health only"
    result = health_checker(env) if health_checker is not None else check_ocr_health(env=env)
    if result.status is OcrHealthStatus.DISABLED:
        return _not_attempted_probe(label=label, claim=claim, blocker="ocr_disabled")
    return _probe_status_from_bool(
        label=label,
        claim=claim,
        passed=result.status is OcrHealthStatus.HEALTHY,
    )


def _media_local_probe_status(
    env: Mapping[str, str],
    health_checker: Callable[[Mapping[str, str]], MediaHealthCheck] | None,
) -> _LocalProbeStatus:
    label = "media_local_health"
    claim = "media STT runtime health only"
    result = health_checker(env) if health_checker is not None else check_media_health(env=env)
    if result.status is MediaHealthStatus.DISABLED:
        return _not_attempted_probe(label=label, claim=claim, blocker="media_disabled")
    return _probe_status_from_bool(
        label=label,
        claim=claim,
        passed=result.status is MediaHealthStatus.HEALTHY,
    )


def _neo4j_local_probe_status(
    env: Mapping[str, str],
    health_checker: Callable[[Mapping[str, str]], Neo4jHealthCheck] | None,
) -> _LocalProbeStatus:
    if not _is_local_url(str(env.get("NEO4J_URI", ""))):
        return _not_attempted_probe(
            label="neo4j_local_health",
            claim="Neo4j local health only",
            blocker="not_local_probe_target",
        )
    if health_checker is not None:
        result = health_checker(env)
    else:
        from idis.persistence.neo4j_driver import check_neo4j_health

        result = check_neo4j_health(env=env)
    return _probe_status_from_bool(
        label="neo4j_local_health",
        claim="Neo4j local health only",
        passed=result.status == Neo4jHealthStatus.HEALTHY,
    )


def _object_store_local_probe_status(
    *,
    env: Mapping[str, str],
    object_store_probe_base_dir: str | Path | None,
) -> _LocalProbeStatus:
    label = "filesystem_object_store_temp_write_delete"
    claim = "filesystem object-store temp write/delete only"
    probe_base_dir = _safe_object_store_probe_base_dir(object_store_probe_base_dir)
    if probe_base_dir is None:
        return _not_attempted_probe(
            label=label,
            claim=claim,
            blocker="explicit_temp_local_probe_base_required",
        )
    from idis.services.runs.strict_full_live import _product_export_object_store_ready

    probe_env = {
        **env,
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": str(probe_base_dir),
    }
    return _probe_status_from_bool(
        label=label,
        claim=claim,
        passed=_product_export_object_store_ready(probe_env, probe=True),
    )


def _not_attempted_probe(*, label: str, claim: str, blocker: str) -> _LocalProbeStatus:
    return _LocalProbeStatus(
        label=label,
        claim=claim,
        attempted=False,
        passed=False,
        health_check_status="configured_not_checked",
        blocker=blocker,
    )


def _probe_status_from_bool(*, label: str, claim: str, passed: bool) -> _LocalProbeStatus:
    return _LocalProbeStatus(
        label=label,
        claim=claim,
        attempted=True,
        passed=passed,
        health_check_status="healthy" if passed else "configured_failed",
    )


def _safe_object_store_probe_base_dir(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    try:
        resolved = Path(value).expanduser().resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved.relative_to(temp_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _is_local_url(value: str) -> bool:
    if not value.strip():
        return False
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
        "host.docker.internal",
    } or hostname.startswith("127.")


def _strict_env_canonicalization() -> dict[str, dict[str, Any]]:
    return {
        "api_keys": {
            "canonical": "IDIS_API_KEYS_JSON",
            "aliases": ["IDIS_API_KEYS"],
            "stale": [],
            "status": "reconciled",
        }
    }


def _static_neo4j_health(env: Mapping[str, str]) -> Neo4jHealthCheck:
    required = ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
    missing = [key for key in required if not _has_value(env, key)]
    if missing:
        return Neo4jHealthCheck.missing(missing_env_vars=missing)
    return Neo4jHealthCheck.failed()


def _static_embedding_health(env: Mapping[str, str]) -> EmbeddingHealthCheck:
    required = ["IDIS_ENABLE_VECTOR_SEARCH", "OPENAI_API_KEY"]
    missing = [key for key in required if not _has_value(env, key)]
    if missing:
        return EmbeddingHealthCheck.missing(missing_env_vars=missing)
    return EmbeddingHealthCheck.failed(
        error="Embedding runtime probe not executed in strict provisioning truth report."
    )


def _static_pgvector_health(env: Mapping[str, str]) -> PgvectorHealthCheck:
    if not _has_value(env, "IDIS_DATABASE_URL"):
        return PgvectorHealthCheck.missing(missing_env_vars=["IDIS_DATABASE_URL"])
    return PgvectorHealthCheck.failed()


def _static_ocr_health(env: Mapping[str, str]) -> OcrHealthCheck:
    enabled = str(env.get("IDIS_OCR_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return OcrHealthCheck.disabled()
    return OcrHealthCheck.failed(
        error="OCR runtime probe not executed in strict provisioning truth report."
    )


def _static_media_health(env: Mapping[str, str]) -> MediaHealthCheck:
    adapter = str(env.get("IDIS_MEDIA_ADAPTER", "")).strip().lower()
    if not adapter:
        return MediaHealthCheck.disabled()
    return MediaHealthCheck.failed(
        error="Media runtime probe not executed in strict provisioning truth report."
    )


def _assert_strict_provisioning_truth_safe(
    report: dict[str, Any],
    *,
    env: Mapping[str, str],
) -> None:
    serialized = json.dumps(report, sort_keys=True).lower()
    forbidden = (
        "postgresql://",
        "c:\\projects",
        "object_key",
        "prompt_transcript",
        "raw_text",
        "embedding_payload",
        "vector_payload",
    )
    env_values = []
    for key, value in env.items():
        sanitized_value = str(value).strip().lower()
        if _is_sensitive_report_value(key=key, value=sanitized_value):
            env_values.append(sanitized_value)
    leaked = [token for token in forbidden if token in serialized]
    leaked.extend(value for value in env_values if value and value in serialized)
    if leaked:
        raise ValueError(
            f"STRICT_PROVISIONING_TRUTH_REPORT_LEAKAGE: leaked_token_count={len(set(leaked))}"
        )


def _is_sensitive_report_value(*, key: str, value: str) -> bool:
    if len(value) < 8:
        return False
    sensitive_key_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "URL", "URI", "PATH")
    if any(marker in key.upper() for marker in sensitive_key_markers):
        return True
    return "://" in value or ":\\" in value or "/" in value or "\\" in value


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit secret-safe strict provisioning/runtime reports."
    )
    parser.add_argument(
        "--strict-runtime-profile",
        action="store_true",
        help="Emit the Slice74 secret-safe strict runtime profile JSON.",
    )
    parser.add_argument(
        "--dotenv",
        default=None,
        help="Optional strict dotenv path. Values are loaded but never printed.",
    )
    args = parser.parse_args(argv)
    if args.strict_runtime_profile:
        report = build_strict_runtime_profile_report(dotenv_path=args.dotenv)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    parser.error("--strict-runtime-profile is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
