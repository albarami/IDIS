"""RED contracts for Task-6-linked EvidenceItem provenance and identity."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.models.methodology_extraction import (
    MethodologyResultEvidenceLink,
    generate_methodology_result_link_id,
)
from idis.persistence.repositories.methodology_extraction import (
    MethodologyExtractionRepositoryError,
    PostgresMethodologyExtractionRepository,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    set_tenant_context,
)
from tests.slice100_task6.test_postgres_0037_red import (
    CLAIM_ID,
    DEAL_ID,
    DOCUMENT_ID,
    EVIDENCE_ID,
    FACTUAL_ID,
    FACTUAL_RECORD_ID,
    LEASE_OWNER_ID,
    LOCATOR_HASH,
    RESULT_ID,
    RUN_ID,
    SECOND_SPAN_ID,
    SPAN_ID,
    TASK_ID,
    TENANT_A,
)
from tests.slice100_task6.test_postgres_canonical_lineage_guards_red import (
    CLAIM_LINK_ID,
    SECOND_CLAIM_ID,
    _prepare_unlinked_graph,
)

INSERTED_EVIDENCE_ID = "53535353-5353-4353-8353-535353535353"

_REQUIRED_FIELD_CASES = (
    ("source_system", "", "blank-source-system"),
    ("upstream_origin_id", "", "blank-upstream-origin-id"),
    ("retrieval_timestamp", None, "null-retrieval-timestamp"),
    ("verification_status", None, "null-verification-status"),
    ("rationale", None, "null-rationale"),
    ("updated_at", None, "null-updated-at"),
)

_INVALID_REQUIRED_FIELDS = tuple(
    pytest.param(field, value, id=case_id) for field, value, case_id in _REQUIRED_FIELD_CASES
)

_INVALID_LINKED_UPDATES = (
    *_INVALID_REQUIRED_FIELDS,
    pytest.param("claim_id", SECOND_CLAIM_ID, id="claim-mismatch"),
    pytest.param("source_span_id", SECOND_SPAN_ID, id="source-span-mismatch"),
)

_EVIDENCE_STATE_FIELDS = (
    "source_system",
    "upstream_origin_id",
    "retrieval_timestamp",
    "verification_status",
    "rationale",
    "updated_at",
    "claim_id",
    "source_span_id",
)


@contextmanager
def _prepared_database() -> Iterator[DisposablePostgres]:
    with disposable_postgres() as database:
        _prepare_unlinked_graph(database)
        yield database


def _set_evidence_field(
    database: DisposablePostgres,
    *,
    field: str,
    value: object,
    bypass_triggers: bool = False,
) -> None:
    expressions = {
        "source_system": ":value",
        "upstream_origin_id": ":value",
        "retrieval_timestamp": "CAST(:value AS timestamptz)",
        "verification_status": ":value",
        "rationale": "CAST(:value AS jsonb)",
        "updated_at": "CAST(:value AS timestamptz)",
        "claim_id": "CAST(:value AS uuid)",
        "source_span_id": "CAST(:value AS uuid)",
    }
    if field not in expressions:
        raise AssertionError(f"Unsupported test field: {field}")
    engine = database.admin_engine if bypass_triggers else database.app_engine
    with engine.begin() as connection:
        if bypass_triggers:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
        else:
            set_tenant_context(connection, TENANT_A)
        changed = connection.execute(
            text(
                f"""
                UPDATE evidence_items
                SET {field} = {expressions[field]}
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND evidence_id = CAST(:evidence_id AS uuid)
                """
            ),
            {
                "value": value,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "evidence_id": EVIDENCE_ID,
            },
        )
        assert changed.rowcount == 1


def _evidence_state(database: DisposablePostgres) -> dict[str, Any]:
    with database.admin_engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT {", ".join(_EVIDENCE_STATE_FIELDS)}
                FROM evidence_items
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND evidence_id = CAST(:evidence_id AS uuid)
                """
            ),
            {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "evidence_id": EVIDENCE_ID},
        ).one()
    return dict(row._mapping)


def _persist_evidence_link(database: DisposablePostgres) -> None:
    logical_record = {
        "tenant_id": TENANT_A,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "result_id": RESULT_ID,
        "factual_output_record_id": FACTUAL_RECORD_ID,
        "claim_link_id": CLAIM_LINK_ID,
        "factual_output_id": FACTUAL_ID,
        "evidence_id": EVIDENCE_ID,
        "document_id": DOCUMENT_ID,
        "source_span_id": SPAN_ID,
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
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
        repository.persist_evidence_links(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            task_record_id=TASK_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
            records=(evidence_link,),
        )


@pytest.mark.parametrize(
    "field",
    (
        pytest.param("source_system", id="blank-source-system"),
        pytest.param("upstream_origin_id", id="blank-upstream-origin-id"),
    ),
)
def test_repository_insert_uses_required_field_validator(field: str) -> None:
    with _prepared_database() as database:
        payload = {
            "evidence_id": INSERTED_EVIDENCE_ID,
            "source_span_id": SPAN_ID,
            "source_system": "fixture",
            "upstream_origin_id": "fixture-origin",
            "retrieval_timestamp": "2026-07-21T00:00:00Z",
            "verification_status": "UNVERIFIED",
            "source_grade": "B",
            "source_rank_subgrade": None,
            "rationale": {},
            "updated_at": "2026-07-21T00:00:00Z",
        }
        payload[field] = ""

        with (
            database.app_engine.begin() as connection,
            pytest.raises(MethodologyExtractionRepositoryError),
        ):
            set_tenant_context(connection, TENANT_A)
            PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).insert_canonical_evidence_item(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                task_record_id=TASK_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
                evidence_item=payload,
                claim_id=CLAIM_ID,
            )


