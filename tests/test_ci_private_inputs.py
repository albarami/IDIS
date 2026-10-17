"""Hermetic bridge mechanics. These are NOT GitHub platform/canonical FULL evidence."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import io
import json
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bridge():
    path = ROOT / "scripts/ci_private_inputs.py"
    assert path.is_file(), "The finite CI transport/admission boundary is missing"
    spec = importlib.util.spec_from_file_location("ci_private_inputs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _check():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["check"]


def test_workflow_adds_external_environment_without_conditional_or_duplicate_check():
    job = _check()
    assert job.get("environment") == "cir04-private-inputs"
    assert "if" not in job
    assert job.get("permissions") == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "read",
    }
    assert job.get("timeout-minutes") == 360
    assert job.get("concurrency", {}).get("cancel-in-progress") is False


def test_secret_free_admission_precedes_key_receiving_step_and_installs():
    steps = _check()["steps"]
    admission = next((i for i, s in enumerate(steps) if s.get("id") == "private-admission"), -1)
    fetch = next((i for i, s in enumerate(steps) if s.get("id") == "private-fetch"), -1)
    assert 0 <= admission < fetch, "Admission and key-using phases must be distinct"
    assert "secrets." not in str(steps[admission])
    assert "secrets.IDIS_RECOVERY_READ_KEY" in str(steps[fetch])
    setup = next(i for i, s in enumerate(steps) if "setup-python" in s.get("uses", ""))
    assert fetch < setup


def test_original_make_check_is_really_executed_with_private_capture_shell():
    step = next(s for s in _check()["steps"] if s.get("name") == "Run all checks")
    assert step["run"] == "make check"
    assert "capture" in step.get("shell", ""), "Private assertion diffs must not reach public logs"
    assert "/usr/bin/python3 -I -S" in step["shell"]


def test_both_phases_use_isolated_absolute_interpreter_and_verified_blob():
    steps = _check()["steps"]
    for phase in ("private-admission", "private-fetch", "private-overlay"):
        step = next((s for s in steps if s.get("id") == phase), {})
        assert "/usr/bin/python3 -I -S" in step.get("run", "")
        assert "sha256sum --check --status" in step["run"]
        assert "/usr/bin/git " in step["run"] and "cat-file blob" in step["run"]


@pytest.mark.parametrize("flag", ["RUNNER_DEBUG", "ACTIONS_STEP_DEBUG"])
def test_any_set_debug_flag_rejects_before_metadata_or_key_handling(bridge, flag):
    with pytest.raises(bridge.Rejected, match="DEBUG"):
        bridge.reject_debug({flag: "0"})


def test_tuple_approval_rejects_missing_genuine_review(bridge):
    with pytest.raises(bridge.Rejected, match="APPROVAL"):
        bridge.require_approval([], {"run_id": 41, "run_attempt": 2}, 7)


def test_safe_summary_never_serializes_arbitrary_payload_or_exception(bridge):
    assert bridge.safe_summary("check", 7) == {
        "schema": "idis-ci-safe-summary-v1",
        "command": "check",
        "exit_code": 7,
    }
    with pytest.raises(bridge.Rejected):
        bridge.safe_summary("private SENTINEL", 0)


def _record(bridge, observed, *, reviewer=96863471, environments=(7,), state="approved"):
    return {
        "state": state,
        "user": {"id": reviewer, "login": "synthetic-owner"},
        "environments": [{"id": value, "name": "cir04-private-inputs"} for value in environments],
        "comment": json.dumps(
            {
                "schema": "idis-ci-approval-v1",
                "run_id": observed["run_id"],
                "run_attempt": observed["run_attempt"],
                "tuple_sha256": bridge.tuple_digest(observed),
                "reviewer_assertions": {
                    "kind": "coordinator",
                    "authority": "AUTH-RECOVERY-20261017",
                    "opus_review_sha256": "a" * 64,
                },
            }
        ),
    }


def test_official_schema_without_review_timestamp_accepts_one_exact_record(bridge):
    observed = {"run_id": 41, "run_attempt": 2, "tested_sha": "b" * 40, "tree": "c" * 40}
    bridge.require_approval([_record(bridge, observed)], observed, 7)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "reviewer", "extra-env", "reject", "old-attempt", "digest"]
)
def test_current_attempt_approval_conflicts_or_replays_fail_closed(bridge, mutation):
    observed = {"run_id": 41, "run_attempt": 2}
    record = _record(bridge, observed)
    records = [record]
    if mutation == "duplicate":
        records.append(record)
    elif mutation == "reviewer":
        record["user"]["id"] = 1
    elif mutation == "extra-env":
        record["environments"].append({"id": 8})
    elif mutation == "reject":
        records.append(_record(bridge, observed, state="rejected"))
    else:
        comment = json.loads(record["comment"])
        comment["run_attempt" if mutation == "old-attempt" else "tuple_sha256"] = (
            1 if mutation == "old-attempt" else "f" * 64
        )
        record["comment"] = json.dumps(comment)
    with pytest.raises(bridge.Rejected, match="APPROVAL"):
        bridge.require_approval(records, observed, 7)


def test_history_order_does_not_change_matching_or_fabricate_freshness(bridge):
    now = {"run_id": 41, "run_attempt": 2}
    old = {"run_id": 41, "run_attempt": 1}
    records = [_record(bridge, old), _record(bridge, now)]
    for order in (records, records[::-1]):
        bridge.require_approval(order, now, 7)


@pytest.mark.parametrize(
    "parents", [["a" * 40], ["b" * 40, "a" * 40], ["a" * 40, "b" * 40, "c" * 40]]
)
def test_merge_parent_check_rejects_nonexact_order_or_count(bridge, parents):
    with pytest.raises(bridge.Rejected, match="MERGE_PARENTS"):
        bridge.require_merge_parents(parents, "a" * 40, "b" * 40)


def _stage(bridge, monkeypatch, tmp_path):
    stage = tmp_path / "private" / "stage"
    stage.mkdir(parents=True)
    payloads = {
        "contracts/synthetic.json": b'{"private":"SENTINEL"}\r\n',
        "public-keys/governance-signing-public.pem": b"SYNTHETIC PUBLIC KEY\n",
    }
    pins = {}
    for index, (path, data) in enumerate(payloads.items()):
        dest = stage / "artifacts" / path
        dest.parent.mkdir(parents=True)
        dest.write_bytes(data)
        raw = json.dumps(
            {
                "artifact_count": 1,
                "artifacts": [{"path": path, "sha256": hashlib.sha256(data).hexdigest()}],
            }
        ).encode()
        name = f"manifest-{index}.json"
        (stage / name).write_bytes(raw)
        pins[name] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(bridge, "MANIFEST_PINS", pins)
    monkeypatch.setattr(bridge, "ALLOWED_ARTIFACTS", frozenset(payloads))
    workspace = tmp_path / "workspace"
    keyroot = tmp_path / "key-input"
    workspace.mkdir()
    keyroot.mkdir()
    return stage, workspace, keyroot, payloads


def test_staged_raw_bytes_preserved_and_public_key_is_outside_workspace(
    bridge, monkeypatch, tmp_path
):
    stage, workspace, keyroot, payloads = _stage(bridge, monkeypatch, tmp_path)
    bridge.restore_stage(stage, workspace, keyroot)
    assert (workspace / "contracts/synthetic.json").read_bytes() == payloads[
        "contracts/synthetic.json"
    ]
    assert (keyroot / "governance-signing-public.pem").read_bytes() == payloads[
        "public-keys/governance-signing-public.pem"
    ]
    assert not (workspace / "public-keys").exists()


@pytest.mark.parametrize("same", [True, False])
def test_every_preexisting_destination_is_rejected_even_if_identical(
    bridge, monkeypatch, tmp_path, same
):
    stage, workspace, keyroot, payloads = _stage(bridge, monkeypatch, tmp_path)
    dest = workspace / "contracts/synthetic.json"
    dest.parent.mkdir()
    data = payloads["contracts/synthetic.json"] if same else b"PREEXISTING"
    dest.write_bytes(data)
    with pytest.raises(bridge.Rejected, match="DESTINATION"):
        bridge.restore_stage(stage, workspace, keyroot)
    assert dest.read_bytes() == data
    assert not (keyroot / "governance-signing-public.pem").exists()


@pytest.mark.parametrize("part", ["manifest", "payload"])
def test_phase_b_rejects_staging_drift_using_reviewed_constants(
    bridge, monkeypatch, tmp_path, part
):
    stage, workspace, keyroot, _ = _stage(bridge, monkeypatch, tmp_path)
    path = stage / (
        "manifest-0.json" if part == "manifest" else "artifacts/contracts/synthetic.json"
    )
    path.write_bytes(path.read_bytes() + b"DRIFT")
    with pytest.raises(bridge.Rejected, match="HASH"):
        bridge.restore_stage(stage, workspace, keyroot)
    assert list(workspace.iterdir()) == []
    assert list(keyroot.iterdir()) == []


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../b", "C:/outside", "a\\b", "a//b"])
def test_path_traversal_alias_or_absolute_destinations_rejected(bridge, tmp_path, path):
    with pytest.raises(bridge.Rejected, match="PATH"):
        bridge.contained_path(tmp_path, path)


def test_real_exclusive_creation_rejects_intervening_destination(bridge, tmp_path):
    dest = tmp_path / "target"
    dest.write_bytes(b"ORIGINAL")
    with pytest.raises(bridge.Rejected, match="DESTINATION"):
        bridge.exclusive_write(dest, b"NEW")
    assert dest.read_bytes() == b"ORIGINAL"


@pytest.mark.parametrize("code", [0, 1, 7])
def test_capture_executes_real_script_preserves_exit_and_hides_failure_payload(
    bridge, tmp_path, capsys, code
):
    script = tmp_path / "command.sh"
    script.write_text(f"printf 'PRIVATE_SENTINEL ::error:: {{invalid-json\\n' >&2\nexit {code}\n")
    log = tmp_path / "private.log"
    bash = bridge.bash_path()
    rc = bridge.capture_script(script, log, environment={}, bash=bash)
    assert rc == code
    assert b"PRIVATE_SENTINEL" in log.read_bytes()
    assert capsys.readouterr().out == ""


def test_git_environment_drops_ambient_config_helpers_and_every_trace(bridge):
    env = bridge.git_environment(
        {
            "PATH": os.environ["PATH"],
            "GIT_TRACE_PACKET": "/private/trace",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.sshCommand",
            "GIT_DIR": "/wrong",
            "GIT_INDEX_FILE": "/wrong",
            "GIT_CURL_VERBOSE": "1",
        }
    )
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert not any(key.startswith("GIT_TRACE") for key in env)
    assert "GIT_CURL_VERBOSE" not in env
    assert "GIT_CONFIG_COUNT" not in env
    assert "GIT_DIR" not in env and "GIT_INDEX_FILE" not in env


def test_interpreter_identity_detects_changed_bytes(bridge, tmp_path):
    executable = tmp_path / "python"
    executable.write_bytes(b"INTERPRETER_A")
    original = bridge.file_identity(executable)
    executable.write_bytes(b"INTERPRETER_B")
    with pytest.raises(bridge.Rejected, match="INTERPRETER_DRIFT"):
        bridge.require_interpreter_identity(original, executable)


def test_true_site_packages_pth_and_sitecustomize_planting_are_disabled_by_no_site(tmp_path):
    """Actual subprocess control: -I alone loads site; -I -S does not."""
    envroot = tmp_path / "isolated-python"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(envroot)], check=True)
    executable = envroot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(
        subprocess.check_output(
            [str(executable), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
    )
    assert site.is_relative_to(envroot)
    pth_hit, custom_hit = tmp_path / "pth-hit", tmp_path / "custom-hit"
    (site / "synthetic.pth").write_text(
        f"import pathlib; pathlib.Path({str(pth_hit)!r}).write_text('hit')\n"
    )
    (site / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(custom_hit)!r}).write_text('hit')\n"
    )
    subprocess.run([str(executable), "-I", "-c", "pass"], check=True)
    assert pth_hit.is_file() and custom_hit.is_file(), "Planting positive control did not execute"
    pth_hit.unlink()
    custom_hit.unlink()
    subprocess.run([str(executable), "-I", "-S", "-c", "pass"], check=True)
    assert not pth_hit.exists() and not custom_hit.exists()


def test_workflow_has_no_cache_or_upload_after_overlay_and_reviews_post_hooks():
    steps = _check()["steps"]
    overlay = next((i for i, step in enumerate(steps) if step.get("id") == "private-overlay"), -1)
    assert overlay >= 0
    for step in steps:
        assert "cache" not in step.get("with", {})
        if "uses" in step:
            assert "@" in step["uses"] and len(step["uses"].split("@")[1]) == 40
    assert not any("uses" in step for step in steps[overlay:])


def test_every_check_service_is_pinned_to_immutable_registry_digest():
    services = _check()["services"]
    assert services
    for service in services.values():
        image = service["image"]
        assert "@sha256:" in image
        assert len(image.rsplit("@sha256:", 1)[1]) == 64


@pytest.fixture
def observed_fixture(bridge, tmp_path):
    repo = tmp_path / "git-repo"
    repo.mkdir()

    def git(*args, input=None):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "-C",
                    str(repo),
                    *args,
                ],
                input=input,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

    git("init")
    (repo / ".github/workflows").mkdir(parents=True)
    workflow = "jobs:\n  check:\n" + "".join(f"    - uses: {a}\n" for a in bridge.CHECK_ACTIONS)
    workflow += "  postgres-integration:\n"
    (repo / ".github/workflows/ci.yml").write_text(workflow)
    (repo / "scripts").mkdir()
    raw = b"# Synthetic bootstrap bytes, never executed\n"
    (repo / "scripts/ci_private_inputs.py").write_bytes(raw)
    git("add", ".github/workflows/ci.yml", "scripts/ci_private_inputs.py")
    tree = git("write-tree")
    base = git("commit-tree", tree, input=b"base\n")
    head = git("commit-tree", tree, "-p", base, input=b"head\n")
    merge = git("commit-tree", tree, "-p", base, "-p", head, input=b"merge\n")
    git("update-ref", "HEAD", merge)
    event = {
        "repository": {"id": 1129153696, "owner": {"id": 96863471}},
        "pull_request": {"head": {"sha": head}, "base": {"sha": base}},
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    environment = {
        "GITHUB_REPOSITORY": "albarami/IDIS",
        "GITHUB_JOB": "check",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/119/merge",
        "GITHUB_RUN_ID": "41",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": merge,
        "IDIS_WORKFLOW_SHA": merge,
        "IDIS_BOOTSTRAP_SHA256": hashlib.sha256(raw).hexdigest(),
        "GITHUB_EVENT_PATH": str(event_path),
    }
    responses = {
        "/pulls/119": {
            "state": "open",
            "merge_commit_sha": merge,
            "head": {"sha": head, "repo": {"id": 1129153696}},
            "base": {"sha": base, "repo": {"id": 1129153696}},
        },
        "/actions/runs/41": {
            "id": 41,
            "run_attempt": 2,
            "workflow_id": 221216319,
            "event": "pull_request",
            "path": ".github/workflows/ci.yml",
            "repository": {"id": 1129153696},
            "head_repository": {"id": 1129153696},
            "head_sha": head,
        },
        "/actions/runs/41/attempts/2": {"run_attempt": 2},
        "/actions/runs/41/attempts/2/jobs?per_page=100": {
            "total_count": 1,
            "jobs": [
                {
                    "id": 57,
                    "name": "check",
                    "status": "in_progress",
                    "check_run_url": "https://api.github.com/repos/albarami/IDIS/check-runs/59",
                }
            ],
        },
        "/environments/cir04-private-inputs": {"id": 7, "name": "cir04-private-inputs"},
    }
    return repo, environment, responses, git


def test_real_git_merge_tuple_and_offline_metadata_observation(bridge, observed_fixture):
    repo, environment, responses, _ = observed_fixture
    observed = bridge.observe(repo, environment, responses.__getitem__)
    assert observed["run_id"] == 41 and observed["run_attempt"] == 2
    assert observed["job_id"] == 57 and observed["check_run_id"] == 59
    assert observed["recovery_commit"] == bridge.RECOVERY_COMMIT


@pytest.fixture
def versioned_github_metadata(bridge, observed_fixture, monkeypatch):
    """Public API-shape mechanics, NOT hosted-token or canonical admission evidence."""
    repo, environment, responses, _ = observed_fixture
    expected = bridge.observe(repo, environment, responses.__getitem__)
    responses["/actions/runs/41/approvals"] = [_record(bridge, expected)]
    environment["IDIS_CI_READ_TOKEN"] = "synthetic-read-token"
    requests, reads, statuses = [], [], {}

    class Response(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            prefix = "https://api.github.com/repos/albarami/IDIS"
            assert request.full_url.startswith(prefix + "/")
            path = request.full_url.removeprefix(prefix)
            headers = {key.lower(): value for key, value in request.header_items()}
            version = headers["x-github-api-version"]
            if path in statuses or version not in {"2022-11-28", "2026-03-10"}:
                raise bridge.urllib.error.HTTPError(
                    request.full_url, statuses.get(path, 410), "Synthetic API rejection", None, None
                )
            row = json.loads(json.dumps(responses[path]))
            if path == "/pulls/119" and version == "2026-03-10":
                # GitHub's documented 2026 removal, independently observed on actual PR119.
                row.pop("merge_commit_sha", None)
            return Response(json.dumps(row).encode())

    def build_opener(*handlers):
        assert len(handlers) == 2
        assert isinstance(handlers[0], bridge.urllib.request.ProxyHandler)
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], bridge.NoRedirect)
        return Opener()

    monkeypatch.setattr(bridge.urllib.request, "build_opener", build_opener)
    return repo, environment, responses, expected, requests, reads, statuses


def test_supported_api_schema_reaches_exact_checked_observation(bridge, versioned_github_metadata):
    repo, environment, responses, expected, requests, reads, _ = versioned_github_metadata
    assert bridge.checked_observation(repo, environment) == expected
    assert [
        request.full_url.removeprefix("https://api.github.com/repos/albarami/IDIS")
        for request, _ in requests
    ] == list(responses)
    assert reads == [4_000_001] * 6


@pytest.mark.parametrize(
    "path",
    [
        "/pulls/119",
        "/actions/runs/41",
        "/actions/runs/41/attempts/2",
        "/actions/runs/41/attempts/2/jobs?per_page=100",
        "/environments/cir04-private-inputs",
        "/actions/runs/41/approvals",
    ],
)
def test_github_reader_explicit_supported_version_on_every_endpoint(
    bridge, versioned_github_metadata, path
):
    _, _, responses, _, requests, reads, _ = versioned_github_metadata
    assert bridge.github_reader("synthetic-read-token")(path) == responses[path]
    assert len(requests) == 1
    request, timeout = requests[0]
    assert isinstance(request, bridge.urllib.request.Request)
    assert request.full_url == "https://api.github.com/repos/albarami/IDIS" + path
    assert request.get_method() == "GET" and request.data is None
    assert {key.lower(): value for key, value in request.header_items()} == {
        "authorization": "Bearer synthetic-read-token",
        "accept": "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
    }
    assert timeout == 30 and reads == [4_000_001]


@pytest.mark.parametrize("merge", ["absent", None, "f" * 40])
def test_supported_api_still_refuses_unproven_merge_before_later_reads(
    bridge, versioned_github_metadata, merge
):
    repo, environment, responses, _, requests, reads, _ = versioned_github_metadata
    if merge == "absent":
        del responses["/pulls/119"]["merge_commit_sha"]
    else:
        responses["/pulls/119"]["merge_commit_sha"] = merge
    with pytest.raises(bridge.Rejected, match="^PR_CHANGED_OR_FORK$"):
        bridge.checked_observation(repo, environment)
    assert [request.full_url for request, _ in requests] == [
        "https://api.github.com/repos/albarami/IDIS/pulls/119"
    ]
    assert reads == [4_000_001]


@pytest.mark.parametrize(
    "path",
    [
        "/pulls/119",
        "/actions/runs/41",
        "/actions/runs/41/attempts/2",
        "/actions/runs/41/attempts/2/jobs?per_page=100",
        "/environments/cir04-private-inputs",
        "/actions/runs/41/approvals",
    ],
)
def test_github_reader_unsupported_api_response_never_falls_back_or_retries(
    bridge, versioned_github_metadata, path
):
    _, _, _, _, requests, reads, statuses = versioned_github_metadata
    statuses[path] = 410
    with pytest.raises(bridge.Rejected, match="^API_READ$") as failure:
        bridge.github_reader("synthetic-read-token")(path)
    assert isinstance(failure.value.__cause__, bridge.urllib.error.HTTPError)
    assert failure.value.__cause__.code == 410
    assert len(requests) == 1 and reads == []


@pytest.mark.parametrize(
    "mutation",
    ["fork", "head", "base", "merge", "attempt", "workflow", "job", "environment", "bootstrap"],
)
def test_changed_metadata_or_bootstrap_rejects_before_any_private_fetch(
    bridge, observed_fixture, mutation
):
    repo, env, responses, _ = observed_fixture
    if mutation == "fork":
        responses["/pulls/119"]["head"]["repo"]["id"] = 1
    elif mutation in {"head", "base"}:
        responses["/pulls/119"][mutation]["sha"] = "f" * 40
    elif mutation == "merge":
        responses["/pulls/119"]["merge_commit_sha"] = "f" * 40
    elif mutation == "attempt":
        responses["/actions/runs/41"]["run_attempt"] = 3
    elif mutation == "workflow":
        responses["/actions/runs/41"]["workflow_id"] = 1
    elif mutation == "job":
        responses["/actions/runs/41/attempts/2/jobs?per_page=100"]["jobs"].append(
            {"id": 58, "name": "check", "status": "in_progress"}
        )
    elif mutation == "environment":
        responses["/environments/cir04-private-inputs"]["name"] = "other"
    else:
        env["IDIS_BOOTSTRAP_SHA256"] = "f" * 64
    with pytest.raises(bridge.Rejected):
        bridge.observe(repo, env, responses.__getitem__)


def test_raw_git_blob_ignores_crlf_worktree_and_attributes(bridge, observed_fixture):
    repo, env, _, git = observed_fixture
    original = bridge.git_bytes(
        repo, "cat-file", "blob", env["GITHUB_SHA"] + ":scripts/ci_private_inputs.py"
    )
    (repo / "scripts/ci_private_inputs.py").write_bytes(original.replace(b"\n", b"\r\n"))
    (repo / ".gitattributes").write_text("*.py text eol=crlf filter=untrusted\n")
    git("config", "core.autocrlf", "true")
    assert (
        bridge.git_bytes(
            repo, "cat-file", "blob", env["GITHUB_SHA"] + ":scripts/ci_private_inputs.py"
        )
        == original
    )


@pytest.mark.parametrize("changed", ["attempt", "head", "bootstrap"])
def test_key_step_cheap_recheck_rejects_drift_before_transport(bridge, observed_fixture, changed):
    repo, env, responses, _ = observed_fixture
    observed = bridge.observe(repo, env, responses.__getitem__)
    if changed == "attempt":
        env["GITHUB_RUN_ATTEMPT"] = "3"
    elif changed == "head":
        env["GITHUB_SHA"] = "a" * 40
    else:
        env["IDIS_BOOTSTRAP_SHA256"] = "b" * 64
    with pytest.raises(bridge.Rejected, match="POST_SECRET_RECHECK"):
        bridge.cheap_recheck(repo, env, observed)


def test_cleanup_refuses_root_or_unowned_target_and_preserves_files(bridge, tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    protected = root / "unowned"
    protected.mkdir()
    (protected / "keep").write_bytes(b"ORIGINAL")
    for target in (root, protected, tmp_path):
        with pytest.raises(bridge.Rejected, match="CLEANUP"):
            bridge.delete_transport_tree(root, target)
    assert (protected / "keep").read_bytes() == b"ORIGINAL"


def test_cleanup_deletes_only_owned_bare_tree_not_peer_files(bridge, tmp_path):
    root = tmp_path / "private"
    bare = root / "recovery.git"
    (bare / "objects").mkdir(parents=True)
    (bare / "objects/object").write_bytes(b"SYNTHETIC")
    (root / "keep").write_bytes(b"ORIGINAL")
    bridge.delete_transport_tree(root, bare)
    assert not bare.exists()
    assert (root / "keep").read_bytes() == b"ORIGINAL"


def test_duplicate_json_fields_rejected_instead_of_ambiguously_authorizing(bridge):
    with pytest.raises(bridge.Rejected, match="METADATA_JSON"):
        bridge.decode('{"run_attempt":1,"run_attempt":2}')


def test_workflow_bootstrap_pin_matches_actual_raw_helper_not_self_certification():
    job = _check()
    assert (
        job["env"]["IDIS_BOOTSTRAP_SHA256"]
        == hashlib.sha256((ROOT / "scripts/ci_private_inputs.py").read_bytes()).hexdigest()
    )
    for step in job["steps"]:
        if step.get("id") in {"private-admission", "private-fetch", "private-overlay"}:
            assert step["run"].index("sha256sum --check --status") < step["run"].index(
                "/usr/bin/python3 -I -S"
            )


def test_bare_transport_and_key_cleanup_use_only_synthetic_local_git(
    bridge, observed_fixture, monkeypatch, tmp_path
):
    """Redirect ONLY network fetch to an owned local fixture; not SSH qualification."""
    repo, _, _, git = observed_fixture
    root = tmp_path / "transport"
    root.mkdir()
    payload = b"SYNTHETIC TRANSPORT BYTES\r\n"
    relative = "contracts/synthetic.json"
    (repo / "contracts").mkdir()
    (repo / relative).write_bytes(payload)
    raw = json.dumps(
        {
            "artifact_count": 1,
            "artifacts": [{"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}],
        }
    ).encode()
    (repo / "synthetic-manifest.json").write_bytes(raw)
    git("config", "core.autocrlf", "false")
    git("add", "contracts/synthetic.json", "synthetic-manifest.json")
    tree = git("write-tree")
    commit = git("commit-tree", tree, input=b"synthetic-recovery\n")
    monkeypatch.setattr(bridge, "RECOVERY_COMMIT", commit)
    monkeypatch.setattr(bridge, "RECOVERY_TREE", tree)
    monkeypatch.setattr(bridge, "ALLOWED_ARTIFACTS", frozenset({relative}))
    monkeypatch.setattr(
        bridge, "MANIFEST_PINS", {"synthetic-manifest.json": hashlib.sha256(raw).hexdigest()}
    )
    original_git = bridge.git_bytes
    calls = []

    def local_transport(target, *args, environment=None):
        calls.append(args)
        if args[0] == "fetch":
            assert args == (
                "fetch",
                "--no-tags",
                "--depth=1",
                "git@github.com:albarami/IDIS-private-recovery.git",
                commit,
            )
            assert "StrictHostKeyChecking=yes" in environment["GIT_SSH_COMMAND"]
            assert "IdentityAgent=none" in environment["GIT_SSH_COMMAND"]
            assert not any(key.startswith("GIT_TRACE") for key in environment)
            assert (root / "read-key").read_bytes() == b"SYNTHETIC-NOT-A-CREDENTIAL"
            if os.name == "posix":
                assert (root / "read-key").stat().st_mode & 0o077 == 0
            return subprocess.check_output(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "-C",
                    str(target),
                    "fetch",
                    "--no-tags",
                    "--depth=1",
                    str(repo),
                    commit,
                ],
                stderr=subprocess.DEVNULL,
            )
        return original_git(target, *args, environment=environment)

    monkeypatch.setattr(bridge, "git_bytes", local_transport)
    bridge.fetch_private(root, "SYNTHETIC-NOT-A-CREDENTIAL")
    assert (root / "stage/artifacts" / relative).read_bytes() == payload
    assert not (root / "read-key").exists()
    assert not (root / "known-hosts").exists()
    assert not (root / "recovery.git").exists()
    assert calls[0] == ("init", "--bare")
    assert not any(args[0] in {"checkout", "clone"} for args in calls)


def test_numeric_summary_extracts_only_bounded_aggregate_counts(bridge):
    assert bridge.numeric_counts(b"PRIVATE_PAYLOAD ::error:: not JSON") == {}
    assert bridge.numeric_counts(b"=== 10 passed, 2 failed, 3 skipped in 1.0s ===") == {
        "passed": 10,
        "failed": 2,
        "skipped": 3,
    }
    assert bridge.numeric_counts(b"PRIVATE 999999999999999999999999999 passed") == {}


@pytest.mark.parametrize(
    ("footer", "expected"),
    [
        (b"=== 1 error, 1 warning in 0.1s ===", {"errors": 1, "warnings": 1}),
        (
            b"=== 2 xfailed, 3 xpassed, 4 deselected, 5 errors, 6 warnings in 0.1s ===",
            {"xfailed": 2, "xpassed": 3, "deselected": 4, "errors": 5, "warnings": 6},
        ),
        (b"=== 999999999 error, 999999999 xfailed ===", {}),
        (b"PRIVATE 1 warning, 3 xfailed", {}),
    ],
)
def test_numeric_footer_preserves_all_reported_outcomes_without_payload(bridge, footer, expected):
    assert bridge.numeric_counts(footer) == expected


@pytest.mark.parametrize(
    "field",
    [
        "workflow_blob_sha256",
        "bootstrap_blob_sha256",
        "candidate_head_sha",
        "base_sha",
        "tested_checkout_sha",
        "tested_tree",
        "run_attempt",
        "job_id",
        "check_run_id",
        "environment_id",
        "recovery_commit",
        "recovery_tree",
        "recovery_manifests",
    ],
)
def test_any_observable_tuple_change_invalidates_the_old_genuine_approval(
    bridge, observed_fixture, field
):
    repo, env, responses, _ = observed_fixture
    observed = bridge.observe(repo, env, responses.__getitem__)
    record = _record(bridge, observed)
    changed = dict(observed)
    changed[field] = 3 if field == "run_attempt" else "changed-public-metadata"
    with pytest.raises(bridge.Rejected):
        bridge.require_approval([record], changed, 7)


def test_top_level_failure_funnel_never_emits_exception_text(bridge, monkeypatch, capsys):
    def fail_runtime():
        raise RuntimeError("PRIVATE_SENTINEL ::error::")

    monkeypatch.setattr(bridge, "require_runtime", fail_runtime)
    assert bridge.main(["admit"]) == 1
    output = capsys.readouterr()
    assert output.err == "" and "PRIVATE_SENTINEL" not in output.out
    assert json.loads(output.out) == bridge.safe_summary("admit", 1)


@pytest.mark.parametrize(("original", "expected"), [(0, 1), (1, 1), (7, 7)])
def test_fixture_summary_failure_preserves_nonzero_check_exit(
    bridge, monkeypatch, capsys, original, expected
):
    """Real Bash exits are tested separately; this fixture targets the summary failure funnel."""
    monkeypatch.setattr(bridge, "execute", lambda *_args: original)
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    assert bridge.main(["capture", "synthetic-command.sh"]) == expected
    assert json.loads(capsys.readouterr().out) == bridge.safe_summary("check", expected)


def test_real_directory_alias_rejects_without_touching_target(bridge, tmp_path):
    """Reuse Task4 namespace fixture's unprivileged junction/POSIX symlink pattern."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_bytes(b"ORIGINAL")
    alias = tmp_path / "alias"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(bridge.Rejected, match="PATH_ALIAS"):
            bridge.contained_path(tmp_path, "alias/keep")
    finally:
        # Delete ONLY this owned alias, never recurse or delete its target.
        alias.rmdir() if os.name == "nt" else alias.unlink()
    assert (target / "keep").read_bytes() == b"ORIGINAL"


