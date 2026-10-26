from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
from typing import Any

AUTHORITY_ROW_SCHEMA_HASH_DOMAIN = b"full-strict-v4-authority-row-schema-sha256-v1\x00"

TENANT_PREDICATE = (
    "NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
    "AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid"
)

EXISTING_RLS_POLICIES = {
    "calc_sanads": "calc_sanads_tenant_isolation",
    "data_room_package_files": "tenant_isolation_data_room_package_files",
    "data_room_packages": "tenant_isolation_data_room_packages",
    "debate_sessions": "tenant_isolation_debate_sessions",
    "deliverables": "tenant_isolation_deliverables",
    "deterministic_calculations": "deterministic_calculations_tenant_isolation",
    "document_artifacts": "document_artifacts_tenant_isolation",
    "document_spans": "document_spans_tenant_isolation",
    "documents": "documents_tenant_isolation",
    "evidence_trust_findings": "tenant_isolation_evidence_trust_findings",
    "layer2_ic_challenges": "tenant_isolation_layer2_ic_challenges",
    "layer2_ic_findings": "tenant_isolation_layer2_ic_findings",
    "methodology_coverage_records": "methodology_coverage_records_tenant_isolation",
    "methodology_extraction_results": "methodology_extraction_results_tenant_isolation",
    "methodology_extraction_tasks": "methodology_extraction_tasks_tenant_isolation",
    "methodology_result_claim_links": "methodology_result_claim_links_tenant_isolation",
    "methodology_result_defect_links": "methodology_result_defect_links_tenant_isolation",
    "methodology_result_evidence_links": "methodology_result_evidence_links_tenant_isolation",
    "methodology_result_factual_outputs": ("methodology_result_factual_outputs_tenant_isolation"),
    "methodology_result_sanad_links": "methodology_result_sanad_links_tenant_isolation",
    "muhasabah_records": "tenant_isolation_muhasabah_records",
    "run_steps": "tenant_isolation_run_steps",
    "runs": "tenant_isolation_runs",
    "validated_evidence_packages": "tenant_isolation_validated_evidence_packages",
    "vector_embeddings": "vector_embeddings_tenant_isolation",
}


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def authority_schema_sha256(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "schema_sha256"}
    return hashlib.sha256(
        AUTHORITY_ROW_SCHEMA_HASH_DOMAIN + canonical_json_bytes(payload)
    ).hexdigest()


def registry_sha256(domain: str, rows: object) -> str:
    return hashlib.sha256(canonical_json_bytes([domain, rows])).hexdigest()


def _new_rls(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
        f"TO PUBLIC USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})",
        f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC",
        f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {table} TO <configured_app_role>",
    ]


def _existing_rls(table: str) -> list[str]:
    policy = EXISTING_RLS_POLICIES[table]
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy} ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})",
    ]


def _fk(
    name: str,
    child: tuple[str, ...],
    parent_table: str,
    parent_constraint: str,
    parent: tuple[str, ...],
    support: str,
    *,
    deferred: bool = False,
) -> str:
    suffix = " DEFERRABLE INITIALLY DEFERRED" if deferred else ""
    return (
        f"{name}({','.join(child)}) -> {parent_table}.{parent_constraint}"
        f"({','.join(parent)}) ON UPDATE NO ACTION ON DELETE NO ACTION USING {support}{suffix}"
    )


def _row(
    table: str,
    phase: int,
    *,
    columns: tuple[str, ...],
    checks: tuple[str, ...],
    foreign_keys: tuple[str, ...],
    uniques: tuple[str, ...],
    indexes: tuple[str, ...],
    new_table: bool = False,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "table_name": table,
        "column_tuple": list(columns),
        "check_tuple": list(checks),
        "foreign_key_tuple": list(foreign_keys),
        "unique_tuple": list(uniques),
        "index_tuple": list(indexes),
        "rls_tuple": _new_rls(table) if new_table else _existing_rls(table),
        "erasure_ordinal": phase,
    }
    value["reviewed_authority_utf8_hex"] = canonical_json_bytes(value).hex()
    return value


def _hash_check(name: str, columns: tuple[str, ...]) -> str:
    predicates = " AND ".join(f"{column}~'^[0-9a-f]{{64}}$'" for column in columns)
    return f"{name}({predicates})"


def _nonblank_check(name: str, columns: tuple[str, ...]) -> str:
    predicates = " AND ".join(f"btrim({column})<>''" for column in columns)
    return f"{name}({predicates})"


def _single_kind_root_row(table: str, phase: int, kind: str) -> dict[str, Any]:
    return _row(
        table,
        phase,
        columns=(
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            f"run_output_object_kind TEXT GENERATED ALWAYS AS ('{kind}') STORED",
        ),
        checks=(
            f"ck_task7_{table}_output_shape(run_output_object_id IS NULL OR "
            f"(run_output_object_class='NATIVE_ROW' AND run_output_object_kind='{kind}'))",
        ),
        foreign_keys=(
            _fk(
                f"fk_task7_{table}_output_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                f"ix_task7_{table}_output_root_scope",
            ),
        ),
        uniques=(f"uq_task7_{table}_output_root(run_output_object_id)",),
        indexes=(
            f"ix_task7_{table}_output_root_scope(tenant_id,deal_id,run_id,"
            "run_output_object_id,run_output_object_class,run_output_object_kind)",
        ),
    )


