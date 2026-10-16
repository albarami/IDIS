"""Operator-backed proof for the dedicated Task 7 erasure credential."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from idis.runtime.dependency_probes import collect_erasure_executor_facts
from idis.runtime.dependency_readiness import check_erasure_executor_readiness


def _required_database_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"TASK7_OPERATOR_DATABASE_CONFIGURATION_MISSING:{name}", pytrace=False)
    pytest.skip(f"{name} is required only for the operator-backed PostgreSQL proof")


def test_dedicated_erasure_credential_passes_the_real_catalog_readiness_contract() -> None:
    app_url = _required_database_url("IDIS_DATABASE_URL")
    erasure_url = _required_database_url("IDIS_ERASURE_DATABASE_URL")
    try:
        app_engine = create_engine(app_url, pool_pre_ping=True)
        erasure_engine = create_engine(erasure_url, pool_pre_ping=True)
    except Exception:
        pytest.fail("TASK7_OPERATOR_DATABASE_ENGINE_CONSTRUCTION_FAILED", pytrace=False)

    try:
        facts = collect_erasure_executor_facts(
            app_connection_factory=app_engine.connect,
            erasure_connection_factory=erasure_engine.connect,
        )
        result = check_erasure_executor_readiness(
            env={"IDIS_ERASURE_DATABASE_URL": erasure_url},
            expected_role="api",
            probe=lambda: facts,
        )
    finally:
        app_engine.dispose()
        erasure_engine.dispose()

    assert facts.function_owner_update_acl.relation_tuple == ("erasure_requests",)
    assert facts.function_owner_update_acl.column_update_acl_count == 5
    assert facts.function_owner_update_acl.ordered_column_tuple == (
        "request_id",
        "status",
        "executed_by",
        "executed_at",
        "counts",
    )
    assert facts.function_owner_update_acl.grantable_acl_count == 0
    assert facts.function_owner_update_acl.table_update_privilege_count == 0
    assert result.may_proceed is True, result.code
    assert result.code is None
