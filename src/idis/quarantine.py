"""Module quarantine registry + repo-wide guard (Slice99 Task 4, Q2).

One registry of quarantined modules: legacy code that must not be imported, instantiated, or
re-exported from any canonical runtime path (``src/`` and ``scripts/``). The repo-wide guard
(`iter_quarantine_violations`) consolidates the scattered per-file pins into a single check;
the older targeted pins remain in place as defense in depth.

Scope (Q2): MODULE quarantine only. Document/malware quarantine of uploaded files is a product
feature and is explicitly NOT implemented in Slice99 - see the policy doc.

Prose mentions (docstrings, evidence strings, audit narratives) that merely DESCRIBE the
quarantine are allowed; the guard matches import and instantiation forms only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

QUARANTINE_POLICY_DOC = "docs/architecture/slice99_quarantine_policy.md"

QUARANTINED_MODULES: tuple[dict[str, str], ...] = (
    {
        "module": "idis.pipeline.executor",
        "symbol": "PipelineExecutor",
        "reason": (
            "Legacy demo executor superseded by the canonical RunExecutionService / "
            "PipelineWorker path; it bypasses the run-step ledger, strict gates, and "
            "tenant-scoped persistence."
        ),
        "quarantined_since": "Slice 70 (read-only plan) / Slice 75B (lifecycle parity)",
        "policy_doc": QUARANTINE_POLICY_DOC,
    },
)

_SCAN_DIRS = ("src", "scripts")


def forbidden_reference_patterns(entry: dict[str, str]) -> tuple[str, ...]:
    """Import/instantiation forms that count as a quarantine violation for one entry."""
    module = entry["module"]
    package, _, leaf = module.rpartition(".")
    symbol = entry["symbol"]
    return (
        f"from {module} import",
        f"import {module}",
        f"from {package} import {leaf}",
        f"{symbol}(",
    )


def _module_relative_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


def iter_quarantine_violations(repo_root: str | Path) -> list[dict[str, Any]]:
    """Scan canonical runtime paths for quarantined imports/instantiations.

    Skips each quarantined module's own file (the definition may exist; using it may not)
    and this registry module. Returns deterministic, repo-relative findings.
    """
    root = Path(repo_root)
    self_relative = "src/" + _module_relative_path("idis.quarantine")
    violations: list[dict[str, Any]] = []

    for entry in QUARANTINED_MODULES:
        patterns = forbidden_reference_patterns(entry)
        own_file = "src/" + _module_relative_path(entry["module"])
        for scan_dir in _SCAN_DIRS:
            base = root / scan_dir
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.py")):
                relative = path.relative_to(root).as_posix()
                if relative in (own_file, self_relative):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    for pattern in patterns:
                        if pattern in line:
                            violations.append(
                                {
                                    "module": entry["module"],
                                    "file": relative,
                                    "line": line_no,
                                    "pattern": pattern,
                                }
                            )

    violations.sort(key=lambda v: (str(v["file"]), int(v["line"]), str(v["pattern"])))
    return violations
