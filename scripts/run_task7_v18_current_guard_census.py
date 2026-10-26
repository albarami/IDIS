"""Execute the full-strict-v18 census with retained-definition restoration proof.

The append-frozen v17 runner is immutable.  This module hash-pins and adapts
that reviewed engine to v18, then adds the missing pre-freeze PostgreSQL proof:
all eight retained function definitions and all nine retained trigger
definitions must execute on revision 0037, reproduce the original catalog, and
leave no mutation after a transaction rollback.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
_V17_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v17_current_guard_census.py")
_V17_RUNNER_SHA256: Final = "9f3585fb6b10a35cf1c73bd3d7f08c6c3aacb60c83d5b4d8e9e32ee9c0ed539c"
_V18_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v18_census_scaffold.py")

_V18_MATERIALIZATION_SOURCE_MANIFEST: Final[tuple[tuple[str, int, str, str], ...]] = (
    (
        "contracts/full_strict_v18_reviewed_preimage_v1.json",
        6_315_410,
        "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038",
        "unfrozen-candidate",
    ),
    (
        "scripts/_task7_v18_census_scaffold.py",
        3_140,
        "f789c074b850f94f80d0740e7934c787a943739fe6fb008c3b54ea7c02bd5702",
        "postgres-census-scaffold",
    ),
    (
        "scripts/materialize_full_strict_v18_preimage.py",
        20_879,
        "da4ea2b038c2d7eadea3d6d70e0ec5ac0469b5c9e42469736f74324f59a36460",
        "candidate-materializer",
    ),
    (
        "scripts/generate_task7_v18_contract_fixture.py",
        6_298,
        "4a9aba57523ce2baaeb8341fcd131789d4842cae17cd5471f22e858c496aa3f3",
        "candidate-admission-generator",
    ),
    (
        "scripts/task7_v17_receipt_guard_sql.py",
        9_380,
        "93ed39c6d8ad018ee8629ea3989381ad39b7a4193f58354c161e7b400a27db1c",
        "immutable-receipt-guard-sql-constructor",
    ),
    (
        "scripts/task7_v17_source_projection_sql.py",
        52_970,
        "3d87ec27b57956ec3e3f1fad47681479616462b963f68303cfd3931b92161f11",
        "immutable-source-projection-sql-constructor",
    ),
)

_V18_ARTIFACT_SCHEMA: Final = "full-strict-v18-reviewed-preimage-v1"
_V18_CONTRACT_VERSION: Final = "full-strict-v18"
_PROOF_SCHEMA: Final = "task7-v18-downgrade-retained-definition-execution-proof-v1"
_PROOF_DIGEST_DOMAIN: Final = _PROOF_SCHEMA
_CATALOG_DIGEST_DOMAIN: Final = "task7-v18-retained-catalog-projection-v1"
_COMPLETE_EXECUTION_DOMAIN: Final = "task7-v18-complete-execution-v1"
_PROOF_ERROR: Final = "TASK7_V18_RETAINED_RESTORE_EXECUTION_INVALID"
_NESTED_ADAPTER_DIGEST_REPLACEMENTS: Final = (
    (
        "b3178fb8e9d89409a29db1d42a209ffea49d62566f9623b780224ccfe60915c5",
        "74aa26548edff669726c729ede2e339e3d6a162142ee63878f94cddc476c5317",
    ),
    (
        "6bd5ec330124325bc714c9f377031bd0d74faa9a463b7ae345c67797969b7c36",
        "a0e19f70a497cf75ac970c3d035326abd7ef5284f4a6a1a8d81dc12443929dac",
    ),
)
_FUNCTION_CATALOG_KEYS: Final = (
    "ordinal",
    "signature",
    "definition_sha256",
    "owner",
    "proconfig",
    "security_definer",
    "volatility",
    "acl",
)
_TRIGGER_CATALOG_KEYS: Final = (
    "ordinal",
    "function_signature",
    "table_name",
    "trigger_name",
    "definition_sha256",
    "enabled",
    "type_bits",
    "constraint_oid",
    "deferrable",
    "initially_deferred",
)
_PROOF_KEYS: Final = (
    "schema_version",
    "base_schema_revision",
    "transaction_mode",
    "executed_as_setup_admin",
    "function_restore_count",
    "trigger_restore_count",
    "function_restore_tuple",
    "trigger_restore_tuple",
    "catalog_before_projection",
    "postrestore_assertion_sha256",
    "catalog_before_sha256",
    "catalog_restored_sha256",
    "catalog_after_rollback_sha256",
    "restored_catalog_equals_before",
    "rollback_catalog_equals_before",
    "transaction_rolled_back",
    "execution_sha256",
)


def _load_pinned_v17() -> ModuleType:
    try:
        raw = _V17_RUNNER_PATH.read_bytes()
    except OSError as exc:
        raise RuntimeError("TASK7_V18_V17_CENSUS_RUNNER_DRIFT") from exc
    if not secrets.compare_digest(
        hashlib.sha256(raw).hexdigest(),
        _V17_RUNNER_SHA256,
    ):
        raise RuntimeError("TASK7_V18_V17_CENSUS_RUNNER_DRIFT")
    name = "_task7_v18_pinned_v17_census_runner"
    spec = importlib.util.spec_from_file_location(name, _V17_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK7_V18_V17_CENSUS_RUNNER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_adapted_v18_base() -> ModuleType:
    raw = _V17_RUNNER_PATH.read_bytes()
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), _V17_RUNNER_SHA256):
        raise RuntimeError("TASK7_V18_V17_CENSUS_RUNNER_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V18_V17_CENSUS_RUNNER_INVALID") from exc
    source = source.replace("V17", "V18").replace("v17", "v18")
    name = "_task7_v18_adapted_v17_census_base"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V17_RUNNER_PATH),
    )
    if spec is None:
        raise RuntimeError("TASK7_V18_V17_CENSUS_RUNNER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(Path(__file__).resolve())
    sys.modules[name] = module
    exec(compile(source, str(_V17_RUNNER_PATH), "exec"), module.__dict__)  # noqa: S102
    adapted = cast(Any, module)
    adapted._V18_MATERIALIZATION_SOURCE_MANIFEST = _V18_MATERIALIZATION_SOURCE_MANIFEST
    adapted._REPO_ROOT = _REPO_ROOT
    adapted._V18_SCAFFOLD_PATH = _V18_SCAFFOLD_PATH
    return module


_V17 = _load_pinned_v17()
_BASE = _load_adapted_v18_base()
_V17_ADAPT_V16_RUNNER_SOURCE = _BASE._adapt_v16_runner_source
_V18_BASE_LOAD_SCRIPT = _BASE._load_script


def _adapt_v18_harness_source(raw: bytes, candidate_raw: bytes) -> str:
    """Retarget the nested held-source coordinates to their exact v18 bytes."""

    source = cast(str, _V17_ADAPT_V16_RUNNER_SOURCE(raw, candidate_raw))
    for prior_sha256, v18_sha256 in _NESTED_ADAPTER_DIGEST_REPLACEMENTS:
        if source.count(prior_sha256) != 2 or v18_sha256 in source:
            raise RuntimeError("TASK7_V18_HARNESS_SOURCE_ADAPTATION_INVALID")
        source = source.replace(prior_sha256, v18_sha256)
    return source


_BASE._adapt_v16_runner_source = _adapt_v18_harness_source


def _load_immutable_v17_helper(path: Path, name: str) -> ModuleType:
    """Keep the unchanged receipt constructor at its hash-bound v17 path."""

    requested = Path(path)
    v18_receipt_helper = _REPO_ROOT / "scripts" / "task7_v18_receipt_guard_sql.py"
    if requested == v18_receipt_helper:
        requested = _REPO_ROOT / "scripts" / "task7_v17_receipt_guard_sql.py"
    return cast(ModuleType, _V18_BASE_LOAD_SCRIPT(requested, name))


_BASE._load_script = _load_immutable_v17_helper
CensusError = _BASE.CensusError

BASE_SCHEMA_REVISION: Final = cast(str, _BASE.BASE_SCHEMA_REVISION)
HARNESS_IDENTITY: Final = cast(str, _BASE.HARNESS_IDENTITY)
INSTALLATION_MODE: Final = cast(str, _BASE.INSTALLATION_MODE)
RESULT_SCHEMA: Final = cast(str, _BASE.RESULT_SCHEMA)
_CENSUS_SIGNING_DOMAIN: Final = cast(str, _BASE._CENSUS_SIGNING_DOMAIN)
_PRODUCTION_CONSTRUCTOR_SPECS: Final = cast(
    tuple[tuple[str, ...], ...],
    _BASE._PRODUCTION_CONSTRUCTOR_SPECS,
)
_DATABASE_EXECUTION_FIELDS: Final = cast(
    tuple[str, ...],
    _BASE._DATABASE_EXECUTION_FIELDS,
)
_canonical_bytes = _BASE._canonical_bytes
_decode_json = _BASE._decode_json
_digest = _BASE._digest
artifact_targets = _BASE.artifact_targets
artifact_trigger_contract = _BASE.artifact_trigger_contract
execution_expectations = _BASE.execution_expectations
materialization_source_manifest = _BASE.materialization_source_manifest
materialization_source_manifest_evidence = _BASE.materialization_source_manifest_evidence
materialization_source_manifest_sha256 = _BASE.materialization_source_manifest_sha256
receipt_equality_execution_plan = _BASE.receipt_equality_execution_plan
receipt_fixture_coordinates = _BASE.receipt_fixture_coordinates
validate_database_execution_projection = _BASE.validate_database_execution_projection
validate_receipt_equality_execution_proof = _BASE.validate_receipt_equality_execution_proof
validate_source_admission_execution_proof = _BASE.validate_source_admission_execution_proof
_database_execution = _BASE._database_execution
_validate_v18_candidate = _BASE._validate_v18_candidate


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _decode_utf8_hex(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) % 2:
        raise CensusError(_PROOF_ERROR)
    try:
        raw = bytes.fromhex(value)
        decoded = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise CensusError(_PROOF_ERROR) from exc
    if decoded.encode("utf-8") != raw or not decoded.strip():
        raise CensusError(_PROOF_ERROR)
    return decoded


def _sha256_utf8_hex(value: object) -> str:
    decoded = _decode_utf8_hex(value)
    return hashlib.sha256(decoded.encode("utf-8")).hexdigest()


def _downgrade_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]
        downgrade = acl["downgrade_authority"]
        function_signatures = downgrade["retained_function_signature_tuple"]
        function_restores = downgrade["retained_function_restore_sql_utf8_hex_tuple"]
        trigger_drops = downgrade["retained_trigger_drop_sql_utf8_hex_tuple"]
        trigger_restores = downgrade["retained_trigger_restore_sql_utf8_hex_tuple"]
        trigger_projection = acl["retained_0037_trigger_catalog_projection_tuple"]
        assertion = downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
    except (KeyError, TypeError) as exc:
        raise CensusError(_PROOF_ERROR) from exc
    if (
        not isinstance(downgrade, Mapping)
        or not isinstance(function_signatures, list)
        or not isinstance(function_restores, list)
        or not isinstance(trigger_drops, list)
        or not isinstance(trigger_restores, list)
        or not isinstance(trigger_projection, list)
        or len(function_signatures) != 8
        or len(function_restores) != 8
        or len(trigger_drops) != 9
        or len(trigger_restores) != 9
        or len(trigger_projection) != 9
        or len(set(function_signatures)) != 8
        or not isinstance(assertion, str)
    ):
        raise CensusError(_PROOF_ERROR)
    trigger_coordinates: list[tuple[str, str, str]] = []
    for row in trigger_projection:
        if (
            not isinstance(row, list)
            or len(row) != 12
            or not all(isinstance(value, str) and value for value in row[:3])
        ):
            raise CensusError(_PROOF_ERROR)
        trigger_coordinates.append((row[0], row[1], row[2]))
    if len(set(trigger_coordinates)) != 9:
        raise CensusError(_PROOF_ERROR)
    for value in (*function_restores, *trigger_drops, *trigger_restores, assertion):
        _decode_utf8_hex(value)
    return downgrade


def _expected_function_restore_rows(
    artifact: Mapping[str, Any],
) -> list[dict[str, Any]]:
    downgrade = _downgrade_authority(artifact)
    return [
        {
            "ordinal": ordinal,
            "signature": signature,
            "restore_sql_sha256": _sha256_utf8_hex(statement_hex),
        }
        for ordinal, (signature, statement_hex) in enumerate(
            zip(
                downgrade["retained_function_signature_tuple"],
                downgrade["retained_function_restore_sql_utf8_hex_tuple"],
                strict=True,
            )
        )
    ]


def _expected_trigger_restore_rows(
    artifact: Mapping[str, Any],
) -> list[dict[str, Any]]:
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    downgrade = _downgrade_authority(artifact)
    return [
        {
            "ordinal": ordinal,
            "function_signature": projection[0],
            "table_name": projection[1],
            "trigger_name": projection[2],
            "drop_sql_sha256": _sha256_utf8_hex(drop_hex),
            "restore_sql_sha256": _sha256_utf8_hex(restore_hex),
        }
        for ordinal, (drop_hex, restore_hex, projection) in enumerate(
            zip(
                downgrade["retained_trigger_drop_sql_utf8_hex_tuple"],
                downgrade["retained_trigger_restore_sql_utf8_hex_tuple"],
                acl["retained_0037_trigger_catalog_projection_tuple"],
                strict=True,
            )
        )
    ]


def _validate_catalog_projection(
    value: object,
    *,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or tuple(value) != (
        "function_tuple",
        "trigger_tuple",
    ):
        raise CensusError(_PROOF_ERROR)
    functions = value["function_tuple"]
    triggers = value["trigger_tuple"]
    if not isinstance(functions, list) or not isinstance(triggers, list):
        raise CensusError(_PROOF_ERROR)
    expected_functions = _expected_function_restore_rows(artifact)
    expected_triggers = _expected_trigger_restore_rows(artifact)
    if len(functions) != 8 or len(triggers) != 9:
        raise CensusError(_PROOF_ERROR)
    for expected, row in zip(expected_functions, functions, strict=True):
        if (
            not isinstance(row, Mapping)
            or tuple(row) != _FUNCTION_CATALOG_KEYS
            or row["ordinal"] != expected["ordinal"]
            or row["signature"] != expected["signature"]
            or not _is_sha256(row["definition_sha256"])
            or not isinstance(row["owner"], str)
            or not row["owner"]
            or (row["proconfig"] is not None and not isinstance(row["proconfig"], str))
            or type(row["security_definer"]) is not bool
            or row["volatility"] not in ("i", "s", "v")
            or (row["acl"] is not None and not isinstance(row["acl"], str))
        ):
            raise CensusError(_PROOF_ERROR)
    for expected, row in zip(expected_triggers, triggers, strict=True):
        if (
            not isinstance(row, Mapping)
            or tuple(row) != _TRIGGER_CATALOG_KEYS
            or row["ordinal"] != expected["ordinal"]
            or row["function_signature"] != expected["function_signature"]
            or row["table_name"] != expected["table_name"]
            or row["trigger_name"] != expected["trigger_name"]
            or not _is_sha256(row["definition_sha256"])
            or not isinstance(row["enabled"], str)
            or len(row["enabled"]) != 1
            or type(row["type_bits"]) is not int
            or type(row["constraint_oid"]) is not int
            or type(row["deferrable"]) is not bool
            or type(row["initially_deferred"]) is not bool
        ):
            raise CensusError(_PROOF_ERROR)
    return {
        "function_tuple": [dict(row) for row in functions],
        "trigger_tuple": [dict(row) for row in triggers],
    }


def validate_downgrade_retained_definition_execution_proof(
    proof: object,
    *,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed v18 rollback-only retained-definition proof."""

    try:
        if not isinstance(proof, Mapping) or tuple(proof) != _PROOF_KEYS:
            raise CensusError(_PROOF_ERROR)
        expected_functions = _expected_function_restore_rows(artifact)
        expected_triggers = _expected_trigger_restore_rows(artifact)
        catalog = _validate_catalog_projection(
            proof["catalog_before_projection"],
            artifact=artifact,
        )
        catalog_sha256 = _digest([_CATALOG_DIGEST_DOMAIN, catalog])
        supplied_execution = proof["execution_sha256"]
        derived_execution = _digest(
            [
                _PROOF_DIGEST_DOMAIN,
                {key: value for key, value in proof.items() if key != "execution_sha256"},
            ]
        )
        downgrade = _downgrade_authority(artifact)
        if (
            proof["schema_version"] != _PROOF_SCHEMA
            or proof["base_schema_revision"] != BASE_SCHEMA_REVISION
            or proof["transaction_mode"] != "ROLLBACK_ONLY"
            or proof["executed_as_setup_admin"] is not True
            or proof["function_restore_count"] != 8
            or proof["trigger_restore_count"] != 9
            or proof["function_restore_tuple"] != expected_functions
            or proof["trigger_restore_tuple"] != expected_triggers
            or proof["postrestore_assertion_sha256"]
            != _sha256_utf8_hex(downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"])
            or proof["catalog_before_sha256"] != catalog_sha256
            or proof["catalog_restored_sha256"] != catalog_sha256
            or proof["catalog_after_rollback_sha256"] != catalog_sha256
            or proof["restored_catalog_equals_before"] is not True
            or proof["rollback_catalog_equals_before"] is not True
            or proof["transaction_rolled_back"] is not True
            or not _is_sha256(supplied_execution)
            or supplied_execution != derived_execution
        ):
            raise CensusError(_PROOF_ERROR)
    except CensusError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError(_PROOF_ERROR) from exc
    return dict(proof)


def _function_catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> list[dict[str, Any]]:
    signatures = _downgrade_authority(artifact)["retained_function_signature_tuple"]
    rows: list[dict[str, Any]] = []
    statement = text(
        "SELECT pg_catalog.pg_get_functiondef(p.oid),owner.rolname,"
        "p.proconfig::text,p.prosecdef,p.provolatile,p.proacl::text "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner "
        "WHERE p.oid=pg_catalog.to_regprocedure(:signature)"
    )
    for ordinal, signature in enumerate(signatures):
        row = connection.execute(statement, {"signature": signature}).one_or_none()
        if row is None:
            raise CensusError(_PROOF_ERROR)
        definition, owner, proconfig, security_definer, volatility, acl = row
        rows.append(
            {
                "ordinal": ordinal,
                "signature": signature,
                "definition_sha256": hashlib.sha256(str(definition).encode("utf-8")).hexdigest(),
                "owner": str(owner),
                "proconfig": None if proconfig is None else str(proconfig),
                "security_definer": bool(security_definer),
                "volatility": str(volatility),
                "acl": None if acl is None else str(acl),
            }
        )
    return rows


def _trigger_catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> list[dict[str, Any]]:
    projection = artifact["sql_authority_registry"]["guard_function_acl_registry"][
        "retained_0037_trigger_catalog_projection_tuple"
    ]
    statement = text(
        "SELECT pg_catalog.pg_get_triggerdef(t.oid,false),t.tgenabled,t.tgtype,"
        "t.tgconstraint,t.tgdeferrable,t.tginitdeferred,"
        "pn.nspname||'.'||p.proname||'('||"
        "pg_catalog.pg_get_function_identity_arguments(p.oid)||')' "
        "FROM pg_catalog.pg_trigger t "
        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE n.nspname='public' AND c.relname=:table_name "
        "AND t.tgname=:trigger_name AND NOT t.tgisinternal"
    )
    rows: list[dict[str, Any]] = []
    for ordinal, expected in enumerate(projection):
        row = connection.execute(
            statement,
            {"table_name": expected[1], "trigger_name": expected[2]},
        ).one_or_none()
        if row is None:
            raise CensusError(_PROOF_ERROR)
        (
            definition,
            enabled,
            type_bits,
            constraint_oid,
            deferrable,
            initially_deferred,
            function_signature,
        ) = row
        if str(function_signature) != expected[0]:
            raise CensusError(_PROOF_ERROR)
        rows.append(
            {
                "ordinal": ordinal,
                "function_signature": str(function_signature),
                "table_name": str(expected[1]),
                "trigger_name": str(expected[2]),
                "definition_sha256": hashlib.sha256(str(definition).encode("utf-8")).hexdigest(),
                "enabled": str(enabled),
                "type_bits": int(type_bits),
                "constraint_oid": int(constraint_oid),
                "deferrable": bool(deferrable),
                "initially_deferred": bool(initially_deferred),
            }
        )
    return rows


def _catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "function_tuple": _function_catalog_projection(connection, artifact),
        "trigger_tuple": _trigger_catalog_projection(connection, artifact),
    }


