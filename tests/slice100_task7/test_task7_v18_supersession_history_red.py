"""RED-first append-only history for frozen-v17 to mechanical-v18 succession."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECONCILIATION = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
)
_HISTORY = _REPO_ROOT / ".local_reports" / "slice100" / "task7_v17_contract_history.jsonl"
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V17_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v17_contract_hash_v1.json"
)
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)

_V17_ARTIFACT_SHA256 = "d6218f7a48f2170099b461ee152410852de9e0f29cfe78fb71627b968c1465e1"
_V17_FIXTURE_SHA256 = "d7539e693ee92831644f2e230054816a2cfcad77e706d5652460df0cc47c341b"
_V17_CONTRACT_SHA256 = "1e01b33daebb1d619808a3d5388cacfad605cddc9f9cefcedae2d0fb75e0102e"
_V17_RECEIPT_SHA256 = "37414a9d0a71072b402d30d272c4f610a9037113a1ed1012e7527b6c02d9c1b8"
_V18_CANDIDATE_SHA256 = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_V18_ARTIFACT_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_V18_FIXTURE_SHA256 = "9efef72429745ac8ccce91f4aea541a1bb958638e83bf08c9eccbc5eabd03a0c"
_V18_CONTRACT_SHA256 = "ad8e0a8fd012af34f34d8fd4b66643d2d31a8a4f436dd3a8021af9a188da378c"
_V18_RECEIPT_SHA256 = "320b407e4b31d4743bc51c7afbd04c1bc9b8d827bc63bdeb1c6ae47b31630cc9"
_BAD_RESTORE_SHA256 = "4d48af69c18fb948c5200b92998063be5a2b34305c4af16201e1d5d2c28f8484"
_GOOD_RESTORE_SHA256 = "c589089618af8d486acb41f046f1bd6969cff9279e6abfd4bdaa4b006752655e"


def _records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_v17_is_immutable_superseded_not_void_and_never_consumed() -> None:
    assert _sha256(_V17_ARTIFACT) == _V17_ARTIFACT_SHA256
    assert _sha256(_V17_FIXTURE) == _V17_FIXTURE_SHA256
    assert {
        _V17_ARTIFACT_SHA256,
        _V17_FIXTURE_SHA256,
        _V17_CONTRACT_SHA256,
    }.isdisjoint(void_evidence.VOID_CONTRACT_HASHES)

    rows = [
        row
        for row in _records(_RECONCILIATION)
        if row.get("event") == "V17_FROZEN_CONTRACT_SUPERSEDED_BY_V18_MATERIALIZATION"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["artifact_sha256"] == _V17_ARTIFACT_SHA256
    assert row["fixture_sha256"] == _V17_FIXTURE_SHA256
    assert row["pipeline_contract_sha256"] == _V17_CONTRACT_SHA256
    assert row["signed_receipt_sha256"] == _V17_RECEIPT_SHA256
    assert row["append_freeze_reached"] is True
    assert row["migration_0038_consumed_candidate"] is False
    assert row["runtime_consumed_candidate"] is False
    assert row["admission_eligible"] is False
    assert row["runtime_selectable"] is False
    assert row["superseded_by"] == "full-strict-v18"
    assert row["mechanical_defect"]["retained_definition_ordinal"] == 0
    assert row["mechanical_defect"]["bad_sql_sha256"] == _BAD_RESTORE_SHA256
    assert row["mechanical_defect"]["corrected_sql_sha256"] == _GOOD_RESTORE_SHA256
    assert "SUPERSEDED" in row["disposition"]
    assert row["disposition"] not in {"VOID", "BLOCKLISTED_VOID"}


def test_v18_candidate_and_atomic_freeze_are_distinct_append_only_events() -> None:
    assert _sha256(_V18_ARTIFACT) == _V18_ARTIFACT_SHA256
    assert _sha256(_V18_FIXTURE) == _V18_FIXTURE_SHA256
    history = _records(_HISTORY)
    superseded = [
        row for row in history if row.get("event") == "V17_FROZEN_CONTRACT_SUPERSEDED_BY_V18"
    ]
    candidates = [
        row for row in history if row.get("event") == "V18_UNFROZEN_CANDIDATE_MATERIALIZED"
    ]
    assert len(superseded) == 1
    assert len(candidates) == 1

    old = superseded[0]
    assert old["artifact_sha256"] == _V17_ARTIFACT_SHA256
    assert old["fixture_sha256"] == _V17_FIXTURE_SHA256
    assert old["contract_sha256"] == _V17_CONTRACT_SHA256
    assert old["signed_census_receipt_sha256"] == _V17_RECEIPT_SHA256
    assert old["append_frozen"] is True
    assert old["runtime_state"] == "INACTIVE"
    assert old["runtime_selectable"] is False
    assert old["migration_0038_consumed"] is False
    assert old["runtime_consumed"] is False
    assert old["superseded_by"] == "full-strict-v18"

    new = candidates[0]
    assert new["artifact_sha256"] == _V18_CANDIDATE_SHA256
    assert new["append_frozen"] is False
    assert new["runtime_state"] == "INACTIVE"
    assert new["runtime_selectable"] is False
    assert new["migration_0038_consumed"] is False
    assert new["runtime_consumed"] is False
    assert new["supersedes"] == "full-strict-v17"
    assert new["fixture_sha256"] is None
    assert new["contract_sha256"] is None
    assert new["signed_census_receipt_sha256"] is None
    assert new["semantic_invariants"] == {
        "semantic_steps": 28,
        "edge_rules": 48,
        "usage_routes": 1_756,
        "usage_extractors": 19,
        "providers": 18,
        "erasure_relations": 86,
    }

    signed = [row for row in history if row.get("event") == "V18_APP_ROLE_CENSUS_SIGNED"]
    frozen = [row for row in history if row.get("event") == "V18_ATOMIC_FREEZE_ADMITTED"]
    assert len(signed) == 1
    assert len(frozen) == 1
    assert signed[0]["candidate_artifact_sha256"] == _V18_CANDIDATE_SHA256
    assert signed[0]["prospective_frozen_artifact_sha256"] == _V18_ARTIFACT_SHA256
    assert signed[0]["contract_sha256"] == _V18_CONTRACT_SHA256
    assert signed[0]["signed_census_receipt_sha256"] == _V18_RECEIPT_SHA256
    assert signed[0]["append_frozen"] is False
    assert signed[0]["migration_0038_consumed"] is False
    assert signed[0]["runtime_consumed"] is False

    final = frozen[0]
    assert final["source_candidate_artifact_sha256"] == _V18_CANDIDATE_SHA256
    assert final["artifact_sha256"] == _V18_ARTIFACT_SHA256
    assert final["fixture_sha256"] == _V18_FIXTURE_SHA256
    assert final["contract_sha256"] == _V18_CONTRACT_SHA256
    assert final["signed_census_receipt_sha256"] == _V18_RECEIPT_SHA256
    assert final["append_frozen"] is True
    assert final["runtime_state"] == "INACTIVE"
    assert final["runtime_selectable"] is False
    assert final["migration_0038_consumed"] is False
    assert final["runtime_consumed"] is False


def test_v18_reconciliation_records_corrected_source_pins_and_freeze() -> None:
    rows = _records(_RECONCILIATION)
    corrections = [row for row in rows if row.get("event") == "V18_CANDIDATE_SOURCE_PINS_CORRECTED"]
    signed = [row for row in rows if row.get("event") == "V18_APP_ROLE_CENSUS_SIGNED"]
    frozen = [row for row in rows if row.get("event") == "V18_ATOMIC_FREEZE_ADMITTED"]
    assert len(corrections) == 1
    assert len(signed) == 1
    assert len(frozen) == 1
    assert corrections[0]["candidate_artifact_sha256"] == _V18_CANDIDATE_SHA256
    assert corrections[0]["prior_record_preserved"] is True
    assert signed[0]["signed_receipt_sha256"] == _V18_RECEIPT_SHA256
    assert signed[0]["census"]["application_role_case_count"] == 219
    assert signed[0]["census"]["constructor_roundtrip_count"] == 4
    assert signed[0]["census"]["source_projection_count"] == 6
    assert signed[0]["census"]["post_install_backfill_rejection_count"] == 6
    assert signed[0]["census"]["supplemental_receipt_case_count"] == 119
    assert signed[0]["census"]["retained_function_restore_count"] == 8
    assert signed[0]["census"]["retained_trigger_restore_count"] == 9
    assert signed[0]["census"]["database_execution_field_count"] == 29
    assert frozen[0]["artifact_sha256"] == _V18_ARTIFACT_SHA256
    assert frozen[0]["fixture_sha256"] == _V18_FIXTURE_SHA256
    assert frozen[0]["pipeline_contract_sha256"] == _V18_CONTRACT_SHA256
    assert frozen[0]["signed_receipt_sha256"] == _V18_RECEIPT_SHA256
