"""Materialize the bounded, unfrozen full-strict-v17 reviewed candidate.

V16 is immutable, append-frozen, inactive, and superseded. V17 changes only
the reviewed source projection/freeze authorities, the reviewed receipt
equality authority, their derived least-privilege ACL, the governed runtime
version literals, and dependent version/hash metadata. All semantic
registries remain byte-identical.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V16_PATH = _SCRIPT_DIR / "materialize_full_strict_v16_preimage.py"
_SOURCE_SQL_PATH = _SCRIPT_DIR / "task7_v17_source_projection_sql.py"
_RECEIPT_SQL_PATH = _SCRIPT_DIR / "task7_v17_receipt_guard_sql.py"
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V16_SOURCE_SHA512 = (
    "99d353b8c27e21a317a62354d780c531f9e1c204a3048a385e3299d1b107293b"
    "a0b6d2188da07db02732b2564660dd24f73959f12e69a71a40dd4dcf8e470fd4"
)
_V16_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v16-sql-authority-registry-v1"
_V17_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v17-sql-authority-registry-v1"
_VERSION_LITERAL_V16 = "full-strict-v16"
_VERSION_LITERAL_V17 = "full-strict-v17"
_VERSION_AUTHORITIES = ((26, "current-task5-coverage-replacement-v1", 0),)
_RECEIPT_AUTHORITY_ORDINAL = 19
_RECEIPT_AUTHORITY_ID = "current-receipt-equality-v1"
_RECEIPT_BODY_BYTES = 18_212
_RECEIPT_BODY_SHA256 = "eb1c87584e024e2a609bc518b134c067d76565c2ec714071737efe9c2a44721a"
_RECEIPT_ROW_SHA256 = "75479db1fe362d8c62f0aafbff1cfeba459a082304c03834dbbe74372a50a7d7"
_EXACT_ACL_COLUMN_DELTA = {
    "source_lineage_objects": frozenset(
        {
            "object_identity_sha256",
            "source_disposition",
            "source_version",
            "write_origin",
        }
    ),
    "data_room_packages": frozenset({"metadata"}),
    "data_room_package_files": frozenset(
        {
            "doc_id",
            "document_id",
            "error_codes",
            "extension",
            "file_entry_id",
            "file_status",
            "package_id",
            "parse_status",
            "path_hash",
            "reason_codes",
            "sequence",
            "sha256",
            "storage_uri",
            "support_status",
            "triage_status",
        }
    ),
    "document_artifacts": frozenset(
        {
            "doc_id",
            "doc_type",
            "ingested_at",
            "metadata",
            "sha256",
            "source_system",
            "title",
            "uri",
            "version_id",
        }
    ),
    "documents": frozenset({"doc_id", "doc_type", "document_id", "metadata", "parse_status"}),
    "document_parse_attempts": frozenset(
        {
            "attempt_ordinal",
            "document_id",
            "finished_at",
            "input_sha256",
            "parse_attempt_id",
            "parser_name",
            "parser_version",
            "receipt_sha256",
            "started_at",
            "status",
            "write_origin",
        }
    ),
    "document_spans": frozenset(
        {
            "content_hash",
            "document_id",
            "locator",
            "parse_attempt_id",
            "span_id",
            "span_type",
        }
    ),
    "runs": frozenset({"pipeline_resume_state"}),
    "run_step_outputs": frozenset(
        {
            "output_kind",
            "output_ordinal",
            "semantic_rule_hash",
            "semantic_rule_version",
            "step_name",
        }
    ),
    "run_consumer_receipts": frozenset({"consumer_output_object_id"}),
}
_RETAINED_MIGRATION_SHA512 = {
    "src/idis/persistence/migrations/versions/0031_strict_preflight_proofs.py": (
        "a2d2f3da6e35505d7b8681a0a722dcfc4b1d486f0325193541c4a571537fae3"
        "b7abd175aeed629b4bbb1de54d40a29b8e0933f3e01cf4bbe1f65411c80e17402"
    ),
    "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py": (
        "679e84f8a9d4378178a4f076bbabddef40a2bbcb7ddcd11a0e9e7b4073e8d1e04"
        "fa2b2cb665097248bf9cd0fbf68d13b326291cbe393d355abcfce3edf891904"
    ),
    "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py": (
        "de166216d565872b88c84d7d5abaff721b157e358e7c12110b0cb95d1468ec4b"
        "811371bd955ea584df754b4f3d4452b9fb2c6370f74cf2d83a9d0c2d61aab232"
    ),
}
_RETAINED_FUNCTION_AUTHORITY = (
    (
        "public.idis_prevent_strict_preflight_proof_mutation()",
        "idis_prevent_strict_preflight_proof_mutation",
        "src/idis/persistence/migrations/versions/0031_strict_preflight_proofs.py",
        4_261,
        "4d48af69c18fb948c5200b92998063be5a2b34305c4af16201e1d5d2c28f8484",
        4_147,
        "b2a1ab9b247bdf45a7028584fc8adb722f4672990698e7d9780700cf9704498e",
    ),
    (
        "public.idis_task5_selection_guard()",
        "idis_task5_selection_guard",
        "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
        1_982,
        "11d57d2867f6de27c2f74e6e947c08df1a71017cbac8262cce777ac4ebe1f76a",
        1_860,
        "6dbb745f2ce3e2d81e422e8575fe66d07452bbd4e44eb595c4d9f95bcb71e99f",
    ),
    (
        "public.idis_task5_coverage_scope_guard()",
        "idis_task5_coverage_scope_guard",
        "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
        2_716,
        "3d4767d2bf5678f8a9a003c3168d79e3050e16eaf0849bc49b8d79e6668b5fbf",
        2_589,
        "ba593849e358a5c772a6af3531d32b193fd01d314432e71b872522c3127cd3ab",
    ),
    (
        "public.idis_task6_erasure_requested(uuid,uuid)",
        "idis_task6_erasure_requested",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        422,
        "13210ca6e61419f0bdc953e44141b3a711270a773397b74698a51c087bdbd1da",
        243,
        "36ad20a33e39b322dd8a582653fa0e3174508a7bb9f3d98d3533aea6bdac3e7f",
    ),
    (
        "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
        "idis_task6_current_lease_is_valid",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        1_883,
        "e83e6e6d60c3ea5bcdf65b010dc26b91a0abfe48513a8fabf3176308022dfda2",
        1_677,
        "cde8f9e3b33a469ce43ec9b7751465d588267fbb1166ed4e8cd155557fb3b499",
    ),
    (
        "public.idis_task6_immutable_guard()",
        "idis_task6_immutable_guard",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        761,
        "136aa75e5bb1f8a26288f532ef81eab0e22d7143759ebb2b7c89dc754db942d1",
        639,
        "97db59522cf42dd91797850e5f9ae438d4d8aa9914d24058bbb4943e7fa3114a",
    ),
    (
        "public.idis_task6_attempt_guard()",
        "idis_task6_attempt_guard",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        5_193,
        "ac02bf2865a239e32d4ee553c13809970773f9a04edeb0d6852ff934e1303b23",
        5_073,
        "545883a6a413f03bc12ead2f8680ec7a8c00518b01a4da30be6317b39aec3ffc",
    ),
    (
        "public.idis_task6_validate_result_outputs()",
        "idis_task6_validate_result_outputs",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        1_056,
        "3c5210854a409d1629925ab636a852dd0961478f66b7f9f6cd3e9d7c3d3bf5e9",
        926,
        "18cc3215c17de18e530bcfba0a3551693c24815f805e5b86848beb54c294d8ad",
    ),
)
_SOURCE_AUTHORITY_IDS = {
    17: "current-source-exact-one-v1",
    22: "current-source-bound-v1",
}
_ALLOWED_CHANGED_AUTHORITY_ORDINALS = {17, 19, 22, 26}
_SEMANTIC_INVARIANT_SECTIONS = (
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
    "erasure_execution_registry",
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


class MaterializationError(RuntimeError):
    """Stable fail-closed v17 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _error("TASK7_V17_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_V16 = _load_script(_V16_PATH, "_task7_v16_materializer_for_v17")
_SOURCE_SQL = _load_script(_SOURCE_SQL_PATH, "_task7_v17_source_projection_sql")
_RECEIPT_SQL = _load_script(_RECEIPT_SQL_PATH, "_task7_v17_receipt_guard_sql")
_H = _V16._V15._V14._V13._V12


def _canonical_bytes(value: object) -> bytes:
    return bytes(_V16._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_V16._render(artifact))


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_V16._changed_paths(old, new, path))


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V17_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if (
        not isinstance(row, dict)
        or row.get("authority_ordinal") != authority_ordinal
        or row.get("authority_id") != authority_id
    ):
        _error("TASK7_V17_AUTHORITY_SHAPE_INVALID", authority_id)
    return cast(dict[str, Any], row)


