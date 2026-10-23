"""Real-PostgreSQL composition proof for all four typed methodology links."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.methodology_extraction import (
    MethodologyFactualOutput,
    MethodologyResultClaimLink,
    MethodologyResultDefectLink,
    MethodologyResultEvidenceLink,
    MethodologyResultSanadLink,
    canonical_json_bytes,
    generate_methodology_result_link_id,
)
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodology_extraction import (
    PostgresMethodologyExtractionRepository,
)
from idis.services.runs.methodology_postgres_materialization_runtime import (
    PostgresCanonicalMethodologyMaterializationBoundary,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6 import test_coverage_projections_postgres_red as coverage_pg
from tests.slice100_task6._support import (
    TASK6_SCHEMA_HEAD,
    bind_frozen_run_source,
    canonical_locator_sha256,
)


def _persist(database: DisposablePostgres, staged: Any) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, coverage_pg.TENANT_ID)
        staged.persist(
            db_conn=connection,
            lease_owner_id=coverage_pg.LEASE_OWNER_ID,
            lease_fencing_token=1,
        )


def _row_mapping(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def _logical_strings(row: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: str(row[field]) if field != "reused_existing_claim" else bool(row[field])
        for field in fields
    }


def _install_production_factual_output(
    database: DisposablePostgres,
    seeded: coverage_pg._SeededRun,
) -> None:
    """Replace the helper's seeded JSON with the production typed constructor."""

    locator = {"page": 1, "line": 1}
    locator_sha256 = canonical_locator_sha256(locator)
    output = MethodologyFactualOutput.model_validate(
        {
            "subject": "Fictional",
            "methodology_question_record_id": seeded.first["question_record"],
            "predicate": "Fictional",
            "claim_class": "FACT",
            "value": {"type": "text", "value": "fictional"},
            "period": {"kind": "none"},
            "unit": "text",
            "claim_text": "Verified fictional fact.",
            "accepted_source_span_ids": [seeded.first["span"]],
            "accepted_locator_sha256s": [locator_sha256],
        }
    )
    payload = output.model_dump(mode="json")
    locator_pairs = sorted(
        zip(
            output.accepted_source_span_ids,
            output.accepted_locator_sha256s,
            strict=True,
        )
    )
    with database.admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                """
                UPDATE methodology_result_factual_outputs
                SET factual_output_id = CAST(:factual_output_id AS uuid),
                    typed_output_payload = CAST(:payload AS jsonb),
                    typed_output_sha256 = :payload_sha256,
                    accepted_source_span_ids = CAST(:span_ids AS uuid[]),
                    accepted_locator_sha256s = CAST(:locator_hashes AS text[]),
                    accepted_locator_set_sha256 = :locator_set_sha256
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND factual_output_record_id = CAST(:factual_output_record_id AS uuid);
                UPDATE methodology_extraction_task_spans
                SET locator_sha256 = :locator_sha256
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND task_record_id = CAST(:task_record_id AS uuid)
                """
            ),
            {
                "factual_output_id": output.factual_output_id,
                "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "payload_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
                "span_ids": list(output.accepted_source_span_ids),
                "locator_hashes": list(output.accepted_locator_sha256s),
                "locator_set_sha256": hashlib.sha256(
                    canonical_json_bytes(locator_pairs)
                ).hexdigest(),
                "tenant_id": coverage_pg.TENANT_ID,
                "deal_id": coverage_pg.DEAL_ID,
                "run_id": coverage_pg.RUN_ID,
                "factual_output_record_id": seeded.first["factual"],
                "task_record_id": seeded.first["task"],
                "locator_sha256": locator_sha256,
            },
        )


