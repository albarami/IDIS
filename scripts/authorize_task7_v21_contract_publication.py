"""Authorize full-strict-v21 publication from its signed PostgreSQL census.

This verifier pins the exact v21 candidate, materializer, admission generator,
final census runner, source-manifest projection, signed receipt, signer, and
governance trust root.  It reuses the hash-pinned v20 fixture derivation as the
component baseline and admits only the reviewed v21 executable-call ACL delta.

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
_V21_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_V21_RUNNER_PATH: Final = _SCRIPT_DIR / "run_task7_v21_current_guard_census.py"
_V21_MATERIALIZER_PATH: Final = _SCRIPT_DIR / "materialize_full_strict_v21_preimage.py"
_V21_ADMISSION_GENERATOR_PATH: Final = _SCRIPT_DIR / "generate_task7_v21_contract_fixture.py"
_V20_AUTHORIZER_PATH: Final = _SCRIPT_DIR / "authorize_task7_v20_contract_publication.py"

_V21_ARTIFACT_SHA256: Final = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"
_V21_MATERIALIZER_SHA256: Final = "f038230702e846d1ee79025abdaa6b08f416702645f6d90f9d72f7ae64996946"
_V21_ADMISSION_GENERATOR_SHA256: Final = (
    "fc7865f40942a6ea0e7eb2c5cd577acf1e33b6250d30bec7396859a4b18b93c1"
)
_V20_AUTHORIZER_SHA256: Final = "631f873b533931ebb30c42d509b51536c2ddd1077d944fb48a73e536592be207"

_EXPECTED_V21_RUNNER_SHA256: Final[str | None] = (
    "d772e5a7341b36463ef130cd237b24744de481b1d2f0ec43d860143297228412"
)
_EXPECTED_V21_SOURCE_MANIFEST_SHA256: Final[str | None] = (
    "2d6edf590a4b4ac439be408be69731f64e79c25af764d9c879e1f14fde6c371c"
)
_EXPECTED_V21_CENSUS_RECEIPT_SHA256: Final[str | None] = (
    "3f36f7b66a53aa74047608f842e29988a7d6cc745904e34fdea681655946513f"
)
_PUBLICATION_INPUT_PINS_FINALIZED: Final = True

_EXPECTED_CENSUS_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256: Final = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_V21_ARTIFACT_SCHEMA: Final = "full-strict-v21-reviewed-preimage-v1"
_V21_CONTRACT_VERSION: Final = "full-strict-v21"
_V21_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v21-postgres-census-v1"
_HEX_DIGITS: Final = frozenset("0123456789abcdef")


class ContractPublicationError(RuntimeError):
    """Secret-safe v21 publication authorization failure."""

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
        _EXPECTED_V21_RUNNER_SHA256,
        _EXPECTED_V21_SOURCE_MANIFEST_SHA256,
        _EXPECTED_V21_CENSUS_RECEIPT_SHA256,
    )
    if _PUBLICATION_INPUT_PINS_FINALIZED is not True or not all(
        _is_sha256(value) for value in values
    ):
        _error(
            "TASK7_V21_CENSUS_PINS_PENDING",
            "runner, source-manifest, and receipt pins are not finalized",
        )
    return cast(tuple[str, str, str], values)


def _load_pinned(path: Path, sha256: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractPublicationError(
            "TASK7_V21_PUBLICATION_HELPER_MISSING",
            path.name,
        ) from exc
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), sha256):
        _error("TASK7_V21_PUBLICATION_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V21_PUBLICATION_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _runner() -> ModuleType:
    runner_sha256, _, _ = _require_final_census_pins()
    return _load_pinned(
        _V21_RUNNER_PATH,
        runner_sha256,
        "_task7_v21_publication_census_runner",
    )


def _bind_receipt_source_manifest(
    runner: ModuleType,
    source_closure: Mapping[str, Any],
) -> None:
    """Bind receipt candidate bytes after the live artifact becomes frozen.

    ``verify_receipt_materialization_source_closure`` has already verified the
    candidate row from ``artifact_raw`` and every executable helper from its
    live path.  The runner's generic database-projection validator otherwise
    re-reads the now-frozen artifact path and mistakes the one authorized
    false→true publication byte for source drift.
    """

    evidence = source_closure.get("materialization_source_manifest_tuple")
    sha256 = source_closure.get("materialization_source_manifest_sha256")
    if (
        not isinstance(evidence, Sequence)
        or isinstance(evidence, (str, bytes, bytearray))
        or not evidence
        or not _is_sha256(sha256)
    ):
        _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
    normalized: tuple[tuple[Any, ...], ...] = tuple(
        tuple(row)
        for row in evidence
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray))
    )
    if len(normalized) != len(evidence) or any(len(row) != 4 for row in normalized):
        _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")

    def manifest() -> tuple[tuple[Any, ...], ...]:
        return normalized

    def manifest_evidence() -> list[list[Any]]:
        return [list(row) for row in normalized]

    def manifest_sha256() -> str:
        return cast(str, sha256)

    current: object = runner
    seen: set[int] = set()
    while isinstance(current, ModuleType) and id(current) not in seen:
        seen.add(id(current))
        for name, replacement in (
            ("materialization_source_manifest", manifest),
            ("materialization_source_manifest_evidence", manifest_evidence),
            ("materialization_source_manifest_sha256", manifest_sha256),
        ):
            if hasattr(current, name):
                setattr(current, name, replacement)
        current = getattr(current, "_BASE", None)


def _materializer() -> ModuleType:
    return _load_pinned(
        _V21_MATERIALIZER_PATH,
        _V21_MATERIALIZER_SHA256,
        "_task7_v21_publication_materializer",
    )


def _admission_generator() -> ModuleType:
    return _load_pinned(
        _V21_ADMISSION_GENERATOR_PATH,
        _V21_ADMISSION_GENERATOR_SHA256,
        "_task7_v21_publication_admission_generator",
    )


def _v20_authorizer() -> ModuleType:
    return _load_pinned(
        _V20_AUTHORIZER_PATH,
        _V20_AUTHORIZER_SHA256,
        "_task7_v21_immutable_v20_derivation_authority",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite number")),
        )
    except (UnicodeError, ValueError) as exc:
        raise ContractPublicationError(
            "TASK7_V21_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V21_CONTRACT_ARTIFACT_INVALID", "root differs")
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
        materializer = _materializer()
        candidate = cast(
            dict[str, Any],
            _admission_generator().validate_candidate_artifact(
                artifact,
                raw=raw,
            ),
        )
        materializer.validate_executable_call_acl_closure(candidate)
    except Exception as exc:
        raise ContractPublicationError("TASK7_V21_CONTRACT_ARTIFACT_INVALID") from exc
    if not secrets.compare_digest(
        hashlib.sha256(_render(candidate)).hexdigest(),
        _V21_ARTIFACT_SHA256,
    ):
        _error("TASK7_V21_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
    return candidate


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Derive exact append-frozen v21 bytes in memory without publishing."""

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


