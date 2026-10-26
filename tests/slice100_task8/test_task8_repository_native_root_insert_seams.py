from __future__ import annotations

import importlib
import inspect
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from idis.persistence.repositories.methodologies import (
    MethodologiesRepositoryError,
    PostgresMethodologiesRepository,
)
from idis.persistence.repositories.methodology_extraction import (
    _METHODOLOGY_RESULT_LINK_COLUMNS,
    MethodologyExtractionRepositoryError,
    PostgresMethodologyExtractionRepository,
)


def test_six_production_batch_writers_expose_optional_native_root_maps() -> None:
    writers = (
        PostgresMethodologiesRepository.insert_coverage_records,
        PostgresMethodologyExtractionRepository.persist_planned_tasks,
        PostgresMethodologyExtractionRepository.persist_claim_links,
        PostgresMethodologyExtractionRepository.persist_evidence_links,
        PostgresMethodologyExtractionRepository.persist_sanad_links,
        PostgresMethodologyExtractionRepository.persist_defect_links,
    )

    for writer in writers:
        parameter = inspect.signature(writer).parameters.get("run_output_object_ids")
        assert parameter is not None
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None


def _root_validator():
    module = importlib.import_module("idis.persistence.repositories._task7_native_roots")
    return module.validate_run_output_object_ids


def test_native_root_map_is_exact_canonical_and_preserves_legacy_none() -> None:
    validate = _root_validator()
    coverage_ids = ("mc_" + "a" * 24, "mc_" + "b" * 24)
    roots = {
        coverage_ids[0]: "10000000-0000-4000-8000-000000000001",
        coverage_ids[1]: "10000000-0000-4000-8000-000000000002",
    }

    assert (
        validate(
            stable_native_ids=coverage_ids,
            run_output_object_ids=None,
            native_ids_are_uuids=False,
        )
        is None
    )
    assert validate(
        stable_native_ids=reversed(coverage_ids),
        run_output_object_ids=roots,
        native_ids_are_uuids=False,
    ) == {
        coverage_ids[1]: roots[coverage_ids[1]],
        coverage_ids[0]: roots[coverage_ids[0]],
    }


@pytest.mark.parametrize(
    "stable_native_ids, roots, native_ids_are_uuids",
    (
        (
            ("mc_" + "a" * 24, "mc_" + "b" * 24),
            {"mc_" + "a" * 24: "10000000-0000-4000-8000-000000000001"},
            False,
        ),
        (
            ("mc_" + "a" * 24,),
            {
                "mc_" + "a" * 24: "10000000-0000-4000-8000-000000000001",
                "mc_" + "b" * 24: "10000000-0000-4000-8000-000000000002",
            },
            False,
        ),
        (
            ("not-a-coverage-id",),
            {"not-a-coverage-id": "10000000-0000-4000-8000-000000000001"},
            False,
        ),
        (
            ("20000000-0000-4000-8000-000000000001",),
            {"20000000-0000-4000-8000-000000000001": "not-a-uuid"},
            True,
        ),
        (
            (
                "20000000-0000-4000-8000-000000000001",
                "20000000-0000-4000-8000-000000000002",
            ),
            {
                "20000000-0000-4000-8000-000000000001": ("10000000-0000-4000-8000-000000000001"),
                "20000000-0000-4000-8000-000000000002": ("10000000-0000-4000-8000-000000000001"),
            },
            True,
        ),
        (
            ("20000000-0000-4000-8000-000000000001",),
            {"20000000-0000-4000-8000-000000000001": "10000000-0000-4000-8000-00000000000A"},
            True,
        ),
    ),
)
def test_native_root_map_rejects_missing_extra_malformed_or_duplicate_bindings(
    stable_native_ids: tuple[str, ...],
    roots: dict[str, str],
    native_ids_are_uuids: bool,
) -> None:
    with pytest.raises(ValueError, match="TASK7_NATIVE_ROOT_BINDING_INVALID"):
        _root_validator()(
            stable_native_ids=stable_native_ids,
            run_output_object_ids=roots,
            native_ids_are_uuids=native_ids_are_uuids,
        )


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: object, params: dict[str, Any]) -> None:
        self.calls.append((str(statement), dict(params)))


class _ReloadResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _ReloadResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _ReloadConnection(_RecordingConnection):
    def __init__(self, rows: list[dict[str, object]]) -> None:
        super().__init__()
        self._rows = rows

    def execute(self, statement: object, params: dict[str, Any]) -> _ReloadResult:
        self.calls.append((str(statement), dict(params)))
        return _ReloadResult(self._rows)


def _methodologies_repository(connection: _RecordingConnection) -> Any:
    repository = object.__new__(PostgresMethodologiesRepository)
    repository._conn = connection
    repository._tenant_id = "30000000-0000-4000-8000-000000000001"
    return repository


def _extraction_repository(connection: _RecordingConnection) -> Any:
    repository = object.__new__(PostgresMethodologyExtractionRepository)
    repository._connection = connection
    repository._tenant_id = "30000000-0000-4000-8000-000000000001"
    return repository


