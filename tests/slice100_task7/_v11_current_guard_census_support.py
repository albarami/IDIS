"""Closed contract for the full-strict-v11 pre-freeze PostgreSQL census.

The v11 census is deliberately independent from migration 0038.  It migrates
one disposable PostgreSQL database to 0037, installs only the unfrozen
artifact SQL, and proves the exact current-trigger event inventory.
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
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v11_reviewed_preimage_v1.json"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v11_current_guard_census.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v11_contract_fixture.py"

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v11-current-guard-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v11-current-guard-census-result-v1"

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
TriggerEvent = Literal["INSERT", "UPDATE", "DELETE"]
CASE_KINDS: Final[tuple[CaseKind, ...]] = ("LEGAL", "ILLEGAL", "DIRECT_SQL")
EVENT_ORDER: Final[tuple[TriggerEvent, ...]] = ("INSERT", "UPDATE", "DELETE")

TASK6_WRITE_CONTEXT_GUCS: Final = (
    "idis.worker_lease_tenant_id",
    "idis.worker_lease_deal_id",
    "idis.worker_lease_run_id",
    "idis.worker_lease_owner_id",
    "idis.worker_lease_fencing_token",
)
TASK6_OUTPUT_ROOT_UPDATE_TARGETS: Final = (
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
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
_TRIGGER_EVENT_RE = re.compile(
    r"\b(?:BEFORE|AFTER)\s+"
    r"(?P<events>(?:INSERT|UPDATE|DELETE)(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)"
    r"\s+ON\s+public\.(?P<table>[a-z][a-z0-9_]*)",
)
_AUTHORITY_EVENTS: Final[dict[str, tuple[TriggerEvent, ...]]] = {
    "current-source-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
    "current-output-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
    "current-receipt-equality-v1": ("INSERT", "UPDATE", "DELETE"),
    "current-input-binding-admission-v1": ("INSERT", "UPDATE", "DELETE"),
    "current-append-only-lease-v1": ("INSERT", "UPDATE", "DELETE"),
    "current-source-bound-v1": ("UPDATE", "DELETE"),
    "current-output-bound-v1": ("UPDATE", "DELETE"),
    "current-runs-contract-immutable-v1": ("UPDATE",),
    "current-run-steps-contract-immutable-v1": ("UPDATE",),
    "current-task5-coverage-replacement-v1": ("INSERT", "UPDATE", "DELETE"),
    "current-task6-immutable-replacement-v1": ("INSERT", "UPDATE", "DELETE"),
}
_AUTHORITY_FUNCTIONS: Final = {
    "current-source-exact-one-v1": "idis_task7_current_source_exact_one_v1",
    "current-output-exact-one-v1": "idis_task7_current_output_exact_one_v1",
    "current-receipt-equality-v1": "idis_task7_current_receipt_equality_v1",
    "current-input-binding-admission-v1": "idis_task7_current_input_binding_admission_v1",
    "current-append-only-lease-v1": "idis_task7_current_append_only_lease_v1",
    "current-source-bound-v1": "idis_task7_current_source_bound_v1",
    "current-output-bound-v1": "idis_task7_current_output_bound_v1",
    "current-runs-contract-immutable-v1": "idis_task7_current_runs_contract_immutable_v1",
    "current-run-steps-contract-immutable-v1": (
        "idis_task7_current_run_steps_contract_immutable_v1"
    ),
    "current-task5-coverage-replacement-v1": "idis_task5_coverage_scope_guard",
    "current-task6-immutable-replacement-v1": "idis_task6_immutable_guard",
}


def _decode_without_duplicate_members(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            assert key not in result, f"TASK7_V11_CENSUS_ARTIFACT_DUPLICATE_MEMBER:{key}"
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    assert isinstance(value, dict)
    return value


def artifact_and_raw() -> tuple[dict[str, Any], bytes]:
    """Load the exact unfrozen v11 bytes."""

    assert _ARTIFACT_PATH.is_file(), (
        "TASK7_V11_CENSUS_ARTIFACT_MISSING:contracts/full_strict_v11_reviewed_preimage_v1.json"
    )
    raw = _ARTIFACT_PATH.read_bytes()
    artifact = _decode_without_duplicate_members(raw)
    return artifact, raw


def current_authority_rows() -> tuple[dict[str, Any], ...]:
    artifact, _raw = artifact_and_raw()
    rows = artifact["sql_authority_registry"]["rows"]
    current = tuple(
        cast(dict[str, Any], row) for row in rows if int(row["authority_ordinal"]) >= 17
    )
    assert len(current) == 11
    return current


def expected_trigger_event_inventory() -> tuple[tuple[str, str, str, str], ...]:
    """Return the reviewed 192 target-event coordinates.

    Task-5/Task-6 triggers are retained from 0037, so their trigger DDL is not
    repeated in the replacement-body artifact rows.  The census proves this
    reviewed inventory against ``pg_trigger`` after installing the artifact.
    Where the artifact does carry trigger DDL, this helper also cross-checks it.
    """

    result: list[tuple[str, str, str, str]] = []
    for row in current_authority_rows():
        statements = tuple(
            bytes.fromhex(str(value)).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]
        )
        authority_id = str(row["authority_id"])
        for target in row["target_tuple"]:
            table_name, guard_name = (str(value) for value in target)
            matches = [
                statement
                for statement in statements
                if re.search(
                    rf"\bTRIGGER\s+{re.escape(guard_name)}\b",
                    statement,
                )
            ]
            assert len(matches) <= 1, (
                f"TASK7_V11_CENSUS_TRIGGER_DEFINITION_NOT_UNIQUE:{table_name}.{guard_name}"
            )
            expected_events = _AUTHORITY_EVENTS[authority_id]
            if matches:
                event_match = _TRIGGER_EVENT_RE.search(matches[0])
                assert event_match is not None
                assert event_match.group("table") == table_name
                assert frozenset(event_match.group("events").split(" OR ")) == frozenset(
                    expected_events
                )
            for event in EVENT_ORDER:
                if event in expected_events:
                    result.append((authority_id, table_name, guard_name, event))
    inventory = tuple(result)
    assert len(EXPECTED_ORDERED_TARGETS) == 73
    assert len(inventory) == 192
    assert len(set(inventory)) == 192
    return inventory


def expected_trigger_projection() -> tuple[tuple[Any, ...], ...]:
    """Return the exact reviewed pg_trigger projection, ordered by target."""

    result: list[tuple[Any, ...]] = []
    for authority_id, table_name, guard_name in EXPECTED_ORDERED_TARGETS:
        constraint = authority_id in {
            "current-source-exact-one-v1",
            "current-output-exact-one-v1",
            "current-receipt-equality-v1",
        }
        result.append(
            (
                authority_id,
                table_name,
                guard_name,
                "AFTER" if constraint else "BEFORE",
                list(_AUTHORITY_EVENTS[authority_id]),
                "ROW",
                "O",
                "CONSTRAINT" if constraint else "PLAIN",
                constraint,
                constraint,
                "public",
                _AUTHORITY_FUNCTIONS[authority_id],
            )
        )
    return tuple(result)


@cache
def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_task7_v11_census_test_generator",
        _GENERATOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: Any) -> str:
    return hashlib.sha256(_generator()._canonical_bytes(value)).hexdigest()


def expected_rejection(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> tuple[str, str]:
    """Return the exact server SQLSTATE/marker required for a rejected case."""

    assert case_kind != "LEGAL"
    if authority_id == "current-source-exact-one-v1":
        return "23514", "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    if authority_id == "current-output-exact-one-v1":
        return "23514", "STEP_OUTPUT_MISSING"
    if authority_id == "current-receipt-equality-v1":
        return "23514", "CONSUMER_RECEIPT_INCOMPLETE"
    if authority_id == "current-input-binding-admission-v1":
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id == "current-append-only-lease-v1":
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id == "current-source-bound-v1":
        return "40001", "TASK7_SOURCE_FREEZE_FENCED"
    if authority_id == "current-output-bound-v1":
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id in {
        "current-runs-contract-immutable-v1",
        "current-run-steps-contract-immutable-v1",
    }:
        return "23514", "PIPELINE_CONTRACT_HASH_MISMATCH"
    if authority_id == "current-task5-coverage-replacement-v1":
        return "23514", "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE"
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in TASK6_OUTPUT_ROOT_UPDATE_TARGETS
        and case_kind == "ILLEGAL"
    ):
        return "40001", "WORKER_LEASE_FENCED"
    return "23514", "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE"


def expected_event_for_case(
    case_kind: CaseKind,
    authority_id: str,
    table_name: str,
    guard_name: str,
) -> TriggerEvent:
    """Choose one real event per outcome while covering every declared event."""

    events = tuple(
        cast(TriggerEvent, event)
        for authority, table, guard, event in expected_trigger_event_inventory()
        if (authority, table, guard) == (authority_id, table_name, guard_name)
    )
    assert events
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in TASK6_OUTPUT_ROOT_UPDATE_TARGETS
    ):
        mapping: dict[CaseKind, TriggerEvent] = {
            "LEGAL": "UPDATE",
            "ILLEGAL": "INSERT",
            "DIRECT_SQL": "DELETE",
        }
        assert frozenset(mapping.values()) == frozenset(events)
        return mapping[case_kind]
    if case_kind == "LEGAL":
        return "INSERT" if "INSERT" in events else "UPDATE"
    if case_kind == "ILLEGAL":
        return "UPDATE" if "UPDATE" in events else events[0]
    return "DELETE" if "DELETE" in events else "UPDATE"


def expected_case_coordinates() -> tuple[tuple[str, str, str, str, str], ...]:
    return tuple(
        (
            case_kind,
            authority_id,
            table_name,
            guard_name,
            expected_event_for_case(case_kind, authority_id, table_name, guard_name),
        )
        for case_kind in CASE_KINDS
        for authority_id, table_name, guard_name in EXPECTED_ORDERED_TARGETS
    )


@cache
def _load_runner() -> ModuleType | None:
    if not _RUNNER_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "_task7_v11_current_guard_census_runner",
        _RUNNER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def runner() -> ModuleType:
    module = _load_runner()
    assert module is not None, (
        "TASK7_V11_EXECUTABLE_CENSUS_RUNNER_MISSING:scripts/run_task7_v11_current_guard_census.py"
    )
    assert getattr(module, "HARNESS_IDENTITY", None) == HARNESS_IDENTITY
    assert getattr(module, "BASE_SCHEMA_REVISION", None) == BASE_SCHEMA_REVISION
    assert getattr(module, "RESULT_SCHEMA", None) == RESULT_SCHEMA
    assert getattr(module, "TASK6_WRITE_CONTEXT_GUCS", None) == TASK6_WRITE_CONTEXT_GUCS
    run = getattr(module, "run_pre_fixture_census", None)
    assert callable(run), "TASK7_V11_CENSUS_ENTRYPOINT_MISSING"
    return module


@cache
def census_result() -> dict[str, Any]:
    module = runner()
    value = module.run_pre_fixture_census(
        artifact_path=_ARTIFACT_PATH,
        base_schema_revision=BASE_SCHEMA_REVISION,
        harness_identity=HARNESS_IDENTITY,
        ordered_authority_target_tuple=EXPECTED_ORDERED_TARGETS,
        ordered_authority_target_event_tuple=expected_trigger_event_inventory(),
        production_constructor_spec_tuple=PRODUCTION_CONSTRUCTOR_SPECS,
    )
    assert isinstance(value, Mapping)
    result = dict(value)
    assert_complete_census_result(result)
    return result


def _assert_digest(value: Any, *, field: str) -> None:
    assert isinstance(value, str) and _SHA256_RE.fullmatch(value), (
        f"TASK7_V11_CENSUS_DIGEST_INVALID:{field}"
    )


def assert_complete_census_result(result: Mapping[str, Any]) -> None:
    """Reject count-only, 0038-backed, partial, or event-masking evidence."""

    artifact, raw = artifact_and_raw()
    assert result.get("schema_version") == RESULT_SCHEMA
    assert result.get("harness_identity") == HARNESS_IDENTITY
    assert result.get("base_schema_revision") == BASE_SCHEMA_REVISION
    assert result.get("alembic_version_before_install") == BASE_SCHEMA_REVISION
    assert result.get("alembic_version_after_install") == BASE_SCHEMA_REVISION
    assert result.get("alembic_version_after_cases") == BASE_SCHEMA_REVISION
    assert result.get("installation_mode") == INSTALLATION_MODE
    assert "migration_revision" not in result
    assert result.get("database_product") == "PostgreSQL"
    server_version = result.get("server_version_num")
    assert isinstance(server_version, int) and server_version >= 160000
    assert result.get("artifact_schema") == artifact["metadata"]["artifact_schema"]
    assert result.get("pipeline_contract_version") == "full-strict-v11"
    assert result.get("candidate_append_frozen") is False
    assert result.get("candidate_artifact_sha256") == hashlib.sha256(raw).hexdigest()
    assert (
        result.get("prospective_frozen_artifact_sha256")
        == hashlib.sha256(_generator().derive_prospective_frozen_bytes(raw)).hexdigest()
    )

    ordered_targets = tuple(
        tuple(target)
        for target in cast(
            Sequence[Sequence[str]],
            result.get("ordered_authority_target_tuple", ()),
        )
    )
    assert ordered_targets == EXPECTED_ORDERED_TARGETS
    event_inventory = tuple(
        tuple(item)
        for item in cast(
            Sequence[Sequence[str]],
            result.get("trigger_event_inventory_tuple", ()),
        )
    )
    assert event_inventory == expected_trigger_event_inventory()
    trigger_projection = tuple(
        tuple(item)
        for item in cast(
            Sequence[Sequence[Any]],
            result.get("pg_trigger_projection_tuple", ()),
        )
    )
    assert trigger_projection == expected_trigger_projection()
    assert result.get("pg_trigger_inventory_sha256") == digest(
        ["task7-v11-pg-trigger-projection-v1", trigger_projection]
    )

    outcomes = cast(Sequence[Mapping[str, Any]], result.get("case_outcome_tuple", ()))
    assert len(outcomes) == 219
    assert (
        tuple(
            (
                row.get("case_kind"),
                row.get("authority_id"),
                row.get("table_name"),
                row.get("guard_name"),
                row.get("trigger_event"),
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
        "trigger_event",
        "trigger_timing",
        "constraint_flush_attempted",
        "scenario_id",
        "execution_path",
        "outcome",
        "sqlstate",
        "authority_rejection_marker",
        "rejection_provenance",
    )
    for row in outcomes:
        assert tuple(row) == allowed_keys
        kind = cast(CaseKind, row["case_kind"])
        authority_id = str(row["authority_id"])
        constraint = authority_id in {
            "current-source-exact-one-v1",
            "current-output-exact-one-v1",
            "current-receipt-equality-v1",
        }
        assert row["trigger_timing"] == ("AFTER" if constraint else "BEFORE")
        assert row["constraint_flush_attempted"] is constraint
        assert isinstance(row["scenario_id"], str) and row["scenario_id"]
        expected_path = "DIRECT_SQL" if kind == "DIRECT_SQL" else "GUARDED_OPERATION"
        assert row["execution_path"] == expected_path
        if kind == "LEGAL":
            assert row["outcome"] == "ACCEPTED"
            assert row["sqlstate"] is None
            assert row["authority_rejection_marker"] is None
            assert row["rejection_provenance"] is None
        else:
            assert row["outcome"] == "REJECTED"
            expected_sqlstate, expected_marker = expected_rejection(
                authority_id=authority_id,
                table_name=str(row["table_name"]),
                case_kind=kind,
            )
            assert row["sqlstate"] == expected_sqlstate
            assert _SQLSTATE_RE.fullmatch(row["sqlstate"])
            assert row["authority_rejection_marker"] == expected_marker
            assert row["rejection_provenance"] == "HASH_BOUND_AUTHORITY"

    covered = tuple(
        sorted(
            {
                (
                    str(row["authority_id"]),
                    str(row["table_name"]),
                    str(row["guard_name"]),
                    str(row["trigger_event"]),
                )
                for row in outcomes
            }
        )
    )
    assert covered == tuple(sorted(expected_trigger_event_inventory()))
    assert result.get("current_guard_target_count") == 73
    assert result.get("current_guard_target_event_count") == 192
    assert result.get("case_outcome_count") == 219
    assert result.get("legal_execution_count") == 73
    assert result.get("illegal_rejection_count") == 73
    assert result.get("direct_sql_rejection_count") == 73
    assert result.get("constructor_schema_proof_count") == 4
    assert result.get("unexpected_outcome_count") == 0
    assert tuple(result.get("task6_write_context_guc_tuple", ())) == TASK6_WRITE_CONTEXT_GUCS
    assert result.get("task6_output_root_update_acceptance_count") == 2
    assert result.get("task6_task_span_update_rejection_count") == 1
    assert result.get("strict_input_binding_queued_admission_count") == 1
    assert result.get("expected_edge_queued_admission_count") == 1
    assert result.get("package_open_to_sealed_binding_count") == 1
    assert result.get("methodology_commercial_question_count") == 21
    assert result.get("methodology_financial_question_count") == 79
    assert result.get("methodology_coverage_count") == 100
    fixture_rejections = tuple(
        tuple(item)
        for item in cast(
            Sequence[Sequence[str]],
            result.get("fixture_rejection_proof_tuple", ()),
        )
    )
    assert fixture_rejections == (
        (
            "UNKNOWN_FIELD",
            "runs",
            "unknown_fixture_field",
            "TASK7_V11_CENSUS_FIXTURE_UNKNOWN_FIELD:runs.unknown_fixture_field",
        ),
        (
            "OTHER_TARGET_FIELD",
            "runs",
            "step_id",
            "TASK7_V11_CENSUS_FIXTURE_OTHER_TARGET_FIELD:runs.step_id",
        ),
        (
            "GENERATED_FIELD",
            "methodology_extraction_results",
            "run_output_object_kind",
            "TASK7_V11_CENSUS_FIXTURE_GENERATED_FIELD:"
            "methodology_extraction_results.run_output_object_kind",
        ),
    )
    seed_proof = cast(Mapping[str, Any], result.get("seed_proof_projection"))
    assert tuple(seed_proof) == (
        "schema_version",
        "strict_input_binding",
        "methodology_selection",
        "task6_write_context",
    )
    assert seed_proof["schema_version"] == "task7-v11-seed-proof-projection-v1"
    assert seed_proof["strict_input_binding"] == {
        "schema_version": "task7-v11-strict-input-seed-proof-v1",
        "run_status": "QUEUED",
        "package_status": "SEALED",
        "package_binding_state": "BOUND",
        "proof_consumed": True,
        "proof_pipeline_contract_version": "full-strict-v11",
        "contract_version_match": True,
        "contract_hash_match": True,
        "source_hash_match": True,
        "corpus_hash_equality_count": 4,
    }
    assert seed_proof["methodology_selection"] == {
        "schema_version": "task7-v11-methodology-seed-proof-v1",
        "selection_count": 1,
        "selected_question_count": 100,
        "frozen_version_count": 2,
        "commercial_version_count": 1,
        "financial_version_count": 1,
        "commercial_question_count": 21,
        "financial_question_count": 79,
        "coverage_count": 100,
        "commercial_lineage_match_count": 21,
        "financial_lineage_match_count": 79,
        "commercial_version_hash_match": True,
        "financial_version_hash_match": True,
        "commercial_source_hash_match_count": 21,
        "financial_source_hash_match_count": 79,
    }
    task6_proof = cast(Mapping[str, Any], seed_proof["task6_write_context"])
    assert tuple(task6_proof) == (
        "schema_version",
        "guc_projection_tuple",
        "transaction_local_value_match_count",
        "post_transaction_empty_guc_tuple",
        "post_transaction_clear_count",
        "output_root_update_acceptance_count",
        "task_span_update_rejection_count",
    )
    assert task6_proof["schema_version"] == "task7-v11-task6-guc-proof-v1"
    gucs = cast(Sequence[Sequence[Any]], task6_proof["guc_projection_tuple"])
    assert tuple(row[0] for row in gucs) == TASK6_WRITE_CONTEXT_GUCS
    assert len(gucs) == 5
    for name, value_type, expected_sha256, observed_sha256, matched in gucs:
        assert name in TASK6_WRITE_CONTEXT_GUCS
        assert value_type in {"UUID", "BIGINT"}
        _assert_digest(expected_sha256, field=f"{name}.expected_sha256")
        assert observed_sha256 == expected_sha256
        assert matched is True
    assert task6_proof["transaction_local_value_match_count"] == 5
    assert tuple(tuple(row) for row in task6_proof["post_transaction_empty_guc_tuple"]) == tuple(
        (name, "") for name in TASK6_WRITE_CONTEXT_GUCS
    )
    assert task6_proof["post_transaction_clear_count"] == 5
    assert task6_proof["output_root_update_acceptance_count"] == 2
    assert task6_proof["task_span_update_rejection_count"] == 1
    assert result.get("seed_proof_sha256") == digest(
        ["task7-v11-seed-proof-projection-v1", seed_proof]
    )

    proofs = cast(Sequence[Mapping[str, Any]], result.get("constructor_proof_tuple", ()))
    assert len(proofs) == 4
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

    assert result.get("complete_execution_sha256") == digest(
        [
            "task7-v11-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    for field in (
        "catalog_projection_sha256",
        "case_result_sha256",
        "constructor_roundtrip_sha256",
        "manifest_parity_sha256",
        "pg_trigger_inventory_sha256",
        "trigger_event_coverage_sha256",
        "seed_proof_sha256",
        "complete_execution_sha256",
    ):
        _assert_digest(result.get(field), field=field)
    assert "v10" not in json.dumps(result, sort_keys=True).lower()


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CASE_KINDS",
    "EXPECTED_AUTHORITY_TARGETS",
    "EXPECTED_ORDERED_TARGETS",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "PRODUCTION_CONSTRUCTOR_SPECS",
    "RESULT_SCHEMA",
    "TASK6_OUTPUT_ROOT_UPDATE_TARGETS",
    "TASK6_WRITE_CONTEXT_GUCS",
    "artifact_and_raw",
    "assert_complete_census_result",
    "census_result",
    "current_authority_rows",
    "expected_case_coordinates",
    "expected_event_for_case",
    "expected_trigger_event_inventory",
    "runner",
]
