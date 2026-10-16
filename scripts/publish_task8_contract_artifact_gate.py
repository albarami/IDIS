"""Process-exit recoverable, no-clobber publication for the Task-8 artifact gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Never, cast

_PREEXISTING = (
    "fdd_formula_policy_fixture",
    "fdd_formula_policy_artifact",
    "fdd_formula_policy_review_map",
)
_ORDER = (
    "full_strict_v24_fixture",
    "full_strict_v24_artifact",
)
_JOURNAL_SCHEMA = "task8-contract-artifact-gate-journal-v2"
_LOCK_SCHEMA = "task8-contract-artifact-gate-lock-v2"
_FIXTURE_GENERATOR = Path(__file__).with_name("generate_task8_contract_fixtures.py")
_V24_MATERIALIZER = Path(__file__).with_name("materialize_full_strict_v24_preimage.py")
_V24_INDEPENDENT_VERIFIER = Path(__file__).with_name(
    "verify_full_strict_v24_fixture_independently.py"
)
_V21_ARTIFACT = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / ("full_strict_v21_reviewed_preimage_v1.json")
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_STABLE_CODE_RE = re.compile(r"TASK8_[A-Z0-9_]+")

FixtureDeriver = Callable[[bytes, bytes, bytes], tuple[bytes, bytes]]


class Task8GatePublicationError(RuntimeError):
    """Raised with a stable code when safe publication cannot complete."""


def _fail(code: str) -> Never:
    raise Task8GatePublicationError(code)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _render(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _load_object(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Task8GatePublicationError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return cast(dict[str, Any], value)


def _validate_frozen_artifact(raw: bytes, *, kind: str) -> bytes:
    value = _load_object(raw, "TASK8_GATE_INPUT_INVALID")
    metadata = value.get("metadata")
    if (
        not isinstance(metadata, dict)
        or metadata.get("append_frozen") is not True
        or metadata.get("activation_status") != "INACTIVE"
        or _render(value) != raw
    ):
        _fail("TASK8_GATE_INPUT_INVALID")
    if kind == "policy":
        if metadata.get("artifact_schema") != "fdd-formula-policy-v2":
            _fail("TASK8_GATE_INPUT_INVALID")
    elif (
        metadata.get("artifact_schema") != "full-strict-v24-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v24"
    ):
        _fail("TASK8_GATE_INPUT_INVALID")
    return bytes(raw)


def _load_exact_module(path: Path, name: str, code: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )
    if spec is None or spec.loader is None:
        _fail(code)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise Task8GatePublicationError(code) from exc
    return module


def _load_fixture_generator() -> ModuleType:
    return _load_exact_module(
        _FIXTURE_GENERATOR,
        "_task8_gate_fixture_generator",
        "TASK8_GATE_FIXTURE_DERIVER_INVALID",
    )


def _load_v24_materializer() -> ModuleType:
    return _load_exact_module(
        _V24_MATERIALIZER,
        "_task8_gate_v24_materializer",
        "TASK8_GATE_INPUT_INVALID",
    )


def _load_v24_independent_verifier() -> ModuleType:
    return _load_exact_module(
        _V24_INDEPENDENT_VERIFIER,
        "_task8_gate_v24_independent_verifier",
        "TASK8_GATE_FIXTURE_DERIVER_INVALID",
    )


def _external_stable_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if not isinstance(code, str):
        code = str(exc).split(":", 1)[0]
    return code if _STABLE_CODE_RE.fullmatch(code) else ""


@lru_cache(maxsize=4)
def _derive_expected_fixture_bytes(
    policy_artifact_raw: bytes,
    v24_artifact_raw: bytes,
    policy_review_map_raw: bytes,
) -> tuple[bytes, bytes]:
    materializer = _load_v24_materializer()
    try:
        parent_artifact_raw = _V21_ARTIFACT.read_bytes()
        materializer.validate_frozen_artifact(
            v24_artifact_raw,
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=policy_review_map_raw,
        )
    except Exception as exc:
        if _external_stable_code(exc) == "TASK8_V24_REVIEWER_MAP_BYTES_DRIFT":
            raise Task8GatePublicationError("TASK8_GATE_REVIEW_MAP_INVALID") from exc
        raise Task8GatePublicationError("TASK8_GATE_INPUT_INVALID") from exc

    generator = _load_fixture_generator()
    try:
        policy_fixture, v24_fixture = generator.derive_task8_fixtures(
            policy_artifact_raw=policy_artifact_raw,
            v24_artifact_raw=v24_artifact_raw,
        )
    except Exception as exc:
        raise Task8GatePublicationError("TASK8_GATE_FIXTURE_INVALID") from exc
    if not isinstance(policy_fixture, dict) or not isinstance(v24_fixture, dict):
        _fail("TASK8_GATE_FIXTURE_INVALID")
    policy_fixture_raw = _render(policy_fixture)
    v24_fixture_raw = _render(v24_fixture)

    verifier = _load_v24_independent_verifier()
    try:
        verifier.verify_fixture(
            artifact_raw=v24_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=policy_review_map_raw,
            fixture_raw=v24_fixture_raw,
        )
    except Exception as exc:
        if "REVIEWER_MAP" in _external_stable_code(exc):
            raise Task8GatePublicationError("TASK8_GATE_REVIEW_MAP_INVALID") from exc
        raise Task8GatePublicationError("TASK8_GATE_FIXTURE_INVALID") from exc
    return policy_fixture_raw, v24_fixture_raw


def _validate_exact_fixtures_and_review_map(
    *,
    policy_artifact_raw: bytes,
    v24_artifact_raw: bytes,
    policy_fixture_raw: bytes,
    v24_fixture_raw: bytes,
    policy_review_map_raw: bytes,
    fixture_deriver: FixtureDeriver,
) -> None:
    try:
        expected = fixture_deriver(
            policy_artifact_raw,
            v24_artifact_raw,
            policy_review_map_raw,
        )
    except Task8GatePublicationError:
        raise
    except Exception as exc:
        raise Task8GatePublicationError("TASK8_GATE_FIXTURE_INVALID") from exc
    if (
        not isinstance(expected, tuple)
        or len(expected) != 2
        or not all(isinstance(raw, bytes) for raw in expected)
    ):
        _fail("TASK8_GATE_FIXTURE_INVALID")
    expected_policy_fixture, expected_v24_fixture = expected
    if policy_fixture_raw != expected_policy_fixture or v24_fixture_raw != expected_v24_fixture:
        _fail("TASK8_GATE_FIXTURE_INVALID")
    policy_fixture = _load_object(
        expected_policy_fixture,
        "TASK8_GATE_FIXTURE_INVALID",
    )
    if policy_fixture.get("review_map_sha256") != _sha256(policy_review_map_raw):
        _fail("TASK8_GATE_REVIEW_MAP_INVALID")


def _journal_path(v24_artifact_path: Path) -> Path:
    path = Path(v24_artifact_path)
    return path.with_name(f".{path.name}.task8-gate.journal.json")


def _lock_path(v24_artifact_path: Path) -> Path:
    path = Path(v24_artifact_path)
    return path.with_name(f".{path.name}.task8-gate.lock")


def _journal_temp_path(journal_path: Path, owner_token: str) -> Path:
    return journal_path.with_name(f"{journal_path.name}.new.{owner_token}")


def _lock_takeover_path(lock_path: Path, owner_token: str) -> Path:
    return lock_path.with_name(f"{lock_path.name}.takeover.{owner_token}")


def _stage_path(target: Path, owner_token: str, ordinal: int) -> Path:
    return target.with_name(f".{target.name}.task8-stage.{owner_token}.{ordinal}")


def _has_reparse_attribute(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _regular_status(path: Path, code: str) -> os.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise Task8GatePublicationError(code) from exc
    if path.is_symlink() or _has_reparse_attribute(path) or not stat.S_ISREG(status.st_mode):
        _fail(code)
    return status


def _validate_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    if _exists(candidate):
        _regular_status(candidate, "TASK8_GATE_PATH_INVALID")
    parent = candidate.parent
    if not parent.is_dir():
        _fail("TASK8_GATE_PATH_INVALID")
    current = parent
    while True:
        if current.is_symlink() or _has_reparse_attribute(current):
            _fail("TASK8_GATE_PATH_INVALID")
        if current.parent == current:
            break
        current = current.parent
    return candidate.resolve(strict=False)


def _validate_distinct_paths(paths: Mapping[str, Path]) -> dict[str, Path]:
    validated = {name: _validate_path(path) for name, path in paths.items()}
    normalized = [os.path.normcase(str(path)) for path in validated.values()]
    if len(normalized) != len(set(normalized)):
        _fail("TASK8_GATE_PATH_INVALID")
    return validated


def _validate_preexisting_exact(
    expected: Mapping[str, bytes],
    paths: Mapping[str, Path],
) -> None:
    """Admit the immutable policy-v2 prerequisite set before any gate write."""

    for name in _PREEXISTING:
        try:
            _regular_status(paths[name], "TASK8_GATE_PREREQUISITE_INVALID")
            observed = paths[name].read_bytes()
        except (KeyError, OSError) as exc:
            raise Task8GatePublicationError("TASK8_GATE_PREREQUISITE_INVALID") from exc
        if observed != expected[name]:
            _fail("TASK8_GATE_PREREQUISITE_INVALID")


def _path_sha256(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve(strict=False)))
    return _sha256(normalized.encode("utf-8"))


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Best-effort metadata flush; this does not claim power-loss durability."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_exclusive(
    path: Path,
    raw: bytes,
    *,
    code: str = "TASK8_GATE_LOCKED",
) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise Task8GatePublicationError(code) from exc
    except OSError as exc:
        raise Task8GatePublicationError("TASK8_GATE_PUBLICATION_INTERRUPTED") from exc
    _fsync_directory(path.parent)


def _file_identity(path: Path, code: str) -> tuple[int, int, int]:
    status = _regular_status(path, code)
    return int(status.st_dev), int(status.st_ino), int(status.st_nlink)


def _same_file(left: Path, right: Path) -> bool:
    left_identity = _file_identity(left, "TASK8_GATE_FILE_IDENTITY_ALIAS")
    right_identity = _file_identity(right, "TASK8_GATE_FILE_IDENTITY_ALIAS")
    return left_identity[:2] == right_identity[:2]


def _process_exit_checkpoint(_name: str, _ordinal: int | None = None) -> None:
    """Private process-exit test seam; production never enables a checkpoint."""


OwnerStatus = Literal["LIVE", "DEAD_OR_REUSED", "UNKNOWN"]


def _windows_process_identity(pid: int) -> tuple[OwnerStatus, str | None]:
    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:
            return "DEAD_OR_REUSED", None
        return "UNKNOWN", None
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return "UNKNOWN", None
        if exit_code.value != 259:
            return "DEAD_OR_REUSED", None
        creation = FileTime()
        exited = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return "UNKNOWN", None
        creation_ticks = (int(creation.high) << 32) | int(creation.low)
        return "LIVE", _sha256(f"windows-filetime:{creation_ticks}".encode("ascii"))
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_identity(pid: int) -> tuple[OwnerStatus, str | None]:
    stat_path = Path("/proc") / str(pid) / "stat"
    boot_path = Path("/proc/sys/kernel/random/boot_id")
    try:
        stat_raw = stat_path.read_text(encoding="ascii")
        boot_id = boot_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return "DEAD_OR_REUSED", None
    except (OSError, UnicodeError):
        return "UNKNOWN", None
    closing = stat_raw.rfind(")")
    fields = stat_raw[closing + 2 :].split() if closing >= 0 else []
    if len(fields) < 20 or not boot_id:
        return "UNKNOWN", None
    return "LIVE", _sha256(f"linux:{boot_id}:{fields[19]}".encode("ascii"))


def _query_process_identity(pid: int) -> tuple[OwnerStatus, str | None]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "DEAD_OR_REUSED", None
    if os.name == "nt":
        return _windows_process_identity(pid)
    return _posix_process_identity(pid)


def _process_start_identity(pid: int) -> str | None:
    status, identity = _query_process_identity(pid)
    return identity if status == "LIVE" else None


def _owner_status(pid: int, recorded_start_identity: str) -> OwnerStatus:
    status, current_identity = _query_process_identity(pid)
    if status != "LIVE":
        return status
    return "LIVE" if current_identity == recorded_start_identity else "DEAD_OR_REUSED"


def _lock_value(*, intent_sha256: str, owner_token: str) -> dict[str, Any]:
    start_identity = _process_start_identity(os.getpid())
    if start_identity is None:
        _fail("TASK8_GATE_OWNER_UNKNOWN")
    return {
        "schema_version": _LOCK_SCHEMA,
        "intent_sha256": intent_sha256,
        "owner_token": owner_token,
        "owner_pid": os.getpid(),
        "owner_start_identity": start_identity,
    }


def _load_lock(path: Path) -> tuple[bytes, dict[str, Any]]:
    _regular_status(path, "TASK8_GATE_LOCK_INVALID")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Task8GatePublicationError("TASK8_GATE_LOCK_INVALID") from exc
    required = {
        "schema_version",
        "intent_sha256",
        "owner_token",
        "owner_pid",
        "owner_start_identity",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != _LOCK_SCHEMA
        or not isinstance(value.get("owner_pid"), int)
        or isinstance(value.get("owner_pid"), bool)
        or value["owner_pid"] <= 0
        or not isinstance(value.get("intent_sha256"), str)
        or _SHA256_RE.fullmatch(value["intent_sha256"]) is None
        or not isinstance(value.get("owner_token"), str)
        or _SHA256_RE.fullmatch(value["owner_token"]) is None
        or not isinstance(value.get("owner_start_identity"), str)
        or _SHA256_RE.fullmatch(value["owner_start_identity"]) is None
        or _render(value) != raw
    ):
        _fail("TASK8_GATE_LOCK_INVALID")
    return raw, cast(dict[str, Any], value)


def _load_exact_raw(path: Path, expected_raw: bytes, code: str) -> None:
    _regular_status(path, code)
    try:
        observed = path.read_bytes()
    except OSError as exc:
        raise Task8GatePublicationError(code) from exc
    if observed != expected_raw:
        _fail(code)


def _unlink_verified_sidecar(
    path: Path,
    *,
    expected_raw: bytes,
    owner_token: str,
    code: str,
) -> None:
    _load_exact_raw(path, expected_raw, code)
    token_bound = owner_token in path.name
    if not token_bound:
        try:
            value = json.loads(expected_raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            value = None
        token_bound = isinstance(value, dict) and value.get("owner_token") == owner_token
    if not token_bound:
        _fail(code)
    # Re-read immediately before unlink so a changed token or byte sequence is
    # never deleted under a stale recovery decision.
    try:
        if path.read_bytes() != expected_raw:
            _fail(code)
        path.unlink()
    except Task8GatePublicationError:
        raise
    except OSError as exc:
        raise Task8GatePublicationError(code) from exc
    _fsync_directory(path.parent)


def _replace_lock_owner_atomically(
    lock_path: Path,
    *,
    expected_lock_raw: bytes,
    replacement_lock_raw: bytes,
    intent_sha256: str,
    owner_token: str,
    checkpoint: str,
) -> None:
    """Replace a dead owner's lock without an observable unlocked interval."""
    takeover_path = _lock_takeover_path(lock_path, owner_token)
    if _exists(takeover_path):
        takeover_raw, takeover_value = _load_lock(takeover_path)
        if (
            takeover_value["intent_sha256"] != intent_sha256
            or takeover_value["owner_token"] != owner_token
        ):
            _fail("TASK8_GATE_SIDECAR_COLLISION")
        takeover_status = _owner_status(
            cast(int, takeover_value["owner_pid"]),
            cast(str, takeover_value["owner_start_identity"]),
        )
        if takeover_status == "LIVE":
            _fail("TASK8_GATE_LOCKED")
        if takeover_status == "UNKNOWN":
            _fail("TASK8_GATE_OWNER_UNKNOWN")
        _unlink_verified_sidecar(
            takeover_path,
            expected_raw=takeover_raw,
            owner_token=owner_token,
            code="TASK8_GATE_LOCK_CHANGED",
        )
    _write_exclusive(
        takeover_path,
        replacement_lock_raw,
        code="TASK8_GATE_LOCKED",
    )

    def clean_unreplaced_takeover() -> None:
        if not _exists(takeover_path):
            return
        with suppress(Exception):
            _unlink_verified_sidecar(
                takeover_path,
                expected_raw=replacement_lock_raw,
                owner_token=owner_token,
                code="TASK8_GATE_CLEANUP_FAILED",
            )

    replaced = False
    try:
        _process_exit_checkpoint(checkpoint)
        _load_exact_raw(lock_path, expected_lock_raw, "TASK8_GATE_LOCK_CHANGED")
        _load_exact_raw(
            takeover_path,
            replacement_lock_raw,
            "TASK8_GATE_LOCK_CHANGED",
        )
        os.replace(takeover_path, lock_path)
        replaced = True
    except OSError as exc:
        if not replaced:
            clean_unreplaced_takeover()
        raise Task8GatePublicationError("TASK8_GATE_PUBLICATION_INTERRUPTED") from exc
    except Exception:
        if not replaced:
            clean_unreplaced_takeover()
        raise
    _fsync_directory(lock_path.parent)
    _load_exact_raw(lock_path, replacement_lock_raw, "TASK8_GATE_LOCK_CHANGED")


