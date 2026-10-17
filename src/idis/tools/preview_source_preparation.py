"""Internal upload-first preview producer; not a two-company acceptance contract.

Only this producer enforces its preparation recipe. Existing server endpoints
continue enforcing their unchanged durable scope, manifest and freeze checks.
No original filename, path or source bytes are retained in the preparation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from idis.models.source_package_manifest import (
    SourcePackageManifest,
    canonical_source_manifest_sha256,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.services.ingestion import IngestionContext
    from idis.storage.compliant_store import ComplianceEnforcedStore

PREVIEW_SOURCE_SYSTEM = "idis-preview-preparation-v1"
_EXTENSION = re.compile(r"^[a-z0-9]{1,12}$")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PreviewPreparationFile:
    """Actual ordered upload identity fixed before any request is sent."""

    sequence: int
    generated_upload_name: str
    source_locator_sha256: str
    content_sha256: str
    size_bytes: int
    extension: str


@dataclass(frozen=True)
class PreviewSourcePreparation:
    """One private client/tool preparation; never serialized as an API schema."""

    tenant_id: str
    deal_id: str
    preparation_id: str
    files: tuple[PreviewPreparationFile, ...]
    source_tree_fingerprint: str

    def validate(self) -> None:
        """Reject a changed preparation, rather than reinterpreting its old identity."""
        if (
            any(
                str(UUID(value)) != value
                for value in (
                    self.tenant_id,
                    self.deal_id,
                    self.preparation_id,
                )
            )
            or not self.files
        ):
            raise ValueError("invalid preview preparation scope")
        seen: set[str] = set()
        for sequence, row in enumerate(self.files):
            if (
                row.sequence != sequence
                or not _EXTENSION.fullmatch(row.extension)
                or row.generated_upload_name
                != f"company-999-document-{sequence + 1:05d}.{row.extension}"
                or row.source_locator_sha256
                != _hash(
                    [
                        "idis-preview-source-locator-v1",
                        self.tenant_id,
                        self.deal_id,
                        self.preparation_id,
                        sequence,
                    ]
                )
                or not re.fullmatch(r"[0-9a-f]{64}", row.content_sha256)
                or type(row.size_bytes) is not int
                or row.size_bytes < 0
                or row.content_sha256 in seen
            ):
                raise ValueError("preview preparation identity drifted")
            seen.add(row.content_sha256)
        if self.source_tree_fingerprint != self._fingerprint():
            raise ValueError("preview preparation fingerprint drifted")

    def _fingerprint(self) -> str:
        return _hash(
            [
                "idis-preview-source-tree-v1",
                self.tenant_id,
                self.deal_id,
                self.preparation_id,
                [
                    [
                        row.sequence,
                        row.generated_upload_name,
                        row.source_locator_sha256,
                        row.content_sha256,
                        row.size_bytes,
                        row.extension,
                    ]
                    for row in self.files
                ],
            ]
        )

    def upload_metadata(self, sequence: int) -> dict[str, object]:
        """Return existing upload metadata, with the actual preselected name."""
        self.validate()
        row = self.files[sequence]
        return {
            "source_system": PREVIEW_SOURCE_SYSTEM,
            "source_tree_fingerprint": self.source_tree_fingerprint,
            "upload_filename": row.generated_upload_name,
        }

    def build_manifest(self, documents: Sequence[Mapping[str, Any]]) -> SourcePackageManifest:
        """Bind only actual returned durable rows; do not rename an old upload."""
        self.validate()
        if len(documents) != len(self.files):
            raise ValueError("preview preparation has incomplete upload receipts")
        by_name: dict[str, Mapping[str, Any]] = {}
        for document in documents:
            metadata = document.get("source_metadata")
            if (
                not isinstance(metadata, Mapping)
                or document.get("tenant_id") != self.tenant_id
                or document.get("deal_id") != self.deal_id
                or metadata.get("source_system") != PREVIEW_SOURCE_SYSTEM
                or metadata.get("source_tree_fingerprint") != self.source_tree_fingerprint
            ):
                raise ValueError("preview upload receipt scope does not match")
            name = metadata.get("upload_filename")
            if not isinstance(name, str) or name in by_name:
                raise ValueError("preview upload receipt name is invalid")
            by_name[name] = document
        files = []
        for row in self.files:
            uploaded = by_name.get(row.generated_upload_name)
            if uploaded is None:
                raise ValueError("preview upload receipt name does not match")
            metadata = uploaded["source_metadata"]
            if (
                uploaded.get("sha256") != row.content_sha256
                or metadata.get("size_bytes") != row.size_bytes
            ):
                raise ValueError("preview upload receipt content does not match")
            files.append(
                {
                    "sequence": row.sequence,
                    "generated_upload_name": row.generated_upload_name,
                    "source_locator_sha256": row.source_locator_sha256,
                    "content_sha256": row.content_sha256,
                    "size_bytes": row.size_bytes,
                    "extension": row.extension,
                    "document_id": str(UUID(str(uploaded["document_id"]))),
                    "doc_id": str(UUID(str(uploaded["doc_id"]))),
                }
            )
        payload = {
            "schema_version": "source-package-manifest-v1",
            "company_alias": "company-999",
            "deal_alias": "deal-999",
            "document_set_alias": "documents-999",
            "run_alias": "run-999",
            "ledger_alias": "ledger-999",
            "package_alias": "package-999",
            "source_tree_fingerprint": self.source_tree_fingerprint,
            "file_count": len(files),
            "total_bytes": sum(row.size_bytes for row in self.files),
            "files": files,
        }
        return SourcePackageManifest.model_validate(
            {
                **payload,
                "manifest_sha256": canonical_source_manifest_sha256(payload),
            }
        )


def create_preview_preparation(
    *,
    tenant_id: str,
    deal_id: str,
    files: Sequence[tuple[str, bytes]],
    preparation_id: str | None = None,
) -> PreviewSourcePreparation:
    """Observe the selected bytes once; caller retains them for actual upload."""
    tenant, deal, preparation = (
        str(UUID(tenant_id)),
        str(UUID(deal_id)),
        str(UUID(preparation_id)) if preparation_id else str(uuid4()),
    )
    rows = tuple(
        PreviewPreparationFile(
            sequence=sequence,
            generated_upload_name=f"company-999-document-{sequence + 1:05d}.{extension}",
            source_locator_sha256=_hash(
                ["idis-preview-source-locator-v1", tenant, deal, preparation, sequence]
            ),
            content_sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            extension=extension,
        )
        for sequence, (extension, data) in enumerate(files)
    )
    result = PreviewSourcePreparation(tenant, deal, preparation, rows, "")
    result = PreviewSourcePreparation(tenant, deal, preparation, rows, result._fingerprint())
    result.validate()
    return result


@dataclass(frozen=True)
class PreviewSourceFile:
    """Private in-process input; never persisted as a new API contract."""

    extension: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class PreparedPreviewSourcePackage:
    """Actual OPEN staging package, still requiring the existing run freeze gate."""

    preparation: PreviewSourcePreparation
    manifest: SourcePackageManifest
    package_id: str
    document_ids: tuple[str, ...]


class PreviewSourcePreparationError(ValueError):
    """Safe prerequisite failure; no raw input or object-store location is exposed."""

    code = "PREVIEW_SOURCE_PREPARATION_REQUIRED"

    def __init__(self, prerequisite_code: str | None = None) -> None:
        self.prerequisite_code = (
            prerequisite_code
            if prerequisite_code
            in {
                "PARSER_NATIVE_IDENTITY_UNAVAILABLE",
                "PARSER_TERMINAL_RECEIPT_UNAVAILABLE",
            }
            else None
        )
        super().__init__(self.code)


def prepare_preview_source_package(
    *,
    engine: Engine,
    ctx: IngestionContext,
    deal_id: UUID,
    files: Sequence[PreviewSourceFile],
    compliant_store: ComplianceEnforcedStore,
    audit_sink: PostgresAuditSink,
) -> PreparedPreviewSourcePackage:
    """Explicit trusted-tool composition of ingestion and OPEN package creation.

    Names and the domain-separated preparation are fixed before upload. Only
    actual tenant-scoped durable receipts enter the unchanged manifest builder.
    This does not freeze, start a run, authorize an exclusion or represent a
    two-company acceptance package. SQL/audit writes use one app transaction;
    prior object puts retain the existing storage retention/erasure semantics.
    """
    from idis.persistence.repositories.data_room_packages import PostgresDataRoomPackagesRepository
    from idis.persistence.repositories.documents import PostgresDocumentsRepository
    from idis.services.data_room.package_service import create_data_room_package
    from idis.services.ingestion import IngestionService

    preparation = create_preview_preparation(
        tenant_id=str(ctx.tenant_id),
        deal_id=str(deal_id),
        files=[(file.extension, file.data) for file in files],
    )
    with engine.begin() as connection:
        ingestion = IngestionService(
            compliant_store=compliant_store,
            audit_sink=audit_sink,
            db_conn=connection,
        )
        documents_repository = PostgresDocumentsRepository(connection, str(ctx.tenant_id))
        documents = []
        for sequence, file in enumerate(files):
            result = ingestion.ingest_bytes(
                ctx=ctx,
                deal_id=deal_id,
                filename=preparation.files[sequence].generated_upload_name,
                media_type=file.media_type,
                data=file.data,
                metadata=preparation.upload_metadata(sequence),
            )
            if result.document_id is None:
                raise PreviewSourcePreparationError()
            document = documents_repository.get_document(str(result.document_id))
            if document is None:
                raise PreviewSourcePreparationError()
            prerequisite = document.get("metadata", {}).get("source_preparation_prerequisite")
            if prerequisite is not None or not result.success:
                raise PreviewSourcePreparationError(prerequisite)
            documents.append(document)
        manifest = preparation.build_manifest(documents)
        document_ids = tuple(str(file.document_id) for file in manifest.files)
        package = create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(connection, str(ctx.tenant_id)),
            tenant_id=str(ctx.tenant_id),
            deal_id=str(deal_id),
            created_by_actor_id=ctx.actor_id,
            created_by_actor_type=ctx.actor_type,
            document_ids=document_ids,
            documents=documents,
            source_manifest=manifest,
        )
        return PreparedPreviewSourcePackage(
            preparation,
            manifest,
            str(package["package_id"]),
            document_ids,
        )
