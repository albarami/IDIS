"""RED-first contracts for the full-strict-v17 census/admission scaffolding."""

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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V17_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v17_current_guard_census.py"
_V17_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v17_contract_fixture.py"
_V17_SIGNED_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v17_signed_census.py"
_V17_SCAFFOLD = _REPO_ROOT / "scripts" / "_task7_v17_census_scaffold.py"
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V17_SOURCE_HELPER = _REPO_ROOT / "scripts" / "task7_v17_source_projection_sql.py"

_SOURCE_KINDS = (
    "PACKAGE",
    "PACKAGE_FILE",
    "ARTIFACT",
    "DOCUMENT",
    "PARSE_ATTEMPT",
    "SPAN",
)
_UNFROZEN_V17_SHA256 = "ae6d42df18d236fad3730be9829fe66b19549e7902c06ea9c62ad7c6d250a5e7"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V17_CENSUS_SCAFFOLD_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _v16_artifact() -> dict[str, Any]:
    value = json.loads(_V16_ARTIFACT.read_bytes())
    assert isinstance(value, dict)
    return value


def _unfrozen_v17() -> tuple[bytes, dict[str, Any]]:
    """Reverse only the append-freeze bit for post-freeze source-set tests."""

    value = json.loads(_V17_ARTIFACT.read_bytes())
    assert isinstance(value, dict)
    assert value["metadata"]["append_frozen"] is True
    candidate = copy.deepcopy(value)
    candidate["metadata"]["append_frozen"] = False
    raw = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == _UNFROZEN_V17_SHA256
    return raw, candidate


