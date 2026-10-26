"""Verify, execute, and sign the approved Task-8 v24 PostgreSQL census.

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
from collections.abc import Mapping, Sequence
from contextlib import suppress
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

ARTIFACT_PATH: Final = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
FIXTURE_PATH: Final = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v24_contract_hash_v1.json"
)
POLICY_PATH: Final = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP_PATH: Final = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
INDEPENDENT_VERIFIER_PATH: Final = (
    REPO_ROOT / "scripts" / "verify_full_strict_v24_fixture_independently.py"
)
SQL_RUNNER_PATH: Final = REPO_ROOT / "scripts" / "run_task8_v24_sql_authority_census.py"
REPORT_ROOT: Final = REPO_ROOT / ".local_reports" / "slice100" / "task8"
DEFAULT_OUTPUT: Final = REPORT_ROOT / "task8_v24_postgres_census_signed_receipt.json"
PRIVATE_KEY_PATH: Final = Path(r"C:\IDIS-secrets\keys\governance-signing-private.pem")
APPROVED_SIGNER_ID: Final = "slice100-governance-signer-v1"

_ARTIFACT_SHA256: Final = "70834e33249a414e5dcc744cdc60934d9d2ed6c8ab3f09c64cf3edfa3f05e447"
_FIXTURE_SHA256: Final = "444e4c573e7ba44185efe0b4c39339797b2df91a0fcbf7841129e131e406526e"
_POLICY_ARTIFACT_SHA256: Final = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_REVIEWER_MAP_SHA256: Final = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_INDEPENDENT_VERIFIER_SHA256: Final = (
    "2c0cc55670bf08c83a13e5495652d35527b81d6e12c70d83b1f5c84b1af10996"
)
_APPROVED_SQL_RUNNER_SHA256: Final = (
    "151dea056d041984608d5c2757eb6710b3b388d635dc15217bd66da0741d89ff"
)
_FULL_STRICT_CONTRACT_HASH: Final = (
    "f05f17f98ea6a8d0356cef5939b7395fef294eb490a77cf8aeecf1ece25dd164"
)
_IDENTITY_GRAPH_SHA256: Final = "5c8b00d2e0930d4c1d5e5a4fbcc1db9ca74f9b28515e48da3a7021a835d84c5f"
_CONSERVATION_SHA256: Final = "635035b2e2a9e88c4cf1a90a1fb4023197f0c0df0bb84e181db2daefddfb36de"
_POLICY_SHA256: Final = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_BINDING_SHA256: Final = "6e6841d07b1891b6692ba210bc1be55aaee7f40a3bb6ffafdad56e5a21212bb3"
_DASHBOARD_AUTHORITY_SHA256: Final = (
    "513ca92f3e3c1c610af724413a1342743fc09b781fcb6fe6d6481b50f8570708"
)
_DASHBOARD_AUTHORITY_BODY_SHA256: Final = (
    "6a6113a72f6e68011e7d6c992060faccf63201c3dd76153b61e5a56386cff34c"
)

_ENVELOPE_SCHEMA: Final = "task8-v24-postgres-authority-census-envelope-v1"
_PAYLOAD_SCHEMA: Final = "task8-v24-postgres-authority-census-payload-v1"
_CLI_SCHEMA: Final = "task8-v24-signed-census-cli-result-v1"
_SIGNING_DOMAIN: Final = "task8-v24-postgres-authority-census-signing-v1"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

_MAX_ARTIFACT_BYTES: Final = 16 * 1024 * 1024
_MAX_FIXTURE_BYTES: Final = 2 * 1024 * 1024
_MAX_POLICY_BYTES: Final = 2 * 1024 * 1024
_MAX_REVIEWER_MAP_BYTES: Final = 512 * 1024
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


class Task8V24SignedCensusError(RuntimeError):
    """Secret-safe failure carrying one stable code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_ARGUMENTS_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class _OperatorMaterial:
    admin_database_url: str
    application_database_url: str
    erasure_database_url: str
    signer_id: str
    base_profile_hmac: str
    private_key: Ed25519PrivateKey
    public_key_raw: bytes


