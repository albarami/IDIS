"""Redacted strict provisioning truth report projection."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from idis.services.runs.strict_full_live import (
    StrictComponentInventory,
    StrictComponentReadiness,
    build_strict_full_live_readiness_report,
)

SLICE72_CLAIM_LANGUAGE = (
    "Slice72 produces a redacted strict provisioning truth report that reconciles "
    "strict readiness inventory, canonical env/service requirements, and "
    "runtime-proof status without clearing strict readiness."
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
        "Neo4j graph projection",
        "graph retrieval",
        "pgvector/RAG",
        "Layer 1 debate",
        "Layer 2 IC challenge",
    }
)


def build_strict_provisioning_truth_report(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    preflight_corpus: Sequence[Mapping[str, Any]] | None = None,
    data_room_file_extensions: Sequence[str] | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
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
        probe_object_store=False,
    )
    readiness_by_inventory_name = _readiness_by_inventory_name(readiness.components)
    components = [
        _strict_provisioning_component(
            inventory_item=inventory_item,
            readiness_component=readiness_by_inventory_name.get(inventory_item.component_name),
        )
        for inventory_item in readiness.component_inventory
    ]
    report = {
        "claim_language": SLICE72_CLAIM_LANGUAGE,
        "source_of_truth": "strict_full_live.REQUIRED_STRICT_COMPONENTS",
        "component_count": len(components),
        "strict_global_may_proceed": False,
        "readiness_may_proceed": readiness.may_proceed,
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
    health_check_status = _slice72_health_check_status(inventory_item)
    return {
        "component_name": inventory_item.component_name,
        "declared": True,
        "configured": inventory_item.config_present,
        "health_checked": health_check_status == "healthy",
        "runtime_call_proven": False,
        "full_run_used": False,
        "required_env_names": required_env_names,
        "required_service_names": required_service_names,
        "health_check_status": health_check_status,
        "blocker": inventory_item.blocker,
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


def _slice72_health_check_status(inventory_item: StrictComponentInventory) -> str:
    if inventory_item.component_name in _STATIC_NOT_PROBED_COMPONENTS:
        return "configured_not_checked" if inventory_item.config_present else "not_run"
    if inventory_item.health_check_status == "healthy":
        return "healthy"
    return "not_run"


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
    return EmbeddingHealthCheck.failed(error="Embedding runtime probe not executed in Slice72.")


def _static_pgvector_health(env: Mapping[str, str]) -> PgvectorHealthCheck:
    if not _has_value(env, "IDIS_DATABASE_URL"):
        return PgvectorHealthCheck.missing(missing_env_vars=["IDIS_DATABASE_URL"])
    return PgvectorHealthCheck.failed()


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
        raise ValueError(f"STRICT_PROVISIONING_TRUTH_REPORT_LEAKAGE: {sorted(leaked)}")


def _is_sensitive_report_value(*, key: str, value: str) -> bool:
    if len(value) < 8:
        return False
    sensitive_key_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "URL", "URI", "PATH")
    if any(marker in key.upper() for marker in sensitive_key_markers):
        return True
    return "://" in value or ":\\" in value or "/" in value or "\\" in value


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())
