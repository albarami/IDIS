"""Slice73 opt-in local strict health-probe supplement tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_slice73_default_does_not_run_local_probes(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Default provisioning truth behavior must remain no-probe/no-side-effect."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report
    from idis.storage.filesystem_store import FilesystemObjectStore

    def fail_put(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("default report must not probe object storage")

    def fail_delete(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("default report must not delete object storage probe artifacts")

    monkeypatch.setattr(FilesystemObjectStore, "put", fail_put)
    monkeypatch.setattr(FilesystemObjectStore, "delete", fail_delete)

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "OPENAI_API_KEY": "openai-secret-slice73",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "neo4j-secret-slice73",
        }
    )

    assert report["strict_global_may_proceed"] is False
    for component_name in ["pgvector/RAG", "Neo4j graph projection", "object storage"]:
        component = _component(report, component_name)
        assert component["local_probe_attempted"] is False
        assert component["local_probe_passed"] is False
        assert component["health_checked"] is False
        assert component["runtime_call_proven"] is False
        assert component["full_run_used"] is False


def test_slice73_opt_in_runs_only_injected_local_pgvector_and_neo4j_probes() -> None:
    """Explicit opt-in should run local-safe injected probes and label proof narrowly."""
    from idis.persistence.neo4j_driver import Neo4jHealthCheck
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    calls: list[str] = []

    def pgvector_probe(env: dict[str, str]) -> PgvectorHealthCheck:
        calls.append(f"pgvector:{env['IDIS_DATABASE_URL']}")
        return PgvectorHealthCheck.healthy()

    def neo4j_probe(env: dict[str, str]) -> Neo4jHealthCheck:
        calls.append(f"neo4j:{env['NEO4J_URI']}")
        return Neo4jHealthCheck.healthy()

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "OPENAI_API_KEY": "openai-secret-slice73",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "neo4j-secret-slice73",
        },
        allow_local_strict_health_probes=True,
        pgvector_health_checker=pgvector_probe,
        neo4j_health_checker=neo4j_probe,
    )
    pgvector = _component(report, "pgvector/RAG")
    neo4j = _component(report, "Neo4j graph projection")
    graph_retrieval = _component(report, "graph retrieval")

    assert calls == [
        "neo4j:bolt://localhost:7687",
        "pgvector:postgresql://user:secret@localhost/idis",
    ]
    assert pgvector["local_probe_label"] == "pgvector_extension_connectivity"
    assert pgvector["local_probe_claim"] == "pgvector extension/connectivity only"
    assert pgvector["local_probe_attempted"] is True
    assert pgvector["local_probe_passed"] is True
    assert pgvector["runtime_call_proven"] is False
    assert pgvector["full_run_used"] is False
    assert "RAG runtime" not in pgvector["local_probe_claim"]

    assert neo4j["local_probe_label"] == "neo4j_local_health"
    assert neo4j["local_probe_claim"] == "Neo4j local health only"
    assert neo4j["local_probe_attempted"] is True
    assert neo4j["local_probe_passed"] is True
    assert neo4j["runtime_call_proven"] is False
    assert graph_retrieval["local_probe_passed"] is True
    assert report["strict_global_may_proceed"] is False


def test_slice73_local_neo4j_health_does_not_clear_graph_runtime_blockers(
    tmp_path: Path,
) -> None:
    """Local Neo4j health must not be promoted into graph runtime readiness."""
    from idis.persistence.neo4j_driver import Neo4jHealthCheck
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "neo4j-secret-slice73",
        },
        allow_local_strict_health_probes=True,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
    )
    graph_projection = _component(report, "Neo4j graph projection")
    graph_retrieval = _component(report, "graph retrieval")

    assert graph_projection["local_probe_passed"] is True
    assert graph_retrieval["local_probe_passed"] is True
    assert graph_projection["runtime_call_proven"] is False
    assert graph_retrieval["runtime_call_proven"] is False
    assert graph_projection["full_run_used"] is False
    assert graph_retrieval["full_run_used"] is False
    assert graph_projection["blocker"]
    assert graph_retrieval["blocker"]
    assert "local health" in graph_projection["local_probe_claim"]


def test_slice73_failed_injected_local_probes_are_reported_as_failed() -> None:
    """Failed local probes should be explicit without becoming runtime proof."""
    from idis.persistence.neo4j_driver import Neo4jHealthCheck
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "neo4j-secret-slice73",
        },
        allow_local_strict_health_probes=True,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.failed(),
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.failed(),
    )

    for component_name in ["pgvector/RAG", "Neo4j graph projection", "graph retrieval"]:
        component = _component(report, component_name)
        assert component["local_probe_attempted"] is True
        assert component["local_probe_passed"] is False
        assert component["health_checked"] is True
        assert component["health_check_status"] == "configured_failed"
        assert component["runtime_call_proven"] is False


def test_slice73_opt_in_rejects_non_local_database_and_neo4j_probe_targets() -> None:
    """Opt-in local probes must not call checkers for remote-looking targets."""
    from idis.persistence.neo4j_driver import Neo4jHealthCheck
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    def fail_pgvector_probe(_env: dict[str, str]) -> PgvectorHealthCheck:
        raise AssertionError("remote database URLs must not be probed")

    def fail_neo4j_probe(_env: dict[str, str]) -> Neo4jHealthCheck:
        raise AssertionError("remote Neo4j URIs must not be probed")

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@db.example.com/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "OPENAI_API_KEY": "openai-secret-slice73",
            "NEO4J_URI": "neo4j+s://secret-host.databases.neo4j.io",
            "NEO4J_USERNAME": "private_user",
            "NEO4J_PASSWORD": "private_password",
        },
        allow_local_strict_health_probes=True,
        pgvector_health_checker=fail_pgvector_probe,
        neo4j_health_checker=fail_neo4j_probe,
    )

    for component_name in ["pgvector/RAG", "Neo4j graph projection", "graph retrieval"]:
        component = _component(report, component_name)
        assert component["local_probe_attempted"] is False
        assert component["local_probe_passed"] is False
        assert component["health_checked"] is False
        assert component["health_check_status"] == "configured_not_checked"
        assert component["local_probe_blocker"] == "not_local_probe_target"


def test_slice73_object_store_probe_requires_explicit_temp_local_base(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Object-store probing must never touch ambient configured paths."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report
    from idis.storage.filesystem_store import FilesystemObjectStore

    ambient_base = tmp_path / "ambient-objects"
    explicit_base = tmp_path / "explicit-probe-objects"
    touched_bases: list[Path] = []

    def fail_if_called_without_explicit_base(
        self: FilesystemObjectStore, *_args: Any, **_kwargs: Any
    ) -> None:
        raise AssertionError(f"unexpected object-store probe at {self.base_dir}")

    monkeypatch.setattr(FilesystemObjectStore, "put", fail_if_called_without_explicit_base)
    monkeypatch.setattr(FilesystemObjectStore, "delete", fail_if_called_without_explicit_base)

    no_explicit_probe_report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(ambient_base),
        },
        allow_local_strict_health_probes=True,
    )

    assert _component(no_explicit_probe_report, "object storage")["local_probe_attempted"] is False

    def record_put(self: FilesystemObjectStore, *_args: Any, **_kwargs: Any) -> object:
        assert self.base_dir == explicit_base.resolve()
        touched_bases.append(self.base_dir)
        return object()

    def record_delete(self: FilesystemObjectStore, *_args: Any, **_kwargs: Any) -> None:
        assert self.base_dir == explicit_base.resolve()
        touched_bases.append(self.base_dir)

    monkeypatch.setattr(FilesystemObjectStore, "put", record_put)
    monkeypatch.setattr(FilesystemObjectStore, "delete", record_delete)

    explicit_probe_report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(ambient_base),
        },
        allow_local_strict_health_probes=True,
        object_store_probe_base_dir=explicit_base,
    )

    object_storage = _component(explicit_probe_report, "object storage")
    assert object_storage["local_probe_label"] == "filesystem_object_store_temp_write_delete"
    assert object_storage["local_probe_claim"] == "filesystem object-store temp write/delete only"
    assert object_storage["local_probe_attempted"] is True
    assert object_storage["local_probe_passed"] is True
    assert touched_bases == [explicit_base.resolve(), explicit_base.resolve()]
    assert ambient_base.resolve() not in touched_bases


def test_slice73_local_object_store_probe_does_not_clear_product_runtime_blockers(
    tmp_path: Path,
) -> None:
    """Local object-store health must not become product export/download proof."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "ambient-objects"),
        },
        allow_local_strict_health_probes=True,
        object_store_probe_base_dir=tmp_path / "explicit-probe-objects",
    )
    object_storage = _component(report, "object storage")
    product_export = _component(report, "product export")
    ui_api_download = _component(report, "UI/API download")

    assert object_storage["local_probe_passed"] is True
    for component in [product_export, ui_api_download]:
        assert component["local_probe_attempted"] is False
        assert component["local_probe_passed"] is False
        assert component["health_checked"] is False
        assert component["health_check_status"] == "configured_not_checked"
        assert component["runtime_call_proven"] is False
        assert component["full_run_used"] is False
        assert component["blocker"]
        assert "strict FULL" in component["blocker"]
    assert report["strict_global_may_proceed"] is False
    assert report["readiness_may_proceed"] is False


