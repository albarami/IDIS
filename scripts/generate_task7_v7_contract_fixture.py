"""Derive full-strict-v7 and admit its fixture only after PostgreSQL census."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast


def _load_v6_helpers() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v6_contract_fixture.py")
    spec = importlib.util.spec_from_file_location(
        "_task7_v6_fixture_helpers",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK7_V7_HELPER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V6 = _load_v6_helpers()
ContractArtifactError = _V6.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V6._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V6._COMPONENT_ORDER)

_CENSUS_ENVELOPE_SCHEMA = "task7-sql-authority-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-sql-authority-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-sql-authority-postgres-census-signable-v1"
_HASH_RE_LENGTH = 64


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V6._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        raise ContractArtifactError(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    value = _V6._V5._V4._decode(raw)
    if not isinstance(value, dict):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v7 artifact root is not an object",
        )
    return cast(dict[str, Any], value)


def _validate_shape(raw: bytes, artifact: dict[str, Any]) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "top-level section order mismatch",
        )
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v7-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v7"
        or metadata.get("append_frozen") is not True
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v7 metadata mismatch",
        )
    try:
        reviewed_design = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "reviewed-design bytes are invalid",
        ) from exc
    _expect(
        _sha256_bytes(reviewed_design),
        metadata["reviewed_design_sha256"],
        "reviewed_design_sha256",
    )

    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v7","full-strict-v7",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ] or recipes[-1] != [
        "baseline_parent_authority_registry_hash",
        "lowercase_hex(SHA256(CJ(ORDERED[baseline_parent_authority_rows])))",
    ]:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v7 digest recipe mismatch",
        )
    if bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "digest reviewed bytes mismatch",
        )

    counts = {
        "semantic_step_registry": 28,
        "edge_rule_registry": 48,
        "usage_route_registry": 1_756,
        "usage_extractor_registry": 19,
        "rights_index_policy_registry": 18,
        "current_ddl_registry": 34,
        "future_native_authority_registry": 18,
        "future_reference_authority_registry": 15,
        "future_child_authority_registry": 5,
        "sql_authority_registry": 17,
        "erasure_execution_registry": 1,
    }
    for section, expected in counts.items():
        if len(artifact[section]["rows"]) != expected:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"{section} count mismatch",
            )
    if (
        len(artifact["erasure_baseline"]["rows"]) != 59
        or len(artifact["erasure_materialized"]["rows"]) != 66
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "erasure census mismatch",
        )
    _V6._V5._validate_baseline_registry(artifact)
    _V6._V5._validate_parent_authority_closure(artifact)
    _V6._V5._V4.validate_sql_authority_registry(artifact)
    _V6._V5._V4.validate_erasure_execution_registry(artifact)
    _V6._V5._V4._validate_reviewed_row_bytes(artifact)
    _V6._V5._V4._validate_scalar_tree(artifact)
    _V6._validate_usage_projection(artifact)
    derive_authority_inventory(artifact)


def _section_hashes(
    artifact: dict[str, Any],
) -> list[dict[str, Any]]:
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
    """Derive v7 exclusively from append-frozen artifact bytes."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    section_hashes = _section_hashes(artifact)
    reviewed_preimage_artifact_hash = _h(
        [
            "full-strict-v7-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in section_hashes],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = cast(
        dict[str, str],
        _V6._V5._derive_components(
            artifact,
            reviewed_preimage_artifact_hash,
        ),
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v7",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_preimage_artifact_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v7-contract-hash-fixture-v1",
        "artifact_sha256": artifact_sha256,
        "section_hashes": section_hashes,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V7_CONTRACT_HASH": pipeline_hash,
    }


def derive_producer_selector_authority(raw: bytes) -> dict[str, Any]:
    """Project normalized producer selectors for deterministic source generation."""

    artifact = _decode_artifact(raw)
    _validate_shape(raw, artifact)
    section = artifact["producer_selector_registry"]
    rows = section["rows"]
    return {
        "schema_version": "task7-producer-selector-authority-v1",
        "row_count": len(rows),
        "rows": rows,
        "expected_sha256": section["expected_sha256"],
    }


def _statement_kind(
    *,
    authority_kind: str,
    statement: str,
) -> str:
    if authority_kind == "CHECK":
        return "CHECK"
    normalized = statement.lstrip().upper()
    if normalized.startswith(("CREATE OR REPLACE FUNCTION ", "CREATE FUNCTION ")):
        return "CREATE_FUNCTION"
    if normalized.startswith(("CREATE CONSTRAINT TRIGGER ", "CREATE TRIGGER ")):
        return "CREATE_TRIGGER"
    if normalized.startswith("REVOKE "):
        return "REVOKE"
    if normalized.startswith("ALTER FUNCTION "):
        return "ALTER_FUNCTION"
    raise ContractArtifactError(
        "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
        "unclassified SQL authority statement",
    )


def derive_authority_inventory(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Derive the closed database-execution census from literal registry bytes."""

    section = artifact.get("sql_authority_registry")
    if not isinstance(section, dict):
        raise ContractArtifactError(
            "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
            "SQL authority registry is absent",
        )
    rows = section.get("rows")
    if not isinstance(rows, list) or len(rows) != 17:
        raise ContractArtifactError(
            "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
            "SQL authority row count is not seventeen",
        )
    totals = {
        "CHECK": 0,
        "CREATE_FUNCTION": 0,
        "CREATE_TRIGGER": 0,
        "REVOKE": 0,
        "ALTER_FUNCTION": 0,
    }
    check_groups = 0
    function_trigger_groups = 0
    authorities: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or row.get("authority_ordinal") != ordinal
            or not isinstance(row.get("authority_id"), str)
            or not isinstance(row.get("authority_kind"), str)
        ):
            raise ContractArtifactError(
                "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
                f"malformed authority row {ordinal}",
            )
        encoded = row.get("body_sql_utf8_hex_tuple")
        if not isinstance(encoded, list) or not encoded:
            raise ContractArtifactError(
                "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
                f"authority row {ordinal} has no SQL bytes",
            )
        kinds: list[str] = []
        for statement_hex in encoded:
            try:
                statement = bytes.fromhex(statement_hex).decode("utf-8")
            except (TypeError, ValueError, UnicodeError) as exc:
                raise ContractArtifactError(
                    "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
                    f"authority row {ordinal} has invalid SQL hex",
                ) from exc
            kind = _statement_kind(
                authority_kind=row["authority_kind"],
                statement=statement,
            )
            totals[kind] += 1
            kinds.append(kind)
        if row["authority_kind"] == "CHECK":
            check_groups += 1
            if set(kinds) != {"CHECK"}:
                raise ContractArtifactError(
                    "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
                    f"CHECK row {ordinal} has non-CHECK bytes",
                )
        else:
            function_trigger_groups += 1
            if kinds.count("CREATE_FUNCTION") != 1 or (
                kinds.count("CREATE_TRIGGER") != len(row["target_tuple"])
            ):
                raise ContractArtifactError(
                    "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
                    f"trigger row {ordinal} has incomplete executable bytes",
                )
        authorities.append(
            {
                "authority_ordinal": ordinal,
                "authority_id": row["authority_id"],
                "authority_kind": row["authority_kind"],
                "body_statement_count": len(encoded),
                "body_tuple_sha256": _h(
                    [
                        "task7-sql-authority-body-tuple-v1",
                        ordinal,
                        row["authority_id"],
                        encoded,
                    ]
                ),
                "statement_kind_tuple": kinds,
            }
        )
    inventory = {
        "schema_version": "task7-sql-authority-inventory-v1",
        "authority_registry_sha256": section["expected_sha256"],
        "authority_row_count": len(rows),
        "check_authority_group_count": check_groups,
        "check_expression_count": totals["CHECK"],
        "function_trigger_group_count": function_trigger_groups,
        "create_function_count": totals["CREATE_FUNCTION"],
        "create_trigger_count": totals["CREATE_TRIGGER"],
        "revoke_count": totals["REVOKE"],
        "alter_function_count": totals["ALTER_FUNCTION"],
        "body_statement_count": sum(row["body_statement_count"] for row in authorities),
        "authorities": authorities,
    }
    expected_counts = (17, 3, 5, 14, 14, 81, 14, 1, 115)
    actual_counts = (
        inventory["authority_row_count"],
        inventory["check_authority_group_count"],
        inventory["check_expression_count"],
        inventory["function_trigger_group_count"],
        inventory["create_function_count"],
        inventory["create_trigger_count"],
        inventory["revoke_count"],
        inventory["alter_function_count"],
        inventory["body_statement_count"],
    )
    if actual_counts != expected_counts:
        raise ContractArtifactError(
            "TASK7_V7_POSTGRES_CENSUS_INVENTORY_INVALID",
            f"authority census {actual_counts!r} != {expected_counts!r}",
        )
    return inventory


def _decode_json_no_duplicates(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate member {key!r}")
            value[key] = child
        return value

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(value, dict):
        raise ValueError("receipt root is not an object")
    return value


def verify_postgres_census_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify the separate signed census receipt before fixture admission."""

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
        ):
            raise ValueError("receipt field set mismatch")
        if receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA:
            raise ValueError("receipt schema mismatch")
        signer_id = receipt["signer_id"]
        if not isinstance(signer_id, str) or not signer_id:
            raise ValueError("receipt signer is invalid")
        payload = receipt["payload"]
        if not isinstance(payload, dict):
            raise ValueError("receipt payload is invalid")
        payload_sha256 = _sha256_bytes(_canonical_bytes(payload))
        if receipt["payload_sha256"] != payload_sha256:
            raise ValueError("receipt payload hash mismatch")
        public_key_sha256 = _sha256_bytes(trusted_public_key_raw)
        if receipt["public_key_sha256"] != public_key_sha256:
            raise ValueError("receipt trust-root hash mismatch")
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("receipt trust root is not Ed25519")
        signature = base64.b64decode(
            receipt["signature_base64"],
            validate=True,
        )
        signable = [
            _CENSUS_SIGNING_DOMAIN,
            signer_id,
            payload_sha256,
            public_key_sha256,
            payload,
        ]
        public_key.verify(signature, _canonical_bytes(signable))

        artifact = _decode_artifact(artifact_raw)
        _validate_shape(artifact_raw, artifact)
        inventory = derive_authority_inventory(artifact)
        expected_payload = {
            "schema_version": _CENSUS_PAYLOAD_SCHEMA,
            "result": "PASS",
            "artifact_schema": artifact["metadata"]["artifact_schema"],
            "pipeline_contract_version": (artifact["metadata"]["pipeline_contract_version"]),
            "artifact_sha256": _sha256_bytes(artifact_raw),
            "authority_inventory": inventory,
            "database_execution": payload.get("database_execution"),
        }
        if payload != expected_payload:
            raise ValueError("receipt payload is not artifact-exact")
        execution = payload["database_execution"]
        if not isinstance(execution, dict) or tuple(execution) != (
            "database_product",
            "server_version_num",
            "disposable_database_count",
            "executed_authority_group_count",
            "executed_statement_count",
            "database_rejection_count",
        ):
            raise ValueError("database execution receipt shape mismatch")
        if (
            execution["database_product"] != "PostgreSQL"
            or not isinstance(execution["server_version_num"], int)
            or execution["server_version_num"] < 160000
            or execution["disposable_database_count"] != 17
            or execution["executed_authority_group_count"] != 17
            or execution["executed_statement_count"] != 115
            or execution["database_rejection_count"] != 0
        ):
            raise ValueError("database execution census is incomplete")
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
            "TASK7_V7_POSTGRES_CENSUS_RECEIPT_INVALID",
            "signed PostgreSQL census receipt did not verify",
        ) from exc
    return payload


