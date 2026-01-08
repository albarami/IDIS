"""PostgreSQL database connectivity and connection helpers for IDIS.

Provides engine creation, connection management, and tenant context setting via RLS.

Environment Variables:
    IDIS_DATABASE_URL: Application/runtime role connection string (non-superuser)
    IDIS_DATABASE_ADMIN_URL: Admin/superuser connection string (migrations/tests only)

Design Requirements (v6.3):
    - PostgreSQL is the canonical store (MUST)
    - Tenant isolation via RLS with SET LOCAL idis.tenant_id
    - Fail closed on missing configuration
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine

logger = logging.getLogger(__name__)

IDIS_DATABASE_URL_ENV = "IDIS_DATABASE_URL"
IDIS_DATABASE_ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"

_app_engine: Engine | None = None
_admin_engine: Engine | None = None


class DatabaseConfigError(Exception):
    """Raised when database configuration is missing or invalid.

    This is a fail-closed error - operations requiring the database
    should not proceed without valid configuration.
    """

    pass


def is_postgres_configured() -> bool:
    """Check if PostgreSQL is configured via environment.

    Returns:
        True if IDIS_DATABASE_URL is set, False otherwise.
    """
    return bool(os.environ.get(IDIS_DATABASE_URL_ENV))


def _ensure_psycopg_driver(url: str) -> str:
    """Ensure the database URL uses psycopg2 driver.

    SQLAlchemy defaults to psycopg2 for postgresql:// URLs.
    We explicitly use psycopg2 for better compatibility.

    Args:
        url: Original database URL.

    Returns:
        URL with correct driver specification.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_database_url(admin: bool = False) -> str:
    """Get the database URL from environment.

    Args:
        admin: If True, return admin URL; otherwise return app URL.

    Returns:
        Database connection string.

    Raises:
        DatabaseConfigError: If the required environment variable is not set.
    """
    env_var = IDIS_DATABASE_ADMIN_URL_ENV if admin else IDIS_DATABASE_URL_ENV
    url = os.environ.get(env_var)

    if not url:
        raise DatabaseConfigError(
            f"Database URL not configured. Set {env_var} environment variable."
        )

    return _ensure_psycopg_driver(url)


def get_app_engine() -> Engine:
    """Get or create the application database engine.

    Uses IDIS_DATABASE_URL for non-superuser application connections.

    Returns:
        SQLAlchemy Engine for application use.

    Raises:
        DatabaseConfigError: If IDIS_DATABASE_URL is not set.
    """
    global _app_engine

    if _app_engine is None:
        url = get_database_url(admin=False)
        _app_engine = create_engine(
            url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
        logger.info("Created application database engine")

    return _app_engine


def get_admin_engine() -> Engine:
    """Get or create the admin database engine.

    Uses IDIS_DATABASE_ADMIN_URL for superuser connections (migrations, tests).

    Returns:
        SQLAlchemy Engine for admin use.

    Raises:
        DatabaseConfigError: If IDIS_DATABASE_ADMIN_URL is not set.
    """
    global _admin_engine

    if _admin_engine is None:
        url = get_database_url(admin=True)
        _admin_engine = create_engine(
            url,
            pool_size=2,
            max_overflow=5,
            pool_pre_ping=True,
            echo=False,
        )
        logger.info("Created admin database engine")

    return _admin_engine


@contextmanager
def begin_app_conn() -> Generator[Connection, None, None]:
    """Context manager for application database connection with transaction.

    Opens a connection from the app engine, begins a transaction,
    yields the connection, and commits on success or rolls back on error.

    Yields:
        SQLAlchemy Connection in a transaction.

    Raises:
        DatabaseConfigError: If database is not configured.
        SQLAlchemyError: If database operation fails.
    """
    engine = get_app_engine()
    with engine.connect() as conn, conn.begin():
        yield conn


@contextmanager
def begin_admin_conn() -> Generator[Connection, None, None]:
    """Context manager for admin database connection with transaction.

    Opens a connection from the admin engine, begins a transaction,
    yields the connection, and commits on success or rolls back on error.

    Yields:
        SQLAlchemy Connection in a transaction.

    Raises:
        DatabaseConfigError: If database is not configured.
        SQLAlchemyError: If database operation fails.
    """
    engine = get_admin_engine()
    with engine.connect() as conn, conn.begin():
        yield conn


_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _validate_tenant_id(tenant_id: str) -> bool:
    """Validate that tenant_id is a valid UUID format.

    Args:
        tenant_id: The tenant ID to validate.

    Returns:
        True if valid UUID format, False otherwise.
    """
    return bool(_UUID_PATTERN.match(tenant_id))


def set_tenant_local(conn: Connection, tenant_id: str) -> None:
    """Set the tenant context for RLS on the given connection.

    Executes SET LOCAL idis.tenant_id = '<uuid>' which scopes all
    subsequent queries on this connection to the specified tenant.

    Args:
        conn: SQLAlchemy Connection to set tenant context on.
        tenant_id: UUID string of the tenant.

    Raises:
        DatabaseConfigError: If tenant_id is not a valid UUID.
        SQLAlchemyError: If the SET LOCAL statement fails.
    """
    if not _validate_tenant_id(tenant_id):
        raise DatabaseConfigError(f"Invalid tenant_id format: {tenant_id}")

    try:
        conn.execute(text(f"SET LOCAL idis.tenant_id = '{tenant_id}'"))
        logger.debug("Set tenant context to %s", tenant_id)
    except SQLAlchemyError as e:
        logger.error("Failed to set tenant context: %s", e)
        raise


def reset_engines() -> None:
    """Reset global engine instances.

    Used for testing to ensure fresh engine creation.
    """
    global _app_engine, _admin_engine
    if _app_engine is not None:
        _app_engine.dispose()
        _app_engine = None
    if _admin_engine is not None:
        _admin_engine.dispose()
        _admin_engine = None
