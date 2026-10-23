"""Durable dependency-governance state for Slice100 Task 2.

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-15

Only the two approved governance tables are introduced.  Credential rotation is
made version-preserving in the existing table, and the existing provider usage
table gains the balances required for reservation/consumption/refund.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def _lock_changed_tables() -> None:
    op.get_bind().execute(
        sa.text(
            "LOCK TABLE enrichment_credentials, provider_budget_usage IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def _lock_downgrade_tables() -> None:
    """Exclude imports and balance changes before checking losslessness."""
    op.get_bind().execute(
        sa.text(
            "LOCK TABLE enrichment_credentials, provider_budget_usage, "
            "enrichment_rights_approvals, provider_budget_policies "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )


def _backfill_credential_version_ids() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT tenant_id, connector_id FROM enrichment_credentials "
            "WHERE credential_version_id IS NULL FOR UPDATE"
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE enrichment_credentials "
                "SET credential_version_id = CAST(:credential_version_id AS UUID) "
                "WHERE tenant_id = :tenant_id AND connector_id = :connector_id "
                "AND credential_version_id IS NULL"
            ),
            {
                "credential_version_id": str(uuid.uuid4()),
                "tenant_id": row.tenant_id,
                "connector_id": row.connector_id,
            },
        )


def assert_downgrade_is_lossless() -> None:
    """Refuse a downgrade that would silently discard Task 2 state."""
    bind = op.get_bind()
    governance_count = bind.execute(
        sa.text(
            "SELECT (SELECT COUNT(*) FROM enrichment_rights_approvals) + "
            "(SELECT COUNT(*) FROM provider_budget_policies)"
        )
    ).scalar_one()
    if int(governance_count) != 0:
        raise RuntimeError("cannot downgrade Task 2 while durable governance rows exist")

    versioned_pairs = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT tenant_id, connector_id FROM enrichment_credentials "
            "GROUP BY tenant_id, connector_id HAVING COUNT(*) > 1"
            ") AS versioned_credentials"
        )
    ).scalar_one()
    if int(versioned_pairs) != 0:
        raise RuntimeError("cannot downgrade Task 2 while credential version history exists")

    extended_usage = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM provider_budget_usage WHERE policy_hash IS NOT NULL "
            "OR reserved_calls <> 0 OR reserved_usd <> 0 OR consumed_usd <> 0 "
            "OR reserved_input_tokens <> 0 OR consumed_input_tokens <> 0 "
            "OR reserved_output_tokens <> 0 OR consumed_output_tokens <> 0 "
            "OR reserved_embedding_tokens <> 0 OR consumed_embedding_tokens <> 0"
        )
    ).scalar_one()
    if int(extended_usage) != 0:
        raise RuntimeError("cannot downgrade Task 2 while governed budget balances exist")


def upgrade() -> None:
    _lock_changed_tables()

    op.execute("ALTER TABLE enrichment_credentials ADD COLUMN credential_version_id UUID")
    _backfill_credential_version_ids()
    op.execute(
        """
        ALTER TABLE enrichment_credentials
            ALTER COLUMN credential_version_id SET NOT NULL,
            DROP CONSTRAINT enrichment_credentials_pk,
            ADD CONSTRAINT enrichment_credentials_pk
                PRIMARY KEY (tenant_id, connector_id, credential_version_id);

        CREATE UNIQUE INDEX ux_enrichment_credentials_one_active
            ON enrichment_credentials (tenant_id, connector_id)
            WHERE rotated_at IS NULL AND revoked_at IS NULL;

        ALTER TABLE enrichment_credentials ENABLE ROW LEVEL SECURITY;
        ALTER TABLE enrichment_credentials FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS enrichment_credentials_tenant_isolation
            ON enrichment_credentials;
        CREATE POLICY enrichment_credentials_tenant_isolation
            ON enrichment_credentials
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

    op.execute(
        """
        ALTER TABLE provider_budget_usage
            ADD COLUMN policy_hash TEXT,
            ADD COLUMN reserved_calls BIGINT NOT NULL DEFAULT 0 CHECK (reserved_calls >= 0),
            ADD COLUMN reserved_usd NUMERIC(20, 8) NOT NULL DEFAULT 0
                CHECK (reserved_usd >= 0),
            ADD COLUMN consumed_usd NUMERIC(20, 8) NOT NULL DEFAULT 0
                CHECK (consumed_usd >= 0),
            ADD COLUMN reserved_input_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (reserved_input_tokens >= 0),
            ADD COLUMN consumed_input_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (consumed_input_tokens >= 0),
            ADD COLUMN reserved_output_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (reserved_output_tokens >= 0),
            ADD COLUMN consumed_output_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (consumed_output_tokens >= 0),
            ADD COLUMN reserved_embedding_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (reserved_embedding_tokens >= 0),
            ADD COLUMN consumed_embedding_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (consumed_embedding_tokens >= 0),
            ADD CONSTRAINT ck_provider_budget_usage_policy_hash
                CHECK (policy_hash IS NULL OR policy_hash ~ '^[0-9a-f]{64}$');
        """
    )

    op.execute(
        """
        CREATE TABLE enrichment_rights_approvals (
            rights_approval_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider_id TEXT NOT NULL
                CHECK (provider_id IN ('finnhub', 'fmp', 'wayback', 'google_news_rss')),
            environment TEXT NOT NULL CHECK (environment = 'PROD'),
            terms_version_hash TEXT NOT NULL
                CHECK (terms_version_hash ~ '^[0-9a-f]{64}$'),
            status TEXT NOT NULL CHECK (status IN ('APPROVED', 'REVOKED')),
            rights_class TEXT NOT NULL CHECK (rights_class IN ('RED', 'YELLOW')),
            license_scope TEXT NOT NULL CHECK (length(btrim(license_scope)) > 0),
            attribution_policy_version TEXT,
            no_store_policy_version TEXT,
            evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
            signer_id TEXT NOT NULL CHECK (length(btrim(signer_id)) > 0),
            approver_actor_id TEXT NOT NULL CHECK (length(btrim(approver_actor_id)) > 0),
            approved_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > approved_at),
            revoked_at TIMESTAMPTZ,
            credential_version_id UUID,
            envelope_hash TEXT NOT NULL CHECK (envelope_hash ~ '^[0-9a-f]{64}$'),
            payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            public_key_hash TEXT NOT NULL CHECK (public_key_hash ~ '^[0-9a-f]{64}$'),
            import_audit_event_id UUID NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
            UNIQUE (tenant_id, provider_id, environment, terms_version_hash),
            CONSTRAINT fk_enrichment_rights_credential_version
                FOREIGN KEY (tenant_id, provider_id, credential_version_id)
                REFERENCES enrichment_credentials
                    (tenant_id, connector_id, credential_version_id),
            CHECK (
                (rights_class = 'RED' AND credential_version_id IS NOT NULL)
                OR (rights_class = 'YELLOW' AND credential_version_id IS NULL)
            ),
            CHECK (
                (status = 'APPROVED' AND revoked_at IS NULL)
                OR (status = 'REVOKED' AND revoked_at IS NOT NULL)
            )
        );

        CREATE INDEX idx_enrichment_rights_active
            ON enrichment_rights_approvals
                (tenant_id, provider_id, environment, expires_at)
            WHERE status = 'APPROVED' AND revoked_at IS NULL;

        ALTER TABLE enrichment_rights_approvals ENABLE ROW LEVEL SECURITY;
        ALTER TABLE enrichment_rights_approvals FORCE ROW LEVEL SECURITY;
        CREATE POLICY enrichment_rights_approvals_tenant_isolation
            ON enrichment_rights_approvals
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

    op.execute(
        """
        CREATE TABLE provider_budget_policies (
            tenant_id UUID NOT NULL,
            policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
            component_id TEXT NOT NULL CHECK (
                component_id IN (
                    'anthropic', 'openai_embeddings', 'sec_edgar', 'companies_house',
                    'github', 'fred', 'finnhub', 'fmp', 'world_bank', 'escwa_catalog',
                    'escwa_ispar', 'qatar_open_data', 'hackernews', 'gdelt',
                    'patentsview', 'wayback', 'google_news_rss'
                )
            ),
            policy_version TEXT NOT NULL CHECK (length(btrim(policy_version)) > 0),
            currency TEXT NOT NULL CHECK (currency = 'USD'),
            total_max_usd NUMERIC(20, 8) NOT NULL CHECK (total_max_usd >= 0),
            max_calls BIGINT NOT NULL CHECK (max_calls > 0),
            max_usd NUMERIC(20, 8) NOT NULL CHECK (max_usd >= 0),
            max_input_tokens BIGINT CHECK (max_input_tokens IS NULL OR max_input_tokens >= 0),
            max_output_tokens BIGINT CHECK (max_output_tokens IS NULL OR max_output_tokens >= 0),
            max_embedding_tokens BIGINT
                CHECK (max_embedding_tokens IS NULL OR max_embedding_tokens >= 0),
            signer_id TEXT NOT NULL CHECK (length(btrim(signer_id)) > 0),
            imported_by_actor_id TEXT NOT NULL
                CHECK (length(btrim(imported_by_actor_id)) > 0),
            envelope_hash TEXT NOT NULL CHECK (envelope_hash ~ '^[0-9a-f]{64}$'),
            payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            public_key_hash TEXT NOT NULL CHECK (public_key_hash ~ '^[0-9a-f]{64}$'),
            import_audit_event_id UUID NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
            PRIMARY KEY (tenant_id, policy_hash, component_id)
        );

        CREATE INDEX idx_provider_budget_policies_import_order
            ON provider_budget_policies (tenant_id, imported_at DESC, policy_hash);

        ALTER TABLE provider_budget_policies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE provider_budget_policies FORCE ROW LEVEL SECURITY;
        CREATE POLICY provider_budget_policies_tenant_isolation
            ON provider_budget_policies
            USING (
                NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
            );

        ALTER TABLE provider_budget_usage
            ADD CONSTRAINT fk_provider_budget_usage_policy
            FOREIGN KEY (tenant_id, policy_hash, provider)
            REFERENCES provider_budget_policies (tenant_id, policy_hash, component_id);

        CREATE FUNCTION idis_reject_governance_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'governance deletion and budget-policy mutation are forbidden';
        END;
        $$;

        CREATE FUNCTION idis_guard_rights_snapshot_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'rights snapshot deletion is forbidden';
            END IF;
            IF (
                NEW.rights_approval_id,
                NEW.tenant_id,
                NEW.provider_id,
                NEW.environment,
                NEW.terms_version_hash
            ) IS DISTINCT FROM (
                OLD.rights_approval_id,
                OLD.tenant_id,
                OLD.provider_id,
                OLD.environment,
                OLD.terms_version_hash
            ) THEN
                RAISE EXCEPTION 'rights snapshot identity is immutable';
            END IF;
            IF NEW.import_audit_event_id = OLD.import_audit_event_id
                OR NEW.payload_hash = OLD.payload_hash
                OR NEW.envelope_hash = OLD.envelope_hash
                OR NEW.imported_at < OLD.imported_at
            THEN
                RAISE EXCEPTION 'rights snapshot transition lineage is invalid';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_enrichment_rights_approvals_immutable
            BEFORE UPDATE OR DELETE ON enrichment_rights_approvals
            FOR EACH ROW EXECUTE FUNCTION idis_guard_rights_snapshot_mutation();

        CREATE TRIGGER trg_provider_budget_policies_immutable
            BEFORE UPDATE OR DELETE ON provider_budget_policies
            FOR EACH ROW EXECUTE FUNCTION idis_reject_governance_mutation();
        """
    )


def downgrade() -> None:
    _lock_downgrade_tables()
    assert_downgrade_is_lossless()

    op.execute(
        """
        ALTER TABLE provider_budget_usage
            DROP CONSTRAINT IF EXISTS fk_provider_budget_usage_policy;
        DROP TRIGGER trg_provider_budget_policies_immutable ON provider_budget_policies;
        DROP TRIGGER trg_enrichment_rights_approvals_immutable
            ON enrichment_rights_approvals;
        DROP FUNCTION idis_guard_rights_snapshot_mutation();
        DROP FUNCTION idis_reject_governance_mutation();
        DROP TABLE provider_budget_policies;
        DROP TABLE enrichment_rights_approvals;

        ALTER TABLE provider_budget_usage
            DROP CONSTRAINT IF EXISTS ck_provider_budget_usage_policy_hash,
            DROP COLUMN consumed_embedding_tokens,
            DROP COLUMN reserved_embedding_tokens,
            DROP COLUMN consumed_output_tokens,
            DROP COLUMN reserved_output_tokens,
            DROP COLUMN consumed_input_tokens,
            DROP COLUMN reserved_input_tokens,
            DROP COLUMN consumed_usd,
            DROP COLUMN reserved_usd,
            DROP COLUMN reserved_calls,
            DROP COLUMN policy_hash;

        DROP INDEX ux_enrichment_credentials_one_active;
        ALTER TABLE enrichment_credentials
            DROP CONSTRAINT enrichment_credentials_pk,
            DROP COLUMN credential_version_id,
            ADD CONSTRAINT enrichment_credentials_pk PRIMARY KEY (tenant_id, connector_id);

        ALTER TABLE enrichment_credentials NO FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS enrichment_credentials_tenant_isolation
            ON enrichment_credentials;
        CREATE POLICY enrichment_credentials_tenant_isolation
            ON enrichment_credentials
            USING (
                NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
            );
        """
    )
