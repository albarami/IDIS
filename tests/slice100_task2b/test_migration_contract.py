"""RED-first source and catalog contract for migration 0034."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import idis.persistence.migrations as migrations_pkg
from idis.runtime import dependency_probes
from tests.slice100_task2b._support import TENANT_TABLES

MIGRATIONS_DIR = Path(migrations_pkg.__file__).parent / "versions"


def _migration_path() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("0034_*.py"))
    assert len(matches) == 1, "Task 2b must add exactly one linear migration 0034"
    return matches[0]


def _load_migration() -> Any:
    path = _migration_path()
    spec = importlib.util.spec_from_file_location("slice100_task2b_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task2b_migration_is_linear_and_pins_the_exact_inventory() -> None:
    module = _load_migration()

    assert module.revision == "0034"
    assert module.down_revision == "0033"
    assert tuple(sorted(module.TENANT_TABLES)) == TENANT_TABLES


def test_task2b_migration_is_rls_only_and_contains_no_task7_schema() -> None:
    source = _migration_path().read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", source).upper()

    for forbidden in (
        "CREATE TABLE",
        "DROP TABLE",
        "ADD COLUMN",
        "DROP COLUMN",
        "CREATE INDEX",
        "CREATE TRIGGER",
        "CONSUMER_RECEIPT",
        "EXPECTED_CONSUMER_EDGE",
        "GETENV(",
        "ENVIRON[",
    ):
        assert forbidden not in normalized

    assert "ENABLE ROW LEVEL SECURITY" in normalized
    assert "FORCE ROW LEVEL SECURITY" in normalized
    assert "USING" in normalized
    assert "WITH CHECK" in normalized


def test_canonical_predicate_is_null_guarded_and_shared_by_both_policy_sides() -> None:
    module = _load_migration()
    predicate = re.sub(r"\s+", " ", module.CANONICAL_PREDICATE).strip().lower()

    assert "nullif(current_setting('idis.tenant_id', true), '') is not null" in predicate
    assert "tenant_id = nullif(current_setting('idis.tenant_id', true), '')::uuid" in predicate
    assert predicate.count("current_setting('idis.tenant_id', true)") == 2


def test_strict_readiness_pins_the_same_complete_tenant_table_inventory() -> None:
    # Revision 0034 is a historical 42-table snapshot; runtime grows at later migrations.
    assert frozenset(TENANT_TABLES) < dependency_probes.TENANT_RLS_TABLES