def _unlink_exact_target(path: Path, expected_raw: bytes, code: str) -> None:
    _load_exact_raw(path, expected_raw, code)
    try:
        path.unlink()
    except OSError as exc:
        raise Task8GatePublicationError(code) from exc
    _fsync_directory(path.parent)


def _intent_value(
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
    *,
    preexisting: Mapping[str, bytes],
    preexisting_paths: Mapping[str, Path],
) -> dict[str, Any]:
    return {
        "schema_version": "task8-contract-artifact-gate-intent-v2",
        "preexisting_verified": list(_PREEXISTING),
        "created_publication_order": list(_ORDER),
        "commit_point": "full_strict_v24_artifact",
        "output_sha256": {name: _sha256(outputs[name]) for name in _ORDER},
        "preexisting_sha256": {name: _sha256(preexisting[name]) for name in _PREEXISTING},
        "target_path_sha256": {name: _path_sha256(targets[name]) for name in _ORDER},
        "preexisting_path_sha256": {
            name: _path_sha256(preexisting_paths[name]) for name in _PREEXISTING
        },
        "activation_status": "INACTIVE",
    }


def _journal_identity(intent: Mapping[str, Any], *, owner_token: str) -> dict[str, Any]:
    return {
        "schema_version": _JOURNAL_SCHEMA,
        "intent_sha256": _sha256(_render(intent)),
        "owner_token": owner_token,
        "preexisting_verified": list(_PREEXISTING),
        "created_publication_order": list(_ORDER),
        "output_sha256": dict(cast(Mapping[str, str], intent["output_sha256"])),
        "preexisting_sha256": dict(cast(Mapping[str, str], intent["preexisting_sha256"])),
        "target_path_sha256": dict(cast(Mapping[str, str], intent["target_path_sha256"])),
        "preexisting_path_sha256": dict(cast(Mapping[str, str], intent["preexisting_path_sha256"])),
        "activation_status": "INACTIVE",
    }


