"""Out-of-band durable writes for Task 6 PostgreSQL fixtures.

Public entry points are ``corrupt_durable_rows_out_of_band`` and
``insert_bound_extra_span`` on ``tests.slice100_task6._support``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, CursorResult

from tests.slice100_task6._support import set_seed_tenant

_OUT_OF_BAND_TRIGGERS: Final[Mapping[str, tuple[str, ...]]] = {
    "document_spans": (
        "trg_task7_document_spans_source_bound",
        "ctrg_task7_document_spans_source_exact_one",
    ),
    "methodology_extraction_task_spans": ("trg_methodology_extraction_task_spans_immutable",),
    "run_steps": (
        "ctrg_task7_run_steps_receipt_complete",
        "trg_task7_run_steps_contract_immutable",
    ),
}


def corrupt_durable_rows_out_of_band(
    connection: Connection,
    statement: str,
    params: Mapping[str, Any] | None = None,
    *,
    table: str = "document_spans",
) -> CursorResult[Any]:
    """Disable the table's guards, run one statement, and enable them again.

    Every name in the table's map is disabled before the statement and enabled
    again in reverse order. A deferred constraint trigger queues an event on
    UPDATE even when its function would not raise. ``ENABLE TRIGGER`` then
    fails in the same transaction with pending trigger events, so the receipt
    trigger cannot be left enabled across an out-of-band ``run_steps`` write.

    ``run_steps`` names ``ctrg_task7_run_steps_receipt_complete`` and
    ``trg_task7_run_steps_contract_immutable``. Name-and-order updates need
    the immutable trigger off. ``result_summary`` updates also disable it.
    That assignment changes none of the columns the immutable trigger
    compares, so the extra disable does not change the committed row.
    """

    mapped = _OUT_OF_BAND_TRIGGERS.get(table)
    if mapped is None:
        raise ValueError(f"no out-of-band trigger list for {table}")
    for trigger_name in mapped:
        connection.exec_driver_sql(f"ALTER TABLE {table} DISABLE TRIGGER {trigger_name}")
    result = connection.execute(text(statement), params or {})
    for trigger_name in reversed(mapped):
        connection.exec_driver_sql(f"ALTER TABLE {table} ENABLE TRIGGER {trigger_name}")
    return result


def _source_lineage_namespace() -> str:
    from idis.pipeline.task7_uuid import TASK7_UUID_NAMESPACE_REGISTRY

    for spec in TASK7_UUID_NAMESPACE_REGISTRY:
        if spec.id_kind == "source_lineage_object_id":
            return str(spec.namespace_uuid)
    raise RuntimeError("source_lineage_object_id namespace is not registered")


def insert_bound_extra_span(
    connection: Connection,
    *,
    document_id: str,
    span_id: str,
) -> None:
    """Insert one projected BOUND span on an already frozen document."""

    tenant_id = connection.execute(
        text(
            "SELECT tenant_id::text FROM documents WHERE document_id = CAST(:document_id AS uuid)"
        ),
        {"document_id": document_id},
    ).scalar_one()
    set_seed_tenant(connection, str(tenant_id))
    inserted = connection.execute(
        text(
            """
            WITH sibling AS (
                SELECT span.tenant_id, span.deal_id, span.document_id,
                       span.span_type, span.locator, span.text_excerpt,
                       span.content_hash, span.parse_attempt_id,
                       span.source_corpus_receipt_id AS package_id,
                       root.corpus_receipt_sha256
                FROM document_spans AS span
                JOIN source_lineage_objects AS root
                  ON root.source_lineage_object_id = span.source_lineage_object_id
                WHERE span.document_id = CAST(:document_id AS uuid)
                  AND span.source_binding_state = 'BOUND'
                ORDER BY span.span_id
                LIMIT 1
            ),
            hashed AS (
                SELECT sibling.*,
                       ('document-span-v1:' || sibling.parse_attempt_id::text
                           || ':' || sibling.span_type) AS source_version,
                       public.idis_task7_source_hash_jsonb_v1(
                           jsonb_build_array(
                               'span-locator-v1', sibling.span_type, sibling.locator
                           )
                       ) AS locator_sha
                FROM sibling
            ),
            identified AS (
                SELECT hashed.*,
                       public.idis_task7_source_hash_jsonb_v1(
                           jsonb_build_array(
                               'document-span-identity-v1',
                               CAST(:span_id AS uuid)::text,
                               hashed.document_id::text,
                               hashed.parse_attempt_id::text,
                               hashed.span_type,
                               hashed.locator_sha,
                               hashed.content_hash
                           )
                       ) AS object_identity_sha256
                FROM hashed
            ),
            named AS (
                SELECT identified.*,
                       public.idis_task7_uuidv5_v1(
                           CAST(:namespace AS uuid),
                           public.idis_task7_source_hash_jsonb_v1(
                               jsonb_build_array(
                                   'source-lineage-object-v2',
                                   identified.tenant_id::text,
                                   identified.deal_id::text,
                                   identified.package_id::text,
                                   'SPAN',
                                   jsonb_build_array(
                                       identified.tenant_id::text,
                                       identified.deal_id::text,
                                       identified.document_id::text,
                                       CAST(:span_id AS uuid)::text
                                   ),
                                   identified.source_version,
                                   'EVIDENCE_BEARING',
                                   identified.object_identity_sha256,
                                   identified.content_hash,
                                   identified.corpus_receipt_sha256
                               )
                           )
                       ) AS root_id
                FROM identified
            ),
            inserted_root AS (
                INSERT INTO source_lineage_objects (
                    source_lineage_object_id, tenant_id, deal_id,
                    corpus_receipt_id, object_kind, source_version,
                    source_disposition, object_identity_sha256, content_sha256,
                    corpus_receipt_sha256, write_origin
                )
                SELECT root_id, tenant_id, deal_id, package_id, 'SPAN',
                       source_version, 'EVIDENCE_BEARING', object_identity_sha256,
                       content_hash, corpus_receipt_sha256, 'RUNTIME'
                FROM named
                RETURNING source_lineage_object_id
            )
            INSERT INTO document_spans (
                span_id, tenant_id, deal_id, document_id, span_type,
                locator, text_excerpt, content_hash, parse_attempt_id,
                source_binding_state, source_lineage_object_id,
                source_corpus_receipt_id
            )
            SELECT CAST(:span_id AS uuid), named.tenant_id, named.deal_id,
                   named.document_id, named.span_type, named.locator,
                   named.text_excerpt, named.content_hash, named.parse_attempt_id,
                   'BOUND', named.root_id, named.package_id
            FROM named
            JOIN inserted_root
              ON inserted_root.source_lineage_object_id = named.root_id
            """
        ),
        {
            "document_id": document_id,
            "span_id": span_id,
            "namespace": _source_lineage_namespace(),
        },
    )
    if inserted.rowcount != 1:
        raise RuntimeError("bound extra span was not inserted")
