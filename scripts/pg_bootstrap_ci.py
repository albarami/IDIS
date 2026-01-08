#!/usr/bin/env python3
"""PostgreSQL bootstrap script for CI/test environments.

This script creates the app role and test database for IDIS integration tests.
It is idempotent and safe to run repeatedly.

Environment variables required:
    IDIS_DATABASE_ADMIN_URL: Admin connection URL (postgres superuser)
    IDIS_DATABASE_URL: App connection URL (for verification)
    IDIS_PG_APP_USER: App role username to create (default: idis_app)
    IDIS_PG_APP_PASSWORD: App role password (default: idis_app_pw)
    IDIS_PG_DB_NAME: Test database name (default: idis_test)

Usage:
    python scripts/pg_bootstrap_ci.py           # Full bootstrap
    python scripts/pg_bootstrap_ci.py --verify-only  # Verify connectivity only
"""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse


def get_env_required(name: str) -> str:
    """Get required environment variable or exit."""
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: Required environment variable {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def get_env_optional(name: str, default: str) -> str:
    """Get optional environment variable with default."""
    return os.environ.get(name, default)


def _normalize_url_for_psycopg2(url: str) -> str:
    """Convert SQLAlchemy URL to psycopg2-compatible URL.

    SQLAlchemy uses postgresql+psycopg2:// but raw psycopg2 needs postgresql://.
    """
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def wait_for_postgres(admin_url: str, max_retries: int = 10, delay: float = 2.0) -> None:
    """Wait for PostgreSQL to be ready."""
    import psycopg2

    print("Waiting for PostgreSQL to be ready...")
    url = _normalize_url_for_psycopg2(admin_url)

    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(url)
            conn.close()
            print("PostgreSQL is ready")
            return
        except psycopg2.OperationalError as e:
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: not ready, waiting...")
                time.sleep(delay)
            else:
                print(f"ERROR: PostgreSQL not ready after {max_retries} attempts", file=sys.stderr)
                print(f"  Last error: {e}", file=sys.stderr)
                sys.exit(1)


def create_app_role(admin_url: str, app_user: str, app_password: str) -> None:
    """Create the app role if it doesn't exist (idempotent)."""
    import psycopg2

    print(f"Creating app role '{app_user}' (if not exists)...")
    url = _normalize_url_for_psycopg2(admin_url)

    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        create_role_sql = """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = %s) THEN
                    EXECUTE format(
                        'CREATE ROLE %%I WITH LOGIN PASSWORD %%L '
                        'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
                        %s, %s
                    );
                    RAISE NOTICE 'Role created';
                ELSE
                    RAISE NOTICE 'Role already exists';
                END IF;
            END
            $$;
        """
        cur.execute(create_role_sql, (app_user, app_user, app_password))
        print(f"  App role '{app_user}' ready (NOSUPERUSER, NOBYPASSRLS)")
    finally:
        cur.close()
        conn.close()


def create_database(admin_url: str, db_name: str, app_user: str) -> None:
    """Create the test database if it doesn't exist (idempotent)."""
    import psycopg2

    print(f"Creating database '{db_name}' (if not exists)...")
    url = _normalize_url_for_psycopg2(admin_url)

    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{db_name}"')
            print(f"  Database '{db_name}' created")
        else:
            print(f"  Database '{db_name}' already exists")

        cur.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO "{app_user}"')
        print(f"  CONNECT granted to '{app_user}'")
    finally:
        cur.close()
        conn.close()


def grant_schema_permissions(db_url: str, app_user: str) -> None:
    """Grant schema permissions to the app role."""
    import psycopg2

    print(f"Granting schema permissions to '{app_user}'...")
    url = _normalize_url_for_psycopg2(db_url)

    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{app_user}"')
        cur.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{app_user}"'
        )
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_user}"'
        )
        cur.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_user}"')
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{app_user}"'
        )
        print("  Schema permissions granted")
    finally:
        cur.close()
        conn.close()


def verify_app_role_security(db_url: str, app_user: str) -> None:
    """Verify the app role has correct security settings."""
    import psycopg2

    print(f"Verifying app role '{app_user}' security settings...")
    url = _normalize_url_for_psycopg2(db_url)

    conn = psycopg2.connect(url)
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
            (app_user,),
        )
        row = cur.fetchone()

        if row is None:
            print(f"ERROR: Role '{app_user}' not found", file=sys.stderr)
            sys.exit(1)

        rolsuper, rolbypassrls = row

        if rolsuper:
            print(f"ERROR: Role '{app_user}' is SUPERUSER - RLS will be bypassed!", file=sys.stderr)
            sys.exit(2)

        if rolbypassrls:
            print(
                f"ERROR: Role '{app_user}' has BYPASSRLS - RLS will be bypassed!", file=sys.stderr
            )
            sys.exit(2)

        print(f"  Role '{app_user}' is secure: NOSUPERUSER, NOBYPASSRLS")
    finally:
        cur.close()
        conn.close()


