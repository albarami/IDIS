"""Disposable-PostgreSQL RED contracts for typed canonical-link lineage."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.models.methodology_extraction import (
    MethodologyResultEvidenceLink,
    generate_methodology_result_link_id,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task6.test_postgres_0037_red import (
    CLAIM_CANONICAL_KEY_SHA256,
    CLAIM_ID,
    COVERAGE_ID,
    DEAL_ID,
    DEFECT_ID,
    DOCUMENT_ID,
    EVIDENCE_ID,
    FACTUAL_ID,
    FACTUAL_RECORD_ID,
    HASH_A,
    HASH_B,
    LOCATOR_HASH,
    RESULT_ID,
    RUN_ID,
    SANAD_ID,
    SECOND_COVERAGE_ID,
    SECOND_QUESTION_RECORD_ID,
    SECOND_SOURCE_CONTENT_SHA256,
    SECOND_SPAN_ID,
    SELECTION_ID,
    SOURCE_CONTENT_SHA256,
    SPAN_ID,
    TASK_ID,
    TENANT_A,
    _insert_second_coverage_record,
    _insert_task6_graph,
    _seed_task6_dependencies,
    _set_active_task6_write_context,
)

CLAIM_LINK_ID = "21212121-2121-4212-8212-212121212121"
VALID_EVIDENCE_LINK_ID = "23232323-2323-4232-8232-232323232323"
VALID_SANAD_LINK_ID = "24242424-2424-4242-8242-242424242424"
SECOND_CLAIM_ID = "31313131-3131-4131-8131-313131313131"
WRONG_CLAIM_EVIDENCE_ID = "32323232-3232-4232-8232-323232323232"
WRONG_SPAN_EVIDENCE_ID = "33323232-3232-4232-8232-323232323233"
SECOND_VALID_CLAIM_EVIDENCE_ID = "34343434-3434-4434-8434-343434343434"
WRONG_CLAIM_SANAD_ID = "35353535-3535-4535-8535-353535353535"
WRONG_PRIMARY_SANAD_ID = "36363636-3636-4636-8636-363636363636"
WRONG_CLAIM_DEFECT_ID = "37373737-3737-4737-8737-373737373737"
VERSION_RECORD_ID = "15151515-1515-4151-8151-151515151515"
QUESTION_RECORD_ID = "17171717-1717-4171-8171-171717171717"
SECOND_VERSION_RECORD_ID = "16161616-1616-4161-8161-161616161616"
SECOND_TASK_ID = "45454545-4545-4545-8545-454545454545"
SECOND_TASK_SPAN_ID = "46464646-4646-4646-8646-464646464646"
WRONG_LINEAGE_CLAIM_LINK_ID = "47474747-4747-4747-8747-474747474747"


def _prepare_unlinked_graph(database: DisposablePostgres) -> None:
    migrate(database.admin_engine, "0037")
    grant_application_access(database)
    _seed_task6_dependencies(database)
    _insert_task6_graph(database, include_links=False)
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES (
                    CAST(:second_span_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:document_id AS uuid),
                    'PAGE_TEXT', jsonb_build_object('page', 1),
                    'Second fictional span', :second_content_sha256
                );
                INSERT INTO claims (
                    claim_id, tenant_id, deal_id, claim_class, claim_text,
                    predicate, value, created_at, normalization_rule_version,
                    normalized_predicate, normalized_subject, normalized_period,
                    normalized_unit, value_sha256
                ) VALUES (
                    CAST(:second_claim_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), 'FACT', 'Second fictional claim',
                    'second_fictional', '{"type":"text"}'::jsonb, now(),
                    'claim-normalization-v1', 'second_fictional',
                    'second fictional', 'none', 'text', :hash_b
                );
                INSERT INTO evidence_items (
                    evidence_id, tenant_id, deal_id, claim_id, source_span_id,
                    source_grade, source_system, upstream_origin_id,
                    retrieval_timestamp, verification_status, rationale, updated_at
                ) VALUES
                (CAST(:wrong_claim_evidence_id AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:second_claim_id AS uuid),
                 CAST(:span_id AS uuid), 'B', 'fixture', 'wrong-claim', now(),
                 'UNVERIFIED', '{}'::jsonb, now()),
                (CAST(:wrong_span_evidence_id AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:claim_id AS uuid),
                 CAST(:second_span_id AS uuid), 'B', 'fixture', 'wrong-span', now(),
                 'UNVERIFIED', '{}'::jsonb, now()),
                (CAST(:second_valid_claim_evidence_id AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:claim_id AS uuid),
                 CAST(:second_span_id AS uuid), 'B', 'fixture', 'second-valid', now(),
                 'UNVERIFIED', '{}'::jsonb, now());
                INSERT INTO sanads (
                    sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                    created_at
                ) VALUES
                (CAST(:wrong_claim_sanad_id AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:second_claim_id AS uuid), CAST(:deal_id AS uuid),
                 CAST(:evidence_id AS uuid), now()),
                (CAST(:wrong_primary_sanad_id AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:claim_id AS uuid), CAST(:deal_id AS uuid),
                 CAST(:evidence_id AS uuid), now());
                INSERT INTO defects (
                    defect_id, tenant_id, deal_id, claim_id, defect_type,
                    severity, status, created_at
                ) VALUES (
                    CAST(:wrong_claim_defect_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:second_claim_id AS uuid),
                    'FIXTURE', 'MINOR', 'OPEN', now()
                )
                """
            ),
            {
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "document_id": DOCUMENT_ID,
                "span_id": SPAN_ID,
                "second_span_id": SECOND_SPAN_ID,
                "claim_id": CLAIM_ID,
                "second_claim_id": SECOND_CLAIM_ID,
                "evidence_id": EVIDENCE_ID,
                "wrong_claim_evidence_id": WRONG_CLAIM_EVIDENCE_ID,
                "wrong_span_evidence_id": WRONG_SPAN_EVIDENCE_ID,
                "second_valid_claim_evidence_id": SECOND_VALID_CLAIM_EVIDENCE_ID,
                "wrong_claim_sanad_id": WRONG_CLAIM_SANAD_ID,
                "wrong_primary_sanad_id": WRONG_PRIMARY_SANAD_ID,
                "wrong_claim_defect_id": WRONG_CLAIM_DEFECT_ID,
                "hash_b": HASH_B,
                "second_content_sha256": SECOND_SOURCE_CONTENT_SHA256,
            },
        )
    _insert_claim_link(database)


