"""Real-PostgreSQL RED proofs for canonical methodology claim identity."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.models.methodology_extraction import (
    CanonicalMethodologyClaimIdentity,
    MethodologyFactualOutput,
    MethodologyResultClaimLink,
    canonical_json_bytes,
    generate_methodology_result_link_id,
)
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task6 import test_postgres_0037_red as pg
from tests.slice100_task6._support import TASK6_SCHEMA_HEAD


def _claim_link_record(
    identity: CanonicalMethodologyClaimIdentity,
    *,
    factual_output_id: str = pg.FACTUAL_ID,
    claim_id: str | None = None,
    canonical_key_sha256: str | None = None,
) -> MethodologyResultClaimLink:
    logical_record = {
        "tenant_id": pg.TENANT_A,
        "deal_id": pg.DEAL_ID,
        "run_id": pg.RUN_ID,
        "result_id": pg.RESULT_ID,
        "factual_output_record_id": pg.FACTUAL_RECORD_ID,
        "coverage_record_id": pg.COVERAGE_ID,
        "factual_output_id": factual_output_id,
        "claim_id": claim_id or identity.claim_id,
        "canonical_key_sha256": canonical_key_sha256 or identity.canonical_key_sha256,
        "reused_existing_claim": False,
    }
    derived_fields = {
        "selection_id": pg.SELECTION_ID,
        "methodology_version_record_id": "15151515-1515-4151-8151-151515151515",
        "methodology_question_record_id": "17171717-1717-4171-8171-171717171717",
    }
    return MethodologyResultClaimLink(
        claim_link_id=generate_methodology_result_link_id(
            table="methodology_result_claim_links",
            logical_record=logical_record,
            derived_fields=derived_fields,
        ),
        **logical_record,
    )


def _fresh_claim_payload() -> tuple[
    MethodologyFactualOutput,
    dict[str, Any],
    CanonicalMethodologyClaimIdentity,
]:
    """Build an identity absent from the seeded graph through the production model."""
    output = MethodologyFactualOutput.model_validate(
        {
            "subject": "Fresh Fictional Identity Co",
            "methodology_question_record_id": "17171717-1717-4171-8171-171717171717",
            "predicate": "Fresh Fictional Metric",
            "claim_class": "FINANCIAL_METRIC",
            "value": {"type": "monetary", "amount": "37.00", "currency": "USD"},
            "period": {"kind": "as_of", "date": "2026-06-30"},
            "unit": "USD",
            "claim_text": "Fresh fictional metric was USD 37.",
            "accepted_source_span_ids": [pg.SPAN_ID],
            "accepted_locator_sha256s": [pg.LOCATOR_HASH],
        }
    )
    identity = CanonicalMethodologyClaimIdentity.from_factual_output(
        tenant_id=pg.TENANT_A,
        deal_id=pg.DEAL_ID,
        factual_output=output,
    )
    return (
        output,
        {
            "claim_id": identity.claim_id,
            "claim_class": output.claim_class,
            "claim_text": output.claim_text,
            "predicate": output.predicate,
            "value": output.value.model_dump(mode="json"),
            "normalization_rule_version": identity.normalization_rule_version,
            "normalized_predicate": identity.normalized_predicate,
            "normalized_subject": identity.normalized_subject,
            "normalized_period": identity.normalized_period,
            "normalized_unit": identity.normalized_unit,
            "value_sha256": identity.value_sha256,
            "canonical_key_sha256": identity.canonical_key_sha256,
        },
        identity,
    )


def _replace_hand_built_factual_fixture(
    database: Any,
    output: MethodologyFactualOutput,
) -> None:
    """Replace the legacy shell with an output built by the production constructor."""
    payload = output.model_dump(mode="json")
    typed_output_sha256 = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    locator_pairs = sorted(
        zip(
            output.accepted_source_span_ids,
            output.accepted_locator_sha256s,
            strict=True,
        )
    )
    locator_set_sha256 = hashlib.sha256(canonical_json_bytes(locator_pairs)).hexdigest()
    with database.admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                """
                UPDATE methodology_result_factual_outputs
                SET factual_output_id = CAST(:factual_output_id AS uuid),
                    typed_output_payload = CAST(:payload AS jsonb),
                    typed_output_sha256 = :typed_output_sha256,
                    accepted_source_span_ids = CAST(:span_ids AS uuid[]),
                    accepted_locator_sha256s = CAST(:locator_hashes AS text[]),
                    accepted_locator_set_sha256 = :locator_set_sha256
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND factual_output_record_id = CAST(:factual_record_id AS uuid)
                """
            ),
            {
                "factual_output_id": output.factual_output_id,
                "payload": canonical_json_bytes(payload).decode("utf-8"),
                "typed_output_sha256": typed_output_sha256,
                "span_ids": list(output.accepted_source_span_ids),
                "locator_hashes": list(output.accepted_locator_sha256s),
                "locator_set_sha256": locator_set_sha256,
                "tenant_id": pg.TENANT_A,
                "deal_id": pg.DEAL_ID,
                "run_id": pg.RUN_ID,
                "factual_record_id": pg.FACTUAL_RECORD_ID,
            },
        )


def _claim_count(connection: Connection, identity: CanonicalMethodologyClaimIdentity) -> int:
    return int(
        connection.execute(
            text(
                """
                SELECT count(*)
                FROM claims
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND normalized_predicate = :normalized_predicate
                  AND normalized_subject = :normalized_subject
                  AND normalized_period = :normalized_period
                  AND normalized_unit = :normalized_unit
                """
            ),
            {
                "tenant_id": pg.TENANT_A,
                "deal_id": pg.DEAL_ID,
                "normalized_predicate": identity.normalized_predicate,
                "normalized_subject": identity.normalized_subject,
                "normalized_period": identity.normalized_period,
                "normalized_unit": identity.normalized_unit,
            },
        ).scalar_one()
    )


def _claim_link_count(connection: Connection) -> int:
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
            {"tenant_id": pg.TENANT_A, "deal_id": pg.DEAL_ID, "run_id": pg.RUN_ID},
        ).scalar_one()
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("claim_id", "abababab-abab-4bab-8bab-abababababab"),
        ("canonical_key_sha256", "e" * 64),
        ("value_sha256", "d" * 64),
    ),
)
def test_fresh_claim_rejects_caller_identity_drift_without_mutation(
    field: str,
    replacement: str,
) -> None:
    """A genuinely fresh key cannot admit caller-selected identity components."""
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    _output, payload, identity = _fresh_claim_payload()
    payload[field] = replacement

    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        pg._seed_task6_dependencies(database)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, pg.TENANT_A)
            assert _claim_count(connection, identity) == 0
            assert _claim_link_count(connection) == 0

            repository = PostgresMethodologyExtractionRepository(connection, pg.TENANT_A)
            with pytest.raises(MethodologyExtractionRepositoryError):
                repository.upsert_canonical_claim(
                    deal_id=pg.DEAL_ID,
                    run_id=pg.RUN_ID,
                    lease_owner_id=pg.LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    claim=payload,
                )

            assert _claim_count(connection, identity) == 0
            assert _claim_link_count(connection) == 0


def test_fresh_valid_claim_inserts_once_then_reuses_exact_winner() -> None:
    """The strict identity proof preserves deterministic convergence semantics."""
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    _output, payload, identity = _fresh_claim_payload()
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        pg._seed_task6_dependencies(database)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, pg.TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, pg.TENANT_A)
            first, first_reused = repository.upsert_canonical_claim(
                deal_id=pg.DEAL_ID,
                run_id=pg.RUN_ID,
                lease_owner_id=pg.LEASE_OWNER_ID,
                lease_fencing_token=7,
                claim=payload,
            )
            second, second_reused = repository.upsert_canonical_claim(
                deal_id=pg.DEAL_ID,
                run_id=pg.RUN_ID,
                lease_owner_id=pg.LEASE_OWNER_ID,
                lease_fencing_token=7,
                claim=payload,
            )

            assert first_reused is False
            assert second_reused is True
            assert first == second
            assert str(first["claim_id"]) == identity.claim_id
            assert _claim_count(connection, identity) == 1
            assert _claim_link_count(connection) == 0


@pytest.mark.parametrize("mutation", ("claim_id", "canonical_key_sha256"))
def test_claim_link_recomputes_durable_claim_identity_before_insert(mutation: str) -> None:
    """Link admission must not trust either caller identity field."""
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    _output, payload, identity = _fresh_claim_payload()
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        pg._seed_task6_dependencies(database)
        pg._insert_task6_graph(database, include_links=False)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, pg.TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, pg.TENANT_A)
            repository.upsert_canonical_claim(
                deal_id=pg.DEAL_ID,
                run_id=pg.RUN_ID,
                lease_owner_id=pg.LEASE_OWNER_ID,
                lease_fencing_token=7,
                claim=payload,
            )
            if mutation == "claim_id":
                record = _claim_link_record(identity, claim_id=pg.CLAIM_ID)
            else:
                record = _claim_link_record(identity, canonical_key_sha256="e" * 64)

            assert _claim_link_count(connection) == 0
            with pytest.raises(MethodologyExtractionRepositoryError):
                repository.persist_claim_links(
                    deal_id=pg.DEAL_ID,
                    run_id=pg.RUN_ID,
                    lease_owner_id=pg.LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    records=[record],
                )
            assert _claim_link_count(connection) == 0


def _corrupt_claim_id(connection: Connection, identity: CanonicalMethodologyClaimIdentity) -> None:
    bad_claim_id = "acacacac-acac-4cac-8cac-acacacacacac"
    connection.execute(
        text(
            """
            UPDATE methodology_result_claim_links
            SET claim_id = CAST(:bad_claim_id AS uuid)
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
              AND claim_id = CAST(:claim_id AS uuid)
            """
        ),
        {
            "bad_claim_id": bad_claim_id,
            "tenant_id": pg.TENANT_A,
            "deal_id": pg.DEAL_ID,
            "run_id": pg.RUN_ID,
            "claim_id": identity.claim_id,
        },
    )
    connection.execute(
        text(
            """
            UPDATE claims SET claim_id = CAST(:bad_claim_id AS uuid)
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND claim_id = CAST(:claim_id AS uuid)
            """
        ),
        {
            "bad_claim_id": bad_claim_id,
            "tenant_id": pg.TENANT_A,
            "deal_id": pg.DEAL_ID,
            "claim_id": identity.claim_id,
        },
    )


def _corrupt_canonical_key(
    connection: Connection, identity: CanonicalMethodologyClaimIdentity
) -> None:
    connection.execute(
        text(
            """
            UPDATE methodology_result_claim_links
            SET canonical_key_sha256 = :bad_hash
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
              AND claim_id = CAST(:claim_id AS uuid)
            """
        ),
        {
            "bad_hash": "e" * 64,
            "tenant_id": pg.TENANT_A,
            "deal_id": pg.DEAL_ID,
            "run_id": pg.RUN_ID,
            "claim_id": identity.claim_id,
        },
    )


def _corrupt_value_sha(connection: Connection, identity: CanonicalMethodologyClaimIdentity) -> None:
    connection.execute(
        text(
            """
            UPDATE claims SET value_sha256 = :bad_hash
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND claim_id = CAST(:claim_id AS uuid)
            """
        ),
        {
            "bad_hash": "d" * 64,
            "tenant_id": pg.TENANT_A,
            "deal_id": pg.DEAL_ID,
            "claim_id": identity.claim_id,
        },
    )


@pytest.mark.parametrize(
    "corrupt",
    (_corrupt_claim_id, _corrupt_canonical_key, _corrupt_value_sha),
    ids=("claim-id", "canonical-key-sha256", "value-sha256"),
)
def test_restart_snapshot_rejects_bypassed_canonical_identity_corruption(
    corrupt: Callable[[Connection, CanonicalMethodologyClaimIdentity], None],
) -> None:
    """Rehydration must re-prove identity even after constraints/triggers were bypassed."""
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        rehydrate_postgres_methodology_claims,
    )

    output, payload, identity = _fresh_claim_payload()
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        pg._seed_task6_dependencies(database)
        pg._insert_task6_graph(database, include_links=False)
        _replace_hand_built_factual_fixture(database, output)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, pg.TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, pg.TENANT_A)
            repository.upsert_canonical_claim(
                deal_id=pg.DEAL_ID,
                run_id=pg.RUN_ID,
                lease_owner_id=pg.LEASE_OWNER_ID,
                lease_fencing_token=7,
                claim=payload,
            )
            repository.persist_claim_links(
                deal_id=pg.DEAL_ID,
                run_id=pg.RUN_ID,
                lease_owner_id=pg.LEASE_OWNER_ID,
                lease_fencing_token=7,
                records=[
                    _claim_link_record(
                        identity,
                        factual_output_id=str(output.factual_output_id),
                    )
                ],
            )

        with database.admin_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            corrupt(connection, identity)

        with pytest.raises(MethodologyExtractionRepositoryError):
            rehydrate_postgres_methodology_claims(
                connection_factory=database.app_engine.begin,
                tenant_id=pg.TENANT_A,
                deal_id=pg.DEAL_ID,
                run_id=pg.RUN_ID,
            )
