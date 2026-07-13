"""Slice98 Task 8 - deletion/erasure + compliance export workflows (hermetic).

RED-first. Approved decisions: per-DEAL erasure + per-TENANT export (Open Question 3); erasure
depth = FULL removal including the deals row (audit events retain deal_id references); no
tenant-wide erasure. Amendments: ``erasure_requests`` has NO foreign key to ``deals`` (the request
row is durable evidence that must OUTLIVE the erased deal), and the deal-scoped deletion surface
is derived from the schema and pinned by a classification tripwire (PG file).

Unit A proves the durable request workflow:
- ``ErasureRequestStore`` seam (get_/set_/reset_/build_default_*) with in-memory/Postgres twins;
  request -> ADMIN execution lifecycle (REQUESTED -> EXECUTED | FAILED; FAILED is re-executable).
- Reason is hashed immediately (hash+length only) - never stored, audited, or logged raw.
- Audit-fatal domain emission: ``erasure.requested`` (HIGH) before the request row is written;
  ``erasure.executed`` (CRITICAL) BEFORE any destruction, failure aborts all of it.
- Fail-closed resolution: a store/backend error DENIES (403 ERASURE_RESOLUTION_FAILED), never
  reading as "no request"; writes fail loudly (500) leaving no durable state.

PYTHONPATH is pinned to this worktree's src for every run.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import InMemoryAuditSink

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_REASON = "Data subject erasure request under contract clause 9.2."


def _ctx(tenant_id: str = _TENANT_A, actor_id: str = "erasure-admin-1") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        name="Erasure Admin",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


@pytest.fixture
def _reset_seam(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate the erasure-request store seam; force the in-memory default (no Postgres env)."""
    from idis.compliance.erasure import reset_erasure_request_store

    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    reset_erasure_request_store()
    yield
    reset_erasure_request_store()


@pytest.mark.usefixtures("_reset_seam")
class TestErasureRequestStoreSeam:
    """Unit A: seam + twins + lifecycle storage."""

    def test_seam_set_get_roundtrip_and_default(self) -> None:
        from idis.compliance.erasure import (
            InMemoryErasureRequestStore,
            build_default_erasure_request_store,
            get_erasure_request_store,
            set_erasure_request_store,
        )

        store = InMemoryErasureRequestStore()
        set_erasure_request_store(store)
        assert get_erasure_request_store() is store
        assert isinstance(build_default_erasure_request_store(), InMemoryErasureRequestStore)

    def test_request_erasure_creates_durable_requested_row(self) -> None:
        from idis.compliance.erasure import (
            ErasureStatus,
            get_erasure_request_store,
            request_erasure,
        )

        request = request_erasure(_ctx(), _DEAL, _REASON, InMemoryAuditSink())
        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None
        assert stored.status == ErasureStatus.REQUESTED
        assert stored.deal_id == _DEAL
        assert stored.requested_by == "erasure-admin-1"
        assert stored.executed_at is None

    def test_reason_is_hashed_never_stored_raw(self) -> None:
        from idis.compliance.erasure import get_erasure_request_store, request_erasure

        sink = InMemoryAuditSink()
        request = request_erasure(_ctx(), _DEAL, _REASON, sink)
        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None
        assert stored.reason_hash == hashlib.sha256(_REASON.encode("utf-8")).hexdigest()
        assert stored.reason_length == len(_REASON)
        assert not hasattr(stored, "reason")
        import json as jsonlib

        for event in sink.events:
            assert _REASON not in jsonlib.dumps(event)  # never in audit either

    def test_request_requires_reason(self) -> None:
        from idis.compliance.erasure import request_erasure

        with pytest.raises(IdisHttpError) as exc_info:
            request_erasure(_ctx(), _DEAL, "   ", InMemoryAuditSink())
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "ERASURE_INVALID_REASON"

    def test_requested_audit_is_emitted_before_row_write_and_is_fatal(self) -> None:
        # Audit-fatal ordering (compliance-core precedent): the domain event precedes the write;
        # a sink failure blocks the request and leaves NO durable state.
        from idis.compliance.erasure import get_erasure_request_store, request_erasure

        class _ExplodingSink(InMemoryAuditSink):
            def emit(self, event: dict[str, Any]) -> None:
                raise RuntimeError("sink down")

        with pytest.raises(IdisHttpError) as exc_info:
            request_erasure(_ctx(), _DEAL, _REASON, _ExplodingSink())
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "ERASURE_AUDIT_FAILED"
        # nothing durable behind: the seam store holds no request for this tenant/deal
        store = get_erasure_request_store()
        assert store.list_for_tenant(_TENANT_A) == []

    def test_requested_event_shape(self) -> None:
        from idis.compliance.erasure import request_erasure

        sink = InMemoryAuditSink()
        request = request_erasure(_ctx(), _DEAL, _REASON, sink)
        events = [e for e in sink.events if e.get("event_type") == "erasure.requested"]
        assert len(events) == 1
        assert events[0]["severity"] == "HIGH"
        assert events[0]["resource"]["resource_type"] == "erasure_request"
        assert events[0]["resource"]["resource_id"] == request.request_id
        assert events[0]["tenant_id"] == _TENANT_A