def _insert_claim_link(database: DisposablePostgres) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO methodology_result_claim_links (
                    claim_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, selection_id, coverage_record_id,
                    methodology_version_record_id, methodology_question_record_id,
                    claim_id, canonical_key_sha256, reused_existing_claim
                ) VALUES (
                    CAST(:claim_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_output_record_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST(:version_record_id AS uuid), CAST(:question_record_id AS uuid),
                    CAST(:claim_id AS uuid), :hash_a, false
                )
                """
            ),
            {
                "claim_link_id": CLAIM_LINK_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "result_id": RESULT_ID,
                "factual_output_record_id": FACTUAL_RECORD_ID,
                "selection_id": SELECTION_ID,
                "coverage_record_id": COVERAGE_ID,
                "version_record_id": VERSION_RECORD_ID,
                "question_record_id": QUESTION_RECORD_ID,
                "claim_id": CLAIM_ID,
                "hash_a": CLAIM_CANONICAL_KEY_SHA256,
            },
        )


def _prepare_two_valid_task_lineages(database: DisposablePostgres) -> None:
    """Seed two valid coverage/task tuples sharing one frozen run selection."""
    migrate(database.admin_engine, "0037")
    grant_application_access(database)
    _seed_task6_dependencies(database)
    _insert_second_coverage_record(database)
    _insert_task6_graph(database, include_links=False)
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
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
                    CAST(:version_record_id AS uuid), 'financial_dd:frozen-v1',
                    CAST(:question_record_id AS uuid),
                    'mq_financial_dd_fixture_0001', 'financial_dd',
                    'et_task6_lineage_fixture_b', CAST(:document_id AS uuid),
                    'dc_lineage_fixture_b', :hash_a, 'selected-pair-planner-v1',
                    'methodology-extraction-task-v1', '{"task":"lineage-b"}'::jsonb,
                    :hash_b, :hash_a, 'READY'
                );
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    CAST(:task_span_record_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_record_id AS uuid), CAST(:document_id AS uuid),
                    CAST(:span_id AS uuid), 1, :content_sha256, :locator_sha256
                )
                """
            ),
            {
                "task_record_id": SECOND_TASK_ID,
                "task_span_record_id": SECOND_TASK_SPAN_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "selection_id": SELECTION_ID,
                "coverage_record_id": SECOND_COVERAGE_ID,
                "version_record_id": SECOND_VERSION_RECORD_ID,
                "question_record_id": SECOND_QUESTION_RECORD_ID,
                "document_id": DOCUMENT_ID,
                "span_id": SPAN_ID,
                "content_sha256": SOURCE_CONTENT_SHA256,
                "locator_sha256": LOCATOR_HASH,
                "hash_a": HASH_A,
                "hash_b": HASH_B,
            },
        )


