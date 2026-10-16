"""Disposable-PostgreSQL RED contract for normalized claim convergence."""

from __future__ import annotations

from sqlalchemy import text

from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6.test_postgres_0037_red import (
    CANONICAL_CLAIM_IDENTITY,
    CANONICAL_FACTUAL_OUTPUT,
    CLAIM_ID,
    DEAL_ID,
    LEASE_OWNER_ID,
    RUN_ID,
    TENANT_A,
    _insert_task6_graph,
    _seed_task6_dependencies,
)


def test_normalized_claim_reuse_accepts_raw_predicate_formatting_variant() -> None:
    """Raw presentation drift must not defeat an identical canonical identity."""
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database, include_links=False)

        with database.admin_engine.connect() as connection:
            before = connection.execute(
                text(
                    """
                    SELECT claim_id, claim_text, predicate, value, created_at, updated_at
                    FROM claims
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND claim_id = CAST(:claim_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "claim_id": CLAIM_ID},
            ).one()

        variant = {
            "claim_id": CLAIM_ID,
            "claim_class": "FACT",
            "claim_text": "A differently formatted presentation must not replace the winner.",
            "predicate": "  Fictional  ",
            "value": CANONICAL_FACTUAL_OUTPUT.value.model_dump(mode="json"),
            "normalization_rule_version": CANONICAL_CLAIM_IDENTITY.normalization_rule_version,
            "normalized_predicate": CANONICAL_CLAIM_IDENTITY.normalized_predicate,
            "normalized_subject": CANONICAL_CLAIM_IDENTITY.normalized_subject,
            "normalized_period": CANONICAL_CLAIM_IDENTITY.normalized_period,
            "normalized_unit": CANONICAL_CLAIM_IDENTITY.normalized_unit,
            "value_sha256": CANONICAL_CLAIM_IDENTITY.value_sha256,
            "canonical_key_sha256": CANONICAL_CLAIM_IDENTITY.canonical_key_sha256,
        }
        with database.app_engine.begin() as connection:
            winner, reused = PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).upsert_canonical_claim(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
                claim=variant,
            )

        assert reused is True
        assert str(winner["claim_id"]) == CLAIM_ID
        assert winner["predicate"] == CANONICAL_FACTUAL_OUTPUT.predicate
        assert winner["claim_text"] == "Fictional claim"
        assert winner["value"] == CANONICAL_FACTUAL_OUTPUT.value.model_dump(mode="json")

        with database.admin_engine.connect() as connection:
            after = connection.execute(
                text(
                    """
                    SELECT claim_id, claim_text, predicate, value, created_at, updated_at
                    FROM claims
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND claim_id = CAST(:claim_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "deal_id": DEAL_ID, "claim_id": CLAIM_ID},
            ).one()
        assert tuple(after) == tuple(before)
