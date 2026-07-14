#!/usr/bin/env python3
"""Contract lock regen/verify CLI (Slice99 Task 5).

Usage:
    python scripts/contract_lock.py verify [--repo-root DIR]
    python scripts/contract_lock.py regen  [--repo-root DIR]

Exit codes: 0 ok, 2 verification failed, 1 unexpected error.
Thin wrapper around ``idis.contracts`` (the hermetic test suite enforces the same checks).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contract_lock")
    parser.add_argument("command", choices=("verify", "regen"))
    parser.add_argument("--repo-root", default=".", metavar="DIR")
    args = parser.parse_args(argv)

    try:
        from idis.contracts import verify_contract_lock, write_lock_document
    except ImportError:
        repo_root = Path(args.repo_root).resolve()
        sys.path.insert(0, str(repo_root / "src"))
        from idis.contracts import verify_contract_lock, write_lock_document

    root = Path(args.repo_root)

    if args.command == "regen":
        target = write_lock_document(root)
        print(f"contract lock regenerated: {target}", file=sys.stderr)
        return 0

    result = verify_contract_lock(root)
    print(
        f"contract lock verify: ok={result['ok']} findings={len(result['findings'])}",
        file=sys.stderr,
    )
    for finding in result["findings"]:
        print(f"  {finding['code']}: {finding['detail']}", file=sys.stderr)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