def _v20_component_baseline(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Derive the exact frozen-v20 component vector through its authorizer."""

    materializer = _materializer()
    try:
        projected_v20 = materializer.project_v21_to_v20(dict(candidate))
        base_fixture = _v20_authorizer().derive_contract_bytes(_render(projected_v20))
        components = copy.deepcopy(dict(base_fixture["component_hashes"]))
    except Exception as exc:
        raise ContractPublicationError("TASK7_V21_CONTRACT_ARTIFACT_INVALID") from exc
    component_order = tuple(name for name in components if name != "pipeline_contract_hash")
    if len(component_order) != 24 or tuple(components) != (
        *component_order,
        "pipeline_contract_hash",
    ):
        _error("TASK7_V21_CONTRACT_ARTIFACT_INVALID", "component order differs")
    return components, component_order


def derive_contract_bytes(frozen_raw: bytes) -> dict[str, Any]:
    """Derive the v21 fixture from exact append-frozen candidate bytes."""

    artifact = _decode(frozen_raw)
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V21_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V21_CONTRACT_VERSION
        or metadata.get("append_frozen") is not True
    ):
        _error("TASK7_V21_APPEND_FREEZE_REQUIRED")
    candidate = copy.deepcopy(artifact)
    cast(dict[str, Any], candidate["metadata"])["append_frozen"] = False
    candidate = _validated_candidate(candidate)
    section_order = metadata.get("section_order")
    if (
        not isinstance(section_order, list)
        or not all(isinstance(name, str) and name for name in section_order)
        or tuple(artifact) != tuple(section_order)
    ):
        _error("TASK7_V21_CONTRACT_ARTIFACT_INVALID", "section order differs")
    sections = _section_hashes(artifact, section_order)
    artifact_sha256 = hashlib.sha256(frozen_raw).hexdigest()
    reviewed_hash = _h(
        [
            "full-strict-v21-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    components, component_order = _v20_component_baseline(candidate)
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
            "pipeline-contract-v21",
            _V21_CONTRACT_VERSION,
            reviewed_hash,
            [components[name] for name in component_order],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v21-contract-hash-fixture-v1",
        "pipeline_contract_version": _V21_CONTRACT_VERSION,
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V21_CONTRACT_HASH": pipeline_hash,
    }


def authorize_fixture_publication(
    *,
    candidate_artifact: Mapping[str, Any],
    verified_census_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return publication bytes only after every final census pin is closed."""

    _, source_manifest_sha256, _ = _require_final_census_pins()
    if verified_census_payload is None:
        _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_REQUIRED")
    candidate = _validated_candidate(candidate_artifact)
    candidate_raw = _render(candidate)
    prospective = derive_prospective_frozen_bytes(candidate_raw)
    result = verified_census_payload.get("census_result")
    if (
        verified_census_payload.get("schema_version") != _V21_CENSUS_PAYLOAD_SCHEMA
        or verified_census_payload.get("result") != "PASS"
        or verified_census_payload.get("artifact_schema") != _V21_ARTIFACT_SCHEMA
        or verified_census_payload.get("pipeline_contract_version") != _V21_CONTRACT_VERSION
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
        _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
    try:
        runner = _runner()
        source_closure = runner.verify_receipt_materialization_source_closure(candidate_raw)
        if source_closure.get(
            "materialization_source_manifest_sha256"
        ) != source_manifest_sha256 or result.get(
            "materialization_source_manifest_tuple"
        ) != source_closure.get("materialization_source_manifest_tuple"):
            _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
        _bind_receipt_source_manifest(runner, source_closure)
        runner.validate_execution_contract(result=result, artifact=candidate)
        expected_database_execution = runner._database_execution(result, candidate)
    except Exception as exc:
        raise ContractPublicationError("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID") from exc
    database_execution = verified_census_payload.get("database_execution")
    if (
        not isinstance(database_execution, Mapping)
        or tuple(database_execution) != tuple(expected_database_execution)
        or dict(database_execution) != expected_database_execution
    ):
        _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
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
    """Verify the exact receipt, signer, trust root, and closed v21 proofs."""

    _, source_manifest_sha256, receipt_sha256 = _require_final_census_pins()
    if not secrets.compare_digest(hashlib.sha256(receipt_raw).hexdigest(), receipt_sha256):
        _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
    try:
        receipt = _decode(receipt_raw)
        if receipt.get("signer_id") != _EXPECTED_CENSUS_SIGNER_ID or not (
            secrets.compare_digest(
                hashlib.sha256(trusted_public_key_raw).hexdigest(),
                _EXPECTED_CENSUS_PUBLIC_KEY_SHA256,
            )
        ):
            _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
        runner = _runner()
        source_closure = runner.verify_receipt_materialization_source_closure(artifact_raw)
        if source_closure.get("materialization_source_manifest_sha256") != (source_manifest_sha256):
            _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
        _bind_receipt_source_manifest(runner, source_closure)
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
            _error("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID")
        runner.validate_execution_contract(
            result=result,
            artifact=_decode(artifact_raw),
        )
        return payload
    except ContractPublicationError:
        raise
    except Exception as exc:
        raise ContractPublicationError("TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID") from exc


__all__ = [
    "ContractPublicationError",
    "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256",
    "_EXPECTED_V21_CENSUS_RECEIPT_SHA256",
    "_EXPECTED_V21_RUNNER_SHA256",
    "_EXPECTED_V21_SOURCE_MANIFEST_SHA256",
    "_V20_AUTHORIZER_SHA256",
    "_V21_ADMISSION_GENERATOR_SHA256",
    "_V21_ARTIFACT_SHA256",
    "_V21_MATERIALIZER_SHA256",
    "authorize_fixture_publication",
    "derive_contract_bytes",
    "derive_prospective_frozen_bytes",
    "verify_postgres_census_receipt",
]
