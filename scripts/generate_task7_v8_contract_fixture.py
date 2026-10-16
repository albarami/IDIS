"""Validate full-strict-v8 and admit its fixture after executable PG proof.

The append-frozen artifact is the sole normative byte input.  Validation
independently closes its reviewed current-guard targets over the 0038 relation
census, parses every literal trigger binding, and verifies the signed
PostgreSQL execution receipt before a fixture may be emitted.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast


def _load_v7_helpers() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v7_contract_fixture.py")
    spec = importlib.util.spec_from_file_location(
        "_task7_v7_fixture_helpers_for_v8",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK7_V8_HELPER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V7 = _load_v7_helpers()
ContractArtifactError = _V7.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V7._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V7._COMPONENT_ORDER)

_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_CENSUS_ENVELOPE_SCHEMA = "task7-current-guard-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-current-guard-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-current-guard-postgres-census-signable-v1"
_V8_AUTHORITY_IDS = (
    "current-source-exact-one-v1",
    "current-output-exact-one-v1",
    "current-receipt-equality-v1",
    "current-input-binding-admission-v1",
    "current-append-only-lease-v1",
    "current-source-bound-v1",
    "current-output-bound-v1",
    "current-runs-contract-immutable-v1",
    "current-run-steps-contract-immutable-v1",
    "current-task5-coverage-replacement-v1",
    "current-task6-immutable-replacement-v1",
)
_V8_AUTHORITY_KINDS = (
    "CONSTRAINT_TRIGGER",
    "CONSTRAINT_TRIGGER",
    "CONSTRAINT_TRIGGER",
    "APPEND_ONLY_TRIGGER",
    "APPEND_ONLY_TRIGGER",
    "BEFORE_ROW_TRIGGER",
    "BEFORE_ROW_TRIGGER",
    "BEFORE_ROW_TRIGGER",
    "BEFORE_ROW_TRIGGER",
    "BEFORE_ROW_TRIGGER",
    "BEFORE_ROW_TRIGGER",
)
_TARGET_GROUP_COUNTS = (32, 7, 25, 9)
_CURRENT_GUARD_TARGET_COUNT = 73
_PRE_0038_TARGET_RELATIONS = frozenset({"methodology_extraction_task_spans"})
_LOCKED_OUTWARD_CODES = frozenset(
    {
        "CONSUMER_RECEIPT_INCOMPLETE",
        "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE",
        "METHODOLOGY_COVERAGE_SCOPE_INVALID",
        "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE",
        "PIPELINE_CONTRACT_HASH_MISMATCH",
        "STEP_OUTPUT_MISSING",
        "STEP_REHYDRATION_FAILED",
        "STRICT_PREFLIGHT_PROOF_MISMATCH",
        "TASK7_SOURCE_FREEZE_FENCED",
        "TASK7_SOURCE_TERMINAL_STATE_INVALID",
        "WORKER_LEASE_FENCED",
    }
)
_BANNED_UNAPPROVED_CODES = frozenset(
    {
        "TASK7_INPUT_BINDING_ADMISSION_INVALID",
        "TASK7_OUTPUT_EXACT_ONE_INVALID",
        "TASK7_RUN_GRAPH_APPEND_ONLY",
        "TASK7_SOURCE_EXACT_ONE_INVALID",
    }
)
_TRIGGER_RE = re.compile(
    r"^CREATE\s+(?P<constraint>CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<trigger>[a-z_][a-z0-9_]*)\s+"
    r"(?P<timing>BEFORE|AFTER)\s+"
    r"(?P<events>(?:INSERT|UPDATE|DELETE)"
    r"(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)\s+"
    r"ON\s+public\.(?P<table>[a-z_][a-z0-9_]*)\s+"
    r"(?P<deferrable>DEFERRABLE\s+INITIALLY\s+DEFERRED\s+)?"
    r"FOR\s+EACH\s+ROW\s+EXECUTE\s+FUNCTION\s+"
    r"public\.(?P<function>[a-z_][a-z0-9_]*)\(\)$",
    flags=re.IGNORECASE,
)
_FUNCTION_RE = re.compile(
    r"^CREATE\s+(?P<replace>OR\s+REPLACE\s+)?FUNCTION\s+"
    r"public\.(?P<function>[a-z_][a-z0-9_]*)\(\)\s+RETURNS\s+trigger\s+"
    r"LANGUAGE\s+plpgsql\s+SET\s+search_path=pg_catalog,pg_temp\s+AS\s+"
    r"\$\$(?P<body>.+)\$\$$",
    flags=re.IGNORECASE | re.DOTALL,
)
_REVOKE_RE = re.compile(
    r"^REVOKE\s+ALL\s+ON\s+FUNCTION\s+"
    r"public\.(?P<function>[a-z_][a-z0-9_]*)\(\)\s+FROM\s+PUBLIC$",
    flags=re.IGNORECASE,
)
_RAISE_CODE_RE = re.compile(
    r"\bRAISE\s+EXCEPTION\s+'(?P<code>[A-Z][A-Z0-9_]+)'",
    flags=re.IGNORECASE,
)


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V7._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> None:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V7._decode_artifact(raw))


def _decode_hex(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", f"{label} is not hex text")
    try:
        decoded = bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            f"{label} is not canonical UTF-8 hex",
        ) from exc
    if decoded.encode("utf-8").hex() != value:
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            f"{label} is not lowercase canonical UTF-8 hex",
        )
    return decoded


def _reviewed_guard_contract(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "metadata is absent")
    reviewed_hex = metadata.get("reviewed_design_utf8_hex")
    reviewed = json.loads(_decode_hex(reviewed_hex, "reviewed_design").encode())
    if (
        not isinstance(reviewed, list)
        or reviewed[:2]
        != [
            "full-strict-v8-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v8"],
        ]
        or not isinstance(reviewed[-1], list)
        or reviewed[-1][0] != "current_guard_authority_v1"
    ):
        _error(
            "TASK7_CURRENT_GUARD_CONTRACT_INVALID",
            "reviewed current-guard literal is absent",
        )
    try:
        contract = dict(reviewed[-1][1])
    except (TypeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_CURRENT_GUARD_CONTRACT_INVALID",
            "reviewed current-guard tuple is malformed",
        ) from exc
    expected_fields = (
        "deferred_target_count",
        "append_only_target_count",
        "bound_immutability_target_count",
        "retained_trigger_target_count",
        "ordered_authority_ids",
        "ordered_target_partition",
        "reviewed_downgrade_target_tuple",
        "current_0038_target_relation_tuple",
        "legal_case_count",
        "illegal_case_count",
        "direct_sql_case_count",
    )
    if tuple(contract) != expected_fields:
        _error(
            "TASK7_CURRENT_GUARD_CONTRACT_INVALID",
            "reviewed current-guard tuple fields/order differ",
        )
    if (
        tuple(contract[field] for field in expected_fields[:4]) != _TARGET_GROUP_COUNTS
        or tuple(contract["ordered_authority_ids"]) != _V8_AUTHORITY_IDS
        or contract["legal_case_count"] != _CURRENT_GUARD_TARGET_COUNT
        or contract["illegal_case_count"] != _CURRENT_GUARD_TARGET_COUNT
        or contract["direct_sql_case_count"] != _CURRENT_GUARD_TARGET_COUNT
    ):
        _error(
            "TASK7_CURRENT_GUARD_CONTRACT_INVALID",
            "reviewed counts/authority IDs differ",
        )
    return contract


def _v7_compatible_projection(artifact: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the v7 validation view using only the v8 artifact bytes."""

    projected = copy.deepcopy(artifact)
    metadata = projected["metadata"]
    reviewed = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    reviewed.pop()
    reviewed[0] = "full-strict-v7-reviewed-design-literal-v1"
    reviewed[1] = ["pipeline_contract_version", "full-strict-v7"]
    reviewed_bytes = _canonical_bytes(reviewed)
    metadata["artifact_schema"] = "full-strict-v7-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v7"
    metadata["append_frozen"] = True
    metadata["reviewed_design_sha256"] = _sha256_bytes(reviewed_bytes)
    metadata["reviewed_design_utf8_hex"] = reviewed_bytes.hex()
    recipes = projected["digest_recipe_registry"]["rows"]
    recipes[5][1] = (
        '["pipeline-contract-v7","full-strict-v7",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    projected["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()
    rows = projected["sql_authority_registry"]["rows"][:17]
    projected["sql_authority_registry"]["rows"] = rows
    projected["sql_authority_registry"]["expected_sha256"] = _h([_REGISTRY_HASH_DOMAIN, rows])
    return projected


def _row_statements(row: dict[str, Any], ordinal: int) -> tuple[str, ...]:
    encoded = row.get("body_sql_utf8_hex_tuple")
    if not isinstance(encoded, list) or not encoded:
        _error(
            "TASK7_CURRENT_GUARD_SQL_INVALID",
            f"authority row {ordinal} has no SQL",
        )
    statements = tuple(
        _decode_hex(value, f"authority[{ordinal}].body[{index}]")
        for index, value in enumerate(encoded)
    )
    if any(
        statement != statement.strip()
        or "\r" in statement
        or "\n" in statement
        or "\x00" in statement
        or "--" in statement
        or "/*" in statement
        for statement in statements
    ):
        _error(
            "TASK7_CURRENT_GUARD_SQL_INVALID",
            f"authority row {ordinal} SQL is not canonical one-line SQL",
        )
    return statements


def _trigger_catalog(
    *,
    authority_id: str,
    statements: tuple[str, ...],
    targets: tuple[tuple[str, str], ...],
    retained: bool,
) -> tuple[dict[str, Any], ...]:
    function_matches = [
        match for statement in statements if (match := _FUNCTION_RE.fullmatch(statement))
    ]
    if len(function_matches) != 1:
        _error(
            "TASK7_CURRENT_GUARD_SQL_INVALID",
            f"{authority_id} must define exactly one trigger function",
        )
    function_name = function_matches[0].group("function").lower()
    replacement = function_matches[0].group("replace") is not None
    trigger_matches = [
        match for statement in statements if (match := _TRIGGER_RE.fullmatch(statement))
    ]
    revoke_matches = [
        match for statement in statements if (match := _REVOKE_RE.fullmatch(statement))
    ]
    matched_count = 1 + len(trigger_matches) + len(revoke_matches)
    if matched_count != len(statements):
        _error(
            "TASK7_CURRENT_GUARD_SQL_INVALID",
            f"{authority_id} contains an unclassified SQL statement",
        )
    if retained:
        if not replacement or trigger_matches or revoke_matches:
            _error(
                "TASK7_CURRENT_GUARD_SQL_INVALID",
                f"{authority_id} retained replacement shape differs",
            )
        return tuple(
            {
                "table_name": table,
                "trigger_name": trigger,
                "timing": "PRESERVE_PRE_0038",
                "event_tuple": ["INSERT", "UPDATE", "DELETE"],
                "deferrable": authority_id == "current-task5-coverage-replacement-v1",
                "function_name": function_name,
                "retained": True,
            }
            for table, trigger in targets
        )
    if replacement or len(trigger_matches) != len(targets) or len(revoke_matches) != 1:
        _error(
            "TASK7_CURRENT_GUARD_SQL_INVALID",
            f"{authority_id} function/trigger/revoke cardinality differs",
        )
    if revoke_matches[0].group("function").lower() != function_name:
        _error(
            "TASK7_CURRENT_GUARD_SQL_INVALID",
            f"{authority_id} revoke targets another function",
        )
    catalog: list[dict[str, Any]] = []
    for match in trigger_matches:
        table = match.group("table").lower()
        trigger = match.group("trigger").lower()
        if match.group("function").lower() != function_name:
            _error(
                "TASK7_CURRENT_GUARD_SQL_INVALID",
                f"{authority_id} trigger/function mismatch",
            )
        catalog.append(
            {
                "table_name": table,
                "trigger_name": trigger,
                "timing": match.group("timing").upper(),
                "event_tuple": [
                    value.upper() for value in re.split(r"\s+OR\s+", match.group("events"))
                ],
                "deferrable": match.group("deferrable") is not None,
                "function_name": function_name,
                "retained": False,
            }
        )
    if tuple((row["table_name"], row["trigger_name"]) for row in catalog) != targets:
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            f"{authority_id} trigger SQL differs from target tuple",
        )
    return tuple(catalog)