def _namespace_candidates(
    targets: Mapping[str, Path],
    journal_path: Path,
    lock_path: Path,
) -> set[Path]:
    prefixes_by_parent: dict[Path, set[str]] = {}
    prefixes_by_parent.setdefault(journal_path.parent, set()).add(f"{journal_path.name}.new.")
    prefixes_by_parent.setdefault(lock_path.parent, set()).add(f"{lock_path.name}.takeover.")
    for target in targets.values():
        prefixes_by_parent.setdefault(target.parent, set()).add(f".{target.name}.task8-stage.")
    observed: set[Path] = set()
    try:
        for parent, prefixes in prefixes_by_parent.items():
            for child in parent.iterdir():
                if any(child.name.startswith(prefix) for prefix in prefixes):
                    observed.add(child.resolve(strict=False))
    except OSError as exc:
        raise Task8GatePublicationError("TASK8_GATE_PATH_INVALID") from exc
    return observed


def _assert_identity_contract(
    paths: set[Path],
    *,
    allowed_pairs: set[frozenset[Path]],
) -> None:
    groups: dict[tuple[int, int], list[tuple[Path, int]]] = {}
    for path in paths:
        if not _exists(path):
            continue
        device, inode, links = _file_identity(path, "TASK8_GATE_FILE_IDENTITY_ALIAS")
        groups.setdefault((device, inode), []).append((path, links))
    for members in groups.values():
        member_paths = frozenset(path for path, _links in members)
        if len(members) == 1:
            if members[0][1] != 1:
                _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
            continue
        if member_paths not in allowed_pairs or len(members) != 2:
            _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
        if any(links != 2 for _path, links in members):
            _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")


