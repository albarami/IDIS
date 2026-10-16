"""Verify, execute, and sign the approved Task-8 v26 PostgreSQL census.

Contract admission is deliberately completed before the strict operator profile
is loaded.  The only durable output is one exclusive, hash-only receipt under
the Task-8 local-report root; database URLs and key material never enter it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
SRC_ROOT: Final = REPO_ROOT / "src"
for _import_root in (REPO_ROOT, SRC_ROOT):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

CANONICAL_ARTIFACT_PATH: Final = (
    REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
)
CANONICAL_FIXTURE_PATH: Final = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v26_contract_hash_v1.json"
)
PARENT_V21_PATH: Final = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY_PATH: Final = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP_PATH: Final = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
INDEPENDENT_VERIFIER_PATH: Final = (
    REPO_ROOT / "scripts" / "verify_full_strict_v26_fixture_independently.py"
)
SQL_RUNNER_PATH: Final = REPO_ROOT / "scripts" / "run_task8_v26_sql_authority_census.py"
REPORT_ROOT: Final = REPO_ROOT / ".local_reports" / "slice100" / "task8"
DEFAULT_OUTPUT: Final = REPORT_ROOT / "task8_v26_postgres_census_signed_receipt.json"
PRIVATE_KEY_PATH: Final = Path(r"C:\IDIS-secrets\keys\governance-signing-private.pem")
APPROVED_SIGNER_ID: Final = "slice100-governance-signer-v1"

_ARTIFACT_SHA256: Final = "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27"
_FIXTURE_SHA256: Final = "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7"
_POLICY_ARTIFACT_SHA256: Final = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_REVIEWER_MAP_SHA256: Final = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_INDEPENDENT_VERIFIER_SHA256: Final = (
    "6bd55cdf12b0e879d3fc5ea3c79324ba2122be8f8cc4304631d5f28d6c9e9fd6"
)
_APPROVED_SQL_RUNNER_SHA256: Final = (
    "34506840551bc49b3eb9c2f0b5ddcbfafd5e8af62143626873e12e283cc630b6"
)
_FULL_STRICT_CONTRACT_HASH: Final = (
    "bb0bd8deb5c7c85aa292a860773353f04e60db01c80d2d3a02ff25aed7e840fc"
)
_IDENTITY_GRAPH_SHA256: Final = "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece"
_CONSERVATION_SHA256: Final = "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235"
_POLICY_SHA256: Final = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_BINDING_SHA256: Final = "3e07e20d1b304e0e22d8956f408562ac194103184d475623118497a21ae5a467"
_PARENT_ARTIFACT_SHA256: Final = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_WORKBOOK_SHA256: Final = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
_WORKBOOK_BYTE_COUNT: Final = 24_905
_DASHBOARD_AUTHORITY_SHA256: Final = (
    "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
)
_DASHBOARD_AUTHORITY_BODY_SHA256: Final = (
    "9dc3d588a8014796b2da01ac4942dd0b2037db2f0fff6a809197ed786cdf95d1"
)

_ENVELOPE_SCHEMA: Final = "task8-v26-postgres-authority-census-envelope-v1"
_PAYLOAD_SCHEMA: Final = "task8-v26-postgres-authority-census-payload-v1"
_CLI_SCHEMA: Final = "task8-v26-signed-census-cli-result-v1"
_SIGNING_DOMAIN: Final = "task8-v26-postgres-authority-census-signing-v1"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

_MAX_ARTIFACT_BYTES: Final = 16 * 1024 * 1024
_MAX_FIXTURE_BYTES: Final = 2 * 1024 * 1024
_MAX_POLICY_BYTES: Final = 2 * 1024 * 1024
_MAX_REVIEWER_MAP_BYTES: Final = 512 * 1024
_MAX_WORKBOOK_BYTES: Final = 64 * 1024 * 1024
_MAX_SOURCE_BYTES: Final = 2 * 1024 * 1024
_MAX_PUBLIC_KEY_BYTES: Final = 64 * 1024
_MAX_RECEIPT_BYTES: Final = 2 * 1024 * 1024

_PAYLOAD_FIELDS: Final = (
    "schema_version",
    "result",
    "artifact_schema",
    "fixture_schema",
    "pipeline_contract_version",
    "artifact_sha256",
    "fixture_sha256",
    "full_strict_contract_hash",
    "identity_graph_sha256",
    "identity_graph_counts",
    "approved_semantics_conservation_sha256",
    "policy_id",
    "policy_artifact_sha256",
    "policy_sha256",
    "reviewer_map_sha256",
    "policy_binding_sha256",
    "base_profile_hmac",
    "runner_sha256",
    "case_manifest_sha256",
    "database_execution",
    "disposable_database_dropped",
)
_ENVELOPE_FIELDS: Final = (
    "schema_version",
    "signer_id",
    "payload",
    "payload_sha256",
    "public_key_sha256",
    "signature_base64",
)
_SAFE_EXECUTION_FIELDS: Final = (
    "admin_behavior_case_count",
    "admin_observation_case_count",
    "admin_dml_case_count",
    "application_seed_dml_count",
    "seed_dml_role_attestation_sha256",
    "application_case_execution_count",
    "erasure_executor_case_execution_count",
    "legal_execution_count",
    "illegal_rejection_count",
    "direct_sql_rejection_count",
    "rejected_case_projection_count",
    "rejected_case_zero_mutation_count",
    "unexpected_outcome_count",
    "behavior_role_match_count",
    "dashboard_coverage_row_count",
    "dashboard_commercial_coverage_row_count",
    "dashboard_financial_coverage_row_count",
    "dashboard_seed_plan_sha256",
    "dashboard_seed_db_projection_sha256",
    "seed_fk_closure_sha256",
    "seed_native_helper_equality_sha256",
    "source_freeze_sha256",
    "task5_roundtrip_sha256",
    "main_dashboard_census_sha256",
    "requirement_matrix_sha256",
    "requirement_predicate_sha256",
    "requirement_legal_case_count",
    "requirement_negative_case_count",
    "requirement_negative_zero_mutation_count",
    "forward_lock_race_passed",
    "reverse_lock_race_passed",
    "rls_behavior_passed",
    "installed_tenant_rls_table_count",
    "catalog_observer_application_role",
    "partial_authority_target_count",
    "partial_authority_event_count",
    "partial_authority_udu_passed",
    "erasure_authority_udu_passed",
    "rejected_case_projection_sha256",
    "behavior_role_attestation_sha256",
    "rls_behavior_sha256",
    "case_result_sha256",
    "catalog_projection_sha256",
    "race_result_sha256",
    "partial_authority_udu_sha256",
    "erasure_authority_udu_sha256",
    "partial_initial_catalog_sha256",
    "partial_downgrade_catalog_sha256",
    "partial_final_catalog_sha256",
    "erasure_initial_catalog_sha256",
    "erasure_downgrade_catalog_sha256",
    "erasure_final_catalog_sha256",
    "complete_execution_sha256",
)


class Task8V26SignedCensusError(RuntimeError):
    """Secret-safe failure carrying one stable code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_ARGUMENTS_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class _OperatorMaterial:
    admin_database_url: str
    application_database_url: str
    erasure_database_url: str
    signer_id: str
    base_profile_hmac: str
    private_key: Ed25519PrivateKey
    public_key_raw: bytes
    workbook_raw: bytes


