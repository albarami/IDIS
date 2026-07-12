"""Slice97 Task 4 — lifecycle webhook wiring + A1 audit-safety.

Drives the REAL shared execution path (``RunExecutionService.execute``, used by both the API and the
worker) and asserts the best-effort webhook emit is wired at each run lifecycle moment (claimed,
completed, failed, cancelled). The mandatory A1 test forces the webhook path to raise and proves the
run still completes AND its audit signal is committed. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.observability.runtime_signals import RUN_CLAIMED
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository, _run_steps_store
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    _in_memory_runs_store,
    clear_in_memory_runs_store,
)
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext
from idis.services.webhooks import lifecycle as webhook_lifecycle

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_RUN = "99999999-9999-9999-9999-999999999999"

_PRE_EXTRACT_STEPS = [
    StepName.DATA_ROOM_INVENTORY_PACKAGE,
    StepName.DATA_ROOM_INGESTION_HANDOFF,
    StepName.INGEST_CHECK,
    StepName.DOCUMENT_PREFLIGHT,
    StepName.METHODOLOGY_COVERAGE_INIT,
]
_DOCUMENTS = [
    {
        "document_id": "doc-001",
        "doc_type": "PDF",
        "document_name": "test.pdf",
        "spans": [
            {
                "span_id": "s1",
                "text_excerpt": "Revenue was $5M.",
                "locator": {"page": 1},
                "span_type": "PAGE_TEXT",
            }
        ],
    }
]


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    clear_in_memory_runs_store()
    _run_steps_store.clear()
    yield
    clear_in_memory_runs_store()
    _run_steps_store.clear()


def _steps_repo() -> InMemoryRunStepsRepository:
    repo = InMemoryRunStepsRepository(_TENANT)
    for order, name in enumerate(_PRE_EXTRACT_STEPS):
        repo.create(
            RunStep(
                step_id=f"00000000-0000-0000-0000-0000000000{order:02d}",
                run_id=_RUN,
                tenant_id=_TENANT,
                step_name=name,
                step_order=order,
                status=StepStatus.COMPLETED,
                started_at="2026-05-27T00:00:00Z",
                finished_at="2026-05-27T00:00:01Z",
                result_summary={},
            )
        )
    return repo


def _extract_ok(**_: Any) -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "created_claim_ids": ["c1"],
        "chunk_count": 1,
        "unique_claim_count": 1,
        "conflict_count": 0,
    }


def _ctx(extract_fn: Any) -> RunContext:
    def grade(*, run_id: str, **_: Any) -> dict[str, Any]:
        return {"graded_count": 1, "failed_count": 0, "total_defects": 0, "all_failed": False}

    def calc(*, run_id: str, **_: Any) -> dict[str, Any]:
        return {"calc_ids": ["calc1"], "reproducibility_hashes": ["h1"]}

    return RunContext(
        run_id=_RUN,
        tenant_id=_TENANT,
        deal_id=_DEAL,
        mode="SNAPSHOT",
        documents=_DOCUMENTS,
        extract_fn=extract_fn,
        grade_fn=grade,
        calc_fn=calc,
    )


def _run(extract_fn: Any = _extract_ok, sink: InMemoryAuditSink | None = None) -> Any:
    sink = sink or InMemoryAuditSink()
    runs_repo = InMemoryRunsRepository(_TENANT)
    runs_repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")
    service = RunExecutionService(
        audit_sink=sink, runs_repo=runs_repo, run_steps_repo=_steps_repo()
    )
    return service.execute(_ctx(extract_fn))


class _Spy:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def __call__(
        self,
        *,
        tenant_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        data: Any = None,
        conn: Any = None,
    ) -> None:
        self.events.append((event_type, tenant_id, resource_id))


def test_run_claimed_and_completed_emit_via_execution_path(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _Spy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)
    result = _run()
    assert result.claimed is True and result.status == "SUCCEEDED"
    types = [e[0] for e in spy.events]
    assert webhook_lifecycle.RUN_CLAIMED in types
    assert webhook_lifecycle.RUN_COMPLETED in types
    # every emit is tenant-scoped and carries the run id
    assert all(e[1] == _TENANT and e[2] == _RUN for e in spy.events)


def test_run_failed_emits_via_execution_path(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _Spy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)

    def _extract_fail(**_: Any) -> dict[str, Any]:
        raise RuntimeError("extract boom")

    result = _run(extract_fn=_extract_fail)  # a failing step fails the run (terminal FAILED)
    assert result.claimed is True and result.status == "FAILED"
    types = [e[0] for e in spy.events]
    assert webhook_lifecycle.RUN_CLAIMED in types
    assert webhook_lifecycle.RUN_FAILED in types


def test_run_cancelled_emits_via_execution_path(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _Spy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)

    def _extract_cancel(**_: Any) -> dict[str, Any]:
        _in_memory_runs_store[_RUN]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return _extract_ok()

    result = _run(extract_fn=_extract_cancel)
    assert result.status == "CANCELLED"
    assert webhook_lifecycle.RUN_CANCELLED in [e[0] for e in spy.events]


def test_a1_raising_webhook_machinery_never_breaks_run_or_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A1 (mandatory): force the webhook path to raise. The run must still complete AND its audit
    # signal must still be committed. notify_webhook_lifecycle runs for real (not spied) so its
    # best-effort guard is genuinely exercised.
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("webhook machinery down")

    monkeypatch.setattr(webhook_lifecycle, "get_webhook_service", _boom)
    monkeypatch.setattr(webhook_lifecycle, "default_webhook_outbox", _boom)

    sink = InMemoryAuditSink()
    result = _run(sink=sink)

    assert result.claimed is True and result.status == "SUCCEEDED"  # mutation still succeeded
    assert any(e.get("event_type") == RUN_CLAIMED for e in sink.events)  # audit still committed


# --- deliverable produced / failed via the real _run_full_deliverables generation path ---


class _ConnSpy(_Spy):
    """Spy that also records the forwarded conn and resource_type per call."""

    def __init__(self) -> None:
        super().__init__()
        self.conns: list[Any] = []
        self.resource_types: list[str] = []

    def __call__(self, **kwargs: Any) -> None:
        self.conns.append(kwargs.get("conn"))
        self.resource_types.append(kwargs.get("resource_type", ""))
        super().__call__(**kwargs)


def test_deliverable_produced_emits_via_run_full_deliverables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.api.routes import runs as runs_route
    from tests.test_deliverables_generator import _make_bundle, _make_context, _make_scorecard

    spy = _ConnSpy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)
    sentinel_conn = object()

    summary = runs_route._run_full_deliverables(
        run_id=_RUN,
        tenant_id=_TENANT,
        deal_id=_DEAL,
        analysis_bundle=_make_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        db_conn=sentinel_conn,  # generator path HAS a conn -> must be forwarded
    )
    assert summary["deliverable_count"] >= 4  # the real bundle was generated
    assert (webhook_lifecycle.DELIVERABLE_PRODUCED, _TENANT, _RUN) in spy.events
    produced_idx = spy.events.index((webhook_lifecycle.DELIVERABLE_PRODUCED, _TENANT, _RUN))
    assert spy.resource_types[produced_idx] == "run"
    assert spy.conns[produced_idx] is sentinel_conn  # caller db conn forwarded


def test_deliverable_failed_emits_via_run_full_deliverables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.api.routes import runs as runs_route

    spy = _ConnSpy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)
    sentinel_conn = object()

    def _generate_boom(self: Any, **_: Any) -> Any:
        raise RuntimeError("generation boom")

    monkeypatch.setattr(
        "idis.deliverables.generator.DeliverablesGenerator.generate", _generate_boom
    )
    with pytest.raises(RuntimeError, match="generation boom"):  # failure still propagates
        runs_route._run_full_deliverables(
            run_id=_RUN,
            tenant_id=_TENANT,
            deal_id=_DEAL,
            analysis_bundle=object(),
            analysis_context=object(),
            scorecard=object(),
            db_conn=sentinel_conn,
        )
    assert (webhook_lifecycle.DELIVERABLE_FAILED, _TENANT, _RUN) in spy.events
    failed_idx = spy.events.index((webhook_lifecycle.DELIVERABLE_FAILED, _TENANT, _RUN))
    assert spy.resource_types[failed_idx] == "run"
    assert spy.conns[failed_idx] is sentinel_conn  # caller db conn forwarded


# --- human gate action + data room package via the real route paths ---

_GATE_TENANT = "11111111-1111-1111-1111-111111111111"
_API_KEY = "slice97-wiring-key"


def _api_keys_env() -> str:
    import json

    return json.dumps(
        {
            _API_KEY: {
                "tenant_id": _GATE_TENANT,
                "actor_id": "actor-97",
                "name": "Slice97 Wiring",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": ["ANALYST"],
            }
        }
    )


def test_human_gate_action_emits_via_real_route(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid as uuid_mod

    from fastapi.testclient import TestClient

    from idis.api.auth import IDIS_API_KEYS_ENV
    from idis.api.main import create_app
    from idis.api.routes.deals import clear_deals_store
    from idis.api.routes.human_gates import clear_human_gates_store, create_test_gate

    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_env())
    clear_deals_store()
    clear_human_gates_store()
    spy = _ConnSpy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="us-east-1")
    client = TestClient(app)
    deal = client.post(
        "/v1/deals",
        json={"name": "Gate Deal", "company_name": "Acme"},
        headers={"X-IDIS-API-Key": _API_KEY},
    )
    assert deal.status_code == 201
    gate_id = str(uuid_mod.uuid4())
    create_test_gate(
        gate_id=gate_id,
        tenant_id=_GATE_TENANT,
        deal_id=deal.json()["deal_id"],
        gate_type="CLAIM_VERIFICATION",
    )

    resp = client.post(
        f"/v1/deals/{deal.json()['deal_id']}/human-gates",
        json={"gate_id": gate_id, "action": "APPROVE", "notes": "ok"},
        headers={"X-IDIS-API-Key": _API_KEY},
    )
    assert resp.status_code == 201  # the real mutation succeeded

    key = (webhook_lifecycle.HUMAN_GATE_ACTION_SUBMITTED, _GATE_TENANT, gate_id)
    assert key in spy.events  # event_type + tenant + resource_id (the gate)
    idx = spy.events.index(key)
    assert spy.resource_types[idx] == "human_gate"
    # in the in-memory stack request.state.db_conn is absent -> the route forwards None
    assert spy.conns[idx] is None


def test_data_room_package_created_emits_via_real_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from idis.api.auth import IDIS_API_KEYS_ENV
    from idis.api.main import create_app
    from idis.api.routes.deals import clear_deals_store
    from tests.test_slice77_data_room_package import DOC1, _corpus_doc

    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_env())
    clear_deals_store()
    spy = _ConnSpy()
    monkeypatch.setattr(webhook_lifecycle, "notify_webhook_lifecycle", spy)

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="us-east-1")
    app.state.deal_documents = {}
    client = TestClient(app)
    deal = client.post(
        "/v1/deals",
        json={"name": "DR Deal", "company_name": "Acme"},
        headers={"X-IDIS-API-Key": _API_KEY},
    )
    assert deal.status_code == 201
    deal_id = deal.json()["deal_id"]
    app.state.deal_documents[deal_id] = [_corpus_doc(DOC1)]

    resp = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        json={"document_ids": [DOC1]},
        headers={"X-IDIS-API-Key": _API_KEY},
    )
    assert resp.status_code == 201, resp.text  # the real mutation succeeded
    package_id = resp.json()["package_id"]

    key = (webhook_lifecycle.DATA_ROOM_PACKAGE_CREATED, _GATE_TENANT, package_id)
    assert key in spy.events  # event_type + tenant + resource_id (the package)
    idx = spy.events.index(key)
    assert spy.resource_types[idx] == "data_room_package"
    # in the in-memory stack request.state.db_conn is absent -> the route forwards None
    assert spy.conns[idx] is None
