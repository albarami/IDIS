"""RED-first contracts for the full-strict-v21 PostgreSQL census.

V21 adds one direct schema-USAGE grant to every authoritative erasure ACL
copy.  The executable census must therefore admit the exact 209-statement
current ACL without weakening the frozen v20 208-statement negative control.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_SCAFFOLD = _REPO_ROOT / "scripts" / "_task7_v21_census_scaffold.py"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v21_current_guard_census.py"
_SIGNED = _REPO_ROOT / "scripts" / "run_task7_v21_signed_census.py"

_EXECUTOR_SCHEMA_USAGE = "GRANT USAGE ON SCHEMA public TO idis_erasure_executor"
_CANDIDATE_SHA256 = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"
_FROZEN_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V21_CENSUS_MODULE_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_raw() -> bytes:
    raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FROZEN_SHA256
    artifact = cast(dict[str, Any], json.loads(raw))
    assert artifact["metadata"]["append_frozen"] is True
    artifact["metadata"]["append_frozen"] = False
    candidate_raw = (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode()
    assert hashlib.sha256(candidate_raw).hexdigest() == _CANDIDATE_SHA256
    return candidate_raw


def _artifact() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_candidate_raw()))


def test_v21_scaffold_admits_exact_current_acl_without_weakening_v20() -> None:
    scaffold = _load(_SCAFFOLD, "_task7_v21_scaffold_acl_count_red")
    artifact = _artifact()
    projected = scaffold._project_metadata_to_v20(artifact)

    with pytest.raises(
        RuntimeError,
        match="TASK7_V19_ERASURE_AUTHORITY_INVALID",
    ):
        scaffold._V20.current_erasure_sql(projected)

    body, acl = scaffold.current_erasure_sql(artifact)
    assert len(body) == 1
    assert len(acl) == 209
    assert acl.count(_EXECUTOR_SCHEMA_USAGE) == 1


def test_v21_scaffold_installs_body_then_all_209_acl_statements() -> None:
    scaffold = _load(_SCAFFOLD, "_task7_v21_scaffold_install_red")
    statements: list[str] = []

    class RecordingConnection:
        def exec_driver_sql(self, statement: str, *args: object, **kwargs: object) -> None:
            del args, kwargs
            statements.append(statement)

    body, acl = scaffold.current_erasure_sql(_artifact())
    scaffold.install_current_erasure_authority(
        cast(Any, RecordingConnection()),
        _artifact(),
    )

    # DBAPI percent escaping is transport-only; the server receives the reviewed SQL bytes.
    assert statements == [statement.replace("%", "%%") for statement in (*body, *acl)]
    assert len(statements) == 210
    assert statements.count(_EXECUTOR_SCHEMA_USAGE) == 1


def test_v21_runner_and_signed_wrapper_pin_the_corrected_scaffold_chain() -> None:
    runner = _load(_RUNNER, "_task7_v21_runner_corrected_scaffold_red")
    signed = _load(_SIGNED, "_task7_v21_signed_corrected_scaffold_red")

    assert hashlib.sha256(_SCAFFOLD.read_bytes()).hexdigest() == runner._V21_SCAFFOLD_SHA256
    assert runner.erasure_acl_census_counts(_artifact()) == {
        "current_statement_count": 209,
        "current_select_relation_count": 51,
        "current_select_column_count": 116,
        "future_statement_count": 361,
        "future_select_relation_count": 89,
        "future_select_column_count": 192,
    }
    source_closure = runner.verify_receipt_materialization_source_closure(_candidate_raw())
    assert source_closure["materialization_source_manifest_sha256"] == (
        signed._V21_SOURCE_MANIFEST_SHA256
    )
    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == signed._V21_RUNNER_SHA256
    assert signed._load_approved_runner().__file__ == str(_RUNNER)


def test_v21_erasure_execution_expectations_admit_the_reviewed_acl_count() -> None:
    runner = _load(_RUNNER, "_task7_v21_erasure_expectations_acl_count_red")

    expected = runner.erasure_authority_execution_expectations(_artifact())

    assert expected["owner_select_relation_count"] == 51
    assert expected["owner_select_column_count"] == 116
    assert expected["current_relation_count"] == 48