def expected_current_ddl_rows() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    rows["deliverable_manifest_references"] = _row(
        "deliverable_manifest_references",
        0,
        new_table=True,
        columns=(
            "manifest_reference_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "manifest_deliverable_id UUID NOT NULL",
            "manifest_output_object_id UUID NOT NULL",
            "manifest_catalog_ordinal INTEGER GENERATED ALWAYS AS (14) STORED",
            "run_output_object_id UUID NOT NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('DELIVERABLE_MANIFEST_REFERENCE') STORED",
            "member_kind TEXT NOT NULL",
            "catalog_ordinal INTEGER NULL",
            "member_step_output_id UUID NOT NULL",
            "member_output_object_id UUID NOT NULL",
            "member_identity_sha256 TEXT NOT NULL",
            "member_value_sha256 TEXT NOT NULL",
            "member_content_sha256 TEXT NULL",
            "reference_sha256 TEXT NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_deliverable_manifest_references_member_kind(member_kind IN ('ARTIFACT','RECONCILIATION'))",
            "ck_task7_deliverable_manifest_references_coordinate((member_kind='ARTIFACT' AND catalog_ordinal BETWEEN 0 AND 13 AND member_content_sha256 IS NOT NULL) OR (member_kind='RECONCILIATION' AND catalog_ordinal IS NULL AND member_content_sha256 IS NULL))",
            _hash_check(
                "ck_task7_deliverable_manifest_references_hashes",
                (
                    "member_identity_sha256",
                    "member_value_sha256",
                    "reference_sha256",
                ),
            )[:-1]
            + " AND (member_content_sha256 IS NULL OR member_content_sha256~'^[0-9a-f]{64}$'))",
            "ck_task7_deliverable_manifest_references_distinct_roots(member_output_object_id<>manifest_output_object_id AND run_output_object_id<>manifest_output_object_id AND run_output_object_id<>member_output_object_id)",
            "ck_task7_deliverable_manifest_references_lease(lease_owner_id IS NOT NULL AND lease_fencing_token>0)",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_deliverable_manifest_references_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_deliverable_manifest_references_run_fk",
            ),
            _fk(
                "fk_task7_deliverable_manifest_references_manifest",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "manifest_deliverable_id",
                    "manifest_output_object_id",
                    "manifest_catalog_ordinal",
                ),
                "deliverables",
                "uq_task7_deliverables_manifest_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "deliverable_id",
                    "run_output_object_id",
                    "catalog_ordinal",
                ),
                "ix_task7_deliverable_manifest_references_manifest_fk",
            ),
            _fk(
                "fk_task7_deliverable_manifest_references_manifest_root",
                ("tenant_id", "deal_id", "run_id", "manifest_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_task7_deliverable_manifest_references_manifest_fk",
            ),
            _fk(
                "fk_task7_deliverable_manifest_references_native_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                "ix_task7_deliverable_manifest_references_native_root_fk",
            ),
            _fk(
                "fk_task7_deliverable_manifest_references_member_step",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "member_step_output_id",
                    "member_output_object_id",
                ),
                "run_step_outputs",
                "uq_task7_run_step_outputs_scoped_output",
                ("tenant_id", "deal_id", "run_id", "step_output_id", "output_object_id"),
                "ix_task7_deliverable_manifest_references_member_step_fk",
            ),
            _fk(
                "fk_task7_deliverable_manifest_references_member_root",
                ("tenant_id", "deal_id", "run_id", "member_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_task7_deliverable_manifest_references_member_root_fk",
            ),
        ),
        uniques=(
            "pk_task7_deliverable_manifest_references(manifest_reference_id)",
            "uq_task7_deliverable_manifest_references_scoped_id(tenant_id,deal_id,run_id,manifest_reference_id)",
            "uq_task7_deliverable_manifest_references_native_root(run_output_object_id)",
            "uq_task7_deliverable_manifest_references_coordinate(tenant_id,run_id,manifest_deliverable_id,member_kind,catalog_ordinal) NULLS NOT DISTINCT",
            "uq_task7_deliverable_manifest_references_member(tenant_id,run_id,manifest_deliverable_id,member_output_object_id)",
            "uq_task7_deliverable_manifest_references_reference_sha(tenant_id,run_id,manifest_deliverable_id,reference_sha256)",
        ),
        indexes=(
            "ix_task7_deliverable_manifest_references_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_deliverable_manifest_references_manifest_fk(tenant_id,deal_id,run_id,manifest_deliverable_id,manifest_output_object_id,manifest_catalog_ordinal)",
            "ix_task7_deliverable_manifest_references_native_root_fk(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_task7_deliverable_manifest_references_member_step_fk(tenant_id,deal_id,run_id,member_step_output_id,member_output_object_id)",
            "ix_task7_deliverable_manifest_references_member_root_fk(tenant_id,deal_id,run_id,member_output_object_id)",
        ),
    )

    rows["run_consumer_receipts"] = _row(
        "run_consumer_receipts",
        0,
        new_table=True,
        columns=(
            "receipt_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "expected_edge_id UUID NOT NULL",
            "consumer_step_output_id UUID NOT NULL",
            "consumer_output_object_id UUID NOT NULL",
            "consumer_step_name VARCHAR(100) NOT NULL",
            "consumer_subrole TEXT NOT NULL",
            "usage_evidence_sha256 TEXT NOT NULL",
            "consumed_at TIMESTAMPTZ NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_run_consumer_receipts_usage_sha(usage_evidence_sha256~'^[0-9a-f]{64}$')",
            _nonblank_check(
                "ck_task7_run_consumer_receipts_nonblank",
                ("consumer_step_name", "consumer_subrole"),
            ),
            "ck_task7_run_consumer_receipts_lease(lease_owner_id IS NOT NULL AND lease_fencing_token>0)",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_consumer_receipts_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_run_consumer_receipts_consumer",
            ),
            _fk(
                "fk_task7_run_consumer_receipts_edge",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "expected_edge_id",
                    "consumer_step_name",
                    "consumer_subrole",
                ),
                "run_expected_consumer_edges",
                "uq_task7_run_expected_consumer_edges_consumer_tuple",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "expected_edge_id",
                    "consumer_step_name",
                    "consumer_subrole",
                ),
                "ix_task7_run_consumer_receipts_edge_fk",
            ),
            _fk(
                "fk_task7_run_consumer_receipts_consumer_output",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "consumer_step_output_id",
                    "consumer_output_object_id",
                    "consumer_step_name",
                    "consumer_subrole",
                ),
                "run_step_outputs",
                "uq_task7_run_step_outputs_receipt_tuple",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "step_output_id",
                    "output_object_id",
                    "step_name",
                    "subrole",
                ),
                "ix_task7_run_consumer_receipts_consumer_output",
            ),
        ),
        uniques=(
            "pk_task7_run_consumer_receipts(receipt_id)",
            "uq_task7_run_consumer_receipts_scoped_id(tenant_id,deal_id,run_id,receipt_id)",
            "uq_task7_run_consumer_receipts_edge_output(tenant_id,run_id,expected_edge_id,consumer_output_object_id)",
            "uq_task7_run_consumer_receipts_one_per_edge(tenant_id,run_id,expected_edge_id)",
        ),
        indexes=(
            "ix_task7_run_consumer_receipts_edge_fk(tenant_id,deal_id,run_id,expected_edge_id,consumer_step_name,consumer_subrole)",
            "ix_task7_run_consumer_receipts_consumer_output(tenant_id,deal_id,run_id,consumer_step_output_id,consumer_output_object_id,consumer_step_name,consumer_subrole)",
            "ix_task7_run_consumer_receipts_consumer(tenant_id,run_id,consumer_step_name,consumer_subrole,receipt_id)",
        ),
    )

    rows["run_expected_consumer_edges"] = _row(
        "run_expected_consumer_edges",
        1,
        new_table=True,
        columns=(
            "expected_edge_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "producer_input_binding_id UUID NULL",
            "producer_output_object_id UUID NULL",
            "consumer_step_name VARCHAR(100) NOT NULL",
            "consumer_subrole TEXT NOT NULL",
            "usage_kind TEXT NOT NULL",
            "applicability_decision_id UUID NULL",
            "contract_rule_id TEXT NOT NULL",
            "contract_rule_version TEXT NOT NULL",
            "contract_rule_hash TEXT NOT NULL",
            "edge_tuple_sha256 TEXT NOT NULL",
            "materialization_origin TEXT NOT NULL",
            "lease_owner_id UUID NULL",
            "lease_fencing_token BIGINT NOT NULL DEFAULT 0",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_run_expected_consumer_edges_producer_xor((producer_input_binding_id IS NULL)<>(producer_output_object_id IS NULL))",
            "ck_task7_run_expected_consumer_edges_origin(materialization_origin IN ('ADMISSION','WORKER'))",
            _hash_check(
                "ck_task7_run_expected_consumer_edges_hashes",
                ("contract_rule_hash", "edge_tuple_sha256"),
            ),
            _nonblank_check(
                "ck_task7_run_expected_consumer_edges_nonblank",
                (
                    "consumer_step_name",
                    "consumer_subrole",
                    "usage_kind",
                    "contract_rule_id",
                    "contract_rule_version",
                ),
            ),
            "ck_task7_run_expected_consumer_edges_lease((materialization_origin='ADMISSION' AND lease_owner_id IS NULL AND lease_fencing_token=0) OR (materialization_origin='WORKER' AND lease_owner_id IS NOT NULL AND lease_fencing_token>0))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_expected_consumer_edges_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_run_expected_consumer_edges_run_fk",
            ),
            _fk(
                "fk_task7_run_expected_consumer_edges_input",
                ("tenant_id", "deal_id", "run_id", "producer_input_binding_id"),
                "run_input_bindings",
                "uq_task7_run_input_bindings_scoped_id",
                ("tenant_id", "deal_id", "run_id", "binding_id"),
                "ix_task7_run_expected_consumer_edges_input",
            ),
            _fk(
                "fk_task7_run_expected_consumer_edges_output",
                ("tenant_id", "deal_id", "run_id", "producer_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_task7_run_expected_consumer_edges_output",
            ),
            _fk(
                "fk_task7_run_expected_consumer_edges_decision",
                ("tenant_id", "deal_id", "run_id", "applicability_decision_id"),
                "run_applicability_decisions",
                "uq_task7_run_applicability_decisions_scoped_id",
                ("tenant_id", "deal_id", "run_id", "applicability_decision_id"),
                "ix_task7_run_expected_consumer_edges_decision",
            ),
            _fk(
                "fk_task7_run_expected_consumer_edges_consumer_step",
                ("tenant_id", "run_id", "consumer_step_name"),
                "run_steps",
                "uq_task7_run_steps_tenant_run_name",
                ("tenant_id", "run_id", "step_name"),
                "ix_task7_run_expected_consumer_edges_consumer",
            ),
        ),
        uniques=(
            "pk_task7_run_expected_consumer_edges(expected_edge_id)",
            "uq_task7_run_expected_consumer_edges_scoped_id(tenant_id,deal_id,run_id,expected_edge_id)",
            "uq_task7_run_expected_consumer_edges_consumer_tuple(tenant_id,deal_id,run_id,expected_edge_id,consumer_step_name,consumer_subrole)",
            "uq_task7_run_expected_consumer_edges_tuple_sha(tenant_id,run_id,edge_tuple_sha256)",
            "uq_task7_run_expected_consumer_edges_natural(tenant_id,run_id,producer_input_binding_id,producer_output_object_id,consumer_step_name,consumer_subrole,usage_kind,applicability_decision_id,contract_rule_id,contract_rule_version) NULLS NOT DISTINCT",
        ),
        indexes=(
            "ix_task7_run_expected_consumer_edges_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_run_expected_consumer_edges_input(tenant_id,deal_id,run_id,producer_input_binding_id) WHERE producer_input_binding_id IS NOT NULL",
            "ix_task7_run_expected_consumer_edges_output(tenant_id,deal_id,run_id,producer_output_object_id) WHERE producer_output_object_id IS NOT NULL",
            "ix_task7_run_expected_consumer_edges_consumer(tenant_id,run_id,consumer_step_name,consumer_subrole,expected_edge_id)",
            "ix_task7_run_expected_consumer_edges_decision(tenant_id,deal_id,run_id,applicability_decision_id) WHERE applicability_decision_id IS NOT NULL",
        ),
    )

    rows["run_applicability_decisions"] = _row(
        "run_applicability_decisions",
        2,
        new_table=True,
        columns=(
            "applicability_decision_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "decision_phase TEXT NOT NULL",
            "decision_kind TEXT NOT NULL",
            "subject_kind TEXT NOT NULL",
            "subject_key TEXT NOT NULL",
            "outcome TEXT NOT NULL",
            "upstream_input_binding_id UUID NULL",
            "upstream_output_object_id UUID NULL",
            "deciding_step_output_id UUID NOT NULL",
            "contract_rule_id TEXT NOT NULL",
            "contract_rule_version TEXT NOT NULL",
            "contract_rule_hash TEXT NOT NULL",
            "evidence_sha256 TEXT NOT NULL",
            "decision_sha256 TEXT NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_run_applicability_decisions_phase(decision_phase IN ('STEP_5','STEP_16'))",
            "ck_task7_run_applicability_decisions_outcome(outcome IN ('APPLICABLE','NOT_APPLICABLE'))",
            "ck_task7_run_applicability_decisions_upstream_xor((upstream_input_binding_id IS NULL)<>(upstream_output_object_id IS NULL))",
            _hash_check(
                "ck_task7_run_applicability_decisions_hashes",
                ("contract_rule_hash", "evidence_sha256", "decision_sha256"),
            ),
            _nonblank_check(
                "ck_task7_run_applicability_decisions_nonblank",
                (
                    "decision_kind",
                    "subject_kind",
                    "subject_key",
                    "contract_rule_id",
                    "contract_rule_version",
                ),
            ),
            "ck_task7_run_applicability_decisions_lease(lease_owner_id IS NOT NULL AND lease_fencing_token>0)",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_applicability_decisions_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_run_applicability_decisions_subject",
            ),
            _fk(
                "fk_task7_run_applicability_decisions_input",
                ("tenant_id", "deal_id", "run_id", "upstream_input_binding_id"),
                "run_input_bindings",
                "uq_task7_run_input_bindings_scoped_id",
                ("tenant_id", "deal_id", "run_id", "binding_id"),
                "ix_task7_run_applicability_decisions_input",
            ),
            _fk(
                "fk_task7_run_applicability_decisions_output",
                ("tenant_id", "deal_id", "run_id", "upstream_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_task7_run_applicability_decisions_output",
            ),
            _fk(
                "fk_task7_run_applicability_decisions_deciding_output",
                ("tenant_id", "deal_id", "run_id", "deciding_step_output_id"),
                "run_step_outputs",
                "uq_task7_run_step_outputs_scoped_id",
                ("tenant_id", "deal_id", "run_id", "step_output_id"),
                "ix_task7_run_applicability_decisions_deciding",
            ),
        ),
        uniques=(
            "pk_task7_run_applicability_decisions(applicability_decision_id)",
            "uq_task7_run_applicability_decisions_scoped_id(tenant_id,deal_id,run_id,applicability_decision_id)",
            "uq_task7_run_applicability_decisions_subject(tenant_id,run_id,decision_phase,decision_kind,subject_kind,subject_key,contract_rule_id,contract_rule_version)",
            "uq_task7_run_applicability_decisions_decision_sha(tenant_id,run_id,decision_sha256)",
        ),
        indexes=(
            "ix_task7_run_applicability_decisions_subject(tenant_id,run_id,decision_phase,subject_kind,subject_key)",
            "ix_task7_run_applicability_decisions_input(tenant_id,deal_id,run_id,upstream_input_binding_id) WHERE upstream_input_binding_id IS NOT NULL",
            "ix_task7_run_applicability_decisions_output(tenant_id,deal_id,run_id,upstream_output_object_id) WHERE upstream_output_object_id IS NOT NULL",
            "ix_task7_run_applicability_decisions_deciding(tenant_id,deal_id,run_id,deciding_step_output_id)",
        ),
    )

    rows["run_step_outputs"] = _row(
        "run_step_outputs",
        3,
        new_table=True,
        columns=(
            "step_output_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "step_id UUID NOT NULL",
            "output_object_id UUID NOT NULL",
            "step_name VARCHAR(100) NOT NULL",
            "subrole TEXT NOT NULL DEFAULT '__step__'",
            "output_kind TEXT NOT NULL",
            "output_ordinal INTEGER NOT NULL",
            "semantic_rule_version TEXT NOT NULL",
            "semantic_rule_hash TEXT NOT NULL",
            "summary_sha256 TEXT NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_run_step_outputs_ordinal(output_ordinal>=0)",
            _hash_check(
                "ck_task7_run_step_outputs_hashes",
                ("semantic_rule_hash", "summary_sha256"),
            ),
            _nonblank_check(
                "ck_task7_run_step_outputs_nonblank",
                ("step_name", "subrole", "output_kind", "semantic_rule_version"),
            ),
            "ck_task7_run_step_outputs_lease(lease_owner_id IS NOT NULL AND lease_fencing_token>0)",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_step_outputs_step",
                ("tenant_id", "run_id", "step_id", "step_name"),
                "run_steps",
                "uq_task7_run_steps_scoped_id_name",
                ("tenant_id", "run_id", "step_id", "step_name"),
                "ix_task7_run_step_outputs_step_fk",
            ),
            _fk(
                "fk_task7_run_step_outputs_output",
                ("tenant_id", "deal_id", "run_id", "output_object_id", "output_kind"),
                "run_output_objects",
                "uq_task7_run_output_objects_step_ref",
                ("tenant_id", "deal_id", "run_id", "output_object_id", "output_kind"),
                "ix_task7_run_step_outputs_output_fk",
            ),
        ),
        uniques=(
            "pk_task7_run_step_outputs(step_output_id)",
            "uq_task7_run_step_outputs_output_object(output_object_id)",
            "uq_task7_run_step_outputs_scoped_id(tenant_id,deal_id,run_id,step_output_id)",
            "uq_task7_run_step_outputs_scoped_output(tenant_id,deal_id,run_id,step_output_id,output_object_id)",
            "uq_task7_run_step_outputs_coordinate(tenant_id,run_id,step_id,subrole,output_kind,output_ordinal)",
            "uq_task7_run_step_outputs_receipt_tuple(tenant_id,deal_id,run_id,step_output_id,output_object_id,step_name,subrole)",
        ),
        indexes=(
            "ix_task7_run_step_outputs_step_fk(tenant_id,run_id,step_id,step_name)",
            "ix_task7_run_step_outputs_output_fk(tenant_id,deal_id,run_id,output_object_id,output_kind)",
            "ix_task7_run_step_outputs_consumer_lookup(tenant_id,run_id,step_name,subrole,output_kind,output_ordinal)",
        ),
    )

    for table, phase, kind in (
        ("methodology_result_claim_links", 4, "METHODOLOGY_CLAIM_LINK"),
        ("methodology_result_factual_outputs", 5, "METHODOLOGY_FACTUAL_OUTPUT"),
        ("methodology_extraction_results", 6, "METHODOLOGY_EXTRACTION_RESULT"),
        ("methodology_extraction_tasks", 7, "METHODOLOGY_EXTRACTION_TASK"),
        ("methodology_coverage_records", 8, "METHODOLOGY_COVERAGE_IDENTITY"),
        ("layer2_ic_findings", 11, "LAYER2_FINDING"),
        ("layer2_ic_challenges", 12, "LAYER2_CHALLENGE"),
        ("debate_sessions", 13, "DEBATE_SESSION"),
        ("evidence_trust_findings", 13, "EVIDENCE_TRUST_FINDING"),
        ("validated_evidence_packages", 13, "VALIDATED_EVIDENCE_PACKAGE"),
        ("methodology_result_evidence_links", 63, "METHODOLOGY_EVIDENCE_LINK"),
        ("methodology_result_sanad_links", 63, "METHODOLOGY_SANAD_LINK"),
        ("methodology_result_defect_links", 64, "METHODOLOGY_DEFECT_LINK"),
    ):
        rows[table] = _single_kind_root_row(table, phase, kind)

    # The calculation lineage FK must resolve to the exact immutable Task-5
    # coverage/question row, never to a reusable external question identifier.
    coverage_row = rows["methodology_coverage_records"]
    coverage_row["unique_tuple"].append(
        "uq_task7_methodology_coverage_calculation_parent(tenant_id,deal_id,run_id,coverage_record_id,methodology_question_record_id)"
    )
    # Re-snapshot the authority payload, never the stale snapshot field itself.
    coverage_payload = {
        key: value for key, value in coverage_row.items() if key != "reviewed_authority_utf8_hex"
    }
    coverage_row["reviewed_authority_utf8_hex"] = canonical_json_bytes(coverage_payload).hex()

    rows["calc_sanads"] = _row(
        "calc_sanads",
        9,
        columns=("deal_id UUID NULL", "run_id UUID NULL"),
        checks=(
            "ck_task7_calc_sanads_scope((deal_id IS NULL AND run_id IS NULL) OR (deal_id IS NOT NULL AND run_id IS NOT NULL))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_calc_sanads_calculation",
                ("tenant_id", "deal_id", "run_id", "calc_id"),
                "deterministic_calculations",
                "uq_task7_deterministic_calculations_scope",
                ("tenant_id", "deal_id", "run_id", "calc_id"),
                "ix_task7_calc_sanads_calculation_fk",
            ),
        ),
        uniques=("uq_task7_calc_sanads_scope(tenant_id,deal_id,run_id,calc_sanad_id)",),
        indexes=("ix_task7_calc_sanads_calculation_fk(tenant_id,deal_id,run_id,calc_id)",),
    )

    rows["deterministic_calculations"] = _row(
        "deterministic_calculations",
        10,
        columns=(
            "run_id UUID NULL",
            "producer_step_name VARCHAR(100) NULL",
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT NULL",
            "coverage_record_id TEXT NULL",
            "methodology_question_record_id UUID NULL",
        ),
        checks=(
            "ck_task7_deterministic_calculations_output_shape((producer_step_name IS NULL AND run_output_object_id IS NULL AND run_output_object_kind IS NULL AND coverage_record_id IS NULL AND methodology_question_record_id IS NULL) OR (run_id IS NOT NULL AND run_output_object_id IS NOT NULL AND run_output_object_class='NATIVE_ROW' AND ((producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' AND run_output_object_kind='METHODOLOGY_CALCULATION' AND coverage_record_id IS NOT NULL AND methodology_question_record_id IS NOT NULL) OR (producer_step_name='CALC' AND run_output_object_kind='CALCULATION' AND coverage_record_id IS NULL AND methodology_question_record_id IS NULL))))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_deterministic_calculations_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_deterministic_calculations_run_fk",
            ),
            _fk(
                "fk_task7_deterministic_calculations_output_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                "ix_task7_deterministic_calculations_output_root_scope",
            ),
            _fk(
                "fk_task7_deterministic_calculations_coverage_question",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "coverage_record_id",
                    "methodology_question_record_id",
                ),
                "methodology_coverage_records",
                "uq_task7_methodology_coverage_calculation_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "coverage_record_id",
                    "methodology_question_record_id",
                ),
                "ix_task7_deterministic_calculations_coverage_question",
            ),
        ),
        uniques=(
            "uq_task7_deterministic_calculations_scope(tenant_id,deal_id,run_id,calc_id)",
            "uq_task7_deterministic_calculations_output_root(run_output_object_id)",
        ),
        indexes=(
            "ix_task7_deterministic_calculations_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_deterministic_calculations_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_task7_deterministic_calculations_coverage_question(tenant_id,deal_id,run_id,coverage_record_id,methodology_question_record_id)",
        ),
    )

    rows["muhasabah_records"] = _row(
        "muhasabah_records",
        14,
        columns=(
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT NULL",
        ),
        checks=(
            "ck_task7_muhasabah_records_output_shape((run_output_object_id IS NULL AND run_output_object_kind IS NULL) OR (run_output_object_id IS NOT NULL AND run_output_object_class='NATIVE_ROW' AND ((source_step='METHODOLOGY_EVIDENCE_TRUST_COURT' AND run_output_object_kind='COURT_MUHASABAH') OR (source_step='DEBATE' AND run_output_object_kind='DEBATE_MUHASABAH') OR (source_step='ANALYSIS' AND agent_id IN ('financial_agent','market_agent','technical_agent','terms_agent','team_agent','risk_officer_agent','historian_agent','sector_specialist_agent') AND run_output_object_kind='ANALYSIS_MUHASABAH') OR (source_step='LAYER2_IC_CHALLENGE' AND run_output_object_kind='LAYER2_MUHASABAH'))))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_muhasabah_records_output_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                "ix_task7_muhasabah_records_output_root_scope",
            ),
        ),
        uniques=(
            "uq_task7_muhasabah_records_output_root(run_output_object_id)",
            "uq_v2_muhasabah_debate_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "uq_v2_muhasabah_analysis_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "uq_v2_muhasabah_layer2_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
        ),
        indexes=(
            "ix_task7_muhasabah_records_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_v2_muhasabah_debate_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "ix_v2_muhasabah_analysis_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "ix_v2_muhasabah_layer2_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
        ),
    )

    rows["runs"] = _row(
        "runs",
        15,
        columns=(
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "pipeline_resume_state TEXT NOT NULL",
            "pipeline_resume_error_code VARCHAR(100) NULL",
        ),
        checks=(
            "ck_task7_runs_pipeline_contract_version(btrim(pipeline_contract_version)<>'')",
            "ck_task7_runs_pipeline_contract_hash(pipeline_contract_hash~'^[0-9a-f]{64}$')",
            "ck_task7_runs_pipeline_resume_state(pipeline_resume_state IN ('RESUMABLE','HISTORICAL_TERMINAL','NON_RESUMABLE'))",
            "ck_task7_runs_pipeline_resume_error((pipeline_resume_state='NON_RESUMABLE' AND pipeline_resume_error_code='LEGACY_RUN_RESUME_UNSAFE') OR (pipeline_resume_state<>'NON_RESUMABLE' AND pipeline_resume_error_code IS NULL))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_runs_deal",
                ("tenant_id", "deal_id"),
                "deals",
                "uq_task7_deals_tenant_deal",
                ("tenant_id", "deal_id"),
                "uq_task7_deals_tenant_deal",
            ),
        ),
        uniques=(
            "uq_task7_runs_tenant_run(tenant_id,run_id)",
            "uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id)",
        ),
        indexes=("idx_runs_tenant_id(tenant_id)", "idx_runs_deal_id(deal_id)"),
    )

    rows["deliverables"] = _row(
        "deliverables",
        16,
        columns=(
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('DELIVERABLE_ARTIFACT') STORED",
            "content_type TEXT NULL",
            "size_bytes BIGINT NULL",
            "content_sha256 TEXT NULL",
            "uri_sha256 TEXT NULL",
            "object_version_hash TEXT NULL",
            "download_proof_hash TEXT NULL",
            "catalog_ordinal INTEGER NULL",
        ),
        checks=(
            "ck_task7_deliverables_terminal_root((run_output_object_id IS NULL AND content_type IS NULL AND size_bytes IS NULL AND content_sha256 IS NULL AND uri_sha256 IS NULL AND object_version_hash IS NULL AND download_proof_hash IS NULL AND catalog_ordinal IS NULL) OR (run_output_object_id IS NOT NULL AND run_id IS NOT NULL AND status='COMPLETED' AND run_output_object_class='NATIVE_ROW' AND run_output_object_kind='DELIVERABLE_ARTIFACT' AND btrim(content_type)<>'' AND size_bytes>0 AND content_sha256~'^[0-9a-f]{64}$' AND uri_sha256~'^[0-9a-f]{64}$' AND object_version_hash~'^[0-9a-f]{64}$' AND download_proof_hash~'^[0-9a-f]{64}$' AND catalog_ordinal BETWEEN 0 AND 14))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_deliverables_output_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                "ix_task7_deliverables_output_root_scope",
            ),
        ),
        uniques=(
            "uq_task7_deliverables_output_root(run_output_object_id)",
            "uq_task7_deliverables_manifest_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id,catalog_ordinal)",
            "uq_v2_deliverables_reference_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id)",
        ),
        indexes=(
            "ix_task7_deliverables_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_v2_deliverables_reference_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id)",
        ),
    )

    rows["vector_embeddings"] = _row(
        "vector_embeddings",
        16,
        columns=(
            "producer_step_name VARCHAR(100) NULL",
            "embedding_namespace TEXT NULL",
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT NULL",
        ),
        checks=(
            "ck_task7_vector_embeddings_output_shape((run_id IS NULL AND producer_step_name IS NULL AND embedding_namespace IS NULL AND run_output_object_id IS NULL AND run_output_object_kind IS NULL) OR (run_id IS NOT NULL AND run_output_object_id IS NOT NULL AND run_output_object_class='NATIVE_ROW' AND ((producer_step_name='EXTRACT' AND embedding_namespace='internal_evidence' AND run_output_object_kind='EXTRACT_RAG_EMBEDDING') OR (producer_step_name='RAG_EVIDENCE' AND embedding_namespace IN ('internal_evidence','external_context') AND run_output_object_kind='RAG_EVIDENCE_EMBEDDING'))))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_vector_embeddings_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_vector_embeddings_run_fk",
            ),
            _fk(
                "fk_task7_vector_embeddings_output_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                "ix_task7_vector_embeddings_output_root_scope",
            ),
        ),
        uniques=(
            "uq_task7_vector_embeddings_output_root(run_output_object_id)",
            "ux_vector_embeddings_reusable(tenant_id,deal_id,source_type,source_id,content_hash,embedding_model,embedding_dimensions) WHERE run_id IS NULL",
            "ux_vector_embeddings_strict_run(tenant_id,deal_id,run_id,source_type,source_id,content_hash,embedding_model,embedding_dimensions) WHERE run_id IS NOT NULL",
        ),
        indexes=(
            "ix_task7_vector_embeddings_run_fk(tenant_id,deal_id,run_id) WHERE run_id IS NOT NULL",
            "ix_task7_vector_embeddings_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
        ),
    )

    rows["run_output_objects"] = _row(
        "run_output_objects",
        17,
        new_table=True,
        columns=(
            "output_object_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "output_class TEXT NOT NULL",
            "output_kind TEXT NOT NULL",
            "output_schema_version TEXT NOT NULL",
            "native_identity_sha256 TEXT NOT NULL",
            "native_output_sha256 TEXT NOT NULL",
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "write_origin TEXT NOT NULL",
            "lease_owner_id UUID NULL",
            "lease_fencing_token BIGINT NOT NULL DEFAULT 0",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_run_output_objects_class(output_class IN ('STEP_RECEIPT','NATIVE_ROW'))",
            "ck_task7_run_output_objects_origin(write_origin IN ('BACKFILL','RUNTIME'))",
            _hash_check(
                "ck_task7_run_output_objects_hashes",
                ("native_identity_sha256", "native_output_sha256", "pipeline_contract_hash"),
            ),
            _nonblank_check(
                "ck_task7_run_output_objects_nonblank",
                ("output_kind", "output_schema_version", "pipeline_contract_version"),
            ),
            "ck_task7_run_output_objects_lease((write_origin='BACKFILL' AND lease_owner_id IS NULL AND lease_fencing_token=0) OR (write_origin='RUNTIME' AND lease_owner_id IS NOT NULL AND lease_fencing_token>0))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_output_objects_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_run_output_objects_kind",
            ),
        ),
        uniques=(
            "pk_task7_run_output_objects(output_object_id)",
            "uq_task7_run_output_objects_scope(tenant_id,deal_id,run_id,output_object_id)",
            "uq_task7_run_output_objects_tenant_run_id(tenant_id,run_id,output_object_id)",
            "uq_task7_run_output_objects_step_ref(tenant_id,deal_id,run_id,output_object_id,output_kind)",
            "uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind)",
            "uq_task7_run_output_objects_semantic_identity(tenant_id,run_id,output_kind,native_identity_sha256)",
        ),
        indexes=(
            "ix_task7_run_output_objects_kind(tenant_id,deal_id,run_id,output_kind)",
            "ix_task7_run_output_objects_created(tenant_id,run_id,created_at,output_object_id)",
            "ix_task7_run_output_objects_output_sha(tenant_id,run_id,output_kind,native_output_sha256)",
        ),
    )

    rows["run_input_bindings"] = _row(
        "run_input_bindings",
        18,
        new_table=True,
        columns=(
            "binding_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "source_lineage_object_id UUID NOT NULL",
            "source_object_kind TEXT NOT NULL",
            "source_disposition TEXT NOT NULL",
            "binding_purpose TEXT NOT NULL",
            "corpus_receipt_id UUID NOT NULL",
            "selected_source_version TEXT NOT NULL",
            "selected_identity_sha256 TEXT NOT NULL",
            "selected_content_sha256 TEXT NOT NULL",
            "corpus_receipt_sha256 TEXT NOT NULL",
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "binding_sha256 TEXT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_run_input_bindings_source_kind(source_object_kind IN ('PACKAGE','PACKAGE_FILE','ARTIFACT','DOCUMENT','PARSE_ATTEMPT','SPAN'))",
            "ck_task7_run_input_bindings_disposition(source_disposition IN ('CORPUS','EVIDENCE_BEARING','CONFIRMED_EXCLUSION'))",
            "ck_task7_run_input_bindings_purpose(binding_purpose IN ('INVENTORY','HANDOFF','EVIDENCE'))",
            _hash_check(
                "ck_task7_run_input_bindings_hashes",
                (
                    "selected_identity_sha256",
                    "selected_content_sha256",
                    "corpus_receipt_sha256",
                    "pipeline_contract_hash",
                    "binding_sha256",
                ),
            ),
            _nonblank_check(
                "ck_task7_run_input_bindings_nonblank",
                ("selected_source_version", "pipeline_contract_version"),
            ),
            "ck_task7_run_input_bindings_recipe((source_object_kind='PACKAGE' AND source_disposition='CORPUS' AND binding_purpose='INVENTORY') OR (source_object_kind='PACKAGE_FILE' AND source_disposition IN ('EVIDENCE_BEARING','CONFIRMED_EXCLUSION') AND binding_purpose='INVENTORY') OR (source_object_kind IN ('ARTIFACT','DOCUMENT','PARSE_ATTEMPT') AND source_disposition IN ('EVIDENCE_BEARING','CONFIRMED_EXCLUSION') AND binding_purpose='HANDOFF') OR (source_object_kind='SPAN' AND source_disposition='EVIDENCE_BEARING' AND binding_purpose='EVIDENCE'))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_input_bindings_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                "ix_task7_run_input_bindings_run_fk",
            ),
            _fk(
                "fk_task7_run_input_bindings_corpus",
                ("tenant_id", "deal_id", "corpus_receipt_id"),
                "data_room_packages",
                "uq_task7_data_room_packages_scope",
                ("tenant_id", "deal_id", "package_id"),
                "ix_task7_run_input_bindings_corpus",
            ),
            _fk(
                "fk_task7_run_input_bindings_source",
                (
                    "tenant_id",
                    "deal_id",
                    "corpus_receipt_id",
                    "source_lineage_object_id",
                    "source_object_kind",
                    "selected_source_version",
                    "source_disposition",
                    "selected_identity_sha256",
                    "selected_content_sha256",
                    "corpus_receipt_sha256",
                ),
                "source_lineage_objects",
                "uq_task7_source_lineage_objects_binding_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "corpus_receipt_id",
                    "source_lineage_object_id",
                    "object_kind",
                    "source_version",
                    "source_disposition",
                    "object_identity_sha256",
                    "content_sha256",
                    "corpus_receipt_sha256",
                ),
                "ix_task7_run_input_bindings_source_fk",
            ),
        ),
        uniques=(
            "pk_task7_run_input_bindings(binding_id)",
            "uq_task7_run_input_bindings_run_source(tenant_id,run_id,source_lineage_object_id)",
            "uq_task7_run_input_bindings_scoped_id(tenant_id,deal_id,run_id,binding_id)",
            "uq_task7_run_input_bindings_binding_sha(tenant_id,run_id,binding_sha256)",
        ),
        indexes=(
            "ix_task7_run_input_bindings_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_run_input_bindings_corpus(tenant_id,deal_id,corpus_receipt_id)",
            "ix_task7_run_input_bindings_source_fk(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,source_object_kind,selected_source_version,source_disposition,selected_identity_sha256,selected_content_sha256,corpus_receipt_sha256)",
        ),
    )

    _add_source_rows(rows)
    _add_run_steps_row(rows)

    order = (
        "deliverable_manifest_references",
        "run_consumer_receipts",
        "run_expected_consumer_edges",
        "run_applicability_decisions",
        "run_step_outputs",
        "methodology_result_claim_links",
        "methodology_result_factual_outputs",
        "methodology_extraction_results",
        "methodology_extraction_tasks",
        "methodology_coverage_records",
        "calc_sanads",
        "deterministic_calculations",
        "layer2_ic_findings",
        "layer2_ic_challenges",
        "debate_sessions",
        "evidence_trust_findings",
        "validated_evidence_packages",
        "muhasabah_records",
        "runs",
        "deliverables",
        "vector_embeddings",
        "run_output_objects",
        "run_input_bindings",
        "document_spans",
        "data_room_package_files",
        "document_parse_attempts",
        "documents",
        "document_artifacts",
        "data_room_packages",
        "source_lineage_objects",
        "run_steps",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
    )
    assert set(rows) == set(order)
    return [rows[table] for table in order]


