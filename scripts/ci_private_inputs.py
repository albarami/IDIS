"""Finite CIR-04 CI transport boundary; no runtime admission or signing authority.

Only externally reviewed code may execute this on a fresh hosted runner. Isolation
flags and identity rechecks prevent accidental drift, not malicious admitted code
or passwordless-sudo dependencies. Never print private data or exception text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from http.client import HTTPMessage
from pathlib import Path
from types import FrameType, FunctionType
from typing import IO

PUBLIC_REPO = "albarami/IDIS"
PUBLIC_REPO_ID = 1129153696
OWNER_ID = 96863471
WORKFLOW_ID = 221216319
ENVIRONMENT = "cir04-private-inputs"
RECOVERY_REPO_ID = 1422375522
RECOVERY_COMMIT = "1a094b854c2e8766ba4516ff6edd1a4023ec1171"
RECOVERY_TREE = "489027790f7f480a5186db926f866da44fbcebc9"
MANIFEST_PINS = {
    "recovery-manifest.json": "cad7244900e79af573db4b666a0a12baa629ddf950e731bfa7ddd61cf9f8d018",
    "historical-verification-manifest.json": (
        "eb55e0dab732486bc2dfa451e9efee111f59fe5bee2126d270cc45f337446e44"
    ),
}
PUBLIC_KEY = "public-keys/governance-signing-public.pem"
ALLOWED_ARTIFACTS = frozenset(
    {
        "contracts/fdd_formula_policy_v1.json",
        "contracts/fdd_formula_policy_v2.json",
        "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v1-review-map.md",
        "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md",
        ".local_reports/slice100/task7_v21_current_guard_postgres_census_receipt.json",
        ".local_reports/slice100/task7_contract_reconciliation_log.jsonl",
        ".local_reports/slice100/task7_v17_contract_history.jsonl",
        ".local_reports/slice100/task8/task8_contract_reconciliation_log.jsonl",
        ".local_reports/slice100/task8/v22-current-output-extension-regeneration-002/"
        "full_strict_v22_contract_hash_v1.json",
        PUBLIC_KEY,
    }
)
CHECK_ACTIONS = (
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
)
SSH_HOST_KEY = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
)


class Rejected(Exception):
    """A fixed public-safe rejection code; never include external input."""


class Interrupted(BaseException):
    """Keep the first real signal code without serializing exception text."""

    def __init__(self, signum: int, cleanup_failed: bool = False) -> None:
        self.signum = signum
        self.cleanup_failed = cleanup_failed


def interrupt(signum: int, _frame: FrameType | None) -> None:
    # Cleanup is bounded; repeated cancellation must not interrupt owned-child retirement.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise Interrupted(signum)


@contextmanager
def deferred_owned_cleanup(cleanup: Callable[[], None]) -> Iterator[Callable[[], None]]:
    """Arm before ownership; each checkpoint commits at most one bounded operation."""
    pending: Interrupted | None = None
    previous = {int(signum): signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def remember(exc: BaseException) -> None:
        nonlocal pending
        if isinstance(exc, Interrupted):
            if pending is None:
                pending = exc
            else:
                pending.cleanup_failed |= exc.cleanup_failed
        elif isinstance(exc, KeyboardInterrupt) and pending is None:
            pending = Interrupted(signal.SIGINT)

    def record(signum: int, frame: FrameType | None) -> None:
        nonlocal pending
        prior = previous[signum]
        # Nested owned cleanup must share observation order during handler installation.
        # Forward ONLY this helper's recorder, never an arbitrary raising prior handler.
        if isinstance(prior, FunctionType) and prior.__code__ is record.__code__:
            prior(signum, frame)
        if pending is None:
            pending = Interrupted(signum)

    def checkpoint() -> None:
        if pending is not None:
            raise pending

    for signum in previous:
        signal.signal(signum, record)
    failure: BaseException | None = None
    try:
        yield checkpoint
    except BaseException as exc:
        failure = exc
        remember(exc)
    finally:
        cleanup_failure: BaseException | None = None
        try:
            # The SAME recorders remain armed across entry and every owned cleanup operation.
            cleanup()
        except Interrupted as exc:
            # A nested owned cleanup already completed and reports its first signal/status.
            remember(exc)
        except BaseException as exc:
            cleanup_failure = exc
            remember(exc)
        if pending is None:
            try:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
            except Interrupted as exc:
                remember(exc)
            except KeyboardInterrupt:
                remember(KeyboardInterrupt())
        if pending is not None:
            # No children launch here. Keep repeats from reopening the terminal summary gap.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            pending.cleanup_failed |= cleanup_failure is not None
            raise pending
        if cleanup_failure is not None:
            raise cleanup_failure
        if failure is not None:
            raise failure


def object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise Rejected("METADATA_SHAPE")
    return value


def object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise Rejected("METADATA_SHAPE")
    return value


def integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise Rejected("METADATA_INTEGER")
    return value


def text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise Rejected("METADATA_TEXT")
    return value


def decode(raw: bytes | str) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise Rejected("METADATA_JSON_DUPLICATE")
            result[key] = value
        return result

    try:
        value: object = json.loads(raw, object_pairs_hook=unique)
        return value
    except (ValueError, UnicodeError) as exc:
        raise Rejected("METADATA_JSON") from exc


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def tuple_digest(observed: Mapping[str, object]) -> str:
    return digest(json.dumps(dict(observed), sort_keys=True, separators=(",", ":")).encode())


def reject_debug(environment: Mapping[str, str]) -> None:
    if "RUNNER_DEBUG" in environment or "ACTIONS_STEP_DEBUG" in environment:
        raise Rejected("DEBUG_UNQUALIFIED")


def require_approval(
    history: Sequence[object], observed: Mapping[str, object], env_id: int
) -> None:
    """The API has no approval timestamp or stable history ordering.

    Review/authority/UTC are assertions from the real reviewer, not observations.
    Only an exact current-attempt digest in a genuine platform record admits.
    """
    expected = tuple_digest(observed)
    matches = 0
    for value in history:
        record = object_map(value)
        try:
            comment = object_map(decode(text(record.get("comment"))))
        except Rejected:
            # Unrelated historical comments cannot supply the required match.
            continue
        if (comment.get("run_id"), comment.get("run_attempt")) != (
            observed.get("run_id"),
            observed.get("run_attempt"),
        ):
            continue
        environments = object_list(record.get("environments"))
        ids = [object_map(item).get("id") for item in environments]
        assertions = object_map(comment.get("reviewer_assertions"))
        valid = (
            record.get("state") == "approved"
            and object_map(record.get("user")).get("id") == OWNER_ID
            and ids == [env_id]
            and comment.get("schema") == "idis-ci-approval-v1"
            and comment.get("tuple_sha256") == expected
            and assertions.get("kind") == "coordinator"
            and assertions.get("authority") == "AUTH-RECOVERY-20261017"
            and re.fullmatch(r"[a-f0-9]{64}", text(assertions.get("opus_review_sha256")))
        )
        if not valid:
            raise Rejected("APPROVAL_CONFLICT")
        matches += 1
    if matches != 1:
        raise Rejected("APPROVAL_MISSING_OR_DUPLICATE")


def require_merge_parents(parents: Sequence[str], base: str, head: str) -> None:
    if list(parents) != [base, head]:
        raise Rejected("MERGE_PARENTS")


def safe_summary(command: str, exit_code: int) -> dict[str, object]:
    if command not in {"admit", "fetch", "overlay", "prepare", "check", "cleanup"}:
        raise Rejected("SUMMARY_COMMAND")
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise Rejected("SUMMARY_EXIT")
    return {"schema": "idis-ci-safe-summary-v1", "command": command, "exit_code": exit_code}


def numeric_counts(raw: bytes) -> dict[str, int]:
    """Extract numbers from pytest footer only; never emit a source substring."""
    result: dict[str, int] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("===") or not line.endswith("==="):
            continue
        for number, label in re.findall(
            r"\b([0-9]{1,7}) "
            r"(passed|failed|skipped|errors?|warnings?|xfailed|xpassed|deselected)\b",
            line,
        ):
            label = {"error": "errors", "warning": "warnings"}.get(label, label)
            result[label] = int(number)
    return result


def no_alias(path: Path) -> None:
    for item in (path, *path.parents):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Rejected("PATH_ALIAS")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise Rejected("PATH_HARDLINK")


def contained_path(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or ":" in relative:
        raise Rejected("PATH_INVALID")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Rejected("PATH_INVALID")
    no_alias(root)
    target = root.joinpath(*parts)
    no_alias(target)
    if not target.resolve().is_relative_to(root.resolve()):
        raise Rejected("PATH_OUTSIDE")
    return target


def exclusive_write(
    path: Path, data: bytes, *, on_create: Callable[[os.stat_result], None] | None = None
) -> None:
    no_alias(path)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise Rejected("DESTINATION_EXISTS") from exc
    with os.fdopen(fd, "wb", buffering=0) as stream:
        try:
            if on_create is not None:
                on_create(os.fstat(stream.fileno()))
            if stream.write(data) != len(data):
                raise Rejected("DESTINATION_WRITE")
            os.fsync(stream.fileno())
        except BaseException:
            # This descriptor was exclusively created here: never unlink a replacement name.
            os.ftruncate(stream.fileno(), 0)
            os.fsync(stream.fileno())
            raise


def read_regular(path: Path) -> bytes:
    no_alias(path)
    if not path.is_file():
        raise Rejected("PATH_NOT_REGULAR")
    return path.read_bytes()


def manifest_artifacts(
    stage: Path, *, checkpoint: Callable[[], None] | None = None
) -> dict[str, str]:
    """Always validate using reviewed constants, never Phase A handoff hashes."""
    artifacts: dict[str, str] = {}
    for name, pin in MANIFEST_PINS.items():
        if checkpoint is not None:
            checkpoint()
        raw = read_regular(contained_path(stage, name))
        if checkpoint is not None:
            checkpoint()
        if digest(raw) != pin:
            raise Rejected("MANIFEST_HASH")
        manifest = object_map(decode(raw))
        entries = object_list(manifest.get("artifacts"))
        if manifest.get("artifact_count") != len(entries):
            raise Rejected("MANIFEST_COUNT")
        for entry in entries:
            row = object_map(entry)
            path, sha = text(row.get("path")), text(row.get("sha256"))
            if path not in ALLOWED_ARTIFACTS or path in artifacts:
                raise Rejected("MANIFEST_ALLOWLIST")
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise Rejected("MANIFEST_HASH")
            artifacts[path] = sha
    if set(artifacts) != ALLOWED_ARTIFACTS:
        raise Rejected("MANIFEST_ALLOWLIST")
    return artifacts


def restore_stage(
    stage: Path,
    workspace: Path,
    keyroot: Path,
    *,
    ownership: Callable[[str, os.stat_result, bool], None] | None = None,
) -> list[Path]:
    if keyroot.resolve().is_relative_to(workspace.resolve()):
        raise Rejected("PATH_KEY_IN_WORKSPACE")
    artifacts = manifest_artifacts(stage)
    copies: list[tuple[str, Path, bytes]] = []
    for relative, sha in artifacts.items():
        source = contained_path(stage, "artifacts/" + relative)
        raw = read_regular(source)
        if digest(raw) != sha:
            raise Rejected("PAYLOAD_HASH")
        target = (
            contained_path(keyroot, "governance-signing-public.pem")
            if relative == PUBLIC_KEY
            else contained_path(workspace, relative)
        )
        if target.exists() or target.is_symlink():
            raise Rejected("DESTINATION_EXISTS")
        copies.append((relative, target, raw))
    # No destination changes before ALL manifests, bytes and destinations pass.
    created: list[Path] = []
    for relative, target, raw in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if ownership is None:
            exclusive_write(target, raw)
        else:
            # Persist the new inode BEFORE writing any payload bytes into it.
            def remember_creation(
                info: os.stat_result,
                name: str = relative,
                record: Callable[[str, os.stat_result, bool], None] = ownership,
            ) -> None:
                record(name, info, False)

            exclusive_write(target, raw, on_create=remember_creation)
        if read_regular(target) != raw:
            raise Rejected("DESTINATION_HASH")
        if ownership is not None:
            ownership(relative, target.lstat(), True)
        created.append(target)
    return created


def file_identity(executable: Path) -> dict[str, object]:
    resolved = executable.resolve(strict=True)
    return {"path": str(resolved), "sha256": digest(resolved.read_bytes())}


def require_interpreter_identity(expected: Mapping[str, object], executable: Path) -> None:
    if file_identity(executable) != dict(expected):
        raise Rejected("INTERPRETER_DRIFT")


def require_runtime() -> dict[str, object]:
    if not sys.flags.isolated or not sys.flags.no_site:
        raise Rejected("INTERPRETER_FLAGS")
    if Path(sys.executable).resolve() != Path("/usr/bin/python3").resolve():
        raise Rejected("INTERPRETER_PATH")
    return file_identity(Path("/usr/bin/python3"))


def git_environment(environment: Mapping[str, str]) -> dict[str, str]:
    # No ambient Git config, SSH configuration, credential helper, trace or proxy.
    result = {
        key: environment[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in environment
    }
    result.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "LC_ALL": "C",
        }
    )
    return result


def git_bytes(repo: Path, *args: str, environment: Mapping[str, str] | None = None) -> bytes:
    env = git_environment(os.environ) if environment is None else dict(environment)
    result = subprocess.run(
        [
            "/usr/bin/git" if sys.platform == "linux" else "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "protocol.file.allow=never",
            "-c",
            "credential.helper=",
            "-C",
            str(repo),
            *args,
        ],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise Rejected("GIT_COMMAND")
    return result.stdout


def git_text(repo: Path, *args: str) -> str:
    return git_bytes(repo, *args).decode("ascii").strip()


def bash_path() -> str:
    # Windows is only the hermetic mechanics test host; Actions uses /bin/bash.
    path = Path(r"C:\Program Files\Git\bin\bash.exe") if os.name == "nt" else Path("/bin/bash")
    if not path.is_file():
        raise Rejected("BASH_UNAVAILABLE")
    return str(path)


def capture_script(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
    no_alias(log)
    with log.open("xb") as stream:
        process: subprocess.Popen[bytes] | None = None
        constructing = True
        pending: int | None = None
        interrupted = False

        def capture_interrupt(signum: int, frame: FrameType | None) -> None:
            nonlocal pending
            if pending is None:
                pending = signum
            if not constructing:
                interrupt(pending, frame)

        previous = {}
        try:
            for number in (signal.SIGINT, signal.SIGTERM):
                previous[number] = signal.signal(number, capture_interrupt)
            # Callable handlers reset on exec: never inherit SIG_IGN or a blocked mask.
            # Defer raising until assignment gives the cleanup boundary its owned Popen.
            process = subprocess.Popen(
                [bash, "--noprofile", "--norc", "-eo", "pipefail", str(script)],
                env=dict(environment),
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform == "linux",
            )
            constructing = False
            if pending is not None:
                interrupt(pending, None)
            if sys.platform == "linux":
                # Retain the leader's identity, even on normal exit, until its group is stopped.
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
                stop_capture_child(process)
                if process.returncode is None:
                    raise Rejected("CHILD_CLEANUP")
                result = process.returncode
            else:
                result = process.wait()
        except BaseException as exc:
            if pending is not None:
                if isinstance(exc, Interrupted):
                    exc.signum = pending
                else:
                    exc = Interrupted(pending)
            interrupted = isinstance(exc, (Interrupted, KeyboardInterrupt))
            if interrupted:
                # A first cancellation already exists; suppress repeats only AFTER launch.
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                if process is not None:
                    stop_capture_child(process)
            except Exception as cleanup_error:
                if isinstance(exc, Interrupted):
                    exc.cleanup_failed = True
                elif isinstance(exc, KeyboardInterrupt):
                    raise Interrupted(signal.SIGINT, cleanup_failed=True) from cleanup_error
                else:
                    raise Rejected("CHILD_CLEANUP") from cleanup_error
            raise exc
        finally:
            if not interrupted:
                for number, handler in previous.items():
                    signal.signal(number, handler)
    return result if result >= 0 else 128 - result


def stop_capture_child(process: subprocess.Popen[bytes]) -> None:
    """Only a still-owned Popen child, and its newly created Linux session/group."""
    if sys.platform == "linux":
        if process.returncode is not None:
            return
        flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
        # Prove child custody WITHOUT reaping: its PID/group cannot be reused before final kill.
        os.waitid(os.P_PID, process.pid, flags)
        if os.getpgid(process.pid) != process.pid:
            raise Rejected("CHILD_GROUP_CUSTODY")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=2)
            return
        deadline = time.monotonic() + 2
        while os.waitid(os.P_PID, process.pid, flags) is None and time.monotonic() < deadline:
            time.sleep(0.01)
        with suppress(ProcessLookupError):
            # Also stop TERM-ignoring descendants when their group leader already exited.
            # ESRCH means no live member remains in this still-owned group, not a hidden failure.
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
        return
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        # The tracked child exited between poll and signal; reap it, never choose another PID.
        process.wait(timeout=2)
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def capture_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Deny inherited runner write channels, not public CI identity/context flags."""
    command_root = Path(text(environment.get("RUNNER_TEMP"))) / "_runner_file_commands"
    resolved_root = command_root.resolve()
    denied = {
        "IDIS_CI_READ_TOKEN",
        "IDIS_RECOVERY_READ_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_ENV",
        "GITHUB_PATH",
        "GITHUB_OUTPUT",
        "GITHUB_STEP_SUMMARY",
        "GITHUB_STATE",
    }
    result: dict[str, str] = {}
    for key, value in environment.items():
        if key in denied or key.startswith(("ACTIONS_", "GIT_TRACE")) or key == "GIT_CURL_VERBOSE":
            continue
        if value and "://" not in value:
            try:
                target = Path(value)
                if target.is_relative_to(command_root) or target.resolve().is_relative_to(
                    resolved_root
                ):
                    continue
            except (OSError, ValueError, RuntimeError):
                # An unclassifiable path-valued context cannot be passed as a write channel.
                continue
        result[key] = value
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise Rejected("API_REDIRECT")


