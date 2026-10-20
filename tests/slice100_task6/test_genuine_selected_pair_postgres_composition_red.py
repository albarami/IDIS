"""Genuine CDD+FDD PostgreSQL composition and restart contracts for Task 6."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

import idis.pipeline.worker as worker_module
from idis.methodology.category_mapping import (
    CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES_SHA256,
    CDD_DOCUMENT_CLASSIFIER_VERSION,
    load_commercial_dd_question_category_map,
)
from idis.methodology.importers.cdd_json import import_cdd_template
from idis.methodology.importers.fdd_excel import import_fdd_workbook
from idis.methodology.models import MethodologyType
from idis.models.extraction_task import ExtractionTaskStatus
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.methodologies import (
    PostgresMethodologiesRepository,
    methodology_source_trace_digest,
)
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import (
    PostgresRunsRepository,
    WorkerLeaseFencedError,
)
from idis.pipeline.worker import (
    build_postgres_methodology_extraction_callbacks,
)
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService
from idis.services.runs.methodology_coverage_init import (
    DurableRunMethodologyCoverageInitService,
)
from idis.services.runs.steps import load_document_preflight_corpus_for_deal
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6._support import (
    RUN_CONTRACT_COLUMN_SQL,
    RUN_CONTRACT_VALUE_SQL,
    TASK6_SCHEMA_HEAD,
    bind_frozen_run_source,
    corrupt_durable_rows_out_of_band,
    set_seed_tenant,
    task6_head_seed_params,
)
from tests.slice100_task8._artifact_oracle import requires_operator_workbook

_CDD_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "idis"
    / "methodology"
    / "templates"
    / "commercial_dd_v1.json"
)
_CDD_SHA256 = "57cfec98436514cae717e359fa872504a7b9e1115c7bc12a4561e0989c4281fc"
_FDD_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
_CDD_REGISTRY_SHA256 = "13f38ef7afa9e96828ff723d32b650a16a9d4817f4dd6b5fab1bf0aafcdde2db"
_FDD_REGISTRY_SHA256 = "229950a1571699223e22706dc1cfa760a4024c1904b2118719b4be8467e6cf2a"
_TASK_COUNT = 112
_QUESTION_COUNT = 100
_LEASE_SECONDS = 600


@dataclass(frozen=True, slots=True)
class _Scope:
    tenant_id: str
    foreign_tenant_id: str
    deal_id: str
    run_id: str
    lease_owner_id: str
    lease_fencing_token: int
    selection_id: str
    preflight_step_id: str
    planning_step_id: str


@dataclass(frozen=True, slots=True)
class _SourceIdentity:
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _DocumentFixture:
    filename: str
    document_type: str
    text: str
    document_id: str
    artifact_id: str
    span_id: str


class _BoundTransaction:
    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield


def _operator_fdd_path(
    record_property: Callable[[str, object], None] | None = None,
) -> Path:
    from tests.slice100_task8._artifact_oracle import require_operator_workbook

    return require_operator_workbook(record_property).resolve()


def test_missing_operator_workbook_records_not_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    record_property: pytest.RecordProperty,
) -> None:
    monkeypatch.setenv("IDIS_OPERATOR_WORKBOOK_PATH", str(tmp_path / "missing.xlsx"))
    monkeypatch.delenv("IDIS_FDD_METHODOLOGY_PATH", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_OPERATOR_WORKBOOK", raising=False)
    recorded: list[tuple[str, object]] = []

    def capture(name: str, value: object) -> None:
        recorded.append((name, value))
        record_property(name, value)

    resolved = _operator_fdd_path(capture)

    assert resolved == (tmp_path / "missing.xlsx").resolve()
    assert not resolved.is_file()
    assert recorded == [
        ("OPERATOR_WORKBOOK", "OPERATOR_WORKBOOK NOT_EXECUTED: workbook file is absent")
    ]
    captured = capsys.readouterr()
    assert captured.out.strip() == "OPERATOR_WORKBOOK NOT_EXECUTED: workbook file is absent"


def test_missing_required_operator_workbook_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    record_property: pytest.RecordProperty,
) -> None:
    monkeypatch.setenv("IDIS_OPERATOR_WORKBOOK_PATH", str(tmp_path / "missing.xlsx"))
    monkeypatch.delenv("IDIS_FDD_METHODOLOGY_PATH", raising=False)
    monkeypatch.setenv("IDIS_REQUIRE_OPERATOR_WORKBOOK", "1")

    with pytest.raises(
        pytest.fail.Exception,
        match=r"^OPERATOR_WORKBOOK NOT_EXECUTED: workbook file is absent$",
    ):
        _operator_fdd_path(record_property)


def test_present_operator_workbook_is_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    record_property: pytest.RecordProperty,
) -> None:
    chosen = tmp_path / "chosen.xlsx"
    legacy = tmp_path / "legacy.xlsx"
    chosen.write_bytes(b"present-workbook")
    legacy.write_bytes(b"legacy-workbook")
    monkeypatch.setenv("IDIS_OPERATOR_WORKBOOK_PATH", str(chosen))
    monkeypatch.setenv("IDIS_FDD_METHODOLOGY_PATH", str(legacy))
    monkeypatch.delenv("IDIS_REQUIRE_OPERATOR_WORKBOOK", raising=False)

    resolved = _operator_fdd_path(record_property)

    assert resolved == chosen.resolve()
    assert "OPERATOR_WORKBOOK NOT_EXECUTED" not in capsys.readouterr().out


def test_safe_comparison_failures_emit_only_ids_and_hashes() -> None:
    private_actual = "private workbook question actual"
    private_expected = "private workbook question expected"
    safe_id = "task-safe-id:question"

    with pytest.raises(pytest.fail.Exception) as canonical_failure:
        _assert_safe_canonical_equal(
            {"question_text": private_actual},
            {"question_text": private_expected},
            safe_id=safe_id,
        )
    with pytest.raises(pytest.fail.Exception) as text_failure:
        _assert_safe_utf8_equal(
            private_actual,
            private_expected,
            safe_id=safe_id,
        )

    canonical_message = str(canonical_failure.value)
    text_message = str(text_failure.value)
    for private_value in (private_actual, private_expected):
        assert private_value not in canonical_message
        assert private_value not in text_message
    assert safe_id in canonical_message
    assert safe_id in text_message
    assert canonical_failure.value.pytrace is False
    assert text_failure.value.pytrace is False
    assert _sha256_json({"question_text": private_actual}) in canonical_message
    assert _sha256_json({"question_text": private_expected}) in canonical_message
    assert hashlib.sha256(private_actual.encode("utf-8")).hexdigest() in text_message
    assert hashlib.sha256(private_expected.encode("utf-8")).hexdigest() in text_message


def _source_identity(path: Path) -> _SourceIdentity:
    stat = path.stat()
    return _SourceIdentity(
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _assert_safe_sha256_equal(
    actual_sha256: str,
    expected_sha256: str,
    *,
    safe_id: str,
) -> None:
    if actual_sha256 == expected_sha256:
        return
    pytest.fail(
        f"{safe_id}: actual_sha256={actual_sha256} expected_sha256={expected_sha256}",
        pytrace=False,
    )


def _assert_safe_canonical_equal(actual: object, expected: object, *, safe_id: str) -> None:
    if actual == expected:
        return
    _assert_safe_sha256_equal(
        _sha256_json(actual),
        _sha256_json(expected),
        safe_id=safe_id,
    )


def _assert_safe_utf8_equal(actual: object, expected: object, *, safe_id: str) -> None:
    if not isinstance(actual, str) or not isinstance(expected, str):
        pytest.fail(f"{safe_id}: UTF-8 comparison requires two strings", pytrace=False)
    if actual == expected:
        return
    _assert_safe_sha256_equal(
        hashlib.sha256(actual.encode("utf-8")).hexdigest(),
        hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        safe_id=safe_id,
    )


def _contract_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _document_fixtures() -> tuple[_DocumentFixture, ...]:
    raw = (
        (
            "financial_model.xlsx",
            "XLSX",
            "Fictional financial model. Go to market. Management team. Commercial risk.",
        ),
        (
            "customer_contract.docx",
            "DOCX",
            "Fictional customer contract renewal evidence.",
        ),
        (
            "market_research.pdf",
            "PDF",
            "Fictional market research sizing and structure.",
        ),
        (
            "product_technology.pdf",
            "PDF",
            "Fictional product technology architecture.",
        ),
        (
            "competitive_landscape.pdf",
            "PDF",
            "Fictional competitive landscape analysis.",
        ),
        (
            "sales_pipeline.xlsx",
            "XLSX",
            "Fictional sales pipeline and revenue drivers.",
        ),
    )
    fixtures: list[_DocumentFixture] = []
    for index, (filename, document_type, source_text) in enumerate(raw, start=1):
        fixtures.append(
            _DocumentFixture(
                filename=filename,
                document_type=document_type,
                text=source_text,
                document_id=str(uuid.UUID(int=100 + index)),
                artifact_id=str(uuid.UUID(int=200 + index)),
                span_id=str(uuid.UUID(int=300 + index)),
            )
        )
    return tuple(fixtures)


def _seed_scope(database: DisposablePostgres, *, fdd_path: Path) -> _Scope:
    tenant_id = str(uuid.uuid4())
    foreign_tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    lease_owner_id = str(uuid.uuid4())
    with database.admin_engine.begin() as connection:
        set_seed_tenant(connection, tenant_id)
        connection.execute(
            text(
                f"""
                INSERT INTO tenants (tenant_id, name) VALUES
                    (CAST(:tenant_id AS uuid), 'Fictional Unit3 tenant'),
                    (CAST(:foreign_tenant_id AS uuid), 'Fictional foreign tenant');
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (
                    CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid),
                    'Fictional genuine composition deal', now()
                );
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status, started_at, created_at,
                    {RUN_CONTRACT_COLUMN_SQL}
                ) VALUES (
                    CAST(:run_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), 'FULL', 'QUEUED', now(), now(),
                    {RUN_CONTRACT_VALUE_SQL}
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "foreign_tenant_id": foreign_tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                **task6_head_seed_params(mode="FULL"),
            },
        )

    with database.app_engine.begin() as connection:
        documents = PostgresDocumentsRepository(connection, tenant_id)
        for fixture in _document_fixtures():
            content_sha256 = hashlib.sha256(fixture.text.encode("utf-8")).hexdigest()
            documents.create_artifact(
                doc_id=fixture.artifact_id,
                deal_id=deal_id,
                doc_type="DATA_ROOM_FILE",
                title=fixture.filename,
                source_system="task6-genuine-composition",
                version_id="fixture-v1",
                ingested_at=datetime.now(UTC),
                sha256=content_sha256,
                uri=f"safe-fixture/{fixture.filename}",
                metadata={"fixture_contract": "task6-genuine-six-document-v1"},
            )
            documents.create_document(
                document_id=fixture.document_id,
                deal_id=deal_id,
                doc_id=fixture.artifact_id,
                doc_type=fixture.document_type,
                parse_status="PARSED",
                metadata={
                    "parser_doc_type": fixture.document_type,
                    "detected_format": fixture.document_type,
                    "parser_runtime_status": "completed",
                    "span_count": 1,
                    "parse_error_codes": [],
                    "parse_warning_codes": [],
                },
            )
            if fixture.document_type == "XLSX":
                span_type = "CELL"
                locator = {"sheet": "Fictional", "cell": "A1", "row": 1, "col": 1}
            elif fixture.document_type == "DOCX":
                span_type = "PARAGRAPH"
                locator = {"paragraph": 0}
            else:
                span_type = "PAGE_TEXT"
                locator = {"page": 1, "line": 1}
            documents.create_document_span(
                span_id=fixture.span_id,
                deal_id=deal_id,
                document_id=fixture.document_id,
                span_type=span_type,
                locator=locator,
                text_excerpt=fixture.text,
                content_hash=content_sha256,
            )

    cdd_bytes = _CDD_PATH.read_bytes()
    fdd_bytes = fdd_path.read_bytes()
    cdd = import_cdd_template(_CDD_PATH)
    fdd = import_fdd_workbook(fdd_path)
    assert cdd.current_version.source_hash == _CDD_SHA256
    assert fdd.current_version.source_hash == _FDD_SHA256
    assert cdd.registry_hash == _CDD_REGISTRY_SHA256
    assert fdd.registry_hash == _FDD_REGISTRY_SHA256
    assert (len(cdd.current_version.questions), len(fdd.current_version.questions)) == (21, 79)

    with database.app_engine.begin() as connection:
        repository = PostgresMethodologiesRepository(connection, tenant_id)
        cdd_imported = repository.import_registry(
            registry=cdd,
            source_type="checked_in_template",
            source_alias=cdd.current_version.source_name,
            source_byte_count=len(cdd_bytes),
            source_contract_hash=_contract_hash("task6-cdd-source-contract-v1"),
            source_trace_digest=methodology_source_trace_digest(cdd),
            imported_by_actor_id="task6-unit3-operator",
            import_audit_event_id=str(uuid.uuid4()),
        )
        fdd_imported = repository.import_registry(
            registry=fdd,
            source_type="operator_workbook",
            source_alias=fdd.current_version.source_name,
            source_byte_count=len(fdd_bytes),
            source_contract_hash=_contract_hash("task6-fdd-source-contract-v1"),
            source_trace_digest=methodology_source_trace_digest(fdd),
            imported_by_actor_id="task6-unit3-operator",
            import_audit_event_id=str(uuid.uuid4()),
        )
        assert cdd_imported.registry_hash == _CDD_REGISTRY_SHA256
        assert fdd_imported.registry_hash == _FDD_REGISTRY_SHA256
        cdd_frozen = repository.freeze_version(
            methodology_version_record_id=cdd_imported.methodology_version_record_id,
            expected_source_sha256=_CDD_SHA256,
            expected_registry=cdd,
            frozen_by_actor_id="task6-unit3-reviewer",
            freeze_audit_event_id=str(uuid.uuid4()),
        )
        fdd_frozen = repository.freeze_version(
            methodology_version_record_id=fdd_imported.methodology_version_record_id,
            expected_source_sha256=_FDD_SHA256,
            expected_registry=fdd,
            frozen_by_actor_id="task6-unit3-reviewer",
            freeze_audit_event_id=str(uuid.uuid4()),
        )
        selection = repository.create_run_selection(
            deal_id=deal_id,
            run_id=run_id,
            commercial_dd_version_record_id=cdd_frozen.methodology_version_record_id,
            financial_dd_version_record_id=fdd_frozen.methodology_version_record_id,
            created_by_actor_id="task6-unit3-operator",
            selection_audit_event_id=str(uuid.uuid4()),
        )
        runs = PostgresRunsRepository(connection, tenant_id)
        claimed = runs.claim_queued_runs(
            limit=1,
            lease_owner_id=lease_owner_id,
            lease_seconds=_LEASE_SECONDS,
        )
        assert len(claimed) == 1
        lease_fencing_token = int(claimed[0]["lease_fencing_token"])
        coverage_result, coverage = DurableRunMethodologyCoverageInitService(
            methodology_repository=repository,
            runs_repository=runs,
            unit_of_work=_BoundTransaction(),
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        ).initialize_for_run(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
        assert len(coverage) == _QUESTION_COUNT
        assert len({record.coverage_record_id for record in coverage}) == _QUESTION_COUNT

    with database.app_engine.begin() as connection:
        corpus = load_document_preflight_corpus_for_deal(
            db_conn=connection,
            deal_id=deal_id,
            tenant_id=tenant_id,
        )
    preflight, eligible = InMemoryRunDocumentPreflightService().run(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        corpus=corpus,
    )
    assert len(eligible) == 6
    preflight_summary = preflight.to_run_step_summary()
    serialized_summary = json.dumps(preflight_summary, sort_keys=True)
    assert "evidence_tags" not in serialized_summary
    assert "text_excerpt" not in serialized_summary
    assert all(fixture.text not in serialized_summary for fixture in _document_fixtures())

    preflight_step_id = str(uuid.uuid4())
    coverage_step_id = str(uuid.uuid4())
    planning_step_id = str(uuid.uuid4())
    with database.app_engine.begin() as connection:
        steps = PostgresRunStepsRepository(connection, tenant_id)
        for step in (
            RunStep(
                step_id=preflight_step_id,
                tenant_id=tenant_id,
                run_id=run_id,
                step_name=StepName.DOCUMENT_PREFLIGHT,
                step_order=STEP_ORDER[StepName.DOCUMENT_PREFLIGHT],
                status=StepStatus.COMPLETED,
                result_summary=preflight_summary,
            ),
            RunStep(
                step_id=coverage_step_id,
                tenant_id=tenant_id,
                run_id=run_id,
                step_name=StepName.METHODOLOGY_COVERAGE_INIT,
                step_order=STEP_ORDER[StepName.METHODOLOGY_COVERAGE_INIT],
                status=StepStatus.COMPLETED,
                result_summary=coverage_result.to_run_step_summary(),
            ),
            RunStep(
                step_id=planning_step_id,
                tenant_id=tenant_id,
                run_id=run_id,
                step_name=StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
                step_order=STEP_ORDER[StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING],
                status=StepStatus.RUNNING,
                result_summary={},
            ),
        ):
            steps.create(
                step,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

    return _Scope(
        tenant_id=tenant_id,
        foreign_tenant_id=foreign_tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        lease_owner_id=lease_owner_id,
        lease_fencing_token=lease_fencing_token,
        selection_id=selection.selection_id,
        preflight_step_id=preflight_step_id,
        planning_step_id=planning_step_id,
    )


def _load_preflight_summary(database: DisposablePostgres, scope: _Scope) -> dict[str, Any]:
    with database.app_engine.begin() as connection:
        step = PostgresRunStepsRepository(connection, scope.tenant_id).get_step(
            scope.run_id,
            StepName.DOCUMENT_PREFLIGHT,
        )
    assert step is not None
    assert step.status is StepStatus.COMPLETED
    return step.result_summary


def _reclaim_with_fresh_worker(database: DisposablePostgres, scope: _Scope) -> _Scope:
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE runs SET lease_expires_at=statement_timestamp()-interval '1 second' "
                "WHERE run_id=CAST(:run_id AS uuid)"
            ),
            {"run_id": scope.run_id},
        )
    successor_owner_id = str(uuid.uuid4())
    with database.app_engine.begin() as connection:
        runs = PostgresRunsRepository(connection, scope.tenant_id)
        reclaimed = runs.requeue_expired_worker_leases(limit=1)
        assert len(reclaimed) == 1
        claimed = runs.claim_queued_runs(
            limit=1,
            lease_owner_id=successor_owner_id,
            lease_seconds=_LEASE_SECONDS,
        )
        assert len(claimed) == 1
        successor_token = int(claimed[0]["lease_fencing_token"])
    assert successor_token > scope.lease_fencing_token
    return replace(
        scope,
        lease_owner_id=successor_owner_id,
        lease_fencing_token=successor_token,
    )