def _source_root_fk(table: str, *, deferred: bool = False) -> str:
    return _fk(
        f"fk_task7_{table}_source_root",
        (
            "tenant_id",
            "deal_id",
            "source_corpus_receipt_id",
            "source_lineage_object_id",
            "source_lineage_object_kind",
        ),
        "source_lineage_objects",
        "uq_task7_source_lineage_objects_corpus_scope_kind",
        (
            "tenant_id",
            "deal_id",
            "corpus_receipt_id",
            "source_lineage_object_id",
            "object_kind",
        ),
        f"ix_task7_{table}_source_root_scope",
        deferred=deferred,
    )


def _add_source_rows(rows: dict[str, dict[str, Any]]) -> None:
    rows["source_lineage_objects"] = _row(
        "source_lineage_objects",
        24,
        new_table=True,
        columns=(
            "source_lineage_object_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "corpus_receipt_id UUID NOT NULL",
            "object_kind TEXT NOT NULL",
            "source_version TEXT NOT NULL",
            "source_disposition TEXT NOT NULL",
            "object_identity_sha256 TEXT NOT NULL",
            "content_sha256 TEXT NOT NULL",
            "corpus_receipt_sha256 TEXT NOT NULL",
            "write_origin TEXT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_source_lineage_objects_kind(object_kind IN ('PACKAGE','PACKAGE_FILE','ARTIFACT','DOCUMENT','PARSE_ATTEMPT','SPAN'))",
            "ck_task7_source_lineage_objects_disposition(source_disposition IN ('CORPUS','EVIDENCE_BEARING','CONFIRMED_EXCLUSION'))",
            "ck_task7_source_lineage_objects_origin(write_origin IN ('BACKFILL','RUNTIME'))",
            _hash_check(
                "ck_task7_source_lineage_objects_hashes",
                ("object_identity_sha256", "content_sha256", "corpus_receipt_sha256"),
            ),
            "ck_task7_source_lineage_objects_nonblank(btrim(source_version)<>'')",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_source_lineage_objects_deal",
                ("tenant_id", "deal_id"),
                "deals",
                "uq_task7_deals_tenant_deal",
                ("tenant_id", "deal_id"),
                "uq_task7_deals_tenant_deal",
            ),
            _fk(
                "fk_task7_source_lineage_objects_corpus",
                ("tenant_id", "deal_id", "corpus_receipt_id"),
                "data_room_packages",
                "uq_task7_data_room_packages_scope",
                ("tenant_id", "deal_id", "package_id"),
                "ix_task7_source_lineage_objects_corpus_kind",
                deferred=True,
            ),
        ),
        uniques=(
            "pk_task7_source_lineage_objects(source_lineage_object_id)",
            "uq_task7_source_lineage_objects_scope_kind(tenant_id,deal_id,source_lineage_object_id,object_kind)",
            "uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind)",
            "uq_task7_source_lineage_objects_binding_parent(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind,source_version,source_disposition,object_identity_sha256,content_sha256,corpus_receipt_sha256)",
            "uq_task7_source_lineage_objects_identity_version(tenant_id,deal_id,corpus_receipt_id,object_kind,object_identity_sha256,source_version)",
        ),
        indexes=(
            "ix_task7_source_lineage_objects_corpus_kind(tenant_id,deal_id,corpus_receipt_id,object_kind)",
            "ix_task7_source_lineage_objects_content(tenant_id,deal_id,content_sha256)",
        ),
    )

    rows["document_parse_attempts"] = _row(
        "document_parse_attempts",
        20,
        new_table=True,
        columns=(
            "parse_attempt_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "document_id UUID NOT NULL",
            "attempt_ordinal INTEGER NOT NULL",
            "parser_name TEXT NOT NULL",
            "parser_version TEXT NOT NULL",
            "input_sha256 TEXT NOT NULL",
            "receipt_sha256 TEXT NOT NULL",
            "status TEXT NOT NULL",
            "started_at TIMESTAMPTZ NOT NULL",
            "finished_at TIMESTAMPTZ NOT NULL",
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('PARSE_ATTEMPT') STORED",
            "source_corpus_receipt_id UUID NULL",
            "write_origin TEXT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ),
        checks=(
            "ck_task7_document_parse_attempts_ordinal(attempt_ordinal>0)",
            "ck_task7_document_parse_attempts_status(status IN ('PARSED','FAILED'))",
            "ck_task7_document_parse_attempts_times(finished_at>=started_at)",
            "ck_task7_document_parse_attempts_origin(write_origin IN ('BACKFILL','RUNTIME'))",
            _hash_check(
                "ck_task7_document_parse_attempts_hashes",
                ("input_sha256", "receipt_sha256"),
            ),
            _nonblank_check(
                "ck_task7_document_parse_attempts_nonblank",
                ("parser_name", "parser_version"),
            ),
            "ck_task7_document_parse_attempts_binding_state((source_binding_state='UNBOUND' AND source_lineage_object_id IS NULL AND source_corpus_receipt_id IS NULL) OR (source_binding_state='BOUND' AND source_lineage_object_id IS NOT NULL AND source_corpus_receipt_id IS NOT NULL))",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_parse_attempts_document",
                ("tenant_id", "deal_id", "document_id"),
                "documents",
                "uq_task7_documents_scope",
                ("tenant_id", "deal_id", "document_id"),
                "ix_task7_document_parse_attempts_document_ordinal",
            ),
            _source_root_fk("document_parse_attempts"),
        ),
        uniques=(
            "pk_task7_document_parse_attempts(parse_attempt_id)",
            "uq_task7_document_parse_attempts_ordinal(tenant_id,deal_id,document_id,attempt_ordinal)",
            "uq_task7_document_parse_attempts_scope(tenant_id,deal_id,document_id,parse_attempt_id)",
            "uq_task7_document_parse_attempts_source_root(source_lineage_object_id)",
        ),
        indexes=(
            "ix_task7_document_parse_attempts_document_ordinal(tenant_id,deal_id,document_id,attempt_ordinal DESC,parse_attempt_id)",
            "ix_task7_document_parse_attempts_receipt(tenant_id,deal_id,receipt_sha256)",
            "ix_task7_document_parse_attempts_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)",
        ),
    )

    source_shapes = {
        "data_room_packages": (
            23,
            "PACKAGE",
            "source_corpus_receipt_id UUID GENERATED ALWAYS AS (package_id) STORED",
            "ck_task7_data_room_packages_binding_state((status='OPEN' AND source_binding_state='UNBOUND' AND source_lineage_object_id IS NULL) OR (status='SEALED' AND source_binding_state='BOUND' AND source_lineage_object_id IS NOT NULL))",
        ),
        "data_room_package_files": (
            20,
            "PACKAGE_FILE",
            "source_corpus_receipt_id UUID GENERATED ALWAYS AS (package_id) STORED",
            "ck_task7_data_room_package_files_binding_state((source_binding_state='UNBOUND' AND source_lineage_object_id IS NULL) OR (source_binding_state='BOUND' AND source_lineage_object_id IS NOT NULL))",
        ),
        "document_artifacts": (
            22,
            "ARTIFACT",
            "source_corpus_receipt_id UUID NULL",
            "ck_task7_document_artifacts_binding_state((source_binding_state='UNBOUND' AND source_lineage_object_id IS NULL AND source_corpus_receipt_id IS NULL) OR (source_binding_state='BOUND' AND source_lineage_object_id IS NOT NULL AND source_corpus_receipt_id IS NOT NULL))",
        ),
        "documents": (
            21,
            "DOCUMENT",
            "source_corpus_receipt_id UUID NULL",
            "ck_task7_documents_binding_state((source_binding_state='UNBOUND' AND source_lineage_object_id IS NULL AND source_corpus_receipt_id IS NULL) OR (source_binding_state='BOUND' AND source_lineage_object_id IS NOT NULL AND source_corpus_receipt_id IS NOT NULL))",
        ),
        "document_spans": (
            19,
            "SPAN",
            "source_corpus_receipt_id UUID NULL",
            "ck_task7_document_spans_binding_state((source_binding_state='UNBOUND' AND source_lineage_object_id IS NULL AND source_corpus_receipt_id IS NULL AND parse_attempt_id IS NULL) OR (source_binding_state='BOUND' AND source_lineage_object_id IS NOT NULL AND source_corpus_receipt_id IS NOT NULL AND parse_attempt_id IS NOT NULL))",
        ),
    }

    for table, (phase, kind, corpus_column, binding_check) in source_shapes.items():
        extra_columns: tuple[str, ...] = ()
        if table == "document_spans":
            extra_columns = ("deal_id UUID NOT NULL", "parse_attempt_id UUID NULL")
        columns = (
            *extra_columns,
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            f"source_lineage_object_kind TEXT GENERATED ALWAYS AS ('{kind}') STORED",
            corpus_column,
        )
        checks = [binding_check]
        fks = [_source_root_fk(table, deferred=table == "data_room_packages")]
        uniques = [f"uq_task7_{table}_source_root(source_lineage_object_id)"]
        indexes = [
            f"ix_task7_{table}_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)"
        ]

        if table == "data_room_packages":
            uniques.insert(0, "uq_task7_data_room_packages_scope(tenant_id,deal_id,package_id)")
        elif table == "data_room_package_files":
            checks.append(
                "ck_task7_package_files_document_pair((source_binding_state='UNBOUND' AND doc_id IS NULL AND document_id IS NULL) OR (source_binding_state='BOUND' AND doc_id IS NOT NULL AND document_id IS NOT NULL))"
            )
            fks.extend(
                (
                    _fk(
                        "fk_task7_package_files_package",
                        ("tenant_id", "deal_id", "package_id"),
                        "data_room_packages",
                        "uq_task7_data_room_packages_scope",
                        ("tenant_id", "deal_id", "package_id"),
                        "ix_task7_package_files_package_fk",
                    ),
                    _fk(
                        "fk_task7_package_files_document",
                        ("tenant_id", "deal_id", "doc_id", "document_id"),
                        "documents",
                        "uq_task7_documents_artifact_document",
                        ("tenant_id", "deal_id", "doc_id", "document_id"),
                        "ix_task7_package_files_document_fk",
                    ),
                )
            )
            uniques.extend(
                (
                    "ux_task7_package_files_doc_id_bound(tenant_id,deal_id,doc_id) WHERE doc_id IS NOT NULL",
                    "ux_task7_package_files_document_id_bound(tenant_id,deal_id,document_id) WHERE document_id IS NOT NULL",
                )
            )
            indexes.extend(
                (
                    "ix_task7_package_files_package_fk(tenant_id,deal_id,package_id)",
                    "ix_task7_package_files_document_fk(tenant_id,deal_id,doc_id,document_id)",
                )
            )
        elif table == "document_artifacts":
            uniques.insert(0, "uq_task7_document_artifacts_scope(tenant_id,deal_id,doc_id)")
        elif table == "documents":
            fks.append(
                _fk(
                    "fk_task7_documents_artifact",
                    ("tenant_id", "deal_id", "doc_id"),
                    "document_artifacts",
                    "uq_task7_document_artifacts_scope",
                    ("tenant_id", "deal_id", "doc_id"),
                    "ix_task7_documents_artifact_fk",
                )
            )
            uniques[0:0] = [
                "uq_task7_documents_scope(tenant_id,deal_id,document_id)",
                "uq_task7_documents_artifact_document(tenant_id,deal_id,doc_id,document_id)",
            ]
            indexes.append("ix_task7_documents_artifact_fk(tenant_id,deal_id,doc_id)")
        elif table == "document_spans":
            fks.extend(
                (
                    _fk(
                        "fk_task7_spans_document",
                        ("tenant_id", "deal_id", "document_id"),
                        "documents",
                        "uq_task7_documents_scope",
                        ("tenant_id", "deal_id", "document_id"),
                        "ix_task7_spans_document_fk",
                    ),
                    _fk(
                        "fk_task7_spans_deal",
                        ("tenant_id", "deal_id"),
                        "deals",
                        "uq_task7_deals_tenant_deal",
                        ("tenant_id", "deal_id"),
                        "ix_task7_spans_deal_fk",
                    ),
                    _fk(
                        "fk_task7_spans_parse_attempt",
                        ("tenant_id", "deal_id", "document_id", "parse_attempt_id"),
                        "document_parse_attempts",
                        "uq_task7_document_parse_attempts_scope",
                        ("tenant_id", "deal_id", "document_id", "parse_attempt_id"),
                        "ix_task7_spans_parse_attempt_fk",
                    ),
                )
            )
            uniques.insert(
                0,
                "ux_document_spans_task6_scope(tenant_id,deal_id,span_id) WHERE deal_id IS NOT NULL",
            )
            indexes.extend(
                (
                    "ix_task7_spans_document_fk(tenant_id,deal_id,document_id)",
                    "ix_task7_spans_deal_fk(tenant_id,deal_id)",
                    "ix_task7_spans_parse_attempt_fk(tenant_id,deal_id,document_id,parse_attempt_id) WHERE parse_attempt_id IS NOT NULL",
                )
            )

        rows[table] = _row(
            table,
            phase,
            columns=columns,
            checks=tuple(checks),
            foreign_keys=tuple(fks),
            uniques=tuple(uniques),
            indexes=tuple(indexes),
        )