def test_runtime_boundary_persists_all_four_exact_typed_link_contracts() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        selection_id = coverage_pg._seed_selected_coverage(database)
        seeded = coverage_pg._seed_terminal_tasks(database, selection_id, found_count=1)
        _install_production_factual_output(database, seeded)
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, coverage_pg.TENANT_ID)
            PostgresMethodologyExtractionRepository(
                connection, coverage_pg.TENANT_ID
            ).complete_execution_step_exact_set(
                deal_id=coverage_pg.DEAL_ID,
                run_id=coverage_pg.RUN_ID,
                run_step_id=coverage_pg.RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=coverage_pg.LEASE_OWNER_ID,
                lease_fencing_token=1,
            )

        boundary = PostgresCanonicalMethodologyMaterializationBoundary(
            connection_factory=database.app_engine.begin,
            tenant_id=coverage_pg.TENANT_ID,
            lease_owner_id=coverage_pg.LEASE_OWNER_ID,
            lease_fencing_token=1,
            audit_sink=PostgresAuditSink(),
        )
        scope = {
            "tenant_id": coverage_pg.TENANT_ID,
            "deal_id": coverage_pg.DEAL_ID,
            "run_id": coverage_pg.RUN_ID,
            "run_step_id": coverage_pg.SANAD_STEP_ID,
        }

        with database.app_engine.begin() as connection:
            bind_frozen_run_source(
                connection,
                tenant_id=coverage_pg.TENANT_ID,
                deal_id=coverage_pg.DEAL_ID,
                run_id=coverage_pg.RUN_ID,
                document_ids=(coverage_pg.DOCUMENT_ID,),
            )
        _claim_result, _claims, staged_claims = boundary.prepare_claim_materialization(**scope)
        _persist(database, staged_claims)
        _evidence_result, _evidence, staged_evidence = boundary.prepare_evidence_materialization(
            **scope
        )
        _persist(database, staged_evidence)
        *_sanad_outputs, staged_sanads = boundary.prepare_sanad_materialization(**scope)
        _persist(database, staged_sanads)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, coverage_pg.TENANT_ID)
            claim_rows = connection.execute(
                text(
                    """
                    SELECT l.*, f.factual_output_id
                    FROM methodology_result_claim_links AS l
                    JOIN methodology_result_factual_outputs AS f
                      ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                     AND f.run_id = l.run_id AND f.result_id = l.result_id
                     AND f.factual_output_record_id = l.factual_output_record_id
                    WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                      AND l.deal_id = CAST(:deal_id AS uuid)
                      AND l.run_id = CAST(:run_id AS uuid)
                    ORDER BY l.claim_link_id
                    """
                ),
                scope,
            ).fetchall()
            evidence_rows = connection.execute(
                text(
                    """
                    SELECT l.*, f.factual_output_id
                    FROM methodology_result_evidence_links AS l
                    JOIN methodology_result_factual_outputs AS f
                      ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                     AND f.run_id = l.run_id AND f.result_id = l.result_id
                     AND f.factual_output_record_id = l.factual_output_record_id
                    WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                      AND l.deal_id = CAST(:deal_id AS uuid)
                      AND l.run_id = CAST(:run_id AS uuid)
                    ORDER BY l.evidence_link_id
                    """
                ),
                scope,
            ).fetchall()
            sanad_rows = connection.execute(
                text(
                    """
                    SELECT l.*, f.factual_output_id, s.computed
                    FROM methodology_result_sanad_links AS l
                    JOIN methodology_result_factual_outputs AS f
                      ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                     AND f.run_id = l.run_id AND f.result_id = l.result_id
                     AND f.factual_output_record_id = l.factual_output_record_id
                    JOIN sanads AS s
                      ON s.tenant_id = l.tenant_id AND s.deal_id = l.deal_id
                     AND s.sanad_id = l.sanad_id
                    WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                      AND l.deal_id = CAST(:deal_id AS uuid)
                      AND l.run_id = CAST(:run_id AS uuid)
                    ORDER BY l.sanad_link_id
                    """
                ),
                scope,
            ).fetchall()
            defect_rows = connection.execute(
                text(
                    """
                    SELECT l.*
                    FROM methodology_result_defect_links AS l
                    WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                      AND l.deal_id = CAST(:deal_id AS uuid)
                      AND l.run_id = CAST(:run_id AS uuid)
                    ORDER BY l.defect_link_id
                    """
                ),
                scope,
            ).fetchall()

        assert len(claim_rows) == len(evidence_rows) == len(sanad_rows) == 1
        assert defect_rows

        claim = _row_mapping(claim_rows[0])
        claim_logical = _logical_strings(
            claim,
            (
                "tenant_id",
                "deal_id",
                "run_id",
                "result_id",
                "factual_output_record_id",
                "coverage_record_id",
                "factual_output_id",
                "claim_id",
                "canonical_key_sha256",
                "reused_existing_claim",
            ),
        )
        claim_model = MethodologyResultClaimLink.model_validate(
            {"claim_link_id": str(claim["claim_link_id"]), **claim_logical}
        )
        assert claim_model.claim_link_id == generate_methodology_result_link_id(
            table="methodology_result_claim_links",
            logical_record=claim_logical,
            derived_fields={
                "selection_id": str(claim["selection_id"]),
                "methodology_version_record_id": str(claim["methodology_version_record_id"]),
                "methodology_question_record_id": str(claim["methodology_question_record_id"]),
            },
        )

        evidence = _row_mapping(evidence_rows[0])
        evidence_logical = _logical_strings(
            evidence,
            (
                "tenant_id",
                "deal_id",
                "run_id",
                "result_id",
                "factual_output_record_id",
                "claim_link_id",
                "factual_output_id",
                "evidence_id",
                "document_id",
                "source_span_id",
            ),
        )
        evidence_model = MethodologyResultEvidenceLink.model_validate(
            {"evidence_link_id": str(evidence["evidence_link_id"]), **evidence_logical}
        )
        assert evidence_model.evidence_link_id == generate_methodology_result_link_id(
            table="methodology_result_evidence_links",
            logical_record=evidence_logical,
            derived_fields={"locator_sha256": str(evidence["locator_sha256"])},
        )

        sanad = _row_mapping(sanad_rows[0])
        computed = dict(sanad["computed"])
        sanad_logical = {
            **_logical_strings(
                sanad,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "result_id",
                    "factual_output_record_id",
                    "claim_link_id",
                    "factual_output_id",
                    "sanad_id",
                    "primary_evidence_id",
                ),
            ),
            "grade": str(computed["sanad_grade"]),
            "defect_ids": tuple(sorted(str(value) for value in computed["defect_ids"])),
        }
        sanad_model = MethodologyResultSanadLink.model_validate(
            {"sanad_link_id": str(sanad["sanad_link_id"]), **sanad_logical}
        )
        assert sanad_model.sanad_link_id == generate_methodology_result_link_id(
            table="methodology_result_sanad_links",
            logical_record=sanad_logical,
        )

        for raw_defect in defect_rows:
            defect = _row_mapping(raw_defect)
            defect_logical = _logical_strings(
                defect,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "result_id",
                    "factual_output_record_id",
                    "sanad_link_id",
                    "defect_id",
                ),
            )
            defect_model = MethodologyResultDefectLink.model_validate(
                {"defect_link_id": str(defect["defect_link_id"]), **defect_logical}
            )
            assert defect_model.defect_link_id == generate_methodology_result_link_id(
                table="methodology_result_defect_links",
                logical_record=defect_logical,
            )