def derive_current_guard_closure(artifact: dict[str, Any]) -> dict[str, Any]:
    """Derive the exact 73-target v8 guard closure from literal artifact bytes."""

    contract = _reviewed_guard_contract(artifact)
    rows = artifact.get("sql_authority_registry", {}).get("rows")
    if not isinstance(rows, list) or len(rows) != 28:
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            "SQL authority registry must contain 17 v7 plus 11 v8 rows",
        )
    current_rows = rows[17:]
    if (
        tuple(row.get("authority_id") for row in current_rows) != _V8_AUTHORITY_IDS
        or tuple(row.get("authority_kind") for row in current_rows) != _V8_AUTHORITY_KINDS
    ):
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            "v8 authority ID/kind order differs",
        )
    reviewed_partition = contract["ordered_target_partition"]
    if len(reviewed_partition) != len(current_rows):
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            "reviewed target partition cardinality differs",
        )
    ordered_targets: list[tuple[str, str]] = []
    trigger_catalog: list[dict[str, Any]] = []
    tuple_fields = dict(artifact["tuple_schema_registry"]["rows"])["SqlAuthoritySpecV1"]
    for offset, row in enumerate(current_rows):
        ordinal = offset + 17
        if (
            not isinstance(row, dict)
            or tuple(row) != tuple(tuple_fields)
            or row.get("authority_ordinal") != ordinal
            or row.get("authority_version") != "1"
        ):
            _error(
                "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
                f"authority row {ordinal} shape/ordinal/version differs",
            )
        targets = tuple((str(target[0]), str(target[1])) for target in row["target_tuple"])
        if (
            not targets
            or len(targets) != len(set(targets))
            or [list(target) for target in targets] != reviewed_partition[offset]
        ):
            _error(
                "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
                f"authority row {ordinal} target tuple differs",
            )
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        _expect(
            _sha256_bytes(_ROW_HASH_DOMAIN + _canonical_bytes(payload)),
            row["expected_sha256"],
            f"SQL authority row {ordinal}",
        )
        statements = _row_statements(row, ordinal)
        codes = frozenset(
            match.group("code").upper()
            for statement in statements
            for match in _RAISE_CODE_RE.finditer(statement)
        )
        if codes & _BANNED_UNAPPROVED_CODES or not codes <= _LOCKED_OUTWARD_CODES:
            _error(
                "TASK7_CURRENT_GUARD_OUTWARD_CODE_INVALID",
                f"{row['authority_id']} exposes {sorted(codes)!r}",
            )
        trigger_catalog.extend(
            _trigger_catalog(
                authority_id=row["authority_id"],
                statements=statements,
                targets=targets,
                retained=offset >= 9,
            )
        )
        ordered_targets.extend(targets)
    ordered = tuple(ordered_targets)
    reviewed_flat = tuple(
        (str(target[0]), str(target[1])) for target in contract["reviewed_downgrade_target_tuple"]
    )
    if (
        ordered != reviewed_flat
        or len(ordered) != _CURRENT_GUARD_TARGET_COUNT
        or len(set(ordered)) != _CURRENT_GUARD_TARGET_COUNT
    ):
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            "ordered/reviewed/unique target closure differs",
        )
    split_counts = (
        sum(len(row["target_tuple"]) for row in current_rows[:3]),
        sum(len(row["target_tuple"]) for row in current_rows[3:5]),
        sum(len(row["target_tuple"]) for row in current_rows[5:9]),
        sum(len(row["target_tuple"]) for row in current_rows[9:]),
    )
    if split_counts != _TARGET_GROUP_COUNTS:
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            f"target partition {split_counts!r} differs",
        )
    current_relations = {str(row["table_name"]) for row in artifact["current_ddl_registry"]["rows"]}
    allowed_relations = current_relations | _PRE_0038_TARGET_RELATIONS
    target_relations = {table for table, _trigger in ordered}
    missing_relations = sorted(target_relations - allowed_relations)
    reviewed_relations = tuple(contract["current_0038_target_relation_tuple"])
    if (
        tuple(dict.fromkeys(table for table, _trigger in ordered)) != reviewed_relations
        or missing_relations
    ):
        _error(
            "TASK7_CURRENT_GUARD_TARGET_CLOSURE_MISMATCH",
            f"target relation closure differs: {missing_relations!r}",
        )
    registry_hash = _h([_REGISTRY_HASH_DOMAIN, rows])
    _expect(
        registry_hash,
        artifact["sql_authority_registry"]["expected_sha256"],
        "sql_authority_registry_hash",
    )
    return {
        "schema_version": "task7-current-guard-closure-v1",
        "ordered_target_tuple": [list(target) for target in ordered],
        "trigger_catalog": trigger_catalog,
        "target_count": len(ordered),
        "orphan_authority_target_tuple": [],
        "missing_ddl_or_downgrade_target_tuple": [],
        "authority_registry_sha256": registry_hash,
    }


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return {
        "semantic_step_count": len(artifact["semantic_step_registry"]["rows"]),
        "edge_rule_count": len(artifact["edge_rule_registry"]["rows"]),
        "usage_route_count": len(artifact["usage_route_registry"]["rows"]),
        "usage_extractor_count": len(artifact["usage_extractor_registry"]["rows"]),
        "rights_policy_count": len(artifact["rights_index_policy_registry"]["rows"]),
        "current_ddl_count": len(artifact["current_ddl_registry"]["rows"]),
        "erasure_baseline_count": len(artifact["erasure_baseline"]["rows"]),
        "erasure_materialized_count": len(artifact["erasure_materialized"]["rows"]),
    }


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
        metadata.get("artifact_schema") != "full-strict-v8-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v8"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v8 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V8_APPEND_FREEZE_REQUIRED",
            "fixture admission requires explicitly frozen v8 bytes",
        )
    reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
    _expect(
        _sha256_bytes(reviewed),
        metadata["reviewed_design_sha256"],
        "reviewed_design_sha256",
    )
    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v8","full-strict-v8",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ] or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v8 digest recipe differs")
    _V7._validate_shape(
        _canonical_bytes(_v7_compatible_projection(artifact)) + b"\n",
        _v7_compatible_projection(artifact),
    )
    derive_current_guard_closure(artifact)
    _V7._V6._V5._validate_parent_authority_closure(artifact)
    _V7._V6._V5._V4.validate_erasure_execution_registry(artifact)
    _V7._V6._V5._V4._validate_reviewed_row_bytes(artifact)
    _V7._V6._V5._V4._validate_scalar_tree(artifact)
    _V7._V6._validate_usage_projection(artifact)


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
    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v8-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = cast(
        dict[str, str],
        _V7._V6._V5._derive_components(artifact, reviewed_hash),
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v8",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v8-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v8",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V8_CONTRACT_HASH": pipeline_hash,
    }