def _stage_prefreeze_source_set(
    runner: ModuleType,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Recreate the signed source closure without mutating the frozen artifact."""

    candidate_raw, _candidate = _unfrozen_v17()
    for relative_path, _length, _sha256, _role in runner._V17_MATERIALIZATION_SOURCE_MANIFEST:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = _REPO_ROOT / relative_path
        destination.write_bytes(
            candidate_raw
            if relative_path == "contracts/full_strict_v17_reviewed_preimage_v1.json"
            else source.read_bytes()
        )
    monkeypatch.setattr(runner, "_REPO_ROOT", tmp_path)
    return tmp_path / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"


def _synthetic_v17_delta() -> dict[str, Any]:
    """Apply only the reviewed event-coordinate delta to immutable v16 bytes."""

    artifact = copy.deepcopy(_v16_artifact())
    artifact["metadata"]["artifact_schema"] = "full-strict-v17-reviewed-preimage-v1"
    artifact["metadata"]["pipeline_contract_version"] = "full-strict-v17"
    function_rows = artifact["sql_authority_registry"]["guard_function_acl_registry"][
        "function_rows"
    ]
    guard = next(
        row
        for row in function_rows
        if row["authority_id"] == "current-source-bound-v1"
        and row["function_signature"] == "public.idis_task7_current_source_bound_v1()"
    )
    package_file_update = next(
        list(row)
        for row in guard["target_event_coordinate_tuple"]
        if row[0] == "data_room_package_files" and row[3] == "UPDATE"
    )
    package_file_insert = [*package_file_update]
    package_file_insert[3] = "INSERT"
    assert package_file_insert not in guard["target_event_coordinate_tuple"]
    insertion_index = next(
        index
        for index, row in enumerate(guard["target_event_coordinate_tuple"])
        if row[0] == "data_room_package_files"
    )
    guard["target_event_coordinate_tuple"].insert(insertion_index, package_file_insert)
    return artifact


def _semantic_proof() -> dict[str, Any]:
    return {
        "schema_version": "task7-v17-source-admission-execution-proof-v1",
        "executed_as_application_role": True,
        "projection_proof_tuple": [
            {
                "object_kind": kind,
                "legal_projection_accepted": True,
                "constructor_roundtrip_equal": True,
                "expected_projection_sha256": hashlib.sha256(
                    f"expected:{kind}".encode()
                ).hexdigest(),
                "observed_projection_sha256": hashlib.sha256(
                    f"expected:{kind}".encode()
                ).hexdigest(),
                "noncanonical_projection_rejected": True,
                "rejection_marker": "TASK7_SOURCE_TERMINAL_STATE_INVALID",
            }
            for kind in _SOURCE_KINDS
        ],
        "backfill_rejection_proof_tuple": [
            {
                "object_kind": kind,
                "write_origin": "BACKFILL",
                "rejected": True,
                "rejection_marker": "WORKER_LEASE_FENCED",
            }
            for kind in _SOURCE_KINDS
        ],
        "native_parse_attempt_backfill_rejection": {
            "write_origin": "BACKFILL",
            "rejected": True,
            "rejection_marker": "WORKER_LEASE_FENCED",
            "zero_mutation": True,
        },
        "package_file_insert_proof": {
            "open_unbound_parent_accepted": True,
            "sealed_bound_parent_rejected": True,
            "rejection_marker": "SOURCE_TREE_MUTATED",
            "sealed_rejection_zero_mutation": True,
        },
    }


def test_v17_scaffolding_files_are_new_and_importable() -> None:
    for ordinal, path in enumerate(
        (_V17_RUNNER, _V17_GENERATOR, _V17_SIGNED_RUNNER, _V17_SCAFFOLD),
        start=1,
    ):
        _load(path, f"_task7_v17_scaffold_red_{ordinal}")


def test_event_count_is_derived_from_the_artifact_narrow_delta() -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_event_projection_red")
    v16 = _v16_artifact()
    v17 = _synthetic_v17_delta()

    old_contract = runner.artifact_trigger_contract(v16)
    new_contract = runner.artifact_trigger_contract(v17)
    added = tuple(row for row in new_contract if row not in set(old_contract))
    removed = tuple(row for row in old_contract if row not in set(new_contract))

    assert len(old_contract) == 192
    assert len(new_contract) == 193
    assert removed == ()
    assert len(added) == 1
    assert added[0][:5] == (
        "current-source-bound-v1",
        "data_room_package_files",
        "trg_task7_data_room_package_files_source_bound",
        "BEFORE",
        "INSERT",
    )
    assert len(runner.artifact_targets(v17)) == 73
    assert runner.execution_expectations(v17) == {
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 193,
        "application_case_execution_count": 219,
        "constructor_schema_proof_count": 4,
    }


def test_v17_semantic_execution_proof_requires_every_approved_branch() -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_semantic_proof_red")
    proof = _semantic_proof()

    assert runner.validate_source_admission_execution_proof(proof) == proof

    for kind in _SOURCE_KINDS:
        incomplete = copy.deepcopy(proof)
        incomplete["projection_proof_tuple"] = [
            row for row in incomplete["projection_proof_tuple"] if row["object_kind"] != kind
        ]
        with pytest.raises(runner.CensusError, match="TASK7_V17_SOURCE_PROOF_INVALID"):
            runner.validate_source_admission_execution_proof(incomplete)

        incomplete = copy.deepcopy(proof)
        incomplete["backfill_rejection_proof_tuple"] = [
            row
            for row in incomplete["backfill_rejection_proof_tuple"]
            if row["object_kind"] != kind
        ]
        with pytest.raises(runner.CensusError, match="TASK7_V17_SOURCE_PROOF_INVALID"):
            runner.validate_source_admission_execution_proof(incomplete)

    incomplete = copy.deepcopy(proof)
    incomplete["package_file_insert_proof"]["sealed_rejection_zero_mutation"] = False
    with pytest.raises(runner.CensusError, match="TASK7_V17_SOURCE_PROOF_INVALID"):
        runner.validate_source_admission_execution_proof(incomplete)

    incomplete = copy.deepcopy(proof)
    incomplete["native_parse_attempt_backfill_rejection"]["zero_mutation"] = False
    with pytest.raises(runner.CensusError, match="TASK7_V17_SOURCE_PROOF_INVALID"):
        runner.validate_source_admission_execution_proof(incomplete)


def test_v17_result_validation_derives_counts_and_requires_semantic_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.slice100_task7.test_full_strict_v17_receipt_execution_proof_red import (
        _valid_proof,
    )

    runner = _load(_V17_RUNNER, "_task7_v17_result_validation_red")
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    artifact = _synthetic_v17_delta()
    result = {
        **runner.execution_expectations(artifact),
        "source_admission_execution_proof": _semantic_proof(),
        "receipt_equality_execution_proof": _valid_proof(runner, artifact),
        "materialization_source_manifest_tuple": (
            runner.materialization_source_manifest_evidence()
        ),
        "materialization_source_manifest_sha256": (runner.materialization_source_manifest_sha256()),
    }

    runner.validate_execution_contract(result=result, artifact=artifact)

    masked = dict(result)
    masked["current_guard_target_event_count"] = 192
    with pytest.raises(runner.CensusError, match="TASK7_V17_EXECUTION_INCOMPLETE"):
        runner.validate_execution_contract(result=masked, artifact=artifact)

    masked = dict(result)
    masked.pop("source_admission_execution_proof")
    with pytest.raises(runner.CensusError, match="TASK7_V17_SOURCE_PROOF_INVALID"):
        runner.validate_execution_contract(result=masked, artifact=artifact)


def test_v17_generator_refuses_freeze_without_a_verified_v17_census() -> None:
    generator = _load(_V17_GENERATOR, "_task7_v17_generator_gate_red")
    artifact = _synthetic_v17_delta()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V17_POSTGRES_CENSUS_RECEIPT_REQUIRED",
    ):
        generator.authorize_fixture_publication(
            candidate_artifact=artifact,
            verified_census_payload=None,
        )


def test_real_v17_candidate_derives_helper_and_acl_growth_without_target_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_real_candidate_runner_red")
    generator = _load(_V17_GENERATOR, "_task7_v17_real_candidate_generator_red")
    candidate_path = _stage_prefreeze_source_set(
        runner,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    monkeypatch.setattr(generator, "_runner", lambda: runner)
    raw, artifact = _unfrozen_v17()

    assert len(runner._current_function_rows(artifact)) == 24
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    relation_columns = acl["guard_owner_role"]["select_relation_column_tuple"]
    assert len(relation_columns) == 36
    flattened_columns = tuple(
        (str(relation), str(column)) for relation, columns in relation_columns for column in columns
    )
    assert flattened_columns
    assert len(flattened_columns) == len(set(flattened_columns))
    assert runner.execution_expectations(artifact) == {
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 193,
        "application_case_execution_count": 219,
        "constructor_schema_proof_count": 4,
    }
    assert generator.validate_candidate_artifact(artifact, raw=raw) == artifact
    engine = runner._load_execution_engine(candidate_path)
    assert len(engine._artifact_trigger_contract(artifact)) == 193
    assert len(engine.current_function_rows(artifact)) == 24


def test_v17_generator_rederives_the_published_freeze_without_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_derivation_runner_red")
    generator = _load(_V17_GENERATOR, "_task7_v17_derivation_only_red")
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setattr(generator, "_runner", lambda: runner)
    raw, candidate = _unfrozen_v17()
    prospective = generator.derive_prospective_frozen_bytes(raw)
    frozen = json.loads(prospective)

    assert frozen["metadata"]["append_frozen"] is True
    assert candidate["metadata"]["append_frozen"] is False
    assert prospective == _V17_ARTIFACT.read_bytes()
    fixture = generator.derive_contract_bytes(prospective)
    assert fixture["fixture_schema"] == "full-strict-v17-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v17"
    assert fixture["artifact_sha256"] == hashlib.sha256(prospective).hexdigest()
    assert len(fixture["section_hashes"]) == len(frozen)
    assert len(fixture["FULL_STRICT_V17_CONTRACT_HASH"]) == 64
    published_fixture_path = (
        _REPO_ROOT
        / "tests"
        / "slice100_task7"
        / "fixtures"
        / "full_strict_v17_contract_hash_v1.json"
    )
    published_fixture = json.loads(published_fixture_path.read_bytes())
    assert {key: published_fixture[key] for key in fixture} == fixture


def test_v17_scaffold_escapes_percent_only_at_the_dbapi_transport_boundary() -> None:
    scaffold = _load(_V17_SCAFFOLD, "_task7_v17_percent_transport_red")

    class RecordingConnection:
        def __init__(self) -> None:
            self.statement: str | None = None

        def exec_driver_sql(self, statement: str) -> str:
            self.statement = statement
            return "executed"

    connection = RecordingConnection()
    transport = scaffold._LiteralSqlTransport(connection)
    result = transport.exec_driver_sql(
        "SELECT (octet_length(payload) % 64), value LIKE 'compliant:%'"
    )

    assert result == "executed"
    assert connection.statement == (
        "SELECT (octet_length(payload) %% 64), value LIKE 'compliant:%%'"
    )


def test_v17_source_manifest_binds_both_sql_helpers_and_candidate_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_source_manifest_red")
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    manifest = runner.materialization_source_manifest()
    by_path = {row[0]: row for row in manifest}

    assert set(by_path) == {
        "contracts/full_strict_v17_reviewed_preimage_v1.json",
        "scripts/_task7_v17_census_scaffold.py",
        "scripts/materialize_full_strict_v17_preimage.py",
        "scripts/task7_v17_receipt_guard_sql.py",
        "scripts/task7_v17_source_projection_sql.py",
    }
    for relative_path, byte_length, sha256, _role in manifest:
        raw = (tmp_path / relative_path).read_bytes()
        assert len(raw) == byte_length
        assert hashlib.sha256(raw).hexdigest() == sha256
    assert len(runner.materialization_source_manifest_sha256()) == 64


def test_v17_source_manifest_evidence_is_canonical_json_not_python_tuples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_source_manifest_json_red")
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    evidence = runner.materialization_source_manifest_evidence()

    assert isinstance(evidence, list)
    assert evidence
    assert all(isinstance(row, list) and len(row) == 4 for row in evidence)
    assert runner._digest(["task7-v17-source-manifest-evidence-v1", evidence])


def test_v17_receipt_fixture_is_registry_derived_and_subrole_complete() -> None:
    import json

    runner = _load(_V17_RUNNER, "_task7_v17_receipt_fixture_red")
    artifact = json.loads(_V17_ARTIFACT.read_bytes())
    ordinal, coordinates = runner.receipt_fixture_coordinates(artifact)
    semantic_row = next(
        row for row in artifact["semantic_step_registry"]["rows"] if row[1] == "EXTRACT"
    )

    assert ordinal == semantic_row[0]
    assert coordinates == (
        (
            "EXTRACT",
            "__step__",
            "EXTRACT_RECEIPT_V1",
            0,
            "2",
            semantic_row[3],
        ),
        (
            "EXTRACT",
            "rag_context",
            "EXTRACT_RAG_CONTEXT_RECEIPT_V1",
            0,
            "2",
            semantic_row[3],
        ),
    )


def test_v17_source_update_fence_is_coordinate_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_V17_RUNNER, "_task7_v17_source_rejection_coordinate_red")
    candidate_path = _stage_prefreeze_source_set(
        runner,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    engine = runner._load_execution_engine(candidate_path)

    assert engine._expected_rejection(
        authority_id="current-source-exact-one-v1",
        table_name="source_lineage_objects",
        case_kind="ILLEGAL",
    ) == ("40001", "WORKER_LEASE_FENCED")
    assert engine._expected_rejection(
        authority_id="current-source-exact-one-v1",
        table_name="documents",
        case_kind="ILLEGAL",
    ) == ("23514", "TASK7_SOURCE_TERMINAL_STATE_INVALID")
    assert engine._expected_rejection(
        authority_id="current-source-exact-one-v1",
        table_name="source_lineage_objects",
        case_kind="DIRECT_SQL",
    ) == ("23514", "TASK7_SOURCE_TERMINAL_STATE_INVALID")


def test_v17_source_projection_timestamps_are_utc_under_non_utc_census_session() -> None:
    helper = _load(_V17_SOURCE_HELPER, "_task7_v17_source_timezone_red")
    v16 = _v16_artifact()
    source_row = next(
        row
        for row in v16["sql_authority_registry"]["rows"]
        if row["authority_id"] == "current-source-exact-one-v1"
    )
    body = "\n".join(helper.build_source_exact_one_statements(source_row))
    runner_source = _V17_RUNNER.read_text(encoding="utf-8")

    assert body.count("AT TIME ZONE 'UTC'") == 3
    assert "SET LOCAL TIME ZONE 'Asia/Qatar'" in runner_source


def test_source_proof_restores_original_tenant_context_before_backfill() -> None:
    import inspect

    runner = _load(_V17_RUNNER, "_task7_v17_source_context_restore_red")

    class RecordingConnection:
        def __init__(self) -> None:
            self.statement = ""
            self.parameters: dict[str, str] = {}

        def execute(
            self,
            statement: object,
            parameters: dict[str, str],
        ) -> None:
            self.statement = str(statement)
            self.parameters = parameters

    connection = RecordingConnection()
    tenant_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    runner._restore_source_proof_tenant_context(connection, tenant_id)

    assert "pg_catalog.set_config('idis.tenant_id',:tenant_id,true)" in (
        " ".join(connection.statement.split())
    )
    assert connection.parameters == {"tenant_id": tenant_id}
    source = inspect.getsource(runner._execute_source_admission_proofs)
    assert (
        source.index("reject_fresh_noncanonical_projection(root.object_kind)")
        < (source.index("_restore_source_proof_tenant_context"))
        < source.index("backfill_rows")
    )