def _safe_url_info(url: str) -> str:
    """Extract safe (no password) info from URL for logging."""
    try:
        parsed = urlparse(url)
        db = parsed.path.lstrip("/")
        return f"host={parsed.hostname}, port={parsed.port}, db={db}, user={parsed.username}"
    except Exception:
        return "(could not parse URL)"


def verify_connectivity(admin_url: str, app_url: str) -> None:
    """Verify connectivity for both admin and app roles with diagnostics."""
    import psycopg2

    print("=" * 60)
    print("Verifying PostgreSQL Connectivity")
    print("=" * 60)

    # Check env vars are set (no values printed)
    print("Environment check:")
    print(f"  IDIS_DATABASE_ADMIN_URL set: {bool(admin_url)}")
    print(f"  IDIS_DATABASE_URL set: {bool(app_url)}")

    # Admin connection check
    print("\nAdmin connection:")
    print(f"  Target: {_safe_url_info(admin_url)}")
    admin_normalized = _normalize_url_for_psycopg2(admin_url)

    try:
        conn = psycopg2.connect(admin_normalized)
        cur = conn.cursor()
        cur.execute("SELECT current_user")
        admin_user = cur.fetchone()[0]
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        print(f"  Connected as: {admin_user}")
        print(f"  rolsuper={row[0]}, rolbypassrls={row[1]}")
        cur.close()
        conn.close()
        print("  Status: OK")
    except psycopg2.Error as e:
        print("  Status: FAILED")
        print(f"  Error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    # App connection check
    print("\nApp connection:")
    print(f"  Target: {_safe_url_info(app_url)}")
    app_normalized = _normalize_url_for_psycopg2(app_url)

    try:
        conn = psycopg2.connect(app_normalized)
        cur = conn.cursor()
        cur.execute("SELECT current_user")
        app_user = cur.fetchone()[0]
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        print(f"  Connected as: {app_user}")
        print(f"  rolsuper={row[0]}, rolbypassrls={row[1]}")

        # Hard gate: app role must NOT be superuser or bypassrls
        if row[0]:
            print("  FATAL: App role is SUPERUSER - RLS bypassed!", file=sys.stderr)
            cur.close()
            conn.close()
            sys.exit(2)
        if row[1]:
            print("  FATAL: App role has BYPASSRLS - RLS bypassed!", file=sys.stderr)
            cur.close()
            conn.close()
            sys.exit(2)

        cur.close()
        conn.close()
        print("  Status: OK (non-superuser, no bypassrls)")
    except psycopg2.Error as e:
        print("  Status: FAILED")
        print(f"  Error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Connectivity verification PASSED")
    print("=" * 60)


def main() -> None:
    """Main bootstrap entry point."""
    verify_only = "--verify-only" in sys.argv

    admin_url = get_env_required("IDIS_DATABASE_ADMIN_URL")
    app_url = os.environ.get("IDIS_DATABASE_URL", "")

    if verify_only:
        # Verification mode: just check connectivity and security
        if not app_url:
            print("ERROR: IDIS_DATABASE_URL required for --verify-only", file=sys.stderr)
            sys.exit(1)
        verify_connectivity(admin_url, app_url)
        return

    # Full bootstrap mode
    print("=" * 60)
    print("IDIS PostgreSQL CI Bootstrap")
    print("=" * 60)

    app_user = get_env_optional("IDIS_PG_APP_USER", "idis_app")
    app_password = get_env_optional("IDIS_PG_APP_PASSWORD", "idis_app_pw")
    db_name = get_env_optional("IDIS_PG_DB_NAME", "idis_test")

    wait_for_postgres(admin_url)

    create_app_role(admin_url, app_user, app_password)

    create_database(admin_url, db_name, app_user)

    db_url = admin_url.rsplit("/", 1)[0] + f"/{db_name}"
    grant_schema_permissions(db_url, app_user)

    verify_app_role_security(db_url, app_user)

    print("=" * 60)
    print("Bootstrap complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
