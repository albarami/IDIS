"""Temporary CIR-04 P4 canary. V1 only: admit, then synthetic capture.

The shell verifies this file and the sibling bootstrap blob before execution.
This module loads that verified blob by path and reuses its primitives.
It does not import the workspace copy and it does not write the production root.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import signal
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

_SCHEMA = "idis-ci-canary-v1"
_ADMIT = "canary-admit"
_CAPTURE = "canary-capture"
_SEED = "IDIS_CI_CANARY_SEED"
_SENTINEL_NAME = "CANARY_SENTINEL"
_ADMISSION = "canary-admission.json"
_BOOTSTRAP = "canary-bootstrap.py"
_LOG = "canary-capture.log"
_BOOTSTRAP_MODULE = "idis_canary_verified_bootstrap"
_SENTINEL_PREFIX = b"idis-canary-sentinel-v1\n"
_SEED_TEXT = re.compile(r"[a-f0-9]{64}")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_EXIT_LINE = re.compile(rb"^CANARY_OBSERVED_EXIT ([0-9]{1,3})$")
_FILE_COMMANDS = (
    "GITHUB_ENV",
    "GITHUB_PATH",
    "GITHUB_OUTPUT",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_STATE",
)
# V2/V3 are not wired. V2 replaces this line, the capture run body, and the probe pin.
_CHILD_EXIT = "sleep 600; exit 7"
# A re-read after start does not certify the bytes the interpreter already loaded.
_INITIAL_BYTES_CERTIFIED = False


class PinError(Exception):
    """A fixed pin failed before the verified bootstrap could be executed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Bootstrap(Protocol):
    """Primitives reused from the verified blob. Not a second implementation."""

    Rejected: type[BaseException]
    Interrupted: type[BaseException]

    def interrupt(self, signum: int, _frame: object) -> None: ...

    def require_runtime(self) -> dict[str, object]: ...

    def text(self, value: object) -> str: ...

    def integer(self, value: object) -> int: ...

    def object_map(self, value: object) -> dict[str, object]: ...

    def object_list(self, value: object) -> list[object]: ...

    def decode(self, raw: bytes | str) -> object: ...

    def digest(self, raw: bytes) -> str: ...

    def tuple_digest(self, observed: Mapping[str, object]) -> str: ...

    def github_reader(self, token: str) -> Callable[[str], object]: ...

    def observe(
        self,
        workspace: Path,
        environment: Mapping[str, str],
        get: Callable[[str], object],
    ) -> dict[str, object]: ...

    def require_approval(
        self,
        history: Sequence[object],
        observed: Mapping[str, object],
        env_id: int,
    ) -> None: ...

    def exclusive_write(self, path: Path, data: bytes) -> None: ...

    def contained_path(self, root: Path, relative: str) -> Path: ...

    def read_regular(self, path: Path) -> bytes: ...

    def require_interpreter_identity(
        self, expected: Mapping[str, object], executable: Path
    ) -> None: ...

    def capture_environment(self, environment: Mapping[str, str]) -> dict[str, str]: ...

    def cheap_recheck(
        self,
        workspace: Path,
        environment: Mapping[str, str],
        observed: Mapping[str, object],
    ) -> None: ...

    def capture_script(
        self,
        script: Path,
        log: Path,
        *,
        environment: Mapping[str, str],
        bash: str,
    ) -> int: ...

    def bash_path(self) -> str: ...


def _environment() -> Mapping[str, str]:
    """Return the process environment. Production returns os.environ."""
    return os.environ


def _interpreter_executable() -> Path:
    """Return the interpreter part B rechecks against part A's recorded identity."""
    return Path("/usr/bin/python3")


def sentinel_hex(seed: str) -> str:
    """Return the domain-separated sentinel hex for one synthetic seed.

    The sentinel is not registered and is not written to a public sink.
    """
    return hashlib.sha256(_SENTINEL_PREFIX + seed.encode("utf-8")).hexdigest()


def synthetic_boundary_line() -> str:
    """Handle one synthetic exception and return a closed failure line.

    The line has no traceback and no exception text. Capture does not print it.
    """
    try:
        raise RuntimeError("synthetic-canary-boundary")
    except RuntimeError:
        return json.dumps(
            {
                "schema": _SCHEMA,
                "command": _CAPTURE,
                "case": "boundary",
                "exit_code": 1,
            },
            separators=(",", ":"),
        )


