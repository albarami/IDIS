"""Materialize the bounded, unfrozen full-strict-v12 reviewed candidate.

V11 never reached append-freeze. V12 preserves every semantic vector while
binding the reviewed guard-function owner, ownership, search-path, RLS, and
least-privilege ACL model as literal contract bytes. The command authenticates
the exact v11 input, writes with exclusive-create semantics, and can emit only
``append_frozen=false``.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_V11_SOURCE_SHA512 = (
    "fd9d97c10de4bd4b5f571c32b633d620a811d75d8474d2fa20128d27156178ff"
    "ce5d588c7428a372d1639544b4c6042eaff0e7ce6a84d4604e860ebce2e82aed"
)

_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_V11_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_V12_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v12-sql-authority-registry-v1"
_ACL_ROLE_HASH_DOMAIN = b"full-strict-v12-guard-owner-role-sha256-v1\x00"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"

_GUARD_OWNER = "idis_task7_guard_function_owner"
_APPLICATION_ROLE = "AUTHENTICATED_APPLICATION_ROLE"
_ERASURE_OWNER = "idis_erasure_function_owner"
_CURRENT_AUTHORITY_START = 17
_ROLE_POSTURE = {
    "login": False,
    "superuser": False,
    "createdb": False,
    "createrole": False,
    "inherit": False,
    "bypassrls": False,
}
_RLS_CONTRACT = [
    "FORCE_RLS",
    "TRANSACTION_LOCAL_TENANT_CONTEXT",
    "idis.tenant_id",
    "canonical-null-guarded-using-and-with-check-v1",
]
_HELPER_CORRECTIONS = {
    "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)": (
        "V11_42501_CURRENT_NATIVE_HASHES"
    ),
    "public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid)": (
        "V11_42501_EXPECTED_EDGE_ADMISSION"
    ),
    "public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)": (
        "V11_42501_PACKAGE_SOURCE_TRANSITION"
    ),
    "public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid)": (
        "V11_42501_TASK6_OUTPUT_ROOT_ATTACH"
    ),
}
_SOURCE_EXACT_COLUMNS = {
    "source_lineage_objects": (
        "source_lineage_object_id",
        "object_kind",
        "corpus_receipt_id",
        "tenant_id",
        "deal_id",
    ),
    "data_room_packages": (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "tenant_id",
        "deal_id",
        "source_binding_state",
    ),
    "data_room_package_files": (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "tenant_id",
        "deal_id",
        "source_binding_state",
    ),
    "document_artifacts": (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "tenant_id",
        "deal_id",
        "source_binding_state",
    ),
    "documents": (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "tenant_id",
        "deal_id",
        "source_binding_state",
    ),
    "document_parse_attempts": (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "tenant_id",
        "deal_id",
        "source_binding_state",
    ),
    "document_spans": (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "tenant_id",
        "deal_id",
        "source_binding_state",
    ),
}
_OUTPUT_EXACT_NATIVE_RELATIONS = (
    "methodology_coverage_records",
    "methodology_extraction_tasks",
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
    "methodology_result_claim_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_defect_links",
    "deterministic_calculations",
    "vector_embeddings",
    "evidence_trust_findings",
    "muhasabah_records",
    "validated_evidence_packages",
    "debate_sessions",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "deliverables",
)
_DIRECT_READ_COLUMN_OVERRIDES = {
    "public.idis_task7_current_source_exact_one_v1()": _SOURCE_EXACT_COLUMNS,
    "public.idis_task7_current_output_exact_one_v1()": {
        "run_output_objects": (
            "output_object_id",
            "output_kind",
            "tenant_id",
            "deal_id",
            "run_id",
            "output_class",
            "native_identity_sha256",
            "native_output_sha256",
        ),
        **dict.fromkeys(
            _OUTPUT_EXACT_NATIVE_RELATIONS,
            (
                "tenant_id",
                "deal_id",
                "run_id",
                "run_output_object_id",
                "run_output_object_kind",
            ),
        ),
    },
}
_EXTERNAL_READ_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)": {
        "runs": (
            "tenant_id",
            "deal_id",
            "run_id",
            "status",
            "lease_owner_id",
            "lease_fencing_token",
            "lease_expires_at",
        )
    },
    "public.idis_task7_approved_erasure_context_v1(uuid,uuid)": {
        "erasure_requests": ("tenant_id", "deal_id", "status")
    },
}
_RETAINED_0037_SIGNATURES = (
    "public.idis_task5_coverage_scope_guard()",
    "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
    "public.idis_task6_immutable_guard()",
)
_RETAINED_MIGRATION_SOURCES = (
    (
        "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
        "679e84f8a9d4378178a4f076bbabddef40a2bbcb7ddcd11a0e9e7b4073e8d1e04"
        "fa2b2cb665097248bf9cd0fbf68d13b326291cbe393d355abcfce3edf891904",
    ),
    (
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        "de166216d565872b88c84d7d5abaff721b157e358e7c12110b0cb95d1468ec4b"
        "811371bd955ea584df754b4f3d4452b9fb2c6370f74cf2d83a9d0c2d61aab232",
    ),
)
_RETAINED_0037_FUNCTION_AUTHORITY = {
    "public.idis_task5_coverage_scope_guard()": {
        "migration_path": _RETAINED_MIGRATION_SOURCES[0][0],
        "migration_sha512": _RETAINED_MIGRATION_SOURCES[0][1],
        "function_name": "idis_task5_coverage_scope_guard",
        "function_sql_length": 2716,
        "function_sql_sha256": ("3d4767d2bf5678f8a9a003c3168d79e3050e16eaf0849bc49b8d79e6668b5fbf"),
        "prosrc_length": 2589,
        "prosrc_sha256": ("ba593849e358a5c772a6af3531d32b193fd01d314432e71b872522c3127cd3ab"),
    },
    "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)": {
        "migration_path": _RETAINED_MIGRATION_SOURCES[1][0],
        "migration_sha512": _RETAINED_MIGRATION_SOURCES[1][1],
        "function_name": "idis_task6_current_lease_is_valid",
        "function_sql_length": 1883,
        "function_sql_sha256": ("e83e6e6d60c3ea5bcdf65b010dc26b91a0abfe48513a8fabf3176308022dfda2"),
        "prosrc_length": 1677,
        "prosrc_sha256": ("cde8f9e3b33a469ce43ec9b7751465d588267fbb1166ed4e8cd155557fb3b499"),
    },
    "public.idis_task6_immutable_guard()": {
        "migration_path": _RETAINED_MIGRATION_SOURCES[1][0],
        "migration_sha512": _RETAINED_MIGRATION_SOURCES[1][1],
        "function_name": "idis_task6_immutable_guard",
        "function_sql_length": 761,
        "function_sql_sha256": ("136aa75e5bb1f8a26288f532ef81eab0e22d7143759ebb2b7c89dc754db942d1"),
        "prosrc_length": 639,
        "prosrc_sha256": ("97db59522cf42dd91797850e5f9ae438d4d8aa9914d24058bbb4943e7fa3114a"),
    },
}
_RETAINED_0037_TRIGGER_AUTHORITY = (
    (
        "public.idis_task5_coverage_scope_guard()",
        "methodology_coverage_records",
        "trg_methodology_coverage_scope",
        _RETAINED_MIGRATION_SOURCES[0][0],
        _RETAINED_MIGRATION_SOURCES[0][1],
    ),
    *(
        (
            "public.idis_task6_immutable_guard()",
            table,
            (
                "trg_methodology_result_factual_outputs_immutable"
                if table == "methodology_result_factual_outputs"
                else f"trg_{table}_immutable"
            ),
            _RETAINED_MIGRATION_SOURCES[1][0],
            _RETAINED_MIGRATION_SOURCES[1][1],
        )
        for table in (
            "methodology_extraction_tasks",
            "methodology_extraction_task_spans",
            "methodology_extraction_results",
            "methodology_result_factual_outputs",
            "methodology_result_claim_links",
            "methodology_result_evidence_links",
            "methodology_result_sanad_links",
            "methodology_result_defect_links",
        )
    ),
)
_RETAINED_0037_TRIGGER_FUNCTIONS = {
    "public.idis_task5_coverage_scope_guard()",
    "public.idis_task6_immutable_guard()",
}
_EXTERNAL_HELPERS: tuple[dict[str, Any], ...] = (
    {
        "authority_id": "current-task6-immutable-replacement-v1",
        "function_signature": "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
        "owner_role": _GUARD_OWNER,
        "security_mode": "INVOKER",
        "select_relation_tuple": ["runs"],
        "explicit_execute_grantee_tuple": [],
        "definition_locator": (
            "erasure_execution_registry.rows[0]."
            "legacy_guard_upgrade_sql_utf8_hex_tuple:"
            "public.idis_task6_current_lease_is_valid"
        ),
    },
    {
        "authority_id": "approved-erasure-append-only-v1",
        "function_signature": "public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
        "owner_role": _ERASURE_OWNER,
        "security_mode": "DEFINER",
        "select_relation_tuple": ["erasure_requests"],
        "explicit_execute_grantee_tuple": [_GUARD_OWNER],
        "definition_locator": (
            "erasure_execution_registry.rows[0]."
            "legacy_guard_upgrade_sql_utf8_hex_tuple:"
            "public.idis_task7_approved_erasure_context_v1"
        ),
    },
)

_FUNCTION_RE = re.compile(
    r"CREATE(?:\s+OR\s+REPLACE)?\s+FUNCTION\s+"
    r"(?P<name>public\.[a-z_][a-z0-9_]*)\s*"
    r"\((?P<arguments>.*?)\)\s+RETURNS\s+"
    r"(?P<returns>.*?)\s+LANGUAGE\s+(?P<language>[a-z]+)\s+"
    r"(?P<attributes>.*?)\s+AS\s+\$\$(?P<body>.*?)\$\$",
    re.IGNORECASE | re.DOTALL,
)
_TRIGGER_RE = re.compile(
    r"CREATE\s+(?P<constraint>CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<trigger>[a-z_][a-z0-9_]*)\s+.*?\s+ON\s+"
    r"public\.(?P<table>[a-z_][a-z0-9_]*)\s+.*?"
    r"EXECUTE\s+FUNCTION\s+(?P<function>public\.[a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE | re.DOTALL,
)
_TRIGGER_COORDINATE_RE = re.compile(
    r"CREATE\s+(?P<constraint>CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<trigger>[a-z_][a-z0-9_]*)\s+"
    r"(?P<timing>BEFORE|AFTER)\s+"
    r"(?P<events>(?:INSERT|UPDATE|DELETE)"
    r"(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)\s+ON\s+"
    r"public\.(?P<table>[a-z_][a-z0-9_]*)\s+"
    r"(?P<middle>.*?)FOR\s+EACH\s+(?P<level>ROW|STATEMENT)\s+"
    r"EXECUTE\s+FUNCTION\s+(?P<function>public\.[a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE | re.DOTALL,
)
_RELATION_RE = re.compile(
    r"(?<!DISTINCT )\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM|"
    r"REFERENCES)\s+(?P<relation>[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)
_FUNCTION_CALL_RE = re.compile(
    r"\b(?P<function>public\.idis_[a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
_CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([a-z_][a-z0-9_]*)\s+AS\s*\(", re.IGNORECASE)


class MaterializationError(RuntimeError):
    """Stable fail-closed v12 materialization error."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _decode_statements(row: dict[str, Any]) -> list[str]:
    try:
        return [bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]]
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise MaterializationError("TASK7_V12_SQL_AUTHORITY_BODY_INVALID") from exc


