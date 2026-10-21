"""Schema-0037 planning fixture for the populated downgrade test.

The shared head fixture drives the current schema-0038 writer seams —
``queued.pipeline_contract_version`` and the other run/step contract
columns, the coverage-init service, ``require_run_scope``, and frozen
source binding — none of which exist at revision 0037.  This module
instead migrates a genuine 0037 schema (never 0038-then-stamp) and seeds
the identical fictional planning graph through explicit 0037 column
projections, so schema-head drift can never silently redefine this
historical test.  Production repositories keep their head-only behavior;
no legacy fallback is added.  Task payloads, identity UUIDs, hashes,
ordinals, and ordering are still computed solely by the unchanged
production spec/model/equality helpers — only the row projections and the
single-run lease transition are fixture-local.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.methodology.category_mapping import load_commercial_dd_question_category_map
from idis.methodology.models import MethodologyType
from idis.models.extraction_task import ExtractionTaskStatus, PlannedExtractionTask
from idis.models.methodology_coverage import (
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
)
from idis.models.run_step import STEP_ORDER, StepName
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodologies import (
    PostgresMethodologiesRepository,
    methodology_source_trace_digest,
)
from idis.persistence.repositories.methodology_extraction import (
    METHODOLOGY_PLANNER_CONTRACT_VERSION,
    METHODOLOGY_TASK_CONTRACT_VERSION,
    PostgresMethodologyExtractionRepository,
    _canonical_json,
    _canonicalize_planned_task,
    _json_value,
    _planned_graph_signature,
    _PlannedTaskSpec,
    _value,
)
from idis.services.runs.methodology_coverage_init import _coverage_records_for_registries
from tests.slice100_task2b._support import (
    DisposablePostgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task5.test_durable_selection_coverage_red import _registry
from tests.slice100_task6._support import set_seed_tenant
from tests.slice100_task6.test_coverage_projections_postgres_red import (
    ARTIFACT_ID,
    DEAL_ID,
    DOCUMENT_ID,
    HASH_A,
    HASH_B,
    LEASE_OWNER_ID,
    RUN_ID,
    RUN_STEP_ID,
    SANAD_STEP_ID,
    SOURCE_CONTENT_SHA256,
    SOURCE_TEXT,
    SPAN_ID,
    TENANT_ID,
)
from tests.slice100_task6.test_postgres_task6_write_fencing_red import (
    _build_fictional_selected_pair_tasks,
)
from tests.slice100_task6.test_selected_pair_planning_red import (
    CDD_DOCUMENT_ID,
    CDD_SOURCE_SHA256,
    CDD_SOURCE_TEXT,
    CDD_SPAN_ID,
    FDD_DOCUMENT_ID,
    FDD_SOURCE_SHA256,
    FDD_SOURCE_TEXT,
    FDD_SPAN_ID,
)

_LEGACY_LEASE_SECONDS: Final = 3600

_LEGACY_TASK_INSERT = """
    INSERT INTO methodology_extraction_tasks (
        task_record_id, tenant_id, deal_id, run_id, selection_id,
        coverage_record_id, methodology_version_record_id,
        methodology_version_id, methodology_question_record_id,
        methodology_question_id, methodology_type, extraction_task_id,
        document_id, document_classification_id,
        document_classification_sha256, planner_contract_version,
        task_contract_version, task_payload, task_input_sha256,
        candidate_span_set_sha256, status
    ) VALUES (
        CAST(:task_record_id AS uuid), CAST(:tenant_id AS uuid),
        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
        CAST(:selection_id AS uuid), :coverage_record_id,
        CAST(:methodology_version_record_id AS uuid), :methodology_version_id,
        CAST(:methodology_question_record_id AS uuid), :methodology_question_id,
        :methodology_type, :extraction_task_id, CAST(:document_id AS uuid),
        :classification_id, :classification_sha256,
        :planner_contract_version, :task_contract_version,
        CAST(:task_payload AS jsonb), :task_input_sha256,
        :candidate_set_sha256, 'READY'
    )
"""

_LEGACY_SPAN_INSERT = """
    INSERT INTO methodology_extraction_task_spans (
        task_span_record_id, tenant_id, deal_id, run_id, task_record_id,
        document_id, span_id, span_ordinal, content_sha256, locator_sha256
    ) VALUES (
        CAST(:task_span_record_id AS uuid), CAST(:tenant_id AS uuid),
        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
        CAST(:task_record_id AS uuid), CAST(:document_id AS uuid),
        CAST(:span_id AS uuid), :span_ordinal, :content_sha256, :locator_sha256
    )
