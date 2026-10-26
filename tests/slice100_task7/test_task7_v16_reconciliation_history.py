"""Append-only lifecycle evidence for the frozen v16 authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests._private_artifacts import requires_private_artifact

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG = _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v16_current_guard_postgres_census_receipt.json"
)
_FIRST_29_RECORDS_SHA256 = "57fe9ad1f30cf4de4ba065e8f006426ef079dbb32e5a5a4d91bd88922cc44055"


def _records() -> list[dict[str, object]]:
    return [json.loads(line) for line in _LOG.read_text("utf-8").splitlines() if line.strip()]


@requires_private_artifact("task7_v16_current_guard_postgres_census_receipt.json")
def test_v16_source_supersession_signed_census_and_freeze_are_append_only() -> None:
    records = _records()
    assert len(records) >= 29
    prefix = ("\n".join(_LOG.read_text("utf-8").splitlines()[:29]) + "\n").encode("utf-8")
    assert hashlib.sha256(prefix).hexdigest() == _FIRST_29_RECORDS_SHA256
    by_event = {str(record["event"]): record for record in records}

    assert by_event["V16_PRE_FREEZE_CENSUS_SOURCE_SET_SUPERSEDED"]["current_source_hashes"][
        "census_runner_sha256"
    ] == ("6974b148b4e7f8b7fcd85ab476283f117c246e39058ff0acc920766180747e43")
    signed = by_event["V16_PRE_FREEZE_APP_ROLE_CENSUS_REEXECUTED_AND_SIGNED"]
    assert signed["signed_receipt_sha256"] == hashlib.sha256(_RECEIPT.read_bytes()).hexdigest()
    assert signed["signature_verified"] is True
    assert signed["census"] == {
        **signed["census"],
        "application_role_case_count": 219,
        "legal_acceptance_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "constructor_roundtrip_count": 4,
        "admin_case_execution_count": 0,
        "unexpected_outcome_count": 0,
    }

    frozen = by_event["V16_ATOMIC_FREEZE_ADMITTED"]
    assert frozen["artifact_sha256"] == (
        "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
    )
    assert frozen["fixture_sha256"] == (
        "999dafef745feaa56448e0a84ebed3aff37729c3d85f98267ae72a19d702ab79"
    )
    assert frozen["pipeline_contract_sha256"] == (
        "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
    )
    assert frozen["runtime_state"] == "INACTIVE"
    assert frozen["migration_0038_consumed_candidate"] is False
    assert frozen["runtime_consumed_candidate"] is False


def test_both_v5_candidate_lifecycles_are_explicit_and_never_conflated() -> None:
    by_event = {str(record["event"]): record for record in _records()}
    diagnostic = by_event["V5_FIRST_PRE_FREEZE_CANDIDATE_LIFECYCLE_COMPLETED"]
    corrected = by_event["V5_CORRECTED_FROZEN_CANDIDATE_VOIDED"]

    assert diagnostic["append_freeze_reached"] is False
    assert diagnostic["never_frozen"] is True
    assert diagnostic["never_signed"] is True
    assert len(diagnostic["permanent_blocklist_hashes"]) == 3

    assert corrected["append_freeze_reached"] is True
    assert corrected["never_signed"] is True
    assert corrected["migration_0038_consumption_status"] == "NOT_EVIDENCED"
    assert len(corrected["permanent_blocklist_hashes"]) == 3