def _encode_statements(statements: list[str]) -> list[str]:
    return [statement.encode("utf-8").hex() for statement in statements]


def _literal_migration_sql(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    statements: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            statements.append(node.args[0].value)
    return statements


def _migration_function_sql(
    statements: list[str],
    function_name: str,
) -> str:
    matches = [
        statement
        for statement in statements
        if re.search(
            rf"\bCREATE\s+OR\s+REPLACE\s+FUNCTION\s+{re.escape(function_name)}\s*\(",
            statement,
            re.IGNORECASE,
        )
    ]
    if len(matches) != 1:
        raise MaterializationError(
            f"TASK7_V12_RETAINED_MIGRATION_FUNCTION_CENSUS_INVALID:{function_name}"
        )
    return matches[0]


def _function_prosrc(function_sql: str) -> str:
    match = re.search(
        r"\bAS\s+\$\$(?P<body>.*)\$\$\s*$",
        function_sql,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise MaterializationError("TASK7_V12_RETAINED_MIGRATION_BODY_INVALID")
    return match.group("body")


def _authenticated_migration_statements(repo_root: Path) -> dict[str, list[str]]:
    statements: dict[str, list[str]] = {}
    for relative_path, expected_sha512 in _RETAINED_MIGRATION_SOURCES:
        path = repo_root / relative_path
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MaterializationError(
                f"TASK7_V12_RETAINED_MIGRATION_SOURCE_MISSING:{relative_path}"
            ) from exc
        if hashlib.sha512(raw).hexdigest() != expected_sha512:
            raise MaterializationError(
                f"TASK7_V12_RETAINED_MIGRATION_SOURCE_HASH_MISMATCH:{relative_path}"
            )
        statements[relative_path] = _literal_migration_sql(path)
    return statements


def _authenticated_retained_0037_catalog_projection(
    repo_root: Path,
) -> list[list[Any]]:
    statements = _authenticated_migration_statements(repo_root)
    projection: list[list[Any]] = []
    for signature in _RETAINED_0037_SIGNATURES:
        authority = _RETAINED_0037_FUNCTION_AUTHORITY[signature]
        relative_path = str(authority["migration_path"])
        function_sql = _migration_function_sql(
            statements[relative_path],
            str(authority["function_name"]),
        )
        prosrc = _function_prosrc(function_sql)
        if (
            len(function_sql) != authority["function_sql_length"]
            or _sha256(function_sql.encode("utf-8")) != authority["function_sql_sha256"]
            or len(prosrc) != authority["prosrc_length"]
            or _sha256(prosrc.encode("utf-8")) != authority["prosrc_sha256"]
        ):
            raise MaterializationError(
                f"TASK7_V12_RETAINED_MIGRATION_FUNCTION_BYTES_INVALID:{signature}"
            )
        projection.append(
            [
                signature,
                function_sql.encode("utf-8").hex(),
                prosrc.encode("utf-8").hex(),
                relative_path,
                authority["migration_sha512"],
                "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER",
                False,
                None,
                None,
            ]
        )
    return projection


def _validate_retained_0037_catalog_projection(
    projection: list[list[Any]],
) -> list[list[Any]]:
    if len(projection) != 3:
        raise MaterializationError("TASK7_V12_RETAINED_FUNCTION_PROJECTION_CENSUS_INVALID")
    for expected_signature, row in zip(
        _RETAINED_0037_SIGNATURES,
        projection,
        strict=True,
    ):
        if not isinstance(row, list) or len(row) != 9 or row[0] != expected_signature:
            raise MaterializationError(
                f"TASK7_V12_RETAINED_FUNCTION_PROJECTION_SHAPE_INVALID:{expected_signature}"
            )
        authority = _RETAINED_0037_FUNCTION_AUTHORITY[expected_signature]
        try:
            function_sql = bytes.fromhex(row[1]).decode("utf-8")
            prosrc = bytes.fromhex(row[2]).decode("utf-8")
        except (TypeError, UnicodeError, ValueError) as exc:
            raise MaterializationError(
                f"TASK7_V12_RETAINED_FUNCTION_PROJECTION_BYTES_INVALID:{expected_signature}"
            ) from exc
        if (
            row[3] != authority["migration_path"]
            or row[4] != authority["migration_sha512"]
            or row[5:]
            != [
                "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER",
                False,
                None,
                None,
            ]
            or len(function_sql) != authority["function_sql_length"]
            or _sha256(function_sql.encode("utf-8")) != authority["function_sql_sha256"]
            or len(prosrc) != authority["prosrc_length"]
            or _sha256(prosrc.encode("utf-8")) != authority["prosrc_sha256"]
            or _function_prosrc(function_sql) != prosrc
        ):
            raise MaterializationError(
                f"TASK7_V12_RETAINED_FUNCTION_PROJECTION_INVALID:{expected_signature}"
            )
    return copy.deepcopy(projection)


def _authenticated_retained_0037_trigger_catalog_projection(
    repo_root: Path,
) -> list[list[Any]]:
    statements = _authenticated_migration_statements(repo_root)
    task5_statements = statements[_RETAINED_MIGRATION_SOURCES[0][0]]
    if sum("CREATE TRIGGER trg_methodology_coverage_scope" in sql for sql in task5_statements) != 1:
        raise MaterializationError("TASK7_V12_RETAINED_TASK5_TRIGGER_SOURCE_INVALID")
    task6_source = (repo_root / _RETAINED_MIGRATION_SOURCES[1][0]).read_text(encoding="utf-8")
    if not all(
        f'"{relation}"' in task6_source
        for _signature, relation, _trigger, _path, _sha512 in _RETAINED_0037_TRIGGER_AUTHORITY[1:]
    ):
        raise MaterializationError("TASK7_V12_RETAINED_TASK6_TRIGGER_SOURCE_INVALID")
    return [
        [
            signature,
            relation,
            trigger,
            "BEFORE",
            ["INSERT", "UPDATE", "DELETE"],
            "ROW",
            "O",
            False,
            False,
            False,
            relative_path,
            source_sha512,
        ]
        for signature, relation, trigger, relative_path, source_sha512 in (
            _RETAINED_0037_TRIGGER_AUTHORITY
        )
    ]


def _validate_retained_0037_trigger_catalog_projection(
    projection: list[list[Any]],
) -> list[list[Any]]:
    expected = [
        [
            signature,
            relation,
            trigger,
            "BEFORE",
            ["INSERT", "UPDATE", "DELETE"],
            "ROW",
            "O",
            False,
            False,
            False,
            relative_path,
            source_sha512,
        ]
        for signature, relation, trigger, relative_path, source_sha512 in (
            _RETAINED_0037_TRIGGER_AUTHORITY
        )
    ]
    if projection != expected:
        raise MaterializationError("TASK7_V12_RETAINED_TRIGGER_PROJECTION_INVALID")
    return copy.deepcopy(projection)


def _rehash_authority_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ROW_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()


def _rehash_acl_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ACL_FUNCTION_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()


def _rehash_role(role: dict[str, Any]) -> None:
    payload = {key: value for key, value in role.items() if key != "expected_sha256"}
    role["expected_sha256"] = hashlib.sha256(
        _ACL_ROLE_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if type(old) is not type(new):
        return {path}
    if isinstance(old, dict):
        if tuple(old) != tuple(new):
            return {path}
        changed: set[tuple[str | int, ...]] = set()
        for key in old:
            changed.update(_changed_paths(old[key], new[key], (*path, key)))
        return changed
    if isinstance(old, list):
        if len(old) != len(new):
            return {path}
        changed = set()
        for ordinal, (old_child, new_child) in enumerate(zip(old, new, strict=True)):
            changed.update(_changed_paths(old_child, new_child, (*path, ordinal)))
        return changed
    return set() if old == new else {path}


def _argument_types(arguments: str) -> tuple[str, ...]:
    if not arguments.strip():
        return ()
    result: list[str] = []
    for argument in arguments.split(","):
        tokens = argument.strip().split()
        if len(tokens) < 2:
            raise MaterializationError("TASK7_V12_FUNCTION_ARGUMENT_SHAPE_INVALID")
        result.append(" ".join(tokens[1:]).lower())
    return tuple(result)


def _signature(match: re.Match[str]) -> str:
    types = ",".join(_argument_types(match.group("arguments")))
    return f"{match.group('name').lower()}({types})"


def _replace_function_security_modes(statement: str, *, reverse: bool) -> str:
    matches = list(_FUNCTION_RE.finditer(statement))
    rendered = statement
    for match in reversed(matches):
        signature = _signature(match)
        returns_trigger = match.group("returns").strip().lower() == "trigger"
        attributes = match.group("attributes")
        if reverse:
            if returns_trigger and "SECURITY DEFINER" in attributes.upper():
                replacement = re.sub(
                    r"\s+SECURITY\s+DEFINER\b",
                    "",
                    match.group(0),
                    count=1,
                    flags=re.IGNORECASE,
                )
            elif signature in _HELPER_CORRECTIONS and "SECURITY INVOKER" in attributes.upper():
                replacement = re.sub(
                    r"\s+SECURITY\s+INVOKER\b",
                    "",
                    match.group(0),
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                continue
        else:
            desired = (
                "DEFINER"
                if returns_trigger
                else ("INVOKER" if signature in _HELPER_CORRECTIONS else None)
            )
            if desired is None:
                continue
            if re.search(r"\bSECURITY\s+(?:DEFINER|INVOKER)\b", attributes, re.IGNORECASE):
                if f"SECURITY {desired}" not in attributes.upper():
                    raise MaterializationError(
                        f"TASK7_V12_FUNCTION_SECURITY_MODE_CONFLICT:{signature}"
                    )
                continue
            full = match.group(0)
            search_path = re.search(
                r"\s+SET\s+search_path=pg_catalog,pg_temp\s+AS\s+\$\$",
                full,
                re.IGNORECASE,
            )
            if search_path is None:
                raise MaterializationError(f"TASK7_V12_FUNCTION_SEARCH_PATH_MISSING:{signature}")
            replacement = (
                full[: search_path.start()] + f" SECURITY {desired}" + full[search_path.start() :]
            )
        rendered = rendered[: match.start()] + replacement + rendered[match.end() :]
    return rendered


def _transform_sql_bodies(artifact: dict[str, Any], *, reverse: bool) -> None:
    rows = artifact["sql_authority_registry"]["rows"]
    if len(rows) != 28:
        raise MaterializationError("TASK7_V12_AUTHORITY_CENSUS_INVALID")
    for ordinal, row in enumerate(rows):
        statements = _decode_statements(row)
        changed = False
        for statement_ordinal, statement in enumerate(statements):
            transformed = (
                statement
                if reverse and ordinal < _CURRENT_AUTHORITY_START
                else _replace_function_security_modes(statement, reverse=reverse)
            )
            if ordinal in (19, 26):
                old = "full-strict-v12" if reverse else "full-strict-v11"
                new = "full-strict-v11" if reverse else "full-strict-v12"
                if old in transformed:
                    if transformed.count(old) != 1:
                        raise MaterializationError("TASK7_V12_PIPELINE_VERSION_BODY_SHAPE_INVALID")
                    transformed = transformed.replace(old, new, 1)
            if transformed != statement:
                statements[statement_ordinal] = transformed
                changed = True
        if changed:
            row["body_sql_utf8_hex_tuple"] = _encode_statements(statements)
            _rehash_authority_row(row)


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    if reverse:
        if not isinstance(design, list) or design[:2] != [
            "full-strict-v12-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v12"],
        ]:
            raise MaterializationError("TASK7_V12_REVIEWED_DESIGN_SHAPE_INVALID")
        if not design or design[-1] != _reviewed_acl_design_tuple():
            raise MaterializationError("TASK7_V12_REVIEWED_ACL_DESIGN_MISSING")
        design.pop()
        design[0] = "full-strict-v11-reviewed-design-literal-v1"
        design[1] = ["pipeline_contract_version", "full-strict-v11"]
        version = "full-strict-v11"
    else:
        if not isinstance(design, list) or design[:2] != [
            "full-strict-v11-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v11"],
        ]:
            raise MaterializationError("TASK7_V12_REVIEWED_DESIGN_BASE_MISMATCH")
        design[0] = "full-strict-v12-reviewed-design-literal-v1"
        design[1] = ["pipeline_contract_version", "full-strict-v12"]
        design.append(_reviewed_acl_design_tuple())
        version = "full-strict-v12"
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{version}-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = version
    metadata["append_frozen"] = False
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()


def _reviewed_acl_design_tuple() -> list[Any]:
    return [
        "guard_function_acl_authority",
        [
            "guard_owner_role",
            _GUARD_OWNER,
            [
                "posture",
                "NOLOGIN",
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOINHERIT",
                "NOBYPASSRLS",
            ],
            ["function_count", 34],
            ["trigger_guard_count", 25],
            ["helper_count", 9],
            ["application_role", _APPLICATION_ROLE],
            ["helper_execute_policy", "OWNER_OR_HASH_BOUND_EXPLICIT_GRANT_ONLY"],
            ["rls_context", *_RLS_CONTRACT],
        ],
    ]


def _transform_digest_recipe(artifact: dict[str, Any], *, reverse: bool) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "12" if reverse else "11"
    new_ordinal = "11" if reverse else "12"
    old_version = "full-strict-v12" if reverse else "full-strict-v11"
    new_version = "full-strict-v11" if reverse else "full-strict-v12"
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        raise MaterializationError("TASK7_V12_PIPELINE_RECIPE_BASE_MISMATCH")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _direct_relations(body: str, authority_parents: list[str]) -> list[str]:
    observed = {
        match.group("relation").lower().removeprefix("public.")
        for match in _RELATION_RE.finditer(body)
        if not match.group("relation").lower().startswith("pg_catalog.")
    }
    ordered = [relation for relation in authority_parents if relation in observed]
    extras = sorted(observed - set(ordered))
    return [*ordered, *extras]


def _direct_read_columns(
    signature: str,
    body: str,
    relations: list[str],
) -> list[list[Any]]:
    """Return deterministic lexical column evidence for every direct relation.

    Qualified references are resolved within boolean/UNION query segments so
    reused aliases do not bleed across subqueries. Unqualified references stay
    bound by the exact body hash and are resolved by the executable PostgreSQL
    census before freeze; they are never used to widen current grants.
    """

    columns: dict[str, set[str]] = {relation: set() for relation in relations}
    segments = re.split(
        r"\bUNION\s+ALL\b|\bOR\s+EXISTS\b|\bAND\s+EXISTS\b",
        body,
        flags=re.IGNORECASE,
    )
    alias_re = re.compile(
        r"\b(?:FROM|JOIN)\s+public\.(?P<relation>[a-z_][a-z0-9_]*)"
        r"(?:\s+(?:AS\s+)?(?P<alias>[a-z_][a-z0-9_]*))?",
        re.IGNORECASE,
    )
    reserved = {
        "where",
        "join",
        "on",
        "union",
        "group",
        "order",
        "having",
        "for",
        "left",
        "right",
        "inner",
        "outer",
        "cross",
        "limit",
        "returning",
        "set",
    }
    qualified_re = re.compile(
        r"\b(?P<alias>[a-z_][a-z0-9_]*)\.(?P<column>[a-z_][a-z0-9_]*)\b",
        re.IGNORECASE,
    )
    for segment in segments:
        aliases: dict[str, str] = {}
        for match in alias_re.finditer(segment):
            relation = match.group("relation").lower()
            alias = (match.group("alias") or relation).lower()
            if alias in reserved:
                alias = relation
            aliases[relation] = relation
            aliases[alias] = relation
        for match in qualified_re.finditer(segment):
            relation = aliases.get(match.group("alias").lower())
            if relation in columns:
                columns[relation].add(match.group("column").lower())
    override = _DIRECT_READ_COLUMN_OVERRIDES.get(signature, {})
    if set(override) - set(relations):
        raise MaterializationError(f"TASK7_V12_DIRECT_READ_OVERRIDE_ORPHAN:{signature}")
    for relation, reviewed_columns in override.items():
        columns[relation] = set(reviewed_columns)
    empty_relations = [relation for relation in relations if not columns.get(relation)]
    if empty_relations:
        raise MaterializationError(
            "TASK7_V12_DIRECT_READ_COLUMN_CLOSURE_INCOMPLETE:"
            f"{signature}:{','.join(empty_relations)}"
        )
    body_sha256 = _sha256(body.encode("utf-8"))
    return [
        [
            relation,
            sorted(columns[relation]),
            ["SQL_BODY_SHA256", body_sha256],
        ]
        for relation in relations
    ]


def _trigger_targets(
    statements: list[str],
) -> dict[str, list[list[str]]]:
    targets: dict[str, list[list[str]]] = {}
    for statement in statements:
        for match in _TRIGGER_RE.finditer(statement):
            targets.setdefault(match.group("function").lower(), []).append(
                [match.group("table").lower(), match.group("trigger").lower()]
            )
    return targets


def _trigger_event_coordinates(
    statements: list[str],
) -> dict[str, list[list[Any]]]:
    coordinates: dict[str, list[list[Any]]] = {}
    for statement in statements:
        for match in _TRIGGER_COORDINATE_RE.finditer(statement):
            function_name = match.group("function").lower()
            middle = match.group("middle").upper()
            for event in re.split(r"\s+OR\s+", match.group("events").upper()):
                coordinates.setdefault(function_name, []).append(
                    [
                        match.group("table").lower(),
                        match.group("trigger").lower(),
                        match.group("timing").upper(),
                        event,
                        match.group("level").upper(),
                        "O",
                        match.group("constraint") is not None,
                        "DEFERRABLE" in middle and "NOT DEFERRABLE" not in middle,
                        "INITIALLY DEFERRED" in middle,
                    ]
                )
    return coordinates


def _legacy_trigger_targets(artifact: dict[str, Any]) -> dict[str, list[list[str]]]:
    targets: dict[str, list[list[str]]] = {}
    replacements = artifact["erasure_execution_registry"]["rows"][0][
        "legacy_guard_replacement_tuple"
    ]
    for replacement in replacements:
        function_name = str(replacement[1]).partition("(")[0].lower()
        for trigger in replacement[5]:
            relation, trigger_name = str(trigger[0]), str(trigger[1])
            targets.setdefault(function_name, []).append(
                [relation.removeprefix("public.").lower(), trigger_name.lower()]
            )
    return targets


def _legacy_trigger_event_coordinates(
    artifact: dict[str, Any],
) -> dict[str, list[list[Any]]]:
    coordinates: dict[str, list[list[Any]]] = {}
    replacements = artifact["erasure_execution_registry"]["rows"][0][
        "legacy_guard_replacement_tuple"
    ]
    for replacement in replacements:
        function_name = str(replacement[1]).partition("(")[0].lower()
        for trigger in replacement[5]:
            relation, trigger_name, timing, events, level, enabled, deferred, initially = trigger
            for event in events:
                coordinates.setdefault(function_name, []).append(
                    [
                        str(relation).removeprefix("public.").lower(),
                        str(trigger_name).lower(),
                        str(timing).upper(),
                        str(event).upper(),
                        str(level).upper(),
                        str(enabled),
                        False,
                        bool(deferred),
                        bool(initially),
                    ]
                )
    return coordinates


def _function_sql(
    signature: str,
    owner: str,
    security_mode: str,
    explicit_grantees: list[str],
) -> tuple[str, list[str]]:
    owner_sql = f"ALTER FUNCTION {signature} OWNER TO {owner}"
    acl_sql = [
        f"ALTER FUNCTION {signature} SECURITY {security_mode}",
        f"ALTER FUNCTION {signature} SET search_path TO pg_catalog, pg_temp",
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
    ]
    acl_sql.extend(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {grantee}" for grantee in explicit_grantees
    )
    return owner_sql, acl_sql


def _build_function_rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    provisional: list[dict[str, Any]] = []
    sql_rows = artifact["sql_authority_registry"]["rows"]
    authority_ids = {str(row["authority_id"]) for row in sql_rows}
    legacy_targets = _legacy_trigger_targets(artifact)
    legacy_coordinates = _legacy_trigger_event_coordinates(artifact)
    for row_ordinal, authority in enumerate(sql_rows):
        authority_id = str(authority["authority_id"])
        parents = [str(value) for value in authority["declared_parent_relation_tuple"]]
        statements = _decode_statements(authority)
        targets = _trigger_targets(statements)
        coordinates = _trigger_event_coordinates(statements)
        for statement_ordinal, statement in enumerate(statements):
            for occurrence_ordinal, match in enumerate(_FUNCTION_RE.finditer(statement)):
                signature = _signature(match)
                name = signature.partition("(")[0]
                returns_trigger = match.group("returns").strip().lower() == "trigger"
                attributes = match.group("attributes").upper()
                security_mode = "DEFINER" if "SECURITY DEFINER" in attributes else "INVOKER"
                if returns_trigger and security_mode != "DEFINER":
                    raise MaterializationError(
                        f"TASK7_V12_TRIGGER_NOT_SECURITY_DEFINER:{signature}"
                    )
                body = match.group("body")
                select_relations = _direct_relations(body, parents)
                provisional.append(
                    {
                        "authority_id": authority_id,
                        "function_signature": signature,
                        "function_kind": "TRIGGER_GUARD" if returns_trigger else "HELPER",
                        "definition_origin": "SQL_AUTHORITY",
                        "definition_locator": (
                            f"sql_authority_registry.rows[{row_ordinal}]."
                            f"body_sql_utf8_hex_tuple[{statement_ordinal}]."
                            f"function[{occurrence_ordinal}]"
                        ),
                        "owner_role": _GUARD_OWNER,
                        "install_scope": (
                            "CURRENT_0038"
                            if row_ordinal >= _CURRENT_AUTHORITY_START
                            else "FUTURE_INACTIVE"
                        ),
                        "security_mode": security_mode,
                        "search_path_tuple": ["pg_catalog", "pg_temp"],
                        "sql_authority_parent_relation_tuple": parents,
                        "declared_parent_relation_tuple": select_relations,
                        "select_relation_tuple": select_relations,
                        "direct_read_column_tuple": _direct_read_columns(
                            signature,
                            body,
                            select_relations,
                        ),
                        "insert_relation_tuple": [],
                        "update_relation_tuple": [],
                        "delete_relation_tuple": [],
                        "truncate_relation_tuple": [],
                        "references_relation_tuple": [],
                        "trigger_relation_tuple": [],
                        "target_trigger_tuple": (
                            targets.get(name, []) or legacy_targets.get(name, [])
                        ),
                        "target_event_coordinate_tuple": (
                            coordinates.get(name, []) or legacy_coordinates.get(name, [])
                        ),
                        "_callee_names": sorted(
                            {
                                called.group("function").lower()
                                for called in _FUNCTION_CALL_RE.finditer(body)
                                if called.group("function").lower() != name
                            }
                        ),
                        "explicit_execute_grantee_tuple": [],
                        "public_execute": False,
                        "application_role": _APPLICATION_ROLE,
                        "application_role_binding": "RUNTIME_SESSION_USER",
                        "application_role_execute": False,
                        "acl_correction_id": _HELPER_CORRECTIONS.get(signature),
                    }
                )
    for external in _EXTERNAL_HELPERS:
        if external["authority_id"] not in authority_ids:
            raise MaterializationError(
                f"TASK7_V12_EXTERNAL_HELPER_AUTHORITY_MISSING:{external['authority_id']}"
            )
        provisional.append(
            {
                "authority_id": external["authority_id"],
                "function_signature": external["function_signature"],
                "function_kind": "HELPER",
                "definition_origin": "EXTERNAL",
                "definition_locator": external["definition_locator"],
                "owner_role": external["owner_role"],
                "install_scope": (
                    "CURRENT_0038"
                    if external["owner_role"] == _GUARD_OWNER
                    else "EXTERNAL_DEPENDENCY"
                ),
                "security_mode": external["security_mode"],
                "search_path_tuple": ["pg_catalog", "pg_temp"],
                "sql_authority_parent_relation_tuple": external["select_relation_tuple"],
                "declared_parent_relation_tuple": external["select_relation_tuple"],
                "select_relation_tuple": external["select_relation_tuple"],
                "direct_read_column_tuple": [
                    [
                        relation,
                        sorted(_EXTERNAL_READ_COLUMNS[external["function_signature"]][relation]),
                        ["EXTERNAL_DEFINITION_LOCATOR", external["definition_locator"]],
                    ]
                    for relation in external["select_relation_tuple"]
                ],
                "insert_relation_tuple": [],
                "update_relation_tuple": [],
                "delete_relation_tuple": [],
                "truncate_relation_tuple": [],
                "references_relation_tuple": [],
                "trigger_relation_tuple": [],
                "target_trigger_tuple": [],
                "target_event_coordinate_tuple": [],
                "_callee_names": [],
                "explicit_execute_grantee_tuple": external["explicit_execute_grantee_tuple"],
                "public_execute": False,
                "application_role": _APPLICATION_ROLE,
                "application_role_binding": "RUNTIME_SESSION_USER",
                "application_role_execute": False,
                "acl_correction_id": None,
            }
        )
    by_name = {
        str(row["function_signature"]).partition("(")[0]: str(row["function_signature"])
        for row in provisional
    }
    if len(by_name) != len(provisional):
        raise MaterializationError("TASK7_V12_FUNCTION_CENSUS_DUPLICATE")
    for ordinal, row in enumerate(provisional):
        callees: list[str] = []
        for name in row.pop("_callee_names"):
            callee_signature = by_name.get(name)
            if callee_signature is None:
                raise MaterializationError(f"TASK7_V12_HELPER_CALL_ORPHAN:{name}")
            callees.append(callee_signature)
        row["function_ordinal"] = ordinal
        row["helper_call_signature_tuple"] = callees
        owner_sql, acl_sql = _function_sql(
            str(row["function_signature"]),
            str(row["owner_role"]),
            str(row["security_mode"]),
            list(row["explicit_execute_grantee_tuple"]),
        )
        row["owner_sql_utf8_hex"] = owner_sql.encode("utf-8").hex()
        row["acl_sql_utf8_hex_tuple"] = [statement.encode("utf-8").hex() for statement in acl_sql]
    by_signature = {str(row["function_signature"]): row for row in provisional}

    def transitive_reads(
        signature: str,
        visiting: frozenset[str] = frozenset(),
    ) -> list[list[Any]]:
        if signature in visiting:
            return []
        row = by_signature[signature]
        merged: dict[str, tuple[set[str], set[tuple[str, str]]]] = {}
        sources = [row["direct_read_column_tuple"]]
        sources.extend(
            transitive_reads(callee, visiting | {signature})
            for callee in row["helper_call_signature_tuple"]
        )
        for source in sources:
            for relation, columns, evidence in source:
                column_set, evidence_set = merged.setdefault(
                    str(relation),
                    (set(), set()),
                )
                column_set.update(map(str, columns))
                evidence_rows = (
                    evidence if evidence and isinstance(evidence[0], list) else [evidence]
                )
                for evidence_row in evidence_rows:
                    evidence_set.add((str(evidence_row[0]), str(evidence_row[1])))
        return [
            [
                relation,
                sorted(column_set),
                [list(value) for value in sorted(evidence_set)],
            ]
            for relation, (column_set, evidence_set) in sorted(merged.items())
        ]

    for row in provisional:
        row["transitive_read_column_tuple"] = transitive_reads(str(row["function_signature"]))
        _rehash_acl_row(row)
    if len(provisional) != 34:
        raise MaterializationError(f"TASK7_V12_FUNCTION_CENSUS_INVALID:{len(provisional)}")
    for row in provisional:
        target_pairs = {
            (str(relation), str(trigger)) for relation, trigger in row["target_trigger_tuple"]
        }
        coordinate_targets = {
            (str(value[0]), str(value[1])) for value in row["target_event_coordinate_tuple"]
        }
        if target_pairs != coordinate_targets:
            raise MaterializationError(
                f"TASK7_V12_TRIGGER_COORDINATE_CLOSURE_INVALID:{row['function_signature']}"
            )
    trigger_census = {
        scope: (
            sum(
                len(row["target_trigger_tuple"])
                for row in provisional
                if row["install_scope"] == scope
            ),
            sum(
                len(row["target_event_coordinate_tuple"])
                for row in provisional
                if row["install_scope"] == scope
            ),
        )
        for scope in ("CURRENT_0038", "FUTURE_INACTIVE")
    }
    if trigger_census != {
        "CURRENT_0038": (73, 192),
        "FUTURE_INACTIVE": (81, 242),
    }:
        raise MaterializationError(
            f"TASK7_V12_TRIGGER_COORDINATE_CENSUS_INVALID:{trigger_census!r}"
        )
    return provisional


def _clean_role_preflight_sql() -> str:
    return (
        "DO $$DECLARE guard_oid oid; BEGIN SELECT oid INTO guard_oid FROM "
        f"pg_catalog.pg_roles WHERE rolname='{_GUARD_OWNER}'; "
        "IF guard_oid IS NULL OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m "
        "WHERE m.member=guard_oid OR m.roleid=guard_oid) OR EXISTS (SELECT 1 FROM "
        "pg_catalog.pg_database d WHERE d.datdba=guard_oid OR EXISTS (SELECT 1 FROM "
        "pg_catalog.aclexplode(d.datacl) a WHERE a.grantee=guard_oid)) OR EXISTS "
        "(SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspowner=guard_oid OR "
        "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(n.nspacl) a WHERE "
        "a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c "
        "WHERE c.relowner=guard_oid OR EXISTS (SELECT 1 FROM "
        "pg_catalog.aclexplode(c.relacl) a WHERE a.grantee=guard_oid)) OR EXISTS "
        "(SELECT 1 FROM pg_catalog.pg_attribute at WHERE at.attnum>0 AND NOT "
        "at.attisdropped AND EXISTS (SELECT 1 FROM "
        "pg_catalog.aclexplode(at.attacl) a WHERE a.grantee=guard_oid)) OR EXISTS "
        "(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.proowner=guard_oid OR EXISTS "
        "(SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a WHERE "
        "a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM "
        "pg_catalog.pg_default_acl d WHERE d.defaclrole=guard_oid OR EXISTS "
        "(SELECT 1 FROM pg_catalog.aclexplode(d.defaclacl) a WHERE "
        "a.grantee=guard_oid)) THEN RAISE EXCEPTION "
        "'TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN' USING ERRCODE='42501'; "
        "END IF; END$$"
    )


def _posture_assertion_sql() -> str:
    return (
        "DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r "
        f"WHERE r.rolname='{_GUARD_OWNER}' AND NOT r.rolcanlogin AND "
        "NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND "
        "NOT r.rolinherit AND NOT r.rolbypassrls AND NOT r.rolreplication) "
        "OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN "
        "pg_catalog.pg_roles member_role ON member_role.oid=m.member "
        "JOIN pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid "
        f"WHERE member_role.rolname='{_GUARD_OWNER}' OR "
        f"granted_role.rolname='{_GUARD_OWNER}') THEN RAISE EXCEPTION "
        "'TASK7_GUARD_FUNCTION_OWNER_POSTURE_INVALID' USING ERRCODE='42501'; "
        "END IF; END$$"
    )


def _column_acl_sql(
    relation_columns: list[list[Any]],
) -> tuple[list[str], list[str], list[str]]:
    table_revokes: list[str] = []
    column_revokes: list[str] = []
    column_grants: list[str] = []
    for relation, columns in relation_columns:
        rendered_columns = ",".join(map(str, columns))
        if not rendered_columns:
            raise MaterializationError(f"TASK7_V12_COLUMN_GRANT_EMPTY:{relation}")
        table_revokes.append(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{relation} FROM {_GUARD_OWNER}"
        )
        column_revokes.append(
            f"REVOKE SELECT ({rendered_columns}) ON TABLE public.{relation} FROM {_GUARD_OWNER}"
        )
        column_grants.append(
            f"GRANT SELECT ({rendered_columns}) ON TABLE public.{relation} TO {_GUARD_OWNER}"
        )
    return table_revokes, column_revokes, column_grants


def _function_drop_order(rows: list[dict[str, Any]]) -> list[str]:
    current = {
        str(row["function_signature"]): row
        for row in rows
        if row["install_scope"] == "CURRENT_0038"
    }
    depths: dict[str, int] = {}

    def depth(signature: str, visiting: frozenset[str] = frozenset()) -> int:
        if signature in visiting:
            raise MaterializationError("TASK7_V12_CURRENT_HELPER_CALL_CYCLE")
        if signature in depths:
            return depths[signature]
        callees = [
            str(callee)
            for callee in current[signature]["helper_call_signature_tuple"]
            if callee in current
        ]
        value = (
            0
            if not callees
            else 1 + max(depth(callee, visiting | {signature}) for callee in callees)
        )
        depths[signature] = value
        return value

    for signature in current:
        depth(signature)
    return sorted(
        current,
        key=lambda signature: (
            -depths[signature],
            int(current[signature]["function_ordinal"]),
        ),
    )


def _legacy_restoration_sql(
    artifact: dict[str, Any],
) -> tuple[list[str], list[str]]:
    function_projection = _retained_0037_catalog_projection(artifact)
    trigger_projection = _retained_0037_trigger_catalog_projection(artifact)
    function_restores = [bytes.fromhex(row[1]).decode("utf-8") for row in function_projection]
    trigger_restores = [
        f"CREATE TRIGGER {trigger_name} {timing} "
        f"{' OR '.join(map(str, events))} ON public.{relation} "
        f"FOR EACH ROW EXECUTE FUNCTION {signature}"
        for (
            signature,
            relation,
            trigger_name,
            timing,
            events,
            level,
            _enabled,
            is_constraint,
            deferred,
            initially,
            _source_path,
            _source_sha512,
        ) in trigger_projection
        if level == "ROW" and not is_constraint and not deferred and not initially
    ]
    if len(function_restores) != 3 or len(trigger_restores) != 9:
        raise MaterializationError("TASK7_V12_RETAINED_RESTORATION_CENSUS_INVALID")
    return function_restores, trigger_restores


def _retained_0037_catalog_projection(
    artifact: dict[str, Any],
) -> list[list[Any]]:
    acl = artifact.get("sql_authority_registry", {}).get("guard_function_acl_registry")
    if isinstance(acl, dict) and "retained_0037_catalog_projection_tuple" in acl:
        return _validate_retained_0037_catalog_projection(
            acl["retained_0037_catalog_projection_tuple"]
        )
    repo_root = Path(__file__).resolve().parents[1]
    return _authenticated_retained_0037_catalog_projection(repo_root)


def _retained_0037_trigger_catalog_projection(
    artifact: dict[str, Any],
) -> list[list[Any]]:
    acl = artifact.get("sql_authority_registry", {}).get("guard_function_acl_registry")
    if isinstance(acl, dict) and "retained_0037_trigger_catalog_projection_tuple" in acl:
        return _validate_retained_0037_trigger_catalog_projection(
            acl["retained_0037_trigger_catalog_projection_tuple"]
        )
    repo_root = Path(__file__).resolve().parents[1]
    return _authenticated_retained_0037_trigger_catalog_projection(repo_root)


def _retained_0037_catalog_assertion_sql(
    projection: list[list[Any]],
) -> str:
    values = ",".join(
        f"('{signature}','{prosrc_hex}')"
        for (
            signature,
            _function_sql_hex,
            prosrc_hex,
            _source_path,
            _source_sha512,
            _owner,
            _definer,
            _config,
            _acl,
        ) in projection
    )
    return (
        "DO $$BEGIN IF EXISTS (SELECT 1 FROM (VALUES "
        f"{values}) AS expected(function_signature,prosrc_utf8_hex) "
        "LEFT JOIN pg_catalog.pg_proc p ON "
        "p.oid=pg_catalog.to_regprocedure(expected.function_signature) WHERE "
        "p.oid IS NULL OR p.proowner<>(current_user)::pg_catalog.regrole OR "
        "p.prosrc<>pg_catalog.convert_from(pg_catalog.decode("
        "expected.prosrc_utf8_hex,'hex'),'UTF8') OR p.prosecdef OR "
        "p.proconfig IS NOT NULL OR p.proacl IS NOT NULL) THEN RAISE EXCEPTION "
        "'TASK7_RETAINED_0037_CATALOG_BASELINE_INVALID' USING ERRCODE='42501'; "
        "END IF; END$$"
    )


def _zero_privilege_assertion_sql() -> str:
    return _clean_role_preflight_sql().replace(
        "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN",
        "TASK7_GUARD_FUNCTION_OWNER_DOWNGRADE_ACL_INVALID",
    )


def _build_downgrade_authority(
    artifact: dict[str, Any],
    rows: list[dict[str, Any]],
    table_revokes: list[str],
    column_revokes: list[str],
) -> dict[str, Any]:
    current_rows = [row for row in rows if row["install_scope"] == "CURRENT_0038"]
    new_trigger_drops: list[str] = []
    retained_trigger_drops: list[str] = []
    for row in current_rows:
        destination = (
            retained_trigger_drops
            if row["function_signature"] in _RETAINED_0037_TRIGGER_FUNCTIONS
            else new_trigger_drops
        )
        destination.extend(
            f"DROP TRIGGER {trigger} ON public.{relation}"
            for relation, trigger in row["target_trigger_tuple"]
        )
    if len(new_trigger_drops) != 64 or len(retained_trigger_drops) != 9:
        raise MaterializationError(
            "TASK7_V12_DOWNGRADE_TRIGGER_CENSUS_INVALID:"
            f"new={len(new_trigger_drops)}:retained={len(retained_trigger_drops)}"
        )
    function_order = _function_drop_order(rows)
    function_drops = [f"DROP FUNCTION {signature}" for signature in function_order]
    function_restores, trigger_restores = _legacy_restoration_sql(artifact)
    retained_projection = _retained_0037_catalog_projection(artifact)
    retained_catalog_assertion = _retained_0037_catalog_assertion_sql(retained_projection)
    external_revoke = [
        "REVOKE EXECUTE ON FUNCTION "
        "public.idis_task7_approved_erasure_context_v1(uuid,uuid) "
        f"FROM {_GUARD_OWNER}"
    ]
    schema_revoke = [f"REVOKE USAGE ON SCHEMA public FROM {_GUARD_OWNER}"]
    zero_assertion = _zero_privilege_assertion_sql()
    ordered = [
        *new_trigger_drops,
        *retained_trigger_drops,
        *external_revoke,
        *column_revokes,
        *table_revokes,
        *schema_revoke,
        *function_drops,
        zero_assertion,
        *function_restores,
        *trigger_restores,
        retained_catalog_assertion,
    ]
    authority = {
        "registry_version": "task7-guard-function-downgrade-authority-v1",
        "new_trigger_drop_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in new_trigger_drops
        ],
        "retained_trigger_drop_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in retained_trigger_drops
        ],
        "external_helper_execute_revoke_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in external_revoke
        ],
        "column_select_revoke_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in column_revokes
        ],
        "table_privilege_revoke_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in table_revokes
        ],
        "schema_usage_revoke_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in schema_revoke
        ],
        "current_function_drop_signature_tuple": function_order,
        "current_function_drop_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in function_drops
        ],
        "retained_function_signature_tuple": list(_RETAINED_0037_SIGNATURES),
        "retained_function_restore_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in function_restores
        ],
        "retained_trigger_restore_sql_utf8_hex_tuple": [
            value.encode("utf-8").hex() for value in trigger_restores
        ],
        "retained_catalog_postrestore_assertion_sql_utf8_hex": (
            retained_catalog_assertion.encode("utf-8").hex()
        ),
        "zero_privilege_assertion_sql_utf8_hex": zero_assertion.encode("utf-8").hex(),
        "restored_catalog_contract": {
            "owner_binding": "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER",
            "prosecdef": False,
            "proconfig": None,
            "proacl": None,
        },
        "ordered_sql_utf8_hex_tuple": [value.encode("utf-8").hex() for value in ordered],
    }
    authority["expected_sha256"] = _h(
        [
            "task7-guard-function-downgrade-authority-v1",
            {key: value for key, value in authority.items() if key != "expected_sha256"},
        ]
    )
    return authority


