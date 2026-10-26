"""RED-first PostgreSQL proof for v18 retained downgrade definitions.

The frozen v17 candidate proved the guard census but never executed its eight
literal retained-function restore statements.  The first statement omits a
LANGUAGE clause, so PostgreSQL rejects it.  V18 must prove every retained
function and trigger restore before any fixture can be signed or frozen.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text
from tests.slice100_task2b._support import disposable_postgres, migrate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V17_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v17_current_guard_census.py"
_V18_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v18_current_guard_census.py"
_V17_RUNNER_SHA256 = "9f3585fb6b10a35cf1c73bd3d7f08c6c3aacb60c83d5b4d8e9e32ee9c0ed539c"

_PROOF_SCHEMA = "task7-v18-downgrade-retained-definition-execution-proof-v1"
_PROOF_ERROR = "TASK7_V18_RETAINED_RESTORE_EXECUTION_INVALID"
_V18_CANDIDATE_SHA256 = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_V18_FROZEN_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_V17_ADAPTED_V11_SHA256 = "b3178fb8e9d89409a29db1d42a209ffea49d62566f9623b780224ccfe60915c5"
_V17_ADAPTED_V9_SHA256 = "6bd5ec330124325bc714c9f377031bd0d74faa9a463b7ae345c67797969b7c36"
_V18_ADAPTED_V11_SHA256 = "74aa26548edff669726c729ede2e339e3d6a162142ee63878f94cddc476c5317"
_V18_ADAPTED_V9_SHA256 = "a0e19f70a497cf75ac970c3d035326abd7ef5284f4a6a1a8d81dc12443929dac"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V18_RETAINED_PROOF_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _v17() -> dict[str, Any]:
    value = json.loads(_V17_ARTIFACT.read_bytes())
    assert isinstance(value, dict)
    return value


def _v18_labeled_v17() -> dict[str, Any]:
    """Preserve v17's bad SQL while exercising the v18 proof surface."""

    value = copy.deepcopy(_v17())
    value["metadata"]["artifact_schema"] = "full-strict-v18-reviewed-preimage-v1"
    value["metadata"]["pipeline_contract_version"] = "full-strict-v18"
    value["metadata"]["append_frozen"] = False
    return value


