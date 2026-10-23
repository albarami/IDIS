"""Disposable-PostgreSQL RED proofs for the reviewed Task-7 migration 0038."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from tests.slice100_task2b._support import (
    disposable_postgres,
    downgrade,
    migrate,
    set_tenant_context,
    tenant_catalog_snapshot,
    tenant_table_inventory,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals
from tests.slice100_task7.test_migration_0038_schema_red import (
    EXPECTED_TENANT_TABLES,
    TASK7_TABLES,
)

from idis.models.source_package_manifest import canonical_source_manifest_sha256

_OUTPUT_BACKFILL_GUARD_TRIGGER = "trg_task7_run_output_objects_append_only"


def _provision(database: Any) -> None:
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
        connection.exec_driver_sql(
            """
            DO $task7$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname='idis_task7_guard_function_owner'
              ) THEN
                CREATE ROLE idis_task7_guard_function_owner
                  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                  NOINHERIT NOBYPASSRLS;
              END IF;
            END
            $task7$;
            """
        )


def _revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _user_trigger_states(connection: Connection) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            text(
                """
                SELECT c.relname,t.tgname,t.tgenabled
                FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal
                ORDER BY c.relname,t.tgname
                """
            )
        ).fetchall()
    )


@contextmanager
def _disabled_output_backfill_guard(engine: Engine) -> Iterator[None]:
    """Create one disposable corruption window without relaxing sibling guards."""

    target = ("run_output_objects", _OUTPUT_BACKFILL_GUARD_TRIGGER)
    with engine.begin() as connection:
        before = _user_trigger_states(connection)
        before_by_target = {(table, trigger): state for table, trigger, state in before}
        assert before_by_target[target] == "O"
        connection.exec_driver_sql(
            "ALTER TABLE public.run_output_objects "
            f"DISABLE TRIGGER {_OUTPUT_BACKFILL_GUARD_TRIGGER}"
        )
        disabled = _user_trigger_states(connection)
        disabled_by_target = {(table, trigger): state for table, trigger, state in disabled}
        changed = {
            key for key, state in before_by_target.items() if disabled_by_target.get(key) != state
        }
        assert changed == {target}
        assert disabled_by_target[target] == "D"
    try:
        yield
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE public.run_output_objects "
                f"ENABLE TRIGGER {_OUTPUT_BACKFILL_GUARD_TRIGGER}"
            )
            assert _user_trigger_states(connection) == before


def _catalog_snapshot(connection: Connection) -> tuple[Any, ...]:
    tables = (*TASK7_TABLES, "runs", "run_steps", "data_room_packages")
    params = {"tables": list(tables)}
    columns = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT table_name, column_name,
                       row_number() OVER (
                         PARTITION BY table_name ORDER BY ordinal_position
                       ) AS logical_ordinal,
                       data_type, udt_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name=ANY(CAST(:tables AS text[]))
                -- Dropped PostgreSQL attnums are never reused; compare live logical order.
                ORDER BY table_name, ordinal_position
                """
            ),
            params,
        ).fetchall()
    )
    constraints = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT c.relname, con.conname, con.contype,
                       pg_get_constraintdef(con.oid, true)
                FROM pg_constraint con
                JOIN pg_class c ON c.oid=con.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public'
                  AND c.relname=ANY(CAST(:tables AS text[]))
                ORDER BY c.relname, con.conname
                """
            ),
            params,
        ).fetchall()
    )
    indexes = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname='public'
                  AND tablename=ANY(CAST(:tables AS text[]))
                ORDER BY tablename, indexname
                """
            ),
            params,
        ).fetchall()
    )
    policies = tuple(row for row in tenant_catalog_snapshot(connection) if str(row[0]) in tables)
    return columns, constraints, indexes, policies


def _run_step_type(connection: Connection) -> tuple[str, int | None]:
    return tuple(
        connection.execute(
            text(
                """
                SELECT data_type, character_maximum_length
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='run_steps'
                  AND column_name='step_name'
                """
            )
        ).one()
    )


