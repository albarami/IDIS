"""Slice97 acceptance capstone (Task 7).

Composes the landed webhook controls on the REAL path and locks the docs/CI reconciliation:

- E2E: subscribe -> real lifecycle event -> safe outbox enqueue -> dispatcher -> SIGNED delivery
  (signature verified) -> ``webhook.delivery.succeeded`` audit (fully validated).
- A1: throwing webhook machinery never breaks the mutation or its required audit signal.
- A2: no private content / paths / URLs / secrets / headers leak into the outbox row, the delivered
  body, or the delivery audit payload.
- Pins: architecture doc present, traceability matrix WH-001 reconciled to delivered (tied to
  ``webhook.delivery.*``), plan status reconciled, and CI's postgres-integration job runs BOTH new
  Slice97 Postgres webhook tests. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.observability.runtime_signals import RUN_CLAIMED
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository, _run_steps_store
from idis.persistence.repositories.runs import InMemoryRunsRepository, clear_in_memory_runs_store
from idis.persistence.repositories.webhook_outbox import InMemoryWebhookOutboxRepository
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext
from idis.services.webhooks import lifecycle as webhook_lifecycle
from idis.services.webhooks.dispatcher import WebhookDispatcher
from idis.services.webhooks.service import WebhookSubscription
from idis.services.webhooks.signing import verify_webhook_signature
from idis.validators.audit_event_validator import validate_audit_event

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_RUN = "99999999-9999-9999-9999-999999999999"
_URL = "https://example.test/hook"
_SECRET = "s97-capstone-secret-XYZ"
_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)

_ARCH_DOC = Path("docs/architecture/slice97_webhooks_lifecycle.md")
_MATRIX = Path("docs/11_IDIS_Traceability_Matrix_v6_3.md")
_PLAN = Path("docs/plans/2026-07-10-slice97-webhooks-lifecycle-events.md")
_CI = Path(".github/workflows/ci.yml")

_FORBIDDEN_EVERYWHERE = (
    _SECRET.lower(),
    "local_path",
    "/var/",
    "transcript",
    "x-idis-webhook-signature",
)


# --- shared real-path scaffolding (SNAPSHOT run through RunExecutionService) ---


@pytest.fixture(autouse=True)
def _clean_stores() -> Iterator[None]:
    clear_in_memory_runs_store()
    _run_steps_store.clear()
    yield
    clear_in_memory_runs_store()
    _run_steps_store.clear()


def _steps_repo() -> InMemoryRunStepsRepository:
    repo = InMemoryRunStepsRepository(_TENANT)
    pre = [
        StepName.DATA_ROOM_INVENTORY_PACKAGE,
        StepName.DATA_ROOM_INGESTION_HANDOFF,
        StepName.INGEST_CHECK,
        StepName.DOCUMENT_PREFLIGHT,
        StepName.METHODOLOGY_COVERAGE_INIT,
    ]
    for order, name in enumerate(pre):
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


def _ctx() -> RunContext:
    def extract(**_: Any) -> dict[str, Any]:
        return {
            "status": "COMPLETED",
            "created_claim_ids": ["c1"],
            "chunk_count": 1,
            "unique_claim_count": 1,
            "conflict_count": 0,
        }

    def grade(*, run_id: str, **_: Any) -> dict[str, Any]:
        return {"graded_count": 1, "failed_count": 0, "total_defects": 0, "all_failed": False}

    def calc(*, run_id: str, **_: Any) -> dict[str, Any]:
        return {"calc_ids": ["calc1"], "reproducibility_hashes": ["h1"]}

    return RunContext(
        run_id=_RUN,
        tenant_id=_TENANT,
        deal_id=_DEAL,
        mode="SNAPSHOT",
        documents=[
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
        ],
        extract_fn=extract,
        grade_fn=grade,
        calc_fn=calc,
    )


def _execute(sink: InMemoryAuditSink) -> Any:
    runs_repo = InMemoryRunsRepository(_TENANT)
    runs_repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")
    service = RunExecutionService(
        audit_sink=sink, runs_repo=runs_repo, run_steps_repo=_steps_repo()
    )
    return service.execute(_ctx())


class _Lister:
    """Subscription store stand-in ('subscribe' step): one active subscription for the tenant."""

    def __init__(self, events: list[str]) -> None:
        self._sub = WebhookSubscription(
            webhook_id=str(uuid.uuid4()),
            tenant_id=_TENANT,
            url=_URL,
            events=events,
            active=True,
            created_at="2026-07-10T00:00:00Z",
            updated_at="2026-07-10T00:00:00Z",
        )

    @property
    def webhook_id(self) -> str:
        return self._sub.webhook_id

    def list_webhooks(self, conn: Any, active_only: bool = False) -> list[WebhookSubscription]:
        return [self._sub]


class _CapturingDelivery:
    def __init__(self) -> None:
        from idis.services.webhooks.delivery import DeliveryResult

        self._result = DeliveryResult(
            success=True, status_code=200, error=None, attempt_id="", duration_ms=1
        )
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        webhook_id: str,
        attempt_id: str,
        timeout_seconds: int = 30,
    ) -> Any:
        self.calls.append({"url": url, "payload": payload, "headers": dict(headers)})
        return self._result


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _dispatch_all(
    outbox: InMemoryWebhookOutboxRepository, now: datetime | None = None
) -> tuple[_CapturingDelivery, _CapturingSink, dict[str, int]]:
    delivery = _CapturingDelivery()
    audit = _CapturingSink()
    dispatcher = WebhookDispatcher(
        outbox=outbox,
        secret_loader=lambda conn, webhook_id: (_URL, _SECRET),
        deliver_fn=delivery,
        audit_sink=audit,
    )
    summary = dispatcher.drain_once(tenant_id=_TENANT, now=now or datetime.now(UTC), limit=10)
    return delivery, audit, summary


def _assert_no_leaks(blob: str) -> None:
    lowered = blob.lower()
    for forbidden in _FORBIDDEN_EVERYWHERE:
        assert forbidden not in lowered, f"{forbidden!r} leaked"


# --- E2E: subscribe -> lifecycle event -> outbox -> dispatch -> signed delivery -> audit ---


def test_subscribe_lifecycle_outbox_dispatch_signed_delivery_audit_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lister = _Lister(["run.completed"])
    outbox = InMemoryWebhookOutboxRepository()
    # 'subscribe': the default service/outbox seams resolve to this tenant's subscription store.
    monkeypatch.setattr(webhook_lifecycle, "get_webhook_service", lambda: lister)
    monkeypatch.setattr(webhook_lifecycle, "default_webhook_outbox", lambda: outbox)

    # REAL notify path (conn provided, as at the route/generator call sites), carrying sensitive
    # fields the safe builder must strip before anything is persisted or delivered (A2).
    webhook_lifecycle.notify_webhook_lifecycle(
        tenant_id=_TENANT,
        event_type="run.completed",
        resource_type="run",
        resource_id=_RUN,
        data={
            "status": "COMPLETED",
            "artifact_count": 2,
            "local_path": "/var/data/secret.pdf",
            "transcript": "the CEO said revenue was 10m",
        },
        conn=object(),  # tenant-scoped conn stand-in; the fake lister ignores it
    )

    rows = outbox.claim_due(tenant_id=_TENANT, now=datetime.now(UTC), limit=10)
    assert len(rows) == 1 and rows[0].webhook_id == lister.webhook_id
    _assert_no_leaks(json.dumps(rows[0].payload))  # A2: outbox row is already safe

    delivery, audit, summary = _dispatch_all(outbox)
    assert summary["claimed"] == 1 and summary["succeeded"] == 1

    (call,) = delivery.calls
    body = json.dumps(call["payload"]).encode("utf-8")
    timestamp = int(call["headers"]["X-IDIS-Webhook-Timestamp"])
    assert verify_webhook_signature(  # SIGNED delivery, verified round-trip
        _SECRET, timestamp, body, call["headers"]["X-IDIS-Webhook-Signature"]
    )
    _assert_no_leaks(json.dumps(call["payload"]))  # A2: delivered body is safe

    (event,) = audit.events
    assert event["event_type"] == "webhook.delivery.succeeded"
    assert validate_audit_event(event).passed
    assert event["payload"]["safe"]["outcome"] == "succeeded"
    _assert_no_leaks(json.dumps(event, default=str))  # A2: audit carries no url/secret/body
    assert "example.test" not in json.dumps(event, default=str)  # url never in audit


def test_full_run_lifecycle_chain_through_execution_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The genuinely-full chain: RunExecutionService.execute -> notify_webhook_lifecycle (REAL) ->
    # emitter (REAL) -> outbox -> dispatcher -> signed delivery -> succeeded audit. The run path
    # opens its own tenant-scoped conn; stub the db seams only (no Postgres in this hermetic test).
    lister = _Lister(["run.completed"])
    outbox = InMemoryWebhookOutboxRepository()
    monkeypatch.setattr(webhook_lifecycle, "get_webhook_service", lambda: lister)
    monkeypatch.setattr(webhook_lifecycle, "default_webhook_outbox", lambda: outbox)

    import idis.persistence.db as db_mod

    @contextlib.contextmanager
    def _fake_conn() -> Iterator[object]:
        yield object()

    monkeypatch.setattr(db_mod, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(db_mod, "begin_app_conn", _fake_conn)
    monkeypatch.setattr(db_mod, "set_tenant_local", lambda conn, tenant_id: None)

    result = _execute(InMemoryAuditSink())
    assert result.claimed is True and result.status == "SUCCEEDED"

    rows = outbox.claim_due(tenant_id=_TENANT, now=datetime.now(UTC), limit=10)
    assert len(rows) == 1  # run.completed enqueued through the real execute -> notify chain
    assert rows[0].event_type == "run.completed"
    assert rows[0].payload["resource_id"] == _RUN

    delivery, audit, summary = _dispatch_all(outbox)
    assert summary["succeeded"] == 1
    (event,) = audit.events
    assert event["event_type"] == "webhook.delivery.succeeded"
    assert validate_audit_event(event).passed


def test_a1_throwing_webhook_machinery_never_breaks_mutation_or_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("webhook machinery down")

    monkeypatch.setattr(webhook_lifecycle, "get_webhook_service", _boom)
    monkeypatch.setattr(webhook_lifecycle, "default_webhook_outbox", _boom)

    sink = InMemoryAuditSink()
    result = _execute(sink)

    assert result.claimed is True and result.status == "SUCCEEDED"  # mutation unaffected
    assert any(e.get("event_type") == RUN_CLAIMED for e in sink.events)  # audit still committed


# --- docs / CI reconciliation pins ---


def test_architecture_doc_records_slice97_design() -> None:
    doc = _ARCH_DOC.read_text(encoding="utf-8")
    assert "webhook_delivery_attempts" in doc  # durable outbox
    assert "best-effort" in doc.lower()  # the A1 discipline
    assert "SKIP LOCKED" in doc  # no double-delivery drain
    assert "webhook.delivery.succeeded" in doc  # delivery audit taxonomy
    assert "safe" in doc.lower() and "A2" in doc  # safe-payload composition
    assert "webhook_delivery_success_total" in doc  # metrics note


def test_traceability_matrix_reconciles_wh001_to_delivered() -> None:
    matrix = _MATRIX.read_text(encoding="utf-8")
    assert "WH-001" in matrix
    wh_lines = [line for line in matrix.splitlines() if "WH-001" in line]
    assert not any("Planned" in line for line in wh_lines)  # no longer planned
    assert any("Delivered" in line or "✅" in line for line in wh_lines)
    assert "webhook.delivery.succeeded/failed" in matrix  # tied to the delivery audit events


def test_slice97_plan_status_reconciled() -> None:
    plan = _PLAN.read_text(encoding="utf-8")
    assert "post-Slice97" in plan
    assert "acceptance met" in plan.lower()


def test_ci_postgres_integration_runs_slice97_webhook_tests() -> None:
    ci = _CI.read_text(encoding="utf-8")
    assert "tests/test_slice97_webhook_outbox_postgres.py" in ci
    assert "tests/test_slice97_webhook_dispatcher_postgres.py" in ci
    assert 'IDIS_REQUIRE_POSTGRES: "1"' in ci
