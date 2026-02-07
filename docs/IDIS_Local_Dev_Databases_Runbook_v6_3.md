# IDIS Local Dev Databases Runbook (v6.3)

This runbook gets **Postgres (RLS)** and optional **Neo4j** running locally in a way that matches the v6.3 infrastructure assumptions.

## 1) Prereqs

- Docker Desktop (Windows/macOS) or Docker Engine (Linux)
- Ports available (defaults): `5432` (Postgres), `7687`/`7474` (Neo4j bolt/http)

## 2) Environment variables

Set these in your local `.env` (do **not** commit secrets):

### Postgres
- `IDIS_DATABASE_URL` — app connection string
- `IDIS_DATABASE_ADMIN_URL` — admin connection string (used for bootstrap / migrations)

### Neo4j (optional)
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`

## 3) Recommended local Docker Compose

Create `infra/docker-compose.dev.yml` (or similar) with services:

- `postgres` (with a persistent volume)
- `neo4j` (optional; persistent volume)
- `redis` (optional; if you want to validate caching behavior)

Bring it up:

```bash
docker compose -f infra/docker-compose.dev.yml up -d postgres
docker compose -f infra/docker-compose.dev.yml up -d neo4j   # optional
```

## 4) Bootstrap + migrations

The v6.3 CI flow runs a bootstrap script and migrations (mirror that locally):

```bash
python scripts/pg_bootstrap_ci.py
alembic upgrade head
```

If `pg_bootstrap_ci.py` is CI-specific, clone it to `scripts/pg_bootstrap_local.py` and make it accept local admin/app URLs.

## 5) Validation checklist

- App can connect using `IDIS_DATABASE_URL`
- Migration tables exist (expect 0001–0004 migrations applied)
- RLS policies are active and tenant isolation works (run the existing RLS/audit test module)

## 6) Common issues

- **Port conflict**: change host port mapping in compose.
- **Docker volume permission issues**: on Linux, ensure correct UID/GID mapping or use named volumes.
- **Windows line endings**: ensure `.gitattributes` treats binaries correctly (already handled for PDFs/XLSX).

