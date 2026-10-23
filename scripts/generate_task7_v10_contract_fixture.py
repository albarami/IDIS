"""Validate full-strict-v10 and admit it only after signed PostgreSQL proof.

The v10 artifact is the sole normative byte source for contract derivation.
Historical v9 machinery is imported only to validate byte-identical sections;
the v10-to-v9 projection is authenticated by the reviewer-pinned SHA-512 in
``materialize_full_strict_v10_preimage.py``.  No environment value, runtime
product module, database state, or credential participates in hash derivation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V10_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V9 = _load_script(
    _SCRIPT_DIR / "generate_task7_v9_contract_fixture.py",
    "_task7_v9_fixture_helpers_for_v10",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v10_preimage.py",
    "_task7_v10_materializer_helpers",
)

ContractArtifactError = _V9.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V9._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V9._COMPONENT_ORDER)
_EXPECTED_INVARIANTS = cast(dict[str, int], _V9._EXPECTED_INVARIANTS)
_CENSUS_ENVELOPE_SCHEMA = "task7-v10-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-v10-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-v10-postgres-census-signable-v1"
_CENSUS_HARNESS_IDENTITY = "task7-v10-current-guard-pre-freeze-artifact-sql-on-0037-v1"
_CENSUS_HARNESS_SHA256 = hashlib.sha256(_CENSUS_HARNESS_IDENTITY.encode("utf-8")).hexdigest()
_OLD_MULTI_ARRAY = _MATERIALIZER._OLD_MULTI_ARRAY
_ROWS_FROM_REPLACEMENT = _MATERIALIZER._ROWS_FROM_REPLACEMENT


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V9._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> Never:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V9._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V10_UNFROZEN_CANDIDATE_REQUIRED",
            "prospective freeze requires append_frozen=false",
        )
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    return frozen_raw


def _decode_sql(row: dict[str, Any]) -> tuple[str, ...]:
    try:
        return tuple(
            bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]
        )
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V10_CURRENT_GUARD_INVALID",
            "current-guard SQL is not canonical UTF-8 hex",
        ) from exc


def derive_manifest_reference_sha256(record: dict[str, Any]) -> str:
    return cast(str, _V9.derive_manifest_reference_sha256(record))


def validate_native_payload_semantics(
    schema_id: str,
    payload: dict[str, Any],
) -> None:
    _V9.validate_native_payload_semantics(schema_id, payload)


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V9.invariant_vector(artifact))


def _validate_rows_from_authority(artifact: dict[str, Any]) -> None:
    rows = artifact["sql_authority_registry"]["rows"]
    by_id = {row["authority_id"]: row for row in rows}
    output = by_id.get("current-output-exact-one-v1")
    if output is None:
        _error(
            "TASK7_V10_ROWS_FROM_AUTHORITY_INVALID",
            "current output authority is absent",
        )
    statements = _decode_sql(output)
    if (
        len(statements) <= 2
        or statements[2].count(_ROWS_FROM_REPLACEMENT) != 1
        or _OLD_MULTI_ARRAY in statements[2]
    ):
        _error(
            "TASK7_V10_ROWS_FROM_AUTHORITY_INVALID",
            "approved ROWS FROM replacement differs",
        )
    all_sql = "\n".join(statement for row in rows[17:] for statement in _decode_sql(row))
    if "full-strict-v9" in all_sql or all_sql.count("full-strict-v10") != 2:
        _error(
            "TASK7_V10_VERSION_AUTHORITY_INVALID",
            "current guard version literals differ",
        )


def _validate_shape(
    raw: bytes,
    artifact: dict[str, Any],
    *,
    require_frozen: bool = False,
) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "section order differs")
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v10-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v10"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v10 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V10_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v10 bytes",
        )

    reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
    _expect(
        _sha256_bytes(reviewed),
        metadata["reviewed_design_sha256"],
        "v10 reviewed design",
    )
    reviewed_value = json.loads(reviewed)
    if not isinstance(reviewed_value, list) or reviewed_value[:2] != [
        "full-strict-v10-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v10"],
    ]:
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v10 reviewed design version differs",
        )

    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v10","full-strict-v10",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ] or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v10 digest recipe differs")

    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v9 = _MATERIALIZER.project_v10_to_v9(candidate)
        _MATERIALIZER._validate_exact_diff(v9, candidate)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V10_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v9 successor",
        ) from exc
    v9_raw = _render_artifact(v9)
    _V9._validate_shape(v9_raw, v9)
    _V9._validate_schema_registry(artifact)
    _V9._validate_current_authorities(artifact)
    _validate_rows_from_authority(artifact)
    if invariant_vector(artifact) != _EXPECTED_INVARIANTS:
        _error(
            "TASK7_V10_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def derive_contract_bytes(
    raw: bytes,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Derive every v10 contract component from artifact bytes only."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v10-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = cast(
        dict[str, str],
        _V9._V7._V6._V5._derive_components(artifact, reviewed_hash),
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v10",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v10-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v10",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V10_CONTRACT_HASH": pipeline_hash,
    }


def derive_authority_inventory(artifact: dict[str, Any]) -> dict[str, Any]:
    """Bind a signed census to the exact 73-target v10 inventory."""

    rows = artifact["sql_authority_registry"]["rows"][17:]
    targets = [list(target) for row in rows for target in row["target_tuple"]]
    return {
        "schema_version": "task7-v10-authority-inventory-v1",
        "authority_registry_sha256": artifact["sql_authority_registry"]["expected_sha256"],
        "authority_row_count": len(rows),
        "current_guard_target_count": len(targets),
        "body_statement_count": sum(len(row["body_sql_utf8_hex_tuple"]) for row in rows),
        "ordered_target_sha256": _h(["task7-current-guard-ordered-targets-v1", targets]),
        "native_schema_registry_sha256": artifact["schema_registry"]["expected_sha256"],
    }


def _decode_json_no_duplicates(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate member {key!r}")
            value[key] = child
        return value

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise ValueError("receipt root is not an object")
    return value


def verify_postgres_census_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify a signed complete v10 executable PostgreSQL census."""

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        receipt = _decode_json_no_duplicates(receipt_raw)
        if (
            tuple(receipt)
            != (
                "schema_version",
                "signer_id",
                "payload",
                "payload_sha256",
                "public_key_sha256",
                "signature_base64",
            )
            or receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA
        ):
            raise ValueError("receipt envelope shape differs")
        signer_id = receipt["signer_id"]
        payload = receipt["payload"]
        if not isinstance(signer_id, str) or not signer_id or not isinstance(payload, dict):
            raise ValueError("receipt signer/payload differs")
        payload_sha256 = _sha256_bytes(_canonical_bytes(payload))
        public_key_sha256 = _sha256_bytes(trusted_public_key_raw)
        if (
            receipt["payload_sha256"] != payload_sha256
            or receipt["public_key_sha256"] != public_key_sha256
        ):
            raise ValueError("receipt binding differs")
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("trust root is not Ed25519")
        signature = base64.b64decode(
            receipt["signature_base64"],
            validate=True,
        )
        public_key.verify(
            signature,
            _canonical_bytes(
                [
                    _CENSUS_SIGNING_DOMAIN,
                    signer_id,
                    payload_sha256,
                    public_key_sha256,
                    payload,
                ]
            ),
        )

        artifact = _decode_artifact(artifact_raw)
        _validate_shape(artifact_raw, artifact)
        if artifact["metadata"]["append_frozen"] is not False:
            raise ValueError("receipt must bind the unfrozen candidate")
        prospective_frozen_raw = derive_prospective_frozen_bytes(artifact_raw)
        execution = payload.get("database_execution")
        expected_payload = {
            "schema_version": _CENSUS_PAYLOAD_SCHEMA,
            "result": "PASS",
            "artifact_schema": artifact["metadata"]["artifact_schema"],
            "pipeline_contract_version": "full-strict-v10",
            "candidate_artifact_sha256": _sha256_bytes(artifact_raw),
            "prospective_frozen_artifact_sha256": _sha256_bytes(prospective_frozen_raw),
            "authority_inventory": derive_authority_inventory(artifact),
            "database_execution": execution,
        }
        if payload != expected_payload or not isinstance(execution, dict):
            raise ValueError("receipt payload is not artifact-exact")
        required = {
            "database_product": "PostgreSQL",
            "base_schema_revision": "0037",
            "harness_identity": _CENSUS_HARNESS_IDENTITY,
            "harness_sha256": _CENSUS_HARNESS_SHA256,
            "current_guard_target_count": 73,
            "legal_execution_count": 73,
            "illegal_rejection_count": 73,
            "direct_sql_rejection_count": 73,
            "constructor_schema_proof_count": 4,
            "unexpected_outcome_count": 0,
        }
        if any(execution.get(key) != value for key, value in required.items()):
            raise ValueError("executable v10 census is incomplete")
        allowed_fields = (
            "database_product",
            "server_version_num",
            "base_schema_revision",
            "harness_identity",
            "harness_sha256",
            "current_guard_target_count",
            "legal_execution_count",
            "illegal_rejection_count",
            "direct_sql_rejection_count",
            "constructor_schema_proof_count",
            "unexpected_outcome_count",
            "catalog_projection_sha256",
            "case_result_sha256",
            "constructor_roundtrip_sha256",
            "manifest_parity_sha256",
        )
        if (
            tuple(execution) != allowed_fields
            or not isinstance(execution["server_version_num"], int)
            or execution["server_version_num"] < 160000
            or any(
                re.fullmatch(r"[0-9a-f]{64}", execution[field]) is None
                for field in (
                    "harness_sha256",
                    "catalog_projection_sha256",
                    "case_result_sha256",
                    "constructor_roundtrip_sha256",
                    "manifest_parity_sha256",
                )
            )
        ):
            raise ValueError("database execution shape differs")
    except (
        InvalidSignature,
        binascii.Error,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ContractArtifactError(
            "TASK7_V10_POSTGRES_CENSUS_RECEIPT_INVALID",
            "signed executable PostgreSQL census did not verify",
        ) from exc
    return payload


def _render_fixture(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _normalized_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _lock_path(artifact_path: Path, fixture_path: Path) -> Path:
    identity = _h(
        [
            "task7-v10-atomic-admission-lock-v1",
            _normalized_path(artifact_path),
            _normalized_path(fixture_path),
        ]
    )
    root = Path(tempfile.gettempdir()) / "idis-task7-v10-admission-locks"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{identity}.lock"


@contextmanager
def _exclusive_admission_lock(
    artifact_path: Path,
    fixture_path: Path,
) -> Iterator[None]:
    """Serialize all admission/recovery transitions for one target pair."""

    path = _lock_path(artifact_path, fixture_path)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = cast(Any, __import__("fcntl"))
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.task7-v10-admission-stage")


def _journal_path(artifact_path: Path, fixture_path: Path) -> Path:
    fixture_coordinate = _sha256_bytes(_normalized_path(fixture_path).encode("utf-8"))[:16]
    return artifact_path.with_name(
        f".{artifact_path.name}.{fixture_coordinate}.task7-v10-admission-journal.json"
    )


def _write_durable_exact(
    path: Path,
    content: bytes,
    *,
    mismatch_code: str,
) -> bool:
    """Create one durable sidecar, or prove an interrupted exact copy exists."""

    if not path.parent.is_dir():
        _error("TASK7_V10_FIXTURE_OUTPUT_PARENT_MISSING", str(path.parent))
    if path.exists():
        if path.read_bytes() != content:
            _error(mismatch_code, str(path))
        return False
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_parent(path)
    except FileExistsError:
        if path.read_bytes() != content:
            _error(mismatch_code, str(path))
        return False
    except OSError as exc:
        raise ContractArtifactError(
            "TASK7_V10_ATOMIC_ADMISSION_STAGE_FAILED",
            str(path),
        ) from exc
    return True


def _unlink_sidecar(path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)
        _fsync_parent(path)


def _journal_bytes(
    *,
    artifact_path: Path,
    fixture_path: Path,
    candidate_raw: bytes,
    frozen_raw: bytes,
    fixture_raw: bytes,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": "task7-v10-atomic-admission-journal-v1",
                "artifact_path_sha256": _sha256_bytes(
                    _normalized_path(artifact_path).encode("utf-8")
                ),
                "fixture_path_sha256": _sha256_bytes(
                    _normalized_path(fixture_path).encode("utf-8")
                ),
                "candidate_artifact_sha256": _sha256_bytes(candidate_raw),
                "frozen_artifact_sha256": _sha256_bytes(frozen_raw),
                "fixture_sha256": _sha256_bytes(fixture_raw),
                "receipt_sha256": _sha256_bytes(receipt_raw),
                "trusted_public_key_sha256": _sha256_bytes(trusted_public_key_raw),
            },
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _derive_admission_bytes(
    *,
    candidate_raw: bytes,
    frozen_raw: bytes,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> tuple[dict[str, Any], bytes]:
    if derive_prospective_frozen_bytes(candidate_raw) != frozen_raw:
        _error(
            "TASK7_V10_FROZEN_ARTIFACT_RECOVERY_MISMATCH",
            "frozen artifact is not the exact candidate transition",
        )
    payload = verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=candidate_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    result = derive_contract_bytes(frozen_raw, validate=True)
    result["postgres_authority_census"] = payload["database_execution"]
    return result, _render_fixture(result).encode("utf-8")


def _candidate_from_frozen(frozen_raw: bytes) -> bytes:
    frozen = _decode_artifact(frozen_raw)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    candidate = copy.deepcopy(frozen)
    candidate["metadata"]["append_frozen"] = False
    candidate_raw = _render_artifact(candidate)
    if derive_prospective_frozen_bytes(candidate_raw) != frozen_raw:
        _error(
            "TASK7_V10_FROZEN_ARTIFACT_RECOVERY_MISMATCH",
            "existing frozen bytes are not an exact v10 freeze",
        )
    return candidate_raw


def _assert_current_bytes(path: Path, expected: bytes, code: str) -> None:
    if not path.is_file() or path.read_bytes() != expected:
        _error(code, str(path))


def _rollback_intermediate_fixture(
    *,
    fixture_path: Path,
    fixture_raw: bytes,
) -> None:
    if not fixture_path.exists():
        return
    if fixture_path.read_bytes() != fixture_raw:
        _error(
            "TASK7_V10_ATOMIC_ADMISSION_ROLLBACK_MISMATCH",
            str(fixture_path),
        )
    try:
        fixture_path.unlink()
        _fsync_parent(fixture_path)
    except OSError as exc:
        raise ContractArtifactError(
            "TASK7_V10_ATOMIC_ADMISSION_ROLLBACK_FAILED",
            str(fixture_path),
        ) from exc


def _admit_locked(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    current_raw = artifact_path.read_bytes()
    current = _decode_artifact(current_raw)
    _validate_shape(current_raw, current)
    is_frozen = current["metadata"]["append_frozen"] is True
    fixture_exists = fixture_path.exists()

    candidate_raw = _candidate_from_frozen(current_raw) if is_frozen else current_raw
    frozen_raw = current_raw if is_frozen else derive_prospective_frozen_bytes(candidate_raw)
    result, fixture_raw = _derive_admission_bytes(
        candidate_raw=candidate_raw,
        frozen_raw=frozen_raw,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    journal = _journal_path(artifact_path, fixture_path)
    journal_raw = _journal_bytes(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        frozen_raw=frozen_raw,
        fixture_raw=fixture_raw,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    artifact_stage = _stage_path(artifact_path)
    fixture_stage = _stage_path(fixture_path)

    if is_frozen and fixture_exists:
        if fixture_path.read_bytes() != fixture_raw or not journal.is_file():
            _error(
                "TASK7_V10_FIXTURE_OUTPUT_ALREADY_EXISTS",
                str(fixture_path),
            )
        if journal.read_bytes() != journal_raw:
            _error("TASK7_V10_RECOVERY_JOURNAL_MISMATCH", str(journal))
        if artifact_stage.exists() and artifact_stage.read_bytes() != frozen_raw:
            _error("TASK7_V10_ARTIFACT_STAGE_MISMATCH", str(artifact_stage))
        if fixture_stage.exists() and fixture_stage.read_bytes() != fixture_raw:
            _error("TASK7_V10_FIXTURE_STAGE_MISMATCH", str(fixture_stage))
        _assert_current_bytes(
            artifact_path,
            frozen_raw,
            "TASK7_V10_CANDIDATE_CHANGED_DURING_ADMISSION",
        )
        _assert_current_bytes(
            fixture_path,
            fixture_raw,
            "TASK7_V10_INTERMEDIATE_FIXTURE_CHANGED",
        )
        for sidecar in (artifact_stage, fixture_stage, journal):
            _unlink_sidecar(sidecar)
            if sidecar.exists():
                _error("TASK7_V10_ATOMIC_ADMISSION_CLEANUP_FAILED", str(sidecar))
        return result

    if is_frozen:
        if not journal.is_file():
            _error("TASK7_V10_RECOVERY_JOURNAL_REQUIRED", str(journal))
        if journal.read_bytes() != journal_raw:
            _error("TASK7_V10_RECOVERY_JOURNAL_MISMATCH", str(journal))
        _write_durable_exact(
            fixture_stage,
            fixture_raw,
            mismatch_code="TASK7_V10_FIXTURE_STAGE_MISMATCH",
        )
        _assert_current_bytes(
            artifact_path,
            frozen_raw,
            "TASK7_V10_CANDIDATE_CHANGED_DURING_ADMISSION",
        )
        if fixture_path.exists():
            _error(
                "TASK7_V10_FIXTURE_OUTPUT_ALREADY_EXISTS",
                str(fixture_path),
            )
        try:
            os.replace(fixture_stage, fixture_path)
            _fsync_parent(fixture_path)
        except OSError as exc:
            raise ContractArtifactError(
                "TASK7_V10_ATOMIC_ADMISSION_FAILED",
                str(fixture_path),
            ) from exc
        _unlink_sidecar(journal)
        return result

    if fixture_exists:
        if fixture_path.read_bytes() != fixture_raw or not journal.is_file():
            _error(
                "TASK7_V10_FIXTURE_OUTPUT_ALREADY_EXISTS",
                str(fixture_path),
            )
        if journal.read_bytes() != journal_raw:
            _error("TASK7_V10_RECOVERY_JOURNAL_MISMATCH", str(journal))
        _write_durable_exact(
            artifact_stage,
            frozen_raw,
            mismatch_code="TASK7_V10_ARTIFACT_STAGE_MISMATCH",
        )
        _assert_current_bytes(
            artifact_path,
            candidate_raw,
            "TASK7_V10_CANDIDATE_CHANGED_DURING_ADMISSION",
        )
        _assert_current_bytes(
            fixture_path,
            fixture_raw,
            "TASK7_V10_INTERMEDIATE_FIXTURE_CHANGED",
        )
        try:
            os.replace(artifact_stage, artifact_path)
            _fsync_parent(artifact_path)
        except OSError as exc:
            raise ContractArtifactError(
                "TASK7_V10_ATOMIC_ADMISSION_FAILED",
                str(artifact_path),
            ) from exc
        _unlink_sidecar(journal)
        return result

    _write_durable_exact(
        journal,
        journal_raw,
        mismatch_code="TASK7_V10_RECOVERY_JOURNAL_MISMATCH",
    )
    _write_durable_exact(
        fixture_stage,
        fixture_raw,
        mismatch_code="TASK7_V10_FIXTURE_STAGE_MISMATCH",
    )
    _write_durable_exact(
        artifact_stage,
        frozen_raw,
        mismatch_code="TASK7_V10_ARTIFACT_STAGE_MISMATCH",
    )
    fixture_published = False
    artifact_published = False
    try:
        _assert_current_bytes(
            artifact_path,
            candidate_raw,
            "TASK7_V10_CANDIDATE_CHANGED_DURING_ADMISSION",
        )
        if fixture_path.exists():
            _error(
                "TASK7_V10_FIXTURE_OUTPUT_ALREADY_EXISTS",
                str(fixture_path),
            )
        os.replace(fixture_stage, fixture_path)
        fixture_published = True
        _fsync_parent(fixture_path)
        _assert_current_bytes(
            artifact_path,
            candidate_raw,
            "TASK7_V10_CANDIDATE_CHANGED_DURING_ADMISSION",
        )
        os.replace(artifact_stage, artifact_path)
        artifact_published = True
        _fsync_parent(artifact_path)
    except ContractArtifactError:
        if fixture_published and not artifact_published:
            _rollback_intermediate_fixture(
                fixture_path=fixture_path,
                fixture_raw=fixture_raw,
            )
        _unlink_sidecar(artifact_stage)
        _unlink_sidecar(fixture_stage)
        _unlink_sidecar(journal)
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        if fixture_published and not artifact_published:
            _rollback_intermediate_fixture(
                fixture_path=fixture_path,
                fixture_raw=fixture_raw,
            )
        _unlink_sidecar(artifact_stage)
        _unlink_sidecar(fixture_stage)
        _unlink_sidecar(journal)
        raise ContractArtifactError(
            "TASK7_V10_ATOMIC_ADMISSION_FAILED",
            str(artifact_path),
        ) from exc
    _unlink_sidecar(artifact_stage)
    _unlink_sidecar(fixture_stage)
    _unlink_sidecar(journal)
    return result


def admit_candidate_atomically(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Crash-safely publish fixture first and frozen artifact as commit point."""

    artifact_path = Path(artifact_path)
    fixture_path = Path(fixture_path)
    with _exclusive_admission_lock(artifact_path, fixture_path):
        return _admit_locked(
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-census-receipt", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        admit_candidate_atomically(
            artifact_path=args.artifact,
            fixture_path=args.output,
            receipt_raw=args.authority_census_receipt.read_bytes(),
            trusted_public_key_raw=args.trusted_public_key.read_bytes(),
        )
        return 0
    except (
        ContractArtifactError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
