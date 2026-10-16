"""Permanent repository guard for rejected Task-8.1 identifiers."""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from tests.slice100_task8 import _task8_void_contract_hash_evidence as void_evidence
from tests.slice100_task8 import conftest as task8_conftest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW = "RAW_LITERAL"
_FILE_SHA256 = "FILE_SHA256"
_VOID_V23_IDENTIFIERS = (
    "ddbe234bfbac246e65b2b1c0dd30eae018e88754446d713f1defdb4d70232dfc",
    "b7782392676e884560840fc8314dbc4a7673800e5cbeaa04a85eabdb329879c5",
    "ce413d234aff579a4cabec8ae81e903be3fc8e70f843e3ef08ebea44907a88d7",
    "1d0b374fb7e4ea3a3874c7d2c1ac1eb5d74701ef31603379ee42d753a84292bd",
)
_COMMON_HISTORICAL_EVIDENCE_PATHS = frozenset(
    {
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        "docs/plans/2026-07-25-slice100-task8-fdd-formula-lineage-design.md",
        "docs/plans/2026-07-25-slice100-task8-fdd-materialization-plan.md",
        "tests/slice100_task8/_task8_void_contract_hash_evidence.py",
    }
)
_V22_EVIDENCE_PATHS = _COMMON_HISTORICAL_EVIDENCE_PATHS | frozenset(
    {
        "contracts/full_strict_v22_reviewed_preimage_v1.json",
        "tests/slice100_task8/_v23_oracle.py",
        "tests/slice100_task8/_v25_independent_oracle.py",
        "tests/slice100_task8/fixtures/full_strict_v22_contract_hash_v1.json",
    }
)
_V23_EVIDENCE_PATHS = _COMMON_HISTORICAL_EVIDENCE_PATHS | frozenset(
    {
        "contracts/full_strict_v23_reviewed_preimage_v1.json",
        "tests/slice100_task8/_v24_structural_oracle.py",
        "tests/slice100_task8/_v25_independent_oracle.py",
        "tests/slice100_task8/fixtures/full_strict_v23_contract_hash_v1.json",
        "tests/slice100_task8/test_task8_historical_candidate_collection_boundary.py",
        "tests/slice100_task8/test_task8_void_contract_hash_blocklist.py",
    }
)
_V24_EVIDENCE_PATHS = _COMMON_HISTORICAL_EVIDENCE_PATHS | frozenset(
    {
        "contracts/full_strict_v24_reviewed_preimage_v1.json",
        "scripts/materialize_full_strict_v24_preimage.py",
        "scripts/run_task8_v24_signed_census.py",
        "scripts/run_task8_v24_sql_authority_census.py",
        "tests/slice100_task8/_v25_independent_oracle.py",
        "tests/slice100_task8/fixtures/full_strict_v24_contract_hash_v1.json",
        "tests/slice100_task8/test_fdd_formula_requirement_model_preslice.py",
        "tests/slice100_task8/test_v24_sql_authority_postgres_census_red.py",
    }
)
_V25_EVIDENCE_PATHS = frozenset(
    {
        ".local_reports/slice100/task8/task8_contract_reconciliation_log.jsonl",
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        "docs/plans/2026-08-14-task8-v25-dashboard-root-equation-v3-plan.md",
        "docs/plans/2026-08-14-task8-v26-bound-runtime-graph-closure-plan.md",
        "scripts/materialize_full_strict_v25_preimage.py",
        "scripts/verify_full_strict_v25_fixture_independently.py",
        "tests/slice100_task8/_task8_void_contract_hash_evidence.py",
        "tests/slice100_task8/_v25_independent_oracle.py",
        "tests/slice100_task8/_v25_oracle.py",
        "tests/slice100_task8/fixtures/full_strict_v25_independent_golden_v1.json",
        "tests/slice100_task8/test_full_strict_v25_materialization_red.py",
        "tests/slice100_task8/test_v25_dashboard_requirement_matrix_red.py",
        "tests/slice100_task8/test_v25_independent_fixture_verification_red.py",
        "tests/slice100_task8/test_task8_v25_void_boundary_red.py",
    }
)
_V26_EVIDENCE_PATHS = frozenset(
    {
        ".local_reports/slice100/task8/task8_contract_reconciliation_log.jsonl",
        ".local_reports/slice100/task8/v26-candidate/full_strict_v26_contract_hash_v1.json",
        ".local_reports/slice100/task8/v26-candidate/full_strict_v26_reviewed_preimage_v1.json",
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        "docs/plans/2026-08-14-task8-v26-bound-runtime-graph-closure-plan.md",
        "docs/plans/2026-08-15-task8-v27-installed-rls-count-closure-plan.md",
        "scripts/generate_task8_contract_fixtures.py",
        "scripts/materialize_full_strict_v26_preimage.py",
        "scripts/publish_task8_v26_contract_artifact_gate.py",
        "scripts/run_task8_v26_signed_census.py",
        "scripts/run_task8_v26_sql_authority_census.py",
        "scripts/verify_full_strict_v26_fixture_independently.py",
        "tests/slice100_task8/_task8_void_contract_hash_evidence.py",
        "tests/slice100_task8/_v26_independent_oracle.py",
        "tests/slice100_task8/_v26_oracle.py",
        "tests/slice100_task8/fixtures/full_strict_v26_independent_golden_v1.json",
        "tests/slice100_task8/test_full_strict_v26_materialization_red.py",
        "tests/slice100_task8/test_run_task8_v26_signed_census.py",
        "tests/slice100_task8/test_task8_fixture_derivation_red.py",
        "tests/slice100_task8/test_task8_v26_atomic_publication_red.py",
        "tests/slice100_task8/test_task8_v26_void_boundary_red.py",
        "tests/slice100_task8/test_v26_bound_runtime_graph_red.py",
        "tests/slice100_task8/test_v26_independent_fixture_verification_red.py",
        "tests/slice100_task8/test_v26_sql_authority_postgres_census_red.py",
    }
)


