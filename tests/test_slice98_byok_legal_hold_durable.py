"""Slice98 Task 6 - durable BYOK registry + legal holds (hermetic).

RED-first. Approved decisions: KMS boundary = durable POLICY METADATA + documented seam only (no
crypto code, no fake KMS, no cloud SDK - key material lives solely in the customer's KMS); audit is
dual-layer (core audit-fatal domain events unchanged + validated request-shaped AuditMiddleware
events for the new management routes).

Unit A proves the store seams and fail-closed resolution:
- Seam functions (get_/set_/reset_/build_default_*) for both registries; the core workflow
  functions (configure/rotate/revoke key, apply/lift hold, require_key_active,
  block_deletion_if_held) consult the seam default - so the REAL ComplianceEnforcedStore path
  resolves durable state with zero call-site changes.
- Postgres twins fail CLOSED on resolution errors: a DB/registry error DENIES (403
  BYOK_RESOLUTION_FAILED / LEGAL_HOLD_RESOLUTION_FAILED), never degrading to "no policy" /
  "no hold"; writes fail loudly (500) leaving no durable state.
- lift_hold becomes tenant-scoped (get_for_tenant): a cross-tenant hold_id is a uniform 404
  (same as nonexistent - no existence oracle, ADR-011; an RLS-backed twin cannot even see the
  row). Previously this leaked existence via 403 ACCESS_DENIED.

No key material, plaintext hold reasons, or raw aliases are ever stored in events or logs.
PYTHONPATH is pinned to this worktree's src for every run.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import InMemoryAuditSink

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_REASON = "Litigation hold pending case 2026-CV-1138 discovery."


def _ctx(tenant_id: str = _TENANT_A, actor_id: str = "compliance-admin-1") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        name="Compliance Admin",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


@pytest.fixture
def _reset_seams(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate both compliance-registry seams; force in-memory defaults (no Postgres env)."""
    from idis.compliance.byok import reset_byok_policy_registry
    from idis.compliance.retention import reset_legal_hold_registry

    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    reset_byok_policy_registry()
    reset_legal_hold_registry()
    yield
    reset_byok_policy_registry()
    reset_legal_hold_registry()


@pytest.mark.usefixtures("_reset_seams")
class TestByokRegistrySeam:
    """Unit A: BYOK registry seam + core functions consulting it."""

    def test_seam_set_get_roundtrip(self) -> None:
        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            get_byok_policy_registry,
            set_byok_policy_registry,
        )

        registry = BYOKPolicyRegistry()
        set_byok_policy_registry(registry)
        assert get_byok_policy_registry() is registry

    def test_build_default_in_memory_when_postgres_unconfigured(self) -> None:
        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            build_default_byok_policy_registry,
        )

        assert isinstance(build_default_byok_policy_registry(), BYOKPolicyRegistry)

    def test_get_caches_single_instance(self) -> None:
        from idis.compliance.byok import get_byok_policy_registry

        assert get_byok_policy_registry() is get_byok_policy_registry()

    def test_configure_key_writes_through_seam_default(self) -> None:
        from idis.compliance.byok import configure_key, get_byok_policy_registry

        sink = InMemoryAuditSink()
        configure_key(_ctx(), "tenant-a-kms-alias", sink)  # no explicit registry
        policy = get_byok_policy_registry().get(_TENANT_A)
        assert policy is not None
        assert policy.key_alias == "tenant-a-kms-alias"

    def test_require_key_active_reads_seam_default(self) -> None:
        from idis.compliance.byok import (
            DataClass,
            configure_key,
            require_key_active,
            revoke_key,
        )

        sink = InMemoryAuditSink()
        configure_key(_ctx(), "tenant-a-kms-alias", sink)
        revoke_key(_ctx(), sink)  # seam default now holds a REVOKED policy
        with pytest.raises(IdisHttpError) as exc_info:
            require_key_active(_ctx(), DataClass.CLASS_2)  # no explicit registry
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "BYOK_KEY_REVOKED"


