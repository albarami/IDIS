"""Durable bound, one-use strict preflight proofs (Slice100 Task 1).

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-15

Only proof admission state and the nullable run-to-proof link are introduced
here.  Slice100 Task 7 output, receipt, and consumer-edge schemas are
intentionally excluded pending their separate design review.

The proof row is immutable governance evidence made only of UUIDs, hashes,
versions, and timestamps.  It deliberately has no foreign key to the erasable
deal/run parents.  Its lifecycle trigger requires a live same-tenant deal at
insert time, enforces the reciprocal run link on first consumption, and permits
deletion only after that deal has been erased.  This preserves both strict
admission integrity and the existing full-deal removal contract.
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION idis_is_nonempty_sha256_jsonb_array(value JSONB)
        RETURNS BOOLEAN AS $$
            SELECT CASE
                WHEN jsonb_typeof(value) <> 'array' THEN FALSE
                ELSE jsonb_array_length(value) > 0
                     AND NOT EXISTS (
                         SELECT 1
                         FROM jsonb_array_elements(value) AS element(item)
                         WHERE jsonb_typeof(element.item) IS DISTINCT FROM 'string'
                            OR (element.item #>> '{}') !~ '^[0-9a-f]{64}$'
                     )
            END
        $$ LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strict_preflight_proofs (
            proof_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            corpus_hash TEXT NOT NULL CHECK (corpus_hash ~ '^[0-9a-f]{64}$'),
            strict_profile_generation TEXT NOT NULL
                CHECK (length(strict_profile_generation) > 0),
            strict_profile_hmac TEXT NOT NULL
                CHECK (strict_profile_hmac ~ '^[0-9a-f]{64}$'),
            pipeline_contract_version TEXT NOT NULL
                CHECK (length(pipeline_contract_version) > 0),
            pipeline_contract_hash TEXT NOT NULL
                CHECK (pipeline_contract_hash ~ '^[0-9a-f]{64}$'),
            expected_consumer_edge_rule_hash TEXT NOT NULL
                CHECK (expected_consumer_edge_rule_hash ~ '^[0-9a-f]{64}$'),
            static_decision_set_hash TEXT NOT NULL
                CHECK (static_decision_set_hash ~ '^[0-9a-f]{64}$'),
            methodology_version_hashes JSONB NOT NULL
                CHECK (idis_is_nonempty_sha256_jsonb_array(methodology_version_hashes)),
            promoted_pointer_hash TEXT NOT NULL
                CHECK (promoted_pointer_hash ~ '^[0-9a-f]{64}$'),
            provider_receipt_set_hash TEXT NOT NULL
                CHECK (provider_receipt_set_hash ~ '^[0-9a-f]{64}$'),
            dependency_receipt_set_hash TEXT NOT NULL
                CHECK (dependency_receipt_set_hash ~ '^[0-9a-f]{64}$'),
            binding_hash TEXT NOT NULL CHECK (binding_hash ~ '^[0-9a-f]{64}$'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > created_at),
            consumed_at TIMESTAMPTZ,
            consumed_by_run_id UUID,
            CHECK (
                (consumed_at IS NULL AND consumed_by_run_id IS NULL)
                OR (consumed_at IS NOT NULL AND consumed_by_run_id IS NOT NULL)
            ),
            UNIQUE (tenant_id, proof_id)
        );

        CREATE INDEX IF NOT EXISTS idx_strict_preflight_proofs_tenant_deal_expiry
            ON strict_preflight_proofs (tenant_id, deal_id, expires_at);
        """
    )

    op.execute(
        """
        ALTER TABLE strict_preflight_proofs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE strict_preflight_proofs FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS strict_preflight_proofs_tenant_isolation
            ON strict_preflight_proofs;
        CREATE POLICY strict_preflight_proofs_tenant_isolation
            ON strict_preflight_proofs
            USING (
                NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
            );
        """
    )

    # Install the nullable run link before compiling the immutability trigger,
    # whose first-consumption guard verifies the reciprocal run reference.
    op.execute(
        """
        ALTER TABLE runs
            ADD COLUMN IF NOT EXISTS strict_preflight_proof_id UUID;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_prevent_strict_preflight_proof_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.consumed_at IS NULL
                   AND NEW.consumed_by_run_id IS NULL
                THEN
                    -- Hold the parent row through proof-insert commit.  Without this
                    -- lock, a concurrent deal erasure could commit between a plain
                    -- existence check and the proof insert, creating an orphan.
                    PERFORM 1
                    FROM deals
                    WHERE deals.tenant_id = NEW.tenant_id
                      AND deals.deal_id = NEW.deal_id
                    FOR KEY SHARE;
                    IF FOUND THEN
                        RETURN NEW;
                    END IF;
                END IF;

                RAISE EXCEPTION
                    'Proof insert requires unconsumed state and a live same-tenant deal';
            ELSIF TG_OP = 'UPDATE' THEN
                IF NEW.proof_id IS NOT DISTINCT FROM OLD.proof_id
                   AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id
                   AND NEW.deal_id IS NOT DISTINCT FROM OLD.deal_id
                   AND NEW.corpus_hash IS NOT DISTINCT FROM OLD.corpus_hash
                   AND NEW.strict_profile_generation
                       IS NOT DISTINCT FROM OLD.strict_profile_generation
                   AND NEW.strict_profile_hmac IS NOT DISTINCT FROM OLD.strict_profile_hmac
                   AND NEW.pipeline_contract_version
                       IS NOT DISTINCT FROM OLD.pipeline_contract_version
                   AND NEW.pipeline_contract_hash
                       IS NOT DISTINCT FROM OLD.pipeline_contract_hash
                   AND NEW.expected_consumer_edge_rule_hash
                       IS NOT DISTINCT FROM OLD.expected_consumer_edge_rule_hash
                   AND NEW.static_decision_set_hash
                       IS NOT DISTINCT FROM OLD.static_decision_set_hash
                   AND NEW.methodology_version_hashes
                       IS NOT DISTINCT FROM OLD.methodology_version_hashes
                   AND NEW.promoted_pointer_hash IS NOT DISTINCT FROM OLD.promoted_pointer_hash
                   AND NEW.provider_receipt_set_hash
                       IS NOT DISTINCT FROM OLD.provider_receipt_set_hash
                   AND NEW.dependency_receipt_set_hash
                       IS NOT DISTINCT FROM OLD.dependency_receipt_set_hash
                   AND NEW.binding_hash IS NOT DISTINCT FROM OLD.binding_hash
                   AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
                   AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at
                   AND OLD.consumed_at IS NULL
                   AND OLD.consumed_by_run_id IS NULL
                   AND OLD.expires_at > statement_timestamp()
                   AND NEW.consumed_at IS NOT NULL
                   AND NEW.consumed_at <= OLD.expires_at
                   AND NEW.consumed_by_run_id IS NOT NULL
                   AND EXISTS (
                       SELECT 1
                       FROM runs
                       WHERE runs.run_id = NEW.consumed_by_run_id
                         AND runs.tenant_id = NEW.tenant_id
                         AND runs.deal_id = NEW.deal_id
                         AND runs.strict_preflight_proof_id = NEW.proof_id
                   )
                THEN
                    RETURN NEW;
                END IF;

                RAISE EXCEPTION
                    'Strict proof immutable; only first reciprocal consumption is allowed';
            ELSIF TG_OP = 'DELETE' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM deals
                    WHERE deals.tenant_id = OLD.tenant_id
                      AND deals.deal_id = OLD.deal_id
                )
                THEN
                    RETURN OLD;
                END IF;

                RAISE EXCEPTION
                    'Strict preflight proof cannot be deleted while its same-tenant deal exists';
            END IF;

            RAISE EXCEPTION
                'Unsupported strict preflight proof mutation';
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS strict_preflight_proofs_immutability
            ON strict_preflight_proofs;
        CREATE TRIGGER strict_preflight_proofs_immutability
            BEFORE INSERT OR UPDATE OR DELETE ON strict_preflight_proofs
            FOR EACH ROW
            EXECUTE FUNCTION idis_prevent_strict_preflight_proof_mutation();
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_runs_strict_preflight_proof_tenant'
                  AND conrelid = 'runs'::regclass
            ) THEN
                ALTER TABLE runs
                    ADD CONSTRAINT fk_runs_strict_preflight_proof_tenant
                    FOREIGN KEY (tenant_id, strict_preflight_proof_id)
                    REFERENCES strict_preflight_proofs (tenant_id, proof_id);
            END IF;
        END $$;

        CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_strict_preflight_proof_id
            ON runs (strict_preflight_proof_id)
            WHERE strict_preflight_proof_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs
            DROP CONSTRAINT IF EXISTS fk_runs_strict_preflight_proof_tenant;
        DROP INDEX IF EXISTS ux_runs_strict_preflight_proof_id;
        ALTER TABLE runs DROP COLUMN IF EXISTS strict_preflight_proof_id;

        DROP TRIGGER IF EXISTS strict_preflight_proofs_immutability
            ON strict_preflight_proofs;
        DROP TABLE IF EXISTS strict_preflight_proofs CASCADE;
        DROP FUNCTION IF EXISTS idis_prevent_strict_preflight_proof_mutation();
        DROP FUNCTION IF EXISTS idis_is_nonempty_sha256_jsonb_array(JSONB);
        """
    )
