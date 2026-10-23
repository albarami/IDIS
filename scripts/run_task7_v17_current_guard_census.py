"""Execute the unfrozen full-strict-v17 guard census as the application role.

The append-frozen v16 runner remains immutable.  V17 hash-pins and adapts that
reviewed engine only at its explicit version/count seams, installs the v17 SQL
through a pinned scaffold adapter, and adds a closed PostgreSQL execution proof
for the six source projections, post-install BACKFILL rejection, and the
OPEN-versus-SEALED package-file INSERT boundary.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import re
import secrets
import sys
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError


class CensusError(RuntimeError):
    """Secret-safe v17 census failure."""


_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
_V16_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v16_current_guard_census.py")
_V16_RUNNER_SHA256: Final = "6974b148b4e7f8b7fcd85ab476283f117c246e39058ff0acc920766180747e43"
_V16_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v16_census_scaffold.py")
_V16_SCAFFOLD_SHA256: Final = "07da0191eca16bab01dbe15ad6fc9f6ad883fdc3106fc989eb7d233a4a6de7bb"
_V17_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v17_census_scaffold.py")
_V17_MATERIALIZATION_SOURCE_MANIFEST: Final = (
    (
        "contracts/full_strict_v17_reviewed_preimage_v1.json",
        6_315_306,
        "ae6d42df18d236fad3730be9829fe66b19549e7902c06ea9c62ad7c6d250a5e7",
        "unfrozen-candidate",
    ),
    (
        "scripts/_task7_v17_census_scaffold.py",
        5_472,
        "2cde9e1927defc64e2a19924371c9d678c2f8fc0b9e2adb54d77ab84bf867157",
        "postgres-census-scaffold",
    ),
    (
        "scripts/materialize_full_strict_v17_preimage.py",
        50_397,
        "762a0540c7beeecc6067d494aede4bc47509680c1ecda9085b8abe0274f18b5e",
        "candidate-materializer",
    ),
    (
        "scripts/task7_v17_receipt_guard_sql.py",
        9_380,
        "93ed39c6d8ad018ee8629ea3989381ad39b7a4193f58354c161e7b400a27db1c",
        "receipt-guard-sql-constructor",
    ),
    (
        "scripts/task7_v17_source_projection_sql.py",
        52_970,
        "3d87ec27b57956ec3e3f1fad47681479616462b963f68303cfd3931b92161f11",
        "source-projection-sql-constructor",
    ),
)
_V16_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V17_ARTIFACT_SCHEMA: Final = "full-strict-v17-reviewed-preimage-v1"
_V17_CONTRACT_VERSION: Final = "full-strict-v17"
_V16_CONTRACT_VERSION: Final = "full-strict-v16"
_CURRENT_SCOPE: Final = "CURRENT_0038"
_SOURCE_KINDS: Final = (
    "PACKAGE",
    "PACKAGE_FILE",
    "ARTIFACT",
    "DOCUMENT",
    "PARSE_ATTEMPT",
    "SPAN",
)
_CASE_KINDS: Final = ("LEGAL", "ILLEGAL", "DIRECT_SQL")
_PRODUCTION_CONSTRUCTOR_SPECS: Final = (
    (
        "validated-evidence-package-native",
        "task7-validated-evidence-package-native-v1",
        "idis.services.analysis.validated_evidence_package.ValidatedEvidencePackageService",
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
BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = (
    "task7-v17-hash-pinned-predecessor-engine-plus-source-and-receipt-proofs-v1"
)
INSTALLATION_MODE: Final = "ARTIFACT_SQL_APP_ROLE_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v17-current-guard-app-role-census-result-v1"
_CENSUS_ENVELOPE_SCHEMA: Final = "task7-v17-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v17-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN: Final = "task7-v17-postgres-census-signable-v1"


def materialization_source_manifest() -> tuple[tuple[str, int, str, str], ...]:
    """Verify and return the complete v17 materialization/census byte closure."""

    for relative_path, byte_length, sha256, _role in _V17_MATERIALIZATION_SOURCE_MANIFEST:
        try:
            raw = (_REPO_ROOT / relative_path).read_bytes()
        except OSError as exc:
            raise CensusError("TASK7_V17_MATERIALIZATION_SOURCE_DRIFT") from exc
        if len(raw) != byte_length or not secrets.compare_digest(
            hashlib.sha256(raw).hexdigest(),
            sha256,
        ):
            raise CensusError("TASK7_V17_MATERIALIZATION_SOURCE_DRIFT")
    return _V17_MATERIALIZATION_SOURCE_MANIFEST


def materialization_source_manifest_sha256() -> str:
    return _digest(
        [
            "task7-v17-materialization-source-manifest-v1",
            materialization_source_manifest_evidence(),
        ]
    )


def materialization_source_manifest_evidence() -> list[list[Any]]:
    """Return the verified source manifest in canonical JSON evidence shape."""

    return [list(row) for row in materialization_source_manifest()]


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CensusError(f"TASK7_V17_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validate_scalar_json(value: Any) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
        return
    if isinstance(value, list):
        for item in value:
            _validate_scalar_json(item)
        return
    if isinstance(value, Mapping):
        seen: set[str] = set()
        for key, item in value.items():
            if not isinstance(key, str) or key in seen or unicodedata.normalize("NFC", key) != key:
                raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
            seen.add(key)
            _validate_scalar_json(item)
        return
    raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")


def _canonical_bytes(value: Any) -> bytes:
    _validate_scalar_json(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _decode_json(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
            result[key] = value
        return result

    def reject_float(_value: str) -> Any:
        raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")

    if raw.startswith(b"\xef\xbb\xbf"):
        raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_float=reject_float,
            parse_constant=reject_float,
        )
    except (UnicodeError, ValueError) as exc:
        raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED") from exc
    if not isinstance(value, dict):
        raise CensusError("TASK7_V17_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    _validate_scalar_json(value)
    return value


def _render_artifact(artifact: Mapping[str, Any]) -> bytes:
    _validate_scalar_json(artifact)
    return (
        json.dumps(
            dict(artifact),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _validate_v17_candidate(artifact: Mapping[str, Any]) -> None:
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V17_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V17_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        raise CensusError("TASK7_V17_CENSUS_ARTIFACT_VERSION_MISMATCH")
    execution_expectations(artifact)


def _prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    artifact = _decode_json(candidate_raw)
    _validate_v17_candidate(artifact)
    frozen = copy.deepcopy(artifact)
    frozen["metadata"]["append_frozen"] = True
    return _render_artifact(frozen)


def _current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    try:
        rows = artifact["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    except (KeyError, TypeError) as exc:
        raise CensusError("TASK7_V17_TRIGGER_CONTRACT_INVENTORY_INVALID") from exc
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise CensusError("TASK7_V17_TRIGGER_CONTRACT_INVENTORY_INVALID")
    current = tuple(
        row
        for row in rows
        if isinstance(row, Mapping) and row.get("install_scope") == _CURRENT_SCOPE
    )
    signatures = tuple(str(row.get("function_signature", "")) for row in current)
    if not current or "" in signatures or len(signatures) != len(set(signatures)):
        raise CensusError("TASK7_V17_CURRENT_FUNCTION_INVENTORY_MISMATCH")
    return current


def artifact_trigger_contract(
    artifact: Mapping[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    """Derive the ordered trigger/event contract solely from artifact bytes."""

    result: list[tuple[Any, ...]] = []
    for function in _current_function_rows(artifact):
        signature = str(function.get("function_signature", ""))
        function_name = signature.partition("(")[0].removeprefix("public.")
        coordinates = function.get("target_event_coordinate_tuple")
        if not function_name or not isinstance(coordinates, Sequence):
            raise CensusError("TASK7_V17_TRIGGER_CONTRACT_INVENTORY_INVALID")
        for coordinate in coordinates:
            if not isinstance(coordinate, Sequence) or len(coordinate) != 9:
                raise CensusError("TASK7_V17_TRIGGER_CONTRACT_INVENTORY_INVALID")
            (
                table_name,
                trigger_name,
                timing,
                event,
                level,
                enabled,
                constraint,
                deferrable,
                initially_deferred,
            ) = coordinate
            result.append(
                (
                    str(function["authority_id"]),
                    str(table_name),
                    str(trigger_name),
                    str(timing),
                    str(event),
                    str(level),
                    str(enabled),
                    bool(constraint),
                    bool(deferrable),
                    bool(initially_deferred),
                    "public",
                    function_name,
                )
            )
    contract = tuple(result)
    if len(contract) != len(set(contract)):
        raise CensusError("TASK7_V17_TRIGGER_CONTRACT_INVENTORY_INVALID")
    return contract


def artifact_targets(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        dict.fromkeys(
            (str(row[0]), str(row[1]), str(row[2])) for row in artifact_trigger_contract(artifact)
        )
    )


def _v16_artifact() -> dict[str, Any]:
    raw = _V16_ARTIFACT_PATH.read_bytes()
    artifact = _decode_json(raw)
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != "full-strict-v16-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != _V16_CONTRACT_VERSION
        or metadata.get("append_frozen") is not True
    ):
        raise CensusError("TASK7_V17_V16_BASELINE_INVALID")
    return artifact


def execution_expectations(artifact: Mapping[str, Any]) -> dict[str, int]:
    """Derive v17 counts and prove the single reviewed event delta."""

    old_contract = artifact_trigger_contract(_v16_artifact())
    contract = artifact_trigger_contract(artifact)
    old_set = set(old_contract)
    new_set = set(contract)
    added = tuple(row for row in contract if row not in old_set)
    removed = tuple(row for row in old_contract if row not in new_set)
    if (
        removed
        or len(added) != 1
        or added[0][:5]
        != (
            "current-source-bound-v1",
            "data_room_package_files",
            "trg_task7_data_room_package_files_source_bound",
            "BEFORE",
            "INSERT",
        )
    ):
        raise CensusError("TASK7_V17_TRIGGER_CONTRACT_DELTA_INVALID")
    targets = artifact_targets(artifact)
    old_targets = artifact_targets(_v16_artifact())
    if targets != old_targets:
        raise CensusError("TASK7_V17_CENSUS_TARGET_INVENTORY_MISMATCH")
    return {
        "current_guard_target_count": len(targets),
        "current_guard_target_event_count": len(contract),
        "application_case_execution_count": len(targets) * len(_CASE_KINDS),
        "constructor_schema_proof_count": len(_PRODUCTION_CONSTRUCTOR_SPECS),
    }


def receipt_fixture_coordinates(
    artifact: Mapping[str, Any],
) -> tuple[int, tuple[tuple[str, str, str, int, str, str], ...]]:
    """Derive the census receipt set from the reviewed semantic registry."""

    try:
        semantic_rows = artifact["semantic_step_registry"]["rows"]
    except (KeyError, TypeError) as exc:
        raise CensusError("TASK7_V17_RECEIPT_FIXTURE_REGISTRY_INVALID") from exc
    if not isinstance(semantic_rows, list):
        raise CensusError("TASK7_V17_RECEIPT_FIXTURE_REGISTRY_INVALID")
    selected = tuple(
        row
        for row in semantic_rows
        if isinstance(row, list) and len(row) == 5 and row[1] == "EXTRACT"
    )
    if len(selected) != 1 or type(selected[0][0]) is not int:
        raise CensusError("TASK7_V17_RECEIPT_FIXTURE_REGISTRY_INVALID")
    helper = _load_script(
        _REPO_ROOT / "scripts" / "task7_v17_receipt_guard_sql.py",
        "_task7_v17_receipt_fixture_registry_helper",
    )
    try:
        coordinates = tuple(
            row for row in helper.expected_receipt_rows(semantic_rows) if row[0] == "EXTRACT"
        )
    except Exception as exc:
        raise CensusError("TASK7_V17_RECEIPT_FIXTURE_REGISTRY_INVALID") from exc
    if (
        not coordinates
        or coordinates[0][1] != "__step__"
        or tuple(row[1] for row in coordinates[1:]) != ("rag_context",)
    ):
        raise CensusError("TASK7_V17_RECEIPT_FIXTURE_REGISTRY_INVALID")
    return int(selected[0][0]), coordinates


def _receipt_equality_coordinates(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str, str, int, str, str], ...]:
    try:
        semantic_rows = artifact["semantic_step_registry"]["rows"]
    except (KeyError, TypeError) as exc:
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID") from exc
    helper = _load_script(
        _REPO_ROOT / "scripts" / "task7_v17_receipt_guard_sql.py",
        "_task7_v17_receipt_execution_registry_helper",
    )
    try:
        coordinates = tuple(helper.expected_receipt_rows(semantic_rows))
    except Exception as exc:
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID") from exc
    if (
        len(coordinates) != 83
        or sum(row[1] == "__step__" for row in coordinates) != 28
        or sum(row[1] != "__step__" for row in coordinates) != 55
        or len(coordinates) != len(set(coordinates))
    ):
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    return cast(
        tuple[tuple[str, str, str, int, str, str], ...],
        coordinates,
    )


def receipt_equality_execution_plan(
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Derive the closed 119-case receipt proof solely from registry bytes."""

    coordinates = _receipt_equality_coordinates(artifact)
    grouped: dict[str, list[tuple[str, str, str, int, str, str]]] = {}
    for coordinate in coordinates:
        grouped.setdefault(coordinate[0], []).append(coordinate)

    cases: list[dict[str, Any]] = []
    for step_name, group in grouped.items():
        cases.append(
            {
                "scenario_id": f"EXACT:{step_name}",
                "scenario_kind": "EXACT_SET",
                "step_name": step_name,
                "contract_version": _V17_CONTRACT_VERSION,
                "resume_state": "RESUMABLE",
                "coordinate_tuple": [list(row) for row in group],
                "expected_outcome": "ACCEPTED",
            }
        )
    for omitted in coordinates:
        group = grouped[omitted[0]]
        cases.append(
            {
                "scenario_id": (
                    f"OMIT:{omitted[0]}:{omitted[1]}:{omitted[2]}:"
                    f"{omitted[3]}:{omitted[4]}:{omitted[5]}"
                ),
                "scenario_kind": "OMISSION",
                "step_name": omitted[0],
                "contract_version": _V17_CONTRACT_VERSION,
                "resume_state": "RESUMABLE",
                "coordinate_tuple": [list(row) for row in group if row != omitted],
                "expected_outcome": "REJECTED",
            }
        )

    extract_group = tuple(grouped["EXTRACT"])
    rag_coordinate = next(row for row in extract_group if row[1] == "rag_context")

    def drifted(
        label: str,
        replacement: tuple[str, str, str, int, str, str],
    ) -> None:
        cases.append(
            {
                "scenario_id": f"DRIFT:{label}",
                "scenario_kind": f"{label}_DRIFT",
                "step_name": "EXTRACT",
                "contract_version": _V17_CONTRACT_VERSION,
                "resume_state": "RESUMABLE",
                "coordinate_tuple": [
                    list(replacement if row == rag_coordinate else row) for row in extract_group
                ],
                "expected_outcome": "REJECTED",
            }
        )

    cases.append(
        {
            "scenario_id": "DRIFT:EXTRA",
            "scenario_kind": "EXTRA_DRIFT",
            "step_name": "EXTRACT",
            "contract_version": _V17_CONTRACT_VERSION,
            "resume_state": "RESUMABLE",
            "coordinate_tuple": [
                *[list(row) for row in extract_group],
                [
                    "EXTRACT",
                    "unexpected_context",
                    "EXTRACT_RAG_CONTEXT_RECEIPT_V1",
                    0,
                    "2",
                    rag_coordinate[5],
                ],
            ],
            "expected_outcome": "REJECTED",
        }
    )
    drifted(
        "OUTPUT_KIND",
        (*rag_coordinate[:2], f"{rag_coordinate[2]}_DRIFT", *rag_coordinate[3:]),
    )
    drifted(
        "OUTPUT_ORDINAL",
        (*rag_coordinate[:3], rag_coordinate[3] + 1, *rag_coordinate[4:]),
    )
    drifted(
        "SEMANTIC_VERSION",
        (*rag_coordinate[:4], f"{rag_coordinate[4]}-drift", rag_coordinate[5]),
    )
    drifted(
        "SEMANTIC_HASH",
        (*rag_coordinate[:5], "0" * 64),
    )
    cases.append(
        {
            "scenario_id": "CONSUMER:NATIVE_ROW_TARGET",
            "scenario_kind": "NATIVE_CONSUMER_TARGET",
            "step_name": "EXTRACT",
            "contract_version": _V17_CONTRACT_VERSION,
            "resume_state": "RESUMABLE",
            "coordinate_tuple": [list(row) for row in extract_group],
            "expected_outcome": "REJECTED",
        }
    )
    cases.extend(
        (
            {
                "scenario_id": "SCOPE:LEGACY_CONTRACT",
                "scenario_kind": "SCOPE_EXCLUSION",
                "step_name": "EXTRACT",
                "contract_version": _V16_CONTRACT_VERSION,
                "resume_state": "RESUMABLE",
                "coordinate_tuple": [],
                "expected_outcome": "ACCEPTED",
            },
            {
                "scenario_id": "SCOPE:NON_RESUMABLE",
                "scenario_kind": "SCOPE_EXCLUSION",
                "step_name": "EXTRACT",
                "contract_version": _V17_CONTRACT_VERSION,
                "resume_state": "NON_RESUMABLE",
                "coordinate_tuple": [],
                "expected_outcome": "ACCEPTED",
            },
        )
    )
    if (
        len(grouped) != 28
        or len(cases) != 119
        or sum(row["expected_outcome"] == "ACCEPTED" for row in cases) != 30
        or sum(row["expected_outcome"] == "REJECTED" for row in cases) != 89
        or len({row["scenario_id"] for row in cases}) != len(cases)
    ):
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    return tuple(cases)