@pytest.mark.usefixtures("_reset_seams")
class TestLegalHoldSeam:
    """Unit A: legal-hold registry seam, tenant-scoped lookup, oracle-free lift."""

    def test_seam_set_get_roundtrip_and_default(self) -> None:
        from idis.compliance.retention import (
            LegalHoldRegistry,
            build_default_legal_hold_registry,
            get_legal_hold_registry,
            set_legal_hold_registry,
        )

        registry = LegalHoldRegistry()
        set_legal_hold_registry(registry)
        assert get_legal_hold_registry() is registry
        assert isinstance(build_default_legal_hold_registry(), LegalHoldRegistry)

    def test_get_for_tenant_is_tenant_scoped(self) -> None:
        from idis.compliance.retention import HoldTarget, LegalHoldRegistry, apply_hold

        registry = LegalHoldRegistry()
        hold = apply_hold(
            _ctx(), HoldTarget.DOCUMENT, "doc-1", _REASON, InMemoryAuditSink(), registry
        )
        assert registry.get_for_tenant(_TENANT_A, hold.hold_id) is not None
        assert registry.get_for_tenant(_TENANT_B, hold.hold_id) is None

    def test_block_deletion_consults_seam_default(self) -> None:
        from idis.compliance.retention import HoldTarget, apply_hold, block_deletion_if_held

        apply_hold(_ctx(), HoldTarget.DOCUMENT, "doc-held", _REASON, InMemoryAuditSink())
        with pytest.raises(IdisHttpError) as exc_info:
            block_deletion_if_held(_ctx(), HoldTarget.DOCUMENT, "doc-held")
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"

    def test_lift_cross_tenant_hold_is_uniform_404_no_oracle(self) -> None:
        # A hold_id belonging to another tenant answers exactly like a nonexistent one (404):
        # the tenant-scoped lookup cannot see it, so there is no existence oracle. An RLS-backed
        # durable twin enforces the same shape at the database.
        from idis.compliance.retention import HoldTarget, LegalHoldRegistry, apply_hold, lift_hold

        registry = LegalHoldRegistry()
        sink = InMemoryAuditSink()
        hold = apply_hold(_ctx(_TENANT_A), HoldTarget.DEAL, "deal-1", _REASON, sink, registry)
        with pytest.raises(IdisHttpError) as exc_info:
            lift_hold(_ctx(_TENANT_B, actor_id="admin-b"), hold.hold_id, sink, registry)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "HOLD_NOT_FOUND"

    def test_lift_own_tenant_hold_still_works(self) -> None:
        from idis.compliance.retention import HoldTarget, LegalHoldRegistry, apply_hold, lift_hold

        registry = LegalHoldRegistry()
        sink = InMemoryAuditSink()
        hold = apply_hold(_ctx(), HoldTarget.DEAL, "deal-2", _REASON, sink, registry)
        lifted = lift_hold(_ctx(), hold.hold_id, sink, registry)
        assert lifted.lifted_at is not None
        assert registry.has_active_hold(_TENANT_A, HoldTarget.DEAL, "deal-2") is False


