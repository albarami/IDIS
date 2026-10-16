"""Validate and derive the unfrozen full-strict-v12 contract candidate.

The reviewed artifact is the sole normative byte source. This module validates
the v12 ownership/ACL census and can derive prospective frozen bytes for later
review, but its command-line entry point deliberately refuses fixture
publication while the checked-in candidate remains unfrozen.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V12_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V11 = _load_script(
    _SCRIPT_DIR / "generate_task7_v11_contract_fixture.py",
    "_task7_v11_fixture_helpers_for_v12",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v12_preimage.py",
    "_task7_v12_materializer_helpers",
)

ContractArtifactError = _V11.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V11._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V11._COMPONENT_ORDER)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_V12_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v12-sql-authority-registry-v1"
_ACL_ROLE_HASH_DOMAIN = b"full-strict-v12-guard-owner-role-sha256-v1\x00"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_APPLICATION_PRINCIPAL = "AUTHENTICATED_APPLICATION_ROLE"
_ERASURE_OWNER = "idis_erasure_function_owner"
_EXPECTED_POSTURE = {
    "login": False,
    "superuser": False,
    "createdb": False,
    "createrole": False,
    "inherit": False,
    "bypassrls": False,
}
_EXPECTED_RLS_CONTRACT = [
    "FORCE_RLS",
    "TRANSACTION_LOCAL_TENANT_CONTEXT",
    "idis.tenant_id",
    "canonical-null-guarded-using-and-with-check-v1",
]


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V11._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> Never:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V11._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V11.invariant_vector(artifact))


def _acl(artifact: dict[str, Any]) -> dict[str, Any]:
    try:
        return cast(
            dict[str, Any],
            artifact["sql_authority_registry"]["guard_function_acl_registry"],
        )
    except (KeyError, TypeError) as exc:
        raise ContractArtifactError(
            "TASK7_V12_ACL_REGISTRY_MISSING",
            "guard-function ACL authority is absent",
        ) from exc


def guard_function_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(row["function_signature"]),
            str(row["authority_id"]),
            str(row["owner_role"]),
            str(row["install_scope"]),
        )
        for row in _acl(artifact)["function_rows"]
    )


def guard_target_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row["function_signature"]), str(table), str(trigger))
        for row in _acl(artifact)["function_rows"]
        for table, trigger in row["target_trigger_tuple"]
    )


def guard_target_event_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (str(row["function_signature"]), *tuple(coordinate))
        for row in _acl(artifact)["function_rows"]
        for coordinate in row["target_event_coordinate_tuple"]
    )


def guard_helper_call_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(row["function_signature"]), str(callee))
        for row in _acl(artifact)["function_rows"]
        for callee in row["helper_call_signature_tuple"]
    )


def _validate_hash_bound_acl(artifact: dict[str, Any]) -> None:
    sql = artifact["sql_authority_registry"]
    for ordinal, row in enumerate(sql["rows"]):
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        expected = hashlib.sha256(_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()
        _expect(expected, row.get("expected_sha256"), f"v12 SQL authority {ordinal}")

    acl = _acl(artifact)
    role = acl["guard_owner_role"]
    role_payload = {key: value for key, value in role.items() if key != "expected_sha256"}
    _expect(
        hashlib.sha256(_ACL_ROLE_HASH_DOMAIN + _canonical_bytes(role_payload)).hexdigest(),
        role.get("expected_sha256"),
        "v12 guard owner role",
    )
    for ordinal, row in enumerate(acl["function_rows"]):
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        _expect(
            hashlib.sha256(_ACL_FUNCTION_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest(),
            row.get("expected_sha256"),
            f"v12 guard function ACL {ordinal}",
        )
    downgrade = acl["downgrade_authority"]
    downgrade_payload = {key: value for key, value in downgrade.items() if key != "expected_sha256"}
    _expect(
        _h(
            [
                "task7-guard-function-downgrade-authority-v1",
                downgrade_payload,
            ]
        ),
        downgrade.get("expected_sha256"),
        "v12 guard function downgrade authority",
    )
    _expect(
        _h(
            [
                _ACL_REGISTRY_HASH_DOMAIN,
                acl["guard_owner_role"],
                acl["function_rows"],
                acl["current_install_function_signature_tuple"],
                acl["current_column_grant_sql_utf8_hex_tuple"],
                acl["current_column_revoke_sql_utf8_hex_tuple"],
                acl["retained_0037_catalog_projection_tuple"],
                acl["retained_0037_trigger_catalog_projection_tuple"],
                acl["retained_0037_catalog_preflight_sql_utf8_hex"],
                acl["installation_sql_utf8_hex_tuple"],
                acl["downgrade_authority"],
            ]
        ),
        acl.get("expected_sha256"),
        "v12 guard function ACL registry",
    )
    _expect(
        _h([_V12_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]),
        sql.get("expected_sha256"),
        "v12 SQL authority registry",
    )


def _validate_guard_function_acl_registry(artifact: dict[str, Any]) -> None:
    acl = _acl(artifact)
    rows = acl.get("function_rows")
    if not isinstance(rows, list) or len(rows) != 34:
        _error(
            "TASK7_V12_ACL_FUNCTION_CENSUS_INVALID",
            f"rows={len(rows) if isinstance(rows, list) else 'invalid'}",
        )
    signatures = [str(row.get("function_signature")) for row in rows]
    ordinals = [row.get("function_ordinal") for row in rows]
    if len(signatures) != len(set(signatures)) or ordinals != list(range(34)):
        _error(
            "TASK7_V12_ACL_FUNCTION_CENSUS_INVALID",
            "signature or ordinal census differs",
        )
    authority_ids = {str(row["authority_id"]) for row in artifact["sql_authority_registry"]["rows"]}
    for row in rows:
        if str(row.get("authority_id")) not in authority_ids:
            _error(
                "TASK7_V12_ACL_AUTHORITY_ORPHAN",
                str(row.get("authority_id")),
            )

    role = acl.get("guard_owner_role")
    if not isinstance(role, dict) or (
        role.get("role_name") != _GUARD_OWNER
        or role.get("provisioning_mode") != "OPERATOR_BOOTSTRAP_REQUIRED"
        or role.get("posture") != _EXPECTED_POSTURE
        or role.get("role_membership_tuple") != []
        or role.get("table_ownership_tuple") != []
        or role.get("schema_usage_tuple") != ["public"]
        or role.get("schema_create") is not False
        or role.get("insert_relation_tuple") != []
        or role.get("update_relation_tuple") != []
        or role.get("delete_relation_tuple") != []
        or role.get("truncate_relation_tuple") != []
        or role.get("references_relation_tuple") != []
        or role.get("trigger_relation_tuple") != []
        or role.get("rls_contract_tuple") != _EXPECTED_RLS_CONTRACT
    ):
        _error("TASK7_V12_GUARD_OWNER_POSTURE_INVALID", "owner posture differs")
    preflight = bytes.fromhex(str(role.get("clean_role_preflight_sql_utf8_hex"))).decode("utf-8")
    installation = [
        bytes.fromhex(value).decode("utf-8") for value in acl["installation_sql_utf8_hex_tuple"]
    ]
    if (
        installation[0] != preflight
        or "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN" not in preflight
        or any(
            catalog not in preflight
            for catalog in (
                "pg_catalog.pg_auth_members",
                "pg_catalog.pg_database",
                "pg_catalog.pg_namespace",
                "pg_catalog.pg_class",
                "pg_catalog.pg_attribute",
                "pg_catalog.pg_proc",
                "pg_catalog.pg_default_acl",
            )
        )
    ):
        _error(
            "TASK7_V12_GUARD_OWNER_CLEAN_PREFLIGHT_INVALID",
            "clean-role catalog preflight differs",
        )
    retained_projection = _MATERIALIZER._retained_0037_catalog_projection(artifact)
    retained_trigger_projection = _MATERIALIZER._retained_0037_trigger_catalog_projection(artifact)
    retained_preflight = _MATERIALIZER._retained_0037_catalog_assertion_sql(retained_projection)
    if (
        acl.get("retained_0037_catalog_projection_tuple") != retained_projection
        or acl.get("retained_0037_trigger_catalog_projection_tuple") != retained_trigger_projection
        or acl.get("retained_0037_catalog_preflight_sql_utf8_hex")
        != retained_preflight.encode("utf-8").hex()
        or installation[2] != retained_preflight
    ):
        _error(
            "TASK7_V12_RETAINED_0037_CATALOG_BASELINE_INVALID",
            "retained function owner/body/catalog projection differs",
        )

    scope_counts = {
        scope: sum(row.get("install_scope") == scope for row in rows)
        for scope in ("CURRENT_0038", "FUTURE_INACTIVE", "EXTERNAL_DEPENDENCY")
    }
    if scope_counts != {
        "CURRENT_0038": 19,
        "FUTURE_INACTIVE": 14,
        "EXTERNAL_DEPENDENCY": 1,
    }:
        _error("TASK7_V12_ACL_ACTIVATION_SCOPE_INVALID", repr(scope_counts))
    current_signatures = [
        row["function_signature"] for row in rows if row["install_scope"] == "CURRENT_0038"
    ]
    if current_signatures != acl.get("current_install_function_signature_tuple"):
        _error("TASK7_V12_CURRENT_FUNCTION_CENSUS_INVALID", "current routines differ")

    by_signature = {str(row["function_signature"]): row for row in rows}
    helpers = {
        signature for signature, row in by_signature.items() if row.get("function_kind") == "HELPER"
    }
    trigger_guards = {
        signature
        for signature, row in by_signature.items()
        if row.get("function_kind") == "TRIGGER_GUARD"
    }
    if len(trigger_guards) != 25 or len(helpers) != 9:
        _error(
            "TASK7_V12_ACL_FUNCTION_CENSUS_INVALID",
            f"guards={len(trigger_guards)} helpers={len(helpers)}",
        )

    called_helpers = {
        str(callee) for row in rows for callee in row.get("helper_call_signature_tuple", [])
    }
    if not called_helpers <= helpers or any(
        helper not in called_helpers
        for helper in helpers
        if helper not in {"public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)"}
    ):
        _error("TASK7_V12_ACL_HELPER_ORPHAN", repr(sorted(helpers - called_helpers)))

    targets = guard_target_authority(artifact)
    if len(targets) != len(set(targets)):
        _error("TASK7_V12_ACL_TARGET_CENSUS_INVALID", "duplicate target")
    target_events = guard_target_event_authority(artifact)
    if len(target_events) != len(set(target_events)):
        _error("TASK7_V12_ACL_TARGET_EVENT_CENSUS_INVALID", "duplicate coordinate")
    scope_target_census = {
        scope: (
            sum(len(row["target_trigger_tuple"]) for row in rows if row["install_scope"] == scope),
            sum(
                len(row["target_event_coordinate_tuple"])
                for row in rows
                if row["install_scope"] == scope
            ),
        )
        for scope in ("CURRENT_0038", "FUTURE_INACTIVE")
    }
    if scope_target_census != {
        "CURRENT_0038": (73, 192),
        "FUTURE_INACTIVE": (81, 242),
    }:
        _error(
            "TASK7_V12_ACL_TARGET_EVENT_CENSUS_INVALID",
            repr(scope_target_census),
        )
    for signature in trigger_guards:
        row = by_signature[signature]
        if row.get("security_mode") != "DEFINER" or not row.get("target_trigger_tuple"):
            _error("TASK7_V12_TRIGGER_AUTHORITY_INVALID", signature)
        target_set = {tuple(map(str, target)) for target in row["target_trigger_tuple"]}
        coordinate_target_set = {
            (str(value[0]), str(value[1])) for value in row["target_event_coordinate_tuple"]
        }
        if target_set != coordinate_target_set:
            _error("TASK7_V12_TRIGGER_COORDINATE_CLOSURE_INVALID", signature)
    for signature in helpers:
        if by_signature[signature].get("target_trigger_tuple") != []:
            _error("TASK7_V12_HELPER_TARGET_INVALID", signature)

    direct_execute = {
        str(row["function_signature"]): tuple(row["explicit_execute_grantee_tuple"])
        for row in rows
        if row["explicit_execute_grantee_tuple"]
    }
    if direct_execute != {
        "public.idis_task7_approved_erasure_context_v1(uuid,uuid)": (_GUARD_OWNER,)
    }:
        _error("TASK7_V12_HELPER_EXECUTE_GRANT_INVALID", repr(direct_execute))

    current_relations: set[str] = set()
    current_columns: dict[str, set[str]] = {}
    for row in rows:
        if (
            row.get("public_execute") is not False
            or row.get("application_role") != _APPLICATION_PRINCIPAL
            or row.get("application_role_binding") != "RUNTIME_SESSION_USER"
            or row.get("application_role_execute") is not False
            or row.get("search_path_tuple") != ["pg_catalog", "pg_temp"]
            or row.get("insert_relation_tuple") != []
            or row.get("update_relation_tuple") != []
            or row.get("delete_relation_tuple") != []
            or row.get("truncate_relation_tuple") != []
            or row.get("references_relation_tuple") != []
            or row.get("trigger_relation_tuple") != []
        ):
            _error(
                "TASK7_V12_FUNCTION_ACL_INVALID",
                str(row.get("function_signature")),
            )
        if [item[0] for item in row["direct_read_column_tuple"]] != row["select_relation_tuple"]:
            _error(
                "TASK7_V12_DIRECT_READ_CLOSURE_INVALID",
                str(row["function_signature"]),
            )
        transitive = {
            str(relation): set(map(str, columns))
            for relation, columns, _evidence in row["transitive_read_column_tuple"]
        }
        for relation, columns, _evidence in row["direct_read_column_tuple"]:
            if not columns:
                _error(
                    "TASK7_V12_DIRECT_READ_COLUMN_CLOSURE_INCOMPLETE",
                    f"{row['function_signature']}:{relation}",
                )
            if not set(map(str, columns)) <= transitive.get(str(relation), set()):
                _error(
                    "TASK7_V12_TRANSITIVE_READ_CLOSURE_INVALID",
                    str(row["function_signature"]),
                )
        for callee in row["helper_call_signature_tuple"]:
            if callee not in by_signature:
                _error("TASK7_V12_ACL_HELPER_ORPHAN", str(callee))
            for relation, columns, _evidence in by_signature[callee][
                "transitive_read_column_tuple"
            ]:
                if not set(map(str, columns)) <= transitive.get(str(relation), set()):
                    _error(
                        "TASK7_V12_TRANSITIVE_READ_CLOSURE_INVALID",
                        f"{row['function_signature']}->{callee}",
                    )
        if row["install_scope"] == "CURRENT_0038":
            current_relations.update(map(str, row["select_relation_tuple"]))
            for relation, columns, _evidence in row["direct_read_column_tuple"]:
                current_columns.setdefault(str(relation), set()).update(map(str, columns))
    if len(current_relations) != 36 or set(role["select_relation_tuple"]) != current_relations:
        _error(
            "TASK7_V12_CURRENT_READ_CLOSURE_INVALID",
            f"derived={len(current_relations)} role={len(role['select_relation_tuple'])}",
        )
    expected_relation_columns = [
        [relation, sorted(current_columns[relation])] for relation in role["select_relation_tuple"]
    ]
    if (
        role.get("select_relation_column_tuple") != expected_relation_columns
        or sum(len(columns) for _relation, columns in expected_relation_columns) != 366
    ):
        _error(
            "TASK7_V12_CURRENT_COLUMN_CLOSURE_INVALID",
            "current relation/column union differs from 36/366",
        )
    table_revokes, column_revokes, column_grants = _MATERIALIZER._column_acl_sql(
        expected_relation_columns
    )
    encoded_grants = [value.encode("utf-8").hex() for value in column_grants]
    encoded_revokes = [value.encode("utf-8").hex() for value in column_revokes]
    installation = [
        bytes.fromhex(value).decode("utf-8") for value in acl["installation_sql_utf8_hex_tuple"]
    ]
    if (
        acl.get("current_column_grant_sql_utf8_hex_tuple") != encoded_grants
        or acl.get("current_column_revoke_sql_utf8_hex_tuple") != encoded_revokes
        or any("GRANT SELECT ON TABLE" in value for value in installation)
        or any(value not in installation for value in (*column_revokes, *column_grants))
    ):
        _error(
            "TASK7_V12_COLUMN_ACL_AUTHORITY_INVALID",
            "column-scoped installation bytes differ",
        )
    expected_downgrade = _MATERIALIZER._build_downgrade_authority(
        artifact,
        rows,
        table_revokes,
        column_revokes,
    )
    if acl.get("downgrade_authority") != expected_downgrade:
        _error(
            "TASK7_V12_DOWNGRADE_AUTHORITY_INVALID",
            "downgrade bytes differ from deterministic derivation",
        )
    downgrade_zero = bytes.fromhex(
        acl["downgrade_authority"]["zero_privilege_assertion_sql_utf8_hex"]
    ).decode("utf-8")
    if (
        downgrade_zero.replace(
            "TASK7_GUARD_FUNCTION_OWNER_DOWNGRADE_ACL_INVALID",
            "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN",
        )
        != preflight
    ):
        _error(
            "TASK7_V12_DOWNGRADE_ZERO_PRIVILEGE_CLOSURE_INVALID",
            "install and downgrade clean-role catalog predicates differ",
        )
    if (
        bytes.fromhex(
            acl["downgrade_authority"]["retained_catalog_postrestore_assertion_sql_utf8_hex"]
        ).decode("utf-8")
        != retained_preflight
    ):
        _error(
            "TASK7_V12_RETAINED_0037_CATALOG_RESTORE_INVALID",
            "downgrade post-restore assertion differs from pre-mutation baseline",
        )

    expected = _MATERIALIZER._build_acl_registry(artifact)
    if acl != expected:
        _error(
            "TASK7_V12_ACL_REGISTRY_DERIVATION_DRIFT",
            "literal ACL bytes differ from the reviewed derivation",
        )


def _validate_shape(
    raw: bytes,
    artifact: dict[str, Any],
    *,
    require_frozen: bool = False,
) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "section order differs")
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v12-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v12"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v12 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V12_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v12 bytes",
        )

    _validate_hash_bound_acl(artifact)
    try:
        reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
        reviewed_value = json.loads(reviewed)
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v12 reviewed design encoding differs",
        ) from exc
    _expect(
        _sha256_bytes(reviewed),
        metadata.get("reviewed_design_sha256"),
        "v12 reviewed design",
    )
    if (
        not isinstance(reviewed_value, list)
        or reviewed_value[:2]
        != [
            "full-strict-v12-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v12"],
        ]
        or reviewed_value[-1] != _MATERIALIZER._reviewed_acl_design_tuple()
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v12 reviewed design differs")

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected_recipe = [
        "pipeline_contract_hash",
        '["pipeline-contract-v12","full-strict-v12",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if recipes[5] != expected_recipe or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v12 digest recipe differs")

    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v11 = _MATERIALIZER.project_v12_to_v11(candidate)
        _MATERIALIZER._validate_exact_diff(v11, candidate)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V12_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v11 successor",
        ) from exc
    _V11._validate_shape(_render_artifact(v11), v11)
    _validate_guard_function_acl_registry(artifact)
    if invariant_vector(artifact) != _V11.invariant_vector(v11):
        _error(
            "TASK7_V12_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V12_UNFROZEN_CANDIDATE_REQUIRED",
            "prospective freeze requires append_frozen=false",
        )
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    return frozen_raw


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def derive_contract_bytes(
    raw: bytes,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Derive every v12 component solely from the supplied artifact bytes."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v12-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    v11_projection = _MATERIALIZER.project_v12_to_v11(artifact)
    components = cast(
        dict[str, str],
        _V11._V10._V9._V7._V6._V5._derive_components(
            v11_projection,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v12",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v12-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v12",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V12_CONTRACT_HASH": pipeline_hash,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v12_reviewed_preimage_v1.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.artifact.read_bytes()
        artifact = _decode_artifact(raw)
        _validate_shape(raw, artifact)
        if artifact["metadata"]["append_frozen"] is False:
            print("TASK7_V12_FIXTURE_PUBLICATION_REFUSED_UNFROZEN", file=sys.stderr)
            return 2
    except (ContractArtifactError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("TASK7_V12_FIXTURE_PUBLICATION_REQUIRES_REVIEWED_FREEZE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