def test_coverage_insert_binds_root_only_on_the_opt_in_sql_path() -> None:
    connection = _RecordingConnection()
    repository = _methodologies_repository(connection)
    record = SimpleNamespace(
        coverage_record_id="mc_" + "a" * 24,
        tenant_id=repository._tenant_id,
        deal_id="30000000-0000-4000-8000-000000000002",
        run_id="30000000-0000-4000-8000-000000000003",
        methodology_version_id="fdd-v1",
        methodology_question_id="FDD-001",
        methodology_type=SimpleNamespace(value="FINANCIAL_DD"),
        status=SimpleNamespace(value="not_started"),
        reason_code=None,
        evidence_links=(),
        answer=None,
        conflict_ids=(),
        defect_ids=(),
    )
    root = "10000000-0000-4000-8000-000000000001"

    repository._insert_coverage_record(
        record=record,
        selection_id="30000000-0000-4000-8000-000000000004",
        version_record_id="30000000-0000-4000-8000-000000000005",
        question_record_id="30000000-0000-4000-8000-000000000006",
        run_output_object_id=root,
    )
    rooted_sql, rooted_params = connection.calls.pop()
    assert "run_output_object_id" in rooted_sql
    assert rooted_params["run_output_object_id"] == root

    repository._insert_coverage_record(
        record=record,
        selection_id="30000000-0000-4000-8000-000000000004",
        version_record_id="30000000-0000-4000-8000-000000000005",
        question_record_id="30000000-0000-4000-8000-000000000006",
    )
    legacy_sql, legacy_params = connection.calls.pop()
    assert "run_output_object_id" not in legacy_sql
    assert "run_output_object_id" not in legacy_params


def test_planned_task_insert_binds_root_only_on_the_opt_in_sql_path() -> None:
    connection = _RecordingConnection()
    repository = _extraction_repository(connection)
    task_record_id = "20000000-0000-4000-8000-000000000001"
    task = SimpleNamespace(
        methodology_version_id="fdd-v1",
        methodology_question_id="FDD-001",
        methodology_type="FINANCIAL_DD",
        extraction_task_id="task-001",
        document_id="20000000-0000-4000-8000-000000000002",
        classification_id="classification-001",
    )
    spec = SimpleNamespace(
        record=SimpleNamespace(
            task_record_id=task_record_id,
            coverage_record_id="mc_" + "a" * 24,
            methodology_version_record_id="20000000-0000-4000-8000-000000000003",
            methodology_question_record_id="20000000-0000-4000-8000-000000000004",
            task_input_sha256="a" * 64,
            candidate_span_set_sha256="b" * 64,
            task=task,
        ),
        classification_sha256="c" * 64,
        payload={},
        spans=(),
    )
    root = "10000000-0000-4000-8000-000000000002"

    repository._insert_planned_task_spec(
        deal_id="20000000-0000-4000-8000-000000000005",
        run_id="20000000-0000-4000-8000-000000000006",
        selection_id="20000000-0000-4000-8000-000000000007",
        spec=spec,
        run_output_object_id=root,
    )
    rooted_sql, rooted_params = connection.calls.pop()
    assert "run_output_object_id" in rooted_sql
    assert rooted_params["run_output_object_id"] == root

    repository._insert_planned_task_spec(
        deal_id="20000000-0000-4000-8000-000000000005",
        run_id="20000000-0000-4000-8000-000000000006",
        selection_id="20000000-0000-4000-8000-000000000007",
        spec=spec,
    )
    legacy_sql, legacy_params = connection.calls.pop()
    assert "run_output_object_id" not in legacy_sql
    assert "run_output_object_id" not in legacy_params


@pytest.mark.parametrize(
    "table, native_id_column",
    (
        ("methodology_result_claim_links", "claim_link_id"),
        ("methodology_result_evidence_links", "evidence_link_id"),
        ("methodology_result_sanad_links", "sanad_link_id"),
        ("methodology_result_defect_links", "defect_link_id"),
    ),
)
def test_result_link_insert_binds_root_only_on_the_opt_in_sql_path(
    table: str,
    native_id_column: str,
) -> None:
    connection = _RecordingConnection()
    repository = _extraction_repository(connection)
    native_id = "20000000-0000-4000-8000-000000000010"
    record = dict.fromkeys(_METHODOLOGY_RESULT_LINK_COLUMNS[table], "value")
    record.update(
        {
            native_id_column: native_id,
            "tenant_id": repository._tenant_id,
            "deal_id": "20000000-0000-4000-8000-000000000011",
            "run_id": "20000000-0000-4000-8000-000000000012",
        }
    )
    root = "10000000-0000-4000-8000-000000000003"

    repository._persist_exact_links(
        table=table,
        deal_id=record["deal_id"],
        run_id=record["run_id"],
        records=(record,),
        run_output_object_ids={native_id: root},
    )
    rooted_sql, rooted_params = connection.calls.pop()
    assert "run_output_object_id" in rooted_sql
    assert rooted_params["run_output_object_id"] == root

    repository._persist_exact_links(
        table=table,
        deal_id=record["deal_id"],
        run_id=record["run_id"],
        records=(record,),
    )
    legacy_sql, legacy_params = connection.calls.pop()
    assert "run_output_object_id" not in legacy_sql
    assert "run_output_object_id" not in legacy_params


