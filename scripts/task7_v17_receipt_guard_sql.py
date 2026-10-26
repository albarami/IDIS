"""Derive the v17 receipt-equality guard from reviewed semantic registry bytes."""

from __future__ import annotations

from typing import Any


class ReceiptGuardSqlError(RuntimeError):
    """Stable failure for receipt-guard derivation drift."""


_SUBROLE_KINDS: dict[str, str] = {
    "EXTRACT": "EXTRACT_RAG_CONTEXT_RECEIPT_V1",
    "ENRICHMENT": "ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1",
    "GRAPH_EVIDENCE": "GRAPH_NAMESPACE_RECEIPT_V1",
    "RAG_EVIDENCE": "RAG_NAMESPACE_RECEIPT_V1",
    "DEBATE": "DEBATE_ROLE_RECEIPT_V1",
    "ANALYSIS": "ANALYSIS_AGENT_RECEIPT_V1",
    "LAYER2_IC_CHALLENGE": "LAYER2_ROLE_RECEIPT_V1",
}


def _literal(value: str) -> str:
    if "\x00" in value:
        raise ReceiptGuardSqlError("TASK7_V17_RECEIPT_LITERAL_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _semantic_fields(row: object) -> tuple[int, str, str, str, tuple[str, ...]]:
    if not isinstance(row, list) or len(row) != 5:
        raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_ROW_INVALID")
    ordinal, step_name, _preimage_hex, semantic_hash, raw_fields = row
    if (
        type(ordinal) is not int
        or not isinstance(step_name, str)
        or not isinstance(semantic_hash, str)
        or not isinstance(raw_fields, list)
    ):
        raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_ROW_INVALID")
    fields: dict[str, str] = {}
    for item in raw_fields:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
        ):
            raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_FIELD_INVALID")
        key, value = item
        if key in fields:
            raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_FIELD_DUPLICATE")
        fields[key] = value
    if (
        fields.get("step") != step_name
        or fields.get("order") != f"{ordinal:02d}"
        or not fields.get("aggregate")
        or "subroles" not in fields
        or len(semantic_hash) != 64
    ):
        raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_ROW_INVALID")
    subroles = () if fields.get("subroles") == "-" else tuple(fields["subroles"].split(","))
    if any(not value for value in subroles) or len(subroles) != len(set(subroles)):
        raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_SUBROLE_INVALID")
    return ordinal, step_name, fields["aggregate"], semantic_hash, subroles


def expected_receipt_rows(
    semantic_rows: object,
) -> tuple[tuple[str, str, str, int, str, str], ...]:
    """Project exact aggregate and subrole STEP_RECEIPT coordinates."""

    if not isinstance(semantic_rows, list) or len(semantic_rows) != 28:
        raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_CENSUS_INVALID")
    expected: list[tuple[str, str, str, int, str, str]] = []
    for expected_ordinal, raw_row in enumerate(semantic_rows):
        ordinal, step_name, aggregate_kind, semantic_hash, subroles = _semantic_fields(raw_row)
        if ordinal != expected_ordinal:
            raise ReceiptGuardSqlError("TASK7_V17_SEMANTIC_ORDER_INVALID")
        expected.append((step_name, "__step__", aggregate_kind, 0, "2", semantic_hash))
        for subrole in subroles:
            if step_name == "DELIVERABLES":
                kind = (
                    "DELIVERABLE_RECONCILIATION_RECEIPT_V1"
                    if subrole == "reconciliation"
                    else "DELIVERABLE_ARTIFACT_RECEIPT_V1"
                )
            else:
                try:
                    kind = _SUBROLE_KINDS[step_name]
                except KeyError as exc:
                    raise ReceiptGuardSqlError("TASK7_V17_SUBROLE_KIND_AUTHORITY_MISSING") from exc
            expected.append((step_name, subrole, kind, 0, "2", semantic_hash))
    return tuple(expected)


