# ruff: noqa: E501
"""Development-only restart reference revision.

This revision lives outside the package, the wheel, the default sdist, and the
image. Owner ruling AUTH-CIR04-DEV0039-20261017 authorizes it for development
and for the isolated disposable-database path. The reviewed packet is
publication-proposal-v6.md. The production migration ceiling stays 0038.

The executable owner statement is body index 39 (OWNER TO
idis_erasure_function_owner). The ACL row's owner_role field and owner_sql
name idis_task7_guard_function_owner. Those reviewed statements disagree; this
revision executes the body statement and does not choose the other owner.
"""

from __future__ import annotations

import re
from typing import Final

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

_PREIMAGE_SHA256: Final = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_ROW_SCHEMA_SHA256: Final = "fe53467996dd3c3b7fe7ab888f857a01595ea2dd70e3b33e142f3833c6231ab0"
_AUTHORITY_SHA256: Final = "d70799842e56404f4038711edc5c52e9d1a42f31ddd6d21ed3ad0ab6bc885d09"
_TABLE: Final = "step_receipt_input_references"
_ROLE_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_COLUMN_TUPLE: Final = [
    [0, "receipt_reference_id", "UUID NOT NULL"],
    [1, "tenant_id", "UUID NOT NULL"],
    [2, "deal_id", "UUID NOT NULL"],
    [3, "run_id", "UUID NOT NULL"],
    [4, "step_output_id", "UUID NOT NULL"],
    [5, "consumer_output_object_id", "UUID NOT NULL"],
    [6, "producer_type", "TEXT NOT NULL"],
    [7, "producer_input_binding_id", "UUID NULL"],
    [8, "producer_output_object_id", "UUID NULL"],
    [9, "producer_identity_sha256", "TEXT NOT NULL"],
    [10, "producer_value_sha256", "TEXT NOT NULL"],
    [11, "reference_kind", "TEXT NOT NULL"],
    [12, "reference_ordinal", "INTEGER NOT NULL"],
    [13, "lease_owner_id", "UUID NOT NULL"],
    [14, "lease_fencing_token", "BIGINT NOT NULL"],
    [15, "created_at", "TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()"],
]
_KIND_TUPLE: Final = [
    "SOURCE_INVENTORY",
    "SOURCE_HANDOFF",
    "SEMANTIC_INPUT",
    "PREFLIGHT",
    "APPLICABLE_COVERAGE",
    "COVERAGE",
    "METHOD_INTERNAL",
    "GENERIC_INTERNAL",
    "RAG_CONTEXT",
    "IDENTITY",
    "PROVIDER_INPUT",
    "PROVIDER_OUTCOME",
    "PROVIDER_MISS",
    "PROVIDER_FACT",
    "GRAPH_INTERNAL",
    "RAG_INTERNAL",
    "GRAPH_EXTERNAL",
    "RAG_EXTERNAL",
    "CONFLICT_INTERNAL",
    "CONFLICT_EXTERNAL",
    "COURT",
    "VEP",
    "DEBATE",
    "ANALYSIS",
    "SCORING",
    "READINESS",
    "LAYER2",
    "RECONCILIATION_INPUT",
    "BUNDLE_MEMBERSHIP",
    "METHODOLOGY_EVIDENCE",
    "METHODOLOGY_CALCULATION",
    "UNIFIED_CALCULATION",
]
_CHECK_TUPLE: Final = [
    [
        "ck_v2_receipt_input_ref_producer",
        "producer_type IN ('INPUT_BINDING','OUTPUT_OBJECT') AND ((producer_type='INPUT_BINDING' AND producer_input_binding_id IS NOT NULL AND producer_output_object_id IS NULL) OR (producer_type='OUTPUT_OBJECT' AND producer_input_binding_id IS NULL AND producer_output_object_id IS NOT NULL))",
    ],
    [
        "ck_v2_receipt_input_ref_hashes",
        "producer_identity_sha256~'^[0-9a-f]{64}$' AND producer_value_sha256~'^[0-9a-f]{64}$'",
    ],
    [
        "ck_v2_receipt_input_ref_kind",
        "reference_kind IN ('SOURCE_INVENTORY','SOURCE_HANDOFF','SEMANTIC_INPUT','PREFLIGHT','APPLICABLE_COVERAGE','COVERAGE','METHOD_INTERNAL','GENERIC_INTERNAL','RAG_CONTEXT','IDENTITY','PROVIDER_INPUT','PROVIDER_OUTCOME','PROVIDER_MISS','PROVIDER_FACT','GRAPH_INTERNAL','RAG_INTERNAL','GRAPH_EXTERNAL','RAG_EXTERNAL','CONFLICT_INTERNAL','CONFLICT_EXTERNAL','COURT','VEP','DEBATE','ANALYSIS','SCORING','READINESS','LAYER2','RECONCILIATION_INPUT','BUNDLE_MEMBERSHIP','METHODOLOGY_EVIDENCE','METHODOLOGY_CALCULATION','UNIFIED_CALCULATION')",
    ],
    ["ck_v2_receipt_input_ref_ordinal", "reference_ordinal>=0"],
    ["ck_v2_receipt_input_ref_lease", "lease_owner_id IS NOT NULL AND lease_fencing_token>0"],
]
_FOREIGN_KEY_TUPLE: Final = [
    [
        "fk_v2_receipt_input_ref_run",
        ["tenant_id", "deal_id", "run_id"],
        "runs",
        "uq_task7_runs_tenant_deal_run",
        ["tenant_id", "deal_id", "run_id"],
        "NO ACTION",
        "NO ACTION",
        "uq_v2_receipt_input_ref_scope",
    ],
    [
        "fk_v2_receipt_input_ref_consumer",
        ["tenant_id", "deal_id", "run_id", "step_output_id", "consumer_output_object_id"],
        "run_step_outputs",
        "uq_task7_run_step_outputs_scoped_output",
        ["tenant_id", "deal_id", "run_id", "step_output_id", "output_object_id"],
        "NO ACTION",
        "NO ACTION",
        "ix_v2_receipt_input_ref_consumer",
    ],
    [
        "fk_v2_receipt_input_ref_input",
        ["tenant_id", "deal_id", "run_id", "producer_input_binding_id"],
        "run_input_bindings",
        "uq_task7_run_input_bindings_scoped_id",
        ["tenant_id", "deal_id", "run_id", "binding_id"],
        "NO ACTION",
        "NO ACTION",
        "ix_v2_receipt_input_ref_input",
    ],
    [
        "fk_v2_receipt_input_ref_output",
        ["tenant_id", "deal_id", "run_id", "producer_output_object_id"],
        "run_output_objects",
        "uq_task7_run_output_objects_scope",
        ["tenant_id", "deal_id", "run_id", "output_object_id"],
        "NO ACTION",
        "NO ACTION",
        "ix_v2_receipt_input_ref_output",
    ],
]
_UNIQUE_TUPLE: Final = [
    ["pk_v2_receipt_input_ref", ["receipt_reference_id"], False],
    [
        "uq_v2_receipt_input_ref_scope",
        ["tenant_id", "deal_id", "run_id", "receipt_reference_id"],
        False,
    ],
    [
        "uq_v2_receipt_input_ref_coordinate",
        [
            "tenant_id",
            "run_id",
            "step_output_id",
            "producer_type",
            "producer_input_binding_id",
            "producer_output_object_id",
            "reference_kind",
        ],
        True,
    ],
    [
        "uq_v2_receipt_input_ref_ordinal",
        ["tenant_id", "run_id", "step_output_id", "reference_ordinal"],
        False,
    ],
]
_INDEX_TUPLE: Final = [
    [
        "ix_v2_receipt_input_ref_consumer",
        ["tenant_id", "deal_id", "run_id", "step_output_id", "consumer_output_object_id"],
        None,
        False,
    ],
    [
        "ix_v2_receipt_input_ref_input",
        ["tenant_id", "deal_id", "run_id", "producer_input_binding_id"],
        "producer_input_binding_id IS NOT NULL",
        False,
    ],
    [
        "ix_v2_receipt_input_ref_output",
        ["tenant_id", "deal_id", "run_id", "producer_output_object_id"],
        "producer_output_object_id IS NOT NULL",
        False,
    ],
]
_RLS_TUPLE: Final = [
    [
        "ALTER TABLE step_receipt_input_references ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE step_receipt_input_references FORCE ROW LEVEL SECURITY",
        "CREATE POLICY step_receipt_input_references_tenant_isolation ON step_receipt_input_references AS PERMISSIVE FOR ALL TO PUBLIC USING (NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        "REVOKE ALL PRIVILEGES ON TABLE step_receipt_input_references FROM PUBLIC; GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE step_receipt_input_references TO <configured_app_role>",
    ]
]
_FUNCTION_SQL: Final = "CREATE FUNCTION public.idis_task7_approved_erasure_append_only_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='INSERT' THEN RETURN NEW; END IF; IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'TASK7_SQL_AUTHORITY_REJECTED:APPROVED_ERASURE_APPEND_ONLY' USING ERRCODE='23514'; END IF; IF TG_OP='DELETE' AND session_user='idis_erasure_executor' AND current_user='idis_erasure_function_owner' AND pg_catalog.current_setting('idis.erasure_context',true)='true' AND NULLIF(pg_catalog.current_setting('idis.tenant_id',true),'')::uuid=OLD.tenant_id AND EXISTS (SELECT 1 FROM public.erasure_requests r WHERE r.tenant_id=OLD.tenant_id AND r.deal_id=OLD.deal_id AND r.status IN ('REQUESTED','FAILED') FOR UPDATE) THEN RETURN OLD; END IF; RAISE EXCEPTION 'TASK7_SQL_AUTHORITY_REJECTED:APPROVED_ERASURE_APPEND_ONLY' USING ERRCODE='23514'; END$$"
_TRIGGER_SQL: Final = "CREATE TRIGGER trg_task7_step_receipt_input_references_approved_erasure_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.step_receipt_input_references FOR EACH ROW EXECUTE FUNCTION public.idis_task7_approved_erasure_append_only_v1()"
_OWNER_SQL: Final = "ALTER FUNCTION public.idis_task7_approved_erasure_append_only_v1() OWNER TO idis_erasure_function_owner"
_REVOKE_SQL: Final = (
    "REVOKE ALL ON FUNCTION public.idis_task7_approved_erasure_append_only_v1() FROM PUBLIC"
)
_ACL_SQL: Final = (
    "ALTER FUNCTION public.idis_task7_approved_erasure_append_only_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_approved_erasure_append_only_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_approved_erasure_append_only_v1() FROM PUBLIC",
)

