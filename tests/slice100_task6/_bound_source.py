"""Stage and freeze a production bound source graph on the caller's transaction.

The public contract lives on ``tests.slice100_task6._support.bind_frozen_run_source``.
This module holds the body so ``_support.py`` stays within the file-size limit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.models.run_source import RunSource, RunSourceType
from idis.persistence.repositories.task7_source_freeze import (
    PostgresTask7SourceFreezeRepository,
)
from tests.slice100_task6._bound_source_stage import (
    _create_open_package,
    _stable_uuid,
    _stage_document,
)
from tests.slice100_task6._support import schema_head_installed, set_seed_tenant


@dataclass(frozen=True, slots=True)
class FrozenSpanSeed:
    """One unbound span overlay applied before the production freeze."""

    span_id: str
    document_id: str
    span_type: str
    locator: Mapping[str, Any]
    text_excerpt: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class FrozenRunSourceIds:
    """Identifiers the freeze writer created or reloaded."""

    package_id: str
    file_entry_ids: tuple[str, ...]
    parse_attempt_ids: tuple[str, ...]
    manifest_sha256: str
    document_ids: tuple[str, ...]


def bind_frozen_run_source(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str | None,
    document_ids: Sequence[str],
    span_rows: Sequence[FrozenSpanSeed] = (),
) -> FrozenRunSourceIds:
    """Freeze ``document_ids`` on ``connection`` and, when given, set ``runs.source``.

    See ``tests.slice100_task6._support.bind_frozen_run_source`` for the contract.
    Does not commit.
    """

    if not schema_head_installed(connection):
        raise RuntimeError("bind_frozen_run_source requires schema 0038")
    selected = _deduped_document_ids(document_ids)
    set_seed_tenant(connection, tenant_id)
    existing = _reload_if_already_bound(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_ids=selected,
    )
    if existing is not None:
        if run_id is not None:
            _store_run_source(connection, run_id=run_id, document_ids=selected)
        return existing
    _apply_span_rows(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        span_rows=span_rows,
    )
    documents = _load_documents(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_ids=selected,
    )
    spans_by_document = _load_spans(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_ids=selected,
    )
    _reject_duplicate_content(documents)
    staged = [
        _stage_document(
            connection,
            tenant_id=tenant_id,
            deal_id=deal_id,
            document=document,
            spans=spans_by_document[document["document_id"]],
        )
        for document in documents
    ]
    package_id = _stable_uuid("package", tenant_id, deal_id, *selected)
    manifest_sha256 = _create_open_package(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        staged=staged,
    )
    freezer = PostgresTask7SourceFreezeRepository(connection, tenant_id)
    observation = freezer.observe_source_package(deal_id=deal_id, package_id=package_id)
    freezer.freeze_observed_package(observation)
    if run_id is not None:
        _store_run_source(connection, run_id=run_id, document_ids=selected)
    return FrozenRunSourceIds(
        package_id=package_id,
        file_entry_ids=tuple(row["file_entry_id"] for row in staged),
        parse_attempt_ids=tuple(row["parse_attempt_id"] for row in staged),
        manifest_sha256=manifest_sha256,
        document_ids=selected,
    )


def _deduped_document_ids(document_ids: Sequence[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for document_id in document_ids:
        if document_id not in selected:
            selected.append(document_id)
    if not selected:
        raise RuntimeError("bind_frozen_run_source requires at least one document id")
    return tuple(selected)


def _reload_if_already_bound(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    document_ids: tuple[str, ...],
) -> FrozenRunSourceIds | None:
    rows = (
        connection.execute(
            text(
                """
            SELECT source_binding_state, source_corpus_receipt_id::text AS package_id
            FROM document_spans
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND document_id = ANY(CAST(:document_ids AS uuid[]))
            """
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "document_ids": list(document_ids),
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        raise RuntimeError("bind_frozen_run_source found no spans for the selected documents")
    states = {str(row["source_binding_state"]) for row in rows}
    if states == {"UNBOUND"}:
        return None
    if states != {"BOUND"}:
        raise RuntimeError(
            "TASK6_FROZEN_SOURCE_PARTIAL: selected documents mix BOUND and UNBOUND "
            "spans; 0038 will not re-freeze a sealed package to absorb a new span"
        )
    package_ids = {str(row["package_id"]) for row in rows}
    if len(package_ids) != 1:
        raise RuntimeError("selected bound spans do not share one corpus receipt")
    package_id = next(iter(package_ids))
    files = (
        connection.execute(
            text(
                """
            SELECT file_entry_id::text AS file_entry_id
            FROM data_room_package_files
            WHERE package_id = CAST(:package_id AS uuid)
            ORDER BY sequence
            """
            ),
            {"package_id": package_id},
        )
        .scalars()
        .all()
    )
    attempts = (
        connection.execute(
            text(
                """
            SELECT parse_attempt_id::text
            FROM document_parse_attempts
            WHERE deal_id = CAST(:deal_id AS uuid)
              AND document_id = ANY(CAST(:document_ids AS uuid[]))
              AND source_binding_state = 'BOUND'
            ORDER BY document_id
            """
            ),
            {"deal_id": deal_id, "document_ids": list(document_ids)},
        )
        .scalars()
        .all()
    )
    manifest_sha256 = connection.execute(
        text(
            """
            SELECT metadata->'source_manifest'->>'manifest_sha256'
            FROM data_room_packages
            WHERE package_id = CAST(:package_id AS uuid)
            """
        ),
        {"package_id": package_id},
    ).scalar_one()
    return FrozenRunSourceIds(
        package_id=package_id,
        file_entry_ids=tuple(str(item) for item in files),
        parse_attempt_ids=tuple(str(item) for item in attempts),
        manifest_sha256=str(manifest_sha256),
        document_ids=document_ids,
    )


def _apply_span_rows(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    span_rows: Sequence[FrozenSpanSeed],
) -> None:
    for span in span_rows:
        changed = connection.execute(
            text(
                """
                UPDATE document_spans
                SET span_type = :span_type,
                    locator = CAST(:locator AS jsonb),
                    text_excerpt = :text_excerpt,
                    content_hash = :content_hash
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND document_id = CAST(:document_id AS uuid)
                  AND span_id = CAST(:span_id AS uuid)
                  AND source_binding_state = 'UNBOUND'
                """
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "document_id": span.document_id,
                "span_id": span.span_id,
                "span_type": span.span_type,
                "locator": json.dumps(dict(span.locator)),
                "text_excerpt": span.text_excerpt,
                "content_hash": span.content_hash,
            },
        )
        if changed.rowcount != 1:
            raise RuntimeError(
                "frozen span overlay did not match one unbound span: " + span.span_id
            )


def _load_documents(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    document_ids: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    rows = (
        connection.execute(
            text(
                """
            SELECT d.document_id::text AS document_id,
                   d.doc_id::text AS doc_id,
                   d.doc_type,
                   d.parse_status,
                   d.metadata AS document_metadata,
                   a.sha256,
                   a.version_id,
                   a.uri,
                   a.ingested_at,
                   a.title,
                   a.metadata AS artifact_metadata
            FROM documents AS d
            JOIN document_artifacts AS a
              ON a.tenant_id = d.tenant_id
             AND a.deal_id = d.deal_id
             AND a.doc_id = d.doc_id
            WHERE d.tenant_id = CAST(:tenant_id AS uuid)
              AND d.deal_id = CAST(:deal_id AS uuid)
              AND d.document_id = ANY(CAST(:document_ids AS uuid[]))
            ORDER BY d.document_id
            """
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "document_ids": list(document_ids),
            },
        )
        .mappings()
        .all()
    )
    found = {str(row["document_id"]) for row in rows}
    if found != set(document_ids):
        missing = sorted(set(document_ids) - found)
        raise RuntimeError("bind_frozen_run_source is missing documents: " + ", ".join(missing))
    return tuple(dict(row) for row in rows)


def _load_spans(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    document_ids: tuple[str, ...],
) -> dict[str, tuple[dict[str, Any], ...]]:
    rows = (
        connection.execute(
            text(
                """
            SELECT span_id::text AS span_id,
                   document_id::text AS document_id,
                   tenant_id::text AS tenant_id,
                   deal_id::text AS deal_id,
                   span_type,
                   locator,
                   text_excerpt,
                   content_hash
            FROM document_spans
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND document_id = ANY(CAST(:document_ids AS uuid[]))
            ORDER BY document_id, span_id
            """
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "document_ids": list(document_ids),
            },
        )
        .mappings()
        .all()
    )
    grouped: dict[str, list[dict[str, Any]]] = {document_id: [] for document_id in document_ids}
    for row in rows:
        grouped[str(row["document_id"])].append(dict(row))
    empty = [document_id for document_id, spans in grouped.items() if not spans]
    if empty:
        raise RuntimeError("bind_frozen_run_source found a document with no spans")
    return {document_id: tuple(spans) for document_id, spans in grouped.items()}


def _reject_duplicate_content(documents: Sequence[Mapping[str, Any]]) -> None:
    hashes = [str(row["sha256"]) for row in documents]
    if len(hashes) != len(set(hashes)):
        raise RuntimeError(
            "TASK6_FROZEN_SOURCE_DUPLICATE_CONTENT: manifest content_sha256 values "
            "collide; refusing to rewrite artifact hashes"
        )


def _store_run_source(
    connection: Connection,
    *,
    run_id: str,
    document_ids: tuple[str, ...],
) -> None:
    source = RunSource(
        type=RunSourceType.DEAL_DOCUMENTS,
        document_ids=list(document_ids),
    ).to_storage_dict()
    changed = connection.execute(
        text(
            """
            UPDATE runs
            SET source = CAST(:source AS jsonb)
            WHERE run_id = CAST(:run_id AS uuid)
            """
        ),
        {"run_id": run_id, "source": json.dumps(source)},
    )
    if changed.rowcount != 1:
        raise RuntimeError("bind_frozen_run_source did not update one run source")