def _preflight_sidecars(
    *,
    targets: Mapping[str, Path],
    lock_path: Path,
    journal_path: Path,
    owner_token: str | None,
    additional_paths: set[Path],
) -> tuple[Path | None, Path | None, dict[int, Path], set[Path]]:
    observed_namespace = _namespace_candidates(targets, journal_path, lock_path)
    expected_temp = (
        _journal_temp_path(journal_path, owner_token) if owner_token is not None else None
    )
    expected_takeover = (
        _lock_takeover_path(lock_path, owner_token) if owner_token is not None else None
    )
    expected_stages = (
        {
            ordinal: _stage_path(targets[name], owner_token, ordinal)
            for ordinal, name in enumerate(_ORDER, start=1)
        }
        if owner_token is not None
        else {}
    )
    expected_namespace = set(expected_stages.values())
    if expected_temp is not None:
        expected_namespace.add(expected_temp)
    if expected_takeover is not None:
        expected_namespace.add(expected_takeover)
    if observed_namespace - expected_namespace:
        _fail("TASK8_GATE_SIDECAR_COLLISION")
    all_paths = (
        set(targets.values()) | {lock_path, journal_path} | observed_namespace | additional_paths
    )
    allowed_pairs: set[frozenset[Path]] = set()
    if expected_temp is not None and _exists(expected_temp) and _exists(journal_path):
        allowed_pairs.add(frozenset((expected_temp, journal_path)))
    for ordinal, stage in expected_stages.items():
        target = targets[_ORDER[ordinal - 1]]
        if _exists(stage) and _exists(target):
            allowed_pairs.add(frozenset((stage, target)))
    _assert_identity_contract(all_paths, allowed_pairs=allowed_pairs)
    return expected_temp, expected_takeover, expected_stages, observed_namespace