@pytest.mark.parametrize(("field", "invalid_value"), _INVALID_REQUIRED_FIELDS)
def test_repository_link_rejects_evidence_missing_required_provenance(
    field: str,
    invalid_value: object,
) -> None:
    with _prepared_database() as database:
        _set_evidence_field(database, field=field, value=invalid_value)

        with pytest.raises(DBAPIError) as exc_info:
            _persist_evidence_link(database)

        assert getattr(exc_info.value.orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(exc_info.value)


@pytest.mark.parametrize(("field", "invalid_value"), _INVALID_LINKED_UPDATES)
def test_linked_evidence_rejects_required_or_identity_invalidation_atomically(
    field: str,
    invalid_value: object,
) -> None:
    with _prepared_database() as database:
        _persist_evidence_link(database)
        before = _evidence_state(database)

        with pytest.raises(DBAPIError) as exc_info:
            _set_evidence_field(database, field=field, value=invalid_value)

        assert getattr(exc_info.value.orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(exc_info.value)
        assert _evidence_state(database) == before


def test_unlinked_legacy_evidence_retains_nullable_extension_compatibility() -> None:
    with _prepared_database() as database:
        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            changed = connection.execute(
                text(
                    """
                    UPDATE evidence_items
                    SET source_system = NULL,
                        upstream_origin_id = NULL,
                        retrieval_timestamp = NULL,
                        verification_status = NULL,
                        rationale = NULL,
                        updated_at = NULL
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND evidence_id = CAST(:evidence_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "evidence_id": EVIDENCE_ID},
            )
            assert changed.rowcount == 1

        state = _evidence_state(database)
        assert all(state[field] is None for field, _value, _case_id in _REQUIRED_FIELD_CASES)


@pytest.mark.parametrize(("field", "invalid_value"), _INVALID_REQUIRED_FIELDS)
def test_restart_snapshot_rejects_trigger_bypassed_missing_required_provenance(
    field: str,
    invalid_value: object,
) -> None:
    with _prepared_database() as database:
        _persist_evidence_link(database)
        _set_evidence_field(
            database,
            field=field,
            value=invalid_value,
            bypass_triggers=True,
        )

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


def test_evidence_guard_catalog_is_security_invoker_and_indexed_for_reverse_lookup() -> None:
    with _prepared_database() as database, database.admin_engine.connect() as connection:
        functions = dict(
            connection.execute(
                text(
                    """
                    SELECT proname, prosecdef
                    FROM pg_proc
                    WHERE proname IN (
                        'idis_task6_evidence_fields_valid',
                        'idis_task6_evidence_item_update_guard'
                    )
                    ORDER BY proname
                    """
                )
            ).fetchall()
        )
        assert functions == {
            "idis_task6_evidence_fields_valid": False,
            "idis_task6_evidence_item_update_guard": False,
        }
        trigger_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_trigger
                WHERE tgrelid = 'evidence_items'::regclass
                  AND tgname = 'trg_evidence_items_task6_link_guard'
                  AND NOT tgisinternal
                """
            )
        ).scalar_one()
        assert trigger_count == 1
        index_definition = connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'methodology_result_evidence_links'
                  AND indexname =
                      'ix_methodology_result_evidence_links_evidence_reverse_lookup'
                """
            )
        ).scalar_one_or_none()
        assert index_definition is not None
        assert "(tenant_id, deal_id, evidence_id)" in index_definition


def test_concurrent_unlinked_update_commits_before_link_validation() -> None:
    with _prepared_database() as database:
        invalid_update_staged = threading.Event()
        release_invalid_update = threading.Event()
        update_errors: list[BaseException] = []
        link_errors: list[BaseException] = []

        def update_unlinked_evidence() -> None:
            try:
                with database.app_engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        set_tenant_context(connection, TENANT_A)
                        changed = connection.execute(
                            text(
                                """
                                UPDATE evidence_items
                                SET source_system = ''
                                WHERE tenant_id = CAST(:tenant_id AS uuid)
                                  AND deal_id = CAST(:deal_id AS uuid)
                                  AND evidence_id = CAST(:evidence_id AS uuid)
                                """
                            ),
                            {
                                "tenant_id": TENANT_A,
                                "deal_id": DEAL_ID,
                                "evidence_id": EVIDENCE_ID,
                            },
                        )
                        assert changed.rowcount == 1
                        invalid_update_staged.set()
                        assert release_invalid_update.wait(timeout=10)
                        transaction.commit()
                    except BaseException:
                        transaction.rollback()
                        raise
            except BaseException as exc:  # pragma: no cover - asserted by parent thread
                update_errors.append(exc)

        def link_evidence() -> None:
            try:
                _persist_evidence_link(database)
            except BaseException as exc:  # pragma: no cover - asserted by parent thread
                link_errors.append(exc)

        update_thread = threading.Thread(target=update_unlinked_evidence, daemon=True)
        update_thread.start()
        assert invalid_update_staged.wait(timeout=10)

        link_thread = threading.Thread(target=link_evidence, daemon=True)
        link_thread.start()
        # Give the link transaction time to reach the EvidenceItem lock while
        # the invalid unlinked update is deliberately held uncommitted.
        link_thread.join(timeout=1)
        release_invalid_update.set()
        update_thread.join(timeout=10)
        link_thread.join(timeout=10)

        assert not update_thread.is_alive()
        assert not link_thread.is_alive()
        assert update_errors == []
        assert len(link_errors) == 1
        assert isinstance(link_errors[0], DBAPIError)
        assert getattr(link_errors[0].orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(link_errors[0])
        with database.admin_engine.connect() as connection:
            link_count = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM methodology_result_evidence_links
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND evidence_id = CAST(:evidence_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "evidence_id": EVIDENCE_ID},
            ).scalar_one()
        assert link_count == 0