def _complete_planning_step(
    database: DisposablePostgres,
    scope: _Scope,
    *,
    planning_summary: dict[str, Any],
    staged_write: Any,
) -> None:
    # This unit proof isolates deterministic Steps 3-6 before the paid Step-7
    # boundary; it uses the same fenced transaction for task rows and step state.
    with database.app_engine.begin() as connection:
        staged_write.persist(
            db_conn=connection,
            lease_owner_id=scope.lease_owner_id,
            lease_fencing_token=scope.lease_fencing_token,
        )
        steps = PostgresRunStepsRepository(connection, scope.tenant_id)
        running = steps.get_step(scope.run_id, StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING)
        assert running is not None
        steps.update(
            running.model_copy(
                update={
                    "status": StepStatus.COMPLETED,
                    "result_summary": planning_summary,
                    "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            ),
            lease_owner_id=scope.lease_owner_id,
            lease_fencing_token=scope.lease_fencing_token,
        )


def _graph_digest(database: DisposablePostgres, scope: _Scope) -> str:
    inventory = (
        (
            "runs",
            "SELECT to_jsonb(row_value)::text FROM runs AS row_value "
            "WHERE run_id=CAST(:run_id AS uuid) ORDER BY run_id",
            {"run_id": scope.run_id},
        ),
        (
            "run_steps",
            "SELECT to_jsonb(row_value)::text FROM run_steps AS row_value "
            "WHERE run_id=CAST(:run_id AS uuid) ORDER BY step_order, step_id",
            {"run_id": scope.run_id},
        ),
        (
            "documents",
            "SELECT to_jsonb(row_value)::text FROM documents AS row_value "
            "WHERE deal_id=CAST(:deal_id AS uuid) ORDER BY document_id",
            {"deal_id": scope.deal_id},
        ),
        (
            "document_spans",
            "SELECT to_jsonb(span_value)::text FROM document_spans AS span_value "
            "JOIN documents AS document "
            "ON document.document_id=span_value.document_id "
            "WHERE document.deal_id=CAST(:deal_id AS uuid) "
            "ORDER BY span_value.document_id, span_value.span_id",
            {"deal_id": scope.deal_id},
        ),
        (
            "methodology_versions",
            "SELECT to_jsonb(row_value)::text FROM methodology_versions AS row_value "
            "WHERE tenant_id=CAST(:tenant_id AS uuid) "
            "ORDER BY methodology_version_record_id",
            {"tenant_id": scope.tenant_id},
        ),
        (
            "methodology_questions",
            "SELECT to_jsonb(row_value)::text FROM methodology_questions AS row_value "
            "WHERE tenant_id=CAST(:tenant_id AS uuid) "
            "ORDER BY methodology_question_record_id",
            {"tenant_id": scope.tenant_id},
        ),
        (
            "run_methodology_selections",
            "SELECT to_jsonb(row_value)::text "
            "FROM run_methodology_selections AS row_value "
            "WHERE run_id=CAST(:run_id AS uuid) ORDER BY selection_id",
            {"run_id": scope.run_id},
        ),
        (
            "methodology_coverage_records",
            "SELECT to_jsonb(row_value)::text "
            "FROM methodology_coverage_records AS row_value "
            "WHERE run_id=CAST(:run_id AS uuid) ORDER BY coverage_record_id",
            {"run_id": scope.run_id},
        ),
        (
            "methodology_extraction_tasks",
            "SELECT to_jsonb(row_value)::text "
            "FROM methodology_extraction_tasks AS row_value "
            "WHERE run_id=CAST(:run_id AS uuid) ORDER BY task_record_id",
            {"run_id": scope.run_id},
        ),
        (
            "methodology_extraction_task_spans",
            "SELECT to_jsonb(row_value)::text "
            "FROM methodology_extraction_task_spans AS row_value "
            "WHERE run_id=CAST(:run_id AS uuid) ORDER BY task_span_record_id",
            {"run_id": scope.run_id},
        ),
    )
    snapshot: list[tuple[str, list[str]]] = []
    with database.admin_engine.connect() as connection:
        for table_name, select_sql, params in inventory:
            rows = connection.execute(text(select_sql), params).scalars()
            snapshot.append((table_name, list(rows)))
    return _sha256_json(snapshot)


def _execute_corruption_statement(
    connection: Any,
    statement: str,
    params: Mapping[str, Any],
    *,
    suspend_triggers: bool,
) -> Any:
    """Run one corruption statement without leaving a pending trigger event.

    Disabling one named trigger and re-enabling it after the write fails at
    0038: a deferred exact-one constraint trigger on the same table queues an event,
    and ``ALTER TABLE ... ENABLE TRIGGER`` then raises ``ObjectInUse``.
    ``SET LOCAL session_replication_role = replica`` suppresses every trigger
    for the statement, including that deferred census, and is set back to
    ``origin`` before the transaction commits. The following production call
    uses a new transaction, so its triggers fire.
    """

    if suspend_triggers:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
    result = connection.execute(text(statement), params)
    if suspend_triggers:
        connection.execute(text("SET LOCAL session_replication_role = origin"))
    return result


def _set_corruption_tenant(connection: Any, params: Mapping[str, Any]) -> None:
    """Give the deferred receipt guard the tenant that owns this mutation.

    Admin SQL sees the row without a tenant setting. The definer-owned receipt
    trigger does not, so the setting is applied before the write and again
    before the restore.
    """

    if "run_id" in params:
        statement = "SELECT tenant_id::text FROM runs WHERE run_id = CAST(:run_id AS uuid)"
    elif "step_id" in params:
        statement = "SELECT tenant_id::text FROM run_steps WHERE step_id = CAST(:step_id AS uuid)"
    elif "deal_id" in params:
        statement = "SELECT tenant_id::text FROM deals WHERE deal_id = CAST(:deal_id AS uuid)"
    else:
        return
    tenant_id = connection.execute(text(statement), params).scalar_one()
    set_seed_tenant(connection, str(tenant_id))


@contextmanager
def _committed_corruption(
    database: DisposablePostgres,
    *,
    trigger_table: str | None,
    trigger_name: str | None,
    select_sql: str,
    mutate_sql: str,
    restore_sql: str,
    params: dict[str, Any],
) -> Iterator[None]:
    old: Any
    suspend_triggers = trigger_name is not None and trigger_table is not None
    with database.admin_engine.begin() as connection:
        _set_corruption_tenant(connection, params)
        old = connection.execute(text(select_sql), params).scalar_one()
        _execute_corruption_statement(
            connection,
            mutate_sql,
            params,
            suspend_triggers=suspend_triggers,
        )
    try:
        yield
    finally:
        restore_value = json.dumps(old) if isinstance(old, (dict, list)) else old
        with database.admin_engine.begin() as connection:
            _set_corruption_tenant(connection, params)
            _execute_corruption_statement(
                connection,
                restore_sql,
                {**params, "old": restore_value},
                suspend_triggers=suspend_triggers,
            )


def _assert_restart_fails_without_repair(
    database: DisposablePostgres,
    scope: _Scope,
    rehydrate: Callable[..., Any],
) -> None:
    corrupt_digest = _graph_digest(database, scope)
    with pytest.raises(RuntimeError, match="METHODOLOGY_TASK_COVERAGE_INCOMPLETE"):
        rehydrate(tenant_id=scope.tenant_id, deal_id=scope.deal_id, run_id=scope.run_id)
    assert _graph_digest(database, scope) == corrupt_digest


@requires_operator_workbook
def test_graph_digest_covers_every_restart_input_surface(
    record_property: pytest.RecordProperty,
) -> None:
    """A full-row mutation on any restart input must change the graph digest."""
    fdd_path = _operator_fdd_path(record_property)
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        scope = _seed_scope(database, fdd_path=fdd_path)
        baseline_digest = _graph_digest(database, scope)
        mutations = (
            {
                "surface": "runs",
                "select_sql": ("SELECT mode FROM runs WHERE run_id=CAST(:run_id AS uuid)"),
                "mutate_sql": (
                    "UPDATE runs SET mode='SNAPSHOT' WHERE run_id=CAST(:run_id AS uuid)"
                ),
                "restore_sql": ("UPDATE runs SET mode=:old WHERE run_id=CAST(:run_id AS uuid)"),
                "params": {"run_id": scope.run_id},
            },
            {
                "surface": "run_steps",
                "select_sql": (
                    "SELECT retry_count FROM run_steps WHERE step_id=CAST(:step_id AS uuid)"
                ),
                "mutate_sql": (
                    "UPDATE run_steps SET retry_count=retry_count+1 "
                    "WHERE step_id=CAST(:step_id AS uuid)"
                ),
                "restore_sql": (
                    "UPDATE run_steps SET retry_count=:old WHERE step_id=CAST(:step_id AS uuid)"
                ),
                "params": {"step_id": scope.preflight_step_id},
            },
            {
                "surface": "documents",
                "select_sql": (
                    "SELECT parse_status FROM documents WHERE document_id=("
                    "SELECT document_id FROM documents "
                    "WHERE deal_id=CAST(:deal_id AS uuid) ORDER BY document_id LIMIT 1)"
                ),
                "mutate_sql": (
                    "UPDATE documents SET parse_status='PENDING' WHERE document_id=("
                    "SELECT document_id FROM documents "
                    "WHERE deal_id=CAST(:deal_id AS uuid) ORDER BY document_id LIMIT 1)"
                ),
                "restore_sql": (
                    "UPDATE documents SET parse_status=:old WHERE document_id=("
                    "SELECT document_id FROM documents "
                    "WHERE deal_id=CAST(:deal_id AS uuid) ORDER BY document_id LIMIT 1)"
                ),
                "params": {"deal_id": scope.deal_id},
            },
            {
                "surface": "document_spans",
                "select_sql": (
                    "SELECT span.text_excerpt FROM document_spans AS span "
                    "JOIN documents AS document "
                    "ON document.document_id=span.document_id "
                    "WHERE document.deal_id=CAST(:deal_id AS uuid) "
                    "ORDER BY span.span_id LIMIT 1"
                ),
                "mutate_sql": (
                    "UPDATE document_spans SET text_excerpt=text_excerpt || ' digest probe' "
                    "WHERE span_id=(SELECT span.span_id FROM document_spans AS span "
                    "JOIN documents AS document "
                    "ON document.document_id=span.document_id "
                    "WHERE document.deal_id=CAST(:deal_id AS uuid) "
                    "ORDER BY span.span_id LIMIT 1)"
                ),
                "restore_sql": (
                    "UPDATE document_spans SET text_excerpt=:old "
                    "WHERE span_id=(SELECT span.span_id FROM document_spans AS span "
                    "JOIN documents AS document "
                    "ON document.document_id=span.document_id "
                    "WHERE document.deal_id=CAST(:deal_id AS uuid) "
                    "ORDER BY span.span_id LIMIT 1)"
                ),
                "params": {"deal_id": scope.deal_id},
            },
        )
        missed_surfaces: list[str] = []
        for mutation in mutations:
            with _committed_corruption(
                database,
                trigger_table=None,
                trigger_name=None,
                select_sql=mutation["select_sql"],
                mutate_sql=mutation["mutate_sql"],
                restore_sql=mutation["restore_sql"],
                params=mutation["params"],
            ):
                if _graph_digest(database, scope) == baseline_digest:
                    missed_surfaces.append(mutation["surface"])
            assert _graph_digest(database, scope) == baseline_digest

        assert missed_surfaces == []


@requires_operator_workbook
def test_genuine_selected_pair_composes_112_tasks_and_restarts_from_recomputed_state(
    monkeypatch: pytest.MonkeyPatch,
    record_property: pytest.RecordProperty,
) -> None:
    """Prove actual 21+79 sources and six real preflight classifications end to end."""
    fdd_path = _operator_fdd_path(record_property)
    fdd_before = _source_identity(fdd_path)
    assert fdd_before.sha256 == _FDD_SHA256
    provider_dispatches = 0

    def provider_execution_forbidden(**_kwargs: Any) -> Any:
        nonlocal provider_dispatches
        provider_dispatches += 1
        raise AssertionError("Unit3 must stop before provider execution")

    monkeypatch.setattr(
        worker_module,
        "_execute_postgres_methodology_tasks",
        provider_execution_forbidden,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        scope = _seed_scope(database, fdd_path=fdd_path)
        with database.app_engine.begin() as connection:
            bind_frozen_run_source(
                connection,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                run_id=scope.run_id,
                document_ids=tuple(item.document_id for item in _document_fixtures()),
            )
        callbacks = build_postgres_methodology_extraction_callbacks(
            connection_factory=database.app_engine.begin,
            tenant_id=scope.tenant_id,
            lease_owner_id=scope.lease_owner_id,
            lease_fencing_token=scope.lease_fencing_token,
            audit_sink=object(),
            strict_profile=None,
        )
        plan = callbacks["methodology_extraction_task_planning_fn"]
        stale_rehydrate = plan.rehydrate  # type: ignore[attr-defined]
        planning_result, tasks, staged_write = plan(
            tenant_id=scope.tenant_id,
            deal_id=scope.deal_id,
            run_id=scope.run_id,
            run_step_id=scope.planning_step_id,
            document_preflight_summary=_load_preflight_summary(database, scope),
        )

        assert len(tasks) == _TASK_COUNT
        assert len({task.extraction_task_id for task in tasks}) == _TASK_COUNT
        assert len({task.methodology_question_id for task in tasks}) == _QUESTION_COUNT
        assert all(task.status is ExtractionTaskStatus.READY for task in tasks)
        assert Counter(task.methodology_type for task in tasks) == {
            MethodologyType.COMMERCIAL_DD: 33,
            MethodologyType.FINANCIAL_DD: 79,
        }
        assert planning_result.summary.by_status == {"ready": _TASK_COUNT}
        _complete_planning_step(
            database,
            scope,
            planning_summary=planning_result.to_run_step_summary(),
            staged_write=staged_write,
        )

        category_map = load_commercial_dd_question_category_map()
        assert category_map.classifier_version == CDD_DOCUMENT_CLASSIFIER_VERSION
        assert (
            category_map.classifier_reachable_categories_sha256
            == CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES_SHA256
        )
        for task in tasks:
            admission = task.strict_admission
            assert admission is not None
            assert admission.contract_version == "strict-category-span-admission-v2"
            assert admission.cdd_category_mapping_version == category_map.mapping_version
            assert admission.cdd_category_mapping_sha256 == category_map.canonical_payload_sha256
            assert admission.classifier_contract_version == CDD_DOCUMENT_CLASSIFIER_VERSION
            _assert_safe_canonical_equal(
                task.required_evidence,
                task.expected_answer_schema.required_evidence,
                safe_id=f"{task.extraction_task_id}:required_evidence",
            )
            _assert_safe_canonical_equal(
                task.validation_requirements,
                task.expected_answer_schema.validation_requirements,
                safe_id=f"{task.extraction_task_id}:validation_requirements",
            )
            assert len(task.source_spans) == 1
            assert task.source_spans[0].text_excerpt is not None
            assert (
                hashlib.sha256(task.source_spans[0].text_excerpt.encode("utf-8")).hexdigest()
                == task.source_spans[0].content_hash
            )

        management = next(
            task
            for task in tasks
            if task.methodology_question_id == "mq_commercial_dd_management_0001"
        )
        assert management.strict_admission is not None
        assert management.strict_admission.matched_category == "management_team"
        assert (
            management.strict_admission.classified_primary_cdd_category.value
            == "business_plan_assumptions"
        )
        assert "management_team" in {
            category.value
            for category in management.strict_admission.classified_secondary_cdd_categories
        }

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, scope.tenant_id)
            counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM methodology_coverage_records
                       WHERE run_id=CAST(:run_id AS uuid)) AS coverage_count,
                      (SELECT count(*) FROM methodology_extraction_tasks
                       WHERE run_id=CAST(:run_id AS uuid)) AS task_count,
                      (SELECT count(*) FROM methodology_extraction_task_spans
                       WHERE run_id=CAST(:run_id AS uuid)) AS span_count
                    """
                ),
                {"run_id": scope.run_id},
            ).one()
            durable_payloads = (
                connection.execute(
                    text(
                        """
                    SELECT t.extraction_task_id, t.task_payload,
                           q.question_payload
                    FROM methodology_extraction_tasks t
                    JOIN methodology_questions q
                      ON q.tenant_id=t.tenant_id
                     AND q.methodology_question_record_id=t.methodology_question_record_id
                    WHERE t.run_id=CAST(:run_id AS uuid)
                    ORDER BY t.extraction_task_id
                    """
                    ),
                    {"run_id": scope.run_id},
                )
                .mappings()
                .all()
            )
        assert tuple(counts) == (_QUESTION_COUNT, _TASK_COUNT, _TASK_COUNT)
        expected_payload_sha256 = {
            str(task.extraction_task_id): _sha256_json(task.model_dump(mode="json"))
            for task in tasks
        }
        assert len(durable_payloads) == _TASK_COUNT
        for row in durable_payloads:
            task_id = str(row["extraction_task_id"])
            assert task_id in expected_payload_sha256
            _assert_safe_sha256_equal(
                _sha256_json(row["task_payload"]),
                expected_payload_sha256[task_id],
                safe_id=f"{task_id}:task_payload",
            )
            _assert_safe_utf8_equal(
                row["task_payload"]["expected_answer_schema"]["question_text"],
                row["question_payload"]["question_text"],
                safe_id=f"{task_id}:question_text",
            )
            _assert_safe_canonical_equal(
                row["task_payload"]["required_evidence"],
                row["question_payload"]["required_evidence"],
                safe_id=f"{task_id}:required_evidence",
            )
            _assert_safe_canonical_equal(
                row["task_payload"]["validation_requirements"],
                row["question_payload"]["validation_requirements"],
                safe_id=f"{task_id}:validation_requirements",
            )
            source_trace = row["question_payload"]["source_trace"]
            assert source_trace["source_hash"] in {_CDD_SHA256, _FDD_SHA256}
            assert source_trace["sheet_or_section"]
            if source_trace["source_type"] == "operator_workbook":
                assert source_trace["row_number"] > 0
                assert source_trace["source_anchor_cell"]

        scope = _reclaim_with_fresh_worker(database, scope)
        stale_digest = _graph_digest(database, scope)
        with pytest.raises(WorkerLeaseFencedError) as fenced:
            stale_rehydrate(
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                run_id=scope.run_id,
            )
        assert fenced.value.code == "WORKER_LEASE_FENCED"
        assert _graph_digest(database, scope) == stale_digest
        restart_callbacks = build_postgres_methodology_extraction_callbacks(
            connection_factory=database.app_engine.begin,
            tenant_id=scope.tenant_id,
            lease_owner_id=scope.lease_owner_id,
            lease_fencing_token=scope.lease_fencing_token,
            audit_sink=object(),
            strict_profile=None,
        )
        rehydrate = restart_callbacks["methodology_extraction_task_planning_fn"].rehydrate  # type: ignore[attr-defined]
        before_happy_restart = _graph_digest(database, scope)
        resumed = rehydrate(
            tenant_id=scope.tenant_id,
            deal_id=scope.deal_id,
            run_id=scope.run_id,
        )
        assert _graph_digest(database, scope) == before_happy_restart
        assert len(resumed) == len(tasks)
        for position, (resumed_task, original_task) in enumerate(zip(resumed, tasks, strict=True)):
            resumed_id = str(resumed_task.extraction_task_id)
            original_id = str(original_task.extraction_task_id)
            assert (position, resumed_id) == (position, original_id)
            _assert_safe_utf8_equal(
                resumed_task.to_deterministic_json(),
                original_task.to_deterministic_json(),
                safe_id=f"{original_id}:resumed_task_json",
            )

        tenant_tables = (
            "documents",
            "document_spans",
            "methodology_versions",
            "methodology_questions",
            "run_methodology_selections",
            "methodology_coverage_records",
            "methodology_extraction_tasks",
            "methodology_extraction_task_spans",
        )
        with database.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
                {"tenant_id": scope.foreign_tenant_id},
            )
            assert all(
                connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one() == 0
                for table_name in tenant_tables
            )

        baseline_digest = _graph_digest(database, scope)

        with _committed_corruption(
            database,
            trigger_table="run_methodology_selections",
            trigger_name="trg_run_methodology_selections_guard",
            select_sql=(
                "SELECT selection_hash FROM run_methodology_selections "
                "WHERE selection_id=CAST(:selection_id AS uuid)"
            ),
            mutate_sql=(
                "UPDATE run_methodology_selections SET selection_hash="
                "'0000000000000000000000000000000000000000000000000000000000000000' "
                "WHERE selection_id=CAST(:selection_id AS uuid)"
            ),
            restore_sql=(
                "UPDATE run_methodology_selections SET selection_hash=:old "
                "WHERE selection_id=CAST(:selection_id AS uuid)"
            ),
            params={"selection_id": scope.selection_id},
        ):
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        assert _graph_digest(database, scope) == baseline_digest

        with database.admin_engine.connect() as connection:
            question_record_id = str(
                connection.execute(
                    text(
                        "SELECT methodology_question_record_id FROM methodology_questions "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                        "ORDER BY methodology_question_record_id LIMIT 1"
                    ),
                    {"tenant_id": scope.tenant_id},
                ).scalar_one()
            )
        with _committed_corruption(
            database,
            trigger_table="methodology_questions",
            trigger_name="trg_methodology_questions_immutable",
            select_sql=(
                "SELECT question_payload FROM methodology_questions "
                "WHERE methodology_question_record_id=CAST(:record_id AS uuid)"
            ),
            mutate_sql=(
                "UPDATE methodology_questions SET question_payload="
                "jsonb_set(question_payload,'{question_text}',"
                "to_jsonb('Corrupted frozen question'::text)) "
                "WHERE methodology_question_record_id=CAST(:record_id AS uuid)"
            ),
            restore_sql=(
                "UPDATE methodology_questions SET question_payload=CAST(:old AS jsonb) "
                "WHERE methodology_question_record_id=CAST(:record_id AS uuid)"
            ),
            params={"record_id": question_record_id},
        ):
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        assert _graph_digest(database, scope) == baseline_digest

        with database.admin_engine.connect() as connection:
            coverage_record_id = str(
                connection.execute(
                    text(
                        "SELECT coverage_record_id FROM methodology_coverage_records "
                        "WHERE run_id=CAST(:run_id AS uuid) ORDER BY coverage_record_id LIMIT 1"
                    ),
                    {"run_id": scope.run_id},
                ).scalar_one()
            )
        with _committed_corruption(
            database,
            trigger_table="methodology_coverage_records",
            trigger_name="trg_methodology_coverage_scope",
            select_sql=(
                "SELECT jsonb_build_object("
                "'status', status, 'reason_code', reason_code, 'updated_at', updated_at) "
                "FROM methodology_coverage_records "
                "WHERE coverage_record_id=:record_id"
            ),
            mutate_sql=(
                "UPDATE methodology_coverage_records "
                "SET status='blocked', reason_code='TEST_CORRUPTION' "
                "WHERE coverage_record_id=:record_id"
            ),
            restore_sql=(
                "UPDATE methodology_coverage_records SET "
                "status=CAST(:old AS jsonb)->>'status', "
                "reason_code=CAST(:old AS jsonb)->>'reason_code', "
                "updated_at=(CAST(:old AS jsonb)->>'updated_at')::timestamptz "
                "WHERE coverage_record_id=:record_id"
            ),
            params={"record_id": coverage_record_id},
        ):
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        assert _graph_digest(database, scope) == baseline_digest

        with database.admin_engine.connect() as connection:
            task_record_id = str(
                connection.execute(
                    text(
                        "SELECT task_record_id FROM methodology_extraction_tasks "
                        "WHERE run_id=CAST(:run_id AS uuid) ORDER BY task_record_id LIMIT 1"
                    ),
                    {"run_id": scope.run_id},
                ).scalar_one()
            )
        with _committed_corruption(
            database,
            trigger_table="methodology_extraction_tasks",
            trigger_name="trg_methodology_extraction_tasks_immutable",
            select_sql=(
                "SELECT task_payload FROM methodology_extraction_tasks "
                "WHERE task_record_id=CAST(:record_id AS uuid)"
            ),
            mutate_sql=(
                "UPDATE methodology_extraction_tasks SET task_payload="
                "jsonb_set(task_payload,'{reason_codes}','[\"corrupt\"]'::jsonb) "
                "WHERE task_record_id=CAST(:record_id AS uuid)"
            ),
            restore_sql=(
                "UPDATE methodology_extraction_tasks SET task_payload=CAST(:old AS jsonb) "
                "WHERE task_record_id=CAST(:record_id AS uuid)"
            ),
            params={"record_id": task_record_id},
        ):
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        assert _graph_digest(database, scope) == baseline_digest

        with database.admin_engine.connect() as connection:
            task_span_record_id = str(
                connection.execute(
                    text(
                        "SELECT task_span_record_id FROM methodology_extraction_task_spans "
                        "WHERE run_id=CAST(:run_id AS uuid) "
                        "ORDER BY task_span_record_id LIMIT 1"
                    ),
                    {"run_id": scope.run_id},
                ).scalar_one()
            )
        with _committed_corruption(
            database,
            trigger_table="methodology_extraction_task_spans",
            trigger_name="trg_methodology_extraction_task_spans_immutable",
            select_sql=(
                "SELECT content_sha256 FROM methodology_extraction_task_spans "
                "WHERE task_span_record_id=CAST(:record_id AS uuid)"
            ),
            mutate_sql=(
                "UPDATE methodology_extraction_task_spans SET content_sha256="
                "'0000000000000000000000000000000000000000000000000000000000000000' "
                "WHERE task_span_record_id=CAST(:record_id AS uuid)"
            ),
            restore_sql=(
                "UPDATE methodology_extraction_task_spans SET content_sha256=:old "
                "WHERE task_span_record_id=CAST(:record_id AS uuid)"
            ),
            params={"record_id": task_span_record_id},
        ):
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        assert _graph_digest(database, scope) == baseline_digest

        with database.admin_engine.begin() as connection:
            preflight_summary = connection.execute(
                text("SELECT result_summary FROM run_steps WHERE step_id=CAST(:step_id AS uuid)"),
                {"step_id": scope.preflight_step_id},
            ).scalar_one()
            original_preflight_summary = json.loads(json.dumps(preflight_summary))
            financial = next(
                item
                for item in preflight_summary["classifications"]
                if item["cdd_category"] == "business_plan_assumptions"
            )
            financial["secondary_cdd_categories"].remove("management_team")
            corrupt_durable_rows_out_of_band(
                connection,
                (
                    "UPDATE run_steps SET result_summary=CAST(:summary AS jsonb) "
                    "WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {
                    "step_id": scope.preflight_step_id,
                    "summary": json.dumps(preflight_summary),
                },
                table="run_steps",
            )
        try:
            assert _graph_digest(database, scope) != baseline_digest
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        finally:
            with database.admin_engine.begin() as connection:
                corrupt_durable_rows_out_of_band(
                    connection,
                    (
                        "UPDATE run_steps SET result_summary=CAST(:summary AS jsonb) "
                        "WHERE step_id=CAST(:step_id AS uuid)"
                    ),
                    {
                        "step_id": scope.preflight_step_id,
                        "summary": json.dumps(original_preflight_summary),
                    },
                    table="run_steps",
                )
        assert _graph_digest(database, scope) == baseline_digest

        with database.admin_engine.begin() as connection:
            original_planning_summary = connection.execute(
                text("SELECT result_summary FROM run_steps WHERE step_id=CAST(:step_id AS uuid)"),
                {"step_id": scope.planning_step_id},
            ).scalar_one()
            corrupted_planning_summary = dict(original_planning_summary)
            corrupted_planning_summary["task_ids"] = list(
                reversed(corrupted_planning_summary["task_ids"])
            )
            corrupt_durable_rows_out_of_band(
                connection,
                (
                    "UPDATE run_steps SET result_summary=CAST(:summary AS jsonb) "
                    "WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {
                    "step_id": scope.planning_step_id,
                    "summary": json.dumps(corrupted_planning_summary),
                },
                table="run_steps",
            )
        try:
            assert _graph_digest(database, scope) != baseline_digest
            _assert_restart_fails_without_repair(database, scope, rehydrate)
        finally:
            with database.admin_engine.begin() as connection:
                corrupt_durable_rows_out_of_band(
                    connection,
                    (
                        "UPDATE run_steps SET result_summary=CAST(:summary AS jsonb) "
                        "WHERE step_id=CAST(:step_id AS uuid)"
                    ),
                    {
                        "step_id": scope.planning_step_id,
                        "summary": json.dumps(original_planning_summary),
                    },
                    table="run_steps",
                )
        assert _graph_digest(database, scope) == baseline_digest

        assert provider_dispatches == 0

    assert _source_identity(fdd_path) == fdd_before