def _required_receipt_sql(semantic_rows: object) -> str:
    rows = expected_receipt_rows(semantic_rows)
    rendered = ",".join(
        "("
        + ",".join(
            (
                _literal(step_name),
                _literal(subrole),
                _literal(output_kind),
                str(output_ordinal),
                _literal(semantic_version),
                _literal(semantic_hash),
            )
        )
        + ")"
        for (
            step_name,
            subrole,
            output_kind,
            output_ordinal,
            semantic_version,
            semantic_hash,
        ) in rows
    )
    return (
        "OR EXISTS (WITH expected(step_name,subrole,output_kind,output_ordinal,"
        "semantic_rule_version,semantic_rule_hash) AS (VALUES "
        + rendered
        + "), required AS (SELECT s.tenant_id,r.deal_id,s.run_id,s.step_id,"
        "expected.step_name,expected.subrole,expected.output_kind,"
        "expected.output_ordinal,expected.semantic_rule_version,"
        "expected.semantic_rule_hash FROM public.run_steps s JOIN public.runs r "
        "ON r.tenant_id=s.tenant_id AND r.run_id=s.run_id JOIN expected "
        "ON expected.step_name=s.step_name WHERE s.tenant_id=scope_tenant "
        "AND s.run_id=scope_run AND r.deal_id=scope_deal "
        "AND r.pipeline_contract_version='full-strict-v17' "
        "AND r.pipeline_resume_state='RESUMABLE' "
        "AND s.status='COMPLETED'), actual AS (SELECT so.tenant_id,so.deal_id,"
        "so.run_id,so.step_id,so.step_name,so.subrole,so.output_kind,"
        "so.output_ordinal,so.semantic_rule_version,so.semantic_rule_hash "
        "FROM public.run_step_outputs so JOIN public.runs actual_run ON "
        "actual_run.tenant_id=so.tenant_id "
        "AND actual_run.deal_id=so.deal_id "
        "AND actual_run.run_id=so.run_id "
        "AND actual_run.pipeline_contract_version='full-strict-v17' "
        "AND actual_run.pipeline_resume_state='RESUMABLE' "
        "JOIN public.run_output_objects ro "
        "ON ro.tenant_id=so.tenant_id AND ro.deal_id=so.deal_id "
        "AND ro.run_id=so.run_id AND ro.output_object_id=so.output_object_id "
        "WHERE so.tenant_id=scope_tenant AND so.deal_id=scope_deal "
        "AND so.run_id=scope_run AND ro.output_class='STEP_RECEIPT' "
        "AND ro.output_kind=so.output_kind) SELECT 1 FROM required "
        "FULL JOIN actual ON actual.tenant_id=required.tenant_id "
        "AND actual.deal_id=required.deal_id AND actual.run_id=required.run_id "
        "AND actual.step_id=required.step_id "
        "AND actual.step_name=required.step_name "
        "AND actual.subrole=required.subrole "
        "AND actual.output_kind=required.output_kind "
        "AND actual.output_ordinal=required.output_ordinal "
        "AND actual.semantic_rule_version=required.semantic_rule_version "
        "AND actual.semantic_rule_hash=required.semantic_rule_hash "
        "WHERE required.step_id IS NULL OR actual.step_id IS NULL) "
    )


_CONSUMER_COORDINATE = (
    "OR EXISTS (SELECT 1 FROM public.run_consumer_receipts c JOIN "
    "public.run_expected_consumer_edges e ON e.tenant_id=c.tenant_id "
    "AND e.deal_id=c.deal_id AND e.run_id=c.run_id "
    "AND e.expected_edge_id=c.expected_edge_id WHERE c.tenant_id=scope_tenant "
    "AND c.deal_id=scope_deal AND c.run_id=scope_run "
    "AND (c.consumer_step_name,c.consumer_subrole) IS DISTINCT FROM "
    "(e.consumer_step_name,e.consumer_subrole)) "
)

_CONSUMER_CLASS = (
    "OR EXISTS (SELECT 1 FROM public.run_consumer_receipts c JOIN "
    "public.run_output_objects consumer_root ON "
    "consumer_root.tenant_id=c.tenant_id "
    "AND consumer_root.deal_id=c.deal_id "
    "AND consumer_root.run_id=c.run_id "
    "AND consumer_root.output_object_id=c.consumer_output_object_id "
    "WHERE c.tenant_id=scope_tenant AND c.deal_id=scope_deal "
    "AND c.run_id=scope_run "
    "AND consumer_root.output_class<>'STEP_RECEIPT') "
)

_AGGREGATE_SCOPE = "AND r.pipeline_contract_version='full-strict-v17' AND s.status='COMPLETED'"


def build_receipt_equality_statements(
    v16_row: dict[str, Any],
    semantic_rows: object,
) -> tuple[str, ...]:
    """Return row 19 with v17 version and exact receipt-set enforcement."""

    if v16_row.get("authority_id") != "current-receipt-equality-v1":
        raise ReceiptGuardSqlError("TASK7_V17_RECEIPT_AUTHORITY_INVALID")
    encoded = v16_row.get("body_sql_utf8_hex_tuple")
    if not isinstance(encoded, list) or len(encoded) != 11:
        raise ReceiptGuardSqlError("TASK7_V17_RECEIPT_AUTHORITY_SHAPE_INVALID")
    statements = [bytes.fromhex(value).decode("utf-8") for value in encoded]
    body = statements[2]
    if body.count("full-strict-v16") != 1 or "full-strict-v17" in body:
        raise ReceiptGuardSqlError("TASK7_V17_RECEIPT_VERSION_BASE_INVALID")
    body = body.replace("full-strict-v16", "full-strict-v17", 1)
    if body.count(_AGGREGATE_SCOPE) != 1:
        raise ReceiptGuardSqlError("TASK7_V17_RECEIPT_SCOPE_BASE_INVALID")
    body = body.replace(
        _AGGREGATE_SCOPE,
        "AND r.pipeline_contract_version='full-strict-v17' "
        "AND r.pipeline_resume_state='RESUMABLE' "
        "AND s.status='COMPLETED'",
        1,
    )
    if body.count(_CONSUMER_COORDINATE) != 1:
        raise ReceiptGuardSqlError("TASK7_V17_CONSUMER_COORDINATE_BASE_INVALID")
    body = body.replace(
        _CONSUMER_COORDINATE,
        _CONSUMER_COORDINATE + _CONSUMER_CLASS + _required_receipt_sql(semantic_rows),
        1,
    )
    statements[2] = body
    return tuple(statements)