def test_real_hardlink_rejected_without_changing_either_name(bridge, tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"ORIGINAL")
    linked = tmp_path / "linked"
    os.link(original, linked)
    with pytest.raises(bridge.Rejected, match="PATH_HARDLINK"):
        bridge.contained_path(tmp_path, "linked")
    assert original.read_bytes() == linked.read_bytes() == b"ORIGINAL"


def test_destination_created_after_preflight_is_preserved_exclusively(
    bridge, monkeypatch, tmp_path
):
    stage, workspace, keyroot, _ = _stage(bridge, monkeypatch, tmp_path)
    original_write = bridge.exclusive_write

    def race(path, raw):
        path.write_bytes(b"RACER")
        original_write(path, raw)

    monkeypatch.setattr(bridge, "exclusive_write", race)
    with pytest.raises(bridge.Rejected, match="DESTINATION"):
        bridge.restore_stage(stage, workspace, keyroot)
    assert (workspace / "contracts/synthetic.json").read_bytes() == b"RACER"
    assert not (keyroot / "governance-signing-public.pem").exists()


@pytest.fixture
def execute_chain(bridge, observed_fixture, monkeypatch, tmp_path):
    """Real Git/files/Bash with per-instance synthetic recovery, NEVER canonical acceptance."""
    workspace, environment, responses, _ = observed_fixture
    source = tmp_path / "synthetic-recovery"
    source.mkdir()
    payloads = {
        name: ("HERMETIC MECHANICS " + name + "\n").encode() for name in bridge.ALLOWED_ARTIFACTS
    }
    for name, raw in payloads.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    manifest = json.dumps(
        {
            "artifact_count": len(payloads),
            "artifacts": [
                {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}
                for name, raw in sorted(payloads.items())
            ],
        }
    ).encode()
    (source / "synthetic-manifest.json").write_bytes(manifest)

    def git(*args, input=None):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "-c",
                    "core.autocrlf=false",
                    "-C",
                    str(source),
                    *args,
                ],
                input=input,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

    git("init")
    git("add", "--force", ".")
    tree = git("write-tree")
    commit = git("commit-tree", tree, input=b"Hermetic recovery mechanics only\n")
    monkeypatch.setattr(bridge, "RECOVERY_COMMIT", commit)
    monkeypatch.setattr(bridge, "RECOVERY_TREE", tree)
    monkeypatch.setattr(
        bridge, "MANIFEST_PINS", {"synthetic-manifest.json": hashlib.sha256(manifest).hexdigest()}
    )
    original_git = bridge.git_bytes

    def local_transport(target, *args, environment=None):
        if args[0] == "fetch":
            assert args[-2:] == ("git@github.com:albarami/IDIS-private-recovery.git", commit)
            return subprocess.check_output(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "-C",
                    str(target),
                    "fetch",
                    "--no-tags",
                    "--depth=1",
                    str(source),
                    commit,
                ],
                stderr=subprocess.DEVNULL,
            )
        return original_git(target, *args, environment=environment)

    monkeypatch.setattr(bridge, "git_bytes", local_transport)
    runner = tmp_path / "runner-temp"
    root = runner / "idis-ci-41-2"
    root.mkdir(parents=True, mode=0o700)
    environment.update(
        {
            "RUNNER_TEMP": str(runner),
            "GITHUB_WORKSPACE": str(workspace),
            "IDIS_CI_READ_TOKEN": "SYNTHETIC-READ",
            "IDIS_RECOVERY_READ_KEY": "SYNTHETIC-NOT-A-CREDENTIAL",
        }
    )
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("RUNNER_DEBUG", "ACTIONS_STEP_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    # Test interpreter differs from the future Actions /usr/bin/python3: validate its REAL identity.
    identity = bridge.file_identity(Path(sys.executable))
    require_identity = bridge.require_interpreter_identity
    monkeypatch.setattr(bridge, "require_runtime", lambda: identity)
    monkeypatch.setattr(
        bridge,
        "require_interpreter_identity",
        lambda expected, _path: require_identity(expected, Path(sys.executable)),
    )
    monkeypatch.setattr(bridge, "github_reader", lambda _token: responses.__getitem__)
    observed = bridge.observe(workspace, environment, responses.__getitem__)
    responses["/actions/runs/41/approvals"] = [_record(bridge, observed)]
    # A real tiny fixture executable avoids running product make-check/private consumers.
    binaries = tmp_path / "fixture-bin"
    binaries.mkdir()
    make = binaries / "make"
    make.write_text("#!/bin/sh\nprintf 'HERMETIC_CAPTURE_SENTINEL\\n=== 1 passed ===\\n'\nexit 7\n")
    make.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    capture = bridge.capture_script
    monkeypatch.setattr(
        bridge,
        "capture_script",
        lambda script, log, *, environment, bash: capture(
            script, log, environment=environment, bash=bridge.bash_path()
        ),
    )
    monkeypatch.chdir(workspace)
    return root, workspace, responses, payloads


def _overlay_paths(bridge, root, workspace, payloads):
    return [
        root / "key-input/governance-signing-public.pem"
        if name == bridge.PUBLIC_KEY
        else workspace / name
        for name in sorted(payloads)
    ]


def test_execute_chain_roundtrip_capture_and_retire_only_ten_owned_inodes(
    bridge, execute_chain, tmp_path, capsys
):
    root, workspace, _, payloads = execute_chain
    peer = workspace / "keep-unrelated"
    peer.write_bytes(b"ORIGINAL")
    assert bridge.execute("admit") == 0
    admitted = json.loads((root / "admission.json").read_text())
    assert admitted["observed"]["run_attempt"] == 2
    assert bridge.execute("fetch") == 0
    assert not any((root / name).exists() for name in ("read-key", "known-hosts", "recovery.git"))
    assert bridge.execute("overlay") == 0
    paths = _overlay_paths(bridge, root, workspace, payloads)
    before = [(path.stat().st_dev, path.stat().st_ino) for path in paths]
    assert len(paths) == 10 and len(set(before)) == 10
    script = tmp_path / "actual-runner-script.sh"
    script.write_bytes(b"make check\n")
    assert bridge.execute("capture", script) == 7
    assert b"HERMETIC_CAPTURE_SENTINEL" in (root / "check.log").read_bytes()
    assert capsys.readouterr().out == ""
    assert bridge.execute("cleanup") == 0
    # Custody-safe retirement may retain zeroed owned paths until ephemeral runner teardown.
    assert all(not path.exists() or path.read_bytes() == b"" for path in paths)
    assert peer.read_bytes() == b"ORIGINAL"


@pytest.mark.parametrize(
    "failure", ["second-copy", "ownership-record", "journal-fsync", "payload-fsync"]
)
def test_partial_overlay_cleanup_retires_owned_bytes_without_touching_foreign_files(
    bridge, execute_chain, monkeypatch, failure
):
    root, workspace, _, payloads = execute_chain
    bridge.execute("admit")
    bridge.execute("fetch")
    original_write = bridge.exclusive_write
    original_sync = os.fsync
    writes = []
    journal_syncs = 0
    failed_sync = False

    def failing_write(path, raw, **kwargs):
        if path == root / "overlay-owned.json" and failure == "ownership-record":
            raise OSError("SYNTHETIC OWNERSHIP WRITE FAILURE")
        if path in _overlay_paths(bridge, root, workspace, payloads):
            writes.append(path)
            if len(writes) == 2 and failure == "second-copy":
                raise OSError("SYNTHETIC SECOND COPY FAILURE")
        return original_write(path, raw, **kwargs)

    monkeypatch.setattr(bridge, "exclusive_write", failing_write)

    def failing_sync(fd):
        nonlocal journal_syncs, failed_sync
        info = os.fstat(fd)
        journal = root / "overlay-created.jsonl"
        if journal.exists() and info.st_ino == journal.stat().st_ino:
            journal_syncs += 1
            if failure == "journal-fsync" and journal_syncs == 3:
                raise OSError("SYNTHETIC JOURNAL SYNC FAILURE")
        elif failure == "payload-fsync" and not failed_sync:
            failed_sync = True
            raise OSError("SYNTHETIC PAYLOAD SYNC FAILURE")
        return original_sync(fd)

    monkeypatch.setattr(os, "fsync", failing_sync)
    with pytest.raises(OSError, match="SYNTHETIC"):
        bridge.execute("overlay")
    monkeypatch.setattr(bridge, "exclusive_write", original_write)
    monkeypatch.setattr(os, "fsync", original_sync)
    bridge.execute("cleanup")
    assert all(
        not path.exists() or path.read_bytes() == b""
        for path in _overlay_paths(bridge, root, workspace, payloads)
    )


def test_cleanup_preserves_a_foreign_inode_swapped_at_final_retirement_boundary(
    bridge, execute_chain, monkeypatch
):
    root, workspace, _, payloads = execute_chain
    for mode in ("admit", "fetch", "overlay"):
        bridge.execute(mode)
    target = _overlay_paths(bridge, root, workspace, payloads)[0]
    owned = target.with_name("owned-retained")
    unlink = Path.unlink
    truncate = os.ftruncate
    swapped = False
    swap_blocked = False

    def swap():
        nonlocal swapped, swap_blocked
        if not swapped and not swap_blocked:
            try:
                target.rename(owned)
            except PermissionError as exc:
                if os.name != "nt" or exc.winerror != 32:
                    raise
                # Actual Windows held descriptors deny delete/rename sharing.
                swap_blocked = True
                return
            target.write_bytes(b"FOREIGN MUST SURVIVE")
            swapped = True

    def swap_before_unlink(path, *args, **kwargs):
        if path == target:
            swap()
        return unlink(path, *args, **kwargs)

    def swap_before_truncate(fd, length):
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (
            target.stat().st_dev,
            target.stat().st_ino,
        ):
            swap()
        return truncate(fd, length)

    monkeypatch.setattr(Path, "unlink", swap_before_unlink)
    monkeypatch.setattr(os, "ftruncate", swap_before_truncate)
    try:
        bridge.execute("cleanup")
    except bridge.Rejected as exc:
        assert str(exc) == "CLEANUP_OWNERSHIP"
    assert swapped or swap_blocked
    if swapped:
        assert target.read_bytes() == b"FOREIGN MUST SURVIVE"
        assert owned.read_bytes() == b""
    else:
        assert target.read_bytes() == b""


def test_overlay_fresh_approval_drift_stops_before_any_destination(bridge, execute_chain):
    root, workspace, responses, payloads = execute_chain
    bridge.execute("admit")
    bridge.execute("fetch")
    responses["/actions/runs/41/attempts/2/jobs?per_page=100"]["jobs"][0]["id"] = 58
    with pytest.raises(bridge.Rejected, match="APPROVAL"):
        bridge.execute("overlay")
    assert not (root / "key-input").exists()
    assert not any(path.exists() for path in _overlay_paths(bridge, root, workspace, payloads))


def test_cleanup_rejects_preexisting_inode_replacement_without_touching_it(bridge, execute_chain):
    root, workspace, _, payloads = execute_chain
    for mode in ("admit", "fetch", "overlay"):
        bridge.execute(mode)
    target = _overlay_paths(bridge, root, workspace, payloads)[0]
    target.rename(target.with_name("owned-original"))
    target.write_bytes(b"FOREIGN BEFORE CLEANUP")
    with pytest.raises(bridge.Rejected, match="CLEANUP_OWNERSHIP"):
        bridge.execute("cleanup")
    assert target.read_bytes() == b"FOREIGN BEFORE CLEANUP"


def test_execute_capture_denies_every_inherited_file_command_channel(
    bridge, execute_chain, monkeypatch, tmp_path
):
    root, workspace, _, _payloads = execute_chain
    for mode in ("admit", "fetch", "overlay"):
        bridge.execute(mode)
    command_root = root.parent / "_runner_file_commands"
    command_root.mkdir()
    names = (
        "GITHUB_ENV",
        "GITHUB_PATH",
        "GITHUB_OUTPUT",
        "GITHUB_STEP_SUMMARY",
        "GITHUB_STATE",
        "GITHUB_FUTURE_FILE_COMMAND",
        "GITHUB_RELATIVE_COMMAND",
        "ORDINARY_COMMAND_ALIAS",
    )
    targets = {}
    for name in names:
        target = command_root / name.lower()
        target.write_bytes(b"ORIGINAL")
        targets[name] = target
        value = (
            os.path.relpath(target, workspace) if name == "GITHUB_RELATIVE_COMMAND" else str(target)
        )
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_PUBLIC_CONTROL", "retained")
    observation = tmp_path / "channel-observation.json"
    code = (
        "import json,os,pathlib; "
        f"names={names!r}; "
        "present=[name for name in names if name in os.environ]; "
        "[(pathlib.Path(os.environ[name]).write_bytes(b'CHILD_WAS_HERE')) for name in present]; "
        f"pathlib.Path({str(observation)!r}).write_text(json.dumps({{"
        "'commands_present':present,'token_present':'IDIS_CI_READ_TOKEN' in os.environ,"
        "'ci':os.environ.get('GITHUB_ACTIONS'),'ordinary':os.environ.get('GITHUB_PUBLIC_CONTROL')}))"
    )
    make = Path(os.environ["PATH"].split(os.pathsep)[0]) / "make"
    make.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(Path(sys.executable).as_posix())
        + " -c "
        + shlex.quote(code)
        + "\n",
        newline="\n",
    )
    script = tmp_path / "capture-command.sh"
    script.write_bytes(b"make check\n")
    assert bridge.execute("capture", script) == 0
    assert all(target.read_bytes() == b"ORIGINAL" for target in targets.values())
    assert json.loads(observation.read_text()) == {
        "commands_present": [],
        "token_present": False,
        "ci": "true",
        "ordinary": "retained",
    }


