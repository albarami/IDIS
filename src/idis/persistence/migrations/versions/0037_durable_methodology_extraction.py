"""Persist fenced methodology extraction and canonical materialization lineage.

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-20

Task 6 adds only the nine approved tenant-bearing extraction relations.  It
also repairs the legacy canonical claim/evidence/Sanad scope needed to attach
those relations with real composite foreign keys.  No provider call, output
receipt, consumer edge, or Task-7 persistence surface is introduced here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from idis.models.run_step import STEP_ORDER, StepName

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_STEP7_NAME = StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION
_STEP7_ORDER = STEP_ORDER[_STEP7_NAME]

TASK6_TABLES = (
    "methodology_extraction_attempts",
    "methodology_extraction_results",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_result_claim_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_factual_outputs",
    "methodology_result_sanad_links",
)

CANONICAL_TENANT_PREDICATE = """\
NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
"""


def _add_canonical_extensions() -> None:
    """Add the exact legacy-safe canonical columns approved for Task 6."""
    op.execute(
        """
        ALTER TABLE claims
            ADD COLUMN normalization_rule_version TEXT,
            ADD COLUMN normalized_predicate TEXT,
            ADD COLUMN normalized_subject TEXT,
            ADD COLUMN normalized_period TEXT,
            ADD COLUMN normalized_unit TEXT,
            ADD COLUMN value_sha256 TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE claims ADD CONSTRAINT ck_claims_task6_normalization
        CHECK (
            (normalization_rule_version IS NULL
             AND normalized_predicate IS NULL
             AND normalized_subject IS NULL
             AND normalized_period IS NULL
             AND normalized_unit IS NULL
             AND value_sha256 IS NULL)
            OR
            (normalization_rule_version = 'claim-normalization-v1'
             AND NULLIF(btrim(normalized_predicate), '') IS NOT NULL
             AND NULLIF(btrim(normalized_subject), '') IS NOT NULL
             AND NULLIF(btrim(normalized_period), '') IS NOT NULL
             AND NULLIF(btrim(normalized_unit), '') IS NOT NULL
             AND value_sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_claims_task6_normalized_key
        ON claims (
            tenant_id, deal_id, normalized_predicate, normalized_subject,
            normalized_period, normalized_unit, value_sha256
        )
        WHERE normalization_rule_version IS NOT NULL
          AND normalized_predicate IS NOT NULL
          AND normalized_subject IS NOT NULL
          AND normalized_period IS NOT NULL
          AND normalized_unit IS NOT NULL
          AND value_sha256 IS NOT NULL
        """
    )

    op.execute(
        """
        ALTER TABLE evidence_items
            ADD COLUMN source_system TEXT,
            ADD COLUMN upstream_origin_id TEXT,
            ADD COLUMN retrieval_timestamp TIMESTAMPTZ,
            ADD COLUMN verification_status TEXT,
            ADD COLUMN source_rank_subgrade TEXT,
            ADD COLUMN rationale JSONB,
            ADD COLUMN updated_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE evidence_items
        ADD CONSTRAINT ck_evidence_items_task6_extensions CHECK (
            (verification_status IS NULL OR verification_status IN (
                'UNVERIFIED', 'VERIFIED', 'CONTRADICTED'
            ))
            AND (source_rank_subgrade IS NULL OR source_rank_subgrade IN (
                'A+', 'A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D'
            ))
            AND (rationale IS NULL OR jsonb_typeof(rationale) = 'object')
        )
        """
    )

    # Refuse malformed, missing, or cross-scope legacy references rather than
    # silently repairing them.  Validate lexical UUID shape before any cast so
    # PostgreSQL cannot evaluate a failing cast while checking legacy rows.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM sanads s
                WHERE s.primary_evidence_id !~*
                      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            ) THEN
                RAISE EXCEPTION 'TASK6_SANAD_PRIMARY_EVIDENCE_SCOPE_INVALID'
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM sanads s
                WHERE NOT EXISTS (
                    SELECT 1 FROM evidence_items e
                    WHERE e.evidence_id = s.primary_evidence_id::uuid
                      AND e.tenant_id = s.tenant_id
                      AND e.deal_id = s.deal_id
                )
            ) THEN
                RAISE EXCEPTION 'TASK6_SANAD_PRIMARY_EVIDENCE_SCOPE_INVALID'
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE sanads
        ALTER COLUMN primary_evidence_id TYPE UUID
        USING primary_evidence_id::uuid
        """
    )


def _add_scoped_keys() -> None:
    """Add only the unique scope anchors required by Task-6 foreign keys."""
    statements = (
        "CREATE UNIQUE INDEX ux_documents_task6_scope "
        "ON documents (tenant_id, deal_id, document_id)",
        "CREATE UNIQUE INDEX ux_document_spans_task6_scope "
        "ON document_spans (tenant_id, deal_id, document_id, span_id)",
        "CREATE UNIQUE INDEX ux_run_steps_task6_scope ON run_steps (tenant_id, run_id, step_id)",
        "CREATE UNIQUE INDEX ux_coverage_task6_scope ON methodology_coverage_records "
        "(tenant_id, deal_id, run_id, selection_id, coverage_record_id, "
        "methodology_version_record_id, methodology_question_record_id)",
        "CREATE UNIQUE INDEX ux_claims_task6_scope ON claims (tenant_id, deal_id, claim_id)",
        "CREATE UNIQUE INDEX ux_evidence_items_task6_scope "
        "ON evidence_items (tenant_id, deal_id, evidence_id)",
        "CREATE UNIQUE INDEX ux_sanads_task6_scope ON sanads (tenant_id, deal_id, sanad_id)",
        "CREATE UNIQUE INDEX ux_defects_task6_scope ON defects (tenant_id, deal_id, defect_id)",
    )
    for statement in statements:
        op.execute(statement)

    # These constraints VALIDATE existing rows and therefore abort on any
    # legacy cross-tenant/cross-deal relationship.  They never backfill.
    op.execute(
        """
        ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_items_task6_claim_scope
        FOREIGN KEY (tenant_id, deal_id, claim_id)
        REFERENCES claims (tenant_id, deal_id, claim_id)
        """
    )
    op.execute(
        """
        ALTER TABLE sanads ADD CONSTRAINT fk_sanads_task6_claim_scope
        FOREIGN KEY (tenant_id, deal_id, claim_id)
        REFERENCES claims (tenant_id, deal_id, claim_id)
        """
    )
    op.execute(
        """
        ALTER TABLE defects ADD CONSTRAINT fk_defects_task6_claim_scope
        FOREIGN KEY (tenant_id, deal_id, claim_id)
        REFERENCES claims (tenant_id, deal_id, claim_id)
        """
    )
    op.execute(
        """
        ALTER TABLE sanads ADD CONSTRAINT fk_sanads_task6_primary_evidence_scope
        FOREIGN KEY (tenant_id, deal_id, primary_evidence_id)
        REFERENCES evidence_items (tenant_id, deal_id, evidence_id)
        """
    )


def _create_tasks() -> None:
    op.execute(
        """
        CREATE TABLE methodology_extraction_tasks (
            task_record_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            selection_id UUID NOT NULL,
            coverage_record_id TEXT NOT NULL,
            methodology_version_record_id UUID NOT NULL,
            methodology_version_id TEXT NOT NULL,
            methodology_question_record_id UUID NOT NULL,
            methodology_question_id TEXT NOT NULL,
            methodology_type TEXT NOT NULL,
            extraction_task_id TEXT NOT NULL,
            document_id UUID NOT NULL,
            document_classification_id TEXT NOT NULL,
            document_classification_sha256 TEXT NOT NULL,
            planner_contract_version TEXT NOT NULL,
            task_contract_version TEXT NOT NULL,
            task_payload JSONB NOT NULL,
            task_input_sha256 TEXT NOT NULL,
            candidate_span_set_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_extraction_tasks_selection FOREIGN KEY (
                tenant_id, deal_id, run_id, selection_id
            ) REFERENCES run_methodology_selections (
                tenant_id, deal_id, run_id, selection_id
            ),
            CONSTRAINT fk_methodology_extraction_tasks_coverage FOREIGN KEY (
                tenant_id, deal_id, run_id, selection_id, coverage_record_id,
                methodology_version_record_id, methodology_question_record_id
            ) REFERENCES methodology_coverage_records (
                tenant_id, deal_id, run_id, selection_id, coverage_record_id,
                methodology_version_record_id, methodology_question_record_id
            ),
            CONSTRAINT fk_methodology_extraction_tasks_question FOREIGN KEY (
                tenant_id, methodology_version_record_id, methodology_version_id,
                methodology_question_record_id, methodology_question_id,
                methodology_type
            ) REFERENCES methodology_questions (
                tenant_id, methodology_version_record_id, methodology_version_id,
                methodology_question_record_id, methodology_question_id,
                methodology_type
            ),
            CONSTRAINT fk_methodology_extraction_tasks_document FOREIGN KEY (
                tenant_id, deal_id, document_id
            ) REFERENCES documents (tenant_id, deal_id, document_id),
            CONSTRAINT ck_methodology_extraction_tasks_ready CHECK (
                status = 'READY'
                AND methodology_type IN ('commercial_dd', 'financial_dd')
                AND NULLIF(btrim(extraction_task_id), '') IS NOT NULL
                AND NULLIF(btrim(document_classification_id), '') IS NOT NULL
                AND NULLIF(btrim(planner_contract_version), '') IS NOT NULL
                AND NULLIF(btrim(task_contract_version), '') IS NOT NULL
                AND jsonb_typeof(task_payload) = 'object'
                AND document_classification_sha256 ~ '^[0-9a-f]{64}$'
                AND task_input_sha256 ~ '^[0-9a-f]{64}$'
                AND candidate_span_set_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_methodology_extraction_tasks_external
                UNIQUE (tenant_id, run_id, extraction_task_id),
            CONSTRAINT uq_methodology_extraction_tasks_scope
                UNIQUE (tenant_id, deal_id, run_id, task_record_id),
            CONSTRAINT uq_methodology_extraction_tasks_scope_document
                UNIQUE (
                    tenant_id, deal_id, run_id, task_record_id, document_id
                ),
            CONSTRAINT uq_methodology_extraction_tasks_input
                UNIQUE (tenant_id, run_id, task_record_id, task_input_sha256),
            CONSTRAINT uq_methodology_extraction_tasks_scope_input
                UNIQUE (
                    tenant_id, deal_id, run_id, task_record_id,
                    task_input_sha256
                ),
            CONSTRAINT uq_methodology_extraction_tasks_question_document
                UNIQUE (
                    tenant_id, run_id, methodology_question_record_id, document_id
                )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_extraction_task_spans (
            task_span_record_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            task_record_id UUID NOT NULL,
            document_id UUID NOT NULL,
            span_id UUID NOT NULL,
            span_ordinal INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            locator_sha256 TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_extraction_task_spans_task_document FOREIGN KEY (
                tenant_id, deal_id, run_id, task_record_id, document_id
            ) REFERENCES methodology_extraction_tasks (
                tenant_id, deal_id, run_id, task_record_id, document_id
            ),
            CONSTRAINT fk_methodology_extraction_task_spans_span FOREIGN KEY (
                tenant_id, deal_id, document_id, span_id
            ) REFERENCES document_spans (
                tenant_id, deal_id, document_id, span_id
            ),
            CONSTRAINT ck_methodology_extraction_task_spans_payload CHECK (
                span_ordinal > 0
                AND content_sha256 ~ '^[0-9a-f]{64}$'
                AND locator_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_methodology_extraction_task_spans_identity
                UNIQUE (tenant_id, run_id, task_record_id, span_id),
            CONSTRAINT uq_methodology_extraction_task_spans_ordinal
                UNIQUE (tenant_id, run_id, task_record_id, span_ordinal)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_methodology_extraction_task_spans_task_document
        ON methodology_extraction_task_spans (
            tenant_id, deal_id, run_id, task_record_id, document_id
        )
        """
    )


def _create_attempts_and_results() -> None:
    op.execute(
        """
        CREATE TABLE methodology_extraction_attempts (
            attempt_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            run_step_id UUID NOT NULL,
            task_record_id UUID NOT NULL,
            task_input_sha256 TEXT NOT NULL,
            attempt_ordinal INTEGER NOT NULL DEFAULT 1,
            lease_owner_id UUID NOT NULL,
            lease_fencing_token BIGINT NOT NULL,
            status TEXT NOT NULL,
            provider TEXT NOT NULL,
            backend TEXT NOT NULL,
            transport_kind TEXT NOT NULL,
            configured_model TEXT NOT NULL,
            observed_model TEXT,
            prompt_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            prompt_content_sha256 TEXT NOT NULL,
            prompt_input_schema_sha256 TEXT NOT NULL,
            prompt_output_schema_sha256 TEXT NOT NULL,
            request_payload_sha256 TEXT NOT NULL,
            prepared_call_sha256 TEXT NOT NULL,
            billable_payload_sha256 TEXT NOT NULL,
            billable_payload_utf8_bytes BIGINT NOT NULL,
            provider_request_id TEXT,
            provider_response_sha256 TEXT,
            dispatched_call_sha256 TEXT,
            real_call BOOLEAN NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            latency_ms BIGINT,
            configured_max_tokens INTEGER NOT NULL,
            configured_temperature NUMERIC(9,8) NOT NULL,
            configured_json_mode BOOLEAN NOT NULL,
            input_tokens BIGINT,
            output_tokens BIGINT,
            stop_reason TEXT,
            budget_policy_id TEXT NOT NULL,
            budget_component TEXT NOT NULL,
            budget_reservation_id UUID NOT NULL,
            price_catalog_version TEXT NOT NULL,
            price_catalog_row_sha256 TEXT NOT NULL,
            input_usd_per_million_tokens NUMERIC(20,8) NOT NULL,
            output_usd_per_million_tokens NUMERIC(20,8) NOT NULL,
            reserved_calls INTEGER NOT NULL,
            reserved_input_tokens BIGINT NOT NULL,
            reserved_output_tokens BIGINT NOT NULL,
            reserved_embedding_tokens BIGINT NOT NULL DEFAULT 0,
            reserved_usd NUMERIC(20,8) NOT NULL,
            budget_terminal_state TEXT NOT NULL,
            receipt_sha256 TEXT,
            safe_failure_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_extraction_attempts_task_input FOREIGN KEY (
                tenant_id, deal_id, run_id, task_record_id,
                task_input_sha256
            ) REFERENCES methodology_extraction_tasks (
                tenant_id, deal_id, run_id, task_record_id,
                task_input_sha256
            ),
            CONSTRAINT fk_methodology_extraction_attempts_step FOREIGN KEY (
                tenant_id, run_id, run_step_id
            ) REFERENCES run_steps (tenant_id, run_id, step_id),
            CONSTRAINT ck_methodology_extraction_attempts_identity CHECK (
                attempt_ordinal = 1
                AND lease_fencing_token > 0
                AND provider = 'anthropic'
                AND transport_kind IN ('LIVE', 'HERMETIC_TEST')
                AND real_call = (transport_kind = 'LIVE')
                AND task_input_sha256 ~ '^[0-9a-f]{64}$'
                AND prompt_content_sha256 ~ '^[0-9a-f]{64}$'
                AND prompt_input_schema_sha256 ~ '^[0-9a-f]{64}$'
                AND prompt_output_schema_sha256 ~ '^[0-9a-f]{64}$'
                AND request_payload_sha256 ~ '^[0-9a-f]{64}$'
                AND prepared_call_sha256 ~ '^[0-9a-f]{64}$'
                AND billable_payload_sha256 ~ '^[0-9a-f]{64}$'
                AND billable_payload_utf8_bytes > 0
                AND configured_max_tokens > 0
                AND configured_temperature = 0
                AND price_catalog_row_sha256 ~ '^[0-9a-f]{64}$'
                AND input_usd_per_million_tokens >= 0
                AND output_usd_per_million_tokens >= 0
                AND reserved_calls >= 0
                AND reserved_input_tokens >= 0
                AND reserved_output_tokens >= 0
                AND reserved_embedding_tokens = 0
                AND reserved_usd >= 0
            ),
            CONSTRAINT ck_methodology_extraction_attempts_lifecycle CHECK (
                status IN ('STARTED', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN')
                AND (
                    (status = 'STARTED'
                     AND completed_at IS NULL
                     AND receipt_sha256 IS NULL
                     AND safe_failure_code IS NULL
                     AND budget_terminal_state IN ('RESERVED', 'NOT_BILLABLE'))
                    OR
                    (status = 'SUCCEEDED'
                     AND completed_at IS NOT NULL
                     AND observed_model = configured_model
                     AND NULLIF(btrim(provider_request_id), '') IS NOT NULL
                     AND provider_response_sha256 ~ '^[0-9a-f]{64}$'
                     AND dispatched_call_sha256 ~ '^[0-9a-f]{64}$'
                     AND receipt_sha256 ~ '^[0-9a-f]{64}$'
                     AND latency_ms >= 0
                     AND input_tokens >= 0
                     AND input_tokens <= reserved_input_tokens
                     AND output_tokens >= 0
                     AND output_tokens <= reserved_output_tokens
                     AND NULLIF(btrim(stop_reason), '') IS NOT NULL
                     AND safe_failure_code IS NULL
                     AND budget_terminal_state IN ('CONSUMED', 'NOT_BILLABLE'))
                    OR
                    (status = 'FAILED'
                     AND completed_at IS NOT NULL
                     AND NULLIF(btrim(safe_failure_code), '') IS NOT NULL
                     AND receipt_sha256 IS NULL
                     AND (
                         budget_terminal_state IN ('REFUNDED', 'NOT_BILLABLE')
                         OR
                         (budget_terminal_state = 'CONSUMED'
                          AND real_call
                          AND provider_response_sha256 ~ '^[0-9a-f]{64}$'
                          AND dispatched_call_sha256 ~ '^[0-9a-f]{64}$'
                          AND latency_ms >= 0
                          AND (
                              (safe_failure_code IN (
                                  'METHODOLOGY_PROVIDER_PROVENANCE_INVALID',
                                  'METHODOLOGY_PROVIDER_USAGE_INVALID'
                               )
                               AND (
                                   observed_model IS NULL
                                   OR NULLIF(btrim(observed_model), '') IS NOT NULL
                               )
                               AND (
                                   provider_request_id IS NULL
                                   OR NULLIF(btrim(provider_request_id), '') IS NOT NULL
                               )
                               AND (
                                   stop_reason IS NULL
                                   OR NULLIF(btrim(stop_reason), '') IS NOT NULL
                               )
                               AND (input_tokens IS NULL OR input_tokens >= 0)
                               AND (output_tokens IS NULL OR output_tokens >= 0))
                              OR
                              (safe_failure_code NOT IN (
                                  'METHODOLOGY_PROVIDER_PROVENANCE_INVALID',
                                  'METHODOLOGY_PROVIDER_USAGE_INVALID'
                               )
                               AND observed_model = configured_model
                               AND NULLIF(btrim(provider_request_id), '') IS NOT NULL
                               AND NULLIF(btrim(stop_reason), '') IS NOT NULL
                               AND input_tokens >= 0
                               AND input_tokens <= reserved_input_tokens
                               AND output_tokens >= 0
                               AND output_tokens <= reserved_output_tokens)
                          ))
                     ))
                    OR
                    (status = 'OUTCOME_UNKNOWN'
                     AND completed_at IS NOT NULL
                     AND safe_failure_code = 'METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN'
                     AND budget_terminal_state IN ('RESERVED', 'NOT_BILLABLE'))
                )
            ),
            CONSTRAINT uq_methodology_extraction_attempts_task_input
                UNIQUE (tenant_id, run_id, task_record_id, task_input_sha256),
            CONSTRAINT uq_methodology_extraction_attempts_scope
                UNIQUE (tenant_id, deal_id, run_id, attempt_id),
            CONSTRAINT uq_methodology_extraction_attempts_scope_task_input
                UNIQUE (
                    tenant_id, deal_id, run_id, attempt_id, task_record_id,
                    task_input_sha256
                ),
            CONSTRAINT uq_methodology_extraction_attempts_reservation
                UNIQUE (tenant_id, budget_reservation_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_methodology_extraction_attempts_task_input
        ON methodology_extraction_attempts (
            tenant_id, deal_id, run_id, task_record_id, task_input_sha256
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_extraction_results (
            result_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            task_record_id UUID NOT NULL,
            attempt_id UUID NOT NULL,
            task_input_sha256 TEXT NOT NULL,
            outcome TEXT NOT NULL,
            result_payload JSONB NOT NULL,
            result_sha256 TEXT NOT NULL,
            searched_document_ids UUID[] NOT NULL,
            searched_span_ids UUID[] NOT NULL,
            searched_scope_sha256 TEXT NOT NULL,
            provider_provenance_sha256 TEXT NOT NULL,
            gap_finding JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_extraction_results_task_input FOREIGN KEY (
                tenant_id, deal_id, run_id, task_record_id,
                task_input_sha256
            ) REFERENCES methodology_extraction_tasks (
                tenant_id, deal_id, run_id, task_record_id,
                task_input_sha256
            ),
            CONSTRAINT fk_methodology_extraction_results_attempt_task_input FOREIGN KEY (
                tenant_id, deal_id, run_id, attempt_id, task_record_id,
                task_input_sha256
            ) REFERENCES methodology_extraction_attempts (
                tenant_id, deal_id, run_id, attempt_id, task_record_id,
                task_input_sha256
            ),
            CONSTRAINT ck_methodology_extraction_results_terminal CHECK (
                outcome IN ('EVIDENCE_FOUND', 'EVIDENCE_NOT_FOUND')
                AND jsonb_typeof(result_payload) = 'object'
                AND result_payload <> '{}'::jsonb
                AND cardinality(searched_document_ids) > 0
                AND cardinality(searched_span_ids) > 0
                AND array_position(searched_document_ids, NULL) IS NULL
                AND array_position(searched_span_ids, NULL) IS NULL
                AND result_sha256 ~ '^[0-9a-f]{64}$'
                AND task_input_sha256 ~ '^[0-9a-f]{64}$'
                AND searched_scope_sha256 ~ '^[0-9a-f]{64}$'
                AND provider_provenance_sha256 ~ '^[0-9a-f]{64}$'
                AND (
                    (outcome = 'EVIDENCE_FOUND' AND gap_finding IS NULL)
                    OR
                    (outcome = 'EVIDENCE_NOT_FOUND'
                     AND jsonb_typeof(gap_finding) = 'object'
                     AND gap_finding <> '{}'::jsonb)
                )
            ),
            CONSTRAINT uq_methodology_extraction_results_task
                UNIQUE (tenant_id, run_id, task_record_id, task_input_sha256),
            CONSTRAINT uq_methodology_extraction_results_scope
                UNIQUE (tenant_id, deal_id, run_id, result_id),
            CONSTRAINT uq_methodology_extraction_results_attempt
                UNIQUE (tenant_id, deal_id, run_id, attempt_id),
            CONSTRAINT uq_methodology_extraction_results_result_task
                UNIQUE (tenant_id, deal_id, run_id, result_id, task_record_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_methodology_extraction_results_task_input
        ON methodology_extraction_results (
            tenant_id, deal_id, run_id, task_record_id, task_input_sha256
        )
        """
    )


def _create_factual_outputs_and_links() -> None:
    op.execute(
        """
        CREATE TABLE methodology_result_factual_outputs (
            factual_output_record_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            result_id UUID NOT NULL,
            task_record_id UUID NOT NULL,
            output_ordinal INTEGER NOT NULL,
            factual_output_id UUID NOT NULL,
            typed_output_payload JSONB NOT NULL,
            typed_output_sha256 TEXT NOT NULL,
            accepted_source_span_ids UUID[] NOT NULL,
            accepted_locator_sha256s TEXT[] NOT NULL,
            accepted_locator_set_sha256 TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_result_factual_outputs_result FOREIGN KEY (
                tenant_id, deal_id, run_id, result_id, task_record_id
            ) REFERENCES methodology_extraction_results (
                tenant_id, deal_id, run_id, result_id, task_record_id
            ),
            CONSTRAINT ck_methodology_result_factual_outputs_payload CHECK (
                output_ordinal > 0
                AND jsonb_typeof(typed_output_payload) = 'object'
                AND typed_output_payload <> '{}'::jsonb
                AND cardinality(accepted_source_span_ids) > 0
                AND cardinality(accepted_locator_sha256s) > 0
                AND cardinality(accepted_source_span_ids) =
                    cardinality(accepted_locator_sha256s)
                AND array_position(accepted_source_span_ids, NULL) IS NULL
                AND array_position(accepted_locator_sha256s, NULL) IS NULL
                AND typed_output_sha256 ~ '^[0-9a-f]{64}$'
                AND accepted_locator_set_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_methodology_result_factual_outputs_id UNIQUE (
                tenant_id, deal_id, run_id, result_id, factual_output_id
            ),
            CONSTRAINT uq_methodology_result_factual_outputs_ordinal UNIQUE (
                tenant_id, deal_id, run_id, result_id, output_ordinal
            ),
            CONSTRAINT uq_methodology_result_factual_outputs_scope UNIQUE (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_result_claim_links (
            claim_link_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            result_id UUID NOT NULL,
            factual_output_record_id UUID NOT NULL,
            selection_id UUID NOT NULL,
            coverage_record_id TEXT NOT NULL,
            methodology_version_record_id UUID NOT NULL,
            methodology_question_record_id UUID NOT NULL,
            claim_id UUID NOT NULL,
            canonical_key_sha256 TEXT NOT NULL,
            reused_existing_claim BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_result_claim_links_output
            FOREIGN KEY (tenant_id, deal_id, run_id, result_id, factual_output_record_id)
            REFERENCES methodology_result_factual_outputs (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            ),
            CONSTRAINT fk_methodology_result_claim_links_claim FOREIGN KEY (
                tenant_id, deal_id, claim_id
            ) REFERENCES claims (tenant_id, deal_id, claim_id),
            CONSTRAINT fk_methodology_result_claim_links_coverage FOREIGN KEY (
                tenant_id, deal_id, run_id, selection_id, coverage_record_id,
                methodology_version_record_id, methodology_question_record_id
            ) REFERENCES methodology_coverage_records (
                tenant_id, deal_id, run_id, selection_id, coverage_record_id,
                methodology_version_record_id, methodology_question_record_id
            ),
            CONSTRAINT ck_methodology_result_claim_links_hash CHECK (
                canonical_key_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_methodology_result_claim_links_output UNIQUE (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            ),
            CONSTRAINT uq_methodology_result_claim_links_scope UNIQUE (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, claim_link_id
            ),
            CONSTRAINT uq_methodology_result_claim_links_claim UNIQUE (
                tenant_id, run_id, factual_output_record_id, claim_id
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_methodology_result_claim_links_coverage_scope
        ON methodology_result_claim_links (
            tenant_id, deal_id, run_id, selection_id, coverage_record_id,
            methodology_version_record_id, methodology_question_record_id
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_result_evidence_links (
            evidence_link_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            result_id UUID NOT NULL,
            factual_output_record_id UUID NOT NULL,
            claim_link_id UUID NOT NULL,
            evidence_id UUID NOT NULL,
            document_id UUID NOT NULL,
            source_span_id UUID NOT NULL,
            locator_sha256 TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_result_evidence_links_output
            FOREIGN KEY (tenant_id, deal_id, run_id, result_id, factual_output_record_id)
            REFERENCES methodology_result_factual_outputs (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            ),
            CONSTRAINT fk_methodology_result_evidence_links_claim_link FOREIGN KEY (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, claim_link_id
            ) REFERENCES methodology_result_claim_links (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, claim_link_id
            ),
            CONSTRAINT fk_methodology_result_evidence_links_evidence FOREIGN KEY (
                tenant_id, deal_id, evidence_id
            ) REFERENCES evidence_items (tenant_id, deal_id, evidence_id),
            CONSTRAINT fk_methodology_result_evidence_links_span FOREIGN KEY (
                tenant_id, deal_id, document_id, source_span_id
            ) REFERENCES document_spans (tenant_id, deal_id, document_id, span_id),
            CONSTRAINT ck_methodology_result_evidence_links_locator CHECK (
                locator_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_methodology_result_evidence_links_evidence UNIQUE (
                tenant_id, run_id, factual_output_record_id, evidence_id
            ),
            CONSTRAINT uq_methodology_result_evidence_links_span UNIQUE (
                tenant_id, run_id, factual_output_record_id, source_span_id
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_methodology_result_evidence_links_evidence_reverse_lookup
        ON methodology_result_evidence_links (tenant_id, deal_id, evidence_id)
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_result_sanad_links (
            sanad_link_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            result_id UUID NOT NULL,
            factual_output_record_id UUID NOT NULL,
            claim_link_id UUID NOT NULL,
            sanad_id UUID NOT NULL,
            primary_evidence_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_result_sanad_links_output
            FOREIGN KEY (tenant_id, deal_id, run_id, result_id, factual_output_record_id)
            REFERENCES methodology_result_factual_outputs (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            ),
            CONSTRAINT fk_methodology_result_sanad_links_claim_link FOREIGN KEY (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, claim_link_id
            ) REFERENCES methodology_result_claim_links (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, claim_link_id
            ),
            CONSTRAINT fk_methodology_result_sanad_links_sanad FOREIGN KEY (
                tenant_id, deal_id, sanad_id
            ) REFERENCES sanads (tenant_id, deal_id, sanad_id),
            CONSTRAINT fk_methodology_result_sanad_links_evidence FOREIGN KEY (
                tenant_id, deal_id, primary_evidence_id
            ) REFERENCES evidence_items (tenant_id, deal_id, evidence_id),
            CONSTRAINT uq_methodology_result_sanad_links_output UNIQUE (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            ),
            CONSTRAINT uq_methodology_result_sanad_links_scope UNIQUE (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, sanad_link_id
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_result_defect_links (
            defect_link_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            result_id UUID NOT NULL,
            factual_output_record_id UUID NOT NULL,
            sanad_link_id UUID NOT NULL,
            defect_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_result_defect_links_output
            FOREIGN KEY (tenant_id, deal_id, run_id, result_id, factual_output_record_id)
            REFERENCES methodology_result_factual_outputs (
                tenant_id, deal_id, run_id, result_id, factual_output_record_id
            ),
            CONSTRAINT fk_methodology_result_defect_links_sanad_link FOREIGN KEY (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, sanad_link_id
            ) REFERENCES methodology_result_sanad_links (
                tenant_id, deal_id, run_id, result_id,
                factual_output_record_id, sanad_link_id
            ),
            CONSTRAINT fk_methodology_result_defect_links_defect FOREIGN KEY (
                tenant_id, deal_id, defect_id
            ) REFERENCES defects (tenant_id, deal_id, defect_id),
            CONSTRAINT uq_methodology_result_defect_links_defect UNIQUE (
                tenant_id, run_id, defect_id
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_methodology_result_defect_links_defect_scope
        ON methodology_result_defect_links (tenant_id, deal_id, defect_id)
        """
    )


def _create_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_erasure_requested(
            checked_tenant UUID, checked_deal UUID
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM erasure_requests e
                WHERE e.tenant_id = checked_tenant
                  AND e.deal_id = checked_deal
                  AND e.status = 'REQUESTED'
            )
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_current_lease_is_valid(
            checked_tenant UUID, checked_deal UUID, checked_run UUID
        ) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
        DECLARE
            current_tenant_id UUID;
            current_deal_id UUID;
            current_run_id UUID;
            current_owner_id UUID;
            current_fencing_token BIGINT;
        BEGIN
            BEGIN
                current_tenant_id := NULLIF(
                    current_setting('idis.worker_lease_tenant_id', true), ''
                )::uuid;
                current_deal_id := NULLIF(
                    current_setting('idis.worker_lease_deal_id', true), ''
                )::uuid;
                current_run_id := NULLIF(
                    current_setting('idis.worker_lease_run_id', true), ''
                )::uuid;
                current_owner_id := NULLIF(
                    current_setting('idis.worker_lease_owner_id', true), ''
                )::uuid;
                current_fencing_token := NULLIF(
                    current_setting('idis.worker_lease_fencing_token', true), ''
                )::bigint;
            EXCEPTION WHEN OTHERS THEN
                RETURN false;
            END;
            RETURN EXISTS (
                SELECT 1 FROM runs r
                WHERE r.tenant_id = checked_tenant
                  AND r.deal_id = checked_deal
                  AND r.run_id = checked_run
                  AND r.status = 'RUNNING'
                  AND r.tenant_id = current_tenant_id
                  AND r.deal_id = current_deal_id
                  AND r.run_id = current_run_id
                  AND r.lease_owner_id = current_owner_id
                  AND r.lease_fencing_token = current_fencing_token
                  AND r.lease_expires_at > statement_timestamp()
            );
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_immutable_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT idis_task6_current_lease_is_valid(
                    NEW.tenant_id, NEW.deal_id, NEW.run_id
                ) THEN
                    RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE'
               AND idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'
                USING ERRCODE = '23514';
        END
        $$
        """
    )
    immutable_tables = (
        "methodology_extraction_tasks",
        "methodology_extraction_task_spans",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_claim_links",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
    )
    for table in immutable_tables:
        trigger_name = (
            "trg_methodology_result_factual_outputs_immutable"
            if table == "methodology_result_factual_outputs"
            else f"trg_{table}_immutable"
        )
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION idis_task6_immutable_guard()"
        )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION idis_task6_attempt_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            current_tenant_id UUID;
            current_deal_id UUID;
            current_run_id UUID;
            current_owner_id UUID;
            current_fencing_token BIGINT;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            BEGIN
                current_tenant_id := NULLIF(
                    current_setting('idis.worker_lease_tenant_id', true), ''
                )::uuid;
                current_deal_id := NULLIF(
                    current_setting('idis.worker_lease_deal_id', true), ''
                )::uuid;
                current_run_id := NULLIF(
                    current_setting('idis.worker_lease_run_id', true), ''
                )::uuid;
                current_owner_id := NULLIF(
                    current_setting('idis.worker_lease_owner_id', true), ''
                )::uuid;
                current_fencing_token := NULLIF(
                    current_setting('idis.worker_lease_fencing_token', true), ''
                )::bigint;
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
            END;
            IF NOT EXISTS (
                SELECT 1 FROM runs r
                WHERE r.tenant_id = NEW.tenant_id
                  AND r.deal_id = NEW.deal_id
                  AND r.run_id = NEW.run_id
                  AND r.status = 'RUNNING'
                  AND r.tenant_id = current_tenant_id
                  AND r.deal_id = current_deal_id
                  AND r.run_id = current_run_id
                  AND r.lease_owner_id = current_owner_id
                  AND r.lease_fencing_token = current_fencing_token
                  AND r.lease_expires_at > statement_timestamp()
            ) THEN
                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM run_steps s
                WHERE s.tenant_id = NEW.tenant_id
                  AND s.run_id = NEW.run_id
                  AND s.step_id = NEW.run_step_id
                  AND s.step_name = '{_STEP7_NAME.value}'
                  AND s.step_order = {_STEP7_ORDER}
                  AND s.status = 'RUNNING'
            ) THEN
                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'STARTED' THEN
                    RAISE EXCEPTION 'METHODOLOGY_ATTEMPT_MUST_START'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.lease_owner_id <> current_owner_id
                   OR NEW.lease_fencing_token <> current_fencing_token THEN
                    RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'STARTED'
               OR NEW.status NOT IN ('SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN')
               OR (to_jsonb(NEW) - ARRAY[
                    'status', 'observed_model', 'provider_request_id',
                    'provider_response_sha256', 'dispatched_call_sha256',
                    'completed_at', 'latency_ms',
                    'input_tokens', 'output_tokens', 'stop_reason',
                    'budget_terminal_state', 'receipt_sha256', 'safe_failure_code'
                  ]::text[])
                  IS DISTINCT FROM
                  (to_jsonb(OLD) - ARRAY[
                    'status', 'observed_model', 'provider_request_id',
                    'provider_response_sha256', 'dispatched_call_sha256',
                    'completed_at', 'latency_ms',
                    'input_tokens', 'output_tokens', 'stop_reason',
                    'budget_terminal_state', 'receipt_sha256', 'safe_failure_code'
                  ]::text[]) THEN
                RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status IN ('SUCCEEDED', 'FAILED')
               AND (
                    current_owner_id <> OLD.lease_owner_id
                    OR current_fencing_token <> OLD.lease_fencing_token
               ) THEN
                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
            END IF;
            IF NEW.status = 'OUTCOME_UNKNOWN'
               AND NOT (
                    (
                        current_owner_id = OLD.lease_owner_id
                        AND current_fencing_token = OLD.lease_fencing_token
                    )
                    OR current_fencing_token > OLD.lease_fencing_token
               ) THEN
                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_methodology_extraction_attempts_lifecycle
        BEFORE INSERT OR UPDATE OR DELETE ON methodology_extraction_attempts
        FOR EACH ROW EXECUTE FUNCTION idis_task6_attempt_guard()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_evidence_fields_valid(
            checked_source_system TEXT,
            checked_upstream_origin_id TEXT,
            checked_retrieval_timestamp TIMESTAMPTZ,
            checked_verification_status TEXT,
            checked_rationale JSONB,
            checked_updated_at TIMESTAMPTZ
        ) RETURNS BOOLEAN
        LANGUAGE sql IMMUTABLE PARALLEL SAFE SECURITY INVOKER AS $$
            SELECT NULLIF(btrim(checked_source_system), '') IS NOT NULL
               AND NULLIF(btrim(checked_upstream_origin_id), '') IS NOT NULL
               AND checked_retrieval_timestamp IS NOT NULL
               AND NULLIF(btrim(checked_verification_status), '') IS NOT NULL
               AND checked_rationale IS NOT NULL
               AND jsonb_typeof(checked_rationale) = 'object'
               AND checked_updated_at IS NOT NULL
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_link_lineage_guard()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER AS $$
        DECLARE
            evidence_claim_id UUID;
            evidence_source_span_id UUID;
            evidence_required_fields_valid BOOLEAN;
        BEGIN
            IF TG_TABLE_NAME = 'methodology_result_claim_links' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM methodology_result_factual_outputs f
                    JOIN methodology_extraction_tasks t
                      ON t.tenant_id = f.tenant_id
                     AND t.deal_id = f.deal_id
                     AND t.run_id = f.run_id
                     AND t.task_record_id = f.task_record_id
                    WHERE f.tenant_id = NEW.tenant_id
                      AND f.deal_id = NEW.deal_id
                      AND f.run_id = NEW.run_id
                      AND f.result_id = NEW.result_id
                      AND f.factual_output_record_id = NEW.factual_output_record_id
                      AND t.selection_id = NEW.selection_id
                      AND t.coverage_record_id = NEW.coverage_record_id
                      AND t.methodology_version_record_id =
                          NEW.methodology_version_record_id
                      AND t.methodology_question_record_id =
                          NEW.methodology_question_record_id
                ) THEN
                    RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            ELSIF TG_TABLE_NAME = 'methodology_result_evidence_links' THEN
                SELECT e.claim_id, e.source_span_id,
                       idis_task6_evidence_fields_valid(
                           e.source_system,
                           e.upstream_origin_id,
                           e.retrieval_timestamp,
                           e.verification_status,
                           e.rationale,
                           e.updated_at
                       )
                INTO evidence_claim_id, evidence_source_span_id,
                     evidence_required_fields_valid
                FROM evidence_items e
                WHERE e.tenant_id = NEW.tenant_id
                  AND e.deal_id = NEW.deal_id
                  AND e.evidence_id = NEW.evidence_id
                ORDER BY e.evidence_id
                FOR SHARE OF e;
                IF evidence_claim_id IS NULL
                   OR NOT COALESCE(evidence_required_fields_valid, false) THEN
                    RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM methodology_result_factual_outputs f
                    JOIN methodology_extraction_tasks t
                      ON t.tenant_id = f.tenant_id
                     AND t.deal_id = f.deal_id
                     AND t.run_id = f.run_id
                     AND t.task_record_id = f.task_record_id
                    JOIN methodology_extraction_task_spans admitted
                      ON admitted.tenant_id = t.tenant_id
                     AND admitted.deal_id = t.deal_id
                     AND admitted.run_id = t.run_id
                     AND admitted.task_record_id = t.task_record_id
                     AND admitted.document_id = NEW.document_id
                     AND admitted.span_id = NEW.source_span_id
                     AND admitted.locator_sha256 = NEW.locator_sha256
                    JOIN methodology_result_claim_links cl
                      ON cl.tenant_id = f.tenant_id
                     AND cl.deal_id = f.deal_id
                     AND cl.run_id = f.run_id
                     AND cl.result_id = f.result_id
                     AND cl.factual_output_record_id =
                         f.factual_output_record_id
                     AND cl.claim_link_id = NEW.claim_link_id
                    WHERE f.tenant_id = NEW.tenant_id
                      AND f.deal_id = NEW.deal_id
                      AND f.run_id = NEW.run_id
                      AND f.result_id = NEW.result_id
                      AND f.factual_output_record_id =
                          NEW.factual_output_record_id
                      AND evidence_claim_id = cl.claim_id
                      AND evidence_source_span_id = NEW.source_span_id
                ) THEN
                    RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            ELSIF TG_TABLE_NAME = 'methodology_result_sanad_links' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM methodology_result_claim_links cl
                    JOIN sanads s
                      ON s.tenant_id = NEW.tenant_id
                     AND s.deal_id = NEW.deal_id
                     AND s.sanad_id = NEW.sanad_id
                    JOIN methodology_result_evidence_links el
                      ON el.tenant_id = NEW.tenant_id
                     AND el.deal_id = NEW.deal_id
                     AND el.run_id = NEW.run_id
                     AND el.result_id = NEW.result_id
                     AND el.factual_output_record_id = NEW.factual_output_record_id
                     AND el.claim_link_id = NEW.claim_link_id
                     AND el.evidence_id = NEW.primary_evidence_id
                    WHERE cl.tenant_id = NEW.tenant_id
                      AND cl.deal_id = NEW.deal_id
                      AND cl.run_id = NEW.run_id
                      AND cl.result_id = NEW.result_id
                      AND cl.factual_output_record_id = NEW.factual_output_record_id
                      AND cl.claim_link_id = NEW.claim_link_id
                      AND s.claim_id = cl.claim_id
                      AND s.primary_evidence_id = NEW.primary_evidence_id
                ) THEN
                    RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            ELSIF TG_TABLE_NAME = 'methodology_result_defect_links' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM methodology_result_sanad_links sl
                    JOIN methodology_result_claim_links cl
                      ON cl.tenant_id = sl.tenant_id
                     AND cl.deal_id = sl.deal_id
                     AND cl.run_id = sl.run_id
                     AND cl.result_id = sl.result_id
                     AND cl.factual_output_record_id = sl.factual_output_record_id
                     AND cl.claim_link_id = sl.claim_link_id
                    JOIN sanads s
                      ON s.tenant_id = sl.tenant_id
                     AND s.deal_id = sl.deal_id
                     AND s.sanad_id = sl.sanad_id
                    JOIN defects d
                      ON d.tenant_id = NEW.tenant_id
                     AND d.deal_id = NEW.deal_id
                     AND d.defect_id = NEW.defect_id
                    WHERE sl.tenant_id = NEW.tenant_id
                      AND sl.deal_id = NEW.deal_id
                      AND sl.run_id = NEW.run_id
                      AND sl.result_id = NEW.result_id
                      AND sl.factual_output_record_id = NEW.factual_output_record_id
                      AND sl.sanad_link_id = NEW.sanad_link_id
                      AND s.claim_id = cl.claim_id
                      AND d.claim_id = cl.claim_id
                ) THEN
                    RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                USING ERRCODE = '23514';
        END
        $$
        """
    )
    for table in (
        "methodology_result_claim_links",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_lineage BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION idis_task6_link_lineage_guard()"
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_evidence_item_update_guard()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER AS $$
        DECLARE
            linked RECORD;
        BEGIN
            FOR linked IN
                SELECT l.run_id, l.factual_output_record_id,
                       l.evidence_link_id, l.source_span_id, cl.claim_id
                FROM methodology_result_evidence_links l
                JOIN methodology_result_claim_links cl
                  ON cl.tenant_id = l.tenant_id
                 AND cl.deal_id = l.deal_id
                 AND cl.run_id = l.run_id
                 AND cl.result_id = l.result_id
                 AND cl.factual_output_record_id = l.factual_output_record_id
                 AND cl.claim_link_id = l.claim_link_id
                WHERE l.tenant_id = OLD.tenant_id
                  AND l.deal_id = OLD.deal_id
                  AND l.evidence_id = OLD.evidence_id
                ORDER BY l.run_id, l.factual_output_record_id,
                         l.evidence_link_id
                FOR SHARE OF l
            LOOP
                IF NOT idis_task6_evidence_fields_valid(
                           NEW.source_system,
                           NEW.upstream_origin_id,
                           NEW.retrieval_timestamp,
                           NEW.verification_status,
                           NEW.rationale,
                           NEW.updated_at
                       )
                   OR NEW.claim_id IS DISTINCT FROM linked.claim_id
                   OR NEW.source_span_id IS DISTINCT FROM linked.source_span_id THEN
                    RAISE EXCEPTION 'METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED'
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidence_items_task6_link_guard
        BEFORE UPDATE OF source_system, upstream_origin_id,
                         retrieval_timestamp, verification_status,
                         rationale, updated_at, claim_id, source_span_id
        ON evidence_items
        FOR EACH ROW EXECUTE FUNCTION idis_task6_evidence_item_update_guard()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task6_validate_result_outputs()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            checked_result UUID;
            checked_outcome TEXT;
            output_count BIGINT;
        BEGIN
            checked_result := COALESCE(NEW.result_id, OLD.result_id);
            SELECT outcome INTO checked_outcome
            FROM methodology_extraction_results
            WHERE result_id = checked_result;
            IF checked_outcome IS NULL THEN
                RETURN NULL;
            END IF;
            SELECT count(*) INTO output_count
            FROM methodology_result_factual_outputs
            WHERE result_id = checked_result;
            IF (checked_outcome = 'EVIDENCE_FOUND' AND output_count = 0)
               OR (checked_outcome = 'EVIDENCE_NOT_FOUND' AND output_count <> 0) THEN
                RAISE EXCEPTION 'METHODOLOGY_RESULT_FACTUAL_OUTPUT_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_methodology_extraction_results_outputs
        AFTER INSERT OR UPDATE ON methodology_extraction_results
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION idis_task6_validate_result_outputs()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_methodology_result_factual_outputs_count
        AFTER INSERT OR UPDATE OR DELETE ON methodology_result_factual_outputs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION idis_task6_validate_result_outputs()
        """
    )


def _enable_canonical_rls() -> None:
    # Keep every relation explicit: this is a security-review surface and the
    # source-level catalog tripwire intentionally audits literal table names.
    op.execute("ALTER TABLE methodology_extraction_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_attempts FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_results ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_results FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_task_spans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_task_spans FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_extraction_tasks FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_claim_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_claim_links FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_defect_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_defect_links FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_evidence_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_evidence_links FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_factual_outputs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_factual_outputs FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_sanad_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_result_sanad_links FORCE ROW LEVEL SECURITY")

    op.execute(
        f"""CREATE POLICY methodology_extraction_attempts_tenant_isolation
        ON methodology_extraction_attempts AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_extraction_results_tenant_isolation
        ON methodology_extraction_results AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_extraction_task_spans_tenant_isolation
        ON methodology_extraction_task_spans AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_extraction_tasks_tenant_isolation
        ON methodology_extraction_tasks AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_result_claim_links_tenant_isolation
        ON methodology_result_claim_links AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_result_defect_links_tenant_isolation
        ON methodology_result_defect_links AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_result_evidence_links_tenant_isolation
        ON methodology_result_evidence_links AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_result_factual_outputs_tenant_isolation
        ON methodology_result_factual_outputs AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )
    op.execute(
        f"""CREATE POLICY methodology_result_sanad_links_tenant_isolation
        ON methodology_result_sanad_links AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE}) WITH CHECK ({CANONICAL_TENANT_PREDICATE})"""
    )