def _execute_literal(connection: Connection, statement_hex: object) -> None:
    statement = _decode_utf8_hex(statement_hex)
    connection.exec_driver_sql(statement.replace("%", "%%"))


def execute_downgrade_retained_definition_execution_proof(
    *,
    admin_engine: Engine,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute exact v18 retained downgrade bytes in one rollback-only txn."""

    downgrade = _downgrade_authority(artifact)
    connection = admin_engine.connect()
    transaction = connection.begin()
    try:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != BASE_SCHEMA_REVISION:
            raise CensusError(_PROOF_ERROR)
        before = _catalog_projection(connection, artifact)
        for statement_hex in downgrade["retained_trigger_drop_sql_utf8_hex_tuple"]:
            _execute_literal(connection, statement_hex)
        for statement_hex in downgrade["retained_function_restore_sql_utf8_hex_tuple"]:
            _execute_literal(connection, statement_hex)
        for statement_hex in downgrade["retained_trigger_restore_sql_utf8_hex_tuple"]:
            _execute_literal(connection, statement_hex)
        _execute_literal(
            connection,
            downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"],
        )
        restored = _catalog_projection(connection, artifact)
        if restored != before:
            raise CensusError(_PROOF_ERROR)
    except Exception as exc:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        if isinstance(exc, CensusError):
            raise
        raise CensusError(_PROOF_ERROR) from exc
    transaction.rollback()
    connection.close()

    try:
        with admin_engine.connect() as after_connection:
            after = _catalog_projection(after_connection, artifact)
    except Exception as exc:
        if isinstance(exc, CensusError):
            raise
        raise CensusError(_PROOF_ERROR) from exc
    if after != before:
        raise CensusError(_PROOF_ERROR)

    catalog_sha256 = _digest([_CATALOG_DIGEST_DOMAIN, before])
    proof: dict[str, Any] = {
        "schema_version": _PROOF_SCHEMA,
        "base_schema_revision": BASE_SCHEMA_REVISION,
        "transaction_mode": "ROLLBACK_ONLY",
        "executed_as_setup_admin": True,
        "function_restore_count": 8,
        "trigger_restore_count": 9,
        "function_restore_tuple": _expected_function_restore_rows(artifact),
        "trigger_restore_tuple": _expected_trigger_restore_rows(artifact),
        "catalog_before_projection": before,
        "postrestore_assertion_sha256": _sha256_utf8_hex(
            downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
        ),
        "catalog_before_sha256": catalog_sha256,
        "catalog_restored_sha256": catalog_sha256,
        "catalog_after_rollback_sha256": catalog_sha256,
        "restored_catalog_equals_before": True,
        "rollback_catalog_equals_before": True,
        "transaction_rolled_back": True,
    }
    proof["execution_sha256"] = _digest([_PROOF_DIGEST_DOMAIN, proof])
    return validate_downgrade_retained_definition_execution_proof(
        proof,
        artifact=artifact,
    )


def _rehash_complete_execution(result: dict[str, Any]) -> None:
    result["complete_execution_sha256"] = _digest(
        [
            _COMPLETE_EXECUTION_DOMAIN,
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )


def validate_execution_contract(
    *,
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    _BASE.validate_execution_contract(result=result, artifact=artifact)
    validate_downgrade_retained_definition_execution_proof(
        result.get("downgrade_retained_definition_execution_proof"),
        artifact=artifact,
    )
    supplied = result.get("complete_execution_sha256")
    derived = _digest(
        [
            _COMPLETE_EXECUTION_DOMAIN,
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    if supplied != derived:
        raise CensusError("TASK7_V18_EXECUTION_INCOMPLETE")


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Run unchanged v17 cases plus the v18 retained-definition proof."""

    artifact_raw = Path(artifact_path).read_bytes()
    artifact = _decode_json(artifact_raw)
    _validate_v18_candidate(artifact)
    result = dict(
        _BASE.run_pre_fixture_census(
            artifact_path=Path(artifact_path),
            base_schema_revision=base_schema_revision,
            harness_identity=harness_identity,
            ordered_authority_target_tuple=ordered_authority_target_tuple,
            ordered_trigger_contract_coordinate_tuple=(ordered_trigger_contract_coordinate_tuple),
            production_constructor_spec_tuple=production_constructor_spec_tuple,
        )
    )
    execution_engine = _BASE._load_execution_engine(Path(artifact_path))
    with (
        execution_engine._cluster_safety_boundary(),
        execution_engine.disposable_postgres() as (database),
    ):
        execution_engine.migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        proof = execute_downgrade_retained_definition_execution_proof(
            admin_engine=database.admin_engine,
            artifact=artifact,
        )
    result["downgrade_retained_definition_execution_proof"] = proof
    _rehash_complete_execution(result)
    validate_execution_contract(result=result, artifact=artifact)
    return result


def build_and_verify_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    validate_execution_contract(result=result, artifact=artifact)
    return cast(
        dict[str, Any],
        _BASE.build_and_verify_signed_receipt(
            signer_id=signer_id,
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=result,
            private_key=private_key,
            trusted_public_key_raw=trusted_public_key_raw,
        ),
    )


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        _BASE.verify_signed_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        ),
    )
    artifact = _decode_json(artifact_raw)
    result = payload.get("census_result")
    if not isinstance(result, Mapping):
        raise CensusError("TASK7_V18_CENSUS_RECEIPT_INVALID")
    validate_execution_contract(result=result, artifact=artifact)
    return payload


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CensusError",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "RESULT_SCHEMA",
    "_CENSUS_SIGNING_DOMAIN",
    "_PRODUCTION_CONSTRUCTOR_SPECS",
    "_canonical_bytes",
    "_digest",
    "artifact_targets",
    "artifact_trigger_contract",
    "build_and_verify_signed_receipt",
    "execute_downgrade_retained_definition_execution_proof",
    "execution_expectations",
    "materialization_source_manifest",
    "materialization_source_manifest_evidence",
    "materialization_source_manifest_sha256",
    "receipt_equality_execution_plan",
    "receipt_fixture_coordinates",
    "run_pre_fixture_census",
    "validate_database_execution_projection",
    "validate_downgrade_retained_definition_execution_proof",
    "validate_execution_contract",
    "validate_receipt_equality_execution_proof",
    "validate_source_admission_execution_proof",
    "verify_signed_receipt",
]
