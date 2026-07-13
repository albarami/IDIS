"""Slice98 Task 5 - break-glass workflow completion (hermetic).

RED-first. Approved decisions: strict single-use (one grant = one authorized request);
``POST /v1/break-glass/grants`` (ADMIN-only, deal_id required, self-issuance only); flag-gated
cutover ``IDIS_ENABLE_DURABLE_BREAK_GLASS`` (default off = legacy stateless behavior preserved,
durable issuance/consumption never the active authorization path); plaintext justification in the
durable grant row (overrides-table precedent) while audit stays hash+length-only.

Hard constraints proven here:
- createBreakGlassGrant sets request.state.audit_resource_id = deal_id (the generic audit
  middleware fails closed on successful mutations without it), so the 201 + the audited
  ``break_glass.issued`` event with resource_id == deal_id are asserted together.
- Enforcement lookup uses the FULL 64-char SHA-256 of the raw token (no truncated hash).
- Consumption happens ONLY when the grant actually supplies the ABAC override (unassigned ADMIN +
  valid actor/deal-bound token + ABAC would otherwise require break-glass); an assigned admin
  presenting a token does not burn the grant.
- Durable mode rejects valid-HMAC tokens that were never recorded; concurrent consumption yields
  exactly one success; flag off keeps stateless-token behavior byte-identical.

The Postgres twin, migration 0028, RLS, and cross-instance durability are proven in
``test_slice98_break_glass_workflow_postgres.py``. PYTHONPATH is pinned to this worktree's src.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.break_glass import create_break_glass_token
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink

_TENANT = "11111111-1111-1111-1111-111111111111"
_ADMIN_KEY = "bg-admin-key"
_ANALYST_KEY = "bg-analyst-key"
_ADMIN_ACTOR = "bg-admin-1"
_ANALYST_ACTOR = "bg-analyst-1"
_JUSTIFICATION = "Investor call in 10 minutes; need immediate deal access."
_SECRET = "test-break-glass-secret-slice98"


def _api_keys_json() -> str:
    def _entry(actor: str, roles: list[str]) -> dict[str, Any]:
        return {
            "tenant_id": _TENANT,
            "actor_id": actor,
            "name": actor,
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": roles,
        }

    return json.dumps(
        {
            _ADMIN_KEY: _entry(_ADMIN_ACTOR, ["ADMIN"]),
            _ANALYST_KEY: _entry(_ANALYST_ACTOR, ["ANALYST"]),
        }
    )


@pytest.fixture
def _reset_grant_store() -> Iterator[None]:
    """Isolate the module-global break-glass grant store between tests."""
    from idis.api.break_glass_grants import reset_break_glass_grant_store

    reset_break_glass_grant_store()
    yield
    reset_break_glass_grant_store()


def _grant(**overrides: Any) -> Any:
    """A recorded-grant value object with sane defaults."""
    from idis.api.break_glass_grants import BreakGlassGrant

    now = time.time()
    defaults: dict[str, Any] = {
        "grant_id": str(uuid.uuid4()),
        "tenant_id": _TENANT,
        "deal_id": str(uuid.uuid4()),
        "actor_id": _ADMIN_ACTOR,
        "justification": _JUSTIFICATION,
        "token_sha256": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "issued_at": now,
        "expires_at": now + 900,
    }
    defaults.update(overrides)
    return BreakGlassGrant(**defaults)


@pytest.mark.usefixtures("_reset_grant_store")
class TestBreakGlassGrantStore:
    """Unit A: the grant store seam and its in-memory twin (single-use semantics)."""

    def test_record_then_get_roundtrip(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        store = InMemoryBreakGlassGrantStore()
        grant = _grant()
        store.record_grant(grant)
        loaded = store.get_grant(_TENANT, grant.grant_id)
        assert loaded is not None
        assert loaded.token_sha256 == grant.token_sha256
        assert len(loaded.token_sha256) == 64  # full SHA-256, never truncated
        assert loaded.consumed_at is None

    def test_get_unknown_grant_returns_none(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        assert InMemoryBreakGlassGrantStore().get_grant(_TENANT, str(uuid.uuid4())) is None

    def test_consume_succeeds_exactly_once(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        store = InMemoryBreakGlassGrantStore()
        grant = _grant()
        store.record_grant(grant)
        assert store.consume_grant(_TENANT, grant.token_sha256, request_id="req-1") is True
        assert store.consume_grant(_TENANT, grant.token_sha256, request_id="req-2") is False
        loaded = store.get_grant(_TENANT, grant.grant_id)
        assert loaded is not None
        assert loaded.consumed_at is not None
        assert loaded.consumed_request_id == "req-1"

    def test_consume_unknown_token_hash_returns_false(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        store = InMemoryBreakGlassGrantStore()
        assert store.consume_grant(_TENANT, "0" * 64, request_id=None) is False

    def test_consume_expired_grant_returns_false(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        store = InMemoryBreakGlassGrantStore()
        grant = _grant(issued_at=time.time() - 120, expires_at=time.time() - 60)
        store.record_grant(grant)
        assert store.consume_grant(_TENANT, grant.token_sha256, request_id=None) is False

    def test_consume_is_tenant_scoped(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        store = InMemoryBreakGlassGrantStore()
        grant = _grant()
        store.record_grant(grant)
        other_tenant = "22222222-2222-2222-2222-222222222222"
        assert store.consume_grant(other_tenant, grant.token_sha256, request_id=None) is False
        assert store.consume_grant(_TENANT, grant.token_sha256, request_id=None) is True

    def test_duplicate_token_hash_record_rejected(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore
        from idis.api.errors import IdisHttpError

        store = InMemoryBreakGlassGrantStore()
        grant = _grant()
        store.record_grant(grant)
        duplicate = _grant(token_sha256=grant.token_sha256)
        with pytest.raises(IdisHttpError) as exc_info:
            store.record_grant(duplicate)
        assert exc_info.value.code == "break_glass_grant_record_failed"

    def test_concurrent_consume_yields_exactly_one_success(self) -> None:
        from idis.api.break_glass_grants import InMemoryBreakGlassGrantStore

        store = InMemoryBreakGlassGrantStore()
        grant = _grant()
        store.record_grant(grant)

        thread_count = 8
        barrier = threading.Barrier(thread_count)
        results: list[bool] = []
        lock = threading.Lock()

        def _race(worker: int) -> None:
            barrier.wait()
            outcome = store.consume_grant(_TENANT, grant.token_sha256, request_id=f"req-{worker}")
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=_race, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1, f"expected exactly one success, got {results}"

    def test_seam_set_get_roundtrip_and_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from idis.api.break_glass_grants import (
            InMemoryBreakGlassGrantStore,
            build_default_break_glass_grant_store,
            get_break_glass_grant_store,
            set_break_glass_grant_store,
        )

        store = InMemoryBreakGlassGrantStore()
        set_break_glass_grant_store(store)
        assert get_break_glass_grant_store() is store

        monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
        assert isinstance(build_default_break_glass_grant_store(), InMemoryBreakGlassGrantStore)

    def test_durable_flag_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from idis.api.break_glass_grants import is_durable_break_glass_enabled

        monkeypatch.delenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", raising=False)
        assert is_durable_break_glass_enabled() is False
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        assert is_durable_break_glass_enabled() is True


@pytest.fixture
def bg_app(
    monkeypatch: pytest.MonkeyPatch, _reset_grant_store: None
) -> Iterator[tuple[TestClient, InMemoryAuditSink]]:
    """Full app: break-glass secret + admin/analyst keys + in-memory audit sink."""
    from idis.api.routes.deals import clear_deals_store

    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_json())
    monkeypatch.setenv("IDIS_BREAK_GLASS_SECRET", _SECRET)
    monkeypatch.delenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", raising=False)
    clear_deals_store()
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink, service_region="us-east-1")
    yield TestClient(app, raise_server_exceptions=False), sink
    clear_deals_store()


def _hdr(key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": key, "Content-Type": "application/json"}


def _create_deal(client: TestClient) -> str:
    resp = client.post(
        "/v1/deals",
        json={"name": "BG Deal", "company_name": "Acme"},
        headers=_hdr(_ADMIN_KEY),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["deal_id"])


def _issue(
    client: TestClient,
    deal_id: str,
    *,
    key: str = _ADMIN_KEY,
    justification: str = _JUSTIFICATION,
    duration_seconds: int | None = None,
) -> Any:
    body: dict[str, Any] = {"deal_id": deal_id, "justification": justification}
    if duration_seconds is not None:
        body["duration_seconds"] = duration_seconds
    return client.post("/v1/break-glass/grants", json=body, headers=_hdr(key))


class TestBreakGlassIssuanceRoute:
    """Unit B: ADMIN-only issuance route; grant recorded; break_glass.issued audited safely."""

    def test_admin_issuance_returns_grant_and_records_it(
        self, bg_app: tuple[TestClient, InMemoryAuditSink]
    ) -> None:
        from idis.api.break_glass import validate_break_glass_token
        from idis.api.break_glass_grants import get_break_glass_grant_store

        client, _sink = bg_app
        deal_id = _create_deal(client)
        resp = _issue(client, deal_id)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == {"grant_id", "token", "expires_at", "deal_id"}
        assert body["deal_id"] == deal_id

        # the token is a real break-glass credential bound to the REQUESTING admin + deal
        validation = validate_break_glass_token(
            body["token"], expected_tenant_id=_TENANT, expected_deal_id=deal_id
        )
        assert validation.valid, validation.error_code
        assert validation.token is not None
        assert validation.token.actor_id == _ADMIN_ACTOR
        assert validation.token.deal_id == deal_id

        # durably recorded through the seam with the FULL sha256 of the raw token
        grant = get_break_glass_grant_store().get_grant(_TENANT, body["grant_id"])
        assert grant is not None
        assert grant.token_sha256 == hashlib.sha256(body["token"].encode("utf-8")).hexdigest()
        assert grant.actor_id == _ADMIN_ACTOR
        assert grant.deal_id == deal_id
        assert grant.justification == _JUSTIFICATION
        assert grant.consumed_at is None

    def test_issuance_emits_critical_issued_audit_with_deal_resource_and_no_secrets(
        self, bg_app: tuple[TestClient, InMemoryAuditSink]
    ) -> None:
        client, sink = bg_app
        deal_id = _create_deal(client)
        resp = _issue(client, deal_id)
        assert resp.status_code == 201, resp.text
        token = resp.json()["token"]

        events = [e for e in sink.events if e.get("event_type") == "break_glass.issued"]
        assert len(events) == 1, f"expected exactly one break_glass.issued, got {len(events)}"
        event = events[0]
        assert event["severity"] == "CRITICAL"
        assert event["resource"]["resource_type"] == "deal"
        assert event["resource"]["resource_id"] == deal_id  # audit_resource_id = deal_id
        raw = json.dumps(event)
        assert token not in raw  # never the credential
        assert _JUSTIFICATION not in raw  # never the plaintext justification

    def test_non_admin_issuance_denied(self, bg_app: tuple[TestClient, InMemoryAuditSink]) -> None:
        client, _sink = bg_app
        deal_id = _create_deal(client)
        assert _issue(client, deal_id, key=_ANALYST_KEY).status_code == 403

    def test_short_justification_rejected_400(
        self, bg_app: tuple[TestClient, InMemoryAuditSink]
    ) -> None:
        client, _sink = bg_app
        deal_id = _create_deal(client)
        resp = _issue(client, deal_id, justification="too short")
        assert resp.status_code == 400, resp.text

    def test_unknown_deal_404_no_oracle(self, bg_app: tuple[TestClient, InMemoryAuditSink]) -> None:
        client, _sink = bg_app
        assert _issue(client, str(uuid.uuid4())).status_code == 404

    def test_duration_is_clamped_to_max(self, bg_app: tuple[TestClient, InMemoryAuditSink]) -> None:
        from datetime import datetime

        client, _sink = bg_app
        deal_id = _create_deal(client)
        before = time.time()
        resp = _issue(client, deal_id, duration_seconds=7200)
        assert resp.status_code == 201, resp.text
        expires = datetime.fromisoformat(resp.json()["expires_at"].replace("Z", "+00:00"))
        assert expires.timestamp() - before <= 3600 + 5  # core clamp, small slack


def _use_deal(client: TestClient, deal_id: str, *, key: str, token: str | None) -> Any:
    headers = _hdr(key)
    if token is not None:
        headers["X-IDIS-Break-Glass"] = token
    return client.get(f"/v1/deals/{deal_id}", headers=headers)


class TestDurableBreakGlassConsumption:
    """Unit C: flag-gated single-use consumption on the REAL deal-scoped request path."""

    def test_flag_on_issue_use_once_then_second_use_denied(
        self,
        bg_app: tuple[TestClient, InMemoryAuditSink],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        from idis.api.break_glass_grants import get_break_glass_grant_store

        client, _sink = bg_app
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        # break_glass.used is emitted by the core's own JSONL sink (not the app middleware sink)
        audit_log = tmp_path / "break_glass_audit.jsonl"
        monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log))
        deal_id = _create_deal(client)
        issued = _issue(client, deal_id)
        assert issued.status_code == 201, issued.text
        token = issued.json()["token"]
        grant_id = issued.json()["grant_id"]

        first = _use_deal(client, deal_id, key=_ADMIN_KEY, token=token)
        assert first.status_code == 200, first.text
        grant = get_break_glass_grant_store().get_grant(_TENANT, grant_id)
        assert grant is not None
        assert grant.consumed_at is not None  # consumption marked the grant

        # break_glass.used remains CRITICAL and fail-closed audited on the successful use
        used_events = [
            json.loads(line)
            for line in audit_log.read_text(encoding="utf-8").splitlines()
            if '"break_glass.used"' in line
        ]
        assert len(used_events) == 1
        assert used_events[0]["severity"] == "CRITICAL"
        assert token not in audit_log.read_text(encoding="utf-8")  # hash-only, never the token

        second = _use_deal(client, deal_id, key=_ADMIN_KEY, token=token)
        assert second.status_code == 403, second.text  # strict single-use
        assert second.json()["code"] == "BREAK_GLASS_GRANT_INVALID"

    def test_flag_on_unrecorded_valid_hmac_token_denied(
        self, bg_app: tuple[TestClient, InMemoryAuditSink], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _sink = bg_app
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        deal_id = _create_deal(client)
        # cryptographically valid, but never recorded as a durable grant
        stateless_token = create_break_glass_token(
            actor_id=_ADMIN_ACTOR,
            tenant_id=_TENANT,
            justification=_JUSTIFICATION,
            deal_id=deal_id,
        )
        resp = _use_deal(client, deal_id, key=_ADMIN_KEY, token=stateless_token)
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "BREAK_GLASS_GRANT_INVALID"

    def test_flag_on_assigned_admin_does_not_burn_grant_or_emit_used_audit(
        self,
        bg_app: tuple[TestClient, InMemoryAuditSink],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        # The grant is consumed AND break_glass.used is audited ONLY when the token actually
        # supplies the ABAC override. An admin who is assigned to the deal is allowed by
        # assignment: a token on the request must neither burn the grant nor fabricate a false
        # CRITICAL break_glass.used event (no override happened).
        from idis.api.break_glass_grants import get_break_glass_grant_store
        from tests.abac_seed import seed_deal_access

        client, _sink = bg_app
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        audit_log = tmp_path / "assigned_admin_audit.jsonl"
        monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log))
        deal_id = _create_deal(client)
        issued = _issue(client, deal_id)
        assert issued.status_code == 201, issued.text
        seed_deal_access(_TENANT, deal_id, _ADMIN_ACTOR)  # allowed by assignment, not break-glass

        resp = _use_deal(client, deal_id, key=_ADMIN_KEY, token=issued.json()["token"])
        assert resp.status_code == 200, resp.text
        grant = get_break_glass_grant_store().get_grant(_TENANT, issued.json()["grant_id"])
        assert grant is not None
        assert grant.consumed_at is None  # not burned: the override was not needed
        used_lines = (
            [
                line
                for line in audit_log.read_text(encoding="utf-8").splitlines()
                if '"break_glass.used"' in line
            ]
            if audit_log.exists()
            else []
        )
        assert used_lines == [], f"false break_glass.used for assigned admin: {len(used_lines)}"

    def test_flag_off_stateless_token_behavior_preserved(
        self, bg_app: tuple[TestClient, InMemoryAuditSink], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Legacy cutover guarantee: with the flag off, durable records are NOT the authorization
        # path - a valid stateless HMAC token (never recorded) still works exactly as today.
        client, _sink = bg_app
        monkeypatch.delenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", raising=False)
        deal_id = _create_deal(client)
        stateless_token = create_break_glass_token(
            actor_id=_ADMIN_ACTOR,
            tenant_id=_TENANT,
            justification=_JUSTIFICATION,
            deal_id=deal_id,
        )
        resp = _use_deal(client, deal_id, key=_ADMIN_KEY, token=stateless_token)
        assert resp.status_code == 200, resp.text

    def test_flag_off_grant_is_not_consumed(
        self, bg_app: tuple[TestClient, InMemoryAuditSink], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from idis.api.break_glass_grants import get_break_glass_grant_store

        client, _sink = bg_app
        monkeypatch.delenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", raising=False)
        deal_id = _create_deal(client)
        issued = _issue(client, deal_id)
        assert issued.status_code == 201, issued.text

        resp = _use_deal(client, deal_id, key=_ADMIN_KEY, token=issued.json()["token"])
        assert resp.status_code == 200, resp.text
        grant = get_break_glass_grant_store().get_grant(_TENANT, issued.json()["grant_id"])
        assert grant is not None
        assert grant.consumed_at is None  # durable consumption inactive when the flag is off

    def test_flag_on_audit_failure_after_consume_denies_and_burns(
        self, bg_app: tuple[TestClient, InMemoryAuditSink], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ordering: consume first (atomic claim), then CRITICAL audit; audit failure denies and
        # the grant stays burned (safe direction - re-issue is cheap, unaudited access is not).
        import idis.api.middleware.rbac as rbac_module
        from idis.api.break_glass_grants import get_break_glass_grant_store
        from idis.api.errors import IdisHttpError

        client, _sink = bg_app
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        deal_id = _create_deal(client)
        issued = _issue(client, deal_id)
        assert issued.status_code == 201, issued.text

        def _explode(**_kwargs: Any) -> None:
            raise IdisHttpError(
                status_code=500, code="audit_emit_failed", message="audit sink down"
            )

        monkeypatch.setattr(rbac_module, "emit_break_glass_audit_event", _explode)
        resp = _use_deal(client, deal_id, key=_ADMIN_KEY, token=issued.json()["token"])
        assert resp.status_code == 500, resp.text
        assert resp.json()["code"] == "BREAK_GLASS_AUDIT_FAILED"
        grant = get_break_glass_grant_store().get_grant(_TENANT, issued.json()["grant_id"])
        assert grant is not None
        assert grant.consumed_at is not None  # burned before the audit attempt

    def test_flag_on_store_error_fails_closed(
        self, bg_app: tuple[TestClient, InMemoryAuditSink], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from idis.api.break_glass_grants import set_break_glass_grant_store
        from idis.api.errors import IdisHttpError

        client, _sink = bg_app
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        deal_id = _create_deal(client)
        token = create_break_glass_token(
            actor_id=_ADMIN_ACTOR,
            tenant_id=_TENANT,
            justification=_JUSTIFICATION,
            deal_id=deal_id,
        )

        class _ExplodingStore:
            def consume_grant(
                self, tenant_id: str, token_sha256: str, *, request_id: str | None = None
            ) -> bool:
                raise IdisHttpError(
                    status_code=403, code="BREAK_GLASS_RESOLUTION_FAILED", message="Access denied."
                )

        set_break_glass_grant_store(_ExplodingStore())  # type: ignore[arg-type]
        resp = _use_deal(client, deal_id, key=_ADMIN_KEY, token=token)
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "BREAK_GLASS_RESOLUTION_FAILED"

    def test_analyst_with_token_still_denied(
        self, bg_app: tuple[TestClient, InMemoryAuditSink], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Break-glass remains ADMIN-only at the ABAC layer regardless of durable mode.
        client, _sink = bg_app
        monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
        deal_id = _create_deal(client)
        token = create_break_glass_token(
            actor_id=_ANALYST_ACTOR,
            tenant_id=_TENANT,
            justification=_JUSTIFICATION,
            deal_id=deal_id,
        )
        resp = _use_deal(client, deal_id, key=_ANALYST_KEY, token=token)
        assert resp.status_code == 403, resp.text
