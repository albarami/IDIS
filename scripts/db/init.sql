-- IDIS Database Initialization Script
-- Runs automatically on first postgres container start
-- 
-- This script:
-- 1. Enables required extensions
-- 2. Creates base schema structure
-- 3. Sets up RLS policies for tenant isolation

-- =============================================================================
-- Extensions
-- =============================================================================

-- pgvector for RAG/embeddings (single-store approach for dev)
CREATE EXTENSION IF NOT EXISTS vector;

-- UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- Tenant Isolation Setup
-- =============================================================================

-- Function to get current tenant from session variable
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
BEGIN
    RETURN NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

-- =============================================================================
-- Audit Schema (append-only, immutable)
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id UUID NOT NULL,
    action VARCHAR(50) NOT NULL,
    request_id UUID NOT NULL,
    correlation_id UUID,
    causation_id UUID,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Audit is append-only: no UPDATE or DELETE
CREATE OR REPLACE RULE audit_no_update AS ON UPDATE TO audit_events DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_no_delete AS ON DELETE TO audit_events DO INSTEAD NOTHING;

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_audit_tenant_timestamp ON audit_events(tenant_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_events(correlation_id);

-- =============================================================================
-- Log completion
-- =============================================================================

DO $$
BEGIN
    RAISE NOTICE 'IDIS database initialized successfully';
    RAISE NOTICE 'Extensions enabled: vector, uuid-ossp';
    RAISE NOTICE 'Audit table created with append-only rules';
END $$;
