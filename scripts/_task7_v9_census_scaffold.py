"""Artifact-driven PostgreSQL scaffold for the Task 7 v9 pre-freeze census.

The census deliberately starts from schema revision 0037.  This module builds
only a disposable, transaction-scoped test scaffold from the reviewed artifact
bytes; it does not import or claim to execute migration 0038.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, cast

from sqlalchemy import text
from sqlalchemy.engine import Connection

_TASK7_TABLES: Final = frozenset(
    {
        "deliverable_manifest_references",
        "document_parse_attempts",
        "run_applicability_decisions",
        "run_consumer_receipts",
        "run_expected_consumer_edges",
        "run_input_bindings",
        "run_output_objects",
        "run_step_outputs",
        "source_lineage_objects",
    }
)
_CURRENT_AUTHORITY_ORDINALS: Final = tuple(range(17, 28))
_IDENTIFIER_RE: Final = re.compile(r"[a-z_][a-z0-9_]*")
_FUNCTION_DECLARATION_RE: Final = re.compile(
    r"^CREATE(?: OR REPLACE)? FUNCTION public\.([a-z_][a-z0-9_]*)\(",
    re.IGNORECASE,
)
_FOREIGN_KEY_RE: Final = re.compile(
    r"^(?P<name>[a-z0-9_]+)\((?P<child>[^)]*)\) -> "
    r"(?P<parent>[a-z0-9_]+)\.(?P<parent_key>[a-z0-9_]+)"
    r"\((?P<parent_cols>[^)]*)\) "
    r"ON UPDATE (?P<update>NO ACTION|CASCADE|RESTRICT) "
    r"ON DELETE (?P<delete>NO ACTION|CASCADE|RESTRICT) "
    r"USING (?P<index>[a-z0-9_]+)"
    r"(?P<deferred> DEFERRABLE INITIALLY DEFERRED)?$"
)


def _identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise RuntimeError(f"TASK7_V9_CENSUS_IDENTIFIER_INVALID:{field}")
    return value


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"TASK7_V9_CENSUS_MAPPING_INVALID:{field}")
    return cast(Mapping[str, Any], value)


def _sequence(value: Any, *, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RuntimeError(f"TASK7_V9_CENSUS_SEQUENCE_INVALID:{field}")
    return value


def _decode_sql(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) % 2:
        raise RuntimeError(f"TASK7_V9_CENSUS_SQL_HEX_INVALID:{field}")
    try:
        decoded = bytes.fromhex(value).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"TASK7_V9_CENSUS_SQL_HEX_INVALID:{field}") from exc
    if not decoded.strip() or "\x00" in decoded:
        raise RuntimeError(f"TASK7_V9_CENSUS_SQL_EMPTY:{field}")
    return decoded


def _ddl_rows(artifact: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    section = _mapping(artifact.get("current_ddl_registry"), field="current_ddl_registry")
    rows = tuple(
        _mapping(row, field=f"current_ddl_registry.rows[{ordinal}]")
        for ordinal, row in enumerate(
            _sequence(section.get("rows"), field="current_ddl_registry.rows")
        )
    )
    names = tuple(
        _identifier(row.get("table_name"), field=f"current_ddl_registry.rows[{i}].table")
        for i, row in enumerate(rows)
    )
    if len(names) != len(set(names)):
        raise RuntimeError("TASK7_V9_CENSUS_DDL_TABLE_DUPLICATE")
    if not _TASK7_TABLES.issubset(names):
        raise RuntimeError("TASK7_V9_CENSUS_TASK7_TABLE_CENSUS_MISMATCH")
    return rows


def _current_authority_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    section = _mapping(artifact.get("sql_authority_registry"), field="sql_authority_registry")
    rows = tuple(
        _mapping(row, field=f"sql_authority_registry.rows[{ordinal}]")
        for ordinal, row in enumerate(
            _sequence(section.get("rows"), field="sql_authority_registry.rows")
        )
        if isinstance(row, Mapping)
        and type(cast(Mapping[str, Any], row).get("authority_ordinal")) is int
        and int(cast(Mapping[str, Any], row)["authority_ordinal"]) >= 17
    )
    ordinals = tuple(int(row["authority_ordinal"]) for row in rows)
    if ordinals != _CURRENT_AUTHORITY_ORDINALS:
        raise RuntimeError("TASK7_V9_CENSUS_CURRENT_AUTHORITY_ORDINAL_MISMATCH")
    return rows


def _split_named(authority: Any, *, field: str) -> tuple[str, str, str]:
    if not isinstance(authority, str):
        raise RuntimeError(f"TASK7_V9_CENSUS_DDL_AUTHORITY_INVALID:{field}")
    opening = authority.find("(")
    closing = authority.rfind(")")
    if opening <= 0 or closing <= opening:
        raise RuntimeError(f"TASK7_V9_CENSUS_DDL_AUTHORITY_INVALID:{field}")
    name = _identifier(authority[:opening], field=f"{field}.name")
    return name, authority[opening + 1 : closing], authority[closing + 1 :].strip()


def _table_exists(connection: Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
            {"qualified_name": f"public.{table_name}"},
        ).scalar_one()
    )


def _column_exists(connection: Connection, table_name: str, column_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_catalog.pg_attribute "
                "WHERE attrelid=to_regclass(:qualified_name) "
                "AND attname=:column_name AND attnum>0 AND NOT attisdropped)"
            ),
            {
                "qualified_name": f"public.{table_name}",
                "column_name": column_name,
            },
        ).scalar_one()
    )


def _constraint_exists(connection: Connection, table_name: str, name: str) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_catalog.pg_constraint "
                "WHERE conrelid=to_regclass(:qualified_name) AND conname=:name)"
            ),
            {"qualified_name": f"public.{table_name}", "name": name},
        ).scalar_one()
    )


def _index_exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_catalog.pg_class AS c "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='i' AND c.relname=:name)"
            ),
            {"name": name},
        ).scalar_one()
    )


def _trigger_exists(connection: Connection, table_name: str, trigger_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_catalog.pg_trigger "
                "WHERE tgrelid=to_regclass(:qualified_name) "
                "AND tgname=:trigger_name AND NOT tgisinternal)"
            ),
            {
                "qualified_name": f"public.{table_name}",
                "trigger_name": trigger_name,
            },
        ).scalar_one()
    )


def _function_exists(connection: Connection, function_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_catalog.pg_proc AS p "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname=:function_name)"
            ),
            {"function_name": function_name},
        ).scalar_one()
    )


def _column_name(definition: Any, *, field: str) -> str:
    if not isinstance(definition, str) or not definition.strip():
        raise RuntimeError(f"TASK7_V9_CENSUS_COLUMN_DEFINITION_INVALID:{field}")
    return _identifier(definition.split(None, 1)[0], field=f"{field}.name")


def _materialize_columns(
    connection: Connection,
    ddl_rows: Sequence[Mapping[str, Any]],
) -> None:
    for row_ordinal, row in enumerate(ddl_rows):
        table_name = _identifier(
            row.get("table_name"),
            field=f"current_ddl_registry.rows[{row_ordinal}].table_name",
        )
        definitions = tuple(
            str(definition)
            for definition in _sequence(
                row.get("column_tuple"),
                field=f"current_ddl_registry.rows[{row_ordinal}].column_tuple",
            )
        )
        if not definitions:
            raise RuntimeError(f"TASK7_V9_CENSUS_COLUMN_CENSUS_EMPTY:{table_name}")
        if not _table_exists(connection, table_name):
            if table_name not in _TASK7_TABLES:
                raise RuntimeError(f"TASK7_V9_CENSUS_0037_TABLE_MISSING:{table_name}")
            connection.exec_driver_sql(
                f"CREATE TABLE public.{table_name} (\n{','.join(definitions)}\n)"
            )
            continue
        for column_ordinal, definition in enumerate(definitions):
            column_name = _column_name(
                definition,
                field=(f"current_ddl_registry.rows[{row_ordinal}].column_tuple[{column_ordinal}]"),
            )
            if not _column_exists(connection, table_name, column_name):
                connection.exec_driver_sql(
                    f"ALTER TABLE public.{table_name} ADD COLUMN {definition}"
                )


def _install_baseline_parent_authorities(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    section = _mapping(
        artifact.get("baseline_parent_authority_registry"),
        field="baseline_parent_authority_registry",
    )
    rows = _sequence(section.get("rows"), field="baseline_parent_authority_registry.rows")
    installed = 0
    for ordinal, raw_row in enumerate(rows):
        row = _sequence(raw_row, field=f"baseline_parent_authority_registry.rows[{ordinal}]")
        if len(row) != 9:
            raise RuntimeError("TASK7_V9_CENSUS_BASELINE_AUTHORITY_SHAPE_INVALID")
        (
            _authority_ordinal,
            raw_table_name,
            raw_authority_name,
            authority_kind,
            _columns,
            predicate,
            installation_revision,
            upgrade_ddl,
            downgrade_disposition,
        ) = row
        if installation_revision != "0038":
            continue
        table_name = _identifier(
            raw_table_name,
            field=f"baseline_parent_authority_registry.rows[{ordinal}].table",
        )
        authority_name = _identifier(
            raw_authority_name,
            field=f"baseline_parent_authority_registry.rows[{ordinal}].name",
        )
        if (
            authority_kind != "UNIQUE_CONSTRAINT"
            or predicate is not None
            or downgrade_disposition != "DROP_WITH_0038"
            or not isinstance(upgrade_ddl, str)
            or not upgrade_ddl.strip()
        ):
            raise RuntimeError(f"TASK7_V9_CENSUS_BASELINE_AUTHORITY_INVALID:{authority_name}")
        if not _constraint_exists(connection, table_name, authority_name):
            connection.exec_driver_sql(upgrade_ddl)
        installed += 1
    if installed != 2:
        raise RuntimeError("TASK7_V9_CENSUS_BASELINE_AUTHORITY_COUNT_MISMATCH")


def _install_check_authorities(
    connection: Connection,
    row: Mapping[str, Any],
    *,
    row_ordinal: int,
) -> None:
    table_name = _identifier(row.get("table_name"), field=f"ddl[{row_ordinal}].table")
    for authority_ordinal, authority in enumerate(
        _sequence(row.get("check_tuple"), field=f"ddl[{row_ordinal}].check_tuple")
    ):
        name, expression, suffix = _split_named(
            authority,
            field=f"ddl[{row_ordinal}].check_tuple[{authority_ordinal}]",
        )
        if suffix:
            raise RuntimeError(f"TASK7_V9_CENSUS_CHECK_SUFFIX_INVALID:{name}")
        if not _constraint_exists(connection, table_name, name):
            connection.exec_driver_sql(
                f"ALTER TABLE public.{table_name} ADD CONSTRAINT {name} CHECK ({expression})"
            )


def _install_unique_authorities(
    connection: Connection,
    row: Mapping[str, Any],
    *,
    row_ordinal: int,
) -> None:
    table_name = _identifier(row.get("table_name"), field=f"ddl[{row_ordinal}].table")
    for authority_ordinal, authority in enumerate(
        _sequence(row.get("unique_tuple"), field=f"ddl[{row_ordinal}].unique_tuple")
    ):
        name, columns, suffix = _split_named(
            authority,
            field=f"ddl[{row_ordinal}].unique_tuple[{authority_ordinal}]",
        )
        if _constraint_exists(connection, table_name, name) or _index_exists(connection, name):
            continue
        if name.startswith("pk_"):
            if suffix:
                raise RuntimeError(f"TASK7_V9_CENSUS_PRIMARY_KEY_SUFFIX_INVALID:{name}")
            connection.exec_driver_sql(
                f"ALTER TABLE public.{table_name} ADD CONSTRAINT {name} PRIMARY KEY ({columns})"
            )
        elif name.startswith("ux_") or suffix.startswith("WHERE "):
            where = f" {suffix}" if suffix else ""
            connection.exec_driver_sql(
                f"CREATE UNIQUE INDEX {name} ON public.{table_name} ({columns}){where}"
            )
        elif name.startswith("uq_"):
            if suffix not in {"", "NULLS NOT DISTINCT"}:
                raise RuntimeError(f"TASK7_V9_CENSUS_UNIQUE_SUFFIX_INVALID:{name}")
            nulls = " NULLS NOT DISTINCT" if suffix else ""
            connection.exec_driver_sql(
                f"ALTER TABLE public.{table_name} ADD CONSTRAINT {name} UNIQUE{nulls} ({columns})"
            )
        else:
            raise RuntimeError(f"TASK7_V9_CENSUS_UNIQUE_AUTHORITY_INVALID:{name}")


def _install_index_authorities(
    connection: Connection,
    row: Mapping[str, Any],
    *,
    row_ordinal: int,
) -> None:
    table_name = _identifier(row.get("table_name"), field=f"ddl[{row_ordinal}].table")
    for authority_ordinal, authority in enumerate(
        _sequence(row.get("index_tuple"), field=f"ddl[{row_ordinal}].index_tuple")
    ):
        name, columns, suffix = _split_named(
            authority,
            field=f"ddl[{row_ordinal}].index_tuple[{authority_ordinal}]",
        )
        if not _index_exists(connection, name):
            tail = f" {suffix}" if suffix else ""
            connection.exec_driver_sql(
                f"CREATE INDEX {name} ON public.{table_name} ({columns}){tail}"
            )


def _install_foreign_key_authorities(
    connection: Connection,
    row: Mapping[str, Any],
    *,
    row_ordinal: int,
) -> None:
    table_name = _identifier(row.get("table_name"), field=f"ddl[{row_ordinal}].table")
    for authority_ordinal, authority in enumerate(
        _sequence(
            row.get("foreign_key_tuple"),
            field=f"ddl[{row_ordinal}].foreign_key_tuple",
        )
    ):
        if not isinstance(authority, str):
            raise RuntimeError("TASK7_V9_CENSUS_FOREIGN_KEY_AUTHORITY_INVALID")
        match = _FOREIGN_KEY_RE.fullmatch(authority)
        if match is None:
            raise RuntimeError(
                f"TASK7_V9_CENSUS_FOREIGN_KEY_AUTHORITY_INVALID:{table_name}:{authority_ordinal}"
            )
        values = match.groupdict()
        name = _identifier(values["name"], field="foreign_key.name")
        if _constraint_exists(connection, table_name, name):
            continue
        deferred = values["deferred"] or ""
        connection.exec_driver_sql(
            f"ALTER TABLE public.{table_name} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({values['child']}) "
            f"REFERENCES public.{values['parent']} ({values['parent_cols']}) "
            f"ON UPDATE {values['update']} ON DELETE {values['delete']}{deferred}"
        )


def _install_current_ddl_authorities(
    connection: Connection,
    ddl_rows: Sequence[Mapping[str, Any]],
) -> None:
    for row_ordinal, row in enumerate(ddl_rows):
        _install_check_authorities(connection, row, row_ordinal=row_ordinal)
        _install_unique_authorities(connection, row, row_ordinal=row_ordinal)
        _install_index_authorities(connection, row, row_ordinal=row_ordinal)
    for row_ordinal, row in enumerate(ddl_rows):
        _install_foreign_key_authorities(connection, row, row_ordinal=row_ordinal)


def _install_erasure_context_helper(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> str:
    section = _mapping(
        artifact.get("erasure_execution_registry"),
        field="erasure_execution_registry",
    )
    rows = _sequence(section.get("rows"), field="erasure_execution_registry.rows")
    if len(rows) != 1:
        raise RuntimeError("TASK7_V9_CENSUS_ERASURE_ROW_COUNT_MISMATCH")
    row = _mapping(rows[0], field="erasure_execution_registry.rows[0]")
    statements = _sequence(
        row.get("legacy_guard_upgrade_sql_utf8_hex_tuple"),
        field="erasure_execution_registry.rows[0].legacy_guard_upgrade_sql_utf8_hex_tuple",
    )
    if not statements:
        raise RuntimeError("TASK7_V9_CENSUS_ERASURE_HELPER_MISSING")
    statement = _decode_sql(
        statements[0],
        field=("erasure_execution_registry.rows[0].legacy_guard_upgrade_sql_utf8_hex_tuple[0]"),
    )
    expected_prefix = (
        "CREATE FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) RETURNS boolean"
    )
    if not statement.startswith(expected_prefix):
        raise RuntimeError("TASK7_V9_CENSUS_ERASURE_HELPER_IDENTITY_MISMATCH")
    function_name = "idis_task7_approved_erasure_context_v1"
    if not _function_exists(connection, function_name):
        connection.exec_driver_sql(statement)
    return function_name


def _authority_sql(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, str], ...]:
    statements: list[tuple[int, str]] = []
    for row in rows:
        ordinal = int(row["authority_ordinal"])
        body = _sequence(
            row.get("body_sql_utf8_hex_tuple"),
            field=f"sql_authority_registry.rows[{ordinal}].body_sql_utf8_hex_tuple",
        )
        if not body:
            raise RuntimeError(f"TASK7_V9_CENSUS_AUTHORITY_BODY_EMPTY:{ordinal}")
        for statement_ordinal, encoded in enumerate(body):
            statements.append(
                (
                    ordinal,
                    _decode_sql(
                        encoded,
                        field=(
                            f"sql_authority_registry.rows[{ordinal}]"
                            f".body_sql_utf8_hex_tuple[{statement_ordinal}]"
                        ),
                    ),
                )
            )
    return tuple(statements)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Install the reviewed current v9 SQL over an empty schema-0037 database."""

    metadata = _mapping(artifact.get("metadata"), field="metadata")
    if metadata.get("pipeline_contract_version") != "full-strict-v9":
        raise RuntimeError("TASK7_V9_CENSUS_ARTIFACT_VERSION_MISMATCH")
    ddl_rows = _ddl_rows(artifact)
    authority_rows = _current_authority_rows(artifact)

    _materialize_columns(connection, ddl_rows)
    _install_baseline_parent_authorities(connection, artifact)
    _install_current_ddl_authorities(connection, ddl_rows)
    _install_erasure_context_helper(connection, artifact)
    for _authority_ordinal, statement in _authority_sql(authority_rows):
        connection.exec_driver_sql(statement)