def validate_receipt_equality_execution_proof(
    proof: Any,
    *,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-derive every receipt case and its digest before admission."""

    if not isinstance(proof, Mapping) or tuple(proof) != (
        "schema_version",
        "executed_as_application_role",
        "database_actor",
        "canonical_coordinate_count",
        "aggregate_coordinate_count",
        "subrole_coordinate_count",
        "case_execution_count",
        "acceptance_count",
        "rejection_count",
        "case_outcome_tuple",
        "execution_sha256",
    ):
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    plan = receipt_equality_execution_plan(artifact)
    outcomes = proof["case_outcome_tuple"]
    actor = proof["database_actor"]
    if (
        proof["schema_version"] != "task7-v17-receipt-equality-execution-proof-v1"
        or proof["executed_as_application_role"] is not True
        or not isinstance(actor, Mapping)
        or tuple(actor)
        != (
            "role_name",
            "session_user",
            "is_superuser",
            "bypass_rls",
        )
        or not isinstance(actor["role_name"], str)
        or not actor["role_name"]
        or actor["session_user"] != actor["role_name"]
        or actor["is_superuser"] is not False
        or actor["bypass_rls"] is not False
        or proof["canonical_coordinate_count"] != 83
        or proof["aggregate_coordinate_count"] != 28
        or proof["subrole_coordinate_count"] != 55
        or proof["case_execution_count"] != 119
        or proof["acceptance_count"] != 30
        or proof["rejection_count"] != 89
        or not isinstance(outcomes, Sequence)
        or isinstance(outcomes, (str, bytes, bytearray))
        or len(outcomes) != len(plan)
    ):
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    for expected, observed in zip(plan, outcomes, strict=True):
        if not isinstance(observed, Mapping) or tuple(observed) != (
            "scenario_id",
            "scenario_kind",
            "scenario_sha256",
            "outcome",
            "sqlstate",
            "rejection_marker",
            "zero_mutation",
        ):
            raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
        expected_outcome = expected["expected_outcome"]
        expected_sha256 = _digest(["task7-v17-receipt-case-v1", expected])
        if (
            observed["scenario_id"] != expected["scenario_id"]
            or observed["scenario_kind"] != expected["scenario_kind"]
            or observed["scenario_sha256"] != expected_sha256
            or observed["outcome"] != expected_outcome
            or observed["zero_mutation"] is not (expected_outcome == "REJECTED")
        ):
            raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
        if expected_outcome == "REJECTED":
            if (
                observed["sqlstate"] != "23514"
                or observed["rejection_marker"] != "CONSUMER_RECEIPT_INCOMPLETE"
            ):
                raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
        elif observed["sqlstate"] is not None or observed["rejection_marker"] is not None:
            raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    expected_digest = _digest(
        [
            "task7-v17-receipt-equality-execution-proof-v1",
            {key: value for key, value in proof.items() if key != "execution_sha256"},
        ]
    )
    if proof["execution_sha256"] != expected_digest:
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    return dict(proof)


def validate_source_admission_execution_proof(
    proof: Any,
) -> dict[str, Any]:
    """Validate the closed executed proof; declarations cannot satisfy it."""

    if not isinstance(proof, Mapping) or tuple(proof) != (
        "schema_version",
        "executed_as_application_role",
        "projection_proof_tuple",
        "backfill_rejection_proof_tuple",
        "native_parse_attempt_backfill_rejection",
        "package_file_insert_proof",
    ):
        raise CensusError("TASK7_V17_SOURCE_PROOF_INVALID")
    projection = proof["projection_proof_tuple"]
    backfill = proof["backfill_rejection_proof_tuple"]
    native_backfill = proof["native_parse_attempt_backfill_rejection"]
    insert_proof = proof["package_file_insert_proof"]
    if (
        proof["schema_version"] != "task7-v17-source-admission-execution-proof-v1"
        or proof["executed_as_application_role"] is not True
        or not isinstance(projection, Sequence)
        or not isinstance(backfill, Sequence)
        or not isinstance(native_backfill, Mapping)
        or not isinstance(insert_proof, Mapping)
    ):
        raise CensusError("TASK7_V17_SOURCE_PROOF_INVALID")
    expected_backfill = tuple(
        {
            "object_kind": kind,
            "write_origin": "BACKFILL",
            "rejected": True,
            "rejection_marker": "WORKER_LEASE_FENCED",
        }
        for kind in _SOURCE_KINDS
    )
    projection_rows: list[Mapping[str, Any]] = []
    for ordinal, row in enumerate(projection):
        if not isinstance(row, Mapping) or tuple(row) != (
            "object_kind",
            "legal_projection_accepted",
            "constructor_roundtrip_equal",
            "expected_projection_sha256",
            "observed_projection_sha256",
            "noncanonical_projection_rejected",
            "rejection_marker",
        ):
            raise CensusError("TASK7_V17_SOURCE_PROOF_INVALID")
        expected_hash = row["expected_projection_sha256"]
        observed_hash = row["observed_projection_sha256"]
        if (
            ordinal >= len(_SOURCE_KINDS)
            or row["object_kind"] != _SOURCE_KINDS[ordinal]
            or row["legal_projection_accepted"] is not True
            or row["constructor_roundtrip_equal"] is not True
            or not isinstance(expected_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
            or not secrets.compare_digest(expected_hash, str(observed_hash))
            or row["noncanonical_projection_rejected"] is not True
            or row["rejection_marker"] != "TASK7_SOURCE_TERMINAL_STATE_INVALID"
        ):
            raise CensusError("TASK7_V17_SOURCE_PROOF_INVALID")
        projection_rows.append(row)
    if (
        len(projection_rows) != len(_SOURCE_KINDS)
        or tuple(backfill) != expected_backfill
        or dict(native_backfill)
        != {
            "write_origin": "BACKFILL",
            "rejected": True,
            "rejection_marker": "WORKER_LEASE_FENCED",
            "zero_mutation": True,
        }
        or dict(insert_proof)
        != {
            "open_unbound_parent_accepted": True,
            "sealed_bound_parent_rejected": True,
            "rejection_marker": "SOURCE_TREE_MUTATED",
            "sealed_rejection_zero_mutation": True,
        }
    ):
        raise CensusError("TASK7_V17_SOURCE_PROOF_INVALID")
    return dict(proof)


def validate_execution_contract(
    *,
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    expected = execution_expectations(artifact)
    if any(result.get(key) != value for key, value in expected.items()):
        raise CensusError("TASK7_V17_EXECUTION_INCOMPLETE")
    if (
        result.get("materialization_source_manifest_tuple")
        != materialization_source_manifest_evidence()
        or result.get("materialization_source_manifest_sha256")
        != materialization_source_manifest_sha256()
    ):
        raise CensusError("TASK7_V17_MATERIALIZATION_SOURCE_DRIFT")
    validate_source_admission_execution_proof(result.get("source_admission_execution_proof"))
    validate_receipt_equality_execution_proof(
        result.get("receipt_equality_execution_proof"),
        artifact=artifact,
    )


def _adapt_v16_runner_source(raw: bytes, candidate_raw: bytes) -> str:
    if hashlib.sha256(raw).hexdigest() != _V16_RUNNER_SHA256:
        raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_DRIFT")
    if hashlib.sha256(_V16_SCAFFOLD_PATH.read_bytes()).hexdigest() != (_V16_SCAFFOLD_SHA256):
        raise CensusError("TASK7_V17_V16_CENSUS_SCAFFOLD_DRIFT")
    artifact = _decode_json(candidate_raw)
    _validate_v17_candidate(artifact)
    prospective = _prospective_frozen_bytes(candidate_raw)
    function_count = len(_current_function_rows(artifact))
    try:
        relation_columns = artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "guard_owner_role"
        ]["select_relation_column_tuple"]
        acl_column_count = sum(len(row[1]) for row in relation_columns)
    except (KeyError, TypeError) as exc:
        raise CensusError("TASK7_V17_GUARD_ACL_INVENTORY_INVALID") from exc
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_INVALID") from exc
    replacements = (
        (
            "dd7b2585ff01909765342549bf60b4ba32d18cec398c8b002a77c55d0f92ce74",
            hashlib.sha256(candidate_raw).hexdigest(),
        ),
        (
            "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35",
            hashlib.sha256(prospective).hexdigest(),
        ),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_ADAPTATION_INVALID")
        source = source.replace(old, new)
    candidate_sha512 = hashlib.sha512(candidate_raw).hexdigest()
    for old, new in (
        (
            "f002c51ed7ecc9728a98e5f6f23825e235368823fc19a9e41564e90fa2f96925",
            candidate_sha512[:64],
        ),
        (
            "d9c02f347cc506378da90ee4d4a7ff6bda48184c0a209256d86ea073e408ae2d",
            candidate_sha512[64:],
        ),
    ):
        if source.count(old) != 1:
            raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_ADAPTATION_INVALID")
        source = source.replace(old, new)
    for old, new in (
        (
            "879c0976c233837a9ddd85b90812e632f0a9a6a936b656a5bc3fdd9ad7dbfa1e",
            "b3178fb8e9d89409a29db1d42a209ffea49d62566f9623b780224ccfe60915c5",
        ),
        (
            "a7a8464e53b024a681b6616cc5660098519c3173e393ba3105e9dea77bf0519f",
            "6bd5ec330124325bc714c9f377031bd0d74faa9a463b7ae345c67797969b7c36",
        ),
    ):
        if source.count(old) != 2:
            raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_ADAPTATION_INVALID")
        source = source.replace(old, new)
    source = source.replace("TASK7_V16_", "TASK7_V17_")
    source = source.replace("task7-v16", "task7-v17")
    source = source.replace("full-strict-v16", "full-strict-v17")
    source = source.replace(
        r"v(?:11|14|15)",
        r"v(?:11|14|15|16)",
    )
    source, count = re.subn(r"\b192\b", "193", source)
    if count != 7:
        raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_ADAPTATION_INVALID")
    source, count = re.subn(r"\b19\b", str(function_count), source)
    if count != 4:
        raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_ADAPTATION_INVALID")
    source, count = re.subn(r"\b366\b", str(acl_column_count), source)
    if count != 3:
        raise CensusError("TASK7_V17_V16_CENSUS_RUNNER_ADAPTATION_INVALID")
    return source


def _load_execution_engine(artifact_path: Path) -> ModuleType:
    candidate_raw = artifact_path.read_bytes()
    source = _adapt_v16_runner_source(_V16_RUNNER_PATH.read_bytes(), candidate_raw)
    module_name = (
        "_task7_v17_adapted_execution_engine_" + hashlib.sha256(candidate_raw).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_loader(
        module_name,
        loader=None,
        origin=str(_V16_RUNNER_PATH),
    )
    if spec is None:
        raise CensusError("TASK7_V17_CENSUS_ENGINE_IMPORT_FAILED")
    engine = importlib.util.module_from_spec(spec)
    engine.__file__ = str(_V16_RUNNER_PATH)
    sys.modules[module_name] = engine
    exec(compile(source, str(_V16_RUNNER_PATH), "exec"), engine.__dict__)  # noqa: S102
    scaffold = _load_script(
        _V17_SCAFFOLD_PATH,
        f"_task7_v17_census_scaffold_for_{module_name}",
    )
    for name in (
        "catalog_projection",
        "current_function_rows",
        "disable_all_current_user_triggers",
        "disable_target_trigger",
        "enable_target_trigger",
        "install_artifact_sql_scaffold",
    ):
        setattr(engine, name, getattr(scaffold, name))
    engine.__dict__["GUARD_OWNER_ROLE"] = scaffold.GUARD_OWNER_ROLE
    engine.__dict__["_artifact_trigger_contract"] = artifact_trigger_contract
    engine.__dict__["_artifact_targets"] = artifact_targets
    engine.__dict__["HARNESS_IDENTITY"] = HARNESS_IDENTITY
    engine.__dict__["RESULT_SCHEMA"] = RESULT_SCHEMA
    _patch_source_case_execution(engine, _decode_json(candidate_raw))
    return engine


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_open_source(connection: Connection, *, label: str) -> dict[str, str]:
    """Seed one fictional production-shaped source graph through real tables."""

    from idis.models.source_package_manifest import (  # type: ignore[import-untyped]
        canonical_source_manifest_sha256,
    )
    from idis.persistence.db import set_tenant_local  # type: ignore[import-untyped]

    values = {
        key: str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-v17-source:{label}:{key}"))
        for key in (
            "tenant_id",
            "deal_id",
            "package_id",
            "file_entry_id",
            "doc_id",
            "document_id",
            "attempt_id",
            "span_id",
            "version_id",
        )
    }
    set_tenant_local(connection, values["tenant_id"])
    content_sha256 = _sha256_text(f"{label}:source-bytes")
    locator_sha256 = _sha256_text(f"{label}:source-locator")
    span_text = f"Fictional source-freeze evidence {label}"
    span_sha256 = _sha256_text(span_text)
    object_uri = f"object://fictional-task7-v17/{values['doc_id']}"
    manifest: dict[str, Any] = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "company-001",
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": _sha256_text(f"{label}:source-tree"),
        "file_count": 1,
        "total_bytes": 24,
        "files": [
            {
                "sequence": 0,
                "generated_upload_name": "company-001-document-00001.docx",
                "source_locator_sha256": locator_sha256,
                "content_sha256": content_sha256,
                "size_bytes": 24,
                "extension": "docx",
                "document_id": values["document_id"],
                "doc_id": values["doc_id"],
            }
        ],
    }
    manifest["manifest_sha256"] = canonical_source_manifest_sha256(manifest)
    terminal_projection = {
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "applicable_schema_refs": [],
    }
    terminal_receipt_sha256 = _digest(
        ["terminal-document-metadata-v2", "DOCX", "PARSED", terminal_projection]
    )
    artifact_metadata = {
        "durable_object_integrity": {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": values["doc_id"],
            "content_sha256": content_sha256,
            "size_bytes": 24,
            "uri_sha256": _sha256_text(object_uri),
            "version_id": values["version_id"],
            "backend": "compliant:postgres-test-object-store",
            "streaming_verified": True,
        }
    }
    document_metadata = {
        "parser_runtime": "built_in",
        "parser_runtime_status": "completed",
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "parser_reason_codes": ["docx_parse_completed"],
        "parse_warning_codes": [],
        "parse_error_codes": [],
        "applicable_schema_refs": [],
        "span_count": 1,
    }
    observed_at = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO tenants (tenant_id,name)
            VALUES (CAST(:tenant_id AS uuid),:tenant_name);
            INSERT INTO deals (deal_id,tenant_id,name,created_at)
            VALUES (
                CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),:deal_name,
                CAST(:observed_at AS timestamptz)
            );
            INSERT INTO document_artifacts (
                doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,
                ingested_at,sha256,uri,metadata,source_binding_state,
                source_lineage_object_id,source_corpus_receipt_id
            ) VALUES (
                CAST(:doc_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                'DATA_ROOM_FILE',:title,'task7-v17-postgres-fixture',:version_id,
                CAST(:started_at AS timestamptz),:content_sha256,:object_uri,
                CAST(:artifact_metadata AS jsonb),'UNBOUND',NULL,NULL
            );
            INSERT INTO documents (
                document_id,tenant_id,deal_id,doc_id,doc_type,parse_status,metadata,
                created_at,updated_at,source_binding_state,
                source_lineage_object_id,source_corpus_receipt_id
            ) VALUES (
                CAST(:document_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:doc_id AS uuid),'DOCX','PARSED',
                CAST(:document_metadata AS jsonb),CAST(:started_at AS timestamptz),
                CAST(:finished_at AS timestamptz),'UNBOUND',NULL,NULL
            );
            INSERT INTO document_parse_attempts (
                parse_attempt_id,tenant_id,deal_id,document_id,attempt_ordinal,
                parser_name,parser_version,input_sha256,receipt_sha256,status,
                started_at,finished_at,source_binding_state,source_lineage_object_id,
                source_corpus_receipt_id,write_origin
            ) VALUES (
                CAST(:attempt_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:document_id AS uuid),1,
                'built_in','runtime-parser-v1',:content_sha256,
                :terminal_receipt_sha256,'PARSED',CAST(:started_at AS timestamptz),
                CAST(:finished_at AS timestamptz),'UNBOUND',NULL,NULL,'RUNTIME'
            );
            INSERT INTO document_spans (
                span_id,tenant_id,deal_id,document_id,span_type,locator,text_excerpt,
                content_hash,parse_attempt_id,source_binding_state,
                source_lineage_object_id,source_corpus_receipt_id
            ) VALUES (
                CAST(:span_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                CAST(:document_id AS uuid),'PARAGRAPH',CAST(:locator AS jsonb),
                :span_text,:span_sha256,NULL,'UNBOUND',NULL,NULL
            );
            INSERT INTO data_room_packages (
                package_id,tenant_id,deal_id,status,metadata,created_at,updated_at,
                source_binding_state,source_lineage_object_id
            ) VALUES (
                CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),'OPEN',CAST(:package_metadata AS jsonb),
                CAST(:observed_at AS timestamptz),CAST(:observed_at AS timestamptz),
                'UNBOUND',NULL
            );
            INSERT INTO data_room_package_files (
                file_entry_id,tenant_id,package_id,deal_id,sequence,path_hash,
                extension,sha256,file_status,support_status,triage_status,
                parse_status,reason_codes,error_codes,doc_id,document_id,storage_uri,
                source_binding_state,source_lineage_object_id
            ) VALUES (
                CAST(:file_entry_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:package_id AS uuid),CAST(:deal_id AS uuid),0,:locator_sha256,
                'docx',:content_sha256,'supported','supported','ready','PARSED',
                '["docx_parse_completed"]'::jsonb,'[]'::jsonb,
                NULL,NULL,NULL,'UNBOUND',NULL
            )
            """
        ),
        {
            **values,
            "tenant_name": f"Task 7 v17 tenant {label}",
            "deal_name": f"Task 7 v17 deal {label}",
            "title": f"Fictional Task 7 v17 source {label}",
            "content_sha256": content_sha256,
            "locator_sha256": locator_sha256,
            "span_text": span_text,
            "span_sha256": span_sha256,
            "object_uri": object_uri,
            "artifact_metadata": json.dumps(artifact_metadata),
            "document_metadata": json.dumps(document_metadata),
            "package_metadata": json.dumps({"source_manifest": manifest}),
            "terminal_receipt_sha256": terminal_receipt_sha256,
            "locator": json.dumps({"paragraph": 0}),
            "observed_at": observed_at,
            "started_at": observed_at - timedelta(seconds=2),
            "finished_at": observed_at - timedelta(seconds=1),
        },
    )
    return values


