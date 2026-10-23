"""Validate full-strict-v9 and admit a fixture only after signed PG proof.

The artifact is the sole normative byte source for contract derivation.  This
module imports only the already-admitted v7 validation machinery for unchanged
sections; v9 schema and current-guard bytes are validated independently here.
No environment value, runtime product module, database state, or credential
participates in hash derivation.
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
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any, cast


def _load_v7_helpers() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v7_contract_fixture.py")
    spec = importlib.util.spec_from_file_location(
        "_task7_v7_fixture_helpers_for_v9",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK7_V9_HELPER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V7 = _load_v7_helpers()
ContractArtifactError = _V7.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V7._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V7._COMPONENT_ORDER)

_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_SCHEMA_REGISTRY_HASH_DOMAIN = "task7-schema-registry-v1"
_CENSUS_ENVELOPE_SCHEMA = "task7-v9-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-v9-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-v9-postgres-census-signable-v1"
_CENSUS_HARNESS_IDENTITY = (
    "task7-v9-current-guard-pre-freeze-artifact-sql-on-0037-v1"
)
_CENSUS_HARNESS_SHA256 = hashlib.sha256(
    _CENSUS_HARNESS_IDENTITY.encode("utf-8")
).hexdigest()

_NATIVE_SCHEMA_VERSIONS = {
    "validated-evidence-package-native": (
        "task7-validated-evidence-package-native-v1"
    ),
    "muhasabah-native": "task7-muhasabah-native-v1",
    "layer2-challenge-native": "task7-layer2-challenge-native-v1",
    "debate-session-native": "task7-debate-session-native-v1",
}
_CURRENT_AUTHORITY_IDS = (
    "current-source-exact-one-v1",
    "current-output-exact-one-v1",
    "current-receipt-equality-v1",
    "current-input-binding-admission-v1",
    "current-append-only-lease-v1",
    "current-source-bound-v1",
    "current-output-bound-v1",
    "current-runs-contract-immutable-v1",
    "current-run-steps-contract-immutable-v1",
    "current-task5-coverage-replacement-v1",
    "current-task6-immutable-replacement-v1",
)
_CURRENT_NATIVE_TABLES = (
    "methodology_coverage_records",
    "methodology_extraction_tasks",
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
    "methodology_result_claim_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_defect_links",
    "deterministic_calculations",
    "vector_embeddings",
    "evidence_trust_findings",
    "muhasabah_records",
    "validated_evidence_packages",
    "debate_sessions",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "deliverables",
)
_MANIFEST_REFERENCE_FIELDS = (
    "tenant_id",
    "deal_id",
    "run_id",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "manifest_deliverable_id",
    "member_kind",
    "catalog_ordinal",
    "member_step_output_id",
    "member_output_object_id",
    "member_identity_sha256",
    "member_value_sha256",
    "member_content_sha256",
)
_ROOT_ASSIGNMENT_FIELDS = (
    "manifest_reference_id",
    "manifest_output_object_id",
    "run_output_object_id",
)
_EXPECTED_INVARIANTS = {
    "semantic_step_count": 28,
    "edge_rule_count": 48,
    "usage_route_count": 1756,
    "usage_extractor_count": 19,
    "provider_count": 18,
    "current_ddl_count": 34,
    "materialized_physical_table_count": 72,
    "erasure_phase_count": 66,
}


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V7._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> None:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V7._decode_artifact(raw))


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
    """Return the exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V9_UNFROZEN_CANDIDATE_REQUIRED",
            "prospective freeze requires append_frozen=false",
        )
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    return frozen_raw


def _decode_sql(row: dict[str, Any]) -> tuple[str, ...]:
    try:
        statements = tuple(
            bytes.fromhex(value).decode("utf-8")
            for value in row["body_sql_utf8_hex_tuple"]
        )
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V9_CURRENT_GUARD_INVALID",
            "current-guard SQL is not canonical UTF-8 hex",
        ) from exc
    return statements