def test_slice73_rejects_explicit_object_store_probe_base_outside_temp(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Even explicit object-store probe roots must be temp/local paths."""
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report
    from idis.storage.filesystem_store import FilesystemObjectStore

    def fail_put(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("non-temp explicit probe base must not be touched")

    monkeypatch.setattr(FilesystemObjectStore, "put", fail_put)
    monkeypatch.setattr(FilesystemObjectStore, "delete", fail_put)

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "ambient-objects"),
        },
        allow_local_strict_health_probes=True,
        object_store_probe_base_dir=Path("C:/Projects/IDIS/non-temp-probe"),
    )
    object_storage = _component(report, "object storage")

    assert object_storage["local_probe_attempted"] is False
    assert object_storage["local_probe_passed"] is False
    assert object_storage["local_probe_blocker"] == "explicit_temp_local_probe_base_required"


def test_slice73_provider_keys_do_not_trigger_provider_or_embedding_calls(
    monkeypatch: Any,
) -> None:
    """Provider-like env should remain static and never call external-provider health code."""
    import idis.services.runs.strict_full_live as strict_full_live
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    def fail_embedding_call(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("OpenAI embedding health must not run in Slice73")

    monkeypatch.setattr(strict_full_live, "check_embedding_health", fail_embedding_call)

    report = build_strict_provisioning_truth_report(
        env={
            "ANTHROPIC_API_KEY": "anthropic-secret-slice73",
            "OPENAI_API_KEY": "openai-secret-slice73",
            "FINNHUB_API_KEY": "finnhub-secret-slice73",
            "FMP_API_KEY": "fmp-secret-slice73",
            "IDIS_EXTRACT_BACKEND": "anthropic",
            "IDIS_DEBATE_BACKEND": "anthropic",
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "IDIS_DATABASE_URL": "postgresql://user:secret@localhost/idis",
            "IDIS_API_KEYS_JSON": "{}",
        },
        allow_local_strict_health_probes=True,
    )

    assert report["live_provider_calls_made"] is False
    assert report["external_enrichment_calls_made"] is False
    for component_name in [
        "Anthropic extraction",
        "Anthropic debate",
        "Anthropic analysis",
        "Anthropic scoring",
        "enrichment public providers",
        "enrichment BYOL providers",
    ]:
        component = _component(report, component_name)
        assert component["local_probe_attempted"] is False
        assert component["local_probe_passed"] is False
        assert component["runtime_call_proven"] is False


def test_slice73_report_has_no_secret_path_object_key_prompt_vector_or_raw_text_leakage(
    tmp_path: Path,
) -> None:
    """Slice73 report must remain redacted even when local probes are opted in."""
    from idis.persistence.neo4j_driver import Neo4jHealthCheck
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_provisioning_truth_report

    report = build_strict_provisioning_truth_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:super-secret@localhost/idis",
            "IDIS_API_KEYS_JSON": '{"secret": "api-secret-slice73"}',
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "ambient-objects"),
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "OPENAI_API_KEY": "openai-secret-slice73",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j-private-user",
            "NEO4J_PASSWORD": "neo4j-secret-slice73",
        },
        allow_local_strict_health_probes=True,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
    )
    serialized = json.dumps(report, sort_keys=True).lower()

    assert "postgresql://user" not in serialized
    assert "super-secret" not in serialized
    assert "api-secret" not in serialized
    assert "openai-secret" not in serialized
    assert "neo4j-secret" not in serialized
    assert "neo4j-private-user" not in serialized
    assert str(tmp_path).lower() not in serialized
    assert "object_key" not in serialized
    assert "prompt_transcript" not in serialized
    assert "raw_text" not in serialized
    assert "embedding_payload" not in serialized
    assert "vector_payload" not in serialized
    assert report["strict_global_may_proceed"] is False
    assert report["vc_ready_claim"] is False


def _component(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        component for component in report["components"] if component["component_name"] == name
    )
