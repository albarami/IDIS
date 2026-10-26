"""Check the authorized 0001..0038 Alembic chain without importing application code.

This admission check never imports IDIS, Alembic, a migration, or a database
driver. It validates source metadata, not the safety of migration SQL. Keep the
existing migration review and disposable-database controls in place.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

AUTHORIZED_HEAD = "0038"
_FIELDS = frozenset({"revision", "down_revision", "branch_labels", "depends_on"})


class MigrationCeilingError(ValueError):
    """The migration source graph does not match the authorized chain."""


def _literal_metadata(path: Path) -> dict[str, str | None]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise MigrationCeilingError("unreadable or invalid migration source") from exc

    metadata: dict[str, str | None] = {}
    value: ast.expr | None
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in _FIELDS:
            continue
        if target.id in metadata:
            raise MigrationCeilingError("repeated migration metadata")
        if not isinstance(value, ast.Constant) or (
            value.value is not None and not isinstance(value.value, str)
        ):
            raise MigrationCeilingError("migration metadata must be literal strings or None")
        metadata[target.id] = value.value

    if set(metadata) != _FIELDS:
        raise MigrationCeilingError("missing literal migration metadata")
    for child in ast.walk(module):
        if isinstance(child, ast.alias) and (child.asname or child.name.split(".")[0]) in _FIELDS:
            raise MigrationCeilingError("imported migration metadata is not authorized")
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and child.name in _FIELDS
        ):
            raise MigrationCeilingError("migration metadata cannot be a definition")
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id in {"globals", "locals", "exec", "eval"}
        ):
            raise MigrationCeilingError("dynamic migration namespace access is not authorized")
    for field in _FIELDS:
        writes = sum(
            isinstance(node, ast.Name)
            and node.id == field
            and isinstance(node.ctx, (ast.Store, ast.Del))
            for node in ast.walk(module)
        )
        if writes != 1:
            raise MigrationCeilingError("non-unique migration metadata assignment")
    return metadata


def check_migration_ceiling(versions_dir: Path) -> int:
    """Require the exact literal linear chain, including names and side metadata."""
    if not versions_dir.is_dir():
        raise MigrationCeilingError("migration directory is missing")
    paths = sorted(path for path in versions_dir.glob("*.py") if path.name != "__init__.py")
    expected = tuple(f"{number:04d}" for number in range(1, int(AUTHORIZED_HEAD) + 1))
    if len(paths) != len(expected):
        raise MigrationCeilingError("migration count is outside the authorized chain")
    seen: set[str] = set()
    for path in paths:
        metadata = _literal_metadata(path)
        revision = metadata["revision"]
        if revision not in expected or revision in seen:
            raise MigrationCeilingError("unexpected or duplicate migration revision")
        if not isinstance(revision, str) or not path.name.startswith(f"{revision}_"):
            raise MigrationCeilingError("migration filename does not match its revision")
        expected_parent = None if revision == "0001" else f"{int(revision) - 1:04d}"
        if metadata["down_revision"] != expected_parent:
            raise MigrationCeilingError("migration parent is outside the authorized linear chain")
        if metadata["branch_labels"] is not None or metadata["depends_on"] is not None:
            raise MigrationCeilingError("migration branches or dependencies are not authorized")
        seen.add(revision)
    if seen != set(expected):
        raise MigrationCeilingError("the authorized migration chain is incomplete")
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--versions-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/idis/persistence/migrations/versions",
    )
    args = parser.parse_args()
    try:
        count = check_migration_ceiling(args.versions_dir)
    except MigrationCeilingError as exc:
        print(f"MIGRATION_CEILING_REJECTED: {exc}", file=sys.stderr)
        return 1
    print(f"MIGRATION_CEILING_OK: {count} revisions; single head {AUTHORIZED_HEAD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
