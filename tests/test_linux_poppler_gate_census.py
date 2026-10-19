"""Census for the Linux-Poppler closed list and the Windows byte-pin rows."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest

import tests._canonical_poppler_pin as pin_gate
import tests._linux_poppler_gate as linux_gate
from idis.parsers.poppler_runtime import (
    PINNED_POPPLER_VERSION,
    PinnedPopplerRuntime,
    PopplerRuntimeBindingError,
    _runtime_identity_sha256,
)
from tests._canonical_poppler_pin import CANONICAL_POPPLER_PIN_GATED_NODES
from tests._linux_poppler_gate import LINUX_POPPLER_DATABASE_NODES, LINUX_POPPLER_GATED_NODES

_REPO = Path(__file__).resolve().parents[1]
_LINUX_DECORATOR = "requires_linux_governed_poppler"
_PIN_DECORATOR = "requires_canonical_poppler_pin"
_ITEM_K = frozenset({"requires_private_artifact", "requires_operator_postgres"})
_PROBE = "linux_governed_poppler_absent"
_PDF_FUNCS = frozenset(
    {
        "convert_from_bytes",
        "convert_from_path",
        "pdfinfo_from_bytes",
        "pdfinfo_from_path",
    }
)
_SYMBOLS = frozenset(
    {
        "resolve_pinned_poppler_runtime",
        "install_poppler_child_environment",
        "PinnedLinuxPopplerRuntime",
    }
)
_PREFIX = "/opt/idis-native/poppler-25.12.0"
_PIN_IDENTITY = "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"

_MODULE_ALLOW: dict[str, str] = {
    "tests/_cir04c3_exclusion_signer.py": "Signer resolves the governed binding.",
    "tests/_linux_poppler_gate.py": "Linux gate defines the probe and the closed list.",
    "tests/_canonical_poppler_pin.py": "Pin gate defines the Windows type predicate.",
    "tests/test_linux_poppler_gate_census.py": "Census names the symbols it searches for.",
    "tests/test_pg_bootstrap_ci_required_preview.py": "CI recipe pins the prefix and signer seam.",
    "tests/slice100_task4/test_linux_poppler_parent_guard.py": "Parent-guard stubs the binding.",
    "tests/slice100_task4/test_linux_poppler_identity_admission.py": (
        "Identity admission probes the binding."
    ),
    "tests/slice100_task4/test_linux_poppler_loader_negative.py": (
        "Loader-negative probes the prefix."
    ),
    "tests/slice100_task4/test_linux_poppler_image_recipe.py": "Image recipe pins the prefix path.",
    "tests/slice100_task4/test_poppler_runtime_binding.py": "Binding tests inject resolvers.",
    "tests/slice100_task4/test_ocr_render_admission_adapter.py": (
        "Adapter tests inject the resolver."
    ),
    "tests/slice100_task4/test_strict_durable_api_gate.py": "F2 hermetic resolver replacement.",
    "tests/slice100_task2/test_worker_strict_preflight_admission.py": (
        "F2 hermetic resolver replacement."
    ),
    "tests/slice100_task2/test_strict_profile_conformance.py": "F2 hermetic Linux runtime.",
    "tests/slice100_task2/test_startup_dependency_admission.py": (
        "F2 hermetic resolver replacement."
    ),
    "tests/test_ci_bootstrap_health_composition.py": "F2 hermetic resolver name.",
    "tests/slice100_task4/test_ocr_batched_file_worker.py": (
        "Calls the parent-guard stub seam. Not a closed-list row."
    ),
    "tests/slice100_task4/test_ocr_no_consensus_proof.py": (
        "Calls the parent-guard stub seam. Not a closed-list row."
    ),
    "tests/slice100_task4/test_ocr_rescue.py": (
        "Calls the parent-guard stub seam. Not a closed-list row."
    ),
}
_FUNCTION_ALLOW: dict[tuple[str, str], str] = {}

_IN_BODY_GATE_RULE = (
    "An in-body record-and-pass gate with a required mode is registered as a gate, "
    "never allow-listed."
)


class InBodyGate(NamedTuple):
    """Registry row for an in-body record-and-pass gate."""

    name: str
    token: str
    property: str
    variable: str


_IN_BODY_GATES = (
    InBodyGate(
        name=(
            "tests/slice100_task4/test_linux_poppler_loader_negative.py::"
            "test_planted_libjpeg_is_not_loaded_under_helper_environment"
        ),
        token="LOADER_NEGATIVE NOT_EXECUTED",
        property="loader_negative",
        variable="IDIS_REQUIRE_LOADER_NEGATIVE",
    ),
)
_IN_BODY_GATE_NAMES = frozenset(gate.name for gate in _IN_BODY_GATES)

_LINUX_PARAMS: dict[tuple[str, str], tuple[str, ...]] = {
    (
        "tests/test_slice100_task1_queue_worker.py",
        "test_strict_api_uses_reviewed_role_despite_ambient_mutation",
    ): ("[None]", "[]", "[worker]", "[api,worker]", "[combined]"),
    (
        "tests/slice100_task4/test_complete_pdf_page_census_red.py",
        "test_image_only_receipt_without_render_or_no_consensus_provenance_fails_closed",
    ): ("[ocr_render_admission]", "[ocr_page_execution_receipt]"),
    (
        "tests/slice100_task4/test_watermark_batch_attestation_red.py",
        "test_batch_rejects_digest_or_exemplar_drift",
    ): (
        "[candidate_set_sha256-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc]",
        (
            "[classifier_policy_sha256-"
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff]"
        ),
        (
            "[exemplar_canonical_bitmap_sha256-"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb]"
        ),
        "[exemplar_content_sha256-0000000000000000000000000000000000000000000000000000000000000000]",
        (
            "[exemplar_envelope_sha256-"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa]"
        ),
        "[exemplar_safe_alias-company-002-document-00054.pdf]",
        "[prescan_receipt_sha256-dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd]",
        "[sample_set_sha256-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee]",
    ),
    (
        "tests/slice100_task4/test_watermark_batch_attestation_red.py",
        "test_batch_rejects_order_duplicates_and_schema_drift",
    ): (
        "[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID0]",
        "[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID1]",
        "[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID2]",
        "[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID3]",
    ),
    (
        "tests/slice100_task4/test_watermark_batch_attestation_red.py",
        "test_batch_rejects_signed_exemplar_candidate_identity_drift",
    ): (
        "[exemplar-canonical-drift]",
        "[exemplar-not-exact-self-match]",
        "[exemplar-not-page-one]",
    ),
    (
        "tests/slice100_task4/test_watermark_batch_attestation_red.py",
        "test_batch_requires_active_half_open_reviewer_interval",
    ): ("[now0]", "[now1]", "[now2]"),
    (
        "tests/slice100_task4/test_watermark_classifier_domains_red.py",
        "test_canonical_exemplar_domain_rejects_degenerate_or_wrong_geometry_mutations",
    ): (
        "[all-background]",
        "[all-foreground]",
        "[one-byte-long]",
        "[one-byte-short]",
    ),
    (
        "tests/slice100_task4/test_watermark_page_detector_red.py",
        "test_any_content_or_incomplete_rescue_bypasses_classifier",
    ): (
        "[boolean-native-count]",
        "[native-content-present]",
        "[primary-ocr-content-present]",
        "[rescue-content-present]",
        "[rescue-matrix-incomplete]",
        "[rescue-matrix-not-24]",
        "[rescue-not-terminal]",
    ),
    (
        "tests/slice100_task4/test_watermark_page_detector_red.py",
        "test_faint_signature_bounds_are_inclusive",
    ): (
        "[near-white-0.970000-ink-0.030000]",
        "[near-white-0.985000-ink-0.015000]",
    ),
    (
        "tests/slice100_task4/test_watermark_page_detector_red.py",
        "test_faint_signature_fails_immediately_outside_exact_bounds",
    ): (
        "[both-lower-limits-missed]",
        "[both-upper-limits-exceeded]",
    ),
    (
        "tests/slice100_task4/test_watermark_page_detector_red.py",
        "test_render_dpi_requires_the_exact_pinned_integer_type",
    ): ("[float-equal-value]", "[wrong-value]"),
    (
        "tests/slice100_task4/test_watermark_pre_ocr_classifier.py",
        "test_pre_ocr_classifier_requires_an_exact_integer_zero_native_text_count",
    ): ("[boolean-zero]", "[native-text-present]"),
    (
        "tests/slice100_task4/test_watermark_review_resolution.py",
        "test_resolved_prescan_mutation_fails_closed",
    ): ("[<lambda>0]", "[<lambda>1]", "[<lambda>2]", "[<lambda>3]"),
    (
        "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
        "test_replay_rejects_current_admission_binding_drift",
    ): (
        "[batch_envelope_sha256]",
        "[candidate_set_sha256]",
        "[classifier_policy_sha256]",
        "[manifest_sha256]",
    ),
    (
        "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
        "test_source_package_revalidates_v2_receipt_before_membership",
    ): (
        "[mixed-document-page-exclusion]",
        "[whole-document-terminal]",
    ),
}
_LINUX_CLASSES = {
    "test_api_corpus_exists_but_no_usable_docs_fails_document_preflight": (
        "TestStartRunPreflightCorpusBehavior"
    ),
    "test_api_mixed_corpus_continues_with_eligible_docs_only": (
        "TestStartRunPreflightCorpusBehavior"
    ),
}


def _linux_node_ids() -> tuple[str, ...]:
    ids: list[str] = []
    for module, name in LINUX_POPPLER_GATED_NODES:
        owner = _LINUX_CLASSES.get(name)
        head = f"{module}::{owner}::{name}" if owner else f"{module}::{name}"
        ids.extend(head + suffix for suffix in _LINUX_PARAMS.get((module, name), ("",)))
    return tuple(ids)


def _half_pixel_node_ids() -> tuple[str, ...]:
    prefix = (
        "tests/slice100_task4/test_pdf_render_rounding_red.py::"
        "test_real_poppler_half_pixel_matrix_emits_exact_v2_render_provenance"
    )
    return tuple(
        f"{prefix}[{position}-{dpi}dpi]"
        for dpi in (200, 150, 120, 96, 72)
        for position in ("below-half", "half", "above-half")
    )


def _canonical_node_ids() -> tuple[str, ...]:
    worker = (
        "tests/slice100_task4/test_pdf_render_rounding_red.py::"
        "test_worker_rejects_expected_dimension_mismatch_and_actual_resource_overrun"
    )
    return (
        "tests/slice100_task4/test_complete_pdf_page_census_red.py::"
        "test_production_mixed_fixture_persists_complete_disjoint_census",
        "tests/slice100_task4/test_complete_pdf_page_census_red.py::"
        "test_page_claimed_by_native_text_and_image_only_receipt_fails_closed",
        "tests/slice100_task4/test_complete_pdf_page_census_red.py::"
        "test_all_image_only_pdf_remains_reviewer_disposition_blocker",
        "tests/slice100_task4/test_pdf_page_ocr_merge.py::"
        "test_read_only_pdf_ocr_census_binds_required_files_by_content_hash",
        "tests/slice100_task4/test_pdf_page_ocr_merge.py::"
        "test_read_only_census_derives_highest_fitting_rung_from_source_geometry",
        *_half_pixel_node_ids(),
        f"{worker}[exact-mode-one-pixel-mismatch]",
        f"{worker}[actual-resource-overrun]",
        (
            "tests/slice100_task4/test_pdf_render_rounding_red.py::"
            "test_real_rounding_artifact_succeeds_through_file_backed_worker"
        ),
    )


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _functions(tree: ast.AST) -> Iterator[tuple[str, ast.FunctionDef]]:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    yield f"{node.name}.{item.name}", item


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _module_file(dotted: str, root: Path) -> str | None:
    rel = dotted.replace(".", "/")
    for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return None


class _Parsed:
    def __init__(self, path: str, tree: ast.AST, root: Path) -> None:
        self.path = path
        self.tree = tree
        self.root = root
        self.functions = dict(_functions(tree))
        self.bare = {name.split(".")[-1]: node for name, node in self.functions.items()}
        self.pdf_names: set[str] = set()
        self.pdf_modules: set[str] = set()
        self.imports: dict[str, tuple[str, str]] = {}
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.ImportFrom) and node.module == "pdf2image":
                for alias in node.names:
                    self.pdf_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pdf2image":
                        self.pdf_modules.add(alias.asname or alias.name)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module.startswith("tests"))
            ):
                target = _module_file(node.module, root)
                if target is None:
                    continue
                for alias in node.names:
                    if alias.name != "*":
                        self.imports[alias.asname or alias.name] = (target, alias.name)


def _parsed_modules(root: Path = _REPO) -> dict[str, _Parsed]:
    found: dict[str, _Parsed] = {}
    tests = root / "tests"
    for path in tests.rglob("*.py"):
        rel = _rel(path, root)
        found[rel] = _Parsed(rel, ast.parse(path.read_text(encoding="utf-8")), root)
    return found


def _direct(parsed: _Parsed, fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in _SYMBOLS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _SYMBOLS:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if _PREFIX in text or text in _PDF_FUNCS or any(symbol in text for symbol in _SYMBOLS):
                return True
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in parsed.pdf_names:
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _PDF_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id in parsed.pdf_modules
        ):
            return True
    return False


def _local_imports(parsed: _Parsed, node: ast.AST) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for child in ast.walk(node):
        if not isinstance(child, ast.ImportFrom) or not child.module:
            continue
        if not child.module.startswith("tests"):
            continue
        target = _module_file(child.module, parsed.root)
        if target is None:
            continue
        for alias in child.names:
            if alias.name != "*":
                found[alias.asname or alias.name] = (target, alias.name)
    return found


def _callees(parsed: _Parsed, qual: str, fn: ast.AST) -> set[tuple[str, str]]:
    owner = qual.split(".", 1)[0] if "." in qual else ""
    imports = {**parsed.imports, **_local_imports(parsed, fn)}
    found: set[tuple[str, str]] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in parsed.functions:
                found.add((parsed.path, func.id))
            elif func.id in parsed.bare:
                matched = parsed.bare[func.id]
                qualname = next(q for q, item in parsed.functions.items() if item is matched)
                found.add((parsed.path, qualname))
            imported = imports.get(func.id)
            if imported is not None:
                found.add(imported)
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and owner
        ):
            target = f"{owner}.{func.attr}"
            if target in parsed.functions:
                found.add((parsed.path, target))
    return found


def _reaches(
    modules: dict[str, _Parsed],
    key: tuple[str, str],
    seen: set[tuple[str, str]],
) -> bool:
    if key in seen:
        return False
    parsed = modules.get(key[0])
    if parsed is None or key[1] not in parsed.functions:
        return False
    fn = parsed.functions[key[1]]
    if _direct(parsed, fn):
        return True
    seen.add(key)
    return any(_reaches(modules, callee, seen) for callee in _callees(parsed, key[1], fn))


def _calls_probe(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        called = isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        if called and node.func.id == _PROBE:
            return True
    return False


_INJECT_KEYWORDS = frozenset({"binary_resolver", "version_probe", "file_hasher"})
_STUB_CALLS = frozenset(
    {
        "install_poppler_child_environment",
        "PinnedLinuxPopplerRuntime",
        "_stub_linux_binding",
    }
)
_PATCH_CALLS = frozenset({"setattr", "patch"})


def _called_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _walk_statement(stmt: ast.stmt) -> Iterator[ast.AST]:
    stack = list(ast.iter_child_nodes(stmt))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _call_is_injected(call: ast.Call) -> bool:
    return _called_name(call.func) == "resolve_pinned_poppler_runtime" and any(
        keyword.arg in _INJECT_KEYWORDS for keyword in call.keywords
    )


def _call_is_stub(call: ast.Call) -> bool:
    return _called_name(call.func) in _STUB_CALLS


def _text_kind(text: str) -> str | None:
    if text in _PDF_FUNCS or "pdf2image" in text:
        return "pdf"
    if text in _SYMBOLS or "poppler" in text:
        return "resolver"
    return None


def _call_patch_kinds(call: ast.Call) -> set[str]:
    if _called_name(call.func) not in _PATCH_CALLS:
        return set()
    kinds: set[str] = set()
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            kind = _text_kind(node.value)
            if kind is not None:
                kinds.add(kind)
        elif isinstance(node, ast.Attribute) and node.attr in _SYMBOLS | _PDF_FUNCS:
            kinds.add("pdf" if node.attr in _PDF_FUNCS else "resolver")
    return kinds


def _stmt_patch_kinds(stmt: ast.stmt) -> set[str]:
    kinds: set[str] = set()
    for node in ast.walk(stmt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in _PDF_FUNCS:
                    kinds.add("pdf")
                if isinstance(target, ast.Attribute) and target.attr in _SYMBOLS:
                    kinds.add("resolver")
        if isinstance(node, ast.Call):
            kinds.update(_call_patch_kinds(node))
    return kinds


def _decorator_patch_kinds(fn: ast.FunctionDef) -> set[str]:
    kinds: set[str] = set()
    for deco in fn.decorator_list:
        if isinstance(deco, ast.Call):
            kinds.update(_call_patch_kinds(deco))
    return kinds


def _call_reach_kind(parsed: _Parsed, call: ast.Call) -> str | None:
    if _call_is_injected(call) or _call_is_stub(call) or _call_patch_kinds(call):
        return None
    name = _called_name(call.func)
    if name in _SYMBOLS:
        return "resolver"
    if name in parsed.pdf_names or name in _PDF_FUNCS:
        return "pdf"
    return None


def _stmt_callees(parsed: _Parsed, qual: str, stmt: ast.stmt) -> set[tuple[str, str]]:
    owner = qual.split(".", 1)[0] if "." in qual else ""
    imports = {**parsed.imports, **_local_imports(parsed, stmt)}
    found: set[tuple[str, str]] = set()
    for node in _walk_statement(stmt):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in parsed.functions:
                found.add((parsed.path, func.id))
            elif func.id in parsed.bare:
                matched = parsed.bare[func.id]
                qualname = next(name for name, item in parsed.functions.items() if item is matched)
                found.add((parsed.path, qualname))
            imported = imports.get(func.id)
            if imported is not None:
                found.add(imported)
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and owner
        ):
            target = f"{owner}.{func.attr}"
            if target in parsed.functions:
                found.add((parsed.path, target))
    return found


def _stmt_reach_kinds(
    modules: dict[str, _Parsed],
    parsed: _Parsed,
    qual: str,
    stmt: ast.stmt,
    guard: frozenset[str],
    seen: set[tuple[str, str]],
) -> set[str]:
    kinds: set[str] = set()
    for node in _walk_statement(stmt):
        if isinstance(node, ast.Call):
            kind = _call_reach_kind(parsed, node)
            if kind is not None:
                kinds.add(kind)
    for callee in _stmt_callees(parsed, qual, stmt):
        if callee == (parsed.path, qual):
            continue
        kinds.update(_unguarded_kinds(modules, callee, seen, guard))
    return kinds


def _record_header(
    modules: dict[str, _Parsed],
    parsed: _Parsed,
    qual: str,
    header: ast.AST,
    local: set[str],
    found: set[str],
    seen: set[tuple[str, str]],
) -> None:
    for kind in _stmt_reach_kinds(modules, parsed, qual, ast.Expr(header), frozenset(local), seen):
        if kind not in local:
            found.add(kind)


def _collect_kinds(
    modules: dict[str, _Parsed],
    parsed: _Parsed,
    qual: str,
    body: list[ast.stmt],
    guard: set[str],
    found: set[str],
    seen: set[tuple[str, str]],
) -> None:
    local = set(guard)
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.If):
            _record_header(modules, parsed, qual, stmt.test, local, found, seen)
            _collect_kinds(modules, parsed, qual, stmt.body, local, found, seen)
            _collect_kinds(modules, parsed, qual, stmt.orelse, local, found, seen)
            continue
        if isinstance(stmt, ast.For):
            _record_header(modules, parsed, qual, stmt.iter, local, found, seen)
            _collect_kinds(modules, parsed, qual, stmt.body, local, found, seen)
            _collect_kinds(modules, parsed, qual, stmt.orelse, local, found, seen)
            continue
        if isinstance(stmt, ast.While):
            _record_header(modules, parsed, qual, stmt.test, local, found, seen)
            _collect_kinds(modules, parsed, qual, stmt.body, local, found, seen)
            _collect_kinds(modules, parsed, qual, stmt.orelse, local, found, seen)
            continue
        if isinstance(stmt, ast.Try):
            branches = [stmt.body, stmt.orelse, stmt.finalbody]
            branches.extend(handler.body for handler in stmt.handlers)
            for branch in branches:
                _collect_kinds(modules, parsed, qual, branch, local, found, seen)
            continue
        if isinstance(stmt, ast.With):
            inner = set(local)
            inner.update(_stmt_patch_kinds(stmt))
            _collect_kinds(modules, parsed, qual, stmt.body, inner, found, seen)
            continue
        patch_kinds = _stmt_patch_kinds(stmt)
        if patch_kinds:
            local.update(patch_kinds)
            continue
        for kind in _stmt_reach_kinds(modules, parsed, qual, stmt, frozenset(local), seen):
            if kind not in local:
                found.add(kind)


def _unguarded_kinds(
    modules: dict[str, _Parsed],
    key: tuple[str, str],
    seen: set[tuple[str, str]],
    incoming: frozenset[str],
) -> frozenset[str]:
    if key in seen:
        return frozenset()
    parsed = modules.get(key[0])
    if parsed is None or key[1] not in parsed.functions:
        return frozenset()
    if not _reaches(modules, key, set()):
        return frozenset()
    seen.add(key)
    fn = parsed.functions[key[1]]
    found: set[str] = set()
    guard = set(incoming)
    guard.update(_decorator_patch_kinds(fn))
    _collect_kinds(modules, parsed, key[1], fn.body, guard, found, seen)
    return frozenset(found)


def _is_hermetic(
    modules: dict[str, _Parsed],
    key: tuple[str, str],
    seen: set[tuple[str, str]],
) -> bool:
    return not _unguarded_kinds(modules, key, seen, frozenset())


def _reach_reason(path: str, qual: str) -> str | None:
    named = _FUNCTION_ALLOW.get((path, _bare_name(qual)))
    if named:
        return named
    return _MODULE_ALLOW.get(path)


def _ungated_reaches(root: Path = _REPO) -> tuple[list[str], list[str]]:
    modules = _parsed_modules(root)
    unproven: list[str] = []
    ungated: list[str] = []
    for parsed in modules.values():
        for qual, fn in parsed.functions.items():
            names = _decorator_names(fn)
            if _LINUX_DECORATOR in names or _PIN_DECORATOR in names or names & _ITEM_K:
                continue
            is_fixture = "fixture" in names
            is_test = _bare_name(qual).startswith("test_") and not is_fixture
            if not is_fixture and not is_test:
                continue
            if is_fixture and _calls_probe(fn):
                continue
            key = (parsed.path, qual)
            if not _reaches(modules, key, set()):
                continue
            label = f"{parsed.path}::{qual}"
            if label in _IN_BODY_GATE_NAMES:
                continue
            if _is_hermetic(modules, key, set()) and _reach_reason(parsed.path, qual):
                continue
            if _is_hermetic(modules, key, set()):
                unproven.append(label)
            else:
                ungated.append(label)
    return sorted(unproven), sorted(ungated)


def _fixture_candidates(
    modules: dict[str, _Parsed], fixture_name: str
) -> list[tuple[str, str, ast.FunctionDef]]:
    found: list[tuple[str, str, ast.FunctionDef]] = []
    for parsed in modules.values():
        for qual, fn in parsed.functions.items():
            if "fixture" not in _decorator_names(fn) or _bare_name(qual) != fixture_name:
                continue
            found.append((parsed.path, qual, fn))
    return found


def _pytest_plugin_paths(parsed: _Parsed) -> tuple[str, ...]:
    paths: list[str] = []
    for node in getattr(parsed.tree, "body", []):
        if not isinstance(node, ast.Assign):
            continue
        named = any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        )
        if not named:
            continue
        value = node.value
        elements = value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value]
        for element in elements:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                continue
            target = _module_file(element.value, parsed.root)
            if target is not None:
                paths.append(target)
    return tuple(paths)


def _scoped_fixture(
    modules: dict[str, _Parsed], test_path: str, fixture_name: str
) -> tuple[str, str, ast.FunctionDef] | None:
    candidates = _fixture_candidates(modules, fixture_name)
    same_module = [item for item in candidates if item[0] == test_path]
    if len(same_module) == 1:
        return same_module[0]
    if len(same_module) > 1:
        return None
    for parent in Path(test_path).parents:
        conftest = "conftest.py" if parent.as_posix() == "." else f"{parent.as_posix()}/conftest.py"
        located = [item for item in candidates if item[0] == conftest]
        if len(located) == 1:
            return located[0]
        if len(located) > 1:
            return None
    parsed = modules.get(test_path)
    if parsed is not None:
        plugin_paths = set(_pytest_plugin_paths(parsed))
        plugin_hits = [item for item in candidates if item[0] in plugin_paths]
        if len(plugin_hits) == 1:
            return plugin_hits[0]
        if len(plugin_hits) > 1:
            return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def _bare_name(qual: str) -> str:
    return qual.split(".")[-1]


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )


def _printed_linux_ids(*flags: str) -> set[str]:
    completed = _run([sys.executable, "-m", "tests._linux_poppler_gate", "--node-ids", *flags])
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return {line.replace("\\", "/") for line in completed.stdout.splitlines() if line}


_DATABASE_FIXTURE_NAMES = frozenset({"app_engine", "is_postgres_configured"})


def _named_function(
    modules: dict[str, _Parsed], module: str, name: str
) -> tuple[str, ast.FunctionDef]:
    parsed = modules[module]
    matches = [(qual, fn) for qual, fn in parsed.functions.items() if _bare_name(qual) == name]
    if len(matches) != 1:
        raise AssertionError(f"{module}::{name} matched {len(matches)} functions")
    return matches[0]


def _calls_database_symbol(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _DATABASE_FIXTURE_NAMES:
            return True
        if isinstance(func, ast.Attribute) and func.attr in _DATABASE_FIXTURE_NAMES:
            return True
    return False


def _requested_fixtures(fn: ast.FunctionDef) -> tuple[str, ...]:
    names = [
        arg.arg
        for arg in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)
        if arg.arg not in {"self", "cls"}
    ]
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        target = deco.func
        label = target.attr if isinstance(target, ast.Attribute) else ""
        if isinstance(target, ast.Name):
            label = target.id
        if label != "usefixtures":
            continue
        for arg in deco.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.append(arg.value)
    return tuple(names)


def _reaches_database_fixture(
    modules: dict[str, _Parsed],
    path: str,
    qual: str,
    fn: ast.FunctionDef,
    seen: set[tuple[str, str]],
) -> bool:
    if (path, qual) in seen:
        return False
    seen.add((path, qual))
    if _bare_name(qual) in _DATABASE_FIXTURE_NAMES:
        return True
    if "fixture" in _decorator_names(fn) and _calls_database_symbol(fn):
        return True
    for fixture_name in _requested_fixtures(fn):
        if fixture_name in _DATABASE_FIXTURE_NAMES:
            return True
        if not _fixture_candidates(modules, fixture_name):
            continue
        located = _scoped_fixture(modules, path, fixture_name)
        if located is None:
            for candidate_path, candidate_qual, candidate_fn in _fixture_candidates(
                modules, fixture_name
            ):
                if _reaches_database_fixture(
                    modules, candidate_path, candidate_qual, candidate_fn, seen
                ):
                    return True
            continue
        fixture_path, fixture_qual, fixture_fn = located
        if _reaches_database_fixture(modules, fixture_path, fixture_qual, fixture_fn, seen):
            return True
    return False


def _gated_reaches_database_fixture(modules: dict[str, _Parsed], module: str, name: str) -> bool:
    qual, fn = _named_function(modules, module, name)
    return _reaches_database_fixture(modules, module, qual, fn, set())


class _PinSubclass(PinnedPopplerRuntime):
    """A distinct type. The canonical predicate admits only the exact pin class."""


def _windows_pin(
    *,
    cls: type[PinnedPopplerRuntime] = PinnedPopplerRuntime,
) -> PinnedPopplerRuntime:
    digest = "a" * 64
    root = Path.cwd().resolve() / "synthetic-pin"
    pin = cls(
        version=PINNED_POPPLER_VERSION,
        pdftoppm_sha256=digest,
        poppler_dll_sha256=digest,
        runtime_identity_sha256=_runtime_identity_sha256(
            version=PINNED_POPPLER_VERSION,
            pdftoppm_sha256=digest,
            poppler_dll_sha256=digest,
        ),
        _pdftoppm_path=root / "pdftoppm",
        _pdfinfo_path=root / "pdfinfo",
        _poppler_library_path=root / "poppler.dll",
        _platform_name="Windows",
    )
    return pin


def _assert_decorators_match(root: Path) -> None:
    """Require the two tuples and the decorators under ``root`` to name the same functions."""

    found_linux: set[tuple[str, str]] = set()
    found_pin: set[tuple[str, str]] = set()
    tests = root / "tests"
    for path in tests.rglob("*.py"):
        rel = _rel(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qual, fn in _functions(tree):
            names = _decorator_names(fn)
            pair = (rel, _bare_name(qual))
            assert _LINUX_DECORATOR not in names or _PIN_DECORATOR not in names
            if _LINUX_DECORATOR in names:
                found_linux.add(pair)
            if _PIN_DECORATOR in names:
                found_pin.add(pair)
    missing_linux = sorted(set(LINUX_POPPLER_GATED_NODES) - found_linux)
    extra_linux = sorted(found_linux - set(LINUX_POPPLER_GATED_NODES))
    missing_pin = sorted(set(CANONICAL_POPPLER_PIN_GATED_NODES) - found_pin)
    extra_pin = sorted(found_pin - set(CANONICAL_POPPLER_PIN_GATED_NODES))
    assert not missing_linux and not extra_linux and not missing_pin and not extra_pin, (
        missing_linux,
        extra_linux,
        missing_pin,
        extra_pin,
    )


def test_decorated_functions_match_both_tuples() -> None:
    """AST decorators and the two tuples name the same functions, both ways."""

    _assert_decorators_match(_REPO)


def test_collected_linux_node_ids_match_the_printer() -> None:
    """The printer emits every parametrisation and nothing outside the tuple."""

    printed = _printed_linux_ids()
    assert printed == set(_linux_node_ids())
    assert len(printed) == 113


def test_database_class_partition_and_fixture_reach() -> None:
    """The database class is the three app_engine rows, and the printer partitions it.

    Fixture dependencies, followed through the census fixture map, reach
    app_engine or is_postgres_configured on every database-class row and on
    no no-database row.
    """

    assert LINUX_POPPLER_DATABASE_NODES == (
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_rejected_watermark_receipts_persist_no_attempt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_verified_watermark_exclusion_persists_one_pre_ocr_attempt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_verified_watermark_receipt_writes_registered_schema_ref",
        ),
    )
    citation = LINUX_POPPLER_DATABASE_NODES.__doc__ or ""
    assert "app_engine" in citation
    assert "is_postgres_configured" in citation
    assert "cir04-j-h3-6g-ruling.json" in citation
    assert "cir04h3-6g-ruling.json" in citation
    database_pairs = set(LINUX_POPPLER_DATABASE_NODES)
    gated = set(LINUX_POPPLER_GATED_NODES)
    assert database_pairs <= gated
    assert len(database_pairs) == 3
    modules = _parsed_modules()
    for module, name in LINUX_POPPLER_DATABASE_NODES:
        _qual, fn = _named_function(modules, module, name)
        assert _LINUX_DECORATOR in _decorator_names(fn)
    full = _printed_linux_ids()
    database_ids = _printed_linux_ids("--database-class")
    no_database_ids = _printed_linux_ids("--no-database-class")
    assert len(full) == 113
    assert len(database_ids) == 3
    assert len(no_database_ids) == 110
    assert database_ids.isdisjoint(no_database_ids)
    assert database_ids | no_database_ids == full
    reached = {
        (module, name)
        for module, name in LINUX_POPPLER_GATED_NODES
        if _gated_reaches_database_fixture(modules, module, name)
    }
    assert reached == database_pairs


def test_collected_canonical_node_ids_match_the_tuple() -> None:
    """Collection through the repo root loads every Windows-pin parametrisation."""

    completed = _run(
        [
            sys.executable,
            "-c",
            "from tests._canonical_poppler_pin import collected_canonical_poppler_pin_node_ids\n"
            "print('\\n'.join(collected_canonical_poppler_pin_node_ids()))\n",
        ]
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    collected = {line.replace("\\", "/") for line in completed.stdout.splitlines() if line}
    assert collected == set(_canonical_node_ids())
    assert len(collected) == 23


def test_tuples_are_disjoint() -> None:
    """A row is in at most one of the two gates."""

    assert set(LINUX_POPPLER_GATED_NODES).isdisjoint(set(CANONICAL_POPPLER_PIN_GATED_NODES))


def test_tokens_properties_and_variables_are_exact() -> None:
    """The recorded lines, property names, and requirement variables stay fixed."""

    assert linux_gate.LINUX_POPPLER_ABSENT_LINE == (
        "LINUX_POPPLER NOT_EXECUTED: governed prefix absent"
    )
    assert linux_gate.LINUX_POPPLER_PROPERTY == "linux_poppler"
    assert linux_gate.LINUX_POPPLER_REQUIRE_ENV == "IDIS_REQUIRE_LINUX_POPPLER"
    assert pin_gate.CANONICAL_POPPLER_PIN_ABSENT_LINE == (
        "CANONICAL_POPPLER_PIN NOT_EXECUTED: Windows byte pin absent"
    )
    assert pin_gate.CANONICAL_POPPLER_PIN_PROPERTY == "canonical_poppler_pin"
    assert pin_gate.CANONICAL_POPPLER_PIN_REQUIRE_ENV == "IDIS_REQUIRE_CANONICAL_POPPLER_PIN"
    assert pin_gate.pin_identity_sha256() == _PIN_IDENTITY
    citation = LINUX_POPPLER_GATED_NODES.__doc__ or ""
    assert "f8acde68fc2979c4d19c8d489f8c27750aa50dd95fa27da70e66f1f6ff51eef1" in citation
    assert "b0b6ba35eacbafa3c85a51375103b0e5f31cccdd510d789418d5c0418ec1d3f4" in citation
    assert "01c5371d0d1a05dcac0c6ac39b78283fa88e60b0290830055ab4568c3e4194fe" in citation
    for node_id in linux_gate._CONDITIONAL_NODE_IDS:
        assert node_id in citation
    pin_doc = CANONICAL_POPPLER_PIN_GATED_NODES.__doc__ or ""
    assert _PIN_IDENTITY in pin_doc
    assert "CANONICAL_POPPLER_PIN rows count toward acceptance only from the owner's" in pin_doc
    assert "A hosted NOT_EXECUTED result is never a pass." in pin_doc
    assert "Finding (e) is closed by option (i):" in citation
    assert (
        "The second class is derived mechanically: red in the check configuration "
        "after H3-6 with POPPLER_RUNTIME_UNAVAILABLE and green in hosted check at "
        "checkpoint 22; 'Ungated governed render' is not an allow-list reason."
    ) in citation
    assert "64 is a floor." in citation
    assert "3016b24fa46419e3e48e9fb7cfdd86bcab7ab227bd2fc63f4998d8380a90be4d" in citation
    assert "2584a5e3b869bb0613dd09f2295df0e93bd8e1fe60cd697f589f87ce3f2ace9b" in citation
    assert "3085c49491dd7b217420c769c5e19858f53d9c3e0df58e48294f953be9384ed4" in citation
    assert (
        "Rows whose binding cause is demonstrated per node by the hook join the "
        "closed list exactly as rows whose block shows it; the product's laundering "
        "of the code does not exclude them."
    ) in citation
    assert (
        "The run-4 exclusion of the three Tesseract-adapter rows rested on a false "
        "premise (the baseline had PATH Poppler) and is withdrawn."
    ) in citation
    remains = (
        "tests/test_tesseract_ocr_adapter.py::"
        "test_private_gate_ocr_enabled_remains_aggregate_only (call)"
    )
    spans = (
        "tests/test_tesseract_ocr_adapter.py::"
        "test_tesseract_adapter_success_creates_deterministic_spans (call)"
    )
    retries = (
        "tests/test_tesseract_ocr_adapter.py::"
        "test_private_gate_ocr_enabled_retries_prior_ocr_required_ledger_entry (call)"
    )
    assert remains in citation
    assert retries in citation
    assert citation.count(spans) == 2
    assert "Ungated governed render." not in _MODULE_ALLOW.values()
    assert all("Replaces pdf2image rendering" not in reason for reason in _MODULE_ALLOW.values())
    for source in (
        (_REPO / "tests/_linux_poppler_gate.py").read_text(encoding="utf-8"),
        (_REPO / "tests/_canonical_poppler_pin.py").read_text(encoding="utf-8"),
    ):
        assert "pytest.skip" not in source


def test_ungated_poppler_reach_is_zero() -> None:
    """Every governed reach is decorated, hermetic with a reason, or an in-body gate."""

    unproven, ungated = _ungated_reaches()
    assert unproven == []
    assert ungated == []


def test_in_body_loader_negative_required_mode_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Required mode fails the registered loader-negative gate."""

    assert _IN_BODY_GATE_RULE == (
        "An in-body record-and-pass gate with a required mode is registered as a gate, "
        "never allow-listed."
    )
    assert len(_IN_BODY_GATES) == 1
    gate = _IN_BODY_GATES[0]
    assert (gate.name, gate.token, gate.property, gate.variable) == (
        (
            "tests/slice100_task4/test_linux_poppler_loader_negative.py::"
            "test_planted_libjpeg_is_not_loaded_under_helper_environment"
        ),
        "LOADER_NEGATIVE NOT_EXECUTED",
        "loader_negative",
        "IDIS_REQUIRE_LOADER_NEGATIVE",
    )
    path, qual = gate.name.split("::", 1)
    assert (path, qual) not in _FUNCTION_ALLOW
    assert (path, qual) not in LINUX_POPPLER_GATED_NODES
    assert (path, qual) not in CANONICAL_POPPLER_PIN_GATED_NODES
    source = (_REPO / path).read_text(encoding="utf-8")
    assert gate.token in source
    assert gate.variable in source
    assert f'record_property("{gate.property}"' in source
    module = importlib.import_module(path.removesuffix(".py").replace("/", "."))
    reason = "cannot execute: host platform is not Linux"
    monkeypatch.setattr(module, "_inapplicable_reason", lambda: reason)
    recorded: list[tuple[str, object]] = []
    monkeypatch.delenv(gate.variable, raising=False)
    assert (
        getattr(module, qual)(tmp_path, lambda name, value: recorded.append((name, value))) is None
    )
    assert recorded == [(gate.property, f"NOT_EXECUTED: {reason}")]
    monkeypatch.setenv(gate.variable, "1")
    with pytest.raises(pytest.fail.Exception, match=reason):
        getattr(module, qual)(tmp_path, lambda name, value: None)


