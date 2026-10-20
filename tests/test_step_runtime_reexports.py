"""Identity table for names re-exported from ``idis.api.routes.runs``.

An empty table means the route module re-exports none of the moved names.
``MOVED_CLUSTER_NAMES`` is the names burst B moved out of the route module,
plus ``logger``, which both modules define. The absence check imports the route
module and requires each listed name to be absent except ``logger`` and
``_resolve_run_audit_sink``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import types
from pathlib import Path
from types import ModuleType

import pytest

REEXPORTED_NAMES: tuple[tuple[str, str], ...] = ()

# Names burst B moved out of the route module, plus logger, which both modules define.
MOVED_CLUSTER_NAMES: tuple[str, ...] = (
    "AnalysisClientFactory",
    "AnalysisClientSelection",
    "DebateRoleRunnerSelection",
    "DebateRoleRunnersFactory",
    "ExtractorClientFactory",
    "ExtractorClientSelection",
    "STRICT_LIVE_ANALYSIS_PROVIDER_FAILED",
    "STRICT_LIVE_ANALYSIS_REQUIRED",
    "STRICT_LIVE_DEBATE_PROVIDER_FAILED",
    "STRICT_LIVE_DEBATE_REQUIRED",
    "STRICT_LIVE_EXTRACTION_PROVIDER_FAILED",
    "STRICT_LIVE_EXTRACTION_REQUIRED",
    "STRICT_LIVE_SCORING_PROVIDER_FAILED",
    "STRICT_LIVE_SCORING_REQUIRED",
    "ScoringClientFactory",
    "ScoringClientSelection",
    "StrictLiveExtractionError",
    "StrictLiveRoleError",
    "_ANALYSIS_ROLES",
    "_DEBATE_DEFAULT_ROLES",
    "_DEBATE_PROMPT_IDS",
    "_DEBATE_PROMPT_VERSION_ID",
    "_EXTRACTION_PROMPT_ID",
    "_INPUT_REF_KEY_TO_TYPE",
    "_LAYER2_ARBITER_PROMPT_ID",
    "_LAYER2_CHALLENGER_PROMPT_ID",
    "_LAYER2_PROMPT_IDS",
    "_LAYER2_PROMPT_VERSION",
    "_METHODOLOGY_EXTRACTION_PROMPT_ID",
    "_SCORING_PROMPT_ID",
    "_SCORING_PROMPT_VERSION",
    "_STAGE_UNKNOWN_MESSAGE_LIMIT",
    "_analysis_context_stage_text",
    "_build_analysis_calc_registry",
    "_build_analysis_claim_registry",
    "_build_analysis_llm_client",
    "_build_analysis_provenance",
    "_build_debate_observability",
    "_build_debate_provenance",
    "_build_debate_role_runners",
    "_build_extraction_llm_client",
    "_build_extraction_provenance",
    "_build_layer2_provenance",
    "_build_live_debate_role_runners",
    "_build_role_client_provenance",
    "_build_scoring_llm_client",
    "_build_scoring_provenance",
    "_build_strict_anthropic_extractor",
    "_build_strict_anthropic_role_client",
    "_build_strict_debate_role_runners",
    "_calc_assumptions",
    "_calc_evidence_block",
    "_calc_input_claim_ids",
    "_calc_output_payload",
    "_calc_output_summary",
    "_claim_source_summary",
    "_deal_metadata_stage_text",
    "_extraction_prompt_version",
    "_find_project_root",
    "_get_extraction_output_schema",
    "_get_extraction_prompt",
    "_graph_calculations_by_claim",
    "_graph_claims",
    "_graph_defects_by_claim",
    "_graph_documents",
    "_graph_evidence_by_claim",
    "_graph_spans",
    "_graph_transmission_nodes_by_claim",
    "_index_full_calc_outputs",
    "_index_full_graph_summaries",
    "_load_debate_prompts",
    "_new_budgeted_llm_client",
    "_normalize_input_ref",
    "_project_graph_evidence",
    "_prompt_registry_version",
    "_provider_label",
    "_resolve_analysis_selection",
    "_resolve_debate_selection",
    "_resolve_extraction_selection",
    "_resolve_run_audit_sink",
    "_resolve_scoring_selection",
    "_retrieve_claims_for_debate",
    "_retrieve_graph_evidence",
    "_run_full_analysis",
    "_run_full_debate",
    "_run_full_deliverables",
    "_run_full_enrichment",
    "_run_full_graph_evidence",
    "_run_full_layer2_ic_challenge",
    "_run_full_methodology_deterministic_calculation",
    "_run_full_rag_evidence",
    "_run_full_rag_evidence_inner",
    "_run_full_scoring",
    "_run_snapshot_auto_grade",
    "_run_snapshot_calc",
    "_run_snapshot_extraction",
    "_safe_client_request_id",
    "_safe_runner_executed",
    "_safe_runner_request_id",
    "_shared_role_config",
    "logger",
)
_KEPT_ROUTE_USE = "_resolve_run_audit_sink"
ROUTE_ALLOWED_NAMES = frozenset({"logger", _KEPT_ROUTE_USE})
_STEP_RUNTIME_STEMS = (
    "analysis",
    "common",
    "deliverables",
    "enrichment",
    "evidence",
    "layer2",
    "roles",
    "snapshot",
)

_MISSING = object()


def module_defined_names(source: str) -> frozenset[str]:
    """Return names a module assigns or defines at module level."""
    defined: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.append(node.target.id)
    return frozenset(defined)


def unexpected_closure_names(
    runs_module: ModuleType,
    names: tuple[str, ...],
    *,
    allowed: frozenset[str],
) -> tuple[str, ...]:
    """Return closure names bound on the route module outside ``allowed``."""
    return tuple(name for name in names if name in vars(runs_module) and name not in allowed)


def assert_moved_names_absent(
    runs_module: ModuleType,
    names: tuple[str, ...],
    *,
    allowed: frozenset[str],
) -> None:
    """Fail when a moved name is still an attribute of the route module."""
    unexpected = unexpected_closure_names(runs_module, names, allowed=allowed)
    if unexpected:
        rendered = ", ".join(unexpected)
        raise AssertionError(f"moved names still bound on the route module: {rendered}")


def step_runtime_definitions_bound_on_route(runs_module: ModuleType) -> tuple[str, ...]:
    """Return step_runtime definitions that are also attributes of the route module."""
    found: list[str] = []
    for stem in _STEP_RUNTIME_STEMS:
        module = importlib.import_module(f"idis.services.runs.step_runtime.{stem}")
        module_source = Path(module.__file__).read_text(encoding="utf-8")
        for name in module_defined_names(module_source):
            if name in vars(runs_module) and name != "logger" and name not in found:
                found.append(name)
    return tuple(found)


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
        assert reexport_problems((), ModuleType("synthetic.runs"), {}) == ()
        runs_module = importlib.import_module("idis.api.routes.runs")
        assert_moved_names_absent(
            runs_module,
            MOVED_CLUSTER_NAMES,
            allowed=ROUTE_ALLOWED_NAMES,
        )
        assert _KEPT_ROUTE_USE in vars(runs_module)
        assert step_runtime_definitions_bound_on_route(runs_module) == (_KEPT_ROUTE_USE,)
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
        assert unlisted_step_runtime_callables(runs_module, REEXPORTED_NAMES) == (_KEPT_ROUTE_USE,)
        return
    runs_module = importlib.import_module("idis.api.routes.runs")
    assert_step_runtime_callables_listed(runs_module, REEXPORTED_NAMES)


def test_planted_closure_name_on_the_route_module_fails() -> None:
    """A moved name planted on a route module fails the absence check."""
    runs = ModuleType("synthetic.runs")

    def moved() -> str:
        return "moved"

    runs._run_full_analysis = moved
    assert unexpected_closure_names(
        runs,
        ("_run_full_analysis",),
        allowed=frozenset(),
    ) == ("_run_full_analysis",)
    with pytest.raises(AssertionError, match="_run_full_analysis"):
        assert_moved_names_absent(runs, ("_run_full_analysis",), allowed=frozenset())


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


def _load_synthetic_route(directory: Path, module_name: str, source: str) -> ModuleType:
    """Load route source from a file under ``directory``."""
    path = directory / f"{module_name}.py"
    path.write_text(source, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load synthetic route module {module_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_source_that_defines_a_moved_name_fails_both_checks(tmp_path: Path) -> None:
    """A source-level copy of a moved name fails the real allowed set and stays visible."""
    source = "logger = None\n\ndef _run_full_analysis():\n    return None\n"
    route = _load_synthetic_route(tmp_path, "s11c_planted_moved_definition", source)
    with pytest.raises(AssertionError, match="_run_full_analysis"):
        assert_moved_names_absent(route, MOVED_CLUSTER_NAMES, allowed=ROUTE_ALLOWED_NAMES)
    assert step_runtime_definitions_bound_on_route(route) == ("_run_full_analysis",)


def test_route_source_that_keeps_only_the_allowed_use_passes(tmp_path: Path) -> None:
    """Logger plus the kept import of the audit sink passes both checks."""
    source = (
        "logger = None\n"
        "from idis.services.runs.step_runtime.common import _resolve_run_audit_sink\n"
    )
    route = _load_synthetic_route(tmp_path, "s11c_kept_route_use", source)
    assert_moved_names_absent(route, MOVED_CLUSTER_NAMES, allowed=ROUTE_ALLOWED_NAMES)
    assert step_runtime_definitions_bound_on_route(route) == (_KEPT_ROUTE_USE,)
