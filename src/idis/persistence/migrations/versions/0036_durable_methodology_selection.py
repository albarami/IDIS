"""Persist immutable CDD/FDD methodology versions and run selections.

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-20

Task 5 deliberately adds only the four tenant-bearing methodology relations.
Versions and questions are immutable after import, a version has one narrowly
defined IMPORTED-to-FROZEN transition, and every FULL-run selection binds one
frozen CDD version plus one frozen FDD version.  Coverage identity is fenced by
that immutable selection while its answer/status payload remains updateable by
later run steps.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

TASK5_TABLES = (
    "methodology_coverage_records",
    "methodology_questions",
    "methodology_versions",
    "run_methodology_selections",
)

CANONICAL_TENANT_PREDICATE = """\
NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
"""


def _create_tables() -> None:
    op.execute(
        """
        CREATE TABLE methodology_versions (
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
            methodology_version_record_id UUID PRIMARY KEY,
            methodology_version_id TEXT NOT NULL,
            methodology_id TEXT NOT NULL,
            methodology_type TEXT NOT NULL,
            version_label TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_alias TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_byte_count BIGINT NOT NULL,
            source_contract_hash TEXT NOT NULL,
            source_trace_digest TEXT NOT NULL,
            registry_hash TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            question_count INTEGER NOT NULL,
            source_section_count INTEGER NOT NULL,
            header_row INTEGER,
            status TEXT NOT NULL,
            imported_by_actor_id TEXT NOT NULL,
            import_audit_event_id UUID NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            frozen_by_actor_id TEXT,
            freeze_audit_event_id UUID,
            frozen_at TIMESTAMPTZ,
            CONSTRAINT ck_methodology_versions_family_contract CHECK (
                (methodology_type = 'commercial_dd'
                 AND methodology_id = 'commercial_dd'
                 AND source_type = 'checked_in_template'
                 AND question_count = 21
                 AND header_row IS NULL)
                OR
                (methodology_type = 'financial_dd'
                 AND methodology_id = 'financial_dd'
                 AND source_type = 'operator_workbook'
                 AND question_count = 79
                 AND header_row IS NOT NULL AND header_row > 0)
            ),
            CONSTRAINT ck_methodology_versions_freeze_state CHECK (
                (status = 'IMPORTED'
                 AND frozen_by_actor_id IS NULL
                 AND freeze_audit_event_id IS NULL
                 AND frozen_at IS NULL)
                OR
                (status = 'FROZEN'
                 AND NULLIF(btrim(frozen_by_actor_id), '') IS NOT NULL
                 AND freeze_audit_event_id IS NOT NULL
                 AND frozen_at IS NOT NULL)
            ),
            CONSTRAINT ck_methodology_versions_hashes CHECK (
                source_sha256 ~ '^[0-9a-f]{64}$'
                AND source_contract_hash ~ '^[0-9a-f]{64}$'
                AND source_trace_digest ~ '^[0-9a-f]{64}$'
                AND registry_hash ~ '^[0-9a-f]{64}$'
                AND version_hash ~ '^[0-9a-f]{64}$'
                AND source_byte_count > 0
                AND source_section_count > 0
                AND NULLIF(btrim(methodology_version_id), '') IS NOT NULL
                AND NULLIF(btrim(version_label), '') IS NOT NULL
                AND NULLIF(btrim(source_alias), '') IS NOT NULL
                AND NULLIF(btrim(imported_by_actor_id), '') IS NOT NULL
            ),
            CONSTRAINT uq_methodology_versions_tenant_external_id
                UNIQUE (tenant_id, methodology_version_id),
            CONSTRAINT uq_methodology_versions_tenant_record_hash
                UNIQUE (tenant_id, methodology_version_record_id, version_hash),
            CONSTRAINT uq_methodology_versions_question_fk
                UNIQUE (
                    tenant_id, methodology_version_record_id,
                    methodology_version_id, methodology_id,
                    methodology_type, source_sha256
                ),
            CONSTRAINT uq_methodology_versions_selection_fk
                UNIQUE (
                    tenant_id, methodology_version_record_id,
                    methodology_version_id, version_hash
                )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_questions (
            tenant_id UUID NOT NULL,
            methodology_question_record_id UUID PRIMARY KEY,
            methodology_version_record_id UUID NOT NULL,
            methodology_version_id TEXT NOT NULL,
            methodology_question_id TEXT NOT NULL,
            methodology_id TEXT NOT NULL,
            methodology_type TEXT NOT NULL,
            question_ordinal INTEGER NOT NULL,
            section TEXT NOT NULL,
            source_sheet_or_section TEXT NOT NULL,
            source_row_number INTEGER,
            source_anchor_cell TEXT,
            source_sha256 TEXT NOT NULL,
            semantic_row_hash TEXT NOT NULL,
            question_payload_hash TEXT NOT NULL,
            question_payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_questions_version FOREIGN KEY (
                tenant_id, methodology_version_record_id,
                methodology_version_id, methodology_id,
                methodology_type, source_sha256
            ) REFERENCES methodology_versions (
                tenant_id, methodology_version_record_id,
                methodology_version_id, methodology_id,
                methodology_type, source_sha256
            ),
            CONSTRAINT ck_methodology_questions_trace CHECK (
                question_ordinal > 0
                AND NULLIF(btrim(section), '') IS NOT NULL
                AND NULLIF(btrim(source_sheet_or_section), '') IS NOT NULL
                AND source_sha256 ~ '^[0-9a-f]{64}$'
                AND semantic_row_hash ~ '^[0-9a-f]{64}$'
                AND question_payload_hash ~ '^[0-9a-f]{64}$'
                AND jsonb_typeof(question_payload) = 'object'
                AND (
                    (methodology_type = 'commercial_dd'
                     AND source_row_number IS NULL
                     AND source_anchor_cell IS NULL)
                    OR
                    (methodology_type = 'financial_dd'
                     AND source_row_number IS NOT NULL AND source_row_number > 0
                     AND source_anchor_cell ~ '^[A-Z]{1,3}[1-9][0-9]*$')
                )
            ),
            CONSTRAINT uq_methodology_questions_semantic_row
                UNIQUE (tenant_id, methodology_version_record_id, semantic_row_hash),
            CONSTRAINT uq_methodology_questions_source_trace
                UNIQUE (
                    tenant_id, methodology_version_record_id,
                    source_sheet_or_section, source_row_number
                ),
            CONSTRAINT uq_methodology_questions_external_id
                UNIQUE (
                    tenant_id, methodology_version_record_id,
                    methodology_question_id
                ),
            CONSTRAINT uq_methodology_questions_ordinal
                UNIQUE (
                    tenant_id, methodology_version_record_id,
                    question_ordinal
                ),
            CONSTRAINT uq_methodology_questions_coverage_fk
                UNIQUE (
                    tenant_id, methodology_version_record_id,
                    methodology_version_id, methodology_question_record_id,
                    methodology_question_id, methodology_type
                )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE run_methodology_selections (
            selection_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            commercial_dd_version_record_id UUID NOT NULL,
            commercial_methodology_version_id TEXT NOT NULL,
            commercial_version_hash TEXT NOT NULL,
            financial_dd_version_record_id UUID NOT NULL,
            financial_methodology_version_id TEXT NOT NULL,
            financial_version_hash TEXT NOT NULL,
            selection_hash TEXT NOT NULL,
            selected_question_count INTEGER NOT NULL,
            created_by_actor_id TEXT NOT NULL,
            selection_audit_event_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_run_methodology_selections_run_scope FOREIGN KEY (
                tenant_id, deal_id, run_id
            ) REFERENCES runs (tenant_id, deal_id, run_id),
            CONSTRAINT fk_run_methodology_selections_cdd_version FOREIGN KEY (
                tenant_id, commercial_dd_version_record_id,
                commercial_methodology_version_id, commercial_version_hash
            ) REFERENCES methodology_versions (
                tenant_id, methodology_version_record_id,
                methodology_version_id, version_hash
            ),
            CONSTRAINT fk_run_methodology_selections_fdd_version FOREIGN KEY (
                tenant_id, financial_dd_version_record_id,
                financial_methodology_version_id, financial_version_hash
            ) REFERENCES methodology_versions (
                tenant_id, methodology_version_record_id,
                methodology_version_id, version_hash
            ),
            CONSTRAINT ck_run_methodology_selections_exact_pair CHECK (
                commercial_dd_version_record_id <> financial_dd_version_record_id
                AND commercial_methodology_version_id <> financial_methodology_version_id
                AND commercial_version_hash ~ '^[0-9a-f]{64}$'
                AND financial_version_hash ~ '^[0-9a-f]{64}$'
                AND selection_hash ~ '^[0-9a-f]{64}$'
                AND selected_question_count = 100
                AND NULLIF(btrim(created_by_actor_id), '') IS NOT NULL
            ),
            CONSTRAINT uq_run_methodology_selections_tenant_run
                UNIQUE (tenant_id, run_id),
            CONSTRAINT uq_run_methodology_selections_scope
                UNIQUE (tenant_id, deal_id, run_id, selection_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodology_coverage_records (
            coverage_record_id TEXT PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            selection_id UUID NOT NULL,
            methodology_version_record_id UUID NOT NULL,
            methodology_version_id TEXT NOT NULL,
            methodology_question_record_id UUID NOT NULL,
            methodology_question_id TEXT NOT NULL,
            methodology_type TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_code TEXT,
            evidence_links JSONB NOT NULL DEFAULT '[]'::jsonb,
            answer JSONB,
            conflict_ids TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
            defect_ids TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_methodology_coverage_selection_scope FOREIGN KEY (
                tenant_id, deal_id, run_id, selection_id
            ) REFERENCES run_methodology_selections (
                tenant_id, deal_id, run_id, selection_id
            ),
            CONSTRAINT fk_methodology_coverage_question FOREIGN KEY (
                tenant_id, methodology_version_record_id,
                methodology_version_id, methodology_question_record_id,
                methodology_question_id, methodology_type
            ) REFERENCES methodology_questions (
                tenant_id, methodology_version_record_id,
                methodology_version_id, methodology_question_record_id,
                methodology_question_id, methodology_type
            ),
            CONSTRAINT ck_methodology_coverage_payloads CHECK (
                coverage_record_id ~ '^mc_[0-9a-f]{24}$'
                AND methodology_type IN ('commercial_dd', 'financial_dd')
                AND status IN (
                    'not_started', 'evidence_missing', 'unsupported_source',
                    'extracted', 'partially_answered', 'answered',
                    'contradicted', 'not_applicable', 'blocked'
                )
                AND jsonb_typeof(evidence_links) = 'array'
                AND (answer IS NULL OR jsonb_typeof(answer) = 'object')
                AND array_position(conflict_ids, NULL) IS NULL
                AND array_position(defect_ids, NULL) IS NULL
                AND (reason_code IS NULL OR NULLIF(btrim(reason_code), '') IS NOT NULL)
            ),
            CONSTRAINT uq_methodology_coverage_run_question
                UNIQUE (tenant_id, run_id, methodology_question_record_id)
        )
        """
    )


def _create_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task5_methodology_versions_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actual_question_count integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'METHODOLOGY_VERSION_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status <> 'IMPORTED' OR NEW.status <> 'FROZEN'
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.methodology_version_record_id IS DISTINCT FROM
                  OLD.methodology_version_record_id
               OR NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id
               OR NEW.methodology_id IS DISTINCT FROM OLD.methodology_id
               OR NEW.methodology_type IS DISTINCT FROM OLD.methodology_type
               OR NEW.version_label IS DISTINCT FROM OLD.version_label
               OR NEW.source_type IS DISTINCT FROM OLD.source_type
               OR NEW.source_alias IS DISTINCT FROM OLD.source_alias
               OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
               OR NEW.source_byte_count IS DISTINCT FROM OLD.source_byte_count
               OR NEW.source_contract_hash IS DISTINCT FROM OLD.source_contract_hash
               OR NEW.source_trace_digest IS DISTINCT FROM OLD.source_trace_digest
               OR NEW.registry_hash IS DISTINCT FROM OLD.registry_hash
               OR NEW.version_hash IS DISTINCT FROM OLD.version_hash
               OR NEW.question_count IS DISTINCT FROM OLD.question_count
               OR NEW.source_section_count IS DISTINCT FROM OLD.source_section_count
               OR NEW.header_row IS DISTINCT FROM OLD.header_row
               OR NEW.imported_by_actor_id IS DISTINCT FROM OLD.imported_by_actor_id
               OR NEW.import_audit_event_id IS DISTINCT FROM OLD.import_audit_event_id
               OR NEW.imported_at IS DISTINCT FROM OLD.imported_at
               OR NEW.frozen_by_actor_id IS NULL
               OR NEW.freeze_audit_event_id IS NULL
               OR NEW.frozen_at IS NULL THEN
                RAISE EXCEPTION 'METHODOLOGY_VERSION_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*) INTO actual_question_count
            FROM methodology_questions
            WHERE tenant_id = OLD.tenant_id
              AND methodology_version_record_id = OLD.methodology_version_record_id;
            IF actual_question_count <> OLD.question_count THEN
                RAISE EXCEPTION 'METHODOLOGY_VERSION_QUESTION_COUNT_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_methodology_versions_guard
        BEFORE UPDATE OR DELETE ON methodology_versions
        FOR EACH ROW EXECUTE FUNCTION idis_task5_methodology_versions_guard()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task5_methodology_questions_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'METHODOLOGY_QUESTION_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM methodology_versions v
                WHERE v.tenant_id = NEW.tenant_id
                  AND v.methodology_version_record_id = NEW.methodology_version_record_id
                  AND v.methodology_version_id = NEW.methodology_version_id
                  AND v.methodology_id = NEW.methodology_id
                  AND v.methodology_type = NEW.methodology_type
                  AND v.source_sha256 = NEW.source_sha256
                  AND v.status = 'IMPORTED'
            ) THEN
                RAISE EXCEPTION 'METHODOLOGY_QUESTION_PARENT_NOT_IMPORTABLE'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_methodology_questions_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON methodology_questions
        FOR EACH ROW EXECUTE FUNCTION idis_task5_methodology_questions_guard()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task5_selection_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            commercial_count integer;
            financial_count integer;
        BEGIN
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM erasure_requests e
                WHERE e.tenant_id = OLD.tenant_id
                  AND e.deal_id = OLD.deal_id
                  AND e.status = 'REQUESTED'
            ) THEN
                RETURN OLD;
            END IF;
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            SELECT question_count INTO commercial_count
            FROM methodology_versions
            WHERE tenant_id = NEW.tenant_id
              AND methodology_version_record_id = NEW.commercial_dd_version_record_id
              AND methodology_version_id = NEW.commercial_methodology_version_id
              AND version_hash = NEW.commercial_version_hash
              AND methodology_type = 'commercial_dd'
              AND status = 'FROZEN';
            SELECT question_count INTO financial_count
            FROM methodology_versions
            WHERE tenant_id = NEW.tenant_id
              AND methodology_version_record_id = NEW.financial_dd_version_record_id
              AND methodology_version_id = NEW.financial_methodology_version_id
              AND version_hash = NEW.financial_version_hash
              AND methodology_type = 'financial_dd'
              AND status = 'FROZEN';
            IF commercial_count IS DISTINCT FROM 21
               OR financial_count IS DISTINCT FROM 79
               OR commercial_count + financial_count <> NEW.selected_question_count THEN
                RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_methodology_selections_guard
        BEFORE INSERT OR UPDATE OR DELETE ON run_methodology_selections
        FOR EACH ROW EXECUTE FUNCTION idis_task5_selection_guard()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_task5_coverage_scope_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE' AND (
                NEW.coverage_record_id IS DISTINCT FROM OLD.coverage_record_id
                OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                OR NEW.deal_id IS DISTINCT FROM OLD.deal_id
                OR NEW.run_id IS DISTINCT FROM OLD.run_id
                OR NEW.selection_id IS DISTINCT FROM OLD.selection_id
                OR NEW.methodology_version_record_id IS DISTINCT FROM
                   OLD.methodology_version_record_id
                OR NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id
                OR NEW.methodology_question_record_id IS DISTINCT FROM
                   OLD.methodology_question_record_id
                OR NEW.methodology_question_id IS DISTINCT FROM OLD.methodology_question_id
                OR NEW.methodology_type IS DISTINCT FROM OLD.methodology_type
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM run_methodology_selections s
                JOIN methodology_questions q
                  ON q.tenant_id = NEW.tenant_id
                 AND q.methodology_version_record_id = NEW.methodology_version_record_id
                 AND q.methodology_version_id = NEW.methodology_version_id
                 AND q.methodology_question_record_id = NEW.methodology_question_record_id
                 AND q.methodology_question_id = NEW.methodology_question_id
                 AND q.methodology_type = NEW.methodology_type
                WHERE s.tenant_id = NEW.tenant_id
                  AND s.deal_id = NEW.deal_id
                  AND s.run_id = NEW.run_id
                  AND s.selection_id = NEW.selection_id
                  AND (
                    (q.methodology_type = 'commercial_dd'
                     AND q.methodology_version_record_id = s.commercial_dd_version_record_id)
                    OR
                    (q.methodology_type = 'financial_dd'
                     AND q.methodology_version_record_id = s.financial_dd_version_record_id)
                  )
            ) THEN
                RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            NEW.updated_at := now();
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_methodology_coverage_scope
        BEFORE INSERT OR UPDATE OR DELETE ON methodology_coverage_records
        FOR EACH ROW EXECUTE FUNCTION idis_task5_coverage_scope_guard()
        """
    )


def _enable_canonical_rls() -> None:
    op.execute("ALTER TABLE methodology_coverage_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_coverage_records FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_questions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_questions FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE methodology_versions FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE run_methodology_selections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE run_methodology_selections FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY methodology_coverage_records_tenant_isolation
        ON methodology_coverage_records AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE})
        WITH CHECK ({CANONICAL_TENANT_PREDICATE})
        """
    )
    op.execute(
        f"""
        CREATE POLICY methodology_questions_tenant_isolation
        ON methodology_questions AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE})
        WITH CHECK ({CANONICAL_TENANT_PREDICATE})
        """
    )
    op.execute(
        f"""
        CREATE POLICY methodology_versions_tenant_isolation
        ON methodology_versions AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE})
        WITH CHECK ({CANONICAL_TENANT_PREDICATE})
        """
    )
    op.execute(
        f"""
        CREATE POLICY run_methodology_selections_tenant_isolation
        ON run_methodology_selections AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({CANONICAL_TENANT_PREDICATE})
        WITH CHECK ({CANONICAL_TENANT_PREDICATE})
        """
    )


def assert_downgrade_is_lossless() -> None:
    """Refuse downgrade when any Task 5 durable row would be discarded."""
    bind = op.get_bind()
    table_list = ", ".join(TASK5_TABLES)
    bind.execute(sa.text(f"LOCK TABLE {table_list} IN ACCESS EXCLUSIVE MODE"))
    for table in TASK5_TABLES:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if int(count) != 0:
            raise RuntimeError("cannot downgrade Task 5 while durable methodology rows exist")


def upgrade() -> None:
    """Create the frozen methodology and run-selection persistence boundary."""
    op.execute(
        "CREATE UNIQUE INDEX ux_runs_task5_tenant_deal_run ON runs (tenant_id, deal_id, run_id)"
    )
    _create_tables()
    _create_guards()
    _enable_canonical_rls()


def downgrade() -> None:
    """Drop Task 5 only when the four durable relations are empty."""
    assert_downgrade_is_lossless()
    op.execute("DROP TABLE methodology_coverage_records")
    op.execute("DROP TABLE run_methodology_selections")
    op.execute("DROP TABLE methodology_questions")
    op.execute("DROP TABLE methodology_versions")
    op.execute("DROP FUNCTION idis_task5_coverage_scope_guard()")
    op.execute("DROP FUNCTION idis_task5_selection_guard()")
    op.execute("DROP FUNCTION idis_task5_methodology_questions_guard()")
    op.execute("DROP FUNCTION idis_task5_methodology_versions_guard()")
    op.execute("DROP INDEX ux_runs_task5_tenant_deal_run")