def _patch_source_case_execution(
    engine: ModuleType,
    artifact: Mapping[str, Any],
) -> None:
    original_exact_one = engine._V11._source_exact_one_case
    original_source_bound = engine._V11._source_bound_case
    original_receipt_equality = engine._V11._receipt_equality_case
    original_expected_rejection = engine._expected_rejection
    receipt_step_order, receipt_coordinates = receipt_fixture_coordinates(artifact)
    rejection_markers = dict(engine._V11._AUTHORITY_REJECTION_MARKERS)
    rejection_markers["current-source-exact-one-v1"] = (
        *rejection_markers["current-source-exact-one-v1"],
        "WORKER_LEASE_FENCED",
    )
    engine._V11._AUTHORITY_REJECTION_MARKERS = rejection_markers

    def source_exact_one_case(
        connection: Connection,
        columns: Mapping[str, Mapping[str, tuple[str, bool]]],
        *,
        table_name: str,
        case_kind: str,
        trigger_event: str,
    ) -> tuple[str, dict[str, Any]]:
        if case_kind != "LEGAL":
            return cast(
                tuple[str, dict[str, Any]],
                original_exact_one(
                    connection,
                    columns,
                    table_name=table_name,
                    case_kind=case_kind,
                    trigger_event=trigger_event,
                ),
            )
        if trigger_event != "INSERT":
            raise CensusError("TASK7_V17_SOURCE_LEGAL_EVENT_INVALID")
        engine._V11._set_replica(connection, True)
        seeded = _seed_open_source(
            connection,
            label=f"base-{table_name}-{uuid.uuid4()}",
        )
        from idis.persistence.repositories.task7_source_freeze import (  # type: ignore[import-untyped]
            PostgresTask7SourceFreezeRepository,
            build_source_freeze_plan,
        )

        repository = PostgresTask7SourceFreezeRepository(
            connection,
            seeded["tenant_id"],
        )
        observation = repository.observe_source_package(
            deal_id=seeded["deal_id"],
            package_id=seeded["package_id"],
        )
        plan = build_source_freeze_plan(repository.lock_source_graph(observation))
        native_table = (
            "data_room_packages" if table_name == "source_lineage_objects" else table_name
        )
        binding = next(
            candidate for candidate in plan.bindings if candidate.table_name == native_table
        )
        root = next(
            candidate
            for candidate in plan.roots
            if candidate.source_lineage_object_id == binding.root_id
        )
        bound = plan.expected_bound_snapshot()
        bound_rows: Mapping[str, Mapping[str, Any]] = {
            "data_room_packages": bound.package,
            "data_room_package_files": bound.files[0],
            "document_artifacts": bound.artifacts[0],
            "documents": bound.documents[0],
            "document_parse_attempts": bound.attempts[0],
            "document_spans": bound.spans[0],
        }
        key_columns = {
            "data_room_packages": "package_id",
            "data_room_package_files": "file_entry_id",
            "document_artifacts": "doc_id",
            "documents": "document_id",
            "document_parse_attempts": "parse_attempt_id",
            "document_spans": "span_id",
        }
        for planned_root in plan.roots:
            engine._V11._insert_strict(
                connection,
                columns,
                "source_lineage_objects",
                planned_root.as_row(),
            )
        repository._bind_native_rows(plan, observation)
        if table_name == "source_lineage_objects":
            connection.execute(
                text(
                    "DELETE FROM public.source_lineage_objects "
                    "WHERE source_lineage_object_id=CAST(:root_id AS uuid)"
                ),
                {"root_id": root.source_lineage_object_id},
            )
        else:
            key_column = key_columns[native_table]
            native_values = bound_rows[native_table]
            connection.execute(
                text(
                    f"DELETE FROM public.{engine._identifier(native_table)} "
                    f"WHERE {engine._identifier(key_column)}=CAST(:native_id AS uuid)"
                ),
                {"native_id": str(native_values[key_column])},
            )
        engine._V11._set_replica(connection, False)
        if table_name == "source_lineage_objects":
            engine._V11._insert_strict(
                connection,
                columns,
                table_name,
                root.as_row(),
            )
        else:
            generated_columns = set(
                connection.execute(
                    text(
                        "SELECT attname FROM pg_catalog.pg_attribute "
                        "WHERE attrelid=to_regclass(:qualified_name) "
                        "AND attnum>0 AND NOT attisdropped AND attgenerated<>''"
                    ),
                    {"qualified_name": f"public.{native_table}"},
                ).scalars()
            )
            engine._V11._insert_strict(
                connection,
                columns,
                native_table,
                {
                    key: value
                    for key, value in bound_rows[native_table].items()
                    if key not in generated_columns
                },
            )
        engine._V11._flush_deferred(connection)
        projection = connection.execute(
            text(
                "SELECT object_kind,source_lineage_object_id "
                "FROM public.source_lineage_objects "
                "WHERE source_lineage_object_id=CAST(:root_id AS uuid)"
            ),
            {"root_id": root.source_lineage_object_id},
        ).one()
        if tuple(map(str, projection)) != (
            root.object_kind,
            root.source_lineage_object_id,
        ):
            raise CensusError("TASK7_V17_SOURCE_PROJECTION_EXECUTION_INCOMPLETE")
        return "SOURCE_EXACT_ONE_EVENT", {
            "v17_source_projection_kind_count": 1,
        }

    def source_bound_case(
        connection: Connection,
        columns: Mapping[str, Mapping[str, tuple[str, bool]]],
        *,
        table_name: str,
        case_kind: str,
        trigger_event: str,
    ) -> tuple[str, dict[str, Any]]:
        if not (
            table_name == "data_room_package_files"
            and case_kind == "LEGAL"
            and trigger_event == "INSERT"
        ):
            return cast(
                tuple[str, dict[str, Any]],
                original_source_bound(
                    connection,
                    columns,
                    table_name=table_name,
                    case_kind=case_kind,
                    trigger_event=trigger_event,
                ),
            )
        values = dict(engine.corrected_unbound_source_values(table_name=table_name))
        engine._V11._set_replica(connection, True)
        engine._V11._seed_tenant_deal(
            connection,
            columns,
            tenant_id=values["tenant_id"],
            deal_id=values["deal_id"],
        )
        engine._V11._insert_strict(
            connection,
            columns,
            "data_room_packages",
            {
                "package_id": values["package_id"],
                "tenant_id": values["tenant_id"],
                "deal_id": values["deal_id"],
                "status": "OPEN",
                "metadata": {},
                "source_binding_state": "UNBOUND",
                "source_lineage_object_id": None,
            },
        )
        engine._V11._set_replica(connection, False)
        engine._V11._insert_strict(
            connection,
            columns,
            "data_room_package_files",
            values,
        )
        return "SOURCE_BOUND_EVENT", {
            "open_package_file_insert_acceptance_count": 1,
        }

    def receipt_equality_case(
        connection: Connection,
        columns: Mapping[str, Mapping[str, tuple[str, bool]]],
        *,
        table_name: str,
        case_kind: str,
        trigger_event: str,
    ) -> tuple[str, dict[str, Any]]:
        if case_kind != "LEGAL":
            return cast(
                tuple[str, dict[str, Any]],
                original_receipt_equality(
                    connection,
                    columns,
                    table_name=table_name,
                    case_kind=case_kind,
                    trigger_event=trigger_event,
                ),
            )
        if trigger_event != "INSERT":
            raise CensusError("TASK7_V17_RECEIPT_LEGAL_EVENT_INVALID")
        graph = engine._V11._receipt_graph(connection, columns)
        step_values = dict(engine._V11._step_values(graph, status="COMPLETED"))
        step_values.update(
            {
                "step_name": "EXTRACT",
                "step_order": receipt_step_order,
                "pipeline_contract_version": _V17_CONTRACT_VERSION,
                "semantic_rule_version": receipt_coordinates[0][4],
                "semantic_rule_hash": receipt_coordinates[0][5],
            }
        )
        receipt_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for (
            step_name,
            subrole,
            output_kind,
            output_ordinal,
            semantic_version,
            semantic_hash,
        ) in receipt_coordinates:
            output_object_id = str(uuid.uuid4())
            root_values = dict(
                engine._V11._receipt_root_values(
                    graph,
                    output_object_id=output_object_id,
                )
            )
            root_values.update(
                {
                    "output_kind": output_kind,
                    "pipeline_contract_version": _V17_CONTRACT_VERSION,
                }
            )
            step_output_values = dict(
                engine._V11._step_output_values(
                    graph,
                    output_object_id=output_object_id,
                    subrole=subrole,
                )
            )
            step_output_values.update(
                {
                    "step_name": step_name,
                    "subrole": subrole,
                    "output_kind": output_kind,
                    "output_ordinal": output_ordinal,
                    "semantic_rule_version": semantic_version,
                    "semantic_rule_hash": semantic_hash,
                }
            )
            receipt_rows.append((root_values, step_output_values))

        engine._V11._set_replica(connection, True)
        if table_name != "run_steps":
            engine._V11._insert_strict(
                connection,
                columns,
                "run_steps",
                step_values,
            )
        for ordinal, (root_values, step_output_values) in enumerate(receipt_rows):
            if not (table_name == "run_output_objects" and ordinal == 0):
                engine._V11._insert_strict(
                    connection,
                    columns,
                    "run_output_objects",
                    root_values,
                )
            if not (table_name == "run_step_outputs" and ordinal == 0):
                engine._V11._insert_strict(
                    connection,
                    columns,
                    "run_step_outputs",
                    step_output_values,
                )

        if table_name == "run_steps":
            target_values = step_values
        elif table_name == "run_step_outputs":
            target_values = receipt_rows[0][1]
        elif table_name == "run_output_objects":
            target_values = receipt_rows[0][0]
        elif table_name in {
            "run_expected_consumer_edges",
            "run_consumer_receipts",
        }:
            expected_edge_id = str(uuid.uuid4())
            edge_values = dict(
                engine._V11._edge_values(
                    graph,
                    expected_edge_id=expected_edge_id,
                )
            )
            edge_values.update(
                {
                    "consumer_step_name": receipt_coordinates[0][0],
                    "consumer_subrole": receipt_coordinates[0][1],
                }
            )
            consumer_values = dict(
                engine._V11._receipt_values(
                    graph,
                    expected_edge_id=expected_edge_id,
                )
            )
            consumer_values.update(
                {
                    "consumer_step_output_id": receipt_rows[0][1]["step_output_id"],
                    "consumer_output_object_id": receipt_rows[0][0]["output_object_id"],
                    "consumer_step_name": receipt_coordinates[0][0],
                    "consumer_subrole": receipt_coordinates[0][1],
                }
            )
            if table_name == "run_expected_consumer_edges":
                engine._V11._insert_strict(
                    connection,
                    columns,
                    "run_consumer_receipts",
                    consumer_values,
                )
                target_values = edge_values
            else:
                engine._V11._insert_strict(
                    connection,
                    columns,
                    "run_expected_consumer_edges",
                    edge_values,
                )
                target_values = consumer_values
        else:
            manifest_id = str(uuid.uuid4())
            manifest_root = str(uuid.uuid4())
            for ordinal in range(14):
                engine._V11._insert_strict(
                    connection,
                    columns,
                    table_name,
                    engine._V11._manifest_values(
                        graph,
                        manifest_id=manifest_id,
                        manifest_root=manifest_root,
                        ordinal=ordinal,
                    ),
                )
            target_values = engine._V11._manifest_values(
                graph,
                manifest_id=manifest_id,
                manifest_root=manifest_root,
                ordinal=14,
            )

        engine._V11._set_replica(connection, False)
        engine._V11._insert_strict(
            connection,
            columns,
            table_name,
            target_values,
        )
        engine._V11._flush_deferred(connection)
        return "RECEIPT_EQUALITY_EVENT", {
            "v17_receipt_aggregate_count": 1,
            "v17_receipt_subrole_count": len(receipt_coordinates) - 1,
        }

    engine._V11._source_exact_one_case = source_exact_one_case
    engine._V11._source_bound_case = source_bound_case
    engine._V11._receipt_equality_case = receipt_equality_case

    def expected_rejection(
        *,
        authority_id: str,
        table_name: str,
        case_kind: str,
    ) -> tuple[str, str]:
        if (
            authority_id == "current-source-exact-one-v1"
            and table_name == "source_lineage_objects"
            and case_kind == "ILLEGAL"
        ):
            return "40001", "WORKER_LEASE_FENCED"
        return cast(
            tuple[str, str],
            original_expected_rejection(
                authority_id=authority_id,
                table_name=table_name,
                case_kind=case_kind,
            ),
        )

    engine._expected_rejection = expected_rejection  # type: ignore[attr-defined]