def _insert_claim_link_with_lineage(
    database: DisposablePostgres,
    *,
    claim_link_id: str,
    coverage_record_id: str,
    version_record_id: str,
    question_record_id: str,
) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO methodology_result_claim_links (
                    claim_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, selection_id, coverage_record_id,
                    methodology_version_record_id, methodology_question_record_id,
                    claim_id, canonical_key_sha256, reused_existing_claim
                ) VALUES (
                    CAST(:claim_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_output_record_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST(:version_record_id AS uuid), CAST(:question_record_id AS uuid),
                    CAST(:claim_id AS uuid), :canonical_key_sha256, false
                )
                """
            ),
            {
                "claim_link_id": claim_link_id,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "result_id": RESULT_ID,
                "factual_output_record_id": FACTUAL_RECORD_ID,
                "selection_id": SELECTION_ID,
                "coverage_record_id": coverage_record_id,
                "version_record_id": version_record_id,
                "question_record_id": question_record_id,
                "claim_id": CLAIM_ID,
                "canonical_key_sha256": CLAIM_CANONICAL_KEY_SHA256,
            },
        )


def _valid_task_lineage_count(database: DisposablePostgres) -> int:
    with database.admin_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM methodology_extraction_tasks t
                    JOIN methodology_coverage_records c
                      ON c.tenant_id = t.tenant_id
                     AND c.deal_id = t.deal_id
                     AND c.run_id = t.run_id
                     AND c.selection_id = t.selection_id
                     AND c.coverage_record_id = t.coverage_record_id
                     AND c.methodology_version_record_id =
                         t.methodology_version_record_id
                     AND c.methodology_question_record_id =
                         t.methodology_question_record_id
                    WHERE t.tenant_id = CAST(:tenant_id AS uuid)
                      AND t.deal_id = CAST(:deal_id AS uuid)
                      AND t.run_id = CAST(:run_id AS uuid)
                      AND t.task_record_id IN (
                          CAST(:task_a AS uuid), CAST(:task_b AS uuid)
                      )
                    """
                ),
                {
                    "tenant_id": TENANT_A,
                    "deal_id": DEAL_ID,
                    "run_id": RUN_ID,
                    "task_a": TASK_ID,
                    "task_b": SECOND_TASK_ID,
                },
            ).scalar_one()
        )


def _claim_link_count(database: DisposablePostgres) -> int:
    with database.admin_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM methodology_result_claim_links
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "run_id": RUN_ID},
            ).scalar_one()
        )


