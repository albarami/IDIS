#!/usr/bin/env python3
"""Secret-safe logical Postgres backup/restore drill tooling (Slice99 Task 7).

Usage:
    python scripts/db_backup_restore.py backup --out DIR
    python scripts/db_backup_restore.py restore --from DIR

Connection material comes ONLY from the environment (``IDIS_DATABASE_ADMIN_URL`` via the
repo's existing ``idis.persistence.db`` engine seam) - never from command-line flags, and
never echoed to stdout/stderr, logs, or the manifest (the manifest records the database NAME
and safe aggregates only).

Design (reuses existing repo tooling, no external pg_dump dependency):
- backup: per-table CSV dumps via COPY (deterministic ordering) + a safe manifest
  {schema_revision, tables: {name: {rows, sha256}}}.
- restore: verify every dump's sha256 against the manifest (fail-closed), replay the schema
  with alembic to the manifest's revision (migrations are the schema source of truth, so RLS
  ENABLE+FORCE and the audit immutability trigger are restored exactly), truncate, then COPY
  the data back under ``session_replication_role=replica`` and verify row counts.

RPO/RTO context and drill procedure: docs/runbooks/RB-11_backup_restore.md. The 15-minute RPO
target additionally requires deployment-level WAL archiving/PITR; this tool covers the
logical backup/restore drill path.

Exit codes: 0 ok, 2 fail-closed (missing env, hash/count mismatch), 1 unexpected error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"


def _bootstrap_import_path() -> None:
    try:
        import idis  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _public_tables(conn: Any) -> list[str]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name <> 'alembic_version'
            ORDER BY table_name
            """
        )
    ).fetchall()
    return [row[0] for row in rows]


def _schema_revision(conn: Any) -> str:
    from sqlalchemy import text

    row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    return str(row[0]) if row else ""


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def cmd_backup(out_dir: Path) -> int:
    from idis.persistence.db import get_admin_engine

    engine = get_admin_engine()
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    with engine.begin() as conn:
        revision = _schema_revision(conn)
        tables = _public_tables(conn)

    manifest_tables: dict[str, Any] = {}
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        for table in tables:
            target = tables_dir / f"{table}.csv"
            with open(target, "w", encoding="utf-8", newline="") as handle:
                cursor.copy_expert(
                    f"COPY (SELECT * FROM {_quote_ident(table)} ORDER BY 1) "
                    "TO STDOUT WITH (FORMAT csv, HEADER true)",
                    handle,
                )
            with open(target, encoding="utf-8") as handle:
                rows = max(sum(1 for _ in handle) - 1, 0)
            manifest_tables[table] = {"rows": rows, "sha256": _sha256_file(target)}
        raw.commit()
    finally:
        raw.close()

    manifest = {
        "tool": "copy_csv_v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "database": engine.url.database,
        "schema_revision": revision,
        "tables": dict(sorted(manifest_tables.items())),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    total_rows = sum(entry["rows"] for entry in manifest_tables.values())
    print(
        f"backup complete: schema_revision={revision} tables={len(manifest_tables)} "
        f"rows={total_rows}",
        file=sys.stderr,
    )
    return 0


def cmd_restore(from_dir: Path) -> int:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import get_admin_engine

    manifest_path = from_dir / "manifest.json"
    if not manifest_path.is_file():
        print("restore failed: manifest.json not found in backup directory", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables: dict[str, Any] = manifest["tables"]

    # Fail-closed integrity check BEFORE touching the database.
    for table, entry in sorted(tables.items()):
        dump = from_dir / "tables" / f"{table}.csv"
        if not dump.is_file():
            print(f"restore failed: dump missing for table {table}", file=sys.stderr)
            return 2
        if _sha256_file(dump) != entry["sha256"]:
            print(f"restore failed: dump sha256 mismatch for table {table}", file=sys.stderr)
            return 2

    engine = get_admin_engine()

    # Schema restore: migrations are the schema source of truth (RLS + triggers included).
    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, str(manifest["schema_revision"]))

    with engine.begin() as conn:
        quoted = ", ".join(_quote_ident(table) for table in sorted(tables))
        conn.execute(text(f"TRUNCATE {quoted} CASCADE"))

    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("SET session_replication_role = replica")
        for table in sorted(tables):
            dump = from_dir / "tables" / f"{table}.csv"
            with open(dump, encoding="utf-8") as handle:
                cursor.copy_expert(
                    f"COPY {_quote_ident(table)} FROM STDIN WITH (FORMAT csv, HEADER true)",
                    handle,
                )
        cursor.execute("SET session_replication_role = origin")
        raw.commit()
    finally:
        raw.close()

    # Verify restored row counts against the manifest (fail-closed).
    with engine.begin() as conn:
        for table, entry in sorted(tables.items()):
            count = conn.execute(
                text(f"SELECT COUNT(*) FROM {_quote_ident(table)}")  # noqa: S608
            ).scalar()
            if int(count or 0) != int(entry["rows"]):
                print(
                    f"restore failed: row count mismatch for {table}: "
                    f"expected {entry['rows']}, got {count}",
                    file=sys.stderr,
                )
                return 2

    print(
        f"restore complete: schema_revision={manifest['schema_revision']} tables={len(tables)}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="db_backup_restore")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--out", required=True, metavar="DIR")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--from", dest="from_dir", required=True, metavar="DIR")
    args = parser.parse_args(argv)

    if not os.environ.get(ADMIN_URL_ENV):
        print(
            f"failed closed: {ADMIN_URL_ENV} is not set (connection material comes from the "
            "environment only; it is never accepted as a flag or printed)",
            file=sys.stderr,
        )
        return 2

    _bootstrap_import_path()
    try:
        if args.command == "backup":
            return cmd_backup(Path(args.out))
        return cmd_restore(Path(args.from_dir))
    except Exception as exc:  # fail-closed without echoing connection details
        print(f"failed: {type(exc).__name__} (connection details withheld)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
