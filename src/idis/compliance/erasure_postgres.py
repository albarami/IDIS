"""Postgres erasure executor + export collector (Slice98 Task 8, Unit D).

``DEAL_SCOPED_TABLE_CLASSIFICATION`` is the pinned answer to "what happens to every table that
carries a deal_id column" - derived from the live schema and enforced by a tripwire test that
FAILS if a future migration adds a deal-scoped table without classifying it here as ``erased``,
``retained`` (with the reason in this module), or ``out_of_scope``. Three child tables carry no
deal_id and are erased through their parent (``_CHILD_TABLE_DELETES``): calc_sanads (via
deterministic_calculations), run_steps (via runs), human_gate_actions (via human_gates).

Retained-with-reason:
- ``erasure_requests``: the durable evidence OF the erasure itself; deliberately has no FK to
  deals and must outlive the erased row.
(``audit_events`` and ``legal_holds`` carry no deal_id column - audit events reference deals
only inside their JSON resource and are immutable by trigger; holds reference target_id text.)

The executor deletes in FK-safe order (children before parents, ``deals`` last), object
artifacts through the hold-aware ``ComplianceEnforcedStore`` (uri convention ``idis://<key>``),
and ``vector_embeddings`` rows (the wired embedding store; no graph store exists in code). Its
``scan_holds`` runs BEFORE the CRITICAL audit event: any active hold on the deal, its documents,
or its artifact keys aborts the whole execution with zero deletions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from idis.api.errors import IdisHttpError
from idis.compliance.retention import HoldTarget, get_legal_hold_registry

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)

# The pinned classification of EVERY table carrying a deal_id column (tripwire-enforced).
DEAL_SCOPED_TABLE_CLASSIFICATION: dict[str, str] = {
    "break_glass_grants": "erased",
    "claims": "erased",
    "data_room_package_files": "erased",
    "data_room_packages": "erased",
    "deal_assignments": "erased",
    "deals": "erased",  # deleted LAST - full removal per the locked Task 8 depth decision
    "debate_sessions": "erased",
    "defects": "erased",
    "deliverables": "erased",
    "deterministic_calculations": "erased",
    "document_artifacts": "erased",
    "document_spans": "erased",
    "documents": "erased",
    "erasure_requests": "retained",  # durable evidence of the erasure itself (no FK to deals)
    "evidence_items": "erased",
    "evidence_trust_findings": "erased",
    "human_gates": "erased",
    "layer2_ic_challenges": "erased",
    "layer2_ic_findings": "erased",
    "muhasabah_records": "erased",
    "overrides": "erased",
    "runs": "erased",
    "sanads": "erased",
    "validated_evidence_packages": "erased",
    "vector_embeddings": "erased",
}

# FK-safe deletion order for tables with a direct deal_id column (children before parents;
# deals last). Derived from the live foreign-key graph.
_DIRECT_DELETE_ORDER: tuple[str, ...] = (
    "document_spans",
    "sanads",
    "defects",
    "evidence_items",
    "debate_sessions",
    "deliverables",
    "data_room_package_files",
    "data_room_packages",
    "claims",
    "documents",
    "document_artifacts",
    "runs",
    "human_gates",
    "deterministic_calculations",
    "overrides",
    "vector_embeddings",
    "validated_evidence_packages",
    "evidence_trust_findings",
    "muhasabah_records",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "deal_assignments",
    "break_glass_grants",
)

# Children WITHOUT a deal_id column, erased through their deal-scoped parent (before it).
_CHILD_TABLE_DELETES: tuple[tuple[str, str], ...] = (
    (
        "calc_sanads",
        "DELETE FROM calc_sanads WHERE tenant_id = CAST(:tenant_id AS uuid) AND calc_id IN "
        "(SELECT calc_id FROM deterministic_calculations "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) AND deal_id = CAST(:deal_id AS uuid))",
    ),
    (
        "run_steps",
        "DELETE FROM run_steps WHERE run_id IN (SELECT run_id FROM runs "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) AND deal_id = CAST(:deal_id AS uuid))",
    ),
    (
        "human_gate_actions",
        "DELETE FROM human_gate_actions WHERE gate_id IN (SELECT gate_id FROM human_gates "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) AND deal_id = CAST(:deal_id AS uuid))",
    ),
)


def _janitor_ctx(tenant_id: str) -> Any:
    from idis.api.auth import TenantContext

    return TenantContext(
        tenant_id=tenant_id,
        actor_id="erasure-executor",
        name="Erasure Executor",
        timezone="UTC",
        data_region=None,
        roles=frozenset({"ADMIN"}),
    )


class PostgresErasureExecutor:
    """Full per-deal removal over the classified table surface (guarded by scan_holds)."""

    def _artifact_keys(self, conn: Connection, tenant_id: str, deal_id: str) -> list[str]:
        from sqlalchemy import text

        rows = conn.execute(
            text(
                "SELECT uri FROM document_artifacts WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND deal_id = CAST(:deal_id AS uuid) AND uri IS NOT NULL"
            ),
            {"tenant_id": tenant_id, "deal_id": deal_id},
        )
        return [row.uri.removeprefix("idis://") for row in rows if row.uri.startswith("idis://")]

    def _document_ids(self, conn: Connection, tenant_id: str, deal_id: str) -> list[str]:
        from sqlalchemy import text

        rows = conn.execute(
            text(
                "SELECT doc_id FROM document_artifacts "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) AND deal_id = CAST(:deal_id AS uuid)"
            ),
            {"tenant_id": tenant_id, "deal_id": deal_id},
        )
        return [str(row.doc_id) for row in rows]

    def scan_holds(self, tenant_id: str, deal_id: str) -> None:
        from idis.persistence.db import begin_app_conn, set_tenant_local

        registry = get_legal_hold_registry()
        targets: list[tuple[HoldTarget, str]] = [(HoldTarget.DEAL, deal_id)]
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            targets.extend(
                (HoldTarget.ARTIFACT, key) for key in self._artifact_keys(conn, tenant_id, deal_id)
            )
            targets.extend(
                (HoldTarget.DOCUMENT, doc_id)
                for doc_id in self._document_ids(conn, tenant_id, deal_id)
            )
        for target_type, target_id in targets:
            if registry.has_active_hold(tenant_id, target_type, target_id):
                logger.warning(
                    "Erasure blocked by active hold: tenant=%s target=%s/%s",
                    tenant_id,
                    target_type.value,
                    target_id,
                )
                raise IdisHttpError(
                    status_code=403,
                    code="DELETION_BLOCKED_BY_HOLD",
                    message="Access denied.",
                )

    def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local
        from idis.services.ingestion.defaults import build_default_compliance_store
        from idis.storage.errors import ObjectNotFoundError

        # Object artifacts first (keys enumerated from rows about to be deleted); the
        # hold-aware store re-checks holds per key as defense in depth after scan_holds.
        objects_deleted = 0
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            artifact_keys = self._artifact_keys(conn, tenant_id, deal_id)
        store = build_default_compliance_store()
        ctx = _janitor_ctx(tenant_id)
        for key in artifact_keys:
            try:
                store.delete(ctx, key, resource_id=key, hold_target_type=HoldTarget.ARTIFACT)
                objects_deleted += 1
            except ObjectNotFoundError:
                continue  # row referenced bytes that were never stored or already removed

        rows_deleted = 0
        embeddings_deleted = 0
        params = {"tenant_id": tenant_id, "deal_id": deal_id}
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            for _table, sql in _CHILD_TABLE_DELETES:
                rows_deleted += conn.execute(text(sql), params).rowcount
            for table in _DIRECT_DELETE_ORDER:
                result = conn.execute(
                    text(
                        f"DELETE FROM {table} WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "AND deal_id = CAST(:deal_id AS uuid)"
                    ),
                    params,
                )
                if table == "vector_embeddings":
                    embeddings_deleted = result.rowcount
                rows_deleted += result.rowcount
            rows_deleted += conn.execute(
                text(
                    "DELETE FROM deals WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND deal_id = CAST(:deal_id AS uuid)"
                ),
                params,
            ).rowcount

        return {
            "rows_deleted": int(rows_deleted),
            "objects_deleted": int(objects_deleted),
            "embeddings_deleted": int(embeddings_deleted),
        }


class PostgresExportCollector:
    """Tenant-scoped export inventory over the durable tables (metadata columns only)."""

    _QUERIES: dict[str, str] = {
        "deals": "SELECT deal_id, name, created_at FROM deals "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) ORDER BY created_at",
        "documents": "SELECT doc_id, deal_id, title, sha256, doc_type FROM document_artifacts "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) ORDER BY created_at",
        "claims": "SELECT claim_id, deal_id, created_at FROM claims "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) ORDER BY created_at",
        "sanads": "SELECT sanad_id, deal_id, created_at FROM sanads "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) ORDER BY created_at",
        "deliverables": "SELECT deliverable_id, deal_id, created_at FROM deliverables "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) ORDER BY created_at",
    }

    def collect(self, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        inventory: dict[str, list[dict[str, Any]]] = {}
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            for section, sql in self._QUERIES.items():
                rows = conn.execute(text(sql), {"tenant_id": tenant_id})
                inventory[section] = [
                    {
                        key: (str(value) if value is not None else None)
                        for key, value in row._mapping.items()
                    }
                    for row in rows
                ]
        return inventory
