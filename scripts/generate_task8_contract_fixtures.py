"""Validate and optionally stage the inactive full-strict-v26 hash fixture.

The caller supplies every contract authority explicitly.  The staged artifact
is admitted by the production materializer, independently derived and
verified by the stdlib verifier, and never read from or written to a canonical
publication path.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Never

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_INDEPENDENT_VERIFIER_PATH = _SCRIPT_DIR / "verify_full_strict_v26_fixture_independently.py"
_ADMISSION_MATERIALIZER_PATH = _SCRIPT_DIR / "materialize_full_strict_v26_preimage.py"
_PARENT_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_POLICY_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
_REVIEWER_MAP_PATH = (
    _REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_POLICY_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)
_CANONICAL_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
_CANONICAL_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v26_contract_hash_v1.json"
)

_VERSION = "full-strict-v26"
_FIXTURE_SCHEMA = "full-strict-v26-contract-hash-fixture-v1"
_VERIFICATION_PATH = "TASK8_V26_PRODUCTION_PLUS_INDEPENDENT_V1"
_INDEPENDENT_VERIFICATION_PATH = "TASK8_V26_INDEPENDENT_STDLIB_V1"
_PARENT_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_POLICY_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"

_EXPECTED_IDENTITIES = {
    "artifact_sha256": "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27",
    "fixture_sha256": "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7",
    "contract_hash": "bb0bd8deb5c7c85aa292a860773353f04e60db01c80d2d3a02ff25aed7e840fc",
    "graph_sha256": "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece",
    "conservation_sha256": "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235",
    "policy_binding_sha256": "3e07e20d1b304e0e22d8956f408562ac194103184d475623118497a21ae5a467",
}
_FIXTURE_KEYS = (
    "artifact_schema",
    "pipeline_contract_version",
    "artifact_sha256",
    "FULL_STRICT_V26_CONTRACT_HASH",
    "component_hashes",
    "identity_hash_dependency_graph",
    "approved_semantics_conservation",
    "policy_binding",
    "counts",
    "activation_status",
)


class Task8FixtureError(RuntimeError):
    """Fail-closed fixture-generation error carrying only a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise Task8FixtureError(code)


