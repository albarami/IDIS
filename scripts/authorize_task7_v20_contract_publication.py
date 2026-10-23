"""Authorize full-strict-v20 publication from its signed PostgreSQL census.

This verifier admits only the final v20 census runner, source-manifest
projection, and independently verified signed receipt hashes.  It also pins
the reviewed unfrozen candidate and both candidate-construction helpers.

Importing this module never freezes, signs, writes, or publishes an artifact.
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
_V20_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_RUNNER_PATH: Final = _SCRIPT_DIR / "run_task7_v20_current_guard_census.py"
_V20_MATERIALIZER_PATH: Final = _SCRIPT_DIR / "materialize_full_strict_v20_preimage.py"
_V20_ADMISSION_GENERATOR_PATH: Final = _SCRIPT_DIR / "generate_task7_v20_contract_fixture.py"
_V18_AUTHORIZER_PATH: Final = _SCRIPT_DIR / "authorize_task7_v18_contract_publication.py"

_V20_ARTIFACT_SHA256: Final = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_V20_MATERIALIZER_SHA256: Final = "f49b9f4e427505909715c7a84f6ecb88b1a1fb2082f5c652f15da5cffbdee509"
_V20_ADMISSION_GENERATOR_SHA256: Final = (
    "3ac5826bfe9909bbedaee6a3984fe9201edd2043dab7bf547c4d81887cd16c9d"
)
_V18_AUTHORIZER_SHA256: Final = "ae2937339c663cb46012307993938e90e7260910840ff9edeedaebc5a9ae8a6c"

_EXPECTED_V20_RUNNER_SHA256: Final[str | None] = (
    "823230359182e1a0b38483c96f268577bf6fd9f9a5a23d8b2dba1752ca94d217"
)
_EXPECTED_V20_SOURCE_MANIFEST_SHA256: Final[str | None] = (
    "9375b1cbbafafca1c3d4263c43d0f7a4fa32b2df66acc42bf3d00447600404f2"
)
_EXPECTED_V20_CENSUS_RECEIPT_SHA256: Final[str | None] = (
    "7b39bde8b6746921fa144edf84a5d075617bd9ca8fe09afa078467fce0f20f98"
)
_PUBLICATION_INPUT_PINS_FINALIZED: Final = True

_EXPECTED_CENSUS_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256: Final = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_V20_ARTIFACT_SCHEMA: Final = "full-strict-v20-reviewed-preimage-v1"
_V20_CONTRACT_VERSION: Final = "full-strict-v20"
_V20_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v20-postgres-census-v1"
_HEX_DIGITS: Final = frozenset("0123456789abcdef")


class ContractPublicationError(RuntimeError):
    """Secret-safe v20 publication authorization failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _error(code: str, detail: str = "") -> Never:
    raise ContractPublicationError(code, detail)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _require_final_census_pins() -> tuple[str, str, str]:
    """Return the three closed census pins or reject before any I/O."""

    values = (
        _EXPECTED_V20_RUNNER_SHA256,
        _EXPECTED_V20_SOURCE_MANIFEST_SHA256,
        _EXPECTED_V20_CENSUS_RECEIPT_SHA256,
    )
    if _PUBLICATION_INPUT_PINS_FINALIZED is not True or not all(
        _is_sha256(value) for value in values
    ):
        _error(
            "TASK7_V20_CENSUS_PINS_PENDING",
            "runner, source-manifest, and receipt pins are not finalized",
        )
    return cast(tuple[str, str, str], values)