def test_claim_link_rejects_valid_lineage_tuple_from_another_task() -> None:
    with disposable_postgres() as database:
        _prepare_two_valid_task_lineages(database)
        assert _valid_task_lineage_count(database) == 2
        assert _claim_link_count(database) == 0

        with pytest.raises(DBAPIError) as exc_info:
            _insert_claim_link_with_lineage(
                database,
                claim_link_id=WRONG_LINEAGE_CLAIM_LINK_ID,
                coverage_record_id=SECOND_COVERAGE_ID,
                version_record_id=SECOND_VERSION_RECORD_ID,
                question_record_id=SECOND_QUESTION_RECORD_ID,
            )

        assert getattr(exc_info.value.orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(exc_info.value)
        assert _claim_link_count(database) == 0


def test_restart_rejects_bypassed_claim_link_task_lineage_mismatch() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
    )
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        rehydrate_postgres_methodology_claims,
    )

    with disposable_postgres() as database:
        _prepare_two_valid_task_lineages(database)
        assert _valid_task_lineage_count(database) == 2
        _insert_claim_link_with_lineage(
            database,
            claim_link_id=WRONG_LINEAGE_CLAIM_LINK_ID,
            coverage_record_id=COVERAGE_ID,
            version_record_id=VERSION_RECORD_ID,
            question_record_id=QUESTION_RECORD_ID,
        )
        with database.admin_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            changed = connection.execute(
                text(
                    """
                    UPDATE methodology_result_claim_links
                    SET coverage_record_id = :coverage_record_id,
                        methodology_version_record_id = CAST(:version_record_id AS uuid),
                        methodology_question_record_id = CAST(:question_record_id AS uuid)
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND claim_link_id = CAST(:claim_link_id AS uuid)
                    """
                ),
                {
                    "coverage_record_id": SECOND_COVERAGE_ID,
                    "version_record_id": SECOND_VERSION_RECORD_ID,
                    "question_record_id": SECOND_QUESTION_RECORD_ID,
                    "tenant_id": TENANT_A,
                    "deal_id": DEAL_ID,
                    "run_id": RUN_ID,
                    "claim_link_id": WRONG_LINEAGE_CLAIM_LINK_ID,
                },
            )
            assert changed.rowcount == 1

        with pytest.raises(MethodologyExtractionRepositoryError):
            rehydrate_postgres_methodology_claims(
                connection_factory=database.app_engine.begin,
                tenant_id=TENANT_A,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
            )


def _insert_evidence_link(
    database: DisposablePostgres,
    *,
    evidence_link_id: str,
    evidence_id: str,
    source_span_id: str,
    locator_sha256: str = LOCATOR_HASH,
) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO methodology_result_evidence_links (
                    evidence_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, evidence_id,
                    document_id, source_span_id, locator_sha256
                ) VALUES (
                    CAST(:evidence_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_output_record_id AS uuid),
                    CAST(:claim_link_id AS uuid), CAST(:evidence_id AS uuid),
                    CAST(:document_id AS uuid), CAST(:source_span_id AS uuid),
                    :locator_sha256
                )
                """
            ),
            {
                "evidence_link_id": evidence_link_id,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "result_id": RESULT_ID,
                "factual_output_record_id": FACTUAL_RECORD_ID,
                "claim_link_id": CLAIM_LINK_ID,
                "evidence_id": evidence_id,
                "document_id": DOCUMENT_ID,
                "source_span_id": source_span_id,
                "locator_sha256": locator_sha256,
            },
        )


def _admit_second_span_to_task(
    database: DisposablePostgres,
    *,
    task_record_id: str,
    task_span_record_id: str,
) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    CAST(:task_span_record_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_record_id AS uuid), CAST(:document_id AS uuid),
                    CAST(:span_id AS uuid), 2, :content_sha256, :locator_sha256
                )
                """
            ),
            {
                "task_span_record_id": task_span_record_id,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "task_record_id": task_record_id,
                "document_id": DOCUMENT_ID,
                "span_id": SECOND_SPAN_ID,
                "content_sha256": SECOND_SOURCE_CONTENT_SHA256,
                "locator_sha256": LOCATOR_HASH,
            },
        )


