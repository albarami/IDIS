"""RED proof that the 86-relation erasure order is executable on PostgreSQL.

The graph is derived from normalized v3 FK bytes plus the live migration-0037
catalog.  It is then re-materialized as real PostgreSQL constraints, populated,
deleted in the literal callable order, and committed.  The sole reviewed mutual
DEFERRABLE SCC is treated as one atomic group; no other cycle is admissible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from tests.slice100_task2b._support import disposable_postgres, migrate
from tests.slice100_task7.test_erasure_runner_superset_red import (
    _artifact,
    _authority_row,
    _reviewed_relation_tuple,
    _runner_delete_entries,
)

_REVIEWED_DEFERRED_SCC = frozenset({"data_room_packages", "source_lineage_objects"})
_MANDATORY_INDIRECT_EDGES = frozenset(
    {
        ("run_steps", "runs"),
        ("calc_sanads", "deterministic_calculations"),
        ("human_gate_actions", "human_gates"),
    }
)
_CURRENT_FK_PARENT_RE = re.compile(r"->\s*([a-z][a-z0-9_]*)\.")
_CHILD_SQL_RELATION_RE = re.compile(r"\bFROM\s+([a-z][a-z0-9_]*)\b", re.I)


@dataclass(frozen=True, order=True)
class _Edge:
    child: str
    parent: str
    deferrable: bool
    initially_deferred: bool


def _structured_parent(foreign_key: object) -> str:
    assert isinstance(foreign_key, list) and len(foreign_key) == 8
    parent = foreign_key[2]
    assert isinstance(parent, str) and re.fullmatch(r"[a-z][a-z0-9_]*", parent)
    return parent


def _artifact_fk_edges(artifact: dict[str, Any], runner_relations: frozenset[str]) -> set[_Edge]:
    edges: set[_Edge] = set()

    current = artifact["current_ddl_registry"]["rows"]
    assert isinstance(current, list)
    for row in current:
        child = row["table_name"]
        for foreign_key in row["foreign_key_tuple"]:
            assert isinstance(foreign_key, str)
            match = _CURRENT_FK_PARENT_RE.search(foreign_key)
            assert match is not None
            parent = match.group(1)
            if child in runner_relations and parent in runner_relations and child != parent:
                deferred = "DEFERRABLE INITIALLY DEFERRED" in foreign_key.upper()
                edges.add(_Edge(child, parent, deferred, deferred))

    structured_sections = (
        ("future_native_authority_registry", "planned_table", "reference_fields"),
        ("future_reference_authority_registry", "table_name", "foreign_key_tuple"),
        ("future_child_authority_registry", "table_name", "foreign_key_tuple"),
    )
    for section_name, table_field, foreign_key_field in structured_sections:
        rows = artifact[section_name]["rows"]
        assert isinstance(rows, list)
        for row in rows:
            child = row[table_field]
            for foreign_key in row[foreign_key_field]:
                parent = _structured_parent(foreign_key)
                if child in runner_relations and parent in runner_relations and child != parent:
                    edges.add(_Edge(child, parent, False, False))

    return edges


def _production_indirect_edges() -> set[_Edge]:
    from idis.compliance.erasure_postgres import _CHILD_TABLE_DELETES

    edges: set[_Edge] = set()
    for declared_child, sql in _CHILD_TABLE_DELETES:
        relations = _CHILD_SQL_RELATION_RE.findall(sql)
        assert len(relations) == 2
        assert relations == [declared_child, relations[-1]]
        edges.add(_Edge(declared_child, relations[1], False, False))
    assert {(edge.child, edge.parent) for edge in edges} == _MANDATORY_INDIRECT_EDGES
    return edges


def _live_fk_edges(connection: Connection, targets: frozenset[str]) -> set[_Edge]:
    rows = connection.execute(
        text(
            """
            SELECT child.relname, parent.relname, constraint_row.condeferrable,
                   constraint_row.condeferred
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS child ON child.oid=constraint_row.conrelid
            JOIN pg_class AS parent ON parent.oid=constraint_row.confrelid
            JOIN pg_namespace AS child_namespace ON child_namespace.oid=child.relnamespace
            JOIN pg_namespace AS parent_namespace ON parent_namespace.oid=parent.relnamespace
            WHERE constraint_row.contype='f'
              AND child_namespace.nspname='public'
              AND parent_namespace.nspname='public'
              AND child.relname=ANY(CAST(:targets AS text[]))
              AND parent.relname=ANY(CAST(:targets AS text[]))
              AND child.relname<>parent.relname
            ORDER BY child.relname,parent.relname,constraint_row.conname
            """
        ),
        {"targets": sorted(targets)},
    )
    return {
        _Edge(str(child), str(parent), bool(deferrable), bool(initially_deferred))
        for child, parent, deferrable, initially_deferred in rows
    }


def _strongly_connected_components(
    nodes: frozenset[str], edges: set[_Edge]
) -> tuple[frozenset[str], ...]:
    adjacency = {node: set() for node in nodes}
    for edge in edges:
        adjacency[edge.child].add(edge.parent)

    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[frozenset[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for parent in sorted(adjacency[node]):
            if parent not in indexes:
                visit(parent)
                lowlinks[node] = min(lowlinks[node], lowlinks[parent])
            elif parent in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[parent])
        if lowlinks[node] == indexes[node]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(frozenset(component))

    for node in sorted(nodes):
        if node not in indexes:
            visit(node)
    return tuple(components)


def _assert_child_first_order(
    *,
    runner_order: tuple[str, ...],
    reviewed_order: tuple[str, ...],
    edges: set[_Edge],
) -> None:
    runner_relations = frozenset(runner_order)
    components = _strongly_connected_components(runner_relations, edges)
    cyclic = tuple(component for component in components if len(component) > 1)
    assert cyclic == (_REVIEWED_DEFERRED_SCC,), (
        f"TASK7_ERASURE_RUNNER_UNREVIEWED_FK_CYCLE:{cyclic!r}"
    )

    scc_edges = {
        edge
        for edge in edges
        if edge.child in _REVIEWED_DEFERRED_SCC and edge.parent in _REVIEWED_DEFERRED_SCC
    }
    assert {(edge.child, edge.parent) for edge in scc_edges} == {
        ("data_room_packages", "source_lineage_objects"),
        ("source_lineage_objects", "data_room_packages"),
    }
    assert all(edge.deferrable and edge.initially_deferred for edge in scc_edges)

    runner_index = {relation: index for index, relation in enumerate(runner_order)}
    reviewed_scc_order = tuple(
        relation for relation in reviewed_order if relation in _REVIEWED_DEFERRED_SCC
    )
    runner_scc_order = tuple(
        relation for relation in runner_order if relation in _REVIEWED_DEFERRED_SCC
    )
    assert runner_scc_order == reviewed_scc_order
    assert runner_index[runner_scc_order[1]] == runner_index[runner_scc_order[0]] + 1

    for edge in edges - scc_edges:
        assert runner_index[edge.child] < runner_index[edge.parent], (
            f"TASK7_ERASURE_RUNNER_PARENT_BEFORE_CHILD:{edge.child}->{edge.parent}"
        )


def _materialize_and_execute_live_graph(
    connection: Connection,
    *,
    runner_order: tuple[str, ...],
    edges: set[_Edge],
) -> None:
    schema = "task7_erasure_runner_graph"
    connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    for relation in sorted(runner_order):
        connection.exec_driver_sql(
            f"CREATE TABLE {schema}.{relation} (task7_row_id INTEGER PRIMARY KEY)"
        )

    for ordinal, edge in enumerate(sorted(edges)):
        column = f"parent_{ordinal}"
        deferred = " DEFERRABLE INITIALLY DEFERRED" if edge.deferrable else ""
        connection.exec_driver_sql(
            f"ALTER TABLE {schema}.{edge.child} ADD COLUMN {column} INTEGER, "
            f"ADD CONSTRAINT fk_task7_{ordinal} FOREIGN KEY ({column}) "
            f"REFERENCES {schema}.{edge.parent}(task7_row_id){deferred}"
        )

    for relation in sorted(runner_order):
        connection.exec_driver_sql(f"INSERT INTO {schema}.{relation} (task7_row_id) VALUES (1)")
    for ordinal, edge in enumerate(sorted(edges)):
        connection.exec_driver_sql(
            f"UPDATE {schema}.{edge.child} SET parent_{ordinal}=1 WHERE task7_row_id=1"
        )

    connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
    for relation in runner_order:
        connection.exec_driver_sql(f"DELETE FROM {schema}.{relation}")

    for relation in runner_order:
        count = connection.exec_driver_sql(f"SELECT count(*) FROM {schema}.{relation}").scalar_one()
        assert count == 0


def test_runner_order_is_child_first_over_the_normalized_86_relation_graph() -> None:
    artifact = _artifact()
    reviewed_order = _reviewed_relation_tuple(artifact)
    runner_order = tuple(
        table for table, _statement in _runner_delete_entries(_authority_row(artifact))
    )
    runner_relations = frozenset(runner_order)
    edges = _artifact_fk_edges(artifact, runner_relations) | _production_indirect_edges()
    assert {(edge.child, edge.parent) for edge in edges} >= _MANDATORY_INDIRECT_EDGES
    _assert_child_first_order(
        runner_order=runner_order,
        reviewed_order=reviewed_order,
        edges=edges,
    )


def test_runner_order_executes_against_disposable_postgres_live_fk_graph() -> None:
    artifact = _artifact()
    reviewed_order = _reviewed_relation_tuple(artifact)
    runner_order = tuple(
        table for table, _statement in _runner_delete_entries(_authority_row(artifact))
    )
    runner_relations = frozenset(runner_order)

    # The repository support skips only when IDIS_DATABASE_ADMIN_URL is unavailable.
    # No provider or operator acceptance service is contacted by this proof.
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            edges = (
                _artifact_fk_edges(artifact, runner_relations)
                | _production_indirect_edges()
                | _live_fk_edges(connection, runner_relations)
            )
            assert {(edge.child, edge.parent) for edge in edges} >= _MANDATORY_INDIRECT_EDGES
            _assert_child_first_order(
                runner_order=runner_order,
                reviewed_order=reviewed_order,
                edges=edges,
            )
            _materialize_and_execute_live_graph(
                connection,
                runner_order=runner_order,
                edges=edges,
            )
