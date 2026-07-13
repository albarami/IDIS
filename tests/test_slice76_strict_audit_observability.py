"""Slice76 Cluster 4: StepProvenance model and strict-readiness mapping.

A safe, typed provenance model plus a helper that maps existing strict readiness
truth (StrictComponentReadiness / StrictComponentInventory / env_sources) into
StepProvenance using classes/statuses only - never raw env values or secrets.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from idis.models.step_provenance import (
    ComponentMode,
    EnvSourceClass,
    OutputVisibilityStatus,
    RuntimeUseStatus,
    StepProvenance,
)
from idis.services.runs.strict_full_live import (
    StrictComponentInventory,
    StrictComponentReadiness,
    StrictComponentStatus,
    build_step_provenance,
)
from tests.abac_seed import seed_deal_access

ALL_ENV_SOURCE_CLASSES = {
    EnvSourceClass.PROCESS_ENV,
    EnvSourceClass.DOTENV,
    EnvSourceClass.MISSING,
    EnvSourceClass.UNKNOWN,
}


@pytest.fixture(autouse=True)
def _reset_in_memory_stores() -> Any:
    """Isolate the module-global in-memory run/step stores from other test files."""
    from idis.persistence.repositories.run_steps import clear_run_steps_store
    from idis.persistence.repositories.runs import clear_in_memory_runs_store

    clear_in_memory_runs_store()
    clear_run_steps_store()
    yield
    clear_in_memory_runs_store()
    clear_run_steps_store()


def _readiness(
    *,
    status: StrictComponentStatus,
    required_env_vars: list[str] | None = None,
) -> StrictComponentReadiness:
    return StrictComponentReadiness(
        component_name="live_llm_model_clients",
        status=status,
        blocker_message="",
        required_env_vars=required_env_vars or [],
        required_services=[],
        evidence="component evidence",
        may_proceed=status == StrictComponentStatus.LIVE_WIRED_AND_USED,
    )


def _inventory(*, health_check_status: str, output_visible: bool) -> StrictComponentInventory:
    return StrictComponentInventory(
        component_name="live_llm_model_clients",
        exists_in_code=True,
        full_wired=output_visible,
        config_present=True,
        health_check_status=health_check_status,
        output_visible=output_visible,
        blocker="",
        implementation_slice="slice76",
        evidence_files=[],
    )


def test_step_provenance_model_roundtrip() -> None:
    """StepProvenance validates and serializes all five required dimensions."""
    provenance = StepProvenance(
        component_name="live_llm_model_clients",
        component_mode=ComponentMode.LIVE_WIRED_AND_USED,
        env_source_class=EnvSourceClass.PROCESS_ENV,
        health_status="contract_only",
        runtime_use_status=RuntimeUseStatus.USED,
        output_visibility_status=OutputVisibilityStatus.VISIBLE,
    )

    dumped = provenance.model_dump(mode="json")
    assert dumped["component_mode"] == "live-wired-and-used"
    assert dumped["env_source_class"] == "process_env"
    assert dumped["health_status"] == "contract_only"
    assert dumped["runtime_use_status"] == "used"
    assert dumped["output_visibility_status"] == "visible"

    restored = StepProvenance.model_validate(dumped)
    assert restored == provenance


@pytest.mark.parametrize(
    "secret_like",
    [
        "postgresql://user:secret@localhost:5432/idis",
        "/var/secrets/key.pem",
        "deals/object-key/raw.bin",
        "sk-ant-1234567890",
        "AWS_SECRET_ACCESS_KEY=abcd",
    ],
)
def test_step_provenance_rejects_raw_secret_like_values(secret_like: str) -> None:
    """Secret-like raw values must be rejected for safe string dimensions."""
    with pytest.raises(ValidationError):
        StepProvenance(
            component_name="live_llm_model_clients",
            component_mode=ComponentMode.LIVE_WIRED_AND_USED,
            env_source_class=EnvSourceClass.PROCESS_ENV,
            health_status=secret_like,
            runtime_use_status=RuntimeUseStatus.USED,
            output_visibility_status=OutputVisibilityStatus.VISIBLE,
        )


def test_strict_readiness_component_maps_to_step_provenance() -> None:
    """StrictComponentReadiness/Inventory/env_sources map into StepProvenance."""
    readiness = _readiness(
        status=StrictComponentStatus.LIVE_WIRED_AND_USED,
        required_env_vars=["IDIS_LLM_API_KEY"],
    )
    inventory = _inventory(health_check_status="contract_only", output_visible=True)
    env_sources = {"IDIS_LLM_API_KEY": "process"}

    provenance = build_step_provenance(
        readiness=readiness,
        inventory=inventory,
        env_sources=env_sources,
    )

    assert provenance.component_name == "live_llm_model_clients"
    assert provenance.component_mode == ComponentMode.LIVE_WIRED_AND_USED
    assert provenance.env_source_class == EnvSourceClass.PROCESS_ENV
    assert provenance.health_status == "contract_only"
    assert provenance.runtime_use_status == RuntimeUseStatus.USED
    assert provenance.output_visibility_status == OutputVisibilityStatus.VISIBLE


def test_strict_readiness_mapping_uses_classes_not_values() -> None:
    """env_source_class must be a class/status, never a raw env value."""
    readiness = _readiness(
        status=StrictComponentStatus.MISSING_CREDENTIALS,
        required_env_vars=["IDIS_LLM_API_KEY"],
    )
    inventory = _inventory(health_check_status="missing_config", output_visible=False)
    # A raw secret value should never propagate; only the source class is consulted.
    env_sources = {"IDIS_LLM_API_KEY": "missing"}

    provenance = build_step_provenance(
        readiness=readiness,
        inventory=inventory,
        env_sources=env_sources,
    )

    assert provenance.env_source_class in ALL_ENV_SOURCE_CLASSES
    assert provenance.env_source_class == EnvSourceClass.MISSING
    assert provenance.runtime_use_status == RuntimeUseStatus.NOT_USED
    assert provenance.output_visibility_status == OutputVisibilityStatus.NOT_VISIBLE


# --- Cluster 5: wire StepProvenance into strict blocker run-step evidence ---

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SECRET_DSN = "postgresql://user:secret@localhost:5432/idis"
SECRET_PATH = "/var/secrets/anthropic.key"

_PROVENANCE_DIMENSIONS = {
    "component_mode",
    "env_source_class",
    "health_status",
    "runtime_use_status",
    "output_visibility_status",
}


def _blocking_report() -> Any:
    """Build a real StrictFullLiveReadinessReport with one blocking component.

    The readiness blocker_message/evidence intentionally embed secret-like text to
    prove the ledger never stores raw blocker text.
    """
    from idis.services.runs.strict_full_live import StrictFullLiveReadinessReport

    return StrictFullLiveReadinessReport(
        required=True,
        may_proceed=False,
        blocker_count=1,
        blocking_components=["Anthropic extraction"],
        components=[
            StrictComponentReadiness(
                component_name="Anthropic extraction",
                status=StrictComponentStatus.MISSING_CREDENTIALS,
                blocker_message=f"missing credential at {SECRET_PATH}",
                required_env_vars=["ANTHROPIC_API_KEY"],
                required_services=[],
                evidence=f"see {SECRET_DSN}",
                may_proceed=False,
            )
        ],
        component_inventory=[
            StrictComponentInventory(
                component_name="Anthropic extraction",
                exists_in_code=True,
                full_wired=False,
                config_present=False,
                health_check_status="missing_config",
                output_visible=False,
                blocker=f"blocked at {SECRET_PATH}",
                implementation_slice="slice55",
                evidence_files=[],
            )
        ],
        env_sources={"ANTHROPIC_API_KEY": "missing"},
    )


def test_worker_strict_preflight_block_step_has_safe_provenance() -> None:
    """Worker strict preflight block must write safe StepProvenance evidence."""
    from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
    from idis.pipeline.worker import PipelineWorker

    class _RunsRepo:
        def try_complete_active(self, run_id: str, *, status: str, finished_at: str) -> bool:
            return True

    steps_repo = InMemoryRunStepsRepository(TENANT_A)
    worker = PipelineWorker(poll_interval=0, tenant_ids=[TENANT_A])

    with (
        patch("idis.pipeline.worker.get_runs_repository", return_value=_RunsRepo()),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=steps_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        worker._persist_worker_preflight_block(
            conn=MagicMock(),
            tenant_id=TENANT_A,
            run_id="run-1",
            reason_code="STRICT_FULL_LIVE_BLOCKED",
            message="Strict full-live preflight blocked queued FULL run before execution",
            strict_report=_blocking_report(),
        )

    steps = steps_repo.get_by_run_id("run-1")
    assert steps
    provenance_items = steps[-1].result_summary["provenance_items"]
    assert provenance_items
    for item in provenance_items:
        assert set(item) >= _PROVENANCE_DIMENSIONS

    encoded = json.dumps(steps[-1].model_dump(mode="json"), sort_keys=True)
    assert SECRET_DSN not in encoded
    assert SECRET_PATH not in encoded
    assert "://" not in encoded


def test_retry_strict_block_lifecycle_step_has_safe_provenance() -> None:
    """Retry/resume strict block lifecycle evidence must include safe provenance."""
    from idis.models.run_step import StepName
    from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
    from idis.services.runs.lifecycle import RunLifecycleService
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    provenance_items = [
        p.model_dump(mode="json") for p in build_blocking_step_provenance(_blocking_report())
    ]
    assert provenance_items

    class _RunsRepo:
        def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
            return None

        def try_requeue_failed(self, run_id: str) -> bool:
            return True

        def try_cancel_active(self, run_id: str) -> bool:
            return True

    steps_repo = InMemoryRunStepsRepository(TENANT_A)
    lifecycle = RunLifecycleService(runs_repo=_RunsRepo(), run_steps_repo=steps_repo)

    lifecycle.persist_failed_block(
        run_id="run-1",
        tenant_id=TENANT_A,
        reason_code="STRICT_FULL_LIVE_BLOCKED",
        message="Strict full live retry admission blocked",
        provenance_items=provenance_items,
    )

    step = steps_repo.get_step("run-1", StepName.RUN_LIFECYCLE)
    assert step is not None
    # Original lifecycle behavior intact.
    assert step.error_code == "STRICT_FULL_LIVE_BLOCKED"
    assert step.result_summary["reason_code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert len(step.result_summary["lifecycle_events"]) == 1
    # Safe provenance attached.
    attached = step.result_summary["provenance_items"]
    assert attached
    for item in attached:
        assert set(item) >= _PROVENANCE_DIMENSIONS

    encoded = json.dumps(step.model_dump(mode="json"), sort_keys=True)
    assert SECRET_DSN not in encoded
    assert SECRET_PATH not in encoded


def test_strict_blocker_operator_evidence_is_safe_and_actionable() -> None:
    """Strict blocker evidence must be actionable (codes/classes) and never leak raw text."""
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    items = [p.model_dump(mode="json") for p in build_blocking_step_provenance(_blocking_report())]
    assert items

    item = items[0]
    # Actionable: stable component label + safe status classes.
    assert item["component_name"] == "Anthropic extraction"
    assert item["component_mode"] == "missing-credentials"
    assert item["env_source_class"] == "missing"
    assert item["health_status"] == "missing_config"
    assert item["runtime_use_status"] == "not_used"
    assert item["output_visibility_status"] == "not_visible"

    # Safe: no raw blocker text, paths, DSNs, or env values leak through.
    encoded = json.dumps(items, sort_keys=True)
    assert SECRET_DSN not in encoded
    assert SECRET_PATH not in encoded
    assert "://" not in encoded
    assert "ANTHROPIC_API_KEY" not in encoded


# --- Cluster 6: strict-mode audit sink policy ---

_DEAL_BODY = {"name": "Strict Deal", "company_name": "Acme Corp"}


def _api_keys_json(tenant_id: str) -> str:
    return json.dumps(
        {
            "test-api-key-c6": {
                "tenant_id": tenant_id,
                "actor_id": f"actor-{tenant_id[:8]}",
                "name": "Tenant C6",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": ["ANALYST"],
            }
        }
    )


def test_strict_mode_rejects_jsonl_only_audit_sink(tmp_path, monkeypatch) -> None:
    """Strict mode with only a JSONL file sink and no Postgres must fail closed."""
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.sink import JsonlFileAuditSink

    tenant_id = str(uuid.uuid4())
    audit_log_path = tmp_path / "strict_jsonl.jsonl"
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json(tenant_id))
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    app = create_app(audit_sink=JsonlFileAuditSink(str(audit_log_path)), service_region="us-east-1")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": "test-api-key-c6", "Content-Type": "application/json"},
        content=json.dumps(_DEAL_BODY),
    )

    assert response.status_code == 500
    assert response.json()["code"] == "AUDIT_EMIT_FAILED"
    if audit_log_path.exists():
        assert audit_log_path.read_text().strip() == ""


def test_non_strict_mode_still_allows_jsonl_audit_sink(tmp_path, monkeypatch) -> None:
    """Non-strict mode preserves existing JSONL audit emission."""
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.sink import JsonlFileAuditSink

    tenant_id = str(uuid.uuid4())
    audit_log_path = tmp_path / "nonstrict_jsonl.jsonl"
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json(tenant_id))
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)

    app = create_app(audit_sink=JsonlFileAuditSink(str(audit_log_path)), service_region="us-east-1")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": "test-api-key-c6", "Content-Type": "application/json"},
        content=json.dumps(_DEAL_BODY),
    )

    assert response.status_code == 201, response.text
    assert audit_log_path.exists()
    assert audit_log_path.read_text().strip() != ""


def test_strict_mode_uses_postgres_audit_sink_when_available(tmp_path, monkeypatch) -> None:
    """Strict mode with a durable Postgres sink + db_conn emits through the durable sink."""
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.sink import JsonlFileAuditSink

    tenant_id = str(uuid.uuid4())
    audit_log_path = tmp_path / "strict_durable.jsonl"
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json(tenant_id))
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    postgres_sink = MagicMock()
    app = create_app(
        audit_sink=JsonlFileAuditSink(str(audit_log_path)),
        postgres_audit_sink=postgres_sink,
        service_region="us-east-1",
    )

    @app.middleware("http")
    async def _inject_db_conn(request: Any, call_next: Any) -> Any:
        request.state.db_conn = MagicMock()
        return await call_next(request)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": "test-api-key-c6", "Content-Type": "application/json"},
        content=json.dumps(_DEAL_BODY),
    )

    assert response.status_code == 201, response.text
    assert postgres_sink.emit_in_tx.called
    if audit_log_path.exists():
        assert audit_log_path.read_text().strip() == ""


def test_worker_strict_audit_sink_is_fail_closed(monkeypatch) -> None:
    """The worker audit sink must fail closed when PostgresAuditSink is unavailable."""
    from idis.pipeline.worker import WorkerAuditConfigurationError, _default_worker_audit_sink

    with (
        patch("idis.audit.postgres_sink.PostgresAuditSink", side_effect=RuntimeError("no pg")),
        pytest.raises(WorkerAuditConfigurationError),
    ):
        _default_worker_audit_sink()


# --- Cluster 7: API start-run strict block details are operator-safe ---

_SAFE_BLOCK_DETAIL_KEYS = {
    "may_proceed",
    "blocker_count",
    "blocking_components",
    "provenance_items",
}


def _cluster7_preflight_doc(*, deal_id: str, tenant_id: str) -> dict[str, Any]:
    """Minimal parsed preflight doc so a FULL run reaches the strict preflight block."""
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "document_id": "doc-c7",
        "doc_id": "artifact-doc-c7",
        "doc_type": "DOCX",
        "parse_status": "PARSED",
        "document_name": "doc-c7.docx",
        "sha256": "a" * 64,
        "uri": "deals/doc-c7.docx",
        "metadata": {},
        "source_metadata": {},
        "spans": [
            {
                "span_id": "span-c7",
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "document_id": "doc-c7",
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "Cluster7 revenue sentence",
                "content_hash": "b" * 64,
            }
        ],
    }


def _post_strict_blocked_full_run(monkeypatch: Any, *, report: Any = None) -> Any:
    """POST a FULL run blocked by strict preflight; return the API response.

    The strict admission report is patched to ``report`` (default: a blocking
    report whose evidence/blocker text embeds SECRET_DSN/SECRET_PATH, so leakage
    through the 409 ``details`` payload is provable). Pass a real readiness
    report to exercise the production strict-block shape end to end.
    """
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.sink import InMemoryAuditSink

    tenant_id = str(uuid.uuid4())
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json(tenant_id))

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="us-east-1")
    app.state.deal_documents = {}
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-IDIS-API-Key": "test-api-key-c6", "Content-Type": "application/json"}

    create_resp = client.post("/v1/deals", headers=headers, content=json.dumps(_DEAL_BODY))
    assert create_resp.status_code == 201, create_resp.text
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(tenant_id, deal_id, f"actor-{tenant_id[:8]}")
    app.state.deal_documents[deal_id] = [
        _cluster7_preflight_doc(deal_id=deal_id, tenant_id=tenant_id)
    ]

    with patch(
        "idis.api.routes.runs.build_strict_full_live_admission_report",
        return_value=report if report is not None else _blocking_report(),
    ):
        return client.post(
            f"/v1/deals/{deal_id}/runs",
            headers=headers,
            content=json.dumps({"mode": "FULL"}),
        )


def test_api_start_run_strict_block_details_are_operator_safe(monkeypatch: Any) -> None:
    """The strict-block 409 details must carry no paths, DSNs, env names, or raw text."""
    response = _post_strict_blocked_full_run(monkeypatch)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STRICT_FULL_LIVE_BLOCKED"

    # Only the safe envelope and safe summary keys are present.
    assert set(body["details"]) == {"strict_full_live"}
    details = body["details"]["strict_full_live"]
    assert set(details) == _SAFE_BLOCK_DETAIL_KEYS

    encoded = json.dumps(body, sort_keys=True)
    # Raw report fields (evidence/blocker_message/blocker/implementation_slice) are gone.
    assert SECRET_DSN not in encoded
    assert SECRET_PATH not in encoded
    assert "://" not in encoded
    assert "slice55" not in encoded
    # Required env var names are not surfaced (prefer classes/statuses over names).
    assert "ANTHROPIC_API_KEY" not in encoded
    # No unsafe raw-field tokens leak through nested structures. (Distinctive compound
    # tokens only - bare "blocker" would false-match the safe "blocker_count" key.)
    for unsafe_key in ("evidence_files", "blocker_message", "implementation_slice", "env_sources"):
        assert unsafe_key not in encoded


def test_api_start_run_strict_block_preserves_structured_blockers(monkeypatch: Any) -> None:
    """The safe details must still preserve blocker count, names, and safe provenance."""
    response = _post_strict_blocked_full_run(monkeypatch)

    assert response.status_code == 409
    details = response.json()["details"]["strict_full_live"]

    assert details["may_proceed"] is False
    assert details["blocker_count"] == 1
    assert details["blocking_components"] == ["Anthropic extraction"]

    provenance_items = details["provenance_items"]
    assert provenance_items
    for item in provenance_items:
        assert set(item) >= _PROVENANCE_DIMENSIONS
    # Safe classes/statuses for the blocking component.
    item = provenance_items[0]
    assert item["component_name"] == "Anthropic extraction"
    assert item["component_mode"] == "missing-credentials"
    assert item["env_source_class"] == "missing"
    assert item["health_status"] == "missing_config"
    assert item["runtime_use_status"] == "not_used"
    assert item["output_visibility_status"] == "not_visible"


def test_api_start_run_strict_block_does_not_create_run_or_ledger(monkeypatch: Any) -> None:
    """A strict-blocked start-run must not persist a run row or step ledger."""
    from idis.persistence.repositories.run_steps import _run_steps_store
    from idis.persistence.repositories.runs import _in_memory_runs_store

    response = _post_strict_blocked_full_run(monkeypatch)

    assert response.status_code == 409
    assert "run_id" not in response.json()
    # Block happens before run creation, so neither store is written.
    assert _in_memory_runs_store == {}
    assert _run_steps_store == {}


# --- Cluster 8: real strict-report blockers carry safe non-empty provenance ---


def _real_blocking_report() -> Any:
    """A real StrictFullLiveReadinessReport blocked by an empty strict env.

    Built hermetically from ``env={}`` (no object-store probing) so its blocking
    components and evidence (real source paths) are deterministic.
    """
    from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

    return build_strict_full_live_readiness_report(env={}, probe_object_store=False)


def test_real_strict_report_blocker_provenance_is_non_empty() -> None:
    """A real strict report's blocking components must carry provenance, not [] ."""
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    report = _real_blocking_report()
    assert report.may_proceed is False
    assert report.blocking_components

    items = build_blocking_step_provenance(report)
    assert items, "real strict blockers must carry provenance, not an empty list"


