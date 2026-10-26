"""Disposable-PostgreSQL composition proofs for the Task 7 source freeze."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, text
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals

from idis.models.source_package_manifest import canonical_source_manifest_sha256
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.task7_source_freeze import (
    PostgresTask7SourceFreezeRepository,
    SourceFreezeObservation,
    SourceFreezePlan,
    Task7SourceFreezeError,
    freeze_source_package,
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _provision(database: Any) -> None:
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
    migrate(database.admin_engine, "0038")
    grant_application_access(database)


def _seed_open_source(
    connection: Connection,
    *,
    label: str,
) -> dict[str, str]:
    values = {
        key: str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-source-freeze:{label}:{key}"))
        for key in (
            "tenant_id",
            "deal_id",
            "package_id",
            "file_entry_id",
            "doc_id",
            "document_id",
            "attempt_id",
            "span_id",
            "version_id",
        )
    }
    content_sha256 = _sha256(f"{label}:source-bytes")
    locator_sha256 = _sha256(f"{label}:source-locator")
    span_text = f"Fictional source-freeze evidence {label}"
    span_sha256 = _sha256(span_text)
    object_uri = f"object://fictional-task7/{values['doc_id']}"
    manifest: dict[str, Any] = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "company-001",
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": _sha256(f"{label}:source-tree"),
        "file_count": 1,
        "total_bytes": 24,
        "files": [
            {
                "sequence": 0,
                "generated_upload_name": "company-001-document-00001.docx",
                "source_locator_sha256": locator_sha256,
                "content_sha256": content_sha256,
                "size_bytes": 24,
                "extension": "docx",
                "document_id": values["document_id"],
                "doc_id": values["doc_id"],
            }
        ],
    }
    manifest["manifest_sha256"] = canonical_source_manifest_sha256(manifest)
    terminal_projection = {
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "applicable_schema_refs": [],
    }
    terminal_receipt_sha256 = _canonical_sha256(
        [
            "terminal-document-metadata-v2",
            "DOCX",
            "PARSED",
            terminal_projection,
        ]
    )
    artifact_metadata = {
        "durable_object_integrity": {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": values["doc_id"],
            "content_sha256": content_sha256,
            "size_bytes": 24,
            "uri_sha256": _sha256(object_uri),
            "version_id": values["version_id"],
            "backend": "compliant:postgres-test-object-store",
            "streaming_verified": True,
        }
    }
    document_metadata = {
        "parser_runtime": "built_in",
        "parser_runtime_status": "completed",
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "parser_reason_codes": ["docx_parse_completed"],
        "parse_warning_codes": [],
        "parse_error_codes": [],
        "applicable_schema_refs": [],
        "span_count": 1,
    }
    observed_at = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    # The frozen source-bound guard is a FORCE-RLS definer and requires canonical tenant context.
    set_tenant_local(connection, values["tenant_id"])
    connection.execute(
        text(
            """
            INSERT INTO tenants (tenant_id,name)
            VALUES (CAST(:tenant_id AS uuid),:tenant_name);
            INSERT INTO deals (deal_id,tenant_id,name,created_at)
            VALUES (
                CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),:deal_name,
                CAST(:observed_at AS timestamptz)
            );
            INSERT INTO document_artifacts (
                doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,
                ingested_at,sha256,uri,metadata,source_binding_state,
                source_lineage_object_id,source_corpus_receipt_id
            ) VALUES (
                CAST(:doc_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                'DATA_ROOM_FILE',:title,'task7-postgres-fixture',:version_id,
                CAST(:started_at AS timestamptz),:content_sha256,:object_uri,
                CAST(:artifact_metadata AS jsonb),'UNBOUND',NULL,NULL
            );
            INSERT INTO documents (
                document_id,tenant_id,deal_id,doc_id,doc_type,parse_status,metadata,
                created_at,updated_at,source_binding_state,
                source_lineage_object_id,source_corpus_receipt_id
            ) VALUES (
                CAST(:document_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:doc_id AS uuid),'DOCX','PARSED',
                CAST(:document_metadata AS jsonb),CAST(:started_at AS timestamptz),
                CAST(:finished_at AS timestamptz),'UNBOUND',NULL,NULL
            );
            INSERT INTO document_parse_attempts (
                parse_attempt_id,tenant_id,deal_id,document_id,attempt_ordinal,
                parser_name,parser_version,input_sha256,receipt_sha256,status,
                started_at,finished_at,source_binding_state,source_lineage_object_id,
                source_corpus_receipt_id,write_origin
            ) VALUES (
                CAST(:attempt_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:document_id AS uuid),1,
                'built_in','runtime-parser-v1',:content_sha256,
                :terminal_receipt_sha256,'PARSED',CAST(:started_at AS timestamptz),
                CAST(:finished_at AS timestamptz),'UNBOUND',NULL,NULL,'RUNTIME'
            );
            INSERT INTO document_spans (
                span_id,tenant_id,deal_id,document_id,span_type,locator,text_excerpt,
                content_hash,parse_attempt_id,source_binding_state,
                source_lineage_object_id,source_corpus_receipt_id
            ) VALUES (
                CAST(:span_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                CAST(:document_id AS uuid),'PARAGRAPH',CAST(:locator AS jsonb),
                :span_text,:span_sha256,NULL,'UNBOUND',NULL,NULL
            );
            INSERT INTO data_room_packages (
                package_id,tenant_id,deal_id,status,metadata,created_at,updated_at,
                source_binding_state,source_lineage_object_id
            ) VALUES (
                CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),'OPEN',CAST(:package_metadata AS jsonb),
                CAST(:observed_at AS timestamptz),CAST(:observed_at AS timestamptz),
                'UNBOUND',NULL
            );
            INSERT INTO data_room_package_files (
                file_entry_id,tenant_id,package_id,deal_id,sequence,path_hash,
                extension,sha256,file_status,support_status,triage_status,
                parse_status,reason_codes,error_codes,doc_id,document_id,storage_uri,
                source_binding_state,source_lineage_object_id
            ) VALUES (
                CAST(:file_entry_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:package_id AS uuid),CAST(:deal_id AS uuid),0,:locator_sha256,
                'docx',:content_sha256,'supported','supported','ready','PARSED',
                '["docx_parse_completed"]'::jsonb,'[]'::jsonb,
                NULL,NULL,NULL,
                'UNBOUND',NULL
            );
            """
        ),
        {
            **values,
            "tenant_name": f"Task 7 tenant {label}",
            "deal_name": f"Task 7 deal {label}",
            "title": f"Fictional Task 7 source {label}",
            "content_sha256": content_sha256,
            "locator_sha256": locator_sha256,
            "span_text": span_text,
            "span_sha256": span_sha256,
            "object_uri": object_uri,
            "artifact_metadata": json.dumps(artifact_metadata),
            "document_metadata": json.dumps(document_metadata),
            "package_metadata": json.dumps({"source_manifest": manifest}),
            "terminal_receipt_sha256": terminal_receipt_sha256,
            "locator": json.dumps({"paragraph": 0}),
            "observed_at": observed_at,
            "started_at": observed_at - timedelta(seconds=2),
            "finished_at": observed_at - timedelta(seconds=1),
        },
    )
    return values


def _source_state(connection: Connection, package_id: str) -> tuple[Any, ...]:
    return tuple(
        connection.execute(
            text(
                """
                SELECT p.status,p.source_binding_state,
                       (p.source_lineage_object_id IS NOT NULL) AS has_package_root,
                       (SELECT count(*) FROM source_lineage_objects root
                        WHERE root.corpus_receipt_id=p.package_id) AS root_count,
                       (SELECT count(*) FROM data_room_package_files f
                        WHERE f.package_id=p.package_id
                          AND f.source_binding_state='BOUND') AS bound_file_count,
                       (SELECT count(*) FROM document_artifacts a
                        WHERE a.source_corpus_receipt_id=p.package_id
                          AND a.source_binding_state='BOUND') AS bound_artifact_count,
                       (SELECT count(*) FROM documents d
                        WHERE d.source_corpus_receipt_id=p.package_id
                          AND d.source_binding_state='BOUND') AS bound_document_count,
                       (SELECT count(*) FROM document_parse_attempts a
                        WHERE a.source_corpus_receipt_id=p.package_id
                          AND a.source_binding_state='BOUND') AS bound_attempt_count,
                       (SELECT count(*) FROM document_spans s
                        WHERE s.source_corpus_receipt_id=p.package_id
                          AND s.source_binding_state='BOUND') AS bound_span_count,
                       (SELECT count(*) FROM data_room_package_files f
                        WHERE f.package_id=p.package_id
                          AND f.doc_id IS NOT NULL
                          AND f.document_id IS NOT NULL) AS linked_file_count
                FROM data_room_packages p
                WHERE p.package_id=CAST(:package_id AS uuid)
                """
            ),
            {"package_id": package_id},
        ).one()
    )


class _CrashAfterNativeBindRepository(PostgresTask7SourceFreezeRepository):
    bind_calls = 0

    def _bind_native_rows(
        self,
        plan: SourceFreezePlan,
        observation: SourceFreezeObservation,
    ) -> None:
        super()._bind_native_rows(plan, observation)
        type(self).bind_calls += 1
        raise RuntimeError("fictional crash after native bind")


def test_postgres_freeze_success_retry_and_cross_tenant_rls_denial() -> None:
    with disposable_postgres() as database:
        _provision(database)
        with database.admin_engine.begin() as connection:
            seeded = _seed_open_source(connection, label="success")

        first = freeze_source_package(
            engine=database.app_engine,
            tenant_id=seeded["tenant_id"],
            deal_id=seeded["deal_id"],
            package_id=seeded["package_id"],
        )
        second = freeze_source_package(
            engine=database.app_engine,
            tenant_id=seeded["tenant_id"],
            deal_id=seeded["deal_id"],
            package_id=seeded["package_id"],
        )

        assert first == second
        with database.admin_engine.connect() as connection:
            assert _source_state(connection, seeded["package_id"]) == (
                "SEALED",
                "BOUND",
                True,
                6,
                1,
                1,
                1,
                1,
                1,
                1,
            )
        foreign_tenant = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO tenants(tenant_id,name) VALUES (CAST(:id AS uuid),'Foreign')"),
                {"id": foreign_tenant},
            )
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, foreign_tenant)
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM data_room_packages "
                        "WHERE package_id=CAST(:package_id AS uuid)"
                    ),
                    {"package_id": seeded["package_id"]},
                ).scalar_one()
                == 0
            )
            denied_write = connection.execute(
                text(
                    "UPDATE data_room_packages SET status=status "
                    "WHERE package_id=CAST(:package_id AS uuid)"
                ),
                {"package_id": seeded["package_id"]},
            )
            assert denied_write.rowcount == 0
        with pytest.raises(Task7SourceFreezeError) as caught:
            freeze_source_package(
                engine=database.app_engine,
                tenant_id=foreign_tenant,
                deal_id=seeded["deal_id"],
                package_id=seeded["package_id"],
            )
        assert caught.value.code == "TASK7_SOURCE_FREEZE_FENCED"
        with database.admin_engine.connect() as connection:
            assert _source_state(connection, seeded["package_id"]) == (
                "SEALED",
                "BOUND",
                True,
                6,
                1,
                1,
                1,
                1,
                1,
                1,
            )


def test_postgres_mid_transition_failure_rolls_back_every_write() -> None:
    with disposable_postgres() as database:
        _provision(database)
        with database.admin_engine.begin() as connection:
            seeded = _seed_open_source(connection, label="rollback")

        _CrashAfterNativeBindRepository.bind_calls = 0
        with pytest.raises(Task7SourceFreezeError):
            freeze_source_package(
                engine=database.app_engine,
                tenant_id=seeded["tenant_id"],
                deal_id=seeded["deal_id"],
                package_id=seeded["package_id"],
                repository_factory=_CrashAfterNativeBindRepository,
            )
        assert _CrashAfterNativeBindRepository.bind_calls == 1

        with database.admin_engine.connect() as connection:
            assert _source_state(connection, seeded["package_id"]) == (
                "OPEN",
                "UNBOUND",
                False,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )


def test_postgres_stale_observation_is_fenced_without_mutation() -> None:
    with disposable_postgres() as database:
        _provision(database)
        with database.admin_engine.begin() as connection:
            seeded = _seed_open_source(connection, label="stale")
        with database.app_engine.begin() as connection:
            repository = PostgresTask7SourceFreezeRepository(
                connection,
                seeded["tenant_id"],
            )
            observation = repository.observe_source_package(
                deal_id=seeded["deal_id"],
                package_id=seeded["package_id"],
            )
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE data_room_packages SET updated_at=updated_at+interval '1 microsecond' "
                    "WHERE package_id=CAST(:package_id AS uuid)"
                ),
                {"package_id": seeded["package_id"]},
            )
        with database.app_engine.begin() as connection:
            repository = PostgresTask7SourceFreezeRepository(
                connection,
                seeded["tenant_id"],
            )
            with pytest.raises(Task7SourceFreezeError) as caught:
                repository.freeze_observed_package(observation)
            assert caught.value.code == "TASK7_SOURCE_FREEZE_FENCED"
        with database.admin_engine.connect() as connection:
            assert _source_state(connection, seeded["package_id"]) == (
                "OPEN",
                "UNBOUND",
                False,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
