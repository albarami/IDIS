"""RED tests for the closed-list private-artifact inapplicability gate."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests import _private_artifacts as artifacts
from tests._private_artifact_scan import (
    ci_regenerated_slice100_paths,
    ci_regenerated_slice100_pins,
    consumed_slice100_paths,
    overlay_slice100_paths,
    skip_on_slice100_paths,
)
from tests.slice100_task8.conftest import should_ignore_historical_v22

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE_ROOTS = (
    _REPO_ROOT / "tests" / "slice100_task7",
    _REPO_ROOT / "tests" / "slice100_task8",
)
_SEPARATE_RULING_MODULES = frozenset(
    {
        "tests/slice100_task7/test_task7_void_contract_hash_blocklist.py",
        "tests/slice100_task8/test_task8_void_contract_hash_blocklist.py",
    }
)
_SLICE_MARKERS = frozenset({".local_reports", "slice100"})


def _expr_parts(expr: ast.AST, bindings: Mapping[str, set[str]]) -> set[str]:
    """Collect path fragments without inheriting unrelated name bindings."""

    parts: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.add(node.value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for side in (node.left, node.right):
                if isinstance(side, ast.Name):
                    parts.update(bindings.get(side.id, set()))
    return parts


def _bindings(tree: ast.Module) -> dict[str, set[str]]:
    bindings: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            parts = _expr_parts(node.value, bindings)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = parts
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            bindings[node.target.id] = _expr_parts(node.value, bindings)
    return bindings


def _gated_paths() -> frozenset[str]:
    return frozenset(
        relative
        for _module, _name, paths in artifacts.PRIVATE_ARTIFACT_GATED_NODES
        for relative in paths
    )


def _local_only_gated_paths() -> frozenset[str]:
    return _gated_paths() - overlay_slice100_paths()


def _paths_in_strings(strings: set[str]) -> set[str]:
    found: set[str] = set()
    for relative in _local_only_gated_paths():
        prefixed = f".local_reports/slice100/{relative}"
        filename = relative.rsplit("/", 1)[-1]
        parents = relative.split("/")[:-1]
        if relative in strings or prefixed in strings:
            found.add(relative)
            continue
        if (
            filename in strings
            and strings >= _SLICE_MARKERS
            and all(part in strings for part in parents)
        ):
            found.add(relative)
    return found


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    found: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            found[node.name] = node
    return found


def _loaded_names(fn: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _string_constants(fn: ast.AST) -> set[str]:
    return {
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _called_names(fn: ast.AST) -> set[str]:
    called: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    return called


def _function_paths(fn: ast.AST, bindings: Mapping[str, set[str]]) -> set[str]:
    strings = set(_string_constants(fn))
    for name in _loaded_names(fn):
        strings.update(bindings.get(name, set()))
    return _paths_in_strings(strings)


def _decorator_paths(fn: ast.FunctionDef) -> tuple[str, ...] | None:
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Name):
            continue
        if deco.func.id != "requires_private_artifact":
            continue
        return tuple(ast.literal_eval(arg) for arg in deco.args)
    return None


def _module_path(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _collected_modules() -> list[Path]:
    modules: list[Path] = []
    for root in _GATE_ROOTS:
        for path in sorted(root.glob("test_*.py")):
            module = _module_path(path)
            if module in _SEPARATE_RULING_MODULES:
                continue
            if should_ignore_historical_v22(path) is True:
                continue
            modules.append(path)
    return modules


def _decorated_nodes() -> set[tuple[str, str, tuple[str, ...]]]:
    found: set[tuple[str, str, tuple[str, ...]]] = set()
    for path in _collected_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module_path(path)
        for fn in _functions(tree).values():
            if not fn.name.startswith("test_"):
                continue
            paths = _decorator_paths(fn)
            if paths is not None:
                found.add((module, fn.name, paths))
    return found


def _unguarded_readers() -> set[tuple[str, str]]:
    readers: set[tuple[str, str]] = set()
    for path in _collected_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bindings = _bindings(tree)
        functions = _functions(tree)
        gated_by_name = {name: _function_paths(fn, bindings) for name, fn in functions.items()}
        reachable = {name for name, paths in gated_by_name.items() if paths}
        changed = True
        while changed:
            changed = False
            for name, fn in functions.items():
                if name in reachable:
                    continue
                if _called_names(fn) & reachable:
                    reachable.add(name)
                    changed = True
        module = _module_path(path)
        readers.update((module, name) for name in reachable if name.startswith("test_"))
    return readers


def _decorate_probe(*relative_paths: str):
    ran: list[str] = []

    @artifacts.requires_private_artifact(*relative_paths)
    def _probe() -> str:
        ran.append("ran")
        return "ran"

    return _probe, ran


def test_absent_artifact_records_not_executed_and_does_not_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", raising=False)
    recorded: list[tuple[str, object]] = []
    probe, ran = _decorate_probe("task7_v16_current_guard_postgres_census_receipt.json")

    result = probe(record_property=lambda name, value: recorded.append((name, value)))

    assert result is None
    assert ran == []
    line = (
        "PRIVATE_ARTIFACT NOT_EXECUTED: task7_v16_current_guard_postgres_census_receipt.json absent"
    )
    assert recorded == [("PRIVATE_ARTIFACT", line)]
    assert capsys.readouterr().out == f"{line}\n"


def test_required_absent_artifact_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", "1")
    recorded: list[tuple[str, object]] = []
    probe, ran = _decorate_probe("task8/v25-candidate/v25_independent_fixture_red.txt")
    line = (
        "PRIVATE_ARTIFACT NOT_EXECUTED: task8/v25-candidate/v25_independent_fixture_red.txt absent"
    )

    with pytest.raises(pytest.fail.Exception, match=f"^{line}$"):
        probe(record_property=lambda name, value: recorded.append((name, value)))

    assert ran == []
    assert recorded == [("PRIVATE_ARTIFACT", line)]
    assert capsys.readouterr().out == f"{line}\n"


def test_present_artifact_runs_the_identity_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", "1")
    target = tmp_path / ".local_reports" / "slice100" / "receipt.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"present-bytes")
    seen: list[bytes] = []

    @artifacts.requires_private_artifact("receipt.json")
    def _probe() -> None:
        seen.append((tmp_path / ".local_reports" / "slice100" / "receipt.json").read_bytes())

    _probe(record_property=lambda _name, _value: None)

    assert seen == [b"present-bytes"]
    assert "PRIVATE_ARTIFACT NOT_EXECUTED" not in capsys.readouterr().out


def test_each_missing_path_prints_one_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", raising=False)
    probe, ran = _decorate_probe("one.json", "task8/v26-candidate/two.json")

    assert probe() is None
    assert ran == []
    assert capsys.readouterr().out == (
        "PRIVATE_ARTIFACT NOT_EXECUTED: one.json absent\n"
        "PRIVATE_ARTIFACT NOT_EXECUTED: task8/v26-candidate/two.json absent\n"
    )


def test_absence_does_not_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", raising=False)
    probe, _ran = _decorate_probe("missing.json")

    try:
        probe()
    except pytest.skip.Exception as exc:
        raise AssertionError("private-artifact gate skipped") from exc


def test_non_one_required_mode_does_not_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", "true")
    probe, ran = _decorate_probe("missing.json")

    assert probe() is None
    assert ran == []


def test_module_recorder_is_used_when_no_argument_is_passed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(artifacts, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("IDIS_REQUIRE_PRIVATE_ARTIFACTS", raising=False)
    recorded: list[tuple[str, object]] = []
    monkeypatch.setattr(
        artifacts, "_RECORD_PROPERTY", lambda name, value: recorded.append((name, value))
    )
    probe, _ran = _decorate_probe("missing.json")

    probe()

    assert recorded == [
        (
            "PRIVATE_ARTIFACT",
            "PRIVATE_ARTIFACT NOT_EXECUTED: missing.json absent",
        )
    ]


def test_wrapper_requests_record_property_without_passing_it_through() -> None:
    def _bare() -> None:
        return None

    wrapped = artifacts.requires_private_artifact("missing.json")(_bare)

    assert "record_property" in inspect.signature(wrapped).parameters
    assert not hasattr(wrapped, "__wrapped__")


def test_closed_list_decorators_match_the_constant() -> None:
    assert _decorated_nodes() == set(artifacts.PRIVATE_ARTIFACT_GATED_NODES)


def test_no_other_collected_test_reads_a_gated_path_without_the_decorator() -> None:
    decorated = {(module, name) for module, name, _paths in _decorated_nodes()}
    overlay = overlay_slice100_paths()
    local_nodes = {
        (module, name)
        for module, name, paths in artifacts.PRIVATE_ARTIFACT_GATED_NODES
        if set(paths) - overlay
    }
    readers = _unguarded_readers()
    assert readers == local_nodes
    assert readers <= decorated
    for module in _SEPARATE_RULING_MODULES:
        source = (_REPO_ROOT / module).read_text(encoding="utf-8")
        assert "requires_private_artifact" not in source


def test_no_collected_module_skips_because_of_a_slice100_path() -> None:
    assert skip_on_slice100_paths(_collected_modules()) == set()


def test_every_slice100_reference_is_overlay_supplied_gated_or_ci_regenerated() -> None:
    allowed = overlay_slice100_paths() | _gated_paths() | ci_regenerated_slice100_paths()
    assert consumed_slice100_paths(_collected_modules()) <= allowed


_V27_PREIMAGE = "task8/v27-candidate/full_strict_v27_reviewed_preimage_v1.json"
_V27_CONTRACT = "task8/v27-candidate/full_strict_v27_contract_hash_v1.json"
# tests/slice100_task8/test_v27_fixture_derivation_red.py:47-54
_V27_PREIMAGE_SHA256 = "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
_V27_CONTRACT_SHA256 = "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
_V27_MKDIR = "mkdir -p .local_reports/slice100/task8/v27-candidate"


def _overlay_path(relative: str) -> str:
    return f".local_reports/slice100/{relative}"


def _regen_run(
    materialize: tuple[str, ...],
    generate: tuple[str, ...],
    pins: tuple[tuple[str, str], ...],
) -> str:
    lines = [_V27_MKDIR]
    for relative in materialize:
        lines.append(
            "python scripts/materialize_full_strict_v27_preimage.py "
            f"--staging-output {_overlay_path(relative)}"
        )
    outputs = " ".join(f"--staging-output {_overlay_path(relative)}" for relative in generate)
    lines.append(
        "python scripts/generate_task8_v27_contract_fixtures.py "
        f"--staged-artifact {_overlay_path(materialize[0])} {outputs}"
    )
    quoted = " ".join(f"'{digest}  {_overlay_path(relative)}'" for digest, relative in pins)
    lines.append(f"printf '%s\\n' {quoted} | sha256sum --check --status")
    return "\n".join(lines)


def _write_check_workflow(directory: Path, run: str, *, copies: int = 1) -> Path:
    body = "".join(f"          {line}\n" for line in run.splitlines())
    step = f"      - name: regenerate\n        run: |\n{body}"
    path = directory / "ci.yml"
    path.write_text(f"jobs:\n  check:\n    steps:\n{step * copies}", encoding="utf-8")
    return path


def _reject_workflow(directory: Path, case: str) -> Path:
    preimage = (_V27_PREIMAGE_SHA256, _V27_PREIMAGE)
    contract = (_V27_CONTRACT_SHA256, _V27_CONTRACT)
    if case == "no-step":
        return _write_check_workflow(directory, "echo untouched")
    if case == "two-steps":
        run = _regen_run((_V27_PREIMAGE,), (_V27_CONTRACT,), (preimage, contract))
        return _write_check_workflow(directory, run, copies=2)
    if case == "third-output":
        third = "task8/v27-candidate/third.json"
        run = _regen_run(
            (_V27_PREIMAGE, third),
            (_V27_CONTRACT,),
            (preimage, contract, ("ab" * 32, third)),
        )
        return _write_check_workflow(directory, run)
    if case == "short-digest":
        run = _regen_run(
            (_V27_PREIMAGE,),
            (_V27_CONTRACT,),
            (("abcd", _V27_PREIMAGE), contract),
        )
        return _write_check_workflow(directory, run)
    if case == "sources-disagree":
        run = _regen_run(
            (_V27_PREIMAGE,),
            ("task8/v27-candidate/other.json",),
            (preimage, contract),
        )
        return _write_check_workflow(directory, run)
    raise AssertionError(case)


def test_ci_regenerated_slice100_paths_are_the_v27_pair() -> None:
    assert ci_regenerated_slice100_paths() == frozenset({_V27_PREIMAGE, _V27_CONTRACT})


def test_ci_regenerated_pins_restate_the_v27_identity_literals() -> None:
    # tests/slice100_task8/test_v27_fixture_derivation_red.py:47-54
    assert ci_regenerated_slice100_pins() == {
        _V27_PREIMAGE: _V27_PREIMAGE_SHA256,
        _V27_CONTRACT: _V27_CONTRACT_SHA256,
    }


def test_ci_regenerated_paths_are_disjoint_from_overlay_and_gated_paths() -> None:
    regenerated = ci_regenerated_slice100_paths()
    assert regenerated.isdisjoint(overlay_slice100_paths())
    assert regenerated.isdisjoint(_gated_paths())


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("two-steps", "exactly one"),
        ("no-step", "exactly one"),
        ("third-output", "exactly two"),
        ("short-digest", "64-hex"),
        ("sources-disagree", "disagree"),
    ],
)
def test_ci_regenerated_workflow_rejects(tmp_path: Path, case: str, message: str) -> None:
    workflow = _reject_workflow(tmp_path, case)
    with pytest.raises(AssertionError, match=message):
        ci_regenerated_slice100_paths(workflow)
    with pytest.raises(AssertionError, match=message):
        ci_regenerated_slice100_pins(workflow)
