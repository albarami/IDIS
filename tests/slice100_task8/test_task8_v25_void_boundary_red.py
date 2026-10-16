"""RED-first boundary for the permanently void full-strict-v25 candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.slice100_task8 import _task8_void_contract_hash_evidence as void_evidence
from tests.slice100_task8 import conftest as task8_conftest
from tests.slice100_task8.test_task8_historical_candidate_collection_boundary import (
    _write_successor_pair as _write_complete_successor_pair,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK8_ROOT = _REPO_ROOT / "tests" / "slice100_task8"
_V25_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v25_reviewed_preimage_v1.json"
_V25_FIXTURE = _TASK8_ROOT / "fixtures" / "full_strict_v25_contract_hash_v1.json"
_RECONCILIATION_LOG = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task8" / "task8_contract_reconciliation_log.jsonl"
)

_V25_CONSTANT_NAMES = (
    "VOID_V25_ARTIFACT_SHA256",
    "VOID_V25_FIXTURE_SHA256",
    "VOID_V25_CANDIDATE_CONTRACT_SHA256",
    "VOID_V25_TASK8_CONTRACT_SHA256",
    "VOID_V25_STRUCTURAL_ADMISSION_SHA256",
    "VOID_V25_PRODUCER_MANIFEST_SHA256",
    "VOID_V25_EXTERNAL_MANIFEST_SHA256",
    "VOID_V25_IDENTITY_GRAPH_SHA256",
    "VOID_V25_CONSERVATION_SHA256",
    "VOID_V25_POLICY_BINDING_SHA256",
    "VOID_V25_REQUIREMENT_LITERAL_SHA256",
    "VOID_V25_EQUATION_AUTHORITY_SHA256",
    "VOID_V25_EQUATION_BODY_SHA256",
)
_V25_IDENTIFIERS = (
    "008dcf51b015e8191c93c607832b3e44d8e364f0b2902b05e6fc44bacebf86a1",
    "3eab3b34412e9ba4f83584ce19c999c782ab9f85444c29e33379d22678a4ee0f",
    "48cca9fa4a50a1a456493e36c696ddb888669fa023d25426bacc19b8fa0620ed",
    "bad09ef3a9ce4773643ecb60b8c60989c47327f6a0158d5cf9a9ca8081d4c398",
    "0d11924cbdaa472d131b912dd0ee09195a2a41cbce0a872972c424a3a204b7d1",
    "67a838da50a8f1d81af68c3fad800bd42151f687d5f73e86bfbb9490f755fd64",
    "d5caf7232507ee99d051e355bd994e008162d8770beab117cb47926f10a0d4fa",
    "a8f27747ec5b13d0962f2202b21a37718f59c3a0e44a3f1672f6071ce2aa9bd0",
    "a16b12a71640b40bc472deb23d6d4b3318ad22d35ce471c957099b8a9962dc60",
    "8915fc53da0eda52e256ad8da6fcfbe614cc066eea57c60a85f69bfb3bd357a2",
    "17c43749db2c3c4282868ccf7689f6eb9b113b42f97612962c487f0f45649c94",
    "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5",
    "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7",
)
_V25_CANDIDATE_IDENTIFIERS = _V25_IDENTIFIERS[:-2]
_V25_INHERITED_SEMANTIC_IDENTIFIERS = _V25_IDENTIFIERS[-2:]
_V25_FROZEN_FILE_SHA256_BY_PATH = {
    "scripts/materialize_full_strict_v25_preimage.py": (
        "4d4d9104df9900324603451bd33f56f2ec84361ebf81b3849758915bbe8658d4"
    ),
    "tests/slice100_task8/_v25_oracle.py": (
        "4b2d499630881ff77682859af2d40b7bcd02270c27176e35690446ed65862929"
    ),
    "tests/slice100_task8/test_full_strict_v25_materialization_red.py": (
        "905fb641b28c0e4c3380c892d365ff4329b2fe07f079596351bcb2261ad76618"
    ),
    "tests/slice100_task8/test_v25_dashboard_requirement_matrix_red.py": (
        "0ab3a477df943e387b45fb1670e689be206cbe6457e574d9a51a610a87351ed5"
    ),
    "scripts/verify_full_strict_v25_fixture_independently.py": (
        "1e62e0bd7ee9e61e18e42060d36e3fc08c952afab77d13d7f626bf81164e86f9"
    ),
    "tests/slice100_task8/_v25_independent_oracle.py": (
        "7cc442268c310aa8b96d49765abed9713a8484892a6b6ed67bbe14545dbbc7e6"
    ),
    "tests/slice100_task8/test_v25_independent_fixture_verification_red.py": (
        "5b5c64f7ada0c11cc5ec63c1947c65427faab1794913d37611cb25563255d95d"
    ),
    "tests/slice100_task8/fixtures/full_strict_v25_independent_golden_v1.json": (
        "8a883c69d8efd461f7add82533c3a3f61f991447770087b5411875add8efc37f"
    ),
}
_V25_RED_EVIDENCE_PATH = (
    ".local_reports/slice100/task8/v25-candidate/v25_independent_fixture_red.txt"
)
_V25_RED_EVIDENCE_SHA256 = "39e6bd7047dd83ed8664cad5cf7f78e4c1537f5003cb3f501683804719a7d15f"
_V25_ACTIVE_MARKERS = (
    b"full-strict-v25",
    b"full_strict_v25",
    b"task8-v25",
    b"task8_v25",
    b"FULL_STRICT_V25",
)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_successor_pair(
    root: Path,
    *,
    version: int,
    frozen: bool = True,
    activation_status: str = "INACTIVE",
    fixture_artifact_sha256: str | None = None,
) -> tuple[Path, Path]:
    artifact = root / f"full_strict_v{version}_reviewed_preimage_v1.json"
    artifact.write_text(
        json.dumps(
            {
                "metadata": {
                    "artifact_schema": f"full-strict-v{version}-reviewed-preimage-v1",
                    "pipeline_contract_version": f"full-strict-v{version}",
                    "append_frozen": frozen,
                    "activation_status": activation_status,
                }
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    fixture = root / f"full_strict_v{version}_contract_hash_v1.json"
    fixture.write_text(
        json.dumps(
            {
                "artifact_schema": f"full-strict-v{version}-contract-hash-fixture-v1",
                "pipeline_contract_version": f"full-strict-v{version}",
                "artifact_sha256": fixture_artifact_sha256,
                "activation_status": activation_status,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return artifact, fixture


def _v25_reconciliation_record() -> dict[str, Any]:
    records = [json.loads(line) for line in _RECONCILIATION_LOG.read_text("utf-8").splitlines()]
    matches = [
        record
        for record in records
        if record.get("event") == "V25_DEFECTIVE_RENDERED_CANDIDATE_PERMANENTLY_VOID"
    ]
    assert len(matches) == 1, "TASK8_VOID_V25_RECONCILIATION_RECORD_INVALID"
    record = matches[0]
    assert isinstance(record, dict)
    return record


def test_v25_candidate_identities_are_blocklisted_but_approved_sql_hashes_are_reusable() -> None:
    recorded = tuple(getattr(void_evidence, name, None) for name in _V25_CONSTANT_NAMES)
    assert recorded == _V25_IDENTIFIERS, "TASK8_VOID_V25_IDENTIFIERS_MISSING"
    assert tuple(getattr(void_evidence, "VOID_V25_IDENTIFIERS", ())) == _V25_IDENTIFIERS
    assert tuple(getattr(void_evidence, "VOID_V25_CANDIDATE_IDENTIFIERS", ())) == (
        _V25_CANDIDATE_IDENTIFIERS
    )
    assert tuple(getattr(void_evidence, "VOID_V25_INHERITED_SEMANTIC_IDENTIFIERS", ())) == (
        _V25_INHERITED_SEMANTIC_IDENTIFIERS
    )
    active_blocklist = set(void_evidence.TASK8_VOID_CONTRACT_HASHES)
    assert set(_V25_CANDIDATE_IDENTIFIERS) < active_blocklist
    assert not (set(_V25_INHERITED_SEMANTIC_IDENTIFIERS) & active_blocklist)


def test_v25_materializer_verifier_oracles_tests_and_golden_are_byte_frozen() -> None:
    recorded = dict(getattr(void_evidence, "VOID_V25_FROZEN_FILE_SHA256_BY_PATH", ()))
    assert recorded == _V25_FROZEN_FILE_SHA256_BY_PATH
    for relative, expected_sha256 in recorded.items():
        assert hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest() == expected_sha256

    red_evidence = _REPO_ROOT / _V25_RED_EVIDENCE_PATH
    assert red_evidence.is_file(), "TASK8_VOID_V25_RED_EVIDENCE_MISSING"
    assert hashlib.sha256(red_evidence.read_bytes()).hexdigest() == _V25_RED_EVIDENCE_SHA256


def test_v25_canonical_outputs_locks_receipts_and_contract_lock_entries_are_absent() -> None:
    assert not _V25_ARTIFACT.exists(), "TASK8_VOID_V25_CANONICAL_ARTIFACT_PRESENT"
    assert not _V25_FIXTURE.exists(), "TASK8_VOID_V25_CANONICAL_FIXTURE_PRESENT"

    forbidden_sidecars = {
        path.relative_to(_REPO_ROOT).as_posix()
        for root in (_REPO_ROOT / "contracts", _TASK8_ROOT / "fixtures")
        for path in root.rglob("*")
        if path.is_file()
        and "v25" in path.name.lower()
        and path != (_TASK8_ROOT / "fixtures" / "full_strict_v25_independent_golden_v1.json")
    }
    assert forbidden_sidecars == set(), "TASK8_VOID_V25_PUBLICATION_SIDECAR_PRESENT"

    locked = set(_load_object(_REPO_ROOT / "contracts" / "contract_lock.json")["files"])
    assert _V25_ARTIFACT.relative_to(_REPO_ROOT).as_posix() not in locked
    assert _V25_FIXTURE.relative_to(_REPO_ROOT).as_posix() not in locked

    task8_report_root = _REPO_ROOT / ".local_reports" / "slice100" / "task8"
    v25_report_files = {
        path.relative_to(_REPO_ROOT).as_posix()
        for path in task8_report_root.rglob("*")
        if path.is_file() and "v25" in path.relative_to(task8_report_root).as_posix().lower()
    }
    assert v25_report_files == {_V25_RED_EVIDENCE_PATH}, (
        "TASK8_VOID_V25_CENSUS_OR_PUBLICATION_RECEIPT_PRESENT"
    )


def test_v25_has_no_generator_publisher_census_migration_or_runtime_consumer() -> None:
    active_paths = [
        _REPO_ROOT / "scripts" / "generate_task8_contract_fixtures.py",
        _REPO_ROOT / "scripts" / "publish_task8_contract_artifact_gate.py",
        *(_REPO_ROOT / "scripts").glob("run_task8_*census*.py"),
        *(_REPO_ROOT / "src" / "idis").rglob("*"),
    ]
    forbidden = (
        tuple(value.encode("ascii") for value in _V25_CANDIDATE_IDENTIFIERS) + _V25_ACTIVE_MARKERS
    )
    offenders = {
        path.relative_to(_REPO_ROOT).as_posix()
        for path in active_paths
        if path.is_file() and any(marker in path.read_bytes() for marker in forbidden)
    }
    assert offenders == set(), "TASK8_VOID_V25_ACTIVE_CONSUMER_PRESENT"
    assert not list((_REPO_ROOT / "scripts").glob("run_task8_v25*census*.py")), (
        "TASK8_VOID_V25_CENSUS_IMPLEMENTATION_PRESENT"
    )
    assert not list(
        (_REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions").glob("0039*.py")
    ), "TASK8_MIGRATION_0039_EXISTS_BEFORE_CHECKPOINT"


def test_v25_void_record_is_exact_nonreusable_and_advances_to_v26_without_semantic_change() -> None:
    record = _v25_reconciliation_record()
    assert tuple(record["void_identifiers"].values()) == _V25_IDENTIFIERS
    assert record["standing_delegation_record"] == "T1-20260814-007"
    assert record["void_contract_version"] == "full-strict-v25"
    assert record["version_name_reusable"] is False
    assert record["successor_contract_version"] == "full-strict-v26"
    assert record["successor_parent_version"] == "full-strict-v21"
    assert record["semantic_vector_changed"] is False
    assert record["canonical_artifact_existed"] is False
    assert record["canonical_fixture_existed"] is False
    assert record["postgres_census_run"] is False
    assert record["publication_run"] is False
    assert record["migration_0039_written"] is False
    assert record["activation_status"] == "NEVER_ACTIVATED"
    assert record["defect_code"] == "TASK8_V25_BOUND_RUNTIME_INPUT_AUTHORITY_UNREACHABLE"
    assert record["affected_runtime_binding_tuple"] == [
        "$identity_and_trace_without_self_output",
        "$disposition",
        "$disposition_reason_code",
        "$result_records",
        "$answer_values",
        "$task_gaps",
        "$calculations",
    ]
    assert record["recorded_leaf_authority"] == "SCALAR_OR_FOREIGN_INPUT"
    assert record["required_leaf_authority"] == "BOUND_RUNTIME_INPUT"
    assert record["token_prefix_was_preserved"] is False
    assert record["identity_changing_correction_required"] is True


def test_successor_boundary_rejects_v25_v26_and_structurally_synthetic_v27(
    tmp_path: Path,
) -> None:
    assert not task8_conftest.successor_publication_is_complete(), (
        "TASK8_V27_CANONICAL_PUBLICATION_UNEXPECTEDLY_COMPLETE"
    )
    v25_artifact, v25_fixture = _write_successor_pair(tmp_path, version=25)
    v25_artifact_sha256 = hashlib.sha256(v25_artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=25,
        fixture_artifact_sha256=v25_artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=v25_artifact,
        fixture_path=v25_fixture,
    ), "TASK8_VOID_V25_RECOGNIZED_AS_SUCCESSOR"

    v26_artifact, v26_fixture = _write_successor_pair(tmp_path, version=26)
    v26_artifact_sha256 = hashlib.sha256(v26_artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=26,
        fixture_artifact_sha256=v26_artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=v26_artifact,
        fixture_path=v26_fixture,
    ), "TASK8_VOID_V26_RECOGNIZED_AS_SUCCESSOR"

    for frozen, activation_status in ((False, "INACTIVE"), (True, "ACTIVE")):
        artifact, fixture = _write_successor_pair(
            tmp_path,
            version=27,
            frozen=frozen,
            activation_status=activation_status,
        )
        artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
        _write_successor_pair(
            tmp_path,
            version=27,
            frozen=frozen,
            activation_status=activation_status,
            fixture_artifact_sha256=artifact_sha256,
        )
        assert not task8_conftest.successor_publication_is_complete(
            artifact_path=artifact,
            fixture_path=fixture,
        )

    artifact, fixture = _write_complete_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=None,
    )
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _write_complete_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    ), "TASK8_V27_SYNTHETIC_FIXTURE_IDENTITY_ADMITTED"
