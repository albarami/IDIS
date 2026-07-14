"""Slice99 Task 4 - module quarantine policy consolidation (RED-first, Q2 boundaries).

Pins the quarantine contract:

1. A single registry (``idis.quarantine``) lists every quarantined module with its symbol,
   reason, and policy doc - covering the current surface (the legacy ``PipelineExecutor``).
2. A repo-wide guard scans the canonical runtime paths (``src/`` + ``scripts/``) and fails if
   anything imports or instantiates a quarantined module - consolidating the scattered
   per-file pins (which remain in place).
3. The policy doc exists, names each quarantined module, and states explicitly that
   DOCUMENT/MALWARE quarantine is NOT implemented in Slice99 (out of scope per Q2).

PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from idis.quarantine import (
    QUARANTINED_MODULES,
    forbidden_reference_patterns,
    iter_quarantine_violations,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DOC = _REPO_ROOT / "docs" / "architecture" / "slice99_quarantine_policy.md"


# ---------------------------------------------------------------------------
# 1. registry covers the current quarantined surface
# ---------------------------------------------------------------------------


def test_registry_covers_current_quarantined_surface() -> None:
    modules = {entry["module"] for entry in QUARANTINED_MODULES}
    symbols = {entry["symbol"] for entry in QUARANTINED_MODULES}

    assert "idis.pipeline.executor" in modules
    assert "PipelineExecutor" in symbols
    for entry in QUARANTINED_MODULES:
        assert entry["reason"].strip(), f"{entry['module']} must state a quarantine reason"
        assert entry["policy_doc"] == "docs/architecture/slice99_quarantine_policy.md"


def test_forbidden_patterns_cover_import_and_instantiation_forms() -> None:
    entry = next(e for e in QUARANTINED_MODULES if e["module"] == "idis.pipeline.executor")
    patterns = forbidden_reference_patterns(entry)

    assert "from idis.pipeline.executor import" in patterns
    assert "import idis.pipeline.executor" in patterns
    assert "from idis.pipeline import executor" in patterns
    assert "PipelineExecutor(" in patterns


def test_quarantined_symbols_are_not_package_exports() -> None:
    """Consolidates the Slice75B pin at registry level: no package re-export resurrection."""
    for entry in QUARANTINED_MODULES:
        package_name = entry["module"].rsplit(".", 1)[0]
        package = importlib.import_module(package_name)
        assert entry["symbol"] not in getattr(package, "__all__", [])
        assert not hasattr(package, entry["symbol"])


# ---------------------------------------------------------------------------
# 2. repo-wide guard over canonical runtime paths
# ---------------------------------------------------------------------------


def test_canonical_runtime_paths_have_no_quarantine_violations() -> None:
    violations = iter_quarantine_violations(_REPO_ROOT)

    assert violations == [], (
        f"canonical runtime paths must not import/instantiate quarantined modules: {violations}"
    )


def test_guard_detects_a_violation_in_a_synthetic_tree(tmp_path: Path) -> None:
    offender = tmp_path / "src" / "offender.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(
        "from idis.pipeline.executor import PipelineExecutor\n",
        encoding="utf-8",
    )

    violations = iter_quarantine_violations(tmp_path)

    assert violations, "the guard must detect a quarantined import"
    assert violations[0]["module"] == "idis.pipeline.executor"
    assert "offender.py" in violations[0]["file"]


def test_guard_ignores_the_quarantined_module_itself(tmp_path: Path) -> None:
    quarantined_self = tmp_path / "src" / "idis" / "pipeline" / "executor.py"
    quarantined_self.parent.mkdir(parents=True)
    quarantined_self.write_text("class PipelineExecutor:\n    pass\n", encoding="utf-8")

    assert iter_quarantine_violations(tmp_path) == []


# ---------------------------------------------------------------------------
# 3. policy doc: registry mirror + explicit Slice99 non-goal
# ---------------------------------------------------------------------------


def test_policy_doc_names_every_quarantined_module() -> None:
    assert _POLICY_DOC.is_file(), "quarantine policy doc must exist"
    text = _POLICY_DOC.read_text(encoding="utf-8")
    for entry in QUARANTINED_MODULES:
        assert entry["module"] in text
        assert entry["symbol"] in text


def test_policy_doc_declares_document_malware_quarantine_out_of_scope() -> None:
    text = _POLICY_DOC.read_text(encoding="utf-8").lower()
    assert "document/malware quarantine" in text
    assert "not implemented in slice99" in text
