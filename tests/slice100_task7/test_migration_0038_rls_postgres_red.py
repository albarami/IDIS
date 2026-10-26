"""Operator-backed RED proof for the complete Task-7 64-table RLS boundary.

The test deliberately separates RLS from product immutability.  Fictional rows
are inserted by the disposable-database administrator while each table's USER
triggers are disabled by a table-local statement; the original trigger state is
restored and compared before any assertion is admitted.  Same-tenant DML that
would otherwise be intercepted by an immutable product trigger uses the same
table-local, transaction-rolled-back technique.  Cross/unset UPDATE and DELETE
run through the real application login with every product trigger enabled.

No blanket trigger-disable form or session-wide replication bypass is used.
These tests prove RLS only; the independent Task-5/6/7 guard suites prove the
product triggers themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import (
    SEED_TABLE_ORDER as REVISION_0034_SEED_ORDER,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
    tenant_catalog_snapshot,
    tenant_table_inventory,
)
from tests.slice100_task2b._support import (
    _seed_value as _task2b_seed_value,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals
from tests.slice100_task7.test_migration_0038_schema_red import (
    EXPECTED_TENANT_TABLES,
    TASK7_TABLES,
)

OWNER_TENANT_ID = "a1000000-0000-4000-8000-000000000064"
FOREIGN_TENANT_ID = "b1000000-0000-4000-8000-000000000064"
_UUID_NAMESPACE = uuid.UUID("71000000-0000-4000-8000-000000000064")
_CRUD_ORDER = ("SELECT", "INSERT", "UPDATE", "DELETE")
_CRUD = frozenset(_CRUD_ORDER)
_UNSET = object()

_TASK5_TABLES = frozenset(
    {
        "methodology_coverage_records",
        "methodology_questions",
        "methodology_versions",
        "run_methodology_selections",
    }
)
_TASK6_TABLES = frozenset(
    {
        "methodology_extraction_attempts",
        "methodology_extraction_results",
        "methodology_extraction_task_spans",
        "methodology_extraction_tasks",
        "methodology_result_claim_links",
        "methodology_result_defect_links",
        "methodology_result_evidence_links",
        "methodology_result_factual_outputs",
        "methodology_result_sanad_links",
    }
)
_POST_0034_TABLES = _TASK5_TABLES | _TASK6_TABLES | frozenset(TASK7_TABLES)
assert frozenset(EXPECTED_TENANT_TABLES) - _POST_0034_TABLES == frozenset(REVISION_0034_SEED_ORDER)

# Revision 0037 made sanad.primary_evidence_id a real FK, so the formerly
# independent evidence row must now precede its sanad lineage row.
_BASE_SEED_ORDER_LIST = list(REVISION_0034_SEED_ORDER)
_BASE_SEED_ORDER_LIST.remove("evidence_items")
_BASE_SEED_ORDER_LIST.insert(_BASE_SEED_ORDER_LIST.index("sanads"), "evidence_items")
_BASE_SEED_ORDER = tuple(_BASE_SEED_ORDER_LIST)
assert frozenset(_BASE_SEED_ORDER) == frozenset(REVISION_0034_SEED_ORDER)

_CANONICAL_RLS_EXPRESSION = (
    "((NULLIF(current_setting('idis.tenant_id'::text,true),''::text)ISNOTNULL)AND"
    "(tenant_id=(NULLIF(current_setting('idis.tenant_id'::text,true),''::text))::uuid))"
)


def _identifier(value: str) -> str:
    assert re.fullmatch(r"[a-z][a-z0-9_]*", value)
    return f'"{value}"'


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, label))


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _normalise_policy(expression: object) -> str:
    return re.sub(r"\s+", "", str(expression))


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "pgcode", None) or getattr(error.orig, "sqlstate", None)


def _assert_rls_denial(error: DBAPIError, table: str) -> None:
    assert _sqlstate(error) == "42501", table
    assert "row-level security" in str(error.orig).lower(), table


def _user_trigger_snapshot(connection: Connection) -> tuple[tuple[Any, ...], ...]:
    rows = connection.execute(
        text(
            """
            SELECT c.relname, t.tgname, t.tgenabled
            FROM pg_trigger AS t
            JOIN pg_class AS c ON c.oid=t.tgrelid
            JOIN pg_namespace AS n ON n.oid=c.relnamespace
            WHERE n.nspname='public'
              AND NOT t.tgisinternal
              AND c.relname=ANY(CAST(:tables AS text[]))
            ORDER BY c.relname,t.tgname
            """
        ),
        {"tables": list(EXPECTED_TENANT_TABLES)},
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def _set_all_user_triggers(connection: Connection, *, enabled: bool) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    for table in EXPECTED_TENANT_TABLES:
        connection.exec_driver_sql(f"ALTER TABLE {_identifier(table)} {action} TRIGGER USER")


def _base_seed_value(
    table: str,
    column: str,
    udt_name: str,
    *,
    shared_values: dict[str, str],
) -> Any:
    if column == "primary_evidence_id" and udt_name == "uuid":
        return shared_values["evidence_id"]
    if column == "pipeline_contract_version":
        return "full-strict-v4"
    if column == "pipeline_resume_state":
        return "HISTORICAL_TERMINAL"
    if column == "semantic_rule_version":
        return "task7-rls-semantic-v1"
    if column == "source_binding_state":
        return "UNBOUND"
    return _task2b_seed_value(
        table,
        column,
        udt_name,
        tenant_id=OWNER_TENANT_ID,
        shared_values=shared_values,
    )


def _cast_parameter(name: str, udt_name: str) -> str:
    casts = {
        "_text": "text[]",
        "_uuid": "uuid[]",
        "jsonb": "jsonb",
        "uuid": "uuid",
        "vector": "vector",
    }
    cast = casts.get(udt_name)
    return f"CAST(:{name} AS {cast})" if cast is not None else f":{name}"


def _seed_revision_0034_tables(connection: Connection) -> dict[str, str]:
    required = connection.execute(
        text(
            """
            SELECT table_name,column_name,udt_name
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=ANY(CAST(:tables AS text[]))
              AND (
                    is_nullable='NO'
                    OR (
                        table_name IN ('document_spans','defects')
                        AND column_name='deal_id'
                    )
                  )
              AND column_default IS NULL
              AND is_generated='NEVER'
            ORDER BY table_name,ordinal_position
            """
        ),
        {"tables": list(_BASE_SEED_ORDER)},
    ).fetchall()
    grouped: dict[str, list[tuple[str, str]]] = {table: [] for table in _BASE_SEED_ORDER}
    for table, column, udt_name in required:
        grouped[str(table)].append((str(column), str(udt_name)))

    shared_values: dict[str, str] = {"tenant_id": OWNER_TENANT_ID}
    for table in _BASE_SEED_ORDER:
        names: list[str] = []
        placeholders: list[str] = []
        params: dict[str, Any] = {}
        for index, (column, udt_name) in enumerate(grouped[table]):
            parameter = f"p{index}"
            names.append(_identifier(column))
            placeholders.append(_cast_parameter(parameter, udt_name))
            params[parameter] = (
                OWNER_TENANT_ID
                if column == "tenant_id"
                else _base_seed_value(
                    table,
                    column,
                    udt_name,
                    shared_values=shared_values,
                )
            )
        connection.execute(
            text(
                f"INSERT INTO {_identifier(table)} ({', '.join(names)}) "
                f"VALUES ({', '.join(placeholders)})"
            ),
            params,
        )
    return shared_values


def _seed_task5_tables(connection: Connection, shared: dict[str, str]) -> dict[str, str]:
    values = {
        "cdd_version": _uuid("task5-cdd-version"),
        "fdd_version": _uuid("task5-fdd-version"),
        "question": _uuid("task5-question"),
        "selection": _uuid("task5-selection"),
        "coverage": "mc_" + _hash("task5-coverage")[:24],
    }
    params = {
        **values,
        "tenant": OWNER_TENANT_ID,
        "deal": shared["deal_id"],
        "run": shared["run_id"],
        "hash_a": _hash("task5-a"),
        "hash_b": _hash("task5-b"),
    }
    connection.execute(
        text(
            """
            INSERT INTO methodology_versions (
                tenant_id,methodology_version_record_id,methodology_version_id,
                methodology_id,methodology_type,version_label,source_type,
                source_alias,source_sha256,source_byte_count,source_contract_hash,
                source_trace_digest,registry_hash,version_hash,question_count,
                source_section_count,header_row,status,imported_by_actor_id,
                import_audit_event_id
            ) VALUES
            (CAST(:tenant AS uuid),CAST(:cdd_version AS uuid),'commercial_dd:rls-v1',
             'commercial_dd','commercial_dd','rls-v1','checked_in_template',
             'fictional-cdd',:hash_a,1,:hash_a,:hash_a,:hash_a,:hash_a,21,1,
             NULL,'IMPORTED','task7-rls-seeder',gen_random_uuid()),
            (CAST(:tenant AS uuid),CAST(:fdd_version AS uuid),'financial_dd:rls-v1',
             'financial_dd','financial_dd','rls-v1','operator_workbook',
             'fictional-fdd',:hash_b,1,:hash_b,:hash_b,:hash_b,:hash_b,79,1,
             2,'IMPORTED','task7-rls-seeder',gen_random_uuid());

            INSERT INTO methodology_questions (
                tenant_id,methodology_question_record_id,
                methodology_version_record_id,methodology_version_id,
                methodology_question_id,methodology_id,methodology_type,
                question_ordinal,section,source_sheet_or_section,source_sha256,
                semantic_row_hash,question_payload_hash,question_payload
            ) VALUES (
                CAST(:tenant AS uuid),CAST(:question AS uuid),
                CAST(:cdd_version AS uuid),'commercial_dd:rls-v1',
                'mq_commercial_dd_rls_0001','commercial_dd','commercial_dd',1,
                'Fictional','Fictional',:hash_a,:hash_a,:hash_b,
                '{"question":"fictional"}'::jsonb
            );

            INSERT INTO run_methodology_selections (
                selection_id,tenant_id,deal_id,run_id,
                commercial_dd_version_record_id,commercial_methodology_version_id,
                commercial_version_hash,financial_dd_version_record_id,
                financial_methodology_version_id,financial_version_hash,
                selection_hash,selected_question_count,created_by_actor_id,
                selection_audit_event_id
            ) VALUES (
                CAST(:selection AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:cdd_version AS uuid),
                'commercial_dd:rls-v1',:hash_a,CAST(:fdd_version AS uuid),
                'financial_dd:rls-v1',:hash_b,:hash_a,100,
                'task7-rls-seeder',gen_random_uuid()
            );

            INSERT INTO methodology_coverage_records (
                coverage_record_id,tenant_id,deal_id,run_id,selection_id,
                methodology_version_record_id,methodology_version_id,
                methodology_question_record_id,methodology_question_id,
                methodology_type,status
            ) VALUES (
                :coverage,CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:selection AS uuid),
                CAST(:cdd_version AS uuid),'commercial_dd:rls-v1',
                CAST(:question AS uuid),'mq_commercial_dd_rls_0001',
                'commercial_dd','not_started'
            )
            """
        ),
        params,
    )
    return values


def _seed_task6_tables(
    connection: Connection,
    shared: dict[str, str],
    task5: dict[str, str],
) -> dict[str, str]:
    ids = {
        "task": _uuid("task6-task"),
        "task_span": _uuid("task6-task-span"),
        "attempt": _uuid("task6-attempt"),
        "result": _uuid("task6-result"),
        "factual": _uuid("task6-factual"),
        "factual_id": _uuid("task6-factual-id"),
        "claim_link": _uuid("task6-claim-link"),
        "evidence_link": _uuid("task6-evidence-link"),
        "sanad_link": _uuid("task6-sanad-link"),
        "defect_link": _uuid("task6-defect-link"),
    }
    params = {
        **ids,
        "tenant": OWNER_TENANT_ID,
        "deal": shared["deal_id"],
        "run": shared["run_id"],
        "selection": task5["selection"],
        "coverage": task5["coverage"],
        "version": task5["cdd_version"],
        "question": task5["question"],
        "document": shared["document_id"],
        "span": shared["span_id"],
        "step": shared["step_id"],
        "claim": shared["claim_id"],
        "evidence": shared["evidence_id"],
        "sanad": shared["sanad_id"],
        "defect": shared["defect_id"],
        "lease_owner": _uuid("task6-lease-owner"),
        "task_payload": json.dumps({"fixture": True}),
        "hash_a": _hash("task6-a"),
        "hash_b": _hash("task6-b"),
    }
    connection.execute(
        text(
            """
            INSERT INTO methodology_extraction_tasks (
                task_record_id,tenant_id,deal_id,run_id,selection_id,
                coverage_record_id,methodology_version_record_id,
                methodology_version_id,methodology_question_record_id,
                methodology_question_id,methodology_type,extraction_task_id,
                document_id,document_classification_id,
                document_classification_sha256,planner_contract_version,
                task_contract_version,task_payload,task_input_sha256,
                candidate_span_set_sha256,status
            ) VALUES (
                CAST(:task AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:selection AS uuid),:coverage,
                CAST(:version AS uuid),'commercial_dd:rls-v1',
                CAST(:question AS uuid),'mq_commercial_dd_rls_0001',
                'commercial_dd','et_task7_rls_fixture',CAST(:document AS uuid),
                'dc_task7_rls_fixture',:hash_a,'selected-pair-planner-v1',
                'methodology-extraction-task-v1',CAST(:task_payload AS jsonb),
                :hash_a,:hash_b,'READY'
            );

            INSERT INTO methodology_extraction_task_spans (
                task_span_record_id,tenant_id,deal_id,run_id,task_record_id,
                document_id,span_id,span_ordinal,content_sha256,locator_sha256
            ) VALUES (
                CAST(:task_span AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:task AS uuid),CAST(:document AS uuid),
                CAST(:span AS uuid),1,:hash_a,:hash_b
            );

            INSERT INTO methodology_extraction_attempts (
                attempt_id,tenant_id,deal_id,run_id,run_step_id,task_record_id,
                task_input_sha256,lease_owner_id,lease_fencing_token,status,
                provider,backend,transport_kind,configured_model,prompt_id,
                prompt_version,prompt_content_sha256,prompt_input_schema_sha256,
                prompt_output_schema_sha256,request_payload_sha256,
                prepared_call_sha256,billable_payload_sha256,
                billable_payload_utf8_bytes,real_call,configured_max_tokens,
                configured_temperature,configured_json_mode,budget_policy_id,
                budget_component,budget_reservation_id,price_catalog_version,
                price_catalog_row_sha256,input_usd_per_million_tokens,
                output_usd_per_million_tokens,reserved_calls,
                reserved_input_tokens,reserved_output_tokens,reserved_usd,
                budget_terminal_state
            ) VALUES (
                CAST(:attempt AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:step AS uuid),CAST(:task AS uuid),
                :hash_a,CAST(:lease_owner AS uuid),1,'STARTED','anthropic',
                'hermetic','HERMETIC_TEST','fixture-model','TASK7_RLS_FIXTURE',
                '1.0.0',:hash_a,:hash_a,:hash_a,:hash_a,:hash_b,:hash_a,1,
                false,1,0,true,'fixture-policy','anthropic',gen_random_uuid(),
                'fixture-price-v1',:hash_a,0,0,0,0,0,0,'NOT_BILLABLE'
            );

            INSERT INTO methodology_extraction_results (
                result_id,tenant_id,deal_id,run_id,task_record_id,attempt_id,
                task_input_sha256,outcome,result_payload,result_sha256,
                searched_document_ids,searched_span_ids,searched_scope_sha256,
                provider_provenance_sha256
            ) VALUES (
                CAST(:result AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:task AS uuid),CAST(:attempt AS uuid),
                :hash_a,'EVIDENCE_FOUND','{"outcome":"EVIDENCE_FOUND"}'::jsonb,
                :hash_a,ARRAY[CAST(:document AS uuid)],ARRAY[CAST(:span AS uuid)],
                :hash_a,:hash_b
            );

            INSERT INTO methodology_result_factual_outputs (
                factual_output_record_id,tenant_id,deal_id,run_id,result_id,
                task_record_id,output_ordinal,factual_output_id,
                typed_output_payload,typed_output_sha256,
                accepted_source_span_ids,accepted_locator_sha256s,
                accepted_locator_set_sha256
            ) VALUES (
                CAST(:factual AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:result AS uuid),CAST(:task AS uuid),1,
                CAST(:factual_id AS uuid),'{"fact":"fictional"}'::jsonb,
                :hash_a,ARRAY[CAST(:span AS uuid)],ARRAY[:hash_b],:hash_b
            );

            INSERT INTO methodology_result_claim_links (
                claim_link_id,tenant_id,deal_id,run_id,result_id,
                factual_output_record_id,selection_id,coverage_record_id,
                methodology_version_record_id,methodology_question_record_id,
                claim_id,canonical_key_sha256,reused_existing_claim
            ) VALUES (
                CAST(:claim_link AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:result AS uuid),CAST(:factual AS uuid),
                CAST(:selection AS uuid),:coverage,CAST(:version AS uuid),
                CAST(:question AS uuid),CAST(:claim AS uuid),:hash_a,false
            );

            INSERT INTO methodology_result_evidence_links (
                evidence_link_id,tenant_id,deal_id,run_id,result_id,
                factual_output_record_id,claim_link_id,evidence_id,document_id,
                source_span_id,locator_sha256
            ) VALUES (
                CAST(:evidence_link AS uuid),CAST(:tenant AS uuid),
                CAST(:deal AS uuid),CAST(:run AS uuid),CAST(:result AS uuid),
                CAST(:factual AS uuid),CAST(:claim_link AS uuid),
                CAST(:evidence AS uuid),CAST(:document AS uuid),
                CAST(:span AS uuid),:hash_b
            );

            INSERT INTO methodology_result_sanad_links (
                sanad_link_id,tenant_id,deal_id,run_id,result_id,
                factual_output_record_id,claim_link_id,sanad_id,primary_evidence_id
            ) VALUES (
                CAST(:sanad_link AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:result AS uuid),CAST(:factual AS uuid),
                CAST(:claim_link AS uuid),CAST(:sanad AS uuid),
                CAST(:evidence AS uuid)
            );

            INSERT INTO methodology_result_defect_links (
                defect_link_id,tenant_id,deal_id,run_id,result_id,
                factual_output_record_id,sanad_link_id,defect_id
            ) VALUES (
                CAST(:defect_link AS uuid),CAST(:tenant AS uuid),
                CAST(:deal AS uuid),CAST(:run AS uuid),CAST(:result AS uuid),
                CAST(:factual AS uuid),CAST(:sanad_link AS uuid),
                CAST(:defect AS uuid)
            )
            """
        ),
        params,
    )
    return ids


def _seed_task7_tables(connection: Connection, shared: dict[str, str]) -> None:
    step_name = str(
        connection.execute(
            text("SELECT step_name FROM run_steps WHERE step_id=CAST(:step AS uuid)"),
            {"step": shared["step_id"]},
        ).scalar_one()
    )
    ids = {
        "source_root": _uuid("task7-source-root"),
        "parse_attempt": _uuid("task7-parse-attempt"),
        "binding": _uuid("task7-binding"),
        "member_root": _uuid("task7-member-root"),
        "manifest_root": _uuid("task7-manifest-root"),
        "reference_root": _uuid("task7-reference-root"),
        "step_output": _uuid("task7-step-output"),
        "decision": _uuid("task7-decision"),
        "edge": _uuid("task7-edge"),
        "receipt": _uuid("task7-receipt"),
        "manifest_deliverable": _uuid("task7-manifest-deliverable"),
        "member_deliverable": _uuid("task7-member-deliverable"),
        "manifest_reference": _uuid("task7-manifest-reference"),
        "lease_owner": _uuid("task7-lease-owner"),
    }
    params = {
        **ids,
        "tenant": OWNER_TENANT_ID,
        "deal": shared["deal_id"],
        "run": shared["run_id"],
        "step": shared["step_id"],
        "step_name": step_name,
        "package": shared["package_id"],
        "document": shared["document_id"],
        "hash_source_identity": _hash("task7-source-identity"),
        "hash_source_content": _hash("task7-source-content"),
        "hash_corpus": _hash("task7-corpus"),
        "hash_contract": _hash("task7-contract"),
        "hash_rule": _hash("task7-rule"),
        "hash_member_identity": _hash("task7-member-identity"),
        "hash_member_value": _hash("task7-member-value"),
        "hash_member_content": _hash("task7-member-content"),
        "hash_manifest_identity": _hash("task7-manifest-identity"),
        "hash_manifest_value": _hash("task7-manifest-value"),
        "hash_reference_identity": _hash("task7-reference-identity"),
        "hash_reference_value": _hash("task7-reference-value"),
        "hash_reference": _hash("task7-reference"),
    }
    connection.execute(
        text(
            """
            INSERT INTO source_lineage_objects (
                source_lineage_object_id,tenant_id,deal_id,corpus_receipt_id,
                object_kind,source_version,source_disposition,
                object_identity_sha256,content_sha256,corpus_receipt_sha256,
                write_origin
            ) VALUES (
                CAST(:source_root AS uuid),CAST(:tenant AS uuid),
                CAST(:deal AS uuid),CAST(:package AS uuid),'PACKAGE',
                'task7-rls-source-v1','CORPUS',:hash_source_identity,
                :hash_source_content,:hash_corpus,'RUNTIME'
            );

            INSERT INTO document_parse_attempts (
                parse_attempt_id,tenant_id,deal_id,document_id,attempt_ordinal,
                parser_name,parser_version,input_sha256,receipt_sha256,status,
                started_at,finished_at,source_binding_state,write_origin
            ) VALUES (
                CAST(:parse_attempt AS uuid),CAST(:tenant AS uuid),
                CAST(:deal AS uuid),CAST(:document AS uuid),1,
                'task7-rls-parser','v1',:hash_source_content,:hash_corpus,'PARSED',
                now(),now(),'UNBOUND','RUNTIME'
            );

            INSERT INTO run_input_bindings (
                binding_id,tenant_id,deal_id,run_id,source_lineage_object_id,
                source_object_kind,source_disposition,binding_purpose,
                corpus_receipt_id,selected_source_version,
                selected_identity_sha256,selected_content_sha256,
                corpus_receipt_sha256,pipeline_contract_version,
                pipeline_contract_hash,binding_sha256
            ) VALUES (
                CAST(:binding AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:source_root AS uuid),'PACKAGE','CORPUS',
                'INVENTORY',CAST(:package AS uuid),'task7-rls-source-v1',
                :hash_source_identity,:hash_source_content,:hash_corpus,
                'full-strict-v4',:hash_contract,:hash_reference
            );

            INSERT INTO run_output_objects (
                output_object_id,tenant_id,deal_id,run_id,output_class,
                output_kind,output_schema_version,native_identity_sha256,
                native_output_sha256,pipeline_contract_version,
                pipeline_contract_hash,write_origin,lease_owner_id,
                lease_fencing_token
            ) VALUES
            (CAST(:member_root AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
             CAST(:run AS uuid),'NATIVE_ROW','DELIVERABLE_ARTIFACT','v1',
             :hash_member_identity,:hash_member_value,'full-strict-v4',
             :hash_contract,'RUNTIME',CAST(:lease_owner AS uuid),1),
            (CAST(:manifest_root AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
             CAST(:run AS uuid),'NATIVE_ROW','DELIVERABLE_ARTIFACT','v1',
             :hash_manifest_identity,:hash_manifest_value,'full-strict-v4',
             :hash_contract,'RUNTIME',CAST(:lease_owner AS uuid),1),
            (CAST(:reference_root AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
             CAST(:run AS uuid),'NATIVE_ROW','DELIVERABLE_MANIFEST_REFERENCE','v1',
             :hash_reference_identity,:hash_reference_value,'full-strict-v4',
             :hash_contract,'RUNTIME',CAST(:lease_owner AS uuid),1);

            INSERT INTO run_step_outputs (
                step_output_id,tenant_id,deal_id,run_id,step_id,output_object_id,
                step_name,subrole,output_kind,output_ordinal,
                semantic_rule_version,semantic_rule_hash,summary_sha256,
                lease_owner_id,lease_fencing_token
            ) VALUES (
                CAST(:step_output AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:step AS uuid),CAST(:member_root AS uuid),
                :step_name,'task7_rls_consumer','DELIVERABLE_ARTIFACT',0,
                'task7-rls-semantic-v1',:hash_rule,:hash_member_value,
                CAST(:lease_owner AS uuid),1
            );

            INSERT INTO run_applicability_decisions (
                applicability_decision_id,tenant_id,deal_id,run_id,
                decision_phase,decision_kind,subject_kind,subject_key,outcome,
                upstream_output_object_id,deciding_step_output_id,
                contract_rule_id,contract_rule_version,contract_rule_hash,
                evidence_sha256,decision_sha256,lease_owner_id,
                lease_fencing_token
            ) VALUES (
                CAST(:decision AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),'STEP_5','TASK7_RLS','OUTPUT','fixture',
                'APPLICABLE',CAST(:member_root AS uuid),CAST(:step_output AS uuid),
                'TASK7_RLS_RULE','v1',:hash_rule,:hash_member_value,
                :hash_reference,CAST(:lease_owner AS uuid),1
            );

            INSERT INTO run_expected_consumer_edges (
                expected_edge_id,tenant_id,deal_id,run_id,
                producer_output_object_id,consumer_step_name,consumer_subrole,
                usage_kind,applicability_decision_id,contract_rule_id,
                contract_rule_version,contract_rule_hash,edge_tuple_sha256,
                materialization_origin,lease_owner_id,lease_fencing_token
            ) VALUES (
                CAST(:edge AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:member_root AS uuid),:step_name,
                'task7_rls_consumer','TASK7_RLS_USAGE',CAST(:decision AS uuid),
                'TASK7_RLS_RULE','v1',:hash_rule,:hash_reference,'WORKER',
                CAST(:lease_owner AS uuid),1
            );

            INSERT INTO run_consumer_receipts (
                receipt_id,tenant_id,deal_id,run_id,expected_edge_id,
                consumer_step_output_id,consumer_output_object_id,
                consumer_step_name,consumer_subrole,usage_evidence_sha256,
                consumed_at,lease_owner_id,lease_fencing_token
            ) VALUES (
                CAST(:receipt AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:edge AS uuid),CAST(:step_output AS uuid),
                CAST(:member_root AS uuid),:step_name,'task7_rls_consumer',
                :hash_reference,now(),CAST(:lease_owner AS uuid),1
            );

            INSERT INTO deliverables (
                deliverable_id,tenant_id,deal_id,run_id,deliverable_type,format,
                status,uri,run_output_object_id,content_type,size_bytes,
                content_sha256,uri_sha256,object_version_hash,
                download_proof_hash,catalog_ordinal
            ) VALUES
            (CAST(:member_deliverable AS uuid),CAST(:tenant AS uuid),
             CAST(:deal AS uuid),CAST(:run AS uuid),'task7_rls_member','JSON',
             'COMPLETED','object://fictional/member',CAST(:member_root AS uuid),
             'application/json',1,:hash_member_content,:hash_member_identity,
             :hash_member_value,:hash_reference,0),
            (CAST(:manifest_deliverable AS uuid),CAST(:tenant AS uuid),
             CAST(:deal AS uuid),CAST(:run AS uuid),'task7_rls_manifest','JSON',
             'COMPLETED','object://fictional/manifest',CAST(:manifest_root AS uuid),
             'application/json',1,:hash_manifest_value,:hash_manifest_identity,
             :hash_reference_identity,:hash_reference_value,14);

            INSERT INTO deliverable_manifest_references (
                manifest_reference_id,tenant_id,deal_id,run_id,
                manifest_deliverable_id,manifest_output_object_id,
                run_output_object_id,member_kind,catalog_ordinal,
                member_step_output_id,member_output_object_id,
                member_identity_sha256,member_value_sha256,
                member_content_sha256,reference_sha256,lease_owner_id,
                lease_fencing_token
            ) VALUES (
                CAST(:manifest_reference AS uuid),CAST(:tenant AS uuid),
                CAST(:deal AS uuid),CAST(:run AS uuid),
                CAST(:manifest_deliverable AS uuid),CAST(:manifest_root AS uuid),
                CAST(:reference_root AS uuid),'ARTIFACT',0,
                CAST(:step_output AS uuid),CAST(:member_root AS uuid),
                :hash_member_identity,:hash_member_value,:hash_member_content,
                :hash_reference,CAST(:lease_owner AS uuid),1
            )
            """
        ),
        params,
    )


def _insertable_columns(connection: Connection) -> dict[str, tuple[str, ...]]:
    rows = connection.execute(
        text(
            """
            SELECT table_name,column_name
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=ANY(CAST(:tables AS text[]))
              AND is_generated='NEVER'
              AND is_identity='NO'
            ORDER BY table_name,ordinal_position
            """
        ),
        {"tables": list(EXPECTED_TENANT_TABLES)},
    ).fetchall()
    result: dict[str, list[str]] = {table: [] for table in EXPECTED_TENANT_TABLES}
    for table, column in rows:
        result[str(table)].append(str(column))
    return {table: tuple(columns) for table, columns in result.items()}


def _row_identity_keys(connection: Connection) -> dict[str, tuple[str, ...]]:
    rows = connection.execute(
        text(
            """
            WITH selected_constraint AS (
                SELECT DISTINCT ON (table_class.relname)
                       table_class.relname,table_class.oid AS table_oid,
                       constraint_row.conkey
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS table_class
                  ON table_class.oid=constraint_row.conrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid=table_class.relnamespace
                WHERE namespace.nspname='public'
                  AND constraint_row.contype IN ('p','u')
                  AND table_class.relname=ANY(CAST(:tables AS text[]))
                ORDER BY table_class.relname,
                         CASE constraint_row.contype WHEN 'p' THEN 0 ELSE 1 END,
                         constraint_row.conname
            )
            SELECT selected_constraint.relname,attribute.attname
            FROM selected_constraint
            CROSS JOIN LATERAL unnest(selected_constraint.conkey)
                WITH ORDINALITY AS key_column(attnum,ordinality)
            JOIN pg_attribute AS attribute
              ON attribute.attrelid=selected_constraint.table_oid
             AND attribute.attnum=key_column.attnum
            ORDER BY selected_constraint.relname,key_column.ordinality
            """
        ),
        {"tables": list(EXPECTED_TENANT_TABLES)},
    ).fetchall()
    result: dict[str, list[str]] = {table: [] for table in EXPECTED_TENANT_TABLES}
    for table, column in rows:
        result[str(table)].append(str(column))
    fallback_columns = _insertable_columns(connection)
    row_identity_keys = {
        table: tuple(columns) if columns else fallback_columns[table]
        for table, columns in result.items()
    }
    assert all(row_identity_keys.values()), row_identity_keys
    return row_identity_keys


def _delete_path_fk_snapshot(
    connection: Connection,
    *,
    table: str,
) -> tuple[tuple[Any, ...], ...]:
    rows = connection.execute(
        text(
            """
            WITH RECURSIVE affected_table(table_oid) AS (
                SELECT parent.oid
                FROM pg_class AS parent
                JOIN pg_namespace AS parent_namespace
                  ON parent_namespace.oid=parent.relnamespace
                WHERE parent_namespace.nspname='public'
                  AND parent.relname=:table
                UNION
                SELECT cascade_constraint.conrelid
                FROM pg_constraint AS cascade_constraint
                JOIN affected_table
                  ON affected_table.table_oid=cascade_constraint.confrelid
                JOIN pg_class AS cascade_child
                  ON cascade_child.oid=cascade_constraint.conrelid
                JOIN pg_namespace AS cascade_namespace
                  ON cascade_namespace.oid=cascade_child.relnamespace
                WHERE cascade_constraint.contype='f'
                  AND cascade_constraint.confdeltype='c'
                  AND cascade_namespace.nspname='public'
            )
            SELECT child.relname,constraint_row.conname,
                   constraint_row.condeferrable,constraint_row.condeferred,
                   pg_get_constraintdef(constraint_row.oid,true)
            FROM pg_constraint AS constraint_row
            JOIN affected_table
              ON affected_table.table_oid=constraint_row.confrelid
            JOIN pg_class AS child ON child.oid=constraint_row.conrelid
            JOIN pg_namespace AS child_namespace
              ON child_namespace.oid=child.relnamespace
            WHERE constraint_row.contype='f'
              AND child_namespace.nspname='public'
            ORDER BY child.relname,constraint_row.conname
            """
        ),
        {"table": table},
    ).fetchall()
    return tuple(tuple(row) for row in rows)


@dataclass(frozen=True)
class _RlsDatabase:
    database: DisposablePostgres
    payloads: dict[str, dict[str, Any]]
    insertable_columns: dict[str, tuple[str, ...]]
    row_identity_keys: dict[str, tuple[str, ...]]
    expected_policy_names: dict[str, str]


@contextmanager
def _task7_rls_database() -> Iterator[_RlsDatabase]:
    with disposable_postgres() as database:
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
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            expected_policy_names = {
                str(row[0]): str(row[3]) for row in tenant_catalog_snapshot(connection)
            }
        expected_policy_names.update({table: f"{table}_tenant_isolation" for table in TASK7_TABLES})
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with database.admin_engine.begin() as connection:
            before_triggers = _user_trigger_snapshot(connection)
            assert all(str(row[2]) == "O" for row in before_triggers)
            _set_all_user_triggers(connection, enabled=False)
        # PostgreSQL forbids ALTER TRIGGER while the seed transaction has pending events.
        with database.admin_engine.begin() as connection:
            shared = _seed_revision_0034_tables(connection)
            task5 = _seed_task5_tables(connection, shared)
            _seed_task6_tables(connection, shared, task5)
            _seed_task7_tables(connection, shared)
        with database.admin_engine.begin() as connection:
            _set_all_user_triggers(connection, enabled=True)
            assert _user_trigger_snapshot(connection) == before_triggers

        with database.admin_engine.connect() as connection:
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
            insertable = _insertable_columns(connection)
            row_identity_keys = _row_identity_keys(connection)
            payloads: dict[str, dict[str, Any]] = {}
            for table in EXPECTED_TENANT_TABLES:
                row = connection.execute(
                    text(
                        f"SELECT to_jsonb(t) FROM {_identifier(table)} AS t "
                        "WHERE tenant_id=CAST(:tenant AS uuid) LIMIT 1"
                    ),
                    {"tenant": OWNER_TENANT_ID},
                ).scalar_one()
                assert isinstance(row, dict), table
                payloads[table] = {column: row[column] for column in insertable[table]}

        yield _RlsDatabase(
            database=database,
            payloads=payloads,
            insertable_columns=insertable,
            row_identity_keys=row_identity_keys,
            expected_policy_names=expected_policy_names,
        )


@contextmanager
def _table_local_app_role_connection(
    fixture: _RlsDatabase,
    *,
    table: str,
    tenant_id: str | object,
) -> Iterator[Connection]:
    database = fixture.database
    with database.admin_engine.connect() as connection:
        transaction = connection.begin()
        before = _user_trigger_snapshot(connection)
        try:
            connection.exec_driver_sql(f"ALTER TABLE {_identifier(table)} DISABLE TRIGGER USER")
            disabled = _user_trigger_snapshot(connection)
            assert disabled == tuple(
                (row_table, trigger_name, "D" if row_table == table else enabled)
                for row_table, trigger_name, enabled in before
            )
            connection.exec_driver_sql(f"SET LOCAL ROLE {_identifier(database.role_name)}")
            identity = connection.execute(text("SELECT session_user,current_user")).one()
            assert str(identity.current_user) == database.role_name
            assert str(identity.session_user) != database.role_name
            if tenant_id is _UNSET:
                connection.exec_driver_sql("RESET idis.tenant_id")
                configured = connection.execute(
                    text("SELECT current_setting('idis.tenant_id', true)")
                ).scalar_one_or_none()
                assert configured in (None, "")
            else:
                set_tenant_context(connection, str(tenant_id))
            yield connection
        finally:
            transaction.rollback()
    with database.admin_engine.connect() as verification:
        assert _user_trigger_snapshot(verification) == before


@contextmanager
def _genuine_insert_app_role_connection(
    fixture: _RlsDatabase,
    *,
    table: str,
) -> Iterator[Connection]:
    """Expose a genuinely empty row slot without weakening persistent schema state."""
    database = fixture.database
    with database.admin_engine.connect() as connection:
        transaction = connection.begin()
        before_triggers = _user_trigger_snapshot(connection)
        before_foreign_keys = _delete_path_fk_snapshot(connection, table=table)
        try:
            for child_table, constraint_name, *_ in before_foreign_keys:
                connection.exec_driver_sql(
                    f"ALTER TABLE {_identifier(str(child_table))} "
                    f"ALTER CONSTRAINT {_identifier(str(constraint_name))} "
                    "DEFERRABLE INITIALLY DEFERRED"
                )
            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            connection.exec_driver_sql(f"ALTER TABLE {_identifier(table)} DISABLE TRIGGER USER")
            disabled = _user_trigger_snapshot(connection)
            assert disabled == tuple(
                (row_table, trigger_name, "D" if row_table == table else enabled)
                for row_table, trigger_name, enabled in before_triggers
            )
            connection.exec_driver_sql(f"SET LOCAL ROLE {_identifier(database.role_name)}")
            identity = connection.execute(text("SELECT session_user,current_user")).one()
            assert str(identity.current_user) == database.role_name
            assert str(identity.session_user) != database.role_name
            set_tenant_context(connection, OWNER_TENANT_ID)
            yield connection
        finally:
            transaction.rollback()
    with database.admin_engine.connect() as verification:
        assert _user_trigger_snapshot(verification) == before_triggers
        assert _delete_path_fk_snapshot(verification, table=table) == before_foreign_keys


@contextmanager
def _app_login_connection(
    fixture: _RlsDatabase,
    *,
    tenant_id: str | object,
) -> Iterator[Connection]:
    database = fixture.database
    if tenant_id is _UNSET:
        # A new physical login proves a genuinely absent custom GUC, not merely
        # an empty value inherited from a pooled prior transaction.
        database.app_engine.dispose()
    with database.app_engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert connection.execute(text("SELECT current_user")).scalar_one() == (
                database.role_name
            )
            if tenant_id is _UNSET:
                assert (
                    connection.execute(
                        text("SELECT current_setting('idis.tenant_id', true)")
                    ).scalar_one_or_none()
                    is None
                )
            else:
                set_tenant_context(connection, str(tenant_id))
            yield connection
        finally:
            transaction.rollback()


def _clone_insert_sql(table: str, columns: tuple[str, ...]) -> str:
    quoted = ",".join(_identifier(column) for column in columns)
    selected = ",".join(f"src.{_identifier(column)}" for column in columns)
    return (
        f"INSERT INTO {_identifier(table)} ({quoted}) SELECT {selected} "
        f"FROM jsonb_populate_record(NULL::{_identifier(table)}, "
        "CAST(:payload AS jsonb)) AS src"
    )


def _delete_seed_sql(table: str, row_identity_key: tuple[str, ...]) -> str:
    comparisons = " AND ".join(
        f"target.{_identifier(column)} IS NOT DISTINCT FROM src.{_identifier(column)}"
        for column in row_identity_key
    )
    return (
        f"DELETE FROM {_identifier(table)} AS target USING "
        f"jsonb_populate_record(NULL::{_identifier(table)}, "
        f"CAST(:payload AS jsonb)) AS src WHERE {comparisons}"
    )


def test_0038_exact_64_table_catalog_role_ownership_and_grants() -> None:
    with _task7_rls_database() as fixture:
        database = fixture.database
        with database.admin_engine.connect() as connection:
            assert len(EXPECTED_TENANT_TABLES) == 64
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
            snapshot = tenant_catalog_snapshot(connection)
            assert len(snapshot) == 64

            for row in snapshot:
                table, enabled, forced, policy, permissive, command, roles, using, check = row
                assert table in EXPECTED_TENANT_TABLES
                assert enabled is True, table
                assert forced is True, table
                assert policy == fixture.expected_policy_names[table], table
                assert permissive is True, table
                assert command == "*", table
                assert roles == "{0}", table
                assert using is not None and check is not None, table
                assert _normalise_policy(using) == _CANONICAL_RLS_EXPRESSION, table
                assert _normalise_policy(check) == _CANONICAL_RLS_EXPRESSION, table

            role = connection.execute(
                text(
                    """
                    SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,
                           EXISTS (
                               SELECT 1 FROM pg_auth_members
                               WHERE member=pg_roles.oid
                           )
                    FROM pg_roles WHERE rolname=:role
                    """
                ),
                {"role": database.role_name},
            ).one()
            assert tuple(bool(value) for value in role) == (
                False,
                False,
                False,
                False,
                False,
            )

            ownership = connection.execute(
                text(
                    """
                    SELECT schema_owner.rolname,database_owner.rolname
                    FROM pg_namespace AS namespace
                    JOIN pg_roles AS schema_owner ON schema_owner.oid=namespace.nspowner
                    JOIN pg_database AS database_row
                      ON database_row.datname=current_database()
                    JOIN pg_roles AS database_owner
                      ON database_owner.oid=database_row.datdba
                    WHERE namespace.nspname='public'
                    """
                )
            ).one()
            assert database.role_name not in {
                str(ownership[0]),
                str(ownership[1]),
            }

            effective_namespace_access = connection.execute(
                text(
                    """
                    SELECT has_schema_privilege(
                               CAST(:role AS name),'public','USAGE'
                           ),
                           has_schema_privilege(
                               CAST(:role AS name),'public','CREATE'
                           ),
                           has_database_privilege(
                               CAST(:role AS name),current_database(),'CREATE'
                           )
                    """
                ),
                {"role": database.role_name},
            ).one()
            assert tuple(bool(value) for value in effective_namespace_access) == (
                True,
                False,
                False,
            )

            direct_schema_acl = connection.execute(
                text(
                    """
                    SELECT acl.privilege_type
                    FROM pg_namespace AS namespace
                    CROSS JOIN LATERAL aclexplode(COALESCE(
                        namespace.nspacl,
                        acldefault('n',namespace.nspowner)
                    )) AS acl
                    JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
                    WHERE namespace.nspname='public'
                      AND grantee.rolname=:role
                    ORDER BY acl.privilege_type
                    """
                ),
                {"role": database.role_name},
            ).scalars()
            assert tuple(str(privilege) for privilege in direct_schema_acl) == ("USAGE",)

            posture = connection.execute(
                text(
                    """
                    SELECT c.relname,
                           owner.rolname,
                           has_table_privilege(CAST(:role AS name),c.oid,'SELECT'),
                           has_table_privilege(CAST(:role AS name),c.oid,'INSERT'),
                           has_table_privilege(CAST(:role AS name),c.oid,'UPDATE'),
                           has_table_privilege(CAST(:role AS name),c.oid,'DELETE'),
                           has_table_privilege(CAST(:role AS name),c.oid,'TRUNCATE'),
                           has_table_privilege(CAST(:role AS name),c.oid,'REFERENCES'),
                           has_table_privilege(CAST(:role AS name),c.oid,'TRIGGER')
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid=c.relnamespace
                    JOIN pg_roles AS owner ON owner.oid=c.relowner
                    WHERE n.nspname='public'
                      AND c.relname=ANY(CAST(:tables AS text[]))
                    ORDER BY c.relname
                    """
                ),
                {
                    "role": database.role_name,
                    "tables": list(EXPECTED_TENANT_TABLES),
                },
            ).fetchall()
            assert tuple(str(row[0]) for row in posture) == EXPECTED_TENANT_TABLES
            for row in posture:
                table, owner, select, insert, update, delete, truncate, references, trigger = row
                assert owner != database.role_name, table
                assert {
                    name for name, granted in zip(_CRUD_ORDER, row[2:6], strict=True) if granted
                } == _CRUD
                assert (truncate, references, trigger) == (False, False, False), table

            direct_table_acl_rows = connection.execute(
                text(
                    """
                    SELECT c.relname,acl.privilege_type
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid=c.relnamespace
                    CROSS JOIN LATERAL aclexplode(COALESCE(
                        c.relacl,
                        acldefault('r',c.relowner)
                    )) AS acl
                    JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
                    WHERE n.nspname='public'
                      AND c.relname=ANY(CAST(:tables AS text[]))
                      AND grantee.rolname=:role
                    ORDER BY c.relname,acl.privilege_type
                    """
                ),
                {
                    "role": database.role_name,
                    "tables": list(EXPECTED_TENANT_TABLES),
                },
            ).fetchall()
            direct_table_acl: dict[str, set[str]] = {
                table: set() for table in EXPECTED_TENANT_TABLES
            }
            for table, privilege in direct_table_acl_rows:
                direct_table_acl[str(table)].add(str(privilege))
            assert all(privileges == _CRUD for privileges in direct_table_acl.values()), (
                direct_table_acl
            )

            public_acl = connection.execute(
                text(
                    """
                    SELECT c.relname,a.privilege_type
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid=c.relnamespace
                    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,
                        acldefault('r',c.relowner))) AS a
                    WHERE n.nspname='public'
                      AND c.relname=ANY(CAST(:tables AS text[]))
                      AND a.grantee=0
                    ORDER BY c.relname,a.privilege_type
                    """
                ),
                {"tables": list(EXPECTED_TENANT_TABLES)},
            ).fetchall()
            assert public_acl == []


def test_0038_every_table_select_is_same_tenant_visible_and_cross_unset_hidden() -> None:
    with _task7_rls_database() as fixture:
        for table in EXPECTED_TENANT_TABLES:
            with _app_login_connection(fixture, tenant_id=OWNER_TENANT_ID) as connection:
                assert (
                    connection.execute(
                        text(f"SELECT count(*) FROM {_identifier(table)}")
                    ).scalar_one()
                    > 0
                ), table
            with _app_login_connection(fixture, tenant_id=FOREIGN_TENANT_ID) as connection:
                assert (
                    connection.execute(
                        text(f"SELECT count(*) FROM {_identifier(table)}")
                    ).scalar_one()
                    == 0
                ), table
            with _app_login_connection(fixture, tenant_id=_UNSET) as connection:
                assert (
                    connection.execute(
                        text(f"SELECT count(*) FROM {_identifier(table)}")
                    ).scalar_one()
                    == 0
                ), table


def test_0038_every_table_insert_with_check_accepts_owner_and_rejects_cross_unset() -> None:
    with _task7_rls_database() as fixture:
        for table in EXPECTED_TENANT_TABLES:
            statement = text(_clone_insert_sql(table, fixture.insertable_columns[table]))
            params = {
                "payload": json.dumps(
                    fixture.payloads[table],
                    sort_keys=True,
                    default=str,
                )
            }
            with _genuine_insert_app_role_connection(
                fixture,
                table=table,
            ) as connection:
                delete_seed = text(_delete_seed_sql(table, fixture.row_identity_keys[table]))
                assert connection.execute(delete_seed, params).rowcount == 1, table
                assert connection.execute(statement, params).rowcount == 1, table

            for tenant_id in (FOREIGN_TENANT_ID, _UNSET):
                try:
                    with _table_local_app_role_connection(
                        fixture,
                        table=table,
                        tenant_id=tenant_id,
                    ) as connection:
                        connection.execute(statement, params)
                except DBAPIError as error:
                    _assert_rls_denial(error, table)
                else:
                    raise AssertionError(f"{table} INSERT bypassed tenant WITH CHECK")


def test_0038_every_table_update_using_and_with_check_are_fail_closed() -> None:
    with _task7_rls_database() as fixture:
        for table in EXPECTED_TENANT_TABLES:
            same_tenant = text(
                f"UPDATE {_identifier(table)} SET tenant_id=tenant_id "
                "WHERE tenant_id=CAST(:owner AS uuid)"
            )
            cross_target = text(
                f"UPDATE {_identifier(table)} SET tenant_id=CAST(:foreign AS uuid) "
                "WHERE tenant_id=CAST(:owner AS uuid)"
            )
            params = {"owner": OWNER_TENANT_ID, "foreign": FOREIGN_TENANT_ID}
            with _table_local_app_role_connection(
                fixture,
                table=table,
                tenant_id=OWNER_TENANT_ID,
            ) as connection:
                assert connection.execute(same_tenant, params).rowcount > 0, table
            try:
                with _table_local_app_role_connection(
                    fixture,
                    table=table,
                    tenant_id=OWNER_TENANT_ID,
                ) as connection:
                    connection.execute(cross_target, params)
            except DBAPIError as error:
                _assert_rls_denial(error, table)
            else:
                raise AssertionError(f"{table} UPDATE bypassed tenant WITH CHECK")

            for tenant_id in (FOREIGN_TENANT_ID, _UNSET):
                with _app_login_connection(fixture, tenant_id=tenant_id) as connection:
                    assert connection.execute(same_tenant, params).rowcount == 0, table


def test_0038_every_table_delete_allows_owner_and_hides_rows_from_cross_unset() -> None:
    with _task7_rls_database() as fixture:
        for table in EXPECTED_TENANT_TABLES:
            statement = text(
                f"DELETE FROM {_identifier(table)} WHERE tenant_id=CAST(:owner AS uuid)"
            )
            params = {"owner": OWNER_TENANT_ID}
            # Defer child FKs inside the rolled-back probe so RLS, not seed topology, is tested.
            with _genuine_insert_app_role_connection(
                fixture,
                table=table,
            ) as connection:
                assert connection.execute(statement, params).rowcount > 0, table
            for tenant_id in (FOREIGN_TENANT_ID, _UNSET):
                with _app_login_connection(fixture, tenant_id=tenant_id) as connection:
                    assert connection.execute(statement, params).rowcount == 0, table
