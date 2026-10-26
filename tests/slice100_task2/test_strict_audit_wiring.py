"""Slice100 Task 2 RED: strict runtime must use one durable PostgreSQL audit sink.

These tests are hermetic.  PostgreSQL sink objects are constructed but never emit, and all
production helpers that would touch storage/providers are replaced with behavioral recorders.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from idis.api.main import create_app
from idis.api.middleware.audit import AuditMiddleware
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import InMemoryAuditSink, JsonlFileAuditSink
from tests._linux_poppler_gate import requires_linux_governed_poppler

_TENANT_ID = "10000000-0000-4000-8000-000000000002"
_PERSISTENT_OBJECT_PATH = r"C:\IDIS_TASK2_HERMETIC_PERSISTENT_OBJECTS"


def _api_keys_json() -> str:
    return json.dumps(
        {
            "task2-acceptance-key": {
                "tenant_id": _TENANT_ID,
                "actor_id": "slice100-acceptance-service",
                "name": "Slice100 Acceptance Service",
                "timezone": "Asia/Qatar",
                "data_region": "me-south-1",
                "roles": ["INTEGRATION_SERVICE"],
            }
        }
    )


def _strict_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "api")
    monkeypatch.setenv("IDIS_DATABASE_URL", "postgresql://task2.invalid/idis")
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json())
    monkeypatch.setenv("IDIS_SERVICE_REGION", "me-south-1")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    # Configuration only: constructing the filesystem adapter performs no write.  Keep this path
    # outside temp/worktree roots so a future object-store admission guard cannot mask audit RED.
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", _PERSISTENT_OBJECT_PATH)


def _reviewed_api_profile(tmp_path: Path) -> Any:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    return _reviewed_profile(
        tmp_path,
        "api",
        additions={"IDIS_ACCEPTANCE_TENANT_ID": _TENANT_ID},
    )


@pytest.mark.parametrize("sink_kind", ["memory", "jsonl"])
def test_strict_app_rejects_explicit_non_durable_audit_sink(
    sink_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strict process must reject an explicitly injected JSONL or memory sink at composition."""
    _strict_env(monkeypatch)
    sink = (
        InMemoryAuditSink()
        if sink_kind == "memory"
        else JsonlFileAuditSink(str(tmp_path / "audit.jsonl"))
    )

    with pytest.raises(Exception) as exc_info:
        create_app(
            audit_sink=sink,
            service_region="me-south-1",
            strict_profile=_reviewed_api_profile(tmp_path / "profile"),
        )

    assert "AUDIT" in str(exc_info.value).upper()


def test_non_strict_app_preserves_explicit_test_audit_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 2 must not remove the explicit hermetic injection seam outside strict mode."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json())
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    sink = InMemoryAuditSink()

    app = create_app(audit_sink=sink, service_region="me-south-1")

    assert app.state.audit_sink is sink


def test_strict_default_threads_one_postgres_sink_through_api_and_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed no-argument factory must not split API and ingestion audit durability."""
    import idis.api.main as main_module

    _strict_env(monkeypatch)
    ingestion_calls: list[Any] = []
    ingestion_service = object()

    def build_ingestion(*, audit_sink: Any, env: Any, strict: bool) -> object:
        # Task 4 controlled drift: strict parser limits/adapters use the reviewed profile.
        assert strict is True
        assert env["IDIS_INGEST_MAX_BYTES"] == "536870912"
        ingestion_calls.append(audit_sink)
        return ingestion_service

    monkeypatch.setattr(main_module, "build_default_ingestion_service", build_ingestion)

    app = main_module.create_app(
        service_region="me-south-1",
        strict_profile=_reviewed_api_profile(tmp_path / "profile"),
    )

    sink = app.state.audit_sink
    assert isinstance(sink, PostgresAuditSink)
    assert ingestion_calls == [sink]
    assert app.state.ingestion_service is ingestion_service
    audit_middleware = next(item for item in app.user_middleware if item.cls is AuditMiddleware)
    assert audit_middleware.kwargs["sink"] is sink
    assert audit_middleware.kwargs["postgres_sink"] is sink


@requires_linux_governed_poppler
def test_strict_app_uses_reviewed_database_after_ambient_environment_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Later ambient deletion cannot change the reviewed strict composition."""
    _strict_env(monkeypatch)
    profile = _reviewed_api_profile(tmp_path / "profile")
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)

    app = create_app(service_region="me-south-1", strict_profile=profile)

    assert isinstance(app.state.audit_sink, PostgresAuditSink)