def child_source() -> str:
    """Return the reviewed synthetic child. The sentinel arrives only in the environment."""
    lines = [
        'sentinel="${CANARY_SENTINEL-}"',
        "printf '%s\\n' \"$sentinel\"",
        "printf '%s\\n' \"$sentinel\" >&2",
        "printf '%s\\n' 'Traceback (most recent call last):'",
        "printf '%s\\n' '  File \"canary_child.py\", line 1, in <module>'",
        "printf '%s\\n' 'RuntimeError: synthetic boundary'",
        "printf '%s\\n' '{malformed-json'",
        "printf '%s\\n' '<malformed'",
        "printf '%s\\n' \"::error::${sentinel}\"",
        "printf '%s\\n' \"::warning::${sentinel}\"",
        "printf '%s\\n' '##[group]canary-footer'",
        "set +e",
        "for name in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT \\",
        "  GITHUB_STEP_SUMMARY GITHUB_STATE; do",
        '  target="${!name-}"',
        '  if [ -n "$target" ]; then',
        '    printf \'%s\\n\' "$sentinel" >> "$target"',
        "  fi",
        "done",
        "while IFS= read -r name; do",
        '  case "$name" in',
        "    *[!A-Za-z0-9_]*) continue ;;",
        "  esac",
        '  target="${!name-}"',
        '  case "$target" in',
        "    *_runner_file_commands*)",
        '      printf \'%s\\n\' "$sentinel" >> "$target"',
        "      ;;",
        "  esac",
        "done <<EOF",
        "$(compgen -v)",
        "EOF",
        "(exit 0)",
        "printf '%s\\n' \"CANARY_OBSERVED_EXIT $?\"",
        "(exit 1)",
        "printf '%s\\n' \"CANARY_OBSERVED_EXIT $?\"",
        "(exit 7)",
        "printf '%s\\n' \"CANARY_OBSERVED_EXIT $?\"",
        _CHILD_EXIT,
    ]
    return "\n".join(lines) + "\n"


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":")))


def _case(command: str) -> str:
    if command == _ADMIT:
        return "admit"
    return "capture"


def _fixed_code(exc: BaseException) -> str:
    if type(exc).__name__ != "Rejected" or not exc.args:
        return "REJECTED"
    code = exc.args[0]
    if isinstance(code, str) and _CODE.fullmatch(code):
        return code
    return "REJECTED"


def _require_pins(root: Path, environment: Mapping[str, str]) -> None:
    try:
        bootstrap_raw = (root / _BOOTSTRAP).read_bytes()
        probe_raw = Path(__file__).read_bytes()
    except OSError as exc:
        raise PinError("BOOTSTRAP_DRIFT") from exc
    bootstrap_hash = hashlib.sha256(bootstrap_raw).hexdigest()
    probe_hash = hashlib.sha256(probe_raw).hexdigest()
    if bootstrap_hash != environment.get("IDIS_BOOTSTRAP_SHA256"):
        raise PinError("BOOTSTRAP_DRIFT")
    if probe_hash != environment.get("IDIS_CANARY_PROBE_SHA256"):
        raise PinError("PROBE_DRIFT")


def load_verified_bootstrap(root: Path) -> Bootstrap:
    """Load ``canary-bootstrap.py`` from ``root`` after its pin check.

    The import name is private. Nothing is inserted on ``sys.path``.
    """
    _require_pins(root, _environment())
    path = root / _BOOTSTRAP
    spec = importlib.util.spec_from_file_location(_BOOTSTRAP_MODULE, path)
    if spec is None or spec.loader is None:
        raise PinError("BOOTSTRAP_DRIFT")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Bootstrap, module)


def _matching_comment(
    bootstrap: Bootstrap,
    history: Sequence[object],
    observed: Mapping[str, object],
) -> str:
    expected = bootstrap.tuple_digest(observed)
    found: list[str] = []
    for value in history:
        record = bootstrap.object_map(value)
        comment = record.get("comment")
        if not isinstance(comment, str) or not comment:
            continue
        try:
            body = bootstrap.object_map(bootstrap.decode(comment))
        except bootstrap.Rejected:
            continue
        same_attempt = body.get("run_id") == observed.get("run_id") and body.get(
            "run_attempt"
        ) == observed.get("run_attempt")
        if same_attempt and body.get("tuple_sha256") == expected:
            found.append(comment)
    if len(found) != 1:
        raise bootstrap.Rejected("APPROVAL_MISSING_OR_DUPLICATE")
    return found[0]