def _add_run_steps_row(rows: dict[str, dict[str, Any]]) -> None:
    rows["run_steps"] = _row(
        "run_steps",
        25,
        columns=(
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "semantic_rule_version TEXT NOT NULL",
            "semantic_rule_hash TEXT NOT NULL",
        ),
        checks=(
            "ck_task7_run_steps_order(step_order>=0)",
            "ck_task7_run_steps_pipeline_contract_version(btrim(pipeline_contract_version)<>'')",
            "ck_task7_run_steps_pipeline_contract_hash(pipeline_contract_hash~'^[0-9a-f]{64}$')",
            "ck_task7_run_steps_semantic_rule_version(btrim(semantic_rule_version)<>'')",
            "ck_task7_run_steps_semantic_rule_hash(semantic_rule_hash~'^[0-9a-f]{64}$')",
        ),
        foreign_keys=(
            _fk(
                "fk_task7_run_steps_run",
                ("tenant_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_run",
                ("tenant_id", "run_id"),
                "uq_task7_run_steps_tenant_run_name",
            ),
        ),
        uniques=(
            "uq_task7_run_steps_order(tenant_id,run_id,step_order)",
            "uq_task7_run_steps_tenant_run_name(tenant_id,run_id,step_name)",
            "uq_task7_run_steps_scoped_id_name(tenant_id,run_id,step_id,step_name)",
        ),
        indexes=(
            "idx_run_steps_tenant_run(tenant_id,run_id)",
            "idx_run_steps_tenant_run_order(tenant_id,run_id,step_order)",
        ),
    )


PROVIDERS = (
    "sec_edgar",
    "companies_house",
    "github",
    "fred",
    "finnhub",
    "fmp",
    "world_bank",
    "escwa_catalog",
    "escwa_ispar",
    "qatar_open_data",
    "hackernews",
    "gdelt",
    "patentsview",
    "wayback",
    "google_news_rss",
    "epo_open_patent",
    "semantic_scholar",
    "perplexity",
)
DEBATE_ROLES = (
    "advocate",
    "sanad_breaker",
    "contradiction_finder",
    "risk_officer",
    "arbiter",
)
ANALYSIS_ROLES = (
    "financial_agent",
    "market_agent",
    "technical_agent",
    "terms_agent",
    "team_agent",
    "risk_officer_agent",
    "historian_agent",
    "sector_specialist_agent",
)
LAYER2_ROLES = ("ic_advocate", "ic_challenger", "ic_arbiter")


def _sql_literals(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _ordered_columns(definitions: tuple[tuple[str, str], ...]) -> list[list[Any]]:
    return [
        [ordinal, column, definition] for ordinal, (column, definition) in enumerate(definitions)
    ]


def _future_fk(
    name: str,
    child: tuple[str, ...],
    parent_table: str,
    parent_constraint: str,
    parent: tuple[str, ...],
    support: str,
) -> list[Any]:
    return [
        name,
        list(child),
        parent_table,
        parent_constraint,
        list(parent),
        "NO ACTION",
        "NO ACTION",
        support,
    ]


def _future_unique(
    name: str, columns: tuple[str, ...], *, nulls_not_distinct: bool = False
) -> list[Any]:
    return [name, list(columns), nulls_not_distinct]


def _future_index(
    name: str,
    columns: tuple[str, ...],
    predicate: str | None = None,
    *,
    unique: bool = False,
) -> list[Any]:
    return [name, list(columns), predicate, unique]


def _future_rls(table: str) -> list[list[str]]:
    return [
        [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY {table}_tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
            f"TO PUBLIC USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})",
            f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC; GRANT "
            f"SELECT,INSERT,UPDATE,DELETE ON TABLE {table} TO <configured_app_role>",
        ]
    ]


NATIVE_AUTHORITIES = (
    (
        0,
        "truth-dashboard-native-v1",
        "methodology_truth_dashboard_rows",
        "dashboard_row_id",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
        41,
    ),
    (
        1,
        "company-identity-native-v1",
        "company_identity_outputs",
        "identity_output_id",
        "COMPANY_IDENTITY_OUTPUT",
        42,
    ),
    (
        2,
        "provider-outcome-native-v1",
        "enrichment_provider_outcomes",
        "provider_outcome_id",
        "ENRICHMENT_PROVIDER_OUTCOME",
        43,
    ),
    (3, "provider-fact-native-v1", "enrichment_normalized_facts", "fact_id", "ENRICHMENT_FACT", 44),
    (
        4,
        "graph-native-v1",
        "graph_projection_results",
        "graph_result_id",
        "GRAPH_PROJECTION_RESULT",
        45,
    ),
    (5, "rag-index-native-v1", "rag_index_results", "rag_index_result_id", "RAG_INDEX_RESULT", 46),
    (6, "rag-query-native-v1", "rag_query_results", "rag_query_result_id", "RAG_QUERY_RESULT", 47),
    (
        7,
        "conflict-plan-native-v1",
        "external_intelligence_conflict_plans",
        "conflict_plan_id",
        "CONFLICT_PLAN",
        48,
    ),
    (
        8,
        "conflict-execution-native-v1",
        "external_intelligence_conflict_executions",
        "conflict_execution_id",
        "CONFLICT_EXECUTION",
        49,
    ),
    (
        9,
        "conflict-finding-native-v1",
        "external_intelligence_conflict_findings",
        "conflict_finding_id",
        "CONFLICT_FINDING",
        50,
    ),
    (
        10,
        "vep-content-reference-native-v1",
        "validated_evidence_package_references",
        "vep_reference_id",
        "VEP_CONTENT_REFERENCE",
        51,
    ),
    (
        11,
        "debate-role-native-v1",
        "debate_role_outputs",
        "debate_role_output_id",
        "DEBATE_ROLE_OUTPUT",
        52,
    ),
    (
        12,
        "analysis-role-native-v1",
        "analysis_agent_outputs",
        "analysis_output_id",
        "ANALYSIS_AGENT_OUTPUT",
        53,
    ),
    (13, "scorecard-native-v1", "scoring_outputs", "scoring_output_id", "SCORING_OUTPUT", 54),
    (
        14,
        "layer2-readiness-native-v1",
        "layer2_readiness_outputs",
        "readiness_output_id",
        "LAYER2_READINESS_OUTPUT",
        55,
    ),
    (
        15,
        "layer2-role-native-v1",
        "layer2_role_outputs",
        "layer2_role_output_id",
        "LAYER2_ROLE_OUTPUT",
        56,
    ),
    (
        16,
        "generic-extraction-native-v1",
        "generic_extraction_outputs",
        "generic_output_id",
        "GENERIC_EXTRACTION_OUTPUT",
        57,
    ),
    (
        17,
        "grading-link-native-v1",
        "run_claim_grading_links",
        "grading_link_id",
        "RUN_CLAIM_GRADING_LINK",
        58,
    ),
)

NATIVE_PREFIXES = {
    "methodology_truth_dashboard_rows": "v2_methodology_truth_dashboard_rows",
    "company_identity_outputs": "v2_company_identity_outputs",
    "enrichment_provider_outcomes": "v2_enrichment_provider_outcomes",
    "enrichment_normalized_facts": "v2_enrichment_normalized_facts",
    "graph_projection_results": "v2_graph_projection_results",
    "rag_index_results": "v2_rag_index_results",
    "rag_query_results": "v2_rag_query_results",
    "external_intelligence_conflict_plans": "v2_conflict_plans",
    "external_intelligence_conflict_executions": "v2_conflict_executions",
    "external_intelligence_conflict_findings": "v2_conflict_findings",
    "validated_evidence_package_references": "v2_vep_content_references",
    "debate_role_outputs": "v2_debate_role_outputs",
    "analysis_agent_outputs": "v2_analysis_agent_outputs",
    "scoring_outputs": "v2_scoring_outputs",
    "layer2_readiness_outputs": "v2_layer2_readiness_outputs",
    "layer2_role_outputs": "v2_layer2_role_outputs",
    "generic_extraction_outputs": "v2_generic_extraction_outputs",
    "run_claim_grading_links": "v2_run_claim_grading_links",
}


def _hash_definition(column: str, *, nullable: bool = False) -> str:
    nullability = "NULL" if nullable else "NOT NULL"
    if nullable:
        return f"TEXT NULL CHECK ({column} IS NULL OR {column}~'^[0-9a-f]{{64}}$')"
    return f"TEXT {nullability} CHECK ({column}~'^[0-9a-f]{{64}}$')"


NATIVE_IMMUTABLE_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "methodology_truth_dashboard_rows": (
        ("coverage_record_id", "TEXT NOT NULL"),
        ("root_projection_sha256", _hash_definition("root_projection_sha256")),
        (
            "row_disposition",
            "TEXT NOT NULL CHECK (row_disposition IN ('ANSWERED','GAP','RED_FLAG'))",
        ),
        ("row_payload_sha256", _hash_definition("row_payload_sha256")),
    ),
    "company_identity_outputs": (
        ("identity_version_hash", _hash_definition("identity_version_hash")),
        ("legal_identity_sha256", _hash_definition("legal_identity_sha256")),
        ("verification_provenance_sha256", _hash_definition("verification_provenance_sha256")),
        ("identifier_projection_sha256", _hash_definition("identifier_projection_sha256")),
    ),
    "enrichment_provider_outcomes": (
        ("provider_id", f"TEXT NOT NULL CHECK (provider_id IN ({_sql_literals(PROVIDERS)}))"),
        ("outcome", "TEXT NOT NULL CHECK (outcome IN ('HIT','MISS','NOT_APPLICABLE'))"),
        ("identity_output_object_id", "UUID NOT NULL"),
        ("applicability_decision_id", "UUID NOT NULL"),
        ("rights_hash", _hash_definition("rights_hash")),
        ("budget_hash", _hash_definition("budget_hash")),
        ("probe_hash", _hash_definition("probe_hash")),
        ("request_hash", _hash_definition("request_hash")),
        ("provenance_hash", _hash_definition("provenance_hash")),
    ),
    "enrichment_normalized_facts": (
        ("provider_outcome_id", "UUID NOT NULL"),
        ("provider_id", f"TEXT NOT NULL CHECK (provider_id IN ({_sql_literals(PROVIDERS)}))"),
        ("required_outcome", "TEXT GENERATED ALWAYS AS ('HIT') STORED"),
        (
            "fact_domain",
            "TEXT NOT NULL CHECK (fact_domain IN ('commercial_diligence','financial_diligence'))",
        ),
        ("fact_sha256", _hash_definition("fact_sha256")),
        ("source_sha256", _hash_definition("source_sha256")),
        ("provenance_sha256", _hash_definition("provenance_sha256")),
    ),
    "graph_projection_results": (
        (
            "namespace",
            "TEXT NOT NULL CHECK (namespace IN ('internal_evidence','external_context'))",
        ),
        ("provider_id", "TEXT NULL"),
        ("fact_domain", "TEXT NULL"),
        ("projection_sha256", _hash_definition("projection_sha256")),
        ("query_sha256", _hash_definition("query_sha256")),
        ("result_sha256", _hash_definition("result_sha256")),
    ),
    "rag_index_results": (
        (
            "namespace",
            "TEXT NOT NULL CHECK (namespace IN ('internal_evidence','external_context'))",
        ),
        ("provider_id", "TEXT NULL"),
        ("fact_domain", "TEXT NULL"),
        ("embedding_model_hash", _hash_definition("embedding_model_hash")),
        ("index_sha256", _hash_definition("index_sha256")),
    ),
    "rag_query_results": (
        ("rag_index_result_id", "UUID NOT NULL"),
        (
            "namespace",
            "TEXT NOT NULL CHECK (namespace IN ('internal_evidence','external_context'))",
        ),
        ("provider_id", "TEXT NULL"),
        ("fact_domain", "TEXT NULL"),
        ("query_sha256", _hash_definition("query_sha256")),
        ("result_sha256", _hash_definition("result_sha256")),
    ),
    "external_intelligence_conflict_plans": (
        ("identity_output_object_id", "UUID NOT NULL"),
        ("check_kind", "TEXT NOT NULL"),
        ("subject_key", "TEXT NOT NULL"),
        ("plan_sha256", _hash_definition("plan_sha256")),
    ),
    "external_intelligence_conflict_executions": (
        ("conflict_plan_id", "UUID NOT NULL"),
        ("outcome", "TEXT NOT NULL CHECK (outcome IN ('SUCCEEDED','NO_FINDING','FINDING'))"),
        ("request_sha256", _hash_definition("request_sha256")),
        ("result_sha256", _hash_definition("result_sha256")),
        ("provenance_sha256", _hash_definition("provenance_sha256")),
    ),
    "external_intelligence_conflict_findings": (
        ("conflict_execution_id", "UUID NOT NULL"),
        (
            "evidence_class",
            "TEXT NOT NULL CHECK (evidence_class IN ('INTERNAL_EVIDENCE','EXTERNAL_CONTEXT'))",
        ),
        ("finding_sha256", _hash_definition("finding_sha256")),
    ),
    "validated_evidence_package_references": (
        ("validated_evidence_package_id", "UUID NOT NULL"),
        (
            "reference_kind",
            "TEXT NOT NULL CHECK (reference_kind IN ('CLAIM','EVIDENCE','SANAD','CALC','COURT_FINDING'))",
        ),
        ("producer_output_object_id", "UUID NOT NULL"),
        ("reference_sha256", _hash_definition("reference_sha256")),
    ),
    "debate_role_outputs": (
        ("debate_id", "UUID NOT NULL"),
        ("role", f"TEXT NOT NULL CHECK (role IN ({_sql_literals(DEBATE_ROLES)}))"),
        ("request_sha256", _hash_definition("request_sha256")),
        ("prompt_sha256", _hash_definition("prompt_sha256")),
        ("output_sha256", _hash_definition("output_sha256")),
        ("muhasabah_record_id", "UUID NOT NULL"),
        ("muhasabah_source_step", "VARCHAR(50) GENERATED ALWAYS AS ('DEBATE') STORED"),
        ("muhasabah_agent_id", "VARCHAR(255) GENERATED ALWAYS AS (role::varchar(255)) STORED"),
        (
            "muhasabah_output_id",
            "VARCHAR(255) GENERATED ALWAYS AS (debate_role_output_id::text) STORED",
        ),
        ("muhasabah_output_kind", "TEXT GENERATED ALWAYS AS ('DEBATE_MUHASABAH') STORED"),
    ),
    "analysis_agent_outputs": (
        ("role", f"TEXT NOT NULL CHECK (role IN ({_sql_literals(ANALYSIS_ROLES)}))"),
        ("request_sha256", _hash_definition("request_sha256")),
        ("prompt_sha256", _hash_definition("prompt_sha256")),
        ("report_sha256", _hash_definition("report_sha256")),
        ("muhasabah_record_id", "UUID NOT NULL"),
        ("muhasabah_source_step", "VARCHAR(50) GENERATED ALWAYS AS ('ANALYSIS') STORED"),
        ("muhasabah_agent_id", "VARCHAR(255) GENERATED ALWAYS AS (role::varchar(255)) STORED"),
        (
            "muhasabah_output_id",
            "VARCHAR(255) GENERATED ALWAYS AS (analysis_output_id::text) STORED",
        ),
        ("muhasabah_output_kind", "TEXT GENERATED ALWAYS AS ('ANALYSIS_MUHASABAH') STORED"),
    ),
    "scoring_outputs": (
        ("request_sha256", _hash_definition("request_sha256")),
        ("prompt_sha256", _hash_definition("prompt_sha256")),
        ("scorecard_sha256", _hash_definition("scorecard_sha256")),
        ("dimension_projection_sha256", _hash_definition("dimension_projection_sha256")),
    ),
    "layer2_readiness_outputs": (
        ("status", "TEXT NOT NULL CHECK (status='READY')"),
        ("required_projection_sha256", _hash_definition("required_projection_sha256")),
        ("observed_projection_sha256", _hash_definition("observed_projection_sha256")),
        ("blocker_count", "INTEGER NOT NULL CHECK (blocker_count=0)"),
        ("deferred_count", "INTEGER NOT NULL CHECK (deferred_count=0)"),
        ("default_count", "INTEGER NOT NULL CHECK (default_count=0)"),
    ),
    "layer2_role_outputs": (
        ("challenge_id", "UUID NOT NULL"),
        ("role", f"TEXT NOT NULL CHECK (role IN ({_sql_literals(LAYER2_ROLES)}))"),
        ("request_sha256", _hash_definition("request_sha256")),
        ("prompt_sha256", _hash_definition("prompt_sha256")),
        ("output_sha256", _hash_definition("output_sha256")),
        ("muhasabah_record_id", "UUID NOT NULL"),
        ("muhasabah_source_step", "VARCHAR(50) GENERATED ALWAYS AS ('LAYER2_IC_CHALLENGE') STORED"),
        ("muhasabah_agent_id", "VARCHAR(255) GENERATED ALWAYS AS (role::varchar(255)) STORED"),
        (
            "muhasabah_output_id",
            "VARCHAR(255) GENERATED ALWAYS AS (layer2_role_output_id::text) STORED",
        ),
        ("muhasabah_output_kind", "TEXT GENERATED ALWAYS AS ('LAYER2_MUHASABAH') STORED"),
    ),
    "generic_extraction_outputs": (
        ("rag_context_step_output_id", "UUID NOT NULL"),
        ("request_sha256", _hash_definition("request_sha256")),
        ("prompt_sha256", _hash_definition("prompt_sha256")),
        ("scope_sha256", _hash_definition("scope_sha256")),
        ("result_sha256", _hash_definition("result_sha256")),
    ),
    "run_claim_grading_links": (
        ("source_claim_output_object_id", "UUID NOT NULL"),
        ("source_claim_output_class", "TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED"),
        ("source_claim_output_kind", "TEXT GENERATED ALWAYS AS ('METHODOLOGY_CLAIM_LINK') STORED"),
        (
            "source_claim_step_name",
            "VARCHAR(100) NOT NULL CHECK (source_claim_step_name IN ('METHODOLOGY_CLAIM_MATERIALIZATION','EXTRACT'))",
        ),
        ("claim_id", "UUID NOT NULL"),
        ("methodology_sanad_link_id", "UUID NULL"),
        ("sanad_id", "UUID NOT NULL"),
        ("grade", "TEXT NOT NULL CHECK (grade IN ('A','B','C','D'))"),
        ("grading_sha256", _hash_definition("grading_sha256")),
    ),
}


NATIVE_CHILD_RELATIONS: dict[str, tuple[str, ...]] = {
    "methodology_truth_dashboard_rows": ("methodology_truth_dashboard_root_links",),
    "company_identity_outputs": (
        "company_identity_input_references",
        "company_identity_identifiers",
    ),
    "graph_projection_results": ("graph_result_input_references",),
    "rag_index_results": ("rag_index_input_references",),
    "rag_query_results": ("rag_query_input_references",),
    "external_intelligence_conflict_plans": ("conflict_input_references",),
    "debate_role_outputs": ("debate_role_input_references",),
    "analysis_agent_outputs": ("analysis_agent_input_references",),
    "scoring_outputs": ("scoring_input_references", "scoring_output_dimensions"),
    "layer2_readiness_outputs": ("layer2_readiness_input_references",),
    "layer2_role_outputs": ("layer2_role_input_references",),
    "generic_extraction_outputs": (
        "generic_extraction_input_references",
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
    ),
}


def _child_schema_id(table: str) -> str:
    return f"{table.replace('_', '-')}-v1"


def _native_specific_references(table: str) -> list[list[Any]]:
    rows: dict[
        str,
        tuple[tuple[str, tuple[str, ...], str, str, tuple[str, ...], str], ...],
    ] = {
        "methodology_truth_dashboard_rows": (
            (
                "fk_v2_dashboard_coverage",
                ("tenant_id", "deal_id", "run_id", "coverage_record_id"),
                "methodology_coverage_records",
                "uq_v2_coverage_dashboard_parent",
                ("tenant_id", "deal_id", "run_id", "coverage_record_id"),
                "ix_v2_dashboard_coverage",
            ),
        ),
        "enrichment_provider_outcomes": (
            (
                "fk_v2_provider_outcome_identity_root",
                ("tenant_id", "deal_id", "run_id", "identity_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_v2_provider_outcome_identity_root",
            ),
            (
                "fk_v2_provider_outcome_decision",
                ("tenant_id", "deal_id", "run_id", "applicability_decision_id"),
                "run_applicability_decisions",
                "uq_task7_run_applicability_decisions_scoped_id",
                ("tenant_id", "deal_id", "run_id", "applicability_decision_id"),
                "ix_v2_provider_outcome_decision",
            ),
        ),
        "enrichment_normalized_facts": (
            (
                "fk_v2_fact_outcome",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "provider_outcome_id",
                    "provider_id",
                    "required_outcome",
                ),
                "enrichment_provider_outcomes",
                "uq_v2_provider_outcome_fact_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "provider_outcome_id",
                    "provider_id",
                    "outcome",
                ),
                "ix_v2_fact_outcome",
            ),
        ),
        "rag_query_results": (
            (
                "fk_v2_rag_query_index",
                ("tenant_id", "deal_id", "run_id", "rag_index_result_id"),
                "rag_index_results",
                "uq_v2_rag_index_result_scope",
                ("tenant_id", "deal_id", "run_id", "rag_index_result_id"),
                "ix_v2_rag_query_index",
            ),
        ),
        "external_intelligence_conflict_plans": (
            (
                "fk_v2_conflict_plan_identity_root",
                ("tenant_id", "deal_id", "run_id", "identity_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_v2_conflict_plan_identity_root",
            ),
        ),
        "external_intelligence_conflict_executions": (
            (
                "fk_v2_conflict_execution_plan",
                ("tenant_id", "deal_id", "run_id", "conflict_plan_id"),
                "external_intelligence_conflict_plans",
                "uq_v2_conflict_plan_scope",
                ("tenant_id", "deal_id", "run_id", "conflict_plan_id"),
                "ix_v2_conflict_execution_plan",
            ),
        ),
        "external_intelligence_conflict_findings": (
            (
                "fk_v2_conflict_finding_execution",
                ("tenant_id", "deal_id", "run_id", "conflict_execution_id"),
                "external_intelligence_conflict_executions",
                "uq_v2_conflict_execution_scope",
                ("tenant_id", "deal_id", "run_id", "conflict_execution_id"),
                "ix_v2_conflict_finding_execution",
            ),
        ),
        "validated_evidence_package_references": (
            (
                "fk_v2_vep_content_package",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "validated_evidence_package_id",
                ),
                "validated_evidence_packages",
                "uq_v2_validated_evidence_package_scope",
                ("tenant_id", "deal_id", "run_id", "package_id"),
                "ix_v2_vep_content_package",
            ),
            (
                "fk_v2_vep_content_producer_root",
                ("tenant_id", "deal_id", "run_id", "producer_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                "ix_v2_vep_content_producer_root",
            ),
        ),
        "debate_role_outputs": (
            (
                "fk_v2_debate_output_session",
                ("tenant_id", "deal_id", "run_id", "debate_id"),
                "debate_sessions",
                "uq_v2_debate_session_scope",
                ("tenant_id", "deal_id", "run_id", "debate_id"),
                "ix_v2_debate_output_session",
            ),
            (
                "fk_v2_debate_output_muhasabah",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "muhasabah_record_id",
                    "muhasabah_source_step",
                    "muhasabah_agent_id",
                    "muhasabah_output_id",
                    "muhasabah_output_kind",
                ),
                "muhasabah_records",
                "uq_v2_muhasabah_debate_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "record_id",
                    "source_step",
                    "agent_id",
                    "output_id",
                    "run_output_object_kind",
                ),
                "ix_v2_debate_output_muhasabah_fk",
            ),
        ),
        "analysis_agent_outputs": (
            (
                "fk_v2_analysis_output_muhasabah",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "muhasabah_record_id",
                    "muhasabah_source_step",
                    "muhasabah_agent_id",
                    "muhasabah_output_id",
                    "muhasabah_output_kind",
                ),
                "muhasabah_records",
                "uq_v2_muhasabah_analysis_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "record_id",
                    "source_step",
                    "agent_id",
                    "output_id",
                    "run_output_object_kind",
                ),
                "ix_v2_analysis_output_muhasabah_fk",
            ),
        ),
        "layer2_role_outputs": (
            (
                "fk_v2_layer2_output_challenge",
                ("tenant_id", "deal_id", "run_id", "challenge_id"),
                "layer2_ic_challenges",
                "uq_v2_layer2_challenge_scope",
                ("tenant_id", "deal_id", "run_id", "challenge_id"),
                "ix_v2_layer2_output_challenge",
            ),
            (
                "fk_v2_layer2_output_muhasabah",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "muhasabah_record_id",
                    "muhasabah_source_step",
                    "muhasabah_agent_id",
                    "muhasabah_output_id",
                    "muhasabah_output_kind",
                ),
                "muhasabah_records",
                "uq_v2_muhasabah_layer2_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "record_id",
                    "source_step",
                    "agent_id",
                    "output_id",
                    "run_output_object_kind",
                ),
                "ix_v2_layer2_output_muhasabah_fk",
            ),
        ),
        "generic_extraction_outputs": (
            (
                "fk_v2_generic_output_rag_context",
                ("tenant_id", "deal_id", "run_id", "rag_context_step_output_id"),
                "run_step_outputs",
                "uq_task7_run_step_outputs_scoped_id",
                ("tenant_id", "deal_id", "run_id", "step_output_id"),
                "ix_v2_generic_output_rag_context",
            ),
        ),
        "run_claim_grading_links": (
            (
                "fk_v2_run_claim_grading_links_source_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "source_claim_output_object_id",
                    "source_claim_output_class",
                    "source_claim_output_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                "ix_v2_run_claim_grading_links_source_root",
            ),
            (
                "fk_v2_run_claim_grading_links_claim",
                ("tenant_id", "deal_id", "claim_id"),
                "claims",
                "ux_claims_task6_scope",
                ("tenant_id", "deal_id", "claim_id"),
                "ix_v2_run_claim_grading_links_claim",
            ),
            (
                "fk_v2_run_claim_grading_links_sanad",
                ("tenant_id", "deal_id", "sanad_id"),
                "sanads",
                "ux_sanads_task6_scope",
                ("tenant_id", "deal_id", "sanad_id"),
                "ix_v2_run_claim_grading_links_sanad",
            ),
            (
                "fk_v2_run_claim_grading_links_methodology_sanad",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "methodology_sanad_link_id",
                    "sanad_id",
                ),
                "methodology_result_sanad_links",
                "uq_v2_methodology_sanad_grading_parent",
                ("tenant_id", "deal_id", "run_id", "sanad_link_id", "sanad_id"),
                "ix_v2_run_claim_grading_links_methodology_sanad",
            ),
        ),
    }
    return [_future_fk(*spec) for spec in rows.get(table, ())]