"""


def seed_legacy_0037_planned_graph(
    database: DisposablePostgres,
) -> tuple[str, tuple[object, ...]]:
    """Migrate to real 0037 and seed the populated Task-6 planning graph.

    Returns ``(selection_id, planned_tasks)``; the persisted task/span rows are
    built by the shared fictional builder and validated against the unchanged
    production spec/equality helpers before the caller snapshots catalog/rows.
    """
    migrate(database.admin_engine, "0037")
    grant_application_access(database)
    _seed_legacy_scope(database)
    selection_id = _seed_legacy_selection_coverage(database)
    _seed_legacy_planning_documents(database)
    planned = _load_legacy_planned_tasks(database)
    _persist_legacy_planned_graph(database, selection_id, planned)
    return selection_id, planned


def _seed_legacy_scope(database: DisposablePostgres) -> None:
    """Seed tenant, deal, a fresh QUEUED run, and the base artifact/span."""
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) VALUES "
                "(CAST(:tenant AS uuid),'Coverage projection tenant')"
            ),
            {"tenant": TENANT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO deals (deal_id,tenant_id,name,created_at) VALUES "
                "(CAST(:deal AS uuid),CAST(:tenant AS uuid),'Fictional deal',now())"
            ),
            {"tenant": TENANT_ID, "deal": DEAL_ID},
        )
        connection.execute(
            text(
                "INSERT INTO runs (run_id,tenant_id,deal_id,mode,status,created_at) "
                "VALUES (CAST(:run AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "'FULL','QUEUED',now())"
            ),
            {"tenant": TENANT_ID, "deal": DEAL_ID, "run": RUN_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title,
                    source_system, version_id, sha256
                ) VALUES (
                    CAST(:artifact AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), 'DATA_ROOM_FILE', 'Fictional fixture',
                    'fixture', 'v1', :hash
                );
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                ) VALUES (
                    CAST(:document AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:artifact AS uuid), 'PDF', 'PARSED'
                );
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES (
                    CAST(:span AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:document AS uuid), 'PAGE_TEXT',
                    jsonb_build_object('page', 1, 'line', 1),
                    :source_text, :source_content_sha256
                )
                """
            ),
            {
                "tenant": TENANT_ID,
                "deal": DEAL_ID,
                "artifact": ARTIFACT_ID,
                "document": DOCUMENT_ID,
                "span": SPAN_ID,
                "hash": HASH_A,
                "source_text": SOURCE_TEXT,
                "source_content_sha256": SOURCE_CONTENT_SHA256,
            },
        )


