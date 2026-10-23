"""V11-aware adapter for the immutable historical PostgreSQL census engine.

Only the historical scaffold's version entry guard is projected.  Every DDL
and function byte executed comes from the v11 artifact.  The execution seam
also supplies the v11 run-parent and complete five-GUC lease context that the
historical v10 case constructor did not know how to create.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _v10_entry_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != "full-strict-v11-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v11"
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V11_CENSUS_ARTIFACT_VERSION_MISMATCH")
    projected = copy.deepcopy(dict(artifact))
    projected["metadata"]["artifact_schema"] = "full-strict-v10-reviewed-preimage-v1"
    projected["metadata"]["pipeline_contract_version"] = "full-strict-v10"
    return projected


def _decode_lease_upgrade(artifact: Mapping[str, Any]) -> str:
    execution = artifact["erasure_execution_registry"]["rows"][0]
    matches = [
        statement
        for encoded in execution["legacy_guard_upgrade_sql_utf8_hex_tuple"]
        if (statement := bytes.fromhex(encoded).decode("utf-8")).startswith(
            "CREATE OR REPLACE FUNCTION public.idis_task6_current_lease_is_valid("
        )
    ]
    if len(matches) != 1:
        raise RuntimeError("TASK7_V11_LEASE_UPGRADE_CARDINALITY_INVALID")
    return matches[0]


def _decode_lease_restoration(artifact: Mapping[str, Any]) -> str:
    execution = artifact["erasure_execution_registry"]["rows"][0]
    matches = [
        statement
        for encoded in execution["legacy_guard_downgrade_sql_utf8_hex_tuple"]
        if (statement := bytes.fromhex(encoded).decode("utf-8"))
        .lstrip()
        .startswith("CREATE OR REPLACE FUNCTION idis_task6_current_lease_is_valid(")
    ]
    if len(matches) != 1:
        raise RuntimeError("TASK7_V11_LEASE_RESTORATION_CARDINALITY_INVALID")
    return matches[0]


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Install exact v11 SQL plus the separately hash-bound lease replacement."""

    from scripts import _task7_v10_census_scaffold as historical

    historical.install_artifact_sql_scaffold(
        connection,
        _v10_entry_projection(artifact),
    )
    connection.exec_driver_sql(_decode_lease_upgrade(artifact))


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    from scripts import _task7_v10_census_scaffold as historical

    return historical.catalog_projection(
        connection,
        _v10_entry_projection(artifact),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    from scripts import _task7_v10_census_scaffold as historical

    historical.disable_all_current_user_triggers(
        connection,
        _v10_entry_projection(artifact),
    )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    from scripts import _task7_v10_census_scaffold as historical

    historical.enable_target_trigger(connection, table_name, trigger_name)


@contextmanager
def v11_execution_seam(engine: Any) -> Iterator[None]:
    """Bind the old mechanical case engine to v11 parent/lease semantics."""

    original_disable = engine.disable_all_current_user_triggers
    original_enable = engine.enable_target_trigger
    original_seed_run = engine._seed_running_run
    original_seed_receipt = engine._seed_receipt_scope
    original_set_lease = engine._set_worker_lease
    original_insert_relaxed = engine._insert_relaxed

    def insert_relaxed(
        connection: Connection,
        columns: Mapping[str, Mapping[str, tuple[str, bool]]],
        table_name: str,
        values: Mapping[str, Any],
        *,
        returning: tuple[str, ...] = (),
    ) -> Any:
        corrected = dict(values)
        if table_name == "methodology_extraction_task_spans":
            # Historical constructor defect only: 0037 has no defaults for
            # these required span coordinates.  No contract byte changes.
            corrected.setdefault("document_id", str(uuid.uuid4()))
            corrected.setdefault("span_id", str(uuid.uuid4()))
            corrected.setdefault("span_ordinal", 1)
            corrected.setdefault("content_sha256", "c" * 64)
            corrected.setdefault("locator_sha256", "d" * 64)
        return original_insert_relaxed(
            connection,
            columns,
            table_name,
            corrected,
            returning=returning,
        )

    def seed_running_run(
        connection: Connection,
        columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    ) -> dict[str, Any]:
        graph = dict(original_seed_run(connection, columns))
        connection.execute(
            text(
                "UPDATE public.runs SET "
                "pipeline_contract_version='full-strict-v11' "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                "AND run_id=CAST(:run_id AS uuid)"
            ),
            graph,
        )
        graph["pipeline_contract_version"] = "full-strict-v11"
        return graph

    def seed_receipt_scope(
        connection: Connection,
        columns: Mapping[str, Mapping[str, tuple[str, bool]]],
        *,
        completed_step: bool,
    ) -> dict[str, Any]:
        graph = dict(
            original_seed_receipt(
                connection,
                columns,
                completed_step=completed_step,
            )
        )
        exists = connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM public.runs "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                "AND run_id=CAST(:run_id AS uuid))"
            ),
            graph,
        ).scalar_one()
        if not exists:
            engine._insert_relaxed(
                connection,
                columns,
                "runs",
                {
                    **graph,
                    "mode": "FULL",
                    "status": "RUNNING",
                    "idempotency_key": f"task7-v11-receipt-{uuid.uuid4()}",
                    "pipeline_contract_version": "full-strict-v11",
                    "pipeline_contract_hash": "a" * 64,
                    "pipeline_resume_state": "RESUMABLE",
                },
            )
        else:
            connection.execute(
                text(
                    "UPDATE public.runs SET "
                    "pipeline_contract_version='full-strict-v11' "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND run_id=CAST(:run_id AS uuid)"
                ),
                graph,
            )
        return graph

    def set_worker_lease(
        connection: Connection,
        *,
        owner_id: str,
        fencing_token: int,
    ) -> None:
        scope = connection.execute(
            text(
                "SELECT tenant_id::text,deal_id::text,run_id::text "
                "FROM public.runs WHERE lease_owner_id=CAST(:owner_id AS uuid) "
                "AND lease_fencing_token=:fencing_token "
                "ORDER BY created_at DESC,run_id DESC LIMIT 1"
            ),
            {"owner_id": owner_id, "fencing_token": fencing_token},
        ).one()
        connection.execute(
            text(
                "SELECT "
                "pg_catalog.set_config("
                "'idis.worker_lease_tenant_id',:tenant_id,true),"
                "pg_catalog.set_config("
                "'idis.worker_lease_deal_id',:deal_id,true),"
                "pg_catalog.set_config("
                "'idis.worker_lease_run_id',:run_id,true),"
                "pg_catalog.set_config("
                "'idis.worker_lease_owner_id',:owner_id,true),"
                "pg_catalog.set_config("
                "'idis.worker_lease_fencing_token',:fencing_token,true)"
            ),
            {
                "tenant_id": scope[0],
                "deal_id": scope[1],
                "run_id": scope[2],
                "owner_id": owner_id,
                "fencing_token": str(fencing_token),
            },
        )

    engine.disable_all_current_user_triggers = disable_all_current_user_triggers
    engine.enable_target_trigger = enable_target_trigger
    engine._seed_running_run = seed_running_run
    engine._seed_receipt_scope = seed_receipt_scope
    engine._set_worker_lease = set_worker_lease
    engine._insert_relaxed = insert_relaxed
    try:
        yield
    finally:
        engine.disable_all_current_user_triggers = original_disable
        engine.enable_target_trigger = original_enable
        engine._seed_running_run = original_seed_run
        engine._seed_receipt_scope = original_seed_receipt
        engine._set_worker_lease = original_set_lease
        engine._insert_relaxed = original_insert_relaxed


__all__ = [
    "_decode_lease_restoration",
    "_decode_lease_upgrade",
    "catalog_projection",
    "disable_all_current_user_triggers",
    "enable_target_trigger",
    "install_artifact_sql_scaffold",
    "v11_execution_seam",
]
