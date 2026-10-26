"""Task 3 RED tests for durable source-manifest/package binding.

These are intentionally service/repository tests: source discovery and upload
resume live in the other Task 3 modules.  A strict manifest must be validated
before any package row is created, then remain bound to the exact durable
documents and safe per-file ledger rows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

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
    SourcePackageManifest,
    SourcePackageManifestFile,
    canonical_source_manifest_sha256,
)
from idis.persistence.repositories.data_room_packages import (
    InMemoryDataRoomPackagesRepository,
    PostgresDataRoomPackagesRepository,
    clear_data_room_packages_store,
)
from idis.services.data_room.package_service import (
    DataRoomPackageError,
    build_data_room_package_summary,
    create_data_room_package,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
DEAL = UUID("22222222-2222-2222-2222-222222222222")
OTHER_DEAL = UUID("22222222-2222-2222-2222-222222222223")
DOC_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DOC_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SHA_A = "a" * 64
SHA_B = "b" * 64
PATH_A = "1" * 64
PATH_B = "2" * 64
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_package_store() -> None:
    clear_data_room_packages_store()


def _document(document_id: UUID, sha256: str, doc_type: str) -> dict[str, Any]:
    sequence = 0 if document_id == DOC_A else 1
    extension = doc_type.lower()
    return {
        "tenant_id": str(TENANT),
        "deal_id": str(DEAL),
        "document_id": str(document_id),
        "doc_id": str(document_id),
        "doc_type": doc_type,
        "parse_status": "PARSED",
        "sha256": sha256,
        # A strict package must take its path hash from the source manifest, not
        # from this private persisted object locator.
        "uri": f"deals/private-customer/{document_id}/private-board-pack.{doc_type.lower()}",
        "metadata": {
            "parser_support_status": "supported",
            "parser_triage_status": "ready",
            "parser_reason_codes": [],
            "parse_error_codes": [],
        },
        "source_metadata": {
            "upload_filename": f"company-001-document-{sequence + 1:05d}.{extension}",
            "size_bytes": 10 if document_id == DOC_A else 20,
        },
    }


def _documents() -> list[dict[str, Any]]:
    return [_document(DOC_A, SHA_A, "PDF"), _document(DOC_B, SHA_B, "XLSX")]


def _manifest_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "company_alias": "company-001",
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": "c" * 64,
        "file_count": 2,
        "total_bytes": 30,
        "files": [
            {
                "sequence": 0,
                "generated_upload_name": "company-001-document-00001.pdf",
                "source_locator_sha256": PATH_A,
                "content_sha256": SHA_A,
                "size_bytes": 10,
                "extension": "pdf",
                "document_id": str(DOC_A),
                "doc_id": str(DOC_A),
            },
            {
                "sequence": 1,
                "generated_upload_name": "company-001-document-00002.xlsx",
                "source_locator_sha256": PATH_B,
                "content_sha256": SHA_B,
                "size_bytes": 20,
                "extension": "xlsx",
                "document_id": str(DOC_B),
                "doc_id": str(DOC_B),
            },
        ],
    }
    payload["manifest_sha256"] = canonical_source_manifest_sha256(payload)
    return payload


def _manifest() -> SourcePackageManifest:
    return SourcePackageManifest.model_validate(_manifest_payload())


def _create(
    repo: InMemoryDataRoomPackagesRepository,
    *,
    document_ids: list[str] | None = None,
    documents: list[dict[str, Any]] | None = None,
    source_manifest: SourcePackageManifest | dict[str, Any] | None = None,
) -> dict[str, Any]:
    return create_data_room_package(
        repo=repo,
        tenant_id=str(TENANT),
        deal_id=str(DEAL),
        created_by_actor_id="slice100-acceptance-service",
        created_by_actor_type="SERVICE",
        document_ids=document_ids or [str(DOC_A), str(DOC_B)],
        documents=documents or _documents(),
        source_manifest=source_manifest,
    )


def _rehashed(payload: dict[str, Any]) -> dict[str, Any]:
    payload["manifest_sha256"] = canonical_source_manifest_sha256(payload)
    return payload


def test_strict_manifest_is_persisted_and_drives_exact_safe_file_rows() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    manifest = _manifest()

    summary = _create(repo, source_manifest=manifest)

    package = repo.get_package(summary["package_id"], str(DEAL))
    assert package is not None
    assert package.metadata["source_manifest"] == manifest.model_dump(mode="json")

    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    assert [row.sequence for row in files] == [0, 1]
    # Upload-first staging is not a durable source binding. The unchanged
    # manifest pins real upload IDs; only actual freeze attaches ledger IDs.
    assert all(row.document_id is None and row.doc_id is None for row in files)
    assert [row.document_id for row in manifest.files] == [DOC_A, DOC_B]
    assert [row.path_hash for row in files] == [PATH_A, PATH_B]
    assert [row.sha256 for row in files] == [SHA_A, SHA_B]
    assert [row.extension for row in files] == ["pdf", "xlsx"]
    assert summary["file_count"] == manifest.file_count == 2


def test_strict_manifest_size_must_match_durable_upload_byte_count() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    payload = _manifest_payload()
    payload["files"][0]["size_bytes"] = 11
    payload["total_bytes"] = 31
    _rehashed(payload)

    with pytest.raises(DataRoomPackageError) as exc:
        _create(repo, source_manifest=payload)

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"


def test_invalid_persisted_strict_manifest_fails_closed_on_read() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    summary = _create(repo, source_manifest=_manifest())
    package = repo.get_package(summary["package_id"], str(DEAL))
    assert package is not None
    package.metadata["source_manifest"]["manifest_sha256"] = "f" * 64
    files = repo.list_files_by_package(summary["package_id"], str(DEAL))

    with pytest.raises(DataRoomPackageError) as exc:
        build_data_room_package_summary(package, files)

    assert exc.value.reason_code == "SOURCE_MANIFEST_RECONCILIATION_FAILED"


def test_null_persisted_strict_manifest_cannot_downgrade_to_legacy() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    summary = _create(repo, source_manifest=_manifest())
    package = repo.get_package(summary["package_id"], str(DEAL))
    assert package is not None
    package.metadata["source_manifest"] = None
    files = repo.list_files_by_package(summary["package_id"], str(DEAL))

    with pytest.raises(DataRoomPackageError) as exc:
        build_data_room_package_summary(package, files)

    assert exc.value.reason_code == "SOURCE_MANIFEST_RECONCILIATION_FAILED"


def test_strict_manifest_and_durable_file_rows_are_reconciled_on_read() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    summary = _create(repo, source_manifest=_manifest())
    package = repo.get_package(summary["package_id"], str(DEAL))
    assert package is not None
    files = repo.list_files_by_package(summary["package_id"], str(DEAL))

    with pytest.raises(DataRoomPackageError) as exc:
        build_data_room_package_summary(package, files[:1])

    assert exc.value.reason_code == "SOURCE_MANIFEST_RECONCILIATION_FAILED"


@pytest.mark.parametrize(
    ("document_ids", "mutate_documents"),
    [
        ([str(DOC_B), str(DOC_A)], None),
        ([str(DOC_A)], None),
        ([str(DOC_A), str(DOC_B)], lambda docs: docs[:1]),
        (
            [str(DOC_A), str(DOC_B)],
            lambda docs: [docs[0], {**docs[1], "sha256": "d" * 64}],
        ),
    ],
    ids=("wrong-order", "missing-requested-id", "missing-corpus-member", "content-hash-drift"),
)
def test_strict_manifest_requires_exact_document_order_membership_and_content_hashes(
    document_ids: list[str], mutate_documents: Any
) -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    documents = _documents()
    if mutate_documents is not None:
        documents = mutate_documents(documents)

    with pytest.raises(DataRoomPackageError) as exc:
        _create(
            repo,
            document_ids=document_ids,
            documents=documents,
            source_manifest=_manifest(),
        )

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"
    assert repo.list_packages_by_deal(str(DEAL)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.pop("source_metadata"),
        lambda doc: doc.update(deal_id=str(OTHER_DEAL)),
        lambda doc: doc.update(tenant_id="11111111-1111-1111-1111-111111111112"),
        lambda doc: doc.update(doc_id=str(DOC_B)),
    ],
    ids=("missing-upload-binding", "cross-deal", "cross-tenant", "artifact-id-drift"),
)
def test_strict_manifest_requires_exact_durable_scope_and_artifact_binding(
    mutation: Any,
) -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    document = _documents()[0]
    mutation(document)
    payload = _manifest_payload()
    payload["files"] = [payload["files"][0]]
    payload["file_count"] = 1
    payload["total_bytes"] = 10
    _rehashed(payload)

    with pytest.raises(DataRoomPackageError) as exc:
        _create(
            repo,
            document_ids=[str(DOC_A)],
            documents=[document],
            source_manifest=payload,
        )

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"
    assert repo.list_packages_by_deal(str(DEAL)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["files"][1].update(
            source_locator_sha256=p["files"][0]["source_locator_sha256"]
        ),
        lambda p: p["files"][1].update(content_sha256=p["files"][0]["content_sha256"]),
        lambda p: p["files"][0].update(source_locator_sha256=""),
        lambda p: p["files"][0].update(content_sha256=""),
    ],
    ids=(
        "duplicate-path-hash",
        "duplicate-content-hash",
        "missing-path-hash",
        "missing-content-hash",
    ),
)
def test_strict_manifest_rejects_duplicate_or_missing_hashes_before_persistence(
    mutation: Any,
) -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    payload = _manifest_payload()
    mutation(payload)
    _rehashed(payload)

    with pytest.raises(DataRoomPackageError) as exc:
        _create(repo, source_manifest=payload)

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"
    assert repo.list_packages_by_deal(str(DEAL)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(file_count=3),
        lambda p: p.update(total_bytes=31),
        lambda p: p["files"][1].update(sequence=3),
        lambda p: p["files"][1].update(extension="pdf"),
        lambda p: p["files"][0].update(document_id=str(DOC_B)),
    ],
    ids=("count", "bytes", "sequence", "extension", "document-id"),
)
def test_strict_manifest_rejects_rehashed_internal_contract_mismatch(mutation: Any) -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    payload = _manifest_payload()
    mutation(payload)
    _rehashed(payload)

    with pytest.raises(DataRoomPackageError) as exc:
        _create(repo, source_manifest=payload)

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"
    assert repo.list_packages_by_deal(str(DEAL)) == []


def test_strict_manifest_rejects_manifest_digest_mismatch_before_persistence() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    payload = _manifest_payload()
    payload["manifest_sha256"] = "f" * 64

    with pytest.raises(DataRoomPackageError) as exc:
        _create(repo, source_manifest=payload)

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"
    assert repo.list_packages_by_deal(str(DEAL)) == []


def test_manifest_file_model_rejects_unsafe_extension_and_invalid_path_hash() -> None:
    file_payload = _manifest_payload()["files"][0]

    with pytest.raises(ValueError):
        SourcePackageManifestFile.model_validate({**file_payload, "extension": "../pdf"})
    with pytest.raises(ValueError):
        SourcePackageManifestFile.model_validate(
            {**file_payload, "source_locator_sha256": "not-a-hash"}
        )


def test_strict_manifest_binds_generated_upload_name_for_generic_document_type() -> None:
    """PNG/media/text uploads retain their real extension in safe source metadata."""
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    payload = _manifest_payload()
    payload["files"] = [
        {
            **payload["files"][0],
            "generated_upload_name": "company-001-document-00001.png",
            "extension": "png",
        }
    ]
    payload["file_count"] = 1
    payload["total_bytes"] = 10
    _rehashed(payload)
    document = _document(DOC_A, SHA_A, "IMAGE")
    document["source_metadata"] = {
        "upload_filename": "company-001-document-00001.png",
        "size_bytes": 10,
    }

    summary = _create(
        repo,
        document_ids=[str(DOC_A)],
        documents=[document],
        source_manifest=payload,
    )

    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    assert files[0].extension == "png"
    assert "sequence" not in files[0].safe_dict()
    assert files[0].safe_dict(include_sequence=True)["sequence"] == 0


def test_strict_manifest_rejects_durable_generated_upload_name_drift() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    document = _documents()[0]
    document["source_metadata"] = {"upload_filename": "company-001-document-99999.pdf"}
    payload = _manifest_payload()
    payload["files"] = [payload["files"][0]]
    payload["file_count"] = 1
    payload["total_bytes"] = 10
    _rehashed(payload)

    with pytest.raises(DataRoomPackageError) as exc:
        _create(
            repo,
            document_ids=[str(DOC_A)],
            documents=[document],
            source_manifest=payload,
        )

    assert exc.value.reason_code == "SOURCE_MANIFEST_INVALID"
    assert repo.list_packages_by_deal(str(DEAL)) == []


def test_in_memory_repository_rejects_file_whose_deal_does_not_own_package() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    package = DataRoomPackage.model_validate(
        {
            "package_id": "33333333-3333-3333-3333-333333333333",
            "tenant_id": str(TENANT),
            "deal_id": str(DEAL),
            "status": DataRoomPackageStatus.OPEN,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    repo.create_package(package)
    wrong_deal_file = DataRoomPackageFile.model_validate(
        {
            "file_entry_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": str(TENANT),
            "package_id": str(package.package_id),
            "deal_id": str(OTHER_DEAL),
            "sequence": 0,
            "path_hash": PATH_A,
            "extension": "pdf",
            "sha256": SHA_A,
            "file_status": DataRoomFileStatus.SUPPORTED,
            "support_status": DocumentSupportStatus.SUPPORTED,
            "triage_status": DocumentTriageStatus.READY,
            "parse_status": ParseStatus.PARSED,
            "document_id": str(DOC_A),
            "doc_id": str(DOC_A),
            "created_at": NOW,
        }
    )

    with pytest.raises(ValueError, match="Package/deal mismatch"):
        repo.add_file(wrong_deal_file)
    assert repo.list_files_by_package(str(package.package_id), str(DEAL)) == []


def test_postgres_repository_atomically_rejects_package_deal_mismatch() -> None:
    class _NoRowResult:
        rowcount = 0

        @staticmethod
        def fetchone() -> None:
            return None

    class _NoRowConnection:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, statement: Any, _params: Any = None) -> _NoRowResult:
            self.sql.append(str(statement))
            return _NoRowResult()

    connection = _NoRowConnection()
    with patch("idis.persistence.repositories.data_room_packages.set_tenant_local"):
        repo = PostgresDataRoomPackagesRepository(connection, str(TENANT))  # type: ignore[arg-type]

    package = DataRoomPackage.model_validate(
        {
            "package_id": "33333333-3333-3333-3333-333333333333",
            "tenant_id": str(TENANT),
            "deal_id": str(DEAL),
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    file = DataRoomPackageFile.model_validate(
        {
            "file_entry_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": str(TENANT),
            "package_id": str(package.package_id),
            "deal_id": str(OTHER_DEAL),
            "sequence": 0,
            "path_hash": PATH_A,
            "extension": "pdf",
            "sha256": SHA_A,
            "file_status": "supported",
            "support_status": "supported",
            "triage_status": "ready",
            "parse_status": "PARSED",
            "document_id": str(DOC_A),
            "doc_id": str(DOC_A),
            "created_at": NOW,
        }
    )

    with pytest.raises(ValueError, match="Package/deal mismatch"):
        repo.add_file(file)
    insert_sql = connection.sql[0]
    assert "INSERT INTO data_room_package_files" in insert_sql
    assert "FROM data_room_packages" in insert_sql
    assert "deal_id = :deal_id" in insert_sql


def test_legacy_source_manifest_none_preserves_slice77_package_behavior() -> None:
    repo = InMemoryDataRoomPackagesRepository(str(TENANT))

    summary = _create(repo, source_manifest=None)

    package = repo.get_package(summary["package_id"], str(DEAL))
    assert package is not None
    assert "source_manifest" not in package.metadata
    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    assert len(files) == 2
    assert [row.sha256 for row in files] == [SHA_A, SHA_B]
    assert [row.extension for row in files] == ["pdf", "xlsx"]


def test_strict_manifest_roundtrips_through_real_http_route_with_idempotent_replay(
    monkeypatch: Any,
) -> None:
    from tests.test_slice77_data_room_package import _ANALYST, _setup_data_room_api

    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [])
    tenant_id = client.app.state.test_tenant_id
    document = _documents()[0]
    document["tenant_id"] = tenant_id
    document["deal_id"] = deal_id
    client.app.state.deal_documents[deal_id] = [document]
    payload = _manifest_payload()
    payload["files"] = [payload["files"][0]]
    payload["file_count"] = 1
    payload["total_bytes"] = 10
    _rehashed(payload)
    request_body = json.dumps(
        {"document_ids": [str(DOC_A)], "source_manifest": payload},
        sort_keys=True,
    )
    headers = {**_ANALYST, "Idempotency-Key": "idis-task3-route-replay"}

    first = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=headers,
        content=request_body,
    )
    replay = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=headers,
        content=request_body,
    )

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    record = client.get(
        f"/v1/deals/{deal_id}/data-room-packages/{first.json()['package_id']}",
        headers=_ANALYST,
    )
    assert record.status_code == 200
    assert record.json()["source_manifest"]["run_alias"] == "run-001"
    assert "doc_id" not in record.json()["files"][0]
    assert "document_id" not in record.json()["files"][0]
    assert record.json()["files"][0]["sequence"] == 0


@pytest.mark.parametrize(
    ("status", "pair", "allowed"),
    [
        ("OPEN", "null", True),
        ("OPEN", "exact", True),
        ("SEALED", "exact", True),
        ("SEALED", "null", False),
        ("OPEN", "half", False),
        ("SEALED", "half", False),
        ("OPEN", "wrong", False),
        (None, "null", False),
        (None, "exact", False),
        ("UNKNOWN", "exact", False),
    ],
)
def test_acceptance_producer_reconciles_only_open_null_or_exact_known_state(
    status: str | None, pair: str, allowed: bool
) -> None:
    from idis.evaluation.real_example_source_package import (
        SourcePackageContractError,
        _validate_durable_package_record,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    manifest = _manifest_payload()
    record = _create(repo, source_manifest=manifest)
    package_id = record["package_id"]
    files = repo.list_files_by_package(package_id, str(DEAL))
    record["files"] = [row.safe_dict(include_sequence=True) for row in files]
    if status is None:
        record.pop("status")
    else:
        record["status"] = status
    for actual, expected in zip(record["files"], manifest["files"], strict=True):
        actual.pop("doc_id", None)
        actual.pop("document_id", None)
        if pair in {"exact", "half", "wrong"}:
            actual["document_id"] = expected["document_id"]
        if pair in {"exact", "wrong"}:
            actual["doc_id"] = expected["doc_id"] if pair == "exact" else str(OTHER_DEAL)

    def call() -> None:
        _validate_durable_package_record(
            record=record, deal_id=str(DEAL), package_id=package_id, manifest_payload=manifest
        )

    if allowed:
        call()
    else:
        with pytest.raises(SourcePackageContractError) as caught:
            call()
        assert caught.value.code == "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE"


@pytest.mark.parametrize("field", ["sequence", "path_hash", "sha256", "extension"])
def test_acceptance_open_null_pair_never_waives_other_file_identity(field: str) -> None:
    from idis.evaluation.real_example_source_package import (
        SourcePackageContractError,
        _validate_durable_package_record,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    manifest = _manifest_payload()
    record = _create(repo, source_manifest=manifest)
    files = repo.list_files_by_package(record["package_id"], str(DEAL))
    record["files"] = [row.safe_dict(include_sequence=True) for row in files]
    for row in record["files"]:
        row.pop("document_id", None)
        row.pop("doc_id", None)
    record["files"][0][field] = 99 if field == "sequence" else "drift"
    with pytest.raises(SourcePackageContractError) as caught:
        _validate_durable_package_record(
            record=record,
            deal_id=str(DEAL),
            package_id=record["package_id"],
            manifest_payload=manifest,
        )
    assert caught.value.code == "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE"