def _validated_statements(value: object, *, authority_id: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        _error("TASK7_V17_SOURCE_SQL_INVALID", authority_id)
    statements = list(value)
    if any(not isinstance(statement, str) or not statement for statement in statements):
        _error("TASK7_V17_SOURCE_SQL_INVALID", authority_id)
    return statements


def _transform_source_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    if reverse:
        base = _load_pinned_v16()
        for ordinal, authority_id in _SOURCE_AUTHORITY_IDS.items():
            artifact["sql_authority_registry"]["rows"][ordinal] = copy.deepcopy(
                _authority_row(
                    base,
                    authority_ordinal=ordinal,
                    authority_id=authority_id,
                )
            )
        return

    exact_one = _authority_row(
        artifact,
        authority_ordinal=17,
        authority_id=_SOURCE_AUTHORITY_IDS[17],
    )
    source_bound = _authority_row(
        artifact,
        authority_ordinal=22,
        authority_id=_SOURCE_AUTHORITY_IDS[22],
    )
    exact_one_statements = _validated_statements(
        _SOURCE_SQL.build_source_exact_one_statements(copy.deepcopy(exact_one)),
        authority_id=_SOURCE_AUTHORITY_IDS[17],
    )
    source_bound_statements = _validated_statements(
        _SOURCE_SQL.build_source_bound_statements(copy.deepcopy(source_bound)),
        authority_id=_SOURCE_AUTHORITY_IDS[22],
    )
    exact_one["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(exact_one_statements))
    source_bound["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(source_bound_statements))
    source_bound["declared_parent_relation_tuple"] = ["data_room_packages"]
    _H._rehash_authority_row(exact_one)
    _H._rehash_authority_row(source_bound)


def _transform_version_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    if reverse:
        pinned_v16 = _load_pinned_v16()
        artifact["sql_authority_registry"]["rows"][_RECEIPT_AUTHORITY_ORDINAL] = copy.deepcopy(
            _authority_row(
                pinned_v16,
                authority_ordinal=_RECEIPT_AUTHORITY_ORDINAL,
                authority_id=_RECEIPT_AUTHORITY_ID,
            )
        )
    else:
        receipt = _authority_row(
            artifact,
            authority_ordinal=_RECEIPT_AUTHORITY_ORDINAL,
            authority_id=_RECEIPT_AUTHORITY_ID,
        )
        receipt_statements = _validated_statements(
            _RECEIPT_SQL.build_receipt_equality_statements(
                copy.deepcopy(receipt),
                artifact["semantic_step_registry"]["rows"],
            ),
            authority_id=_RECEIPT_AUTHORITY_ID,
        )
        receipt["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(receipt_statements))
        receipt["declared_parent_relation_tuple"] = [
            *receipt["declared_parent_relation_tuple"],
            "deliverables",
        ]
        _H._rehash_authority_row(receipt)

    old = _VERSION_LITERAL_V17 if reverse else _VERSION_LITERAL_V16
    new = _VERSION_LITERAL_V16 if reverse else _VERSION_LITERAL_V17
    for authority_ordinal, authority_id, statement_ordinal in _VERSION_AUTHORITIES:
        row = _authority_row(
            artifact,
            authority_ordinal=authority_ordinal,
            authority_id=authority_id,
        )
        statements = list(_H._decode_statements(row))
        if statement_ordinal >= len(statements):
            _error("TASK7_V17_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        statement = statements[statement_ordinal]
        if statement.count(old) != 1 or statement.count(new) != 0:
            _error("TASK7_V17_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = statement.replace(old, new, 1)
        row["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(statements))
        _H._rehash_authority_row(row)


def _rehash_sql_registry(
    artifact: dict[str, Any],
    *,
    hash_domain: str,
) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V17_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            hash_domain,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _validate_receipt_authority_delta(
    v16: dict[str, Any],
    v17: dict[str, Any],
) -> None:
    """Reject any row-19 or ACL expansion beyond the reviewed normalized bytes."""

    base = copy.deepcopy(
        _authority_row(
            v16,
            authority_ordinal=_RECEIPT_AUTHORITY_ORDINAL,
            authority_id=_RECEIPT_AUTHORITY_ID,
        )
    )
    statements = _validated_statements(
        _RECEIPT_SQL.build_receipt_equality_statements(
            copy.deepcopy(base),
            v16["semantic_step_registry"]["rows"],
        ),
        authority_id=_RECEIPT_AUTHORITY_ID,
    )
    base["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(statements))
    base["declared_parent_relation_tuple"] = [
        *base["declared_parent_relation_tuple"],
        "deliverables",
    ]
    _H._rehash_authority_row(base)
    observed = _authority_row(
        v17,
        authority_ordinal=_RECEIPT_AUTHORITY_ORDINAL,
        authority_id=_RECEIPT_AUTHORITY_ID,
    )
    body = bytes.fromhex(observed["body_sql_utf8_hex_tuple"][2])
    if (
        observed != base
        or len(body) != _RECEIPT_BODY_BYTES
        or _sha256(body) != _RECEIPT_BODY_SHA256
        or observed["expected_sha256"] != _RECEIPT_ROW_SHA256
    ):
        _error("TASK7_V17_RECEIPT_AUTHORITY_DELTA_INVALID")

    def relation_columns(artifact: dict[str, Any]) -> dict[str, frozenset[str]]:
        rows = artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "guard_owner_role"
        ]["select_relation_column_tuple"]
        return {str(relation): frozenset(map(str, columns)) for relation, columns in rows}

    old_columns = relation_columns(v16)
    new_columns = relation_columns(v17)
    delta = {
        relation: new_columns[relation] - old_columns[relation]
        for relation in new_columns
        if new_columns[relation] != old_columns[relation]
    }
    if delta != _EXACT_ACL_COLUMN_DELTA:
        _error("TASK7_V17_RECEIPT_ACL_DELTA_INVALID")


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V17_REVIEWED_DESIGN_BASE_INVALID") from exc
    old = _VERSION_LITERAL_V17 if reverse else _VERSION_LITERAL_V16
    new = _VERSION_LITERAL_V16 if reverse else _VERSION_LITERAL_V17
    expected = [
        f"{old}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old],
    ]
    if not isinstance(design, list) or design[:2] != expected:
        _error("TASK7_V17_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = f"{new}-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", new]
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{new}-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = new
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(artifact: dict[str, Any], *, reverse: bool) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "17" if reverse else "16"
    new_ordinal = "16" if reverse else "17"
    old_version = _VERSION_LITERAL_V17 if reverse else _VERSION_LITERAL_V16
    new_version = _VERSION_LITERAL_V16 if reverse else _VERSION_LITERAL_V17
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V17_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _load_pinned_v16() -> dict[str, Any]:
    raw = _V16_ARTIFACT.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V16_SOURCE_SHA512:
        _error("TASK7_V17_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V17_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw)
    if not isinstance(value, dict):
        _error("TASK7_V17_BASE_ARTIFACT_INVALID")
    return value


def _direct_read_columns(
    signature: str,
    body: str,
    relations: list[str],
) -> list[list[Any]]:
    """Bind the two changed source functions to their exact reviewed closure."""

    projection_signature = "public.idis_task7_source_projection_v1(uuid)"
    exact_one_signature = "public.idis_task7_current_source_exact_one_v1()"
    if signature not in {projection_signature, exact_one_signature}:
        return list(_H._direct_read_columns(signature, body, relations))

    projection_closure = {
        str(relation): tuple(map(str, columns))
        for relation, columns in _SOURCE_SQL.projection_read_column_closure().items()
    }
    if signature == projection_signature:
        closure = projection_closure
    else:
        closure = {
            str(relation): tuple(map(str, columns))
            for relation, columns in _H._SOURCE_EXACT_COLUMNS.items()
        }
        closure["source_lineage_objects"] = projection_closure["source_lineage_objects"]
    if set(closure) != set(relations):
        _error(
            "TASK7_V17_DIRECT_READ_RELATION_CLOSURE_INVALID",
            f"{signature}:{sorted(set(closure) ^ set(relations))!r}",
        )
    for relation, columns in closure.items():
        if not columns or len(columns) != len(set(columns)):
            _error(
                "TASK7_V17_DIRECT_READ_COLUMN_CLOSURE_INVALID",
                f"{signature}:{relation}",
            )
        if any(column not in body for column in columns):
            _error(
                "TASK7_V17_DIRECT_READ_COLUMN_NOT_IN_BODY",
                f"{signature}:{relation}",
            )
    body_sha256 = _sha256(body.encode("utf-8"))
    return [
        [
            relation,
            sorted(closure[relation]),
            ["SQL_BODY_SHA256", body_sha256],
        ]
        for relation in relations
    ]


def _build_function_rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive every ACL row while enforcing the v17 73-target/193-event census."""

    provisional: list[dict[str, Any]] = []
    sql_rows = artifact["sql_authority_registry"]["rows"]
    authority_ids = {str(row["authority_id"]) for row in sql_rows}
    legacy_targets = _H._legacy_trigger_targets(artifact)
    legacy_coordinates = _H._legacy_trigger_event_coordinates(artifact)
    for row_ordinal, authority in enumerate(sql_rows):
        authority_id = str(authority["authority_id"])
        parents = [str(value) for value in authority["declared_parent_relation_tuple"]]
        statements = _H._decode_statements(authority)
        targets = _H._trigger_targets(statements)
        coordinates = _H._trigger_event_coordinates(statements)
        for statement_ordinal, statement in enumerate(statements):
            for occurrence_ordinal, match in enumerate(_H._FUNCTION_RE.finditer(statement)):
                signature = _H._signature(match)
                name = signature.partition("(")[0]
                returns_trigger = match.group("returns").strip().lower() == "trigger"
                attributes = match.group("attributes").upper()
                security_mode = "DEFINER" if "SECURITY DEFINER" in attributes else "INVOKER"
                if returns_trigger and security_mode != "DEFINER":
                    _error("TASK7_V17_TRIGGER_NOT_SECURITY_DEFINER", signature)
                body = match.group("body")
                select_relations = _H._direct_relations(body, parents)
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
                        "owner_role": _H._GUARD_OWNER,
                        "install_scope": (
                            "CURRENT_0038"
                            if row_ordinal >= _H._CURRENT_AUTHORITY_START
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
                                for called in _H._FUNCTION_CALL_RE.finditer(body)
                                if called.group("function").lower() != name
                            }
                        ),
                        "explicit_execute_grantee_tuple": [],
                        "public_execute": False,
                        "application_role": _H._APPLICATION_ROLE,
                        "application_role_binding": "RUNTIME_SESSION_USER",
                        "application_role_execute": False,
                        "acl_correction_id": _H._HELPER_CORRECTIONS.get(signature),
                    }
                )
    for external in _H._EXTERNAL_HELPERS:
        if external["authority_id"] not in authority_ids:
            _error(
                "TASK7_V17_EXTERNAL_HELPER_AUTHORITY_MISSING",
                str(external["authority_id"]),
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
                    if external["owner_role"] == _H._GUARD_OWNER
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
                        sorted(_H._EXTERNAL_READ_COLUMNS[external["function_signature"]][relation]),
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
                "application_role": _H._APPLICATION_ROLE,
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
        _error("TASK7_V17_FUNCTION_CENSUS_DUPLICATE")
    for ordinal, row in enumerate(provisional):
        callees: list[str] = []
        for name in row.pop("_callee_names"):
            callee_signature = by_name.get(name)
            if callee_signature is None:
                _error("TASK7_V17_HELPER_CALL_ORPHAN", name)
            callees.append(callee_signature)
        row["function_ordinal"] = ordinal
        row["helper_call_signature_tuple"] = callees
        owner_sql, acl_sql = _H._function_sql(
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
        _H._rehash_acl_row(row)
    for row in provisional:
        target_pairs = {
            (str(relation), str(trigger)) for relation, trigger in row["target_trigger_tuple"]
        }
        coordinate_targets = {
            (str(value[0]), str(value[1])) for value in row["target_event_coordinate_tuple"]
        }
        if target_pairs != coordinate_targets:
            _error(
                "TASK7_V17_TRIGGER_COORDINATE_CLOSURE_INVALID",
                str(row["function_signature"]),
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
        "CURRENT_0038": (73, 193),
        "FUTURE_INACTIVE": (81, 242),
    }:
        _error("TASK7_V17_TRIGGER_COORDINATE_CENSUS_INVALID", repr(trigger_census))
    return provisional


def _render_task6_attempt_guard(path: Path) -> str:
    """Render the one pinned migration f-string without importing runtime code."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise MaterializationError(
            f"TASK7_V17_RETAINED_MIGRATION_SOURCE_INVALID:{path.relative_to(_REPO_ROOT).as_posix()}"
        ) from exc
    matches: list[ast.JoinedStr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.JoinedStr)
        ):
            constants = "".join(
                str(part.value) for part in node.args[0].values if isinstance(part, ast.Constant)
            )
            if "CREATE OR REPLACE FUNCTION idis_task6_attempt_guard" in constants:
                matches.append(node.args[0])
    if len(matches) != 1:
        _error("TASK7_V17_RETAINED_ATTEMPT_SOURCE_CENSUS_INVALID")
    rendered: list[str] = []
    for part in matches[0].values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            rendered.append(part.value)
            continue
        if (
            isinstance(part, ast.FormattedValue)
            and part.conversion == -1
            and part.format_spec is None
        ):
            value = part.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "_STEP7_NAME"
                and value.attr == "value"
            ):
                rendered.append("METHODOLOGY_EXTRACTION_TASK_EXECUTION")
                continue
            if isinstance(value, ast.Name) and value.id == "_STEP7_ORDER":
                rendered.append("6")
                continue
        _error("TASK7_V17_RETAINED_ATTEMPT_INTERPOLATION_INVALID")
    return "".join(rendered)


def _retained_source_function_sql(path: Path, function_name: str) -> str:
    if function_name == "idis_task6_attempt_guard":
        return _render_task6_attempt_guard(path)
    statements = _H._literal_migration_sql(path)
    statement = _H._migration_function_sql(statements, function_name)
    if not isinstance(statement, str):
        _error("TASK7_V17_RETAINED_FUNCTION_BODY_INVALID", function_name)
    if function_name != "idis_prevent_strict_preflight_proof_mutation":
        return statement
    marker = statement.find("AS $$")
    if marker < 0:
        _error("TASK7_V17_RETAINED_STRICT_PROOF_BODY_INVALID")
    closing = statement.find("$$", marker + len("AS $$"))
    if closing < 0:
        _error("TASK7_V17_RETAINED_STRICT_PROOF_BODY_INVALID")
    return statement[: closing + 2]


def _expanded_retained_catalog_projection() -> list[list[Any]]:
    source_hashes: dict[str, str] = {}
    projection: list[list[Any]] = []
    for (
        signature,
        function_name,
        relative_path,
        sql_length,
        sql_sha256,
        prosrc_length,
        prosrc_sha256,
    ) in _RETAINED_FUNCTION_AUTHORITY:
        path = _REPO_ROOT / relative_path
        if relative_path not in source_hashes:
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise MaterializationError(
                    f"TASK7_V17_RETAINED_MIGRATION_SOURCE_MISSING:{relative_path}"
                ) from exc
            source_hashes[relative_path] = hashlib.sha512(raw).hexdigest()
        expected_source_hash = _RETAINED_MIGRATION_SHA512[relative_path]
        if source_hashes[relative_path] != expected_source_hash:
            _error("TASK7_V17_RETAINED_MIGRATION_SOURCE_HASH_MISMATCH", relative_path)
        function_sql = _retained_source_function_sql(path, function_name)
        prosrc = _H._function_prosrc(function_sql)
        if (
            len(function_sql) != sql_length
            or _sha256(function_sql.encode("utf-8")) != sql_sha256
            or len(prosrc) != prosrc_length
            or _sha256(prosrc.encode("utf-8")) != prosrc_sha256
        ):
            _error("TASK7_V17_RETAINED_FUNCTION_BYTES_INVALID", signature)
        projection.append(
            [
                signature,
                function_sql.encode("utf-8").hex(),
                prosrc.encode("utf-8").hex(),
                relative_path,
                expected_source_hash,
                "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER",
                False,
                None,
                None,
            ]
        )
    return projection


def _expand_downgrade_authority(
    authority: dict[str, Any],
    retained_projection: list[list[Any]],
    retained_catalog_assertion: str,
) -> dict[str, Any]:
    expanded = copy.deepcopy(authority)
    old_restores = list(expanded["retained_function_restore_sql_utf8_hex_tuple"])
    trigger_restores = list(expanded["retained_trigger_restore_sql_utf8_hex_tuple"])
    if len(old_restores) != 3 or len(trigger_restores) != 9:
        _error("TASK7_V17_DOWNGRADE_BASE_RESTORATION_CENSUS_INVALID")
    new_restores = [str(row[1]) for row in retained_projection]
    assertion_hex = retained_catalog_assertion.encode("utf-8").hex()
    ordered = list(expanded["ordered_sql_utf8_hex_tuple"])
    zero_hex = str(expanded["zero_privilege_assertion_sql_utf8_hex"])
    try:
        restore_offset = ordered.index(zero_hex) + 1
    except ValueError as exc:
        raise MaterializationError("TASK7_V17_DOWNGRADE_ZERO_ASSERTION_MISSING") from exc
    if (
        ordered[restore_offset : restore_offset + len(old_restores)] != old_restores
        or ordered[
            restore_offset + len(old_restores) : restore_offset
            + len(old_restores)
            + len(trigger_restores)
        ]
        != trigger_restores
        or ordered[-1] != expanded["retained_catalog_postrestore_assertion_sql_utf8_hex"]
    ):
        _error("TASK7_V17_DOWNGRADE_BASE_ORDER_INVALID")
    expanded["retained_function_signature_tuple"] = [str(row[0]) for row in retained_projection]
    expanded["retained_function_restore_sql_utf8_hex_tuple"] = new_restores
    expanded["retained_catalog_postrestore_assertion_sql_utf8_hex"] = assertion_hex
    expanded["ordered_sql_utf8_hex_tuple"] = [
        *ordered[:restore_offset],
        *new_restores,
        *trigger_restores,
        assertion_hex,
    ]
    if len(expanded["ordered_sql_utf8_hex_tuple"]) != len(ordered) + 5:
        _error("TASK7_V17_DOWNGRADE_ORDERED_CENSUS_INVALID")
    expanded["expected_sha256"] = _h(
        [
            "task7-guard-function-downgrade-authority-v1",
            {key: value for key, value in expanded.items() if key != "expected_sha256"},
        ]
    )
    return expanded


def _build_acl_registry(artifact: dict[str, Any]) -> dict[str, Any]:
    """Derive least privilege from v17 bodies without predecessor count literals."""

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
    old_role = _load_pinned_v16()["sql_authority_registry"]["guard_function_acl_registry"][
        "guard_owner_role"
    ]
    old_columns = {
        str(relation): set(map(str, columns))
        for relation, columns in old_role["select_relation_column_tuple"]
    }
    new_columns = {
        str(relation): set(map(str, columns)) for relation, columns in select_relation_columns
    }
    if set(new_columns) != set(old_columns):
        _error("TASK7_V17_ACL_RELATION_DRIFT")
    if any(
        not columns.issubset(new_columns[relation]) for relation, columns in old_columns.items()
    ):
        _error("TASK7_V17_ACL_PRIVILEGE_REGRESSION")
    reviewed_expansion_relations = {
        "source_lineage_objects",
        "data_room_packages",
        "data_room_package_files",
        "document_artifacts",
        "documents",
        "document_parse_attempts",
        "document_spans",
        "run_consumer_receipts",
        "run_step_outputs",
        "runs",
    }
    expanded_relations = {
        relation for relation in old_columns if new_columns[relation] != old_columns[relation]
    }
    if not expanded_relations or not expanded_relations.issubset(reviewed_expansion_relations):
        _error("TASK7_V17_ACL_DERIVED_DELTA_INVALID", repr(sorted(expanded_relations)))

    retained_projection = _expanded_retained_catalog_projection()
    retained_trigger_projection = _H._retained_0037_trigger_catalog_projection(artifact)
    retained_catalog_preflight = _H._retained_0037_catalog_assertion_sql(retained_projection)
    role = {
        "role_name": _H._GUARD_OWNER,
        "provisioning_mode": "OPERATOR_BOOTSTRAP_REQUIRED",
        "posture": copy.deepcopy(_H._ROLE_POSTURE),
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
        "rls_contract_tuple": copy.deepcopy(_H._RLS_CONTRACT),
        "clean_role_preflight_sql_utf8_hex": _H._clean_role_preflight_sql().encode("utf-8").hex(),
        "posture_assertion_sql_utf8_hex": _H._posture_assertion_sql().encode("utf-8").hex(),
    }
    role_acl_sql = [
        f"REVOKE ALL ON SCHEMA public FROM {_H._GUARD_OWNER}",
        f"GRANT USAGE ON SCHEMA public TO {_H._GUARD_OWNER}",
    ]
    table_revokes, column_revokes, column_grants = _H._column_acl_sql(select_relation_columns)
    for table_revoke, column_revoke, column_grant in zip(
        table_revokes,
        column_revokes,
        column_grants,
        strict=True,
    ):
        role_acl_sql.extend((table_revoke, column_revoke, column_grant))
    role["acl_sql_utf8_hex_tuple"] = [statement.encode("utf-8").hex() for statement in role_acl_sql]
    _H._rehash_role(role)

    installation = [
        _H._clean_role_preflight_sql(),
        _H._posture_assertion_sql(),
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
    downgrade_base = copy.deepcopy(artifact)
    pinned_v16_acl = _load_pinned_v16()["sql_authority_registry"]["guard_function_acl_registry"]
    downgrade_base_acl = downgrade_base["sql_authority_registry"]["guard_function_acl_registry"]
    downgrade_base_acl["retained_0037_catalog_projection_tuple"] = copy.deepcopy(
        pinned_v16_acl["retained_0037_catalog_projection_tuple"]
    )
    downgrade_base_acl["retained_0037_trigger_catalog_projection_tuple"] = copy.deepcopy(
        pinned_v16_acl["retained_0037_trigger_catalog_projection_tuple"]
    )
    downgrade = _H._build_downgrade_authority(
        downgrade_base,
        rows,
        table_revokes,
        column_revokes,
    )
    downgrade = _expand_downgrade_authority(
        downgrade,
        retained_projection,
        retained_catalog_preflight,
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
        "retained_0037_catalog_preflight_sql_utf8_hex": (
            retained_catalog_preflight.encode("utf-8").hex()
        ),
        "installation_sql_utf8_hex_tuple": [
            statement.encode("utf-8").hex() for statement in installation
        ],
        "downgrade_authority": downgrade,
    }
    registry["expected_sha256"] = _h(
        [
            _H._ACL_REGISTRY_HASH_DOMAIN,
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


def project_v17_to_v16(v17: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact immutable append-frozen v16 bytes from v17."""

    projected = copy.deepcopy(v17)
    _transform_source_authorities(projected, reverse=True)
    _transform_version_authorities(projected, reverse=True)
    pinned_v16 = _load_pinned_v16()
    projected["sql_authority_registry"]["guard_function_acl_registry"] = copy.deepcopy(
        pinned_v16["sql_authority_registry"]["guard_function_acl_registry"]
    )
    _rehash_sql_registry(projected, hash_domain=_V16_SQL_REGISTRY_HASH_DOMAIN)
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    projected["metadata"]["append_frozen"] = True
    if _render(projected) != _V16_ARTIFACT.read_bytes():
        _error("TASK7_V17_EXACT_V16_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v16: dict[str, Any], v17: dict[str, Any]) -> None:
    if tuple(v17) != tuple(v16):
        _error("TASK7_V17_SECTION_ORDER_DRIFT")
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        if v17[section] != v16[section]:
            _error("TASK7_V17_INVARIANT_DRIFT", section)
    changed = _changed_paths(v16, v17)
    if {path[0] for path in changed} != {
        "metadata",
        "digest_recipe_registry",
        "sql_authority_registry",
    }:
        _error("TASK7_V17_TOP_LEVEL_DIFF_INVALID")
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != _ALLOWED_CHANGED_AUTHORITY_ORDINALS:
        _error("TASK7_V17_SQL_AUTHORITY_DIFF_INVALID", repr(sorted(changed_rows)))
    if v17["metadata"]["append_frozen"] is not False:
        _error("TASK7_V17_UNFROZEN_CANDIDATE_REQUIRED")
    if v17["sql_authority_registry"]["guard_function_acl_registry"] != (_build_acl_registry(v17)):
        _error("TASK7_V17_ACL_NOT_DERIVED")
    _validate_receipt_authority_delta(v16, v17)
    if project_v17_to_v16(v17) != v16:
        _error("TASK7_V17_V16_PROJECTION_DRIFT")


def build_v17(v16: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v17 candidate."""

    if hashlib.sha512(_render(v16)).hexdigest() != _V16_SOURCE_SHA512:
        _error("TASK7_V17_BASE_ARTIFACT_HASH_MISMATCH")
    artifact = copy.deepcopy(v16)
    artifact["metadata"]["append_frozen"] = False
    _transform_source_authorities(artifact, reverse=False)
    _transform_version_authorities(artifact, reverse=False)
    artifact["sql_authority_registry"]["guard_function_acl_registry"] = _build_acl_registry(
        artifact
    )
    _rehash_sql_registry(artifact, hash_domain=_V17_SQL_REGISTRY_HASH_DOMAIN)
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v16, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V17_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V17_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V16_ARTIFACT)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.base_artifact.read_bytes()
        if hashlib.sha512(raw).hexdigest() != _V16_SOURCE_SHA512:
            _error("TASK7_V17_BASE_ARTIFACT_HASH_MISMATCH")
        v16 = json.loads(raw)
        if not isinstance(v16, dict):
            _error("TASK7_V17_BASE_ARTIFACT_INVALID")
        rendered = _render(build_v17(v16))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V17_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