def _write_journal(path: Path, journal: Mapping[str, Any]) -> None:
    raw = _render(journal)
    owner_token = cast(str, journal["owner_token"])
    temporary = _journal_temp_path(path, owner_token)
    if _exists(path):
        _load_exact_raw(path, raw, "TASK8_GATE_RECOVERY_JOURNAL_MISMATCH")
        if _exists(temporary):
            if not _same_file(path, temporary):
                _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
            _unlink_verified_sidecar(
                temporary,
                expected_raw=raw,
                owner_token=owner_token,
                code="TASK8_GATE_RECOVERY_JOURNAL_INVALID",
            )
        return
    if not _exists(temporary):
        _write_exclusive(
            temporary,
            raw,
            code="TASK8_GATE_SIDECAR_COLLISION",
        )
    else:
        _load_exact_raw(temporary, raw, "TASK8_GATE_RECOVERY_JOURNAL_INVALID")
    _process_exit_checkpoint("after_journal_temp_fsync_before_link")
    try:
        os.link(temporary, path)
    except FileExistsError as exc:
        raise Task8GatePublicationError("TASK8_GATE_LOCKED") from exc
    except OSError as exc:
        raise Task8GatePublicationError("TASK8_GATE_PUBLICATION_INTERRUPTED") from exc
    _fsync_directory(path.parent)
    if not _same_file(temporary, path):
        _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
    _unlink_verified_sidecar(
        temporary,
        expected_raw=raw,
        owner_token=owner_token,
        code="TASK8_GATE_RECOVERY_JOURNAL_INVALID",
    )


def _parse_stage_owner(stage: Path) -> tuple[str, int]:
    try:
        suffix = stage.name.rsplit(".task8-stage.", 1)[1]
        token, ordinal_raw = suffix.rsplit(".", 1)
        ordinal = int(ordinal_raw)
    except (IndexError, ValueError):
        _fail("TASK8_GATE_SIDECAR_COLLISION")
    if _SHA256_RE.fullmatch(token) is None or ordinal not in range(1, len(_ORDER) + 1):
        _fail("TASK8_GATE_SIDECAR_COLLISION")
    return token, ordinal


def _validate_recovery_topology(
    *,
    targets: Mapping[str, Path],
    expected_stages: Mapping[int, Path],
    journal_path: Path,
    expected_temp: Path | None,
) -> bool:
    """Validate the only ordered process-exit states the publisher can create."""
    target_present = [_exists(targets[name]) for name in _ORDER]
    prefix_length = 0
    while prefix_length < len(_ORDER) and target_present[prefix_length]:
        prefix_length += 1
    if any(target_present[prefix_length:]):
        _fail("TASK8_GATE_RECOVERY_TOPOLOGY_INVALID")

    present_stages = [ordinal for ordinal, stage in expected_stages.items() if _exists(stage)]
    journal_exists = _exists(journal_path)
    temp_exists = expected_temp is not None and _exists(expected_temp)

    if not journal_exists and not temp_exists:
        if prefix_length == 0 and not present_stages:
            return False
        if prefix_length == len(_ORDER) and not present_stages:
            return True
        _fail("TASK8_GATE_RECOVERY_TOPOLOGY_INVALID")

    if temp_exists:
        if prefix_length != 0 or present_stages:
            _fail("TASK8_GATE_RECOVERY_TOPOLOGY_INVALID")
        if journal_exists and (
            expected_temp is None or not _same_file(expected_temp, journal_path)
        ):
            _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
        return False

    if len(present_stages) > 1:
        _fail("TASK8_GATE_RECOVERY_TOPOLOGY_INVALID")
    if not present_stages:
        return False

    stage_ordinal = present_stages[0]
    stage = expected_stages[stage_ordinal]
    if stage_ordinal == prefix_length + 1:
        return False
    if stage_ordinal == prefix_length and prefix_length > 0:
        target = targets[_ORDER[stage_ordinal - 1]]
        if _exists(target):
            if _same_file(stage, target):
                return False
            _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
    _fail("TASK8_GATE_RECOVERY_TOPOLOGY_INVALID")


def _publish_no_clobber(
    stage: Path,
    target: Path,
    expected_raw: bytes,
    *,
    on_target_linked: Callable[[], None],
) -> None:
    stage_path = Path(stage)
    target_path = Path(target)
    owner_token, ordinal = _parse_stage_owner(stage_path)
    _load_exact_raw(stage_path, expected_raw, "TASK8_GATE_RECOVERY_STAGE_INVALID")
    if _exists(target_path):
        _load_exact_raw(target_path, expected_raw, "TASK8_GATE_RECOVERY_TARGET_MISMATCH")
        if not _same_file(stage_path, target_path):
            _fail("TASK8_GATE_TARGET_ALREADY_EXISTS")
        _unlink_verified_sidecar(
            stage_path,
            expected_raw=expected_raw,
            owner_token=owner_token,
            code="TASK8_GATE_RECOVERY_STAGE_INVALID",
        )
        return
    _fsync_file(stage_path)
    _process_exit_checkpoint("after_stage_fsync_before_target_link", ordinal)
    _load_exact_raw(stage_path, expected_raw, "TASK8_GATE_RECOVERY_STAGE_INVALID")
    try:
        os.link(stage_path, target_path)
    except FileExistsError as exc:
        raise Task8GatePublicationError("TASK8_GATE_TARGET_ALREADY_EXISTS") from exc
    except OSError as exc:
        raise Task8GatePublicationError("TASK8_GATE_PUBLICATION_INTERRUPTED") from exc
    try:
        on_target_linked()
    except BaseException:
        if _exists(target_path):
            _unlink_exact_target(
                target_path,
                expected_raw,
                "TASK8_GATE_CLEANUP_FAILED",
            )
        raise
    _fsync_directory(target_path.parent)
    _process_exit_checkpoint("after_target_link_before_stage_unlink", ordinal)
    _load_exact_raw(stage_path, expected_raw, "TASK8_GATE_RECOVERY_STAGE_INVALID")
    _load_exact_raw(target_path, expected_raw, "TASK8_GATE_RECOVERY_TARGET_MISMATCH")
    if not _same_file(stage_path, target_path):
        _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
    _unlink_verified_sidecar(
        stage_path,
        expected_raw=expected_raw,
        owner_token=owner_token,
        code="TASK8_GATE_RECOVERY_STAGE_INVALID",
    )


