# RB-11: Postgres Backup and Restore Drill

## Purpose

Prove IDIS can recover its transactional and audit state: back up the Postgres database,
destroy the schema, restore it, and verify tenancy, RLS, and audit integrity. This runbook
covers the LOGICAL backup/restore drill path (Slice99 Task 7); continuous WAL archiving /
PITR is a deployment-infrastructure requirement layered on top to meet the RPO target.

## Targets (from IDIS_SLO_SLA_Runbooks_v6_3.md section 7.1)

- Postgres (transactional): RPO 15 minutes, RTO 2 hours
- Audit store: RPO 15 minutes, RTO 2 hours
- The drill validates the restore path end to end; meeting the 15 minutes RPO in production
  additionally requires WAL archiving or provider PITR alongside these logical backups.

## Drill cadence

- Quarterly restore drills at minimum, and at least once before go-live
  (10_IDIS_GoLive_Execution_Plan_v6_3.md section 6.2).
- Additionally after any migration that changes guarded (RLS) tables.

## Secret handling rules

- Connection material comes ONLY from the environment (`IDIS_DATABASE_ADMIN_URL` for the
  admin engine; `IDIS_DATABASE_URL` for app-role verification). Never pass a DSN or password
  on the command line, never paste one into a ticket or log, and never commit one.
- The tooling never prints DSNs or passwords; the backup manifest records the database NAME
  and safe aggregates (row counts, sha256) only.
- Backup artifacts contain tenant data: store them under the same access controls as the
  database itself and delete drill copies after verification.

## Procedure

1. Set the environment (never flags):
   `IDIS_DATABASE_ADMIN_URL=postgresql://<admin>@<host>:<port>/<db>` (value from the secret
   manager; do not echo it).
2. Backup: `python scripts/db_backup_restore.py backup --out <backup-dir>`
   - Produces `tables/<table>.csv` per table plus `manifest.json`
     (schema_revision, per-table row counts + sha256).
3. Simulate loss (drill only): alembic downgrade to base, or point at a scratch database.
4. Restore: `python scripts/db_backup_restore.py restore --from <backup-dir>`
   - Verifies every dump sha256 against the manifest (fail-closed) BEFORE touching the
     database, replays the schema via alembic to the manifest revision (migrations are the
     schema source of truth, so RLS and the audit immutability trigger come back exactly),
     reloads the data, and verifies restored row counts against the manifest.

## Restore verification (all must pass)

- Tenant-scoped row counts match the pre-backup state (per-tenant `deals` counts).
- Guarded tables (`deals`, `claims`, `audit_events`) report RLS ENABLED and FORCED
  (`pg_class.relrowsecurity` / `relforcerowsecurity`).
- `audit_events` rows survive with their JSONB event content intact.
- The app role (non-superuser, NOBYPASSRLS) remains tenant-scoped: with tenant context it
  sees only its tenant's rows, with no context it sees zero rows, and cross-tenant writes
  are rejected by the RLS WITH CHECK.
- Automated AND CI-enforced: `tests/test_slice99_backup_restore_postgres.py` runs this exact
  drill on every CI run in the dedicated `backup-restore-drill` job (disposable Postgres
  service, `IDIS_REQUIRE_POSTGRES=1`, `postgresql+psycopg2://` URLs), and the `release-gate`
  job depends on it - release promotion cannot pass without recovery proof. Locally, run it
  against the disposable container `idis-slice99-pgtest` (port 15499).

## Escalation

Failed drill = SEV-2 until root-caused (backup integrity or restore path regression blocks
go-live). Record the drill outcome (date, schema revision, verification results) in the
`.local_reports` reconciliation log workflow where applicable.