@pytest.mark.usefixtures("_reset_seam")
class TestCoreDomainEventValidation:
    """The core fail-closed domain events must THEMSELVES be schema-valid, and core emission
    must validate BEFORE emitting - a validation failure fails closed and aborts the guarded
    action (row write / destruction / bundle write), the Task 7 janitor precedent."""

    def _validated(self, sink: InMemoryAuditSink, event_type: str) -> None:
        from idis.validators.audit_event_validator import validate_audit_event

        events = [e for e in sink.events if e.get("event_type") == event_type]
        assert events, f"no {event_type} event emitted"
        for event in events:
            result = validate_audit_event(event)
            assert result.passed, f"{event_type}: {[e.code for e in result.errors]}"

    def test_core_erasure_requested_event_is_schema_valid(self) -> None:
        from idis.compliance.erasure import request_erasure

        sink = InMemoryAuditSink()
        request_erasure(_ctx(), _DEAL, _REASON, sink)
        self._validated(sink, "erasure.requested")

    def test_core_erasure_executed_event_is_schema_valid(self) -> None:
        from idis.compliance.erasure import execute_erasure, request_erasure

        sink = InMemoryAuditSink()
        request = request_erasure(_ctx(), _DEAL, _REASON, sink)
        execute_erasure(
            _ctx(),
            request.request_id,
            sink,
            executor=_RecordingExecutor(),
            hold_checker=_RecordingHoldChecker(),
        )
        self._validated(sink, "erasure.executed")

    def test_core_export_created_event_is_schema_valid(self) -> None:
        from idis.compliance.compliance_export import build_compliance_export

        sink = InMemoryAuditSink()
        build_compliance_export(_ctx(), sink)
        self._validated(sink, "export.created")

    def test_requested_validation_failure_aborts_row_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import idis.compliance.erasure as erasure_module
        from idis.compliance.erasure import get_erasure_request_store, request_erasure

        class _Failed:
            passed = False
            errors: list[Any] = []

        monkeypatch.setattr(erasure_module, "validate_audit_event", lambda event: _Failed())
        sink = InMemoryAuditSink()
        with pytest.raises(IdisHttpError) as exc_info:
            request_erasure(_ctx(), _DEAL, _REASON, sink)
        assert exc_info.value.code == "ERASURE_AUDIT_FAILED"
        assert sink.events == []  # invalid event never emitted
        assert get_erasure_request_store().list_for_tenant(_TENANT_A) == []  # no row written

    def test_executed_validation_failure_aborts_destruction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import idis.compliance.erasure as erasure_module
        from idis.compliance.erasure import (
            ErasureStatus,
            execute_erasure,
            get_erasure_request_store,
            request_erasure,
        )

        sink = InMemoryAuditSink()
        request = request_erasure(_ctx(), _DEAL, _REASON, sink)

        class _Failed:
            passed = False
            errors: list[Any] = []

        monkeypatch.setattr(erasure_module, "validate_audit_event", lambda event: _Failed())
        executor = _RecordingExecutor()
        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                request.request_id,
                sink,
                executor=executor,
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.code == "ERASURE_AUDIT_FAILED"
        assert executor.calls == []  # destruction aborted
        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None and stored.status == ErasureStatus.REQUESTED

    def test_export_validation_failure_aborts_bundle_write(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        import idis.compliance.compliance_export as export_module
        from idis.compliance.compliance_export import build_compliance_export

        class _Failed:
            passed = False
            errors: list[Any] = []

        writes: list[str] = []

        class _RecordingStore:
            def put(self, *args: Any, **kwargs: Any) -> None:
                writes.append("put")

        monkeypatch.setattr(export_module, "validate_audit_event", lambda event: _Failed())
        monkeypatch.setattr(
            "idis.services.ingestion.defaults.build_default_compliance_store",
            lambda: _RecordingStore(),
        )
        sink = InMemoryAuditSink()
        with pytest.raises(IdisHttpError) as exc_info:
            build_compliance_export(_ctx(), sink)
        assert exc_info.value.code == "EXPORT_AUDIT_FAILED"
        assert sink.events == []
        assert writes == []  # bundle write aborted


class _RecordingExecutor:
    """Erasure executor recording calls; optionally raising."""

    def __init__(self, error: Exception | None = None, scan_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.scans: list[tuple[str, str]] = []
        self._error = error
        self._scan_error = scan_error

    def scan_holds(self, tenant_id: str, deal_id: str) -> None:
        self.scans.append((tenant_id, deal_id))
        if self._scan_error is not None:
            raise self._scan_error

    def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
        if self._error is not None:
            raise self._error
        self.calls.append((tenant_id, deal_id))
        return {"rows_deleted": 42, "objects_deleted": 3, "embeddings_deleted": 5}


class _RecordingHoldChecker:
    def __init__(self, *, held: bool = False) -> None:
        self.checked: list[tuple[str, str]] = []
        self._held = held

    def __call__(self, tenant_id: str, deal_id: str) -> None:
        self.checked.append((tenant_id, deal_id))
        if self._held:
            raise IdisHttpError(
                status_code=403, code="DELETION_BLOCKED_BY_HOLD", message="Access denied."
            )


@pytest.mark.usefixtures("_reset_seam")
class TestErasureExecution:
    """Unit A: execute lifecycle - hold-abort, audit-before-destruction, idempotent retry."""

    def _request(self, sink: InMemoryAuditSink) -> Any:
        from idis.compliance.erasure import request_erasure

        return request_erasure(_ctx(), _DEAL, _REASON, sink)

    def test_execute_transitions_to_executed_with_counts(self) -> None:
        from idis.compliance.erasure import (
            ErasureStatus,
            execute_erasure,
            get_erasure_request_store,
        )

        sink = InMemoryAuditSink()
        request = self._request(sink)
        executor = _RecordingExecutor()
        holds = _RecordingHoldChecker()

        executed = execute_erasure(
            _ctx(actor_id="erasure-admin-2"),
            request.request_id,
            sink,
            executor=executor,
            hold_checker=holds,
        )
        assert executed.status == ErasureStatus.EXECUTED
        assert executed.executed_by == "erasure-admin-2"
        assert executed.counts == {
            "rows_deleted": 42,
            "objects_deleted": 3,
            "embeddings_deleted": 5,
        }
        assert executor.calls == [(_TENANT_A, _DEAL)]
        assert holds.checked == [(_TENANT_A, _DEAL)]  # holds checked before destruction
        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None and stored.status == ErasureStatus.EXECUTED

    def test_unknown_request_404_uniform(self) -> None:
        from idis.compliance.erasure import execute_erasure

        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                str(uuid.uuid4()),
                InMemoryAuditSink(),
                executor=_RecordingExecutor(),
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "ERASURE_REQUEST_NOT_FOUND"

    def test_cross_tenant_request_is_uniform_404(self) -> None:
        from idis.compliance.erasure import execute_erasure

        sink = InMemoryAuditSink()
        request = self._request(sink)
        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(tenant_id=_TENANT_B, actor_id="admin-b"),
                request.request_id,
                sink,
                executor=_RecordingExecutor(),
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "ERASURE_REQUEST_NOT_FOUND"

    def test_active_hold_aborts_before_any_destruction(self) -> None:
        from idis.compliance.erasure import ErasureStatus, execute_erasure

        sink = InMemoryAuditSink()
        request = self._request(sink)
        executor = _RecordingExecutor()

        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                request.request_id,
                sink,
                executor=executor,
                hold_checker=_RecordingHoldChecker(held=True),
            )
        assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"
        assert executor.calls == []  # nothing was deleted
        # the request stays REQUESTED (re-executable after the hold is lifted)
        from idis.compliance.erasure import get_erasure_request_store

        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None and stored.status == ErasureStatus.REQUESTED
        executed_events = [e for e in sink.events if e.get("event_type") == "erasure.executed"]
        assert executed_events == []  # no destruction, no executed event

    def test_executed_audit_emitted_before_destruction_and_failure_aborts(self) -> None:
        from idis.compliance.erasure import ErasureStatus, execute_erasure

        timeline: list[str] = []

        class _TimelineSink(InMemoryAuditSink):
            def emit(self, event: dict[str, Any]) -> None:
                timeline.append(str(event.get("event_type")))
                super().emit(event)

        class _TimelineExecutor(_RecordingExecutor):
            def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
                timeline.append("DESTRUCTION")
                return super().erase_deal(tenant_id, deal_id)

        sink = _TimelineSink()
        request = self._request(sink)
        execute_erasure(
            _ctx(),
            request.request_id,
            sink,
            executor=_TimelineExecutor(),
            hold_checker=_RecordingHoldChecker(),
        )
        assert timeline.index("erasure.executed") < timeline.index("DESTRUCTION")

        # and: an executed-audit failure aborts destruction entirely
        class _FailOnExecuted(InMemoryAuditSink):
            def emit(self, event: dict[str, Any]) -> None:
                if event.get("event_type") == "erasure.executed":
                    raise RuntimeError("sink down")
                super().emit(event)

        from idis.compliance.erasure import get_erasure_request_store, request_erasure

        fail_sink = _FailOnExecuted()
        second = request_erasure(_ctx(), _DEAL, _REASON, fail_sink)
        executor = _RecordingExecutor()
        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                second.request_id,
                fail_sink,
                executor=executor,
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.code == "ERASURE_AUDIT_FAILED"
        assert executor.calls == []  # audit failure -> zero destruction
        stored = get_erasure_request_store().get(_TENANT_A, second.request_id)
        assert stored is not None and stored.status == ErasureStatus.REQUESTED

    def test_executor_failure_marks_failed_and_is_reexecutable(self) -> None:
        from idis.compliance.erasure import (
            ErasureStatus,
            execute_erasure,
            get_erasure_request_store,
        )

        sink = InMemoryAuditSink()
        request = self._request(sink)
        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                request.request_id,
                sink,
                executor=_RecordingExecutor(error=RuntimeError("db down mid-erasure")),
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "ERASURE_EXECUTION_FAILED"
        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None and stored.status == ErasureStatus.FAILED

        # idempotent retry after the failure succeeds and lands EXECUTED
        retried = execute_erasure(
            _ctx(),
            request.request_id,
            sink,
            executor=_RecordingExecutor(),
            hold_checker=_RecordingHoldChecker(),
        )
        assert retried.status == ErasureStatus.EXECUTED

    def test_artifact_hold_scan_aborts_before_audit_and_destruction(self) -> None:
        # Artifact-level holds are scanned by the EXECUTOR before the CRITICAL audit event: a
        # held artifact aborts the whole execution with no destruction, NO executed event
        # (nothing happened), and the request stays REQUESTED for after the hold is lifted.
        from idis.compliance.erasure import (
            ErasureStatus,
            execute_erasure,
            get_erasure_request_store,
        )

        sink = InMemoryAuditSink()
        request = self._request(sink)
        executor = _RecordingExecutor(
            scan_error=IdisHttpError(
                status_code=403, code="DELETION_BLOCKED_BY_HOLD", message="Access denied."
            )
        )
        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                request.request_id,
                sink,
                executor=executor,
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"
        assert executor.scans == [(_TENANT_A, _DEAL)]
        assert executor.calls == []  # zero destruction
        assert [e for e in sink.events if e.get("event_type") == "erasure.executed"] == []
        stored = get_erasure_request_store().get(_TENANT_A, request.request_id)
        assert stored is not None and stored.status == ErasureStatus.REQUESTED

    def test_already_executed_request_is_conflict(self) -> None:
        from idis.compliance.erasure import execute_erasure

        sink = InMemoryAuditSink()
        request = self._request(sink)
        execute_erasure(
            _ctx(),
            request.request_id,
            sink,
            executor=_RecordingExecutor(),
            hold_checker=_RecordingHoldChecker(),
        )
        with pytest.raises(IdisHttpError) as exc_info:
            execute_erasure(
                _ctx(),
                request.request_id,
                sink,
                executor=_RecordingExecutor(),
                hold_checker=_RecordingHoldChecker(),
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ERASURE_ALREADY_EXECUTED"


@pytest.mark.usefixtures("_reset_seam")
class TestErasureFailClosedResolution:
    """Unit A: backend errors deny - never degrade to 'no request'; writes fail loudly."""

    def test_pg_store_get_without_database_denies(self) -> None:
        from idis.compliance.erasure import PostgresErasureRequestStore

        with pytest.raises(IdisHttpError) as exc_info:
            PostgresErasureRequestStore().get(_TENANT_A, str(uuid.uuid4()))
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "ERASURE_RESOLUTION_FAILED"

    def test_pg_store_create_without_database_fails_loudly(self) -> None:
        from idis.compliance.erasure import ErasureRequest, PostgresErasureRequestStore

        request = ErasureRequest.new(
            tenant_id=_TENANT_A, deal_id=_DEAL, requested_by="admin", reason=_REASON
        )
        with pytest.raises(IdisHttpError) as exc_info:
            PostgresErasureRequestStore().create(request)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "ERASURE_REQUEST_WRITE_FAILED"


# --- Units B + C: routes, export bundle, and end-to-end erasure through the app ---

_ADMIN_KEY = "erasure-admin-key"
_ANALYST_KEY = "erasure-analyst-key"
_ADMIN_ACTOR = "erasure-admin-1"


def _api_keys_json() -> str:
    import json

    def _entry(actor: str, roles: list[str]) -> dict[str, Any]:
        return {
            "tenant_id": _TENANT_A,
            "actor_id": actor,
            "name": actor,
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": roles,
        }

    return json.dumps(
        {
            _ADMIN_KEY: _entry(_ADMIN_ACTOR, ["ADMIN"]),
            _ANALYST_KEY: _entry("erasure-analyst-1", ["ANALYST"]),
        }
    )


@pytest.fixture
def erasure_app(monkeypatch: pytest.MonkeyPatch, _reset_seam: None) -> Iterator[tuple]:
    """Full app with in-memory seams: erasure store/executor, export collector, compliance."""
    from fastapi.testclient import TestClient

    from idis.api.auth import IDIS_API_KEYS_ENV
    from idis.api.main import create_app
    from idis.api.routes.deals import clear_deals_store
    from idis.api.routes.documents import clear_document_store
    from idis.compliance.compliance_export import reset_export_collector
    from idis.compliance.erasure import reset_erasure_executor
    from idis.compliance.retention import reset_legal_hold_registry

    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_json())
    clear_deals_store()
    clear_document_store()
    reset_erasure_executor()
    reset_export_collector()
    reset_legal_hold_registry()
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink, service_region="us-east-1")
    yield TestClient(app, raise_server_exceptions=False), sink
    clear_deals_store()
    clear_document_store()
    reset_erasure_executor()
    reset_export_collector()
    reset_legal_hold_registry()


