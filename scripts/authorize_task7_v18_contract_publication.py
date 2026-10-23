"""Authorize atomic publication of full-strict-v18 from its signed census.

The executable census source set is already closed and signed by the v18
runner.  This separate verifier avoids a runner/generator hash cycle: it pins
the immutable census runner and admission generator, verifies the signed
receipt against the governance trust root, and only then derives prospective
append-frozen artifact and fixture bytes.  It never writes either output.
"""

from __future__ import annotations

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
_V18_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_RUNNER_PATH: Final = _SCRIPT_DIR / "run_task7_v18_current_guard_census.py"
_V18_ADMISSION_GENERATOR_PATH: Final = _SCRIPT_DIR / "generate_task7_v18_contract_fixture.py"
_V17_GENERATOR_PATH: Final = _SCRIPT_DIR / "generate_task7_v17_contract_fixture.py"

_V18_ARTIFACT_SHA256: Final = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_EXPECTED_V18_RUNNER_SHA256: Final = (
    "10988bb9cbbc368b496c7d9e55177b5d7390219d8d38dc70e40e10583a6cf006"
)
_V18_ADMISSION_GENERATOR_SHA256: Final = (
    "4a9aba57523ce2baaeb8341fcd131789d4842cae17cd5471f22e858c496aa3f3"
)
_V17_GENERATOR_SHA256: Final = "f5df862fd05ad57ccaae4f829add8e90e5a810f0eaa992721e87c2b1317ebace"
_EXPECTED_CENSUS_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256: Final = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_V18_ARTIFACT_SCHEMA: Final = "full-strict-v18-reviewed-preimage-v1"
_V18_CONTRACT_VERSION: Final = "full-strict-v18"
_V18_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v18-postgres-census-v1"