def _native_specific_uniques(table: str) -> list[list[Any]]:
    rows: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
        "enrichment_provider_outcomes": (
            (
                "uq_v2_provider_outcome_fact_parent",
                ("tenant_id", "deal_id", "run_id", "provider_outcome_id", "provider_id", "outcome"),
            ),
            ("uq_v2_provider_outcome_provider", ("tenant_id", "run_id", "provider_id")),
        ),
        "enrichment_normalized_facts": (
            (
                "uq_v2_fact_coordinate",
                ("tenant_id", "run_id", "provider_id", "fact_domain", "fact_id"),
            ),
        ),
        "external_intelligence_conflict_plans": (
            ("uq_v2_conflict_plan_subject", ("tenant_id", "run_id", "check_kind", "subject_key")),
        ),
        "external_intelligence_conflict_executions": (
            ("uq_v2_conflict_execution_plan", ("tenant_id", "run_id", "conflict_plan_id")),
            (
                "uq_v2_conflict_execution_scope",
                ("tenant_id", "deal_id", "run_id", "conflict_execution_id"),
            ),
        ),
        "validated_evidence_package_references": (
            (
                "uq_v2_vep_content_coordinate",
                (
                    "tenant_id",
                    "run_id",
                    "validated_evidence_package_id",
                    "reference_kind",
                    "producer_output_object_id",
                ),
            ),
        ),
        "debate_role_outputs": (("uq_v2_debate_output_role", ("tenant_id", "run_id", "role")),),
        "analysis_agent_outputs": (
            ("uq_v2_analysis_output_role", ("tenant_id", "run_id", "role")),
            ("uq_v2_analysis_output_muhasabah", ("tenant_id", "run_id", "muhasabah_record_id")),
        ),
        "layer2_role_outputs": (
            ("uq_v2_layer2_output_role", ("tenant_id", "run_id", "challenge_id", "role")),
            ("uq_v2_layer2_output_muhasabah", ("tenant_id", "run_id", "muhasabah_record_id")),
        ),
        "run_claim_grading_links": (
            (
                "uq_v2_run_claim_grading_links_source_root",
                ("tenant_id", "run_id", "source_claim_output_object_id"),
            ),
        ),
    }
    return [_future_unique(name, columns) for name, columns in rows.get(table, ())]