def github_reader(token: str) -> Callable[[str], object]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(path: str) -> object:
        if (
            not path.startswith("/")
            or ".." in path
            or "?" in path
            and not path.endswith("?per_page=100")
        ):
            raise Rejected("API_PATH")
        request = urllib.request.Request(
            "https://api.github.com/repos/" + PUBLIC_REPO + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise Rejected("API_SIZE")
            return decode(raw)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Rejected("API_READ") from exc

    return get


def observe(
    workspace: Path, environment: Mapping[str, str], get: Callable[[str], object]
) -> dict[str, object]:
    reject_debug(environment)
    if (
        environment.get("GITHUB_REPOSITORY") != PUBLIC_REPO
        or environment.get("GITHUB_JOB") != "check"
    ):
        raise Rejected("RUN_IDENTITY")
    event = environment.get("GITHUB_EVENT_NAME")
    ref = environment.get("GITHUB_REF")
    run_id = integer(int(environment.get("GITHUB_RUN_ID", "0")))
    attempt = integer(int(environment.get("GITHUB_RUN_ATTEMPT", "0")))
    sha = text(environment.get("GITHUB_SHA"))
    workflow_sha = text(environment.get("IDIS_WORKFLOW_SHA"))
    for item in (sha, workflow_sha):
        if not re.fullmatch(r"[a-f0-9]{40}", item):
            raise Rejected("RUN_SHA")
    if git_text(workspace, "rev-parse", "HEAD") != sha:
        raise Rejected("CHECKOUT_HEAD")
    tree = git_text(workspace, "rev-parse", sha + "^{tree}")
    event_data = object_map(decode(read_regular(Path(text(environment.get("GITHUB_EVENT_PATH"))))))
    repo = object_map(event_data.get("repository"))
    if repo.get("id") != PUBLIC_REPO_ID or object_map(repo.get("owner")).get("id") != OWNER_ID:
        raise Rejected("REPOSITORY")
    head, base = sha, text(event_data.get("before", sha))
    if event == "pull_request" and ref == "refs/pull/119/merge":
        pr = object_map(get("/pulls/119"))
        event_pr = object_map(event_data.get("pull_request"))
        head_row, base_row = object_map(pr.get("head")), object_map(pr.get("base"))
        head, base = text(head_row.get("sha")), text(base_row.get("sha"))
        if (
            object_map(head_row.get("repo")).get("id") != PUBLIC_REPO_ID
            or object_map(base_row.get("repo")).get("id") != PUBLIC_REPO_ID
            or object_map(event_pr.get("head")).get("sha") != head
            or object_map(event_pr.get("base")).get("sha") != base
            or pr.get("merge_commit_sha") != sha
            or pr.get("state") != "open"
        ):
            raise Rejected("PR_CHANGED_OR_FORK")
        require_merge_parents(
            git_text(workspace, "show", "-s", "--format=%P", sha).split(), base, head
        )
    elif event != "push" or ref != "refs/heads/main":
        raise Rejected("EVENT_UNAPPROVED")
    run = object_map(get(f"/actions/runs/{run_id}"))
    attempt_row = object_map(get(f"/actions/runs/{run_id}/attempts/{attempt}"))
    if (
        run.get("id") != run_id
        or run.get("run_attempt") != attempt
        or attempt_row.get("run_attempt") != attempt
        or run.get("workflow_id") != WORKFLOW_ID
        or run.get("event") != event
        or run.get("path") != ".github/workflows/ci.yml"
        or object_map(run.get("repository")).get("id") != PUBLIC_REPO_ID
        or object_map(run.get("head_repository")).get("id") != PUBLIC_REPO_ID
        or run.get("head_sha") not in {head, sha}
    ):
        raise Rejected("RUN_CHANGED")
    jobs_page = object_map(get(f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"))
    jobs = object_list(jobs_page.get("jobs"))
    if jobs_page.get("total_count") != len(jobs) or len(jobs) > 100:
        raise Rejected("JOB_PAGINATION")
    matching = [object_map(row) for row in jobs if object_map(row).get("name") == "check"]
    if len(matching) != 1 or matching[0].get("status") != "in_progress":
        raise Rejected("JOB_IDENTITY")
    job = matching[0]
    check_url = text(job.get("check_run_url"))
    prefix = "https://api.github.com/repos/" + PUBLIC_REPO + "/check-runs/"
    if not check_url.startswith(prefix):
        raise Rejected("JOB_CHECK_ID")
    check_id = integer(int(check_url.removeprefix(prefix)))
    env = object_map(get("/environments/" + ENVIRONMENT))
    if env.get("name") != ENVIRONMENT:
        raise Rejected("ENVIRONMENT_IDENTITY")
    workflow = git_bytes(workspace, "cat-file", "blob", workflow_sha + ":.github/workflows/ci.yml")
    bootstrap = git_bytes(workspace, "cat-file", "blob", sha + ":scripts/ci_private_inputs.py")
    bootstrap_pin = text(environment.get("IDIS_BOOTSTRAP_SHA256"))
    if digest(bootstrap) != bootstrap_pin:
        raise Rejected("BOOTSTRAP_DRIFT")
    check_block = (
        workflow.decode().split("  check:\n", 1)[1].split("  postgres-integration:\n", 1)[0]
    )
    actions = re.findall(r'["\']?uses["\']?\s*:\s*["\']?([^\s"\'},]+)', check_block)
    if tuple(actions) != CHECK_ACTIONS:
        raise Rejected("ACTION_GRAPH")
    return {
        "public_repo_id": PUBLIC_REPO_ID,
        "head_repo_id": PUBLIC_REPO_ID,
        "workflow_id": WORKFLOW_ID,
        "workflow_path": ".github/workflows/ci.yml",
        "workflow_commit_sha": workflow_sha,
        "workflow_blob_sha256": digest(workflow),
        "bootstrap_blob_sha256": bootstrap_pin,
        "check_action_shas": actions,
        "event": event,
        "ref": ref,
        "candidate_head_sha": head,
        "base_sha": base,
        "tested_checkout_sha": sha,
        "tested_tree": tree,
        "run_head_sha": run.get("head_sha"),
        "run_id": run_id,
        "run_attempt": attempt,
        "job_key": "check",
        "job_id": integer(job.get("id")),
        "check_run_id": check_id,
        "environment_id": integer(env.get("id")),
        "recovery_repo_id": RECOVERY_REPO_ID,
        "recovery_commit": RECOVERY_COMMIT,
        "recovery_tree": RECOVERY_TREE,
        "recovery_manifests": MANIFEST_PINS,
    }


def cheap_recheck(
    workspace: Path, environment: Mapping[str, str], observed: Mapping[str, object]
) -> None:
    """Invoked by the secret-receiving step BEFORE reading/writing its key."""
    reject_debug(environment)
    try:
        sha = text(environment.get("GITHUB_SHA"))
        valid = (
            int(environment.get("GITHUB_RUN_ID", "0")) == observed.get("run_id")
            and int(environment.get("GITHUB_RUN_ATTEMPT", "0")) == observed.get("run_attempt")
            and sha == observed.get("tested_checkout_sha")
            and git_text(workspace, "rev-parse", "HEAD") == sha
            and git_text(workspace, "rev-parse", "HEAD^{tree}") == observed.get("tested_tree")
            and environment.get("IDIS_BOOTSTRAP_SHA256") == observed.get("bootstrap_blob_sha256")
            and digest(
                git_bytes(workspace, "cat-file", "blob", sha + ":scripts/ci_private_inputs.py")
            )
            == observed.get("bootstrap_blob_sha256")
        )
    except (Rejected, ValueError) as exc:
        raise Rejected("POST_SECRET_RECHECK") from exc
    if not valid:
        raise Rejected("POST_SECRET_RECHECK")


def delete_transport_tree(root: Path, target: Path) -> None:
    """Only this invocation's explicitly named owned bare repository, never workspace."""
    no_alias(root)
    if target.parent != root or target.name != "recovery.git" or target == root:
        raise Rejected("CLEANUP_OWNERSHIP")
    no_alias(target)
    if not target.is_dir():
        raise Rejected("CLEANUP_OWNERSHIP")
    files: list[Path] = []
    directories: list[Path] = []
    for directory, dirs, names in os.walk(target, followlinks=False):
        here = Path(directory)
        for name in [*dirs, *names]:
            path = here / name
            no_alias(path)
            if not path.resolve().is_relative_to(target.resolve()):
                raise Rejected("CLEANUP_OUTSIDE")
        files.extend(here / name for name in names)
        directories.append(here)
    # Full alias/containment preflight before deleting any owned path.
    for path in files:
        no_alias(path)
        if sys.platform == "win32":
            # Git marks its owned object files read-only on the hermetic Windows test host.
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        path.unlink()
    for path in reversed(directories):
        path.rmdir()


def fetch_private(root: Path, key: str) -> None:
    stage = contained_path(root, "stage")
    key_path, known = contained_path(root, "read-key"), contained_path(root, "known-hosts")
    bare = contained_path(root, "recovery.git")
    owned: list[Path] = []
    bare_created = False

    def cleanup_transport() -> None:
        cleanup_failed = False
        for path in owned:
            try:
                no_alias(path)
                path.unlink()
            except Exception:
                cleanup_failed = True
        if bare_created:
            try:
                delete_transport_tree(root, bare)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise Rejected("TRANSPORT_CLEANUP")

    # Record before ANY private-file creation, not only after entering a late finally.
    with deferred_owned_cleanup(cleanup_transport) as checkpoint:
        checkpoint()
        stage.mkdir(mode=0o700)
        checkpoint()
        if not key:
            raise Rejected("SSH_KEY_MISSING")
        exclusive_write(key_path, key.encode(), on_create=lambda _info: owned.append(key_path))
        checkpoint()
        exclusive_write(known, SSH_HOST_KEY.encode(), on_create=lambda _info: owned.append(known))
        checkpoint()
        bare.mkdir(mode=0o700)
        bare_created = True
        checkpoint()
        ssh = (
            "/usr/bin/ssh -F /dev/null -o LogLevel=ERROR -o StrictHostKeyChecking=yes "
            "-o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes "
            "-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile="
            + shlex.quote(str(known))
            + " -i "
            + shlex.quote(str(key_path))
        )
        env = git_environment(os.environ)
        env["GIT_SSH_COMMAND"] = ssh

        def fetch_git(*args: str) -> bytes:
            # One call committed after this check may finish/reach its EXISTING 120s timeout.
            checkpoint()
            raw = git_bytes(bare, *args, environment=env)
            checkpoint()
            return raw

        fetch_git("init", "--bare")
        fetch_git(
            "fetch",
            "--no-tags",
            "--depth=1",
            "git@github.com:albarami/IDIS-private-recovery.git",
            RECOVERY_COMMIT,
        )
        actual_tree = fetch_git("rev-parse", RECOVERY_COMMIT + "^{tree}").decode().strip()
        if actual_tree != RECOVERY_TREE:
            raise Rejected("RECOVERY_TREE")
        for name, pin in MANIFEST_PINS.items():
            raw = fetch_git("cat-file", "blob", RECOVERY_COMMIT + ":" + name)
            if digest(raw) != pin:
                raise Rejected("MANIFEST_HASH")
            checkpoint()
            exclusive_write(contained_path(stage, name), raw)
            checkpoint()
        artifacts = manifest_artifacts(stage, checkpoint=checkpoint)
        checkpoint()
        for relative, sha in artifacts.items():
            raw = fetch_git("cat-file", "blob", RECOVERY_COMMIT + ":" + relative)
            if digest(raw) != sha:
                raise Rejected("PAYLOAD_HASH")
            target = contained_path(stage, "artifacts/" + relative)
            checkpoint()
            target.parent.mkdir(parents=True, exist_ok=True)
            checkpoint()
            exclusive_write(target, raw)
            checkpoint()


def private_root(environment: Mapping[str, str], workspace: Path) -> Path:
    run, attempt = (
        int(environment.get("GITHUB_RUN_ID", "0")),
        int(environment.get("GITHUB_RUN_ATTEMPT", "0")),
    )
    integer(run)
    integer(attempt)
    parent = Path(text(environment.get("RUNNER_TEMP")))
    no_alias(parent)
    root = contained_path(parent, f"idis-ci-{run}-{attempt}")
    if not root.is_dir() or root.resolve().is_relative_to(workspace.resolve()):
        raise Rejected("PRIVATE_ROOT")
    if sys.platform == "linux" and (
        root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077
    ):
        raise Rejected("PRIVATE_ROOT_MODE")
    return root


def admitted(root: Path) -> dict[str, object]:
    return object_map(decode(read_regular(contained_path(root, "admission.json"))))


def checked_observation(workspace: Path, environment: Mapping[str, str]) -> dict[str, object]:
    get = github_reader(text(environment.get("IDIS_CI_READ_TOKEN")))
    observed = observe(workspace, environment, get)
    history = object_list(get(f"/actions/runs/{observed['run_id']}/approvals"))
    require_approval(history, observed, integer(observed.get("environment_id")))
    return observed


def remove_overlay(root: Path, workspace: Path) -> None:
    with deferred_owned_cleanup(lambda: remove_overlay_owned(root, workspace)):
        pass


def remove_overlay_owned(root: Path, workspace: Path) -> None:
    journal = contained_path(root, "overlay-created.jsonl")
    record_path = contained_path(root, "overlay-owned.json")
    if not journal.exists():
        if record_path.exists():
            raise Rejected("CLEANUP_OWNERSHIP")
        return
    pins = manifest_artifacts(contained_path(root, "stage"))
    keyroot = contained_path(root, "key-input")
    owned: dict[str, dict[str, object]] = {}
    # An incomplete final append grants no new authority; an earlier durable create row remains.
    lines = read_regular(journal).split(b"\n")[:-1]
    for raw in lines:
        row = object_map(decode(raw))
        relative = text(row.get("path"))
        if relative not in pins or set(row) != {"path", "device", "inode", "complete"}:
            raise Rejected("CLEANUP_OWNERSHIP")
        if type(row["complete"]) is not bool:
            raise Rejected("CLEANUP_OWNERSHIP")
        previous = owned.get(relative)
        if previous is None:
            if row["complete"]:
                raise Rejected("CLEANUP_OWNERSHIP")
        elif (
            previous["complete"]
            or not row["complete"]
            or any(previous[key] != row[key] for key in ("device", "inode"))
        ):
            raise Rejected("CLEANUP_OWNERSHIP")
        owned[relative] = row
    if record_path.exists() and read_regular(record_path):
        completed = object_map(decode(read_regular(record_path)))
        expected = {
            name: {key: row[key] for key in ("device", "inode")}
            for name, row in owned.items()
            if row["complete"]
        }
        if completed != expected or set(completed) != set(pins):
            raise Rejected("CLEANUP_OWNERSHIP")
    checked: list[tuple[Path, int, dict[str, object]]] = []
    failed = False
    try:
        for relative, row in owned.items():
            fd = None
            try:
                path = (
                    contained_path(keyroot, "governance-signing-public.pem")
                    if relative == PUBLIC_KEY
                    else contained_path(workspace, relative)
                )
                fd = os.open(
                    path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                )
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != (row["device"], row["inode"])
                ):
                    raise Rejected("CLEANUP_OWNERSHIP")
                if row["complete"]:
                    with os.fdopen(os.dup(fd), "rb") as stream:
                        if digest(stream.read()) != pins[relative]:
                            raise Rejected("CLEANUP_OWNERSHIP")
                checked.append((path, fd, row))
                fd = None  # Ownership transferred only after every per-file check passed.
            except (OSError, Rejected):
                failed = True
            finally:
                if fd is not None:
                    os.close(fd)
        for path, fd, row in checked:
            # Reuse the census custody rule: erase ONLY a held owned inode, never pathname unlink.
            try:
                os.ftruncate(fd, 0)
                os.fsync(fd)
                current = path.lstat()
                failed |= (current.st_dev, current.st_ino) != (row["device"], row["inode"])
            except OSError:
                failed = True
        if failed:
            raise Rejected("CLEANUP_OWNERSHIP")
    finally:
        for _path, fd, _row in checked:
            os.close(fd)


def execute(mode: str, script: Path | None = None) -> int:
    reject_debug(os.environ)
    identity = require_runtime()
    workspace = Path(text(os.environ.get("GITHUB_WORKSPACE")))
    root = private_root(os.environ, workspace)
    os.umask(0o077)
    if mode == "admit":
        observed = checked_observation(workspace, os.environ)
        exclusive_write(
            contained_path(root, "admission.json"),
            json.dumps(
                {
                    "observed": observed,
                    "interpreter": identity,
                },
                sort_keys=True,
            ).encode(),
        )
        return 0
    if mode == "cleanup":
        remove_overlay(root, workspace)
        return 0
    admission = admitted(root)
    observed = object_map(admission.get("observed"))
    cheap_recheck(workspace, os.environ, observed)
    require_interpreter_identity(object_map(admission.get("interpreter")), Path("/usr/bin/python3"))
    if mode == "fetch":
        key = os.environ.pop("IDIS_RECOVERY_READ_KEY", "")
        fetch_private(root, key)
        return 0
    if mode == "overlay":
        fresh = checked_observation(workspace, os.environ)
        if fresh != observed:
            raise Rejected("ADMISSION_CHANGED")
        keyroot = contained_path(root, "key-input")
        keyroot.mkdir(mode=0o700)
        record_path = contained_path(root, "overlay-owned.json")
        if record_path.exists():
            raise Rejected("DESTINATION_EXISTS")
        journal_path = contained_path(root, "overlay-created.jsonl")
        with journal_path.open("xb", buffering=0) as journal:

            def record_created(relative: str, info: os.stat_result, complete: bool) -> None:
                row = {
                    "path": relative,
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "complete": complete,
                }
                raw = json.dumps(row, sort_keys=True).encode() + b"\n"
                if journal.write(raw) != len(raw):
                    raise Rejected("OWNERSHIP_WRITE")
                os.fsync(journal.fileno())

            paths = restore_stage(
                contained_path(root, "stage"), workspace, keyroot, ownership=record_created
            )
        record: dict[str, object] = {}
        for path in paths:
            relative = (
                PUBLIC_KEY if path.parent == keyroot else path.relative_to(workspace).as_posix()
            )
            info = path.lstat()
            record[relative] = {"device": info.st_dev, "inode": info.st_ino}
        exclusive_write(record_path, json.dumps(record).encode())
        # Manifests are retained for constant-pinned cleanup; redundant payload copies are removed.
        for relative in ALLOWED_ARTIFACTS:
            path = contained_path(root, "stage/artifacts/" + relative)
            no_alias(path)
            path.unlink()
        return 0
    if mode in {"capture", "prepare"} and script is not None:
        name = "check" if mode == "capture" else "prepare"
        if mode == "capture" and read_regular(script).strip() != b"make check":
            raise Rejected("COMMAND_IDENTITY")
        environment = capture_environment(os.environ)
        environment["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"] = str(
            contained_path(root, "key-input/governance-signing-public.pem")
        )
        return capture_script(
            script, contained_path(root, name + ".log"), environment=environment, bash="/bin/bash"
        )
    raise Rejected("MODE")


def run(argv: Sequence[str]) -> int:
    mode = argv[0] if argv else ""
    command = "check" if mode == "capture" else mode
    if command not in {"admit", "fetch", "overlay", "cleanup", "check", "prepare"}:
        print(json.dumps({"schema": "idis-ci-safe-summary-v1", "exit_code": 1}))
        return 1
    try:
        rc = execute(mode, Path(argv[1]) if len(argv) == 2 else None)
    except Exception:
        # Deliberately do not serialize traceback, exception text, paths or payload hashes.
        rc = 1
        print(json.dumps(safe_summary(command, rc), sort_keys=True))
        return rc
    summary = safe_summary(command, rc)
    if mode in {"capture", "prepare"}:
        try:
            workspace = Path(text(os.environ.get("GITHUB_WORKSPACE")))
            root = private_root(os.environ, workspace)
            log = contained_path(root, command + ".log")
            no_alias(log)
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 65_536))
                counts = numeric_counts(stream.read())
            if mode == "capture" and rc == 0 and "passed" not in counts:
                raise Rejected("SUMMARY_MISSING")
            # Public Git/runner identities only, never handoff fields or payload hashes.
            summary.update(
                {
                    "run_id": integer(int(os.environ["GITHUB_RUN_ID"])),
                    "run_attempt": integer(int(os.environ["GITHUB_RUN_ATTEMPT"])),
                    "source_sha": git_text(workspace, "rev-parse", "HEAD"),
                    "source_tree": git_text(workspace, "rev-parse", "HEAD^{tree}"),
                    "recovery_commit": RECOVERY_COMMIT,
                    "counts": counts,
                }
            )
        except Exception:
            # Failure to summarize must not mask the original failed command's exit.
            rc = rc or 1
            summary = safe_summary(command, rc)
    print(json.dumps(summary, sort_keys=True))
    return rc