def test_rendering_fixtures_of_gated_nodes_consult_the_probe() -> None:
    """A rendering fixture used by a gated node is unique and calls the probe."""

    modules = _parsed_modules()
    rendering: dict[str, list[str]] = {}
    for parsed in modules.values():
        for qual, fn in parsed.functions.items():
            if "fixture" not in _decorator_names(fn):
                continue
            if not _reaches(modules, (parsed.path, qual), set()):
                continue
            rendering.setdefault(_bare_name(qual), []).append(f"{parsed.path}::{qual}")
    duplicates = {name: places for name, places in rendering.items() if len(places) != 1}
    assert duplicates == {}
    misses: list[str] = []
    for parsed in modules.values():
        for qual, fn in parsed.functions.items():
            names = _decorator_names(fn)
            if _LINUX_DECORATOR not in names and _PIN_DECORATOR not in names:
                continue
            for arg in fn.args.args:
                if arg.arg in {"self", "cls"}:
                    continue
                if not _fixture_candidates(modules, arg.arg):
                    continue
                located = _scoped_fixture(modules, parsed.path, arg.arg)
                if located is None:
                    misses.append(f"{parsed.path}::{qual} fixture {arg.arg} is ambiguous")
                    continue
                fixture_path, fixture_qual, fixture_fn = located
                if not _reaches(modules, (fixture_path, fixture_qual), set()):
                    continue
                if not _calls_probe(fixture_fn):
                    misses.append(f"{parsed.path}::{qual} fixture {fixture_path}::{fixture_qual}")
    assert misses == []