def _v18_candidate() -> tuple[bytes, dict[str, Any]]:
    frozen_raw = _V18_ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _V18_FROZEN_SHA256
    value = json.loads(frozen_raw)
    assert isinstance(value, dict) and value["metadata"]["append_frozen"] is True
    value["metadata"]["append_frozen"] = False
    raw = (json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == _V18_CANDIDATE_SHA256
    return raw, value


def _valid_closed_proof(runner: ModuleType, artifact: dict[str, Any]) -> dict[str, Any]:
    downgrade = artifact["sql_authority_registry"]["guard_function_acl_registry"][
        "downgrade_authority"
    ]
    function_rows = [
        {
            "ordinal": ordinal,
            "signature": signature,
            "restore_sql_sha256": runner._sha256_utf8_hex(statement_hex),
        }
        for ordinal, (signature, statement_hex) in enumerate(
            zip(
                downgrade["retained_function_signature_tuple"],
                downgrade["retained_function_restore_sql_utf8_hex_tuple"],
                strict=True,
            )
        )
    ]
    trigger_projection = artifact["sql_authority_registry"]["guard_function_acl_registry"][
        "retained_0037_trigger_catalog_projection_tuple"
    ]
    trigger_rows = [
        {
            "ordinal": ordinal,
            "function_signature": projection[0],
            "table_name": projection[1],
            "trigger_name": projection[2],
            "drop_sql_sha256": runner._sha256_utf8_hex(drop_hex),
            "restore_sql_sha256": runner._sha256_utf8_hex(restore_hex),
        }
        for ordinal, (drop_hex, restore_hex, projection) in enumerate(
            zip(
                downgrade["retained_trigger_drop_sql_utf8_hex_tuple"],
                downgrade["retained_trigger_restore_sql_utf8_hex_tuple"],
                trigger_projection,
                strict=True,
            )
        )
    ]
    catalog_before = {
        "function_tuple": [
            {
                "ordinal": ordinal,
                "signature": signature,
                "definition_sha256": f"{ordinal + 1:064x}",
                "owner": "setup_admin",
                "proconfig": None,
                "security_definer": False,
                "volatility": "v",
                "acl": None,
            }
            for ordinal, signature in enumerate(downgrade["retained_function_signature_tuple"])
        ],
        "trigger_tuple": [
            {
                "ordinal": ordinal,
                "function_signature": projection[0],
                "table_name": projection[1],
                "trigger_name": projection[2],
                "definition_sha256": f"{ordinal + 9:064x}",
                "enabled": "O",
                "type_bits": 31,
                "constraint_oid": 0,
                "deferrable": False,
                "initially_deferred": False,
            }
            for ordinal, projection in enumerate(trigger_projection)
        ],
    }
    before = runner._digest(["task7-v18-retained-catalog-projection-v1", catalog_before])
    proof: dict[str, Any] = {
        "schema_version": _PROOF_SCHEMA,
        "base_schema_revision": "0037",
        "transaction_mode": "ROLLBACK_ONLY",
        "executed_as_setup_admin": True,
        "function_restore_count": 8,
        "trigger_restore_count": 9,
        "function_restore_tuple": function_rows,
        "trigger_restore_tuple": trigger_rows,
        "catalog_before_projection": catalog_before,
        "postrestore_assertion_sha256": runner._sha256_utf8_hex(
            downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
        ),
        "catalog_before_sha256": before,
        "catalog_restored_sha256": before,
        "catalog_after_rollback_sha256": before,
        "restored_catalog_equals_before": True,
        "rollback_catalog_equals_before": True,
        "transaction_rolled_back": True,
    }
    proof["execution_sha256"] = runner._digest(
        [_PROOF_SCHEMA, proof],
    )
    return proof


def test_v18_runner_is_a_hash_pinned_adapter_over_immutable_v17() -> None:
    runner = _load(_V18_RUNNER, "_task7_v18_retained_adapter_red")

    assert runner._V17_RUNNER_PATH == _V17_RUNNER
    assert runner._V17_RUNNER_SHA256 == _V17_RUNNER_SHA256
    assert runner.BASE_SCHEMA_REVISION == "0037"


def test_v18_materialization_source_manifest_pins_the_complete_adapter_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V18_RUNNER, "_task7_v18_source_manifest_red")
    candidate_raw, _artifact = _v18_candidate()
    for relative, _length, _sha256, _role in runner._V18_MATERIALIZATION_SOURCE_MANIFEST:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            candidate_raw
            if relative == "contracts/full_strict_v18_reviewed_preimage_v1.json"
            else (_REPO_ROOT / relative).read_bytes()
        )
    monkeypatch.setattr(runner._BASE, "_REPO_ROOT", tmp_path)
    rows = runner.materialization_source_manifest()

    assert rows == runner._V18_MATERIALIZATION_SOURCE_MANIFEST
    assert tuple(row[0] for row in rows) == (
        "contracts/full_strict_v18_reviewed_preimage_v1.json",
        "scripts/_task7_v18_census_scaffold.py",
        "scripts/materialize_full_strict_v18_preimage.py",
        "scripts/generate_task7_v18_contract_fixture.py",
        "scripts/task7_v17_receipt_guard_sql.py",
        "scripts/task7_v17_source_projection_sql.py",
    )
    for path, length, sha256, _role in rows:
        observed = (
            candidate_raw
            if path == "contracts/full_strict_v18_reviewed_preimage_v1.json"
            else (_REPO_ROOT / path).read_bytes()
        )
        assert len(observed) == length
        assert hashlib.sha256(observed).hexdigest() == sha256
    assert runner.materialization_source_manifest_evidence() == [list(row) for row in rows]
    assert len(runner.materialization_source_manifest_sha256()) == 64


def test_v18_proof_validator_is_closed_and_binds_every_literal() -> None:
    runner = _load(_V18_RUNNER, "_task7_v18_retained_validator_red")
    artifact = _v18_labeled_v17()
    proof = _valid_closed_proof(runner, artifact)

    assert (
        runner.validate_downgrade_retained_definition_execution_proof(
            proof,
            artifact=artifact,
        )
        == proof
    )

    mutations = (
        lambda value: value.pop("transaction_rolled_back"),
        lambda value: value["function_restore_tuple"][0].__setitem__(
            "restore_sql_sha256", "0" * 64
        ),
        lambda value: value["trigger_restore_tuple"][0].__setitem__("drop_sql_sha256", "0" * 64),
        lambda value: value.__setitem__("catalog_restored_sha256", "2" * 64),
        lambda value: value.__setitem__("restored_catalog_equals_before", False),
        lambda value: value.__setitem__("execution_sha256", "0" * 64),
    )
    for mutate in mutations:
        invalid = copy.deepcopy(proof)
        mutate(invalid)
        with pytest.raises(runner.CensusError, match=_PROOF_ERROR):
            runner.validate_downgrade_retained_definition_execution_proof(
                invalid,
                artifact=artifact,
            )


def test_v18_execution_contract_requires_retained_restore_proof_and_digest_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V18_RUNNER, "_task7_v18_retained_contract_red")
    artifact = _v18_labeled_v17()
    proof = _valid_closed_proof(runner, artifact)
    monkeypatch.setattr(
        runner._BASE,
        "validate_execution_contract",
        lambda *, result, artifact: None,
    )
    result = {"downgrade_retained_definition_execution_proof": proof}
    runner._rehash_complete_execution(result)

    runner.validate_execution_contract(result=result, artifact=artifact)
    assert tuple(runner._DATABASE_EXECUTION_FIELDS) == tuple(
        _load(_V17_RUNNER, "_task7_v17_projection_control")._DATABASE_EXECUTION_FIELDS
    )

    for mutation in ("missing", "tampered"):
        invalid = copy.deepcopy(result)
        if mutation == "missing":
            invalid.pop("downgrade_retained_definition_execution_proof")
        else:
            invalid["downgrade_retained_definition_execution_proof"][
                "catalog_after_rollback_sha256"
            ] = "3" * 64
        runner._rehash_complete_execution(invalid)
        with pytest.raises(runner.CensusError, match=_PROOF_ERROR):
            runner.validate_execution_contract(result=invalid, artifact=artifact)