def _render(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Task8FixtureError("TASK8_V26_FIXTURE_INVALID") from exc


def _decode(raw: bytes, code: str) -> dict[str, Any]:
    def reject_nonfinite(_value: str) -> Never:
        raise ValueError("NONFINITE_JSON")

    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=reject_nonfinite)
    except (UnicodeError, ValueError) as exc:
        raise Task8FixtureError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _load_module(path: Path, name: str, failure_code: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _fail(failure_code)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise Task8FixtureError(failure_code) from exc
    return module


def _load_admission_materializer() -> ModuleType:
    return _load_module(
        _ADMISSION_MATERIALIZER_PATH,
        "_task8_v26_fixture_admission_materializer",
        "TASK8_V26_MATERIALIZER_ADMISSION_INVALID",
    )


def _load_independent_verifier() -> ModuleType:
    return _load_module(
        _INDEPENDENT_VERIFIER_PATH,
        "_task8_v26_fixture_independent_verifier",
        "TASK8_V26_INDEPENDENT_VERIFIER_INVALID",
    )


@contextlib.contextmanager
def _bytecode_writes_disabled() -> Iterator[None]:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = previous


def _validate_fixed_input_hashes(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v26_artifact_raw: bytes,
) -> None:
    if hashlib.sha256(parent_artifact_raw).hexdigest() != _PARENT_ARTIFACT_SHA256:
        _fail("TASK8_V26_PARENT_MUST_BE_FROZEN_V21")
    if hashlib.sha256(policy_artifact_raw).hexdigest() != _POLICY_ARTIFACT_SHA256:
        _fail("TASK8_V26_POLICY_MUST_BE_FROZEN_V2")
    if hashlib.sha256(reviewer_map_raw).hexdigest() != _REVIEWER_MAP_SHA256:
        _fail("TASK8_V26_REVIEWER_MAP_BYTES_DRIFT")
    if hashlib.sha256(v26_artifact_raw).hexdigest() != _EXPECTED_IDENTITIES["artifact_sha256"]:
        _fail("TASK8_V26_CONTRACT_ARTIFACT_INVALID")


def _validate_policy_fixture_prerequisite() -> None:
    try:
        raw = _POLICY_FIXTURE_PATH.read_bytes()
    except OSError as exc:
        raise Task8FixtureError("TASK8_V26_POLICY_FIXTURE_PREREQUISITE_INVALID") from exc
    if hashlib.sha256(raw).hexdigest() != _POLICY_FIXTURE_SHA256:
        _fail("TASK8_V26_POLICY_FIXTURE_PREREQUISITE_INVALID")
    fixture = _decode(raw, "TASK8_V26_POLICY_FIXTURE_PREREQUISITE_INVALID")
    if (
        _render(fixture) != raw
        or fixture.get("artifact_schema") != "fdd-formula-policy-v2-hash-fixture-v1"
        or fixture.get("policy_id") != "fdd-formula-policy-v2"
        or fixture.get("artifact_sha256") != _POLICY_ARTIFACT_SHA256
        or fixture.get("fdd_formula_policy_v2_sha256") != _POLICY_SHA256
        or fixture.get("review_map_sha256") != _REVIEWER_MAP_SHA256
        or fixture.get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V26_POLICY_FIXTURE_PREREQUISITE_INVALID")


def _identity_set(fixture: Mapping[str, Any], fixture_raw: bytes) -> dict[str, str]:
    graph = fixture.get("identity_hash_dependency_graph")
    conservation = fixture.get("approved_semantics_conservation")
    binding = fixture.get("policy_binding")
    if (
        not isinstance(graph, Mapping)
        or not isinstance(conservation, Mapping)
        or not isinstance(binding, Mapping)
    ):
        _fail("TASK8_V26_FIXTURE_INVALID")
    identities = {
        "artifact_sha256": fixture.get("artifact_sha256"),
        "fixture_sha256": hashlib.sha256(fixture_raw).hexdigest(),
        "contract_hash": fixture.get("FULL_STRICT_V26_CONTRACT_HASH"),
        "graph_sha256": graph.get("graph_sha256"),
        "conservation_sha256": conservation.get("sha256"),
        "policy_binding_sha256": binding.get("binding_sha256"),
    }
    if identities != _EXPECTED_IDENTITIES:
        _fail("TASK8_V26_FIXTURE_IDENTITY_MISMATCH")
    return _EXPECTED_IDENTITIES.copy()


def _validate_fixture_shape(fixture: object, artifact_raw: bytes) -> dict[str, Any]:
    if not isinstance(fixture, dict) or tuple(fixture) != _FIXTURE_KEYS:
        _fail("TASK8_V26_FIXTURE_INVALID")
    if (
        fixture.get("artifact_schema") != _FIXTURE_SCHEMA
        or fixture.get("pipeline_contract_version") != _VERSION
        or fixture.get("artifact_sha256") != hashlib.sha256(artifact_raw).hexdigest()
        or fixture.get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V26_FIXTURE_INVALID")
    return fixture


def derive_task8_fixture(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v26_artifact_raw: bytes,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate exact authorities and derive the v26 fixture entirely in memory."""

    _validate_fixed_input_hashes(
        parent_artifact_raw=parent_artifact_raw,
        policy_artifact_raw=policy_artifact_raw,
        reviewer_map_raw=reviewer_map_raw,
        v26_artifact_raw=v26_artifact_raw,
    )
    _validate_policy_fixture_prerequisite()

    with _bytecode_writes_disabled():
        materializer = _load_admission_materializer()
        validate_frozen_artifact = getattr(materializer, "validate_frozen_artifact", None)
        if not callable(validate_frozen_artifact):
            _fail("TASK8_V26_MATERIALIZER_ADMISSION_INVALID")
        try:
            admitted = materializer.validate_frozen_artifact(
                v26_artifact_raw,
                parent_artifact_raw=parent_artifact_raw,
                policy_artifact_raw=policy_artifact_raw,
                reviewer_map_raw=reviewer_map_raw,
            )
        except Exception as exc:
            raise Task8FixtureError("TASK8_V26_MATERIALIZER_ADMISSION_INVALID") from exc
        if not isinstance(admitted, Mapping):
            _fail("TASK8_V26_MATERIALIZER_ADMISSION_INVALID")

        verifier = _load_independent_verifier()
        derive_fixture = getattr(verifier, "derive_fixture", None)
        verify_fixture = getattr(verifier, "verify_fixture", None)
        if not callable(derive_fixture) or not callable(verify_fixture):
            _fail("TASK8_V26_INDEPENDENT_VERIFIER_INVALID")
        try:
            derived_fixture = verifier.derive_fixture(
                parent_artifact_raw=parent_artifact_raw,
                policy_artifact_raw=policy_artifact_raw,
                reviewer_map_raw=reviewer_map_raw,
                v26_artifact_raw=v26_artifact_raw,
            )
        except Exception as exc:
            raise Task8FixtureError("TASK8_V26_INDEPENDENT_VERIFIER_INVALID") from exc
        fixture = _validate_fixture_shape(derived_fixture, v26_artifact_raw)
        fixture_raw = _render(fixture)
        try:
            verification = verifier.verify_fixture(
                parent_artifact_raw=parent_artifact_raw,
                policy_artifact_raw=policy_artifact_raw,
                reviewer_map_raw=reviewer_map_raw,
                v26_artifact_raw=v26_artifact_raw,
                fixture_raw=fixture_raw,
            )
        except Exception as exc:
            raise Task8FixtureError("TASK8_V26_INDEPENDENT_VERIFIER_INVALID") from exc
    if not isinstance(verification, Mapping):
        _fail("TASK8_V26_INDEPENDENT_VERIFIER_INVALID")

    identities = _identity_set(fixture, fixture_raw)
    graph = fixture["identity_hash_dependency_graph"]
    if (
        verification.get("verification_path") != _INDEPENDENT_VERIFICATION_PATH
        or verification.get("parent_artifact_sha256") != _PARENT_ARTIFACT_SHA256
        or verification.get("artifact_sha256") != identities["artifact_sha256"]
        or verification.get("fixture_sha256") != identities["fixture_sha256"]
        or verification.get("contract_hash") != identities["contract_hash"]
        or verification.get("identity_graph_sha256") != identities["graph_sha256"]
        or verification.get("conservation_sha256") != identities["conservation_sha256"]
        or verification.get("policy_binding_sha256") != identities["policy_binding_sha256"]
        or verification.get("component_hashes") != fixture["component_hashes"]
        or verification.get("identity_graph_counts") != graph["counts"]
        or verification.get("counts") != fixture["counts"]
    ):
        _fail("TASK8_V26_INDEPENDENT_VERIFICATION_MISMATCH")
    return fixture, identities


def _resolved(path: Path, code: str) -> Path:
    try:
        return path.resolve()
    except OSError as exc:
        raise Task8FixtureError(code) from exc


def _forbidden_output_paths() -> tuple[Path, ...]:
    return (
        _CANONICAL_ARTIFACT_PATH,
        _CANONICAL_FIXTURE_PATH,
        _PARENT_ARTIFACT_PATH,
        _POLICY_ARTIFACT_PATH,
        _REVIEWER_MAP_PATH,
        _POLICY_FIXTURE_PATH,
    )


def _file_identity(path: Path, code: str) -> tuple[int, int]:
    try:
        observed = path.stat()
    except OSError as exc:
        raise Task8FixtureError(code) from exc
    return observed.st_dev, observed.st_ino


def _same_existing_file(left: Path, right: Path, code: str) -> bool:
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise Task8FixtureError(code) from exc


def _aliases_any(path: Path, authorities: Sequence[Path], code: str) -> bool:
    resolved = _resolved(path, code)
    for authority in authorities:
        if resolved == _resolved(authority, code):
            return True
        if _same_existing_file(path, authority, code):
            return True
    return False


def _reject_canonical_artifact_input(path: Path) -> None:
    if _aliases_any(
        path,
        (_CANONICAL_ARTIFACT_PATH, _CANONICAL_FIXTURE_PATH),
        "TASK8_V26_STAGED_ARTIFACT_PATH_INVALID",
    ):
        _fail("TASK8_V26_CANONICAL_ARTIFACT_INPUT_FORBIDDEN")


def _validate_staging_output_path(path: Path) -> None:
    if _aliases_any(
        path,
        _forbidden_output_paths(),
        "TASK8_V26_FIXTURE_STAGING_IO_FAILURE",
    ):
        _fail("TASK8_V26_CANONICAL_PUBLICATION_PATH_FORBIDDEN")


def _open_file_identity(handle: Any) -> tuple[int, int]:
    try:
        observed = os.fstat(handle.fileno())
    except (OSError, ValueError) as exc:
        raise Task8FixtureError("TASK8_V26_FIXTURE_STAGING_IO_FAILURE") from exc
    return observed.st_dev, observed.st_ino


def _validate_open_staging_handle(
    path: Path,
    handle: Any,
    expected_parent_identity: tuple[int, int],
) -> tuple[int, int]:
    if _file_identity(path.parent, "TASK8_V26_FIXTURE_PARENT_CHANGED") != (
        expected_parent_identity
    ):
        _fail("TASK8_V26_FIXTURE_PARENT_CHANGED")
    opened_identity = _open_file_identity(handle)
    if _file_identity(path, "TASK8_V26_FIXTURE_TARGET_CHANGED") != opened_identity:
        _fail("TASK8_V26_FIXTURE_TARGET_CHANGED")
    _validate_staging_output_path(path)
    return opened_identity


def _unlink_if_identity_matches(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    try:
        if _file_identity(path, "TASK8_V26_FIXTURE_STAGING_IO_FAILURE") == identity:
            path.unlink()
    except (OSError, Task8FixtureError):
        return


def _write_staging_fixture(path: Path, raw: bytes) -> None:
    _validate_staging_output_path(path)
    created = False
    created_identity: tuple[int, int] | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _validate_staging_output_path(path)
        parent_identity = _file_identity(path.parent, "TASK8_V26_FIXTURE_PARENT_CHANGED")
        with path.open("xb") as handle:
            created = True
            created_identity = _open_file_identity(handle)
            if _validate_open_staging_handle(path, handle, parent_identity) != created_identity:
                _fail("TASK8_V26_FIXTURE_TARGET_CHANGED")
            if handle.write(raw) != len(raw):
                _fail("TASK8_V26_FIXTURE_STAGING_IO_FAILURE")
    except FileExistsError as exc:
        _validate_staging_output_path(path)
        raise Task8FixtureError("TASK8_V26_FIXTURE_TARGET_EXISTS") from exc
    except Task8FixtureError:
        if created:
            _unlink_if_identity_matches(path, created_identity)
        raise
    except OSError as exc:
        if created:
            _unlink_if_identity_matches(path, created_identity)
        raise Task8FixtureError("TASK8_V26_FIXTURE_STAGING_IO_FAILURE") from exc


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        _fail("TASK8_V26_CLI_ARGUMENT_INVALID")


def _parser() -> argparse.ArgumentParser:
    parser = _StableArgumentParser(description=__doc__)
    parser.add_argument("--parent-artifact", type=Path, required=True)
    parser.add_argument("--policy-artifact", type=Path, required=True)
    parser.add_argument("--reviewer-map", type=Path, required=True)
    parser.add_argument("--staged-artifact", type=Path, required=True)
    parser.add_argument("--staging-output", type=Path)
    parser.add_argument("--derive-only", action="store_true")
    return parser


def _success_report(identities: Mapping[str, str], result: str) -> dict[str, str]:
    return {
        "activation_status": "INACTIVE",
        **dict(identities),
        "result": result,
        "verification_path": _VERIFICATION_PATH,
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(None if argv is None else list(argv))
        if args.derive_only and args.staging_output is not None:
            _fail("TASK8_V26_DERIVE_ONLY_OUTPUT_FORBIDDEN")
        if not args.derive_only and args.staging_output is None:
            _fail("TASK8_V26_CLI_ARGUMENT_INVALID")
        _reject_canonical_artifact_input(args.staged_artifact)
        if args.staging_output is not None:
            _validate_staging_output_path(args.staging_output)
        try:
            parent_raw = args.parent_artifact.read_bytes()
            policy_raw = args.policy_artifact.read_bytes()
            reviewer_map_raw = args.reviewer_map.read_bytes()
            artifact_raw = args.staged_artifact.read_bytes()
        except OSError as exc:
            raise Task8FixtureError("TASK8_V26_INPUT_MISSING") from exc
        fixture, identities = derive_task8_fixture(
            parent_artifact_raw=parent_raw,
            policy_artifact_raw=policy_raw,
            reviewer_map_raw=reviewer_map_raw,
            v26_artifact_raw=artifact_raw,
        )
        result = "DERIVED"
        if args.staging_output is not None:
            _write_staging_fixture(args.staging_output, _render(fixture))
            result = "STAGED"
        report = _success_report(identities, result)
    except Task8FixtureError as exc:
        print(json.dumps({"reason_code": exc.code, "result": "FAILED"}, sort_keys=True))
        return 2
    except Exception:  # noqa: BLE001 - final CLI privacy boundary; BaseException propagates
        print(
            json.dumps(
                {"reason_code": "TASK8_V26_UNEXPECTED_FAILURE", "result": "FAILED"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Task8FixtureError", "derive_task8_fixture", "main"]