def _render_fixture(value: dict[str, Any]) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _write_exclusive(path: Path, rendered: str) -> None:
    if not path.parent.is_dir():
        raise ContractArtifactError(
            "TASK7_V7_FIXTURE_OUTPUT_PARENT_MISSING",
            str(path.parent),
        )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ContractArtifactError(
            "TASK7_V7_FIXTURE_OUTPUT_ALREADY_EXISTS",
            str(path),
        ) from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--stdout", action="store_true")
    parser.add_argument("--authority-census-receipt", type=Path)
    parser.add_argument("--trusted-public-key", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        artifact_raw = args.artifact.read_bytes()
        result = derive_contract_bytes(artifact_raw, validate=True)
        if args.authority_census_receipt is None or args.trusted_public_key is None:
            raise ContractArtifactError(
                "TASK7_V7_POSTGRES_CENSUS_RECEIPT_REQUIRED",
                "fixture admission requires the signed PostgreSQL census",
            )
        verify_postgres_census_receipt(
            receipt_raw=args.authority_census_receipt.read_bytes(),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=args.trusted_public_key.read_bytes(),
        )
        rendered = _render_fixture(result)
        if args.stdout:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
        else:
            if args.output is None:
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID",
                    "fixture output path is absent",
                )
            _write_exclusive(args.output, rendered)
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
