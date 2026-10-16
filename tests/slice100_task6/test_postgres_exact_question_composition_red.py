"""RED proof that frozen question bytes survive the durable Task-6 composition."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from idis.methodology.models import MethodologyRegistry, MethodologyType
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.models.methodology_extraction import (
    MethodologyPromptArtifact,
    exact_canonical_json_bytes,
)
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodologies import (
    PostgresMethodologiesRepository,
    methodology_source_trace_digest,
)
from idis.persistence.repositories.methodology_extraction import (
    PostgresMethodologyExtractionRepository,
)
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.services.extraction.methodology_task_extractor import (
    ANTHROPIC_STANDARD_PRICE_CATALOG,
    AnthropicMethodologyTaskExtractor,
)
from idis.services.runs.methodology_coverage_init import (
    DurableRunMethodologyCoverageInitService,
)
from idis.services.runs.methodology_postgres_runtime import (
    PostgresMethodologyExecutionBoundary,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task5.test_durable_selection_coverage_red import _registry

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
LEASE_OWNER_ID = "44444444-4444-4444-8444-444444444444"
DOCUMENT_ID = "55555555-5555-4555-8555-555555555555"
ARTIFACT_ID = "66666666-6666-4666-8666-666666666666"
SPAN_ID = "77777777-7777-4777-8777-777777777777"
SECOND_SPAN_ID = "88888888-8888-4888-8888-888888888888"
EXACT_QUESTION = (
    " \tWhat is the fictional recurring revenue — café?\r\n\r\n"
    "Retain the frozen question Ω exactly. \r\n"
)
EXACT_SOURCE = " \tFictional admitted source — café.\r\n\r\nSecond line Ω. \r\n"
SECOND_EXACT_SOURCE = "Fictional admitted source on page two."


class _UnitOfWork:
    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield


class _NoDispatchTransport:
    backend = "hermetic"

    def create_message(self, **_kwargs: Any) -> Any:
        raise AssertionError("prepare must not invoke the lower transport")


class _FailIfDispatchedTransport:
    backend = "hermetic"

    def __init__(self) -> None:
        self.calls = 0

    def create_message(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("source authorization must fail before transport dispatch")


def _registry_with_exact_question() -> tuple[MethodologyRegistry, str]:
    payload = _registry(MethodologyType.COMMERCIAL_DD, 21).model_dump(mode="json")
    question_id = str(payload["versions"][0]["questions"][0]["methodology_question_id"])
    payload["versions"][0]["questions"][0]["question_text"] = EXACT_QUESTION
    return MethodologyRegistry.model_validate(payload), question_id


def _financial_registry_with_physical_workbook_trace() -> MethodologyRegistry:
    payload = _registry(MethodologyType.FINANCIAL_DD, 79).model_dump(mode="json")
    for question in payload["versions"][0]["questions"]:
        row_number = question["source_trace"]["row_number"]
        assert isinstance(row_number, int) and row_number > 0
        question["source_trace"]["source_anchor_cell"] = f"E{row_number}"
    return MethodologyRegistry.model_validate(payload)


def _seed_real_durable_plan(
    database: DisposablePostgres,
    *,
    include_second_span: bool = False,
) -> tuple[list[ExtractionTask], str]:
    cdd, exact_question_id = _registry_with_exact_question()
    fdd = _financial_registry_with_physical_workbook_trace()
    source_hash = hashlib.sha256(EXACT_SOURCE.encode("utf-8")).hexdigest()
    second_source_hash = hashlib.sha256(SECOND_EXACT_SOURCE.encode("utf-8")).hexdigest()
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) VALUES "
                "(CAST(:tenant AS uuid),'Exact question tenant')"
            ),
            {"tenant": TENANT_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO deals (deal_id,tenant_id,name,created_at)
                VALUES (CAST(:deal AS uuid),CAST(:tenant AS uuid),'Fictional deal',now());
                INSERT INTO runs (run_id,tenant_id,deal_id,mode,status,created_at)
                VALUES (CAST(:run AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                        'FULL','QUEUED',now());
                INSERT INTO document_artifacts (
                    doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,sha256
                ) VALUES (
                    CAST(:artifact AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                    'DATA_ROOM_FILE','Fictional','fixture','v1',:source_hash
                );
                INSERT INTO documents (
                    document_id,tenant_id,deal_id,doc_id,doc_type,parse_status
                ) VALUES (
                    CAST(:document AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                    CAST(:artifact AS uuid),'PDF','PARSED'
                );
                INSERT INTO document_spans (
                    span_id,tenant_id,deal_id,document_id,span_type,
                    locator,text_excerpt,content_hash
                ) VALUES (
                    CAST(:span AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                    CAST(:document AS uuid),'PAGE_TEXT',
                    jsonb_build_object('page',1),:source_text,:source_hash
                )
                """
            ),
            {
                "tenant": TENANT_ID,
                "deal": DEAL_ID,
                "run": RUN_ID,
                "artifact": ARTIFACT_ID,
                "document": DOCUMENT_ID,
                "span": SPAN_ID,
                "source_text": EXACT_SOURCE,
                "source_hash": source_hash,
            },
        )
        if include_second_span:
            connection.execute(
                text(
                    """
                    INSERT INTO document_spans (
                        span_id,tenant_id,deal_id,document_id,span_type,
                        locator,text_excerpt,content_hash
                    ) VALUES (
                        CAST(:span AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                        CAST(:document AS uuid),'PAGE_TEXT',
                        jsonb_build_object('page',2),:source_text,:source_hash
                    )
                    """
                ),
                {
                    "tenant": TENANT_ID,
                    "deal": DEAL_ID,
                    "document": DOCUMENT_ID,
                    "span": SECOND_SPAN_ID,
                    "source_text": SECOND_EXACT_SOURCE,
                    "source_hash": second_source_hash,
                },
            )

    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        methodologies = PostgresMethodologiesRepository(connection, TENANT_ID)
        frozen = []
        for registry in (cdd, fdd):
            imported = methodologies.import_registry(
                registry=registry,
                source_type=registry.current_version.questions[0].source_trace.source_type,
                source_alias="configured-source",
                source_byte_count=1,
                source_contract_hash="a" * 64,
                source_trace_digest=methodology_source_trace_digest(registry),
                imported_by_actor_id="exact-question-test",
                import_audit_event_id=str(uuid.uuid4()),
            )
            frozen.append(
                methodologies.freeze_version(
                    methodology_version_record_id=imported.methodology_version_record_id,
                    expected_source_sha256=registry.current_version.source_hash,
                    frozen_by_actor_id="exact-question-test",
                    freeze_audit_event_id=str(uuid.uuid4()),
                    expected_registry=registry,
                )
            )
        selection = methodologies.create_run_selection(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            commercial_dd_version_record_id=frozen[0].methodology_version_record_id,
            financial_dd_version_record_id=frozen[1].methodology_version_record_id,
            created_by_actor_id="exact-question-test",
            selection_audit_event_id=str(uuid.uuid4()),
        )
        runs = PostgresRunsRepository(connection, TENANT_ID)
        assert (
            len(
                runs.claim_queued_runs(
                    limit=1,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_seconds=3600,
                )
            )
            == 1
        )
        _result, coverage = DurableRunMethodologyCoverageInitService(
            methodology_repository=methodologies,
            runs_repository=runs,
            unit_of_work=_UnitOfWork(),
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        ).initialize_for_run(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)
        assert len(coverage) == 100

        question_by_key = {
            (question.methodology_version_id, question.methodology_question_id): question
            for version in frozen
            for question in version.registry.current_version.questions
        }
        span = SourceSpanReference(
            document_id=DOCUMENT_ID,
            span_id=SPAN_ID,
            locator={"page": 1},
            span_type="PAGE_TEXT",
            content_hash=source_hash,
            text_excerpt=EXACT_SOURCE,
        )
        source_spans = [span]
        if include_second_span:
            source_spans.append(
                SourceSpanReference(
                    document_id=DOCUMENT_ID,
                    span_id=SECOND_SPAN_ID,
                    locator={"page": 2},
                    span_type="PAGE_TEXT",
                    content_hash=second_source_hash,
                    text_excerpt=SECOND_EXACT_SOURCE,
                )
            )
        tasks: list[ExtractionTask] = []
        for item in coverage:
            question = question_by_key[(item.methodology_version_id, item.methodology_question_id)]
            tasks.append(
                ExtractionTask(
                    tenant_id=TENANT_ID,
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    status=ExtractionTaskStatus.READY,
                    reason_codes=["SELECTED_APPLICABLE"],
                    methodology_id=question.methodology_id,
                    methodology_version_id=question.methodology_version_id,
                    methodology_question_id=question.methodology_question_id,
                    methodology_type=question.methodology_type,
                    methodology_section=question.section,
                    coverage_record_id=item.coverage_record_id,
                    document_id=DOCUMENT_ID,
                    classification_id="dc_fictional_exact_question",
                    source_spans=source_spans,
                    required_evidence=list(question.required_evidence),
                    expected_answer_schema=ExpectedAnswerSchema(
                        answer_type="narrative",
                        question_text=question.question_text,
                        required_evidence=list(question.required_evidence),
                        required_calculations=list(question.required_calculations),
                        validation_requirements=list(question.validation_requirements),
                        report_section=question.report_mapping.report_section,
                        report_subsection=question.report_mapping.report_subsection,
                        methodology_type=question.methodology_type,
                        methodology_section=question.section,
                    ),
                    validation_requirements=list(question.validation_requirements),
                )
            )
        PostgresMethodologyExtractionRepository(connection, TENANT_ID).persist_planned_tasks(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            selection_id=selection.selection_id,
            tasks=tasks,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
    return tasks, exact_question_id


def _checked_in_prompt() -> MethodologyPromptArtifact:
    root = Path(__file__).resolve().parents[2]
    content = (root / "prompts/methodology_question_extraction/1.0.0/prompt.md").read_bytes()
    input_schema = json.loads(
        (root / "schemas/extraction/methodology_question_extraction_input.json").read_text(
            encoding="utf-8"
        )
    )
    output_schema = json.loads(
        (root / "schemas/extraction/methodology_question_extraction_output.json").read_text(
            encoding="utf-8"
        )
    )
    return MethodologyPromptArtifact(
        prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
        version="1.0.0",
        status="DRAFT",
        content=content.decode("utf-8"),
        content_sha256=hashlib.sha256(content).hexdigest(),
        input_schema=input_schema,
        input_schema_sha256=hashlib.sha256(exact_canonical_json_bytes(input_schema)).hexdigest(),
        output_schema=output_schema,
        output_schema_sha256=hashlib.sha256(exact_canonical_json_bytes(output_schema)).hexdigest(),
    )


def test_durable_repository_boundary_and_provider_wire_preserve_frozen_question_bytes() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        tasks, exact_question_id = _seed_real_durable_plan(database)
        assert all(not hasattr(task, "required_calculations") for task in tasks)
        assert all(task.expected_answer_schema.required_calculations == [] for task in tasks)
        boundary = object.__new__(PostgresMethodologyExecutionBoundary)
        boundary._connection_factory = database.app_engine.connect
        boundary._tenant_id = TENANT_ID
        boundary._execution_inputs = ()

        inputs = boundary.load_execution_inputs(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            tasks=tasks,
            documents=[],
        )
        execution_input = next(
            item for item in inputs if item.request.methodology_question_id == exact_question_id
        )
        extractor = AnthropicMethodologyTaskExtractor(
            transport=_NoDispatchTransport(),
            prompt_artifact=_checked_in_prompt(),
            configured_model="claude-sonnet-4-20250514",
            max_tokens=512,
            temperature="0",
            price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
        )
        prepared = extractor.prepare(
            execution_input.request,
            budget_policy_id="a" * 64,
        )
        provider_document = json.loads(prepared.wire_request.messages[0]["content"][0]["text"])

        assert execution_input.request.question_text == EXACT_QUESTION
        assert execution_input.request.expected_answer_schema["question_text"] == EXACT_QUESTION
        assert provider_document["question_text"] == EXACT_QUESTION
        assert provider_document["expected_answer_schema"]["question_text"] == EXACT_QUESTION
        assert prepared.billable_payload == prepared.wire_request.exact_payload()
        assert hashlib.sha256(prepared.billable_payload).hexdigest() == (
            prepared.reservation.billable_payload_sha256
        )


def test_durable_source_text_hash_mismatch_fails_before_hermetic_transport() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        tasks, _exact_question_id = _seed_real_durable_plan(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE document_spans
                    SET content_hash = :mismatched_hash
                    WHERE span_id = CAST(:span_id AS uuid)
                    """
                ),
                {"mismatched_hash": "9" * 64, "span_id": SPAN_ID},
            )

        boundary = object.__new__(PostgresMethodologyExecutionBoundary)
        boundary._connection_factory = database.app_engine.connect
        boundary._tenant_id = TENANT_ID
        boundary._execution_inputs = ()
        transport = _FailIfDispatchedTransport()
        extractor = AnthropicMethodologyTaskExtractor(
            transport=transport,
            prompt_artifact=_checked_in_prompt(),
            configured_model="claude-sonnet-4-20250514",
            max_tokens=512,
            temperature="0",
            price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
        )

        with pytest.raises(RuntimeError, match="METHODOLOGY_SOURCE_LOCATOR_REJECTED"):
            inputs = boundary.load_execution_inputs(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                tasks=tasks,
                documents=[],
            )
            extractor.extract(inputs[0].request)

        assert transport.calls == 0


@pytest.mark.parametrize("mutation", ("text_excerpt", "locator"))
def test_live_source_text_or_locator_drift_uses_source_locator_code_before_transport(
    mutation: str,
) -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        tasks, _exact_question_id = _seed_real_durable_plan(database)
        with database.admin_engine.begin() as connection:
            if mutation == "text_excerpt":
                connection.execute(
                    text(
                        """
                        UPDATE document_spans
                        SET text_excerpt = :drifted_text
                        WHERE span_id = CAST(:span_id AS uuid)
                        """
                    ),
                    {"drifted_text": "Drifted fictional source", "span_id": SPAN_ID},
                )
            else:
                connection.execute(
                    text(
                        """
                        UPDATE document_spans
                        SET locator = jsonb_build_object('page', 2)
                        WHERE span_id = CAST(:span_id AS uuid)
                        """
                    ),
                    {"span_id": SPAN_ID},
                )

        boundary = object.__new__(PostgresMethodologyExecutionBoundary)
        boundary._connection_factory = database.app_engine.connect
        boundary._tenant_id = TENANT_ID
        boundary._execution_inputs = ()
        transport = _FailIfDispatchedTransport()
        extractor = AnthropicMethodologyTaskExtractor(
            transport=transport,
            prompt_artifact=_checked_in_prompt(),
            configured_model="claude-sonnet-4-20250514",
            max_tokens=512,
            temperature="0",
            price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
        )

        with pytest.raises(RuntimeError, match="METHODOLOGY_SOURCE_LOCATOR_REJECTED"):
            inputs = boundary.load_execution_inputs(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                tasks=tasks,
                documents=[],
            )
            extractor.extract(inputs[0].request)

        assert transport.calls == 0


def test_all_durable_span_rows_are_validated_before_any_live_source_identity() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        tasks, _exact_question_id = _seed_real_durable_plan(
            database,
            include_second_span=True,
        )
        with database.admin_engine.begin() as connection:
            later_span_record_id = connection.execute(
                text(
                    """
                    SELECT s.task_span_record_id
                    FROM methodology_extraction_task_spans s
                    JOIN methodology_extraction_tasks t
                      ON t.task_record_id = s.task_record_id
                     AND t.tenant_id = s.tenant_id
                     AND t.deal_id = s.deal_id
                     AND t.run_id = s.run_id
                    WHERE s.tenant_id = CAST(:tenant_id AS uuid)
                      AND s.deal_id = CAST(:deal_id AS uuid)
                      AND s.run_id = CAST(:run_id AS uuid)
                      AND s.span_ordinal = 2
                    ORDER BY t.methodology_question_id, t.document_id,
                             t.extraction_task_id, s.task_span_record_id
                    LIMIT 1
                    """
                ),
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
            ).scalar_one()
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    """
                    UPDATE document_spans
                    SET content_hash = :mismatched_hash
                    WHERE span_id = CAST(:span_id AS uuid)
                    """
                ),
                {"mismatched_hash": "9" * 64, "span_id": SPAN_ID},
            )
            connection.execute(
                text(
                    """
                    UPDATE methodology_extraction_task_spans
                    SET span_ordinal = 3
                    WHERE task_span_record_id = CAST(:task_span_record_id AS uuid)
                    """
                ),
                {"task_span_record_id": str(later_span_record_id)},
            )

        boundary = object.__new__(PostgresMethodologyExecutionBoundary)
        boundary._connection_factory = database.app_engine.connect
        boundary._tenant_id = TENANT_ID
        boundary._execution_inputs = ()
        transport = _FailIfDispatchedTransport()
        extractor = AnthropicMethodologyTaskExtractor(
            transport=transport,
            prompt_artifact=_checked_in_prompt(),
            configured_model="claude-sonnet-4-20250514",
            max_tokens=512,
            temperature="0",
            price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
        )

        with pytest.raises(RuntimeError, match="METHODOLOGY_TASK_COVERAGE_INCOMPLETE"):
            inputs = boundary.load_execution_inputs(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                tasks=tasks,
                documents=[],
            )
            extractor.extract(inputs[0].request)

        assert transport.calls == 0