def test_strict_component_helper_cannot_construct_memory_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct strict helper use fails before silently constructing an in-memory authority."""
    from idis.services.runs.step_runtime import common as common_module

    _strict_env(monkeypatch)

    with pytest.raises(Exception) as exc_info:
        common_module._resolve_run_audit_sink(None)

    assert "AUDIT" in str(exc_info.value).upper()


def test_build_run_context_threads_same_sink_to_every_strict_run_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioral recorders prove the canonical context does not discard its supplied sink."""
    import idis.services.runs.step_runtime.analysis as analysis_module
    import idis.services.runs.step_runtime.deliverables as deliverables_module
    import idis.services.runs.step_runtime.enrichment as enrichment_module
    import idis.services.runs.step_runtime.evidence as evidence_module
    import idis.services.runs.step_runtime.snapshot as snapshot_module
    from idis.services.runs.steps import build_run_context

    monkeypatch.delenv("IDIS_OBJECT_STORE_BACKEND", raising=False)
    monkeypatch.delenv("IDIS_OBJECT_STORE_BASE_DIR", raising=False)
    calls: dict[str, dict[str, Any]] = {}

    def recorder(name: str) -> Callable[..., dict[str, Any]]:
        def record(**kwargs: Any) -> dict[str, Any]:
            calls[name] = kwargs
            return {}

        return record

    helper_by_context_field = {
        "extract_fn": "_run_snapshot_extraction",
        "graph_fn": "_run_full_graph_evidence",
        "enrich_fn": "_run_full_enrichment",
        "analysis_fn": "_run_full_analysis",
        "scoring_fn": "_run_full_scoring",
        "deliverables_fn": "_run_full_deliverables",
    }
    defining_module = {
        "_run_snapshot_extraction": snapshot_module,
        "_run_full_graph_evidence": evidence_module,
        "_run_full_enrichment": enrichment_module,
        "_run_full_analysis": analysis_module,
        "_run_full_scoring": analysis_module,
        "_run_full_deliverables": deliverables_module,
    }
    for context_field, helper_name in helper_by_context_field.items():
        monkeypatch.setattr(defining_module[helper_name], helper_name, recorder(context_field))

    sink = PostgresAuditSink()
    context = build_run_context(
        db_conn=None,
        tenant_id=_TENANT_ID,
        run_id="20000000-0000-4000-8000-000000000002",
        deal_id="30000000-0000-4000-8000-000000000002",
        mode="FULL",
        documents=[],
        audit_sink=sink,
        strict_live_extraction_required=True,
        strict_live_debate_backend_required=True,
    )

    for context_field in helper_by_context_field:
        helper = getattr(context, context_field)
        assert helper is not None
        helper(task2_probe=True)

    assert set(calls) == set(helper_by_context_field)
    for kwargs in calls.values():
        assert kwargs["audit_sink"] is sink


def test_graph_projection_factory_receives_strict_postgres_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph helper must not construct an unaudited GraphProjectionService."""
    import idis.persistence.graph_consistency as graph_module
    import idis.services.runs.step_runtime.evidence as evidence_module

    constructed_with: list[Any] = []

    class ProjectionService:
        def __init__(self, *, audit_sink: Any) -> None:
            constructed_with.append(audit_sink)

        def project_deal(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(status=graph_module.ProjectionStatus.SUCCESS)

    used: list[str] = []

    def _recording(name: str, value: Any) -> Callable[..., Any]:
        def fake(**_kwargs: Any) -> Any:
            used.append(name)
            return value

        return fake

    monkeypatch.setattr(graph_module, "GraphProjectionService", ProjectionService)
    monkeypatch.setattr(
        evidence_module,
        "_graph_calculations_by_claim",
        _recording("_graph_calculations_by_claim", {}),
    )
    monkeypatch.setattr(
        evidence_module,
        "_graph_transmission_nodes_by_claim",
        _recording("_graph_transmission_nodes_by_claim", {}),
    )
    monkeypatch.setattr(
        evidence_module,
        "_graph_defects_by_claim",
        _recording("_graph_defects_by_claim", {}),
    )
    monkeypatch.setattr(evidence_module, "_graph_claims", _recording("_graph_claims", []))
    monkeypatch.setattr(
        evidence_module,
        "_graph_evidence_by_claim",
        _recording("_graph_evidence_by_claim", {}),
    )
    sink = PostgresAuditSink()

    result = evidence_module._project_graph_evidence(
        tenant_id=_TENANT_ID,
        deal_id="30000000-0000-4000-8000-000000000002",
        documents=[],
        created_claim_ids=[],
        db_conn=None,
        audit_sink=sink,
    )

    assert result["status"] == "projected"
    assert constructed_with == [sink]
    assert used == [
        "_graph_calculations_by_claim",
        "_graph_transmission_nodes_by_claim",
        "_graph_defects_by_claim",
        "_graph_claims",
        "_graph_evidence_by_claim",
    ]


def test_prompt_governance_cannot_create_memory_sink_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict governance construction without an injected durable sink must fail closed."""
    from idis.services.prompts.versioning import PromptVersioningService

    _strict_env(monkeypatch)

    with pytest.raises(Exception) as exc_info:
        PromptVersioningService(registry=object(), tenant_id=_TENANT_ID)  # type: ignore[arg-type]

    assert "AUDIT" in str(exc_info.value).upper()
