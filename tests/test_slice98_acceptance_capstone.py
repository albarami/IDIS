"""Slice98 acceptance capstone (Task 9) - drift guards + control-surface pins.

Hermetic regression guards that lock the Slice98 closeout so the classes of defect fixed across
this slice cannot silently return:

- CI drift guard: the ACTUAL postgres-integration pytest INVOCATION in .github/workflows/ci.yml
  must include every tests/test_slice98_*_postgres.py on disk (parsing the executed command, not
  the echo text). A future durable test that is not wired to CI fails this test.
- Migration chain linearity: exactly one head, no duplicate revisions, current head 0030.
- Audit-contract surface: the Slice98 audit event prefixes and resource types are present in BOTH
  the Python validator and the JSON schema (they are validated together at emit time).
- Operation wiring: the Slice98 compliance/security operationIds are ADMIN-only in policy and
  mapped in the audit middleware.
- Compliance core-audit convention (the Task 6 repair, generalized): EVERY core compliance domain
  emitter (BYOK, legal hold, erasure, export, retention janitor) produces a schema-valid event
  that uses method POST + an /internal/... path + a {safe, hashes, refs} payload.

PYTHONPATH is pinned to this worktree's src for every run.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from idis.api.auth import TenantContext
from idis.audit.sink import InMemoryAuditSink
from idis.validators.audit_event_validator import (
    VALID_EVENT_PREFIXES,
    VALID_RESOURCE_TYPES,
    validate_audit_event,
)

_REPO = Path(__file__).resolve().parent.parent
_TESTS = _REPO / "tests"
_TENANT = "11111111-1111-1111-1111-111111111111"
_REASON = "Data subject compliance action under contract clause 9.2."


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=_TENANT,
        actor_id="compliance-admin",
        name="Compliance Admin",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


def _slice98_pg_files() -> set[str]:
    return {f"tests/{p.name}" for p in _TESTS.glob("test_slice98_*_postgres.py")}


def _ci_pytest_invocation_files() -> set[str]:
    """Extract the test files from the ACTUAL pytest command in the postgres-integration job.

    Parses the executed ``pytest ... --junitxml=...`` line (not the human-readable echo), so the
    guard fails if the command that CI actually runs omits a durable test.
    """
    ci_text = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    invocation_lines = [
        line
        for line in ci_text.splitlines()
        if line.strip().startswith("pytest ")
        and "--junitxml" in line
        and "postgres_integration" in line
    ]
    assert len(invocation_lines) == 1, (
        f"expected exactly one postgres-integration pytest invocation, found "
        f"{len(invocation_lines)}"
    )
    return set(re.findall(r"tests/[\w./-]+\.py", invocation_lines[0]))


class TestCiPostgresDriftGuard:
    """Amendment 1: assert the executed pytest command, not the echo text."""

    def test_ci_lists_every_slice98_postgres_file_in_the_invocation(self) -> None:
        on_disk = _slice98_pg_files()
        assert len(on_disk) == 7, f"expected 7 Slice98 PG files, found {sorted(on_disk)}"
        in_ci = _ci_pytest_invocation_files()
        missing = on_disk - in_ci
        assert missing == set(), (
            f"Slice98 durable tests missing from the CI postgres-integration pytest invocation: "
            f"{sorted(missing)} - add them to the pytest command in .github/workflows/ci.yml"
        )

    def test_echo_and_invocation_are_consistent(self) -> None:
        # The human-readable echo must not advertise a file the command does not actually run.
        ci_text = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        echo_lines = [line for line in ci_text.splitlines() if 'echo "Test files:' in line]
        assert echo_lines, "postgres-integration echo line not found"
        invocation = _ci_pytest_invocation_files()
        for slice98_file in _slice98_pg_files():
            bare = slice98_file.removeprefix("tests/")
            assert bare in echo_lines[0], f"{bare} missing from the CI echo summary"
            assert slice98_file in invocation


class TestMigrationChainLinearity:
    """Migration drift guard: single linear head at 0030."""

    def _revisions(self) -> list[tuple[str, str | None]]:
        versions = _REPO / "src" / "idis" / "persistence" / "migrations" / "versions"
        pairs: list[tuple[str, str | None]] = []
        for path in versions.glob("0*.py"):
            text = path.read_text(encoding="utf-8")
            rev = re.search(r'^revision = "([^"]+)"', text, re.MULTILINE)
            down = re.search(r'^down_revision = (None|"([^"]+)")', text, re.MULTILINE)
            assert rev is not None, f"{path.name} has no revision"
            assert down is not None, f"{path.name} has no down_revision"
            pairs.append((rev.group(1), down.group(2)))
        return pairs

    def test_single_head_is_0030_and_chain_is_linear(self) -> None:
        pairs = self._revisions()
        revisions = [r for r, _ in pairs]
        assert len(revisions) == len(set(revisions)), "duplicate migration revisions"
        downs = {d for _, d in pairs if d is not None}
        heads = set(revisions) - downs
        assert heads == {"0030"}, f"expected single head 0030, found {sorted(heads)}"
        # exactly one root (down_revision None) -> linear chain, no branches
        roots = [r for r, d in pairs if d is None]
        assert len(roots) == 1, f"expected one root migration, found {roots}"


class TestAuditContractSurface:
    """The Slice98 audit additions are present in BOTH validator and JSON schema."""

    def test_resource_types_in_validator_and_schema(self) -> None:
        slice98_types = {
            "group",
            "session",
            "byok_key",
            "legal_hold",
            "retention_sweep",
            "erasure_request",
            "compliance_export",
        }
        assert slice98_types <= VALID_RESOURCE_TYPES
        schema = json.loads(
            (_REPO / "schemas" / "audit_event.schema.json").read_text(encoding="utf-8")
        )
        enum = set(schema["properties"]["resource"]["properties"]["resource_type"]["enum"])
        assert slice98_types <= enum, f"missing from schema enum: {slice98_types - enum}"

    def test_event_prefixes_registered(self) -> None:
        assert {
            "auth.",
            "rbac.",
            "break_glass.",
            "byok.",
            "legal_hold.",
            "retention.",
            "erasure.",
            "export.",
        } <= VALID_EVENT_PREFIXES


class TestSlice98OperationsWired:
    """The Slice98 compliance/security operationIds are ADMIN-only and audited."""

    _OPERATIONS = (
        "createBreakGlassGrant",
        "configureByokKey",
        "rotateByokKey",
        "revokeByokKey",
        "applyLegalHold",
        "liftLegalHold",
        "createErasureRequest",
        "executeErasureRequest",
        "createComplianceExport",
    )

    def test_admin_only_in_policy(self) -> None:
        from idis.api.policy import ADMIN_ONLY, POLICY_RULES

        for op in self._OPERATIONS:
            assert op in POLICY_RULES, f"{op} missing from POLICY_RULES"
            rule = POLICY_RULES[op]
            assert rule.allowed_roles == ADMIN_ONLY, f"{op} is not ADMIN-only"
            assert rule.is_mutation is True
            assert rule.is_deal_scoped is False

    def test_audit_mapped_in_middleware(self) -> None:
        from idis.api.middleware.audit import OPERATION_ID_TO_EVENT_TYPE

        for op in self._OPERATIONS:
            assert op in OPERATION_ID_TO_EVENT_TYPE, f"{op} missing from audit map"
            event_type, severity, resource_type = OPERATION_ID_TO_EVENT_TYPE[op]
            assert event_type and severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
            assert resource_type in VALID_RESOURCE_TYPES


def _collect_core_events() -> list[dict[str, Any]]:
    """Drive every core compliance emitter and return the events they emit."""
    events: list[dict[str, Any]] = []

    # BYOK: configure -> rotate -> revoke
    from idis.compliance.byok import (
        BYOKPolicyRegistry,
        configure_key,
        revoke_key,
        rotate_key,
    )

    byok_sink = InMemoryAuditSink()
    reg = BYOKPolicyRegistry()
    configure_key(_ctx(), "kms-alias-1", byok_sink, reg)
    rotate_key(_ctx(), "kms-alias-2", byok_sink, reg)
    revoke_key(_ctx(), byok_sink, reg)
    events.extend(byok_sink.events)

    # Legal hold: apply -> lift
    from idis.compliance.retention import (
        HoldTarget,
        LegalHoldRegistry,
        apply_hold,
        lift_hold,
    )

    hold_sink = InMemoryAuditSink()
    hold_reg = LegalHoldRegistry()
    hold = apply_hold(_ctx(), HoldTarget.DEAL, "deal-1", _REASON, hold_sink, hold_reg)
    lift_hold(_ctx(), hold.hold_id, hold_sink, hold_reg)
    events.extend(hold_sink.events)

    # Erasure: request -> execute
    from idis.compliance.erasure import (
        InMemoryErasureRequestStore,
        execute_erasure,
        request_erasure,
    )

    erasure_sink = InMemoryAuditSink()
    erasure_store = InMemoryErasureRequestStore()

    class _NoopExecutor:
        def scan_holds(self, tenant_id: str, deal_id: str) -> None:
            return None

        def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
            return {"rows_deleted": 0, "objects_deleted": 0, "embeddings_deleted": 0}

    req = request_erasure(_ctx(), "deal-1", _REASON, erasure_sink, erasure_store)
    execute_erasure(
        _ctx(),
        req.request_id,
        erasure_sink,
        executor=_NoopExecutor(),
        hold_checker=lambda t, d: None,
        store=erasure_store,
    )
    events.extend(erasure_sink.events)

    # Export
    from idis.compliance.compliance_export import build_compliance_export

    export_sink = InMemoryAuditSink()

    class _EmptyCollector:
        def collect(self, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
            return {k: [] for k in ("deals", "documents", "claims", "sanads", "deliverables")}

    build_compliance_export(_ctx(), export_sink, _EmptyCollector())
    events.extend(export_sink.events)

    # Retention janitor: destructive sweep emits retention.sweep.executed before any work
    from idis.services.compliance.janitor import sweep_tenant

    janitor_sink = InMemoryAuditSink()
    sweep_tenant(
        _TENANT,
        sources=[],
        deleter=lambda t, c: None,
        idempotency_store=None,
        outbox_repo=None,
        audit_sink=janitor_sink,
        now=datetime(2026, 7, 13, tzinfo=UTC),
        destructive=True,
    )
    events.extend(
        e for e in janitor_sink.events if e.get("event_type") == "retention.sweep.executed"
    )

    return events


class TestComplianceCoreAuditConvention:
    """Amendment 2: every core compliance domain event obeys the repaired convention."""

    def test_every_core_emitter_is_valid_internal_post_safe_shape(self) -> None:
        events = _collect_core_events()
        seen = {e["event_type"] for e in events}
        expected = {
            "byok.key.configured",
            "byok.key.rotated",
            "byok.key.revoked",
            "legal_hold.applied",
            "legal_hold.lifted",
            "erasure.requested",
            "erasure.executed",
            "export.created",
            "retention.sweep.executed",
        }
        assert expected <= seen, f"core emitters not exercised: {expected - seen}"

        for event in events:
            etype = event["event_type"]
            result = validate_audit_event(event)
            assert result.passed, (etype, [e.code for e in result.errors])
            assert event["request"]["method"] == "POST", f"{etype}: method not POST"
            assert event["request"]["path"].startswith("/internal/"), (
                f"{etype}: path not /internal/*"
            )
            payload = event["payload"]
            assert set(payload) == {"safe", "hashes", "refs"}, (
                f"{etype}: payload shape is {sorted(payload)}, expected safe/hashes/refs"
            )
