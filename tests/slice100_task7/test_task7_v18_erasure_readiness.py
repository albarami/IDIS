"""RED-first v18 guard-owner and erasure-runner readiness authority."""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

from tests.slice100_task7.test_erasure_executor_profile_readiness_red import (
    _ERASURE_URL,
    _ready_facts,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)
_V18_COLUMN_ACL_SHA256 = "0e15aa84b19ba4f63f95001049702b54279f9d1ffebf984394979c602b271da6"


def _readiness() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_readiness")


def _probes() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_probes")


def _artifact(version: int) -> dict[str, Any]:
    path = _REPO_ROOT / "contracts" / f"full_strict_v{version}_reviewed_preimage_v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _authority(
    version: int,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[tuple[str, str, str, bool], ...],
]:
    artifact = _artifact(version)
    erasure = artifact["erasure_execution_registry"]["rows"][0]
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    current_signatures = tuple(
        sorted(
            row["function_signature"]
            for row in acl["function_rows"]
            if row["install_scope"] == "CURRENT_0038"
        )
    )
    future_signatures = tuple(
        sorted(
            row["function_signature"]
            for row in acl["function_rows"]
            if row["install_scope"] == "FUTURE_INACTIVE"
        )
    )
    projection = tuple(
        sorted(
            (relation, column, "SELECT", False)
            for relation, columns in acl["guard_owner_role"]["select_relation_column_tuple"]
            for column in columns
        )
    )
    return (
        tuple(erasure["current_relation_tuple"]),
        tuple(erasure["future_relation_tuple"]),
        tuple(erasure["complete_relation_tuple"]),
        current_signatures,
        future_signatures,
        projection,
    )


def _projection_sha256(
    version: int,
    projection: tuple[tuple[str, str, str, bool], ...],
) -> str:
    payload = json.dumps(
        projection,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(
        f"task7-v{version}-guard-owner-column-acl-v1".encode("ascii") + b"\0" + payload
    ).hexdigest()


def _facts_for_version(module: ModuleType, version: int) -> object:
    current, _, _, signatures, _, projection = _authority(version)
    relation_count = len({row[0] for row in projection})
    base = _ready_facts(module)
    return replace(
        base,
        guard_owner=replace(
            base.guard_owner,
            owned_routine_count=len(signatures),
            column_select_acl_relation_count=relation_count,
            column_select_acl_count=len(projection),
            column_select_acl_sha256=_projection_sha256(version, projection),
            force_rls_relation_count=relation_count,
            current_routine_signatures=signatures,
        ),
        runner_contract=replace(
            base.runner_contract,
            delete_relation_tuple=current,
            result_relation_tuple=current,
        ),
    )


def _check(module: ModuleType, facts: object) -> object:
    return module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )


def test_v18_readiness_authority_is_derived_from_the_frozen_fixture() -> None:
    module = _readiness()
    artifact_raw = _V18_ARTIFACT.read_bytes()
    fixture = json.loads(_V18_FIXTURE.read_text(encoding="utf-8"))
    current, future, complete, current_signatures, future_signatures, projection = _authority(18)

    assert hashlib.sha256(artifact_raw).hexdigest() == fixture["artifact_sha256"]
    assert fixture["FULL_STRICT_V18_CONTRACT_HASH"] == (
        "ad8e0a8fd012af34f34d8fd4b66643d2d31a8a4f436dd3a8021af9a188da378c"
    )
    assert (len(current), len(future), len(complete)) == (48, 38, 86)
    assert (len(current_signatures), len(future_signatures)) == (24, 14)
    assert len({row[0] for row in projection}) == 36
    assert len(projection) == 424
    assert _projection_sha256(18, projection) == _V18_COLUMN_ACL_SHA256
    assert current == module.TASK7_V18_CURRENT_ERASURE_RELATIONS
    assert future == module.TASK7_V18_FUTURE_ERASURE_RELATIONS
    assert complete == module.TASK7_V18_COMPLETE_ERASURE_RELATIONS
    assert current_signatures == module.TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES
    assert future_signatures == module.TASK7_V18_FUTURE_GUARD_ROUTINE_SIGNATURES


def test_v18_current_guard_routine_postures_match_the_frozen_authority() -> None:
    module = _readiness()
    rows = _artifact(18)["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    expected = tuple(
        sorted(
            (
                row["function_signature"],
                row["security_mode"],
                tuple(row["search_path_tuple"]),
            )
            for row in rows
            if row["install_scope"] == "CURRENT_0038"
        )
    )

    assert expected == module.TASK7_V18_CURRENT_GUARD_ROUTINE_POSTURES
    assert [posture[1] for posture in expected].count("DEFINER") == 12
    assert [posture[1] for posture in expected].count("INVOKER") == 12


def test_operator_facts_for_migrated_v18_pass_readiness() -> None:
    module = _readiness()

    result = _check(module, _facts_for_version(module, 18))

    assert result.may_proceed is True
    assert result.code is None


def test_superseded_v16_operator_posture_fails_readiness() -> None:
    module = _readiness()

    result = _check(module, _facts_for_version(module, 16))

    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


def test_catalog_projection_digest_is_versioned_to_v18() -> None:
    _, _, _, _, _, projection = _authority(18)
    rows = [
        {
            "relation_name": relation,
            "column_name": column,
            "privilege_type": privilege,
            "is_grantable": is_grantable,
        }
        for relation, column, privilege, is_grantable in projection
    ]

    assert _probes()._guard_owner_column_acl_digest(rows) == (
        36,
        424,
        _V18_COLUMN_ACL_SHA256,
    )