def _seed_legacy_run(connection: Connection) -> tuple[str, str, str]:
    tenant_id, deal_id, run_id, step_id = (str(uuid.uuid4()) for _ in range(4))
    connection.execute(
        text(
            """
            INSERT INTO tenants (tenant_id,name)
            VALUES (CAST(:tenant_id AS uuid),'Task 7 migration tenant');
            INSERT INTO deals (deal_id,tenant_id,name,created_at)
            VALUES (CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                    'Fictional migration deal',statement_timestamp());
            INSERT INTO runs (run_id,tenant_id,deal_id,mode,status,idempotency_key)
            VALUES (CAST(:run_id AS uuid),CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid),'FULL','QUEUED',:idempotency_key);
            INSERT INTO run_steps (
                step_id,tenant_id,run_id,step_name,step_order,status
            ) VALUES (
                CAST(:step_id AS uuid),CAST(:tenant_id AS uuid),CAST(:run_id AS uuid),
                -- FULL's historical canonical registry places EXTRACT at order 17.
                'EXTRACT',17,'PENDING'
            );
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "step_id": step_id,
            "idempotency_key": f"task7-{run_id}",
        },
    )
    return tenant_id, deal_id, run_id


def _sha256(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _seed_reconstructible_source(connection: Connection) -> tuple[str, str, str]:
    tenant_id, deal_id, package_id, file_entry_id, document_id, span_id = (
        str(uuid.uuid4()) for _ in range(6)
    )
    content_sha256 = _sha256("task7-valid-source-content")
    locator_sha256 = _sha256("task7-valid-source-locator")
    storage_uri = "object://task7/source-backfill.docx"
    storage_uri_sha256 = hashlib.sha256(storage_uri.encode("utf-8")).hexdigest()
    storage_version_id = str(uuid.uuid4())
    text_excerpt = "Fictional evidence text"
    span_content_sha256 = hashlib.sha256(text_excerpt.encode("utf-8")).hexdigest()
    artifact_metadata = {
        "durable_object_integrity": {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": document_id,
            "content_sha256": content_sha256,
            "size_bytes": 17,
            "uri_sha256": storage_uri_sha256,
            "version_id": storage_version_id,
            "backend": "compliant:filesystem",
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
        "ocr_performed": False,
        "media_transcription_performed": False,
    }
    payload: dict[str, Any] = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "company-001",
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": _sha256("task7-valid-source-tree"),
        "file_count": 1,
        "total_bytes": 17,
        "files": [
            {
                "sequence": 0,
                "generated_upload_name": "company-001-document-00001.docx",
                "source_locator_sha256": locator_sha256,
                "content_sha256": content_sha256,
                "size_bytes": 17,
                "extension": "docx",
                "document_id": document_id,
                "doc_id": document_id,
            }
        ],
    }
    payload["manifest_sha256"] = canonical_source_manifest_sha256(payload)
    connection.execute(
        text(
            """
            INSERT INTO tenants (tenant_id,name)
            VALUES (CAST(:tenant_id AS uuid),'Task 7 source backfill tenant');
            INSERT INTO deals (deal_id,tenant_id,name,created_at)
            VALUES (CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                    'Fictional source backfill deal',statement_timestamp());
            INSERT INTO document_artifacts (
                doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,
                sha256,uri,metadata
            ) VALUES (
                CAST(:document_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                'DATA_ROOM_FILE','Fictional source document','task7-fixture',:artifact_version_id,
                :content_sha256,:storage_uri,CAST(:artifact_metadata AS jsonb)
            );
            INSERT INTO documents (
                document_id,tenant_id,deal_id,doc_id,doc_type,parse_status,metadata,
                created_at,updated_at
            ) VALUES (
                CAST(:document_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                CAST(:document_id AS uuid),'DOCX','PARSED',CAST(:document_metadata AS jsonb),
                CAST('2026-07-21T12:00:00Z' AS timestamptz),
                CAST('2026-07-21T12:00:01Z' AS timestamptz)
            );
            INSERT INTO document_spans (
                span_id,tenant_id,deal_id,document_id,span_type,locator,text_excerpt,
                content_hash
            ) VALUES (
                CAST(:span_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                CAST(:document_id AS uuid),'PARAGRAPH',CAST(:locator AS jsonb),
                :text_excerpt,:span_content_sha256
            );
            INSERT INTO data_room_packages (
                package_id,tenant_id,deal_id,status,metadata
            ) VALUES (
                CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                'SEALED',CAST(:metadata AS jsonb)
            );
            INSERT INTO data_room_package_files (
                file_entry_id,tenant_id,package_id,deal_id,sequence,path_hash,extension,
                sha256,file_status,support_status,triage_status,parse_status,reason_codes,
                error_codes,doc_id,document_id,storage_uri
            ) VALUES (
                CAST(:file_entry_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:package_id AS uuid),CAST(:deal_id AS uuid),0,:locator_sha256,'docx',
                :content_sha256,'supported','supported','ready','PARSED',
                '["docx_parse_completed"]'::jsonb,
                '[]'::jsonb,CAST(:document_id AS uuid),CAST(:document_id AS uuid),
                :storage_uri
            );
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "package_id": package_id,
            "file_entry_id": file_entry_id,
            "document_id": document_id,
            "span_id": span_id,
            "content_sha256": content_sha256,
            "locator_sha256": locator_sha256,
            "storage_uri": storage_uri,
            "artifact_version_id": content_sha256[:12],
            "artifact_metadata": json.dumps(artifact_metadata),
            "document_metadata": json.dumps(document_metadata),
            "text_excerpt": text_excerpt,
            "span_content_sha256": span_content_sha256,
            "locator": json.dumps({"paragraph": 0}),
            "metadata": json.dumps({"source_manifest": payload}),
        },
    )
    return tenant_id, deal_id, package_id


def _source_backfill_snapshot(connection: Connection, package_id: str) -> tuple[Any, ...]:
    source_rows = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT source_lineage_object_id,object_kind,source_version,
                       source_disposition,object_identity_sha256,content_sha256,
                       corpus_receipt_sha256,write_origin
                FROM source_lineage_objects
                WHERE corpus_receipt_id=CAST(:package_id AS uuid)
                ORDER BY object_kind,source_lineage_object_id
                """
            ),
            {"package_id": package_id},
        ).fetchall()
    )
    attempts = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT parse_attempt_id,document_id,attempt_ordinal,status,write_origin,
                       source_binding_state,source_lineage_object_id,source_corpus_receipt_id
                FROM document_parse_attempts
                WHERE source_corpus_receipt_id=CAST(:package_id AS uuid)
                ORDER BY document_id,attempt_ordinal,parse_attempt_id
                """
            ),
            {"package_id": package_id},
        ).fetchall()
    )
    bindings = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT 'PACKAGE',source_binding_state,source_lineage_object_id
                FROM data_room_packages WHERE package_id=CAST(:package_id AS uuid)
                UNION ALL
                SELECT 'PACKAGE_FILE',source_binding_state,source_lineage_object_id
                FROM data_room_package_files WHERE package_id=CAST(:package_id AS uuid)
                UNION ALL
                SELECT 'ARTIFACT',source_binding_state,source_lineage_object_id
                FROM document_artifacts WHERE source_corpus_receipt_id=CAST(:package_id AS uuid)
                UNION ALL
                SELECT 'DOCUMENT',source_binding_state,source_lineage_object_id
                FROM documents WHERE source_corpus_receipt_id=CAST(:package_id AS uuid)
                UNION ALL
                SELECT 'SPAN',source_binding_state,source_lineage_object_id
                FROM document_spans WHERE source_corpus_receipt_id=CAST(:package_id AS uuid)
                ORDER BY 1
                """
            ),
            {"package_id": package_id},
        ).fetchall()
    )
    return source_rows, attempts, bindings


def _legacy_run_snapshot(connection: Connection, run_id: str) -> tuple[Any, ...]:
    run = tuple(
        connection.execute(
            text(
                """
                SELECT run_id,tenant_id,deal_id,mode,status,idempotency_key,
                       pipeline_contract_version,pipeline_contract_hash,
                       pipeline_resume_state,pipeline_resume_error_code
                FROM runs WHERE run_id=CAST(:run_id AS uuid)
                """
            ),
            {"run_id": run_id},
        ).one()
    )
    steps = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT step_id,tenant_id,run_id,step_name,step_order,status,
                       pipeline_contract_version,pipeline_contract_hash,
                       semantic_rule_version,semantic_rule_hash
                FROM run_steps WHERE run_id=CAST(:run_id AS uuid)
                ORDER BY step_order,step_id
                """
            ),
            {"run_id": run_id},
        ).fetchall()
    )
    return run, steps


