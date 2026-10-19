"""RED-first append-only history contracts for the v16-to-v17 succession."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests._private_artifacts import requires_private_artifact
from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECONCILIATION = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
)
_V16_HISTORY = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_contract_materialization_history_v2_v16.safe.json"
)
_V17_HISTORY = _REPO_ROOT / ".local_reports" / "slice100" / "task7_v17_contract_history.jsonl"

_V16_ARTIFACT_SHA256 = "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
_V16_FIXTURE_SHA256 = "999dafef745feaa56448e0a84ebed3aff37729c3d85f98267ae72a19d702ab79"
_V16_CONTRACT_SHA256 = "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
_V16_CENSUS_RECEIPT_SHA256 = "b47f062aa339d8fa288e4b1eefc9ed7fea25a4a02920bc9fcc0adc964dbb41ce"
_V16_HISTORY_SHA256 = "8ad311cb69c7145904cbfe51963b0438f0c89bfe6aac7b2f5c7fd10d1460ca78"
_V17_CANDIDATE_SHA256 = "ae6d42df18d236fad3730be9829fe66b19549e7902c06ea9c62ad7c6d250a5e7"
_V17_ARTIFACT_SHA256 = "d6218f7a48f2170099b461ee152410852de9e0f29cfe78fb71627b968c1465e1"
_V17_FIXTURE_SHA256 = "d7539e693ee92831644f2e230054816a2cfcad77e706d5652460df0cc47c341b"
_V17_CONTRACT_SHA256 = "1e01b33daebb1d619808a3d5388cacfad605cddc9f9cefcedae2d0fb75e0102e"
_V17_CENSUS_RECEIPT_SHA256 = "37414a9d0a71072b402d30d272c4f610a9037113a1ed1012e7527b6c02d9c1b8"
_IMMUTABLE_PREFIX_ROW_COUNT = 29
_IMMUTABLE_PREFIX_BYTES = 39_030
_IMMUTABLE_PREFIX_SHA256 = "57fe9ad1f30cf4de4ba065e8f006426ef079dbb32e5a5a4d91bd88922cc44055"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _reconciliation_records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in _RECONCILIATION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _v17_history_records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in _V17_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_first_29_reconciliation_rows_remain_byte_immutable() -> None:
    lines = _RECONCILIATION.read_bytes().splitlines(keepends=True)
    assert len(lines) >= _IMMUTABLE_PREFIX_ROW_COUNT + 1

    prefix = b"".join(lines[:_IMMUTABLE_PREFIX_ROW_COUNT])
    assert len(prefix) == _IMMUTABLE_PREFIX_BYTES
    assert hashlib.sha256(prefix).hexdigest() == _IMMUTABLE_PREFIX_SHA256
    assert json.loads(lines[_IMMUTABLE_PREFIX_ROW_COUNT - 1])["event"] == (
        "V16_ATOMIC_FREEZE_ADMITTED"
    )


def test_v16_supersession_is_append_only_not_void_and_never_runtime_selectable() -> None:
    records = _reconciliation_records()
    matches = [
        record
        for record in records[_IMMUTABLE_PREFIX_ROW_COUNT:]
        if record.get("event") == "V16_FROZEN_CONTRACT_SUPERSEDED_BY_V17_APPROVAL"
    ]
    assert len(matches) == 1
    record = matches[0]

    assert record["artifact_sha256"] == _V16_ARTIFACT_SHA256
    assert record["fixture_sha256"] == _V16_FIXTURE_SHA256
    assert record["pipeline_contract_sha256"] == _V16_CONTRACT_SHA256
    assert record["signed_receipt_sha256"] == _V16_CENSUS_RECEIPT_SHA256
    assert record["append_freeze_reached"] is True
    assert record["runtime_state"] == "INACTIVE"
    assert record["migration_0038_consumed_candidate"] is False
    assert record["runtime_consumed_candidate"] is False
    assert record["admission_eligible"] is False
    assert record["runtime_selectable"] is False
    assert record["superseded_by"] == "full-strict-v17"
    assert record["superseding_contract_version"] == "full-strict-v17"
    assert "SUPERSEDED" in record["disposition"]
    assert "VOID" not in record["disposition"]
    assert {
        _V16_ARTIFACT_SHA256,
        _V16_FIXTURE_SHA256,
        _V16_CONTRACT_SHA256,
    }.isdisjoint(void_evidence.VOID_CONTRACT_HASHES)


@requires_private_artifact("task7_contract_materialization_history_v2_v16.safe.json")
def test_v17_successor_history_is_separate_and_preserves_v16_history_bytes() -> None:
    assert _sha256(_V16_HISTORY) == _V16_HISTORY_SHA256
    assert _V17_HISTORY.is_file(), (
        "TASK7_V17_SUCCESSOR_HISTORY_NOT_IMPLEMENTED:"
        f"{_V17_HISTORY.relative_to(_REPO_ROOT).as_posix()}"
    )

    v16 = _load_json(_V16_HISTORY)
    history = _v17_history_records()
    assert v16["invariance_wall"] == {
        "semantic_steps": 28,
        "edge_rules": 48,
        "usage_routes": 1_756,
        "usage_extractors": 19,
        "providers": 18,
        "erasure_relations": 86,
    }
    supersessions = [
        entry
        for entry in history
        if entry.get("contract_version") == "full-strict-v16"
        and entry.get("superseded_by") == "full-strict-v17"
    ]
    # Later append-only v17 supersession records are history, not candidate/freeze duplicates.
    successors = [
        entry
        for entry in history
        if entry.get("event")
        in {
            "V17_UNFROZEN_CANDIDATE_MATERIALIZED",
            "V17_ATOMIC_FREEZE_ADMITTED",
        }
    ]
    assert len(supersessions) == 1
    assert len(successors) == 2

    supersession = supersessions[0]
    assert supersession["artifact_sha256"] == _V16_ARTIFACT_SHA256
    assert supersession["fixture_sha256"] == _V16_FIXTURE_SHA256
    assert supersession["contract_sha256"] == _V16_CONTRACT_SHA256
    assert supersession["signed_census_receipt_sha256"] == _V16_CENSUS_RECEIPT_SHA256
    assert supersession["append_frozen"] is True
    assert supersession["runtime_state"] == "INACTIVE"
    assert supersession["runtime_selectable"] is False
    assert "SUPERSEDED" in supersession["disposition"]
    assert "VOID" not in supersession["disposition"]

    candidate, frozen = successors
    assert candidate["event"] == "V17_UNFROZEN_CANDIDATE_MATERIALIZED"
    assert candidate["artifact_sha256"] == _V17_CANDIDATE_SHA256
    assert candidate["supersedes"] == "full-strict-v16"
    assert candidate["append_frozen"] is False
    assert candidate["runtime_state"] == "INACTIVE"
    assert candidate["runtime_selectable"] is False
    assert "VOID" not in candidate["disposition"]

    assert frozen["event"] == "V17_ATOMIC_FREEZE_ADMITTED"
    assert frozen["source_candidate_artifact_sha256"] == _V17_CANDIDATE_SHA256
    assert frozen["artifact_sha256"] == _V17_ARTIFACT_SHA256
    assert frozen["fixture_sha256"] == _V17_FIXTURE_SHA256
    assert frozen["contract_sha256"] == _V17_CONTRACT_SHA256
    assert frozen["signed_census_receipt_sha256"] == _V17_CENSUS_RECEIPT_SHA256
    assert frozen["supersedes"] == "full-strict-v16"
    assert frozen["append_frozen"] is True
    assert frozen["runtime_state"] == "INACTIVE"
    assert frozen["runtime_selectable"] is False
    assert frozen["migration_0038_consumed"] is False
    assert frozen["runtime_consumed"] is False
    assert "VOID" not in frozen["disposition"]
    assert _sha256(_V16_HISTORY) == _V16_HISTORY_SHA256


def test_v17_signed_census_and_atomic_freeze_are_append_only_reconciliation_events() -> None:
    records = _reconciliation_records()[_IMMUTABLE_PREFIX_ROW_COUNT:]
    signed = [row for row in records if row.get("event") == "V17_APP_ROLE_CENSUS_SIGNED"]
    frozen = [row for row in records if row.get("event") == "V17_ATOMIC_FREEZE_ADMITTED"]
    assert len(signed) == 1
    assert len(frozen) == 1

    assert signed[0]["candidate_artifact_sha256"] == _V17_CANDIDATE_SHA256
    assert signed[0]["signed_receipt_sha256"] == _V17_CENSUS_RECEIPT_SHA256
    assert signed[0]["signature_verified"] is True
    assert signed[0]["append_freeze_reached"] is False

    assert frozen[0]["source_candidate_artifact_sha256"] == _V17_CANDIDATE_SHA256
    assert frozen[0]["artifact_sha256"] == _V17_ARTIFACT_SHA256
    assert frozen[0]["fixture_sha256"] == _V17_FIXTURE_SHA256
    assert frozen[0]["pipeline_contract_sha256"] == _V17_CONTRACT_SHA256
    assert frozen[0]["signed_receipt_sha256"] == _V17_CENSUS_RECEIPT_SHA256
    assert frozen[0]["append_freeze_reached"] is True
    assert frozen[0]["runtime_state"] == "INACTIVE"
    assert frozen[0]["migration_0038_consumed_candidate"] is False
    assert frozen[0]["runtime_consumed_candidate"] is False