def _validate_final_targets(
    outputs: Mapping[str, bytes],
    targets: Mapping[str, Path],
) -> None:
    for name in _ORDER:
        _load_exact_raw(
            targets[name],
            outputs[name],
            "TASK8_GATE_RECOVERY_TARGET_MISMATCH",
        )
    _assert_identity_contract(set(targets.values()), allowed_pairs=set())


def _target_link_recorder(created_targets: list[str], name: str) -> Callable[[], None]:
    def record_created_target() -> None:
        created_targets.append(name)

    return record_created_target


def _stable_code(exc: Exception) -> str:
    if isinstance(exc, Task8GatePublicationError):
        value = str(exc)
        if _STABLE_CODE_RE.fullmatch(value):
            return value
    return "TASK8_GATE_INTERNAL_FAILURE"


def _raise_clean(code: str) -> Never:
    raise Task8GatePublicationError(code) from None


def _publish_task8_gate_impl(
    *,
    policy_candidate_raw: bytes,
    v24_candidate_raw: bytes,
    policy_fixture_raw: bytes,
    v24_fixture_raw: bytes,
    policy_review_map_raw: bytes,
    policy_artifact_path: Path,
    v24_artifact_path: Path,
    policy_fixture_path: Path,
    v24_fixture_path: Path,
    policy_review_map_path: Path,
    fixture_deriver: FixtureDeriver,
) -> dict[str, Any]:
    policy_frozen_raw = _validate_frozen_artifact(policy_candidate_raw, kind="policy")
    v24_frozen_raw = _validate_frozen_artifact(v24_candidate_raw, kind="v24")
    preexisting = {
        "fdd_formula_policy_fixture": bytes(policy_fixture_raw),
        "fdd_formula_policy_artifact": policy_frozen_raw,
        "fdd_formula_policy_review_map": bytes(policy_review_map_raw),
    }
    outputs = {
        "full_strict_v24_fixture": bytes(v24_fixture_raw),
        "full_strict_v24_artifact": v24_frozen_raw,
    }
    all_paths = _validate_distinct_paths(
        {
            "fdd_formula_policy_fixture": Path(policy_fixture_path),
            "full_strict_v24_fixture": Path(v24_fixture_path),
            "fdd_formula_policy_artifact": Path(policy_artifact_path),
            "full_strict_v24_artifact": Path(v24_artifact_path),
            "fdd_formula_policy_review_map": Path(policy_review_map_path),
        }
    )
    preexisting_paths = {name: all_paths[name] for name in _PREEXISTING}
    targets = {name: all_paths[name] for name in _ORDER}
    _validate_preexisting_exact(preexisting, preexisting_paths)
    _validate_exact_fixtures_and_review_map(
        policy_artifact_raw=policy_frozen_raw,
        v24_artifact_raw=v24_frozen_raw,
        policy_fixture_raw=policy_fixture_raw,
        v24_fixture_raw=v24_fixture_raw,
        policy_review_map_raw=policy_review_map_raw,
        fixture_deriver=fixture_deriver,
    )
    journal_path = _journal_path(targets["full_strict_v24_artifact"])
    lock_path = _lock_path(targets["full_strict_v24_artifact"])
    if _exists(lock_path):
        _regular_status(lock_path, "TASK8_GATE_LOCK_INVALID")
    if _exists(journal_path):
        _regular_status(journal_path, "TASK8_GATE_RECOVERY_JOURNAL_INVALID")

    intent = _intent_value(
        outputs,
        targets,
        preexisting=preexisting,
        preexisting_paths=preexisting_paths,
    )
    intent_sha256 = _sha256(_render(intent))
    existing_lock_raw: bytes | None = None
    existing_lock_value: dict[str, Any] | None = None
    if _exists(lock_path):
        existing_lock_raw, existing_lock_value = _load_lock(lock_path)
        owner_token = cast(str, existing_lock_value["owner_token"])
    else:
        owner_token = None
    expected_temp, _expected_takeover, expected_stages, observed_namespace = _preflight_sidecars(
        targets=targets,
        lock_path=lock_path,
        journal_path=journal_path,
        owner_token=owner_token,
        additional_paths=set(preexisting_paths.values()),
    )
    if existing_lock_value is None and (_exists(journal_path) or observed_namespace):
        _fail("TASK8_GATE_SIDECAR_COLLISION")

    existing_targets = [name for name, path in targets.items() if _exists(path)]

    recovery = existing_lock_value is not None
    completed_recovery = False
    old_lock_raw = existing_lock_raw
    current_lock_raw: bytes
    if recovery:
        if existing_lock_value is None or existing_lock_raw is None:
            _fail("TASK8_GATE_LOCK_INVALID")
        if existing_lock_value["intent_sha256"] != intent_sha256:
            _fail("TASK8_GATE_LOCK_MISMATCH")
        for name in existing_targets:
            _load_exact_raw(
                targets[name],
                outputs[name],
                "TASK8_GATE_RECOVERY_TARGET_MISMATCH",
            )
        status = _owner_status(
            cast(int, existing_lock_value["owner_pid"]),
            cast(str, existing_lock_value["owner_start_identity"]),
        )
        if status == "LIVE":
            _fail("TASK8_GATE_LOCKED")
        if status == "UNKNOWN":
            _fail("TASK8_GATE_OWNER_UNKNOWN")
        if owner_token is None:
            _fail("TASK8_GATE_LOCK_INVALID")
        expected_journal = _journal_identity(intent, owner_token=owner_token)
        expected_journal_raw = _render(expected_journal)
        journal_exists = _exists(journal_path)
        temp_exists = expected_temp is not None and _exists(expected_temp)
        if journal_exists:
            _load_exact_raw(
                journal_path,
                expected_journal_raw,
                "TASK8_GATE_RECOVERY_JOURNAL_MISMATCH",
            )
        if temp_exists:
            if expected_temp is None:
                _fail("TASK8_GATE_RECOVERY_STATE_INVALID")
            _load_exact_raw(
                expected_temp,
                expected_journal_raw,
                "TASK8_GATE_RECOVERY_JOURNAL_INVALID",
            )
        for ordinal, stage in expected_stages.items():
            if _exists(stage):
                _load_exact_raw(
                    stage,
                    outputs[_ORDER[ordinal - 1]],
                    "TASK8_GATE_RECOVERY_STAGE_INVALID",
                )
        completed_recovery = _validate_recovery_topology(
            targets=targets,
            expected_stages=expected_stages,
            journal_path=journal_path,
            expected_temp=expected_temp,
        )
        current_lock_raw = _render(
            _lock_value(intent_sha256=intent_sha256, owner_token=owner_token)
        )
        _replace_lock_owner_atomically(
            lock_path,
            expected_lock_raw=existing_lock_raw,
            replacement_lock_raw=current_lock_raw,
            intent_sha256=intent_sha256,
            owner_token=owner_token,
            checkpoint="after_takeover_temp_fsync_before_replace",
        )
    else:
        if existing_targets:
            if len(existing_targets) == len(_ORDER) and all(
                targets[name].read_bytes() == outputs[name] for name in _ORDER
            ):
                _fail("TASK8_GATE_ALREADY_PUBLISHED")
            _fail("TASK8_GATE_TARGET_ALREADY_EXISTS")
        owner_token = secrets.token_hex(32)
        expected_journal = _journal_identity(intent, owner_token=owner_token)
        expected_journal_raw = _render(expected_journal)
        expected_temp = _journal_temp_path(journal_path, owner_token)
        expected_stages = {
            ordinal: _stage_path(targets[name], owner_token, ordinal)
            for ordinal, name in enumerate(_ORDER, start=1)
        }
        current_lock_raw = _render(
            _lock_value(intent_sha256=intent_sha256, owner_token=owner_token)
        )
        _write_exclusive(lock_path, current_lock_raw, code="TASK8_GATE_LOCKED")

    _process_exit_checkpoint("after_lock_acquired_before_journal")
    if owner_token is None:
        _fail("TASK8_GATE_LOCK_INVALID")

    if completed_recovery:
        completed_cleanup_code: str | None = None
        for ordinal, stage in expected_stages.items():
            if _exists(stage):
                try:
                    _unlink_verified_sidecar(
                        stage,
                        expected_raw=outputs[_ORDER[ordinal - 1]],
                        owner_token=owner_token,
                        code="TASK8_GATE_CLEANUP_FAILED",
                    )
                except Exception:
                    completed_cleanup_code = "TASK8_GATE_CLEANUP_FAILED"
        try:
            _unlink_verified_sidecar(
                lock_path,
                expected_raw=current_lock_raw,
                owner_token=owner_token,
                code="TASK8_GATE_CLEANUP_FAILED",
            )
        except Exception:
            completed_cleanup_code = completed_cleanup_code or "TASK8_GATE_CLEANUP_FAILED"
        if completed_cleanup_code is not None:
            _fail(completed_cleanup_code)
        _fail("TASK8_GATE_ALREADY_PUBLISHED")

    created_targets: list[str] = []
    primary_code: str | None = None
    cleanup_code: str | None = None
    try:
        _write_journal(journal_path, expected_journal)
        for ordinal, name in enumerate(_ORDER, start=1):
            target = targets[name]
            expected_raw = outputs[name]
            stage = expected_stages[ordinal]
            if _exists(target):
                _load_exact_raw(
                    target,
                    expected_raw,
                    "TASK8_GATE_RECOVERY_TARGET_MISMATCH",
                )
                if _exists(stage):
                    if not _same_file(stage, target):
                        _fail("TASK8_GATE_FILE_IDENTITY_ALIAS")
                    _unlink_verified_sidecar(
                        stage,
                        expected_raw=expected_raw,
                        owner_token=owner_token,
                        code="TASK8_GATE_RECOVERY_STAGE_INVALID",
                    )
                continue
            if not _exists(stage):
                _write_exclusive(
                    stage,
                    expected_raw,
                    code="TASK8_GATE_SIDECAR_COLLISION",
                )
            else:
                _load_exact_raw(
                    stage,
                    expected_raw,
                    "TASK8_GATE_RECOVERY_STAGE_INVALID",
                )

            _publish_no_clobber(
                stage,
                target,
                expected_raw,
                on_target_linked=_target_link_recorder(created_targets, name),
            )
        _validate_final_targets(outputs, targets)
        _validate_preexisting_exact(preexisting, preexisting_paths)
        _assert_identity_contract(set(all_paths.values()), allowed_pairs=set())
        _unlink_verified_sidecar(
            journal_path,
            expected_raw=expected_journal_raw,
            owner_token=owner_token,
            code="TASK8_GATE_CLEANUP_FAILED",
        )
        _process_exit_checkpoint("after_journal_removal_before_lock_cleanup")
    except Exception as exc:
        primary_code = _stable_code(exc)

    if primary_code is not None:
        if recovery:
            try:
                if old_lock_raw is None:
                    _fail("TASK8_GATE_LOCK_INVALID")
                _replace_lock_owner_atomically(
                    lock_path,
                    expected_lock_raw=current_lock_raw,
                    replacement_lock_raw=old_lock_raw,
                    intent_sha256=intent_sha256,
                    owner_token=owner_token,
                    checkpoint="after_restore_temp_fsync_before_replace",
                )
            except Exception:
                cleanup_code = "TASK8_GATE_CLEANUP_FAILED"
        else:
            for name in reversed(created_targets):
                try:
                    _unlink_exact_target(
                        targets[name],
                        outputs[name],
                        "TASK8_GATE_CLEANUP_FAILED",
                    )
                except Exception:
                    cleanup_code = cleanup_code or "TASK8_GATE_CLEANUP_FAILED"
            for ordinal, stage in expected_stages.items():
                if _exists(stage):
                    try:
                        _unlink_verified_sidecar(
                            stage,
                            expected_raw=outputs[_ORDER[ordinal - 1]],
                            owner_token=owner_token,
                            code="TASK8_GATE_CLEANUP_FAILED",
                        )
                    except Exception:
                        cleanup_code = cleanup_code or "TASK8_GATE_CLEANUP_FAILED"
            for sidecar, raw in (
                (expected_temp, expected_journal_raw),
                (journal_path, expected_journal_raw),
            ):
                if sidecar is not None and _exists(sidecar):
                    try:
                        _unlink_verified_sidecar(
                            sidecar,
                            expected_raw=raw,
                            owner_token=owner_token,
                            code="TASK8_GATE_CLEANUP_FAILED",
                        )
                    except Exception:
                        cleanup_code = cleanup_code or "TASK8_GATE_CLEANUP_FAILED"
            if _exists(lock_path):
                try:
                    _unlink_verified_sidecar(
                        lock_path,
                        expected_raw=current_lock_raw,
                        owner_token=owner_token,
                        code="TASK8_GATE_CLEANUP_FAILED",
                    )
                except Exception:
                    cleanup_code = cleanup_code or "TASK8_GATE_CLEANUP_FAILED"
        # A stable primary is never replaced by a later cleanup failure.
        _fail(primary_code)

    try:
        _unlink_verified_sidecar(
            lock_path,
            expected_raw=current_lock_raw,
            owner_token=owner_token,
            code="TASK8_GATE_CLEANUP_FAILED",
        )
    except Exception:
        cleanup_code = "TASK8_GATE_CLEANUP_FAILED"
    if cleanup_code is not None:
        _fail(cleanup_code)

    return {
        "preexisting_verified": list(_PREEXISTING),
        "created_publication_order": list(_ORDER),
        "commit_point": "full_strict_v24_artifact",
        "activation_status": "INACTIVE",
        "gate_admitted": True,
        "output_sha256": {name: _sha256(outputs[name]) for name in _ORDER},
        "preexisting_sha256": {name: _sha256(preexisting[name]) for name in _PREEXISTING},
        "policy_review_map_sha256": _sha256(policy_review_map_raw),
    }


