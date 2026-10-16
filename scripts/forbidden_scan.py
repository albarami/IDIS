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
import hashlib
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple

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

# Banned development tokens - must not appear in src/ (some allowed in tests/)
BANNED_TOKEN_PATTERNS_SRC_ONLY: list[tuple[str, re.Pattern[str]]] = [
    ("BANNED_TODO", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("BANNED_FIXME", re.compile(r"\bFIXME\b", re.IGNORECASE)),
    ("BANNED_PLACEHOLDER", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("BANNED_MOCK", re.compile(r"\bmock\b", re.IGNORECASE)),
    ("BANNED_HARDCODED", re.compile(r"\bhardcoded\b", re.IGNORECASE)),
]

# Banned tokens that apply to tests/ as well (mock is allowed in tests per user rules)
BANNED_TOKEN_PATTERNS_TESTS: list[tuple[str, re.Pattern[str]]] = [
    ("BANNED_TODO", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("BANNED_FIXME", re.compile(r"\bFIXME\b", re.IGNORECASE)),
    ("BANNED_PLACEHOLDER", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("BANNED_HARDCODED", re.compile(r"\bhardcoded\b", re.IGNORECASE)),
]

# Unsafe SQL patterns
SQL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("UNSAFE_JSONB_CAST", re.compile(r":\w+::jsonb")),
]

# Combined patterns for scanning (used for pattern count display)
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = (
    SECRET_PATTERNS + BANNED_TOKEN_PATTERNS_SRC_ONLY + SQL_PATTERNS
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


class FrozenNegativeAcknowledgement(NamedTuple):
    """One pattern occurrence in raw hash-pinned archived test evidence."""

    file_path: str
    raw_sha256: str
    line_number: int
    pattern_name: Literal["BANNED_PLACEHOLDER"]


FROZEN_NEGATIVE_ACKNOWLEDGEMENTS: tuple[FrozenNegativeAcknowledgement, ...] = (
    FrozenNegativeAcknowledgement(
        "tests/slice100_task8/test_task8_fixture_derivation_red.py",
        "cff1d8276ef8f30bcf40c3d266f0f1e6bcff91c86b2cdbe29c3ac3f91b55a09f",
        305,
        "BANNED_PLACEHOLDER",
    ),
    FrozenNegativeAcknowledgement(
        "tests/slice100_task8/test_v24_sql_authority_postgres_census_red.py",
        "6bea316ef53b6b9b328fc1801fe273a93d540d6d541facf3399c958a0efd9c5e",
        960,
        "BANNED_PLACEHOLDER",
    ),
    FrozenNegativeAcknowledgement(
        "tests/slice100_task8/test_v24_sql_authority_postgres_census_red.py",
        "6bea316ef53b6b9b328fc1801fe273a93d540d6d541facf3399c958a0efd9c5e",
        961,
        "BANNED_PLACEHOLDER",
    ),
)


def _valid_acknowledgement(acknowledgement: FrozenNegativeAcknowledgement) -> bool:
    path = PurePosixPath(acknowledgement.file_path)
    return (
        acknowledgement.file_path.startswith("tests/")
        and acknowledgement.file_path == path.as_posix()
        and not {"..", "."} & set(path.parts)
        and "\\" not in acknowledgement.file_path
        and ":" not in acknowledgement.file_path
        and len(acknowledgement.raw_sha256) == 64
        and all(character in "0123456789abcdef" for character in acknowledgement.raw_sha256)
        and acknowledgement.line_number > 0
        and acknowledgement.pattern_name == "BANNED_PLACEHOLDER"
    )


def should_skip_path(path: Path) -> bool:
    """Check if path should be skipped."""
    for part in path.parts:
        if part in EXCLUDED_DIRS:
            return True
    if path.name in EXCLUDED_FILES:
        return True
    return path.suffix.lower() in BINARY_EXTENSIONS


def get_code_directory_type(path: Path, repo_root: Path) -> str | None:
    """Check if path is in src/ or tests/ directories.

    Returns:
        'src' if in src/, 'tests' if in tests/, None otherwise.
    """
    try:
        rel_path = path.relative_to(repo_root)
        parts = rel_path.parts
        if len(parts) > 0:
            if parts[0] == "src":
                return "src"
            if parts[0] == "tests":
                return "tests"
        return None
    except ValueError:
        return None


def scan_file(
    file_path: Path,
    repo_root: Path,
    *,
    acknowledgements: tuple[FrozenNegativeAcknowledgement, ...] = (),
) -> list[Match]:
    """Scan a single file for forbidden patterns."""
    matches: list[Match] = []
    try:
        relative_path = file_path.relative_to(repo_root).as_posix()
    except ValueError:
        relative_path = ""
    file_acknowledgements = tuple(row for row in acknowledgements if row.file_path == relative_path)

    try:
        raw = file_path.read_bytes()
    except OSError:
        if file_acknowledgements:
            matches.append(Match(str(file_path), 0, "FROZEN_ACK_FILE_UNREADABLE"))
        return matches
    content = raw.decode("utf-8", errors="ignore")
    raw_sha256 = hashlib.sha256(raw).hexdigest() if file_acknowledgements else ""
    accepted: set[FrozenNegativeAcknowledgement] = set()
    for row in file_acknowledgements:
        if not _valid_acknowledgement(row):
            matches.append(Match(str(file_path), row.line_number, "FROZEN_ACK_INVALID"))
        elif raw_sha256 != row.raw_sha256:
            matches.append(Match(str(file_path), row.line_number, "FROZEN_ACK_HASH_MISMATCH"))
        else:
            accepted.add(row)
    used: set[FrozenNegativeAcknowledgement] = set()
    acknowledged_lines = {row.line_number for row in accepted}

    # Determine which patterns to apply based on directory
    code_dir_type = get_code_directory_type(file_path, repo_root)

    # Apply banned token and SQL patterns only to src/ and tests/
    # Note: mock is allowed in tests/ per user rules
    patterns_to_check = SECRET_PATTERNS[:]
    if code_dir_type == "src":
        patterns_to_check.extend(BANNED_TOKEN_PATTERNS_SRC_ONLY)
        patterns_to_check.extend(SQL_PATTERNS)
    elif code_dir_type == "tests":
        patterns_to_check.extend(BANNED_TOKEN_PATTERNS_TESTS)
        patterns_to_check.extend(SQL_PATTERNS)

    lines = content.splitlines()
    for line_num, line in enumerate(lines, start=1):
        if line_num not in acknowledged_lines:
            if line.strip().startswith("#") and "example" in line.lower():
                continue
            if "REDACTED" in line or "[REDACTED]" in line:
                continue

        for pattern_name, pattern in patterns_to_check:
            if pattern.search(line):
                acknowledgement = FrozenNegativeAcknowledgement(
                    relative_path, raw_sha256, line_num, "BANNED_PLACEHOLDER"
                )
                if pattern_name == "BANNED_PLACEHOLDER" and acknowledgement in accepted:
                    used.add(acknowledgement)
                    continue
                matches.append(Match(str(file_path), line_num, pattern_name))

    matches.extend(
        Match(str(file_path), row.line_number, "FROZEN_ACK_UNUSED")
        for row in file_acknowledgements
        if row in accepted and row not in used
    )

    return matches


def scan_repository(
    repo_root: Path,
    verbose: bool = False,
    *,
    acknowledgements: tuple[FrozenNegativeAcknowledgement, ...] = FROZEN_NEGATIVE_ACKNOWLEDGEMENTS,
) -> list[Match]:
    """Scan entire repository for forbidden patterns."""
    all_matches: list[Match] = []
    valid_acknowledgements = tuple(
        row
        for row in acknowledgements
        if _valid_acknowledgement(row) and acknowledgements.count(row) == 1
    )
    all_matches.extend(
        Match(str(repo_root / row.file_path), row.line_number, "FROZEN_ACK_INVALID")
        for row in acknowledgements
        if row not in valid_acknowledgements
    )
    scanned_paths: set[str] = set()

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip_path(path):
            continue

        if verbose:
            print(f"Scanning: {path}", file=sys.stderr)

        scanned_paths.add(path.relative_to(repo_root).as_posix())
        file_matches = scan_file(path, repo_root, acknowledgements=valid_acknowledgements)
        all_matches.extend(file_matches)

    for row in valid_acknowledgements:
        if row.file_path not in scanned_paths:
            path = repo_root / row.file_path
            code = "FROZEN_ACK_UNUSED" if path.is_file() else "FROZEN_ACK_FILE_MISSING"
            all_matches.append(Match(str(path), row.line_number, code))

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