def derive_authority_inventory(artifact: dict[str, Any]) -> dict[str, Any]:
    closure = derive_current_guard_closure(artifact)
    rows = artifact["sql_authority_registry"]["rows"][17:]
    statement_count = sum(len(row["body_sql_utf8_hex_tuple"]) for row in rows)
    return {
        "schema_version": "task7-current-guard-authority-inventory-v1",
        "authority_registry_sha256": closure["authority_registry_sha256"],
        "authority_row_count": len(rows),
        "current_guard_target_count": closure["target_count"],
        "body_statement_count": statement_count,
        "ordered_target_sha256": _h(
            [
                "task7-current-guard-ordered-targets-v1",
                closure["ordered_target_tuple"],
            ]
        ),
        "trigger_catalog_sha256": _h(
            [
                "task7-current-guard-trigger-catalog-v1",
                closure["trigger_catalog"],
            ]
        ),
    }


def _decode_json_no_duplicates(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate member {key!r}")
            value[key] = child
        return value

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise ValueError("receipt root is not an object")
    return value


def verify_postgres_census_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        receipt = _decode_json_no_duplicates(receipt_raw)
        if (
            tuple(receipt)
            != (
                "schema_version",
                "signer_id",
                "payload",
                "payload_sha256",
                "public_key_sha256",
                "signature_base64",
            )
            or receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA
        ):
            raise ValueError("receipt envelope shape differs")
        signer_id = receipt["signer_id"]
        payload = receipt["payload"]
        if not isinstance(signer_id, str) or not signer_id or not isinstance(payload, dict):
            raise ValueError("receipt signer/payload differs")
        payload_sha256 = _sha256_bytes(_canonical_bytes(payload))
        public_key_sha256 = _sha256_bytes(trusted_public_key_raw)
        if (
            receipt["payload_sha256"] != payload_sha256
            or receipt["public_key_sha256"] != public_key_sha256
        ):
            raise ValueError("receipt binding differs")
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("trust root is not Ed25519")
        signature = base64.b64decode(receipt["signature_base64"], validate=True)
        public_key.verify(
            signature,
            _canonical_bytes(
                [
                    _CENSUS_SIGNING_DOMAIN,
                    signer_id,
                    payload_sha256,
                    public_key_sha256,
                    payload,
                ]
            ),
        )
        artifact = _decode_artifact(artifact_raw)
        _validate_shape(artifact_raw, artifact, require_frozen=True)
        execution = payload.get("database_execution")
        expected_payload = {
            "schema_version": _CENSUS_PAYLOAD_SCHEMA,
            "result": "PASS",
            "artifact_schema": artifact["metadata"]["artifact_schema"],
            "pipeline_contract_version": "full-strict-v8",
            "artifact_sha256": _sha256_bytes(artifact_raw),
            "authority_inventory": derive_authority_inventory(artifact),
            "database_execution": execution,
        }
        if payload != expected_payload or not isinstance(execution, dict):
            raise ValueError("receipt payload is not artifact-exact")
        required_execution = {
            "database_product": "PostgreSQL",
            "current_guard_target_count": 73,
            "legal_execution_count": 73,
            "illegal_rejection_count": 73,
            "direct_sql_rejection_count": 73,
            "database_rejection_count": 0,
        }
        if any(execution.get(key) != value for key, value in required_execution.items()):
            raise ValueError("executable guard census is incomplete")
        allowed_execution_fields = (
            "database_product",
            "server_version_num",
            "migration_revision",
            "current_guard_target_count",
            "legal_execution_count",
            "illegal_rejection_count",
            "direct_sql_rejection_count",
            "database_rejection_count",
            "catalog_projection_sha256",
            "nfc_parity_sha256",
        )
        if (
            tuple(execution) != allowed_execution_fields
            or not isinstance(execution["server_version_num"], int)
            or execution["server_version_num"] < 160000
            or execution["migration_revision"] != "0038"
            or re.fullmatch(r"[0-9a-f]{64}", execution["catalog_projection_sha256"]) is None
            or re.fullmatch(r"[0-9a-f]{64}", execution["nfc_parity_sha256"]) is None
        ):
            raise ValueError("database execution shape differs")
    except (
        InvalidSignature,
        binascii.Error,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ContractArtifactError(
            "TASK7_V8_POSTGRES_CENSUS_RECEIPT_INVALID",
            "signed executable PostgreSQL census did not verify",
        ) from exc
    return payload


def _render_fixture(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_exclusive(path: Path, rendered: str) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V8_FIXTURE_OUTPUT_PARENT_MISSING", str(path.parent))
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ContractArtifactError(
            "TASK7_V8_FIXTURE_OUTPUT_ALREADY_EXISTS",
            str(path),
        ) from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--stdout", action="store_true")
    parser.add_argument("--authority-census-receipt", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        artifact_raw = args.artifact.read_bytes()
        artifact = _decode_artifact(artifact_raw)
        _validate_shape(artifact_raw, artifact, require_frozen=True)
        result = derive_contract_bytes(artifact_raw, validate=True)
        payload = verify_postgres_census_receipt(
            receipt_raw=args.authority_census_receipt.read_bytes(),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=args.trusted_public_key.read_bytes(),
        )
        result["postgres_authority_census"] = payload["database_execution"]
        rendered = _render_fixture(result)
        if args.stdout:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
        elif args.output is not None:
            _write_exclusive(args.output, rendered)
        else:
            _error("TASK7_CONTRACT_ARTIFACT_INVALID", "fixture output is absent")
        return 0
    except (
        ContractArtifactError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
