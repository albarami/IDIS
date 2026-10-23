"""Authorize full-strict-v19 publication from its signed PostgreSQL census.

This verifier breaks the runner/generator hash cycle.  It pins the unfrozen
candidate, admission generator, census runner, immutable v18 derivation
authority, signer label, and trust root.  It derives prospective frozen bytes
and fixture bytes in memory only; it never writes or publishes either output.
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
_V19_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V19_RUNNER_PATH: Final = _SCRIPT_DIR / "run_task7_v19_current_guard_census.py"
_V19_ADMISSION_GENERATOR_PATH: Final = _SCRIPT_DIR / "generate_task7_v19_contract_fixture.py"
_V18_AUTHORIZER_PATH: Final = _SCRIPT_DIR / "authorize_task7_v18_contract_publication.py"

_V19_ARTIFACT_SHA256: Final = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_EXPECTED_V19_RUNNER_SHA256: Final = (
    "503ed962bf14e12e72b2a398ea8fc0cc65a9d92e034fcfab7aaba2eb288e01be"
)
_V19_ADMISSION_GENERATOR_SHA256: Final = (
    "5f1217e5327ac04c11d1fe30be973a36ec76235e146c9bf869483f08a7fa2206"
)
_V18_AUTHORIZER_SHA256: Final = "ae2937339c663cb46012307993938e90e7260910840ff9edeedaebc5a9ae8a6c"
_EXPECTED_CENSUS_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256: Final = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_V19_ARTIFACT_SCHEMA: Final = "full-strict-v19-reviewed-preimage-v1"
_V19_CONTRACT_VERSION: Final = "full-strict-v19"
_V19_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v19-postgres-census-v1"


class ContractPublicationError(RuntimeError):
    """Secret-safe v19 publication authorization failure."""

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
            "TASK7_V19_PUBLICATION_HELPER_MISSING",
            path.name,
        ) from exc
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), sha256):
        _error("TASK7_V19_PUBLICATION_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V19_PUBLICATION_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _runner() -> ModuleType:
    return _load_pinned(
        _V19_RUNNER_PATH,
        _EXPECTED_V19_RUNNER_SHA256,
        "_task7_v19_publication_census_runner",
    )


def _admission_generator() -> ModuleType:
    return _load_pinned(
        _V19_ADMISSION_GENERATOR_PATH,
        _V19_ADMISSION_GENERATOR_SHA256,
        "_task7_v19_publication_admission_generator",
    )


def _v18_authorizer() -> ModuleType:
    return _load_pinned(
        _V18_AUTHORIZER_PATH,
        _V18_AUTHORIZER_SHA256,
        "_task7_v19_immutable_v18_derivation_authority",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite number")),
        )
    except (UnicodeError, ValueError) as exc:
        raise ContractPublicationError(
            "TASK7_V19_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "root differs")
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
        raise ContractPublicationError("TASK7_V19_CONTRACT_ARTIFACT_INVALID") from exc
    if not secrets.compare_digest(
        hashlib.sha256(_render(candidate)).hexdigest(),
        _V19_ARTIFACT_SHA256,
    ):
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
    return candidate


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Derive exact append-frozen v19 bytes without publishing them."""

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


def _v18_component_baseline(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, str], tuple[str, ...]]:
    materializer = _admission_generator()._materializer()
    projected_v18 = materializer.project_v19_to_v18(dict(candidate))
    base_authorizer = _v18_authorizer()
    base_fixture = base_authorizer.derive_contract_bytes(_render(projected_v18))
    components = copy.deepcopy(dict(base_fixture["component_hashes"]))
    component_order = tuple(base_authorizer._v17_generator()._v16()._COMPONENT_ORDER)
    if (
        tuple(name for name in component_order if name in components) != component_order
        or "pipeline_contract_hash" not in components
    ):
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "component order differs")
    return components, component_order


