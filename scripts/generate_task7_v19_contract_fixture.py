"""Validate the unfrozen full-strict-v19 materialization candidate.

Despite its historical filename convention, this bounded scaffold cannot
freeze, sign, publish, or generate a fixture. It admits only the exact
frozen-v18 to unfrozen-v19 projection reviewed for the erasure-composition
authority.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never

_SCRIPT_DIR: Final = Path(__file__).resolve().parent
_REPO_ROOT: Final = _SCRIPT_DIR.parent
_MATERIALIZER_PATH: Final = _SCRIPT_DIR / "materialize_full_strict_v19_preimage.py"
_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_MATERIALIZER_SHA512: Final = (
    "354ba2f7603376340f3b9f8774d1aeb572ffae86b9fe95f7897ba80c8b4b2e234"
    "056fc7235a6588082c13c08242d3cc0cdcb4915377bf001c16971e4a0fdc3f6"
)
_ARTIFACT_SHA256: Final = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_ARTIFACT_SCHEMA: Final = "full-strict-v19-reviewed-preimage-v1"
_CONTRACT_VERSION: Final = "full-strict-v19"
_ERASURE_ROW_HASH_DOMAIN: Final = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_ERASURE_REGISTRY_HASH_DOMAIN: Final = "full-strict-v4-erasure-execution-registry-v1"
_RUNNER_SIGNATURE: Final = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ATTEMPT_NAMESPACE: Final = "4520d8ed-e929-5486-821c-82f8901ae7fa"
_EVENT_NAMESPACE: Final = "dd76cee1-28d5-55f8-b21a-eb64bc2e4be4"
_FIXED_ATTEMPT_ID: Final = "fbb3b87f-d9d0-56b4-a983-084fb804d4ae"
_FIXED_EVENT_IDS: Final = (
    ("task7.erasure.attempted", "ATTEMPTED", "593d952b-1aff-5e4e-b673-fe02a28e8f2c"),
    ("task7.erasure.failed", "FAILED", "1407c4f8-0229-5b82-97ff-5f44eeb29e97"),
    ("task7.erasure.succeeded", "SUCCEEDED", "70e0d93e-ecb4-5099-944d-37c786d57854"),
)
_DELETE_RE: Final = re.compile(
    r"\bDELETE\s+FROM\s+public\.(?P<table>[a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)


class ContractArtifactError(RuntimeError):
    """Secret-safe v19 candidate admission failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _error(code: str, detail: str = "") -> Never:
    raise ContractArtifactError(code, detail)