def _fail(code: str) -> Never:
    raise Task8V24SignedCensusError(code)


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
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID") from exc


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


def _read_exact_regular_file(path: Path, *, max_bytes: int, code: str) -> bytes:
    try:
        if (
            not path.is_absolute()
            or not path.is_file()
            or _is_link_or_reparse(path)
            or path.stat().st_size <= 0
            or path.stat().st_size > max_bytes
        ):
            _fail(code)
        raw = path.read_bytes()
    except Task8V24SignedCensusError:
        raise
    except OSError as exc:
        raise Task8V24SignedCensusError(code) from exc
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
        raise Task8V24SignedCensusError(code) from exc
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
        raise Task8V24SignedCensusError(code) from exc
    return module


def load_and_verify_preflight_assets() -> dict[str, Any]:
    """Independently admit exact v24 bytes without profile or database access."""

    code = "TASK8_V24_SIGNED_CENSUS_PREFLIGHT_INVALID"
    artifact_raw = _read_exact_regular_file(ARTIFACT_PATH, max_bytes=_MAX_ARTIFACT_BYTES, code=code)
    fixture_raw = _read_exact_regular_file(FIXTURE_PATH, max_bytes=_MAX_FIXTURE_BYTES, code=code)
    policy_raw = _read_exact_regular_file(POLICY_PATH, max_bytes=_MAX_POLICY_BYTES, code=code)
    reviewer_map_raw = _read_exact_regular_file(
        REVIEWER_MAP_PATH, max_bytes=_MAX_REVIEWER_MAP_BYTES, code=code
    )
    exact_hashes = (
        (artifact_raw, _ARTIFACT_SHA256),
        (fixture_raw, _FIXTURE_SHA256),
        (policy_raw, _POLICY_ARTIFACT_SHA256),
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
        module_name="_task8_v24_signed_census_independent_verifier",
        code=code,
    )
    try:
        report = verifier.verify_fixture(
            artifact_raw=artifact_raw,
            policy_artifact_raw=policy_raw,
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
            and metadata["artifact_schema"] == "full-strict-v24-reviewed-preimage-v1"
            and metadata["pipeline_contract_version"] == "full-strict-v24"
            and metadata["activation_status"] == "INACTIVE"
            and fixture["artifact_schema"] == "full-strict-v24-contract-hash-fixture-v1"
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
    except Task8V24SignedCensusError:
        raise
    except Exception as exc:
        raise Task8V24SignedCensusError(code) from exc
    if not valid:
        _fail(code)
    return {
        "schema_version": "task8-v24-postgres-census-preflight-v1",
        "pipeline_contract_version": "full-strict-v24",
        "activation_status": "INACTIVE",
        "artifact_sha256": _ARTIFACT_SHA256,
        "fixture_sha256": _FIXTURE_SHA256,
        "contract_sha256": _FULL_STRICT_CONTRACT_HASH,
        "identity_graph_sha256": _IDENTITY_GRAPH_SHA256,
        "identity_graph_counts": dict(graph["counts"]),
        "artifact_counts": dict(counts),
        "conservation_sha256": _CONSERVATION_SHA256,
        "policy_artifact_sha256": _POLICY_ARTIFACT_SHA256,
        "policy_sha256": _POLICY_SHA256,
        "policy_reviewer_map_sha256": _REVIEWER_MAP_SHA256,
        "policy_binding_sha256": _POLICY_BINDING_SHA256,
    }


def validate_v24_dashboard_requirement_assets() -> None:
    """Block the known policy-v2/dashboard-key mismatch before profile access."""

    code = "TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID"
    artifact_raw = _read_exact_regular_file(
        ARTIFACT_PATH,
        max_bytes=_MAX_ARTIFACT_BYTES,
        code=code,
    )
    policy_raw = _read_exact_regular_file(
        POLICY_PATH,
        max_bytes=_MAX_POLICY_BYTES,
        code=code,
    )
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
            if row.get("authority_id") == "dashboard-root-equation-v2"
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
        raise Task8V24SignedCensusError(code) from exc
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
        or dashboard_sql.count("item->>'calc_type'") != 3
        or "item->>'formula_type'" not in dashboard_sql
        or "item->>'formula_ordinal'" not in dashboard_sql
    ):
        _fail(code)