@pytest.mark.usefixtures("_reset_seams")
class TestFailClosedResolution:
    """Unit A: resolution errors DENY - never degrade to 'no policy' / 'no hold'."""

    def test_pg_byok_get_without_database_denies(self) -> None:
        from idis.compliance.byok import PostgresBYOKPolicyRegistry

        with pytest.raises(IdisHttpError) as exc_info:
            PostgresBYOKPolicyRegistry().get(_TENANT_A)
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "BYOK_RESOLUTION_FAILED"

    def test_require_key_active_with_broken_registry_denies_not_allows(self) -> None:
        # The dangerous wrong outcome: a resolution error surfacing as policy-is-None would
        # ALLOW Class2/3 access (BYOK optional). It must deny instead.
        from idis.compliance.byok import DataClass, PostgresBYOKPolicyRegistry, require_key_active

        with pytest.raises(IdisHttpError) as exc_info:
            require_key_active(_ctx(), DataClass.CLASS_2, PostgresBYOKPolicyRegistry())
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "BYOK_RESOLUTION_FAILED"

    def test_pg_byok_set_without_database_fails_loudly(self) -> None:
        from idis.compliance.byok import BYOKPolicy, PostgresBYOKPolicyRegistry

        policy = BYOKPolicy(tenant_id=_TENANT_A, key_alias="alias-a")
        with pytest.raises(IdisHttpError) as exc_info:
            PostgresBYOKPolicyRegistry().set(policy)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "BYOK_POLICY_WRITE_FAILED"

    def test_pg_hold_check_without_database_denies(self) -> None:
        from idis.compliance.retention import HoldTarget, PostgresLegalHoldRegistry

        with pytest.raises(IdisHttpError) as exc_info:
            PostgresLegalHoldRegistry().has_active_hold(_TENANT_A, HoldTarget.DOCUMENT, "doc-1")
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "LEGAL_HOLD_RESOLUTION_FAILED"

    def test_block_deletion_with_broken_registry_denies_not_allows(self) -> None:
        # A hold-resolution error must block the deletion, never read as "no active hold".
        from idis.compliance.retention import (
            HoldTarget,
            PostgresLegalHoldRegistry,
            block_deletion_if_held,
        )

        with pytest.raises(IdisHttpError) as exc_info:
            block_deletion_if_held(
                _ctx(), HoldTarget.DOCUMENT, "doc-1", PostgresLegalHoldRegistry()
            )
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "LEGAL_HOLD_RESOLUTION_FAILED"

    def test_pg_hold_add_without_database_fails_loudly(self) -> None:
        from idis.compliance.retention import HoldTarget, LegalHold, PostgresLegalHoldRegistry

        hold = LegalHold(
            hold_id=str(uuid.uuid4()),
            tenant_id=_TENANT_A,
            target_type=HoldTarget.DOCUMENT,
            target_id="doc-1",
            reason_hash="a" * 64,
            reason_length=32,
            applied_at=datetime.now(UTC),
            applied_by="compliance-admin-1",
        )
        with pytest.raises(IdisHttpError) as exc_info:
            PostgresLegalHoldRegistry().add(hold)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "LEGAL_HOLD_WRITE_FAILED"


# --- Units B + C: management routes, dual-layer audit, and the real compliance path ---

_ADMIN_A_KEY = "byok-admin-a"
_ANALYST_A_KEY = "byok-analyst-a"
_ADMIN_B_KEY = "byok-admin-b"
_ALIAS = "tenant-a-kms-alias-2026"


def _api_keys_json() -> str:
    import json

    def _entry(tenant_id: str, actor: str, roles: list[str]) -> dict[str, object]:
        return {
            "tenant_id": tenant_id,
            "actor_id": actor,
            "name": actor,
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": roles,
        }

    return json.dumps(
        {
            _ADMIN_A_KEY: _entry(_TENANT_A, "admin-a", ["ADMIN"]),
            _ANALYST_A_KEY: _entry(_TENANT_A, "analyst-a", ["ANALYST"]),
            _ADMIN_B_KEY: _entry(_TENANT_B, "admin-b", ["ADMIN"]),
        }
    )


@pytest.fixture
def compliance_app(monkeypatch: pytest.MonkeyPatch, _reset_seams: None) -> Iterator[tuple]:
    """Full app with in-memory audit sink and seam-backed compliance registries."""
    from fastapi.testclient import TestClient

    from idis.api.auth import IDIS_API_KEYS_ENV
    from idis.api.main import create_app
    from idis.api.routes.deals import clear_deals_store
    from idis.api.routes.documents import clear_document_store

    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_json())
    clear_deals_store()
    clear_document_store()
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink, service_region="us-east-1")
    yield TestClient(app, raise_server_exceptions=False), sink
    clear_deals_store()
    clear_document_store()


