"""RED contract for Task-4 safe-report/ledger namespace binding."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _gate() -> Any:
    return importlib.import_module("idis.evaluation.real_example_gate")


def _launcher() -> Any:
    return importlib.import_module("idis.runtime.strict_launcher")


def _initial_argv(*, root: Path, report_dir: Path, ledger_path: Path) -> list[str]:
    return [
        "--root",
        str(root),
        "--api-base-url",
        "http://127.0.0.1:8000",
        "--api-key-env",
        "IDIS_REAL_EXAMPLE_API_KEY",
        "--canary-api-key-env",
        "IDIS_ISOLATION_CANARY_API_KEY",
        "--require-full-live",
        "--prepare-data-rooms",
        "--preflight-only",
        "--verify-tenant-isolation",
        "--company-mode",
        "isolated",
        "--expected-company-count",
        "2",
        "--expected-file-counts",
        "58,209",
        "--expected-total-files",
        "267",
        "--safe-report-dir",
        str(report_dir),
        "--prepare-ledger",
        str(ledger_path),
        "--no-secret-output",
    ]


def _restart_argv(*, root: Path, report_dir: Path, ledger_path: Path) -> list[str]:
    return [
        "--root",
        str(root),
        "--api-base-url",
        "http://127.0.0.1:8000",
        "--api-key-env",
        "IDIS_REAL_EXAMPLE_API_KEY",
        "--canary-api-key-env",
        "IDIS_ISOLATION_CANARY_API_KEY",
        "--require-full-live",
        "--verify-only",
        "--verify-restart-survival",
        "--verify-tenant-isolation",
        "--company-mode",
        "isolated",
        "--expected-company-count",
        "2",
        "--expected-file-counts",
        "58,209",
        "--expected-total-files",
        "267",
        "--safe-report-dir",
        str(report_dir),
        "--resume-ledger",
        str(ledger_path),
        "--no-secret-output",
    ]


def _install_pre_client_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate()

    def admitted_profile_digest(profile: Any) -> str:
        return hashlib.sha256(
            json.dumps(profile.values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def admitted_runtime_digest(**kwargs: Any) -> str:
        safe_shape = sorted(str(key) for key in kwargs)
        return hashlib.sha256(
            json.dumps(safe_shape, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    monkeypatch.setattr(gate, "_require_task4_gate_profile", admitted_profile_digest)
    monkeypatch.setattr(
        gate,
        "_require_task4_local_runtime_readiness",
        admitted_runtime_digest,
    )
    monkeypatch.setattr(
        gate,
        "_build_durable_api_settings",
        lambda **_kwargs: SimpleNamespace(
            api_key="fictional-acceptance-key",
            canary_api_key="fictional-canary-key",
        ),
    )

    def forbidden_baseline_reader(_path: Path) -> dict[str, object]:
        raise AssertionError("baseline read before namespace rejection")

    monkeypatch.setattr(
        gate,
        "_read_safe_durable_ingestion_receipt",
        forbidden_baseline_reader,
    )

    class ForbiddenClient:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("client constructed before namespace rejection")

    monkeypatch.setattr(gate, "_PacedExternalApiClient", ForbiddenClient)


def _assert_namespace_failure(
    *,
    argv: list[str],
    private_tokens: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _gate()
    _install_pre_client_guards(monkeypatch)

    result = gate.main(argv, strict_profile=SimpleNamespace(values={}))

    assert result == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    for token in private_tokens:
        assert token not in captured.out
        assert token not in captured.err


def test_initial_gate_rejects_cross_namespace_ledger_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / "acceptance-003"
    ledger = tmp_path / ".local_reports" / "slice100" / "acceptance-002" / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source",),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_restart_gate_rejects_wrong_ledger_filename_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / "acceptance-003"
    ledger = report / "copied-or-renamed-ledger.jsonl"

    _assert_namespace_failure(
        argv=_restart_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source", "copied-or-renamed-ledger"),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_initial_gate_rejects_mixed_prepare_and_resume_flags_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / "acceptance-003"
    ledger = report / "prepare-ledger.jsonl"
    argv = _initial_argv(root=root, report_dir=report, ledger_path=ledger)
    argv.extend(["--resume-ledger", str(ledger)])

    _assert_namespace_failure(
        argv=argv,
        private_tokens=("fictional-private-source",),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_restart_gate_rejects_mixed_resume_and_prepare_flags_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / "acceptance-003"
    ledger = report / "prepare-ledger.jsonl"
    argv = _restart_argv(root=root, report_dir=report, ledger_path=ledger)
    argv.extend(["--prepare-ledger", str(ledger)])

    _assert_namespace_failure(
        argv=argv,
        private_tokens=("fictional-private-source",),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


@pytest.mark.parametrize(
    "report_leaf",
    ["current", "acceptance-03", "acceptance-0003", "acceptance-abc"],
)
def test_gate_rejects_noncanonical_report_leaf_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    report_leaf: str,
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / report_leaf
    ledger = report / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source",),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_gate_rejects_nested_acceptance_namespace_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / "fictional-nested" / "acceptance-003"
    ledger = report / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source", "fictional-nested"),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_gate_rejects_relative_report_and_ledger_paths_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = Path(".local_reports") / "slice100" / "acceptance-003"
    ledger = report / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source",),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_gate_rejects_report_root_not_bound_to_source_parent_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / "fictional-other-root" / ".local_reports" / "slice100" / "acceptance-003"
    ledger = report / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source", "fictional-other-root"),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_gate_rejects_parent_traversal_ledger_spelling_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report = tmp_path / ".local_reports" / "slice100" / "acceptance-003"
    ledger = report / "child" / ".." / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source",),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def test_gate_rejects_existing_report_directory_symlink_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "fictional-private-source"
    report_parent = tmp_path / ".local_reports" / "slice100"
    report_parent.mkdir(parents=True)
    target = tmp_path / "fictional-report-target"
    target.mkdir()
    report = report_parent / "acceptance-003"
    try:
        report.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this test host")
    ledger = report / "prepare-ledger.jsonl"

    _assert_namespace_failure(
        argv=_initial_argv(root=root, report_dir=report, ledger_path=ledger),
        private_tokens=("fictional-private-source", "fictional-report-target"),
        monkeypatch=monkeypatch,
        capsys=capsys,
    )


def _launcher_initial_tokens() -> list[str]:
    root = Path("C:/fictional/source")
    report = Path("C:/fictional/.local_reports/slice100/acceptance-003")
    return [
        "gate",
        *_initial_argv(root=root, report_dir=report, ledger_path=report / "prepare-ledger.jsonl"),
    ]


def _launcher_restart_tokens() -> list[str]:
    root = Path("C:/fictional/source")
    report = Path("C:/fictional/.local_reports/slice100/acceptance-003")
    return [
        "gate",
        *_restart_argv(root=root, report_dir=report, ledger_path=report / "prepare-ledger.jsonl"),
    ]


def test_launcher_matcher_accepts_only_the_two_exact_ledger_flag_families() -> None:
    launcher = _launcher()
    initial = _launcher_initial_tokens()
    restart = _launcher_restart_tokens()

    assert launcher._is_task4_durable_gate_invocation(initial) is True  # noqa: SLF001
    assert launcher._is_task4_durable_gate_invocation(restart) is True  # noqa: SLF001

    mixed_initial = [*initial, "--resume-ledger", "C:/fictional/resume.jsonl"]
    mixed_restart = [*restart, "--prepare-ledger", "C:/fictional/prepare.jsonl"]
    assert launcher._is_task4_durable_gate_invocation(mixed_initial) is False  # noqa: SLF001
    assert launcher._is_task4_durable_gate_invocation(mixed_restart) is False  # noqa: SLF001
