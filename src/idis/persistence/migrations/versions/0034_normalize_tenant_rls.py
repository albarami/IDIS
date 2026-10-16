"""Normalize tenant RLS across the complete Slice100 Task 2b inventory.

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-16

This migration is deliberately limited to row-level-security state and policy
definitions.  Both directions validate the complete catalog before changing
it, so an unexpected policy or tenant-bearing relation fails closed instead of
being silently overwritten.
"""

from __future__ import annotations

from typing import TypeAlias

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "audit_events",
    "break_glass_grants",
    "byok_policies",
    "calc_sanads",
    "claims",
    "data_room_package_files",
    "data_room_packages",
    "deal_assignments",
    "deals",
    "debate_sessions",
    "defects",
    "deliverables",
    "deterministic_calculations",
    "document_artifacts",
    "document_spans",
    "documents",
    "enrichment_credentials",
    "enrichment_rights_approvals",
    "erasure_requests",
    "evidence_items",
    "evidence_trust_findings",
    "group_memberships",
    "groups",
    "human_gate_actions",
    "human_gates",
    "idempotency_records",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "legal_holds",
    "muhasabah_records",
    "overrides",
    "provider_budget_policies",
    "provider_budget_usage",
    "run_steps",
    "runs",
    "sanads",
    "strict_preflight_proofs",
    "tenants",
    "validated_evidence_packages",
    "vector_embeddings",
    "webhook_delivery_attempts",
    "webhooks",
)

CANONICAL_PREDICATE = """\
NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
"""

_NULLIF_PREDICATE = """\
tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
"""
_RAW_SETTING_PREDICATE = """\
tenant_id = current_setting('idis.tenant_id', true)::uuid
"""

# pg_get_expr renders the three approved 0033 predicate shapes this way.  The
# exact strings make the admission check reject logical changes as well as
# missing policy metadata.
_CATALOG_CANONICAL_PREDICATE = (
    "((NULLIF(current_setting('idis.tenant_id'::text, true), ''::text) IS NOT NULL) "
    "AND (tenant_id = "
    "(NULLIF(current_setting('idis.tenant_id'::text, true), ''::text))::uuid))"
)
_CATALOG_NULLIF_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('idis.tenant_id'::text, true), ''::text))::uuid)"
)
_CATALOG_RAW_SETTING_PREDICATE = (
    "(tenant_id = (current_setting('idis.tenant_id'::text, true))::uuid)"
)

_PredicateKind: TypeAlias = str
_PolicySpec: TypeAlias = tuple[str, _PredicateKind, bool, bool]
_CatalogRow: TypeAlias = tuple[
    str,
    bool,
    bool,
    str | None,
    bool | None,
    str | None,
    str | None,
    str | None,
    str | None,
]

_GUARDED = "guarded"
_NULLIF = "nullif"
_RAW_SETTING = "raw_setting"