def _hdr(key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": key, "Content-Type": "application/json"}


def _events(sink: InMemoryAuditSink, event_type: str) -> list[dict]:
    return [e for e in sink.events if e.get("event_type") == event_type]


class TestByokManagementRoutes:
    """Unit B: ADMIN-only BYOK key routes; hash-only exposure; dual-layer audit."""

    def test_configure_returns_hash_only_and_dual_audits(self, compliance_app: tuple) -> None:
        import json as jsonlib

        from idis.compliance.byok import get_byok_policy_registry
        from idis.validators.audit_event_validator import validate_audit_event

        client, sink = compliance_app
        resp = client.post("/v1/byok/key", json={"key_alias": _ALIAS}, headers=_hdr(_ADMIN_A_KEY))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == {"key_alias_hash", "key_state"}
        assert body["key_state"] == "ACTIVE"
        assert len(body["key_alias_hash"]) == 16
        assert _ALIAS not in resp.text  # raw alias never in responses

        policy = get_byok_policy_registry().get(_TENANT_A)
        assert policy is not None

        events = _events(sink, "byok.key.configured")
        assert len(events) == 2, f"dual-layer audit expected 2 events, got {len(events)}"
        # Both layers are POST now (Task6 audit-core repair); disambiguate by path.
        middleware_events = [e for e in events if e["request"]["path"].startswith("/v1")]
        core_events = [e for e in events if e["request"]["path"].startswith("/internal/")]
        assert len(middleware_events) == 1 and len(core_events) == 1
        for event in events:  # BOTH layers must now be schema-valid
            assert validate_audit_event(event).passed
        assert middleware_events[0]["resource"]["resource_type"] == "byok_key"
        assert middleware_events[0]["resource"]["resource_id"] == body["key_alias_hash"]
        for event in events:
            assert _ALIAS not in jsonlib.dumps(event)  # raw alias never in audit

    def test_configure_invalid_alias_400(self, compliance_app: tuple) -> None:
        client, _sink = compliance_app
        resp = client.post(
            "/v1/byok/key", json={"key_alias": "bad alias!"}, headers=_hdr(_ADMIN_A_KEY)
        )
        assert resp.status_code == 400, resp.text

    def test_non_admin_denied(self, compliance_app: tuple) -> None:
        client, _sink = compliance_app
        assert (
            client.post(
                "/v1/byok/key", json={"key_alias": _ALIAS}, headers=_hdr(_ANALYST_A_KEY)
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/legal-holds",
                json={"target_type": "DOCUMENT", "target_id": "d1", "reason": _REASON},
                headers=_hdr(_ANALYST_A_KEY),
            ).status_code
            == 403
        )

    def test_rotate_requires_existing_key_then_rotates(self, compliance_app: tuple) -> None:
        client, _sink = compliance_app
        missing = client.post(
            "/v1/byok/key/rotate", json={"key_alias": "next-alias"}, headers=_hdr(_ADMIN_A_KEY)
        )
        assert missing.status_code == 404, missing.text

        first = client.post("/v1/byok/key", json={"key_alias": _ALIAS}, headers=_hdr(_ADMIN_A_KEY))
        rotated = client.post(
            "/v1/byok/key/rotate", json={"key_alias": "next-alias"}, headers=_hdr(_ADMIN_A_KEY)
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["key_state"] == "ACTIVE"
        assert rotated.json()["key_alias_hash"] != first.json()["key_alias_hash"]

    def test_revoke_then_class2_access_denied(self, compliance_app: tuple) -> None:
        from idis.compliance.byok import DataClass, require_key_active

        client, _sink = compliance_app
        missing = client.post("/v1/byok/key/revoke", headers=_hdr(_ADMIN_A_KEY))
        assert missing.status_code == 404, missing.text

        client.post("/v1/byok/key", json={"key_alias": _ALIAS}, headers=_hdr(_ADMIN_A_KEY))
        revoked = client.post("/v1/byok/key/revoke", headers=_hdr(_ADMIN_A_KEY))
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["key_state"] == "REVOKED"

        with pytest.raises(IdisHttpError) as exc_info:
            require_key_active(_ctx(), DataClass.CLASS_2)  # seam default now holds REVOKED
        assert exc_info.value.code == "BYOK_KEY_REVOKED"

    def test_write_failure_leaves_no_state_and_proves_audit_ordering(
        self, compliance_app: tuple
    ) -> None:
        # The core emits its audit-fatal domain event BEFORE the registry write. A write failure
        # must (a) fail the request, (b) leave no durable policy behind, and (c) leave only
        # truthful records of the ATTEMPT: the core domain event plus the middleware's mutation
        # record carrying the 500 outcome (5xx mutations are audited; only 4xx are skipped).
        from idis.compliance.byok import (
            BYOKPolicy,
            BYOKPolicyRegistry,
            set_byok_policy_registry,
        )

        client, sink = compliance_app

        class _WriteExplodingRegistry(BYOKPolicyRegistry):
            def set(self, policy: BYOKPolicy) -> None:
                raise IdisHttpError(
                    status_code=500,
                    code="BYOK_POLICY_WRITE_FAILED",
                    message="BYOK policy could not be persisted",
                )

        exploding = _WriteExplodingRegistry()
        set_byok_policy_registry(exploding)
        resp = client.post("/v1/byok/key", json={"key_alias": _ALIAS}, headers=_hdr(_ADMIN_A_KEY))
        assert resp.status_code == 500, resp.text
        assert exploding.get(_TENANT_A) is None  # no durable state behind
        events = _events(sink, "byok.key.configured")
        # Two truthful records of the failed ATTEMPT, zero state: the core domain event was
        # emitted BEFORE the write (ordering proof), and the middleware records the mutation
        # attempt with its 500 outcome (5xx mutations are audited; only 4xx are skipped).
        core_events = [e for e in events if e["request"]["path"].startswith("/internal/")]
        middleware_events = [e for e in events if e["request"]["path"].startswith("/v1")]
        assert len(core_events) == 1  # audit-before-write: emitted despite the failed write
        assert len(middleware_events) == 1
        assert middleware_events[0]["request"]["status_code"] == 500
        import json as jsonlib

        for event in events:
            assert _ALIAS not in jsonlib.dumps(event)


class TestLegalHoldRoutes:
    """Unit B: ADMIN-only hold routes; reasons never leak; oracle-free lift."""

    def test_apply_hold_201_reason_never_leaks(
        self, compliance_app: tuple, caplog: pytest.LogCaptureFixture
    ) -> None:
        import json as jsonlib

        from idis.validators.audit_event_validator import validate_audit_event

        client, sink = compliance_app
        with caplog.at_level("DEBUG"):
            resp = client.post(
                "/v1/legal-holds",
                json={"target_type": "DOCUMENT", "target_id": "doc-42", "reason": _REASON},
                headers=_hdr(_ADMIN_A_KEY),
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == {"hold_id", "target_type", "target_id", "applied_at"}
        assert _REASON not in resp.text  # plaintext reason never in responses

        events = _events(sink, "legal_hold.applied")
        assert len(events) == 2  # dual-layer: middleware (/v1 route) + core (/internal path)
        middleware_events = [e for e in events if e["request"]["path"].startswith("/v1")]
        assert len(middleware_events) == 1
        for event in events:  # BOTH layers must now be schema-valid
            assert validate_audit_event(event).passed
        assert middleware_events[0]["severity"] == "CRITICAL"
        assert middleware_events[0]["resource"]["resource_type"] == "legal_hold"
        assert middleware_events[0]["resource"]["resource_id"] == body["hold_id"]
        for event in events:
            assert _REASON not in jsonlib.dumps(event)  # reason never in audit
        assert _REASON not in caplog.text  # reason never in logs

    def test_apply_hold_invalid_target_type_rejected(self, compliance_app: tuple) -> None:
        client, _sink = compliance_app
        resp = client.post(
            "/v1/legal-holds",
            json={"target_type": "SPACESHIP", "target_id": "x", "reason": _REASON},
            headers=_hdr(_ADMIN_A_KEY),
        )
        assert resp.status_code == 422, resp.text

    def test_apply_hold_empty_reason_400(self, compliance_app: tuple) -> None:
        client, _sink = compliance_app
        resp = client.post(
            "/v1/legal-holds",
            json={"target_type": "DOCUMENT", "target_id": "doc-1", "reason": "   "},
            headers=_hdr(_ADMIN_A_KEY),
        )
        assert resp.status_code == 400, resp.text

    def test_lift_hold_roundtrip_and_uniform_404(self, compliance_app: tuple) -> None:
        client, _sink = compliance_app
        applied = client.post(
            "/v1/legal-holds",
            json={"target_type": "DEAL", "target_id": "deal-7", "reason": _REASON},
            headers=_hdr(_ADMIN_A_KEY),
        )
        assert applied.status_code == 201, applied.text
        hold_id = applied.json()["hold_id"]

        # cross-tenant lift answers exactly like a nonexistent hold (no oracle)
        cross = client.post(f"/v1/legal-holds/{hold_id}/lift", headers=_hdr(_ADMIN_B_KEY))
        assert cross.status_code == 404, cross.text
        unknown = client.post(f"/v1/legal-holds/{uuid.uuid4()}/lift", headers=_hdr(_ADMIN_A_KEY))
        assert unknown.status_code == 404, unknown.text

        lifted = client.post(f"/v1/legal-holds/{hold_id}/lift", headers=_hdr(_ADMIN_A_KEY))
        assert lifted.status_code == 200, lifted.text
        assert lifted.json()["hold_id"] == hold_id
        assert lifted.json()["lifted_at"] is not None


class TestComplianceRealPath:
    """Unit C: the routes govern the REAL storage/deletion boundary via the seam defaults."""

    def _create_deal(self, client) -> str:  # type: ignore[no-untyped-def]
        from tests.abac_seed import seed_deal_access

        resp = client.post(
            "/v1/deals",
            json={"name": "Compliance Deal", "company_name": "Acme"},
            headers=_hdr(_ADMIN_A_KEY),
        )
        assert resp.status_code == 201, resp.text
        deal_id = str(resp.json()["deal_id"])
        seed_deal_access(_TENANT_A, deal_id, "admin-a")
        return deal_id

    def test_route_revoked_key_denies_real_document_content_read(
        self, compliance_app: tuple, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        # No explicit registry anywhere: the storage boundary resolves BYOK state through the
        # seam default, so the route-driven revoke governs the REAL document read path.
        from fastapi.testclient import TestClient

        from idis.api.main import create_app
        from idis.audit.sink import InMemoryAuditSink as _Sink
        from idis.idempotency.store import SqliteIdempotencyStore
        from idis.services.ingestion import IngestionService
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        sink = _Sink()
        compliant_store = ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path)
        )
        app = create_app(
            audit_sink=sink,
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            ingestion_service=IngestionService(compliant_store=compliant_store, audit_sink=sink),
            service_region="us-east-1",
        )
        client = TestClient(app, raise_server_exceptions=False)
        deal_id = self._create_deal(client)

        created = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers=_hdr(_ADMIN_A_KEY),
            json={
                "doc_type": "PITCH_DECK",
                "title": "Task6 BYOK Real Path",
                "uri": "idis://documents/task6-byok.pdf",
                "auto_ingest": False,
            },
        )
        assert created.status_code == 201, created.text
        doc_id = created.json()["doc_id"]

        assert (
            client.post(
                "/v1/byok/key", json={"key_alias": _ALIAS}, headers=_hdr(_ADMIN_A_KEY)
            ).status_code
            == 201
        )
        compliant_store.put(
            tenant_ctx=_ctx(actor_id="admin-a"),
            key="documents/task6-byok.pdf",
            data=b"task6 byok real-path content",
        )

        assert client.post("/v1/byok/key/revoke", headers=_hdr(_ADMIN_A_KEY)).status_code == 200
        denied = client.get(f"/v1/documents/{doc_id}", headers=_hdr(_ADMIN_A_KEY))
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "BYOK_KEY_REVOKED"

    def test_route_applied_hold_blocks_real_delete_until_lifted(
        self, compliance_app: tuple
    ) -> None:
        client, _sink = compliance_app
        deal_id = self._create_deal(client)
        created = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers=_hdr(_ADMIN_A_KEY),
            json={
                "doc_type": "PITCH_DECK",
                "title": "Task6 Hold Real Path",
                "uri": "idis://documents/task6-hold.pdf",
                "auto_ingest": False,
            },
        )
        assert created.status_code == 201, created.text
        doc_id = created.json()["doc_id"]

        applied = client.post(
            "/v1/legal-holds",
            json={"target_type": "ARTIFACT", "target_id": doc_id, "reason": _REASON},
            headers=_hdr(_ADMIN_A_KEY),
        )
        assert applied.status_code == 201, applied.text

        blocked = client.delete(f"/v1/documents/{doc_id}", headers=_hdr(_ADMIN_A_KEY))
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["code"] == "DELETION_BLOCKED_BY_HOLD"

        lifted = client.post(
            f"/v1/legal-holds/{applied.json()['hold_id']}/lift", headers=_hdr(_ADMIN_A_KEY)
        )
        assert lifted.status_code == 200, lifted.text
        allowed = client.delete(f"/v1/documents/{doc_id}", headers=_hdr(_ADMIN_A_KEY))
        assert allowed.status_code in (200, 204), allowed.text