def test_cleanup_retires_other_owned_files_despite_one_foreign_replacement(bridge, execute_chain):
    root, workspace, _, payloads = execute_chain
    for mode in ("admit", "fetch", "overlay"):
        bridge.execute(mode)
    targets = _overlay_paths(bridge, root, workspace, payloads)
    foreign = targets[0]
    foreign.rename(foreign.with_name("retained-owned-original"))
    foreign.write_bytes(b"FOREIGN MUST SURVIVE")
    with pytest.raises(bridge.Rejected, match="CLEANUP_OWNERSHIP"):
        bridge.execute("cleanup")
    assert foreign.read_bytes() == b"FOREIGN MUST SURVIVE"
    assert all(path.read_bytes() == b"" for path in targets[1:])


@pytest.mark.parametrize("kind", ["SIGINT", "SIGTERM", "KeyboardInterrupt"])
def test_interrupted_fetch_preserves_first_code_when_credential_cleanup_fails(
    bridge, execute_chain, monkeypatch, capsys, kind
):
    root, _workspace, _, _payloads = execute_chain
    bridge.execute("admit")
    unlink = Path.unlink
    git_bytes = bridge.git_bytes

    def fail_key_unlink(path, *args, **kwargs):
        if path == root / "read-key":
            raise OSError("SYNTHETIC_PRIVATE_CLEANUP_SENTINEL")
        return unlink(path, *args, **kwargs)

    def interrupted(path, *args, **kwargs):
        if path == root / "recovery.git":
            if kind == "KeyboardInterrupt":
                raise KeyboardInterrupt("SYNTHETIC_INTERRUPT_SENTINEL")
            raise bridge.Interrupted(getattr(signal, kind))
        return git_bytes(path, *args, **kwargs)

    monkeypatch.setattr(bridge, "git_bytes", interrupted)
    monkeypatch.setattr(Path, "unlink", fail_key_unlink)
    expected = 143 if kind == "SIGTERM" else 130
    assert bridge.main(["fetch"]) == expected
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        **bridge.safe_summary("fetch", expected),
        "cleanup_failed": True,
    }
    assert not (root / "known-hosts").exists()
    assert not (root / "recovery.git").exists()