def _admit(bootstrap: Bootstrap, root: Path, environment: Mapping[str, str]) -> int:
    identity = bootstrap.require_runtime()
    workspace = Path(bootstrap.text(environment.get("GITHUB_WORKSPACE")))
    token = bootstrap.text(environment.get("IDIS_CI_READ_TOKEN"))
    get = bootstrap.github_reader(token)
    observed = bootstrap.observe(workspace, environment, get)
    run_id = observed["run_id"]
    history = bootstrap.object_list(get(f"/actions/runs/{run_id}/approvals"))
    bootstrap.require_approval(
        history,
        observed,
        bootstrap.integer(observed.get("environment_id")),
    )
    comment = _matching_comment(bootstrap, history, observed)
    raw_comment = comment.encode("utf-8")
    record = {
        "interpreter": identity,
        "observed": observed,
        "tuple_digest": bootstrap.tuple_digest(observed),
    }
    payload = json.dumps(record, sort_keys=True).encode("utf-8")
    bootstrap.exclusive_write(bootstrap.contained_path(root, _ADMISSION), payload)
    _emit(
        {
            "schema": _SCHEMA,
            "command": _ADMIT,
            "case": "admit",
            "exit_code": 0,
            "approval_comment_sha256": hashlib.sha256(raw_comment).hexdigest(),
            "approval_comment_utf8_length": len(raw_comment),
            "matches": 1,
        }
    )
    return 0


def _recheck(bootstrap: Bootstrap, root: Path, environment: Mapping[str, str]) -> None:
    """Call the verified recheck, then the probe pin and interpreter identity."""
    raw = bootstrap.read_regular(bootstrap.contained_path(root, _ADMISSION))
    record = bootstrap.object_map(bootstrap.decode(raw))
    observed = bootstrap.object_map(record.get("observed"))
    digest = bootstrap.text(record.get("tuple_digest"))
    if digest != bootstrap.tuple_digest(observed):
        raise bootstrap.Rejected("CANARY_RECHECK")
    workspace = Path(bootstrap.text(environment.get("GITHUB_WORKSPACE")))
    bootstrap.cheap_recheck(workspace, environment, observed)
    probe_raw = Path(__file__).read_bytes()
    if bootstrap.digest(probe_raw) != environment.get("IDIS_CANARY_PROBE_SHA256"):
        raise bootstrap.Rejected("CANARY_RECHECK")
    bootstrap.require_interpreter_identity(
        bootstrap.object_map(record.get("interpreter")),
        _interpreter_executable(),
    )


def _owned_script(bootstrap: Bootstrap, environment: Mapping[str, str], script: Path) -> bytes:
    """Return one regular file under this invocation's RUNNER_TEMP."""
    runner = Path(bootstrap.text(environment.get("RUNNER_TEMP")))
    workspace = Path(bootstrap.text(environment.get("GITHUB_WORKSPACE")))
    raw = bootstrap.read_regular(script)
    resolved = script.resolve()
    if not resolved.is_relative_to(runner.resolve()):
        raise bootstrap.Rejected("CANARY_SCRIPT")
    if resolved.is_relative_to(workspace.resolve()):
        raise bootstrap.Rejected("CANARY_SCRIPT")
    if sys.platform == "linux":
        info = script.stat()
        if info.st_uid != os.geteuid():
            raise bootstrap.Rejected("CANARY_SCRIPT")
    return raw


def _file_targets(environment: Mapping[str, str]) -> list[Path]:
    targets: list[Path] = []
    seen: set[str] = set()
    for key, value in environment.items():
        named = key in _FILE_COMMANDS
        if not named and "_runner_file_commands" not in value:
            continue
        if not value or value in seen:
            continue
        seen.add(value)
        targets.append(Path(value))
    return targets


def _marker(path: Path) -> bytes:
    try:
        if path.is_symlink():
            return b"SYMLINK"
        if path.is_file():
            return path.read_bytes()
        if path.exists():
            return b"PRESENT"
    except OSError:
        return b"UNREADABLE"
    return b"ABSENT"


def _boundary_is_closed(line: str) -> bool:
    if "Traceback" in line or "synthetic-canary-boundary" in line:
        return False
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False
    return (
        set(payload) == {"schema", "command", "case", "exit_code"}
        and payload["schema"] == _SCHEMA
        and payload["command"] == _CAPTURE
        and payload["case"] == "boundary"
        and payload["exit_code"] == 1
    )


def _observed_exits(raw: bytes) -> list[int]:
    exits: list[int] = []
    for line in raw.splitlines():
        match = _EXIT_LINE.fullmatch(line)
        if match is None:
            continue
        number = int(match.group(1))
        if number <= 255:
            exits.append(number)
    return exits