def _publish_task8_gate_with_deriver(
    *,
    policy_candidate_raw: bytes,
    v24_candidate_raw: bytes,
    policy_fixture_raw: bytes,
    v24_fixture_raw: bytes,
    policy_review_map_raw: bytes,
    policy_artifact_path: Path,
    v24_artifact_path: Path,
    policy_fixture_path: Path,
    v24_fixture_path: Path,
    policy_review_map_path: Path,
    fixture_deriver: FixtureDeriver,
) -> dict[str, Any]:
    """Process-exit recoverable publication; not a power-loss durability claim."""
    failure_code: str | None = None
    try:
        return _publish_task8_gate_impl(
            policy_candidate_raw=policy_candidate_raw,
            v24_candidate_raw=v24_candidate_raw,
            policy_fixture_raw=policy_fixture_raw,
            v24_fixture_raw=v24_fixture_raw,
            policy_review_map_raw=policy_review_map_raw,
            policy_artifact_path=policy_artifact_path,
            v24_artifact_path=v24_artifact_path,
            policy_fixture_path=policy_fixture_path,
            v24_fixture_path=v24_fixture_path,
            policy_review_map_path=policy_review_map_path,
            fixture_deriver=fixture_deriver,
        )
    except Exception as exc:
        failure_code = _stable_code(exc)
    if failure_code is None:
        failure_code = "TASK8_GATE_INTERNAL_FAILURE"
    _raise_clean(failure_code)


def publish_task8_gate_atomically(
    *,
    policy_candidate_raw: bytes,
    v24_candidate_raw: bytes,
    policy_fixture_raw: bytes,
    v24_fixture_raw: bytes,
    policy_review_map_raw: bytes,
    policy_artifact_path: Path,
    v24_artifact_path: Path,
    policy_fixture_path: Path,
    v24_fixture_path: Path,
    policy_review_map_path: Path,
) -> dict[str, Any]:
    """Publish only after fixtures are rederived by the production hermetic generator."""
    return _publish_task8_gate_with_deriver(
        policy_candidate_raw=policy_candidate_raw,
        v24_candidate_raw=v24_candidate_raw,
        policy_fixture_raw=policy_fixture_raw,
        v24_fixture_raw=v24_fixture_raw,
        policy_review_map_raw=policy_review_map_raw,
        policy_artifact_path=policy_artifact_path,
        v24_artifact_path=v24_artifact_path,
        policy_fixture_path=policy_fixture_path,
        v24_fixture_path=v24_fixture_path,
        policy_review_map_path=policy_review_map_path,
        fixture_deriver=_derive_expected_fixture_bytes,
    )
