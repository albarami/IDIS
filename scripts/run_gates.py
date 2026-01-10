#!/usr/bin/env python3
"""Run all Phase gate checks without requiring GNU make.

This script provides a deterministic, cross-platform way to run
all required quality gates for IDIS development.

FAIL-CLOSED: Uses subprocess.run(check=True) to ensure any gate
failure immediately stops execution and returns non-zero exit code.

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


def run_command(name: str, cmd: list[str], cwd: Path | None = None) -> None:
    """Run a command with fail-closed behavior.

    Uses subprocess.run(check=True) to ensure failures are not swallowed.
    Raises subprocess.CalledProcessError on non-zero exit code.
    """
    print(f"\n{'=' * 60}")
    print(f"Running: {name}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)

    # FAIL-CLOSED: check=True raises CalledProcessError on failure
    subprocess.run(cmd, cwd=cwd or REPO_ROOT, check=True)

    print(f"✅ PASSED: {name}")


def gate_format() -> None:
    """Run ruff format."""
    run_command("Format (ruff)", ["ruff", "format", "."])


def gate_lint() -> None:
    """Run ruff check."""
    run_command("Lint (ruff)", ["ruff", "check", "."])


def gate_typecheck() -> None:
    """Run mypy type checking."""
    run_command(
        "Typecheck (mypy)",
        [sys.executable, "-m", "mypy", "src/idis", "--ignore-missing-imports"],
    )


def gate_test() -> None:
    """Run pytest."""
    run_command("Test (pytest)", [sys.executable, "-m", "pytest", "-q"])


def gate_forbidden() -> None:
    """Run forbidden pattern scan."""
    run_command(
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


def run_all_gates() -> None:
    """Run all gates in sequence. Fails closed on first error via CalledProcessError."""
    print("\n" + "=" * 60)
    print("IDIS Phase Gate Runner (FAIL-CLOSED)")
    print("=" * 60)

    for gate_fn in GATES.values():
        gate_fn()

    print("\n" + "=" * 60)
    print("✅ ALL GATES PASSED")
    print("=" * 60)


def main() -> int:
    """Main entry point with fail-closed exception handling."""
    try:
        if len(sys.argv) > 1:
            gate_name = sys.argv[1].lower()
            if gate_name in GATES:
                GATES[gate_name]()
                return 0
            elif gate_name in ("all", "check"):
                run_all_gates()
                return 0
            else:
                print(f"Unknown gate: {gate_name}")
                print(f"Available gates: {', '.join(GATES.keys())}, all")
                return 1

        run_all_gates()
        return 0

    except subprocess.CalledProcessError as e:
        print(f"\n❌ GATE FAILED (exit code {e.returncode})")
        print("Stopping immediately - fail closed.")
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
