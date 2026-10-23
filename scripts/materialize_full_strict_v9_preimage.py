"""Materialize the unfrozen full-strict-v9 reviewed preimage candidate.

V8 never reached append-freeze.  It is accepted here only as the byte-for-byte
source authenticated by the reviewer-pinned SHA-512.  V9 appends four closed
current-native payload schemas and replaces bodies inside the eleven already
reviewed current-guard authority rows.  It does not add a registry, authority
row, table, environment variable, or flag.

The normal command always emits ``append_frozen=false``.  Freeze is a separate
admission transition performed only after the signed executable PostgreSQL
census proves all 73 legal, illegal, and direct-SQL cases plus the four real
constructor round trips.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_V8_SOURCE_SHA512 = (
    "e4a3f91cf753a62a548e7d1c1463f8bd6534c1a42a41656eb451857ec12aa367"
    "1dc87210813400cfc53b788c196aed79d94f0154708c64aa69a89e8f71aa6d2a"
)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_SCHEMA_REGISTRY_HASH_DOMAIN = "task7-schema-registry-v1"

_UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_SAFE_CODE_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$"
_SAFE_TOKEN_PATTERN = "^[A-Za-z0-9._-]{1,64}$"
_NONBLANK = {"type": "string", "minLength": 1}
_UUID = {"type": "string", "pattern": _UUID_PATTERN}
_COUNT = {"type": "integer", "minimum": 0}
_STRING_SET = {
    "type": "array",
    "items": _NONBLANK,
    "uniqueItems": True,
}
_COUNT_MAP = {
    "type": "object",
    "propertyNames": {"pattern": _SAFE_CODE_PATTERN},
    "additionalProperties": _COUNT,
}
_TOKEN_COUNT_MAP = {
    "type": "object",
    "propertyNames": {"pattern": _SAFE_TOKEN_PATTERN},
    "additionalProperties": _COUNT,
}
_NUMBER_MAP = {
    "type": "object",
    "propertyNames": {"pattern": _SAFE_TOKEN_PATTERN},
    "additionalProperties": {
        "type": "number",
        "minimum": 0,
        "x-idis-number-encoding": "binary64",
    },
}
_ID_SET_MAP = {
    "type": "object",
    "propertyNames": {"pattern": _SAFE_CODE_PATTERN},
    "additionalProperties": _STRING_SET,
}


class MaterializationError(RuntimeError):
    """Stable fail-closed v9 materialization error."""


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


def _closed_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _base_schema(schema_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_closed_object(properties),
        "x-idis-schema-id": schema_id,
        "x-idis-collection-contract": (
            "collection_paths supplies every SET, sorted-key map, and ORDERED semantic path"
        ),
    }


def _vep_schema() -> dict[str, Any]:
    safe_summary = _closed_object(
        {
            "claim_ids_by_disposition": _ID_SET_MAP,
            "evidence_ids": _STRING_SET,
            "source_span_ids": _STRING_SET,
            "sanad_ids": _STRING_SET,
            "defect_ids": _STRING_SET,
            "calc_ids": _STRING_SET,
            "finding_ids": _STRING_SET,
            "finding_types": {
                "type": "array",
                "items": {
                    "enum": [
                        "provenance",
                        "sanad_defect",
                        "contradiction",
                        "dashboard_consistency",
                        "muhasabah_gate",
                    ]
                },
                "uniqueItems": True,
            },
            "role_names": {
                "type": "array",
                "items": {
                    "enum": [
                        "advocate",
                        "sanad_breaker",
                        "contradiction_finder",
                        "risk_officer",
                        "arbiter",
                    ]
                },
                "uniqueItems": True,
            },
            "reason_codes": {
                "type": "array",
                "items": {"type": "string", "pattern": _SAFE_CODE_PATTERN},
                "uniqueItems": True,
            },
            "by_disposition": _COUNT_MAP,
            "by_grade": _COUNT_MAP,
            "by_dashboard_verdict": _COUNT_MAP,
            "by_finding_type": _COUNT_MAP,
            "by_reason": _COUNT_MAP,
        }
    )
    return _base_schema(
        "validated-evidence-package-native",
        {
            "tenant_id": _UUID,
            "deal_id": _UUID,
            "run_id": _UUID,
            "package_id": _UUID,
            "court_id": _UUID,
            "dashboard_id": _UUID,
            "status": {"const": "completed"},
            "safe_summary": safe_summary,
        },
    )


def _muhasabah_schema() -> dict[str, Any]:
    uncertainty = _closed_object(
        {
            "uncertainty": _NONBLANK,
            "impact": {"enum": ["HIGH", "MEDIUM", "LOW"]},
            "mitigation": _NONBLANK,
        }
    )
    return _base_schema(
        "muhasabah-native",
        {
            "tenant_id": _UUID,
            "deal_id": _UUID,
            "run_id": _UUID,
            "record_id": _UUID,
            "source_step": {
                "enum": [
                    "METHODOLOGY_EVIDENCE_TRUST_COURT",
                    "DEBATE",
                    "ANALYSIS",
                    "LAYER2_IC_CHALLENGE",
                ]
            },
            "agent_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "output_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "x-idis-number-encoding": "binary64",
            },
            "is_subjective": {"type": "boolean"},
            "supported_claim_ids": _STRING_SET,
            "supported_calc_ids": _STRING_SET,
            "uncertainties": {"type": "array", "items": uncertainty},
            "record_timestamp": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "format": "date-time",
                "pattern": (
                    "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
                    "[0-9]{2}:[0-9]{2}(?:\\.[0-9]{1,6})?(?:Z|[+-]00:00)$"
                ),
                "x-idis-canonical": "utc-rfc3339-micros",
            },
        },
    ) | {
        "x-idis-high-confidence-threshold-binary64": "3fe999999999999a",
        "x-idis-high-confidence-requires-uncertainty": True,
        "allOf": [
            {
                "if": {
                    "properties": {"is_subjective": {"const": False}},
                    "required": ["is_subjective"],
                },
                "then": {"properties": {"supported_claim_ids": {"minItems": 1}}},
            },
        ],
    }


def _layer2_schema() -> dict[str, Any]:
    stage_emphasis = _closed_object(
        {
            "stage": {"type": "string", "const": "SEED"},
            "emphasized_categories": {
                "type": "array",
                "items": {
                    "enum": [
                        "market_risk",
                        "team_risk",
                        "product_risk",
                        "traction_risk",
                        "thesis_fit_risk",
                        "capital_efficiency_risk",
                        "scalability_risk",
                        "execution_risk",
                    ]
                },
                "uniqueItems": True,
                "maxItems": 8,
            },
            "weighted_by_category": _NUMBER_MAP,
        }
    )
    safe_summary = _closed_object(
        {
            "claim_ids": _STRING_SET,
            "calc_ids": _STRING_SET,
            "graph_ref_ids": _STRING_SET,
            "rag_ref_ids": _STRING_SET,
            "enrichment_ref_ids": _STRING_SET,
            "finding_ids": _STRING_SET,
            "finding_count": _COUNT,
            "unresolved_question_count": _COUNT,
            "muhasabah_passed": {"type": "boolean"},
            "by_finding_type": _TOKEN_COUNT_MAP,
            "by_severity": _TOKEN_COUNT_MAP,
            "by_category": _TOKEN_COUNT_MAP,
            "stage_emphasis": stage_emphasis,
        }
    )
    return _base_schema(
        "layer2-challenge-native",
        {
            "tenant_id": _UUID,
            "deal_id": _UUID,
            "run_id": _UUID,
            "challenge_id": _UUID,
            "source_debate_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
            },
            "status": {"const": "completed"},
            "safe_summary": safe_summary,
        },
    )


def _debate_schema() -> dict[str, Any]:
    message = _closed_object(
        {
            "round_number": {"type": "integer", "minimum": 1, "maximum": 5},
            "message_ordinal": {"type": "integer", "minimum": 0},
            "message_id": _NONBLANK,
            "role": {
                "enum": [
                    "advocate",
                    "sanad_breaker",
                    "contradiction_finder",
                    "risk_officer",
                    "arbiter",
                ]
            },
            "claim_refs": _STRING_SET,
            "calc_refs": _STRING_SET,
        }
    )
    return _base_schema(
        "debate-session-native",
        {
            "debate_id": _UUID,
            "tenant_id": _UUID,
            "deal_id": _UUID,
            "run_id": _UUID,
            "protocol_version": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
            },
            "max_rounds": {"type": "integer", "minimum": 1, "maximum": 5},
            "rounds": {
                "type": "array",
                "minItems": 1,
                "items": message,
                "x-idis-unique-key": [
                    "round_number",
                    "message_ordinal",
                    "message_id",
                ],
            },
            "status": {"const": "SUCCEEDED"},
        },
    )


_SCHEMA_SPECS = (
    (
        "validated-evidence-package-native",
        "task7-validated-evidence-package-native-v1",
        "task7-current-native:validated_evidence_packages",
        _vep_schema,
        (
            ("/safe_summary/claim_ids_by_disposition", "SORTED_KEY_MAP<IdSet>"),
            ("/safe_summary/evidence_ids", "SET"),
            ("/safe_summary/source_span_ids", "SET"),
            ("/safe_summary/sanad_ids", "SET"),
            ("/safe_summary/defect_ids", "SET"),
            ("/safe_summary/calc_ids", "SET"),
            ("/safe_summary/finding_ids", "SET"),
            ("/safe_summary/finding_types", "SET"),
            ("/safe_summary/role_names", "SET"),
            ("/safe_summary/reason_codes", "SET"),
            ("/safe_summary/by_disposition", "SORTED_KEY_MAP<Count>"),
            ("/safe_summary/by_grade", "SORTED_KEY_MAP<Count>"),
            ("/safe_summary/by_dashboard_verdict", "SORTED_KEY_MAP<Count>"),
            ("/safe_summary/by_finding_type", "SORTED_KEY_MAP<Count>"),
            ("/safe_summary/by_reason", "SORTED_KEY_MAP<Count>"),
        ),
    ),
    (
        "muhasabah-native",
        "task7-muhasabah-native-v1",
        "task7-current-native:muhasabah_records",
        _muhasabah_schema,
        (
            ("/supported_claim_ids", "SET"),
            ("/supported_calc_ids", "SET"),
            ("/uncertainties", "ORDERED(uncertainty_ordinal)"),
        ),
    ),
    (
        "layer2-challenge-native",
        "task7-layer2-challenge-native-v1",
        "task7-current-native:layer2_ic_challenges",
        _layer2_schema,
        (
            ("/safe_summary/claim_ids", "SET"),
            ("/safe_summary/calc_ids", "SET"),
            ("/safe_summary/graph_ref_ids", "SET"),
            ("/safe_summary/rag_ref_ids", "SET"),
            ("/safe_summary/enrichment_ref_ids", "SET"),
            ("/safe_summary/finding_ids", "SET"),
            ("/safe_summary/by_finding_type", "SORTED_KEY_MAP<Count>"),
            ("/safe_summary/by_severity", "SORTED_KEY_MAP<Count>"),
            ("/safe_summary/by_category", "SORTED_KEY_MAP<Count>"),
            (
                "/safe_summary/stage_emphasis/emphasized_categories",
                "SET",
            ),
            (
                "/safe_summary/stage_emphasis/weighted_by_category",
                "SORTED_KEY_MAP<Binary64>",
            ),
        ),
    ),
    (
        "debate-session-native",
        "task7-debate-session-native-v1",
        "task7-current-native:debate_sessions",
        _debate_schema,
        (
            (
                "/rounds",
                "ORDERED(round_number,message_ordinal,message_id);"
                "message_id is not globally unique",
            ),
            ("/rounds/*/claim_refs", "SET"),
            ("/rounds/*/calc_refs", "SET"),
        ),
    ),
)


def _schema_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for schema_id, version, locator, builder, paths in _SCHEMA_SPECS:
        snapshot = builder()
        collection_paths = [list(value) for value in paths]
        row = {
            "schema_id": schema_id,
            "schema_version": version,
            "authority_locator": locator,
            "normalized_schema_snapshot": snapshot,
            "collection_paths": collection_paths,
            "origin": {
                "kind": "literal-validator",
                "authority": locator,
            },
        }
        row["expected_schema_sha256"] = _h(
            [
                "typed-schema-definition-v1",
                schema_id,
                version,
                locator,
                snapshot,
                collection_paths,
            ]
        )
        rows.append(row)
    return rows


_CANONICAL_JSONB_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_canonical_jsonb_text_v1(p_value jsonb) "
    "RETURNS text LANGUAGE plpgsql IMMUTABLE SET "
    "search_path=pg_catalog,pg_temp AS $$DECLARE kind text; rendered text; "
    "BEGIN kind:=pg_catalog.jsonb_typeof(p_value); IF kind='null' THEN RETURN "
    "'null'; ELSIF kind='boolean' OR kind='number' THEN RETURN p_value::text; "
    "ELSIF kind='string' THEN RETURN pg_catalog.to_json(normalize("
    "p_value#>>'{}',NFC))::text; ELSIF kind='array' THEN SELECT '['||"
    "COALESCE(pg_catalog.string_agg(public.idis_task7_canonical_jsonb_text_v1("
    "item),',' ORDER BY ordinal),'')||']' INTO rendered FROM "
    "pg_catalog.jsonb_array_elements(p_value) WITH ORDINALITY AS values("
    "item,ordinal); RETURN rendered; ELSIF kind='object' THEN SELECT '{'||"
    "COALESCE(pg_catalog.string_agg(pg_catalog.to_json(normalize(key,NFC))::text"
    "||':'||public.idis_task7_canonical_jsonb_text_v1(value),',' ORDER BY "
    "normalize(key,NFC) COLLATE \"C\"),'')||'}' INTO rendered FROM "
    "pg_catalog.jsonb_each(p_value); RETURN rendered; END IF; RAISE EXCEPTION "
    "'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END$$"
)
_HASH_JSONB_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_hash_jsonb_v1(p_value jsonb) RETURNS "
    "text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS "
    "$$SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
    "public.idis_task7_canonical_jsonb_text_v1(p_value),'UTF8')),'hex')$$"
)


def _native_hash_function(schema_hashes: dict[str, str]) -> str:
    """Return the static current-native dispatch authority.

    The complete per-table NK/P implementation is deliberately literal.  JSON
    payload rows include their frozen schema coordinate in ``P``.  No caller
    supplies a table name and no dynamic SQL is admitted.
    """

    vep_hash = schema_hashes["validated-evidence-package-native"]
    muhasabah_hash = schema_hashes["muhasabah-native"]
    layer2_hash = schema_hashes["layer2-challenge-native"]
    debate_hash = schema_hashes["debate-session-native"]
    branches = (
        "SELECT c.run_output_object_kind AS output_kind,"
        "pg_catalog.jsonb_build_array(c.coverage_record_id) AS nk,"
        "pg_catalog.jsonb_build_array(c.selection_id::text,"
        "c.methodology_version_record_id::text,c.methodology_version_id,"
        "c.methodology_question_record_id::text,c.methodology_question_id,"
        "c.methodology_type) AS payload FROM "
        "public.methodology_coverage_records c WHERE "
        "c.tenant_id=p_tenant_id AND c.deal_id=p_deal_id AND "
        "c.run_id=p_run_id AND c.run_output_object_id=p_output_object_id",
        "SELECT t.run_output_object_kind,pg_catalog.jsonb_build_array("
        "t.task_record_id::text),pg_catalog.jsonb_build_array("
        "t.selection_id::text,t.coverage_record_id,"
        "t.methodology_version_record_id::text,t.methodology_version_id,"
        "t.methodology_question_record_id::text,t.methodology_question_id,"
        "t.methodology_type,t.extraction_task_id,t.document_id::text,"
        "t.document_classification_id,t.document_classification_sha256,"
        "t.planner_contract_version,t.task_contract_version,"
        "t.task_input_sha256,t.candidate_span_set_sha256,"
        "COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array("
        "s.span_ordinal,s.task_span_record_id::text,s.document_id::text,"
        "s.span_id::text,s.content_sha256,s.locator_sha256) ORDER BY "
        "s.span_ordinal,s.task_span_record_id) FROM "
        "public.methodology_extraction_task_spans s WHERE "
        "s.tenant_id=t.tenant_id AND s.deal_id=t.deal_id AND "
        "s.run_id=t.run_id AND s.task_record_id=t.task_record_id),'[]'::jsonb)"
        ") FROM public.methodology_extraction_tasks t WHERE "
        "t.tenant_id=p_tenant_id AND t.deal_id=p_deal_id AND "
        "t.run_id=p_run_id AND t.run_output_object_id=p_output_object_id",
        "SELECT r.run_output_object_kind,pg_catalog.jsonb_build_array("
        "r.result_id::text),pg_catalog.jsonb_build_array("
        "r.task_record_id::text,r.attempt_id::text,r.task_input_sha256,"
        "r.outcome,r.result_sha256,to_jsonb(ARRAY(SELECT value::text FROM "
        "pg_catalog.unnest(r.searched_document_ids) value ORDER BY "
        'value::text COLLATE "C")),to_jsonb(ARRAY(SELECT value::text FROM '
        "pg_catalog.unnest(r.searched_span_ids) value ORDER BY "
        'value::text COLLATE "C")),r.searched_scope_sha256,'
        "r.provider_provenance_sha256,r.gap_finding) FROM "
        "public.methodology_extraction_results r WHERE "
        "r.tenant_id=p_tenant_id AND r.deal_id=p_deal_id AND "
        "r.run_id=p_run_id AND r.run_output_object_id=p_output_object_id",
        "SELECT f.run_output_object_kind,pg_catalog.jsonb_build_array("
        "f.factual_output_record_id::text),pg_catalog.jsonb_build_array("
        "f.result_id::text,f.task_record_id::text,f.output_ordinal,"
        "f.factual_output_id::text,f.typed_output_sha256,"
        "COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array("
        'span_id::text,locator_sha256) ORDER BY span_id::text COLLATE "C",'
        'locator_sha256 COLLATE "C") FROM pg_catalog.unnest('
        "f.accepted_source_span_ids,f.accepted_locator_sha256s) zipped("
        "span_id,locator_sha256)),'[]'::jsonb),"
        "f.accepted_locator_set_sha256) FROM "
        "public.methodology_result_factual_outputs f WHERE "
        "f.tenant_id=p_tenant_id AND f.deal_id=p_deal_id AND "
        "f.run_id=p_run_id AND f.run_output_object_id=p_output_object_id",
        "SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array("
        "l.claim_link_id::text),pg_catalog.jsonb_build_array("
        "l.result_id::text,l.factual_output_record_id::text,"
        "l.selection_id::text,l.coverage_record_id,"
        "l.methodology_version_record_id::text,"
        "l.methodology_question_record_id::text,l.claim_id::text,"
        "l.canonical_key_sha256,l.reused_existing_claim) FROM "
        "public.methodology_result_claim_links l WHERE "
        "l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND "
        "l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id",
        "SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array("
        "l.evidence_link_id::text),pg_catalog.jsonb_build_array("
        "l.result_id::text,l.factual_output_record_id::text,"
        "l.claim_link_id::text,l.evidence_id::text,l.document_id::text,"
        "l.source_span_id::text,l.locator_sha256) FROM "
        "public.methodology_result_evidence_links l WHERE "
        "l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND "
        "l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id",
        "SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array("
        "l.sanad_link_id::text),pg_catalog.jsonb_build_array("
        "l.result_id::text,l.factual_output_record_id::text,"
        "l.claim_link_id::text,l.sanad_id::text,"
        "l.primary_evidence_id::text) FROM "
        "public.methodology_result_sanad_links l WHERE "
        "l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND "
        "l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id",
        "SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array("
        "l.defect_link_id::text),pg_catalog.jsonb_build_array("
        "l.result_id::text,l.factual_output_record_id::text,"
        "l.sanad_link_id::text,l.defect_id::text) FROM "
        "public.methodology_result_defect_links l WHERE "
        "l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND "
        "l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id",
        "SELECT c.run_output_object_kind,pg_catalog.jsonb_build_array("
        "c.calc_id::text),pg_catalog.jsonb_build_array(c.calc_type,c.inputs,"
        "c.output,c.formula_hash,c.code_version,c.reproducibility_hash,"
        "(SELECT pg_catalog.jsonb_build_array(s.calc_sanad_id::text,"
        "COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY "
        'public.idis_task7_canonical_jsonb_text_v1(value) COLLATE "C") '
        "FROM pg_catalog.jsonb_array_elements(s.input_claim_ids) value),"
        "'[]'::jsonb),s.input_min_sanad_grade,s.calc_grade,s.explanation) "
        "FROM public.calc_sanads s WHERE s.tenant_id=c.tenant_id AND "
        "s.deal_id=c.deal_id AND s.run_id=c.run_id AND "
        "s.calc_id=c.calc_id)) FROM public.deterministic_calculations c "
        "WHERE c.tenant_id=p_tenant_id AND c.deal_id=p_deal_id AND "
        "c.run_id=p_run_id AND c.run_output_object_id=p_output_object_id",
        "SELECT v.run_output_object_kind,pg_catalog.jsonb_build_array("
        "v.embedding_id::text),pg_catalog.jsonb_build_array(v.source_type,"
        "v.source_id::text,v.embedding_model,v.embedding_dimensions,"
        "v.content_hash,pg_catalog.encode(pg_catalog.sha256("
        "public.vector_send(v.embedding)),'hex')) FROM "
        "public.vector_embeddings v WHERE v.tenant_id=p_tenant_id AND "
        "v.deal_id=p_deal_id AND v.run_id=p_run_id AND "
        "v.run_output_object_id=p_output_object_id",
        "SELECT f.run_output_object_kind,pg_catalog.jsonb_build_array("
        "f.tenant_id::text,f.run_id::text,f.finding_id),"
        "pg_catalog.jsonb_build_array(f.court_id::text,f.finding_type,"
        "f.claim_id,COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY "
        'public.idis_task7_canonical_jsonb_text_v1(value) COLLATE "C") '
        "FROM pg_catalog.jsonb_array_elements(f.evidence_ids) value),"
        "'[]'::jsonb),f.sanad_id::text,COALESCE((SELECT "
        "pg_catalog.jsonb_agg(value ORDER BY "
        'public.idis_task7_canonical_jsonb_text_v1(value) COLLATE "C") '
        "FROM pg_catalog.jsonb_array_elements(f.calc_ids) value),'[]'::jsonb),"
        "COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY "
        'public.idis_task7_canonical_jsonb_text_v1(value) COLLATE "C") '
        "FROM pg_catalog.jsonb_array_elements(f.defect_ids) value),"
        "'[]'::jsonb),COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY "
        'public.idis_task7_canonical_jsonb_text_v1(value) COLLATE "C") '
        "FROM pg_catalog.jsonb_array_elements(f.reason_codes) value),"
        "'[]'::jsonb)) FROM public.evidence_trust_findings f WHERE "
        "f.tenant_id=p_tenant_id AND f.deal_id=p_deal_id AND "
        "f.run_id=p_run_id AND f.run_output_object_id=p_output_object_id",
        "SELECT m.run_output_object_kind,pg_catalog.jsonb_build_array("
        "m.record_id::text),pg_catalog.jsonb_build_array('muhasabah-native',"
        "'task7-muhasabah-native-v1','"
        + muhasabah_hash
        + "',pg_catalog.jsonb_build_object('tenant_id',m.tenant_id::text,"
        "'deal_id',m.deal_id::text,'run_id',m.run_id::text,'record_id',"
        "m.record_id::text,'source_step',m.source_step,'agent_id',m.agent_id,"
        "'output_id',m.output_id,'confidence',m.confidence,'is_subjective',"
        "m.is_subjective,'supported_claim_ids',m.supported_claim_ids,"
        "'supported_calc_ids',m.supported_calc_ids,'uncertainties',"
        "m.uncertainties,'record_timestamp',m.record_timestamp)) FROM "
        "public.muhasabah_records m WHERE m.tenant_id=p_tenant_id AND "
        "m.deal_id=p_deal_id AND m.run_id=p_run_id AND "
        "m.run_output_object_id=p_output_object_id",
        "SELECT v.run_output_object_kind,pg_catalog.jsonb_build_array("
        "v.package_id::text),pg_catalog.jsonb_build_array("
        "'validated-evidence-package-native',"
        "'task7-validated-evidence-package-native-v1','"
        + vep_hash
        + "',pg_catalog.jsonb_build_object('tenant_id',v.tenant_id::text,"
        "'deal_id',v.deal_id::text,'run_id',v.run_id::text,'package_id',"
        "v.package_id::text,'court_id',v.court_id::text,'dashboard_id',"
        "v.dashboard_id::text,'status',v.status,'safe_summary',"
        "v.safe_summary)) FROM public.validated_evidence_packages v WHERE "
        "v.tenant_id=p_tenant_id AND v.deal_id=p_deal_id AND "
        "v.run_id=p_run_id AND v.run_output_object_id=p_output_object_id",
        "SELECT d.run_output_object_kind,pg_catalog.jsonb_build_array("
        "d.debate_id::text),pg_catalog.jsonb_build_array("
        "'debate-session-native','task7-debate-session-native-v1','"
        + debate_hash
        + "',pg_catalog.jsonb_build_object('debate_id',d.debate_id::text,"
        "'tenant_id',d.tenant_id::text,'deal_id',d.deal_id::text,'run_id',"
        "d.run_id::text,'protocol_version',d.protocol_version,'max_rounds',"
        "d.max_rounds,'rounds',d.rounds,'status',d.status)) FROM "
        "public.debate_sessions d WHERE d.tenant_id=p_tenant_id AND "
        "d.deal_id=p_deal_id AND d.run_id=p_run_id AND "
        "d.run_output_object_id=p_output_object_id",
        "SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array("
        "l.challenge_id::text),pg_catalog.jsonb_build_array("
        "'layer2-challenge-native','task7-layer2-challenge-native-v1','"
        + layer2_hash
        + "',pg_catalog.jsonb_build_object('tenant_id',l.tenant_id::text,"
        "'deal_id',l.deal_id::text,'run_id',l.run_id::text,'challenge_id',"
        "l.challenge_id::text,'source_debate_id',l.source_debate_id,"
        "'status',l.status,'safe_summary',l.safe_summary)) FROM "
        "public.layer2_ic_challenges l WHERE l.tenant_id=p_tenant_id AND "
        "l.deal_id=p_deal_id AND l.run_id=p_run_id AND "
        "l.run_output_object_id=p_output_object_id",
        "SELECT f.run_output_object_kind,pg_catalog.jsonb_build_array("
        "f.tenant_id::text,f.run_id::text,f.finding_id),"
        "pg_catalog.jsonb_build_array(f.challenge_id::text,f.finding_type,"
        "f.severity,f.category,f.supported_claim_ids,"
        "f.supported_calc_ids,f.graph_ref_ids,f.rag_ref_ids,"
        "f.enrichment_ref_ids) FROM public.layer2_ic_findings f WHERE "
        "f.tenant_id=p_tenant_id AND f.deal_id=p_deal_id AND "
        "f.run_id=p_run_id AND f.run_output_object_id=p_output_object_id",
        "SELECT d.run_output_object_kind,pg_catalog.jsonb_build_array("
        "d.deliverable_id::text),pg_catalog.jsonb_build_array("
        "d.deliverable_type,d.format,d.status,d.uri,d.content_type,"
        "d.size_bytes,d.content_sha256,d.uri_sha256,d.object_version_hash,"
        "d.download_proof_hash,d.catalog_ordinal) FROM public.deliverables d "
        "WHERE d.tenant_id=p_tenant_id AND d.deal_id=p_deal_id AND "
        "d.run_id=p_run_id AND d.run_output_object_id=p_output_object_id",
    )
    native_union = " UNION ALL ".join(branches)
    return (
        "CREATE FUNCTION public.idis_task7_current_native_hashes_v1("
        "p_tenant_id uuid,p_deal_id uuid,p_run_id uuid,p_output_object_id uuid) "
        "RETURNS TABLE(output_kind text,expected_native_identity_sha256 text,"
        "expected_native_output_sha256 text) LANGUAGE sql STABLE SET "
        "search_path=pg_catalog,pg_temp AS $$WITH roots AS (SELECT "
        "r.output_kind,r.pipeline_contract_version,r.pipeline_contract_hash "
        "FROM public.run_output_objects r WHERE r.tenant_id=p_tenant_id AND "
        "r.deal_id=p_deal_id AND r.run_id=p_run_id AND "
        "r.output_object_id=p_output_object_id AND r.output_class='NATIVE_ROW'),"
        "native_rows AS ("
        + native_union
        + "),resolved AS (SELECT r.output_kind,r.pipeline_contract_version,"
        "r.pipeline_contract_hash,n.nk,n.payload FROM roots r JOIN "
        "native_rows n ON n.output_kind=r.output_kind) SELECT output_kind,"
        "public.idis_task7_hash_jsonb_v1(pg_catalog.jsonb_build_array("
        "'native-identity-v1',p_tenant_id::text,p_deal_id::text,"
        "p_run_id::text,output_kind,nk)),public.idis_task7_hash_jsonb_v1("
        "pg_catalog.jsonb_build_array('native-output-v1',p_tenant_id::text,"
        "p_deal_id::text,p_run_id::text,pipeline_contract_version,"
        "pipeline_contract_hash,output_kind,nk,payload)) FROM resolved$$"
    )


_MANIFEST_REFERENCE_DIGEST_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_manifest_reference_digest_v1("
    "p_tenant_id uuid,p_deal_id uuid,p_run_id uuid,"
    "p_pipeline_contract_version text,p_pipeline_contract_hash text,"
    "p_manifest_deliverable_id uuid,p_member_kind text,"
    "p_catalog_ordinal integer,p_member_step_output_id uuid,"
    "p_member_output_object_id uuid,p_member_identity_sha256 text,"
    "p_member_value_sha256 text,p_member_content_sha256 text) RETURNS text "
    "LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS "
    "$$DECLARE preimage text; BEGIN preimage:='[\"deliverable-manifest-"
    "reference-digest-v1\",'||pg_catalog.to_json(normalize("
    "p_tenant_id::text,NFC))::text||','||pg_catalog.to_json(normalize("
    "p_deal_id::text,NFC))::text||','||pg_catalog.to_json(normalize("
    "p_run_id::text,NFC))::text||','||pg_catalog.to_json(normalize("
    "p_pipeline_contract_version,NFC))::text||','||pg_catalog.to_json("
    "normalize(p_pipeline_contract_hash,NFC))::text||','||pg_catalog.to_json("
    "normalize(p_manifest_deliverable_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(p_member_kind,NFC))::text||','||"
    "COALESCE(p_catalog_ordinal::text,'null')||','||pg_catalog.to_json("
    "normalize(p_member_step_output_id::text,NFC))::text||','||"
    "pg_catalog.to_json(normalize(p_member_output_object_id::text,NFC))::text"
    "||','||pg_catalog.to_json(normalize(p_member_identity_sha256,NFC))::text"
    "||','||pg_catalog.to_json(normalize(p_member_value_sha256,NFC))::text"
    "||','||CASE WHEN p_member_content_sha256 IS NULL THEN 'null' ELSE "
    "pg_catalog.to_json(normalize(p_member_content_sha256,NFC))::text END||']';"
    " RETURN pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
    "preimage,'UTF8')),'hex'); END$$"
)

_OUTPUT_BOUND_FUNCTION = (
    "CREATE FUNCTION public.idis_task7_current_output_bound_v1() RETURNS "
    "trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF "
    "TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1("
    "OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION "
    "'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; IF "
    "OLD.run_output_object_id IS NOT NULL AND pg_catalog.to_jsonb(NEW) IS "
    "DISTINCT FROM pg_catalog.to_jsonb(OLD) THEN RAISE EXCEPTION "
    "'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; RETURN NEW; END$$"
)


def _decode_statements(row: dict[str, Any]) -> list[str]:
    return [bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]]


def _encode_statements(statements: list[str]) -> list[str]:
    return [statement.encode("utf-8").hex() for statement in statements]


def _rehash_authority_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = _sha256(_ROW_HASH_DOMAIN + _canonical_bytes(payload))


def _replace_trigger_function(
    statements: list[str],
    function_name: str,
    replacement: str,
) -> list[str]:
    prefix = f"CREATE FUNCTION public.{function_name}() RETURNS "
    matches = [index for index, statement in enumerate(statements) if statement.startswith(prefix)]
    if len(matches) != 1:
        raise MaterializationError(f"TASK7_V9_CURRENT_GUARD_FUNCTION_MISSING:{function_name}")
    statements[matches[0]] = replacement
    return statements


def _bind_native_hash_guard(statement: str) -> str:
    marker = "RETURN NULL; END$$"
    if statement.count(marker) != 1:
        raise MaterializationError("TASK7_V9_CURRENT_OUTPUT_TRIGGER_SHAPE_INVALID")
    check = (
        "IF EXISTS (SELECT 1 FROM public.run_output_objects ro LEFT JOIN "
        "LATERAL public.idis_task7_current_native_hashes_v1(ro.tenant_id,"
        "ro.deal_id,ro.run_id,ro.output_object_id) expected ON true WHERE "
        "ro.tenant_id=scope_tenant AND ro.deal_id=scope_deal AND "
        "ro.run_id=scope_run AND ro.output_class='NATIVE_ROW' AND "
        "(expected.output_kind IS NULL OR expected.output_kind IS DISTINCT "
        "FROM ro.output_kind OR expected.expected_native_identity_sha256 IS "
        "DISTINCT FROM ro.native_identity_sha256 OR "
        "expected.expected_native_output_sha256 IS DISTINCT FROM "
        "ro.native_output_sha256)) THEN RAISE EXCEPTION "
        "'STEP_OUTPUT_MISSING' USING ERRCODE='23514'; END IF; "
    )
    return statement.replace(marker, check + marker)


def _bind_manifest_reference_guard(statement: str) -> str:
    marker = ") THEN RAISE EXCEPTION 'CONSUMER_RECEIPT_INCOMPLETE'"
    if statement.count(marker) != 1:
        raise MaterializationError("TASK7_V9_CURRENT_RECEIPT_TRIGGER_SHAPE_INVALID")
    check = (
        ") OR EXISTS (SELECT 1 FROM public.deliverable_manifest_references m "
        "JOIN public.runs r ON r.tenant_id=m.tenant_id AND "
        "r.deal_id=m.deal_id AND r.run_id=m.run_id JOIN "
        "public.run_output_objects member_root ON "
        "member_root.tenant_id=m.tenant_id AND "
        "member_root.deal_id=m.deal_id AND member_root.run_id=m.run_id AND "
        "member_root.output_object_id=m.member_output_object_id LEFT JOIN "
        "public.deliverables member_deliverable ON "
        "member_deliverable.tenant_id=m.tenant_id AND "
        "member_deliverable.deal_id=m.deal_id AND "
        "member_deliverable.run_id=m.run_id AND "
        "member_deliverable.run_output_object_id=m.member_output_object_id "
        "WHERE m.tenant_id=scope_tenant AND m.deal_id=scope_deal AND "
        "m.run_id=scope_run AND (m.member_identity_sha256 IS DISTINCT FROM "
        "member_root.native_identity_sha256 OR m.member_value_sha256 IS "
        "DISTINCT FROM member_root.native_output_sha256 OR "
        "(m.member_kind='ARTIFACT' AND (member_deliverable.deliverable_id IS "
        "NULL OR m.catalog_ordinal IS DISTINCT FROM "
        "member_deliverable.catalog_ordinal OR m.member_content_sha256 IS "
        "DISTINCT FROM member_deliverable.content_sha256)) OR "
        "(m.member_kind='RECONCILIATION' AND "
        "m.member_content_sha256 IS NOT NULL) OR m.reference_sha256 IS "
        "DISTINCT FROM public.idis_task7_manifest_reference_digest_v1("
        "m.tenant_id,m.deal_id,m.run_id,r.pipeline_contract_version,"
        "r.pipeline_contract_hash,m.manifest_deliverable_id,m.member_kind,"
        "m.catalog_ordinal,m.member_step_output_id,m.member_output_object_id,"
        "m.member_identity_sha256,m.member_value_sha256,"
        "m.member_content_sha256))"
    )
    return statement.replace(marker, check + marker)


def _remove_runtime_backfill_bypass(statement: str) -> str:
    bypass = (
        "IF TG_TABLE_NAME='run_output_objects' AND "
        "NEW.write_origin='BACKFILL' AND NEW.lease_owner_id IS NULL AND "
        "NEW.lease_fencing_token=0 THEN RETURN NEW; END IF; "
    )
    if statement.count(bypass) != 1:
        raise MaterializationError("TASK7_V9_RUNTIME_BACKFILL_BYPASS_SHAPE_INVALID")
    statement = statement.replace(bypass, "")
    old_admission = (
        "NEW.materialization_origin='ADMISSION' AND "
        "NEW.lease_owner_id IS NULL AND NEW.lease_fencing_token=0 AND "
        "EXISTS (SELECT 1 FROM public.runs r WHERE "
    )
    new_admission = (
        "NEW.materialization_origin='ADMISSION' AND "
        "NEW.contract_rule_id IN ('E001_SOURCE_INVENTORY_ADMISSION',"
        "'E002_SOURCE_HANDOFF_ADMISSION') AND "
        "NEW.usage_kind IN ('SOURCE_INVENTORY','SOURCE_HANDOFF') AND "
        "NEW.producer_input_binding_id IS NOT NULL AND "
        "NEW.producer_output_object_id IS NULL AND "
        "NEW.lease_owner_id IS NULL AND NEW.lease_fencing_token=0 AND "
        "EXISTS (SELECT 1 FROM public.runs r WHERE "
    )
    if statement.count(old_admission) != 1:
        raise MaterializationError("TASK7_V9_ADMISSION_BYPASS_SHAPE_INVALID")
    return statement.replace(old_admission, new_admission)


def _transform_current_authorities(
    artifact: dict[str, Any],
    schema_hashes: dict[str, str],
) -> None:
    rows = artifact["sql_authority_registry"]["rows"]
    current = {row["authority_id"]: row for row in rows[17:]}
    if len(current) != 11:
        raise MaterializationError("TASK7_V9_CURRENT_AUTHORITY_CENSUS_INVALID")

    output_row = current["current-output-exact-one-v1"]
    output_statements = _decode_statements(output_row)
    output_statements = [
        _bind_native_hash_guard(statement)
        if statement.startswith("CREATE FUNCTION public.idis_task7_current_output_exact_one_v1()")
        else statement
        for statement in output_statements
    ]
    output_statements[0:0] = [
        _CANONICAL_JSONB_FUNCTION,
        _HASH_JSONB_FUNCTION,
        _native_hash_function(schema_hashes),
        "REVOKE ALL ON FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION public.idis_task7_hash_jsonb_v1(jsonb) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION public.idis_task7_current_native_hashes_v1("
        "uuid,uuid,uuid,uuid) FROM PUBLIC",
    ]
    output_row["body_sql_utf8_hex_tuple"] = _encode_statements(
        statement.replace("full-strict-v8", "full-strict-v9") for statement in output_statements
    )

    receipt_row = current["current-receipt-equality-v1"]
    receipt_statements = _decode_statements(receipt_row)
    receipt_statements = [
        _bind_manifest_reference_guard(statement)
        if statement.startswith("CREATE FUNCTION public.idis_task7_current_receipt_equality_v1()")
        else statement
        for statement in receipt_statements
    ]
    receipt_statements[0:0] = [
        _MANIFEST_REFERENCE_DIGEST_FUNCTION,
        "REVOKE ALL ON FUNCTION "
        "public.idis_task7_manifest_reference_digest_v1("
        "uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text) "
        "FROM PUBLIC",
    ]
    receipt_row["body_sql_utf8_hex_tuple"] = _encode_statements(
        statement.replace("full-strict-v8", "full-strict-v9") for statement in receipt_statements
    )

    bound_row = current["current-output-bound-v1"]
    bound_statements = _replace_trigger_function(
        _decode_statements(bound_row),
        "idis_task7_current_output_bound_v1",
        _OUTPUT_BOUND_FUNCTION,
    )
    bound_row["body_sql_utf8_hex_tuple"] = _encode_statements(bound_statements)

    append_row = current["current-append-only-lease-v1"]
    append_statements = [
        _remove_runtime_backfill_bypass(statement)
        if statement.startswith("CREATE FUNCTION public.idis_task7_current_append_only_lease_v1()")
        else statement
        for statement in _decode_statements(append_row)
    ]
    append_row["body_sql_utf8_hex_tuple"] = _encode_statements(append_statements)

    for row in current.values():
        statements = _decode_statements(row)
        row["body_sql_utf8_hex_tuple"] = _encode_statements(
            [statement.replace("full-strict-v8", "full-strict-v9") for statement in statements]
        )
        _rehash_authority_row(row)
    artifact["sql_authority_registry"]["expected_sha256"] = _h([_SQL_REGISTRY_HASH_DOMAIN, rows])


def _transform_metadata(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v8-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v8"],
    ]:
        raise MaterializationError("TASK7_V9_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = "full-strict-v9-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", "full-strict-v9"]
    design.append(
        [
            "closed_current_native_payload_authority_v1",
            [
                [
                    "schema_coordinate_tuple",
                    [
                        [schema_id, version, locator]
                        for schema_id, version, locator, _builder, _paths in _SCHEMA_SPECS
                    ],
                ],
                [
                    "manifest_reference_digest_field_tuple",
                    [
                        "tenant_id",
                        "deal_id",
                        "run_id",
                        "pipeline_contract_version",
                        "pipeline_contract_hash",
                        "manifest_deliverable_id",
                        "member_kind",
                        "catalog_ordinal",
                        "member_step_output_id",
                        "member_output_object_id",
                        "member_identity_sha256",
                        "member_value_sha256",
                        "member_content_sha256",
                    ],
                ],
                ["current_guard_target_count", 73],
                ["constructor_backed_schema_proof_count", 4],
            ],
        ]
    )
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v9-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v9"
    metadata["append_frozen"] = False
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected = (
        '["pipeline-contract-v8","full-strict-v8",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    if recipes[5] != ["pipeline_contract_hash", expected]:
        raise MaterializationError("TASK7_V9_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        '["pipeline-contract-v9","full-strict-v9",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


def _validate_exact_diff(v8: dict[str, Any], v9: dict[str, Any]) -> None:
    if tuple(v9) != tuple(v8):
        raise MaterializationError("TASK7_V9_SECTION_ORDER_DRIFT")
    allowed = {
        "metadata",
        "digest_recipe_registry",
        "schema_registry",
        "sql_authority_registry",
    }
    for section in v8:
        if section not in allowed and v9[section] != v8[section]:
            raise MaterializationError(f"TASK7_V9_UNAUTHORIZED_DRIFT:{section}")

    old_schema_rows = v8["schema_registry"]["rows"]
    new_schema_rows = v9["schema_registry"]["rows"]
    if new_schema_rows[: len(old_schema_rows)] != old_schema_rows:
        raise MaterializationError("TASK7_V9_PRIOR_SCHEMA_DRIFT")
    if len(new_schema_rows) != len(old_schema_rows) + 4:
        raise MaterializationError("TASK7_V9_SCHEMA_DELTA_INVALID")

    old_authorities = v8["sql_authority_registry"]["rows"]
    new_authorities = v9["sql_authority_registry"]["rows"]
    if len(new_authorities) != len(old_authorities):
        raise MaterializationError("TASK7_V9_AUTHORITY_ROW_DELTA_FORBIDDEN")
    if new_authorities[:17] != old_authorities[:17]:
        raise MaterializationError("TASK7_V9_PRIOR_AUTHORITY_DRIFT")
    immutable_row_fields = (
        "authority_ordinal",
        "authority_id",
        "authority_version",
        "authority_kind",
        "target_tuple",
        "declared_parent_relation_tuple",
        "legal_red_case_tuple",
        "illegal_red_case_tuple",
        "direct_sql_red_case_tuple",
    )
    for old, new in zip(
        old_authorities[17:],
        new_authorities[17:],
        strict=True,
    ):
        if any(old[field] != new[field] for field in immutable_row_fields):
            raise MaterializationError(f"TASK7_V9_AUTHORITY_SHAPE_DRIFT:{old['authority_id']}")


def _load_v8(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V8_SOURCE_SHA512:
        raise MaterializationError("TASK7_V9_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V9_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MaterializationError("TASK7_V9_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v8-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v8"
        or metadata.get("append_frozen") is not False
    ):
        raise MaterializationError("TASK7_V9_BASE_METADATA_INVALID")
    return value


def build_v9(v8: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic unfrozen v9 candidate."""

    artifact = copy.deepcopy(v8)
    added = _schema_rows()
    artifact["schema_registry"]["rows"].extend(added)
    schema_projection = [
        [
            row["schema_id"],
            row["schema_version"],
            row["expected_schema_sha256"],
        ]
        for row in artifact["schema_registry"]["rows"]
    ]
    artifact["schema_registry"]["expected_sha256"] = _h(
        [_SCHEMA_REGISTRY_HASH_DOMAIN, schema_projection]
    )
    schema_hashes = {row["schema_id"]: row["expected_schema_sha256"] for row in added}
    _transform_current_authorities(artifact, schema_hashes)
    _transform_metadata(artifact)
    _validate_exact_diff(v8, artifact)
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
        raise MaterializationError("TASK7_V9_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V9_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v8_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        v8 = _load_v8(args.base_artifact)
        rendered = _render(build_v9(v8))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V9_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
