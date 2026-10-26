"""Validate the unfrozen full-strict-v20 materialization candidate.

Despite the historical filename convention, this bounded admission scaffold
cannot freeze, sign, publish, or generate a fixture. It admits only the exact
void-v19 to unfrozen-v20 projection reviewed for RETURNING-to-SELECT closure.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never

_SCRIPT_DIR: Final = Path(__file__).resolve().parent
_REPO_ROOT: Final = _SCRIPT_DIR.parent
_MATERIALIZER_PATH: Final = _SCRIPT_DIR / "materialize_full_strict_v20_preimage.py"
_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_MATERIALIZER_SHA512: Final = (
    "6d4d421e975c7eb6b159faf6cd86535d3929bc11d6c882f5ac0f1eb649e16082"
    "e7f2132643491c75a3d5e67f871855750114480d26ce63f260eeac0cadfc6e42"
)
_ARTIFACT_SHA256: Final = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_ARTIFACT_SCHEMA: Final = "full-strict-v20-reviewed-preimage-v1"
_CONTRACT_VERSION: Final = "full-strict-v20"


class ContractArtifactError(RuntimeError):
    """Secret-safe v20 candidate admission failure."""

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
        raise ContractArtifactError("TASK7_V20_PINNED_HELPER_MISSING", path.name) from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V20_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V20_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _materializer() -> ModuleType:
    return _load_pinned(
        _MATERIALIZER_PATH,
        _MATERIALIZER_SHA512,
        "_task7_v20_materializer_helpers",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V20_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "root differs")
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


def _translate_materialization_error(exc: Exception) -> ContractArtifactError:
    rendered = str(exc)
    code, separator, detail = rendered.partition(":")
    if not code.startswith("TASK7_"):
        return ContractArtifactError("TASK7_V20_EXACT_DIFF_INVALID", type(exc).__name__)
    return ContractArtifactError(code, detail if separator else "")


def validate_returning_select_closure(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Validate the generator-derived DML RETURNING privilege dependency."""

    materializer = _materializer()
    try:
        return materializer.validate_returning_select_closure(copy.deepcopy(dict(artifact)))
    except Exception as exc:
        raise _translate_materialization_error(exc) from exc


def validate_candidate_artifact(
    artifact: Mapping[str, Any],
    *,
    raw: bytes | None = None,
) -> dict[str, Any]:
    """Prove exact void-v19 to unfrozen-v20 materialization."""

    candidate = copy.deepcopy(dict(artifact))
    metadata = candidate.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "metadata differs")
    if raw is not None and _render(candidate) != raw:
        _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "rendering differs")
    validate_returning_select_closure(candidate)
    materializer = _materializer()
    try:
        v19 = materializer._load_pinned_v19()
        rebuilt = materializer.build_v20(v19)
        projected = materializer.project_v20_to_v19(candidate)
        materializer._validate_exact_diff(v19, candidate)
    except ContractArtifactError:
        raise
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V20_EXACT_DIFF_INVALID",
            type(exc).__name__,
        ) from exc
    if materializer._render(rebuilt) != _render(candidate):
        _error("TASK7_V20_EXACT_DIFF_INVALID", "candidate differs")
    if materializer._render(projected) != materializer._render(v19):
        _error("TASK7_V20_EXACT_DIFF_INVALID", "v19 projection differs")
    return candidate


def reject_fixture_generation() -> Never:
    """Fail closed until reviewed v20 PostgreSQL proofs authorize freezing."""

    _error("TASK7_V20_APPEND_FREEZE_NOT_AUTHORIZED")


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
            _error("TASK7_V20_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
        validate_candidate_artifact(_decode(raw), raw=raw)
    except (ContractArtifactError, OSError) as exc:
        code = (
            exc.code
            if isinstance(exc, ContractArtifactError)
            else "TASK7_V20_CONTRACT_ARTIFACT_INVALID"
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
    "validate_returning_select_closure",
]