@pytest.mark.parametrize("violation", ["hash", "hardlink", "missing"])
def test_independent_retirement_preserves_every_per_file_custody_check(
    bridge, execute_chain, violation
):
    root, workspace, _, payloads = execute_chain
    for mode in ("admit", "fetch", "overlay"):
        bridge.execute(mode)
    targets = _overlay_paths(bridge, root, workspace, payloads)
    target, peer = targets[0], targets[0].with_name("kept-peer")
    if violation == "hash":
        target.write_bytes(b"MODIFIED CONTENT MUST NOT BE TRUNCATED")
    elif violation == "hardlink":
        os.link(target, peer)
    else:
        target.rename(peer)
    original = target.read_bytes() if target.exists() else None
    peer_original = peer.read_bytes() if peer.exists() else None
    with pytest.raises(bridge.Rejected, match="CLEANUP_OWNERSHIP"):
        bridge.execute("cleanup")
    assert (target.read_bytes() if target.exists() else None) == original
    assert (peer.read_bytes() if peer.exists() else None) == peer_original
    assert all(path.read_bytes() == b"" for path in targets[1:])


def test_signal_fixture_cleanup_never_reloads_numeric_group_authority():
    """Structural safety regression, not a claim that PID reuse was forced in a live OS."""
    fixtures = (
        test_real_signals_close_output_stop_owned_group_and_preserve_peer,
        test_term_ignoring_descendant_stops_before_early_leader_is_reaped,
    )
    unsafe = []
    for fixture in fixtures:
        for node in ast.walk(ast.parse(inspect.getsource(fixture))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in {"getpgid", "killpg"}
            ):
                unsafe.append((fixture.__name__, node.func.attr))
    assert unsafe == [], "Fixture cleanup must use held pidfds, not stale PID/group lookup"