def test_public_writers_validate_and_forward_root_maps_before_insert() -> None:
    writers = (
        PostgresMethodologiesRepository.insert_coverage_records,
        PostgresMethodologyExtractionRepository.persist_planned_tasks,
        PostgresMethodologyExtractionRepository.persist_claim_links,
        PostgresMethodologyExtractionRepository.persist_evidence_links,
        PostgresMethodologyExtractionRepository.persist_sanad_links,
        PostgresMethodologyExtractionRepository.persist_defect_links,
    )

    for writer in writers:
        source = inspect.getsource(writer)
        assert "validate_run_output_object_ids" in source
        assert "run_output_object_ids" in source


def test_coverage_same_root_map_replay_matches_durable_rows() -> None:
    coverage_id = "mc_" + "a" * 24
    root = "10000000-0000-4000-8000-000000000001"
    connection = _ReloadConnection(
        [
            {
                "coverage_record_id": coverage_id,
                "run_output_object_id": uuid.UUID(root),
            }
        ]
    )
    repository = _methodologies_repository(connection)

    repository._require_coverage_root_bindings(
        deal_id="30000000-0000-4000-8000-000000000002",
        run_id="30000000-0000-4000-8000-000000000003",
        expected_root_bindings={coverage_id: root},
    )

    assert len(connection.calls) == 1
    assert "coverage_record_id, run_output_object_id" in connection.calls[0][0]


@pytest.mark.parametrize(
    "rows",
    (
        [],
        [{"coverage_record_id": "mc_" + "a" * 24, "run_output_object_id": None}],
        [
            {
                "coverage_record_id": "mc_" + "a" * 24,
                "run_output_object_id": uuid.UUID("10000000-0000-4000-8000-000000000009"),
            }
        ],
    ),
)
def test_coverage_supplied_root_map_rejects_missing_null_or_different_durable_rows(
    rows: list[dict[str, object]],
) -> None:
    coverage_id = "mc_" + "a" * 24
    repository = _methodologies_repository(_ReloadConnection(rows))

    with pytest.raises(MethodologiesRepositoryError) as raised:
        repository._require_coverage_root_bindings(
            deal_id="30000000-0000-4000-8000-000000000002",
            run_id="30000000-0000-4000-8000-000000000003",
            expected_root_bindings={coverage_id: "10000000-0000-4000-8000-000000000001"},
        )

    assert raised.value.code == "METHODOLOGY_COVERAGE_INCOMPLETE"


def test_planned_task_same_root_map_replay_matches_durable_rows() -> None:
    task_id = "20000000-0000-4000-8000-000000000001"
    root = "10000000-0000-4000-8000-000000000002"
    connection = _ReloadConnection(
        [
            {
                "task_record_id": uuid.UUID(task_id),
                "run_output_object_id": uuid.UUID(root),
            }
        ]
    )
    repository = _extraction_repository(connection)

    repository._require_planned_task_root_bindings(
        deal_id="20000000-0000-4000-8000-000000000005",
        run_id="20000000-0000-4000-8000-000000000006",
        expected_root_bindings={task_id: root},
    )

    assert len(connection.calls) == 1
    assert "task_record_id, run_output_object_id" in connection.calls[0][0]


@pytest.mark.parametrize(
    "rows",
    (
        [],
        [
            {
                "task_record_id": uuid.UUID("20000000-0000-4000-8000-000000000001"),
                "run_output_object_id": None,
            }
        ],
        [
            {
                "task_record_id": uuid.UUID("20000000-0000-4000-8000-000000000001"),
                "run_output_object_id": uuid.UUID("10000000-0000-4000-8000-000000000009"),
            }
        ],
    ),
)
def test_planned_task_supplied_root_map_rejects_missing_null_or_different_durable_rows(
    rows: list[dict[str, object]],
) -> None:
    repository = _extraction_repository(_ReloadConnection(rows))

    with pytest.raises(MethodologyExtractionRepositoryError) as raised:
        repository._require_planned_task_root_bindings(
            deal_id="20000000-0000-4000-8000-000000000005",
            run_id="20000000-0000-4000-8000-000000000006",
            expected_root_bindings={
                "20000000-0000-4000-8000-000000000001": ("10000000-0000-4000-8000-000000000002")
            },
        )

    assert raised.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


def test_root_reload_queries_are_reached_only_for_supplied_maps() -> None:
    coverage_source = inspect.getsource(PostgresMethodologiesRepository.insert_coverage_records)
    planned_source = inspect.getsource(
        PostgresMethodologyExtractionRepository._validate_exact_planned_graph
    )

    assert "if root_bindings is not None:" in coverage_source
    assert "_require_coverage_root_bindings" in coverage_source
    assert "if expected_root_bindings is not None:" in planned_source
    assert "_require_planned_task_root_bindings" in planned_source
