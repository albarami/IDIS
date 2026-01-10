#!/usr/bin/env python3
"""Run all Phase gate checks without requiring GNU make.

This script provides a deterministic, cross-platform way to run
all required quality gates for IDIS development.

Usage:
    python scripts/run_gates.py         # Run all gates
    python scripts/run_gates.py format  # Run only format
    python scripts/run_gates.py lint    # Run only lint
    python scripts/run_gates.py typecheck  # Run only typecheck
    python scripts/run_gates.py test    # Run only test
    python scripts/run_gates.py forbidden  # Run only forbidden scan
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def run_command(name: str, cmd: list[str], cwd: Path | None = None) -> bool:
    """Run a command and return True if successful."""
    print(f"\n{'=' * 60}")
    print(f"Running: {name}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)

    result = subprocess.run(cmd, cwd=cwd or REPO_ROOT)

    if result.returncode != 0:
        print(f"\n❌ FAILED: {name} (exit code {result.returncode})")
        return False

    print(f"✅ PASSED: {name}")
    return True


def gate_format() -> bool:
    """Run ruff format."""
    return run_command("Format (ruff)", ["ruff", "format", "."])


def gate_lint() -> bool:
    """Run ruff check."""
    return run_command("Lint (ruff)", ["ruff", "check", "."])


def gate_typecheck() -> bool:
    """Run mypy type checking."""
    return run_command(
        "Typecheck (mypy)",
        [sys.executable, "-m", "mypy", "src/idis", "--ignore-missing-imports"],
    )


def gate_test() -> bool:
    """Run pytest."""
    return run_command("Test (pytest)", [sys.executable, "-m", "pytest", "-q"])


def gate_forbidden() -> bool:
    """Run forbidden pattern scan."""
    return run_command(
        "Forbidden Scan",
        [sys.executable, "scripts/forbidden_scan.py"],
    )


GATES = {
    "format": gate_format,
    "lint": gate_lint,
    "typecheck": gate_typecheck,
    "test": gate_test,
    "forbidden": gate_forbidden,
}


def run_all_gates() -> bool:
    """Run all gates in sequence, stop on first failure."""
    print("\n" + "=" * 60)
    print("IDIS Phase Gate Runner")
    print("=" * 60)

    for name, gate_fn in GATES.items():
        if not gate_fn():
            print(f"\n❌ Gate '{name}' failed. Stopping.")
            return False

    print("\n" + "=" * 60)
    print("✅ ALL GATES PASSED")
    print("=" * 60)
    return True


def main() -> int:
    """Main entry point."""
    if len(sys.argv) > 1:
        gate_name = sys.argv[1].lower()
        if gate_name in GATES:
            return 0 if GATES[gate_name]() else 1
        elif gate_name in ("all", "check"):
            return 0 if run_all_gates() else 1
        else:
            print(f"Unknown gate: {gate_name}")
            print(f"Available gates: {', '.join(GATES.keys())}, all")
            return 1

    return 0 if run_all_gates() else 1


if __name__ == "__main__":
    sys.exit(main())