def _insert_other_task_for_second_span(database: DisposablePostgres) -> None:
    _insert_second_coverage_record(database)
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
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
                    CAST(:version_record_id AS uuid), 'financial_dd:frozen-v1',
                    CAST(:question_record_id AS uuid),
                    'mq_financial_dd_fixture_0001', 'financial_dd',
                    'et_task6_evidence_pair_fixture_b', CAST(:document_id AS uuid),
                    'dc_evidence_pair_fixture_b', :hash_a,
                    'selected-pair-planner-v1', 'methodology-extraction-task-v1',
                    '{"task":"evidence-pair-b"}'::jsonb, :hash_b, :hash_a, 'READY'
                )
                """
            ),
            {
                "task_record_id": SECOND_TASK_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "selection_id": SELECTION_ID,
                "coverage_record_id": SECOND_COVERAGE_ID,
                "version_record_id": SECOND_VERSION_RECORD_ID,
                "question_record_id": SECOND_QUESTION_RECORD_ID,
                "document_id": DOCUMENT_ID,
                "hash_a": HASH_A,
                "hash_b": HASH_B,
            },
        )
    _admit_second_span_to_task(
        database,
        task_record_id=SECOND_TASK_ID,
        task_span_record_id=SECOND_TASK_SPAN_ID,
    )


def _insert_sanad_link(
    database: DisposablePostgres,
    *,
    sanad_link_id: str,
    sanad_id: str,
    primary_evidence_id: str,
) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO methodology_result_sanad_links (
                    sanad_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, sanad_id,
                    primary_evidence_id
                ) VALUES (
                    CAST(:sanad_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_output_record_id AS uuid),
                    CAST(:claim_link_id AS uuid), CAST(:sanad_id AS uuid),
                    CAST(:primary_evidence_id AS uuid)
                )
                """
            ),
            {
                "sanad_link_id": sanad_link_id,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "result_id": RESULT_ID,
                "factual_output_record_id": FACTUAL_RECORD_ID,
                "claim_link_id": CLAIM_LINK_ID,
                "sanad_id": sanad_id,
                "primary_evidence_id": primary_evidence_id,
            },
        )


def test_evidence_link_rejects_wrong_same_deal_claim() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        with pytest.raises(DBAPIError):
            _insert_evidence_link(
                database,
                evidence_link_id="38383838-3838-4838-8838-383838383838",
                evidence_id=WRONG_CLAIM_EVIDENCE_ID,
                source_span_id=SPAN_ID,
            )


def test_evidence_link_rejects_wrong_same_deal_source_span() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        with pytest.raises(DBAPIError):
            _insert_evidence_link(
                database,
                evidence_link_id="39393939-3939-4939-8939-393939393939",
                evidence_id=WRONG_SPAN_EVIDENCE_ID,
                source_span_id=SPAN_ID,
            )


def test_evidence_link_rejects_pair_admitted_only_to_another_task() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _insert_other_task_for_second_span(database)

        with pytest.raises(DBAPIError) as exc_info:
            _insert_evidence_link(
                database,
                evidence_link_id="48484848-4848-4848-8848-484848484848",
                evidence_id=SECOND_VALID_CLAIM_EVIDENCE_ID,
                source_span_id=SECOND_SPAN_ID,
                locator_sha256=LOCATOR_HASH,
            )

        assert getattr(exc_info.value.orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(exc_info.value)


def test_evidence_link_rejects_admitted_span_with_wrong_locator() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)

        with pytest.raises(DBAPIError) as exc_info:
            _insert_evidence_link(
                database,
                evidence_link_id="49494949-4949-4949-8949-494949494949",
                evidence_id=EVIDENCE_ID,
                source_span_id=SPAN_ID,
                locator_sha256=HASH_B,
            )

        assert getattr(exc_info.value.orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(exc_info.value)


def test_repository_derives_evidence_pair_from_factual_output_task() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        METHODOLOGY_SOURCE_LOCATOR_REJECTED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _insert_other_task_for_second_span(database)

        logical_record = {
            "tenant_id": TENANT_A,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "result_id": RESULT_ID,
            "factual_output_record_id": FACTUAL_RECORD_ID,
            "claim_link_id": CLAIM_LINK_ID,
            "factual_output_id": FACTUAL_ID,
            "evidence_id": SECOND_VALID_CLAIM_EVIDENCE_ID,
            "document_id": DOCUMENT_ID,
            "source_span_id": SECOND_SPAN_ID,
        }
        evidence_link = MethodologyResultEvidenceLink.model_validate(
            {
                "evidence_link_id": generate_methodology_result_link_id(
                    table="methodology_result_evidence_links",
                    logical_record=logical_record,
                    derived_fields={"locator_sha256": LOCATOR_HASH},
                ),
                **logical_record,
            }
        )
        with (
            database.app_engine.begin() as connection,
            pytest.raises(MethodologyExtractionRepositoryError) as exc_info,
        ):
            PostgresMethodologyExtractionRepository(connection, TENANT_A).persist_evidence_links(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                task_record_id=SECOND_TASK_ID,
                lease_owner_id="66666666-6666-4666-8666-666666666666",
                lease_fencing_token=7,
                records=(evidence_link,),
            )
        assert exc_info.value.code == METHODOLOGY_SOURCE_LOCATOR_REJECTED


def test_restart_rejects_bypassed_evidence_locator_pair_mismatch() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _insert_evidence_link(
            database,
            evidence_link_id=VALID_EVIDENCE_LINK_ID,
            evidence_id=EVIDENCE_ID,
            source_span_id=SPAN_ID,
        )
        with database.admin_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            changed = connection.execute(
                text(
                    """
                    UPDATE methodology_result_evidence_links
                    SET locator_sha256 = :wrong_locator_sha256
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND evidence_link_id = CAST(:evidence_link_id AS uuid)
                    """
                ),
                {
                    "wrong_locator_sha256": HASH_B,
                    "tenant_id": TENANT_A,
                    "deal_id": DEAL_ID,
                    "run_id": RUN_ID,
                    "evidence_link_id": VALID_EVIDENCE_LINK_ID,
                },
            )
            assert changed.rowcount == 1

        with (
            database.app_engine.begin() as connection,
            pytest.raises(MethodologyExtractionRepositoryError),
        ):
            PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).load_canonical_materialization_snapshot(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
            )


