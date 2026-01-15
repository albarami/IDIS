#!/usr/bin/env python3
"""Forbidden pattern scanner for IDIS repository.

Scans the repository for common secret patterns, private keys,
accidental credentials, and banned development tokens. Exits non-zero if any matches are found.

Usage:
    python scripts/forbidden_scan.py [--verbose]

Patterns detected:
- Private keys (BEGIN PRIVATE KEY, BEGIN RSA PRIVATE KEY, etc.)
- API keys (OpenAI sk-, AWS keys, Slack tokens)
- Password assignments (password=, passwd=, PGPASSWORD=)
- Banned development tokens (TODO, FIXME, placeholder, mock, hardcoded)
- Unsafe SQL patterns (::jsonb casts)
- Other sensitive patterns

Security:
- Matched content is REDACTED in output (only file:line shown)
- Never echoes raw secrets to stdout/stderr
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Secret patterns - high severity
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "PRIVATE_KEY_HEADER",
        re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY", re.IGNORECASE),
    ),
    ("PRIVATE_KEY_GENERIC", re.compile(r"BEGIN\s+PRIVATE\s+KEY", re.IGNORECASE)),
    ("OPENAI_API_KEY", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    (
        "AWS_SECRET_KEY",
        re.compile(r"AWS_SECRET_ACCESS_KEY\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{30,}", re.IGNORECASE),
    ),
    ("AWS_ACCESS_KEY_ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("SLACK_BOT_TOKEN", re.compile(r"xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}")),
    ("SLACK_USER_TOKEN", re.compile(r"xoxp-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}")),
    (
        "HARDCODED_PASSWORD",
        re.compile(r"(?:password|passwd)\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    ),
    (
        "PGPASSWORD_ENV",
        re.compile(r"PGPASSWORD\s*=\s*['\"]?[^'\"\s]{8,}", re.IGNORECASE),
    ),
    ("GITHUB_TOKEN", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("GITHUB_OAUTH", re.compile(r"gho_[a-zA-Z0-9]{36}")),
    (
        "GENERIC_API_KEY",
        re.compile(r"api[_-]?key\s*=\s*['\"][a-zA-Z0-9]{20,}['\"]", re.IGNORECASE),
    ),
    ("BEARER_TOKEN", re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{40,}")),
]

# Banned development tokens - must not appear in src/ or tests/
BANNED_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("BANNED_TODO", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("BANNED_FIXME", re.compile(r"\bFIXME\b", re.IGNORECASE)),
    ("BANNED_PLACEHOLDER", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("BANNED_MOCK", re.compile(r"\bmock\b", re.IGNORECASE)),
    ("BANNED_HARDCODED", re.compile(r"\bhardcoded\b", re.IGNORECASE)),
]

# Unsafe SQL patterns
SQL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("UNSAFE_JSONB_CAST", re.compile(r":\w+::jsonb")),
]

# Combined patterns for scanning
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = (
    SECRET_PATTERNS + BANNED_TOKEN_PATTERNS + SQL_PATTERNS
)

ALLOWLIST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"get_env(?:_optional)?\s*\("),
    re.compile(r"os\.environ\.get\s*\("),
    re.compile(r"def\s+\w+.*password"),
    re.compile(r":\s*str\s*[,)]"),
    re.compile(r"#.*example", re.IGNORECASE),
]

EXCLUDED_DIRS: set[str] = {
    ".git",
    ".tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    ".eggs",
    "artifacts",
}

EXCLUDED_FILES: set[str] = {
    "forbidden_scan.py",
    "poetry.lock",
    "package-lock.json",
}

BINARY_EXTENSIONS: set[str] = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".whl",
}


class Match(NamedTuple):
    """A forbidden pattern match."""

    file_path: str
    line_number: int
    pattern_name: str


def should_skip_path(path: Path) -> bool:
    """Check if path should be skipped."""
    for part in path.parts:
        if part in EXCLUDED_DIRS:
            return True
    if path.name in EXCLUDED_FILES:
        return True
    return path.suffix.lower() in BINARY_EXTENSIONS


def is_in_code_directory(path: Path, repo_root: Path) -> bool:
    """Check if path is in src/ or tests/ directories."""
    try:
        rel_path = path.relative_to(repo_root)
        parts = rel_path.parts
        return len(parts) > 0 and parts[0] in ("src", "tests")
    except ValueError:
        return False


def scan_file(file_path: Path, repo_root: Path) -> list[Match]:
    """Scan a single file for forbidden patterns."""
    matches: list[Match] = []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return matches

    # Determine which patterns to apply
    in_code_dir = is_in_code_directory(file_path, repo_root)

    # Apply banned token and SQL patterns only to src/ and tests/
    patterns_to_check = SECRET_PATTERNS[:]
    if in_code_dir:
        patterns_to_check.extend(BANNED_TOKEN_PATTERNS)
        patterns_to_check.extend(SQL_PATTERNS)

    lines = content.splitlines()
    for line_num, line in enumerate(lines, start=1):
        if line.strip().startswith("#") and "example" in line.lower():
            continue
        if "REDACTED" in line or "[REDACTED]" in line:
            continue

        for pattern_name, pattern in patterns_to_check:
            if pattern.search(line):
                matches.append(Match(str(file_path), line_num, pattern_name))
                break

    return matches


def scan_repository(repo_root: Path, verbose: bool = False) -> list[Match]:
    """Scan entire repository for forbidden patterns."""
    all_matches: list[Match] = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip_path(path):
            continue

        if verbose:
            print(f"Scanning: {path}", file=sys.stderr)

        file_matches = scan_file(path, repo_root)
        all_matches.extend(file_matches)

    return all_matches


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Scan repository for forbidden patterns (secrets, keys, passwords)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print files being scanned")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: auto-detect)",
    )
    args = parser.parse_args()

    if args.repo_root:
        repo_root = args.repo_root
    else:
        script_path = Path(__file__).resolve()
        repo_root = script_path.parent.parent

    if not repo_root.exists():
        print(f"ERROR: Repository root not found: {repo_root}", file=sys.stderr)
        return 1

    print(f"Scanning repository: {repo_root}")
    print(f"Patterns checked: {len(FORBIDDEN_PATTERNS)}")
    print()

    matches = scan_repository(repo_root, verbose=args.verbose)

    if matches:
        print("=" * 60)
        print("FORBIDDEN PATTERNS DETECTED")
        print("=" * 60)
        print()
        for match in matches:
            print(f"  {match.file_path}:{match.line_number} [{match.pattern_name}] [REDACTED]")
        print()
        print(f"Total matches: {len(matches)}")
        print("FAILED: Repository contains forbidden patterns")
        return 1

    print("OK: No forbidden patterns detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