def test_v18_full_harness_source_closure_uses_v18_nested_adapter_digests(
    tmp_path: Path,
) -> None:
    """Exercise the production composition root that the signed census invokes."""

    runner = _load(_V18_RUNNER, "_task7_v18_full_harness_source_red")
    artifact_raw, _artifact = _v18_candidate()
    source = runner._BASE._adapt_v16_runner_source(
        runner._BASE._V16_RUNNER_PATH.read_bytes(),
        artifact_raw,
    )

    assert _V17_ADAPTED_V11_SHA256 not in source
    assert _V17_ADAPTED_V9_SHA256 not in source
    assert source.count(_V18_ADAPTED_V11_SHA256) == 2
    assert source.count(_V18_ADAPTED_V9_SHA256) == 2

    candidate_path = tmp_path / "full_strict_v18_candidate.json"
    candidate_path.write_bytes(artifact_raw)
    try:
        engine = runner._BASE._load_execution_engine(candidate_path)
        try:
            coordinates = engine.HARNESS_SOURCE_EXECUTION_COORDINATES
            assert any(row[-1] == _V18_ADAPTED_V11_SHA256 for row in coordinates)
            assert any(row[-1] == _V18_ADAPTED_V9_SHA256 for row in coordinates)
        finally:
            engine._HARNESS_SOURCE_STORE.close()
    finally:
        candidate_path.unlink(missing_ok=True)


def test_frozen_v17_missing_language_is_rejected_by_real_postgres_proof() -> None:
    runner = _load(_V18_RUNNER, "_task7_v18_retained_postgres_red")
    artifact = _v18_labeled_v17()
    downgrade = artifact["sql_authority_registry"]["guard_function_acl_registry"][
        "downgrade_authority"
    ]
    first_restore = bytes.fromhex(
        downgrade["retained_function_restore_sql_utf8_hex_tuple"][0]
    ).decode("utf-8")
    assert "LANGUAGE" not in first_restore.upper()
    assert (
        hashlib.sha256(first_restore.encode("utf-8")).hexdigest()
        == "4d48af69c18fb948c5200b92998063be5a2b34305c4af16201e1d5d2c28f8484"
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            before = runner._catalog_projection(connection, artifact)
            live_definition = connection.execute(
                text(
                    "SELECT pg_catalog.pg_get_functiondef("
                    "pg_catalog.to_regprocedure("
                    "'public.idis_prevent_strict_preflight_proof_mutation()'))"
                )
            ).scalar_one()
        assert (
            hashlib.sha256(str(live_definition).encode("utf-8")).hexdigest()
            == "c589089618af8d486acb41f046f1bd6969cff9279e6abfd4bdaa4b006752655e"
        )
        with pytest.raises(runner.CensusError, match=_PROOF_ERROR) as captured:
            runner.execute_downgrade_retained_definition_execution_proof(
                admin_engine=database.admin_engine,
                artifact=artifact,
            )
        with database.admin_engine.connect() as connection:
            after = runner._catalog_projection(connection, artifact)

    assert captured.value.__cause__ is not None
    assert "no language specified" in str(captured.value.__cause__).casefold()
    assert after == before


def test_unfrozen_v18_executes_all_retained_definitions_and_restores_catalog() -> None:
    runner = _load(_V18_RUNNER, "_task7_v18_retained_postgres_green")
    _raw, artifact = _v18_candidate()
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v18-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v18",
        "append_frozen": False,
    }

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            before = runner._catalog_projection(connection, artifact)
        proof = runner.execute_downgrade_retained_definition_execution_proof(
            admin_engine=database.admin_engine,
            artifact=artifact,
        )
        with database.admin_engine.connect() as connection:
            after = runner._catalog_projection(connection, artifact)

    assert proof["function_restore_count"] == 8
    assert proof["trigger_restore_count"] == 9
    assert (
        proof["function_restore_tuple"][0]["restore_sql_sha256"]
        == "c589089618af8d486acb41f046f1bd6969cff9279e6abfd4bdaa4b006752655e"
    )
    assert proof["catalog_before_projection"] == before
    assert proof["catalog_before_sha256"] == proof["catalog_restored_sha256"]
    assert proof["catalog_before_sha256"] == proof["catalog_after_rollback_sha256"]
    assert proof["transaction_rolled_back"] is True
    assert after == before