def derive_manifest_reference_sha256(record: dict[str, Any]) -> str:
    """Derive the exact root-independent manifest member reference digest."""

    try:
        preimage = [
            "deliverable-manifest-reference-digest-v1",
            *(record[field] for field in _MANIFEST_REFERENCE_FIELDS),
        ]
    except KeyError as exc:
        raise ContractArtifactError(
            "TASK7_V9_MANIFEST_REFERENCE_INVALID",
            f"missing digest field {exc.args[0]}",
        ) from exc
    return _h(preimage)


def validate_native_payload_semantics(
    schema_id: str,
    payload: dict[str, Any],
) -> None:
    """Validate collection semantics not expressible by Draft 2020-12 alone.

    Fixed-field/type validation belongs to the hash-bound JSON Schema.  This
    companion closes semantic ordering and composite-coordinate rules.
    """

    if schema_id != "debate-session-native":
        return
    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise ContractArtifactError(
            "TASK7_V9_NATIVE_COLLECTION_INVALID",
            "debate rounds are absent",
        )
    keys: list[tuple[int, int, str]] = []
    ordinals_by_round: dict[int, list[int]] = {}
    for item in rounds:
        if not isinstance(item, dict):
            raise ContractArtifactError(
                "TASK7_V9_NATIVE_COLLECTION_INVALID",
                "debate round item is not an object",
            )
        try:
            key = (
                int(item["round_number"]),
                int(item["message_ordinal"]),
                str(item["message_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractArtifactError(
                "TASK7_V9_NATIVE_COLLECTION_INVALID",
                "debate coordinate is malformed",
            ) from exc
        keys.append(key)
        ordinals_by_round.setdefault(key[0], []).append(key[1])
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ContractArtifactError(
            "TASK7_V9_NATIVE_COLLECTION_INVALID",
            "debate composite coordinates are unordered or duplicated",
        )
    for round_number, ordinals in ordinals_by_round.items():
        if ordinals != list(range(len(ordinals))):
            raise ContractArtifactError(
                "TASK7_V9_NATIVE_COLLECTION_INVALID",
                f"round {round_number} message ordinals are not contiguous",
            )


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    """Return the reviewer-locked semantic/physical invariants."""

    return {
        "semantic_step_count": len(artifact["semantic_step_registry"]["rows"]),
        "edge_rule_count": len(artifact["edge_rule_registry"]["rows"]),
        "usage_route_count": len(artifact["usage_route_registry"]["rows"]),
        "usage_extractor_count": len(
            artifact["usage_extractor_registry"]["rows"]
        ),
        "provider_count": len(artifact["provider_registry"]["rows"]),
        "current_ddl_count": len(artifact["current_ddl_registry"]["rows"]),
        "materialized_physical_table_count": int(
            artifact["activation_assertions"][
                "materialized_physical_table_count"
            ]
        ),
        "erasure_phase_count": len(artifact["erasure_materialized"]["rows"]),
    }


def _v7_compatible_projection(artifact: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the unchanged v7 view using only v9 artifact bytes."""

    projected = copy.deepcopy(artifact)
    schema_rows = projected["schema_registry"]["rows"][:-4]
    projected["schema_registry"]["rows"] = schema_rows
    projected["schema_registry"]["expected_sha256"] = _h(
        [
            _SCHEMA_REGISTRY_HASH_DOMAIN,
            [
                [
                    row["schema_id"],
                    row["schema_version"],
                    row["expected_schema_sha256"],
                ]
                for row in schema_rows
            ],
        ]
    )

    authority_rows = projected["sql_authority_registry"]["rows"][:17]
    projected["sql_authority_registry"]["rows"] = authority_rows
    projected["sql_authority_registry"]["expected_sha256"] = _h(
        [_SQL_REGISTRY_HASH_DOMAIN, authority_rows]
    )

    metadata = projected["metadata"]
    reviewed = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    # V8 and v9 appended one reviewed literal each to the v7 design.
    del reviewed[-2:]
    reviewed[0] = "full-strict-v7-reviewed-design-literal-v1"
    reviewed[1] = ["pipeline_contract_version", "full-strict-v7"]
    reviewed_bytes = _canonical_bytes(reviewed)
    metadata["artifact_schema"] = "full-strict-v7-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v7"
    metadata["append_frozen"] = True
    metadata["reviewed_design_sha256"] = _sha256_bytes(reviewed_bytes)
    metadata["reviewed_design_utf8_hex"] = reviewed_bytes.hex()

    recipes = projected["digest_recipe_registry"]["rows"]
    recipes[5][1] = (
        '["pipeline-contract-v7","full-strict-v7",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    projected["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = (
        _canonical_bytes(recipes).hex()
    )
    return projected


def _assert_closed_schema(value: Any, path: str) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            additional = value.get("additionalProperties")
            if isinstance(additional, dict):
                if not value.get("propertyNames", {}).get("pattern"):
                    _error(
                        "TASK7_V9_NATIVE_SCHEMA_OPEN",
                        f"typed map key domain absent at {path}",
                    )
            elif additional is not False:
                _error(
                    "TASK7_V9_NATIVE_SCHEMA_OPEN",
                    f"record object is open at {path}",
                )
        for key, child in value.items():
            _assert_closed_schema(child, f"{path}.{key}")
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            _assert_closed_schema(child, f"{path}[{ordinal}]")


def _validate_schema_registry(artifact: dict[str, Any]) -> None:
    rows = artifact["schema_registry"]["rows"]
    if len(rows) != 66:
        _error("TASK7_V9_NATIVE_SCHEMA_INVALID", "schema census is not 66")
    added = rows[-4:]
    if {
        row["schema_id"]: row["schema_version"] for row in added
    } != _NATIVE_SCHEMA_VERSIONS:
        _error(
            "TASK7_V9_NATIVE_SCHEMA_INVALID",
            "the exact four native schemas are absent",
        )
    projection: list[list[str]] = []
    for row in rows:
        schema_hash = _h(
            [
                "typed-schema-definition-v1",
                row["schema_id"],
                row["schema_version"],
                row["authority_locator"],
                row["normalized_schema_snapshot"],
                row["collection_paths"],
            ]
        )
        _expect(
            schema_hash,
            row["expected_schema_sha256"],
            f"schema {row['schema_id']}",
        )
        projection.append(
            [row["schema_id"], row["schema_version"], schema_hash]
        )
    _expect(
        _h([_SCHEMA_REGISTRY_HASH_DOMAIN, projection]),
        artifact["schema_registry"]["expected_sha256"],
        "v9 schema registry",
    )
    for row in added:
        _assert_closed_schema(
            row["normalized_schema_snapshot"],
            f"schema:{row['schema_id']}",
        )


def _validate_current_authorities(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    rows = section["rows"]
    if len(rows) != 28:
        _error("TASK7_V9_CURRENT_GUARD_INVALID", "authority census is not 28")
    current = rows[17:]
    if tuple(row["authority_id"] for row in current) != _CURRENT_AUTHORITY_IDS:
        _error(
            "TASK7_V9_CURRENT_GUARD_INVALID",
            "current authority ordering differs",
        )
    targets = [
        tuple(target)
        for row in current
        for target in row["target_tuple"]
    ]
    if len(targets) != 73 or len(set(targets)) != 73:
        _error(
            "TASK7_V9_CURRENT_GUARD_INVALID",
            "current target closure is not 73 unique tuples",
        )
    for row in rows:
        payload = {
            key: value for key, value in row.items() if key != "expected_sha256"
        }
        _expect(
            _sha256_bytes(_ROW_HASH_DOMAIN + _canonical_bytes(payload)),
            row["expected_sha256"],
            f"SQL authority {row['authority_id']}",
        )
    _expect(
        _h([_SQL_REGISTRY_HASH_DOMAIN, rows]),
        section["expected_sha256"],
        "v9 SQL authority registry",
    )

    decoded = {
        row["authority_id"]: _decode_sql(row) for row in current
    }
    all_sql = "\n".join(
        statement
        for statements in decoded.values()
        for statement in statements
    )
    for forbidden in (
        "EXECUTE format(",
        "EXECUTE IMMEDIATE",
        "p_table_name",
        "p_relation_name",
    ):
        if forbidden.lower() in all_sql.lower():
            _error(
                "TASK7_V9_DYNAMIC_SQL_FORBIDDEN",
                f"current authority contains {forbidden}",
            )
    output_sql = "\n".join(decoded["current-output-exact-one-v1"])
    required_output_literals = (
        "idis_task7_current_native_hashes_v1",
        "native-identity-v1",
        "native-output-v1",
        "expected_native_identity_sha256",
        "expected_native_output_sha256",
        *_CURRENT_NATIVE_TABLES,
        *_NATIVE_SCHEMA_VERSIONS,
    )
    missing = [
        value for value in required_output_literals if value not in output_sql
    ]
    if missing:
        _error(
            "TASK7_V9_NATIVE_DISPATCH_INCOMPLETE",
            f"missing static output authority literals: {missing}",
        )
    receipt_sql = "\n".join(decoded["current-receipt-equality-v1"])
    required_receipt_literals = (
        "idis_task7_manifest_reference_digest_v1",
        "deliverable-manifest-reference-digest-v1",
        *_MANIFEST_REFERENCE_FIELDS,
    )
    missing = [
        value for value in required_receipt_literals if value not in receipt_sql
    ]
    digest_helpers = [
        statement
        for statement in decoded["current-receipt-equality-v1"]
        if statement.startswith(
            "CREATE FUNCTION public.idis_task7_manifest_reference_digest_v1("
        )
    ]
    if (
        missing
        or len(digest_helpers) != 1
        or any(field in digest_helpers[0] for field in _ROOT_ASSIGNMENT_FIELDS)
    ):
        _error(
            "TASK7_V9_MANIFEST_REFERENCE_INVALID",
            f"manifest digest authority differs; missing={missing}",
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
        metadata.get("artifact_schema")
        != "full-strict-v9-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v9"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v9 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V9_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v9 bytes",
        )
    reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
    _expect(
        _sha256_bytes(reviewed),
        metadata["reviewed_design_sha256"],
        "v9 reviewed design",
    )
    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v9","full-strict-v9",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ] or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v9 digest recipe differs")

    projection = _v7_compatible_projection(artifact)
    projection_raw = (
        json.dumps(
            projection,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    _V7._validate_shape(projection_raw, projection)
    _validate_schema_registry(artifact)
    _validate_current_authorities(artifact)
    if invariant_vector(artifact) != _EXPECTED_INVARIANTS:
        _error(
            "TASK7_V9_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )
    _V7._V6._V5._validate_parent_authority_closure(artifact)
    _V7._V6._V5._V4.validate_erasure_execution_registry(artifact)
    _V7._V6._V5._V4._validate_reviewed_row_bytes(artifact)
    _V7._V6._V5._V4._validate_scalar_tree(artifact)
    _V7._V6._validate_usage_projection(artifact)


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
    """Derive every v9 contract component from artifact bytes only."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v9-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [
                [row["ordinal"], row["name"], row["sha256"]]
                for row in sections
            ],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = cast(
        dict[str, str],
        _V7._V6._V5._derive_components(artifact, reviewed_hash),
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v9",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v9-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v9",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V9_CONTRACT_HASH": pipeline_hash,
    }


def derive_authority_inventory(artifact: dict[str, Any]) -> dict[str, Any]:
    """Bind the signed census to the exact 73-target artifact inventory."""

    rows = artifact["sql_authority_registry"]["rows"][17:]
    targets = [
        list(target)
        for row in rows
        for target in row["target_tuple"]
    ]
    return {
        "schema_version": "task7-v9-authority-inventory-v1",
        "authority_registry_sha256": artifact["sql_authority_registry"][
            "expected_sha256"
        ],
        "authority_row_count": len(rows),
        "current_guard_target_count": len(targets),
        "body_statement_count": sum(
            len(row["body_sql_utf8_hex_tuple"]) for row in rows
        ),
        "ordered_target_sha256": _h(
            ["task7-current-guard-ordered-targets-v1", targets]
        ),
        "native_schema_registry_sha256": artifact["schema_registry"][
            "expected_sha256"
        ],
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
    """Verify the signed complete v9 execution census."""

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        receipt = _decode_json_no_duplicates(receipt_raw)
        if tuple(receipt) != (
            "schema_version",
            "signer_id",
            "payload",
            "payload_sha256",
            "public_key_sha256",
            "signature_base64",
        ) or receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA:
            raise ValueError("receipt envelope shape differs")
        signer_id = receipt["signer_id"]
        payload = receipt["payload"]
        if (
            not isinstance(signer_id, str)
            or not signer_id
            or not isinstance(payload, dict)
        ):
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
            "pipeline_contract_version": "full-strict-v9",
            "candidate_artifact_sha256": _sha256_bytes(artifact_raw),
            "prospective_frozen_artifact_sha256": _sha256_bytes(
                prospective_frozen_raw
            ),
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
            raise ValueError("executable v9 census is incomplete")
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
            "TASK7_V9_POSTGRES_CENSUS_RECEIPT_INVALID",
            "signed executable PostgreSQL census did not verify",
        ) from exc
    return payload


def _render_fixture(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _stage_bytes(path: Path, content: bytes) -> Path:
    if not path.parent.is_dir():
        _error("TASK7_V9_FIXTURE_OUTPUT_PARENT_MISSING", str(path.parent))
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".task7-v9-admission",
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ContractArtifactError(
            "TASK7_V9_ATOMIC_ADMISSION_STAGE_FAILED",
            str(path),
        ) from exc
    return Path(temporary_name)


def _unlink_staging(path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)


def admit_candidate_atomically(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify the pre-freeze census, then publish artifact and fixture together."""

    artifact_path = Path(artifact_path)
    fixture_path = Path(fixture_path)
    artifact_stage: Path | None = None
    fixture_stage: Path | None = None
    rollback_stage: Path | None = None
    artifact_published = False
    if fixture_path.exists():
        _error(
            "TASK7_V9_FIXTURE_OUTPUT_ALREADY_EXISTS",
            str(fixture_path),
        )
    try:
        candidate_raw = artifact_path.read_bytes()
        candidate = _decode_artifact(candidate_raw)
        _validate_shape(candidate_raw, candidate)
        if candidate["metadata"]["append_frozen"] is not False:
            _error(
                "TASK7_V9_UNFROZEN_CANDIDATE_REQUIRED",
                str(artifact_path),
            )
        frozen_raw = derive_prospective_frozen_bytes(candidate_raw)
        payload = verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=candidate_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        )
        result = derive_contract_bytes(frozen_raw, validate=True)
        result["postgres_authority_census"] = payload["database_execution"]
        fixture_raw = _render_fixture(result).encode("utf-8")

        if artifact_path.read_bytes() != candidate_raw:
            _error(
                "TASK7_V9_CANDIDATE_CHANGED_DURING_ADMISSION",
                str(artifact_path),
            )
        artifact_stage = _stage_bytes(artifact_path, frozen_raw)
        fixture_stage = _stage_bytes(fixture_path, fixture_raw)
        rollback_stage = _stage_bytes(artifact_path, candidate_raw)

        os.replace(artifact_stage, artifact_path)
        artifact_stage = None
        artifact_published = True
        os.replace(fixture_stage, fixture_path)
        fixture_stage = None
        _unlink_staging(rollback_stage)
        rollback_stage = None
        return result
    except ContractArtifactError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        if artifact_published and rollback_stage is not None:
            try:
                os.replace(rollback_stage, artifact_path)
                rollback_stage = None
                fixture_path.unlink(missing_ok=True)
            except OSError as rollback_exc:
                raise ContractArtifactError(
                    "TASK7_V9_ATOMIC_ADMISSION_ROLLBACK_FAILED",
                    str(artifact_path),
                ) from rollback_exc
        raise ContractArtifactError(
            "TASK7_V9_ATOMIC_ADMISSION_FAILED",
            str(artifact_path),
        ) from exc
    finally:
        _unlink_staging(artifact_stage)
        _unlink_staging(fixture_stage)
        _unlink_staging(rollback_stage)


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
