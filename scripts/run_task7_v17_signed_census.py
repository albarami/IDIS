"""Run the approved v17 PostgreSQL census once and sign its receipt.

The CLI fixes the candidate, runner, signer, and key locations.  It never
freezes the artifact or writes a contract fixture; its only output is an
exclusive signed census receipt under the Task 7 local-report root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never

_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
_SRC_ROOT: Final = _REPO_ROOT / "src"
for _import_root in (_REPO_ROOT, _SRC_ROOT):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_RUNNER_PATH: Final = _REPO_ROOT / "scripts" / "run_task7_v17_current_guard_census.py"
_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_REPORT_ROOT: Final = _REPO_ROOT / ".local_reports" / "slice100"
_DEFAULT_OUTPUT_NAME: Final = "task7_v17_current_guard_postgres_census_receipt.json"
_PRIVATE_KEY_PATH: Final = Path(r"C:\IDIS-secrets\keys\governance-signing-private.pem")
_APPROVED_RUNNER_SHA256: Final = "9f3585fb6b10a35cf1c73bd3d7f08c6c3aacb60c83d5b4d8e9e32ee9c0ed539c"
_CANDIDATE_ARTIFACT_SHA256: Final = (
    "ae6d42df18d236fad3730be9829fe66b19549e7902c06ea9c62ad7c6d250a5e7"
)
_APPROVED_MATERIALIZATION_SOURCE_MANIFEST_SHA256: Final = (
    "3f8da0bdc0c3bae230ca057388b1a45a30154dacffe85f60c47d07496763e5ca"
)
_APPROVED_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EVENT_SCHEMA: Final = "task7-v17-signed-census-cli-result-v1"
_RUNNER_MODULE_NAME: Final = "_task7_v17_signed_census_approved_runner"
_MAX_PUBLIC_KEY_BYTES: Final = 64 * 1024
_MAX_RUNNER_BYTES: Final = 2 * 1024 * 1024


class SignedCensusCliError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class _OperatorMaterial:
    signer_id: str
    private_key: Ed25519PrivateKey
    public_key_raw: bytes


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_ARGUMENTS_INVALID")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _approved_digest(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _load_approved_runner() -> ModuleType:
    try:
        raw = _RUNNER_PATH.read_bytes()
    except OSError as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_RUNNER_UNAUTHORIZED") from exc
    if (
        not _RUNNER_PATH.is_absolute()
        or _is_link_or_reparse(_RUNNER_PATH)
        or not raw
        or len(raw) > _MAX_RUNNER_BYTES
        or not _approved_digest(_APPROVED_RUNNER_SHA256)
        or not secrets.compare_digest(
            hashlib.sha256(raw).hexdigest(),
            _APPROVED_RUNNER_SHA256,
        )
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_RUNNER_UNAUTHORIZED")
    module = ModuleType(_RUNNER_MODULE_NAME)
    module.__file__ = str(_RUNNER_PATH)
    module.__package__ = ""
    sys.modules[_RUNNER_MODULE_NAME] = module
    try:
        exec(compile(raw, str(_RUNNER_PATH), "exec"), module.__dict__)  # noqa: S102
    except Exception as exc:
        sys.modules.pop(_RUNNER_MODULE_NAME, None)
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_RUNNER_IMPORT_FAILED") from exc
    return module


def _load_operator_material() -> _OperatorMaterial:
    from idis.runtime.profile import (  # type: ignore[import-untyped]
        StrictProfileError,
        load_strict_runtime_profile,
    )
    from idis.runtime.secure_files import (  # type: ignore[import-untyped]
        SecureFileError,
        secure_read_integrity_file,
    )
    from idis.tools.governance_signing import (  # type: ignore[import-untyped]
        GovernanceSigningError,
        _read_secure_private_key,
    )

    try:
        profile = load_strict_runtime_profile(
            command="governance",
            process_env=os.environ,
        )
    except StrictProfileError as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_PROFILE_INVALID") from exc
    signer_id = str(profile.values.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", ""))
    if not secrets.compare_digest(signer_id, _APPROVED_SIGNER_ID):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_SIGNER_INVALID")
    try:
        public_path = Path(str(profile.values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"]))
        public_raw = secure_read_integrity_file(
            str(public_path),
            max_bytes=_MAX_PUBLIC_KEY_BYTES,
            require_readonly=True,
            require_secure_parent=True,
        )
        if not secrets.compare_digest(
            hashlib.sha256(public_raw).hexdigest(),
            profile.governance_public_key_sha256,
        ):
            raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_KEY_INVALID")
        private_key = serialization.load_pem_private_key(
            _read_secure_private_key(_PRIVATE_KEY_PATH),
            password=None,
        )
        public_key = serialization.load_pem_public_key(public_raw)
    except SignedCensusCliError:
        raise
    except (
        GovernanceSigningError,
        SecureFileError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_KEY_INVALID") from exc
    if not isinstance(private_key, Ed25519PrivateKey) or not isinstance(
        public_key,
        Ed25519PublicKey,
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_KEY_INVALID")
    if not secrets.compare_digest(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_KEY_MISMATCH")
    return _OperatorMaterial(
        signer_id=signer_id,
        private_key=private_key,
        public_key_raw=public_raw,
    )


def _load_candidate(runner: ModuleType) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = _ARTIFACT_PATH.read_bytes()
        artifact = runner._decode_json(raw)
        runner._validate_v17_candidate(artifact)
    except Exception as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_ARTIFACT_INVALID") from exc
    if not _approved_digest(_CANDIDATE_ARTIFACT_SHA256) or not secrets.compare_digest(
        hashlib.sha256(raw).hexdigest(),
        _CANDIDATE_ARTIFACT_SHA256,
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_ARTIFACT_INVALID")
    if not _approved_digest(
        _APPROVED_MATERIALIZATION_SOURCE_MANIFEST_SHA256
    ) or not secrets.compare_digest(
        runner.materialization_source_manifest_sha256(),
        _APPROVED_MATERIALIZATION_SOURCE_MANIFEST_SHA256,
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_ARTIFACT_INVALID")
    return raw, dict(artifact)


def _validate_output(path: Path) -> Path:
    if (
        not path.is_absolute()
        or path.suffix.casefold() != ".json"
        or path.exists()
        or path.is_symlink()
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    try:
        root = _REPORT_ROOT.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_PATH_INVALID") from exc
    if (
        not root.is_dir()
        or _is_link_or_reparse(root)
        or (parent != root and root not in parent.parents)
    ):
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
    current = parent
    while True:
        if not current.is_dir() or _is_link_or_reparse(current):
            raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        if current == root:
            break
        if current.parent == current:
            raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_PATH_INVALID")
        current = current.parent
    return parent / path.name


def _write_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
    except FileExistsError as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_EXISTS") from exc
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(OSError):
            path.unlink(missing_ok=True)
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_OUTPUT_WRITE_FAILED") from exc


def _execute(output_argument: Path) -> dict[str, Any]:
    output = _validate_output(output_argument)
    runner = _load_approved_runner()
    artifact_raw, artifact = _load_candidate(runner)
    material = _load_operator_material()
    try:
        result = runner.run_pre_fixture_census(
            artifact_path=_ARTIFACT_PATH,
            base_schema_revision=runner.BASE_SCHEMA_REVISION,
            harness_identity=runner.HARNESS_IDENTITY,
            ordered_authority_target_tuple=runner.artifact_targets(artifact),
            ordered_trigger_contract_coordinate_tuple=(runner.artifact_trigger_contract(artifact)),
            production_constructor_spec_tuple=runner._PRODUCTION_CONSTRUCTOR_SPECS,
        )
        receipt = runner.build_and_verify_signed_receipt(
            signer_id=material.signer_id,
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=result,
            private_key=material.private_key,
            trusted_public_key_raw=material.public_key_raw,
        )
        rendered = (
            json.dumps(
                receipt,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        runner.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=material.public_key_raw,
        )
    except Exception as exc:
        raise SignedCensusCliError("TASK7_V17_SIGNED_CENSUS_EXECUTION_FAILED") from exc
    _write_exclusive(output, rendered)
    return {
        "schema_version": _EVENT_SCHEMA,
        "result": "PASS",
        "receipt_sha256": hashlib.sha256(rendered).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPORT_ROOT / _DEFAULT_OUTPUT_NAME,
    )
    return parser


def _emit(value: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(None if argv is None else list(argv))
        event = _execute(args.output)
    except SignedCensusCliError as exc:
        _emit(
            {
                "schema_version": _EVENT_SCHEMA,
                "result": "FAILED",
                "reason_code": exc.code,
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "schema_version": _EVENT_SCHEMA,
                "result": "FAILED",
                "reason_code": "TASK7_V17_SIGNED_CENSUS_FAILED",
            }
        )
        return 2
    _emit(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
