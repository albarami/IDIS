"""Slice72 strict provisioning truth report tests."""

from __future__ import annotations

import json
from typing import Any


def test_strict_provisioning_truth_lists_required_components_once() -> None:
    """Truth report must use REQUIRED_STRICT_COMPONENTS as its source of truth."""
    from idis.services.runs.strict_full_live import (
        REQUIRED_STRICT_COMPONENTS,
        build_strict_provisioning_truth_report,
    )

    report = build_strict_provisioning_truth_report(env={})
    component_names = [component["component_name"] for component in report["components"]]

    assert component_names == list(REQUIRED_STRICT_COMPONENTS)
    assert len(component_names) == len(set(component_names))
    assert report["component_count"] == len(REQUIRED_STRICT_COMPONENTS)
    assert report["source_of_truth"] == "strict_full_live.REQUIRED_STRICT_COMPONENTS"


def test_strict_provisioning_truth_reports_names_only_and_api_key_canonicalization() -> None:
    """Env/service requirements should expose names and API-key canonical status only."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    secret_value = "slice72-secret-value"
    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_API_KEYS_JSON": secret_value,
            "IDIS_API_KEYS": "legacy-value",
            "ANTHROPIC_API_KEY": secret_value,
            "NEO4J_PASSWORD": secret_value,
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
        }
    )
    serialized = json.dumps(report, sort_keys=True)
    durable_runtime = _component(report, "Postgres/RLS")
    api_key_mapping = report["env_canonicalization"]["api_keys"]

    assert "IDIS_DATABASE_URL" in durable_runtime["required_env_names"]
    assert "Postgres" in durable_runtime["required_service_names"]
    assert secret_value not in serialized
    assert "legacy-value" not in serialized
    assert "postgresql://user" not in serialized
    assert api_key_mapping == {
        "canonical": "IDIS_API_KEYS_JSON",
        "aliases": ["IDIS_API_KEYS"],
        "stale": [],
        "status": "reconciled",
    }


def test_strict_provisioning_truth_separates_truth_axes_without_clearing_readiness() -> None:
    """Truth report must not turn inventory/provisioning into runtime proof."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://configured/db",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": "C:\\private\\objects",
        }
    )

    assert report["strict_global_may_proceed"] is False
    assert report["claim_language"] == (
        "Slice72 produces a redacted strict provisioning truth report that reconciles "
        "strict readiness inventory, canonical env/service requirements, and "
        "runtime-proof status without clearing strict readiness."
    )
    for component in report["components"]:
        assert set(component) >= {
            "component_name",
            "declared",
            "configured",
            "health_checked",
            "runtime_call_proven",
            "full_run_used",
        }
        assert component["declared"] is True
        assert component["runtime_call_proven"] is False
        assert component["full_run_used"] is False


def test_strict_provisioning_truth_report_has_no_runtime_or_leakage_claims() -> None:
    """Slice72 report is inventory truth, not live probing or private-data proof."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "ANTHROPIC_API_KEY": "anthropic-secret-slice72",
            "OPENAI_API_KEY": "openai-secret-slice72",
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_OBJECT_STORE_BASE_DIR": "C:\\Projects\\IDIS\\private-objects",
        }
    )
    serialized = json.dumps(report, sort_keys=True).lower()

    assert report["real_example_not_run"] is True
    assert report["live_provider_calls_made"] is False
    assert report["external_enrichment_calls_made"] is False
    assert report["rag_vector_runtime_proven"] is False
    assert report["graph_runtime_proven"] is False
    assert report["layer2_live_challenge_proven"] is False
    assert report["vc_ready_claim"] is False
    assert "anthropic-secret" not in serialized
    assert "openai-secret" not in serialized
    assert "postgresql://user" not in serialized
    assert "c:\\projects" not in serialized
    assert "object_key" not in serialized
    assert "prompt_transcript" not in serialized
    assert "raw_text" not in serialized
    assert "embedding_payload" not in serialized
    assert "vector_payload" not in serialized


def test_strict_provisioning_truth_does_not_probe_object_store(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Truth report must not perform object-store put/delete health writes."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report
    from idis.storage.filesystem_store import FilesystemObjectStore

    def fail_put(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("truth report must not write object-store probe artifacts")

    def fail_delete(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("truth report must not delete object-store probe artifacts")

    monkeypatch.setattr(FilesystemObjectStore, "put", fail_put)
    monkeypatch.setattr(FilesystemObjectStore, "delete", fail_delete)

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://configured/db",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path),
        }
    )

    assert _component(report, "object storage")["configured"] is True
    assert _component(report, "object storage")["runtime_call_proven"] is False


def test_strict_provisioning_truth_marks_static_runtime_checks_not_run() -> None:
    """Static Slice72 checks must not report health_checked as if probes ran."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://configured/db",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": "C:\\private\\objects",
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "OPENAI_API_KEY": "openai-secret-slice72",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "neo4j-secret-slice72",
            "ANTHROPIC_API_KEY": "anthropic-secret-slice72",
            "IDIS_EXTRACT_BACKEND": "anthropic",
            "IDIS_DEBATE_BACKEND": "anthropic",
        }
    )

    for component_name in [
        "Neo4j graph projection",
        "graph retrieval",
        "pgvector/RAG",
        "object storage",
        "Anthropic extraction",
        "Anthropic debate",
        "Anthropic analysis",
        "Anthropic scoring",
        "Layer 2 IC challenge",
    ]:
        component = _component(report, component_name)
        assert component["health_checked"] is False
        assert component["health_check_status"] in {"not_run", "configured_not_checked"}


def _component(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        component for component in report["components"] if component["component_name"] == name
    )