def _rejection_marker(error: DBAPIError, expected: str) -> None:
    message = str(getattr(error, "orig", error))
    if expected not in message:
        raise CensusError("TASK7_V17_SOURCE_PROOF_REJECTION_MARKER_INVALID") from error
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    if sqlstate == "42501":
        raise CensusError("TASK7_V17_SOURCE_PROOF_PRIVILEGE_MASKED") from error


def _expect_rejection(
    connection: Connection,
    *,
    statement: str,
    parameters: Mapping[str, Any],
    marker: str,
) -> None:
    savepoint = connection.begin_nested()
    try:
        connection.execute(text(statement), dict(parameters))
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    except DBAPIError as exc:
        savepoint.rollback()
        _rejection_marker(exc, marker)
        connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
        return
    savepoint.rollback()
    raise CensusError("TASK7_V17_SOURCE_PROOF_REJECTION_MISSING")


def _restore_source_proof_tenant_context(
    connection: Connection,
    tenant_id: str,
) -> None:
    connection.execute(
        text("SELECT pg_catalog.set_config('idis.tenant_id',:tenant_id,true)"),
        {"tenant_id": tenant_id},
    )


def _execute_source_admission_proofs(
    engine: ModuleType,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute every approved v17 source branch on disposable PostgreSQL."""

    from idis.persistence.repositories.task7_source_freeze import (
        PostgresTask7SourceFreezeRepository,
        build_source_freeze_plan,
    )

    with engine.disposable_postgres() as database:
        engine.migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        engine.grant_application_access(database)
        with database.admin_engine.begin() as admin:
            engine.provision_exact_erasure_principals(admin)
            engine.install_artifact_sql_scaffold(
                admin,
                artifact,
                application_role=database.role_name,
            )
            engine._install_external_erasure_helper_acl(admin, artifact)
            engine._grant_external_erasure_helper_read_closure(admin)
            columns = cast(
                dict[str, dict[str, tuple[str, bool]]],
                engine._V11._ENGINE._column_inventory(admin),
            )

        with database.app_engine.begin() as app:
            app.exec_driver_sql("SET LOCAL TIME ZONE 'Asia/Qatar'")
            seeded = _seed_open_source(app, label="semantic-proof")
            repository = PostgresTask7SourceFreezeRepository(
                app,
                seeded["tenant_id"],
            )
            observation = repository.observe_source_package(
                deal_id=seeded["deal_id"],
                package_id=seeded["package_id"],
            )
            plan = build_source_freeze_plan(repository.lock_source_graph(observation))
            receipt = repository.freeze_observed_package(observation)
            app.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
            if receipt.root_count != 6:
                raise CensusError("TASK7_V17_SOURCE_PROOF_INVALID")
            persisted = {
                str(row.object_kind): tuple(row)
                for row in app.execute(
                    text(
                        "SELECT source_lineage_object_id,tenant_id,deal_id,"
                        "corpus_receipt_id,object_kind,source_version,"
                        "source_disposition,object_identity_sha256,content_sha256,"
                        "corpus_receipt_sha256,write_origin "
                        "FROM public.source_lineage_objects "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                        "AND deal_id=CAST(:deal_id AS uuid)"
                    ),
                    seeded,
                )
            }
            expected = {
                root.object_kind: (
                    uuid.UUID(root.source_lineage_object_id),
                    uuid.UUID(root.tenant_id),
                    uuid.UUID(root.deal_id),
                    uuid.UUID(root.corpus_receipt_id),
                    root.object_kind,
                    root.source_version,
                    root.source_disposition,
                    root.object_identity_sha256,
                    root.content_sha256,
                    root.corpus_receipt_sha256,
                    root.write_origin,
                )
                for root in plan.roots
            }
            if persisted != expected:
                raise CensusError("TASK7_V17_SOURCE_PROJECTION_EXECUTION_INCOMPLETE")

            def reject_fresh_noncanonical_projection(object_kind: str) -> None:
                fresh = _seed_open_source(
                    app,
                    label=f"noncanonical-{object_kind.lower()}",
                )
                fresh_repository = PostgresTask7SourceFreezeRepository(
                    app,
                    fresh["tenant_id"],
                )
                fresh_observation = fresh_repository.observe_source_package(
                    deal_id=fresh["deal_id"],
                    package_id=fresh["package_id"],
                )
                fresh_plan = build_source_freeze_plan(
                    fresh_repository.lock_source_graph(fresh_observation)
                )
                target = next(
                    candidate
                    for candidate in fresh_plan.roots
                    if candidate.object_kind == object_kind
                )
                savepoint = app.begin_nested()
                try:
                    for candidate in fresh_plan.roots:
                        values = dict(candidate.as_row())
                        if candidate.source_lineage_object_id == (target.source_lineage_object_id):
                            values["object_identity_sha256"] = (
                                "f" * 64
                                if candidate.object_identity_sha256 != "f" * 64
                                else "e" * 64
                            )
                        engine._V11._insert_strict(
                            app,
                            columns,
                            "source_lineage_objects",
                            values,
                        )
                    fresh_repository._bind_native_rows(
                        fresh_plan,
                        fresh_observation,
                    )
                    app.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                except DBAPIError as exc:
                    savepoint.rollback()
                    app.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
                    _rejection_marker(
                        exc,
                        "TASK7_SOURCE_TERMINAL_STATE_INVALID",
                    )
                    remaining_roots = int(
                        app.execute(
                            text(
                                "SELECT count(*) FROM public.source_lineage_objects "
                                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                                "AND deal_id=CAST(:deal_id AS uuid) "
                                "AND corpus_receipt_id=CAST(:package_id AS uuid)"
                            ),
                            fresh,
                        ).scalar_one()
                    )
                    package_binding = app.execute(
                        text(
                            "SELECT status,source_binding_state,"
                            "source_lineage_object_id "
                            "FROM public.data_room_packages "
                            "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                            "AND deal_id=CAST(:deal_id AS uuid) "
                            "AND package_id=CAST(:package_id AS uuid)"
                        ),
                        fresh,
                    ).one()
                    if tuple(package_binding) != ("OPEN", "UNBOUND", None) or (
                        remaining_roots != 0
                    ):
                        raise CensusError("TASK7_V17_SOURCE_PROOF_ZERO_MUTATION_FAILED") from exc
                    return
                savepoint.rollback()
                raise CensusError("TASK7_V17_SOURCE_PROOF_REJECTION_MISSING")

            projection_rows: list[dict[str, Any]] = []
            for root in plan.roots:
                expected_root = expected[root.object_kind]
                observed_root = persisted[root.object_kind]
                expected_projection_sha256 = _digest(
                    [
                        "task7-v17-source-root-projection-v1",
                        [str(value) for value in expected_root],
                    ]
                )
                observed_projection_sha256 = _digest(
                    [
                        "task7-v17-source-root-projection-v1",
                        [str(value) for value in observed_root],
                    ]
                )
                if expected_projection_sha256 != observed_projection_sha256:
                    raise CensusError("TASK7_V17_SOURCE_PROJECTION_EXECUTION_INCOMPLETE")
                reject_fresh_noncanonical_projection(root.object_kind)
                projection_rows.append(
                    {
                        "object_kind": root.object_kind,
                        "legal_projection_accepted": True,
                        "constructor_roundtrip_equal": True,
                        "expected_projection_sha256": expected_projection_sha256,
                        "observed_projection_sha256": observed_projection_sha256,
                        "noncanonical_projection_rejected": True,
                        "rejection_marker": "TASK7_SOURCE_TERMINAL_STATE_INVALID",
                    }
                )

            _restore_source_proof_tenant_context(
                app,
                seeded["tenant_id"],
            )
            backfill_rows: list[dict[str, Any]] = []
            for kind in _SOURCE_KINDS:
                _expect_rejection(
                    app,
                    statement=(
                        "INSERT INTO public.source_lineage_objects("
                        "source_lineage_object_id,tenant_id,deal_id,corpus_receipt_id,"
                        "object_kind,source_version,source_disposition,"
                        "object_identity_sha256,content_sha256,corpus_receipt_sha256,"
                        "write_origin) VALUES ("
                        "CAST(:root_id AS uuid),CAST(:tenant_id AS uuid),"
                        "CAST(:deal_id AS uuid),CAST(:package_id AS uuid),:kind,"
                        "'post-install-backfill-v1','EVIDENCE_BEARING',"
                        ":identity_hash,:content_hash,:corpus_hash,'BACKFILL')"
                    ),
                    parameters={
                        **seeded,
                        "root_id": str(uuid.uuid4()),
                        "kind": kind,
                        "identity_hash": "1" * 64,
                        "content_hash": "2" * 64,
                        "corpus_hash": plan.manifest_sha256,
                    },
                    marker="WORKER_LEASE_FENCED",
                )
                backfill_rows.append(
                    {
                        "object_kind": kind,
                        "write_origin": "BACKFILL",
                        "rejected": True,
                        "rejection_marker": "WORKER_LEASE_FENCED",
                    }
                )

            attempt_count = int(
                app.execute(
                    text(
                        "SELECT count(*) FROM public.document_parse_attempts "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                        "AND deal_id=CAST(:deal_id AS uuid)"
                    ),
                    seeded,
                ).scalar_one()
            )
            _expect_rejection(
                app,
                statement=(
                    "INSERT INTO public.document_parse_attempts("
                    "parse_attempt_id,tenant_id,deal_id,document_id,attempt_ordinal,"
                    "parser_name,parser_version,input_sha256,receipt_sha256,status,"
                    "started_at,finished_at,source_binding_state,"
                    "source_lineage_object_id,source_corpus_receipt_id,write_origin)"
                    " VALUES(CAST(:new_attempt_id AS uuid),CAST(:tenant_id AS uuid),"
                    "CAST(:deal_id AS uuid),CAST(:document_id AS uuid),2,"
                    "'built_in','runtime-parser-v1',:input_hash,:receipt_hash,'PARSED',"
                    "CAST(:started_at AS timestamptz),CAST(:finished_at AS timestamptz),"
                    "'UNBOUND',NULL,NULL,'BACKFILL')"
                ),
                parameters={
                    **seeded,
                    "new_attempt_id": str(uuid.uuid4()),
                    "input_hash": "3" * 64,
                    "receipt_hash": "4" * 64,
                    "started_at": datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
                    "finished_at": datetime(2026, 7, 24, 9, 1, tzinfo=UTC),
                },
                marker="WORKER_LEASE_FENCED",
            )
            if (
                int(
                    app.execute(
                        text(
                            "SELECT count(*) FROM public.document_parse_attempts "
                            "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                            "AND deal_id=CAST(:deal_id AS uuid)"
                        ),
                        seeded,
                    ).scalar_one()
                )
                != attempt_count
            ):
                raise CensusError("TASK7_V17_SOURCE_PROOF_ZERO_MUTATION_FAILED")

            file_count = int(
                app.execute(
                    text(
                        "SELECT count(*) FROM public.data_room_package_files "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                        "AND package_id=CAST(:package_id AS uuid)"
                    ),
                    seeded,
                ).scalar_one()
            )
            _expect_rejection(
                app,
                statement=(
                    "INSERT INTO public.data_room_package_files("
                    "file_entry_id,tenant_id,package_id,deal_id,sequence,path_hash,"
                    "extension,sha256,file_status,support_status,triage_status,"
                    "parse_status,reason_codes,error_codes,source_binding_state,"
                    "source_lineage_object_id) VALUES("
                    "CAST(:new_file_id AS uuid),CAST(:tenant_id AS uuid),"
                    "CAST(:package_id AS uuid),CAST(:deal_id AS uuid),99,:path_hash,"
                    "'docx',:content_hash,'pending','supported','ready','PARSED',"
                    "'[]'::jsonb,'[]'::jsonb,'UNBOUND',NULL)"
                ),
                parameters={
                    **seeded,
                    "new_file_id": str(uuid.uuid4()),
                    "path_hash": "5" * 64,
                    "content_hash": "6" * 64,
                },
                marker="SOURCE_TREE_MUTATED",
            )
            if (
                int(
                    app.execute(
                        text(
                            "SELECT count(*) FROM public.data_room_package_files "
                            "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                            "AND package_id=CAST(:package_id AS uuid)"
                        ),
                        seeded,
                    ).scalar_one()
                )
                != file_count
            ):
                raise CensusError("TASK7_V17_SOURCE_PROOF_ZERO_MUTATION_FAILED")

    proof = {
        "schema_version": "task7-v17-source-admission-execution-proof-v1",
        "executed_as_application_role": True,
        "projection_proof_tuple": sorted(
            projection_rows,
            key=lambda row: _SOURCE_KINDS.index(str(row["object_kind"])),
        ),
        "backfill_rejection_proof_tuple": backfill_rows,
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
    return validate_source_admission_execution_proof(proof)


def _receipt_case_step_order(
    artifact: Mapping[str, Any],
    step_name: str,
) -> int:
    rows = artifact["semantic_step_registry"]["rows"]
    selected = tuple(
        row for row in rows if isinstance(row, list) and len(row) == 5 and row[1] == step_name
    )
    if len(selected) != 1 or type(selected[0][0]) is not int:
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    return int(selected[0][0])


def _seed_receipt_case(
    engine: ModuleType,
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    artifact: Mapping[str, Any],
    case: Mapping[str, Any],
) -> dict[str, Any]:
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    step_id = str(uuid.uuid4())
    lease_owner_id = str(uuid.uuid4())
    pipeline_hash = "a" * 64
    step_name = str(case["step_name"])
    contract_version = str(case["contract_version"])
    resume_state = str(case["resume_state"])
    raw_coordinates = cast(Sequence[Sequence[Any]], case["coordinate_tuple"])
    coordinates = tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            int(row[3]),
            str(row[4]),
            str(row[5]),
        )
        for row in raw_coordinates
    )
    canonical = tuple(row for row in _receipt_equality_coordinates(artifact) if row[0] == step_name)
    if not canonical:
        raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
    semantic_hash = canonical[0][5]

    connection.execute(
        text("SELECT pg_catalog.set_config('idis.tenant_id',:tenant_id,true)"),
        {"tenant_id": tenant_id},
    )
    engine._V11._insert_strict(
        connection,
        columns,
        "tenants",
        {
            "tenant_id": tenant_id,
            "name": "Task 7 v17 receipt proof tenant",
        },
    )
    engine._V11._insert_strict(
        connection,
        columns,
        "deals",
        {
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "name": "Fictional Task 7 v17 receipt proof deal",
            "created_at": datetime.now(UTC),
        },
    )
    engine._V11._insert_strict(
        connection,
        columns,
        "runs",
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "mode": "FULL",
            "status": "RUNNING",
            "idempotency_key": f"task7-v17-receipt-{uuid.uuid4()}",
            "lease_owner_id": lease_owner_id,
            "lease_fencing_token": 7,
            "lease_expires_at": datetime.now(UTC) + timedelta(hours=1),
            "last_heartbeat_at": datetime.now(UTC),
            "pipeline_contract_version": contract_version,
            "pipeline_contract_hash": pipeline_hash,
            "pipeline_resume_state": resume_state,
            "pipeline_resume_error_code": (
                "LEGACY_RUN_RESUME_UNSAFE" if resume_state == "NON_RESUMABLE" else None
            ),
        },
    )
    engine._V11._insert_strict(
        connection,
        columns,
        "run_steps",
        {
            "step_id": step_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "step_name": step_name,
            "step_order": _receipt_case_step_order(artifact, step_name),
            "status": "PENDING",
            "pipeline_contract_version": contract_version,
            "pipeline_contract_hash": pipeline_hash,
            "semantic_rule_version": canonical[0][4],
            "semantic_rule_hash": semantic_hash,
        },
    )

    coordinate_objects: list[dict[str, str]] = []
    for coordinate_ordinal, coordinate in enumerate(coordinates):
        (
            coordinate_step,
            subrole,
            output_kind,
            output_ordinal,
            semantic_version,
            coordinate_hash,
        ) = coordinate
        output_object_id = str(uuid.uuid4())
        step_output_id = str(uuid.uuid4())
        native_identity_sha256 = _digest(
            [
                "task7-v17-receipt-root-identity-v1",
                str(case["scenario_id"]),
                coordinate_ordinal,
                list(coordinate),
            ]
        )
        native_output_sha256 = _digest(
            [
                "task7-v17-receipt-root-output-v1",
                str(case["scenario_id"]),
                coordinate_ordinal,
                list(coordinate),
            ]
        )
        engine._V11._insert_strict(
            connection,
            columns,
            "run_output_objects",
            {
                "output_object_id": output_object_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "output_class": "STEP_RECEIPT",
                "output_kind": output_kind,
                "output_schema_version": "task7-v17-receipt-proof-v1",
                "native_identity_sha256": native_identity_sha256,
                "native_output_sha256": native_output_sha256,
                "pipeline_contract_version": contract_version,
                "pipeline_contract_hash": pipeline_hash,
                "write_origin": "RUNTIME",
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": 7,
            },
        )
        engine._V11._insert_strict(
            connection,
            columns,
            "run_step_outputs",
            {
                "step_output_id": step_output_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "step_id": step_id,
                "output_object_id": output_object_id,
                "step_name": coordinate_step,
                "subrole": subrole,
                "output_kind": output_kind,
                "output_ordinal": output_ordinal,
                "semantic_rule_version": semantic_version,
                "semantic_rule_hash": coordinate_hash,
                "summary_sha256": native_output_sha256,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": 7,
            },
        )
        coordinate_objects.append(
            {
                "step_output_id": step_output_id,
                "output_object_id": output_object_id,
            }
        )

    if case["scenario_kind"] == "NATIVE_CONSUMER_TARGET":
        producer = coordinate_objects[0]
        native_output_id = str(uuid.uuid4())
        native_step_output_id = str(uuid.uuid4())
        native_kind = "TASK7_RECEIPT_NATIVE_PROOF_V1"
        native_hash = _digest(["task7-v17-native-consumer-target-v1", str(case["scenario_id"])])
        engine._V11._insert_strict(
            connection,
            columns,
            "run_output_objects",
            {
                "output_object_id": native_output_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "output_class": "NATIVE_ROW",
                "output_kind": native_kind,
                "output_schema_version": "task7-v17-receipt-proof-v1",
                "native_identity_sha256": native_hash,
                "native_output_sha256": native_hash,
                "pipeline_contract_version": contract_version,
                "pipeline_contract_hash": pipeline_hash,
                "write_origin": "RUNTIME",
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": 7,
            },
        )
        engine._V11._insert_strict(
            connection,
            columns,
            "run_step_outputs",
            {
                "step_output_id": native_step_output_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "step_id": step_id,
                "output_object_id": native_output_id,
                "step_name": step_name,
                "subrole": "native_context",
                "output_kind": native_kind,
                "output_ordinal": 0,
                "semantic_rule_version": canonical[0][4],
                "semantic_rule_hash": semantic_hash,
                "summary_sha256": native_hash,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": 7,
            },
        )
        expected_edge_id = str(uuid.uuid4())
        engine._V11._insert_strict(
            connection,
            columns,
            "run_expected_consumer_edges",
            {
                "expected_edge_id": expected_edge_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "producer_input_binding_id": None,
                "producer_output_object_id": producer["output_object_id"],
                "consumer_step_name": step_name,
                "consumer_subrole": "native_context",
                "usage_kind": "SOURCE_INVENTORY",
                "applicability_decision_id": None,
                "contract_rule_id": "E001_SOURCE_INVENTORY_ADMISSION",
                "contract_rule_version": "1",
                "contract_rule_hash": "3" * 64,
                "edge_tuple_sha256": "4" * 64,
                "materialization_origin": "ADMISSION",
                "lease_owner_id": None,
                "lease_fencing_token": 0,
            },
        )
        engine._V11._insert_strict(
            connection,
            columns,
            "run_consumer_receipts",
            {
                "receipt_id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "expected_edge_id": expected_edge_id,
                "consumer_step_output_id": native_step_output_id,
                "consumer_output_object_id": native_output_id,
                "consumer_step_name": step_name,
                "consumer_subrole": "native_context",
                "usage_evidence_sha256": "5" * 64,
                "consumed_at": datetime.now(UTC),
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": 7,
            },
        )
    return {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "step_id": step_id,
    }


def _execute_receipt_equality_proofs(
    engine: ModuleType,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute the registry-derived receipt matrix as the application role."""

    plan = receipt_equality_execution_plan(artifact)
    outcomes: list[dict[str, Any]] = []
    with engine.disposable_postgres() as database:
        engine.migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        engine.grant_application_access(database)
        with database.admin_engine.begin() as admin:
            engine.provision_exact_erasure_principals(admin)
            engine.install_artifact_sql_scaffold(
                admin,
                artifact,
                application_role=database.role_name,
            )
            engine._install_external_erasure_helper_acl(admin, artifact)
            engine._grant_external_erasure_helper_read_closure(admin)
            engine.disable_all_current_user_triggers(admin, artifact)
            engine.enable_target_trigger(
                admin,
                "run_steps",
                "ctrg_task7_run_steps_receipt_complete",
            )
            columns = cast(
                dict[str, dict[str, tuple[str, bool]]],
                engine._V11._ENGINE._column_inventory(admin),
            )

        with database.app_engine.connect() as app:
            actor_row = app.execute(
                text(
                    "SELECT current_user AS role_name,session_user,"
                    "role.rolsuper AS is_superuser,"
                    "role.rolbypassrls AS bypass_rls "
                    "FROM pg_catalog.pg_roles role "
                    "WHERE role.rolname=current_user"
                )
            ).one()
            database_actor = {
                "role_name": str(actor_row.role_name),
                "session_user": str(actor_row.session_user),
                "is_superuser": bool(actor_row.is_superuser),
                "bypass_rls": bool(actor_row.bypass_rls),
            }
            app.rollback()
            if database_actor != {
                "role_name": database.role_name,
                "session_user": database.role_name,
                "is_superuser": False,
                "bypass_rls": False,
            }:
                raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")

            for case in plan:
                transaction = app.begin()
                try:
                    graph = _seed_receipt_case(
                        engine,
                        app,
                        columns,
                        artifact,
                        case,
                    )
                    savepoint = app.begin_nested()
                    error: DBAPIError | None = None
                    try:
                        app.execute(
                            text(
                                "UPDATE public.run_steps SET status='COMPLETED' "
                                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                                "AND run_id=CAST(:run_id AS uuid) "
                                "AND step_id=CAST(:step_id AS uuid)"
                            ),
                            graph,
                        )
                        app.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                    except DBAPIError as exc:
                        error = exc
                        savepoint.rollback()
                        app.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
                    else:
                        savepoint.commit()

                    expected_outcome = str(case["expected_outcome"])
                    if error is None:
                        if expected_outcome != "ACCEPTED":
                            raise CensusError("TASK7_V17_RECEIPT_PROOF_REJECTION_MISSING")
                        persisted_status = app.execute(
                            text(
                                "SELECT status FROM public.run_steps "
                                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                                "AND run_id=CAST(:run_id AS uuid) "
                                "AND step_id=CAST(:step_id AS uuid)"
                            ),
                            graph,
                        ).scalar_one()
                        if persisted_status != "COMPLETED":
                            raise CensusError("TASK7_V17_RECEIPT_PROOF_INVALID")
                        sqlstate: str | None = None
                        marker: str | None = None
                        zero_mutation = False
                    else:
                        if expected_outcome != "REJECTED":
                            raise CensusError(
                                "TASK7_V17_RECEIPT_PROOF_ACCEPTANCE_MISSING"
                            ) from error
                        sqlstate = str(
                            getattr(error.orig, "sqlstate", None)
                            or getattr(error.orig, "pgcode", None)
                            or ""
                        )
                        rendered = str(error.orig)
                        marker = (
                            "CONSUMER_RECEIPT_INCOMPLETE"
                            if "CONSUMER_RECEIPT_INCOMPLETE" in rendered
                            else ""
                        )
                        persisted_status = app.execute(
                            text(
                                "SELECT status FROM public.run_steps "
                                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                                "AND run_id=CAST(:run_id AS uuid) "
                                "AND step_id=CAST(:step_id AS uuid)"
                            ),
                            graph,
                        ).scalar_one()
                        zero_mutation = persisted_status == "PENDING"
                        if (
                            sqlstate != "23514"
                            or marker != "CONSUMER_RECEIPT_INCOMPLETE"
                            or not zero_mutation
                        ):
                            raise CensusError(
                                "TASK7_V17_RECEIPT_PROOF_UNRELATED_REJECTION"
                            ) from error
                    outcomes.append(
                        {
                            "scenario_id": case["scenario_id"],
                            "scenario_kind": case["scenario_kind"],
                            "scenario_sha256": _digest(["task7-v17-receipt-case-v1", case]),
                            "outcome": expected_outcome,
                            "sqlstate": sqlstate,
                            "rejection_marker": marker,
                            "zero_mutation": zero_mutation,
                        }
                    )
                finally:
                    if transaction.is_active:
                        transaction.rollback()

    proof: dict[str, Any] = {
        "schema_version": "task7-v17-receipt-equality-execution-proof-v1",
        "executed_as_application_role": True,
        "database_actor": database_actor,
        "canonical_coordinate_count": 83,
        "aggregate_coordinate_count": 28,
        "subrole_coordinate_count": 55,
        "case_execution_count": len(outcomes),
        "acceptance_count": sum(row["outcome"] == "ACCEPTED" for row in outcomes),
        "rejection_count": sum(row["outcome"] == "REJECTED" for row in outcomes),
        "case_outcome_tuple": outcomes,
    }
    proof["execution_sha256"] = _digest(["task7-v17-receipt-equality-execution-proof-v1", proof])
    return validate_receipt_equality_execution_proof(proof, artifact=artifact)


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Run 73×3 cases, four constructors, and the v17 source proof."""

    raw = Path(artifact_path).read_bytes()
    artifact = _decode_json(raw)
    _validate_v17_candidate(artifact)
    if (
        base_schema_revision != BASE_SCHEMA_REVISION
        or harness_identity != HARNESS_IDENTITY
        or tuple(tuple(row) for row in ordered_authority_target_tuple) != artifact_targets(artifact)
        or tuple(tuple(row) for row in ordered_trigger_contract_coordinate_tuple)
        != artifact_trigger_contract(artifact)
        or tuple(tuple(row) for row in production_constructor_spec_tuple)
        != _PRODUCTION_CONSTRUCTOR_SPECS
    ):
        raise CensusError("TASK7_V17_CENSUS_REQUEST_INVALID")
    engine = _load_execution_engine(Path(artifact_path))
    result = dict(
        engine.run_pre_fixture_census(
            artifact_path=Path(artifact_path),
            base_schema_revision=BASE_SCHEMA_REVISION,
            harness_identity=HARNESS_IDENTITY,
            ordered_authority_target_tuple=artifact_targets(artifact),
            ordered_trigger_contract_coordinate_tuple=artifact_trigger_contract(artifact),
            production_constructor_spec_tuple=_PRODUCTION_CONSTRUCTOR_SPECS,
        )
    )
    result["source_admission_execution_proof"] = _execute_source_admission_proofs(
        engine,
        artifact,
    )
    result["receipt_equality_execution_proof"] = _execute_receipt_equality_proofs(
        engine,
        artifact,
    )
    result["materialization_source_manifest_tuple"] = materialization_source_manifest_evidence()
    result["materialization_source_manifest_sha256"] = materialization_source_manifest_sha256()
    result["v17_adapter_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["v17_scaffold_source_sha256"] = hashlib.sha256(
        _V17_SCAFFOLD_PATH.read_bytes()
    ).hexdigest()
    result["complete_execution_sha256"] = _digest(
        [
            "task7-v17-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    validate_execution_contract(result=result, artifact=artifact)
    return result


_DATABASE_EXECUTION_FIELDS: Final = (
    "application_case_execution_count",
    "admin_case_execution_count",
    "admin_fixture_helper_call_count",
    "current_guard_target_count",
    "current_guard_target_event_count",
    "case_outcome_count",
    "legal_execution_count",
    "illegal_rejection_count",
    "direct_sql_rejection_count",
    "constructor_schema_proof_count",
    "current_routine_catalog_count",
    "guard_owner_column_acl_relation_count",
    "guard_owner_column_acl_count",
    "application_direct_helper_execute_count",
    "public_direct_helper_execute_count",
    "unexpected_outcome_count",
    "catalog_projection_sha256",
    "case_result_sha256",
    "constructor_roundtrip_sha256",
    "admin_fixture_helper_projection_sha256",
    "manifest_parity_sha256",
    "pg_trigger_inventory_sha256",
    "trigger_event_coverage_sha256",
    "guard_acl_projection_sha256",
    "rls_context_proof_sha256",
    "complete_execution_sha256",
    "source_admission_execution_proof_sha256",
    "receipt_equality_execution_proof_sha256",
    "materialization_source_manifest_sha256",
)


def _database_execution(
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    projection = {field: result.get(field) for field in _DATABASE_EXECUTION_FIELDS[:26]}
    projection["source_admission_execution_proof_sha256"] = _digest(
        result.get("source_admission_execution_proof")
    )
    projection["receipt_equality_execution_proof_sha256"] = _digest(
        result.get("receipt_equality_execution_proof")
    )
    projection["materialization_source_manifest_sha256"] = result.get(
        "materialization_source_manifest_sha256"
    )
    validate_database_execution_projection(
        projection=projection,
        result=result,
        artifact=artifact,
    )
    return projection


def validate_database_execution_projection(
    *,
    projection: Mapping[str, Any],
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    """Prove exact conservation of legacy evidence plus three v17 hashes."""

    acl_rows = artifact["sql_authority_registry"]["guard_function_acl_registry"][
        "guard_owner_role"
    ]["select_relation_column_tuple"]
    expected_counts = {
        **execution_expectations(artifact),
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "current_routine_catalog_count": len(_current_function_rows(artifact)),
        "guard_owner_column_acl_relation_count": len(acl_rows),
        "guard_owner_column_acl_count": sum(len(row[1]) for row in acl_rows),
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "unexpected_outcome_count": 0,
    }
    expected = {field: result.get(field) for field in _DATABASE_EXECUTION_FIELDS[:26]}
    expected["source_admission_execution_proof_sha256"] = _digest(
        result.get("source_admission_execution_proof")
    )
    expected["receipt_equality_execution_proof_sha256"] = _digest(
        result.get("receipt_equality_execution_proof")
    )
    expected["materialization_source_manifest_sha256"] = result.get(
        "materialization_source_manifest_sha256"
    )
    if (
        tuple(projection) != _DATABASE_EXECUTION_FIELDS
        or dict(projection) != expected
        or any(result.get(key) != value for key, value in expected_counts.items())
        or projection["materialization_source_manifest_sha256"]
        != materialization_source_manifest_sha256()
        or any(
            not isinstance(projection[field], str)
            or re.fullmatch(r"[0-9a-f]{64}", str(projection[field])) is None
            for field in _DATABASE_EXECUTION_FIELDS
            if field.endswith("_sha256")
        )
    ):
        raise CensusError("TASK7_V17_DATABASE_EXECUTION_PROJECTION_INVALID")


def _validate_complete_result(
    result: Mapping[str, Any],
    *,
    artifact: Mapping[str, Any],
) -> None:
    validate_execution_contract(result=result, artifact=artifact)
    expected = execution_expectations(artifact)
    required = {
        "admin_case_execution_count": 0,
        "case_outcome_count": expected["application_case_execution_count"],
        "legal_execution_count": expected["current_guard_target_count"],
        "illegal_rejection_count": expected["current_guard_target_count"],
        "direct_sql_rejection_count": expected["current_guard_target_count"],
        "unexpected_outcome_count": 0,
    }
    if any(result.get(key) != value for key, value in required.items()):
        raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
    supplied = result.get("complete_execution_sha256")
    derived = _digest(
        [
            "task7-v17-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    if supplied != derived:
        raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")


def build_and_verify_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    _validate_v17_candidate(artifact)
    _validate_complete_result(result, artifact=artifact)
    if not isinstance(signer_id, str) or not signer_id.strip():
        raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
    payload = {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": _V17_ARTIFACT_SCHEMA,
        "pipeline_contract_version": _V17_CONTRACT_VERSION,
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(
            _prospective_frozen_bytes(artifact_raw)
        ).hexdigest(),
        "database_execution": _database_execution(result, artifact),
        "census_result": dict(result),
    }
    payload_sha256 = _digest(payload)
    public_key_sha256 = hashlib.sha256(trusted_public_key_raw).hexdigest()
    signature = private_key.sign(
        _canonical_bytes(
            [
                _CENSUS_SIGNING_DOMAIN,
                signer_id,
                payload_sha256,
                public_key_sha256,
                payload,
            ]
        )
    )
    receipt = {
        "schema_version": _CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    verify_signed_receipt(
        receipt_raw=(
            json.dumps(receipt, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        artifact_raw=artifact_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    return receipt


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    try:
        artifact = _decode_json(artifact_raw)
        _validate_v17_candidate(artifact)
        receipt = _decode_json(receipt_raw)
        if tuple(receipt) != (
            "schema_version",
            "signer_id",
            "payload",
            "payload_sha256",
            "public_key_sha256",
            "signature_base64",
        ):
            raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
        payload = receipt["payload"]
        if (
            receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA
            or not isinstance(payload, Mapping)
            or payload.get("schema_version") != _CENSUS_PAYLOAD_SCHEMA
            or payload.get("result") != "PASS"
            or payload.get("artifact_schema") != _V17_ARTIFACT_SCHEMA
            or payload.get("pipeline_contract_version") != _V17_CONTRACT_VERSION
            or payload.get("candidate_artifact_sha256") != hashlib.sha256(artifact_raw).hexdigest()
            or payload.get("prospective_frozen_artifact_sha256")
            != hashlib.sha256(_prospective_frozen_bytes(artifact_raw)).hexdigest()
            or receipt["payload_sha256"] != _digest(payload)
            or receipt["public_key_sha256"] != hashlib.sha256(trusted_public_key_raw).hexdigest()
        ):
            raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
        result = payload.get("census_result")
        if not isinstance(result, Mapping):
            raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
        _validate_complete_result(result, artifact=artifact)
        if payload.get("database_execution") != _database_execution(result, artifact):
            raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID")
        signature = base64.b64decode(str(receipt["signature_base64"]), validate=True)
        public_key.verify(
            signature,
            _canonical_bytes(
                [
                    _CENSUS_SIGNING_DOMAIN,
                    receipt["signer_id"],
                    receipt["payload_sha256"],
                    receipt["public_key_sha256"],
                    payload,
                ]
            ),
        )
    except (
        CensusError,
        InvalidSignature,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        if isinstance(exc, CensusError) and str(exc) == ("TASK7_V17_CENSUS_RECEIPT_INVALID"):
            raise
        raise CensusError("TASK7_V17_CENSUS_RECEIPT_INVALID") from exc
    return dict(payload)


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CensusError",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "RESULT_SCHEMA",
    "_CENSUS_SIGNING_DOMAIN",
    "_PRODUCTION_CONSTRUCTOR_SPECS",
    "_canonical_bytes",
    "_digest",
    "artifact_targets",
    "artifact_trigger_contract",
    "build_and_verify_signed_receipt",
    "execution_expectations",
    "materialization_source_manifest",
    "materialization_source_manifest_evidence",
    "materialization_source_manifest_sha256",
    "receipt_equality_execution_plan",
    "receipt_fixture_coordinates",
    "run_pre_fixture_census",
    "validate_database_execution_projection",
    "validate_execution_contract",
    "validate_receipt_equality_execution_proof",
    "validate_source_admission_execution_proof",
    "verify_signed_receipt",
]