def test_real_strict_report_provenance_count_matches_blocking_components() -> None:
    """Every blocking component is represented exactly once (none silently dropped)."""
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    report = _real_blocking_report()
    items = build_blocking_step_provenance(report)

    assert len(items) == len(report.blocking_components)
    assert {item.component_name for item in items} == set(report.blocking_components)


def test_api_start_run_strict_block_real_report_has_non_empty_provenance(monkeypatch: Any) -> None:
    """The API 409 details for a real strict block must include safe provenance_items."""
    response = _post_strict_blocked_full_run(monkeypatch, report=_real_blocking_report())

    assert response.status_code == 409
    details = response.json()["details"]["strict_full_live"]
    assert details["blocker_count"] > 0
    assert details["blocking_components"]
    assert details["provenance_items"], "real strict block must surface non-empty provenance"
    assert len(details["provenance_items"]) == len(details["blocking_components"])


def test_real_strict_report_provenance_has_all_five_dimensions() -> None:
    """Each provenance item carries the five dimensions with safe closed values."""
    import re

    from idis.models.step_provenance import (
        ComponentMode,
        EnvSourceClass,
        OutputVisibilityStatus,
        RuntimeUseStatus,
    )
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    safe_health = re.compile(r"^[a-z][a-z0-9_]*$")
    valid_modes = {m.value for m in ComponentMode}
    valid_env = {e.value for e in EnvSourceClass}
    valid_runtime = {r.value for r in RuntimeUseStatus}
    valid_visibility = {o.value for o in OutputVisibilityStatus}

    items = [
        p.model_dump(mode="json") for p in build_blocking_step_provenance(_real_blocking_report())
    ]
    assert items
    for item in items:
        assert set(item) >= _PROVENANCE_DIMENSIONS
        assert item["component_mode"] in valid_modes
        assert item["env_source_class"] in valid_env
        assert item["runtime_use_status"] in valid_runtime
        assert item["output_visibility_status"] in valid_visibility
        assert safe_health.match(item["health_status"])


def test_real_strict_report_provenance_is_leakage_safe() -> None:
    """Real-report provenance must not leak evidence paths, env names, or raw fields."""
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    items = [
        p.model_dump(mode="json") for p in build_blocking_step_provenance(_real_blocking_report())
    ]
    assert items
    encoded = json.dumps(items, sort_keys=True)
    for forbidden in (
        "://",
        "src/idis",
        "src\\idis",
        ".py",
        "C:\\",
        "blocker_message",
        "evidence_files",
        "implementation_slice",
        "env_sources",
        "ANTHROPIC_API_KEY",
    ):
        assert forbidden not in encoded