_OWNER_ROLE: Final = _OWNER_SQL.rsplit(" OWNER TO ", 1)[1]
_POLICY: Final = "step_receipt_input_references_tenant_isolation"
_TRIGGER: Final = "trg_task7_step_receipt_input_references_approved_erasure_append_only"
_TRIGGER_CATALOG: Final = _TRIGGER[:63]
_FUNCTION: Final = "idis_task7_approved_erasure_append_only_v1"


def _ident(value: str) -> str:
    if _ROLE_NAME.fullmatch(value) is None:
        raise RuntimeError("DEV0039_NAME_INVALID")
    return value


def _cells(value: object) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError("DEV0039_REVISION_INVALID")
    return value


def _application_role() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("DEV0039_APP_ROLE_UNRESOLVED")
    role = config.attributes.get("dev0039_app_role")
    if not isinstance(role, str) or _ROLE_NAME.fullmatch(role) is None:
        raise RuntimeError("DEV0039_APP_ROLE_UNRESOLVED")
    return role


def _require_prerequisites() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT to_regclass('public.step_receipt_input_references') IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("DEV0039_RELATION_PRESENT")
    for item in _FOREIGN_KEY_TUPLE:
        parent = _ident(str(item[2]))
        constraint = _ident(str(item[3]))
        found = bind.execute(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
                "JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace "
                "WHERE namespace_row.nspname = 'public' "
                "AND table_row.relname = :parent "
                "AND constraint_row.conname = :constraint "
                "AND constraint_row.contype IN ('u', 'p'))"
            ),
            {"parent": parent, "constraint": constraint},
        ).scalar_one()
        if not found:
            raise RuntimeError("DEV0039_PARENT_AUTHORITY_ABSENT")
    owner = bind.execute(
        sa.text("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :role"),
        {"role": _OWNER_ROLE},
    ).scalar_one_or_none()
    if owner is None:
        raise RuntimeError("DEV0039_OWNER_ROLE_ABSENT")