def _seed_legacy_selection_coverage(database: DisposablePostgres) -> str:
    """Import/freeze the fictional registries and write the 100-row ledger."""
    cdd = _registry(MethodologyType.COMMERCIAL_DD, 21)
    fdd = _registry(MethodologyType.FINANCIAL_DD, 79)
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        repository = PostgresMethodologiesRepository(connection, TENANT_ID)
        frozen = []
        for registry in (cdd, fdd):
            imported = repository.import_registry(
                registry=registry,
                source_type=registry.current_version.questions[0].source_trace.source_type,
                source_alias="configured-source",
                source_byte_count=1,
                source_contract_hash=HASH_A,
                source_trace_digest=methodology_source_trace_digest(registry),
                imported_by_actor_id="coverage-projection-test",
                import_audit_event_id=str(uuid.uuid4()),
            )
            frozen.append(
                repository.freeze_version(
                    methodology_version_record_id=imported.methodology_version_record_id,
                    expected_source_sha256=registry.current_version.source_hash,
                    frozen_by_actor_id="coverage-projection-test",
                    freeze_audit_event_id=str(uuid.uuid4()),
                    expected_registry=registry,
                )
            )
        selection = repository.create_run_selection(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            commercial_dd_version_record_id=frozen[0].methodology_version_record_id,
            financial_dd_version_record_id=frozen[1].methodology_version_record_id,
            created_by_actor_id="coverage-projection-test",
            selection_audit_event_id=str(uuid.uuid4()),
        )
        _transition_legacy_run_lease(connection)
        extraction = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
        extraction._require_fence(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
        records = _coverage_records_for_registries(
            scope=(TENANT_ID, DEAL_ID, RUN_ID),
            registries=repository.load_selected_registries(selection=selection),
        )
        persisted = repository.insert_coverage_records(records=records)
        _assert_legacy_coverage(persisted)
        extraction._require_fence(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
        for step_id, step_name in (
            (RUN_STEP_ID, StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION),
            (SANAD_STEP_ID, StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO run_steps (
                        step_id, tenant_id, run_id, step_name, step_order, status
                    ) VALUES (
                        CAST(:step AS uuid), CAST(:tenant AS uuid), CAST(:run AS uuid),
                        :step_name, :step_order, 'RUNNING'
                    )
                    """
                ),
                {
                    "step": step_id,
                    "tenant": TENANT_ID,
                    "run": RUN_ID,
                    "step_name": step_name.value,
                    "step_order": STEP_ORDER[step_name],
                },
            )
    return selection.selection_id


def _transition_legacy_run_lease(connection: Connection) -> None:
    """Move the freshly seeded QUEUED run into a valid RUNNING token-1 lease."""
    rows = connection.execute(
        text(
            """
            UPDATE runs
            SET status = 'RUNNING',
                lease_owner_id = CAST(:lease_owner_id AS uuid),
                lease_fencing_token = lease_fencing_token + 1,
                lease_expires_at = statement_timestamp()
                    + make_interval(secs => :lease_seconds),
                last_heartbeat_at = statement_timestamp(),
                started_at = COALESCE(started_at, statement_timestamp())
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
              AND status = 'QUEUED'
              AND lease_fencing_token = 0
            RETURNING status, lease_owner_id, lease_fencing_token,
                      lease_expires_at > statement_timestamp() AS live_lease
            """
        ),
        {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "lease_owner_id": LEASE_OWNER_ID,
            "lease_seconds": _LEGACY_LEASE_SECONDS,
        },
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.status) == "RUNNING"
    assert str(row.lease_owner_id) == LEASE_OWNER_ID
    assert int(row.lease_fencing_token) == 1
    assert bool(row.live_lease) is True


def _assert_legacy_coverage(persisted: Sequence[MethodologyCoverageRecord]) -> None:
    """Require the exact 100-row fictional coverage ledger after reload."""
    assert len(persisted) == 100
    assert len({record.coverage_record_id for record in persisted}) == 100
    assert {record.status for record in persisted} == {MethodologyCoverageStatus.NOT_STARTED}
    assert {(record.tenant_id, record.deal_id, record.run_id) for record in persisted} == {
        (TENANT_ID, DEAL_ID, RUN_ID)
    }
    assert Counter(record.methodology_type for record in persisted) == {
        MethodologyType.COMMERCIAL_DD: 21,
        MethodologyType.FINANCIAL_DD: 79,
    }


def _seed_legacy_planning_documents(database: DisposablePostgres) -> None:
    """Seed the CDD/FDD planning artifact, document, and live span rows."""
    with database.admin_engine.begin() as connection:
        set_seed_tenant(connection, TENANT_ID)
        connection.execute(
            text(
                """
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title,
                    source_system, version_id, sha256
                ) VALUES
                (gen_random_uuid(), CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid),
                 'DATA_ROOM_FILE', 'Fictional CDD', 'fixture', 'v1', :hash_a),
                (gen_random_uuid(), CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid),
                 'DATA_ROOM_FILE', 'Fictional FDD', 'fixture', 'v1', :hash_b)
                RETURNING doc_id
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "hash_a": HASH_A,
                "hash_b": HASH_B,
            },
        ).fetchall()
        artifact_rows = connection.execute(
            text(
                """
                SELECT doc_id FROM document_artifacts
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND title IN ('Fictional CDD', 'Fictional FDD')
                ORDER BY title
                """
            ),
            {"tenant_id": TENANT_ID, "deal_id": DEAL_ID},
        ).fetchall()
        assert len(artifact_rows) == 2
        cdd_artifact_id = str(artifact_rows[0].doc_id)
        fdd_artifact_id = str(artifact_rows[1].doc_id)
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                ) VALUES
                (CAST(:cdd_document AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:cdd_artifact AS uuid),
                 'PDF', 'PARSED'),
                (CAST(:fdd_document AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:fdd_artifact AS uuid),
                 'XLSX', 'PARSED');
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES
                (CAST(:cdd_span AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:cdd_document AS uuid), 'PAGE_TEXT',
                 jsonb_build_object('page', 1, 'line', 1),
                 :cdd_text, :cdd_source_sha256),
                (CAST(:fdd_span AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:fdd_document AS uuid), 'CELL',
                 jsonb_build_object('sheet', 'P&L', 'cell', 'A1', 'row', 1, 'col', 1),
                 :fdd_text, :fdd_source_sha256)
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "cdd_artifact": cdd_artifact_id,
                "fdd_artifact": fdd_artifact_id,
                "cdd_document": CDD_DOCUMENT_ID,
                "fdd_document": FDD_DOCUMENT_ID,
                "cdd_span": CDD_SPAN_ID,
                "fdd_span": FDD_SPAN_ID,
                "cdd_text": CDD_SOURCE_TEXT,
                "fdd_text": FDD_SOURCE_TEXT,
                "cdd_source_sha256": CDD_SOURCE_SHA256,
                "fdd_source_sha256": FDD_SOURCE_SHA256,
            },
        )


