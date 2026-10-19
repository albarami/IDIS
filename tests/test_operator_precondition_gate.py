"""Census and mode tests for the operator-PostgreSQL record-and-pass gate."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _operator_preconditions as gate

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TASK7 = _REPO_ROOT / "tests" / "slice100_task7"
_URL_NAME = re.compile(r"IDIS_[A-Z0-9_]*DATABASE_URL")
_COLLECTION_TIMEOUT_SECONDS = 120
_SYNTHETIC_CODE = "TASK7_OPERATOR_DATABASE_CONFIGURATION_MISSING:IDIS_SYNTHETIC_DATABASE_URL"
_EXISTING_POSTGRES_FAILURE = "EXISTING_POSTGRES_FAILURE"


def _module_path(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _decorator_variables(fn: ast.FunctionDef) -> tuple[str, ...] | None:
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Name):
            continue
        if deco.func.id != "requires_operator_postgres":
            continue
        return tuple(ast.literal_eval(arg) for arg in deco.args)
    return None


def _decorated_nodes() -> set[tuple[str, str, tuple[str, ...]]]:
    found: set[tuple[str, str, tuple[str, ...]]] = set()
    for path in sorted(_TASK7.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module_path(path)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            variables = _decorator_variables(node)
            if variables is not None:
                found.add((module, node.name, variables))
    return found


def _literal_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal_text(part) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _literal_text(node.value)
    return ""


def _pytest_skip_texts(tree: ast.AST) -> list[str]:
    texts: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "skip"
            and isinstance(func.value, ast.Name)
            and func.value.id == "pytest"
        ):
            continue
        parts = [_literal_text(arg) for arg in node.args]
        parts.extend(
            _literal_text(keyword.value)
            for keyword in node.keywords
            if keyword.arg in {None, "reason"}
        )
        texts.append("".join(parts))
    return texts


def _operator_database_skip_messages(paths: list[Path]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module_path(path)
        for text in _pytest_skip_texts(tree):
            if "operator" in text or _URL_NAME.search(text):
                found.append((module, text))
    return found


def _collected_task7_nodeids() -> tuple[str, ...]:
    env = os.environ.copy()
    for key in (
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_REQUIRE_POSTGRES",
        "IDIS_REQUIRE_OPERATOR_POSTGRES",
        "IDIS_STRICT_DOTENV_PATH",
    ):
        env.pop(key, None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = "src;."
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_socket",
            "-p",
            "no:cacheprovider",
            "--disable-socket",
            "--allow-hosts=127.0.0.1,::1",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "tests/slice100_task7",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_COLLECTION_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    return tuple(
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if "::" in line and "slice100_task7/" in line.replace("\\", "/")
    )


def _decorate_probe(*variable_names: str, failure_code: str = _SYNTHETIC_CODE):
    ran: list[str] = []

    @gate.requires_operator_postgres(*variable_names, failure_code=failure_code)
    def _probe() -> str:
        ran.append("ran")
        if (
            os.environ.get("IDIS_REQUIRE_POSTGRES") == "1"
            and not os.environ.get("IDIS_SYNTHETIC_DATABASE_URL", "").strip()
        ):
            pytest.fail(_EXISTING_POSTGRES_FAILURE, pytrace=False)
        return "ran"

    return _probe, ran


def test_present_operator_postgres_runs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("IDIS_SYNTHETIC_DATABASE_URL", "postgresql://present")
    monkeypatch.setenv("IDIS_REQUIRE_OPERATOR_POSTGRES", "1")
    monkeypatch.setenv("IDIS_REQUIRE_POSTGRES", "1")
    probe, ran = _decorate_probe("IDIS_SYNTHETIC_DATABASE_URL")

    assert probe(record_property=lambda _name, _value: None) == "ran"
    assert ran == ["ran"]
    assert "OPERATOR_POSTGRES NOT_EXECUTED" not in capsys.readouterr().out


def test_absent_operator_postgres_records_one_line_and_passes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("IDIS_SYNTHETIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_OTHER_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_OPERATOR_POSTGRES", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_POSTGRES", raising=False)
    recorded: list[tuple[str, object]] = []
    probe, ran = _decorate_probe("IDIS_SYNTHETIC_DATABASE_URL", "IDIS_OTHER_DATABASE_URL")
    line = (
        "OPERATOR_POSTGRES NOT_EXECUTED: IDIS_SYNTHETIC_DATABASE_URL,IDIS_OTHER_DATABASE_URL absent"
    )

    result = probe(record_property=lambda name, value: recorded.append((name, value)))

    assert result is None
    assert ran == []
    assert recorded == [("operator_postgres", line)]
    assert capsys.readouterr().out == f"{line}\n"


def test_required_operator_postgres_fails_with_the_given_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("IDIS_SYNTHETIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_POSTGRES", raising=False)
    monkeypatch.setenv("IDIS_REQUIRE_OPERATOR_POSTGRES", "1")
    recorded: list[tuple[str, object]] = []
    probe, ran = _decorate_probe("IDIS_SYNTHETIC_DATABASE_URL")
    line = "OPERATOR_POSTGRES NOT_EXECUTED: IDIS_SYNTHETIC_DATABASE_URL absent"

    with pytest.raises(pytest.fail.Exception, match=f"^{_SYNTHETIC_CODE}$"):
        probe(record_property=lambda name, value: recorded.append((name, value)))

    assert ran == []
    assert recorded == [("operator_postgres", line)]
    assert capsys.readouterr().out == f"{line}\n"


def test_require_postgres_runs_the_existing_failure_first(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("IDIS_SYNTHETIC_DATABASE_URL", raising=False)
    monkeypatch.setenv("IDIS_REQUIRE_POSTGRES", "1")
    monkeypatch.setenv("IDIS_REQUIRE_OPERATOR_POSTGRES", "1")
    probe, ran = _decorate_probe("IDIS_SYNTHETIC_DATABASE_URL")

    with pytest.raises(pytest.fail.Exception, match=f"^{_EXISTING_POSTGRES_FAILURE}$"):
        probe(record_property=lambda _name, _value: None)

    assert ran == ["ran"]
    assert "OPERATOR_POSTGRES NOT_EXECUTED" not in capsys.readouterr().out


def test_absence_does_not_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDIS_SYNTHETIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_POSTGRES", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_OPERATOR_POSTGRES", raising=False)
    probe, _ran = _decorate_probe("IDIS_SYNTHETIC_DATABASE_URL")

    try:
        probe()
    except pytest.skip.Exception as exc:
        raise AssertionError("operator postgres gate skipped") from exc


def test_fixture_helper_blocks_without_skipping_and_preserves_require_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = (
        "TASK7_V19_API_POSTGRES_CONFIGURATION_MISSING:"
        "IDIS_DATABASE_ADMIN_URL,IDIS_ERASURE_DATABASE_URL"
    )
    monkeypatch.delenv("IDIS_DATABASE_ADMIN_URL", raising=False)
    monkeypatch.delenv("IDIS_ERASURE_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_POSTGRES", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_OPERATOR_POSTGRES", raising=False)

    try:
        assert gate.operator_postgres_fixture_blocked(
            "IDIS_DATABASE_ADMIN_URL",
            "IDIS_ERASURE_DATABASE_URL",
            failure_code=code,
        )
    except pytest.skip.Exception as exc:
        raise AssertionError("operator postgres fixture gate skipped") from exc

    monkeypatch.setenv("IDIS_REQUIRE_OPERATOR_POSTGRES", "1")
    with pytest.raises(pytest.fail.Exception, match=f"^{code}$"):
        gate.operator_postgres_fixture_blocked(
            "IDIS_DATABASE_ADMIN_URL",
            "IDIS_ERASURE_DATABASE_URL",
            failure_code=code,
        )

    monkeypatch.setenv("IDIS_REQUIRE_POSTGRES", "1")
    with pytest.raises(pytest.fail.Exception, match=f"^{code}$"):
        gate.operator_postgres_fixture_blocked(
            "IDIS_DATABASE_ADMIN_URL",
            "IDIS_ERASURE_DATABASE_URL",
            failure_code=code,
        )

    monkeypatch.setenv("IDIS_DATABASE_ADMIN_URL", "postgresql://admin")
    monkeypatch.setenv("IDIS_ERASURE_DATABASE_URL", "postgresql://erasure")
    assert (
        gate.operator_postgres_fixture_blocked(
            "IDIS_DATABASE_ADMIN_URL",
            "IDIS_ERASURE_DATABASE_URL",
            failure_code=code,
        )
        is False
    )


def test_closed_list_decorators_match_the_constant() -> None:
    decorated = _decorated_nodes()
    listed = gate.OPERATOR_POSTGRES_GATED_NODES
    assert len(listed) == len(set(listed))
    assert decorated == set(listed)


def test_every_gated_node_is_collected() -> None:
    nodeids = _collected_task7_nodeids()
    for module, name, _variables in gate.OPERATOR_POSTGRES_GATED_NODES:
        prefix = f"{module}::{name}"
        assert any(nodeid == prefix or nodeid.startswith(f"{prefix}[") for nodeid in nodeids), (
            prefix
        )


def test_no_collected_task7_module_skips_on_an_operator_database_url() -> None:
    nodeids = _collected_task7_nodeids()
    modules = sorted({nodeid.split("::", 1)[0] for nodeid in nodeids})
    assert modules
    assert _operator_database_skip_messages([_REPO_ROOT / module for module in modules]) == []
