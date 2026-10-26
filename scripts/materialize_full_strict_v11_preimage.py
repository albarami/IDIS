"""Materialize the bounded, unfrozen full-strict-v11 reviewed candidate.

V10 never reached append-freeze and remains an authenticated historical input
only.  V11 corrects four heterogeneous-target trigger bodies, replaces the
Task-6 lease helper with its locked-search-path equivalent, binds the exact
0037 restoration body, advances version literals, and re-derives only the
dependent hashes approved for that change.

This command uses exclusive-create output semantics and always emits
``append_frozen=false``.  It cannot publish a fixture or freeze the candidate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_V10_SOURCE_SHA512 = (
    "d466d70824af9befabb46ecc78f1bce0c91120c47de7b5dfef8f801fb84a49f61"
    "93e7b5632370db835222a572d597b33bf3606f1dc08cc72287adfa68d414edf"
)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_ERASURE_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_ERASURE_REGISTRY_HASH_DOMAIN = "full-strict-v4-erasure-execution-registry-v1"

_RECEIPT_OLD_SCOPE = (
    "IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; "
    "scope_deal:=OLD.deal_id; scope_run:=OLD.run_id; ELSE "
    "scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; "
    "scope_run:=NEW.run_id; END IF;"
)
_RECEIPT_NEW_SCOPE = (
    "IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; "
    "scope_run:=OLD.run_id; ELSE scope_tenant:=NEW.tenant_id; "
    "scope_run:=NEW.run_id; END IF; "
    "SELECT r.deal_id INTO scope_deal FROM public.runs r "
    "WHERE r.tenant_id=scope_tenant AND r.run_id=scope_run; "
    "IF scope_deal IS NULL THEN RAISE EXCEPTION "
    "'CONSUMER_RECEIPT_INCOMPLETE' USING ERRCODE='23514'; END IF;"
)

_APPEND_OLD_ADMISSION = (
    "IF TG_TABLE_NAME='run_expected_consumer_edges' AND "
    "NEW.materialization_origin='ADMISSION' AND NEW.contract_rule_id IN "
    "('E001_SOURCE_INVENTORY_ADMISSION','E002_SOURCE_HANDOFF_ADMISSION') "
    "AND NEW.usage_kind IN ('SOURCE_INVENTORY','SOURCE_HANDOFF') AND "
    "NEW.producer_input_binding_id IS NOT NULL AND "
    "NEW.producer_output_object_id IS NULL AND NEW.lease_owner_id IS NULL "
    "AND NEW.lease_fencing_token=0 AND EXISTS (SELECT 1 FROM public.runs r "
    "WHERE r.tenant_id=scope_tenant AND r.deal_id=scope_deal AND "
    "r.run_id=scope_run AND r.status='QUEUED') THEN RETURN NEW; END IF;"
)
_APPEND_HELPER = (
    "CREATE FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1("
    "p_row jsonb,p_tenant uuid,p_deal uuid,p_run uuid) RETURNS boolean "
    "LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$SELECT "
    "p_row->>'materialization_origin'='ADMISSION' AND "
    "p_row->>'contract_rule_id' IN "
    "('E001_SOURCE_INVENTORY_ADMISSION','E002_SOURCE_HANDOFF_ADMISSION') "
    "AND p_row->>'usage_kind' IN ('SOURCE_INVENTORY','SOURCE_HANDOFF') "
    "AND p_row->>'producer_input_binding_id' IS NOT NULL AND "
    "p_row->>'producer_output_object_id' IS NULL AND "
    "p_row->>'lease_owner_id' IS NULL AND "
    "p_row->>'lease_fencing_token'='0' AND EXISTS (SELECT 1 FROM "
    "public.runs r WHERE r.tenant_id=p_tenant AND r.deal_id=p_deal AND "
    "r.run_id=p_run AND r.status='QUEUED')$$; REVOKE ALL ON FUNCTION "
    "public.idis_task7_expected_edge_admission_is_valid_v1("
    "jsonb,uuid,uuid,uuid) FROM PUBLIC; "
)
_APPEND_NEW_ADMISSION = (
    "IF TG_TABLE_NAME='run_expected_consumer_edges' AND "
    "public.idis_task7_expected_edge_admission_is_valid_v1("
    "pg_catalog.to_jsonb(NEW),scope_tenant,scope_deal,scope_run) "
    "THEN RETURN NEW; END IF;"
)

_SOURCE_OLD_PACKAGE = (
    "(OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' "
    "AND TG_TABLE_NAME='data_room_packages' AND NOT (OLD.status='OPEN' AND "
    "NEW.status='SEALED' AND (pg_catalog.to_jsonb(NEW)-"
    "ARRAY['status','source_binding_state','source_lineage_object_id',"
    "'updated_at']::text[]) IS NOT DISTINCT FROM (pg_catalog.to_jsonb(OLD)-"
    "ARRAY['status','source_binding_state','source_lineage_object_id',"
    "'updated_at']::text[])))"
)
_SOURCE_HELPER = (
    "CREATE FUNCTION public.idis_task7_package_source_transition_is_valid_v1("
    "p_old jsonb,p_new jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE "
    "SET search_path=pg_catalog,pg_temp AS $$SELECT "
    "p_old->>'status'='OPEN' AND p_new->>'status'='SEALED' AND "
    "(p_new-ARRAY['status','source_binding_state','source_lineage_object_id',"
    "'updated_at']::text[]) IS NOT DISTINCT FROM "
    "(p_old-ARRAY['status','source_binding_state','source_lineage_object_id',"
    "'updated_at']::text[])$$; REVOKE ALL ON FUNCTION "
    "public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb) "
    "FROM PUBLIC; "
)
_SOURCE_NEW_PACKAGE = (
    "(OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' "
    "AND TG_TABLE_NAME='data_room_packages' AND NOT "
    "public.idis_task7_package_source_transition_is_valid_v1("
    "pg_catalog.to_jsonb(OLD),pg_catalog.to_jsonb(NEW)))"
)

_TASK6_OLD_OUTPUT_BRANCH = (
    "IF TG_OP='UPDATE' AND TG_TABLE_NAME IN "
    "('methodology_extraction_results','methodology_result_factual_outputs') "
    "AND OLD.run_output_object_id IS NULL AND NEW.run_output_object_id IS NOT "
    "NULL AND (pg_catalog.to_jsonb(NEW)-ARRAY['run_output_object_id']::text[]) "
    "IS NOT DISTINCT FROM (pg_catalog.to_jsonb(OLD)-"
    "ARRAY['run_output_object_id']::text[]) AND "
    "public.idis_task6_current_lease_is_valid("
    "NEW.tenant_id,NEW.deal_id,NEW.run_id) AND EXISTS (SELECT 1 FROM "
    "public.run_steps s WHERE s.tenant_id=NEW.tenant_id AND "
    "s.run_id=NEW.run_id AND "
    "s.step_name='METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order=6 "
    "AND s.status='RUNNING') AND EXISTS (SELECT 1 FROM "
    "public.run_output_objects r WHERE r.tenant_id=NEW.tenant_id AND "
    "r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND "
    "r.output_object_id=NEW.run_output_object_id AND "
    "r.output_class=NEW.run_output_object_class AND "
    "r.output_kind=NEW.run_output_object_kind) THEN RETURN NEW; END IF;"
)
_TASK6_HELPER = (
    "CREATE FUNCTION public.idis_task6_output_root_attach_is_valid_v1("
    "p_old jsonb,p_new jsonb,p_tenant uuid,p_deal uuid,p_run uuid) "
    "RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp "
    "AS $$SELECT p_old->>'run_output_object_id' IS NULL AND "
    "p_new->>'run_output_object_id' IS NOT NULL AND "
    "(p_new-ARRAY['run_output_object_id']::text[]) IS NOT DISTINCT FROM "
    "(p_old-ARRAY['run_output_object_id']::text[]) AND "
    "public.idis_task6_current_lease_is_valid(p_tenant,p_deal,p_run) AND "
    "EXISTS (SELECT 1 FROM public.run_steps s WHERE "
    "s.tenant_id=p_tenant AND s.run_id=p_run AND "
    "s.step_name='METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order=6 "
    "AND s.status='RUNNING') AND EXISTS (SELECT 1 FROM "
    "public.run_output_objects r WHERE r.tenant_id=p_tenant AND "
    "r.deal_id=p_deal AND r.run_id=p_run AND "
    "r.output_object_id=(p_new->>'run_output_object_id')::uuid AND "
    "r.output_class=p_new->>'run_output_object_class' AND "
    "r.output_kind=p_new->>'run_output_object_kind')$$; REVOKE ALL ON "
    "FUNCTION public.idis_task6_output_root_attach_is_valid_v1("
    "jsonb,jsonb,uuid,uuid,uuid) FROM PUBLIC; "
)
_TASK6_NEW_OUTPUT_BRANCH = (
    "IF TG_OP='UPDATE' AND TG_TABLE_NAME IN "
    "('methodology_extraction_results','methodology_result_factual_outputs') "
    "AND public.idis_task6_output_root_attach_is_valid_v1("
    "pg_catalog.to_jsonb(OLD),pg_catalog.to_jsonb(NEW),"
    "NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RETURN NEW; END IF;"
)

_LEASE_UPGRADE = (
    "CREATE OR REPLACE FUNCTION public.idis_task6_current_lease_is_valid("
    "checked_tenant uuid,checked_deal uuid,checked_run uuid) RETURNS boolean "
    "LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$DECLARE "
    "current_tenant_id uuid; current_deal_id uuid; current_run_id uuid; "
    "current_owner_id uuid; current_fencing_token bigint; BEGIN BEGIN "
    "current_tenant_id:=NULLIF(pg_catalog.current_setting("
    "'idis.worker_lease_tenant_id',true),'')::uuid; "
    "current_deal_id:=NULLIF(pg_catalog.current_setting("
    "'idis.worker_lease_deal_id',true),'')::uuid; "
    "current_run_id:=NULLIF(pg_catalog.current_setting("
    "'idis.worker_lease_run_id',true),'')::uuid; "
    "current_owner_id:=NULLIF(pg_catalog.current_setting("
    "'idis.worker_lease_owner_id',true),'')::uuid; "
    "current_fencing_token:=NULLIF(pg_catalog.current_setting("
    "'idis.worker_lease_fencing_token',true),'')::bigint; EXCEPTION WHEN "
    "OTHERS THEN RETURN false; END; RETURN EXISTS (SELECT 1 FROM public.runs r "
    "WHERE r.tenant_id=checked_tenant AND r.deal_id=checked_deal AND "
    "r.run_id=checked_run AND r.status='RUNNING' AND "
    "r.tenant_id=current_tenant_id AND r.deal_id=current_deal_id AND "
    "r.run_id=current_run_id AND r.lease_owner_id=current_owner_id AND "
    "r.lease_fencing_token=current_fencing_token AND "
    "r.lease_expires_at>pg_catalog.statement_timestamp()); END$$"
)
_LEASE_0037_RESTORE = """
        CREATE OR REPLACE FUNCTION idis_task6_current_lease_is_valid(
            checked_tenant UUID, checked_deal UUID, checked_run UUID
        ) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
        DECLARE
            current_tenant_id UUID;
            current_deal_id UUID;
            current_run_id UUID;
            current_owner_id UUID;
            current_fencing_token BIGINT;
        BEGIN
            BEGIN
                current_tenant_id := NULLIF(
                    current_setting('idis.worker_lease_tenant_id', true), ''
                )::uuid;
                current_deal_id := NULLIF(
                    current_setting('idis.worker_lease_deal_id', true), ''
                )::uuid;
                current_run_id := NULLIF(
                    current_setting('idis.worker_lease_run_id', true), ''
                )::uuid;
                current_owner_id := NULLIF(
                    current_setting('idis.worker_lease_owner_id', true), ''
                )::uuid;
                current_fencing_token := NULLIF(
                    current_setting('idis.worker_lease_fencing_token', true), ''
                )::bigint;
            EXCEPTION WHEN OTHERS THEN
                RETURN false;
            END;
            RETURN EXISTS (
                SELECT 1 FROM runs r
                WHERE r.tenant_id = checked_tenant
                  AND r.deal_id = checked_deal
                  AND r.run_id = checked_run
                  AND r.status = 'RUNNING'
                  AND r.tenant_id = current_tenant_id
                  AND r.deal_id = current_deal_id
                  AND r.run_id = current_run_id
                  AND r.lease_owner_id = current_owner_id
                  AND r.lease_fencing_token = current_fencing_token
                  AND r.lease_expires_at > statement_timestamp()
            );
        END
        $$
        """
_LEASE_SIGNATURE = "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)"
_LEASE_REPLACEMENT = [
    "0037",
    _LEASE_SIGNATURE,
    "PRESERVE_PRE_0038_OWNER",
    ["search_path=pg_catalog, pg_temp"],
    [],
    [],
    hashlib.sha256(_LEASE_0037_RESTORE.encode("utf-8")).hexdigest(),
]

_EXPECTED_CHANGED_PATHS = {
    ("metadata", "artifact_schema"),
    ("metadata", "pipeline_contract_version"),
    ("metadata", "reviewed_design_sha256"),
    ("metadata", "reviewed_design_utf8_hex"),
    ("digest_recipe_registry", "rows", 5, 1),
    ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
    ("sql_authority_registry", "rows", 19, "body_sql_utf8_hex_tuple", 2),
    ("sql_authority_registry", "rows", 19, "expected_sha256"),
    ("sql_authority_registry", "rows", 21, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 21, "expected_sha256"),
    ("sql_authority_registry", "rows", 22, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 22, "expected_sha256"),
    ("sql_authority_registry", "rows", 26, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 26, "expected_sha256"),
    ("sql_authority_registry", "rows", 27, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 27, "expected_sha256"),
    ("sql_authority_registry", "expected_sha256"),
    ("erasure_execution_registry", "rows", 0, "legacy_guard_replacement_tuple"),
    (
        "erasure_execution_registry",
        "rows",
        0,
        "legacy_guard_upgrade_sql_utf8_hex_tuple",
    ),
    (
        "erasure_execution_registry",
        "rows",
        0,
        "legacy_guard_downgrade_sql_utf8_hex_tuple",
    ),
    ("erasure_execution_registry", "rows", 0, "expected_sha256"),
    ("erasure_execution_registry", "expected_sha256"),
}


class MaterializationError(RuntimeError):
    """Stable fail-closed v11 materialization error."""


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
        raise MaterializationError("TASK7_V11_SQL_AUTHORITY_BODY_INVALID") from exc


def _encode_statements(statements: list[str]) -> list[str]:
    return [statement.encode("utf-8").hex() for statement in statements]


def _rehash_authority_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ROW_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()


def _rehash_erasure_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ERASURE_ROW_HASH_DOMAIN + _canonical_bytes(payload)
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


def _replace_once(value: str, old: str, new: str, code: str) -> str:
    if value.count(old) != 1:
        raise MaterializationError(code)
    return value.replace(old, new, 1)


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    old_version = "full-strict-v11" if reverse else "full-strict-v10"
    new_version = "full-strict-v10" if reverse else "full-strict-v11"
    if not isinstance(design, list) or design[:2] != [
        f"{old_version}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old_version],
    ]:
        raise MaterializationError("TASK7_V11_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = f"{new_version}-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", new_version]
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{new_version}-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = new_version
    metadata["append_frozen"] = False
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()


def _transform_digest_recipe(artifact: dict[str, Any], *, reverse: bool) -> None:
    recipes = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "11" if reverse else "10"
    new_ordinal = "10" if reverse else "11"
    old_version = "full-strict-v11" if reverse else "full-strict-v10"
    new_version = "full-strict-v10" if reverse else "full-strict-v11"
    expected = (
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    if recipes[5] != ["pipeline_contract_hash", expected]:
        raise MaterializationError("TASK7_V11_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


def _transform_sql_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    section = artifact["sql_authority_registry"]
    rows = section["rows"]
    if len(rows) != 28:
        raise MaterializationError("TASK7_V11_AUTHORITY_CENSUS_INVALID")
    by_id = {row["authority_id"]: row for row in rows}

    transformations = (
        (
            "current-receipt-equality-v1",
            2,
            (
                (_RECEIPT_NEW_SCOPE, _RECEIPT_OLD_SCOPE)
                if reverse
                else (_RECEIPT_OLD_SCOPE, _RECEIPT_NEW_SCOPE)
            ),
            "TASK7_V11_RECEIPT_SCOPE_SHAPE_INVALID",
        ),
        (
            "current-append-only-lease-v1",
            0,
            (
                (
                    _APPEND_HELPER + "CREATE FUNCTION",
                    "CREATE FUNCTION",
                )
                if reverse
                else (
                    "CREATE FUNCTION",
                    _APPEND_HELPER + "CREATE FUNCTION",
                )
            ),
            "TASK7_V11_APPEND_HELPER_SHAPE_INVALID",
        ),
        (
            "current-source-bound-v1",
            0,
            (
                (
                    _SOURCE_HELPER + "CREATE FUNCTION",
                    "CREATE FUNCTION",
                )
                if reverse
                else (
                    "CREATE FUNCTION",
                    _SOURCE_HELPER + "CREATE FUNCTION",
                )
            ),
            "TASK7_V11_SOURCE_HELPER_SHAPE_INVALID",
        ),
        (
            "current-task6-immutable-replacement-v1",
            0,
            (
                (
                    _TASK6_HELPER + "CREATE OR REPLACE FUNCTION",
                    "CREATE OR REPLACE FUNCTION",
                )
                if reverse
                else (
                    "CREATE OR REPLACE FUNCTION",
                    _TASK6_HELPER + "CREATE OR REPLACE FUNCTION",
                )
            ),
            "TASK7_V11_TASK6_HELPER_SHAPE_INVALID",
        ),
    )
    for authority_id, statement_ordinal, (old, new), code in transformations:
        row = by_id.get(authority_id)
        if row is None:
            raise MaterializationError(f"TASK7_V11_AUTHORITY_MISSING:{authority_id}")
        statements = _decode_statements(row)
        statements[statement_ordinal] = _replace_once(statements[statement_ordinal], old, new, code)
        row["body_sql_utf8_hex_tuple"] = _encode_statements(statements)

    receipt = by_id["current-receipt-equality-v1"]
    append_only = by_id["current-append-only-lease-v1"]
    source_bound = by_id["current-source-bound-v1"]
    task6 = by_id["current-task6-immutable-replacement-v1"]
    receipt_sql = _decode_statements(receipt)
    append_sql = _decode_statements(append_only)
    source_sql = _decode_statements(source_bound)
    task6_sql = _decode_statements(task6)
    pairs = (
        (
            receipt_sql,
            2,
            (
                ("full-strict-v11", "full-strict-v10")
                if reverse
                else ("full-strict-v10", "full-strict-v11")
            ),
            "TASK7_V11_RECEIPT_VERSION_SHAPE_INVALID",
        ),
        (
            append_sql,
            0,
            (
                (_APPEND_NEW_ADMISSION, _APPEND_OLD_ADMISSION)
                if reverse
                else (_APPEND_OLD_ADMISSION, _APPEND_NEW_ADMISSION)
            ),
            "TASK7_V11_APPEND_ADMISSION_SHAPE_INVALID",
        ),
        (
            source_sql,
            0,
            (
                (_SOURCE_NEW_PACKAGE, _SOURCE_OLD_PACKAGE)
                if reverse
                else (_SOURCE_OLD_PACKAGE, _SOURCE_NEW_PACKAGE)
            ),
            "TASK7_V11_SOURCE_PACKAGE_SHAPE_INVALID",
        ),
        (
            task6_sql,
            0,
            (
                (_TASK6_NEW_OUTPUT_BRANCH, _TASK6_OLD_OUTPUT_BRANCH)
                if reverse
                else (_TASK6_OLD_OUTPUT_BRANCH, _TASK6_NEW_OUTPUT_BRANCH)
            ),
            "TASK7_V11_TASK6_OUTPUT_SHAPE_INVALID",
        ),
    )
    for statements, ordinal, (old, new), code in pairs:
        statements[ordinal] = _replace_once(statements[ordinal], old, new, code)

    receipt["body_sql_utf8_hex_tuple"] = _encode_statements(receipt_sql)
    append_only["body_sql_utf8_hex_tuple"] = _encode_statements(append_sql)
    source_bound["body_sql_utf8_hex_tuple"] = _encode_statements(source_sql)
    task6["body_sql_utf8_hex_tuple"] = _encode_statements(task6_sql)

    task5 = by_id.get("current-task5-coverage-replacement-v1")
    if task5 is None:
        raise MaterializationError("TASK7_V11_TASK5_AUTHORITY_MISSING")
    task5_sql = _decode_statements(task5)
    old_version = "full-strict-v11" if reverse else "full-strict-v10"
    new_version = "full-strict-v10" if reverse else "full-strict-v11"
    task5_sql[0] = _replace_once(
        task5_sql[0],
        old_version,
        new_version,
        "TASK7_V11_TASK5_VERSION_SHAPE_INVALID",
    )
    task5["body_sql_utf8_hex_tuple"] = _encode_statements(task5_sql)

    for row in (receipt, append_only, source_bound, task5, task6):
        _rehash_authority_row(row)
    section["expected_sha256"] = _h([_SQL_REGISTRY_HASH_DOMAIN, rows])


def _transform_erasure_execution(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    section = artifact["erasure_execution_registry"]
    rows = section["rows"]
    if len(rows) != 1:
        raise MaterializationError("TASK7_V11_ERASURE_CENSUS_INVALID")
    row = rows[0]
    replacements = row["legacy_guard_replacement_tuple"]
    upgrades = [
        bytes.fromhex(value).decode("utf-8")
        for value in row["legacy_guard_upgrade_sql_utf8_hex_tuple"]
    ]
    downgrades = [
        bytes.fromhex(value).decode("utf-8")
        for value in row["legacy_guard_downgrade_sql_utf8_hex_tuple"]
    ]

    erasure_signature = "public.idis_task6_erasure_requested(uuid,uuid)"
    replacement_ordinal = next(
        (ordinal + 1 for ordinal, item in enumerate(replacements) if item[1] == erasure_signature),
        -1,
    )
    upgrade_ordinal = next(
        (
            ordinal + 1
            for ordinal, statement in enumerate(upgrades)
            if "public.idis_task6_erasure_requested(" in statement
        ),
        -1,
    )
    downgrade_ordinal = next(
        (
            ordinal + 1
            for ordinal, statement in enumerate(downgrades)
            if "idis_task6_erasure_requested(" in statement
        ),
        -1,
    )
    if min(replacement_ordinal, upgrade_ordinal, downgrade_ordinal) < 0:
        raise MaterializationError("TASK7_V11_LEASE_INSERTION_ANCHOR_MISSING")

    if reverse:
        if (
            replacements[replacement_ordinal] != _LEASE_REPLACEMENT
            or upgrades[upgrade_ordinal] != _LEASE_UPGRADE
            or downgrades[downgrade_ordinal] != _LEASE_0037_RESTORE
        ):
            raise MaterializationError("TASK7_V11_LEASE_REPLACEMENT_SHAPE_INVALID")
        replacements.pop(replacement_ordinal)
        upgrades.pop(upgrade_ordinal)
        downgrades.pop(downgrade_ordinal)
    else:
        if any(item[1] == _LEASE_SIGNATURE for item in replacements):
            raise MaterializationError("TASK7_V11_LEASE_REPLACEMENT_ALREADY_PRESENT")
        replacements.insert(replacement_ordinal, copy.deepcopy(_LEASE_REPLACEMENT))
        upgrades.insert(upgrade_ordinal, _LEASE_UPGRADE)
        downgrades.insert(downgrade_ordinal, _LEASE_0037_RESTORE)

    row["legacy_guard_upgrade_sql_utf8_hex_tuple"] = _encode_statements(upgrades)
    row["legacy_guard_downgrade_sql_utf8_hex_tuple"] = _encode_statements(downgrades)
    _rehash_erasure_row(row)
    section["expected_sha256"] = _h([_ERASURE_REGISTRY_HASH_DOMAIN, rows])


def project_v11_to_v10(v11: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact reviewer-pinned v10 bytes from a v11 candidate."""

    projected = copy.deepcopy(v11)
    _transform_erasure_execution(projected, reverse=True)
    _transform_sql_authorities(projected, reverse=True)
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    if hashlib.sha512(_render(projected)).hexdigest() != _V10_SOURCE_SHA512:
        raise MaterializationError("TASK7_V11_EXACT_V10_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v10: dict[str, Any], v11: dict[str, Any]) -> None:
    changed = _changed_paths(v10, v11)
    if changed != _EXPECTED_CHANGED_PATHS:
        rendered = ",".join("/".join(map(str, path)) for path in sorted(changed))
        raise MaterializationError(f"TASK7_V11_UNAUTHORIZED_DRIFT:{rendered}")
    if project_v11_to_v10(v11) != v10:
        raise MaterializationError("TASK7_V11_V10_PROJECTION_DRIFT")


def _load_v10(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V10_SOURCE_SHA512:
        raise MaterializationError("TASK7_V11_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V11_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MaterializationError("TASK7_V11_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v10-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v10"
        or metadata.get("append_frozen") is not False
    ):
        raise MaterializationError("TASK7_V11_BASE_METADATA_INVALID")
    return value


def build_v11(v10: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v11 candidate."""

    artifact = copy.deepcopy(v10)
    _transform_sql_authorities(artifact, reverse=False)
    _transform_erasure_execution(artifact, reverse=False)
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v10, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise MaterializationError("TASK7_V11_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V11_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v10_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rendered = _render(build_v11(_load_v10(args.base_artifact)))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V11_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