def _build_acl_registry(artifact: dict[str, Any]) -> dict[str, Any]:
    rows = _build_function_rows(artifact)
    select_relations: list[str] = []
    relation_columns: dict[str, set[str]] = {}
    observed: set[str] = set()
    for row in rows:
        if row["install_scope"] != "CURRENT_0038":
            continue
        for relation, columns, _evidence in row["direct_read_column_tuple"]:
            if relation not in observed:
                observed.add(relation)
                select_relations.append(relation)
            relation_columns.setdefault(str(relation), set()).update(map(str, columns))
    select_relation_columns = [
        [relation, sorted(relation_columns[relation])] for relation in select_relations
    ]
    column_count = sum(len(columns) for _relation, columns in select_relation_columns)
    if column_count != 366:
        raise MaterializationError(f"TASK7_V12_CURRENT_COLUMN_CENSUS_INVALID:{column_count}")
    retained_projection = _retained_0037_catalog_projection(artifact)
    retained_trigger_projection = _retained_0037_trigger_catalog_projection(artifact)
    retained_catalog_preflight = _retained_0037_catalog_assertion_sql(retained_projection)
    role = {
        "role_name": _GUARD_OWNER,
        "provisioning_mode": "OPERATOR_BOOTSTRAP_REQUIRED",
        "posture": copy.deepcopy(_ROLE_POSTURE),
        "role_membership_tuple": [],
        "table_ownership_tuple": [],
        "schema_usage_tuple": ["public"],
        "schema_create": False,
        "select_relation_tuple": select_relations,
        "select_relation_column_tuple": select_relation_columns,
        "insert_relation_tuple": [],
        "update_relation_tuple": [],
        "delete_relation_tuple": [],
        "truncate_relation_tuple": [],
        "references_relation_tuple": [],
        "trigger_relation_tuple": [],
        "rls_contract_tuple": copy.deepcopy(_RLS_CONTRACT),
        "clean_role_preflight_sql_utf8_hex": _clean_role_preflight_sql().encode("utf-8").hex(),
        "posture_assertion_sql_utf8_hex": _posture_assertion_sql().encode("utf-8").hex(),
    }
    role_acl_sql = [
        f"REVOKE ALL ON SCHEMA public FROM {_GUARD_OWNER}",
        f"GRANT USAGE ON SCHEMA public TO {_GUARD_OWNER}",
    ]
    table_revokes, column_revokes, column_grants = _column_acl_sql(select_relation_columns)
    for table_revoke, column_revoke, column_grant in zip(
        table_revokes,
        column_revokes,
        column_grants,
        strict=True,
    ):
        role_acl_sql.extend((table_revoke, column_revoke, column_grant))
    role["acl_sql_utf8_hex_tuple"] = [statement.encode("utf-8").hex() for statement in role_acl_sql]
    _rehash_role(role)
    installation = [
        _clean_role_preflight_sql(),
        _posture_assertion_sql(),
        retained_catalog_preflight,
        *role_acl_sql,
    ]
    for row in rows:
        if row["install_scope"] == "FUTURE_INACTIVE":
            continue
        if row["install_scope"] == "CURRENT_0038":
            installation.append(bytes.fromhex(row["owner_sql_utf8_hex"]).decode("utf-8"))
        installation.extend(
            bytes.fromhex(value).decode("utf-8") for value in row["acl_sql_utf8_hex_tuple"]
        )
    current_signatures = [
        row["function_signature"] for row in rows if row["install_scope"] == "CURRENT_0038"
    ]
    if len(current_signatures) != 19 or len(select_relations) != 36:
        raise MaterializationError(
            "TASK7_V12_CURRENT_ACL_CENSUS_INVALID:"
            f"functions={len(current_signatures)}:relations={len(select_relations)}"
        )
    downgrade = _build_downgrade_authority(
        artifact,
        rows,
        table_revokes,
        column_revokes,
    )
    registry = {
        "registry_version": "task7-guard-function-acl-authority-v1",
        "guard_owner_role": role,
        "function_rows": rows,
        "current_install_function_signature_tuple": current_signatures,
        "current_column_grant_sql_utf8_hex_tuple": [
            statement.encode("utf-8").hex() for statement in column_grants
        ],
        "current_column_revoke_sql_utf8_hex_tuple": [
            statement.encode("utf-8").hex() for statement in column_revokes
        ],
        "retained_0037_catalog_projection_tuple": retained_projection,
        "retained_0037_trigger_catalog_projection_tuple": retained_trigger_projection,
        "retained_0037_catalog_preflight_sql_utf8_hex": retained_catalog_preflight.encode(
            "utf-8"
        ).hex(),
        "installation_sql_utf8_hex_tuple": [
            statement.encode("utf-8").hex() for statement in installation
        ],
        "downgrade_authority": downgrade,
    }
    registry["expected_sha256"] = _h(
        [
            _ACL_REGISTRY_HASH_DOMAIN,
            registry["guard_owner_role"],
            registry["function_rows"],
            registry["current_install_function_signature_tuple"],
            registry["current_column_grant_sql_utf8_hex_tuple"],
            registry["current_column_revoke_sql_utf8_hex_tuple"],
            registry["retained_0037_catalog_projection_tuple"],
            registry["retained_0037_trigger_catalog_projection_tuple"],
            registry["retained_0037_catalog_preflight_sql_utf8_hex"],
            registry["installation_sql_utf8_hex_tuple"],
            registry["downgrade_authority"],
        ]
    )
    return registry