def _execute_reviewed_table(role: str) -> None:
    column_sql = []
    for column in _COLUMN_TUPLE:
        cells = _cells(column)
        column_sql.append(f"    {_ident(str(cells[1]))} {cells[2]}")
    columns = ",\n".join(column_sql)
    op.execute(f"CREATE TABLE public.{_TABLE} (\n{columns}\n)")
    for check in _CHECK_TUPLE:
        cells = _cells(check)
        op.execute(
            f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_ident(str(cells[0]))} CHECK ({cells[1]})"
        )
    for unique in _UNIQUE_TUPLE:
        cells = _cells(unique)
        unique_name = str(cells[0])
        rendered = ", ".join(_ident(str(column)) for column in _cells(cells[1]))
        if unique_name.startswith("pk_"):
            op.execute(
                f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_ident(unique_name)} "
                f"PRIMARY KEY ({rendered})"
            )
            continue
        nulls = " NULLS NOT DISTINCT" if cells[2] else ""
        op.execute(
            f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_ident(unique_name)} "
            f"UNIQUE{nulls} ({rendered})"
        )
    for foreign in _FOREIGN_KEY_TUPLE:
        cells = _cells(foreign)
        if (str(cells[5]), str(cells[6])) != ("NO ACTION", "NO ACTION"):
            raise RuntimeError("DEV0039_PARENT_AUTHORITY_ABSENT")
        child = ", ".join(_ident(str(column)) for column in _cells(cells[1]))
        parent_rendered = ", ".join(_ident(str(column)) for column in _cells(cells[4]))
        op.execute(
            f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_ident(str(cells[0]))} "
            f"FOREIGN KEY ({child}) REFERENCES {_ident(str(cells[2]))} ({parent_rendered}) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION"
        )
    for index in _INDEX_TUPLE:
        cells = _cells(index)
        if cells[3]:
            raise RuntimeError("DEV0039_NAME_INVALID")
        rendered = ", ".join(_ident(str(column)) for column in _cells(cells[1]))
        where = f" WHERE {cells[2]}" if cells[2] else ""
        op.execute(f"CREATE INDEX {_ident(str(cells[0]))} ON public.{_TABLE} ({rendered}){where}")
    for group in _RLS_TUPLE:
        for statement in _cells(group):
            if not isinstance(statement, str):
                raise RuntimeError("DEV0039_REVISION_INVALID")
            rendered = statement.replace("<configured_app_role>", _ident(role))
            if "<configured_app_role>" in rendered:
                raise RuntimeError("DEV0039_APP_ROLE_UNRESOLVED")
            for part in rendered.split("; "):
                op.execute(part)


