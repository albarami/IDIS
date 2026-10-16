"""Operator-backed direct-SQL proof for the v7 binary64 authorities."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import disposable_postgres

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v7_reviewed_preimage_v1.json"


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _authority_sql(authority_id: str) -> tuple[str, ...]:
    section = _artifact().get("sql_authority_registry")
    assert isinstance(section, dict), "v4 omits sql_authority_registry"
    matches = [row for row in section["rows"] if row.get("authority_id") == authority_id]
    assert len(matches) == 1, f"expected one {authority_id} authority"
    authority = matches[0]
    encoded = authority["body_sql_utf8_hex_tuple"]
    assert isinstance(encoded, list) and encoded
    bodies = tuple(bytes.fromhex(statement).decode("utf-8") for statement in encoded)
    if authority["authority_kind"] != "CHECK":
        return bodies
    targets = authority["target_tuple"]
    assert isinstance(targets, list) and len(targets) == 1
    table, constraint = targets[0]
    assert all(
        isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]*", value)
        for value in (table, constraint)
    )
    assert len(bodies) == 1
    return (f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} CHECK ({bodies[0]})",)


def _binary64_authorities() -> tuple[str, ...]:
    return tuple(
        statement
        for authority_id in (
            "scoring-binary64-finite-v1",
            "scoring-binary64-negative-zero-v1",
        )
        for statement in _authority_sql(authority_id)
    )


def _install_binary64_authorities(connection: Any, statements: tuple[str, ...]) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE scoring_output_dimensions (
                scoring_dimension_id UUID PRIMARY KEY,
                score DOUBLE PRECISION NOT NULL,
                confidence DOUBLE PRECISION NOT NULL
            )
            """
        )
    )
    for statement in statements:
        connection.exec_driver_sql(statement)


def _insert_literal(engine: Any, row_id: str, score_sql: str, confidence_sql: str) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO scoring_output_dimensions "
            "(scoring_dimension_id,score,confidence) VALUES "
            f"('{row_id}'::uuid,{score_sql},{confidence_sql})"
        )


def test_direct_sql_accepts_binary64_boundaries() -> None:
    statements = _binary64_authorities()
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            _install_binary64_authorities(connection, statements)

        _insert_literal(
            database.admin_engine,
            "00000000-0000-0000-0000-000000000010",
            "0::double precision",
            "1::double precision",
        )
        _insert_literal(
            database.admin_engine,
            "00000000-0000-0000-0000-000000000011",
            "1::double precision",
            "0::double precision",
        )


@pytest.mark.parametrize(
    "invalid_sql",
    (
        "'NaN'::double precision",
        "'Infinity'::double precision",
        "'-Infinity'::double precision",
    ),
)
def test_direct_sql_rejects_every_nonfinite_binary64_value(invalid_sql: str) -> None:
    statements = _binary64_authorities()
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            _install_binary64_authorities(connection, statements)

        _insert_literal(
            database.admin_engine,
            "00000000-0000-0000-0000-000000000001",
            "0.5::double precision",
            "1::double precision",
        )
        with pytest.raises(DBAPIError):
            _insert_literal(
                database.admin_engine,
                "00000000-0000-0000-0000-000000000002",
                invalid_sql,
                "0.5::double precision",
            )
        with pytest.raises(DBAPIError):
            _insert_literal(
                database.admin_engine,
                "00000000-0000-0000-0000-000000000003",
                "0.5::double precision",
                invalid_sql,
            )


@pytest.mark.parametrize(
    ("score_sql", "confidence_sql"),
    (
        ("-0.000001::double precision", "0.5::double precision"),
        ("1.000001::double precision", "0.5::double precision"),
        ("0.5::double precision", "-0.000001::double precision"),
        ("0.5::double precision", "1.000001::double precision"),
    ),
)
def test_direct_sql_rejects_finite_out_of_range_values(
    score_sql: str,
    confidence_sql: str,
) -> None:
    statements = _binary64_authorities()
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            _install_binary64_authorities(connection, statements)

        with pytest.raises(DBAPIError):
            _insert_literal(
                database.admin_engine,
                "00000000-0000-0000-0000-000000000012",
                score_sql,
                confidence_sql,
            )


def test_direct_sql_normalizes_negative_zero_to_positive_zero_bits() -> None:
    statements = _binary64_authorities()
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            _install_binary64_authorities(connection, statements)

        _insert_literal(
            database.admin_engine,
            "00000000-0000-0000-0000-000000000004",
            "'-0'::double precision",
            "'-0'::double precision",
        )
        with database.admin_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT encode(float8send(score),'hex'),"
                    "encode(float8send(confidence),'hex') "
                    "FROM scoring_output_dimensions"
                )
            ).one()
        assert tuple(row) == ("0000000000000000", "0000000000000000")


def test_direct_sql_update_normalizes_negative_zero_to_positive_zero_bits() -> None:
    """Exercise the UPDATE trigger seam even though production rows are append-only."""

    statements = _binary64_authorities()
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            _install_binary64_authorities(connection, statements)

        _insert_literal(
            database.admin_engine,
            "00000000-0000-0000-0000-000000000005",
            "0.5::double precision",
            "0.5::double precision",
        )
        with database.admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE scoring_output_dimensions "
                "SET score='-0'::double precision,confidence='-0'::double precision "
                "WHERE scoring_dimension_id="
                "'00000000-0000-0000-0000-000000000005'::uuid"
            )
        with database.admin_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT encode(float8send(score),'hex'),"
                    "encode(float8send(confidence),'hex') "
                    "FROM scoring_output_dimensions"
                )
            ).one()
        assert tuple(row) == ("0000000000000000", "0000000000000000")