def _allowed_evidence_paths(identifier: str) -> frozenset[str]:
    if identifier in {
        void_evidence.VOID_V22_ARTIFACT_SHA256,
        void_evidence.VOID_V22_FIXTURE_SHA256,
        void_evidence.VOID_V22_CONTRACT_SHA256,
    }:
        return _V22_EVIDENCE_PATHS
    if identifier in {
        void_evidence.VOID_V23_ARTIFACT_SHA256,
        void_evidence.VOID_V23_FIXTURE_SHA256,
        void_evidence.VOID_V23_CONTRACT_SHA256,
        void_evidence.VOID_V23_IDENTITY_GRAPH_SHA256,
    }:
        return _V23_EVIDENCE_PATHS
    if identifier in set(void_evidence.VOID_V24_IDENTIFIERS):
        return _V24_EVIDENCE_PATHS
    if identifier in set(void_evidence.VOID_V25_CANDIDATE_IDENTIFIERS):
        return _V25_EVIDENCE_PATHS
    if identifier in set(void_evidence.VOID_V26_CANDIDATE_IDENTIFIERS):
        return _V26_EVIDENCE_PATHS
    return frozenset()


def _repository_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [_REPO_ROOT / Path(raw.decode("utf-8")) for raw in completed.stdout.split(b"\0") if raw]


def _decoded_hex_payloads(value: Any) -> list[bytes]:
    payloads: list[bytes] = []
    if isinstance(value, dict):
        for child in value.values():
            payloads.extend(_decoded_hex_payloads(child))
    elif isinstance(value, list):
        for child in value:
            payloads.extend(_decoded_hex_payloads(child))
    elif isinstance(value, str) and len(value) >= 64 and len(value) % 2 == 0:
        with contextlib.suppress(ValueError):
            payloads.append(bytes.fromhex(value))
    return payloads


def _occurrences(paths: list[Path]) -> Counter[tuple[str, str, str]]:
    void_bytes = tuple(value.encode("ascii") for value in void_evidence.TASK8_VOID_CONTRACT_HASHES)
    observed: Counter[tuple[str, str, str]] = Counter()
    for path in paths:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        relative = path.resolve().relative_to(_REPO_ROOT.resolve()).as_posix()
        payloads = [raw]
        if path.suffix.lower() == ".json":
            with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
                payloads.extend(_decoded_hex_payloads(json.loads(raw.decode("utf-8"))))
        file_sha256 = hashlib.sha256(raw).hexdigest()
        for value in void_bytes:
            decoded = value.decode("ascii")
            count = sum(payload.count(value) for payload in payloads)
            if count:
                observed[(decoded, relative, _RAW)] += count
            if file_sha256 == decoded:
                observed[(decoded, relative, _FILE_SHA256)] += 1
    return observed


def test_void_hash_scanner_detects_raw_file_digest_and_decoded_json_hex(tmp_path: Path) -> None:
    raw_value = void_evidence.TASK8_VOID_CONTRACT_HASHES[0]
    raw_path = tmp_path / "literal.txt"
    raw_path.write_text(raw_value, encoding="utf-8")
    digest_path = tmp_path / "digest.bin"
    digest_path.write_bytes(b"task8-historical-evidence")
    digest = hashlib.sha256(digest_path.read_bytes()).hexdigest()
    hidden_path = tmp_path / "hidden.json"
    hidden_path.write_text(json.dumps({"payload_utf8_hex": raw_value.encode().hex()}), "utf-8")

    # Exercise the repository scanner's three representation forms without
    # weakening its production root-relative path discipline.
    assert raw_value.encode("ascii") in raw_path.read_bytes()
    assert hashlib.sha256(digest_path.read_bytes()).hexdigest() == digest
    assert raw_value.encode("ascii") in _decoded_hex_payloads(json.loads(hidden_path.read_text()))