def test_evidence_links_allow_distinct_admitted_spans_at_one_locator() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _admit_second_span_to_task(
            database,
            task_record_id=TASK_ID,
            task_span_record_id=SECOND_TASK_SPAN_ID,
        )
        _insert_evidence_link(
            database,
            evidence_link_id=VALID_EVIDENCE_LINK_ID,
            evidence_id=EVIDENCE_ID,
            source_span_id=SPAN_ID,
        )
        _insert_evidence_link(
            database,
            evidence_link_id="51515151-5151-4151-8151-515151515151",
            evidence_id=SECOND_VALID_CLAIM_EVIDENCE_ID,
            source_span_id=SECOND_SPAN_ID,
        )

        with database.admin_engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT source_span_id, locator_sha256
                    FROM methodology_result_evidence_links
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                    ORDER BY source_span_id
                    """
                ),
                {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "run_id": RUN_ID},
            ).fetchall()

        assert {str(row.source_span_id) for row in rows} == {SPAN_ID, SECOND_SPAN_ID}
        assert {str(row.locator_sha256) for row in rows} == {LOCATOR_HASH}
        with database.app_engine.begin() as connection:
            snapshot = PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).load_canonical_materialization_snapshot(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
            )
        assert {
            (str(row["source_span_id"]), str(row["locator_sha256"]))
            for row in snapshot.evidence_items
        } == {(SPAN_ID, LOCATOR_HASH), (SECOND_SPAN_ID, LOCATOR_HASH)}


def test_sanad_link_rejects_wrong_same_deal_claim() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _insert_evidence_link(
            database,
            evidence_link_id=VALID_EVIDENCE_LINK_ID,
            evidence_id=EVIDENCE_ID,
            source_span_id=SPAN_ID,
        )
        with pytest.raises(DBAPIError):
            _insert_sanad_link(
                database,
                sanad_link_id="40404040-4040-4040-8040-404040404040",
                sanad_id=WRONG_CLAIM_SANAD_ID,
                primary_evidence_id=EVIDENCE_ID,
            )


def test_sanad_link_rejects_wrong_same_deal_primary_evidence() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _admit_second_span_to_task(
            database,
            task_record_id=TASK_ID,
            task_span_record_id=SECOND_TASK_SPAN_ID,
        )
        _insert_evidence_link(
            database,
            evidence_link_id=VALID_EVIDENCE_LINK_ID,
            evidence_id=EVIDENCE_ID,
            source_span_id=SPAN_ID,
        )
        _insert_evidence_link(
            database,
            evidence_link_id="41414141-4141-4141-8141-414141414141",
            evidence_id=SECOND_VALID_CLAIM_EVIDENCE_ID,
            source_span_id=SECOND_SPAN_ID,
        )
        with pytest.raises(DBAPIError):
            _insert_sanad_link(
                database,
                sanad_link_id="42424242-4242-4242-8242-424242424242",
                sanad_id=WRONG_PRIMARY_SANAD_ID,
                primary_evidence_id=SECOND_VALID_CLAIM_EVIDENCE_ID,
            )


def test_defect_link_rejects_wrong_same_deal_claim() -> None:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        _insert_evidence_link(
            database,
            evidence_link_id=VALID_EVIDENCE_LINK_ID,
            evidence_id=EVIDENCE_ID,
            source_span_id=SPAN_ID,
        )
        _insert_sanad_link(
            database,
            sanad_link_id=VALID_SANAD_LINK_ID,
            sanad_id=SANAD_ID,
            primary_evidence_id=EVIDENCE_ID,
        )
        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            _set_active_task6_write_context(connection)
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        """
                        INSERT INTO methodology_result_defect_links (
                            defect_link_id, tenant_id, deal_id, run_id, result_id,
                            factual_output_record_id, sanad_link_id, defect_id
                        ) VALUES (
                            gen_random_uuid(), CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                            CAST(:result_id AS uuid),
                            CAST(:factual_output_record_id AS uuid),
                            CAST(:sanad_link_id AS uuid), CAST(:defect_id AS uuid)
                        )
                        """
                    ),
                    {
                        "tenant_id": TENANT_A,
                        "deal_id": DEAL_ID,
                        "run_id": RUN_ID,
                        "result_id": RESULT_ID,
                        "factual_output_record_id": FACTUAL_RECORD_ID,
                        "sanad_link_id": VALID_SANAD_LINK_ID,
                        "defect_id": WRONG_CLAIM_DEFECT_ID,
                    },
                )


def test_restart_snapshot_revalidates_canonical_link_semantics() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        with database.admin_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    """
                    INSERT INTO methodology_result_evidence_links (
                        evidence_link_id, tenant_id, deal_id, run_id, result_id,
                        factual_output_record_id, claim_link_id, evidence_id,
                        document_id, source_span_id, locator_sha256
                    ) VALUES (
                        gen_random_uuid(), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                        CAST(:result_id AS uuid),
                        CAST(:factual_output_record_id AS uuid),
                        CAST(:claim_link_id AS uuid), CAST(:evidence_id AS uuid),
                        CAST(:document_id AS uuid), CAST(:source_span_id AS uuid), :hash_b
                    )
                    """
                ),
                {
                    "tenant_id": TENANT_A,
                    "deal_id": DEAL_ID,
                    "run_id": RUN_ID,
                    "result_id": RESULT_ID,
                    "factual_output_record_id": FACTUAL_RECORD_ID,
                    "claim_link_id": CLAIM_LINK_ID,
                    "evidence_id": WRONG_CLAIM_EVIDENCE_ID,
                    "document_id": DOCUMENT_ID,
                    "source_span_id": SPAN_ID,
                    "hash_b": HASH_B,
                },
            )

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            with pytest.raises(MethodologyExtractionRepositoryError):
                repository.load_canonical_materialization_snapshot(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                )


def test_valid_typed_canonical_lineage_still_loads_after_restart() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database)

        with database.app_engine.begin() as connection:
            snapshot = PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).load_canonical_materialization_snapshot(deal_id=DEAL_ID, run_id=RUN_ID)

        assert len(snapshot.claims) == 1
        assert len(snapshot.evidence_items) == 1
        assert len(snapshot.sanads) == 1
        assert len(snapshot.defects) == 1
        assert snapshot.claims[0]["claim_id"] == CLAIM_ID
        assert snapshot.evidence_items[0]["evidence_id"] == EVIDENCE_ID
        assert snapshot.sanads[0]["sanad_id"] == SANAD_ID
        assert snapshot.defects[0]["defect_id"] == DEFECT_ID