def _capture(
    bootstrap: Bootstrap,
    root: Path,
    environment: Mapping[str, str],
    script: Path,
) -> int:
    try:
        _recheck(bootstrap, root, environment)
    except Exception:
        # Any pre-seed failure is one fixed code. The seed has not been read.
        print("CANARY_RECHECK")
        return 1
    seed = environment.get(_SEED, "")
    if not isinstance(seed, str) or _SEED_TEXT.fullmatch(seed) is None:
        print("CANARY_SEED")
        return 1
    try:
        script_bytes = _owned_script(bootstrap, environment, script)
    except (bootstrap.Rejected, OSError, ValueError):
        print("CANARY_SCRIPT")
        return 1
    sentinel = sentinel_hex(seed)
    before = [(path, _marker(path)) for path in _file_targets(environment)]
    child_env = bootstrap.capture_environment(environment)
    child_env.pop(_SEED, None)
    child_env[_SENTINEL_NAME] = sentinel
    log = bootstrap.contained_path(root, _LOG)
    rc = bootstrap.capture_script(
        script,
        log,
        environment=child_env,
        bash=bootstrap.bash_path(),
    )
    if not _boundary_is_closed(synthetic_boundary_line()):
        print("CANARY_BOUNDARY")
        return 1
    raw = bootstrap.read_regular(log)
    changed = any(_marker(path) != marker for path, marker in before)
    exits = _observed_exits(raw)
    if rc != 7 or exits != [0, 1, 7] or changed:
        print("CANARY_UNEXPECTED")
        return 1
    _emit(
        {
            "schema": _SCHEMA,
            "command": _CAPTURE,
            "case": "capture",
            "exit_code": 7,
            "observed_exits": exits,
            "private_log_bytes": len(raw),
            "file_command_targets_changed": False,
            "boundary_closed": True,
            "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
            "initial_bytes_certified": _INITIAL_BYTES_CERTIFIED,
        }
    )
    return 7


def _emit_interrupt(command: str, signum: int, cleanup_failed: bool) -> None:
    _emit(
        {
            "schema": _SCHEMA,
            "command": command,
            "case": _case(command),
            "exit_code": 128 + signum,
            "cleanup_failed": cleanup_failed,
        }
    )


def _parse(argv: Sequence[str]) -> tuple[str, Path | None] | None:
    if len(argv) == 1 and argv[0] == _ADMIT:
        return _ADMIT, None
    if len(argv) == 2 and argv[0] == _CAPTURE and argv[1]:
        return _CAPTURE, Path(argv[1])
    return None


def _install(bootstrap: Bootstrap) -> dict[int, signal.Handlers]:
    previous: dict[int, signal.Handlers] = {}
    for number in (signal.SIGINT, signal.SIGTERM):
        previous[int(number)] = cast("signal.Handlers", signal.signal(number, bootstrap.interrupt))
    return previous


def _restore(previous: Mapping[int, signal.Handlers]) -> None:
    for number, handler in previous.items():
        signal.signal(number, handler)


def _invoke(command: str, script: Path | None) -> int:
    root = Path(__file__).resolve().parent
    environment = _environment()
    bootstrap = load_verified_bootstrap(root)
    previous = _install(bootstrap)
    try:
        os.umask(0o077)
        try:
            if command == _ADMIT:
                return _admit(bootstrap, root, environment)
            if script is None:
                print("REJECTED")
                return 1
            return _capture(bootstrap, root, environment, script)
        except bootstrap.Rejected as exc:
            print(_fixed_code(exc))
            return 1
        except (bootstrap.Interrupted, KeyboardInterrupt) as exc:
            signum = int(getattr(exc, "signum", signal.SIGINT))
            failed = bool(getattr(exc, "cleanup_failed", False))
            _emit_interrupt(command, signum, failed)
            return 128 + signum
        except PinError as exc:
            print(exc.code)
            return 1
        except SystemExit as exc:
            code = exc.code
            return code if isinstance(code, int) else 1
        except Exception as exc:
            print(_fixed_code(exc))
            return 1
    finally:
        _restore(previous)


def main(argv: Sequence[str]) -> int:
    """Run ``canary-admit`` or ``canary-capture`` and return its exit code."""
    parsed = _parse(argv)
    if parsed is None:
        print("REJECTED")
        return 1
    command, script = parsed
    try:
        return _invoke(command, script)
    except PinError as exc:
        print(exc.code)
        return 1
    except KeyboardInterrupt:
        _emit_interrupt(command, int(signal.SIGINT), False)
        return 128 + int(signal.SIGINT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
