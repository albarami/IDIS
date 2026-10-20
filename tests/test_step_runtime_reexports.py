"""Identity table for names re-exported from ``idis.api.routes.runs``.

Later composition-root slices append ``(name, defining_module)`` pairs.
An empty table passes vacuously.
"""

from __future__ import annotations

import importlib
import types
from types import ModuleType

import pytest

REEXPORTED_NAMES: tuple[tuple[str, str], ...] = (
    ("_resolve_run_audit_sink", "idis.services.runs.step_runtime.common"),
    ("_run_full_layer2_ic_challenge", "idis.services.runs.step_runtime.layer2"),
    ("_build_analysis_calc_registry", "idis.services.runs.step_runtime.analysis"),
    ("_retrieve_claims_for_debate", "idis.services.runs.step_runtime.analysis"),
    ("_run_full_analysis", "idis.services.runs.step_runtime.analysis"),
    ("_run_full_debate", "idis.services.runs.step_runtime.analysis"),
    ("_run_full_scoring", "idis.services.runs.step_runtime.analysis"),
    ("_run_full_deliverables", "idis.services.runs.step_runtime.deliverables"),
    ("_run_full_enrichment", "idis.services.runs.step_runtime.enrichment"),
    ("_normalize_input_ref", "idis.services.runs.step_runtime.evidence"),
    ("_project_graph_evidence", "idis.services.runs.step_runtime.evidence"),
    ("_run_full_graph_evidence", "idis.services.runs.step_runtime.evidence"),
    ("_run_full_rag_evidence", "idis.services.runs.step_runtime.evidence"),
    ("AnalysisClientSelection", "idis.services.runs.step_runtime.roles"),
    ("DebateRoleRunnerSelection", "idis.services.runs.step_runtime.roles"),
    ("ExtractorClientSelection", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_ANALYSIS_PROVIDER_FAILED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_ANALYSIS_REQUIRED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_DEBATE_PROVIDER_FAILED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_DEBATE_REQUIRED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_EXTRACTION_PROVIDER_FAILED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_EXTRACTION_REQUIRED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_SCORING_PROVIDER_FAILED", "idis.services.runs.step_runtime.roles"),
    ("STRICT_LIVE_SCORING_REQUIRED", "idis.services.runs.step_runtime.roles"),
    ("ScoringClientSelection", "idis.services.runs.step_runtime.roles"),
    ("StrictLiveExtractionError", "idis.services.runs.step_runtime.roles"),
    ("StrictLiveRoleError", "idis.services.runs.step_runtime.roles"),
    ("_DEBATE_PROMPT_IDS", "idis.services.runs.step_runtime.roles"),
    ("_DEBATE_PROMPT_VERSION_ID", "idis.services.runs.step_runtime.roles"),
    ("_EXTRACTION_PROMPT_ID", "idis.services.runs.step_runtime.roles"),
    ("_SCORING_PROMPT_ID", "idis.services.runs.step_runtime.roles"),
    ("_SCORING_PROMPT_VERSION", "idis.services.runs.step_runtime.roles"),
    ("_build_analysis_llm_client", "idis.services.runs.step_runtime.roles"),
    ("_build_analysis_provenance", "idis.services.runs.step_runtime.roles"),
    ("_build_debate_observability", "idis.services.runs.step_runtime.roles"),
    ("_build_debate_provenance", "idis.services.runs.step_runtime.roles"),
    ("_build_debate_role_runners", "idis.services.runs.step_runtime.roles"),
    ("_build_extraction_llm_client", "idis.services.runs.step_runtime.roles"),
    ("_build_extraction_provenance", "idis.services.runs.step_runtime.roles"),
    ("_build_layer2_provenance", "idis.services.runs.step_runtime.roles"),
    ("_build_role_client_provenance", "idis.services.runs.step_runtime.roles"),
    ("_build_scoring_llm_client", "idis.services.runs.step_runtime.roles"),
    ("_build_scoring_provenance", "idis.services.runs.step_runtime.roles"),
    ("_extraction_prompt_version", "idis.services.runs.step_runtime.roles"),
    ("_new_budgeted_llm_client", "idis.services.runs.step_runtime.roles"),
    ("_prompt_registry_version", "idis.services.runs.step_runtime.roles"),
    ("_resolve_analysis_selection", "idis.services.runs.step_runtime.roles"),
    ("_resolve_debate_selection", "idis.services.runs.step_runtime.roles"),
    ("_resolve_extraction_selection", "idis.services.runs.step_runtime.roles"),
    ("_resolve_scoring_selection", "idis.services.runs.step_runtime.roles"),
    ("_safe_client_request_id", "idis.services.runs.step_runtime.roles"),
    (
        "_run_full_methodology_deterministic_calculation",
        "idis.services.runs.step_runtime.snapshot",
    ),
    ("_run_snapshot_auto_grade", "idis.services.runs.step_runtime.snapshot"),
    ("_run_snapshot_calc", "idis.services.runs.step_runtime.snapshot"),
    ("_run_snapshot_extraction", "idis.services.runs.step_runtime.snapshot"),
    ("_METHODOLOGY_EXTRACTION_PROMPT_ID", "idis.services.runs.step_runtime.snapshot"),
)

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


_STEP_RUNTIME_PREFIX = "idis.services.runs.step_runtime"


def unlisted_step_runtime_callables(
    runs_module: ModuleType,
    pairs: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    """Return step_runtime functions or classes on runs that the table omits.

    Constants cannot be detected by ``__module__``; they are not returned here.
    """
    listed = {name for name, _module_name in pairs}
    missing: list[str] = []
    for name, value in vars(runs_module).items():
        if not _is_function_or_class(value):
            continue
        module_name = getattr(value, "__module__", "")
        if not isinstance(module_name, str) or not module_name.startswith(_STEP_RUNTIME_PREFIX):
            continue
        if name not in listed:
            missing.append(name)
    return tuple(missing)


def assert_step_runtime_callables_listed(
    runs_module: ModuleType,
    pairs: tuple[tuple[str, str], ...],
) -> None:
    """Fail when a step_runtime function or class on runs is absent from the table."""
    missing = unlisted_step_runtime_callables(runs_module, pairs)
    if missing:
        rendered = ", ".join(missing)
        raise AssertionError(f"step_runtime callables missing from REEXPORTED_NAMES: {rendered}")


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


def test_reexport_checker_reports_name_missing_from_defining_module() -> None:
    """A listed name that the defining module does not contain is a problem."""
    runtime = ModuleType("synthetic.step_runtime")
    runs = ModuleType("synthetic.runs")
    problems = reexport_problems(
        (("missing", runtime.__name__),),
        runs,
        {runtime.__name__: runtime},
    )
    assert any("is not defined in" in problem for problem in problems)


def test_reexport_checker_reports_name_reimported_from_elsewhere() -> None:
    """A name whose ``__module__`` is elsewhere was re-imported, not defined."""

    def defined_elsewhere() -> str:
        return "elsewhere"

    defined_elsewhere.__module__ = "synthetic.origin"
    runtime = ModuleType("synthetic.step_runtime")
    runtime.borrowed = defined_elsewhere
    runs = ModuleType("synthetic.runs")
    runs.borrowed = defined_elsewhere
    problems = reexport_problems(
        (("borrowed", runtime.__name__),),
        runs,
        {runtime.__name__: runtime},
    )
    assert any("re-imported by" in problem for problem in problems)


def test_step_runtime_callables_on_runs_are_listed() -> None:
    """Every step_runtime function or class on ``runs`` is in ``REEXPORTED_NAMES``.

    Constants cannot be detected by ``__module__`` (they have none). The slice
    classification table covers re-exported constants.
    """
    if not REEXPORTED_NAMES:
        runs_module = importlib.import_module("idis.api.routes.runs")
        assert unlisted_step_runtime_callables(runs_module, REEXPORTED_NAMES) == ()
        return
    runs_module = importlib.import_module("idis.api.routes.runs")
    assert_step_runtime_callables_listed(runs_module, REEXPORTED_NAMES)


def test_unlisted_step_runtime_function_fails_the_completeness_check() -> None:
    """A synthetic runs module with an unlisted step_runtime function fails."""

    def moved() -> str:
        return "moved"

    moved.__module__ = "idis.services.runs.step_runtime.common"
    runs = ModuleType("synthetic.runs")
    runs.moved = moved
    assert unlisted_step_runtime_callables(runs, ()) == ("moved",)
    with pytest.raises(AssertionError, match="moved"):
        assert_step_runtime_callables_listed(runs, ())


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
