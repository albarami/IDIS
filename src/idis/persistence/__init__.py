"""IDIS Persistence Module.

Provides PostgreSQL database connectivity, connection helpers, and migration support.
"""

from idis.persistence.db import (
    DatabaseConfigError,
    begin_admin_conn,
    begin_app_conn,
    get_admin_engine,
    get_app_engine,
    get_database_url,
    is_postgres_configured,
    set_tenant_local,
)

__all__ = [
    "DatabaseConfigError",
    "begin_admin_conn",
    "begin_app_conn",
    "get_admin_engine",
    "get_app_engine",
    "get_database_url",
    "is_postgres_configured",
    "set_tenant_local",
]