def _fail(code: str) -> Never:
    raise Task8V26SignedCensusError(code)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID") from exc


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _file_identity_from_stat(metadata: os.stat_result) -> tuple[int, int]:
    return int(metadata.st_dev), int(metadata.st_ino)


def _path_file_identity(path: Path) -> tuple[int, int]:
    try:
        return _file_identity_from_stat(os.lstat(path))
    except OSError as exc:
        raise Task8V26SignedCensusError(
            "TASK8_V26_SIGNED_CENSUS_FILE_IDENTITY_INVALID"
        ) from exc


def _same_file_identity(
    left: tuple[int, int],
    right: tuple[int, int],
) -> bool:
    return left == right and left[1] != 0


def _has_link_or_reparse_ancestor(path: Path) -> bool:
    """Reject lexical ancestor aliases instead of resolving their evidence away."""

    current = path.parent
    while True:
        if _is_link_or_reparse(current):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _read_exact_regular_file(
    path: Path,
    *,
    max_bytes: int,
    code: str,
    forbidden_paths: Sequence[Path] = (),
) -> bytes:
    descriptor = -1
    try:
        if (
            not path.is_absolute()
            or _is_link_or_reparse(path)
            or _has_link_or_reparse_ancestor(path)
        ):
            _fail(code)
        path_metadata = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_metadata = os.fstat(descriptor)
        opened_identity = _file_identity_from_stat(opened_metadata)
        for forbidden_path in forbidden_paths:
            if os.path.abspath(path) == os.path.abspath(forbidden_path):
                _fail("TASK8_V26_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN")
            try:
                forbidden_metadata = os.lstat(forbidden_path)
            except FileNotFoundError:
                continue
            if _same_file_identity(
                opened_identity,
                _file_identity_from_stat(forbidden_metadata),
            ):
                _fail("TASK8_V26_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN")
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or int(getattr(opened_metadata, "st_nlink", 0)) != 1
            or not _same_file_identity(
                _file_identity_from_stat(path_metadata),
                opened_identity,
            )
            or opened_metadata.st_size <= 0
            or opened_metadata.st_size > max_bytes
        ):
            _fail(code)
        chunks: list[bytes] = []
        remaining = int(opened_metadata.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                _fail(code)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        raw = b"".join(chunks)
        final_metadata = os.fstat(descriptor)
        final_path_metadata = os.lstat(path)
        if (
            final_metadata.st_size != len(raw)
            or int(getattr(final_metadata, "st_nlink", 0)) != 1
            or not _same_file_identity(
                opened_identity,
                _file_identity_from_stat(final_metadata),
            )
            or not _same_file_identity(
                opened_identity,
                _file_identity_from_stat(final_path_metadata),
            )
            or _has_link_or_reparse_ancestor(path)
        ):
            _fail(code)
    except Task8V26SignedCensusError:
        raise
    except OSError as exc:
        raise Task8V26SignedCensusError(code) from exc
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
    if not raw or len(raw) > max_bytes:
        _fail(code)
    return raw


def _decode_object(raw: bytes, *, code: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise Task8V26SignedCensusError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _load_hashed_module(
    *,
    path: Path,
    expected_sha256: str,
    module_name: str,
    code: str,
) -> ModuleType:
    raw = _read_exact_regular_file(path, max_bytes=_MAX_SOURCE_BYTES, code=code)
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
        _fail(code)
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise Task8V26SignedCensusError(code) from exc
    return module


def validate_preflight_assets(
    *,
    artifact_raw: bytes,
    fixture_raw: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    """Independently admit caller-supplied candidate bytes before secret access."""

    code = "TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID"
    exact_hashes = (
        (artifact_raw, _ARTIFACT_SHA256),
        (fixture_raw, _FIXTURE_SHA256),
        (parent_artifact_raw, _PARENT_ARTIFACT_SHA256),
        (policy_artifact_raw, _POLICY_ARTIFACT_SHA256),
        (reviewer_map_raw, _REVIEWER_MAP_SHA256),
    )
    if any(
        not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), expected)
        for raw, expected in exact_hashes
    ):
        _fail(code)
    verifier = _load_hashed_module(
        path=INDEPENDENT_VERIFIER_PATH,
        expected_sha256=_INDEPENDENT_VERIFIER_SHA256,
        module_name="_task8_v26_signed_census_independent_verifier",
        code=code,
    )
    try:
        report = verifier.verify_fixture(
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            v26_artifact_raw=artifact_raw,
            fixture_raw=fixture_raw,
            reviewer_map_raw=reviewer_map_raw,
        )
        artifact = _decode_object(artifact_raw, code=code)
        fixture = _decode_object(fixture_raw, code=code)
        metadata = artifact["metadata"]
        graph = fixture["identity_hash_dependency_graph"]
        counts = fixture["counts"]
        conservation = fixture["approved_semantics_conservation"]
        policy_binding = fixture["policy_binding"]
        valid = (
            isinstance(report, Mapping)
            and metadata["artifact_schema"] == "full-strict-v26-reviewed-preimage-v1"
            and metadata["pipeline_contract_version"] == "full-strict-v26"
            and metadata["activation_status"] == "INACTIVE"
            and fixture["artifact_schema"] == "full-strict-v26-contract-hash-fixture-v1"
            and fixture["pipeline_contract_version"] == "full-strict-v26"
            and fixture["activation_status"] == "INACTIVE"
            and report["artifact_sha256"] == _ARTIFACT_SHA256
            and report["contract_hash"] == _FULL_STRICT_CONTRACT_HASH
            and report["identity_graph_sha256"] == _IDENTITY_GRAPH_SHA256
            and report["conservation_sha256"] == _CONSERVATION_SHA256
            and report["policy_binding_sha256"] == _POLICY_BINDING_SHA256
            and graph["graph_sha256"] == _IDENTITY_GRAPH_SHA256
            and conservation["sha256"] == _CONSERVATION_SHA256
            and policy_binding["artifact_sha256"] == _POLICY_ARTIFACT_SHA256
            and policy_binding["policy_sha256"] == _POLICY_SHA256
            and policy_binding["reviewer_map_sha256"] == _REVIEWER_MAP_SHA256
            and policy_binding["binding_sha256"] == _POLICY_BINDING_SHA256
        )
    except Task8V26SignedCensusError:
        raise
    except Exception as exc:
        raise Task8V26SignedCensusError(code) from exc
    if not valid:
        _fail(code)
    return {
        "schema_version": "task8-v26-postgres-census-preflight-v1",
        "artifact_schema": "full-strict-v26-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v26-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v26",
        "activation_status": "INACTIVE",
        "artifact_sha256": _ARTIFACT_SHA256,
        "fixture_sha256": _FIXTURE_SHA256,
        "contract_sha256": _FULL_STRICT_CONTRACT_HASH,
        "identity_graph_sha256": _IDENTITY_GRAPH_SHA256,
        "identity_graph_counts": dict(graph["counts"]),
        "artifact_counts": dict(counts),
        "conservation_sha256": _CONSERVATION_SHA256,
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": _POLICY_ARTIFACT_SHA256,
        "policy_sha256": _POLICY_SHA256,
        "reviewer_map_sha256": _REVIEWER_MAP_SHA256,
        "policy_binding_sha256": _POLICY_BINDING_SHA256,
        "parent_artifact_sha256": _PARENT_ARTIFACT_SHA256,
    }


def load_and_verify_preflight_assets(
    *,
    artifact: Path,
    fixture: Path,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read explicit noncanonical candidates and independently admit them."""

    code = "TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID"
    assets = {
        "artifact_raw": _read_exact_regular_file(
            artifact,
            max_bytes=_MAX_ARTIFACT_BYTES,
            code=code,
            forbidden_paths=(CANONICAL_ARTIFACT_PATH, CANONICAL_FIXTURE_PATH),
        ),
        "fixture_raw": _read_exact_regular_file(
            fixture,
            max_bytes=_MAX_FIXTURE_BYTES,
            code=code,
            forbidden_paths=(CANONICAL_ARTIFACT_PATH, CANONICAL_FIXTURE_PATH),
        ),
        "parent_artifact_raw": _read_exact_regular_file(
            PARENT_V21_PATH.resolve(), max_bytes=_MAX_ARTIFACT_BYTES, code=code
        ),
        "policy_artifact_raw": _read_exact_regular_file(
            POLICY_PATH.resolve(), max_bytes=_MAX_POLICY_BYTES, code=code
        ),
        "reviewer_map_raw": _read_exact_regular_file(
            REVIEWER_MAP_PATH.resolve(), max_bytes=_MAX_REVIEWER_MAP_BYTES, code=code
        ),
    }
    return validate_preflight_assets(**assets), assets


def validate_v26_dashboard_requirement_assets(*, artifact_raw: bytes, policy_raw: bytes) -> None:
    """Bind the frozen v3 requirement body to the exact policy-v2 tuple."""

    code = "TASK8_V26_DASHBOARD_REQUIREMENT_BINDING_INVALID"
    if not secrets.compare_digest(
        hashlib.sha256(artifact_raw).hexdigest(), _ARTIFACT_SHA256
    ) or not secrets.compare_digest(
        hashlib.sha256(policy_raw).hexdigest(),
        _POLICY_ARTIFACT_SHA256,
    ):
        _fail(code)
    try:
        artifact = _decode_object(artifact_raw, code=code)
        policy = _decode_object(policy_raw, code=code)
        authority = next(
            row
            for row in artifact["sql_authority_registry"]["rows"]
            if row.get("authority_id") == "dashboard-root-equation-v3"
        )
        statements = tuple(
            bytes.fromhex(value).decode("utf-8") for value in authority["body_sql_utf8_hex_tuple"]
        )
        dashboard_sql = "\n".join(statements)
        requirements = tuple(
            requirement
            for row in policy["workbook_row_registry"]["rows"]
            for requirement in row["required_calculations"]
        )
    except (KeyError, StopIteration, TypeError, UnicodeError, ValueError) as exc:
        raise Task8V26SignedCensusError(code) from exc
    if (
        authority.get("expected_sha256") != _DASHBOARD_AUTHORITY_SHA256
        or hashlib.sha256(dashboard_sql.encode("utf-8")).hexdigest()
        != _DASHBOARD_AUTHORITY_BODY_SHA256
        or len(requirements) != 10
        or any(
            set(requirement) != {"formula_ordinal", "formula_type", "required", "scope"}
            for requirement in requirements
        )
        or any(requirement["required"] is not True for requirement in requirements)
        or dashboard_sql.count("item->>'calc_type'") != 4
        or dashboard_sql.count("item->>'formula_type'") != 5
        or dashboard_sql.count("item->>'formula_ordinal'") != 2
        or "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE" not in dashboard_sql
    ):
        _fail(code)


def private_profile_mapping(process_env: Mapping[str, str]) -> dict[str, str]:
    locator = str(process_env.get("IDIS_STRICT_DOTENV_PATH", "")).strip()
    if not locator:
        _fail("TASK8_V26_SIGNED_CENSUS_PROFILE_INVALID")
    return {"IDIS_STRICT_DOTENV_PATH": locator}


def load_operator_material(process_env: Mapping[str, str]) -> _OperatorMaterial:
    """Load secrets into one private mapping only after independent admission."""

    from idis.runtime.profile import StrictProfileError, load_strict_runtime_profile
    from idis.runtime.secure_files import SecureFileError, secure_read_integrity_file
    from idis.tools.governance_signing import GovernanceSigningError, _read_secure_private_key

    private_mapping = private_profile_mapping(process_env)
    try:
        profile = load_strict_runtime_profile(
            command="migrate",
            process_env=private_mapping,
        )
    except StrictProfileError as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_PROFILE_INVALID") from exc
    try:
        signer_id = str(profile.values["IDIS_GOVERNANCE_TRUSTED_SIGNER_ID"])
        admin_url = str(profile.values["IDIS_DATABASE_ADMIN_URL"])
        application_url = str(profile.values["IDIS_DATABASE_URL"])
        erasure_url = str(profile.values["IDIS_ERASURE_DATABASE_URL"])
        workbook_path = Path(str(profile.values["IDIS_FDD_METHODOLOGY_PATH"]))
        public_path = Path(str(profile.values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"]))
        public_raw = secure_read_integrity_file(
            str(public_path),
            max_bytes=_MAX_PUBLIC_KEY_BYTES,
            require_readonly=True,
            require_secure_parent=True,
        )
        workbook_raw = _read_exact_regular_file(
            workbook_path,
            max_bytes=_MAX_WORKBOOK_BYTES,
            code="TASK8_V26_SIGNED_CENSUS_WORKBOOK_INVALID",
        )
        private_key = serialization.load_pem_private_key(
            _read_secure_private_key(PRIVATE_KEY_PATH),
            password=None,
        )
        public_key = serialization.load_pem_public_key(public_raw)
    except (
        GovernanceSigningError,
        SecureFileError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_KEY_INVALID") from exc
    if (
        not secrets.compare_digest(signer_id, APPROVED_SIGNER_ID)
        or not all(value.strip() for value in (admin_url, application_url, erasure_url))
        or not isinstance(private_key, Ed25519PrivateKey)
        or not isinstance(public_key, Ed25519PublicKey)
        or not _SHA256_RE.fullmatch(profile.base_profile_hmac)
        or not secrets.compare_digest(
            hashlib.sha256(public_raw).hexdigest(),
            profile.governance_public_key_sha256,
        )
        or not secrets.compare_digest(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            ),
            public_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            ),
        )
        or len(workbook_raw) != _WORKBOOK_BYTE_COUNT
        or not secrets.compare_digest(hashlib.sha256(workbook_raw).hexdigest(), _WORKBOOK_SHA256)
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_OPERATOR_MATERIAL_INVALID")
    return _OperatorMaterial(
        admin_database_url=admin_url,
        application_database_url=application_url,
        erasure_database_url=erasure_url,
        signer_id=signer_id,
        base_profile_hmac=profile.base_profile_hmac,
        private_key=private_key,
        public_key_raw=public_raw,
        workbook_raw=workbook_raw,
    )


def _current_runner_sha256() -> str:
    raw = _read_exact_regular_file(
        SQL_RUNNER_PATH,
        max_bytes=_MAX_SOURCE_BYTES,
        code="TASK8_V26_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
    )
    digest = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(digest, _APPROVED_SQL_RUNNER_SHA256):
        _fail("TASK8_V26_SIGNED_CENSUS_RUNNER_UNAUTHORIZED")
    return digest


def _safe_database_execution(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(
        field not in value for field in _SAFE_EXECUTION_FIELDS
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    result = {field: value[field] for field in _SAFE_EXECUTION_FIELDS}
    exact_values = {
        "admin_behavior_case_count": 0,
        "admin_observation_case_count": 0,
        "admin_dml_case_count": 0,
        "application_case_execution_count": 22,
        "erasure_executor_case_execution_count": 3,
        "legal_execution_count": 6,
        "illegal_rejection_count": 11,
        "direct_sql_rejection_count": 8,
        "rejected_case_projection_count": 19,
        "rejected_case_zero_mutation_count": 19,
        "unexpected_outcome_count": 0,
        "behavior_role_match_count": 25,
        "dashboard_coverage_row_count": 100,
        "dashboard_commercial_coverage_row_count": 21,
        "dashboard_financial_coverage_row_count": 79,
        "requirement_legal_case_count": 4,
        "requirement_negative_case_count": 13,
        "requirement_negative_zero_mutation_count": 13,
        "installed_tenant_rls_table_count": 69,
        "partial_authority_target_count": 19,
        "partial_authority_event_count": 57,
    }
    if any(result.get(key) != expected for key, expected in exact_values.items()):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    if (
        type(result.get("application_seed_dml_count")) is not int
        or result["application_seed_dml_count"] <= 0
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    for field in (
        "forward_lock_race_passed",
        "reverse_lock_race_passed",
        "rls_behavior_passed",
        "catalog_observer_application_role",
        "partial_authority_udu_passed",
        "erasure_authority_udu_passed",
    ):
        if result.get(field) is not True:
            _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    digest_fields = [field for field in _SAFE_EXECUTION_FIELDS if field.endswith("_sha256")]
    if any(
        not isinstance(result[field], str) or not _SHA256_RE.fullmatch(result[field])
        for field in digest_fields
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    if (
        result["partial_initial_catalog_sha256"] != result["partial_final_catalog_sha256"]
        or result["partial_initial_catalog_sha256"] == result["partial_downgrade_catalog_sha256"]
        or result["erasure_initial_catalog_sha256"] != result["erasure_final_catalog_sha256"]
        or result["erasure_initial_catalog_sha256"] == result["erasure_downgrade_catalog_sha256"]
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    return result


def build_signable_tuple(
    *,
    signer_id: str,
    payload_sha256: str,
    public_key_sha256: str,
    payload: Mapping[str, Any],
) -> list[Any]:
    return [
        _SIGNING_DOMAIN,
        signer_id,
        payload_sha256,
        public_key_sha256,
        dict(payload),
    ]


def build_signed_receipt(
    *,
    signer_id: str,
    preflight: Mapping[str, Any],
    census_receipt: Mapping[str, Any],
    base_profile_hmac: str,
    private_key: Ed25519PrivateKey,
    trusted_public_key_pem: bytes,
) -> dict[str, Any]:
    if (
        not secrets.compare_digest(signer_id, APPROVED_SIGNER_ID)
        or not _SHA256_RE.fullmatch(base_profile_hmac)
        or census_receipt.get("schema_version") != "task8-v26-postgres-authority-census-receipt-v1"
        or census_receipt.get("result") != "PASS"
        or census_receipt.get("pipeline_contract_version") != "full-strict-v26"
        or census_receipt.get("artifact_sha256") != preflight.get("artifact_sha256")
        or census_receipt.get("disposable_database_dropped") is not True
        or not isinstance(census_receipt.get("manifest_sha256"), str)
        or not _SHA256_RE.fullmatch(str(census_receipt.get("manifest_sha256")))
        or not isinstance(private_key, Ed25519PrivateKey)
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    try:
        public_key = serialization.load_pem_public_key(trusted_public_key_pem)
    except (TypeError, ValueError) as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_KEY_INVALID") from exc
    if not isinstance(public_key, Ed25519PublicKey) or not secrets.compare_digest(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ),
        public_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ),
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_KEY_MISMATCH")
    payload = {
        "schema_version": _PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": "full-strict-v26-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v26-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v26",
        "artifact_sha256": preflight["artifact_sha256"],
        "fixture_sha256": preflight["fixture_sha256"],
        "full_strict_contract_hash": preflight["contract_sha256"],
        "identity_graph_sha256": preflight["identity_graph_sha256"],
        "identity_graph_counts": dict(preflight["identity_graph_counts"]),
        "approved_semantics_conservation_sha256": preflight["conservation_sha256"],
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": preflight["policy_artifact_sha256"],
        "policy_sha256": preflight["policy_sha256"],
        "reviewer_map_sha256": preflight["reviewer_map_sha256"],
        "policy_binding_sha256": preflight["policy_binding_sha256"],
        "base_profile_hmac": base_profile_hmac,
        "runner_sha256": _current_runner_sha256(),
        "case_manifest_sha256": census_receipt["manifest_sha256"],
        "database_execution": _safe_database_execution(census_receipt.get("database_execution")),
        "disposable_database_dropped": True,
    }
    if tuple(payload) != _PAYLOAD_FIELDS:
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    payload_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    public_key_sha256 = hashlib.sha256(trusted_public_key_pem).hexdigest()
    signable = build_signable_tuple(
        signer_id=signer_id,
        payload_sha256=payload_sha256,
        public_key_sha256=public_key_sha256,
        payload=payload,
    )
    receipt = {
        "schema_version": _ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(private_key.sign(_canonical_bytes(signable))).decode(
            "ascii"
        ),
    }
    if tuple(receipt) != _ENVELOPE_FIELDS:
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    return receipt


def render_signed_receipt(receipt: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(receipt),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID") from exc


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    preflight: Mapping[str, Any],
    trusted_public_key_pem: bytes,
) -> dict[str, Any]:
    if not receipt_raw or len(receipt_raw) > _MAX_RECEIPT_BYTES:
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    receipt = _decode_object(receipt_raw, code="TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    if (
        tuple(receipt) != _ENVELOPE_FIELDS
        or receipt.get("schema_version") != _ENVELOPE_SCHEMA
        or receipt.get("signer_id") != APPROVED_SIGNER_ID
        or not isinstance(receipt.get("payload"), dict)
        or tuple(receipt["payload"]) != _PAYLOAD_FIELDS
        or receipt_raw != render_signed_receipt(receipt)
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    payload = receipt["payload"]
    fixed_values = {
        "schema_version": _PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": "full-strict-v26-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v26-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v26",
        "artifact_sha256": preflight["artifact_sha256"],
        "fixture_sha256": preflight["fixture_sha256"],
        "full_strict_contract_hash": preflight["contract_sha256"],
        "identity_graph_sha256": preflight["identity_graph_sha256"],
        "identity_graph_counts": preflight["identity_graph_counts"],
        "approved_semantics_conservation_sha256": preflight["conservation_sha256"],
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": preflight["policy_artifact_sha256"],
        "policy_sha256": preflight["policy_sha256"],
        "reviewer_map_sha256": preflight["reviewer_map_sha256"],
        "policy_binding_sha256": preflight["policy_binding_sha256"],
        "runner_sha256": _current_runner_sha256(),
        "disposable_database_dropped": True,
    }
    if (
        any(payload.get(key) != value for key, value in fixed_values.items())
        or not isinstance(payload.get("base_profile_hmac"), str)
        or not _SHA256_RE.fullmatch(payload["base_profile_hmac"])
        or not isinstance(payload.get("case_manifest_sha256"), str)
        or not _SHA256_RE.fullmatch(payload["case_manifest_sha256"])
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    _safe_database_execution(payload.get("database_execution"))
    payload_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    public_key_sha256 = hashlib.sha256(trusted_public_key_pem).hexdigest()
    if (
        receipt.get("payload_sha256") != payload_sha256
        or receipt.get("public_key_sha256") != public_key_sha256
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID")
    try:
        public_key = serialization.load_pem_public_key(trusted_public_key_pem)
        signature = base64.b64decode(receipt["signature_base64"], validate=True)
        if not isinstance(public_key, Ed25519PublicKey):
            _fail("TASK8_V26_SIGNED_CENSUS_KEY_INVALID")
        public_key.verify(
            signature,
            _canonical_bytes(
                build_signable_tuple(
                    signer_id=receipt["signer_id"],
                    payload_sha256=payload_sha256,
                    public_key_sha256=public_key_sha256,
                    payload=payload,
                )
            ),
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_RECEIPT_INVALID") from exc
    return dict(payload)


@dataclass(frozen=True, slots=True)
class _HeldDirectoryIdentity:
    path: Path
    identity: tuple[int, int]
    descriptor: int | None = None
    windows_handle: int | None = None


def _open_windows_directory_handle(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x0080,  # FILE_READ_ATTRIBUTES
        0x0001 | 0x0002,  # FILE_SHARE_READ | FILE_SHARE_WRITE; deliberately no DELETE
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise OSError(ctypes.get_last_error(), "directory handle open failed")
    return int(handle)


@contextmanager
def _hold_directory_identity(path: Path) -> Iterator[_HeldDirectoryIdentity]:
    code = "TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID"
    descriptor: int | None = None
    windows_handle: int | None = None
    try:
        if _is_link_or_reparse(path):
            _fail(code)
        identity = _path_file_identity(path)
        if os.name == "nt":
            windows_handle = _open_windows_directory_handle(path)
        else:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            if not _same_file_identity(
                identity,
                _file_identity_from_stat(os.fstat(descriptor)),
            ):
                _fail(code)
        lease = _HeldDirectoryIdentity(
            path=path,
            identity=identity,
            descriptor=descriptor,
            windows_handle=windows_handle,
        )
        _revalidate_held_directory_identity(lease)
        yield lease
    except Task8V26SignedCensusError:
        raise
    except OSError as exc:
        raise Task8V26SignedCensusError(code) from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if windows_handle is not None:
            import ctypes

            with suppress(OSError):
                if not ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                    ctypes.c_void_p(windows_handle)
                ):
                    raise OSError(ctypes.get_last_error(), "directory handle close failed")


def _revalidate_held_directory_identity(lease: _HeldDirectoryIdentity) -> None:
    if (
        _is_link_or_reparse(lease.path)
        or not lease.path.is_dir()
        or not _same_file_identity(lease.identity, _path_file_identity(lease.path))
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")


def _unlink_owned_path(path: Path, owned_identity: tuple[int, int] | None) -> None:
    """Remove only the exact file identity created by this invocation."""

    if owned_identity is None:
        return
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise Task8V26SignedCensusError(
            "TASK8_V26_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED"
        ) from exc
    if not _same_file_identity(owned_identity, _file_identity_from_stat(metadata)):
        return
    try:
        path.unlink()
    except OSError as exc:
        raise Task8V26SignedCensusError(
            "TASK8_V26_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED"
        ) from exc


def validate_output_path(path: Path) -> Path:
    if not path.is_absolute() or path.suffix.casefold() != ".json" or path.is_symlink():
        _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    if path.exists():
        _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_EXISTS")
    lexical_root = Path(os.path.abspath(REPORT_ROOT))
    lexical_parent = Path(os.path.abspath(path.parent))
    if lexical_parent != lexical_root and lexical_root not in lexical_parent.parents:
        _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    current_lexical = lexical_parent
    while True:
        if _is_link_or_reparse(current_lexical):
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        if current_lexical == lexical_root:
            break
        if current_lexical.parent == current_lexical:
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        current_lexical = current_lexical.parent
    try:
        root = REPORT_ROOT.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID") from exc
    if (
        not root.is_dir()
        or _is_link_or_reparse(root)
        or (parent != root and root not in parent.parents)
    ):
        _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    current = parent
    while True:
        if not current.is_dir() or _is_link_or_reparse(current):
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        if current == root:
            break
        if current.parent == current:
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        current = current.parent
    return parent / path.name


@dataclass(slots=True)
class _ReservedOutput:
    path: Path
    descriptor: int
    identity: tuple[int, int]
    parent_lease: _HeldDirectoryIdentity
    committed: bool = False


@contextmanager
def _reserve_output(path: Path) -> Iterator[_ReservedOutput]:
    requested_output = Path(os.path.abspath(path))
    descriptor = -1
    owned_identity: tuple[int, int] | None = None
    with _hold_directory_identity(requested_output.parent) as parent_lease:
        output = validate_output_path(requested_output)
        if not _same_file_identity(
            parent_lease.identity,
            _path_file_identity(output.parent),
        ):
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        try:
            _revalidate_held_directory_identity(parent_lease)
            descriptor = os.open(
                output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            opened_metadata = os.fstat(descriptor)
            owned_identity = _file_identity_from_stat(opened_metadata)
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or int(getattr(opened_metadata, "st_nlink", 0)) != 1
                or not _same_file_identity(owned_identity, _path_file_identity(output))
            ):
                _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_WRITE_FAILED")
            _revalidate_held_directory_identity(parent_lease)
            reservation = _ReservedOutput(
                path=output,
                descriptor=descriptor,
                identity=owned_identity,
                parent_lease=parent_lease,
            )
        except FileExistsError as exc:
            raise Task8V26SignedCensusError(
                "TASK8_V26_SIGNED_CENSUS_OUTPUT_EXISTS"
            ) from exc
        except Task8V26SignedCensusError:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
                descriptor = -1
            _unlink_owned_path(output, owned_identity)
            raise
        except OSError as exc:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
                descriptor = -1
            _unlink_owned_path(output, owned_identity)
            raise Task8V26SignedCensusError(
                "TASK8_V26_SIGNED_CENSUS_OUTPUT_WRITE_FAILED"
            ) from exc
        try:
            yield reservation
        finally:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
                descriptor = -1
            if not reservation.committed:
                _unlink_owned_path(output, owned_identity)


def _write_reserved_output(reservation: _ReservedOutput, content: bytes) -> None:
    try:
        _revalidate_held_directory_identity(reservation.parent_lease)
        if (
            reservation.committed
            or not _same_file_identity(
                reservation.identity,
                _file_identity_from_stat(os.fstat(reservation.descriptor)),
            )
            or not _same_file_identity(
                reservation.identity,
                _path_file_identity(reservation.path),
            )
        ):
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_WRITE_FAILED")
        os.lseek(reservation.descriptor, 0, os.SEEK_SET)
        os.ftruncate(reservation.descriptor, 0)
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(reservation.descriptor, view[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(reservation.descriptor)
        final_metadata = os.fstat(reservation.descriptor)
        if (
            final_metadata.st_size != len(content)
            or int(getattr(final_metadata, "st_nlink", 0)) != 1
            or not _same_file_identity(
                reservation.identity,
                _file_identity_from_stat(final_metadata),
            )
            or not _same_file_identity(
                reservation.identity,
                _path_file_identity(reservation.path),
            )
        ):
            _fail("TASK8_V26_SIGNED_CENSUS_OUTPUT_WRITE_FAILED")
        _revalidate_held_directory_identity(reservation.parent_lease)
        reservation.committed = True
    except Task8V26SignedCensusError:
        raise
    except OSError as exc:
        raise Task8V26SignedCensusError(
            "TASK8_V26_SIGNED_CENSUS_OUTPUT_WRITE_FAILED"
        ) from exc


def write_exclusive(path: Path, content: bytes) -> None:
    with _reserve_output(path) as reservation:
        _write_reserved_output(reservation, content)


def execute_signed_census(
    *,
    output: Path,
    artifact: Path,
    fixture: Path,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    # Order is a security contract: bytes and graph first, profile second, DB last.
    preflight, assets = load_and_verify_preflight_assets(
        artifact=artifact,
        fixture=fixture,
    )
    validate_v26_dashboard_requirement_assets(
        artifact_raw=assets["artifact_raw"],
        policy_raw=assets["policy_artifact_raw"],
    )
    _current_runner_sha256()
    with _reserve_output(output) as reservation:
        operator_locator = (
            {"IDIS_STRICT_DOTENV_PATH": os.environ.get("IDIS_STRICT_DOTENV_PATH", "")}
            if process_env is None
            else process_env
        )
        material = load_operator_material(operator_locator)
        runner = _load_hashed_module(
            path=SQL_RUNNER_PATH,
            expected_sha256=_APPROVED_SQL_RUNNER_SHA256,
            module_name="_task8_v26_signed_census_sql_runner",
            code="TASK8_V26_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
        )
        try:
            census = runner.run_disposable_postgres_census(
                material.admin_database_url,
                material.application_database_url,
                material.erasure_database_url,
                assets["artifact_raw"],
                assets["fixture_raw"],
                assets["parent_artifact_raw"],
                assets["policy_artifact_raw"],
                assets["reviewer_map_raw"],
                material.workbook_raw,
            )
            artifact_object = _decode_object(
                assets["artifact_raw"], code="TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID"
            )
            runner.validate_census_receipt(census, artifact_object)
            receipt = build_signed_receipt(
                signer_id=material.signer_id,
                preflight=preflight,
                census_receipt=census,
                base_profile_hmac=material.base_profile_hmac,
                private_key=material.private_key,
                trusted_public_key_pem=material.public_key_raw,
            )
            rendered = render_signed_receipt(receipt)
            verify_signed_receipt(
                receipt_raw=rendered,
                preflight=preflight,
                trusted_public_key_pem=material.public_key_raw,
            )
        except Task8V26SignedCensusError:
            raise
        except Exception as exc:
            raise Task8V26SignedCensusError(
                "TASK8_V26_SIGNED_CENSUS_EXECUTION_FAILED"
            ) from exc
        _write_reserved_output(reservation, rendered)
    return {
        "schema_version": _CLI_SCHEMA,
        "result": "PASS",
        "receipt_sha256": hashlib.sha256(rendered).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def _emit(value: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(None if argv is None else list(argv))
        event = execute_signed_census(
            output=args.output,
            artifact=args.artifact,
            fixture=args.fixture,
        )
    except Task8V26SignedCensusError as exc:
        _emit({"schema_version": _CLI_SCHEMA, "result": "FAILED", "reason_code": exc.code})
        return 2
    except Exception:
        _emit(
            {
                "schema_version": _CLI_SCHEMA,
                "result": "FAILED",
                "reason_code": "TASK8_V26_SIGNED_CENSUS_FAILED",
            }
        )
        return 2
    _emit(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