def _load_pinned(path: Path, sha256: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractPublicationError(
            "TASK7_V20_PUBLICATION_HELPER_MISSING",
            path.name,
        ) from exc
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), sha256):
        _error("TASK7_V20_PUBLICATION_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V20_PUBLICATION_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _runner() -> ModuleType:
    runner_sha256, _, _ = _require_final_census_pins()
    return _load_pinned(
        _V20_RUNNER_PATH,
        runner_sha256,
        "_task7_v20_publication_census_runner",
    )


def _materializer() -> ModuleType:
    return _load_pinned(
        _V20_MATERIALIZER_PATH,
        _V20_MATERIALIZER_SHA256,
        "_task7_v20_publication_materializer",
    )


def _admission_generator() -> ModuleType:
    return _load_pinned(
        _V20_ADMISSION_GENERATOR_PATH,
        _V20_ADMISSION_GENERATOR_SHA256,
        "_task7_v20_publication_admission_generator",
    )


def _v18_authorizer() -> ModuleType:
    return _load_pinned(
        _V18_AUTHORIZER_PATH,
        _V18_AUTHORIZER_SHA256,
        "_task7_v20_immutable_v18_derivation_authority",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite number")),
        )
    except (UnicodeError, ValueError) as exc:
        raise ContractPublicationError(
            "TASK7_V20_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "root differs")
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
        # Load the materializer independently as well as through the admission
        # generator.  Publication therefore pins both construction layers and
        # cannot rely on the generator's transitive import alone.
        materializer = _materializer()
        candidate = cast(
            dict[str, Any],
            _admission_generator().validate_candidate_artifact(
                artifact,
                raw=raw,
            ),
        )
        materializer.validate_returning_select_closure(candidate)
    except Exception as exc:
        raise ContractPublicationError("TASK7_V20_CONTRACT_ARTIFACT_INVALID") from exc
    if not secrets.compare_digest(
        hashlib.sha256(_render(candidate)).hexdigest(),
        _V20_ARTIFACT_SHA256,
    ):
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
    return candidate


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Derive exact append-frozen v20 bytes in memory without publishing."""

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
    materializer = _materializer()
    projected_v19 = materializer.project_v20_to_v19(dict(candidate))
    projected_v18 = materializer._M19.project_v19_to_v18(projected_v19)
    base_authorizer = _v18_authorizer()
    base_fixture = base_authorizer.derive_contract_bytes(_render(projected_v18))
    components = copy.deepcopy(dict(base_fixture["component_hashes"]))
    component_order = tuple(base_authorizer._v17_generator()._v16()._COMPONENT_ORDER)
    if (
        tuple(name for name in component_order if name in components) != component_order
        or "pipeline_contract_hash" not in components
    ):
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "component order differs")
    return components, component_order


def derive_contract_bytes(frozen_raw: bytes) -> dict[str, Any]:
    """Derive the v20 fixture from exact append-frozen candidate bytes."""

    artifact = _decode(frozen_raw)
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V20_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V20_CONTRACT_VERSION
        or metadata.get("append_frozen") is not True
    ):
        _error("TASK7_V20_APPEND_FREEZE_REQUIRED")
    candidate = copy.deepcopy(artifact)
    cast(dict[str, Any], candidate["metadata"])["append_frozen"] = False
    _validated_candidate(candidate)
    section_order = metadata.get("section_order")
    if (
        not isinstance(section_order, list)
        or not all(isinstance(name, str) and name for name in section_order)
        or tuple(artifact) != tuple(section_order)
    ):
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "section order differs")
    sections = _section_hashes(artifact, section_order)
    artifact_sha256 = hashlib.sha256(frozen_raw).hexdigest()
    reviewed_hash = _h(
        [
            "full-strict-v20-reviewed-preimage-artifact-v1",
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
            "pipeline-contract-v20",
            _V20_CONTRACT_VERSION,
            reviewed_hash,
            [components[name] for name in component_order],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v20-contract-hash-fixture-v1",
        "pipeline_contract_version": _V20_CONTRACT_VERSION,
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V20_CONTRACT_HASH": pipeline_hash,
    }


def authorize_fixture_publication(
    *,
    candidate_artifact: Mapping[str, Any],
    verified_census_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return publication bytes only after every final census pin is closed."""

    _, source_manifest_sha256, _ = _require_final_census_pins()
    if verified_census_payload is None:
        _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_REQUIRED")
    candidate = _validated_candidate(candidate_artifact)
    candidate_raw = _render(candidate)
    prospective = derive_prospective_frozen_bytes(candidate_raw)
    result = verified_census_payload.get("census_result")
    if (
        verified_census_payload.get("schema_version") != _V20_CENSUS_PAYLOAD_SCHEMA
        or verified_census_payload.get("result") != "PASS"
        or verified_census_payload.get("artifact_schema") != _V20_ARTIFACT_SCHEMA
        or verified_census_payload.get("pipeline_contract_version") != _V20_CONTRACT_VERSION
        or verified_census_payload.get("candidate_artifact_sha256")
        != hashlib.sha256(candidate_raw).hexdigest()
        or verified_census_payload.get("prospective_frozen_artifact_sha256")
        != hashlib.sha256(prospective).hexdigest()
        or not isinstance(result, Mapping)
        or result.get("materialization_source_manifest_sha256") != source_manifest_sha256
        or not isinstance(
            result.get("downgrade_retained_definition_execution_proof"),
            Mapping,
        )
        or not isinstance(
            result.get("erasure_authority_execution_proof"),
            Mapping,
        )
    ):
        _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID")
    try:
        runner = _runner()
        runner.validate_execution_contract(result=result, artifact=candidate)
        expected_database_execution = runner._database_execution(result, candidate)
    except Exception as exc:
        raise ContractPublicationError("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID") from exc
    database_execution = verified_census_payload.get("database_execution")
    if (
        not isinstance(database_execution, Mapping)
        or tuple(database_execution) != tuple(expected_database_execution)
        or dict(database_execution) != expected_database_execution
    ):
        _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID")
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
    """Verify the exact receipt, signer, trust root, and closed v20 proofs."""

    _, source_manifest_sha256, receipt_sha256 = _require_final_census_pins()
    if not secrets.compare_digest(hashlib.sha256(receipt_raw).hexdigest(), receipt_sha256):
        _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID")
    try:
        receipt = _decode(receipt_raw)
        if receipt.get("signer_id") != _EXPECTED_CENSUS_SIGNER_ID or not (
            secrets.compare_digest(
                hashlib.sha256(trusted_public_key_raw).hexdigest(),
                _EXPECTED_CENSUS_PUBLIC_KEY_SHA256,
            )
        ):
            _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID")
        runner = _runner()
        source_closure = runner.verify_receipt_materialization_source_closure(artifact_raw)
        if source_closure.get("materialization_source_manifest_sha256") != (source_manifest_sha256):
            _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID")
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
            or result.get("materialization_source_manifest_sha256") != source_manifest_sha256
        ):
            _error("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID")
        runner.validate_execution_contract(
            result=result,
            artifact=_decode(artifact_raw),
        )
        return payload
    except ContractPublicationError:
        raise
    except Exception as exc:
        raise ContractPublicationError("TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID") from exc


__all__ = [
    "ContractPublicationError",
    "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256",
    "_EXPECTED_V20_CENSUS_RECEIPT_SHA256",
    "_EXPECTED_V20_RUNNER_SHA256",
    "_EXPECTED_V20_SOURCE_MANIFEST_SHA256",
    "_V20_ADMISSION_GENERATOR_SHA256",
    "_V20_ARTIFACT_SHA256",
    "_V20_MATERIALIZER_SHA256",
    "authorize_fixture_publication",
    "derive_contract_bytes",
    "derive_prospective_frozen_bytes",
    "verify_postgres_census_receipt",
]
