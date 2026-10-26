"""Task 4 durable HTML/TEXT document-type truthfulness proofs."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.slice100_task2b._support import (
    disposable_postgres,
    downgrade,
    migrate,
    migration_config,
)

MIGRATIONS_DIR = Path("src/idis/persistence/migrations/versions")
_PRE_TASK4_TYPES = frozenset({"PDF", "PPTX", "XLSX", "DOCX", "AUDIO", "VIDEO", "IMAGE"})
_TASK4_TYPES = _PRE_TASK4_TYPES | {"HTML", "TEXT"}


def _migration_path() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("0035_*.py"))
    assert len(matches) == 1, "Task 4 must add exactly one linear migration 0035"
    return matches[0]


def _document_type_constraint_values(connection: Connection) -> frozenset[str]:
    definition = connection.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'documents'::regclass
              AND conname = 'valid_document_doc_type'
              AND contype = 'c'
            """
        )
    ).scalar_one()
    return frozenset(re.findall(r"'([A-Z]+)'", str(definition)))


def _stage_exact_0034_constraint(database_engine: Engine) -> None:
    """Stage the exact 0034 relation touched by 0035, independent of pgvector."""
    config = migration_config()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE documents (
                    document_id UUID PRIMARY KEY,
                    doc_type TEXT NOT NULL,
                    CONSTRAINT valid_document_doc_type CHECK (
                        doc_type IN (
                            'PDF', 'PPTX', 'XLSX', 'DOCX',
                            'AUDIO', 'VIDEO', 'IMAGE'
                        )
                    )
                )
                """
            )
        )
        config.attributes["connection"] = connection
        command.stamp(config, "0034")


def _seed_native_text_documents(connection: Connection) -> tuple[str, str]:
    html_document_id = str(uuid.uuid4())
    text_document_id = str(uuid.uuid4())
    for document_id, doc_type in (
        (html_document_id, "HTML"),
        (text_document_id, "TEXT"),
    ):
        connection.execute(
            text(
                """
                INSERT INTO documents (document_id, doc_type)
                VALUES (:document_id, :doc_type)
                """
            ),
            {
                "document_id": document_id,
                "doc_type": doc_type,
            },
        )
    return html_document_id, text_document_id


def test_0035_source_is_linear_exact_and_never_relabels_existing_rows() -> None:
    source = _migration_path().read_text(encoding="utf-8")

    assert 'revision = "0035"' in source
    assert 'down_revision = "0034"' in source
    assert "valid_document_doc_type" in source
    assert "'HTML'" in source
    assert "'TEXT'" in source
    assert "UPDATE documents" not in source
    assert "TASK4_NATIVE_DOCUMENT_TYPES_BLOCK_DOWNGRADE" in source


def test_0035_upgrade_downgrade_upgrade_and_fail_closed_existing_rows() -> None:
    _migration_path()
    with disposable_postgres() as database:
        _stage_exact_0034_constraint(database.admin_engine)
        with database.admin_engine.connect() as connection:
            assert _document_type_constraint_values(connection) == _PRE_TASK4_TYPES

        migrate(database.admin_engine, "0035")
        with database.admin_engine.begin() as connection:
            assert _document_type_constraint_values(connection) == _TASK4_TYPES
            document_ids = _seed_native_text_documents(connection)
            persisted = connection.execute(
                text(
                    """
                    SELECT doc_type
                    FROM documents
                    WHERE document_id IN (:html_id, :text_id)
                    ORDER BY doc_type
                    """
                ),
                {"html_id": document_ids[0], "text_id": document_ids[1]},
            ).scalars()
            assert tuple(persisted) == ("HTML", "TEXT")

        with pytest.raises(DBAPIError, match="TASK4_NATIVE_DOCUMENT_TYPES_BLOCK_DOWNGRADE"):
            downgrade(database.admin_engine, "0034")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0035"
            assert _document_type_constraint_values(connection) == _TASK4_TYPES

        with database.admin_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM documents WHERE document_id IN (:html_id, :text_id)"),
                {"html_id": document_ids[0], "text_id": document_ids[1]},
            )
        downgrade(database.admin_engine, "0034")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0034"
            assert _document_type_constraint_values(connection) == _PRE_TASK4_TYPES

        migrate(database.admin_engine, "0035")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0035"
            assert _document_type_constraint_values(connection) == _TASK4_TYPES