def _install_acl_registry(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "expected_sha256"):
        raise MaterializationError("TASK7_V12_SQL_REGISTRY_BASE_SHAPE_INVALID")
    acl = _build_acl_registry(artifact)
    artifact["sql_authority_registry"] = {
        "rows": section["rows"],
        "guard_function_acl_registry": acl,
        "expected_sha256": _h([_V12_SQL_REGISTRY_HASH_DOMAIN, section["rows"], acl]),
    }


def _remove_acl_registry(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        raise MaterializationError("TASK7_V12_SQL_REGISTRY_SHAPE_INVALID")
    artifact["sql_authority_registry"] = {
        "rows": section["rows"],
        "expected_sha256": _h([_V11_SQL_REGISTRY_HASH_DOMAIN, section["rows"]]),
    }


def project_v12_to_v11(v12: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact reviewer-pinned v11 bytes from a v12 candidate."""

    projected = copy.deepcopy(v12)
    _remove_acl_registry(projected)
    _transform_sql_bodies(projected, reverse=True)
    projected["sql_authority_registry"]["expected_sha256"] = _h(
        [_V11_SQL_REGISTRY_HASH_DOMAIN, projected["sql_authority_registry"]["rows"]]
    )
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    if hashlib.sha512(_render(projected)).hexdigest() != _V11_SOURCE_SHA512:
        raise MaterializationError("TASK7_V12_EXACT_V11_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v11: dict[str, Any], v12: dict[str, Any]) -> None:
    for section in v11:
        if section in {
            "metadata",
            "digest_recipe_registry",
            "sql_authority_registry",
        }:
            continue
        if v12.get(section) != v11[section]:
            raise MaterializationError(f"TASK7_V12_UNAUTHORIZED_DRIFT:{section}")
    if tuple(v12) != tuple(v11):
        raise MaterializationError("TASK7_V12_SECTION_ORDER_DRIFT")
    if project_v12_to_v11(v12) != v11:
        raise MaterializationError("TASK7_V12_V11_PROJECTION_DRIFT")


def _load_v11(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V11_SOURCE_SHA512:
        raise MaterializationError("TASK7_V12_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V12_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MaterializationError("TASK7_V12_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v11-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v11"
        or metadata.get("append_frozen") is not False
    ):
        raise MaterializationError("TASK7_V12_BASE_METADATA_INVALID")
    return value


def build_v12(v11: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v12 candidate."""

    artifact = copy.deepcopy(v11)
    _transform_sql_bodies(artifact, reverse=False)
    _install_acl_registry(artifact)
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v11, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise MaterializationError("TASK7_V12_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V12_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v11_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rendered = _render(build_v12(_load_v11(args.base_artifact)))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V12_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