def test_linux_absence_records_and_returns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Linux absence records the fixed line and returns without skipping."""

    monkeypatch.setattr(linux_gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        linux_gate,
        "resolve_pinned_poppler_runtime",
        lambda: (_ for _ in ()).throw(PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")),
    )
    monkeypatch.setattr(linux_gate, "_LINUX_PROBE", None)
    monkeypatch.delenv(linux_gate.LINUX_POPPLER_REQUIRE_ENV, raising=False)
    recorded: list[tuple[str, object]] = []

    @linux_gate.requires_linux_governed_poppler
    def sample() -> str:
        return "ran"

    assert sample(record_property=lambda name, value: recorded.append((name, value))) is None
    assert capsys.readouterr().out.strip() == linux_gate.LINUX_POPPLER_ABSENT_LINE
    assert recorded == [(linux_gate.LINUX_POPPLER_PROPERTY, linux_gate.LINUX_POPPLER_ABSENT_LINE)]
    assert not hasattr(sample, "__wrapped__")


def test_linux_required_mode_fails_and_other_codes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required mode fails on absence, and any other binding code fails closed."""

    monkeypatch.setattr(linux_gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        linux_gate,
        "resolve_pinned_poppler_runtime",
        lambda: (_ for _ in ()).throw(PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")),
    )
    monkeypatch.setattr(linux_gate, "_LINUX_PROBE", None)
    monkeypatch.setenv(linux_gate.LINUX_POPPLER_REQUIRE_ENV, "1")

    @linux_gate.requires_linux_governed_poppler
    def sample() -> str:
        return "ran"

    with pytest.raises(pytest.fail.Exception, match="LINUX_POPPLER NOT_EXECUTED"):
        sample()
    monkeypatch.setenv(linux_gate.LINUX_POPPLER_REQUIRE_ENV, "0")
    monkeypatch.setattr(linux_gate, "_LINUX_PROBE", None)
    assert sample() is None
    monkeypatch.setattr(
        linux_gate,
        "resolve_pinned_poppler_runtime",
        lambda: (_ for _ in ()).throw(
            PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
        ),
    )
    monkeypatch.setattr(linux_gate, "_LINUX_PROBE", None)
    with pytest.raises(PopplerRuntimeBindingError) as caught:
        sample()
    assert caught.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"


def test_windows_required_mode_does_not_consult_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Linux key ignores Windows, including when the requirement variable is set."""

    calls: list[str] = []

    def resolver() -> Any:
        calls.append("called")
        raise AssertionError("resolver")

    monkeypatch.setattr(linux_gate.platform, "system", lambda: "Windows")
    monkeypatch.setattr(linux_gate, "resolve_pinned_poppler_runtime", resolver)
    monkeypatch.setattr(linux_gate, "_LINUX_PROBE", None)
    monkeypatch.setenv(linux_gate.LINUX_POPPLER_REQUIRE_ENV, "1")

    @linux_gate.requires_linux_governed_poppler
    def sample() -> str:
        return "ran"

    assert sample() == "ran"
    assert calls == []


def test_linux_probe_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Linux resolver runs once per process."""

    calls: list[int] = []

    def resolver() -> object:
        calls.append(1)
        return object()

    monkeypatch.setattr(linux_gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux_gate, "resolve_pinned_poppler_runtime", resolver)
    monkeypatch.setattr(linux_gate, "_LINUX_PROBE", None)
    assert linux_gate.linux_governed_poppler_absent() is False
    assert linux_gate.linux_governed_poppler_absent() is False
    assert calls == [1]


def test_non_windows_pin_records_and_exact_pin_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only an exact PinnedPopplerRuntime runs. Other results record and return."""

    monkeypatch.setattr(pin_gate, "resolve_pinned_poppler_runtime", lambda: object())
    monkeypatch.setattr(pin_gate, "_PIN_PROBE", None)
    monkeypatch.delenv(pin_gate.CANONICAL_POPPLER_PIN_REQUIRE_ENV, raising=False)
    recorded: list[tuple[str, object]] = []

    @pin_gate.requires_canonical_poppler_pin
    def sample() -> str:
        return "ran"

    assert sample(record_property=lambda name, value: recorded.append((name, value))) is None
    assert capsys.readouterr().out.strip() == pin_gate.CANONICAL_POPPLER_PIN_ABSENT_LINE
    assert recorded == [
        (pin_gate.CANONICAL_POPPLER_PIN_PROPERTY, pin_gate.CANONICAL_POPPLER_PIN_ABSENT_LINE)
    ]
    monkeypatch.setattr(pin_gate, "resolve_pinned_poppler_runtime", _windows_pin)
    monkeypatch.setattr(pin_gate, "_PIN_PROBE", None)
    exact = _windows_pin()
    assert type(exact) is PinnedPopplerRuntime
    assert exact.pdftoppm_path.is_absolute()
    assert sample() == "ran"
    monkeypatch.setattr(
        pin_gate,
        "resolve_pinned_poppler_runtime",
        lambda: _windows_pin(cls=_PinSubclass),
    )
    monkeypatch.setattr(pin_gate, "_PIN_PROBE", None)
    monkeypatch.delenv(pin_gate.CANONICAL_POPPLER_PIN_REQUIRE_ENV, raising=False)
    recorded.clear()
    assert type(_windows_pin(cls=_PinSubclass)) is not PinnedPopplerRuntime
    assert sample(record_property=lambda name, value: recorded.append((name, value))) is None
    assert capsys.readouterr().out.strip() == pin_gate.CANONICAL_POPPLER_PIN_ABSENT_LINE
    monkeypatch.setenv(pin_gate.CANONICAL_POPPLER_PIN_REQUIRE_ENV, "1")
    monkeypatch.setattr(pin_gate, "resolve_pinned_poppler_runtime", lambda: object())
    monkeypatch.setattr(pin_gate, "_PIN_PROBE", None)
    with pytest.raises(pytest.fail.Exception, match="CANONICAL_POPPLER_PIN NOT_EXECUTED"):
        sample()
    monkeypatch.setattr(
        pin_gate,
        "resolve_pinned_poppler_runtime",
        lambda: (_ for _ in ()).throw(PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED")),
    )
    monkeypatch.setattr(pin_gate, "_PIN_PROBE", None)
    with pytest.raises(PopplerRuntimeBindingError) as caught:
        sample()
    assert caught.value.code == "POPPLER_RUNTIME_DRIFTED"


def test_census_fails_when_a_synthetic_tree_omits_one_decorator(tmp_path: Path) -> None:
    """A tree missing one governed decorator fails the same helper the real census uses."""

    victim_module, victim_name = LINUX_POPPLER_GATED_NODES[0]
    for module, name in LINUX_POPPLER_GATED_NODES:
        path = tmp_path / module
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(
            existing + f"@{_LINUX_DECORATOR}\ndef {name}():\n    return None\n",
            encoding="utf-8",
        )
    for module, name in CANONICAL_POPPLER_PIN_GATED_NODES:
        path = tmp_path / module
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(
            existing + f"@{_PIN_DECORATOR}\ndef {name}():\n    return None\n",
            encoding="utf-8",
        )
    victim = tmp_path / victim_module
    victim.write_text(
        victim.read_text(encoding="utf-8").replace(f"@{_LINUX_DECORATOR}\n", "", 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match=victim_name):
        _assert_decorators_match(tmp_path)