@pytest.mark.parametrize("phase", ["execute", "summary"])
def test_keyboard_interrupt_closes_output_with_exit_130(
    bridge, monkeypatch, tmp_path, capsys, phase
):
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    def interrupted(*_args):
        raise KeyboardInterrupt("SYNTHETIC_INTERRUPT_SENTINEL")

    if phase == "execute":
        monkeypatch.setattr(bridge, "execute", interrupted)
        args = ["admit"]
    else:
        root = tmp_path / "runner/idis-ci-41-2"
        root.mkdir(parents=True, mode=0o700)
        (root / "check.log").write_bytes(b"=== 1 passed ===\n")
        for name, value in {
            "RUNNER_TEMP": str(root.parent),
            "GITHUB_RUN_ID": "41",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_WORKSPACE": str(tmp_path / "workspace"),
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(bridge, "execute", lambda *_args: 7)
        monkeypatch.setattr(bridge, "numeric_counts", interrupted)
        args = ["capture", "synthetic-script.sh"]
    caught = None
    try:
        result = bridge.main(args)
    except KeyboardInterrupt:
        caught = "escaped"
        result = None
    assert caught is None, "KeyboardInterrupt escaped the closed public boundary"
    assert result == 130
    assert {number: signal.getsignal(number) for number in previous} == previous
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == bridge.safe_summary(
        "admit" if phase == "execute" else "check", 130
    )


def test_signal_cleanup_failure_is_visible_without_losing_signal_exit(bridge, monkeypatch, capsys):
    def cleanup_failed(*_args):
        raise bridge.Interrupted(signal.SIGTERM, cleanup_failed=True)

    monkeypatch.setattr(bridge, "execute", cleanup_failed)
    assert bridge.main(["admit"]) == 143
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {**bridge.safe_summary("admit", 143), "cleanup_failed": True}


@pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux process-group escalation")
def test_stubborn_owned_child_is_killed_bounded_and_unrelated_peer_survives(bridge, tmp_path):
    ready = tmp_path / "stubborn-ready"
    code = (
        "import pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"pathlib.Path({str(ready)!r}).touch();time.sleep(40)"
    )
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    owned = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", code],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and owned.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        stopped = True
        try:
            bridge.stop_capture_child(owned)
        except subprocess.TimeoutExpired:
            stopped = False
        assert stopped, "SIGTERM-ignoring owned child was not retired within the bound"
        actual_exit = owned.returncode
        assert actual_exit == -signal.SIGKILL
        assert peer.poll() is None
    finally:
        if owned.poll() is None:
            owned.kill()
        owned.wait(timeout=5)
        peer.terminate()
        peer.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux waitable child/group custody")
def test_nonowned_process_group_is_never_signalled(bridge, tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert os.getpgid(child.pid) != child.pid
        with pytest.raises(bridge.Rejected, match="CHILD_GROUP_CUSTODY"):
            bridge.stop_capture_child(child)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux waitid WNOWAIT leader custody")
def test_term_ignoring_descendant_stops_before_early_leader_is_reaped(bridge, tmp_path):
    ready, grand_ready, grand_id = (
        tmp_path / name for name in ("ready", "grand-ready", "grand-id")
    )
    code = (
        "import os,pathlib,signal,time\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"    pathlib.Path({str(grand_ready)!r}).touch()\n"
        "    while True: time.sleep(.05)\n"
        f"pathlib.Path({str(grand_id)!r}).write_text(str(child))\n"
        f"while not pathlib.Path({str(grand_ready)!r}).exists(): time.sleep(.01)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(40)\n"
    )
    owned = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", code],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    grand_handle = None
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and owned.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        # Read once at live readiness, before this fixture triggers any signal/reap.
        grand_handle = os.pidfd_open(int(grand_id.read_text()))
        bridge.stop_capture_child(owned)
        actual_exit = owned.returncode
        assert actual_exit == -signal.SIGTERM
        # A readable held pidfd proves exit, including zombie pending runner/init reaping.
        assert select.select([grand_handle], [], [], 2)[0] == [grand_handle]
    finally:
        try:
            if grand_handle is not None:
                try:
                    with suppress(ProcessLookupError):
                        signal.pidfd_send_signal(grand_handle, signal.SIGKILL)
                finally:
                    os.close(grand_handle)
        finally:
            if owned.poll() is None:
                owned.kill()
            owned.wait(timeout=5)


@pytest.mark.skipif(
    sys.platform != "linux", reason="Real Linux SIGINT/SIGTERM and owned process groups"
)
@pytest.mark.parametrize("phase", ["setup", "capture", "summary"])
@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
def test_real_signals_close_output_stop_owned_group_and_preserve_peer(tmp_path, phase, signal_name):
    ready = tmp_path / "ready"
    child_id = tmp_path / "child-id"
    grandchild_id = tmp_path / "grandchild-id"
    private_log = tmp_path / "private.log"
    runner = tmp_path / "runner/idis-ci-41-2"
    runner.mkdir(parents=True, mode=0o700)
    (runner / "check.log").write_bytes(b"=== 1 passed ===\n")
    program = tmp_path / "signal-boundary.py"
    child_code = (
        "import os,pathlib,signal,subprocess,sys,time\n"
        "grand=subprocess.Popen([sys.executable,'-c','import time; time.sleep(40)'])\n"
        "def stop(signum,_frame):\n"
        "    grand.terminate()\n"
        "    grand.wait(timeout=3)\n"
        "    raise SystemExit(128+signum)\n"
        "signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)\n"
        f"pathlib.Path({str(child_id)!r}).write_text(str(os.getpid()))\n"
        f"pathlib.Path({str(grandchild_id)!r}).write_text(str(grand.pid))\n"
        "print('SYNTHETIC_SIGNAL_SENTINEL ::error::',flush=True)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(.05)\n"
    )
    command = tmp_path / "child-command.sh"
    command.write_text(
        "exec " + shlex.quote(sys.executable) + " -I -S -c " + shlex.quote(child_code) + "\n"
    )
    source = (
        "import importlib.util,os,pathlib,sys,time\n"
        "spec=importlib.util.spec_from_file_location('signal_bridge',"
        f"{str(ROOT / 'scripts/ci_private_inputs.py')!r})\n"
        "bridge=importlib.util.module_from_spec(spec);sys.modules[spec.name]=bridge;spec.loader.exec_module(bridge)\n"
        "def wait_here(*_args):\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        "    while True: time.sleep(.05)\n"
    )
    if phase == "capture":
        source += (
            "bridge.execute=lambda *_args: bridge.capture_script("
            f"pathlib.Path({str(command)!r}),pathlib.Path({str(private_log)!r}),"
            "environment={'PATH':'/usr/bin:/bin'},bash='/bin/bash')\n"
        )
    elif phase == "setup":
        source += "bridge.execute=wait_here\n"
    else:
        source += "bridge.execute=lambda *_args: 7\nbridge.numeric_counts=wait_here\n"
    args = ["admit"] if phase == "setup" else ["capture", str(command)]
    source += f"raise SystemExit(bridge.main({args!r}))\n"
    program.write_text(source)
    environment = {
        "PATH": "/usr/bin:/bin",
        "RUNNER_TEMP": str(runner.parent),
        "GITHUB_RUN_ID": "41",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_WORKSPACE": str(tmp_path / "workspace"),
    }
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", str(program)],
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    handles = []
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Owned signal fixture did not reach its readiness boundary"
        if phase == "capture":
            # Both known children loop until this test sends its first signal.
            for identity in (child_id, grandchild_id):
                handles.append(os.pidfd_open(int(identity.read_text())))
        os.kill(process.pid, getattr(signal, signal_name))
        stdout, stderr = process.communicate(timeout=8)
        expected = 128 + getattr(signal, signal_name)
        actual_exit = process.returncode
        assert actual_exit == expected
        assert stderr == b""
        assert json.loads(stdout) == {
            "schema": "idis-ci-safe-summary-v1",
            "command": "admit" if phase == "setup" else "check",
            "exit_code": expected,
        }
        assert peer.poll() is None, "Unrelated peer was signalled"
        if phase == "capture":
            assert b"SYNTHETIC_SIGNAL_SENTINEL" in private_log.read_bytes()
            for handle in handles:
                with pytest.raises(ProcessLookupError):
                    signal.pidfd_send_signal(handle, 0)
    finally:
        try:
            for handle in handles:
                try:
                    with suppress(ProcessLookupError):
                        signal.pidfd_send_signal(handle, signal.SIGKILL)
                finally:
                    os.close(handle)
        finally:
            try:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
            finally:
                peer.terminate()
                peer.wait(timeout=5)


def _fifo_capture_program(tmp_path, *, gated_publication=False):
    """Only this fixture's leader/descendant program; optional deterministic metadata hold."""
    fifo, ready, release, identities = (
        tmp_path / name for name in ("fifo", "ready", "release", "ids")
    )
    delay = (
        f"pathlib.Path({str(identities)!r}).write_text('{{')\n"
        f"pathlib.Path({str(tmp_path / 'metadata-pending')!r}).touch()\n"
        f"while not pathlib.Path({str(tmp_path / 'allow-publication')!r}).exists():\n"
        "    time.sleep(.01)\n"
        if gated_publication
        else ""
    )
    descendant_code = (
        "import os,pathlib,signal,time\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"fd=os.open({str(fifo)!r},os.O_WRONLY)\n"
        f"if not pathlib.Path({str(tmp_path / 'identity-published')!r}).exists():\n"
        f"    pathlib.Path({str(tmp_path / 'waiting-for-publication')!r}).touch()\n"
        f"while not pathlib.Path({str(tmp_path / 'identity-published')!r}).exists():\n"
        "    time.sleep(.01)\n"
        "os.write(fd,b'READY')\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(.05)\n"
    )
    return (
        "import json,os,pathlib,signal,subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-I','-S','-c',{descendant_code!r}])\n"
        + delay
        + "blocked=signal.pthread_sigmask(signal.SIG_BLOCK,[])\n"
        f"pathlib.Path({str(identities)!r}).write_text(json.dumps({{"
        "'pid':child.pid,'ignored':[int(s) for s in (signal.SIGINT,signal.SIGTERM) "
        "if signal.getsignal(s)==signal.SIG_IGN],'blocked':[int(s) for s in blocked]}))\n"
        f"pathlib.Path({str(tmp_path / 'identity-published')!r}).touch()\n"
        f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
        "raise SystemExit(7)\n"
    )


def _retire_fifo_capture(bridge, children, handles):
    """Retire only descriptors and direct Popen children created by this FIFO fixture."""
    try:
        for handle in handles:
            try:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
            finally:
                os.close(handle)
    finally:
        for child in children:
            # Still-held new-session Popen, never a PID reloaded after reap.
            bridge.stop_capture_child(child)
            child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux FIFO/pidfd/waitid custody")
@pytest.mark.parametrize("window", ["normal-exit", "popen-return", "wnowait-return"])
def test_capture_keeps_leader_until_group_closed_at_every_exit_window(
    bridge, monkeypatch, tmp_path, capsys, window
):
    """Real child/descendant, with FIFO EOF independent of reusable numeric identities."""
    fifo, ready, release, identities = (
        tmp_path / name for name in ("fifo", "ready", "release", "ids")
    )
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    leader_code = _fifo_capture_program(tmp_path)
    script = tmp_path / "capture.sh"
    script.write_text(
        "exec " + shlex.quote(sys.executable) + " -I -S -c " + shlex.quote(leader_code)
    )
    real_popen, real_waitpid = subprocess.Popen, os.waitpid
    real_waitid, real_killpg = os.waitid, os.killpg
    peer = real_popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    children, handles, events = [], [], []
    signalled = False

    def create(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Writer did not reach live readiness"
        details = json.loads(identities.read_text())
        assert details["ignored"] == details["blocked"] == []
        # The direct child waits on release and the descendant loops until signalled.
        handles.append(os.pidfd_open(child.pid))
        handles.append(os.pidfd_open(details["pid"]))
        release.touch()
        if window == "popen-return":
            os.kill(os.getpid(), signal.SIGTERM)
        return child

    def waitid(which, identity, flags):
        nonlocal signalled
        result = real_waitid(which, identity, flags)
        if flags & os.WNOWAIT and not flags & os.WNOHANG and window == "wnowait-return":
            signalled = True
            os.kill(os.getpid(), signal.SIGTERM)
        return result

    def killpg(group, number):
        events.append(("kill", group, number))
        return real_killpg(group, number)

    def waitpid(identity, flags):
        if children and identity == children[0].pid:
            events.append(("reap", identity, flags))
        return real_waitpid(identity, flags)

    monkeypatch.setattr(bridge.subprocess, "Popen", create)
    monkeypatch.setattr(bridge.os, "waitid", waitid)
    monkeypatch.setattr(bridge.os, "waitpid", waitpid)
    monkeypatch.setattr(bridge.os, "killpg", killpg)
    monkeypatch.setattr(
        bridge,
        "execute",
        lambda *_args: bridge.capture_script(
            script,
            tmp_path / "private.log",
            environment={"PATH": "/usr/bin:/bin"},
            bash="/bin/bash",
        ),
    )
    try:
        expected = 7 if window == "normal-exit" else 143
        assert bridge.main(["admit"]) == expected
        output = capsys.readouterr()
        assert output.err == ""
        assert json.loads(output.out) == bridge.safe_summary("admit", expected)
        assert peer.poll() is None, "Unrelated live peer must survive"
        if window == "wnowait-return":
            assert signalled, "Blocking WNOWAIT boundary was never reached"
        eof = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if select.select([reader], [], [], 0.05)[0] and not os.read(reader, 4096):
                eof = True
                break
        assert eof, "A surviving descendant still holds the FIFO writer"
        kills = [
            i for i, event in enumerate(events) if event[0] == "kill" and event[2] == signal.SIGKILL
        ]
        reaps = [i for i, event in enumerate(events) if event[0] == "reap"]
        assert kills and reaps and max(kills) < min(reaps), events
    finally:
        try:
            _retire_fifo_capture(bridge, children, handles)
        finally:
            try:
                peer.terminate()
                peer.wait(timeout=5)
            finally:
                os.close(reader)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual fixture publication barrier/FIFO")
@pytest.mark.parametrize("failure", ["partial-identities", "setup-failure"])
def test_fifo_fixture_closes_pre_readiness_failure_without_reusable_pid_lookup(
    bridge, tmp_path, failure
):
    fifo, ready = tmp_path / "fifo", tmp_path / "ready"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", _fifo_capture_program(tmp_path, gated_publication=True)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 8
        waiting = tmp_path / "waiting-for-publication"
        while time.monotonic() < deadline:
            if (tmp_path / "metadata-pending").exists() and (ready.exists() or waiting.exists()):
                break
            time.sleep(0.01)
        assert (tmp_path / "metadata-pending").exists()
        assert ready.exists() or waiting.exists(), "Descendant did not reach publication boundary"
        assert (tmp_path / "ids").read_text() == "{", "Metadata hold must be real, not a mock"
        if failure == "partial-identities":
            assert waiting.exists() and not ready.exists(), "Readiness escaped incomplete metadata"
            (tmp_path / "allow-publication").touch()
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            details = json.loads((tmp_path / "ids").read_text())
            handles = [os.pidfd_open(child.pid)]
            try:
                handles.append(os.pidfd_open(details["pid"]))
                (tmp_path / "release").touch()
            finally:
                _retire_fifo_capture(bridge, [child], handles)
        else:
            with pytest.raises(RuntimeError, match="SYNTHETIC_SETUP_FAILURE"):
                try:
                    raise RuntimeError("SYNTHETIC_SETUP_FAILURE")
                finally:
                    # No pidfd or complete identities exist yet: direct Popen custody is held.
                    _retire_fifo_capture(bridge, [child], [])
        assert select.select([reader], [], [], 2)[0]
        while os.read(reader, 4096):
            assert select.select([reader], [], [], 2)[0]
        assert peer.poll() is None, "Unrelated peer must survive target retirement"
    finally:
        try:
            # Independent rescue for an intentionally failing retirement-helper RED.
            # This Popen was never reaped before its group was retired; no file PID lookup.
            bridge.stop_capture_child(child)
            child.wait(timeout=5)
        finally:
            try:
                peer.terminate()
                peer.wait(timeout=5)
            finally:
                os.close(reader)


def _repeat_signal_fixture(bridge, monkeypatch, tmp_path, script, *, first=None, setup=None):
    """The N1 constructor boundary, with outer containment for actual repeat-signal regressions."""
    ready, identities = tmp_path / "ready", tmp_path / "ids"
    real_popen, summary = subprocess.Popen, bridge.safe_summary
    children, handles = [], []

    def create(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(
            child
        )  # Hold custody before readiness, metadata or descriptor setup can fail.
        if setup is not None:
            setup(child)
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Writer did not reach live readiness"
        details = json.loads(identities.read_text())
        assert details["ignored"] == details["blocked"] == []
        handles.append(os.pidfd_open(child.pid))
        handles.append(os.pidfd_open(details["pid"]))
        if first is not None:
            os.kill(os.getpid(), getattr(signal, first))
        return child

    def repeat(*args):
        if first is not None:
            os.kill(os.getpid(), signal.SIGTERM if first == "SIGINT" else signal.SIGINT)
        return summary(*args)

    monkeypatch.setattr(bridge.subprocess, "Popen", create)
    monkeypatch.setattr(bridge, "safe_summary", repeat)
    monkeypatch.setattr(
        bridge,
        "execute",
        lambda *_args: bridge.capture_script(
            script,
            tmp_path / "private.log",
            environment={"PATH": "/usr/bin:/bin"},
            bash="/bin/bash",
        ),
    )
    result, caught = None, None
    try:
        try:
            result = bridge.main(["admit"])
        except (bridge.Interrupted, KeyboardInterrupt) as exc:
            caught = exc  # Contain test-runner cancellation, but the caller MUST assert no escape.
        return result, caught
    finally:
        _retire_fifo_capture(bridge, children, handles)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual repeat signals after capture unwind")
@pytest.mark.parametrize("first_name", ["SIGINT", "SIGTERM"])
def test_repeated_signal_at_capture_summary_keeps_first_exit_and_closed_output(
    bridge, monkeypatch, tmp_path, capsys, first_name
):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    script = tmp_path / "capture.sh"
    script.write_text(
        "exec "
        + shlex.quote(sys.executable)
        + " -I -S -c "
        + shlex.quote(_fifo_capture_program(tmp_path))
    )
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    summary = bridge.safe_summary
    try:
        result, caught = _repeat_signal_fixture(
            bridge, monkeypatch, tmp_path, script, first=first_name
        )
        expected = 128 + getattr(signal, first_name)
        assert caught is None, "A repeated cancellation escaped the closed main boundary"
        assert result == expected, "A repeated signal replaced the first exit"
        output = capsys.readouterr()
        assert output.err == ""
        assert json.loads(output.out) == summary("admit", expected)
        assert peer.poll() is None
        assert select.select([reader], [], [], 2)[0]
        while os.read(reader, 4096):
            assert select.select([reader], [], [], 2)[0]
    finally:
        try:
            peer.terminate()
            peer.wait(timeout=5)
        finally:
            os.close(reader)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual repeat-fixture early custody/FIFO")
@pytest.mark.parametrize(
    "failure",
    [
        "pre-ready-exception",
        "incomplete-metadata",
        "readiness-timeout",
        "partial-pidfd-setup",
    ],
)
def test_repeat_fixture_retires_child_before_failed_construction_handoff(
    bridge, monkeypatch, tmp_path, capsys, failure
):
    fifo, ready = tmp_path / "fifo", tmp_path / "ready"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    script = tmp_path / "capture.sh"
    script.write_text(
        "exec "
        + shlex.quote(sys.executable)
        + " -I -S -c "
        + shlex.quote(_fifo_capture_program(tmp_path, gated_publication=True))
    )
    real_popen, real_killpg, real_waitpid = subprocess.Popen, os.killpg, os.waitpid
    real_pidfd_open, real_close = os.pidfd_open, os.close
    peer = real_popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    rescue, events, opened, closed = [], [], [], []
    summary = bridge.safe_summary

    def construct(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        rescue.append(child)  # Separate pass-through rescue, consulted ONLY after assertions.
        return child

    def setup(_child):
        deadline = time.monotonic() + 8
        while (
            not (
                (tmp_path / "metadata-pending").exists()
                and (tmp_path / "waiting-for-publication").exists()
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert (tmp_path / "waiting-for-publication").exists()
        assert (tmp_path / "ids").read_text() == "{" and not ready.exists()
        assert not select.select([reader], [], [], 0)[0], "FIFO writer is not held live"
        if failure == "pre-ready-exception":
            raise RuntimeError("SYNTHETIC_PRE_READY_FAILURE")
        if failure == "incomplete-metadata":
            json.loads((tmp_path / "ids").read_text())
        if failure == "readiness-timeout":
            deadline = time.monotonic() + 0.1
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not ready.exists()
            raise TimeoutError("SYNTHETIC_READINESS_TIMEOUT")
        (tmp_path / "allow-publication").touch()

    def pidfd_open(identity, *args):
        if failure == "partial-pidfd-setup" and opened:
            raise OSError("SYNTHETIC_SECOND_PIDFD_FAILURE")
        handle = real_pidfd_open(identity, *args)
        opened.append(handle)
        return handle

    def close(handle):
        real_close(handle)
        if handle in opened:
            closed.append(handle)

    def killpg(group, number):
        events.append(("kill", group, number))
        return real_killpg(group, number)

    def waitpid(identity, flags):
        if rescue and identity == rescue[0].pid:
            events.append(("reap", identity, flags))
        return real_waitpid(identity, flags)

    monkeypatch.setattr(bridge.subprocess, "Popen", construct)
    monkeypatch.setattr(bridge.os, "pidfd_open", pidfd_open)
    monkeypatch.setattr(bridge.os, "close", close)
    monkeypatch.setattr(bridge.os, "killpg", killpg)
    monkeypatch.setattr(bridge.os, "waitpid", waitpid)
    try:
        result, caught = _repeat_signal_fixture(bridge, monkeypatch, tmp_path, script, setup=setup)
        assert result == 1 and caught is None
        output = capsys.readouterr()
        assert output.err == "" and json.loads(output.out) == summary("admit", 1)
        assert peer.poll() is None
        eof = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if select.select([reader], [], [], 0.05)[0] and not os.read(reader, 4096):
                eof = True
                break
        assert eof, "A live FIFO writer survived failed fixture construction handoff"
        kills = [
            i for i, event in enumerate(events) if event[0] == "kill" and event[2] == signal.SIGKILL
        ]
        reaps = [i for i, event in enumerate(events) if event[0] == "reap"]
        assert kills and reaps and max(kills) < min(reaps), events
        assert closed == opened
        assert len(opened) == int(failure == "partial-pidfd-setup")
    finally:
        try:
            _retire_fifo_capture(bridge, rescue, [])
        finally:
            try:
                peer.terminate()
                peer.wait(timeout=5)
            finally:
                os.close(reader)


_CLEANUP_SIGNAL_PROBE = r"""
import importlib.util,json,os,pathlib,signal,subprocess,sys
cfg=json.loads(pathlib.Path(sys.argv[1]).read_text())
spec=importlib.util.spec_from_file_location('bridge',cfg['helper'])
bridge=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=bridge
spec.loader.exec_module(bridge)
root,workspace=pathlib.Path(cfg['root']),pathlib.Path(cfg['workspace'])
bridge.RECOVERY_COMMIT=cfg['commit']
bridge.RECOVERY_TREE=cfg['tree']
bridge.MANIFEST_PINS=cfg['manifests']
first=getattr(signal,cfg['signal']) if cfg['signal'] else None
other=signal.SIGTERM if first==signal.SIGINT else signal.SIGINT
sent=False
def send(number):
    with pathlib.Path(cfg['events']).open('a') as stream:
        stream.write(json.dumps({'signal':int(number)})+'\n')
    os.kill(os.getpid(),number)
def hit(point):
    global sent
    if point==cfg['point'] and first is not None:
        sent=True
        for number in (first,other,first): send(number)
summary=bridge.safe_summary
def repeat(*args):
    if sent: send(other)
    return summary(*args)
bridge.safe_summary=repeat
if cfg['action']=='fetch':
    original_git=bridge.git_bytes
    def local_git(path,*args,environment=None):
        global sent
        if args[0]=='fetch':
            raw=subprocess.check_output(['git','-c','protocol.file.allow=always','-C',str(path),
                'fetch','--no-tags','--depth=1',cfg['source'],cfg['commit']],stderr=subprocess.DEVNULL)
        else:
            raw=original_git(path,*args,environment=environment)
        if cfg['outcome']=='interrupted' and args[0]=='rev-parse':
            sent=True
            send(first)
        return raw
    bridge.git_bytes=local_git
    unlink=pathlib.Path.unlink
    def remove(path,*args,**kwargs):
        if path==root/'read-key':
            hit('first-key')
            if cfg['fault']: raise OSError('SYNTHETIC_CLEANUP_FAULT')
        elif path==root/'known-hosts': hit('later-key')
        return unlink(path,*args,**kwargs)
    pathlib.Path.unlink=remove
    transport=bridge.delete_transport_tree
    def remove_transport(*args):
        hit('transport')
        return transport(*args)
    bridge.delete_transport_tree=remove_transport
    def execute(*_args):
        bridge.fetch_private(root,'SYNTHETIC-NOT-A-CREDENTIAL')
        pathlib.Path(cfg['continued']).touch()
        return 0
    mode='fetch'
else:
    identities={tuple(pair) for pair in cfg['identities']}
    truncate,sync,close=os.ftruncate,os.fsync,os.close
    truncated=False
    count=0
    def owned(fd):
        info=os.fstat(fd)
        return (info.st_dev,info.st_ino) in identities
    def retire(fd,length):
        global count,truncated
        if owned(fd):
            count+=1
            hit('truncate-first' if count==1 else 'truncate-middle' if count==3 else '')
            if cfg['fault'] and count==3: raise OSError('SYNTHETIC_RETIREMENT_FAULT')
            truncated=True
        return truncate(fd,length)
    def fsync(fd):
        if owned(fd): hit('fsync')
        return sync(fd)
    def fdclose(fd):
        if truncated and owned(fd): hit('close')
        return close(fd)
    os.ftruncate,os.fsync,os.close=retire,fsync,fdclose
    def execute(*_args):
        bridge.remove_overlay(root,workspace)
        pathlib.Path(cfg['continued']).touch()
        return 0
    mode='cleanup'
bridge.execute=execute
raise SystemExit(bridge.main([mode]))
"""


def _cleanup_probe(bridge, execute_chain, tmp_path, *, action, outcome, point, first, fault=False):
    root, workspace, _, payloads = execute_chain
    identities = []
    paths = []
    if action == "overlay":
        for mode in ("admit", "fetch", "overlay"):
            bridge.execute(mode)
        paths = _overlay_paths(bridge, root, workspace, payloads)
        identities = [(path.stat().st_dev, path.stat().st_ino) for path in paths]
        if outcome == "foreign":
            paths[0].rename(paths[0].with_name("retained-original"))
            paths[0].write_bytes(b"FOREIGN MUST SURVIVE")
    peer_file = workspace / "unrelated-cancellation-peer"
    peer_file.write_bytes(b"UNRELATED")
    program, config = tmp_path / "cleanup-probe.py", tmp_path / "cleanup-probe.json"
    program.write_text(_CLEANUP_SIGNAL_PROBE)
    continued, events = tmp_path / "continued", tmp_path / "signals.jsonl"
    config.write_text(
        json.dumps(
            {
                "helper": str(ROOT / "scripts/ci_private_inputs.py"),
                "root": str(root),
                "workspace": str(workspace),
                "commit": bridge.RECOVERY_COMMIT,
                "tree": "0" * 40 if outcome == "rejected" else bridge.RECOVERY_TREE,
                "manifests": bridge.MANIFEST_PINS,
                "source": str(tmp_path / "synthetic-recovery"),
                "action": action,
                "outcome": outcome,
                "point": point,
                "signal": first,
                "fault": fault,
                "identities": identities,
                "continued": str(continued),
                "events": str(events),
            }
        )
    )
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(program), str(config)],
            capture_output=True,
            timeout=15,
        )
        expected = (
            128 + getattr(signal, first) if first else int(outcome in {"rejected", "foreign"})
        )
        assert result.returncode == expected
        assert result.stderr == b""
        if action == "fetch":
            assert (root / "read-key").exists() is fault
            assert not (root / "known-hosts").exists()
            assert not (root / "recovery.git").exists()
        else:
            for index, path in enumerate(paths):
                if outcome == "foreign" and index == 0:
                    assert path.read_bytes() == b"FOREIGN MUST SURVIVE"
                elif fault and index == 2:
                    assert path.read_bytes() != b""
                else:
                    assert path.read_bytes() == b""
        summary = bridge.safe_summary("fetch" if action == "fetch" else "cleanup", expected)
        if first and (fault or outcome == "foreign"):
            summary["cleanup_failed"] = True
        assert json.loads(result.stdout) == summary
        assert continued.exists() is (first is None and outcome not in {"rejected", "foreign"})
        assert peer.poll() is None
        assert peer_file.read_bytes() == b"UNRELATED"
        if first:
            requested = [json.loads(line)["signal"] for line in events.read_text().splitlines()]
            assert requested[0] == getattr(signal, first) and len(requested) >= 4
        return root, paths
    finally:
        peer.terminate()
        peer.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual first/repeat signals in fetch finally")
@pytest.mark.parametrize("first", ["SIGINT", "SIGTERM"])
@pytest.mark.parametrize(
    ("outcome", "point", "fault"),
    [
        ("success", "first-key", False),
        ("success", "transport", False),
        ("rejected", "later-key", False),
        ("interrupted", "first-key", False),
        ("success", "first-key", True),
    ],
)
def test_first_signal_in_fetch_cleanup_finishes_owned_work_and_never_continues(
    bridge, execute_chain, tmp_path, first, outcome, point, fault
):
    root, _ = _cleanup_probe(
        bridge,
        execute_chain,
        tmp_path,
        action="fetch",
        outcome=outcome,
        point=point,
        first=first,
        fault=fault,
    )
    assert (root / "read-key").exists() is fault
    assert not (root / "known-hosts").exists()
    assert not (root / "recovery.git").exists()


@pytest.mark.skipif(
    sys.platform != "linux", reason="Actual first/repeat signals in overlay retirement"
)
@pytest.mark.parametrize("first", ["SIGINT", "SIGTERM"])
@pytest.mark.parametrize(
    ("outcome", "point", "fault"),
    [
        ("success", "truncate-first", False),
        ("success", "fsync", False),
        ("success", "close", False),
        ("foreign", "truncate-middle", False),
        ("success", "truncate-middle", True),
    ],
)
def test_first_signal_in_overlay_cleanup_finishes_verified_inodes_and_preserves_foreign(
    bridge, execute_chain, tmp_path, first, outcome, point, fault
):
    _, paths = _cleanup_probe(
        bridge,
        execute_chain,
        tmp_path,
        action="overlay",
        outcome=outcome,
        point=point,
        first=first,
        fault=fault,
    )
    for index, path in enumerate(paths):
        if outcome == "foreign" and index == 0:
            assert path.read_bytes() == b"FOREIGN MUST SURVIVE"
        elif fault and index == 2:
            assert path.read_bytes() != b""
        else:
            assert path.read_bytes() == b""


@pytest.mark.skipif(sys.platform != "linux", reason="Same isolated worker no-cancellation controls")
@pytest.mark.parametrize(
    ("action", "outcome"),
    [
        ("fetch", "success"),
        ("fetch", "rejected"),
        ("overlay", "success"),
        ("overlay", "foreign"),
    ],
)
def test_cleanup_without_signal_preserves_success_and_rejection(
    bridge, execute_chain, tmp_path, action, outcome
):
    _cleanup_probe(
        bridge,
        execute_chain,
        tmp_path,
        action=action,
        outcome=outcome,
        point="unused",
        first=None,
    )


_ENTRY_SIGNAL_EXTENSION = r"""
import ast,inspect
previous_hit=hit
def hit(point):
    if point==cfg['point'] and first is not None and not sent:
        key,known,bare=root/'read-key',root/'known-hosts',root/'recovery.git'
        nonempty=sum((workspace/p).is_file() and (workspace/p).stat().st_size>0
                     for p in bridge.ALLOWED_ARTIFACTS if p!=bridge.PUBLIC_KEY)
        pathlib.Path(cfg['observed']).write_text(json.dumps({
            'key_size':key.stat().st_size if key.exists() else 0,
            'known_size':known.stat().st_size if known.exists() else 0,
            'bare_exists':bare.exists(),'bare_files':sum(p.is_file() for p in bare.rglob('*')),
            'nonempty_overlay':nonempty}))
    return previous_hit(point)
production_execute=bridge.execute
validated=False
root_check=bridge.private_root
def checked_root(*args):
    global validated
    value=root_check(*args)
    validated=True
    hit('overlay-root-return')
    return value
bridge.private_root=checked_root
bridge.require_runtime=lambda: {}
os.environ.update(GITHUB_WORKSPACE=str(workspace),RUNNER_TEMP=str(root.parent),
                  GITHUB_RUN_ID='41',GITHUB_RUN_ATTEMPT='2')
original_signal=signal.signal
installs=0
def install(number,handler):
    global installs
    if getattr(handler,'__name__','')=='record':
        eligible=all((root/name).exists() for name in ('read-key','known-hosts','recovery.git'))
        eligible|=cfg['action']=='overlay' and validated
        if eligible:
            installs+=1
            hit(('fetch-' if cfg['action']=='fetch' else 'overlay-')+'handler'+str(installs))
    return original_signal(number,handler)
signal.signal=install
mkdir=pathlib.Path.mkdir
def create_directory(path,*args,**kwargs):
    value=mkdir(path,*args,**kwargs)
    if path==root/'recovery.git': hit('bare-return')
    return value
pathlib.Path.mkdir=create_directory
if cfg['action']=='fetch':
    git=bridge.git_bytes
    def count_git(*args,**kwargs):
        with pathlib.Path(cfg['launches']).open('a') as stream: stream.write('GIT\n')
        return git(*args,**kwargs)
    bridge.git_bytes=count_git
else:
    execute=production_execute
guard_code=bridge.deferred_owned_cleanup.__wrapped__.__code__
writer_code=bridge.exclusive_write.__code__
fetch_code=bridge.fetch_private.__code__
tree=ast.parse(inspect.getsource(bridge.fetch_private))
final_lines={fetch_code.co_firstlineno+node.finalbody[0].lineno-1
             for node in ast.walk(tree) if isinstance(node,ast.Try) and node.finalbody}
def trace(frame,event,arg):
    if sent: return None
    name=frame.f_code.co_name
    owned=all((root/name).exists() for name in ('read-key','known-hosts','recovery.git'))
    if cfg['point']=='fetch-outer' and owned and (
        frame.f_code is fetch_code and event=='line' and frame.f_lineno in final_lines
        or name=='cleanup_transport' and event=='call'):
        hit('fetch-outer')
    if cfg['point']=='fetch-guard' and owned and event=='call' and (
        frame.f_code is guard_code or name=='cleanup_transport'):
        hit('fetch-guard')
    if (cfg['point'] in {'fetch-handler1','fetch-handler2'} and owned
        and name=='cleanup_transport' and event=='call'):
        hit(cfg['point'])
    if (cfg['point']=='key-return' and frame.f_code is writer_code and event=='return'
        and frame.f_locals.get('path')==root/'read-key'):
        hit('key-return')
    if (cfg['point']=='overlay-entry' and cfg['action']=='overlay' and event=='call'
        and name in {'remove_overlay','remove_overlay_owned'}):
        hit('overlay-entry')
    return trace
sys.settrace(trace)
if cfg['point']=='overlay-open':
    original_open=os.open
    def acquire(path,*args,**kwargs):
        fd=original_open(path,*args,**kwargs)
        if pathlib.Path(path) in {workspace/relative for relative in bridge.ALLOWED_ARTIFACTS
                                  if relative!=bridge.PUBLIC_KEY}:
            hit('overlay-open')
        return fd
    os.open=acquire
bridge.execute=execute
raise SystemExit(bridge.main([mode]))
"""


def _entry_signal_probe(bridge, execute_chain, tmp_path, *, action, outcome, point, first, fault):
    root, workspace, _, payloads = execute_chain
    paths, identities = [], []
    if action == "overlay":
        for mode in ("admit", "fetch", "overlay"):
            bridge.execute(mode)
        paths = _overlay_paths(bridge, root, workspace, payloads)
        identities = [(path.stat().st_dev, path.stat().st_ino) for path in paths]
        if outcome == "foreign":
            paths[0].rename(paths[0].with_name("retained-original"))
            paths[0].write_bytes(b"FOREIGN MUST SURVIVE")
        assert all(path.read_bytes() for path in paths)
    peer_file = workspace / "entry-peer"
    peer_file.write_bytes(b"UNRELATED")
    program, config = tmp_path / "entry-probe.py", tmp_path / "entry-probe.json"
    terminal = "bridge.execute=execute\nraise SystemExit(bridge.main([mode]))\n"
    assert _CLEANUP_SIGNAL_PROBE.count(terminal) == 1
    program.write_text(_CLEANUP_SIGNAL_PROBE.replace(terminal, _ENTRY_SIGNAL_EXTENSION))
    events, launches, continued, observed = (
        tmp_path / name for name in ("events", "launches", "continued", "observed")
    )
    config.write_text(
        json.dumps(
            {
                "helper": str(ROOT / "scripts/ci_private_inputs.py"),
                "root": str(root),
                "workspace": str(workspace),
                "commit": bridge.RECOVERY_COMMIT,
                "tree": "0" * 40 if outcome == "rejected" else bridge.RECOVERY_TREE,
                "manifests": bridge.MANIFEST_PINS,
                "source": str(tmp_path / "synthetic-recovery"),
                "action": action,
                "outcome": outcome,
                "point": point,
                "signal": first,
                "fault": fault,
                "identities": identities,
                "events": str(events),
                "launches": str(launches),
                "continued": str(continued),
                "observed": str(observed),
            }
        )
    )
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(program), str(config)],
            capture_output=True,
            timeout=20,
        )
        material = json.loads(observed.read_text())
        if action == "overlay":
            assert material["nonempty_overlay"] == 9, "Injection must precede owned-byte retirement"
        else:
            assert material["key_size"] > 0, "Injection must follow real key payload creation"
            if point != "key-return":
                assert material["known_size"] > 0 and material["bare_exists"]
                assert (material["bare_files"] == 0) is (point == "bare-return")
        if action == "fetch":
            assert (root / "read-key").exists() is fault
            assert not (root / "known-hosts").exists()
            assert not (root / "recovery.git").exists()
        else:
            for index, path in enumerate(paths):
                if outcome == "foreign" and index == 0:
                    assert path.read_bytes() == b"FOREIGN MUST SURVIVE"
                elif fault and index == 2:
                    assert path.read_bytes()
                else:
                    assert path.read_bytes() == b""
        expected = 128 + getattr(signal, first)
        assert result.returncode == expected and result.stderr == b""
        summary = bridge.safe_summary("fetch" if action == "fetch" else "cleanup", expected)
        if fault or outcome == "foreign":
            summary["cleanup_failed"] = True
        assert json.loads(result.stdout) == summary
        requested = [json.loads(line)["signal"] for line in events.read_text().splitlines()]
        assert requested[0] == getattr(signal, first) and len(requested) >= 4
        assert not continued.exists()
        if point in {"key-return", "bare-return"}:
            assert not launches.exists(), "Cancellation must precede ANY later Git launch"
        assert peer.poll() is None and peer_file.read_bytes() == b"UNRELATED"
    finally:
        peer.terminate()
        peer.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual lifecycle-entry SIGINT/SIGTERM custody")
@pytest.mark.parametrize("first", ["SIGINT", "SIGTERM"])
@pytest.mark.parametrize(
    ("action", "outcome", "point", "fault"),
    [
        ("fetch", "success", "fetch-outer", False),
        ("fetch", "rejected", "fetch-outer", False),
        ("fetch", "success", "fetch-guard", False),
        ("fetch", "success", "fetch-handler1", False),
        ("fetch", "success", "fetch-handler2", False),
        ("fetch", "success", "key-return", False),
        ("fetch", "success", "bare-return", False),
        ("overlay", "success", "overlay-entry", False),
        ("overlay", "foreign", "overlay-entry", False),
        ("overlay", "success", "overlay-entry", True),
        ("overlay", "success", "overlay-root-return", False),
        ("overlay", "success", "overlay-handler1", False),
        ("overlay", "success", "overlay-handler2", False),
        ("overlay", "success", "overlay-open", False),
    ],
)
def test_entry_cancellation_cannot_skip_owned_cleanup_or_acquisition_registration(
    bridge, execute_chain, tmp_path, first, action, outcome, point, fault
):
    _entry_signal_probe(
        bridge,
        execute_chain,
        tmp_path,
        action=action,
        outcome=outcome,
        point=point,
        first=first,
        fault=fault,
    )


_COMMITTED_GIT_EXTENSION = r"""
import shlex
def committed_git(path,*args,environment=None):
    with pathlib.Path(cfg['launches']).open('a') as stream: stream.write('COMMITTED\n')
    if pathlib.Path(cfg['completed']).exists():
        pathlib.Path(cfg['subsequent']).touch()
        raise bridge.Rejected('SYNTHETIC_SUBSEQUENT_OPERATION')
    if cfg['window']=='before-call':
        send(first)
    command='!exec '+shlex.quote(sys.executable)+' -I -S '+shlex.quote(cfg['barrier'])
    command+=' '+shlex.quote(sys.argv[1])
    raw=original_git(path,'-c','alias.cir04probe='+command,'cir04probe',environment=environment)
    pathlib.Path(cfg['completed']).touch()
    return raw
bridge.git_bytes=committed_git
raise SystemExit(bridge.main([mode]))
"""


@pytest.mark.skipif(
    sys.platform != "linux", reason="Actual Git/checkpoint/signal-inheritance barrier"
)
@pytest.mark.parametrize("window", ["before-call", "in-flight"])
@pytest.mark.parametrize("first", ["SIGINT", "SIGTERM"])
def test_one_committed_git_operation_finishes_before_next_checkpoint_cancels(
    bridge, execute_chain, tmp_path, window, first
):
    """A checkpoint is a finite commitment, NOT an atomic handler-to-exec frontier."""
    root, workspace, _, _ = execute_chain
    paths = {
        name: tmp_path / name
        for name in ("ready", "release", "states", "events", "launches", "completed", "subsequent")
    }
    barrier, program, config = (tmp_path / name for name in ("barrier.py", "committed.py", "cfg"))
    barrier.write_text(
        "import json,os,pathlib,signal,sys,time\n"
        "cfg=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
        "numbers=(signal.SIGINT,signal.SIGTERM)\n"
        "state={'git_pid':os.getppid(),"
        "'ignored':[int(n) for n in numbers if signal.getsignal(n)==signal.SIG_IGN],"
        "'blocked':[int(n) for n in numbers if n in signal.pthread_sigmask(signal.SIG_BLOCK,[])]}\n"
        "pathlib.Path(cfg['states']).write_text(json.dumps(state))\n"
        "if cfg['window']=='in-flight': os.kill(cfg['owner_pid'],getattr(signal,cfg['signal']))\n"
        "pathlib.Path(cfg['ready']).touch()\n"
        "deadline=time.monotonic()+8\n"
        "while not pathlib.Path(cfg['release']).exists():\n"
        " if time.monotonic()>deadline: raise SystemExit(2)\n"
        " time.sleep(.01)\n"
    )
    terminal = "bridge.execute=execute\nraise SystemExit(bridge.main([mode]))\n"
    assert _CLEANUP_SIGNAL_PROBE.count(terminal) == 1
    program.write_text(
        _CLEANUP_SIGNAL_PROBE.replace(
            terminal, "bridge.execute=execute\n" + _COMMITTED_GIT_EXTENSION
        )
    )
    config_data = {
        "helper": str(ROOT / "scripts/ci_private_inputs.py"),
        "root": str(root),
        "workspace": str(workspace),
        "commit": bridge.RECOVERY_COMMIT,
        "tree": bridge.RECOVERY_TREE,
        "manifests": bridge.MANIFEST_PINS,
        "action": "fetch",
        "outcome": "success",
        "point": "never",
        "fault": False,
        "signal": first,
        "continued": str(tmp_path / "continued"),
        "window": window,
        "barrier": str(barrier),
        **{name: str(path) for name, path in paths.items()},
    }
    config.write_text(json.dumps(config_data))
    # The leader waits for its configuration before any Git/private ownership begins.
    launcher = (
        "import json,os,pathlib,runpy,sys,time; p=pathlib.Path(sys.argv[1]);"
        " cfg=json.loads(p.read_text());cfg['owner_pid']=os.getpid();"
        "p.write_text(json.dumps(cfg));sys.argv=[sys.argv[2],str(p)];runpy.run_path(sys.argv[0],"
        "run_name='__main__')"
    )
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child, held = None, None
    try:
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", launcher, str(config), str(program)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        held = os.pidfd_open(child.pid)  # Direct Popen custody exists even if this setup fails.
        deadline = time.monotonic() + 8
        while not paths["ready"].exists() and time.monotonic() < deadline:
            assert not select.select([held], [], [], 0)[0], (
                "Committed Git exited before its barrier"
            )
            time.sleep(0.01)
        assert paths["ready"].exists()
        state = json.loads(paths["states"].read_text())
        assert state["ignored"] == state["blocked"] == []
        # Read only while the held invocation's child is blocked in its live Git alias.
        status = Path(f"/proc/{state['git_pid']}/status").read_text()
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        for field in ("SigIgn", "SigBlk"):
            bits = int(fields[field].strip(), 16)
            assert not any(bits & (1 << (number - 1)) for number in (signal.SIGINT, signal.SIGTERM))
        assert not select.select([held], [], [], 0)[0], "Pending cancellation must wait for one Git"
        assert not paths["completed"].exists()
        paths["release"].touch()
        assert select.select([held], [], [], 8)[0], "No next checkpoint resolved cancellation"
        # Keep the direct leader unreaped until its entire invocation-owned group is retired.
        bridge.stop_capture_child(child)
        out, err = child.communicate(timeout=5)
        expected = 128 + getattr(signal, first)
        assert child.returncode == expected and err == b""
        assert json.loads(out) == bridge.safe_summary("fetch", expected)
        assert paths["completed"].exists(), "The single committed real Git operation did not finish"
        assert paths["launches"].read_text().splitlines() == ["COMMITTED"]
        assert not paths["subsequent"].exists() and not Path(config_data["continued"]).exists()
        assert list((root / "stage").iterdir()) == [], (
            "No manifest/payload staging after cancellation"
        )
        assert all(
            not (root / name).exists() for name in ("read-key", "known-hosts", "recovery.git")
        )
        assert peer.poll() is None
    finally:
        try:
            if child is not None and child.returncode is None:
                bridge.stop_capture_child(child)
                child.communicate(timeout=5)
        finally:
            if held is not None:
                os.close(held)
            peer.terminate()
            peer.wait(timeout=5)


_MANIFEST_READ_EXTENSION = r"""
git=bridge.git_bytes
def counted_git(*args,**kwargs):
    with pathlib.Path(cfg['launches']).open('a') as stream: stream.write(args[1]+'\n')
    return git(*args,**kwargs)
bridge.git_bytes=counted_git
reader=bridge.read_regular
reads=[]
def counted_read(path):
    if path.parent==root/'stage' and path.name in bridge.MANIFEST_PINS:
        reads.append(path.name)
        pathlib.Path(cfg['reads']).write_text(json.dumps(reads))
        if len(reads)==1 and cfg['window']=='before-call': hit('manifest-read')
        raw=reader(path)
        if len(reads)==1:
            pathlib.Path(cfg['material']).write_text(str(len(raw)))
            if cfg['window']=='after-read': hit('manifest-read')
        return raw
    return reader(path)
bridge.read_regular=counted_read
bridge.execute=execute
raise SystemExit(bridge.main([mode]))
"""


@pytest.mark.skipif(sys.platform != "linux", reason="Actual per-manifest read signal checkpoints")
@pytest.mark.parametrize("window", ["before-call", "after-read"])
@pytest.mark.parametrize("first", ["SIGINT", "SIGTERM"])
def test_manifest_read_checkpoint_stops_second_read_and_payload_work(
    bridge, execute_chain, monkeypatch, tmp_path, window, first
):
    """Two real local-Git manifests, per-instance mechanics NOT canonical recovery."""
    root, workspace, _, payloads = execute_chain
    source = tmp_path / "synthetic-recovery"
    pins = {}
    rows = sorted(payloads.items())
    for name, group in (("first.json", rows[:5]), ("second.json", rows[5:])):
        raw = json.dumps(
            {
                "artifact_count": len(group),
                "artifacts": [
                    {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
                    for path, data in group
                ],
            }
        ).encode()
        (source / name).write_bytes(raw)
        pins[name] = hashlib.sha256(raw).hexdigest()

    def git(*args, input=None):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "-c",
                    "core.autocrlf=false",
                    "-C",
                    str(source),
                    *args,
                ],
                input=input,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

    git("add", "--force", "first.json", "second.json")
    tree = git("write-tree")
    commit = git("commit-tree", tree, input=b"Hermetic two-manifest checkpoint mechanics only\n")
    monkeypatch.setattr(bridge, "MANIFEST_PINS", pins)
    terminal = "bridge.execute=execute\nraise SystemExit(bridge.main([mode]))\n"
    assert _CLEANUP_SIGNAL_PROBE.count(terminal) == 1
    program, config = tmp_path / "manifest-probe.py", tmp_path / "manifest-config"
    program.write_text(_CLEANUP_SIGNAL_PROBE.replace(terminal, _MANIFEST_READ_EXTENSION))
    files = {
        name: tmp_path / name for name in ("reads", "launches", "material", "events", "continued")
    }
    config.write_text(
        json.dumps(
            {
                "helper": str(ROOT / "scripts/ci_private_inputs.py"),
                "root": str(root),
                "workspace": str(workspace),
                "source": str(source),
                "commit": commit,
                "tree": tree,
                "manifests": pins,
                "action": "fetch",
                "outcome": "success",
                "point": "manifest-read",
                "signal": first,
                "fault": False,
                "window": window,
                **{name: str(path) for name, path in files.items()},
            }
        )
    )
    foreign = workspace / "manifest-unrelated"
    foreign.write_bytes(b"UNRELATED MUST SURVIVE")
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(program), str(config)], capture_output=True, timeout=20
        )
        expected = 128 + getattr(signal, first)
        assert result.returncode == expected and result.stderr == b""
        assert json.loads(result.stdout) == bridge.safe_summary("fetch", expected)
        assert int(files["material"].read_text()) > 0, "The committed first real read must complete"
        assert not files["continued"].exists()
        assert files["launches"].read_text().splitlines() == [
            "init",
            "fetch",
            "rev-parse",
            "cat-file",
            "cat-file",
        ]
        assert not (root / "stage/artifacts").exists(), "No payload staging after cancellation"
        assert all(
            not (root / name).exists() for name in ("read-key", "known-hosts", "recovery.git")
        )
        assert peer.poll() is None and foreign.read_bytes() == b"UNRELATED MUST SURVIVE"
        # Default cleanup/overlay callers still validate BOTH manifests without cancellation gates.
        assert bridge.manifest_artifacts(root / "stage") == {
            name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()
        }
        assert json.loads(files["reads"].read_text()) == ["first.json"], (
            "Pending cancellation must stop the second manifest read, "
            "not batch both behind one check"
        )
    finally:
        peer.terminate()
        peer.wait(timeout=5)


def _literal_runner_shell_argv(mode, script):
    """Qualify this finite grammar, NOT emulate/claim GitHub server validation.

    actions/runner9abfe11 ScriptHandler splits once, formats {0}, and launches
    ProcessStartInfo.Arguments without a shell. .NET Unix uses double quotes,
    not POSIX single quotes. Restrict this fixture to our literal -c grammar.
    """
    name = (
        "Run all checks"
        if mode == "capture"
        else "Derive labelled v27 staging bytes with existing tools"
    )
    step = next(s for s in _check()["steps"] if s.get("name") == name)
    command, arguments = step["shell"].split(" ", 1)
    assert "${{" not in arguments, "steps.shell cannot use unavailable expression contexts"
    assert command == "/usr/bin/python3"
    assert arguments.count("{0}") == 1
    formatted = arguments.format(str(script).replace('"', '\\"'))
    parsed = re.fullmatch(r'-I -S -c "([^"\\]*)" "((?:\\"|[^"])*)"', formatted)
    assert parsed is not None, "Only the reviewed double-quoted literal runner grammar is allowed"
    code, script_argument = parsed.groups()
    return [command, "-I", "-S", "-c", code, script_argument.replace('\\"', '"')]


@pytest.mark.parametrize("mode", ["prepare", "capture"])
def test_custom_shell_is_literal_and_preserves_runner_script_argument(mode):
    script = Path('/tmp/runner space/step "quoted" script')
    argv = _literal_runner_shell_argv(mode, script)
    assert argv[-1] == str(script)
    assert argv[:4] == ["/usr/bin/python3", "-I", "-S", "-c"]
    code = ast.parse(argv[4])
    assert {node.id for node in ast.walk(code) if isinstance(node, ast.Name)} <= {"os", "sys", "p"}
    assert "os.execv('/usr/bin/python3'" in argv[4]
    assert "'capture-bootstrap.py'" in argv[4] and repr(mode) in argv[4]
    assert "RUNNER_TEMP" in argv[4] and "GITHUB_RUN_ID" in argv[4]
    assert "GITHUB_RUN_ATTEMPT" in argv[4]


def test_custom_shell_predecessor_verifies_same_owned_bootstrap_before_either_mode():
    steps = _check()["steps"]
    overlay = next(i for i, step in enumerate(steps) if step.get("id") == "private-overlay")
    raw = steps[overlay]["run"]
    assert raw.index('"$root/overlay-bootstrap.py" overlay') < raw.index(
        '> "$root/capture-bootstrap.py"'
    )
    assert raw.index('> "$root/capture-bootstrap.py"') < raw.index(
        '"$root/capture-bootstrap.py" | sha256sum --check --status'
    )
    assert 'root="$RUNNER_TEMP/idis-ci-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"' in raw
    assert 'cat-file blob "$GITHUB_SHA:scripts/ci_private_inputs.py"' in raw
    for mode in ("prepare", "capture"):
        argv = _literal_runner_shell_argv(mode, Path("/tmp/actual runner script"))
        name = (
            "Run all checks"
            if mode == "capture"
            else "Derive labelled v27 staging bytes with existing tools"
        )
        assert next(i for i, step in enumerate(steps) if step.get("name") == name) > overlay
        assert "os.environ['RUNNER_TEMP']" in argv[4]
    assert (
        _check()["env"]["IDIS_BOOTSTRAP_SHA256"]
        == hashlib.sha256((ROOT / "scripts/ci_private_inputs.py").read_bytes()).hexdigest()
    )


@pytest.fixture
def literal_shell_invocation(bridge, observed_fixture, monkeypatch, tmp_path):
    """Actual helper/Git/interpreter; synthetic metadata admission, NEVER platform approval."""
    workspace, environment, responses, git = observed_fixture
    raw = (ROOT / "scripts/ci_private_inputs.py").read_bytes()
    (workspace / "scripts/ci_private_inputs.py").write_bytes(raw)
    git("add", "scripts/ci_private_inputs.py")
    tree = git("write-tree")
    base, head = git("rev-parse", "HEAD^1"), git("rev-parse", "HEAD^2")
    merge = git(
        "commit-tree", tree, "-p", base, "-p", head, input=b"Literal shell mechanics only\n"
    )
    git("update-ref", "HEAD", merge)
    environment.update(
        GITHUB_SHA=merge,
        IDIS_WORKFLOW_SHA=merge,
        IDIS_BOOTSTRAP_SHA256=hashlib.sha256(raw).hexdigest(),
        GITHUB_WORKSPACE=str(workspace),
        RUNNER_TEMP=str(tmp_path / "runner temp with spaces"),
        IDIS_CI_READ_TOKEN="SYNTHETIC-READ",
    )
    responses["/pulls/119"]["merge_commit_sha"] = merge
    root = Path(environment["RUNNER_TEMP"]) / "idis-ci-41-2"
    root.mkdir(parents=True, mode=0o700)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("RUNNER_DEBUG", "ACTIONS_STEP_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        bridge, "require_runtime", lambda: bridge.file_identity(Path("/usr/bin/python3"))
    )
    monkeypatch.setattr(bridge, "github_reader", lambda _token: responses.__getitem__)
    observed = bridge.observe(workspace, environment, responses.__getitem__)
    responses["/actions/runs/41/approvals"] = [_record(bridge, observed)]
    assert bridge.execute("admit") == 0
    extracted = subprocess.check_output(
        ["git", "-C", str(workspace), "cat-file", "blob", merge + ":scripts/ci_private_inputs.py"]
    )
    assert hashlib.sha256(extracted).hexdigest() == _check()["env"]["IDIS_BOOTSTRAP_SHA256"]
    bridge.exclusive_write(root / "capture-bootstrap.py", extracted)
    return root, workspace


@pytest.mark.skipif(sys.platform != "linux", reason="Literal Actions /usr/bin/python3 -I -S exec")
@pytest.mark.parametrize("mode", ["prepare", "capture"])
def test_literal_custom_shell_runs_real_helper_spaced_paths_and_private_nonzero(
    literal_shell_invocation, monkeypatch, tmp_path, mode
):
    root, workspace = literal_shell_invocation
    binaries = tmp_path / "synthetic-bin"
    binaries.mkdir()
    make = binaries / "make"
    make.write_text("#!/bin/sh\nprintf 'PRIVATE_LITERAL_SENTINEL\\n=== 1 passed ===\\n'\nexit 7\n")
    make.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    script = tmp_path / 'runner step with spaces and "quotes"'
    script.write_bytes(b"make check\n" if mode == "capture" else b"make labelled-preparation\n")
    result = subprocess.run(
        _literal_runner_shell_argv(mode, script), cwd=workspace, capture_output=True, timeout=10
    )
    assert result.returncode == 7 and result.stderr == b""
    summary = json.loads(result.stdout)
    assert summary["command"] == ("check" if mode == "capture" else "prepare")
    assert summary["exit_code"] == 7 and summary["counts"] == {"passed": 1}
    assert b"PRIVATE_LITERAL_SENTINEL" not in result.stdout
    assert b"PRIVATE_LITERAL_SENTINEL" in (root / (summary["command"] + ".log")).read_bytes()


@pytest.mark.skipif(sys.platform != "linux", reason="Literal shell exec PID and isolated startup")
@pytest.mark.parametrize("mode", ["prepare", "capture"])
def test_literal_custom_shell_exec_replaces_same_pid_without_ambient_startup(tmp_path, mode):
    runner = tmp_path / "runner temp with spaces"
    root = runner / "idis-ci-41-2"
    root.mkdir(parents=True)
    output, marker = tmp_path / "identity.json", tmp_path / "ambient-ran"
    # A separate labelled launcher-only probe; real helper execution is tested above.
    (root / "capture-bootstrap.py").write_text(
        "import json,os,pathlib,sys\n"
        f"pathlib.Path({str(output)!r}).write_text(json.dumps([os.getpid(),sys.argv,sys.flags.isolated,sys.flags.no_site]))\n"
    )
    (tmp_path / "sitecustomize.py").write_text(f"open({str(marker)!r},'w').close()\n")
    environment = os.environ.copy()
    environment.update(
        RUNNER_TEMP=str(runner),
        GITHUB_RUN_ID="41",
        GITHUB_RUN_ATTEMPT="2",
        PYTHONPATH=str(tmp_path),
    )
    script = tmp_path / "runner script with spaces"
    child = subprocess.Popen(
        _literal_runner_shell_argv(mode, script),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0 and stdout == stderr == b""
        identity = json.loads(output.read_text())
        assert identity == [
            child.pid,
            [str(root / "capture-bootstrap.py"), mode, str(script)],
            1,
            1,
        ]
        assert not marker.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(
    sys.platform != "linux", reason="Literal shell real signals/FIFO/pidfd peer custody"
)
@pytest.mark.parametrize("mode", ["prepare", "capture"])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_literal_custom_shell_signals_are_closed_and_retire_only_owned_process(
    literal_shell_invocation, monkeypatch, tmp_path, mode, signum
):
    root, workspace = literal_shell_invocation
    fifo, identities = tmp_path / "writer-fifo", tmp_path / "live-identity.json"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    code = (
        "import json,os,pathlib,signal,time\n"
        "print('PRIVATE_LITERAL_SIGNAL',flush=True)\n"
        f"pathlib.Path({str(identities)!r}).write_text(json.dumps([os.getpid(),"
        "[int(s) for s in (signal.SIGINT,signal.SIGTERM) if signal.getsignal(s)==signal.SIG_IGN],"
        "[int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK,[])]]))\n"
        f"fd=os.open({str(fifo)!r},os.O_WRONLY)\nos.write(fd,b'READY')\n"
        "while True: time.sleep(.05)\n"
    )
    binaries = tmp_path / "signal-bin"
    binaries.mkdir()
    make = binaries / "make"
    make.write_text("#!/bin/sh\nexec /usr/bin/python3 -I -S -c " + shlex.quote(code) + "\n")
    make.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    script = tmp_path / "runner signal script with spaces"
    script.write_bytes(b"make check\n" if mode == "capture" else b"make labelled-preparation\n")
    peer = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(40)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child = None
    held = None
    try:
        child = subprocess.Popen(
            _literal_runner_shell_argv(mode, script),
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + 8
        ready = b""
        while ready != b"READY" and time.monotonic() < deadline:
            if select.select([reader], [], [], 0.05)[0]:
                ready += os.read(reader, 5)
            if child.poll() is not None:
                break
        assert ready == b"READY", "Real captured child did not reach live FIFO readiness"
        identity, ignored, blocked = json.loads(identities.read_text())
        held = os.pidfd_open(
            identity
        )  # Metadata published BEFORE live FIFO readiness, never after reap.
        assert ignored == blocked == []
        child.send_signal(signum)
        stdout, stderr = child.communicate(timeout=8)
        expected = 128 + signum
        assert child.returncode == expected and stderr == b""
        assert json.loads(stdout) == {
            "schema": "idis-ci-safe-summary-v1",
            "command": "check" if mode == "capture" else "prepare",
            "exit_code": expected,
        }
        assert b"PRIVATE_LITERAL_SIGNAL" not in stdout
        assert select.select([held], [], [], 2)[0] == [held]
        assert select.select([reader], [], [], 2)[0] == [reader] and os.read(reader, 1) == b""
        assert peer.poll() is None
        log = root / ("check.log" if mode == "capture" else "prepare.log")
        assert b"PRIVATE_LITERAL_SIGNAL" in log.read_bytes()
    finally:
        try:
            if child is not None:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        finally:
            try:
                if held is not None:
                    with suppress(ProcessLookupError):
                        signal.pidfd_send_signal(held, signal.SIGKILL)
                    os.close(held)
            finally:
                os.close(reader)
                peer.terminate()
                peer.wait(timeout=5)
