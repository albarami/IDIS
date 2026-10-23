"""Run the approved v16 PostgreSQL census once and issue its signed receipt.

This operator-only CLI deliberately has no switches for the candidate, signer,
trust root, or signing key.  Those authorities are fixed by the reviewed
contract and the ACL-verified strict profile.  Output is an exclusive durable
file under the Task 7 local-report root; on Windows ``fsync`` flushes the file
handle, and this tool makes no unsupported parent-directory fsync claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

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

from idis.runtime.profile import StrictProfileError, load_strict_runtime_profile  # noqa: E402
from idis.runtime.secure_files import (  # noqa: E402
    SecureFileError,
    secure_read_integrity_file,
)
from idis.tools.governance_signing import (  # noqa: E402
    GovernanceSigningError,
    _read_secure_private_key,
)

_RUNNER_PATH: Final = _REPO_ROOT / "scripts" / "run_task7_v16_current_guard_census.py"
_ARTIFACT_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_REPORT_ROOT: Final = _REPO_ROOT / ".local_reports" / "slice100"
_DEFAULT_OUTPUT_NAME: Final = "task7_v16_current_guard_postgres_census_receipt.json"
_PRIVATE_KEY_PATH: Final = Path(r"C:\IDIS-secrets\keys\governance-signing-private.pem")
_APPROVED_RUNNER_SHA256: Final = "6974b148b4e7f8b7fcd85ab476283f117c246e39058ff0acc920766180747e43"
_CANDIDATE_ARTIFACT_SHA256: Final = (
    "dd7b2585ff01909765342549bf60b4ba32d18cec398c8b002a77c55d0f92ce74"
)
_APPROVED_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EVENT_SCHEMA: Final = "task7-v16-signed-census-cli-result-v1"
_RUNNER_MODULE_NAME: Final = "_task7_v16_signed_census_approved_runner"
_MAX_PUBLIC_KEY_BYTES: Final = 64 * 1024
_MAX_RUNNER_BYTES: Final = 1024 * 1024


class SignedCensusCliError(RuntimeError):
    """Secret-safe operator failure carrying one stable code."""

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
    def error(self, _message: str) -> None:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_ARGUMENTS_INVALID")


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


def _load_approved_runner() -> ModuleType:
    """Hash the runner bytes before compiling or executing any of them."""

    try:
        if (
            not _RUNNER_PATH.is_absolute()
            or not _RUNNER_PATH.is_file()
            or _is_link_or_reparse(_RUNNER_PATH)
        ):
            raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_RUNNER_UNAUTHORIZED")
        raw = _RUNNER_PATH.read_bytes()
    except SignedCensusCliError:
        raise
    except OSError as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_RUNNER_UNAUTHORIZED") from exc
    if (
        not raw
        or len(raw) > _MAX_RUNNER_BYTES
        or not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), _APPROVED_RUNNER_SHA256)
    ):
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_RUNNER_UNAUTHORIZED")

    module = ModuleType(_RUNNER_MODULE_NAME)
    module.__file__ = str(_RUNNER_PATH)
    module.__package__ = ""
    sys.modules[_RUNNER_MODULE_NAME] = module
    try:
        # Compiling the already-hashed bytes avoids a second path read and its
        # attendant check/use race.
        code = compile(raw, str(_RUNNER_PATH), "exec")
        exec(code, module.__dict__)  # noqa: S102 - exact hash-pinned source bytes
    except Exception as exc:
        sys.modules.pop(_RUNNER_MODULE_NAME, None)
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_RUNNER_IMPORT_FAILED") from exc
    return module


def _load_operator_material() -> _OperatorMaterial:
    try:
        # Receipt signing is a governance action, not a migration.  In
        # particular, it must remain runnable before the separately timed
        # erasure-executor credential is provisioned for migration 0038.
        profile = load_strict_runtime_profile(command="governance", process_env=os.environ)
    except StrictProfileError as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_PROFILE_INVALID") from exc

    signer_id = str(profile.values.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", ""))
    if not secrets.compare_digest(signer_id, _APPROVED_SIGNER_ID):
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_SIGNER_INVALID")

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
            raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_KEY_INVALID")
        private_raw = _read_secure_private_key(_PRIVATE_KEY_PATH)
        private_key = serialization.load_pem_private_key(private_raw, password=None)
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
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_KEY_INVALID") from exc

    if not isinstance(private_key, Ed25519PrivateKey) or not isinstance(
        public_key, Ed25519PublicKey
    ):
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_KEY_INVALID")
    derived_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trusted_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if not secrets.compare_digest(derived_public, trusted_public):
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_KEY_MISMATCH")
    return _OperatorMaterial(
        signer_id=signer_id,
        private_key=private_key,
        public_key_raw=public_raw,
    )


def _validate_output_path(path: Path) -> Path:
    invalid_code = "TASK7_V16_SIGNED_CENSUS_OUTPUT_PATH_INVALID"
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise SignedCensusCliError(invalid_code)
    if path.exists():
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_OUTPUT_EXISTS")
    if path.is_symlink():
        raise SignedCensusCliError(invalid_code)

    try:
        report_root = _REPORT_ROOT.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SignedCensusCliError(invalid_code) from exc
    if (
        not report_root.is_dir()
        or _is_link_or_reparse(report_root)
        or parent != report_root
        and report_root not in parent.parents
    ):
        raise SignedCensusCliError(invalid_code)

    current = path.parent
    while True:
        if not current.is_dir() or _is_link_or_reparse(current):
            raise SignedCensusCliError(invalid_code)
        if current.resolve(strict=True) == report_root:
            break
        if current.parent == current:
            raise SignedCensusCliError(invalid_code)
        current = current.parent
    return parent / path.name


def _load_candidate(runner: ModuleType) -> tuple[Path, bytes, dict[str, Any]]:
    try:
        if (
            not _ARTIFACT_PATH.is_absolute()
            or not _ARTIFACT_PATH.is_file()
            or _is_link_or_reparse(_ARTIFACT_PATH)
        ):
            raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_ARTIFACT_INVALID")
        artifact_raw, artifact = runner._decode_artifact(_ARTIFACT_PATH)
    except SignedCensusCliError:
        raise
    except Exception as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_ARTIFACT_INVALID") from exc
    try:
        metadata = artifact["metadata"]
        valid = (
            secrets.compare_digest(
                hashlib.sha256(artifact_raw).hexdigest(),
                _CANDIDATE_ARTIFACT_SHA256,
            )
            and metadata["artifact_schema"] == "full-strict-v16-reviewed-preimage-v1"
            and metadata["pipeline_contract_version"] == "full-strict-v16"
            and metadata["append_frozen"] is False
        )
    except (KeyError, TypeError) as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_ARTIFACT_INVALID") from exc
    if not valid:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_ARTIFACT_INVALID")
    return _ARTIFACT_PATH.resolve(), artifact_raw, dict(artifact)


def _run_census_once(
    *,
    runner: ModuleType,
    artifact_path: Path,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    result = runner.run_pre_fixture_census(
        artifact_path=artifact_path,
        base_schema_revision=runner.BASE_SCHEMA_REVISION,
        harness_identity=runner.HARNESS_IDENTITY,
        ordered_authority_target_tuple=runner._artifact_targets(artifact),
        ordered_trigger_contract_coordinate_tuple=runner._artifact_trigger_contract(artifact),
        production_constructor_spec_tuple=runner._PRODUCTION_CONSTRUCTOR_SPECS,
    )
    if not isinstance(result, Mapping):
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_EXECUTION_FAILED")
    return dict(result)


def _render_receipt(receipt: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(receipt),
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_RECEIPT_INVALID") from exc


def _write_durable_exclusive(path: Path, content: bytes) -> None:
    descriptor = -1
    stage: Path | None = None
    linked = False
    committed = False
    failure: tuple[str, OSError] | None = None
    try:
        descriptor, stage_name = tempfile.mkstemp(
            prefix=f".{path.name}.task7-v16-signed-census-",
            suffix=".stage",
            dir=path.parent,
        )
        stage = Path(stage_name)
        if os.name != "nt":
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
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
        try:
            os.link(stage, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_OUTPUT_EXISTS") from exc
        linked = True
        if os.name != "nt":
            parent_descriptor = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        committed = True
        try:
            stage.unlink()
            stage = None
        except OSError:
            # The fully flushed hard link is the commit point.  A scanner may
            # transiently hold the non-authoritative stage open on Windows;
            # that must not turn a complete final receipt into a reported
            # failure or trigger deletion of the committed evidence.
            pass
    except FileExistsError as exc:
        failure = ("TASK7_V16_SIGNED_CENSUS_OUTPUT_EXISTS", exc)
    except SignedCensusCliError:
        raise
    except OSError as exc:
        failure = ("TASK7_V16_SIGNED_CENSUS_OUTPUT_WRITE_FAILED", exc)
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        if stage is not None:
            with suppress(OSError):
                stage.unlink()
        if failure is not None and linked and not committed:
            try:
                path.unlink()
            except OSError as cleanup_exc:
                raise SignedCensusCliError(
                    "TASK7_V16_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED"
                ) from cleanup_exc
    if failure is not None:
        code, cause = failure
        raise SignedCensusCliError(code) from cause


def _execute(output_argument: Path) -> dict[str, Any]:
    output = _validate_output_path(output_argument)
    material = _load_operator_material()
    runner = _load_approved_runner()
    artifact_path, artifact_raw, artifact = _load_candidate(runner)

    try:
        # This is the only census invocation in the production path.
        result = _run_census_once(
            runner=runner,
            artifact_path=artifact_path,
            artifact=artifact,
        )
    except SignedCensusCliError:
        raise
    except Exception as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_EXECUTION_FAILED") from exc

    try:
        receipt = runner.build_and_verify_signed_receipt(
            signer_id=material.signer_id,
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=result,
            private_key=material.private_key,
            trusted_public_key_raw=material.public_key_raw,
        )
        rendered = _render_receipt(receipt)
        runner.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=material.public_key_raw,
        )
    except SignedCensusCliError:
        raise
    except Exception as exc:
        raise SignedCensusCliError("TASK7_V16_SIGNED_CENSUS_RECEIPT_INVALID") from exc

    _write_durable_exclusive(output, rendered)
    return {
        "receipt_sha256": hashlib.sha256(rendered).hexdigest(),
        "result": "PASS",
        "schema_version": _EVENT_SCHEMA,
    }


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPORT_ROOT / _DEFAULT_OUTPUT_NAME,
    )
    return parser


def _emit(event: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            dict(event),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
        event = _execute(args.output)
    except SignedCensusCliError as exc:
        _emit(
            {
                "reason_code": exc.code,
                "result": "FAILED",
                "schema_version": _EVENT_SCHEMA,
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "reason_code": "TASK7_V16_SIGNED_CENSUS_FAILED",
                "result": "FAILED",
                "schema_version": _EVENT_SCHEMA,
            }
        )
        return 2
    _emit(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
