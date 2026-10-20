"""Service, pipeline, and runtime modules must not import the runs route.

The allowed set is empty. Evaluation and compliance imports of other route
modules are outside this scan and are not exceptions.
"""

from __future__ import annotations

import ast
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCOPED_RELATIVES = (
    "src/idis/services",
    "src/idis/pipeline",
    "src/idis/runtime",
)
_RUNS_ROUTE_MODULE = "idis.api.routes.runs"
_MODULE_SCOPE = "<module>"
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")

# path, scope, kind, ordinal. The set is empty on purpose.
RunsRouteImport = tuple[str, str, str, int]
ALLOWED_RUNS_ROUTE_IMPORTS: frozenset[RunsRouteImport] = frozenset()


def collect_runs_route_imports(repo_root: Path) -> frozenset[RunsRouteImport]:
    """Collect imports of ``idis.api.routes.runs`` under the three scoped trees."""
    found: set[RunsRouteImport] = set()
    for path in _iter_scoped_python_files(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        source = path.read_text(encoding="utf-8")
        found.update(collect_runs_route_imports_from_source(relative, source))
    return frozenset(found)


def collect_runs_route_imports_from_source(
    relative: str, source: str
) -> frozenset[RunsRouteImport]:
    """Collect runs-route imports from one module, including nested scopes."""
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as exc:
        raise AssertionError(f"{relative} could not be parsed: {exc}") from exc
    pending: list[tuple[str, str]] = []
    _walk(relative, tree, [], pending)
    return _assign_ordinals(relative, pending)


def assert_runs_route_imports_allowed(
    found: frozenset[RunsRouteImport],
    allowed: frozenset[RunsRouteImport],
) -> None:
    """Fail unless the live import set is exactly the allowed set."""
    new_imports = sorted(found - allowed)
    disappeared = sorted(allowed - found)
    if not new_imports and not disappeared:
        return
    lines = ["scoped packages must not import idis.api.routes.runs unless listed."]
    if new_imports:
        lines.append("Imports that are not allowed:")
        lines.extend(f"  {_format_import(item)}" for item in new_imports)
    if disappeared:
        lines.append("Allowed imports that are no longer present:")
        lines.extend(f"  {_format_import(item)}" for item in disappeared)
    raise AssertionError("\n".join(lines))


def test_service_pipeline_and_runtime_do_not_import_runs_route() -> None:
    """The three trees import no runs-route module."""
    found = collect_runs_route_imports(REPO_ROOT)
    assert_runs_route_imports_allowed(found, ALLOWED_RUNS_ROUTE_IMPORTS)


def test_planted_runs_route_import_in_a_copy_is_rejected(tmp_path: Path) -> None:
    """A copy of the scoped trees fails once a runs-route import is added."""
    copy_root = tmp_path / "repo"
    _copy_scoped_trees(copy_root)
    clean = collect_runs_route_imports(copy_root)
    planted = copy_root / "src" / "idis" / "services" / "planted_runs_route_import.py"
    planted.write_text(
        "def planted() -> None:\n    from idis.api.routes.runs import clear_runs_store\n",
        encoding="utf-8",
    )
    found = collect_runs_route_imports(copy_root)
    assert found - clean
    with pytest.raises(AssertionError, match="not allowed"):
        assert_runs_route_imports_allowed(found, clean)


def test_scanner_records_local_imports_and_import_call_literals(tmp_path: Path) -> None:
    """Module imports, function-local imports, and call literals are all records."""
    package = tmp_path / "src" / "idis" / "services"
    package.mkdir(parents=True)
    (package / "forms.py").write_text(
        "import idis.api.routes.runs\n"
        "from importlib import import_module\n"
        "\n"
        "def nested() -> None:\n"
        "    from idis.api.routes.runs import clear_runs_store\n"
        '    import_module("idis.api.routes.runs")\n'
        '    __import__("idis.api.routes.runs")\n',
        encoding="utf-8",
    )
    relative = "src/idis/services/forms.py"
    found = collect_runs_route_imports_from_source(
        relative,
        (package / "forms.py").read_text(encoding="utf-8"),
    )
    assert found == {
        (relative, _MODULE_SCOPE, "import", 0),
        (relative, "nested", "import_from", 0),
        (relative, "nested", "import_module", 0),
        (relative, "nested", "__import__", 0),
    }


def _copy_scoped_trees(copy_root: Path) -> None:
    for relative in _SCOPED_RELATIVES:
        source = REPO_ROOT / relative
        shutil.copytree(source, copy_root / relative, ignore=_COPY_IGNORE)


def _iter_scoped_python_files(repo_root: Path) -> Iterator[Path]:
    for relative in _SCOPED_RELATIVES:
        root = repo_root / relative
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _walk(
    relative: str,
    node: ast.AST,
    stack: list[str],
    pending: list[tuple[str, str]],
) -> None:
    if isinstance(node, ast.ClassDef):
        _walk_class(relative, node, stack, pending)
        return
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        _walk_function(relative, node, stack, pending)
        return
    _consider(relative, node, stack, pending)
    for child in ast.iter_child_nodes(node):
        _walk(relative, child, stack, pending)


def _walk_class(
    relative: str,
    node: ast.ClassDef,
    stack: list[str],
    pending: list[tuple[str, str]],
) -> None:
    for child in (*node.decorator_list, *node.bases, *node.keywords):
        _walk(relative, child, stack, pending)
    inner = [*stack, node.name]
    for statement in node.body:
        _walk(relative, statement, inner, pending)


def _walk_function(
    relative: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    stack: list[str],
    pending: list[tuple[str, str]],
) -> None:
    for decorator in node.decorator_list:
        _walk(relative, decorator, stack, pending)
    _walk(relative, node.args, stack, pending)
    if node.returns is not None:
        _walk(relative, node.returns, stack, pending)
    inner = [*stack, node.name]
    for statement in node.body:
        _walk(relative, statement, inner, pending)


def _consider(
    relative: str,
    node: ast.AST,
    stack: list[str],
    pending: list[tuple[str, str]],
) -> None:
    scope = ".".join(stack) if stack else _MODULE_SCOPE
    if isinstance(node, ast.ImportFrom):
        module = _resolved_module(relative, node)
        if _names_runs_route(module):
            pending.append((scope, "import_from"))
            return
        for alias in node.names:
            candidate = f"{module}.{alias.name}" if module else alias.name
            if _names_runs_route(candidate):
                pending.append((scope, "import_from"))
        return
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _names_runs_route(alias.name):
                pending.append((scope, "import"))
        return
    if isinstance(node, ast.Call):
        kind = _import_call_kind(node)
        if kind is None:
            return
        literal = _string_argument(node)
        if literal is not None and _names_runs_route(literal):
            pending.append((scope, kind))


def _names_runs_route(module: str) -> bool:
    return module == _RUNS_ROUTE_MODULE or module.startswith(f"{_RUNS_ROUTE_MODULE}.")


def _import_call_kind(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in {"import_module", "__import__"}:
        return func.attr
    return None


def _string_argument(node: ast.Call) -> str | None:
    if not node.args:
        return None
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _resolved_module(relative: str, node: ast.ImportFrom) -> str:
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


def _assign_ordinals(
    relative: str,
    pending: list[tuple[str, str]],
) -> frozenset[RunsRouteImport]:
    seen: dict[tuple[str, str], int] = {}
    found: set[RunsRouteImport] = set()
    for scope, kind in pending:
        key = (scope, kind)
        ordinal = seen.get(key, 0)
        seen[key] = ordinal + 1
        found.add((relative, scope, kind, ordinal))
    return frozenset(found)


def _format_import(item: RunsRouteImport) -> str:
    path, scope, kind, ordinal = item
    return f"{path} scope={scope} kind={kind} ordinal={ordinal} {_RUNS_ROUTE_MODULE}"