def _native_checks(table: str, prefix: str) -> list[list[str]]:
    hash_columns = tuple(
        column for column, definition in NATIVE_IMMUTABLE_FIELDS[table] if "[0-9a-f]" in definition
    )
    checks: list[list[str]] = []
    if hash_columns:
        checks.append(
            [
                f"ck_{prefix}_hashes",
                " AND ".join(f"{column}~'^[0-9a-f]{{64}}$'" for column in hash_columns),
            ]
        )
    if table in {"graph_projection_results", "rag_index_results", "rag_query_results"}:
        checks.append(
            [
                f"ck_{prefix}_namespace",
                "(namespace='internal_evidence' AND provider_id IS NULL AND fact_domain IS NULL) OR "
                f"(namespace='external_context' AND provider_id IN ({_sql_literals(PROVIDERS)}) "
                "AND fact_domain IN ('commercial_diligence','financial_diligence'))",
            ]
        )
    checks.append([f"ck_{prefix}_lease", "lease_owner_id IS NOT NULL AND lease_fencing_token>0"])
    return checks


def expected_future_native_rows(*, dashboard_shape: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ordinal, schema_id, table, primary_key, output_kind, phase in NATIVE_AUTHORITIES:
        prefix = NATIVE_PREFIXES[table]
        scope = _ordered_columns(
            (
                ("tenant_id", "UUID NOT NULL"),
                ("deal_id", "UUID NOT NULL"),
                ("run_id", "UUID NOT NULL"),
                ("run_output_object_id", "UUID NOT NULL"),
                ("run_output_object_class", "TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED"),
                ("run_output_object_kind", f"TEXT GENERATED ALWAYS AS ('{output_kind}') STORED"),
                ("lease_owner_id", "UUID NOT NULL"),
                ("lease_fencing_token", "BIGINT NOT NULL"),
                ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()"),
            )
        )
        references = [
            _future_fk(
                f"fk_{prefix}_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                f"ix_{prefix}_run",
            ),
            _future_fk(
                f"fk_{prefix}_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                f"ix_{prefix}_root",
            ),
            *_native_specific_references(table),
        ]
        checks = _native_checks(table, prefix)
        if table == "methodology_truth_dashboard_rows":
            checks.insert(0, ["ck_v2_dashboard_row_shape", dashboard_shape])
        row: dict[str, Any] = {
            "authority_ordinal": ordinal,
            "schema_id": schema_id,
            "schema_version": "1",
            "planned_table": table,
            "primary_key": [[0, primary_key, "UUID NOT NULL"]],
            "scope_columns": scope,
            "immutable_fields": _ordered_columns(NATIVE_IMMUTABLE_FIELDS[table]),
            "check_tuple": checks,
            "reference_fields": references,
            "unique_tuple": [
                _future_unique(f"pk_{prefix}", (primary_key,)),
                _future_unique(f"uq_{prefix}_root", ("run_output_object_id",)),
                _future_unique(
                    f"uq_{prefix}_scope",
                    ("tenant_id", "deal_id", "run_id", primary_key),
                ),
                _future_unique(
                    f"uq_{prefix}_reference_parent",
                    (
                        "tenant_id",
                        "deal_id",
                        "run_id",
                        primary_key,
                        "run_output_object_id",
                    ),
                ),
                *_native_specific_uniques(table),
            ],
            "child_relation_tuple": [
                [child, _child_schema_id(child)] for child in NATIVE_CHILD_RELATIONS.get(table, ())
            ],
            "rls_tuple": _future_rls(table),
            "erasure_ordinal": phase,
            "subrole_field": (
                "provider_id"
                if table in {"enrichment_provider_outcomes", "enrichment_normalized_facts"}
                else "namespace"
                if table in {"graph_projection_results", "rag_index_results", "rag_query_results"}
                else "role"
                if table in {"debate_role_outputs", "analysis_agent_outputs", "layer2_role_outputs"}
                else None
            ),
            "namespace_field": (
                "namespace"
                if table in {"graph_projection_results", "rag_index_results", "rag_query_results"}
                else None
            ),
        }
        row["schema_sha256"] = authority_schema_sha256(row)
        rows.append(row)
    return rows