def derive_contract_bytes(frozen_raw: bytes) -> dict[str, Any]:
    """Derive the v19 fixture from exact append-frozen candidate bytes."""

    artifact = _decode(frozen_raw)
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V19_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V19_CONTRACT_VERSION
        or metadata.get("append_frozen") is not True
    ):
        _error("TASK7_V19_APPEND_FREEZE_REQUIRED")
    candidate = copy.deepcopy(artifact)
    cast(dict[str, Any], candidate["metadata"])["append_frozen"] = False
    _validated_candidate(candidate)
    section_order = metadata.get("section_order")
    if (
        not isinstance(section_order, list)
        or not all(isinstance(name, str) and name for name in section_order)
        or tuple(artifact) != tuple(section_order)
    ):
        _error("TASK7_V19_CONTRACT_ARTIFACT_INVALID", "section order differs")
    sections = _section_hashes(artifact, section_order)
    artifact_sha256 = hashlib.sha256(frozen_raw).hexdigest()
    reviewed_hash = _h(
        [
            "full-strict-v19-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    components, component_order = _v18_component_baseline(candidate)
    components["reviewed_preimage_artifact_hash"] = reviewed_hash
    components["uuid_namespace_registry_hash"] = str(
        artifact["uuid_namespace_registry"]["expected_sha256"]
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    components["erasure_execution_registry_hash"] = str(
        artifact["erasure_execution_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v19",
            _V19_CONTRACT_VERSION,
            reviewed_hash,
            [components[name] for name in component_order],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v19-contract-hash-fixture-v1",
        "pipeline_contract_version": _V19_CONTRACT_VERSION,
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V19_CONTRACT_HASH": pipeline_hash,
    }


def authorize_fixture_publication(
    *,
    candidate_artifact: Mapping[str, Any],
    verified_census_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return exact publication bytes only for a valid signed census payload."""

    if verified_census_payload is None:
        _error("TASK7_V19_POSTGRES_CENSUS_RECEIPT_REQUIRED")
    candidate = _validated_candidate(candidate_artifact)
    candidate_raw = _render(candidate)
    prospective = derive_prospective_frozen_bytes(candidate_raw)
    result = verified_census_payload.get("census_result")
    if (
        verified_census_payload.get("schema_version") != _V19_CENSUS_PAYLOAD_SCHEMA
        or verified_census_payload.get("result") != "PASS"
        or verified_census_payload.get("artifact_schema") != _V19_ARTIFACT_SCHEMA
        or verified_census_payload.get("pipeline_contract_version") != _V19_CONTRACT_VERSION
        or verified_census_payload.get("candidate_artifact_sha256")
        != hashlib.sha256(candidate_raw).hexdigest()
        or verified_census_payload.get("prospective_frozen_artifact_sha256")
        != hashlib.sha256(prospective).hexdigest()
        or not isinstance(result, Mapping)
        or not isinstance(
            result.get("downgrade_retained_definition_execution_proof"),
            Mapping,
        )
        or not isinstance(
            result.get("erasure_authority_execution_proof"),
            Mapping,
        )
    ):
        _error("TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID")
    try:
        runner = _runner()
        runner.validate_execution_contract(result=result, artifact=candidate)
        expected_database_execution = runner._database_execution(result, candidate)
    except Exception as exc:
        raise ContractPublicationError("TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID") from exc
    database_execution = verified_census_payload.get("database_execution")
    if (
        not isinstance(database_execution, Mapping)
        or tuple(database_execution) != tuple(expected_database_execution)
        or dict(database_execution) != expected_database_execution
    ):
        _error("TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID")
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
    """Verify signer, trust root, signature, and both closed proofs."""

    try:
        receipt = _decode(receipt_raw)
        if receipt.get("signer_id") != _EXPECTED_CENSUS_SIGNER_ID or not (
            secrets.compare_digest(
                hashlib.sha256(trusted_public_key_raw).hexdigest(),
                _EXPECTED_CENSUS_PUBLIC_KEY_SHA256,
            )
        ):
            _error("TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID")
        runner = _runner()
        source_closure = runner.verify_receipt_materialization_source_closure(artifact_raw)
        payload = cast(
            dict[str, Any],
            runner.verify_signed_receipt(
                receipt_raw=receipt_raw,
                artifact_raw=artifact_raw,
                trusted_public_key_raw=trusted_public_key_raw,
            ),
        )
        result = payload.get("census_result")
        if (
            not isinstance(result, Mapping)
            or result.get("materialization_source_manifest_tuple")
            != source_closure.get("materialization_source_manifest_tuple")
            or result.get("materialization_source_manifest_sha256")
            != source_closure.get("materialization_source_manifest_sha256")
        ):
            _error("TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID")
        runner.validate_execution_contract(
            result=result,
            artifact=_decode(artifact_raw),
        )
        return payload
    except ContractPublicationError:
        raise
    except Exception as exc:
        raise ContractPublicationError("TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID") from exc


__all__ = [
    "ContractPublicationError",
    "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256",
    "_EXPECTED_V19_RUNNER_SHA256",
    "_V19_ADMISSION_GENERATOR_SHA256",
    "_V19_ARTIFACT_SHA256",
    "authorize_fixture_publication",
    "derive_contract_bytes",
    "derive_prospective_frozen_bytes",
    "verify_postgres_census_receipt",
]