def test_empty_upgrade_downgrade_upgrade_restores_catalog_and_is_deterministic() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            baseline_step_type = _run_step_type(connection)

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            assert _revision(connection) == "0038"
            first = _catalog_snapshot(connection)
            assert set(TASK7_TABLES) <= set(tenant_table_inventory(connection))
            assert _run_step_type(connection) == ("character varying", 100)

        downgrade(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            assert _revision(connection) == "0037"
            assert set(TASK7_TABLES).isdisjoint(tenant_table_inventory(connection))
            assert (
                _run_step_type(connection)
                == baseline_step_type
                == (
                    "character varying",
                    100,
                )
            )

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            assert _revision(connection) == "0038"
            assert _catalog_snapshot(connection) == first


def test_populated_legacy_run_upgrade_downgrade_upgrade_is_lossless() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            _tenant_id, _deal_id, run_id = _seed_legacy_run(connection)

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            first = _legacy_run_snapshot(connection, run_id)
            assert first[0][-2:] == ("NON_RESUMABLE", "LEGACY_RUN_RESUME_UNSAFE")
            original_step_id = first[1][0][0]

        downgrade(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            row = connection.execute(
                text("SELECT step_id FROM run_steps WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": run_id},
            ).one()
            assert row[0] == original_step_id

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            assert _legacy_run_snapshot(connection, run_id) == first


def test_populated_valid_source_upgrade_downgrade_upgrade_is_deterministic() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            _tenant_id, _deal_id, package_id = _seed_reconstructible_source(connection)

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            first = _source_backfill_snapshot(connection, package_id)
            assert {str(row[1]) for row in first[0]} == {
                "PACKAGE",
                "PACKAGE_FILE",
                "ARTIFACT",
                "DOCUMENT",
                "PARSE_ATTEMPT",
                "SPAN",
            }
            assert all(str(row[7]) == "BACKFILL" for row in first[0])
            assert len(first[1]) == 1
            assert first[1][0][3:] == (
                "PARSED",
                "BACKFILL",
                "BOUND",
                first[1][0][6],
                first[1][0][7],
            )
            assert all(str(row[1]) == "BOUND" and row[2] is not None for row in first[2])

        downgrade(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            package = connection.execute(
                text(
                    "SELECT status,metadata->'source_manifest'->>'schema_version' "
                    "FROM data_room_packages WHERE package_id=CAST(:package_id AS uuid)"
                ),
                {"package_id": package_id},
            ).one()
            assert tuple(package) == ("SEALED", "source-package-manifest-v1")

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            assert _source_backfill_snapshot(connection, package_id) == first


def test_any_persisted_run_output_object_blocks_downgrade_without_mutation() -> None:
    """BACKFILL roots are evidence too and may not be discarded by downgrade."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            _tenant_id, _deal_id, run_id = _seed_legacy_run(connection)

        migrate(database.admin_engine, "0038")
        output_object_id = str(uuid.uuid4())
        step_output_id = str(uuid.uuid4())
        lease_owner_id = str(uuid.uuid4())
        with (
            _disabled_output_backfill_guard(database.admin_engine),
            database.admin_engine.begin() as connection,
        ):
            run = connection.execute(
                text(
                    """
                        SELECT
                            r.tenant_id::text,r.deal_id::text,s.step_id::text,
                            s.step_name,s.semantic_rule_version,s.semantic_rule_hash,
                            r.pipeline_contract_version,r.pipeline_contract_hash
                        FROM runs r
                        JOIN run_steps s
                          ON s.tenant_id=r.tenant_id AND s.run_id=r.run_id
                        WHERE r.run_id=CAST(:run_id AS uuid)
                        ORDER BY s.step_order,s.step_id
                        LIMIT 1
                        """
                ),
                {"run_id": run_id},
            ).one()
            tenant_id, deal_id = str(run[0]), str(run[1])
            set_tenant_context(connection, tenant_id)
            connection.execute(
                text(
                    """
                        UPDATE runs
                        SET status='RUNNING',
                            lease_owner_id=CAST(:lease_owner_id AS uuid),
                            lease_fencing_token=1,
                            lease_expires_at=statement_timestamp()+interval '1 hour',
                            last_heartbeat_at=statement_timestamp()
                        WHERE run_id=CAST(:run_id AS uuid);
                        SELECT
                            set_config('idis.worker_lease_tenant_id',:tenant_id,true),
                            set_config('idis.worker_lease_deal_id',:deal_id,true),
                            set_config('idis.worker_lease_run_id',:run_id,true),
                            set_config('idis.worker_lease_owner_id',:lease_owner_id,true),
                            set_config('idis.worker_lease_fencing_token','1',true)
                        """
                ),
                {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "lease_owner_id": lease_owner_id,
                },
            )
            values = {
                "output_object_id": output_object_id,
                "step_output_id": step_output_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "step_id": str(run[2]),
                "step_name": str(run[3]),
                "semantic_rule_version": str(run[4]),
                "semantic_rule_hash": str(run[5]),
                "pipeline_contract_version": str(run[6]),
                "pipeline_contract_hash": str(run[7]),
                "lease_owner_id": lease_owner_id,
                "identity_sha256": _sha256("task7-backfill-root-identity"),
                "output_sha256": _sha256("task7-backfill-root-output"),
            }
            connection.execute(
                text(
                    """
                        INSERT INTO run_output_objects (
                            output_object_id,tenant_id,deal_id,run_id,output_class,
                            output_kind,output_schema_version,native_identity_sha256,
                            native_output_sha256,pipeline_contract_version,
                            pipeline_contract_hash,write_origin,lease_owner_id,
                            lease_fencing_token
                        ) VALUES (
                            CAST(:output_object_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                            'STEP_RECEIPT','HISTORICAL_RECEIPT',
                            'historical-receipt-v1',:identity_sha256,
                            :output_sha256,:pipeline_contract_version,
                            :pipeline_contract_hash,'BACKFILL',NULL,0
                        );
                        INSERT INTO run_step_outputs (
                            step_output_id,tenant_id,deal_id,run_id,step_id,
                            output_object_id,step_name,subrole,output_kind,
                            output_ordinal,semantic_rule_version,semantic_rule_hash,
                            summary_sha256,lease_owner_id,lease_fencing_token
                        ) VALUES (
                            CAST(:step_output_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                            CAST(:step_id AS uuid),CAST(:output_object_id AS uuid),
                            :step_name,'__step__','HISTORICAL_RECEIPT',0,
                            :semantic_rule_version,:semantic_rule_hash,
                            :output_sha256,CAST(:lease_owner_id AS uuid),1
                        )
                        """
                ),
                values,
            )
        with database.admin_engine.connect() as connection:
            states = {
                (table, trigger): state
                for table, trigger, state in _user_trigger_states(connection)
            }
            assert states[("run_output_objects", _OUTPUT_BACKFILL_GUARD_TRIGGER)] == "O"
        with database.admin_engine.connect() as connection:
            before = tuple(
                connection.execute(
                    text(
                        """
                        SELECT output_object_id,tenant_id,deal_id,run_id,output_class,
                               output_kind,output_schema_version,native_identity_sha256,
                               native_output_sha256,pipeline_contract_version,
                               pipeline_contract_hash,write_origin,lease_owner_id,
                               lease_fencing_token,created_at
                        FROM run_output_objects
                        WHERE output_object_id=CAST(:output_object_id AS uuid)
                        """
                    ),
                    {"output_object_id": output_object_id},
                ).one()
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade Task 7 while full-strict-v21 runtime evidence exists",
        ):
            downgrade(database.admin_engine, "0037")

        with database.admin_engine.connect() as connection:
            assert _revision(connection) == "0038"
            after = tuple(
                connection.execute(
                    text(
                        """
                        SELECT output_object_id,tenant_id,deal_id,run_id,output_class,
                               output_kind,output_schema_version,native_identity_sha256,
                               native_output_sha256,pipeline_contract_version,
                               pipeline_contract_hash,write_origin,lease_owner_id,
                               lease_fencing_token,created_at
                        FROM run_output_objects
                        WHERE output_object_id=CAST(:output_object_id AS uuid)
                        """
                    ),
                    {"output_object_id": output_object_id},
                ).one()
            )
            assert after == before


def test_malformed_historical_source_refuses_before_catalog_or_row_mutation() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        tenant_id, deal_id, package_id = (str(uuid.uuid4()) for _ in range(3))
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (tenant_id,name)
                    VALUES (CAST(:tenant_id AS uuid),'Task 7 refusal tenant');
                    INSERT INTO deals (deal_id,tenant_id,name,created_at)
                    VALUES (CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                            'Fictional refusal deal',statement_timestamp());
                    INSERT INTO data_room_packages (
                        package_id,tenant_id,deal_id,status,metadata
                    ) VALUES (
                        CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid),'OPEN',CAST(:metadata AS jsonb)
                    );
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "package_id": package_id,
                    "metadata": json.dumps({"not_source_manifest": True}),
                },
            )
        with database.admin_engine.connect() as connection:
            before_catalog = _catalog_snapshot(connection)
            before_row = tuple(
                connection.execute(
                    text(
                        "SELECT package_id,status,metadata FROM data_room_packages "
                        "WHERE package_id=CAST(:package_id AS uuid)"
                    ),
                    {"package_id": package_id},
                ).one()
            )

        with pytest.raises(RuntimeError, match="TASK7_SOURCE_BACKFILL_UNRECONSTRUCTIBLE"):
            migrate(database.admin_engine, "0038")

        with database.admin_engine.connect() as connection:
            assert _revision(connection) == "0037"
            assert _catalog_snapshot(connection) == before_catalog
            after_row = tuple(
                connection.execute(
                    text(
                        "SELECT package_id,status,metadata FROM data_room_packages "
                        "WHERE package_id=CAST(:package_id AS uuid)"
                    ),
                    {"package_id": package_id},
                ).one()
            )
            assert after_row == before_row


def test_post_backfill_state_columns_are_not_null_and_rls_inventory_is_64() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            nullable = tuple(
                connection.execute(
                    text(
                        """
                        SELECT table_name,is_nullable
                        FROM information_schema.columns
                        WHERE table_schema='public'
                          AND column_name='source_binding_state'
                        ORDER BY table_name
                        """
                    )
                ).fetchall()
            )
            assert nullable == tuple(
                (table, "NO")
                for table in sorted(
                    (
                        "data_room_packages",
                        "data_room_package_files",
                        "document_artifacts",
                        "document_parse_attempts",
                        "document_spans",
                        "documents",
                    )
                )
            )
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
            policies = tenant_catalog_snapshot(connection)
            task7_policies = [row for row in policies if str(row[0]) in TASK7_TABLES]
            assert len(task7_policies) == 9
            assert all(bool(row[1]) and bool(row[2]) for row in task7_policies)
            assert all(row[7] is not None and row[8] is not None for row in task7_policies)