# name, predicate family, WITH CHECK present, FORCE enabled.  At revision 0033
# every relation except tenants had RLS enabled and exactly one ALL/public,
# permissive policy.  The tenants row is represented separately below.
_PRE_0034_POLICY_SPECS: dict[str, _PolicySpec] = {
    "audit_events": ("audit_events_tenant_isolation", _NULLIF, True, True),
    "break_glass_grants": (
        "break_glass_grants_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "byok_policies": ("byok_policies_tenant_isolation", _GUARDED, True, True),
    "calc_sanads": ("calc_sanads_tenant_isolation", _NULLIF, True, False),
    "claims": ("claims_tenant_isolation", _RAW_SETTING, True, True),
    "data_room_package_files": (
        "tenant_isolation_data_room_package_files",
        _GUARDED,
        False,
        True,
    ),
    "data_room_packages": (
        "tenant_isolation_data_room_packages",
        _GUARDED,
        False,
        True,
    ),
    "deal_assignments": (
        "deal_assignments_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "deals": ("deals_tenant_isolation", _NULLIF, True, True),
    "debate_sessions": (
        "tenant_isolation_debate_sessions",
        _GUARDED,
        False,
        False,
    ),
    "defects": ("defects_tenant_isolation", _RAW_SETTING, True, True),
    "deliverables": ("tenant_isolation_deliverables", _GUARDED, False, False),
    "deterministic_calculations": (
        "deterministic_calculations_tenant_isolation",
        _NULLIF,
        True,
        False,
    ),
    "document_artifacts": (
        "document_artifacts_tenant_isolation",
        _NULLIF,
        True,
        True,
    ),
    "document_spans": ("document_spans_tenant_isolation", _NULLIF, True, True),
    "documents": ("documents_tenant_isolation", _NULLIF, True, True),
    "enrichment_credentials": (
        "enrichment_credentials_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "enrichment_rights_approvals": (
        "enrichment_rights_approvals_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "erasure_requests": (
        "erasure_requests_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "evidence_items": ("tenant_isolation_evidence_items", _GUARDED, False, False),
    "evidence_trust_findings": (
        "tenant_isolation_evidence_trust_findings",
        _GUARDED,
        False,
        True,
    ),
    "group_memberships": (
        "group_memberships_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "groups": ("groups_tenant_isolation", _GUARDED, True, True),
    "human_gate_actions": (
        "tenant_isolation_human_gate_actions",
        _GUARDED,
        False,
        False,
    ),
    "human_gates": ("tenant_isolation_human_gates", _GUARDED, False, False),
    "idempotency_records": (
        "idempotency_records_tenant_isolation",
        _NULLIF,
        True,
        True,
    ),
    "layer2_ic_challenges": (
        "tenant_isolation_layer2_ic_challenges",
        _GUARDED,
        False,
        True,
    ),
    "layer2_ic_findings": (
        "tenant_isolation_layer2_ic_findings",
        _GUARDED,
        False,
        True,
    ),
    "legal_holds": ("legal_holds_tenant_isolation", _GUARDED, True, True),
    "muhasabah_records": (
        "tenant_isolation_muhasabah_records",
        _GUARDED,
        False,
        True,
    ),
    "overrides": ("tenant_isolation_overrides", _GUARDED, False, False),
    "provider_budget_policies": (
        "provider_budget_policies_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "provider_budget_usage": (
        "provider_budget_usage_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "run_steps": ("tenant_isolation_run_steps", _GUARDED, False, False),
    "runs": ("tenant_isolation_runs", _GUARDED, False, False),
    "sanads": ("sanads_tenant_isolation", _RAW_SETTING, True, True),
    "strict_preflight_proofs": (
        "strict_preflight_proofs_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "validated_evidence_packages": (
        "tenant_isolation_validated_evidence_packages",
        _GUARDED,
        False,
        True,
    ),
    "vector_embeddings": (
        "vector_embeddings_tenant_isolation",
        _NULLIF,
        True,
        True,
    ),
    "webhook_delivery_attempts": (
        "webhook_delivery_attempts_tenant_isolation",
        _GUARDED,
        True,
        True,
    ),
    "webhooks": ("webhooks_tenant_isolation", _NULLIF, True, True),
}

_TENANTS_POLICY_NAME = "tenants_tenant_isolation"


def _policy_name(table: str) -> str:
    if table == "tenants":
        return _TENANTS_POLICY_NAME
    return _PRE_0034_POLICY_SPECS[table][0]


def _sql_predicate(kind: _PredicateKind) -> str:
    if kind == _GUARDED:
        return CANONICAL_PREDICATE
    if kind == _NULLIF:
        return _NULLIF_PREDICATE
    if kind == _RAW_SETTING:
        return _RAW_SETTING_PREDICATE
    raise AssertionError(f"unknown RLS predicate family: {kind}")


def _catalog_predicate(kind: _PredicateKind) -> str:
    if kind == _GUARDED:
        return _CATALOG_CANONICAL_PREDICATE
    if kind == _NULLIF:
        return _CATALOG_NULLIF_PREDICATE
    if kind == _RAW_SETTING:
        return _CATALOG_RAW_SETTING_PREDICATE
    raise AssertionError(f"unknown RLS predicate family: {kind}")


def _tenant_inventory() -> tuple[str, ...]:
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'tenant_id'
            ORDER BY table_name
            """
        )
    )
    return tuple(str(table) for table in rows.scalars())


def _catalog_snapshot() -> tuple[_CatalogRow, ...]:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT c.relname,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   p.polname,
                   p.polpermissive,
                   p.polcmd,
                   p.polroles::text,
                   pg_get_expr(p.polqual, p.polrelid),
                   pg_get_expr(p.polwithcheck, p.polrelid)
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN pg_policy AS p ON p.polrelid = c.oid
            WHERE n.nspname = 'public'
              AND c.relname = ANY(CAST(:tenant_tables AS text[]))
            ORDER BY c.relname, p.polname
            """
            ),
            {"tenant_tables": list(TENANT_TABLES)},
        )
        .fetchall()
    )
    return tuple(
        (
            str(row[0]),
            bool(row[1]),
            bool(row[2]),
            None if row[3] is None else str(row[3]),
            None if row[4] is None else bool(row[4]),
            None if row[5] is None else str(row[5]),
            None if row[6] is None else str(row[6]),
            None if row[7] is None else str(row[7]),
            None if row[8] is None else str(row[8]),
        )
        for row in rows
    )


def _pre_0034_snapshot() -> tuple[_CatalogRow, ...]:
    expected: list[_CatalogRow] = []
    for table in TENANT_TABLES:
        if table == "tenants":
            expected.append((table, False, False, None, None, None, None, None, None))
            continue
        policy_name, predicate_kind, has_check, forced = _PRE_0034_POLICY_SPECS[table]
        predicate = _catalog_predicate(predicate_kind)
        expected.append(
            (
                table,
                True,
                forced,
                policy_name,
                True,
                "*",
                "{0}",
                predicate,
                predicate if has_check else None,
            )
        )
    return tuple(expected)


def _normalized_snapshot() -> tuple[_CatalogRow, ...]:
    return tuple(
        (
            table,
            True,
            True,
            _policy_name(table),
            True,
            "*",
            "{0}",
            _CATALOG_CANONICAL_PREDICATE,
            _CATALOG_CANONICAL_PREDICATE,
        )
        for table in TENANT_TABLES
    )


def _validate_catalog(expected: tuple[_CatalogRow, ...], *, revision_name: str) -> None:
    inventory = _tenant_inventory()
    if inventory != TENANT_TABLES:
        raise RuntimeError(
            f"refusing RLS migration: tenant inventory does not match {revision_name}"
        )
    actual = _catalog_snapshot()
    if actual != expected:
        raise RuntimeError(f"refusing RLS migration: policy catalog does not match {revision_name}")


def _lock_tenant_tables() -> None:
    table_list = ", ".join(f'"{table}"' for table in TENANT_TABLES)
    op.get_bind().execute(sa.text(f"LOCK TABLE {table_list} IN ACCESS EXCLUSIVE MODE"))


def _create_policy(table: str, policy_name: str, predicate: str, *, check: bool) -> None:
    with_check = f" WITH CHECK ({predicate})" if check else ""
    op.execute(
        f'CREATE POLICY "{policy_name}" ON "{table}" '
        f"AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}){with_check}"
    )


def upgrade() -> None:
    _validate_catalog(_pre_0034_snapshot(), revision_name="0033")
    _lock_tenant_tables()
    _validate_catalog(_pre_0034_snapshot(), revision_name="locked 0033")

    for table in TENANT_TABLES:
        policy_name = _policy_name(table)
        if table != "tenants":
            op.execute(f'DROP POLICY "{policy_name}" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        _create_policy(
            table,
            policy_name,
            CANONICAL_PREDICATE,
            check=True,
        )

    _validate_catalog(_normalized_snapshot(), revision_name="0034")


def downgrade() -> None:
    _validate_catalog(_normalized_snapshot(), revision_name="0034")
    _lock_tenant_tables()
    _validate_catalog(_normalized_snapshot(), revision_name="locked 0034")

    for table in TENANT_TABLES:
        op.execute(f'DROP POLICY "{_policy_name(table)}" ON "{table}"')
        if table == "tenants":
            op.execute('ALTER TABLE "tenants" NO FORCE ROW LEVEL SECURITY')
            op.execute('ALTER TABLE "tenants" DISABLE ROW LEVEL SECURITY')
            continue

        policy_name, predicate_kind, has_check, forced = _PRE_0034_POLICY_SPECS[table]
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        force_clause = "FORCE" if forced else "NO FORCE"
        op.execute(f'ALTER TABLE "{table}" {force_clause} ROW LEVEL SECURITY')
        _create_policy(
            table,
            policy_name,
            _sql_predicate(predicate_kind),
            check=has_check,
        )

    _validate_catalog(_pre_0034_snapshot(), revision_name="restored 0033")
