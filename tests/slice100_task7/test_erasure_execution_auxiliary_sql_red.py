"""RED admission for the Task-7 erasure auxiliary SQL lifecycle.

The runner body alone is not executable safely.  Migration 0038 also has to
serialize direct legal-hold writes, replace the exact legacy delete guards that
would otherwise reject the dedicated executor, and freeze the later FUTURE38
activation/deactivation choreography.  These bytes are part of
``ErasureExecutionSpecV1`` so none of those lifecycle steps can live as
unhashed migration prose.

This module deliberately derives its tuples independently of the materializer.
It uses the one-shot materializer only as an in-memory producer; no v4 artifact
or migration is required for the initial RED run.
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
_MATERIALIZER_PATH = _REPO_ROOT / "scripts" / "materialize_full_strict_v4_preimage.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v4_contract_fixture.py"
_VOID_V2_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v2_reviewed_preimage_v1.json"
_REVIEWED_AUTHORITY = (
    _REPO_ROOT / "tests" / "slice100_task7" / "_full_strict_v4_reviewed_authority.py"
)

_BASE_EXECUTION_FIELDS = (
    "execution_ordinal",
    "execution_id",
    "execution_version",
    "runner_function_signature",
    "activation_state",
    "current_relation_tuple",
    "future_relation_tuple",
    "complete_relation_tuple",
    "declared_read_relation_tuple",
    "advisory_lock_tuple",
    "current_body_sql_utf8_hex_tuple",
    "current_acl_sql_utf8_hex_tuple",
    "future_body_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
    "future_activation_census_tuple",
    "future_activation_census_sha256",
)
_AUXILIARY_EXECUTION_FIELDS = (
    "hold_lock_contract_tuple",
    "guard_authority_contract_tuple",
    "legacy_guard_replacement_tuple",
    "hold_lock_upgrade_sql_utf8_hex_tuple",
    "hold_lock_downgrade_sql_utf8_hex_tuple",
    "legacy_guard_upgrade_sql_utf8_hex_tuple",
    "legacy_guard_downgrade_sql_utf8_hex_tuple",
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
)
_EXECUTION_FIELDS = (
    *_BASE_EXECUTION_FIELDS,
    *_AUXILIARY_EXECUTION_FIELDS,
    "legal_red_case_tuple",
    "illegal_red_case_tuple",
    "direct_sql_red_case_tuple",
    "expected_sha256",
)

_LOCK_DOMAIN = "idis-task7-erasure-tenant-lock-v1:"
_LOCK_GUARD_SIGNATURE = "public.idis_task7_legal_hold_lock_guard()"
_LOCK_TRIGGER = "trg_task7_legal_holds_tenant_lock"
_HOLD_LOCK_CONTRACT = (
    "legal-hold-lock-enforcement-v1",
    _LOCK_GUARD_SIGNATURE,
    _LOCK_TRIGGER,
    "public.legal_holds",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    "PostgreSQL-16-trigger-acquires-xact-lock-v1",
    "TASK7_ERASURE_HOLD_TENANT_CONTEXT_REJECTED",
    "TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED",
)

_GUARD_AUTHORITY_SIGNATURE = "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
_GUARD_AUTHORITY_CONTRACT = (
    "approved-erasure-guard-context-v1",
    _GUARD_AUTHORITY_SIGNATURE,
    "SECURITY DEFINER",
    "idis_erasure_function_owner",
    ("search_path=pg_catalog, pg_temp",),
    "idis_erasure_executor",
    ("idis.tenant_id", "idis.erasure_context"),
    ("REQUESTED", "FAILED"),
    "transaction-local-only",
)

# Trigger tuple:
# (table, trigger, timing, events, orientation, enabled, deferrable, initially_deferred)
_STRICT_PROOF_TRIGGER = (
    "public.strict_preflight_proofs",
    "strict_preflight_proofs_immutability",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_SELECTION_TRIGGER = (
    "public.run_methodology_selections",
    "trg_run_methodology_selections_guard",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_COVERAGE_TRIGGER = (
    "public.methodology_coverage_records",
    "trg_methodology_coverage_scope",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_IMMUTABLE_TRIGGERS = tuple(
    (
        f"public.{table}",
        (
            "trg_methodology_result_factual_outputs_immutable"
            if table == "methodology_result_factual_outputs"
            else f"trg_{table}_immutable"
        ),
        "BEFORE",
        ("INSERT", "UPDATE", "DELETE"),
        "ROW",
        "O",
        False,
        False,
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
)
_ATTEMPT_TRIGGER = (
    "public.methodology_extraction_attempts",
    "trg_methodology_extraction_attempts_lifecycle",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_OUTPUT_VALIDATION_TRIGGERS = (
    (
        "public.methodology_extraction_results",
        "trg_methodology_extraction_results_outputs",
        "AFTER",
        ("INSERT", "UPDATE"),
        "ROW",
        "O",
        True,
        True,
    ),
    (
        "public.methodology_result_factual_outputs",
        "trg_methodology_result_factual_outputs_count",
        "AFTER",
        ("INSERT", "UPDATE", "DELETE"),
        "ROW",
        "O",
        True,
        True,
    ),
)

# Each row is:
# (revision, function signature, owner rule, upgrade proconfig,
#  downgrade proconfig, retained trigger tuple, canonical legacy-body sha256).
# The final digest is over the exact one-line, single-space SQL bytes from the
# named revision, not over a reconstructed or introspected future catalog.
_LEGACY_GUARD_REPLACEMENTS = (
    (
        "0031",
        "public.idis_prevent_strict_preflight_proof_mutation()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_STRICT_PROOF_TRIGGER,),
        "1a05ce8af35fe5102cba13e3198cdf92e82b966a15ea88552024fc7244c7e139",
    ),
    (
        "0036",
        "public.idis_task5_selection_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_SELECTION_TRIGGER,),
        "e4504847befc282ffbfce6d6c123fef5eaf1aac12b2478d496247a8f2df42956",
    ),
    (
        "0036",
        "public.idis_task5_coverage_scope_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_COVERAGE_TRIGGER,),
        "e135a1c656c7d5015f901276daa3b058e042b7d2573928b0090ddd7e93e5ba4f",
    ),
    (
        "0037",
        "public.idis_task6_erasure_requested(uuid,uuid)",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (),
        "66e7319519dbfd4c1b94fd56ed5990b9c9046c4e60f3858034f6e374e47e3de8",
    ),
    (
        "0037",
        "public.idis_task6_immutable_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        _IMMUTABLE_TRIGGERS,
        "6cd61a832cf2af65c911480ce4a6534350a9500b6d526f47354b3d7c9c1059cf",
    ),
    (
        "0037",
        "public.idis_task6_attempt_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_ATTEMPT_TRIGGER,),
        "580389555daf84edd33739439dc46c1203781d681f5d1a30d86e6904f851b85e",
    ),
    (
        "0037",
        "public.idis_task6_validate_result_outputs()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        _OUTPUT_VALIDATION_TRIGGERS,
        "b55d3fb0981fc7a8e5a282cec1c672975e5ac5697c55aa7a8b129b97030ad50e",
    ),
)

_RUNNER_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_REGISTRY_HASH_DOMAIN = "full-strict-v4-erasure-execution-registry-v1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _in_memory_artifact() -> dict[str, Any]:
    module = _load_module(_MATERIALIZER_PATH, "task7_auxiliary_sql_materializer")
    rejected = module._load_rejected_artifact(_VOID_V2_ARTIFACT)
    authority = module._load_module(_REVIEWED_AUTHORITY)
    artifact = module._build_artifact(rejected, authority)
    assert isinstance(artifact, dict)
    return artifact


def _execution_row(artifact: dict[str, Any]) -> dict[str, Any]:
    section = artifact["erasure_execution_registry"]
    assert tuple(section) == ("rows", "expected_sha256")
    rows = section["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _decode_sql_tuple(row: dict[str, Any], field: str) -> tuple[str, ...]:
    encoded = row.get(field)
    assert isinstance(encoded, list) and encoded, f"TASK7_ERASURE_AUXILIARY_FIELD_ABSENT:{field}"
    decoded = tuple(bytes.fromhex(item).decode("utf-8") for item in encoded)
    assert all(statement == statement.strip() for statement in decoded)
    assert all("\r" not in statement and "\n" not in statement for statement in decoded)
    assert all(statement == " ".join(statement.split()) for statement in decoded)
    return decoded


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rehash_execution_section(artifact: dict[str, Any]) -> None:
    row = _execution_row(artifact)
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ROW_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()
    artifact["erasure_execution_registry"]["expected_sha256"] = hashlib.sha256(
        _canonical_bytes([_REGISTRY_HASH_DOMAIN, [row]])
    ).hexdigest()


def _normalized_signature(signature: str) -> str:
    return signature.removeprefix("public.").lower()


def _statement_for_signature(statements: tuple[str, ...], signature: str) -> str:
    normalized = _normalized_signature(signature)
    function_name, expected_arguments = normalized[:-1].split("(", maxsplit=1)
    expected_types = tuple(
        argument.strip() for argument in expected_arguments.split(",") if argument.strip()
    )
    header = re.compile(
        rf"\bcreate\s+or\s+replace\s+function\s+(?:public\.)?"
        rf"{re.escape(function_name)}\s*\((?P<arguments>.*?)\)\s+returns\b",
        re.IGNORECASE,
    )
    matches: list[str] = []
    for statement in statements:
        match = header.search(statement)
        if match is None:
            continue
        actual_types = tuple(
            argument.strip().lower().split()[-1]
            for argument in match.group("arguments").split(",")
            if argument.strip()
        )
        if actual_types == expected_types:
            matches.append(statement)
    assert len(matches) == 1, f"TASK7_ERASURE_GUARD_BODY_CARDINALITY:{signature}"
    return matches[0]


def test_erasure_execution_spec_hash_binds_the_closed_auxiliary_field_set() -> None:
    materializer = _load_module(_MATERIALIZER_PATH, "task7_auxiliary_field_materializer")
    generator = _load_module(_GENERATOR_PATH, "task7_auxiliary_field_generator")

    assert getattr(materializer, "ERASURE_EXECUTION_FIELDS", None) == _EXECUTION_FIELDS
    assert getattr(generator, "_ERASURE_EXECUTION_FIELDS", None) == _EXECUTION_FIELDS

    artifact = _in_memory_artifact()
    row = _execution_row(artifact)
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    assert tuple(tuple_specs["ErasureExecutionSpecV1"]) == _EXECUTION_FIELDS
    assert tuple(row) == _EXECUTION_FIELDS


def test_legal_hold_trigger_always_acquires_the_exact_pg16_transaction_lock() -> None:
    row = _execution_row(_in_memory_artifact())
    assert tuple(row["hold_lock_contract_tuple"]) == _HOLD_LOCK_CONTRACT
    upgrade = _decode_sql_tuple(row, "hold_lock_upgrade_sql_utf8_hex_tuple")
    sql = " ".join(upgrade)
    compact = "".join(sql.lower().split())

    assert f"createfunction{_LOCK_GUARD_SIGNATURE}" in compact
    assert f"createtrigger{_LOCK_TRIGGER}" in compact
    assert "beforeinsertorupdateordeleteonpublic.legal_holds" in compact
    assert "foreachrowexecutefunctionpublic.idis_task7_legal_hold_lock_guard()" in compact
    assert "securitydefiner" in compact
    assert "setsearch_path=pg_catalog,pg_temp" in compact

    # Direct SQL cannot perform an unlocked durable mutation: the BEFORE trigger
    # itself takes the canonical xact-scoped lock.  Repository code still takes
    # the same lock before its first read/mutation, so both paths serialize.
    assert compact.count("pg_catalog.pg_advisory_xact_lock(") == 1
    assert "pg_catalog.hashtextextended(" in compact
    assert _LOCK_DOMAIN in compact
    assert ",0)" in compact
    lock_index = compact.index("pg_catalog.pg_advisory_xact_lock(")
    assert lock_index < compact.rindex("return")

    assert "new.tenant_idisdistinctfromold.tenant_id" in compact
    assert "task7_erasure_hold_tenant_change_rejected" in compact
    assert "pg_catalog.current_setting('idis.tenant_id',true)" in compact
    assert "task7_erasure_hold_tenant_context_rejected" in compact
    assert "new.tenant_id" in compact and "old.tenant_id" in compact
    assert "pg_catalog.pg_locks" not in compact
    assert "pg_catalog.pg_advisory_unlock" not in compact
    assert "pg_catalog.pg_advisory_lock(" not in compact
    assert "pg_advisory_lock_shared" not in compact
    assert "pg_advisory_xact_lock_shared" not in compact
    assert "pg_try_advisory" not in compact
    assert "execute" not in compact.replace("executefunction", "")


def test_hold_lock_downgrade_removes_only_the_exact_task7_objects() -> None:
    row = _execution_row(_in_memory_artifact())
    downgrade = _decode_sql_tuple(row, "hold_lock_downgrade_sql_utf8_hex_tuple")
    assert downgrade == (
        "DROP TRIGGER trg_task7_legal_holds_tenant_lock ON public.legal_holds",
        "DROP FUNCTION public.idis_task7_legal_hold_lock_guard()",
    )
    assert all("IF EXISTS" not in statement.upper() for statement in downgrade)
    assert all("CASCADE" not in statement.upper() for statement in downgrade)


def test_legacy_guard_inventory_is_exact_and_retains_every_trigger_catalog_shape() -> None:
    row = _execution_row(_in_memory_artifact())
    actual = tuple(
        (
            item[0],
            item[1],
            item[2],
            tuple(item[3]),
            tuple(item[4]),
            tuple(
                (
                    trigger[0],
                    trigger[1],
                    trigger[2],
                    tuple(trigger[3]),
                    trigger[4],
                    trigger[5],
                    trigger[6],
                    trigger[7],
                )
                for trigger in item[5]
            ),
            item[6],
        )
        for item in row["legacy_guard_replacement_tuple"]
    )
    assert actual == _LEGACY_GUARD_REPLACEMENTS
    assert tuple(item[0] for item in actual) == (
        "0031",
        "0036",
        "0036",
        "0037",
        "0037",
        "0037",
        "0037",
    )
    assert all(item[2] == "PRESERVE_PRE_0038_OWNER" for item in actual)
    assert all(item[3] == ("search_path=pg_catalog, pg_temp",) for item in actual)
    assert all(item[4] == () for item in actual)
    assert all(_HASH_RE.fullmatch(item[6]) for item in actual)
    assert all(trigger[5] == "O" for item in actual for trigger in item[5])


def test_guard_upgrade_bytes_require_the_authenticated_transaction_local_context() -> None:
    row = _execution_row(_in_memory_artifact())
    assert tuple(row["guard_authority_contract_tuple"]) == _GUARD_AUTHORITY_CONTRACT
    statements = _decode_sql_tuple(row, "legacy_guard_upgrade_sql_utf8_hex_tuple")
    sql = " ".join(statements)
    compact = "".join(sql.lower().split())

    assert f"createfunction{_GUARD_AUTHORITY_SIGNATURE}" in compact
    assert "securitydefiner" in compact
    assert "setsearch_path=pg_catalog,pg_temp" in compact
    assert "session_user='idis_erasure_executor'" in compact
    assert "current_user='idis_erasure_function_owner'" in compact
    assert "pg_catalog.current_setting('idis.tenant_id',true)" in compact
    assert "pg_catalog.current_setting('idis.erasure_context',true)='true'" in compact
    assert "statusin('requested','failed')" in compact
    assert "frompublic.erasure_requests" in compact
    assert (
        "alterfunctionpublic.idis_task7_approved_erasure_context_v1(uuid,uuid)"
        "ownertoidis_erasure_function_owner"
    ) in compact
    assert (
        "revokeallonfunctionpublic.idis_task7_approved_erasure_context_v1(uuid,uuid)frompublic"
    ) in compact

    replacement_statements = tuple(
        _statement_for_signature(statements, item[1]) for item in _LEGACY_GUARD_REPLACEMENTS
    )
    assert len(set(replacement_statements)) == len(_LEGACY_GUARD_REPLACEMENTS)
    for statement in replacement_statements:
        statement_compact = "".join(statement.lower().split())
        assert "setsearch_path=pg_catalog,pg_temp" in statement_compact
        assert "public.idis_task7_approved_erasure_context_v1(" in statement_compact
        assert "fromerasure_requests" not in statement_compact
        assert "fromruns" not in statement_compact
        assert "frommethodology_" not in statement_compact

    deferred = _statement_for_signature(
        statements,
        "public.idis_task6_validate_result_outputs()",
    )
    deferred_compact = "".join(deferred.lower().split())
    early_return = deferred_compact.index("public.idis_task7_approved_erasure_context_v1(")
    first_validation_read = min(
        deferred_compact.index("frompublic.methodology_extraction_results"),
        deferred_compact.index("frompublic.methodology_result_factual_outputs"),
    )
    assert early_return < first_validation_read
    assert "returnnull" in deferred_compact[early_return:first_validation_read]

    assert "disabletrigger" not in compact
    assert "session_replication_role" not in compact
    assert "bypassrls" not in compact
    assert "set_config('idis.erasure_context'" not in compact


def test_downgrade_restores_literal_0031_0036_0037_bytes_and_catalog_contract() -> None:
    row = _execution_row(_in_memory_artifact())
    statements = _decode_sql_tuple(row, "legacy_guard_downgrade_sql_utf8_hex_tuple")
    assert len(statements) == len(_LEGACY_GUARD_REPLACEMENTS) + 1

    for replacement in _LEGACY_GUARD_REPLACEMENTS:
        signature = replacement[1]
        expected_digest = replacement[6]
        statement = _statement_for_signature(statements, signature)
        # One-line canonical SQL cannot retain a source `--` comment: it would
        # consume the remaining PL/pgSQL body and make downgrade non-executable.
        assert "--" not in statement, "TASK7_ERASURE_RESTORE_LINE_COMMENT_FORBIDDEN"
        assert hashlib.sha256(statement.encode("utf-8")).hexdigest() == expected_digest
        compact = "".join(statement.lower().split())
        assert "idis_task7_approved_erasure_context_v1" not in compact
        assert "setsearch_path=" not in compact
        assert "securitydefiner" not in compact

    assert statements[-1] == (
        "DROP FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
    )
    all_sql = " ".join(statements).upper()
    assert "ALTER FUNCTION" not in all_sql
    assert "ALTER TRIGGER" not in all_sql
    assert "DROP TRIGGER" not in all_sql
    assert "CREATE TRIGGER" not in all_sql
    assert "DISABLE TRIGGER" not in all_sql
    assert "IF EXISTS" not in all_sql
    assert "CASCADE" not in all_sql


def test_future_activation_and_inverse_are_literal_drop_create_acl_choreographies() -> None:
    row = _execution_row(_in_memory_artifact())
    activation = _decode_sql_tuple(row, "future_activation_sql_utf8_hex_tuple")
    restoration = _decode_sql_tuple(row, "future_restoration_sql_utf8_hex_tuple")
    future_body = _decode_sql_tuple(row, "future_body_sql_utf8_hex_tuple")
    future_acl = _decode_sql_tuple(row, "future_acl_sql_utf8_hex_tuple")
    current_body = _decode_sql_tuple(row, "current_body_sql_utf8_hex_tuple")
    current_acl = _decode_sql_tuple(row, "current_acl_sql_utf8_hex_tuple")
    drop = f"DROP FUNCTION {_RUNNER_SIGNATURE}"

    assert activation == (drop, *future_body, *future_acl)
    assert restoration == (drop, *current_body, *current_acl)
    for choreography in (activation, restoration):
        assert choreography[0] == drop
        assert choreography.count(drop) == 1
        assert "CREATE FUNCTION " + _RUNNER_SIGNATURE in choreography[1]
        compact = "".join(" ".join(choreography).lower().split())
        assert "createorreplace" not in compact
        # The runner legitimately uses PL/pgSQL IF EXISTS; only conditional
        # replacement of the exact function identity is forbidden here.
        assert "dropfunctionifexists" not in compact
        assert "to_regclass" not in compact
        assert "information_schema" not in compact
        assert "pg_catalog.pg_class" not in compact
        assert "execut format" not in compact
        assert "executeformat" not in compact

    census = row["future_activation_census_tuple"]
    assert len(census) == 38
    assert _HASH_RE.fullmatch(row["future_activation_census_sha256"])


@pytest.mark.parametrize("field", _AUXILIARY_EXECUTION_FIELDS)
def test_generator_rejects_semantic_drift_even_after_auxiliary_hashes_are_recomputed(
    field: str,
) -> None:
    artifact = copy.deepcopy(_in_memory_artifact())
    row = _execution_row(artifact)
    assert field in row, f"TASK7_ERASURE_AUXILIARY_FIELD_ABSENT:{field}"

    value = row[field]
    if field.endswith("_sql_utf8_hex_tuple"):
        assert isinstance(value, list) and value
        value[0] = ("00" if not value[0].startswith("00") else "01") + value[0][2:]
    else:
        assert isinstance(value, list) and value
        value[0] = "CORRUPTED-AUXILIARY-CONTRACT"
    _rehash_execution_section(artifact)

    generator = _load_module(_GENERATOR_PATH, f"task7_auxiliary_mutation_{field}")
    with pytest.raises(generator.ContractArtifactError, match="TASK7_ERASURE"):
        generator.validate_erasure_execution_registry(artifact)