def _hdr(key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": key, "Content-Type": "application/json"}


def _create_deal(client: Any) -> str:
    resp = client.post(
        "/v1/deals",
        json={"name": "Erasure Deal", "company_name": "Acme"},
        headers=_hdr(_ADMIN_KEY),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["deal_id"])


def _events_of(sink: InMemoryAuditSink, event_type: str) -> list[dict]:
    return [e for e in sink.events if e.get("event_type") == event_type]


class TestErasureRoutes:
    """Unit B: ADMIN-only request/execute routes with dual-layer audit."""

    def test_create_request_201_dual_audit_no_reason_leak(self, erasure_app: tuple) -> None:
        import json as jsonlib

        from idis.validators.audit_event_validator import validate_audit_event

        client, sink = erasure_app
        deal_id = _create_deal(client)
        resp = client.post(
            "/v1/erasure-requests",
            json={"deal_id": deal_id, "reason": _REASON},
            headers=_hdr(_ADMIN_KEY),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == {"request_id", "deal_id", "status", "requested_at"}
        assert body["status"] == "REQUESTED"
        assert _REASON not in resp.text

        events = _events_of(sink, "erasure.requested")
        assert len(events) == 2  # dual-layer: core (/internal path) + middleware (/v1 route)
        middleware_events = [e for e in events if e["request"]["path"].startswith("/v1")]
        assert len(middleware_events) == 1
        for event in events:  # BOTH layers must be schema-valid (the acceptance blocker)
            assert validate_audit_event(event).passed
        assert middleware_events[0]["resource"]["resource_type"] == "erasure_request"
        assert middleware_events[0]["resource"]["resource_id"] == body["request_id"]
        for event in events:
            assert _REASON not in jsonlib.dumps(event)

    def test_create_request_unknown_deal_404(self, erasure_app: tuple) -> None:
        client, _sink = erasure_app
        resp = client.post(
            "/v1/erasure-requests",
            json={"deal_id": str(uuid.uuid4()), "reason": _REASON},
            headers=_hdr(_ADMIN_KEY),
        )
        assert resp.status_code == 404, resp.text

    def test_create_request_empty_reason_400(self, erasure_app: tuple) -> None:
        client, _sink = erasure_app
        deal_id = _create_deal(client)
        resp = client.post(
            "/v1/erasure-requests",
            json={"deal_id": deal_id, "reason": "  "},
            headers=_hdr(_ADMIN_KEY),
        )
        assert resp.status_code == 400, resp.text

    def test_non_admin_denied(self, erasure_app: tuple) -> None:
        client, _sink = erasure_app
        deal_id = _create_deal(client)
        assert (
            client.post(
                "/v1/erasure-requests",
                json={"deal_id": deal_id, "reason": _REASON},
                headers=_hdr(_ANALYST_KEY),
            ).status_code
            == 403
        )
        assert client.post("/v1/compliance-exports", headers=_hdr(_ANALYST_KEY)).status_code == 403

    def test_execute_erases_deal_through_real_route_flow(self, erasure_app: tuple) -> None:
        from idis.validators.audit_event_validator import validate_audit_event
        from tests.abac_seed import seed_deal_access

        client, sink = erasure_app
        deal_id = _create_deal(client)
        seed_deal_access(_TENANT_A, deal_id, _ADMIN_ACTOR)
        assert client.get(f"/v1/deals/{deal_id}", headers=_hdr(_ADMIN_KEY)).status_code == 200

        requested = client.post(
            "/v1/erasure-requests",
            json={"deal_id": deal_id, "reason": _REASON},
            headers=_hdr(_ADMIN_KEY),
        )
        request_id = requested.json()["request_id"]

        executed = client.post(
            f"/v1/erasure-requests/{request_id}/execute", headers=_hdr(_ADMIN_KEY)
        )
        assert executed.status_code == 200, executed.text
        body = executed.json()
        assert body["status"] == "EXECUTED"
        assert body["counts"]["rows_deleted"] >= 1  # at least the deals row itself

        # full removal including the deals row: the deal no longer exists for its own tenant
        assert client.get(f"/v1/deals/{deal_id}", headers=_hdr(_ADMIN_KEY)).status_code == 404

        executed_events = _events_of(sink, "erasure.executed")
        middleware_events = [e for e in executed_events if e["request"]["path"].startswith("/v1")]
        assert len(middleware_events) == 1
        assert middleware_events[0]["severity"] == "CRITICAL"
        for event in executed_events:  # core + middleware both schema-valid
            assert validate_audit_event(event).passed

    def test_deal_hold_blocks_execute_until_lifted(self, erasure_app: tuple) -> None:
        client, _sink = erasure_app
        deal_id = _create_deal(client)

        applied = client.post(
            "/v1/legal-holds",
            json={"target_type": "DEAL", "target_id": deal_id, "reason": _REASON},
            headers=_hdr(_ADMIN_KEY),
        )
        assert applied.status_code == 201, applied.text

        requested = client.post(
            "/v1/erasure-requests",
            json={"deal_id": deal_id, "reason": _REASON},
            headers=_hdr(_ADMIN_KEY),
        )
        request_id = requested.json()["request_id"]

        blocked = client.post(
            f"/v1/erasure-requests/{request_id}/execute", headers=_hdr(_ADMIN_KEY)
        )
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["code"] == "DELETION_BLOCKED_BY_HOLD"

        lifted = client.post(
            f"/v1/legal-holds/{applied.json()['hold_id']}/lift", headers=_hdr(_ADMIN_KEY)
        )
        assert lifted.status_code == 200, lifted.text
        retried = client.post(
            f"/v1/erasure-requests/{request_id}/execute", headers=_hdr(_ADMIN_KEY)
        )
        assert retried.status_code == 200, retried.text

    def test_audit_trail_survives_erasure(self, erasure_app: tuple) -> None:
        # Immutable audit survival: executing an erasure never removes audit events - the
        # deal's history (including its creation record) remains, with deal_id references.
        client, sink = erasure_app
        deal_id = _create_deal(client)
        created_events_before = [
            e for e in sink.events if e.get("resource", {}).get("resource_id") == deal_id
        ]
        assert created_events_before, "deal creation must be audited"

        requested = client.post(
            "/v1/erasure-requests",
            json={"deal_id": deal_id, "reason": _REASON},
            headers=_hdr(_ADMIN_KEY),
        )
        client.post(
            f"/v1/erasure-requests/{requested.json()['request_id']}/execute",
            headers=_hdr(_ADMIN_KEY),
        )

        still_there = [
            e for e in sink.events if e.get("resource", {}).get("resource_id") == deal_id
        ]
        assert len(still_there) >= len(created_events_before)  # nothing was purged


class TestComplianceExport:
    """Unit C: per-tenant export bundle - manifest-first, sanitized, dual-audited."""

    def test_export_builds_sanitized_manifest_bundle(self, erasure_app: tuple) -> None:
        import hashlib as hashlib_mod
        import json as jsonlib

        from idis.compliance.compliance_export import set_export_collector
        from idis.validators.audit_event_validator import validate_audit_event

        client, sink = erasure_app

        class _Collector:
            def collect(self, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
                return {
                    "deals": [{"deal_id": _DEAL, "name": "Erasure Deal"}],
                    "documents": [
                        {
                            "doc_id": "doc-1",
                            "title": "Deck",
                            "sha256": "a" * 64,
                            # sensitive keys a careless collector might include: MUST be dropped
                            "raw_text": "SECRET BODY",
                            "local_path": "C:/private/deck.pdf",
                        }
                    ],
                    "claims": [{"claim_id": "c-1", "text_excerpt": "SECRET EXCERPT"}],
                    "sanads": [{"sanad_id": "s-1"}],
                    "deliverables": [{"deliverable_id": "d-1", "embedding": [0.1, 0.2]}],
                }

        set_export_collector(_Collector())
        resp = client.post("/v1/compliance-exports", headers=_hdr(_ADMIN_KEY))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == {"export_id", "object_key", "manifest_sha256", "counts"}
        assert body["counts"] == {
            "deals": 1,
            "documents": 1,
            "claims": 1,
            "sanads": 1,
            "deliverables": 1,
        }

        from idis.services.ingestion.defaults import build_default_compliance_store

        stored = build_default_compliance_store().get(
            _ctx(actor_id=_ADMIN_ACTOR), body["object_key"]
        )
        assert hashlib_mod.sha256(stored.body).hexdigest() == body["manifest_sha256"]
        manifest = jsonlib.loads(stored.body.decode("utf-8"))
        assert manifest["tenant_id"] == _TENANT_A
        assert manifest["counts"] == body["counts"]
        raw = stored.body.decode("utf-8")
        for leak in ("SECRET BODY", "SECRET EXCERPT", "raw_text", "local_path", "embedding"):
            assert leak not in raw, f"sensitive content leaked into manifest: {leak}"
        assert manifest["items"]["documents"][0]["sha256"] == "a" * 64  # safe fields kept

        events = _events_of(sink, "export.created")
        middleware_events = [e for e in events if e["request"]["path"].startswith("/v1")]
        assert len(middleware_events) == 1
        for event in events:  # core + middleware both schema-valid
            assert validate_audit_event(event).passed
        assert middleware_events[0]["resource"]["resource_type"] == "compliance_export"
