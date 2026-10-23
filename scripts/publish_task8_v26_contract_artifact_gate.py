"""Signed, recoverable, no-clobber publication for full-strict-v26.

The fixture is linked first and the artifact is linked last.  Only the
artifact link is the publication commit point.  Recovery sidecars contain
hashes and process identity only; they are never contract authorities.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Never, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_REPO_ROOT = Path(__file__).resolve().parents[1]
_V26_INDEPENDENT_VERIFIER = Path(__file__).with_name(
    "verify_full_strict_v26_fixture_independently.py"
)
_CANONICAL_V26_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
_CANONICAL_V26_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v26_contract_hash_v1.json"
)

_ORDER = ("full_strict_v26_fixture", "full_strict_v26_artifact")
_AUTHORITY_ORDER = (
    "parent_v21_artifact",
    "fdd_formula_policy_artifact",
    "fdd_formula_policy_fixture",
    "fdd_formula_policy_review_map",
)
_JOURNAL_SCHEMA = "task8-v26-contract-artifact-gate-journal-v1"
_LOCK_SCHEMA = "task8-v26-contract-artifact-gate-lock-v1"
_INTENT_SCHEMA = "task8-v26-contract-artifact-gate-intent-v1"
_SIGNER_ID = "slice100-governance-signer-v1"
_SIGNING_DOMAIN = "task8-v26-postgres-authority-census-signing-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_EXPECTED_VERIFIER_SOURCE_SHA256 = (
    "6bd55cdf12b0e879d3fc5ea3c79324ba2122be8f8cc4304631d5f28d6c9e9fd6"
)
_EXPECTED_RUNNER_SHA256 = "34506840551bc49b3eb9c2f0b5ddcbfafd5e8af62143626873e12e283cc630b6"

_EXPECTED_RAW_SHA256 = {
    "parent_v21_artifact": "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd",
    "fdd_formula_policy_artifact": (
        "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
    ),
    "fdd_formula_policy_fixture": (
        "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"
    ),
    "fdd_formula_policy_review_map": (
        "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
    ),
    "full_strict_v26_artifact": "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27",
    "full_strict_v26_fixture": "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7",
}
_EXPECTED_IDENTITIES = {
    "artifact_sha256": _EXPECTED_RAW_SHA256["full_strict_v26_artifact"],
    "fixture_sha256": _EXPECTED_RAW_SHA256["full_strict_v26_fixture"],
    "full_strict_contract_hash": "bb0bd8deb5c7c85aa292a860773353f04e60db01c80d2d3a02ff25aed7e840fc",
    "identity_graph_sha256": "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece",
    "approved_semantics_conservation_sha256": (
        "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235"
    ),
    "policy_binding_sha256": "3e07e20d1b304e0e22d8956f408562ac194103184d475623118497a21ae5a467",
}
_EXPECTED_GRAPH_COUNTS = {
    "edge_count": 35,
    "leaf_count": 834,
    "node_count": 67,
    "source_pointer_count": 67,
}
_EXPECTED_POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"

_VOID_CANDIDATE_SHA256 = (
    "94edc7b19fe73ed75872ad052c84b7a9" + "fb341d32b9741f98a1a4c048a058b507",
    "4368d86b6bdd270f03d94bbc591a9ee2" + "f6fbc6c36c9c65076cd1549d2a4da56b",
    "a8294752ae392f72cbdf83ced9107df1" + "2243f3f26010347457f86603a302dc23",
    "ddbe234bfbac246e65b2b1c0dd30eae0" + "18e88754446d713f1defdb4d70232dfc",
    "b7782392676e884560840fc8314dbc4a" + "7673800e5cbeaa04a85eabdb329879c5",
    "ce413d234aff579a4cabec8ae81e903be" + "3fc8e70f843e3ef08ebea44907a88d7",
    "1d0b374fb7e4ea3a3874c7d2c1ac1eb" + "5d74701ef31603379ee42d753a84292bd",
    "70834e33249a414e5dcc744cdc60934d" + "9d2ed6c8ab3f09c64cf3edfa3f05e447",
    "444e4c573e7ba44185efe0b4c3933979" + "7b2df91a0fcbf7841129e131e406526e",
    "f05f17f98ea6a8d0356cef5939b7395f" + "ef294eb490a77cf8aeecf1ece25dd164",
    "5c8b00d2e0930d4c1d5e5a4fbcc1db9" + "ca74f9b28515e48da3a7021a835d84c5f",
    "635035b2e2a9e88c4cf1a90a1fb40231" + "97f0c0df0bb84e181db2daefddfb36de",
    "6e6841d07b1891b6692ba210bc1be55a" + "aee7f40a3bb6ffafdad56e5a21212bb3",
    "008dcf51b015e8191c93c607832b3e44" + "d8e364f0b2902b05e6fc44bacebf86a1",
    "3eab3b34412e9ba4f83584ce19c999c7" + "82ab9f85444c29e33379d22678a4ee0f",
    "48cca9fa4a50a1a456493e36c696ddb8" + "88669fa023d25426bacc19b8fa0620ed",
    "bad09ef3a9ce4773643ecb60b8c60989" + "c47327f6a0158d5cf9a9ca8081d4c398",
    "0d11924cbdaa472d131b912dd0ee09195" + "a2a41cbce0a872972c424a3a204b7d1",
    "67a838da50a8f1d81af68c3fad800bd4" + "2151f687d5f73e86bfbb9490f755fd64",
    "d5caf7232507ee99d051e355bd994e00" + "8162d8770beab117cb47926f10a0d4fa",
    "a8f27747ec5b13d0962f2202b21a3771" + "8f59c3a0e44a3f1672f6071ce2aa9bd0",
    "a16b12a71640b40bc472deb23d6d4b33" + "18ad22d35ce471c957099b8a9962dc60",
    "8915fc53da0eda52e256ad8da6fcfbe6" + "14cc066eea57c60a85f69bfb3bd357a2",
    "17c43749db2c3c4282868ccf7689f6eb" + "9b113b42f97612962c487f0f45649c94",
)
_VOID_VERSION_MARKERS = (
    b"full-strict-v22",
    b"full-strict-v23",
    b"full-strict-v24",
    b"full-strict-v25",
)

_ENVELOPE_FIELDS = (
    "schema_version",
    "signer_id",
    "payload",
    "payload_sha256",
    "public_key_sha256",
    "signature_base64",
)
_PAYLOAD_FIELDS = (
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
_EXECUTION_FIELDS = (
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
    "dashboard_seed_plan_sha256",
    "dashboard_seed_db_projection_sha256",
    "seed_fk_closure_sha256",
    "seed_native_helper_equality_sha256",
    "source_freeze_sha256",
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
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LOCAL_ERROR_CODES = frozenset(
    {
        "TASK8_V26_GATE_ALREADY_PUBLISHED",
        "TASK8_V26_GATE_CANONICAL_STAGING_FORBIDDEN",
        "TASK8_V26_GATE_CANONICAL_TARGET_REQUIRED",
        "TASK8_V26_GATE_CENSUS_RECEIPT_INVALID",
        "TASK8_V26_GATE_CLEANUP_FAILED",
        "TASK8_V26_GATE_FILE_IDENTITY_ALIAS",
        "TASK8_V26_GATE_FIXTURE_INVALID",
        "TASK8_V26_GATE_INPUT_INVALID",
        "TASK8_V26_GATE_INTERNAL_FAILURE",
        "TASK8_V26_GATE_LINK_IDENTITY_INVALID",
        "TASK8_V26_GATE_LOCK_CHANGED",
        "TASK8_V26_GATE_LOCK_INVALID",
        "TASK8_V26_GATE_LOCK_MISMATCH",
        "TASK8_V26_GATE_LOCKED",
        "TASK8_V26_GATE_OWNER_UNKNOWN",
        "TASK8_V26_GATE_PUBLICATION_INTERRUPTED",
        "TASK8_V26_GATE_RECOVERY_JOURNAL_INVALID",
        "TASK8_V26_GATE_RECOVERY_STAGE_INVALID",
        "TASK8_V26_GATE_RECOVERY_TARGET_INVALID",
        "TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID",
        "TASK8_V26_GATE_SIDECAR_COLLISION",
        "TASK8_V26_GATE_TARGET_ALREADY_EXISTS",
        "TASK8_V26_GATE_VOID_INPUT_FORBIDDEN",
    }
)

FixtureVerifier = Callable[..., Mapping[str, object]]
ReceiptVerifier = Callable[..., Mapping[str, object]]
OwnerStatus = Literal["LIVE", "DEAD_OR_REUSED", "UNKNOWN"]


class Task8V26GatePublicationError(RuntimeError):
    """Secret-safe failure carrying one stable publication code."""


def _fail(code: str) -> Never:
    raise Task8V26GatePublicationError(code)


def _raise_clean(code: str) -> Never:
    raise Task8V26GatePublicationError(code) from None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_object(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Task8V26GatePublicationError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return cast(dict[str, Any], value)


def _reject_void_input(raw: bytes) -> None:
    digest = _sha256(raw)
    if digest in _VOID_CANDIDATE_SHA256 or any(marker in raw for marker in _VOID_VERSION_MARKERS):
        _fail("TASK8_V26_GATE_VOID_INPUT_FORBIDDEN")
    if any(identifier.encode("ascii") in raw for identifier in _VOID_CANDIDATE_SHA256):
        _fail("TASK8_V26_GATE_VOID_INPUT_FORBIDDEN")


def _has_reparse_attribute(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _regular_status(path: Path, code: str) -> os.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise Task8V26GatePublicationError(code) from exc
    if path.is_symlink() or _has_reparse_attribute(path) or not stat.S_ISREG(status.st_mode):
        _fail(code)
    return status


def _validated_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    if _exists(candidate):
        _regular_status(candidate, "TASK8_V26_GATE_INPUT_INVALID")
    if not candidate.parent.is_dir():
        _fail("TASK8_V26_GATE_INPUT_INVALID")
    current = candidate.parent
    while True:
        if current.is_symlink() or _has_reparse_attribute(current):
            _fail("TASK8_V26_GATE_INPUT_INVALID")
        if current.parent == current:
            break
        current = current.parent
    return candidate.resolve(strict=False)


def _file_identity(path: Path) -> tuple[int, int, int]:
    status = _regular_status(path, "TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
    return int(status.st_dev), int(status.st_ino), int(status.st_nlink)


def _validate_distinct_and_unaliased(
    paths: Mapping[str, Path],
    *,
    require_single_link_names: set[str] | None = None,
) -> dict[str, Path]:
    validated = {name: _validated_path(path) for name, path in paths.items()}
    normalized = [os.path.normcase(str(path)) for path in validated.values()]
    if len(normalized) != len(set(normalized)):
        _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
    identities: dict[tuple[int, int], str] = {}
    single_link_names = (
        set(paths) if require_single_link_names is None else require_single_link_names
    )
    for name, path in validated.items():
        if not _exists(path):
            continue
        device, inode, links = _file_identity(path)
        if (name in single_link_names and links != 1) or (device, inode) in identities:
            _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
        identities[(device, inode)] = name
    return validated


def _read_exact(path: Path, expected_raw: bytes, code: str) -> None:
    _regular_status(path, code)
    try:
        observed = path.read_bytes()
    except OSError as exc:
        raise Task8V26GatePublicationError(code) from exc
    if observed != expected_raw:
        _fail(code)


def _path_sha256(path: Path) -> str:
    return _sha256(os.path.normcase(str(path.resolve(strict=False))).encode("utf-8"))


def _validate_input_bytes(
    *,
    raw_by_name: Mapping[str, bytes],
    path_by_name: Mapping[str, Path],
) -> None:
    for name, expected_sha256 in _EXPECTED_RAW_SHA256.items():
        raw = raw_by_name.get(name)
        path = path_by_name.get(name)
        if not isinstance(raw, bytes) or path is None:
            _fail("TASK8_V26_GATE_INPUT_INVALID")
        if name.startswith("full_strict_v26"):
            _reject_void_input(raw)
        if _sha256(raw) != expected_sha256:
            _fail("TASK8_V26_GATE_INPUT_INVALID")
        _read_exact(path, raw, "TASK8_V26_GATE_INPUT_INVALID")


def _load_independent_verifier() -> ModuleType:
    try:
        _regular_status(_V26_INDEPENDENT_VERIFIER, "TASK8_V26_GATE_FIXTURE_INVALID")
        source_raw = _V26_INDEPENDENT_VERIFIER.read_bytes()
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_FIXTURE_INVALID") from exc
    if _sha256(source_raw) != _EXPECTED_VERIFIER_SOURCE_SHA256:
        _fail("TASK8_V26_GATE_FIXTURE_INVALID")
    try:
        source = source_raw.decode("utf-8")
        code = compile(source, str(_V26_INDEPENDENT_VERIFIER), "exec")
        module = ModuleType("_task8_v26_publisher_independent_verifier")
        module.__file__ = str(_V26_INDEPENDENT_VERIFIER)
        previous_dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            exec(code, module.__dict__)
        finally:
            sys.dont_write_bytecode = previous_dont_write_bytecode
    except Exception as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_FIXTURE_INVALID") from exc
    return module


def _normalize_verifier_identities(result: Mapping[str, object]) -> dict[str, str]:
    translated = {
        "artifact_sha256": result.get("artifact_sha256"),
        "fixture_sha256": result.get("fixture_sha256"),
        "full_strict_contract_hash": result.get(
            "full_strict_contract_hash", result.get("contract_hash")
        ),
        "identity_graph_sha256": result.get("identity_graph_sha256"),
        "approved_semantics_conservation_sha256": result.get(
            "approved_semantics_conservation_sha256", result.get("conservation_sha256")
        ),
        "policy_binding_sha256": result.get("policy_binding_sha256"),
    }
    if not all(isinstance(value, str) for value in translated.values()):
        _fail("TASK8_V26_GATE_FIXTURE_INVALID")
    return cast(dict[str, str], translated)


def _validate_v26_fixture_composition(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v26_artifact_raw: bytes,
    fixture_raw: bytes,
) -> dict[str, str]:
    verifier = _load_independent_verifier()
    try:
        result = verifier.verify_fixture(
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=reviewer_map_raw,
            v26_artifact_raw=v26_artifact_raw,
            fixture_raw=fixture_raw,
        )
    except Exception as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_FIXTURE_INVALID") from exc
    if not isinstance(result, Mapping):
        _fail("TASK8_V26_GATE_FIXTURE_INVALID")
    identities = _normalize_verifier_identities(result)
    if identities != _EXPECTED_IDENTITIES:
        _fail("TASK8_V26_GATE_FIXTURE_INVALID")
    return identities


def _validate_execution_receipt(value: object) -> None:
    if not isinstance(value, Mapping) or tuple(value) != _EXECUTION_FIELDS:
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    exact = {
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
        "requirement_legal_case_count": 4,
        "requirement_negative_case_count": 13,
        "requirement_negative_zero_mutation_count": 13,
        "installed_tenant_rls_table_count": 69,
        "partial_authority_target_count": 19,
        "partial_authority_event_count": 57,
    }
    if any(value.get(name) != expected for name, expected in exact.items()):
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    for name in (
        "forward_lock_race_passed",
        "reverse_lock_race_passed",
        "rls_behavior_passed",
        "catalog_observer_application_role",
        "partial_authority_udu_passed",
        "erasure_authority_udu_passed",
    ):
        if value.get(name) is not True:
            _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    for name in _EXECUTION_FIELDS:
        if name.endswith("_sha256") and (
            not isinstance(value.get(name), str)
            or _SHA256_RE.fullmatch(cast(str, value[name])) is None
        ):
            _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    if (
        value["partial_initial_catalog_sha256"] != value["partial_final_catalog_sha256"]
        or value["partial_initial_catalog_sha256"] == value["partial_downgrade_catalog_sha256"]
        or value["erasure_initial_catalog_sha256"] != value["erasure_final_catalog_sha256"]
        or value["erasure_initial_catalog_sha256"] == value["erasure_downgrade_catalog_sha256"]
    ):
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")


def _validate_signed_census_receipt(
    *,
    receipt_raw: bytes,
    identities: Mapping[str, str],
    trusted_public_key_pem: bytes,
) -> dict[str, object]:
    if not isinstance(receipt_raw, bytes) or not receipt_raw or len(receipt_raw) > 2 * 1024 * 1024:
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    envelope = _decode_object(receipt_raw, "TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    if (
        tuple(envelope) != _ENVELOPE_FIELDS
        or envelope.get("schema_version") != "task8-v26-postgres-authority-census-envelope-v1"
        or envelope.get("signer_id") != _SIGNER_ID
        or not isinstance(envelope.get("payload"), dict)
        or tuple(cast(dict[str, Any], envelope["payload"])) != _PAYLOAD_FIELDS
        or _render(envelope) != receipt_raw
    ):
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    payload = cast(dict[str, Any], envelope["payload"])
    fixed: dict[str, object] = {
        "schema_version": "task8-v26-postgres-authority-census-payload-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v26-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v26-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v26",
        **dict(identities),
        "identity_graph_counts": _EXPECTED_GRAPH_COUNTS,
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": _EXPECTED_RAW_SHA256["fdd_formula_policy_artifact"],
        "policy_sha256": _EXPECTED_POLICY_SHA256,
        "reviewer_map_sha256": _EXPECTED_RAW_SHA256["fdd_formula_policy_review_map"],
        "runner_sha256": _EXPECTED_RUNNER_SHA256,
        "disposable_database_dropped": True,
    }
    if any(payload.get(name) != expected for name, expected in fixed.items()):
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    for name in ("base_profile_hmac", "case_manifest_sha256"):
        if not isinstance(payload.get(name), str) or _SHA256_RE.fullmatch(payload[name]) is None:
            _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    _validate_execution_receipt(payload.get("database_execution"))
    payload_sha256 = _sha256(_canonical(payload))
    public_key_sha256 = _sha256(trusted_public_key_pem)
    if (
        envelope.get("payload_sha256") != payload_sha256
        or envelope.get("public_key_sha256") != public_key_sha256
        or public_key_sha256 != _EXPECTED_CENSUS_PUBLIC_KEY_SHA256
    ):
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
    try:
        public_key = serialization.load_pem_public_key(trusted_public_key_pem)
        signature = base64.b64decode(cast(str, envelope["signature_base64"]), validate=True)
        if not isinstance(public_key, Ed25519PublicKey):
            _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")
        public_key.verify(
            signature,
            _canonical(
                [
                    _SIGNING_DOMAIN,
                    _SIGNER_ID,
                    payload_sha256,
                    public_key_sha256,
                    payload,
                ]
            ),
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID") from exc
    return dict(payload)


def _journal_path(artifact_path: Path) -> Path:
    return artifact_path.with_name(f".{artifact_path.name}.task8-v26.journal.json")


def _lock_path(artifact_path: Path) -> Path:
    return artifact_path.with_name(f".{artifact_path.name}.task8-v26.lock")


def _journal_temp_path(journal_path: Path, owner_token: str) -> Path:
    return journal_path.with_name(f"{journal_path.name}.new.{owner_token}")


def _lock_takeover_path(lock_path: Path, owner_token: str) -> Path:
    return lock_path.with_name(f"{lock_path.name}.takeover.{owner_token}")


def _stage_path(target: Path, owner_token: str, ordinal: int) -> Path:
    return target.with_name(f".{target.name}.task8-v26-stage.{owner_token}.{ordinal}")


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Best-effort metadata flush; no power-loss durability claim is made."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, raw: bytes, *, code: str) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise Task8V26GatePublicationError(code) from exc
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_PUBLICATION_INTERRUPTED") from exc
    _fsync_directory(path.parent)


def _same_file(left: Path, right: Path) -> bool:
    left_identity = _file_identity(left)
    right_identity = _file_identity(right)
    return left_identity[:2] == right_identity[:2]


def _process_exit_checkpoint(_name: str, _ordinal: int | None = None) -> None:
    """Private process-death seam.  Production never enables a checkpoint."""


def _windows_process_identity(pid: int) -> tuple[OwnerStatus, str | None]:
    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:
            return "DEAD_OR_REUSED", None
        return "UNKNOWN", None
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return "UNKNOWN", None
        if exit_code.value != 259:
            return "DEAD_OR_REUSED", None
        creation = FileTime()
        exited = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return "UNKNOWN", None
        ticks = (int(creation.high) << 32) | int(creation.low)
        return "LIVE", _sha256(f"windows-filetime:{ticks}".encode("ascii"))
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_identity(pid: int) -> tuple[OwnerStatus, str | None]:
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return "DEAD_OR_REUSED", None
    except (OSError, UnicodeError):
        return "UNKNOWN", None
    closing = raw.rfind(")")
    fields = raw[closing + 2 :].split() if closing >= 0 else []
    if len(fields) < 20 or not boot_id:
        return "UNKNOWN", None
    return "LIVE", _sha256(f"linux:{boot_id}:{fields[19]}".encode("ascii"))


def _query_process_identity(pid: int) -> tuple[OwnerStatus, str | None]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "DEAD_OR_REUSED", None
    if os.name == "nt":
        return _windows_process_identity(pid)
    return _posix_process_identity(pid)


def _process_start_identity(pid: int) -> str | None:
    status, identity = _query_process_identity(pid)
    return identity if status == "LIVE" else None


def _owner_status(pid: int, recorded_identity: str) -> OwnerStatus:
    status, current_identity = _query_process_identity(pid)
    if status != "LIVE":
        return status
    return "LIVE" if current_identity == recorded_identity else "DEAD_OR_REUSED"


def _lock_value(*, intent_sha256: str, owner_token: str) -> dict[str, Any]:
    identity = _process_start_identity(os.getpid())
    if identity is None:
        _fail("TASK8_V26_GATE_OWNER_UNKNOWN")
    return {
        "schema_version": _LOCK_SCHEMA,
        "intent_sha256": intent_sha256,
        "owner_token": owner_token,
        "owner_pid": os.getpid(),
        "owner_start_identity": identity,
    }


def _load_lock(path: Path) -> tuple[bytes, dict[str, Any]]:
    _regular_status(path, "TASK8_V26_GATE_LOCK_INVALID")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_LOCK_INVALID") from exc
    required = {
        "schema_version",
        "intent_sha256",
        "owner_token",
        "owner_pid",
        "owner_start_identity",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != _LOCK_SCHEMA
        or not isinstance(value.get("owner_pid"), int)
        or isinstance(value.get("owner_pid"), bool)
        or cast(int, value["owner_pid"]) <= 0
        or not isinstance(value.get("intent_sha256"), str)
        or _SHA256_RE.fullmatch(cast(str, value["intent_sha256"])) is None
        or not isinstance(value.get("owner_token"), str)
        or _SHA256_RE.fullmatch(cast(str, value["owner_token"])) is None
        or not isinstance(value.get("owner_start_identity"), str)
        or _SHA256_RE.fullmatch(cast(str, value["owner_start_identity"])) is None
        or _render(value) != raw
    ):
        _fail("TASK8_V26_GATE_LOCK_INVALID")
    return raw, cast(dict[str, Any], value)


def _intent_value(
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
    *,
    receipt_raw: bytes,
    identities: Mapping[str, str],
    authority_raw: Mapping[str, bytes],
    authority_paths: Mapping[str, Path],
) -> dict[str, Any]:
    return {
        "schema_version": _INTENT_SCHEMA,
        "authority_verified": list(_AUTHORITY_ORDER),
        "created_publication_order": list(_ORDER),
        "commit_point": "full_strict_v26_artifact",
        "signed_census_receipt_raw_sha256": _sha256(receipt_raw),
        "identity_sha256": {name: identities[name] for name in _EXPECTED_IDENTITIES},
        "output_sha256": {name: _sha256(outputs[name]) for name in _ORDER},
        "authority_sha256": {name: _sha256(authority_raw[name]) for name in _AUTHORITY_ORDER},
        "target_path_sha256": {name: _path_sha256(targets[name]) for name in _ORDER},
        "authority_path_sha256": {
            name: _path_sha256(authority_paths[name]) for name in _AUTHORITY_ORDER
        },
        "activation_status": "INACTIVE",
        "runtime_selectable": False,
    }


def _journal_value(intent: Mapping[str, Any], *, owner_token: str) -> dict[str, Any]:
    return {
        "schema_version": _JOURNAL_SCHEMA,
        "intent_sha256": _sha256(_render(intent)),
        "owner_token": owner_token,
        "created_publication_order": list(_ORDER),
        "signed_census_receipt_raw_sha256": intent["signed_census_receipt_raw_sha256"],
        "identity_sha256": dict(cast(Mapping[str, str], intent["identity_sha256"])),
        "output_sha256": dict(cast(Mapping[str, str], intent["output_sha256"])),
        "authority_sha256": dict(cast(Mapping[str, str], intent["authority_sha256"])),
        "activation_status": "INACTIVE",
        "runtime_selectable": False,
    }


def _write_journal(path: Path, value: Mapping[str, Any]) -> None:
    raw = _render(value)
    owner_token = cast(str, value["owner_token"])
    temporary = _journal_temp_path(path, owner_token)
    if _exists(path):
        _read_exact(path, raw, "TASK8_V26_GATE_RECOVERY_JOURNAL_INVALID")
        if _exists(temporary):
            if not _same_file(path, temporary):
                _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
            _unlink_owned_sidecar(temporary, owner_token=owner_token, allow_link_count=2)
        return
    if not _exists(temporary):
        _write_exclusive(
            temporary,
            raw,
            code="TASK8_V26_GATE_SIDECAR_COLLISION",
        )
    else:
        _read_exact(temporary, raw, "TASK8_V26_GATE_RECOVERY_JOURNAL_INVALID")
    _process_exit_checkpoint("after_journal_temp_fsync_before_link")
    try:
        os.link(temporary, path)
    except FileExistsError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_LOCKED") from exc
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_PUBLICATION_INTERRUPTED") from exc
    _fsync_directory(path.parent)
    if not _same_file(temporary, path):
        _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
    _unlink_owned_sidecar(temporary, owner_token=owner_token, allow_link_count=2)


def _unlink_owned_sidecar(
    path: Path,
    *,
    owner_token: str,
    allow_link_count: int = 1,
) -> None:
    if owner_token not in path.name:
        _fail("TASK8_V26_GATE_CLEANUP_FAILED")
    status = _regular_status(path, "TASK8_V26_GATE_CLEANUP_FAILED")
    if int(status.st_nlink) != allow_link_count:
        _fail("TASK8_V26_GATE_CLEANUP_FAILED")
    try:
        path.unlink()
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_CLEANUP_FAILED") from exc
    _fsync_directory(path.parent)


def _unlink_lock_or_journal(
    path: Path,
    *,
    expected_raw: bytes,
    owner_token: str,
) -> None:
    _read_exact(path, expected_raw, "TASK8_V26_GATE_CLEANUP_FAILED")
    value = _decode_object(expected_raw, "TASK8_V26_GATE_CLEANUP_FAILED")
    if value.get("owner_token") != owner_token:
        _fail("TASK8_V26_GATE_CLEANUP_FAILED")
    status = _regular_status(path, "TASK8_V26_GATE_CLEANUP_FAILED")
    if int(status.st_nlink) != 1:
        _fail("TASK8_V26_GATE_CLEANUP_FAILED")
    try:
        path.unlink()
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_CLEANUP_FAILED") from exc
    _fsync_directory(path.parent)


def _owned_namespace(
    *,
    targets: Mapping[str, Path],
    journal_path: Path,
    lock_path: Path,
    owner_token: str | None,
) -> tuple[Path | None, dict[int, Path]]:
    expected_temp = (
        _journal_temp_path(journal_path, owner_token) if owner_token is not None else None
    )
    expected_stages = (
        {
            ordinal: _stage_path(targets[name], owner_token, ordinal)
            for ordinal, name in enumerate(_ORDER, start=1)
        }
        if owner_token is not None
        else {}
    )
    prefixes: dict[Path, set[str]] = {
        journal_path.parent: {
            f"{journal_path.name}.new.",
            f"{lock_path.name}.takeover.",
        }
    }
    for target in targets.values():
        prefixes.setdefault(target.parent, set()).add(f".{target.name}.task8-v26-stage.")
    observed: set[Path] = set()
    try:
        for parent, names in prefixes.items():
            for child in parent.iterdir():
                if any(child.name.startswith(prefix) for prefix in names):
                    if child.is_symlink() or _has_reparse_attribute(child):
                        _fail("TASK8_V26_GATE_SIDECAR_COLLISION")
                    observed.add(child.absolute())
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_INPUT_INVALID") from exc
    expected: set[Path] = set(expected_stages.values())
    if expected_temp is not None:
        expected.add(expected_temp)
        expected.add(_lock_takeover_path(lock_path, cast(str, owner_token)))
    if observed - expected:
        _fail("TASK8_V26_GATE_SIDECAR_COLLISION")
    return expected_temp, expected_stages


def _validate_recovery_state(
    *,
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
    journal_path: Path,
    journal_raw: bytes,
    expected_temp: Path | None,
    expected_stages: Mapping[int, Path],
) -> bool:
    present = [_exists(targets[name]) for name in _ORDER]
    prefix = 0
    while prefix < len(_ORDER) and present[prefix]:
        prefix += 1
    if any(present[prefix:]):
        _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
    for ordinal, is_present in enumerate(present, start=1):
        if is_present:
            _read_exact(
                targets[_ORDER[ordinal - 1]],
                outputs[_ORDER[ordinal - 1]],
                "TASK8_V26_GATE_RECOVERY_TARGET_INVALID",
            )

    journal_exists = _exists(journal_path)
    temp_exists = expected_temp is not None and _exists(expected_temp)
    stage_ordinals = [ordinal for ordinal, path in expected_stages.items() if _exists(path)]
    if len(stage_ordinals) > 1:
        _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
    for ordinal in stage_ordinals:
        stage = expected_stages[ordinal]
        _read_exact(
            stage,
            outputs[_ORDER[ordinal - 1]],
            "TASK8_V26_GATE_RECOVERY_STAGE_INVALID",
        )
        target = targets[_ORDER[ordinal - 1]]
        if _exists(target):
            if not _same_file(stage, target) or _file_identity(stage)[2] != 2:
                _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
        elif _file_identity(stage)[2] != 1:
            _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
    for ordinal, is_present in enumerate(present, start=1):
        if (
            is_present
            and ordinal not in stage_ordinals
            and _file_identity(targets[_ORDER[ordinal - 1]])[2] != 1
        ):
            _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")

    if journal_exists:
        _read_exact(
            journal_path,
            journal_raw,
            "TASK8_V26_GATE_RECOVERY_JOURNAL_INVALID",
        )
    if temp_exists:
        if expected_temp is None:
            _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
        _read_exact(
            expected_temp,
            journal_raw,
            "TASK8_V26_GATE_RECOVERY_JOURNAL_INVALID",
        )
        if journal_exists:
            if not _same_file(expected_temp, journal_path):
                _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
            if _file_identity(expected_temp)[2] != 2:
                _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
        elif _file_identity(expected_temp)[2] != 1:
            _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
    elif journal_exists and _file_identity(journal_path)[2] != 1:
        _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")

    if not journal_exists and not temp_exists:
        if prefix == 0 and not stage_ordinals:
            return False
        if prefix == len(_ORDER) and not stage_ordinals:
            return True
        _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
    if temp_exists and not journal_exists:
        if prefix != 0 or stage_ordinals:
            _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
        return False
    if stage_ordinals:
        ordinal = stage_ordinals[0]
        if ordinal not in {prefix, prefix + 1}:
            _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
        if ordinal == prefix and prefix == 0:
            _fail("TASK8_V26_GATE_RECOVERY_TOPOLOGY_INVALID")
    return False


def _take_over_dead_lock(
    *,
    lock_path: Path,
    old_lock_raw: bytes,
    new_lock_raw: bytes,
    owner_token: str,
) -> None:
    takeover = _lock_takeover_path(lock_path, owner_token)
    if _exists(takeover):
        takeover_raw, takeover_value = _load_lock(takeover)
        if (
            takeover_value.get("intent_sha256")
            != _decode_object(new_lock_raw, "TASK8_V26_GATE_LOCK_INVALID").get("intent_sha256")
            or takeover_value.get("owner_token") != owner_token
        ):
            _fail("TASK8_V26_GATE_SIDECAR_COLLISION")
        status = _owner_status(
            cast(int, takeover_value["owner_pid"]),
            cast(str, takeover_value["owner_start_identity"]),
        )
        if status == "LIVE":
            _fail("TASK8_V26_GATE_LOCKED")
        if status == "UNKNOWN":
            _fail("TASK8_V26_GATE_OWNER_UNKNOWN")
        _read_exact(takeover, takeover_raw, "TASK8_V26_GATE_LOCK_CHANGED")
        _unlink_owned_sidecar(takeover, owner_token=owner_token)
    _write_exclusive(takeover, new_lock_raw, code="TASK8_V26_GATE_LOCKED")
    replaced = False
    try:
        _process_exit_checkpoint("after_takeover_fsync_before_replace")
        _read_exact(lock_path, old_lock_raw, "TASK8_V26_GATE_LOCK_CHANGED")
        os.replace(takeover, lock_path)
        replaced = True
        _fsync_directory(lock_path.parent)
        _read_exact(lock_path, new_lock_raw, "TASK8_V26_GATE_LOCK_CHANGED")
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_PUBLICATION_INTERRUPTED") from exc
    finally:
        if not replaced and _exists(takeover):
            with suppress(Exception):
                _unlink_owned_sidecar(takeover, owner_token=owner_token)


def _restore_old_lock(
    *,
    lock_path: Path,
    old_lock_raw: bytes,
    current_lock_raw: bytes,
    owner_token: str,
) -> None:
    takeover = _lock_takeover_path(lock_path, owner_token)
    if _exists(takeover):
        _fail("TASK8_V26_GATE_CLEANUP_FAILED")
    _write_exclusive(takeover, old_lock_raw, code="TASK8_V26_GATE_CLEANUP_FAILED")
    _read_exact(lock_path, current_lock_raw, "TASK8_V26_GATE_CLEANUP_FAILED")
    try:
        _process_exit_checkpoint("after_restore_fsync_before_replace")
        os.replace(takeover, lock_path)
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_CLEANUP_FAILED") from exc
    _fsync_directory(lock_path.parent)
    _read_exact(lock_path, old_lock_raw, "TASK8_V26_GATE_CLEANUP_FAILED")


def _unlink_created_target(path: Path, identity: tuple[int, int]) -> None:
    status = _regular_status(path, "TASK8_V26_GATE_CLEANUP_FAILED")
    if (int(status.st_dev), int(status.st_ino)) != identity or int(status.st_nlink) not in {
        1,
        2,
    }:
        _fail("TASK8_V26_GATE_CLEANUP_FAILED")
    try:
        path.unlink()
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_CLEANUP_FAILED") from exc
    _fsync_directory(path.parent)


def _publish_stage(
    *,
    stage: Path,
    target: Path,
    expected_raw: bytes,
    owner_token: str,
    ordinal: int,
    created_targets: list[tuple[str, tuple[int, int]]],
    name: str,
) -> None:
    _read_exact(stage, expected_raw, "TASK8_V26_GATE_RECOVERY_STAGE_INVALID")
    if _exists(target):
        _read_exact(target, expected_raw, "TASK8_V26_GATE_RECOVERY_TARGET_INVALID")
        if not _same_file(stage, target):
            _fail("TASK8_V26_GATE_TARGET_ALREADY_EXISTS")
        _unlink_owned_sidecar(stage, owner_token=owner_token, allow_link_count=2)
        return
    try:
        with stage.open("rb+") as handle:
            source_status = os.fstat(handle.fileno())
            source_identity = (int(source_status.st_dev), int(source_status.st_ino))
            if int(source_status.st_nlink) != 1 or handle.read() != expected_raw:
                _fail("TASK8_V26_GATE_RECOVERY_STAGE_INVALID")
            os.fsync(handle.fileno())
            _process_exit_checkpoint("after_stage_fsync_before_target_link", ordinal)
            handle.seek(0)
            if handle.read() != expected_raw:
                _fail("TASK8_V26_GATE_RECOVERY_STAGE_INVALID")
            try:
                os.link(stage, target)
            except FileExistsError as exc:
                raise Task8V26GatePublicationError("TASK8_V26_GATE_TARGET_ALREADY_EXISTS") from exc
            except OSError as exc:
                raise Task8V26GatePublicationError(
                    "TASK8_V26_GATE_PUBLICATION_INTERRUPTED"
                ) from exc
            target_status = _regular_status(target, "TASK8_V26_GATE_LINK_IDENTITY_INVALID")
            target_identity = (int(target_status.st_dev), int(target_status.st_ino))
            created_targets.append((name, target_identity))
            if target_identity != source_identity or int(target_status.st_nlink) != 2:
                _unlink_created_target(target, target_identity)
                _fail("TASK8_V26_GATE_LINK_IDENTITY_INVALID")
            handle.seek(0)
            if handle.read() != expected_raw:
                _unlink_created_target(target, target_identity)
                _fail("TASK8_V26_GATE_LINK_IDENTITY_INVALID")
    except Task8V26GatePublicationError:
        raise
    except OSError as exc:
        raise Task8V26GatePublicationError("TASK8_V26_GATE_PUBLICATION_INTERRUPTED") from exc
    _fsync_directory(target.parent)
    _process_exit_checkpoint("after_target_link_before_stage_unlink", ordinal)
    _read_exact(stage, expected_raw, "TASK8_V26_GATE_RECOVERY_STAGE_INVALID")
    _read_exact(target, expected_raw, "TASK8_V26_GATE_RECOVERY_TARGET_INVALID")
    if not _same_file(stage, target) or _file_identity(stage)[2] != 2:
        _fail("TASK8_V26_GATE_LINK_IDENTITY_INVALID")
    _unlink_owned_sidecar(stage, owner_token=owner_token, allow_link_count=2)


def _validate_final_state(
    *,
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
    authority_raw: Mapping[str, bytes],
    authority_paths: Mapping[str, Path],
) -> None:
    observed: set[tuple[int, int]] = set()
    for name in _ORDER:
        path = targets[name]
        _read_exact(path, outputs[name], "TASK8_V26_GATE_RECOVERY_TARGET_INVALID")
        device, inode, links = _file_identity(path)
        if links != 1 or (device, inode) in observed:
            _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
        observed.add((device, inode))
    for name in _AUTHORITY_ORDER:
        path = authority_paths[name]
        _read_exact(path, authority_raw[name], "TASK8_V26_GATE_INPUT_INVALID")
        device, inode, links = _file_identity(path)
        if links != 1 or (device, inode) in observed:
            _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
        observed.add((device, inode))


def _stable_code(exc: Exception) -> str:
    if isinstance(exc, Task8V26GatePublicationError):
        code = str(exc)
        if code in _LOCAL_ERROR_CODES:
            return code
    return "TASK8_V26_GATE_INTERNAL_FAILURE"


def _exact_commit_point_present(
    *,
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
) -> bool:
    if not all(_exists(targets[name]) for name in _ORDER):
        return False
    try:
        for name in _ORDER:
            _read_exact(
                targets[name],
                outputs[name],
                "TASK8_V26_GATE_RECOVERY_TARGET_INVALID",
            )
    except Task8V26GatePublicationError:
        return False
    return True


def _finish_exact_committed_attempt(
    *,
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
    authority_raw: Mapping[str, bytes],
    authority_paths: Mapping[str, Path],
    expected_stages: Mapping[int, Path],
    journal_path: Path,
    journal_raw: bytes,
    owner_token: str,
) -> None:
    for ordinal, stage in expected_stages.items():
        if not _exists(stage):
            continue
        name = _ORDER[ordinal - 1]
        target = targets[name]
        _read_exact(stage, outputs[name], "TASK8_V26_GATE_RECOVERY_STAGE_INVALID")
        _read_exact(target, outputs[name], "TASK8_V26_GATE_RECOVERY_TARGET_INVALID")
        if not _same_file(stage, target) or _file_identity(stage)[2] != 2:
            _fail("TASK8_V26_GATE_LINK_IDENTITY_INVALID")
        _unlink_owned_sidecar(stage, owner_token=owner_token, allow_link_count=2)
    _validate_final_state(
        outputs=outputs,
        targets=targets,
        authority_raw=authority_raw,
        authority_paths=authority_paths,
    )
    if _exists(journal_path):
        _unlink_lock_or_journal(
            journal_path,
            expected_raw=journal_raw,
            owner_token=owner_token,
        )


def _cleanup_fresh_attempt(
    *,
    created_targets: list[tuple[str, tuple[int, int]]],
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
    expected_stages: Mapping[int, Path],
    expected_temp: Path,
    journal_path: Path,
    journal_raw: bytes,
    lock_path: Path,
    lock_raw: bytes,
    owner_token: str,
) -> None:
    for name, identity in reversed(created_targets):
        if _exists(targets[name]):
            _unlink_created_target(targets[name], identity)
    for stage in expected_stages.values():
        if _exists(stage):
            status = _regular_status(stage, "TASK8_V26_GATE_CLEANUP_FAILED")
            if int(status.st_nlink) != 1:
                _fail("TASK8_V26_GATE_CLEANUP_FAILED")
            _unlink_owned_sidecar(stage, owner_token=owner_token)
    if _exists(expected_temp):
        links = _file_identity(expected_temp)[2]
        if links not in {1, 2}:
            _fail("TASK8_V26_GATE_CLEANUP_FAILED")
        _unlink_owned_sidecar(
            expected_temp,
            owner_token=owner_token,
            allow_link_count=links,
        )
    if _exists(journal_path):
        _unlink_lock_or_journal(
            journal_path,
            expected_raw=journal_raw,
            owner_token=owner_token,
        )
    if _exists(lock_path):
        _unlink_lock_or_journal(
            lock_path,
            expected_raw=lock_raw,
            owner_token=owner_token,
        )


def _publish_task8_v26_gate_impl(
    *,
    staged_artifact_raw: bytes,
    staged_fixture_raw: bytes,
    signed_census_receipt_raw: bytes,
    trusted_public_key_pem: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    policy_fixture_raw: bytes,
    reviewer_map_raw: bytes,
    staged_artifact_path: Path,
    staged_fixture_path: Path,
    parent_artifact_path: Path,
    policy_artifact_path: Path,
    policy_fixture_path: Path,
    reviewer_map_path: Path,
    v26_artifact_path: Path,
    v26_fixture_path: Path,
    fixture_verifier: FixtureVerifier,
    receipt_verifier: ReceiptVerifier,
) -> dict[str, Any]:
    canonical_staging = {
        os.path.normcase(str(_CANONICAL_V26_ARTIFACT.resolve(strict=False))),
        os.path.normcase(str(_CANONICAL_V26_FIXTURE.resolve(strict=False))),
    }
    supplied_staging = {
        os.path.normcase(str(Path(staged_artifact_path).resolve(strict=False))),
        os.path.normcase(str(Path(staged_fixture_path).resolve(strict=False))),
    }
    if canonical_staging & supplied_staging:
        _fail("TASK8_V26_GATE_CANONICAL_STAGING_FORBIDDEN")

    raw_by_name = {
        "parent_v21_artifact": parent_artifact_raw,
        "fdd_formula_policy_artifact": policy_artifact_raw,
        "fdd_formula_policy_fixture": policy_fixture_raw,
        "fdd_formula_policy_review_map": reviewer_map_raw,
        "full_strict_v26_artifact": staged_artifact_raw,
        "full_strict_v26_fixture": staged_fixture_raw,
    }
    candidate_paths = {
        "parent_v21_artifact": Path(parent_artifact_path),
        "fdd_formula_policy_artifact": Path(policy_artifact_path),
        "fdd_formula_policy_fixture": Path(policy_fixture_path),
        "fdd_formula_policy_review_map": Path(reviewer_map_path),
        "full_strict_v26_artifact": Path(staged_artifact_path),
        "full_strict_v26_fixture": Path(staged_fixture_path),
        "target_full_strict_v26_artifact": Path(v26_artifact_path),
        "target_full_strict_v26_fixture": Path(v26_fixture_path),
    }
    all_paths = _validate_distinct_and_unaliased(
        candidate_paths,
        require_single_link_names=set(raw_by_name),
    )
    path_by_name = {name: all_paths[name] for name in raw_by_name}
    authority_raw = {name: raw_by_name[name] for name in _AUTHORITY_ORDER}
    authority_paths = {name: path_by_name[name] for name in _AUTHORITY_ORDER}
    outputs = {
        "full_strict_v26_fixture": staged_fixture_raw,
        "full_strict_v26_artifact": staged_artifact_raw,
    }
    targets = {
        "full_strict_v26_fixture": all_paths["target_full_strict_v26_fixture"],
        "full_strict_v26_artifact": all_paths["target_full_strict_v26_artifact"],
    }
    _validate_input_bytes(raw_by_name=raw_by_name, path_by_name=path_by_name)

    try:
        fixture_result = fixture_verifier(
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=reviewer_map_raw,
            v26_artifact_raw=staged_artifact_raw,
            fixture_raw=staged_fixture_raw,
        )
    except Task8V26GatePublicationError:
        raise
    except Exception as exc:
        raise RuntimeError("fixture verifier failed") from exc
    if not isinstance(fixture_result, Mapping):
        _fail("TASK8_V26_GATE_FIXTURE_INVALID")
    identities = _normalize_verifier_identities(fixture_result)
    if identities != _EXPECTED_IDENTITIES:
        _fail("TASK8_V26_GATE_FIXTURE_INVALID")
    try:
        receipt_result = receipt_verifier(
            receipt_raw=signed_census_receipt_raw,
            identities=identities,
            trusted_public_key_pem=trusted_public_key_pem,
        )
    except Task8V26GatePublicationError:
        raise
    except Exception as exc:
        raise RuntimeError("receipt verifier failed") from exc
    if not isinstance(receipt_result, Mapping) or any(
        receipt_result.get(name) != expected for name, expected in identities.items()
    ):
        _fail("TASK8_V26_GATE_CENSUS_RECEIPT_INVALID")

    intent = _intent_value(
        outputs,
        targets,
        receipt_raw=signed_census_receipt_raw,
        identities=identities,
        authority_raw=authority_raw,
        authority_paths=authority_paths,
    )
    intent_sha256 = _sha256(_render(intent))
    journal_path = _journal_path(targets["full_strict_v26_artifact"])
    lock_path = _lock_path(targets["full_strict_v26_artifact"])

    existing_lock_raw: bytes | None = None
    existing_lock: dict[str, Any] | None = None
    if _exists(lock_path):
        existing_lock_raw, existing_lock = _load_lock(lock_path)
        owner_token: str | None = cast(str, existing_lock["owner_token"])
    else:
        owner_token = None
    expected_temp, expected_stages = _owned_namespace(
        targets=targets,
        journal_path=journal_path,
        lock_path=lock_path,
        owner_token=owner_token,
    )
    if existing_lock is None and (
        _exists(journal_path)
        or expected_temp is not None
        or any(_exists(stage) for stage in expected_stages.values())
    ):
        _fail("TASK8_V26_GATE_SIDECAR_COLLISION")

    existing_targets = [name for name in _ORDER if _exists(targets[name])]
    if existing_lock is None and existing_targets:
        exact_both = len(existing_targets) == len(_ORDER)
        if exact_both:
            try:
                target_identities: set[tuple[int, int]] = set()
                for name in _ORDER:
                    _read_exact(
                        targets[name],
                        outputs[name],
                        "TASK8_V26_GATE_TARGET_ALREADY_EXISTS",
                    )
                    device, inode, links = _file_identity(targets[name])
                    if links != 1 or (device, inode) in target_identities:
                        _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
                    target_identities.add((device, inode))
            except Task8V26GatePublicationError:
                raise
            _fail("TASK8_V26_GATE_ALREADY_PUBLISHED")
        _fail("TASK8_V26_GATE_TARGET_ALREADY_EXISTS")

    recovery = existing_lock is not None
    old_lock_raw = existing_lock_raw
    completed_recovery = False
    if recovery:
        if existing_lock is None or existing_lock_raw is None or owner_token is None:
            _fail("TASK8_V26_GATE_LOCK_INVALID")
        if existing_lock.get("intent_sha256") != intent_sha256:
            _fail("TASK8_V26_GATE_LOCK_MISMATCH")
        status = _owner_status(
            cast(int, existing_lock["owner_pid"]),
            cast(str, existing_lock["owner_start_identity"]),
        )
        if status == "LIVE":
            _fail("TASK8_V26_GATE_LOCKED")
        if status == "UNKNOWN":
            _fail("TASK8_V26_GATE_OWNER_UNKNOWN")
        journal_value = _journal_value(intent, owner_token=owner_token)
        journal_raw = _render(journal_value)
        completed_recovery = _validate_recovery_state(
            outputs=outputs,
            targets=targets,
            journal_path=journal_path,
            journal_raw=journal_raw,
            expected_temp=expected_temp,
            expected_stages=expected_stages,
        )
        current_lock_raw = _render(
            _lock_value(intent_sha256=intent_sha256, owner_token=owner_token)
        )
        _take_over_dead_lock(
            lock_path=lock_path,
            old_lock_raw=existing_lock_raw,
            new_lock_raw=current_lock_raw,
            owner_token=owner_token,
        )
    else:
        owner_token = secrets.token_hex(32)
        journal_value = _journal_value(intent, owner_token=owner_token)
        journal_raw = _render(journal_value)
        expected_temp = _journal_temp_path(journal_path, owner_token)
        expected_stages = {
            ordinal: _stage_path(targets[name], owner_token, ordinal)
            for ordinal, name in enumerate(_ORDER, start=1)
        }
        current_lock_raw = _render(
            _lock_value(intent_sha256=intent_sha256, owner_token=owner_token)
        )
        _write_exclusive(lock_path, current_lock_raw, code="TASK8_V26_GATE_LOCKED")

    _process_exit_checkpoint("after_lock_acquired_before_journal")
    if owner_token is None or expected_temp is None:
        _fail("TASK8_V26_GATE_LOCK_INVALID")
    if completed_recovery:
        _unlink_lock_or_journal(
            lock_path,
            expected_raw=current_lock_raw,
            owner_token=owner_token,
        )
        _fail("TASK8_V26_GATE_ALREADY_PUBLISHED")

    created_targets: list[tuple[str, tuple[int, int]]] = []
    primary_code: str | None = None
    committed_after_error = False
    try:
        _write_journal(journal_path, journal_value)
        for ordinal, name in enumerate(_ORDER, start=1):
            target = targets[name]
            stage = expected_stages[ordinal]
            expected_raw = outputs[name]
            if _exists(target):
                _read_exact(
                    target,
                    expected_raw,
                    "TASK8_V26_GATE_RECOVERY_TARGET_INVALID",
                )
                if _exists(stage):
                    if not _same_file(stage, target):
                        _fail("TASK8_V26_GATE_FILE_IDENTITY_ALIAS")
                    _unlink_owned_sidecar(
                        stage,
                        owner_token=owner_token,
                        allow_link_count=2,
                    )
                continue
            if not _exists(stage):
                _write_exclusive(
                    stage,
                    expected_raw,
                    code="TASK8_V26_GATE_SIDECAR_COLLISION",
                )
            else:
                _read_exact(
                    stage,
                    expected_raw,
                    "TASK8_V26_GATE_RECOVERY_STAGE_INVALID",
                )
            _publish_stage(
                stage=stage,
                target=target,
                expected_raw=expected_raw,
                owner_token=owner_token,
                ordinal=ordinal,
                created_targets=created_targets,
                name=name,
            )
        _validate_final_state(
            outputs=outputs,
            targets=targets,
            authority_raw=authority_raw,
            authority_paths=authority_paths,
        )
        _unlink_lock_or_journal(
            journal_path,
            expected_raw=journal_raw,
            owner_token=owner_token,
        )
        _process_exit_checkpoint("after_journal_removal_before_lock_cleanup")
    except Exception as exc:
        primary_code = _stable_code(exc)
        committed_after_error = _exact_commit_point_present(
            outputs=outputs,
            targets=targets,
        )
        if committed_after_error:
            try:
                _finish_exact_committed_attempt(
                    outputs=outputs,
                    targets=targets,
                    authority_raw=authority_raw,
                    authority_paths=authority_paths,
                    expected_stages=expected_stages,
                    journal_path=journal_path,
                    journal_raw=journal_raw,
                    owner_token=owner_token,
                )
                primary_code = None
            except Exception as finish_exc:
                primary_code = _stable_code(finish_exc)

    if primary_code is not None:
        if recovery:
            with suppress(Exception):
                if old_lock_raw is None:
                    _fail("TASK8_V26_GATE_LOCK_INVALID")
                _restore_old_lock(
                    lock_path=lock_path,
                    old_lock_raw=old_lock_raw,
                    current_lock_raw=current_lock_raw,
                    owner_token=owner_token,
                )
        elif not committed_after_error:
            with suppress(Exception):
                _cleanup_fresh_attempt(
                    created_targets=created_targets,
                    outputs=outputs,
                    targets=targets,
                    expected_stages=expected_stages,
                    expected_temp=expected_temp,
                    journal_path=journal_path,
                    journal_raw=journal_raw,
                    lock_path=lock_path,
                    lock_raw=current_lock_raw,
                    owner_token=owner_token,
                )
        _fail(primary_code)

    _unlink_lock_or_journal(
        lock_path,
        expected_raw=current_lock_raw,
        owner_token=owner_token,
    )
    return {
        "authority_verified": list(_AUTHORITY_ORDER),
        "created_publication_order": list(_ORDER),
        "commit_point": "full_strict_v26_artifact",
        "activation_status": "INACTIVE",
        "runtime_selectable": False,
        "gate_admitted": True,
        "receipt_raw_sha256": _sha256(signed_census_receipt_raw),
        "identity_sha256": dict(identities),
        "output_sha256": {name: _sha256(outputs[name]) for name in _ORDER},
        "authority_sha256": {name: _sha256(authority_raw[name]) for name in _AUTHORITY_ORDER},
    }


def _publish_task8_v26_gate_with_verifiers(
    *,
    staged_artifact_raw: bytes,
    staged_fixture_raw: bytes,
    signed_census_receipt_raw: bytes,
    trusted_public_key_pem: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    policy_fixture_raw: bytes,
    reviewer_map_raw: bytes,
    staged_artifact_path: Path,
    staged_fixture_path: Path,
    parent_artifact_path: Path,
    policy_artifact_path: Path,
    policy_fixture_path: Path,
    reviewer_map_path: Path,
    v26_artifact_path: Path,
    v26_fixture_path: Path,
    fixture_verifier: FixtureVerifier,
    receipt_verifier: ReceiptVerifier,
) -> dict[str, Any]:
    """Private test seam retaining the complete production composition root."""
    failure_code: str | None = None
    try:
        return _publish_task8_v26_gate_impl(
            staged_artifact_raw=staged_artifact_raw,
            staged_fixture_raw=staged_fixture_raw,
            signed_census_receipt_raw=signed_census_receipt_raw,
            trusted_public_key_pem=trusted_public_key_pem,
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            policy_fixture_raw=policy_fixture_raw,
            reviewer_map_raw=reviewer_map_raw,
            staged_artifact_path=staged_artifact_path,
            staged_fixture_path=staged_fixture_path,
            parent_artifact_path=parent_artifact_path,
            policy_artifact_path=policy_artifact_path,
            policy_fixture_path=policy_fixture_path,
            reviewer_map_path=reviewer_map_path,
            v26_artifact_path=v26_artifact_path,
            v26_fixture_path=v26_fixture_path,
            fixture_verifier=fixture_verifier,
            receipt_verifier=receipt_verifier,
        )
    except Exception as exc:
        failure_code = _stable_code(exc)
    _raise_clean(failure_code or "TASK8_V26_GATE_INTERNAL_FAILURE")


def publish_task8_v26_gate_atomically(
    *,
    staged_artifact_raw: bytes,
    staged_fixture_raw: bytes,
    signed_census_receipt_raw: bytes,
    trusted_public_key_pem: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    policy_fixture_raw: bytes,
    reviewer_map_raw: bytes,
    staged_artifact_path: Path,
    staged_fixture_path: Path,
    parent_artifact_path: Path,
    policy_artifact_path: Path,
    policy_fixture_path: Path,
    reviewer_map_path: Path,
    v26_artifact_path: Path,
    v26_fixture_path: Path,
) -> dict[str, Any]:
    """Publish the independently verified, census-bound v26 gate as INACTIVE."""
    supplied_artifact = os.path.normcase(str(Path(v26_artifact_path).resolve(strict=False)))
    supplied_fixture = os.path.normcase(str(Path(v26_fixture_path).resolve(strict=False)))
    expected_artifact = os.path.normcase(str(_CANONICAL_V26_ARTIFACT.resolve(strict=False)))
    expected_fixture = os.path.normcase(str(_CANONICAL_V26_FIXTURE.resolve(strict=False)))
    if supplied_artifact != expected_artifact or supplied_fixture != expected_fixture:
        _fail("TASK8_V26_GATE_CANONICAL_TARGET_REQUIRED")
    return _publish_task8_v26_gate_with_verifiers(
        staged_artifact_raw=staged_artifact_raw,
        staged_fixture_raw=staged_fixture_raw,
        signed_census_receipt_raw=signed_census_receipt_raw,
        trusted_public_key_pem=trusted_public_key_pem,
        parent_artifact_raw=parent_artifact_raw,
        policy_artifact_raw=policy_artifact_raw,
        policy_fixture_raw=policy_fixture_raw,
        reviewer_map_raw=reviewer_map_raw,
        staged_artifact_path=staged_artifact_path,
        staged_fixture_path=staged_fixture_path,
        parent_artifact_path=parent_artifact_path,
        policy_artifact_path=policy_artifact_path,
        policy_fixture_path=policy_fixture_path,
        reviewer_map_path=reviewer_map_path,
        v26_artifact_path=v26_artifact_path,
        v26_fixture_path=v26_fixture_path,
        fixture_verifier=_validate_v26_fixture_composition,
        receipt_verifier=_validate_signed_census_receipt,
    )
