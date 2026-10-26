"""Materialize the reviewed full-strict-v8 current-guard authority.

V7 is append-frozen and is accepted only at its pinned SHA-512.  V8 appends
the eleven normalized current-guard authority rows approved in section 20.11.
No runtime module, environment value, or database state participates in these
bytes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_V7_ARTIFACT_SHA512 = (
    "efbce2072b74f047739d7a7bf725dd38a4c7943b802f6e74c1c2886877120c666"
    "b64de2fd573aac6e4d5bcf111286472d60e7728c8bf4da172f308a318b6f1c7"
)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"

_SOURCE_EXACT_ONE_TARGETS = (
    ("source_lineage_objects", "ctrg_task7_source_lineage_objects_exact_one"),
    ("data_room_packages", "ctrg_task7_data_room_packages_source_exact_one"),
    (
        "data_room_package_files",
        "ctrg_task7_data_room_package_files_source_exact_one",
    ),
    ("document_artifacts", "ctrg_task7_document_artifacts_source_exact_one"),
    ("documents", "ctrg_task7_documents_source_exact_one"),
    (
        "document_parse_attempts",
        "ctrg_task7_document_parse_attempts_source_exact_one",
    ),
    ("document_spans", "ctrg_task7_document_spans_source_exact_one"),
)
_OUTPUT_EXACT_ONE_TARGETS = (
    ("run_output_objects", "ctrg_task7_run_output_objects_exact_one"),
    (
        "methodology_coverage_records",
        "ctrg_task7_methodology_coverage_records_output_exact_one",
    ),
    (
        "methodology_extraction_tasks",
        "ctrg_task7_methodology_extraction_tasks_output_exact_one",
    ),
    (
        "methodology_extraction_results",
        "ctrg_task7_methodology_extraction_results_output_exact_one",
    ),
    (
        "methodology_result_factual_outputs",
        "ctrg_task7_methodology_result_factual_outputs_output_exact_one",
    ),
    (
        "methodology_result_claim_links",
        "ctrg_task7_methodology_result_claim_links_output_exact_one",
    ),
    (
        "methodology_result_evidence_links",
        "ctrg_task7_methodology_result_evidence_links_output_exact_one",
    ),
    (
        "methodology_result_sanad_links",
        "ctrg_task7_methodology_result_sanad_links_output_exact_one",
    ),
    (
        "methodology_result_defect_links",
        "ctrg_task7_methodology_result_defect_links_output_exact_one",
    ),
    (
        "deterministic_calculations",
        "ctrg_task7_deterministic_calculations_output_exact_one",
    ),
    ("vector_embeddings", "ctrg_task7_vector_embeddings_output_exact_one"),
    (
        "evidence_trust_findings",
        "ctrg_task7_evidence_trust_findings_output_exact_one",
    ),
    ("muhasabah_records", "ctrg_task7_muhasabah_records_output_exact_one"),
    (
        "validated_evidence_packages",
        "ctrg_task7_validated_evidence_packages_output_exact_one",
    ),
    ("debate_sessions", "ctrg_task7_debate_sessions_output_exact_one"),
    (
        "layer2_ic_challenges",
        "ctrg_task7_layer2_ic_challenges_output_exact_one",
    ),
    ("layer2_ic_findings", "ctrg_task7_layer2_ic_findings_output_exact_one"),
    ("deliverables", "ctrg_task7_deliverables_output_exact_one"),
)
_RECEIPT_TARGETS = (
    ("run_steps", "ctrg_task7_run_steps_receipt_complete"),
    ("run_step_outputs", "ctrg_task7_run_step_outputs_receipt_complete"),
    ("run_output_objects", "ctrg_task7_run_output_objects_receipt_complete"),
    (
        "run_expected_consumer_edges",
        "ctrg_task7_expected_edges_decision_complete",
    ),
    (
        "run_consumer_receipts",
        "ctrg_task7_consumer_receipts_edge_complete",
    ),
    (
        "deliverable_manifest_references",
        "ctrg_task7_deliverable_manifest_references_exact_one",
    ),
    (
        "deliverable_manifest_references",
        "ctrg_task7_deliverable_manifest_references_exact_projection",
    ),
)
_INPUT_BINDING_TARGETS = (("run_input_bindings", "trg_task7_run_input_bindings_append_only"),)
_APPEND_TARGETS = (
    ("run_output_objects", "trg_task7_run_output_objects_append_only"),
    ("run_step_outputs", "trg_task7_run_step_outputs_append_only"),
    (
        "run_applicability_decisions",
        "trg_task7_run_applicability_decisions_append_only",
    ),
    (
        "run_expected_consumer_edges",
        "trg_task7_run_expected_consumer_edges_append_only",
    ),
    ("run_consumer_receipts", "trg_task7_run_consumer_receipts_append_only"),
    (
        "deliverable_manifest_references",
        "trg_task7_deliverable_manifest_references_append_only",
    ),
)
_SOURCE_BOUND_TARGETS = (
    ("data_room_packages", "trg_task7_data_room_packages_source_bound"),
    (
        "data_room_package_files",
        "trg_task7_data_room_package_files_source_bound",
    ),
    ("document_artifacts", "trg_task7_document_artifacts_source_bound"),
    ("documents", "trg_task7_documents_source_bound"),
    (
        "document_parse_attempts",
        "trg_task7_document_parse_attempts_source_bound",
    ),
    ("document_spans", "trg_task7_document_spans_source_bound"),
)
_OUTPUT_BOUND_TARGETS = tuple(
    (table, trigger.replace("_exact_one", "").replace("ctrg_", "trg_"))
    for table, trigger in _OUTPUT_EXACT_ONE_TARGETS[1:]
)
_OUTPUT_BOUND_TARGETS = tuple(
    (
        table,
        f"trg_task7_{table}_output_bound",
    )
    for table, _trigger in _OUTPUT_EXACT_ONE_TARGETS[1:]
)
_RUNS_TARGETS = (("runs", "trg_task7_runs_contract_immutable"),)
_RUN_STEPS_TARGETS = (("run_steps", "trg_task7_run_steps_contract_immutable"),)
_TASK5_TARGETS = (("methodology_coverage_records", "trg_methodology_coverage_scope"),)
_TASK6_TARGETS = (
    (
        "methodology_extraction_tasks",
        "trg_methodology_extraction_tasks_immutable",
    ),
    (
        "methodology_extraction_task_spans",
        "trg_methodology_extraction_task_spans_immutable",
    ),
    (
        "methodology_extraction_results",
        "trg_methodology_extraction_results_immutable",
    ),
    (
        "methodology_result_factual_outputs",
        "trg_methodology_result_factual_outputs_immutable",
    ),
    (
        "methodology_result_claim_links",
        "trg_methodology_result_claim_links_immutable",
    ),
    (
        "methodology_result_evidence_links",
        "trg_methodology_result_evidence_links_immutable",
    ),
    (
        "methodology_result_sanad_links",
        "trg_methodology_result_sanad_links_immutable",
    ),
    (
        "methodology_result_defect_links",
        "trg_methodology_result_defect_links_immutable",
    ),
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v8 materialization error."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _load_v7(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V7_ARTIFACT_SHA512:
        raise MaterializationError("TASK7_V8_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V8_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MaterializationError("TASK7_V8_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v7-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v7"
        or metadata.get("append_frozen") is not True
    ):
        raise MaterializationError("TASK7_V8_BASE_METADATA_INVALID")
    return raw, value


def _constraint_trigger_statements(
    targets: tuple[tuple[str, str], ...],
    function_name: str,
) -> tuple[str, ...]:
    return tuple(
        f"CREATE CONSTRAINT TRIGGER {trigger} AFTER INSERT OR UPDATE OR DELETE "
        f"ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE "
        f"FUNCTION public.{function_name}()"
        for table, trigger in targets
    )


def _before_trigger_statements(
    targets: tuple[tuple[str, str], ...],
    function_name: str,
    events: str,
) -> tuple[str, ...]:
    return tuple(
        f"CREATE TRIGGER {trigger} BEFORE {events} ON public.{table} FOR EACH "
        f"ROW EXECUTE FUNCTION public.{function_name}()"
        for table, trigger in targets
    )


_SOURCE_EXACT_ONE_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_source_exact_one_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
    "scope_tenant uuid; scope_deal uuid; BEGIN IF TG_OP='DELETE' THEN "
    "scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; ELSE "
    "scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; END IF; IF EXISTS "
    "(WITH roots AS (SELECT source_lineage_object_id AS root_id,object_kind,"
    "corpus_receipt_id FROM public.source_lineage_objects WHERE "
    "tenant_id=scope_tenant AND deal_id=scope_deal), natives AS (SELECT "
    "source_lineage_object_id AS root_id,source_lineage_object_kind AS "
    "object_kind,source_corpus_receipt_id AS corpus_receipt_id FROM "
    "public.data_room_packages WHERE tenant_id=scope_tenant AND "
    "deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT "
    "source_lineage_object_id,source_lineage_object_kind,"
    "source_corpus_receipt_id FROM public.data_room_package_files WHERE "
    "tenant_id=scope_tenant AND deal_id=scope_deal AND "
    "source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id,"
    "source_lineage_object_kind,source_corpus_receipt_id FROM "
    "public.document_artifacts WHERE tenant_id=scope_tenant AND "
    "deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT "
    "source_lineage_object_id,source_lineage_object_kind,"
    "source_corpus_receipt_id FROM public.documents WHERE "
    "tenant_id=scope_tenant AND deal_id=scope_deal AND "
    "source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id,"
    "source_lineage_object_kind,source_corpus_receipt_id FROM "
    "public.document_parse_attempts WHERE tenant_id=scope_tenant AND "
    "deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT "
    "source_lineage_object_id,source_lineage_object_kind,"
    "source_corpus_receipt_id FROM public.document_spans WHERE "
    "tenant_id=scope_tenant AND deal_id=scope_deal AND "
    "source_binding_state='BOUND'), census AS (SELECT "
    "COALESCE(roots.root_id,natives.root_id) AS root_id,"
    "count(roots.root_id) AS root_count,count(natives.root_id) AS native_count "
    "FROM roots FULL JOIN natives ON natives.root_id=roots.root_id AND "
    "natives.object_kind=roots.object_kind AND "
    "natives.corpus_receipt_id=roots.corpus_receipt_id GROUP BY "
    "COALESCE(roots.root_id,natives.root_id)) SELECT 1 FROM census WHERE "
    "root_count<>1 OR native_count<>1) THEN RAISE EXCEPTION "
    "'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; RETURN "
    "NULL; END$$"
)

_OUTPUT_NATIVE_UNION = (
    "SELECT tenant_id,deal_id,run_id,run_output_object_id AS root_id,"
    "run_output_object_kind AS output_kind FROM public.methodology_coverage_records "
    "WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,"
    "run_id,run_output_object_id,run_output_object_kind FROM "
    "public.methodology_extraction_tasks WHERE run_output_object_id IS NOT NULL "
    "UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.methodology_extraction_results WHERE "
    "run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,"
    "run_output_object_id,run_output_object_kind FROM "
    "public.methodology_result_factual_outputs WHERE run_output_object_id IS NOT "
    "NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.methodology_result_claim_links WHERE "
    "run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,"
    "run_output_object_id,run_output_object_kind FROM "
    "public.methodology_result_evidence_links WHERE run_output_object_id IS NOT "
    "NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.methodology_result_sanad_links WHERE "
    "run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,"
    "run_output_object_id,run_output_object_kind FROM "
    "public.methodology_result_defect_links WHERE run_output_object_id IS NOT "
    "NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.deterministic_calculations WHERE "
    "run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,"
    "run_output_object_id,run_output_object_kind FROM public.vector_embeddings "
    "WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,"
    "run_id,run_output_object_id,run_output_object_kind FROM "
    "public.evidence_trust_findings WHERE run_output_object_id IS NOT NULL UNION "
    "ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.muhasabah_records WHERE "
    "run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,"
    "run_output_object_id,run_output_object_kind FROM "
    "public.validated_evidence_packages WHERE run_output_object_id IS NOT NULL "
    "UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.debate_sessions WHERE "
    "run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,"
    "run_output_object_id,run_output_object_kind FROM public.layer2_ic_challenges "
    "WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,"
    "run_id,run_output_object_id,run_output_object_kind FROM "
    "public.layer2_ic_findings WHERE run_output_object_id IS NOT NULL UNION ALL "
    "SELECT tenant_id,deal_id,run_id,run_output_object_id,"
    "run_output_object_kind FROM public.deliverables WHERE "
    "run_output_object_id IS NOT NULL"
)
_OUTPUT_EXACT_ONE_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_output_exact_one_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
    "scope_tenant uuid; scope_deal uuid; scope_run uuid; BEGIN IF "
    "TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; "
    "scope_run:=OLD.run_id; ELSE scope_tenant:=NEW.tenant_id; "
    "scope_deal:=NEW.deal_id; scope_run:=NEW.run_id; END IF; IF EXISTS (WITH "
    "roots AS (SELECT output_object_id AS root_id,output_kind FROM "
    "public.run_output_objects WHERE tenant_id=scope_tenant AND "
    "deal_id=scope_deal AND run_id=scope_run AND output_class='NATIVE_ROW'), "
    "natives AS ("
    + _OUTPUT_NATIVE_UNION
    + "), scoped_natives AS (SELECT root_id,output_kind FROM natives WHERE "
    "tenant_id=scope_tenant AND deal_id=scope_deal AND run_id=scope_run), "
    "census AS (SELECT COALESCE(roots.root_id,scoped_natives.root_id) AS "
    "root_id,count(roots.root_id) AS root_count,count(scoped_natives.root_id) "
    "AS native_count FROM roots FULL JOIN scoped_natives ON "
    "scoped_natives.root_id=roots.root_id AND "
    "scoped_natives.output_kind=roots.output_kind GROUP BY "
    "COALESCE(roots.root_id,scoped_natives.root_id)) SELECT 1 FROM census WHERE "
    "root_count<>1 OR native_count<>1) THEN RAISE EXCEPTION "
    "'STEP_OUTPUT_MISSING' USING ERRCODE='23514'; END IF; RETURN "
    "NULL; END$$"
)

_RECEIPT_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_receipt_equality_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
    "scope_tenant uuid; scope_deal uuid; scope_run uuid; BEGIN IF "
    "TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; "
    "scope_run:=OLD.run_id; ELSE scope_tenant:=NEW.tenant_id; "
    "scope_deal:=NEW.deal_id; scope_run:=NEW.run_id; END IF; IF EXISTS (SELECT "
    "1 FROM public.run_steps s JOIN public.runs r ON r.tenant_id=s.tenant_id "
    "AND r.run_id=s.run_id WHERE s.tenant_id=scope_tenant AND "
    "r.deal_id=scope_deal AND s.run_id=scope_run AND "
    "r.pipeline_contract_version='full-strict-v8' AND s.status='COMPLETED' AND "
    "(SELECT count(*) FROM public.run_step_outputs so JOIN "
    "public.run_output_objects ro ON ro.tenant_id=so.tenant_id AND "
    "ro.deal_id=so.deal_id AND ro.run_id=so.run_id AND "
    "ro.output_object_id=so.output_object_id WHERE so.tenant_id=s.tenant_id "
    "AND so.run_id=s.run_id AND so.step_id=s.step_id AND "
    "so.subrole='__step__' AND ro.output_class='STEP_RECEIPT')<>1) OR EXISTS "
    "(SELECT 1 FROM public.run_output_objects ro WHERE ro.tenant_id=scope_tenant "
    "AND ro.deal_id=scope_deal AND ro.run_id=scope_run AND (SELECT count(*) "
    "FROM public.run_step_outputs so WHERE so.tenant_id=ro.tenant_id AND "
    "so.deal_id=ro.deal_id AND so.run_id=ro.run_id AND "
    "so.output_object_id=ro.output_object_id)<>1) OR EXISTS (SELECT 1 FROM "
    "public.run_expected_consumer_edges e JOIN public.run_steps s ON "
    "s.tenant_id=e.tenant_id AND s.run_id=e.run_id AND "
    "s.step_name=e.consumer_step_name WHERE e.tenant_id=scope_tenant AND "
    "e.deal_id=scope_deal AND e.run_id=scope_run AND s.status='COMPLETED' AND "
    "(SELECT count(*) FROM public.run_consumer_receipts c WHERE "
    "c.tenant_id=e.tenant_id AND c.deal_id=e.deal_id AND c.run_id=e.run_id AND "
    "c.expected_edge_id=e.expected_edge_id)<>1) OR EXISTS (SELECT 1 FROM "
    "public.run_consumer_receipts c JOIN public.run_expected_consumer_edges e "
    "ON e.tenant_id=c.tenant_id AND e.deal_id=c.deal_id AND e.run_id=c.run_id "
    "AND e.expected_edge_id=c.expected_edge_id WHERE c.tenant_id=scope_tenant "
    "AND c.deal_id=scope_deal AND c.run_id=scope_run AND "
    "(c.consumer_step_name,c.consumer_subrole) IS DISTINCT FROM "
    "(e.consumer_step_name,e.consumer_subrole)) OR EXISTS (SELECT 1 FROM "
    "public.deliverable_manifest_references m WHERE m.tenant_id=scope_tenant "
    "AND m.deal_id=scope_deal AND m.run_id=scope_run GROUP BY "
    "m.manifest_deliverable_id,m.manifest_output_object_id HAVING count(*)<>15 "
    "OR count(*) FILTER (WHERE member_kind='ARTIFACT')<>14 OR count(*) FILTER "
    "(WHERE member_kind='RECONCILIATION')<>1) THEN RAISE EXCEPTION "
    "'CONSUMER_RECEIPT_INCOMPLETE' USING ERRCODE='23514'; END IF; RETURN "
    "NULL; END$$"
)

_INPUT_BINDING_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_input_binding_admission_v1() "
    "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS "
    "$$DECLARE preimage text; expected_hash text; BEGIN IF TG_OP='DELETE' THEN "
    "IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,"
    "OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION "
    "'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; IF "
    "TG_OP<>'INSERT' THEN RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING "
    "ERRCODE='23514'; END IF; preimage:='[\"run-input-binding-digest-v2\",'||"
    "pg_catalog.to_json(normalize(NEW.tenant_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.deal_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.run_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.pipeline_contract_version,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.pipeline_contract_hash,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.corpus_receipt_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.source_lineage_object_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.source_object_kind,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.source_disposition,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.binding_purpose,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.selected_source_version,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.selected_identity_sha256,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.selected_content_sha256,NFC))::text||','||"
    "pg_catalog.to_json(normalize(NEW.corpus_receipt_sha256,NFC))::text||']'; "
    "expected_hash:=pg_catalog.encode(pg_catalog.sha256("
    "pg_catalog.convert_to(preimage,'UTF8')),'hex'); IF "
    "expected_hash<>NEW.binding_sha256 THEN RAISE EXCEPTION "
    "'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; IF NOT "
    "EXISTS (SELECT 1 FROM "
    "public.runs r JOIN public.strict_preflight_proofs p ON "
    "p.tenant_id=r.tenant_id AND p.deal_id=r.deal_id AND "
    "p.proof_id=r.strict_preflight_proof_id AND "
    "p.consumed_by_run_id=r.run_id JOIN public.data_room_packages pkg ON "
    "pkg.tenant_id=r.tenant_id AND pkg.deal_id=r.deal_id AND "
    "pkg.package_id=NEW.corpus_receipt_id JOIN public.source_lineage_objects "
    "package_root ON package_root.tenant_id=pkg.tenant_id AND "
    "package_root.deal_id=pkg.deal_id AND "
    "package_root.source_lineage_object_id=pkg.source_lineage_object_id AND "
    "package_root.object_kind='PACKAGE' JOIN public.source_lineage_objects "
    "selected_root ON selected_root.tenant_id=NEW.tenant_id AND "
    "selected_root.deal_id=NEW.deal_id AND "
    "selected_root.source_lineage_object_id=NEW.source_lineage_object_id AND "
    "selected_root.object_kind=NEW.source_object_kind WHERE "
    "r.tenant_id=NEW.tenant_id AND r.deal_id=NEW.deal_id AND "
    "r.run_id=NEW.run_id AND r.status='QUEUED' AND pkg.status='SEALED' AND "
    "pkg.source_binding_state='BOUND' AND "
    "package_root.corpus_receipt_id=NEW.corpus_receipt_id AND "
    "package_root.corpus_receipt_sha256=NEW.corpus_receipt_sha256 AND "
    "package_root.content_sha256=NEW.corpus_receipt_sha256 AND "
    "selected_root.corpus_receipt_id=NEW.corpus_receipt_id AND "
    "selected_root.corpus_receipt_sha256=NEW.corpus_receipt_sha256 AND "
    "p.corpus_hash=NEW.corpus_receipt_sha256 AND "
    "p.pipeline_contract_version=NEW.pipeline_contract_version AND "
    "p.pipeline_contract_hash=NEW.pipeline_contract_hash AND "
    "r.pipeline_contract_version=NEW.pipeline_contract_version AND "
    "r.pipeline_contract_hash=NEW.pipeline_contract_hash) THEN RAISE EXCEPTION "
    "'STRICT_PREFLIGHT_PROOF_MISMATCH' USING ERRCODE='23514'; END IF; "
    "RETURN NEW; END$$"
)

_APPEND_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_append_only_lease_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
    "scope_tenant uuid; scope_deal uuid; scope_run uuid; owner_id uuid; "
    "fencing_token bigint; BEGIN IF TG_OP='DELETE' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) "
    "THEN RETURN OLD; END IF; RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' "
    "USING ERRCODE='23514'; END IF; IF TG_OP<>'INSERT' THEN RAISE EXCEPTION "
    "'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; "
    "scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; "
    "scope_run:=NEW.run_id; IF TG_TABLE_NAME='run_output_objects' AND "
    "NEW.write_origin='BACKFILL' AND NEW.lease_owner_id IS NULL AND "
    "NEW.lease_fencing_token=0 THEN RETURN NEW; END IF; IF "
    "TG_TABLE_NAME='run_expected_consumer_edges' AND "
    "NEW.materialization_origin='ADMISSION' AND NEW.lease_owner_id IS NULL AND "
    "NEW.lease_fencing_token=0 AND EXISTS (SELECT 1 FROM public.runs r WHERE "
    "r.tenant_id=scope_tenant AND r.deal_id=scope_deal AND "
    "r.run_id=scope_run AND r.status='QUEUED') THEN RETURN NEW; END IF; BEGIN "
    "owner_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_owner_id',"
    "true),'')::uuid; fencing_token:=NULLIF(pg_catalog.current_setting("
    "'idis.worker_lease_fencing_token',true),'')::bigint; EXCEPTION WHEN "
    "OTHERS THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING "
    "ERRCODE='40001'; END; IF NEW.lease_owner_id IS DISTINCT FROM owner_id OR "
    "NEW.lease_fencing_token IS DISTINCT FROM fencing_token OR NOT EXISTS "
    "(SELECT 1 FROM public.runs r WHERE r.tenant_id=scope_tenant AND "
    "r.deal_id=scope_deal AND r.run_id=scope_run AND r.status='RUNNING' AND "
    "r.lease_owner_id=owner_id AND r.lease_fencing_token=fencing_token AND "
    "r.lease_expires_at>pg_catalog.statement_timestamp()) THEN RAISE EXCEPTION "
    "'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END$$"
)

_SOURCE_BOUND_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_source_bound_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF "
    "TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1("
    "OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION "
    "'TASK7_SOURCE_FREEZE_FENCED' USING ERRCODE='40001'; END IF; IF "
    "OLD.source_binding_state='BOUND' OR NEW.source_binding_state NOT IN "
    "('UNBOUND','BOUND') OR (OLD.source_binding_state='UNBOUND' AND "
    "NEW.source_binding_state='BOUND' AND (NEW.source_lineage_object_id IS NULL "
    "OR NEW.source_corpus_receipt_id IS NULL)) OR "
    "(OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' "
    "AND TG_TABLE_NAME='data_room_packages' AND NOT (OLD.status='OPEN' AND "
    "NEW.status='SEALED' AND (pg_catalog.to_jsonb(NEW)-ARRAY['status',"
    "'source_binding_state','source_lineage_object_id','updated_at']::text[]) "
    "IS NOT DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY['status',"
    "'source_binding_state','source_lineage_object_id','updated_at']::text[]))) "
    "OR (OLD.source_binding_state='UNBOUND' AND "
    "NEW.source_binding_state='BOUND' AND TG_TABLE_NAME='document_spans' AND "
    "(pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state',"
    "'source_lineage_object_id','source_corpus_receipt_id','parse_attempt_id']"
    "::text[]) IS DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY["
    "'source_binding_state','source_lineage_object_id',"
    "'source_corpus_receipt_id','parse_attempt_id']::text[])) OR "
    "(OLD.source_binding_state='UNBOUND' AND "
    "NEW.source_binding_state='BOUND' AND TG_TABLE_NAME NOT IN "
    "('data_room_packages','document_spans') AND "
    "(pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state',"
    "'source_lineage_object_id','source_corpus_receipt_id']::text[]) IS "
    "DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY['source_binding_state',"
    "'source_lineage_object_id','source_corpus_receipt_id']::text[])) THEN "
    "RAISE EXCEPTION 'TASK7_SOURCE_FREEZE_FENCED' USING ERRCODE='40001'; "
    "END IF; RETURN NEW; END$$"
)

_OUTPUT_BOUND_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_output_bound_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF "
    "TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1("
    "OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION "
    "'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; IF "
    "OLD.run_output_object_id IS NOT NULL AND "
    "(NEW.run_output_object_id,NEW.run_output_object_class,"
    "NEW.run_output_object_kind) IS DISTINCT FROM "
    "(OLD.run_output_object_id,OLD.run_output_object_class,"
    "OLD.run_output_object_kind) THEN RAISE EXCEPTION "
    "'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; RETURN NEW; "
    "END$$"
)

_RUNS_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_runs_contract_immutable_v1() "
    "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS "
    "$$BEGIN IF (NEW.pipeline_contract_version,NEW.pipeline_contract_hash,"
    "NEW.pipeline_resume_state,NEW.pipeline_resume_error_code) IS DISTINCT FROM "
    "(OLD.pipeline_contract_version,OLD.pipeline_contract_hash,"
    "OLD.pipeline_resume_state,OLD.pipeline_resume_error_code) THEN RAISE "
    "EXCEPTION 'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; "
    "RETURN NEW; END$$"
)
_RUN_STEPS_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() "
    "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS "
    "$$BEGIN IF (NEW.step_id,NEW.tenant_id,NEW.run_id,NEW.step_name,"
    "NEW.step_order,NEW.pipeline_contract_version,NEW.pipeline_contract_hash,"
    "NEW.semantic_rule_version,NEW.semantic_rule_hash) IS DISTINCT FROM "
    "(OLD.step_id,OLD.tenant_id,OLD.run_id,OLD.step_name,OLD.step_order,"
    "OLD.pipeline_contract_version,OLD.pipeline_contract_hash,"
    "OLD.semantic_rule_version,OLD.semantic_rule_hash) THEN RAISE EXCEPTION "
    "'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; RETURN "
    "NEW; END$$"
)

_TASK5_REPLACEMENT_FUNCTION = (
    "CREATE OR REPLACE FUNCTION public.idis_task5_coverage_scope_guard() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF "
    "TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1("
    "OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION "
    "'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END IF; IF "
    "TG_OP='UPDATE' AND (NEW.coverage_record_id IS DISTINCT FROM "
    "OLD.coverage_record_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR "
    "NEW.deal_id IS DISTINCT FROM OLD.deal_id OR NEW.run_id IS DISTINCT FROM "
    "OLD.run_id OR NEW.selection_id IS DISTINCT FROM OLD.selection_id OR "
    "NEW.methodology_version_record_id IS DISTINCT FROM "
    "OLD.methodology_version_record_id OR NEW.methodology_version_id IS "
    "DISTINCT FROM OLD.methodology_version_id OR "
    "NEW.methodology_question_record_id IS DISTINCT FROM "
    "OLD.methodology_question_record_id OR NEW.methodology_question_id IS "
    "DISTINCT FROM OLD.methodology_question_id OR NEW.methodology_type IS "
    "DISTINCT FROM OLD.methodology_type OR NEW.run_output_object_id IS DISTINCT "
    "FROM OLD.run_output_object_id OR NEW.created_at IS DISTINCT FROM "
    "OLD.created_at) THEN RAISE EXCEPTION "
    "'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END IF; IF "
    "TG_OP='INSERT' AND NEW.run_output_object_id IS NULL AND EXISTS (SELECT 1 "
    "FROM public.runs r WHERE r.tenant_id=NEW.tenant_id AND "
    "r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND "
    "r.pipeline_contract_version='full-strict-v8') THEN RAISE EXCEPTION "
    "'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE='23514'; END IF; IF "
    "NOT EXISTS (SELECT 1 FROM public.run_methodology_selections s JOIN "
    "public.methodology_questions q ON q.tenant_id=NEW.tenant_id AND "
    "q.methodology_version_record_id=NEW.methodology_version_record_id AND "
    "q.methodology_version_id=NEW.methodology_version_id AND "
    "q.methodology_question_record_id=NEW.methodology_question_record_id AND "
    "q.methodology_question_id=NEW.methodology_question_id AND "
    "q.methodology_type=NEW.methodology_type WHERE s.tenant_id=NEW.tenant_id "
    "AND s.deal_id=NEW.deal_id AND s.run_id=NEW.run_id AND "
    "s.selection_id=NEW.selection_id AND ((q.methodology_type='commercial_dd' "
    "AND q.methodology_version_record_id=s.commercial_dd_version_record_id) OR "
    "(q.methodology_type='financial_dd' AND "
    "q.methodology_version_record_id=s.financial_dd_version_record_id))) THEN "
    "RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING "
    "ERRCODE='23514'; END IF; NEW.updated_at:=pg_catalog.now(); RETURN NEW; END$$"
)

_TASK6_REPLACEMENT_FUNCTION = (
    "CREATE OR REPLACE FUNCTION public.idis_task6_immutable_guard() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF "
    "TG_OP='INSERT' THEN IF NOT public.idis_task6_current_lease_is_valid("
    "NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RAISE EXCEPTION "
    "'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END IF; "
    "IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1("
    "OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION "
    "'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING ERRCODE='23514'; END IF; "
    "IF TG_OP='UPDATE' AND TG_TABLE_NAME IN "
    "('methodology_extraction_results','methodology_result_factual_outputs') "
    "AND OLD.run_output_object_id IS NULL AND NEW.run_output_object_id IS NOT "
    "NULL AND (pg_catalog.to_jsonb(NEW)-ARRAY['run_output_object_id']::text[]) "
    "IS NOT DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY["
    "'run_output_object_id']::text[]) AND "
    "public.idis_task6_current_lease_is_valid(NEW.tenant_id,NEW.deal_id,"
    "NEW.run_id) AND EXISTS (SELECT 1 FROM public.run_steps s WHERE "
    "s.tenant_id=NEW.tenant_id AND s.run_id=NEW.run_id AND "
    "s.step_name='METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order=6 "
    "AND s.status='RUNNING') AND EXISTS (SELECT 1 FROM "
    "public.run_output_objects r WHERE r.tenant_id=NEW.tenant_id AND "
    "r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND "
    "r.output_object_id=NEW.run_output_object_id AND "
    "r.output_class=NEW.run_output_object_class AND "
    "r.output_kind=NEW.run_output_object_kind) THEN RETURN NEW; END IF; RAISE "
    "EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING "
    "ERRCODE='23514'; END$$"
)


def _row(
    *,
    ordinal: int,
    authority_id: str,
    authority_kind: str,
    targets: tuple[tuple[str, str], ...],
    parents: tuple[str, ...],
    statements: tuple[str, ...],
) -> dict[str, Any]:
    stem = authority_id.replace("-", "_")
    row: dict[str, Any] = {
        "authority_ordinal": ordinal,
        "authority_id": authority_id,
        "authority_version": "1",
        "authority_kind": authority_kind,
        "target_tuple": [list(value) for value in targets],
        "declared_parent_relation_tuple": list(parents),
        "body_sql_utf8_hex_tuple": [statement.encode("utf-8").hex() for statement in statements],
        "legal_red_case_tuple": [
            "tests/slice100_task7/"
            "test_full_strict_v8_current_guard_postgres_census_red.py::"
            f"test_{stem}_accepts_legal"
        ],
        "illegal_red_case_tuple": [
            "tests/slice100_task7/"
            "test_full_strict_v8_current_guard_postgres_census_red.py::"
            f"test_{stem}_rejects_illegal"
        ],
        "direct_sql_red_case_tuple": [
            "tests/slice100_task7/"
            "test_full_strict_v8_current_guard_postgres_census_red.py::"
            f"test_{stem}_rejects_direct_sql"
        ],
    }
    payload = dict(row)
    row["expected_sha256"] = _sha256(_ROW_HASH_DOMAIN + _canonical_bytes(payload))
    return row


def _authority_rows(first_ordinal: int) -> list[dict[str, Any]]:
    specs = (
        (
            "current-source-exact-one-v1",
            "CONSTRAINT_TRIGGER",
            _SOURCE_EXACT_ONE_TARGETS,
            (
                "source_lineage_objects",
                "data_room_packages",
                "data_room_package_files",
                "document_artifacts",
                "documents",
                "document_parse_attempts",
                "document_spans",
            ),
            (
                _SOURCE_EXACT_ONE_FUNCTION,
                *_constraint_trigger_statements(
                    _SOURCE_EXACT_ONE_TARGETS,
                    "idis_task7_current_source_exact_one_v1",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_source_exact_one_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-output-exact-one-v1",
            "CONSTRAINT_TRIGGER",
            _OUTPUT_EXACT_ONE_TARGETS,
            tuple(table for table, _trigger in _OUTPUT_EXACT_ONE_TARGETS),
            (
                _OUTPUT_EXACT_ONE_FUNCTION,
                *_constraint_trigger_statements(
                    _OUTPUT_EXACT_ONE_TARGETS,
                    "idis_task7_current_output_exact_one_v1",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_output_exact_one_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-receipt-equality-v1",
            "CONSTRAINT_TRIGGER",
            _RECEIPT_TARGETS,
            (
                "runs",
                "run_steps",
                "run_step_outputs",
                "run_output_objects",
                "run_expected_consumer_edges",
                "run_consumer_receipts",
                "deliverable_manifest_references",
            ),
            (
                _RECEIPT_FUNCTION,
                *_constraint_trigger_statements(
                    _RECEIPT_TARGETS,
                    "idis_task7_current_receipt_equality_v1",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_receipt_equality_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-input-binding-admission-v1",
            "APPEND_ONLY_TRIGGER",
            _INPUT_BINDING_TARGETS,
            (
                "runs",
                "strict_preflight_proofs",
                "data_room_packages",
                "source_lineage_objects",
            ),
            (
                _INPUT_BINDING_FUNCTION,
                *_before_trigger_statements(
                    _INPUT_BINDING_TARGETS,
                    "idis_task7_current_input_binding_admission_v1",
                    "INSERT OR UPDATE OR DELETE",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_input_binding_admission_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-append-only-lease-v1",
            "APPEND_ONLY_TRIGGER",
            _APPEND_TARGETS,
            ("runs",),
            (
                _APPEND_FUNCTION,
                *_before_trigger_statements(
                    _APPEND_TARGETS,
                    "idis_task7_current_append_only_lease_v1",
                    "INSERT OR UPDATE OR DELETE",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_append_only_lease_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-source-bound-v1",
            "BEFORE_ROW_TRIGGER",
            _SOURCE_BOUND_TARGETS,
            (),
            (
                _SOURCE_BOUND_FUNCTION,
                *_before_trigger_statements(
                    _SOURCE_BOUND_TARGETS,
                    "idis_task7_current_source_bound_v1",
                    "UPDATE OR DELETE",
                ),
                "REVOKE ALL ON FUNCTION public.idis_task7_current_source_bound_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-output-bound-v1",
            "BEFORE_ROW_TRIGGER",
            _OUTPUT_BOUND_TARGETS,
            (),
            (
                _OUTPUT_BOUND_FUNCTION,
                *_before_trigger_statements(
                    _OUTPUT_BOUND_TARGETS,
                    "idis_task7_current_output_bound_v1",
                    "UPDATE OR DELETE",
                ),
                "REVOKE ALL ON FUNCTION public.idis_task7_current_output_bound_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-runs-contract-immutable-v1",
            "BEFORE_ROW_TRIGGER",
            _RUNS_TARGETS,
            (),
            (
                _RUNS_FUNCTION,
                *_before_trigger_statements(
                    _RUNS_TARGETS,
                    "idis_task7_current_runs_contract_immutable_v1",
                    "UPDATE",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_runs_contract_immutable_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-run-steps-contract-immutable-v1",
            "BEFORE_ROW_TRIGGER",
            _RUN_STEPS_TARGETS,
            (),
            (
                _RUN_STEPS_FUNCTION,
                *_before_trigger_statements(
                    _RUN_STEPS_TARGETS,
                    "idis_task7_current_run_steps_contract_immutable_v1",
                    "UPDATE",
                ),
                "REVOKE ALL ON FUNCTION "
                "public.idis_task7_current_run_steps_contract_immutable_v1() FROM PUBLIC",
            ),
        ),
        (
            "current-task5-coverage-replacement-v1",
            "BEFORE_ROW_TRIGGER",
            _TASK5_TARGETS,
            (
                "runs",
                "run_methodology_selections",
                "methodology_questions",
            ),
            (_TASK5_REPLACEMENT_FUNCTION,),
        ),
        (
            "current-task6-immutable-replacement-v1",
            "BEFORE_ROW_TRIGGER",
            _TASK6_TARGETS,
            ("runs", "run_steps", "run_output_objects"),
            (_TASK6_REPLACEMENT_FUNCTION,),
        ),
    )
    return [
        _row(
            ordinal=first_ordinal + index,
            authority_id=authority_id,
            authority_kind=kind,
            targets=targets,
            parents=parents,
            statements=statements,
        )
        for index, (authority_id, kind, targets, parents, statements) in enumerate(specs)
    ]


def _transform_metadata(
    artifact: dict[str, Any],
    *,
    append_frozen: bool,
) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v7-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v7"],
    ]:
        raise MaterializationError("TASK7_V8_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = "full-strict-v8-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", "full-strict-v8"]
    design.append(
        [
            "current_guard_authority_v1",
            [
                ["deferred_target_count", 32],
                ["append_only_target_count", 7],
                ["bound_immutability_target_count", 25],
                ["retained_trigger_target_count", 9],
                [
                    "ordered_authority_ids",
                    [
                        row["authority_id"]
                        for row in artifact["sql_authority_registry"]["rows"][17:]
                    ],
                ],
                [
                    "ordered_target_partition",
                    [
                        row["target_tuple"]
                        for row in artifact["sql_authority_registry"]["rows"][17:]
                    ],
                ],
                [
                    "reviewed_downgrade_target_tuple",
                    [
                        target
                        for row in artifact["sql_authority_registry"]["rows"][17:]
                        for target in row["target_tuple"]
                    ],
                ],
                [
                    "current_0038_target_relation_tuple",
                    list(
                        dict.fromkeys(
                            target[0]
                            for row in artifact["sql_authority_registry"]["rows"][17:]
                            for target in row["target_tuple"]
                        )
                    ),
                ],
                ["legal_case_count", 73],
                ["illegal_case_count", 73],
                ["direct_sql_case_count", 73],
            ],
        ]
    )
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v8-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v8"
    metadata["append_frozen"] = append_frozen
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected = (
        '["pipeline-contract-v7","full-strict-v7",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    if recipes[5] != ["pipeline_contract_hash", expected]:
        raise MaterializationError("TASK7_V8_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        '["pipeline-contract-v8","full-strict-v8",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


def _validate_exact_diff(v7: dict[str, Any], v8: dict[str, Any]) -> None:
    if tuple(v8) != tuple(v7):
        raise MaterializationError("TASK7_V8_SECTION_ORDER_DRIFT")
    allowed = {"metadata", "digest_recipe_registry", "sql_authority_registry"}
    for section in v7:
        if section not in allowed and v8[section] != v7[section]:
            raise MaterializationError(f"TASK7_V8_UNAUTHORIZED_DRIFT:{section}")
    if v8["sql_authority_registry"]["rows"][:17] != (v7["sql_authority_registry"]["rows"]):
        raise MaterializationError("TASK7_V8_V7_AUTHORITY_DRIFT")
    new_rows = v8["sql_authority_registry"]["rows"][17:]
    if len(new_rows) != 11:
        raise MaterializationError("TASK7_V8_CURRENT_AUTHORITY_CENSUS_INVALID")
    targets = [tuple(target) for row in new_rows for target in row["target_tuple"]]
    if len(targets) != 73 or len(set(targets)) != 73:
        raise MaterializationError("TASK7_V8_CURRENT_TARGET_CLOSURE_INVALID")


def _build_v8(
    v7: dict[str, Any],
    *,
    append_frozen: bool = False,
) -> dict[str, Any]:
    artifact = copy.deepcopy(v7)
    section = artifact["sql_authority_registry"]
    rows = section["rows"]
    if len(rows) != 17:
        raise MaterializationError("TASK7_V8_V7_AUTHORITY_CENSUS_INVALID")
    rows.extend(_authority_rows(len(rows)))
    section["expected_sha256"] = _h([_REGISTRY_HASH_DOMAIN, rows])
    _transform_metadata(artifact, append_frozen=append_frozen)
    _validate_exact_diff(v7, artifact)
    return artifact


def _render(artifact: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise MaterializationError("TASK7_V8_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V8_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v7_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _raw, v7 = _load_v7(args.base_artifact)
        rendered = _render(_build_v8(v7))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V8_ARTIFACT_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
