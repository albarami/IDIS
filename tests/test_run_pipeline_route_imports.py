"""AST ratchet for non-route modules that import ``idis.api.routes``.

The frozen set may only shrink. A new import fails the test. An allow-listed
import that disappeared also fails, so the list stays exact.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_ROUTE_MODULE_PREFIX = "idis.api.routes"
_LEADING_SHIFT_BLANK_LINES = 5
_LISTED_SHIFT_RELATIVE = "src/idis/services/runs/steps.py"
_SCOPE_PROBE_RELATIVE = "src/idis/services/runs/scope_probe.py"
_ORDINAL_PROBE_RELATIVE = "src/idis/services/runs/ordinal_probe.py"
_SCOPE_BEFORE = (
    "def original_holder():\n"
    '    import_module("idis.api.routes.runs")\n'
    "\n"
    "\n"
    "def other_holder():\n"
    "    return None\n"
)
_SCOPE_AFTER = (
    "def other_holder():\n"
    '    import_module("idis.api.routes.runs")\n'
    "\n"
    "\n"
    "def original_holder():\n"
    "    return None\n"
)
_ORDINAL_CALLS = (
    "def probe():\n"
    '    import_module("idis.api.routes.runs")\n'
    '    import_module("idis.api.routes.runs")\n'
    '    import_module("idis.api.routes.runs")\n'
)
_ORDINAL_WITHOUT_MIDDLE = (
    "def probe():\n"
    '    import_module("idis.api.routes.runs")\n'
    '    import_module("idis.api.routes.runs")\n'
)

# path, scope, module, imported names, ordinal.
# scope is the dotted innermost function or class, or "<module>".
# ordinal is the source-order index of the same (scope, module, names) in one file.
# Line numbers are not part of this identity.
RouteImport = tuple[str, str, str, tuple[str, ...], int]
_PendingImport = tuple[str, str, tuple[str, ...]]
_Visitor = Callable[[ast.AST, list[str]], None]
_MODULE_SCOPE = "<module>"
_NO_IMPORTED_NAMES: tuple[str, ...] = ()

# Empty imported-name tuples are import_module("idis.api.routes...") calls.
# strict_full_live.py's idis.api.routes.deliverables use is a legitimate API surface
# and stays listed. Old lines are comments only.
FROZEN_ROUTE_IMPORTS: frozenset[RouteImport] = frozenset(
    {
        (
            "src/idis/compliance/compliance_export.py",
            "InMemoryExportCollector.collect",
            "idis.api.routes.documents",
            ("_document_store",),
            0,  # was line 68
        ),
        (
            "src/idis/compliance/erasure.py",
            "InMemoryErasureExecutor.erase_deal",
            "idis.api.routes.documents",
            ("_document_store",),
            0,  # was line 445
        ),
        (
            "src/idis/evaluation/synthetic_strict_runtime_rehearsal.py",
            _MODULE_SCOPE,
            "idis.api.routes.deals",
            ("clear_deals_store",),
            0,  # was line 20
        ),
        (
            "src/idis/evaluation/synthetic_strict_runtime_rehearsal.py",
            _MODULE_SCOPE,
            "idis.api.routes.deliverables",
            ("clear_deliverables_store",),
            0,  # was line 21
        ),
        (
            "src/idis/evaluation/synthetic_strict_runtime_rehearsal.py",
            _MODULE_SCOPE,
            "idis.api.routes.documents",
            ("clear_document_store",),
            0,  # was line 22
        ),
        (
            "src/idis/evaluation/synthetic_strict_runtime_rehearsal.py",
            _MODULE_SCOPE,
            "idis.api.routes.runs",
            ("clear_runs_store",),
            0,  # was line 23
        ),
        (
            "src/idis/services/runs/snapshot_postgres_output_staging.py",
            "SnapshotOutputComposition._extract",
            "idis.api.routes.runs",
            ("_run_snapshot_extraction",),
            0,  # was line 871
        ),
        (
            "src/idis/services/runs/snapshot_postgres_output_staging.py",
            "SnapshotOutputComposition._grade",
            "idis.api.routes.runs",
            ("_run_snapshot_auto_grade",),
            0,  # was line 889
        ),
        (
            "src/idis/services/runs/snapshot_postgres_output_staging.py",
            "SnapshotOutputComposition._calc",
            "idis.api.routes.runs",
            ("_run_snapshot_calc",),
            0,  # was line 908
        ),
        (
            "src/idis/services/runs/steps.py",
            "build_run_context",
            "idis.api.routes.runs",
            (
                "_run_full_analysis",
                "_run_full_debate",
                "_run_full_deliverables",
                "_run_full_enrichment",
                "_run_full_graph_evidence",
                "_run_full_layer2_ic_challenge",
                "_run_full_methodology_deterministic_calculation",
                "_run_full_rag_evidence",
                "_run_full_scoring",
                "_run_snapshot_auto_grade",
                "_run_snapshot_calc",
                "_run_snapshot_extraction",
            ),
            0,  # was line 328
        ),
        (
            "src/idis/services/runs/strict_full_live.py",
            "_layer2_ic_challenge_code_path_wired",
            "idis.api.routes.runs",
            _NO_IMPORTED_NAMES,
            0,  # was line 2505
        ),
        (
            "src/idis/services/runs/strict_full_live.py",
            "_layer2_live_runner_construction_wired",
            "idis.api.routes.runs",
            _NO_IMPORTED_NAMES,
            0,  # was line 2542
        ),
        (
            "src/idis/services/runs/strict_full_live.py",
            "_rag_code_path_wired",
            "idis.api.routes.runs",
            _NO_IMPORTED_NAMES,
            0,  # was line 2599
        ),
        (
            "src/idis/services/runs/strict_full_live.py",
            "_ui_api_download_code_path_wired",
            "idis.api.routes.deliverables",
            _NO_IMPORTED_NAMES,
            0,  # was line 2651
        ),
    }
)


def collect_route_imports(repo_root: Path) -> frozenset[RouteImport]:
    """Collect route imports from every non-API Python module under src/idis."""
    found: set[RouteImport] = set()
    for path in _iter_non_api_python_files(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        source = path.read_text(encoding="utf-8")
        found.update(collect_route_imports_from_source(relative, source))
    return frozenset(found)


def collect_route_imports_from_source(relative: str, source: str) -> frozenset[RouteImport]:
    """Collect route imports from one module's source text."""
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as exc:
        raise AssertionError(f"{relative} could not be parsed: {exc}") from exc
    return frozenset(_records_from_tree(relative, tree))


