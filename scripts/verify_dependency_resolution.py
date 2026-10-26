"""Verify installed distributions against the Python 3.11 constraints, without IDIS imports.

Run after dependency installation and ``pip check``. The already locked
``packaging`` library supplies the PEP 508 parser. This version-resolution check
does not replace package integrity, compatibility tests, or OS qualification.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def parse_pins(text: str, environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Read exact requirement pins; reject directives, URLs and unrecognized syntax."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        specifiers = tuple(requirement.specifier)
        if (
            requirement.url
            or requirement.extras
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            raise ValueError("a dependency is not an exact version pin")
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        name, version = canonicalize_name(requirement.name), specifiers[0].version
        if name in pins and pins[name] != version:
            raise ValueError("conflicting active dependency pins")
        pins[name] = version
    return pins


def is_local_source(direct_url: str | None) -> bool:
    """Only the local project may be exempt from third-party version constraints."""
    if direct_url is None:
        return False
    try:
        data = json.loads(direct_url)
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            return False
        url = urlsplit(data["url"])
    except (ValueError, TypeError):
        return False
    return url.scheme == "file" and not url.netloc and url.path.startswith("/")


def verify_installed(
    pins: Mapping[str, str], installed: Iterable[tuple[str, str, bool]]
) -> list[str]:
    """Report only distribution names, never source paths, URLs or environment values."""
    issues: list[str] = []
    seen: set[str] = set()
    for raw_name, version, local in installed:
        if not isinstance(raw_name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", raw_name
        ):
            raise ValueError("invalid distribution name")
        name = canonicalize_name(raw_name)
        if name in seen:
            issues.append(f"DUPLICATE_DISTRIBUTION: {name}")
        seen.add(name)
        if name == "idis" and local:
            continue
        if name not in pins:
            issues.append(f"UNPINNED: {name}")
        elif pins[name] != version:
            issues.append(f"VERSION_MISMATCH: {name}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--constraints",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "requirements/constraints-py311.txt",
    )
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11) or sys.platform not in {"linux", "win32"}:
        print("DEPENDENCY_RESOLUTION_REJECTED: unsupported Python or platform", file=sys.stderr)
        return 1
    try:
        pins = parse_pins(args.constraints.read_text(encoding="utf-8"))
        installed = [
            (
                dist.metadata["Name"],
                dist.version,
                is_local_source(dist.read_text("direct_url.json")),
            )
            for dist in importlib.metadata.distributions()
        ]
        issues = verify_installed(pins, installed)
    except (OSError, UnicodeError, ValueError, KeyError):
        print("DEPENDENCY_RESOLUTION_REJECTED: invalid inventory or constraints", file=sys.stderr)
        return 1
    if issues:
        for issue in sorted(issues):
            print(f"DEPENDENCY_RESOLUTION_REJECTED: {issue}", file=sys.stderr)
        return 1
    print(f"DEPENDENCY_RESOLUTION_OK: {len(installed)} installed distributions match active pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
