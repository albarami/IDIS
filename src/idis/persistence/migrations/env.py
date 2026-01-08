"""Alembic environment configuration for IDIS migrations.

Provides programmatic migration execution without requiring alembic CLI.
Uses IDIS_DATABASE_ADMIN_URL for migration connections.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alembic import context
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from idis.persistence.db import get_admin_engine, get_database_url

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine

logger = logging.getLogger(__name__)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() emit SQL to stdout.
    """
    url = get_database_url(admin=True)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    engine = get_admin_engine()

    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


def run_migrations_with_connection(connection: Connection) -> None:
    """Run migrations using an existing connection.

    Args:
        connection: SQLAlchemy connection to use for migrations.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def get_alembic_config() -> Config:
    """Create Alembic config pointing to this migrations package.

    Returns:
        Alembic Config object.
    """
    import os

    config = Config()
    migrations_dir = os.path.dirname(__file__)
    config.set_main_option("script_location", migrations_dir)
    return config


def get_current_revision(engine: Engine) -> str | None:
    """Get the current migration revision from the database.

    Args:
        engine: SQLAlchemy engine to check.

    Returns:
        Current revision string or None if no migrations applied.
    """
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def get_head_revision() -> str | None:
    """Get the head revision from the migration scripts.

    Returns:
        Head revision string or None if no migrations exist.
    """
    config = get_alembic_config()
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def run_upgrade(engine: Engine | None = None, revision: str = "head") -> None:
    """Run migrations up to the specified revision.

    Args:
        engine: SQLAlchemy engine to use. If None, uses admin engine.
        revision: Target revision (default: "head").
    """
    from alembic import command

    if engine is None:
        engine = get_admin_engine()

    config = get_alembic_config()

    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)

    logger.info("Migrations upgraded to %s", revision)


def run_downgrade(engine: Engine | None = None, revision: str = "base") -> None:
    """Run migrations down to the specified revision.

    Args:
        engine: SQLAlchemy engine to use. If None, uses admin engine.
        revision: Target revision (default: "base").
    """
    from alembic import command

    if engine is None:
        engine = get_admin_engine()

    config = get_alembic_config()

    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, revision)

    logger.info("Migrations downgraded to %s", revision)


def create_app_role_if_not_exists(admin_conn: Connection, role_name: str) -> None:
    """Create application role if it doesn't exist.

    Args:
        admin_conn: Admin connection to use.
        role_name: Name of the role to create.
    """
    from sqlalchemy import text

    result = admin_conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role_name"),
        {"role_name": role_name},
    )
    if result.fetchone() is None:
        admin_conn.execute(text(f'CREATE ROLE "{role_name}" WITH LOGIN'))
        logger.info("Created application role: %s", role_name)


if context.is_offline_mode():
    run_migrations_offline()
elif context.is_offline_mode() is False and hasattr(context, "get_bind"):
    run_migrations_online()