def assert_downgrade_is_lossless() -> None:
    """Refuse downgrade if Task-6 data or canonical repairs would be lost."""
    bind = op.get_bind()
    lock_tables = ", ".join((*TASK6_TABLES, "claims", "evidence_items", "sanads"))
    bind.execute(sa.text(f"LOCK TABLE {lock_tables} IN ACCESS EXCLUSIVE MODE"))
    for table in TASK6_TABLES:
        if int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()) != 0:
            raise RuntimeError("cannot downgrade Task 6 while durable extraction rows exist")
    canonical_count = bind.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*) FROM claims
                 WHERE normalization_rule_version IS NOT NULL
                    OR normalized_predicate IS NOT NULL
                    OR normalized_subject IS NOT NULL
                    OR normalized_period IS NOT NULL
                    OR normalized_unit IS NOT NULL
                    OR value_sha256 IS NOT NULL)
              + (SELECT count(*) FROM evidence_items
                 WHERE source_system IS NOT NULL
                    OR upstream_origin_id IS NOT NULL
                    OR retrieval_timestamp IS NOT NULL
                    OR verification_status IS NOT NULL
                    OR source_rank_subgrade IS NOT NULL
                    OR rationale IS NOT NULL
                    OR updated_at IS NOT NULL)
              + (SELECT count(*) FROM sanads)
            """
        )
    ).scalar_one()
    if int(canonical_count) != 0:
        raise RuntimeError("cannot downgrade Task 6 while canonical extensions are populated")


def upgrade() -> None:
    """Create Task-6 durable extraction persistence and scoped lineage."""
    _add_canonical_extensions()
    _add_scoped_keys()
    _create_tasks()
    _create_attempts_and_results()
    _create_factual_outputs_and_links()
    _create_guards()
    _enable_canonical_rls()


def downgrade() -> None:
    """Restore revision 0036 only when doing so is provably lossless."""
    assert_downgrade_is_lossless()
    op.execute("DROP TRIGGER trg_evidence_items_task6_link_guard ON evidence_items")
    for table in (
        "methodology_result_defect_links",
        "methodology_result_sanad_links",
        "methodology_result_evidence_links",
        "methodology_result_claim_links",
        "methodology_result_factual_outputs",
        "methodology_extraction_results",
        "methodology_extraction_attempts",
        "methodology_extraction_task_spans",
        "methodology_extraction_tasks",
    ):
        op.execute(f"DROP TABLE {table}")

    op.execute("DROP FUNCTION idis_task6_validate_result_outputs()")
    op.execute("DROP FUNCTION idis_task6_evidence_item_update_guard()")
    op.execute("DROP FUNCTION idis_task6_link_lineage_guard()")
    op.execute(
        "DROP FUNCTION idis_task6_evidence_fields_valid("
        "TEXT, TEXT, TIMESTAMPTZ, TEXT, JSONB, TIMESTAMPTZ)"
    )
    op.execute("DROP FUNCTION idis_task6_attempt_guard()")
    op.execute("DROP FUNCTION idis_task6_immutable_guard()")
    op.execute("DROP FUNCTION idis_task6_current_lease_is_valid(UUID, UUID, UUID)")
    op.execute("DROP FUNCTION idis_task6_erasure_requested(UUID, UUID)")

    op.execute("ALTER TABLE sanads DROP CONSTRAINT fk_sanads_task6_primary_evidence_scope")
    op.execute("ALTER TABLE defects DROP CONSTRAINT fk_defects_task6_claim_scope")
    op.execute("ALTER TABLE sanads DROP CONSTRAINT fk_sanads_task6_claim_scope")
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_items_task6_claim_scope")
    op.execute(
        "ALTER TABLE sanads ALTER COLUMN primary_evidence_id TYPE TEXT "
        "USING primary_evidence_id::text"
    )

    for index in (
        "ux_defects_task6_scope",
        "ux_sanads_task6_scope",
        "ux_evidence_items_task6_scope",
        "ux_claims_task6_scope",
        "ux_coverage_task6_scope",
        "ux_run_steps_task6_scope",
        "ux_document_spans_task6_scope",
        "ux_documents_task6_scope",
    ):
        op.execute(f"DROP INDEX {index}")

    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_task6_extensions")
    for column in (
        "updated_at",
        "rationale",
        "source_rank_subgrade",
        "verification_status",
        "retrieval_timestamp",
        "upstream_origin_id",
        "source_system",
    ):
        op.execute(f"ALTER TABLE evidence_items DROP COLUMN {column}")

    op.execute("DROP INDEX ux_claims_task6_normalized_key")
    op.execute("ALTER TABLE claims DROP CONSTRAINT ck_claims_task6_normalization")
    for column in (
        "value_sha256",
        "normalized_unit",
        "normalized_period",
        "normalized_subject",
        "normalized_predicate",
        "normalization_rule_version",
    ):
        op.execute(f"ALTER TABLE claims DROP COLUMN {column}")
