"""Identity table for names re-exported from ``idis.api.routes.runs``.

Later composition-root slices append ``(name, defining_module)`` pairs.
An empty table passes vacuously.
"""

from __future__ import annotations

import importlib
import types
from types import ModuleType

REEXPORTED_NAMES: tuple[tuple[str, str], ...] = ()

_MISSING = object()


def reexport_problems(
    pairs: tuple[tuple[str, str], ...],
    runs_module: ModuleType,
    modules: dict[str, ModuleType],
) -> tuple[str, ...]:
    """Return identity, definition, and duplicate-name problems for re-export pairs."""
    problems = [f"duplicate name {name}" for name in duplicate_reexport_names(pairs)]
    for name, module_name in pairs:
        problems.extend(_pair_problems(name, module_name, runs_module, modules[module_name]))
    return tuple(problems)


def duplicate_reexport_names(pairs: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Return names listed more than once, in first-seen order."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for name, _module_name in pairs:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return tuple(duplicates)


def _pair_problems(
    name: str,
    module_name: str,
    runs_module: ModuleType,
    module: ModuleType,
) -> list[str]:
    problems: list[str] = []
    if name not in vars(module):
        problems.append(f"{name} is not defined in {module_name}")
    else:
        defined = vars(module)[name]
        if _is_function_or_class(defined) and defined.__module__ != module.__name__:
            problems.append(f"{name} is re-imported by {module_name} rather than defined there")
        runs_defined = vars(runs_module).get(name, _MISSING)
        if (
            _is_function_or_class(defined)
            and _is_function_or_class(runs_defined)
            and defined.__module__ == module.__name__
            and runs_defined.__module__ == runs_module.__name__
            and runs_defined is not defined
        ):
            problems.append(f"duplicate definition {name}")
    if getattr(runs_module, name, _MISSING) is not getattr(module, name, _MISSING):
        problems.append(f"non-identical re-export {name}")
    return problems


def _is_function_or_class(value: object) -> bool:
    return isinstance(value, (types.FunctionType, type))


def test_reexported_names_match_defining_modules() -> None:
    """Each listed name is the same object the defining module itself defines."""
    if not REEXPORTED_NAMES:
        assert reexport_problems((), ModuleType("idis.api.routes.runs"), {}) == ()
        return
    runs_module = importlib.import_module("idis.api.routes.runs")
    module_names = {module_name for _name, module_name in REEXPORTED_NAMES}
    modules = {module_name: importlib.import_module(module_name) for module_name in module_names}
    assert reexport_problems(REEXPORTED_NAMES, runs_module, modules) == ()


def test_reexported_names_have_no_duplicates() -> None:
    """The identity table lists each name once."""
    assert duplicate_reexport_names((("alpha", "mod.a"), ("alpha", "mod.b"))) == ("alpha",)
    assert duplicate_reexport_names(REEXPORTED_NAMES) == ()


def test_reexport_checker_detects_duplicate_definition_and_non_identical_reexport() -> None:
    """A synthetic module pair must expose a duplicate definition and a bad re-export."""

    def defined_in_runtime() -> str:
        return "runtime"

    def defined_in_runs() -> str:
        return "runs"

    runtime = ModuleType("synthetic.step_runtime")
    runtime.shared = defined_in_runtime
    defined_in_runtime.__module__ = runtime.__name__
    runs = ModuleType("synthetic.runs")
    runs.shared = defined_in_runs
    defined_in_runs.__module__ = runs.__name__

    problems = reexport_problems(
        (("shared", runtime.__name__),),
        runs,
        {runtime.__name__: runtime},
    )
    assert any("duplicate definition" in problem for problem in problems)
    assert any("non-identical re-export" in problem for problem in problems)
