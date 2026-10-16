"""Closed contract for the full-strict-v10 pre-freeze PostgreSQL census.

The executable census starts from migration 0037 and installs only the SQL
decoded from the unfrozen v10 artifact into a disposable scaffold.  It never
claims migration 0038 installation and never writes the frozen fixture or the
operator receipt.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, cast

from tests.slice100_task7.test_full_strict_v8_current_guard_authority_red import (
    V8_AUTHORITY_TARGETS,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v10_reviewed_preimage_v1.json"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v10_current_guard_census.py"

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v10-current-guard-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v10-current-guard-census-result-v1"

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
CASE_KINDS: Final[tuple[CaseKind, ...]] = (
    "LEGAL",
    "ILLEGAL",
    "DIRECT_SQL",
)

PRODUCTION_CONSTRUCTOR_SPECS: Final = (
    (
        "validated-evidence-package-native",
        "task7-validated-evidence-package-native-v1",
        "idis.models.layer1_durability.ValidatedEvidencePackageRow.from_record",
        "validated_evidence_packages",
    ),
    (
        "muhasabah-native",
        "task7-muhasabah-native-v1",
        "idis.models.layer1_durability.MuhasabahRecordRow.from_debate_record",
        "muhasabah_records",
    ),
    (
        "layer2-challenge-native",
        "task7-layer2-challenge-native-v1",
        "idis.models.layer2_durability.Layer2ChallengeRow.from_record",
        "layer2_ic_challenges",
    ),
    (
        "debate-session-native",
        "task7-debate-session-native-v1",
        "idis.debate.orchestrator.DebateOrchestrator.run",
        "debate_sessions",
    ),
)

EXPECTED_AUTHORITY_TARGETS: Final = {
    authority_id: tuple(targets) for authority_id, targets in V8_AUTHORITY_TARGETS.items()
}
EXPECTED_ORDERED_TARGETS: Final = tuple(
    (authority_id, table_name, guard_name)
    for authority_id, targets in EXPECTED_AUTHORITY_TARGETS.items()
    for table_name, guard_name in targets
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")


def _decode_without_duplicate_members(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            assert key not in result, f"TASK7_V10_CENSUS_ARTIFACT_DUPLICATE_MEMBER:{key}"
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    assert isinstance(value, dict)
    return value


def artifact_and_raw() -> tuple[dict[str, Any], bytes]:
    """Load the exact candidate bytes without mutating or freezing them."""

    raw = _ARTIFACT_PATH.read_bytes()
    artifact = _decode_without_duplicate_members(raw)
    return artifact, raw


def current_authority_rows() -> tuple[dict[str, Any], ...]:
    """Return the eleven current rows from the v10 artifact."""

    artifact, _raw = artifact_and_raw()
    rows = artifact["sql_authority_registry"]["rows"]
    current = tuple(
        cast(dict[str, Any], row) for row in rows if int(row["authority_ordinal"]) >= 17
    )
    assert len(current) == 11
    return current


def expected_locator_nodes() -> tuple[str, ...]:
    """Return the exact historical RED-node locators bound by v10."""

    return tuple(
        locator
        for row in current_authority_rows()
        for field in (
            "legal_red_case_tuple",
            "illegal_red_case_tuple",
            "direct_sql_red_case_tuple",
        )
        for locator in row[field]
    )


def assert_reviewed_target_and_locator_closure() -> None:
    """Prove the artifact names exactly 73 reviewed targets and 33 RED nodes."""

    rows = current_authority_rows()
    actual_by_authority = {
        str(row["authority_id"]): tuple(
            tuple(cast(Sequence[str], target)) for target in row["target_tuple"]
        )
        for row in rows
    }
    assert actual_by_authority == EXPECTED_AUTHORITY_TARGETS
    assert len(EXPECTED_ORDERED_TARGETS) == 73
    assert len(set(EXPECTED_ORDERED_TARGETS)) == 73

    module_prefix = (
        "tests/slice100_task7/test_full_strict_v8_current_guard_postgres_census_red.py::"
    )
    locators = expected_locator_nodes()
    assert len(locators) == 33
    assert len(set(locators)) == 33
    assert all(locator.startswith(module_prefix) for locator in locators)


@cache
def _load_runner() -> ModuleType | None:
    if not _RUNNER_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "_task7_v10_current_guard_census_runner",
        _RUNNER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _runner() -> ModuleType:
    module = _load_runner()
    assert module is not None, (
        "TASK7_V10_EXECUTABLE_CENSUS_RUNNER_MISSING:scripts/run_task7_v10_current_guard_census.py"
    )
    assert getattr(module, "HARNESS_IDENTITY", None) == HARNESS_IDENTITY
    assert getattr(module, "BASE_SCHEMA_REVISION", None) == BASE_SCHEMA_REVISION
    run = getattr(module, "run_pre_fixture_census", None)
    assert callable(run), "TASK7_V10_CENSUS_ENTRYPOINT_MISSING"
    return module


@cache
def census_result() -> dict[str, Any]:
    """Execute the pre-fixture census once per pytest process."""

    assert_reviewed_target_and_locator_closure()
    module = _runner()
    value = module.run_pre_fixture_census(
        artifact_path=_ARTIFACT_PATH,
        base_schema_revision=BASE_SCHEMA_REVISION,
        harness_identity=HARNESS_IDENTITY,
        ordered_authority_target_tuple=EXPECTED_ORDERED_TARGETS,
        production_constructor_spec_tuple=PRODUCTION_CONSTRUCTOR_SPECS,
    )
    assert isinstance(value, Mapping), "TASK7_V10_CENSUS_RESULT_NOT_MAPPING"
    result = dict(value)
    assert_complete_census_result(result)
    return result


def expected_case_coordinates() -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (case_kind, authority_id, table_name, guard_name)
        for case_kind in CASE_KINDS
        for authority_id, table_name, guard_name in EXPECTED_ORDERED_TARGETS
    )


def _assert_digest(value: Any, *, field: str) -> None:
    assert isinstance(value, str) and _SHA256_RE.fullmatch(value), (
        f"TASK7_V10_CENSUS_DIGEST_INVALID:{field}"
    )


def assert_complete_census_result(result: Mapping[str, Any]) -> None:
    """Reject count-only, 0038-backed, partial, or version-leaking evidence."""

    artifact, raw = artifact_and_raw()
    assert result.get("schema_version") == RESULT_SCHEMA
    assert result.get("harness_identity") == HARNESS_IDENTITY
    assert result.get("base_schema_revision") == BASE_SCHEMA_REVISION
    assert result.get("installation_mode") == INSTALLATION_MODE
    assert "migration_revision" not in result
    assert result.get("database_product") == "PostgreSQL"
    server_version = result.get("server_version_num")
    assert isinstance(server_version, int) and server_version >= 160000
    assert result.get("artifact_schema") == artifact["metadata"]["artifact_schema"]
    assert result.get("pipeline_contract_version") == "full-strict-v10"
    assert result.get("candidate_append_frozen") is False
    assert result.get("candidate_artifact_sha256") == hashlib.sha256(raw).hexdigest()
    prospective = result.get("prospective_frozen_artifact_sha256")
    _assert_digest(prospective, field="prospective_frozen_artifact_sha256")
    assert prospective != result["candidate_artifact_sha256"]

    ordered_targets = tuple(
        tuple(target)
        for target in cast(
            Sequence[Sequence[str]],
            result.get("ordered_authority_target_tuple", ()),
        )
    )
    assert ordered_targets == EXPECTED_ORDERED_TARGETS

    outcomes = cast(Sequence[Mapping[str, Any]], result.get("case_outcome_tuple", ()))
    assert len(outcomes) == 219
    assert (
        tuple(
            (
                row.get("case_kind"),
                row.get("authority_id"),
                row.get("table_name"),
                row.get("guard_name"),
            )
            for row in outcomes
        )
        == expected_case_coordinates()
    )
    allowed_keys = (
        "case_kind",
        "authority_id",
        "table_name",
        "guard_name",
        "execution_path",
        "outcome",
        "sqlstate",
        "authority_rejection_marker",
        "rejection_provenance",
    )
    for row in outcomes:
        assert tuple(row) == allowed_keys
        kind = cast(CaseKind, row["case_kind"])
        expected_path = "DIRECT_SQL" if kind == "DIRECT_SQL" else "GUARDED_OPERATION"
        assert row["execution_path"] == expected_path
        if kind == "LEGAL":
            assert row["outcome"] == "ACCEPTED"
            assert row["sqlstate"] is None
            assert row["authority_rejection_marker"] is None
            assert row["rejection_provenance"] is None
        else:
            assert row["outcome"] == "REJECTED"
            assert isinstance(row["sqlstate"], str)
            assert _SQLSTATE_RE.fullmatch(row["sqlstate"])
            assert isinstance(row["authority_rejection_marker"], str)
            assert row["authority_rejection_marker"].strip()
            assert row["rejection_provenance"] == "HASH_BOUND_AUTHORITY"

    proofs = cast(Sequence[Mapping[str, Any]], result.get("constructor_proof_tuple", ()))
    assert len(proofs) == 4
    assert (
        tuple(
            (
                proof.get("schema_id"),
                proof.get("schema_version"),
                proof.get("constructor_qualname"),
                proof.get("durable_table"),
            )
            for proof in proofs
        )
        == PRODUCTION_CONSTRUCTOR_SPECS
    )
    proof_keys = (
        "schema_id",
        "schema_version",
        "constructor_qualname",
        "durable_table",
        "outcome",
        "original_payload_sha256",
        "reloaded_payload_sha256",
    )
    for proof in proofs:
        assert tuple(proof) == proof_keys
        assert proof["outcome"] == "ROUNDTRIP_MATCH"
        _assert_digest(
            proof["original_payload_sha256"],
            field=f"{proof['schema_id']}.original_payload_sha256",
        )
        assert proof["reloaded_payload_sha256"] == proof["original_payload_sha256"]

    assert result.get("current_guard_target_count") == 73
    assert result.get("legal_execution_count") == 73
    assert result.get("illegal_rejection_count") == 73
    assert result.get("direct_sql_rejection_count") == 73
    assert result.get("constructor_schema_proof_count") == 4
    assert result.get("unexpected_outcome_count") == 0
    for field in (
        "catalog_projection_sha256",
        "case_result_sha256",
        "constructor_roundtrip_sha256",
        "manifest_parity_sha256",
    ):
        _assert_digest(result.get(field), field=field)

    # Historical runner labels must not contaminate the v10 evidence payload.
    assert "v9" not in json.dumps(result, sort_keys=True).lower()
