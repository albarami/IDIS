-- IDIS PostgreSQL Initialization Script
-- Run by docker-compose on first startup to create app user and database setup

-- Create application user (non-superuser for security)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'idis_app') THEN
        CREATE ROLE idis_app WITH LOGIN PASSWORD 'idis_app_password';
    END IF;
END
$$;

-- Grant connect permission
GRANT CONNECT ON DATABASE idis TO idis_app;

-- Create schema if not exists and grant usage
CREATE SCHEMA IF NOT EXISTS public;
GRANT USAGE ON SCHEMA public TO idis_app;
GRANT CREATE ON SCHEMA public TO idis_app;

-- Grant table permissions (will apply to future tables via default privileges)
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO idis_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO idis_app;

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Note: Row-Level Security (RLS) policies are created by Alembic migrations
-- This script only handles initial user and permission setup
