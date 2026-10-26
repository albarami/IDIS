"""RED boundary for the permanently void full-strict-v24 candidate.

The tests protect the rejected bytes, prove that they were never published or
consumed by production/runtime code, and pin every remaining marker-bearing
historical test or evidence source without treating that evidence as active.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tests.slice100_task8 import _task8_void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V24_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
_V24_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v24_contract_hash_v1.json"
)
_RECONCILIATION_LOG = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task8" / "task8_contract_reconciliation_log.jsonl"
)
_V24_CONSTANT_NAMES = (
    "VOID_V24_ARTIFACT_SHA256",
    "VOID_V24_FIXTURE_SHA256",
    "VOID_V24_CONTRACT_SHA256",
    "VOID_V24_IDENTITY_GRAPH_SHA256",
    "VOID_V24_CONSERVATION_SHA256",
    "VOID_V24_POLICY_BINDING_SHA256",
)
_BOUNDARY_EVIDENCE_PATHS = frozenset(
    {
        "tests/slice100_task8/_task8_void_contract_hash_evidence.py",
        "tests/slice100_task8/test_task8_v24_void_boundary_red.py",
        "tests/slice100_task8/fixtures/full_strict_v24_contract_hash_v1.json",
    }
)
_EXPECTED_EVIDENCE_ONLY_V24_MARKER_PATHS = frozenset(
    {
        "scripts/materialize_full_strict_v24_preimage.py",
        "scripts/publish_task8_contract_artifact_gate.py",
        "scripts/publish_task8_v26_contract_artifact_gate.py",
        "scripts/run_task8_v24_signed_census.py",
        "scripts/run_task8_v24_sql_authority_census.py",
        "scripts/verify_full_strict_v24_fixture_independently.py",
        "tests/slice100_task8/_v24_independent_oracle.py",
        "tests/slice100_task8/_v24_structural_oracle.py",
        "tests/slice100_task8/_v25_independent_oracle.py",
        "tests/slice100_task8/_v25_oracle.py",
        "tests/slice100_task8/_v26_independent_oracle.py",
        "tests/slice100_task8/_v26_oracle.py",
        "tests/slice100_task8/fixtures/full_strict_v24_dashboard_identity_golden_v1.json",
        "tests/slice100_task8/test_fdd_formula_requirement_model_preslice.py",
        "tests/slice100_task8/test_full_strict_v25_materialization_red.py",
        "tests/slice100_task8/test_full_strict_v26_materialization_red.py",
        "tests/slice100_task8/test_run_task8_v24_signed_census.py",
        "tests/slice100_task8/test_task8_artifact_gate_boundary.py",
        "tests/slice100_task8/test_task8_atomic_publication_red.py",
        "tests/slice100_task8/test_task8_fixture_derivation_red.py",
        "tests/slice100_task8/test_task8_historical_candidate_collection_boundary.py",
        "tests/slice100_task8/test_task8_v26_atomic_publication_red.py",
        "tests/slice100_task8/test_task8_void_contract_hash_blocklist.py",
        "tests/slice100_task8/test_v24_independent_fixture_verification_red.py",
        "tests/slice100_task8/test_v24_sql_authority_postgres_census_red.py",
        "tests/slice100_task8/test_v24_structural_admission_red.py",
        "tests/slice100_task8/test_v25_independent_fixture_verification_red.py",
        "tests/slice100_task8/test_v26_independent_fixture_verification_red.py",
        "tests/slice100_task8/test_v26_sql_authority_postgres_census_red.py",
    }
)
_V24_ACTIVE_MARKERS = (
    b"full-strict-v24",
    b"full_strict_v24",
    b"task8_v24",
    b"V24_ARTIFACT",
    b"V24_FIXTURE",
)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _rendered_v24_identifiers() -> tuple[str, ...]:
    fixture = _load_object(_V24_FIXTURE)
    return (
        hashlib.sha256(_V24_ARTIFACT.read_bytes()).hexdigest(),
        hashlib.sha256(_V24_FIXTURE.read_bytes()).hexdigest(),
        fixture["FULL_STRICT_V24_CONTRACT_HASH"],
        fixture["identity_hash_dependency_graph"]["graph_sha256"],
        fixture["approved_semantics_conservation"]["sha256"],
        fixture["policy_binding"]["binding_sha256"],
    )


def _reconciliation_records(event: str) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in _RECONCILIATION_LOG.read_text("utf-8").splitlines()]
    return [record for record in records if record.get("event") == event]


def _repository_files() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        _REPO_ROOT / Path(value.decode("utf-8")) for value in completed.stdout.split(b"\0") if value
    )


def _evidence_only_v24_marker_paths() -> frozenset[str]:
    roots = (_REPO_ROOT / "scripts", _REPO_ROOT / "tests" / "slice100_task8")
    observed: set[str] = set()
    for path in _repository_files():
        if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
            continue
        relative = path.relative_to(_REPO_ROOT).as_posix()
        if relative in _BOUNDARY_EVIDENCE_PATHS:
            continue
        raw = path.read_bytes()
        if any(marker in raw for marker in _V24_ACTIVE_MARKERS):
            observed.add(relative)
    return frozenset(observed)


def _production_runtime_v24_consumption_paths() -> frozenset[str]:
    forbidden_tokens = tuple(value.encode("ascii") for value in _rendered_v24_identifiers()) + (
        b"full-strict-v24",
    )
    runtime_root = _REPO_ROOT / "src" / "idis"
    return frozenset(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in runtime_root.rglob("*")
        if path.is_file() and any(token in path.read_bytes() for token in forbidden_tokens)
    )


def test_v24_all_six_rendered_identifiers_are_permanently_blocklisted() -> None:
    rendered = _rendered_v24_identifiers()
    recorded = tuple(getattr(void_evidence, name, None) for name in _V24_CONSTANT_NAMES)
    assert recorded == rendered, "TASK8_VOID_V24_IDENTIFIERS_MISSING"
    assert tuple(getattr(void_evidence, "VOID_V24_IDENTIFIERS", ())) == rendered
    assert set(rendered) < set(void_evidence.TASK8_VOID_CONTRACT_HASHES)


def test_v24_bytes_are_preserved_inactive_and_never_lock_admitted() -> None:
    assert _V24_ARTIFACT.is_file(), "TASK8_VOID_V24_DRAFT_ARTIFACT_MISSING"
    assert _V24_FIXTURE.is_file(), "TASK8_VOID_V24_DRAFT_FIXTURE_MISSING"
    artifact = _load_object(_V24_ARTIFACT)
    fixture = _load_object(_V24_FIXTURE)
    assert artifact["metadata"]["activation_status"] == "INACTIVE"
    assert artifact["activation_assertions"]["runtime_selectable"] is False
    assert fixture["activation_status"] == "INACTIVE"

    lock_path = _V24_ARTIFACT.with_name(f".{_V24_ARTIFACT.name}.task8-gate.lock")
    assert not lock_path.exists(), "TASK8_VOID_V24_PUBLICATION_LOCK_PRESENT"
    contract_lock = _load_object(_REPO_ROOT / "contracts" / "contract_lock.json")
    locked = set(contract_lock["files"])
    assert _V24_ARTIFACT.relative_to(_REPO_ROOT).as_posix() not in locked
    assert _V24_FIXTURE.relative_to(_REPO_ROOT).as_posix() not in locked


def test_v24_has_no_runtime_or_migration_consumption() -> None:
    assert _production_runtime_v24_consumption_paths() == frozenset(), (
        "TASK8_VOID_V24_RUNTIME_OR_MIGRATION_CONSUMPTION"
    )


def test_v24_markers_are_exactly_pinned_as_historical_evidence_not_active_consumers() -> None:
    evidence_only = _evidence_only_v24_marker_paths()
    assert evidence_only == _EXPECTED_EVIDENCE_ONLY_V24_MARKER_PATHS, (
        "TASK8_VOID_V24_EVIDENCE_ONLY_MARKER_INVENTORY_DRIFT"
    )
    assert _production_runtime_v24_consumption_paths() == frozenset()


def test_v24_exact_collection_decision_is_separate_and_auditable() -> None:
    records = _reconciliation_records(
        "V24_OBSOLETE_EXECUTION_SUITES_ARCHIVED_CURRENT_VOID_EVIDENCE_RETAINED"
    )
    assert len(records) == 1, "TASK8_T1_012_RECONCILIATION_RECORD_INVALID"
    record = records[0]
    assert record["standing_delegation_record"] == "T1-20260815-012"
    assert record["precedent_tuple"] == [
        "T1-20260815-010_EXACT_HISTORICAL_COLLECTION_BOUNDARY",
        "V22_V23_V25_V26_PINNED_EXECUTION_ARCHIVE",
        "STANDING_DELEGATION_V2_COLLECTION_BOUNDARY_GUARD_PATTERN",
    ]
    assert record["archived_module_tuple"] == [
        [
            "tests/slice100_task8/test_task8_atomic_publication_red.py",
            "89c287fe62e7f9e5e281ffd4edb4799506047250418575037225d2872f999182",
        ],
        [
            "tests/slice100_task8/test_v24_sql_authority_postgres_census_red.py",
            "6bea316ef53b6b9b328fc1801fe273a93d540d6d541facf3399c958a0efd9c5e",
        ],
    ]
    assert record["v24_void_boundary_source_sha256_before"] == (
        "0af0674ad2418e0d1be503f09429c74eef2e084d4758aa948b1d0effc0dbc44d"
    )
    assert record["evidence_only_marker_path_count"] == 29
    assert record["production_runtime_consumption_count"] == 0
    assert record["current_v24_void_boundary_collected"] is True
    assert record["archive_match_mode"] == "EXACT_REPO_RELATIVE_MODULE_PATH"
    assert record["semantic_vector_changed"] is False
    assert record["database_call_made"] is False
    assert record["profile_accessed"] is False
    assert record["canonical_write_made"] is False
    assert record["commit_created"] is False
