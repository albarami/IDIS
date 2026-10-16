"""Validate and derive the unfrozen full-strict-v17 contract candidate.

This module deliberately performs no publication on import or ordinary CLI
validation.  Append-freezing and fixture bytes become authorizable only after
the exact v17 PostgreSQL census receipt verifies against the pinned trust root.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never, cast

_SCRIPT_DIR: Final = Path(__file__).resolve().parent
_REPO_ROOT: Final = _SCRIPT_DIR.parent
_V16_GENERATOR_PATH: Final = _SCRIPT_DIR / "generate_task7_v16_contract_fixture.py"
_V16_GENERATOR_SHA256: Final = "6208476548bdcb368e4b76598f4bdc940cc197e598336fe210af14efae069e9f"
_V17_MATERIALIZER_PATH: Final = _SCRIPT_DIR / "materialize_full_strict_v17_preimage.py"
_V17_RUNNER_PATH: Final = _SCRIPT_DIR / "run_task7_v17_current_guard_census.py"
_V17_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V16_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V17_ARTIFACT_SCHEMA: Final = "full-strict-v17-reviewed-preimage-v1"
_V17_CONTRACT_VERSION: Final = "full-strict-v17"
_EXPECTED_V17_RUNNER_SHA256: Final = (
    "9f3585fb6b10a35cf1c73bd3d7f08c6c3aacb60c83d5b4d8e9e32ee9c0ed539c"
)
_EXPECTED_CENSUS_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256: Final = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)


class ContractArtifactError(RuntimeError):
    """Secret-safe v17 contract admission failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _error(code: str, detail: str = "") -> Never:
    raise ContractArtifactError(code, detail)


def _load_pinned(path: Path, sha256: str, name: str) -> ModuleType:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        _error("TASK7_V17_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V17_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _load_file(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _error("TASK7_V17_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _v16() -> ModuleType:
    return _load_pinned(
        _V16_GENERATOR_PATH,
        _V16_GENERATOR_SHA256,
        "_task7_v16_fixture_helpers_for_v17",
    )


def _materializer() -> ModuleType:
    if not _V17_MATERIALIZER_PATH.is_file():
        _error(
            "TASK7_V17_MATERIALIZER_REQUIRED",
            _V17_MATERIALIZER_PATH.name,
        )
    runner = _runner()
    manifest = runner.materialization_source_manifest()
    relative = _V17_MATERIALIZER_PATH.relative_to(_REPO_ROOT).as_posix()
    matches = tuple(row for row in manifest if row[0] == relative)
    if len(matches) != 1:
        _error("TASK7_V17_PINNED_HELPER_DRIFT", _V17_MATERIALIZER_PATH.name)
    return _load_pinned(
        _V17_MATERIALIZER_PATH,
        str(matches[0][2]),
        "_task7_v17_materializer_helpers",
    )


def _runner() -> ModuleType:
    return _load_pinned(
        _V17_RUNNER_PATH,
        _EXPECTED_V17_RUNNER_SHA256,
        "_task7_v17_census_verifier",
    )


def _validate_runner_identity(result: Any) -> None:
    if (
        not isinstance(result, Mapping)
        or result.get("v17_adapter_source_sha256") != _EXPECTED_V17_RUNNER_SHA256
    ):
        _error("TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID")


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V17_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V17_CONTRACT_ARTIFACT_INVALID", "root differs")
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


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _h(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def validate_candidate_artifact(
    artifact: Mapping[str, Any],
    *,
    raw: bytes | None = None,
) -> dict[str, Any]:
    """Prove exact v16→v17 materialization and the narrow census delta."""

    candidate = copy.deepcopy(dict(artifact))
    metadata = candidate.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V17_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V17_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V17_CONTRACT_ARTIFACT_INVALID", "metadata differs")
    if raw is not None and _render(candidate) != raw:
        _error("TASK7_V17_CONTRACT_ARTIFACT_INVALID", "rendering differs")

    materializer = _materializer()
    v16_raw = _V16_ARTIFACT_PATH.read_bytes()
    v16 = _decode(v16_raw)
    try:
        rebuilt = materializer.build_v17(v16)
        projected = materializer.project_v17_to_v16(candidate)
        materializer._validate_exact_diff(v16, candidate)
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V17_EXACT_DIFF_INVALID",
            type(exc).__name__,
        ) from exc
    rebuilt["metadata"]["append_frozen"] = False
    if _render(rebuilt) != _render(candidate) or _render(projected) != v16_raw:
        _error("TASK7_V17_EXACT_DIFF_INVALID", "projection differs")

    runner = _runner()
    expectations = runner.execution_expectations(candidate)
    if expectations != {
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 193,
        "application_case_execution_count": 219,
        "constructor_schema_proof_count": 4,
    }:
        _error("TASK7_V17_CENSUS_CONTRACT_INVALID", repr(expectations))
    return candidate


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    candidate = validate_candidate_artifact(_decode(candidate_raw), raw=candidate_raw)
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    return _render(frozen)


def _section_hashes(
    artifact: Mapping[str, Any],
    section_order: Sequence[str],
) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": hashlib.sha256(_canonical_bytes(artifact[name])).hexdigest(),
        }
        for ordinal, name in enumerate(section_order)
    ]