def _current_targets(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    for row in _current_authority_rows(artifact):
        authority_id = str(row.get("authority_id"))
        for target_ordinal, raw_target in enumerate(
            _sequence(
                row.get("target_tuple"),
                field=f"sql_authority_registry.{authority_id}.target_tuple",
            )
        ):
            target = _sequence(
                raw_target,
                field=(f"sql_authority_registry.{authority_id}.target_tuple[{target_ordinal}]"),
            )
            if len(target) != 2:
                raise RuntimeError("TASK7_V9_CENSUS_TARGET_SHAPE_INVALID")
            targets.append(
                (
                    _identifier(target[0], field="target.table_name"),
                    _identifier(target[1], field="target.trigger_name"),
                )
            )
    if len(targets) != 73 or len(targets) != len(set(targets)):
        raise RuntimeError("TASK7_V9_CENSUS_TARGET_CLOSURE_MISMATCH")
    return tuple(targets)


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Disable each reviewed current user trigger inside the caller transaction."""

    for table_name, trigger_name in _current_targets(artifact):
        if not _trigger_exists(connection, table_name, trigger_name):
            raise RuntimeError(f"TASK7_V9_CENSUS_TRIGGER_MISSING:{table_name}.{trigger_name}")
        connection.exec_driver_sql(
            f"ALTER TABLE public.{table_name} DISABLE TRIGGER {trigger_name}"
        )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    """Enable one verified user trigger after the caller disabled the census set."""

    table = _identifier(table_name, field="target.table_name")
    trigger = _identifier(trigger_name, field="target.trigger_name")
    if not _trigger_exists(connection, table, trigger):
        raise RuntimeError(f"TASK7_V9_CENSUS_TRIGGER_MISSING:{table}.{trigger}")
    connection.exec_driver_sql(f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}")


def _authority_object_names(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    names: list[tuple[str, str]] = []
    for row_ordinal, row in enumerate(_ddl_rows(artifact)):
        table_name = _identifier(row.get("table_name"), field=f"ddl[{row_ordinal}].table")
        for field in ("check_tuple", "unique_tuple", "index_tuple", "foreign_key_tuple"):
            for authority_ordinal, authority in enumerate(
                _sequence(row.get(field), field=f"ddl[{row_ordinal}].{field}")
            ):
                if field == "foreign_key_tuple":
                    if not isinstance(authority, str):
                        raise RuntimeError("TASK7_V9_CENSUS_FOREIGN_KEY_AUTHORITY_INVALID")
                    match = _FOREIGN_KEY_RE.fullmatch(authority)
                    if match is None:
                        raise RuntimeError(
                            "TASK7_V9_CENSUS_FOREIGN_KEY_AUTHORITY_INVALID:"
                            f"{table_name}:{authority_ordinal}"
                        )
                    name = _identifier(match.group("name"), field="foreign_key.name")
                else:
                    name, _body, _suffix = _split_named(
                        authority,
                        field=f"ddl[{row_ordinal}].{field}[{authority_ordinal}]",
                    )
                names.append((table_name, name))
    baseline = _mapping(
        artifact.get("baseline_parent_authority_registry"),
        field="baseline_parent_authority_registry",
    )
    for ordinal, raw_row in enumerate(
        _sequence(baseline.get("rows"), field="baseline_parent_authority_registry.rows")
    ):
        baseline_row = _sequence(
            raw_row,
            field=f"baseline_parent_authority_registry.rows[{ordinal}]",
        )
        if len(baseline_row) == 9 and baseline_row[6] == "0038":
            names.append(
                (
                    _identifier(baseline_row[1], field="baseline.table"),
                    _identifier(baseline_row[2], field="baseline.name"),
                )
            )
    return tuple(names)


def _function_names(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    names = {"idis_task7_approved_erasure_context_v1"}
    for _authority_ordinal, statement in _authority_sql(_current_authority_rows(artifact)):
        match = _FUNCTION_DECLARATION_RE.match(statement)
        if match is not None:
            names.add(_identifier(match.group(1), field="authority.function_name"))
    return tuple(sorted(names))


def _stringify(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple("<NULL>" if value is None else str(value) for value in values)


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    """Return a deterministic projection of every artifact-installed SQL object."""

    projection: list[tuple[str, ...]] = []
    for row_ordinal, row in enumerate(_ddl_rows(artifact)):
        table_name = _identifier(row.get("table_name"), field=f"ddl[{row_ordinal}].table")
        if not _table_exists(connection, table_name):
            raise RuntimeError(f"TASK7_V9_CENSUS_CATALOG_TABLE_MISSING:{table_name}")
        projection.append(("TABLE", table_name))
        for column_ordinal, definition in enumerate(
            _sequence(row.get("column_tuple"), field=f"ddl[{row_ordinal}].column_tuple")
        ):
            column_name = _column_name(
                definition,
                field=f"ddl[{row_ordinal}].column_tuple[{column_ordinal}]",
            )
            catalog_row = connection.execute(
                text(
                    "SELECT a.attnum,pg_catalog.format_type(a.atttypid,a.atttypmod),"
                    "a.attnotnull,a.attgenerated,"
                    "pg_catalog.pg_get_expr(d.adbin,d.adrelid) "
                    "FROM pg_catalog.pg_attribute AS a "
                    "LEFT JOIN pg_catalog.pg_attrdef AS d "
                    "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                    "WHERE a.attrelid=to_regclass(:qualified_name) "
                    "AND a.attname=:column_name AND a.attnum>0 AND NOT a.attisdropped"
                ),
                {
                    "qualified_name": f"public.{table_name}",
                    "column_name": column_name,
                },
            ).one_or_none()
            if catalog_row is None:
                raise RuntimeError(
                    f"TASK7_V9_CENSUS_CATALOG_COLUMN_MISSING:{table_name}.{column_name}"
                )
            projection.append(
                (
                    "COLUMN",
                    table_name,
                    column_name,
                    *_stringify(catalog_row),
                )
            )

    for table_name, object_name in _authority_object_names(artifact):
        constraint = connection.execute(
            text(
                "SELECT c.contype,c.condeferrable,c.condeferred,c.convalidated,"
                "pg_catalog.pg_get_constraintdef(c.oid,true) "
                "FROM pg_catalog.pg_constraint AS c "
                "WHERE c.conrelid=to_regclass(:qualified_name) AND c.conname=:name"
            ),
            {"qualified_name": f"public.{table_name}", "name": object_name},
        ).one_or_none()
        index = connection.execute(
            text(
                "SELECT pg_catalog.pg_get_indexdef(c.oid) "
                "FROM pg_catalog.pg_class AS c "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='i' AND c.relname=:name"
            ),
            {"name": object_name},
        ).one_or_none()
        if constraint is None and index is None:
            raise RuntimeError(
                f"TASK7_V9_CENSUS_CATALOG_AUTHORITY_MISSING:{table_name}.{object_name}"
            )
        if constraint is not None:
            projection.append(("CONSTRAINT", table_name, object_name, *_stringify(constraint)))
        if index is not None:
            projection.append(("INDEX", table_name, object_name, *_stringify(index)))

    for table_name, trigger_name in _current_targets(artifact):
        trigger = connection.execute(
            text(
                "SELECT t.tgenabled,pg_catalog.pg_get_triggerdef(t.oid,true) "
                "FROM pg_catalog.pg_trigger AS t "
                "WHERE t.tgrelid=to_regclass(:qualified_name) "
                "AND t.tgname=:trigger_name AND NOT t.tgisinternal"
            ),
            {
                "qualified_name": f"public.{table_name}",
                "trigger_name": trigger_name,
            },
        ).one_or_none()
        if trigger is None:
            raise RuntimeError(
                f"TASK7_V9_CENSUS_CATALOG_TRIGGER_MISSING:{table_name}.{trigger_name}"
            )
        projection.append(("TRIGGER", table_name, trigger_name, *_stringify(trigger)))

    for function_name in _function_names(artifact):
        functions = tuple(
            connection.execute(
                text(
                    "SELECT pg_catalog.pg_get_function_identity_arguments(p.oid),"
                    "pg_catalog.pg_get_function_result(p.oid),p.provolatile,p.prosecdef,"
                    "pg_catalog.pg_get_functiondef(p.oid) "
                    "FROM pg_catalog.pg_proc AS p "
                    "JOIN pg_catalog.pg_namespace AS n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname=:function_name "
                    "ORDER BY pg_catalog.pg_get_function_identity_arguments(p.oid)"
                ),
                {"function_name": function_name},
            )
        )
        if not functions:
            raise RuntimeError(f"TASK7_V9_CENSUS_CATALOG_FUNCTION_MISSING:{function_name}")
        for function in functions:
            projection.append(("FUNCTION", function_name, *_stringify(function)))

    return tuple(sorted(set(projection)))


__all__ = [
    "catalog_projection",
    "disable_all_current_user_triggers",
    "enable_target_trigger",
    "install_artifact_sql_scaffold",
]