class ContractPublicationError(RuntimeError):
    """Secret-safe v18 publication authorization failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _error(code: str, detail: str = "") -> Never:
    raise ContractPublicationError(code, detail)


def _load_pinned(path: Path, sha256: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractPublicationError(
            "TASK7_V18_PUBLICATION_HELPER_MISSING",
            path.name,
        ) from exc
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), sha256):
        _error("TASK7_V18_PUBLICATION_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V18_PUBLICATION_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _runner() -> ModuleType:
    return _load_pinned(
        _V18_RUNNER_PATH,
        _EXPECTED_V18_RUNNER_SHA256,
        "_task7_v18_publication_census_runner",
    )


def _admission_generator() -> ModuleType:
    return _load_pinned(
        _V18_ADMISSION_GENERATOR_PATH,
        _V18_ADMISSION_GENERATOR_SHA256,
        "_task7_v18_publication_admission_generator",
    )


def _v17_generator() -> ModuleType:
    return _load_pinned(
        _V17_GENERATOR_PATH,
        _V17_GENERATOR_SHA256,
        "_task7_v18_publication_v17_generator",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ContractPublicationError(
            "TASK7_V18_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "root differs")
    return value


def _render(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
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


def _validated_candidate(
    artifact: Mapping[str, Any],
    *,
    raw: bytes | None = None,
) -> dict[str, Any]:
    try:
        candidate = cast(
            dict[str, Any],
            _admission_generator().validate_candidate_artifact(
                artifact,
                raw=raw,
            ),
        )
    except Exception as exc:
        raise ContractPublicationError("TASK7_V18_CONTRACT_ARTIFACT_INVALID") from exc
    if hashlib.sha256(_render(candidate)).hexdigest() != _V18_ARTIFACT_SHA256:
        _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
    return candidate


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Derive, but do not publish, exact append-frozen v18 bytes."""

    candidate = _validated_candidate(_decode(candidate_raw), raw=candidate_raw)
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
    """Derive the v18 fixture from exact append-frozen artifact bytes."""

    artifact = _decode(frozen_raw)
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V18_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V18_CONTRACT_VERSION
        or metadata.get("append_frozen") is not True
    ):
        _error("TASK7_V18_APPEND_FREEZE_REQUIRED")
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    _validated_candidate(candidate)

    v17 = _v17_generator()
    v16 = v17._v16()
    section_order = cast(tuple[str, ...], v16._SECTION_ORDER)
    component_order = cast(tuple[str, ...], v16._COMPONENT_ORDER)
    if tuple(artifact) != section_order:
        _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "section order differs")
    sections = _section_hashes(artifact, section_order)
    artifact_sha256 = hashlib.sha256(frozen_raw).hexdigest()
    reviewed_hash = _h(
        [
            "full-strict-v18-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )

    materializer = _admission_generator()._materializer()
    projected_v17 = materializer.project_v18_to_v17(candidate)
    projected_v16 = materializer._V17.project_v17_to_v16(projected_v17)
    projected_v15 = v16._MATERIALIZER.project_v16_to_v15(projected_v16)
    projected_v14 = v16._MATERIALIZER._V15.project_v15_to_v14(projected_v15)
    projected_v13 = v16._MATERIALIZER._V15._V14.project_v14_to_v13(projected_v14)
    projected_v12 = v16._MATERIALIZER._V15._V14._V13.project_v13_to_v12(projected_v13)
    projected_v11 = v16._V15._V14._V13._V12._MATERIALIZER.project_v12_to_v11(projected_v12)
    components = cast(
        dict[str, str],
        v16._V15._V14._V13._V12._V11._V10._V9._V7._V6._V5._derive_components(
            projected_v11,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v18",
            _V18_CONTRACT_VERSION,
            reviewed_hash,
            [components[name] for name in component_order],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v18-contract-hash-fixture-v1",
        "pipeline_contract_version": _V18_CONTRACT_VERSION,
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V18_CONTRACT_HASH": pipeline_hash,
    }


def authorize_fixture_publication(
    *,
    candidate_artifact: Mapping[str, Any],
    verified_census_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return exact publication bytes only for the verified v18 census."""

    if verified_census_payload is None:
        _error("TASK7_V18_POSTGRES_CENSUS_RECEIPT_REQUIRED")
    candidate = _validated_candidate(candidate_artifact)
    candidate_raw = _render(candidate)
    prospective = derive_prospective_frozen_bytes(candidate_raw)
    result = verified_census_payload.get("census_result")
    if (
        verified_census_payload.get("schema_version") != _V18_CENSUS_PAYLOAD_SCHEMA
        or verified_census_payload.get("result") != "PASS"
        or verified_census_payload.get("artifact_schema") != _V18_ARTIFACT_SCHEMA
        or verified_census_payload.get("pipeline_contract_version") != _V18_CONTRACT_VERSION
        or verified_census_payload.get("candidate_artifact_sha256")
        != hashlib.sha256(candidate_raw).hexdigest()
        or verified_census_payload.get("prospective_frozen_artifact_sha256")
        != hashlib.sha256(prospective).hexdigest()
        or not isinstance(result, Mapping)
        or not isinstance(
            result.get("downgrade_retained_definition_execution_proof"),
            Mapping,
        )
    ):
        _error("TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID")
    try:
        runner = _runner()
        runner.validate_execution_contract(result=result, artifact=candidate)
        expected_database_execution = runner._database_execution(result, candidate)
    except Exception as exc:
        raise ContractPublicationError("TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID") from exc
    database_execution = verified_census_payload.get("database_execution")
    if (
        not isinstance(database_execution, Mapping)
        or tuple(database_execution) != tuple(expected_database_execution)
        or dict(database_execution) != expected_database_execution
    ):
        _error("TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID")
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
    """Verify signer label, pinned trust root, signature, and closed v18 proof."""

    try:
        receipt = _decode(receipt_raw)
        if receipt.get("signer_id") != _EXPECTED_CENSUS_SIGNER_ID or not secrets.compare_digest(
            hashlib.sha256(trusted_public_key_raw).hexdigest(),
            _EXPECTED_CENSUS_PUBLIC_KEY_SHA256,
        ):
            _error("TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID")
        payload = cast(
            dict[str, Any],
            _runner().verify_signed_receipt(
                receipt_raw=receipt_raw,
                artifact_raw=artifact_raw,
                trusted_public_key_raw=trusted_public_key_raw,
            ),
        )
        result = payload.get("census_result")
        if not isinstance(result, Mapping):
            _error("TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID")
        _runner().validate_execution_contract(
            result=result,
            artifact=_decode(artifact_raw),
        )
        return payload
    except ContractPublicationError:
        raise
    except Exception as exc:
        raise ContractPublicationError("TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID") from exc


__all__ = [
    "ContractPublicationError",
    "authorize_fixture_publication",
    "derive_contract_bytes",
    "derive_prospective_frozen_bytes",
    "verify_postgres_census_receipt",
]