def derive_contract_bytes(frozen_raw: bytes) -> dict[str, Any]:
    """Derive fixture content from validated append-frozen v17 bytes."""

    artifact = _decode(frozen_raw)
    if artifact.get("metadata", {}).get("append_frozen") is not True:
        _error("TASK7_V17_APPEND_FREEZE_REQUIRED")
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    validate_candidate_artifact(candidate)

    v16 = _v16()
    section_order = cast(tuple[str, ...], v16._SECTION_ORDER)
    component_order = cast(tuple[str, ...], v16._COMPONENT_ORDER)
    if tuple(artifact) != section_order:
        _error("TASK7_V17_CONTRACT_ARTIFACT_INVALID", "section order differs")
    sections = _section_hashes(artifact, section_order)
    artifact_sha256 = hashlib.sha256(frozen_raw).hexdigest()
    reviewed_hash = _h(
        [
            "full-strict-v17-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )

    materializer = _materializer()
    projected_v16 = materializer.project_v17_to_v16(artifact)
    v15 = v16._MATERIALIZER.project_v16_to_v15(projected_v16)
    v14 = v16._MATERIALIZER._V15.project_v15_to_v14(v15)
    v13 = v16._MATERIALIZER._V15._V14.project_v14_to_v13(v14)
    v12 = v16._MATERIALIZER._V15._V14._V13.project_v13_to_v12(v13)
    v11 = v16._V15._V14._V13._V12._MATERIALIZER.project_v12_to_v11(v12)
    components = cast(
        dict[str, str],
        v16._V15._V14._V13._V12._V11._V10._V9._V7._V6._V5._derive_components(
            v11,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v17",
            _V17_CONTRACT_VERSION,
            reviewed_hash,
            [components[name] for name in component_order],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v17-contract-hash-fixture-v1",
        "pipeline_contract_version": _V17_CONTRACT_VERSION,
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V17_CONTRACT_HASH": pipeline_hash,
    }


def authorize_fixture_publication(
    *,
    candidate_artifact: Mapping[str, Any],
    verified_census_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return publication bytes only after a verified, artifact-bound census."""

    if verified_census_payload is None:
        _error("TASK7_V17_POSTGRES_CENSUS_RECEIPT_REQUIRED")
    candidate = validate_candidate_artifact(candidate_artifact)
    candidate_raw = _render(candidate)
    prospective = derive_prospective_frozen_bytes(candidate_raw)
    result = verified_census_payload.get("census_result")
    if (
        verified_census_payload.get("schema_version") != "task7-v17-postgres-census-v1"
        or verified_census_payload.get("result") != "PASS"
        or verified_census_payload.get("artifact_schema") != _V17_ARTIFACT_SCHEMA
        or verified_census_payload.get("pipeline_contract_version") != _V17_CONTRACT_VERSION
        or verified_census_payload.get("candidate_artifact_sha256")
        != hashlib.sha256(candidate_raw).hexdigest()
        or verified_census_payload.get("prospective_frozen_artifact_sha256")
        != hashlib.sha256(prospective).hexdigest()
        or not isinstance(result, Mapping)
    ):
        _error("TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID")
    runner = _runner()
    _validate_runner_identity(result)
    runner.validate_execution_contract(result=result, artifact=candidate)
    database_execution = verified_census_payload.get("database_execution")
    expected_database_execution = runner._database_execution(result, candidate)
    if (
        not isinstance(database_execution, Mapping)
        or tuple(database_execution) != tuple(expected_database_execution)
        or dict(database_execution) != expected_database_execution
    ):
        _error("TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID")
    fixture = derive_contract_bytes(prospective)
    fixture["postgres_authority_census"] = copy.deepcopy(dict(database_execution))
    return {
        "frozen_artifact_bytes": prospective,
        "fixture": fixture,
    }


def verify_postgres_census_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    try:
        receipt = _decode(receipt_raw)
        if receipt.get("signer_id") != _EXPECTED_CENSUS_SIGNER_ID or not secrets.compare_digest(
            hashlib.sha256(trusted_public_key_raw).hexdigest(),
            _EXPECTED_CENSUS_PUBLIC_KEY_SHA256,
        ):
            _error("TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID")
        payload = cast(
            dict[str, Any],
            _runner().verify_signed_receipt(
                receipt_raw=receipt_raw,
                artifact_raw=artifact_raw,
                trusted_public_key_raw=trusted_public_key_raw,
            ),
        )
        _validate_runner_identity(payload.get("census_result"))
        return payload
    except Exception as exc:
        raise ContractArtifactError("TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=_V17_ARTIFACT_PATH)
    parser.add_argument("--census-receipt", type=Path)
    parser.add_argument("--trusted-public-key", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(None if argv is None else list(argv))
    try:
        artifact_raw = args.artifact.read_bytes()
        artifact = validate_candidate_artifact(_decode(artifact_raw), raw=artifact_raw)
        if args.census_receipt is None or args.trusted_public_key is None:
            _error("TASK7_V17_POSTGRES_CENSUS_RECEIPT_REQUIRED")
        payload = verify_postgres_census_receipt(
            receipt_raw=args.census_receipt.read_bytes(),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=args.trusted_public_key.read_bytes(),
        )
        authorization = authorize_fixture_publication(
            candidate_artifact=artifact,
            verified_census_payload=payload,
        )
    except (ContractArtifactError, OSError) as exc:
        code = (
            exc.code
            if isinstance(exc, ContractArtifactError)
            else ("TASK7_V17_CONTRACT_ARTIFACT_INVALID")
        )
        print(json.dumps({"result": "FAILED", "reason_code": code}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "result": "AUTHORIZED_NOT_PUBLISHED",
                "prospective_frozen_artifact_sha256": hashlib.sha256(
                    authorization["frozen_artifact_bytes"]
                ).hexdigest(),
                "pipeline_contract_hash": authorization["fixture"]["FULL_STRICT_V17_CONTRACT_HASH"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContractArtifactError",
    "authorize_fixture_publication",
    "derive_contract_bytes",
    "derive_prospective_frozen_bytes",
    "main",
    "validate_candidate_artifact",
    "verify_postgres_census_receipt",
]
