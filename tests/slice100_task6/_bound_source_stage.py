"""Stage unbound documents and an OPEN package for the Task 6 freeze.

``_bound_source`` calls this module. No production writer enriches an existing
unbound artifact, so the version, URI, and metadata updates below run only
while ``source_binding_state`` is still UNBOUND.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.evaluation.real_example_terminal_reconciliation import (
    OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION,
    PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION,
    _build_pdf_page_census,
    _canonical_sequence_of_mappings_sha256,
)
from idis.models.data_room_package import (
    DataRoomFileStatus,
    DataRoomPackage,
    DataRoomPackageFile,
    DataRoomPackageStatus,
)
from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.models.source_package_manifest import (
    SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
    canonical_source_manifest_sha256,
)
from idis.persistence.repositories.data_room_packages import PostgresDataRoomPackagesRepository
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.task7_source_freeze import (
    _SOURCE_SCHEMA_AUTHORITIES,
    _sha256_json,
    _terminal_document_projection,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIZE_BYTES = 1
_PARSE_REASON = "parse_completed"
_PARSER_NAME = "built_in"
_PARSER_VERSION = "runtime-parser-v1"
_OBJECT_BACKEND = "compliant:postgres-test-object-store"
_COMPANY_ALIAS = "company-001"
_OBSERVED_AT = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
_ATTEMPT_STARTED_AT = _OBSERVED_AT - timedelta(seconds=2)
_ATTEMPT_FINISHED_AT = _OBSERVED_AT - timedelta(seconds=1)
_EXTENSION_BY_DOC_TYPE = {
    "PDF": "pdf",
    "DOCX": "docx",
    "XLSX": "xlsx",
    "PPTX": "pptx",
    "HTML": "html",
    "TEXT": "txt",
    "IMAGE": "png",
}


def _stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "task6-frozen:" + ":".join(parts)))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_object(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            raise RuntimeError("frozen-source JSON object was not an object")
        return loaded
    if isinstance(value, Mapping):
        return dict(value)
    raise RuntimeError("frozen-source JSON object was not an object")


def _locator(value: object) -> dict[str, Any]:
    return _json_object(value)


def _stage_document(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    document: Mapping[str, Any],
    spans: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    doc_type = str(document["doc_type"])
    extension = _EXTENSION_BY_DOC_TYPE.get(doc_type)
    if extension is None:
        raise RuntimeError("bind_frozen_run_source has no extension for " + doc_type)
    if document["parse_status"] != ParseStatus.PARSED.value:
        raise RuntimeError("bind_frozen_run_source only freezes PARSED documents")
    content_sha256 = str(document["sha256"])
    if _SHA256_RE.fullmatch(content_sha256) is None:
        raise RuntimeError("artifact sha256 is not a lowercase sha-256 digest")
    document_id = str(document["document_id"])
    doc_id = str(document["doc_id"])
    version_id = _version_id(document.get("version_id"), doc_id)
    uri = document.get("uri")
    if not isinstance(uri, str) or not uri:
        uri = f"object://task6-frozen/{doc_id}"
    ingested_at = document.get("ingested_at") or _OBSERVED_AT
    schema_refs = _schema_refs(doc_type, spans)
    document_metadata = _json_object(document.get("document_metadata"))
    document_metadata.update(
        {
            "parser_runtime": _PARSER_NAME,
            "parser_runtime_status": "completed",
            "parser_support_status": DocumentSupportStatus.SUPPORTED.value,
            "parser_triage_status": DocumentTriageStatus.READY.value,
            "parser_reason_codes": [_PARSE_REASON],
            "parse_warning_codes": [],
            "parse_error_codes": [],
            "applicable_schema_refs": schema_refs,
            "span_count": len(spans),
        }
    )
    artifact_metadata = _json_object(document.get("artifact_metadata"))
    artifact_metadata["durable_object_integrity"] = {
        "schema_version": "durable-object-integrity-v1",
        "doc_id": doc_id,
        "content_sha256": content_sha256,
        "size_bytes": _SIZE_BYTES,
        "uri_sha256": _sha256_text(uri),
        "version_id": version_id,
        "backend": _OBJECT_BACKEND,
        "streaming_verified": True,
    }
    _update_unbound_rows(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_id=document_id,
        doc_id=doc_id,
        version_id=version_id,
        uri=uri,
        ingested_at=ingested_at,
        document_metadata=document_metadata,
        artifact_metadata=artifact_metadata,
    )
    span_mappings = tuple(_span_mapping(span) for span in spans)
    receipt_sha256 = _sha256_json(
        [
            "terminal-document-metadata-v2",
            doc_type,
            ParseStatus.PARSED.value,
            _terminal_document_projection(
                document_metadata,
                doc_type=doc_type,
                parse_status=ParseStatus.PARSED.value,
                spans=span_mappings,
            ),
        ]
    )
    parse_attempt_id = _stable_uuid("attempt", document_id)
    PostgresDocumentsRepository(connection, tenant_id).create_runtime_parse_attempt(
        parse_attempt_id=parse_attempt_id,
        deal_id=deal_id,
        document_id=document_id,
        parser_name=_PARSER_NAME,
        parser_version=_PARSER_VERSION,
        input_sha256=content_sha256,
        receipt_sha256=receipt_sha256,
        status=ParseStatus.PARSED.value,
        started_at=_ATTEMPT_STARTED_AT,
        finished_at=_ATTEMPT_FINISHED_AT,
    )
    return {
        "document_id": document_id,
        "doc_id": doc_id,
        "extension": extension,
        "content_sha256": content_sha256,
        "file_entry_id": _stable_uuid("file", document_id),
        "parse_attempt_id": parse_attempt_id,
        "path_hash": _sha256_text(f"task6-file-locator:{document_id}"),
    }


def _version_id(current: object, doc_id: str) -> str:
    try:
        return str(uuid.UUID(str(current)))
    except (TypeError, ValueError, AttributeError):
        return _stable_uuid("version", doc_id)


def _span_mapping(span: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "span_id": span["span_id"],
        "tenant_id": span["tenant_id"],
        "deal_id": span["deal_id"],
        "document_id": span["document_id"],
        "span_type": span["span_type"],
        "locator": _locator(span["locator"]),
        "text_excerpt": span["text_excerpt"],
        "content_hash": span["content_hash"],
    }


def _schema_refs(
    doc_type: str,
    spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if doc_type != "PDF":
        return []
    pages = _pdf_pages(spans)
    binding = str(spans[0]["content_hash"])
    census = _build_pdf_page_census(
        page_count=pages,
        native_text_pages=tuple(range(1, pages + 1)),
        ocr_text_pages=(),
        image_only_pages=(),
        watermark_only_pages=(),
        evidence_bindings=dict.fromkeys(range(1, pages + 1), binding),
    )
    empty_digest = _canonical_sequence_of_mappings_sha256([])
    evidence = {
        "schema_version": OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": 0,
        "ocr_page_numbers": [],
        "preclassified_page_numbers": [],
        "ocr_required_page_count": 0,
        "ocr_span_count": 0,
        "confidence_count": 0,
        "mean_confidence": None,
        "minimum_confidence": None,
        "maximum_confidence": None,
        "locator_contract_sha256": empty_digest,
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": empty_digest,
        "ocr_render_admissions": [],
        "ocr_render_admissions_sha256": empty_digest,
        "pdf_page_census": census,
    }
    return [
        _schema_ref("ocr-terminal-evidence", OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION, evidence),
        _schema_ref(
            "pdf-page-disposition-census",
            PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION,
            census,
        ),
    ]


def _pdf_pages(spans: Sequence[Mapping[str, Any]]) -> int:
    pages: list[int] = []
    for span in spans:
        page = _locator(span["locator"]).get("page")
        if type(page) is not int or page < 1:
            raise RuntimeError("a PDF span locator has no positive page")
        pages.append(page)
    return max(pages)


def _schema_ref(schema_id: str, schema_version: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_id": schema_id,
        "schema_version": schema_version,
        "schema_sha256": _SOURCE_SCHEMA_AUTHORITIES[(schema_id, schema_version)],
        "validated_payload": payload,
    }


def _update_unbound_rows(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    document_id: str,
    doc_id: str,
    version_id: str,
    uri: str,
    ingested_at: datetime,
    document_metadata: Mapping[str, Any],
    artifact_metadata: Mapping[str, Any],
) -> None:
    """Fill freeze prerequisites while rows are still UNBOUND.

    No production writer enriches an existing unbound artifact or document.
    The source-bound guard allows these column updates only before freeze.
    """

    artifact = connection.execute(
        text(
            """
            UPDATE document_artifacts
            SET version_id = :version_id,
                uri = :uri,
                ingested_at = :ingested_at,
                metadata = CAST(:metadata AS jsonb)
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND doc_id = CAST(:doc_id AS uuid)
              AND source_binding_state = 'UNBOUND'
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "doc_id": doc_id,
            "version_id": version_id,
            "uri": uri,
            "ingested_at": ingested_at,
            "metadata": json.dumps(dict(artifact_metadata)),
        },
    )
    document = connection.execute(
        text(
            """
            UPDATE documents
            SET metadata = CAST(:metadata AS jsonb)
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND document_id = CAST(:document_id AS uuid)
              AND source_binding_state = 'UNBOUND'
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "document_id": document_id,
            "metadata": json.dumps(dict(document_metadata)),
        },
    )
    if artifact.rowcount != 1 or document.rowcount != 1:
        raise RuntimeError("unbound artifact or document staging did not match one row")


def _create_open_package(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    package_id: str,
    staged: Sequence[Mapping[str, str]],
) -> str:
    ordered = sorted(staged, key=lambda row: row["document_id"])
    files = []
    for sequence, row in enumerate(ordered):
        upload_name = f"{_COMPANY_ALIAS}-document-{sequence + 1:05d}.{row['extension']}"
        files.append(
            {
                "sequence": sequence,
                "generated_upload_name": upload_name,
                "source_locator_sha256": row["path_hash"],
                "content_sha256": row["content_sha256"],
                "size_bytes": _SIZE_BYTES,
                "extension": row["extension"],
                "document_id": row["document_id"],
                "doc_id": row["doc_id"],
            }
        )
    manifest = {
        "schema_version": SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "company_alias": _COMPANY_ALIAS,
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": _sha256_text("|".join(row["document_id"] for row in ordered)),
        "file_count": len(files),
        "total_bytes": _SIZE_BYTES * len(files),
        "files": files,
    }
    manifest_sha256 = canonical_source_manifest_sha256(manifest)
    manifest["manifest_sha256"] = manifest_sha256
    packages = PostgresDataRoomPackagesRepository(connection, tenant_id)
    packages.create_package(
        DataRoomPackage(
            package_id=uuid.UUID(package_id),
            tenant_id=uuid.UUID(tenant_id),
            deal_id=uuid.UUID(deal_id),
            status=DataRoomPackageStatus.OPEN,
            created_by_actor_id="task6-seed",
            created_by_actor_type="SERVICE",
            metadata={"source_manifest": manifest},
            created_at=_OBSERVED_AT,
            updated_at=_OBSERVED_AT,
        )
    )
    for row, manifest_file in zip(ordered, files, strict=True):
        packages.add_file(
            DataRoomPackageFile(
                file_entry_id=uuid.UUID(row["file_entry_id"]),
                tenant_id=uuid.UUID(tenant_id),
                package_id=uuid.UUID(package_id),
                deal_id=uuid.UUID(deal_id),
                sequence=manifest_file["sequence"],
                path_hash=row["path_hash"],
                extension=row["extension"],
                sha256=row["content_sha256"],
                file_status=DataRoomFileStatus.SUPPORTED,
                support_status=DocumentSupportStatus.SUPPORTED,
                triage_status=DocumentTriageStatus.READY,
                parse_status=ParseStatus.PARSED,
                reason_codes=[_PARSE_REASON],
                error_codes=[],
                created_at=_OBSERVED_AT,
            )
        )
    return manifest_sha256