def assert_route_imports_match(
    found: frozenset[RouteImport],
    allowed: frozenset[RouteImport],
) -> None:
    """Fail when the live route-import set is not exactly the allow-list."""
    new_offenders = sorted(found - allowed)
    disappeared = sorted(allowed - found)
    if not new_offenders and not disappeared:
        return
    lines = ["route-import ratchet may only shrink, and the allow-list must stay exact."]
    if new_offenders:
        lines.append("New offenders (not in the allow-list):")
        lines.extend(f"  {_format_route_import(item)}" for item in new_offenders)
    if disappeared:
        lines.append(
            "Allow-listed entries disappeared; shrink the allow-list only if the import is gone:"
        )
        lines.extend(f"  {_format_route_import(item)}" for item in disappeared)
    raise AssertionError("\n".join(lines))


def _format_route_import(item: RouteImport) -> str:
    path, scope, module, names, ordinal = item
    rendered = ", ".join(names) if names else "<module>"
    return f"{path} scope={scope} ordinal={ordinal} {module} ({rendered})"


def test_non_route_modules_import_only_allow_listed_route_modules() -> None:
    """Non-route packages may import only the frozen route-module sites."""
    assert_route_imports_match(collect_route_imports(REPO_ROOT), FROZEN_ROUTE_IMPORTS)


def test_route_import_ratchet_fails_when_allow_list_lacks_one_real_entry() -> None:
    """Omitting a live import reports a new offender."""
    live = collect_route_imports(REPO_ROOT)
    omitted = min(live)
    with pytest.raises(AssertionError, match="New offender"):
        assert_route_imports_match(live, live - {omitted})


def test_route_import_ratchet_fails_when_allow_list_has_one_extra_entry() -> None:
    """An allow-list entry that is not in the source must be reported as gone."""
    live = collect_route_imports(REPO_ROOT)
    extra: RouteImport = (
        "src/idis/services/not_an_offender.py",
        _MODULE_SCOPE,
        "idis.api.routes.runs",
        ("not_real",),
        0,
    )
    with pytest.raises(AssertionError, match="disappeared"):
        assert_route_imports_match(live, live | {extra})


def test_leading_source_shift_keeps_route_import_records() -> None:
    """Blank lines and an unrelated function at the top do not change records."""
    original = (REPO_ROOT / _LISTED_SHIFT_RELATIVE).read_text(encoding="utf-8")
    baseline = collect_route_imports_from_source(_LISTED_SHIFT_RELATIVE, original)
    shifted = _source_with_leading_shift(original, blank_lines=_LEADING_SHIFT_BLANK_LINES)
    assert collect_route_imports_from_source(_LISTED_SHIFT_RELATIVE, shifted) == baseline


def test_moving_import_into_another_function_reports_vanished_and_new_records() -> None:
    """A same-line move into another function is a vanished record and a new offender."""
    before = collect_route_imports_from_source(_SCOPE_PROBE_RELATIVE, _SCOPE_BEFORE)
    after = collect_route_imports_from_source(_SCOPE_PROBE_RELATIVE, _SCOPE_AFTER)
    with pytest.raises(AssertionError, match="(?s)New offender.*disappeared"):
        assert_route_imports_match(after, before)