def _load_legacy_planned_tasks(database: DisposablePostgres) -> tuple[object, ...]:
    """Build the identical 100 fictional tasks the shared head builder emits."""
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        methodology_repository = PostgresMethodologiesRepository(connection, TENANT_ID)
        selection = methodology_repository.load_run_selection(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )
        assert selection is not None
        registries = methodology_repository.load_selected_registries(selection=selection)
        coverage = methodology_repository.list_coverage_records(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )
        questions = {
            question.methodology_question_id: question
            for registry in registries
            for question in registry.current_version.questions
        }
        return _build_fictional_selected_pair_tasks(
            questions=questions,
            coverage=coverage,
            category_map=load_commercial_dd_question_category_map(),
        )


def _persist_legacy_planned_graph(
    database: DisposablePostgres,
    selection_id: str,
    tasks: tuple[object, ...],
) -> None:
    """Persist the 100-task READY graph through the exact 0037 projection."""
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
        repository._require_fence(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
        _assert_legacy_immutable_guards_enabled(connection)
        validated = [_canonicalize_planned_task(task) for task in tasks]
        assert len(validated) == 100
        assert all(
            task.tenant_id == TENANT_ID
            and task.deal_id == DEAL_ID
            and task.run_id == RUN_ID
            and task.status is ExtractionTaskStatus.READY
            and task.coverage_record_id is not None
            and task.document_id is not None
            and task.classification_id is not None
            and task.source_spans
            for task in validated
        )
        selected_id, coverage = repository._load_exact_selected_coverage(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            selection_id=selection_id,
        )
        assert selected_id == selection_id
        assert len(coverage) == 100
        specs = repository._build_planned_task_specs(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            selection_id=selection_id,
            tasks=validated,
            coverage=coverage,
        )
        assert len(specs) == 100
        assert len({spec.record.task_record_id for spec in specs}) == 100
        assert len({spec.record.task.extraction_task_id for spec in specs}) == 100
        assert sum(len(spec.spans) for spec in specs) == 100
        for spec in specs:
            _insert_legacy_task_spec(connection, spec=spec)
        _assert_legacy_graph(
            connection,
            repository=repository,
            selection_id=selection_id,
            coverage=coverage,
            expected=specs,
        )


def _assert_legacy_immutable_guards_enabled(connection: Connection) -> None:
    """Require the task/span immutable BEFORE-INSERT guards to be enabled."""
    rows = connection.execute(
        text(
            """
            SELECT trigger_row.tgname, trigger_row.tgenabled
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relname IN (
                  'methodology_extraction_tasks',
                  'methodology_extraction_task_spans'
              )
              AND trigger_row.tgname IN (
                  'trg_methodology_extraction_tasks_immutable',
                  'trg_methodology_extraction_task_spans_immutable'
              )
              AND NOT trigger_row.tgisinternal
            ORDER BY trigger_row.tgname
            """
        )
    ).fetchall()
    assert len(rows) == 2
    assert all(str(row.tgenabled) == "O" for row in rows)


def _insert_legacy_task_spec(connection: Connection, *, spec: _PlannedTaskSpec) -> None:
    """Insert one task then its spans through the exact 0037 projection."""
    task = spec.record.task
    connection.execute(
        text(_LEGACY_TASK_INSERT),
        {
            "task_record_id": spec.record.task_record_id,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "selection_id": spec.record.selection_id,
            "coverage_record_id": spec.record.coverage_record_id,
            "methodology_version_record_id": spec.record.methodology_version_record_id,
            "methodology_version_id": task.methodology_version_id,
            "methodology_question_record_id": spec.record.methodology_question_record_id,
            "methodology_question_id": task.methodology_question_id,
            "methodology_type": str(_value(task.methodology_type)),
            "extraction_task_id": task.extraction_task_id,
            "document_id": task.document_id,
            "classification_id": task.classification_id,
            "classification_sha256": spec.classification_sha256,
            "planner_contract_version": METHODOLOGY_PLANNER_CONTRACT_VERSION,
            "task_contract_version": METHODOLOGY_TASK_CONTRACT_VERSION,
            "task_payload": _canonical_json(spec.payload),
            "task_input_sha256": spec.record.task_input_sha256,
            "candidate_set_sha256": spec.record.candidate_span_set_sha256,
        },
    )
    for span in spec.spans:
        connection.execute(
            text(_LEGACY_SPAN_INSERT),
            {
                "task_span_record_id": span.task_span_record_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "task_record_id": spec.record.task_record_id,
                "document_id": span.document_id,
                "span_id": span.span_id,
                "span_ordinal": span.span_ordinal,
                "content_sha256": span.content_sha256,
                "locator_sha256": span.locator_sha256,
            },
        )


def _assert_legacy_graph(
    connection: Connection,
    *,
    repository: PostgresMethodologyExtractionRepository,
    selection_id: str,
    coverage: Mapping[str, Any],
    expected: Sequence[_PlannedTaskSpec],
) -> None:
    """Reload the exact 200 durable rows and compare them to planned specs."""
    scope = {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID}
    task_rows = (
        connection.execute(
            text(
                """
            SELECT task_record_id, selection_id, coverage_record_id,
                   methodology_version_record_id, methodology_version_id,
                   methodology_question_record_id, methodology_question_id,
                   methodology_type, extraction_task_id, document_id,
                   document_classification_id, document_classification_sha256,
                   planner_contract_version, task_contract_version,
                   task_payload, task_input_sha256,
                   candidate_span_set_sha256, status
            FROM methodology_extraction_tasks
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND run_id=CAST(:run_id AS uuid)
            ORDER BY methodology_question_id, document_id, extraction_task_id
            """
            ),
            scope,
        )
        .mappings()
        .all()
    )
    assert len(task_rows) == 100
    durable_tasks: list[PlannedExtractionTask] = []
    for row in task_rows:
        raw_payload = _json_value(row["task_payload"])
        assert isinstance(raw_payload, Mapping)
        task = _canonicalize_planned_task(dict(raw_payload))
        assert _canonical_json(raw_payload) == _canonical_json(task.model_dump(mode="json"))
        durable_tasks.append(task)
    assert Counter(task.methodology_type for task in durable_tasks) == {
        MethodologyType.COMMERCIAL_DD: 21,
        MethodologyType.FINANCIAL_DD: 79,
    }
    durable_specs = repository._build_planned_task_specs(
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        selection_id=selection_id,
        tasks=durable_tasks,
        coverage=coverage,
    )
    assert _planned_graph_signature(durable_specs) == _planned_graph_signature(expected)
    assert len(task_rows) == len(durable_specs)
    for row, spec in zip(task_rows, durable_specs, strict=True):
        assert repository._task_row_matches_planned_spec(row=row, spec=spec)
    span_rows = (
        connection.execute(
            text(
                """
            SELECT s.task_span_record_id, s.task_record_id, s.document_id, s.span_id,
                   s.span_ordinal, s.content_sha256, s.locator_sha256,
                   live.document_id AS live_document_id,
                   live.content_hash AS live_content_sha256,
                   live.locator AS live_locator,
                   live.text_excerpt AS live_text_excerpt
            FROM methodology_extraction_task_spans s
            JOIN methodology_extraction_tasks t
              ON t.tenant_id=s.tenant_id AND t.deal_id=s.deal_id
             AND t.run_id=s.run_id AND t.task_record_id=s.task_record_id
            LEFT JOIN document_spans live
              ON live.tenant_id=s.tenant_id
             AND live.deal_id=s.deal_id
             AND live.document_id=s.document_id
             AND live.span_id=s.span_id
            WHERE s.tenant_id=CAST(:tenant_id AS uuid)
              AND s.deal_id=CAST(:deal_id AS uuid)
              AND s.run_id=CAST(:run_id AS uuid)
            ORDER BY t.methodology_question_id, t.document_id,
                     t.extraction_task_id, s.span_ordinal, s.task_span_record_id
            """
            ),
            scope,
        )
        .mappings()
        .all()
    )
    expected_spans = [
        (spec, span, spec.record.task.source_spans[span.span_ordinal - 1])
        for spec in durable_specs
        for span in spec.spans
    ]
    assert len(span_rows) == len(expected_spans) == 100
    for row, (task_spec, span_spec, _source_span) in zip(span_rows, expected_spans, strict=True):
        assert repository._span_row_matches_planned_spec(
            row=row, task_spec=task_spec, span_spec=span_spec
        )
    for row, (_task_spec, span_spec, source_span) in zip(span_rows, expected_spans, strict=True):
        assert repository._live_span_matches_admitted_identity(
            row=row, span_spec=span_spec, source_span=source_span
        )