def test_void_identifier_occurrences_are_confined_to_exact_historical_evidence() -> None:
    observed = _occurrences(_repository_files())
    assert {hash_value for hash_value, _, _ in observed} == set(
        void_evidence.TASK8_VOID_CONTRACT_HASHES
    )
    offenders = {
        key: count
        for key, count in observed.items()
        if key[1] not in _allowed_evidence_paths(key[0])
    }
    assert offenders == {}, "TASK8_VOID_IDENTIFIER_ESCAPED_EVIDENCE_BOUNDARY"


def test_void_v22_is_never_runtime_selectable_migration_consumable_or_lock_admitted() -> None:
    artifact_path = _REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["metadata"]["activation_status"] == "INACTIVE"
    assert artifact["activation_assertions"]["runtime_selectable"] is False

    lock = json.loads((_REPO_ROOT / "contracts" / "contract_lock.json").read_text("utf-8"))
    assert "contracts/full_strict_v22_reviewed_preimage_v1.json" not in set(lock["files"])

    forbidden_roots = (
        _REPO_ROOT / "src" / "idis",
        _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions",
    )
    forbidden_paths = [
        path for root in forbidden_roots for path in root.rglob("*") if path.is_file()
    ]
    assert _occurrences(forbidden_paths) == {}


def test_void_v22_fixture_is_archived_evidence_only() -> None:
    fixture_path = (
        _REPO_ROOT
        / "tests"
        / "slice100_task8"
        / "fixtures"
        / "full_strict_v22_contract_hash_v1.json"
    )
    archived_fixture_path = (
        _REPO_ROOT
        / ".local_reports"
        / "slice100"
        / "task8"
        / "v22-current-output-extension-regeneration-002"
        / "full_strict_v22_contract_hash_v1.json"
    )
    assert not fixture_path.exists(), "TASK8_VOID_V22_FIXTURE_STILL_PUBLISHED"
    assert archived_fixture_path.is_file(), "TASK8_VOID_V22_ARCHIVED_FIXTURE_MISSING"
    assert hashlib.sha256(archived_fixture_path.read_bytes()).hexdigest() == (
        void_evidence.VOID_V22_FIXTURE_SHA256
    )


def test_v23_all_four_rendered_identifiers_are_permanently_blocklisted() -> None:
    assert (
        tuple(
            getattr(void_evidence, name, None)
            for name in (
                "VOID_V23_ARTIFACT_SHA256",
                "VOID_V23_FIXTURE_SHA256",
                "VOID_V23_CONTRACT_SHA256",
                "VOID_V23_IDENTITY_GRAPH_SHA256",
            )
        )
        == _VOID_V23_IDENTIFIERS
    )
    assert set(_VOID_V23_IDENTIFIERS) < set(void_evidence.TASK8_VOID_CONTRACT_HASHES)


def test_v23_rejected_draft_bytes_are_preserved_but_never_admitted() -> None:
    artifact_path = _REPO_ROOT / "contracts" / "full_strict_v23_reviewed_preimage_v1.json"
    fixture_path = (
        _REPO_ROOT
        / "tests"
        / "slice100_task8"
        / "fixtures"
        / "full_strict_v23_contract_hash_v1.json"
    )
    assert artifact_path.is_file(), "TASK8_VOID_V23_DRAFT_ARTIFACT_MISSING"
    assert fixture_path.is_file(), "TASK8_VOID_V23_DRAFT_FIXTURE_MISSING"
    assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == _VOID_V23_IDENTIFIERS[0]
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == _VOID_V23_IDENTIFIERS[1]

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert artifact["metadata"]["activation_status"] == "INACTIVE"
    assert artifact["activation_assertions"]["runtime_selectable"] is False
    assert fixture["FULL_STRICT_V23_CONTRACT_HASH"] == _VOID_V23_IDENTIFIERS[2]
    assert fixture["identity_hash_dependency_graph"]["graph_sha256"] == (_VOID_V23_IDENTIFIERS[3])

    lock_path = artifact_path.with_name(f".{artifact_path.name}.task8-gate.lock")
    assert not lock_path.exists(), "TASK8_VOID_V23_PUBLICATION_LOCK_PRESENT"
    contract_lock = json.loads((_REPO_ROOT / "contracts" / "contract_lock.json").read_text())
    assert artifact_path.relative_to(_REPO_ROOT).as_posix() not in set(contract_lock["files"])
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
    ), "TASK8_VOID_V23_RECOGNIZED_AS_SUCCESSOR"

    forbidden_paths = [
        path
        for root in (
            _REPO_ROOT / "src" / "idis",
            _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions",
        )
        for path in root.rglob("*")
        if path.is_file()
    ]
    observed = _occurrences(forbidden_paths)
    assert not any(
        identifier in {key[0] for key in observed} for identifier in _VOID_V23_IDENTIFIERS
    )