def test_identical_import_module_calls_stay_distinct_by_ordinal() -> None:
    """Three identical calls are three records, and dropping the middle one is seen."""
    found = collect_route_imports_from_source(_ORDINAL_PROBE_RELATIVE, _ORDINAL_CALLS)
    expected: set[RouteImport] = {
        (_ORDINAL_PROBE_RELATIVE, "probe", "idis.api.routes.runs", _NO_IMPORTED_NAMES, ordinal)
        for ordinal in (0, 1, 2)
    }
    assert found == expected
    shortened = collect_route_imports_from_source(_ORDINAL_PROBE_RELATIVE, _ORDINAL_WITHOUT_MIDDLE)
    with pytest.raises(AssertionError, match="disappeared"):
        assert_route_imports_match(shortened, found)


def _source_with_leading_shift(source: str, *, blank_lines: int) -> str:
    """Insert blank lines and an unrelated function after a leading future import.

    ``from __future__`` stays first so the shifted copy still parses. Every later line moves.
    """
    insertion = (
        "\n" * blank_lines
        + "def _unrelated_route_import_shift_probe() -> None:\n"
        + "    return None\n\n"
    )
    marker = "from __future__ import annotations\n"
    if marker in source:
        head, matched, tail = source.partition(marker)
        return head + matched + insertion + tail
    return insertion + source


def _iter_non_api_python_files(repo_root: Path) -> Iterator[Path]:
    package_root = repo_root / "src" / "idis"
    api_root = (package_root / "api").resolve()
    for path in sorted(package_root.rglob("*.py")):
        resolved = path.resolve()
        if resolved == api_root or api_root in resolved.parents:
            continue
        yield path


def _records_from_tree(relative: str, tree: ast.AST) -> set[RouteImport]:
    pending: list[_PendingImport] = []

    def visit(node: ast.AST, stack: list[str]) -> None:
        if isinstance(node, ast.ClassDef):
            _visit_class(node, stack, visit)
            return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _visit_function(node, stack, visit)
            return
        _consider_route_import(relative, node, stack, pending)
        for child in ast.iter_child_nodes(node):
            visit(child, stack)

    visit(tree, [])
    return _assign_ordinals(relative, pending)


def _visit_class(node: ast.ClassDef, stack: list[str], visit: _Visitor) -> None:
    for child in (*node.decorator_list, *node.bases, *node.keywords):
        visit(child, stack)
    for type_param in _type_params(node):
        visit(type_param, stack)
    inner = [*stack, node.name]
    for statement in node.body:
        visit(statement, inner)


def _visit_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    stack: list[str],
    visit: _Visitor,
) -> None:
    for decorator in node.decorator_list:
        visit(decorator, stack)
    visit(node.args, stack)
    if node.returns is not None:
        visit(node.returns, stack)
    for type_param in _type_params(node):
        visit(type_param, stack)
    inner = [*stack, node.name]
    for statement in node.body:
        visit(statement, inner)


def _type_params(node: ast.AST) -> tuple[ast.AST, ...]:
    raw = getattr(node, "type_params", ())
    if isinstance(raw, list | tuple):
        return tuple(item for item in raw if isinstance(item, ast.AST))
    return ()


def _consider_route_import(
    relative: str,
    node: ast.AST,
    stack: list[str],
    pending: list[_PendingImport],
) -> None:
    scope = _scope_name(stack)
    if isinstance(node, ast.ImportFrom):
        imported = _resolved_import_from_module(relative, node)
        if imported.startswith(_ROUTE_MODULE_PREFIX):
            names = tuple(alias.name for alias in node.names)
            pending.append((scope, imported, names))
        return
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith(_ROUTE_MODULE_PREFIX):
                pending.append((scope, alias.name, _NO_IMPORTED_NAMES))
        return
    if isinstance(node, ast.Call) and _is_import_module_call(node):
        called = _constant_call_argument(node)
        if called is not None and called.startswith(_ROUTE_MODULE_PREFIX):
            pending.append((scope, called, _NO_IMPORTED_NAMES))


def _scope_name(stack: list[str]) -> str:
    if not stack:
        return _MODULE_SCOPE
    return ".".join(stack)


def _assign_ordinals(relative: str, pending: list[_PendingImport]) -> set[RouteImport]:
    seen: dict[tuple[str, str, tuple[str, ...]], int] = {}
    found: set[RouteImport] = set()
    for scope, module, names in pending:
        key = (scope, module, names)
        ordinal = seen.get(key, 0)
        seen[key] = ordinal + 1
        found.add((relative, scope, module, names, ordinal))
    return found


def _resolved_import_from_module(relative: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = Path(relative).with_suffix("").parts
    if not parts or parts[0] != "src":
        return node.module or ""
    package = list(parts[1:-1])
    drop = node.level - 1
    if drop > len(package):
        return node.module or ""
    if drop:
        package = package[:-drop]
    if node.module:
        package.extend(node.module.split("."))
    return ".".join(package)


def _is_import_module_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"import_module", "__import__"}
    return isinstance(func, ast.Attribute) and func.attr in {"import_module", "__import__"}


def _constant_call_argument(node: ast.Call) -> str | None:
    if not node.args:
        return None
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None