def _execute_reviewed_guard() -> None:
    if " ON public.step_receipt_input_references " not in _TRIGGER_SQL:
        raise RuntimeError("DEV0039_TRIGGER_TARGET_INVALID")
    op.execute(_FUNCTION_SQL)
    op.execute(_OWNER_SQL)
    op.execute(_REVOKE_SQL)
    for statement in _ACL_SQL:
        op.execute(statement)
    op.execute(_TRIGGER_SQL)
    installed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT owner_role.rolname FROM pg_proc AS procedure "
                "JOIN pg_namespace AS namespace_row ON namespace_row.oid = procedure.pronamespace "
                "JOIN pg_roles AS owner_role ON owner_role.oid = procedure.proowner "
                "WHERE namespace_row.nspname = 'public' "
                "AND procedure.proname = :function"
            ),
            {"function": _FUNCTION},
        )
        .scalar_one()
    )
    if installed != _OWNER_ROLE:
        raise RuntimeError("DEV0039_FUNCTION_OWNER_MISMATCH")


def _assert_removed() -> None:
    remaining = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT "
                "to_regclass('public.step_receipt_input_references') IS NOT NULL "
                "OR EXISTS ("
                "SELECT 1 FROM pg_trigger AS trigger_row "
                "JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid "
                "WHERE trigger_row.tgname::text = :trigger) "
                "OR EXISTS ("
                "SELECT 1 FROM pg_policy AS policy_row "
                "JOIN pg_class AS table_row ON table_row.oid = policy_row.polrelid "
                "WHERE policy_row.polname = :policy)"
            ),
            {"trigger": _TRIGGER_CATALOG, "policy": _POLICY},
        )
        .scalar_one()
    )
    if remaining:
        raise RuntimeError("DEV0039_DOWNGRADE_INCOMPLETE")


def upgrade() -> None:
    """Install reference row 14 and its reviewed append-only guard."""
    pins = (_PREIMAGE_SHA256, _ROW_SCHEMA_SHA256, _AUTHORITY_SHA256)
    if any(len(pin) != 64 for pin in pins):
        raise RuntimeError("DEV0039_REVISION_INVALID")
    role = _application_role()
    _require_prerequisites()
    _execute_reviewed_table(role)
    _execute_reviewed_guard()


def downgrade() -> None:
    """Drop the empty reference relation, its policy, trigger, and guard."""
    bind = op.get_bind()
    count = int(bind.execute(sa.text(f"SELECT count(*) FROM public.{_TABLE}")).scalar_one())
    if count != 0:
        raise RuntimeError("DEV0039_DOWNGRADE_NONEMPTY")
    bind.execute(sa.text(f"DROP TRIGGER {_TRIGGER} ON public.{_TABLE}"))
    referenced = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM pg_trigger AS trigger_row "
                "JOIN pg_proc AS procedure ON procedure.oid = trigger_row.tgfoid "
                "WHERE procedure.proname = :function AND NOT trigger_row.tgisinternal"
            ),
            {"function": _FUNCTION},
        ).scalar_one()
    )
    if referenced == 0:
        bind.execute(sa.text(f"DROP FUNCTION public.{_FUNCTION}()"))
    bind.execute(sa.text(f"DROP POLICY {_POLICY} ON public.{_TABLE}"))
    bind.execute(sa.text(f"DROP TABLE public.{_TABLE}"))
    _assert_removed()