def _load_pinned(path: Path, sha512: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractArtifactError("TASK7_V19_PINNED_HELPER_MISSING", path.name) from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V19_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V19_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _materializer() -> ModuleType:
    return _load_pinned(
        _MATERIALIZER_PATH,
        _MATERIALIZER_SHA512,
        "_task7_v19_materializer_helpers",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V19_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "root differs")
    return value


def _render(artifact: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(artifact),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _h(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _decode_sql(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    try:
        values = row[field]
        if not isinstance(values, list) or not values:
            _error("TASK7_V19_ERASURE_SQL_INVALID", field)
        decoded = tuple(bytes.fromhex(item).decode("utf-8") for item in values)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ContractArtifactError("TASK7_V19_ERASURE_SQL_INVALID", field) from exc
    if any(statement != " ".join(statement.split()) for statement in decoded):
        _error("TASK7_V19_ERASURE_SQL_INVALID", f"{field}:canonical")
    return decoded


def _validate_uuid_contract(artifact: Mapping[str, Any]) -> None:
    try:
        rows = artifact["uuid_namespace_registry"]["rows"]
        section_hash = artifact["uuid_namespace_registry"]["expected_sha256"]
    except (KeyError, TypeError) as exc:
        raise ContractArtifactError("TASK7_V19_UUID_REGISTRY_INVALID") from exc
    if not isinstance(rows, list):
        _error("TASK7_V19_UUID_REGISTRY_INVALID")
    by_name = {row[0]: row for row in rows}
    expected_attempt = [
        "erasure_attempt_id",
        _ATTEMPT_NAMESPACE,
        [
            "task7-erasure-attempt-v1",
            "tenant_id",
            "deal_id",
            "request_id",
            "attempt_ordinal",
            "prior_status",
            "prior_event_id_or_null",
            "prior_event_hash_or_null",
        ],
    ]
    expected_event = [
        "erasure_audit_event_id",
        _EVENT_NAMESPACE,
        [
            "task7-erasure-audit-event-v1",
            "attempt_id",
            "event_type",
            "outcome",
        ],
    ]
    if by_name.get("erasure_attempt_id") != expected_attempt:
        _error("TASK7_V19_ATTEMPT_UUID_PREIMAGE_INVALID")
    if by_name.get("erasure_audit_event_id") != expected_event:
        _error("TASK7_V19_EVENT_UUID_PREIMAGE_INVALID")
    if section_hash != _h(["task7-uuid-namespace-registry-v1", rows]):
        _error("TASK7_V19_UUID_REGISTRY_HASH_MISMATCH")
    first_name = _canonical_bytes(
        [
            "task7-erasure-attempt-v1",
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
            1,
            None,
            None,
            None,
        ]
    ).decode("utf-8")
    if str(uuid.uuid5(uuid.UUID(_ATTEMPT_NAMESPACE), first_name)) != _FIXED_ATTEMPT_ID:
        _error("TASK7_V19_ATTEMPT_UUID_FIXED_VECTOR_INVALID")
    for event_type, outcome, expected_id in _FIXED_EVENT_IDS:
        event_name = _canonical_bytes(
            [
                "task7-erasure-audit-event-v1",
                _FIXED_ATTEMPT_ID,
                event_type,
                outcome,
            ]
        ).decode("utf-8")
        if str(uuid.uuid5(uuid.UUID(_EVENT_NAMESPACE), event_name)) != expected_id:
            _error("TASK7_V19_EVENT_UUID_FIXED_VECTOR_INVALID", event_type)


def _validate_canonical_failed_event_fixed_vector_contract(
    row: Mapping[str, Any],
) -> None:
    """Bind the SQL authority to the Python canonical stored-event hash vector."""

    event = {
        "event_id": "1407c4f8-0229-5b82-97ff-5f44eeb29e97",
        "occurred_at": "2026-07-17T16:00:00.000000Z",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": "admin-1",
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": "33333333-3333-4333-8333-333333333333",
            "method": "POST",
            "path": ("/v1/erasure-requests/33333333-3333-4333-8333-333333333333/execute"),
            "status_code": 500,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": "33333333-3333-4333-8333-333333333333",
            "deal_id": "22222222-2222-4222-8222-222222222222",
        },
        "event_type": "task7.erasure.failed",
        "severity": "CRITICAL",
        "summary": "Task-7 erasure failed",
        "payload": {
            "safe": {
                "request_id": "33333333-3333-4333-8333-333333333333",
                "attempt_id": _FIXED_ATTEMPT_ID,
                "attempt_ordinal": 1,
                "prior_status": None,
                "prior_attempt_event_id": None,
                "prior_attempt_event_hash": None,
                "outcome": "FAILED",
            },
            "hashes": [f"object_target_set_sha256:{'b' * 64}"],
            "refs": ["deal_id:22222222-2222-4222-8222-222222222222"],
        },
    }
    canonical_hash = hashlib.sha256(_canonical_bytes(event)).hexdigest()
    if canonical_hash != "f51eb2892ee7a6a30dca061d18abdf530d28216fc3848d80dfe275a6bd96a766":
        _error("TASK7_V19_CANONICAL_EVENT_FIXED_VECTOR_INVALID")
    for field in ("current_body_sql_utf8_hex_tuple", "future_body_sql_utf8_hex_tuple"):
        body = _decode_sql(row, field)[0]
        required_in_order = (
            "'{\"actor\":{'",
            "'\"actor_id\":'",
            "'\"actor_type\":'",
            "'\"ip\":'",
            "'\"roles\":'",
            "'\"user_agent\":'",
            "'},\"event_id\":'",
            "',\"event_type\":'",
            "',\"occurred_at\":'",
            '\',"payload":{"hashes":\'',
            "',\"refs\":'",
            '\',"safe":{"attempt_id":\'',
            "',\"attempt_ordinal\":'",
            "',\"outcome\":'",
            "',\"prior_attempt_event_hash\":'",
            "',\"prior_attempt_event_id\":'",
            "',\"prior_status\":'",
            "',\"request_id\":'",
            '\'}},"request":{"method":\'',
            "',\"path\":'",
            "',\"request_id\":'",
            "',\"status_code\":'",
            '\'},"resource":{"deal_id":\'',
            "',\"resource_id\":'",
            "',\"resource_type\":'",
            "'},\"severity\":'",
            "',\"summary\":'",
            "',\"tenant_id\":'",
        )
        cursor = 0
        for token in required_in_order:
            cursor = body.find(token, cursor)
            if cursor < 0:
                _error("TASK7_V19_CANONICAL_EVENT_SQL_INVALID", field)
            cursor += len(token)
        if "TASK7_ERASURE_PRIOR_EVENT_HASH_MISMATCH" not in body:
            _error("TASK7_V19_CANONICAL_EVENT_SQL_INVALID", field)
        if "canonical_prior_event::text" in body or "prior_event::text" in body:
            _error("TASK7_V19_CANONICAL_EVENT_SQL_INVALID", f"{field}:jsonb-text")


def validate_erasure_execution_registry(artifact: Mapping[str, Any]) -> None:
    """Validate the exact V2 runner, ACL, hashes, and inline authority."""

    try:
        schemas = dict(artifact["tuple_schema_registry"]["rows"])
        section = artifact["erasure_execution_registry"]
        rows = section["rows"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractArtifactError("TASK7_V19_ERASURE_REGISTRY_INVALID") from exc
    materializer = _materializer()
    expected_v2 = tuple(materializer._V2_FIELDS)
    expected_lineage = tuple(materializer._LINEAGE_FIELDS)
    if tuple(schemas.get("ErasureExecutionSpecV2", ())) != expected_v2:
        _error("TASK7_V19_ERASURE_V2_SCHEMA_INVALID")
    if tuple(schemas.get("ErasureAuditLineageSpecV1", ())) != expected_lineage:
        _error("TASK7_V19_ERASURE_LINEAGE_SCHEMA_INVALID")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        _error("TASK7_V19_ERASURE_REGISTRY_INVALID")
    row = rows[0]
    if tuple(row) != expected_v2 or row.get("execution_version") != "2":
        _error("TASK7_V19_ERASURE_REGISTRY_INVALID", "V2 row shape")
    if row.get("runner_function_signature") != _RUNNER_SIGNATURE:
        _error("TASK7_V19_ERASURE_REGISTRY_INVALID", "signature")
    if tuple(row.get("declared_read_relation_tuple", ())) != tuple(materializer._READ_CLOSURE):
        _error("TASK7_V19_ERASURE_READ_CLOSURE_INVALID")
    if _canonical_bytes(row.get("audit_lineage_contract_tuple")) != _canonical_bytes(
        materializer._AUDIT_LINEAGE_CONTRACT
    ):
        _error("TASK7_V19_ERASURE_LINEAGE_CONTRACT_INVALID")
    if _canonical_bytes(row.get("object_reconciliation_contract_tuple")) != _canonical_bytes(
        materializer._OBJECT_RECONCILIATION_CONTRACT
    ):
        _error("TASK7_V19_ERASURE_OBJECT_CONTRACT_INVALID")
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    expected_row_hash = hashlib.sha256(
        _ERASURE_ROW_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()
    if row.get("expected_sha256") != expected_row_hash:
        _error("TASK7_V19_ERASURE_ROW_HASH_MISMATCH")
    if section.get("expected_sha256") != _h([_ERASURE_REGISTRY_HASH_DOMAIN, rows]):
        _error("TASK7_V19_ERASURE_REGISTRY_HASH_MISMATCH")
    for field, relations in (
        ("current_body_sql_utf8_hex_tuple", row["current_relation_tuple"]),
        ("future_body_sql_utf8_hex_tuple", row["complete_relation_tuple"]),
    ):
        body = _decode_sql(row, field)[0]
        delete_order = tuple(match.group("table") for match in _DELETE_RE.finditer(body))
        if delete_order != tuple(relations):
            _error("TASK7_V19_ERASURE_DELETE_ORDER_INVALID", field)
        compact = "".join(body.split())
        if compact.lower().count("frompublic.audit_events") != 1:
            _error("TASK7_V19_ERASURE_AUDIT_READ_INVALID", field)
        for required in (
            _ATTEMPT_NAMESPACE,
            _EVENT_NAMESPACE,
            "TASK7_ERASURE_ATTEMPT_PREIMAGE_MISMATCH",
            "TASK7_ERASURE_EVENT_PREIMAGE_MISMATCH",
            "TASK7_ERASURE_PRIOR_EVENT_HASH_MISMATCH",
            "object_rows_reconciled",
            "status='EXECUTED'",
        ):
            if required not in body and required not in compact:
                _error("TASK7_V19_ERASURE_BODY_INCOMPLETE", f"{field}:{required}")
        for forbidden in (
            "public.idis_task7_sha1_v1",
            "public.idis_task7_uuidv5_v1",
            "objects_deleted",
        ):
            if forbidden in body:
                _error("TASK7_V19_ERASURE_BODY_FORBIDDEN", f"{field}:{forbidden}")
    _validate_canonical_failed_event_fixed_vector_contract(row)


def validate_candidate_artifact(
    artifact: Mapping[str, Any],
    *,
    raw: bytes | None = None,
) -> dict[str, Any]:
    """Prove exact frozen-v18 to unfrozen-v19 materialization."""

    candidate = copy.deepcopy(dict(artifact))
    metadata = candidate.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "metadata differs")
    if raw is not None and _render(candidate) != raw:
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "rendering differs")
    materializer = _materializer()
    try:
        v18 = materializer._load_pinned_v18()
        rebuilt = materializer.build_v19(v18)
        projected = materializer.project_v19_to_v18(candidate)
        materializer._validate_exact_diff(v18, candidate)
        materializer.validate_uuidv5_fixed_vectors(candidate)
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V19_EXACT_DIFF_INVALID",
            type(exc).__name__,
        ) from exc
    if materializer._render(rebuilt) != _render(candidate):
        _error("TASK7_V19_EXACT_DIFF_INVALID", "candidate differs")
    if materializer._render(projected) != materializer._render(v18):
        _error("TASK7_V19_EXACT_DIFF_INVALID", "v18 projection differs")
    _validate_uuid_contract(candidate)
    validate_erasure_execution_registry(candidate)
    return candidate


def reject_fixture_generation() -> Never:
    """Fail closed until reviewed v19 PostgreSQL proofs authorize freezing."""

    _error("TASK7_V19_APPEND_FREEZE_NOT_AUTHORIZED")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=_ARTIFACT_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(None if argv is None else list(argv))
    try:
        raw = args.artifact.read_bytes()
        if (
            args.artifact.resolve() == _ARTIFACT_PATH.resolve()
            and hashlib.sha256(raw).hexdigest() != _ARTIFACT_SHA256
        ):
            _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
        validate_candidate_artifact(_decode(raw), raw=raw)
    except (ContractArtifactError, OSError) as exc:
        code = (
            exc.code
            if isinstance(exc, ContractArtifactError)
            else "TASK7_V19_CONTRACT_ARTIFACT_INVALID"
        )
        print(json.dumps({"reason_code": code, "result": "FAILED"}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "pipeline_contract_version": _CONTRACT_VERSION,
                "result": "VALIDATED_UNFROZEN_NOT_ADMITTED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContractArtifactError",
    "main",
    "reject_fixture_generation",
    "validate_candidate_artifact",
    "validate_erasure_execution_registry",
]