def private_profile_mapping(process_env: Mapping[str, str]) -> dict[str, str]:
    locator = str(process_env.get("IDIS_STRICT_DOTENV_PATH", "")).strip()
    if not locator:
        _fail("TASK8_V24_SIGNED_CENSUS_PROFILE_INVALID")
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
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_PROFILE_INVALID") from exc
    try:
        signer_id = str(profile.values["IDIS_GOVERNANCE_TRUSTED_SIGNER_ID"])
        admin_url = str(profile.values["IDIS_DATABASE_ADMIN_URL"])
        application_url = str(profile.values["IDIS_DATABASE_URL"])
        erasure_url = str(profile.values["IDIS_ERASURE_DATABASE_URL"])
        public_path = Path(str(profile.values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"]))
        public_raw = secure_read_integrity_file(
            str(public_path),
            max_bytes=_MAX_PUBLIC_KEY_BYTES,
            require_readonly=True,
            require_secure_parent=True,
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
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_KEY_INVALID") from exc
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
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_OPERATOR_MATERIAL_INVALID")
    return _OperatorMaterial(
        admin_database_url=admin_url,
        application_database_url=application_url,
        erasure_database_url=erasure_url,
        signer_id=signer_id,
        base_profile_hmac=profile.base_profile_hmac,
        private_key=private_key,
        public_key_raw=public_raw,
    )


def _current_runner_sha256() -> str:
    raw = _read_exact_regular_file(
        SQL_RUNNER_PATH,
        max_bytes=_MAX_SOURCE_BYTES,
        code="TASK8_V24_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
    )
    digest = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(digest, _APPROVED_SQL_RUNNER_SHA256):
        _fail("TASK8_V24_SIGNED_CENSUS_RUNNER_UNAUTHORIZED")
    return digest


def _safe_database_execution(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(
        field not in value for field in _SAFE_EXECUTION_FIELDS
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    result = {field: value[field] for field in _SAFE_EXECUTION_FIELDS}
    exact_values = {
        "admin_behavior_case_count": 0,
        "admin_observation_case_count": 0,
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
        "installed_tenant_rls_table_count": 69,
        "partial_authority_target_count": 19,
        "partial_authority_event_count": 57,
    }
    if any(result.get(key) != expected for key, expected in exact_values.items()):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    for field in (
        "forward_lock_race_passed",
        "reverse_lock_race_passed",
        "rls_behavior_passed",
        "catalog_observer_application_role",
        "partial_authority_udu_passed",
        "erasure_authority_udu_passed",
    ):
        if result.get(field) is not True:
            _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    digest_fields = [field for field in _SAFE_EXECUTION_FIELDS if field.endswith("_sha256")]
    if any(
        not isinstance(result[field], str) or not _SHA256_RE.fullmatch(result[field])
        for field in digest_fields
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    if (
        result["partial_initial_catalog_sha256"] != result["partial_final_catalog_sha256"]
        or result["partial_initial_catalog_sha256"] == result["partial_downgrade_catalog_sha256"]
        or result["erasure_initial_catalog_sha256"] != result["erasure_final_catalog_sha256"]
        or result["erasure_initial_catalog_sha256"] == result["erasure_downgrade_catalog_sha256"]
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
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
        or census_receipt.get("schema_version") != "task8-v24-postgres-authority-census-receipt-v1"
        or census_receipt.get("result") != "PASS"
        or census_receipt.get("pipeline_contract_version") != "full-strict-v24"
        or census_receipt.get("artifact_sha256") != preflight.get("artifact_sha256")
        or census_receipt.get("disposable_database_dropped") is not True
        or not isinstance(census_receipt.get("manifest_sha256"), str)
        or not _SHA256_RE.fullmatch(str(census_receipt.get("manifest_sha256")))
        or not isinstance(private_key, Ed25519PrivateKey)
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    try:
        public_key = serialization.load_pem_public_key(trusted_public_key_pem)
    except (TypeError, ValueError) as exc:
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_KEY_INVALID") from exc
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
        _fail("TASK8_V24_SIGNED_CENSUS_KEY_MISMATCH")
    payload = {
        "schema_version": _PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": "full-strict-v24-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v24-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v24",
        "artifact_sha256": preflight["artifact_sha256"],
        "fixture_sha256": preflight["fixture_sha256"],
        "full_strict_contract_hash": preflight["contract_sha256"],
        "identity_graph_sha256": preflight["identity_graph_sha256"],
        "identity_graph_counts": dict(preflight["identity_graph_counts"]),
        "approved_semantics_conservation_sha256": preflight["conservation_sha256"],
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": preflight["policy_artifact_sha256"],
        "policy_sha256": preflight["policy_sha256"],
        "reviewer_map_sha256": preflight["policy_reviewer_map_sha256"],
        "policy_binding_sha256": preflight["policy_binding_sha256"],
        "base_profile_hmac": base_profile_hmac,
        "runner_sha256": _current_runner_sha256(),
        "case_manifest_sha256": census_receipt["manifest_sha256"],
        "database_execution": _safe_database_execution(census_receipt.get("database_execution")),
        "disposable_database_dropped": True,
    }
    if tuple(payload) != _PAYLOAD_FIELDS:
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
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
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
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
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID") from exc


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    preflight: Mapping[str, Any],
    trusted_public_key_pem: bytes,
) -> dict[str, Any]:
    if not receipt_raw or len(receipt_raw) > _MAX_RECEIPT_BYTES:
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    receipt = _decode_object(receipt_raw, code="TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    if (
        tuple(receipt) != _ENVELOPE_FIELDS
        or receipt.get("schema_version") != _ENVELOPE_SCHEMA
        or receipt.get("signer_id") != APPROVED_SIGNER_ID
        or not isinstance(receipt.get("payload"), dict)
        or tuple(receipt["payload"]) != _PAYLOAD_FIELDS
        or receipt_raw != render_signed_receipt(receipt)
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    payload = receipt["payload"]
    fixed_values = {
        "schema_version": _PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": "full-strict-v24-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v24-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v24",
        "artifact_sha256": preflight["artifact_sha256"],
        "fixture_sha256": preflight["fixture_sha256"],
        "full_strict_contract_hash": preflight["contract_sha256"],
        "identity_graph_sha256": preflight["identity_graph_sha256"],
        "identity_graph_counts": preflight["identity_graph_counts"],
        "approved_semantics_conservation_sha256": preflight["conservation_sha256"],
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": preflight["policy_artifact_sha256"],
        "policy_sha256": preflight["policy_sha256"],
        "reviewer_map_sha256": preflight["policy_reviewer_map_sha256"],
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
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    _safe_database_execution(payload.get("database_execution"))
    payload_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    public_key_sha256 = hashlib.sha256(trusted_public_key_pem).hexdigest()
    if (
        receipt.get("payload_sha256") != payload_sha256
        or receipt.get("public_key_sha256") != public_key_sha256
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID")
    try:
        public_key = serialization.load_pem_public_key(trusted_public_key_pem)
        signature = base64.b64decode(receipt["signature_base64"], validate=True)
        if not isinstance(public_key, Ed25519PublicKey):
            _fail("TASK8_V24_SIGNED_CENSUS_KEY_INVALID")
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
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID") from exc
    return dict(payload)


def validate_output_path(path: Path) -> Path:
    if not path.is_absolute() or path.suffix.casefold() != ".json" or path.is_symlink():
        _fail("TASK8_V24_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    if path.exists():
        _fail("TASK8_V24_SIGNED_CENSUS_OUTPUT_EXISTS")
    try:
        root = REPORT_ROOT.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_OUTPUT_PATH_INVALID") from exc
    if (
        not root.is_dir()
        or _is_link_or_reparse(root)
        or (parent != root and root not in parent.parents)
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    current = parent
    while True:
        if not current.is_dir() or _is_link_or_reparse(current):
            _fail("TASK8_V24_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        if current == root:
            break
        if current.parent == current:
            _fail("TASK8_V24_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        current = current.parent
    return parent / path.name


def write_exclusive(path: Path, content: bytes) -> None:
    output = validate_output_path(path)
    descriptor = -1
    try:
        descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
    except FileExistsError as exc:
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_OUTPUT_EXISTS") from exc
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(OSError):
            output.unlink(missing_ok=True)
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_OUTPUT_WRITE_FAILED") from exc


def execute_signed_census(
    *,
    output: Path,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    # Order is a security contract: bytes and graph first, profile second, DB last.
    preflight = load_and_verify_preflight_assets()
    validate_v24_dashboard_requirement_assets()
    validated_output = validate_output_path(output)
    operator_locator = (
        {"IDIS_STRICT_DOTENV_PATH": os.environ.get("IDIS_STRICT_DOTENV_PATH", "")}
        if process_env is None
        else process_env
    )
    material = load_operator_material(operator_locator)
    runner = _load_hashed_module(
        path=SQL_RUNNER_PATH,
        expected_sha256=_APPROVED_SQL_RUNNER_SHA256,
        module_name="_task8_v24_signed_census_sql_runner",
        code="TASK8_V24_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
    )
    artifact_raw = _read_exact_regular_file(
        ARTIFACT_PATH,
        max_bytes=_MAX_ARTIFACT_BYTES,
        code="TASK8_V24_SIGNED_CENSUS_PREFLIGHT_INVALID",
    )
    policy_raw = _read_exact_regular_file(
        POLICY_PATH,
        max_bytes=_MAX_POLICY_BYTES,
        code="TASK8_V24_SIGNED_CENSUS_PREFLIGHT_INVALID",
    )
    if (
        hashlib.sha256(artifact_raw).hexdigest() != preflight["artifact_sha256"]
        or hashlib.sha256(policy_raw).hexdigest() != preflight["policy_artifact_sha256"]
    ):
        _fail("TASK8_V24_SIGNED_CENSUS_PREFLIGHT_INVALID")
    try:
        census = runner.run_disposable_postgres_census(
            material.admin_database_url,
            material.application_database_url,
            material.erasure_database_url,
            artifact_raw,
            policy_raw,
        )
        artifact = _decode_object(artifact_raw, code="TASK8_V24_SIGNED_CENSUS_PREFLIGHT_INVALID")
        runner.validate_census_receipt(census, artifact)
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
    except Task8V24SignedCensusError:
        raise
    except Exception as exc:
        raise Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_EXECUTION_FAILED") from exc
    write_exclusive(validated_output, rendered)
    return {
        "schema_version": _CLI_SCHEMA,
        "result": "PASS",
        "receipt_sha256": hashlib.sha256(rendered).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
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
        event = execute_signed_census(output=args.output)
    except Task8V24SignedCensusError as exc:
        _emit({"schema_version": _CLI_SCHEMA, "result": "FAILED", "reason_code": exc.code})
        return 2
    except Exception:
        _emit(
            {
                "schema_version": _CLI_SCHEMA,
                "result": "FAILED",
                "reason_code": "TASK8_V24_SIGNED_CENSUS_FAILED",
            }
        )
        return 2
    _emit(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
