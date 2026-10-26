"""RED-first admission contract for the bounded full-strict-v20 successor.

V19 is the real, unfrozen negative-control candidate. V20 may correct only
the erasure-function owner's two missing RETURNING-column SELECT privileges,
record the generator-derived closure authority, and update version-dependent
hashes. The v20 assets are loaded lazily so the pre-materialization state
fails with one stable absence code instead of an import error.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V19_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V20_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v20_preimage.py"
_V20_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v20_contract_fixture.py"
_V20_CANDIDATE_SHA256 = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_V20_FROZEN_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"

_V19_CANDIDATE_SHA512 = (
    "1b6816457a809a6158a04428d08dbbc4138720fdfddeb9c8be11c524c747d62e"
    "58007a9090d8fbb2d80a22a34adf7a319649db414bfb81e9552c5d8b9ca14de8"
)
_ERASURE_REQUESTS = "public.erasure_requests"
_SELECT_ADDITIONS = frozenset({"executed_by", "executed_at"})
_ACL_FIELDS = (
    "current_acl_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
)
_COMPOSED_ACL_FIELDS = (
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
)
_BODY_FIELDS = (
    "current_body_sql_utf8_hex_tuple",
    "future_body_sql_utf8_hex_tuple",
)
_SEMANTIC_INVARIANCE_SECTIONS = (
    "canonicalization_contract",
    "tuple_schema_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
    "activation_assertions",
)
_GRANT_RE = re.compile(
    r"\AGRANT (?P<privilege>SELECT|UPDATE) "
    r"\((?P<columns>[a-z0-9_,]+)\) ON TABLE "
    r"(?P<table>public\.[a-z][a-z0-9_]*) TO "
    r"(?P<role>[a-z][a-z0-9_]*)\Z"
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_view(artifact: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    return candidate


def _require_v20_assets() -> tuple[dict[str, Any], ModuleType, ModuleType]:
    missing = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_V20_ARTIFACT, _V20_MATERIALIZER, _V20_GENERATOR)
        if not path.is_file()
    ]
    assert not missing, "TASK7_V20_MATERIALIZATION_NOT_IMPLEMENTED:" + ",".join(missing)
    frozen_raw = _V20_ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _V20_FROZEN_SHA256
    artifact = _load_json(_V20_ARTIFACT)
    assert artifact["metadata"]["artifact_schema"] == "full-strict-v20-reviewed-preimage-v1"
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v20"
    assert artifact["metadata"]["append_frozen"] is True
    artifact["metadata"]["append_frozen"] = False
    candidate_raw = (
        json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(candidate_raw).hexdigest() == _V20_CANDIDATE_SHA256
    return (
        artifact,
        _load_module(_V20_MATERIALIZER, "_task7_v20_materializer_red"),
        _load_module(_V20_GENERATOR, "_task7_v20_generator_red"),
    )


def _erasure_row(artifact: dict[str, Any]) -> dict[str, Any]:
    section = artifact["erasure_execution_registry"]
    assert tuple(section) == ("rows", "expected_sha256")
    assert len(section["rows"]) == 1
    row = section["rows"][0]
    assert isinstance(row, dict)
    return row


def _decode(field_value: list[str]) -> tuple[str, ...]:
    return tuple(bytes.fromhex(value).decode("utf-8") for value in field_value)


def _grant_columns(
    row: dict[str, Any],
    *,
    field: str,
    privilege: str,
) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []
    for statement in _decode(row[field]):
        match = _GRANT_RE.fullmatch(statement)
        if (
            match is not None
            and match.group("privilege") == privilege
            and match.group("table") == _ERASURE_REQUESTS
            and match.group("role") == "idis_erasure_function_owner"
        ):
            matches.append(tuple(match.group("columns").split(",")))
    assert len(matches) == 1, (field, privilege, matches)
    return matches[0]


def _changed_top_level_sections(old: dict[str, Any], new: dict[str, Any]) -> set[str]:
    assert tuple(old) == tuple(new)
    return {key for key in old if old[key] != new[key]}


def test_real_v19_candidate_is_the_hash_pinned_unfrozen_source() -> None:
    raw = _V19_ARTIFACT.read_bytes()
    v19 = _load_json(_V19_ARTIFACT)

    assert hashlib.sha512(raw).hexdigest() == _V19_CANDIDATE_SHA512
    assert v19["metadata"]["artifact_schema"] == "full-strict-v19-reviewed-preimage-v1"
    assert v19["metadata"]["pipeline_contract_version"] == "full-strict-v19"
    assert v19["metadata"]["append_frozen"] is False


def test_v20_artifact_materializer_and_hermetic_generator_are_required() -> None:
    artifact, materializer, generator = _require_v20_assets()

    assert artifact["metadata"]["append_frozen"] is False
    assert callable(materializer.build_v20)
    assert callable(materializer.project_v20_to_v19)
    assert callable(materializer._validate_exact_diff)
    assert callable(materializer.derive_returning_select_closure)
    assert callable(materializer.validate_returning_select_closure)
    assert callable(generator.validate_candidate_artifact)
    assert callable(generator.validate_returning_select_closure)


def test_v20_build_is_deterministic_and_inverse_is_byte_exact_v19() -> None:
    v20, materializer, generator = _require_v20_assets()
    v19 = _load_json(_V19_ARTIFACT)

    rebuilt = materializer.build_v20(v19)
    assert materializer._render(rebuilt) == materializer._render(v20)
    assert materializer._render(materializer.build_v20(v19)) == materializer._render(rebuilt)

    projected = materializer.project_v20_to_v19(v20)
    assert projected == v19
    assert materializer._render(projected) == _V19_ARTIFACT.read_bytes()
    assert (
        generator.validate_candidate_artifact(
            v20,
            raw=materializer._render(v20),
        )
        == v20
    )


def test_v19_to_v20_delta_is_only_version_hash_and_returning_select_closure() -> None:
    v20, materializer, _generator = _require_v20_assets()
    v19 = _candidate_view(_load_json(_V19_ARTIFACT))

    assert _changed_top_level_sections(v19, v20) == {
        "metadata",
        "digest_recipe_registry",
        "sql_authority_registry",
        "erasure_execution_registry",
    }
    materializer._validate_exact_diff(v19, v20)

    old_row = _erasure_row(v19)
    new_row = _erasure_row(v20)
    for field in _BODY_FIELDS:
        assert new_row[field] == old_row[field], field
    for field in _ACL_FIELDS:
        old_select = _grant_columns(old_row, field=field, privilege="SELECT")
        new_select = _grant_columns(new_row, field=field, privilege="SELECT")
        assert (
            tuple(column for column in new_select if column not in _SELECT_ADDITIONS) == old_select
        )
        assert frozenset(new_select) - frozenset(old_select) == _SELECT_ADDITIONS
        assert len(new_select) == len(old_select) + len(_SELECT_ADDITIONS)
        assert _grant_columns(new_row, field=field, privilege="UPDATE") == _grant_columns(
            old_row,
            field=field,
            privilege="UPDATE",
        )

    changed_erasure_fields = {key for key in old_row if old_row[key] != new_row[key]}
    assert {
        *_ACL_FIELDS,
        *_COMPOSED_ACL_FIELDS,
        "expected_sha256",
    } <= changed_erasure_fields
    assert changed_erasure_fields <= {
        *_ACL_FIELDS,
        *_COMPOSED_ACL_FIELDS,
        "legal_red_case_tuple",
        "illegal_red_case_tuple",
        "direct_sql_red_case_tuple",
        "expected_sha256",
    }

    reviewed_design = json.loads(bytes.fromhex(v20["metadata"]["reviewed_design_utf8_hex"]))
    closure_rows = [
        row
        for row in reviewed_design
        if isinstance(row, list) and row and row[0] == "erasure_returning_select_closure"
    ]
    assert len(closure_rows) == 1
    closure_bytes = json.dumps(closure_rows[0], separators=(",", ":"), sort_keys=True)
    for required in (
        "task7-returning-select-closure-v1",
        "TASK7_RETURNING_SELECT_CLOSURE_INVALID",
        "public.erasure_requests",
        "executed_by",
        "executed_at",
    ):
        assert required in closure_bytes


def test_v20_preserves_runner_update_acl_and_complete_semantic_invariance_wall() -> None:
    v20, _materializer, _generator = _require_v20_assets()
    v19 = _load_json(_V19_ARTIFACT)

    for section in _SEMANTIC_INVARIANCE_SECTIONS:
        assert v20[section] == v19[section], section

    old_row = _erasure_row(v19)
    new_row = _erasure_row(v20)
    for field in _BODY_FIELDS:
        assert new_row[field] == old_row[field], field
    for field in _ACL_FIELDS:
        assert _grant_columns(new_row, field=field, privilege="UPDATE") == (
            "request_id",
            "status",
            "executed_by",
            "executed_at",
            "counts",
        )
        assert _grant_columns(new_row, field=field, privilege="UPDATE") == _grant_columns(
            old_row,
            field=field,
            privilege="UPDATE",
        )

    assert len(v20["semantic_step_registry"]["rows"]) == 28
    assert len(v20["edge_rule_registry"]["rows"]) == 48
    assert len(v20["usage_route_registry"]["rows"]) == 1_756
    assert len(v20["usage_extractor_registry"]["rows"]) == 19
    assert len(v20["provider_registry"]["rows"]) == 18
    assert len(v20["rights_index_policy_registry"]["rows"]) == 18
    assert new_row["current_relation_tuple"] == old_row["current_relation_tuple"]
    assert new_row["future_relation_tuple"] == old_row["future_relation_tuple"]
    assert new_row["complete_relation_tuple"] == old_row["complete_relation_tuple"]
    assert v20["uuid_namespace_registry"] == v19["uuid_namespace_registry"]


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("current_acl_sql_utf8_hex_tuple", "drop-executed-by"),
        ("future_acl_sql_utf8_hex_tuple", "drop-executed-at"),
        ("current_body_sql_utf8_hex_tuple", "add-returning-column"),
    ],
)
def test_v20_candidate_admission_rejects_each_closure_corruption(
    field: str,
    mutation: str,
) -> None:
    v20, materializer, generator = _require_v20_assets()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    statements = list(_decode(row[field]))

    if mutation == "drop-executed-by":
        statements = [
            statement.replace(",executed_by", "")
            if statement.startswith("GRANT SELECT ") and _ERASURE_REQUESTS in statement
            else statement
            for statement in statements
        ]
    elif mutation == "drop-executed-at":
        statements = [
            statement.replace(",executed_at", "")
            if statement.startswith("GRANT SELECT ") and _ERASURE_REQUESTS in statement
            else statement
            for statement in statements
        ]
    else:
        assert len(statements) == 1
        statements[0] = statements[0].replace(
            "RETURNING status,executed_by,executed_at,counts",
            "RETURNING status,executed_by,executed_at,requested_by,counts",
            1,
        )
    row[field] = [statement.encode("utf-8").hex() for statement in statements]

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_(?:RETURNING_SELECT_CLOSURE_INVALID|V20_EXACT_DIFF_INVALID)",
    ):
        generator.validate_candidate_artifact(
            corrupted,
            raw=materializer._render(corrupted),
        )
