"""RED contracts for the shared durable active budget-policy loader."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from idis.runtime.governance_envelopes import (
    BUDGET_SCHEMA_VERSION,
    VerifiedGovernanceEnvelope,
)
from tests.slice100_task2._support import ACCEPTANCE_TENANT_ID, budget_payload


class _Result:
    def __init__(self, *, rows: list[dict[str, object]] | None = None, scalar: object = None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def fetchall(self) -> list[dict[str, object]]:
        return list(self._rows)

    def scalar_one(self) -> object:
        return self._scalar


class _Connection:
    def __init__(self, rows: list[dict[str, object]], *, audit: bool = True, usage: bool = True):
        self.rows = rows
        self.audit = audit
        self.usage = usage
        self.statements: list[str] = []

    def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _Result:
        sql = " ".join(str(statement).lower().split())
        self.statements.append(sql)
        if sql.startswith("set local idis.tenant_id"):
            return _Result()
        if "from provider_budget_policies" in sql:
            return _Result(rows=self.rows)
        if "from audit_events" in sql:
            return _Result(scalar=self.audit)
        if "from provider_budget_usage" in sql:
            return _Result(scalar=self.usage)
        raise AssertionError(f"unexpected SQL: {sql}")


def _verified_and_rows() -> tuple[VerifiedGovernanceEnvelope, list[dict[str, object]]]:
    payload = budget_payload()
    verified = VerifiedGovernanceEnvelope(
        schema_version=BUDGET_SCHEMA_VERSION,
        signer_id="slice100-governance-signer-v1",
        payload=payload,
        payload_sha256="a" * 64,
        envelope_sha256="b" * 64,
        public_key_sha256="c" * 64,
    )
    raw_components = payload["components"]
    assert isinstance(raw_components, Mapping)
    imported_at = datetime(2026, 7, 20, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for component_id, component in sorted(raw_components.items()):
        assert isinstance(component_id, str)
        assert isinstance(component, Mapping)
        rows.append(
            {
                "policy_hash": verified.payload_sha256,
                "component_id": component_id,
                "policy_version": payload["policy_version"],
                "currency": payload["currency"],
                "total_max_usd": Decimal(str(payload["total_max_usd"])),
                "max_calls": component["max_calls"],
                "max_usd": Decimal(str(component["max_usd"])),
                "max_input_tokens": component.get("max_input_tokens"),
                "max_output_tokens": component.get("max_output_tokens"),
                "max_embedding_tokens": component.get("max_embedding_tokens"),
                "signer_id": verified.signer_id,
                "imported_by_actor_id": "slice100-governance-admin",
                "envelope_hash": verified.envelope_sha256,
                "payload_hash": verified.payload_sha256,
                "public_key_hash": verified.public_key_sha256,
                "import_audit_event_id": "30000000-0000-4000-8000-000000000003",
                "imported_at": imported_at,
            }
        )
    return verified, rows


def test_public_loader_returns_only_exact_verified_active_policy() -> None:
    from idis.persistence.repositories.provider_budget_policies import (
        PostgresProviderBudgetPoliciesRepository,
        VerifiedActiveProviderBudgetPolicy,
    )

    verified, rows = _verified_and_rows()
    connection = _Connection(rows)
    repository = PostgresProviderBudgetPoliciesRepository(connection, ACCEPTANCE_TENANT_ID)

    loaded = repository.load_active_verified_policy(
        verified_envelope=verified,
        trusted_signer_id=verified.signer_id,
        trusted_public_key_sha256=verified.public_key_sha256,
        expected_imported_by_actor_id="slice100-governance-admin",
    )

    assert isinstance(loaded, VerifiedActiveProviderBudgetPolicy)
    assert loaded.signature_verified is True
    assert loaded.tenant_id == ACCEPTANCE_TENANT_ID
    assert loaded.policy_hash == verified.payload_sha256
    assert frozenset(loaded.components) == frozenset(
        component["component_id"] for component in rows
    )
    assert loaded.import_audit_event_id == rows[0]["import_audit_event_id"]
    assert any("integration.governance.imported" in sql for sql in connection.statements)
    assert any("provider_budget_usage" in sql for sql in connection.statements)
    assert not any(
        token in sql
        for sql in connection.statements
        for token in ("insert into", "update ", "delete from")
    )


@pytest.mark.parametrize("defect", ["header", "audit", "usage", "trust-root"])
def test_public_loader_fails_closed_on_any_durable_binding_defect(defect: str) -> None:
    from idis.persistence.repositories.provider_budget_policies import (
        PostgresProviderBudgetPoliciesRepository,
        ProviderBudgetPolicyPersistenceError,
    )

    verified, rows = _verified_and_rows()
    if defect == "header":
        rows[0]["envelope_hash"] = "f" * 64
    connection = _Connection(rows, audit=defect != "audit", usage=defect != "usage")
    repository = PostgresProviderBudgetPoliciesRepository(connection, ACCEPTANCE_TENANT_ID)

    with pytest.raises(ProviderBudgetPolicyPersistenceError) as exc_info:
        repository.load_active_verified_policy(
            verified_envelope=verified,
            trusted_signer_id=verified.signer_id,
            trusted_public_key_sha256=(
                "d" * 64 if defect == "trust-root" else verified.public_key_sha256
            ),
            expected_imported_by_actor_id="slice100-governance-admin",
        )

    assert exc_info.value.code == "PROVIDER_BUDGET_CONFIG_BLOCKED"


def test_dependency_probe_delegates_to_the_single_public_loader_contract() -> None:
    from idis.persistence.repositories.provider_budget_policies import (
        PostgresProviderBudgetPoliciesRepository,
    )
    from idis.runtime import dependency_probes

    source = inspect.getsource(dependency_probes._load_durable_budget_policy)
    assert "PostgresProviderBudgetPoliciesRepository" in source
    assert ".load_active_verified_policy(" in source
    assert "latest_complete" not in source
    assert callable(PostgresProviderBudgetPoliciesRepository.load_active_verified_policy)