def main(argv: Sequence[str]) -> int:
    cleanup_mode = bool(argv and argv[0] == "cleanup")
    previous = {
        number: signal.getsignal(number) if cleanup_mode else signal.signal(number, interrupt)
        for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        try:
            if cleanup_mode:
                rc = 0

                def cleanup_command() -> None:
                    nonlocal rc
                    rc = execute("cleanup", Path(argv[1]) if len(argv) == 2 else None)

                try:
                    # Initial cleanup handlers are recorders BEFORE private authority validation.
                    with deferred_owned_cleanup(cleanup_command):
                        pass
                except Exception:
                    rc = 1
                print(json.dumps(safe_summary("cleanup", rc), sort_keys=True))
                return rc
            return run(argv)
        except (Interrupted, KeyboardInterrupt) as exc:
            code = 128 + exc.signum if isinstance(exc, Interrupted) else 130
            command = "check" if argv and argv[0] == "capture" else argv[0] if argv else "admit"
            if command not in {"admit", "fetch", "overlay", "cleanup", "check", "prepare"}:
                command = "admit"
            summary = safe_summary(command, code)
            if isinstance(exc, Interrupted) and exc.cleanup_failed:
                summary["cleanup_failed"] = True
            print(json.dumps(summary, sort_keys=True))
            return code
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
