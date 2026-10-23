"""Validate the unfrozen full-strict-v18 materialization candidate.

This bounded scaffold intentionally cannot freeze, sign, or generate a
contract fixture. V18 remains an unfrozen candidate until its separately
reviewed executable PostgreSQL proof authorizes publication.
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
_V18_MATERIALIZER_PATH: Final = _SCRIPT_DIR / "materialize_full_strict_v18_preimage.py"
_V18_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_MATERIALIZER_SHA512: Final = (
    "24ddfa5d6bbcb7ef0d05f4db10ec6244ab5b2fa4a9b7abd4e0e0d425e8bfd307"
    "32d588f387f89d4fa50c1b8d996025c8a8d13a6055af51eaaa299b5e12aa0b5b"
)
_V18_ARTIFACT_SHA256: Final = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_V18_ARTIFACT_SCHEMA: Final = "full-strict-v18-reviewed-preimage-v1"
_V18_CONTRACT_VERSION: Final = "full-strict-v18"


class ContractArtifactError(RuntimeError):
    """Secret-safe v18 candidate admission failure."""

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
        raise ContractArtifactError("TASK7_V18_PINNED_HELPER_MISSING", path.name) from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V18_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V18_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _materializer() -> ModuleType:
    return _load_pinned(
        _V18_MATERIALIZER_PATH,
        _V18_MATERIALIZER_SHA512,
        "_task7_v18_materializer_helpers",
    )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V18_CONTRACT_ARTIFACT_INVALID",
            "JSON decode failed",
        ) from exc
    if not isinstance(value, dict):
        _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "root differs")
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


def validate_candidate_artifact(
    artifact: Mapping[str, Any],
    *,
    raw: bytes | None = None,
) -> dict[str, Any]:
    """Prove exact frozen-v17 to unfrozen-v18 materialization."""

    candidate = copy.deepcopy(dict(artifact))
    metadata = candidate.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V18_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V18_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "metadata differs")
    if raw is not None and _render(candidate) != raw:
        _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "rendering differs")
    materializer = _materializer()
    try:
        v17 = materializer._load_pinned_v17()
        rebuilt = materializer.build_v18(v17)
        projected = materializer.project_v18_to_v17(candidate)
        materializer._validate_exact_diff(v17, candidate)
        materializer.validate_retained_restore_language(candidate)
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V18_EXACT_DIFF_INVALID",
            type(exc).__name__,
        ) from exc
    if materializer._render(rebuilt) != _render(candidate):
        _error("TASK7_V18_EXACT_DIFF_INVALID", "candidate differs")
    if materializer._render(projected) != materializer._render(v17):
        _error("TASK7_V18_EXACT_DIFF_INVALID", "v17 projection differs")
    return candidate


def reject_fixture_generation() -> Never:
    """Fail closed until a reviewed v18 executable census authorizes freezing."""

    _error("TASK7_V18_APPEND_FREEZE_NOT_AUTHORIZED")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=_V18_ARTIFACT_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(None if argv is None else list(argv))
    try:
        raw = args.artifact.read_bytes()
        if (
            args.artifact.resolve() == _V18_ARTIFACT_PATH.resolve()
            and hashlib.sha256(raw).hexdigest() != _V18_ARTIFACT_SHA256
        ):
            _error("TASK7_V18_CONTRACT_ARTIFACT_INVALID", "candidate hash differs")
        validate_candidate_artifact(_decode(raw), raw=raw)
    except (ContractArtifactError, OSError) as exc:
        code = (
            exc.code
            if isinstance(exc, ContractArtifactError)
            else "TASK7_V18_CONTRACT_ARTIFACT_INVALID"
        )
        print(json.dumps({"reason_code": code, "result": "FAILED"}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "pipeline_contract_version": _V18_CONTRACT_VERSION,
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
]
