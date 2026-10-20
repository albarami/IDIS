"""Shared, synthetic-only fixtures for the Task 6 RED contract suite."""

from __future__ import annotations

import hashlib
import importlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.models.run_step import RunStep
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.runs import _resolve_run_contract_identity

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
SELECTION_ID = "44444444-4444-4444-8444-444444444444"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
TASK_RECORD_ID = "66666666-6666-4666-8666-666666666666"
ATTEMPT_ID = "77777777-7777-4777-8777-777777777777"
RESULT_ID = "88888888-8888-4888-8888-888888888888"
DOCUMENT_ID = "99999999-9999-4999-8999-999999999999"
SPAN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
QUESTION_ID = "mq_financial_dd_fictional_0001"
QUESTION_RECORD_ID = "16161616-1616-4161-8161-161616161616"
VERSION_ID = "financial_dd:frozen-v1"
TASK_ID = "met_fictional_0001"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
PRICE_CATALOG_ROW_SHA256 = "d2dcb26249b7be143bce3ba02d85dd501a95782cf7e006ade95790fe1240f321"
CANDIDATE_SOURCE_TEXT = "Fictional admitted source text."
CANDIDATE_CONTENT_SHA256 = hashlib.sha256(CANDIDATE_SOURCE_TEXT.encode("utf-8")).hexdigest()
FDD_SOURCE_BYTES = b"fictional-fdd-workbook-source-v1"
FDD_SOURCE_SHA256 = hashlib.sha256(FDD_SOURCE_BYTES).hexdigest()
CDD_SOURCE_BYTES = b"fictional-cdd-template-source-v1"
CDD_SOURCE_SHA256 = hashlib.sha256(CDD_SOURCE_BYTES).hexdigest()


def require_symbol(module_name: str, symbol_name: str) -> Any:
    """Load a wished-for Task 6 symbol without breaking test collection."""

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(f"Task 6 module {module_name!r} is missing: {exc}")
    symbol = getattr(module, symbol_name, None)
    assert symbol is not None, f"Task 6 symbol {module_name}.{symbol_name} is missing"
    return symbol


def candidate_span_kwargs() -> dict[str, Any]:
    return {
        "document_id": DOCUMENT_ID,
        "span_id": SPAN_ID,
        "ordinal": 1,
        "content_sha256": CANDIDATE_CONTENT_SHA256,
        "locator_sha256": SHA_B,
        "text": CANDIDATE_SOURCE_TEXT,
    }


def extractor_request_kwargs(*, candidate_spans: list[Any] | None = None) -> dict[str, Any]:
    if candidate_spans is None:
        candidate_cls = require_symbol(
            "idis.models.methodology_extraction", "MethodologyCandidateSpan"
        )
        candidate_spans = [candidate_cls(**candidate_span_kwargs())]
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "extraction_task_id": TASK_ID,
        "task_input_sha256": SHA_C,
        "methodology_type": "financial_dd",
        "methodology_version_id": VERSION_ID,
        "methodology_version_sha256": SHA_D,
        "methodology_question_id": QUESTION_ID,
        "methodology_question_record_id": QUESTION_RECORD_ID,
        "methodology_question_sha256": SHA_E,
        "source_trace": {
            "kind": "fdd_workbook_row",
            "sheet_name": "Fictional P&L",
            "row_number": 4,
            "anchor_cell": "A4",
            "source_sha256": FDD_SOURCE_SHA256,
        },
        "question_text": "What is the fictional recurring revenue?",
        "expected_answer_schema": {"answer_type": "numeric"},
        "required_evidence": [{"evidence_type": "fictional_support", "min_count": 1}],
        "candidate_spans": candidate_spans,
        "predicates": ["fictional_recurring_revenue"],
        "validation_requirements": ["cite_admitted_span"],
        "required_calculations": [{"calc_type": "fictional_bridge"}],
    }


def provider_receipt_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "provider": "anthropic",
        "backend": "live",
        "configured_model": "claude-sonnet-4-20250514",
        "observed_model": "claude-sonnet-4-20250514",
        "prompt_id": "METHODOLOGY_QUESTION_EXTRACTION_V1",
        "prompt_version": "1.0.0",
        "prompt_content_sha256": SHA_A,
        "prompt_input_schema_sha256": SHA_B,
        "prompt_output_schema_sha256": SHA_C,
        "request_payload_sha256": SHA_D,
        "provider_request_id": "req_fictional_0001",
        "provider_response_sha256": SHA_E,
        "real_call": True,
        "configured_max_tokens": 512,
        "configured_temperature": "0",
        "configured_json_mode": True,
        "input_tokens": 120,
        "output_tokens": 40,
        "stop_reason": "tool_use",
        "budget_policy_id": "budget-policy-fixture-v1",
        "budget_component": "anthropic",
        "budget_reservation_id": str(uuid.UUID(ATTEMPT_ID)),
        "billable_payload_sha256": SHA_C,
        "billable_payload_utf8_bytes": 512,
        "price_catalog_version": "anthropic-standard-price-catalog-2026-07-20-v1",
        "price_catalog_row_sha256": PRICE_CATALOG_ROW_SHA256,
        "input_usd_per_million_tokens": "3.00000000",
        "output_usd_per_million_tokens": "15.00000000",
        "reserved_calls": 1,
        "reserved_input_tokens": 512,
        "reserved_output_tokens": 512,
        "reserved_embedding_tokens": 0,
        "reserved_usd": "0.00921600",
        "budget_terminal_state": "CONSUMED",
        "receipt_sha256": SHA_F,
    }
    values.update(overrides)
    return values


def hermetic_provider_receipt_kwargs(**overrides: Any) -> dict[str, Any]:
    values = provider_receipt_kwargs(
        backend="hermetic",
        provider_request_id="hermetic_req_fictional_0001",
        real_call=False,
        budget_terminal_state="NOT_BILLABLE",
        reserved_calls=0,
        reserved_input_tokens=0,
        reserved_output_tokens=0,
        reserved_usd="0.00000000",
        input_tokens=0,
        output_tokens=0,
    )
    values.update(overrides)
    return values


def factual_output_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "subject": "Fictional Company",
        "methodology_question_record_id": QUESTION_RECORD_ID,
        "predicate": "fictional_recurring_revenue",
        "claim_class": "FINANCIAL_METRIC",
        "value": {"type": "monetary", "amount": "1200.00", "currency": "USD"},
        "period": {"kind": "as_of", "date": "2026-06-30"},
        "unit": "USD",
        "claim_text": "Fictional recurring revenue was USD 1,200.",
        "accepted_source_span_ids": [SPAN_ID],
        "accepted_locator_sha256s": [SHA_B],
    }
    values.update(overrides)
    return values


def default_searched_scope_sha256() -> str:
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    compute = require_symbol(
        "idis.models.methodology_extraction",
        "compute_methodology_searched_scope_sha256",
    )
    return compute((candidate_cls(**candidate_span_kwargs()),))


def terminal_response_kwargs(
    *, outcome: str = "EVIDENCE_FOUND", receipt_overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    receipt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyProviderReceipt")
    receipt = provider_receipt_kwargs(**dict(receipt_overrides or {}))
    values: dict[str, Any] = {
        "outcome": outcome,
        "searched_document_ids": [DOCUMENT_ID],
        "searched_span_ids": [SPAN_ID],
        "searched_scope_sha256": default_searched_scope_sha256(),
        "provider_receipt": receipt_cls(**receipt),
    }
    if outcome == "EVIDENCE_FOUND":
        output_cls = require_symbol(
            "idis.models.methodology_extraction", "MethodologyFactualOutput"
        )
        values["factual_outputs"] = [output_cls(**factual_output_kwargs())]
        values["gap_finding"] = None
    else:
        values["factual_outputs"] = []
        values["gap_finding"] = {
            "code": "GOVERNED_EVIDENCE_GAP",
            "finding": "The admitted fictional scope contains no responsive evidence.",
        }
    return values


# Current production head the Task 6 repository SQL reads and writes.
TASK6_SCHEMA_HEAD: Final = "0038"

# Columns 0038 adds. Values come from the production writers, not from literals here.
RUN_CONTRACT_COLUMN_SQL: Final = (
    "pipeline_contract_version, pipeline_contract_hash, "
    "pipeline_resume_state, pipeline_resume_error_code"
)
RUN_CONTRACT_VALUE_SQL: Final = (
    ":pipeline_contract_version, :pipeline_contract_hash, "
    ":pipeline_resume_state, :pipeline_resume_error_code"
)
STEP_CONTRACT_COLUMN_SQL: Final = (
    "pipeline_contract_version, pipeline_contract_hash, semantic_rule_version, semantic_rule_hash"
)
STEP_CONTRACT_VALUE_SQL: Final = (
    ":step_pipeline_contract_version, :step_pipeline_contract_hash, "
    ":semantic_rule_version, :semantic_rule_hash"
)
SOURCE_BINDING_COLUMN_SQL: Final = (
    "source_binding_state, source_lineage_object_id, source_corpus_receipt_id"
)
SOURCE_BINDING_VALUE_SQL: Final = ":source_binding_state, NULL, NULL"

# Staging vocabulary of ck_task7_*_binding_state. Production document inserts
# write this state with both lineage ids null (documents.py create_*).
_UNBOUND_SOURCE_BINDING_STATE: Final = "UNBOUND"


def schema_head_installed(connection: Connection) -> bool:
    """Return whether Alembic on this connection is at the Task 6 schema head."""
    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return str(revision) == TASK6_SCHEMA_HEAD


def insert_list_suffix(sql: str, *, required: bool) -> str:
    """Return a leading-comma column or value list, empty when the revision lacks it."""
    if not required:
        return ""
    return ", " + sql


def legacy_run_contract_params(*, mode: str) -> dict[str, str | None]:
    """Return the contract identity ``PostgresRunsRepository.create`` writes.

    Callers that omit the explicit contract tuple take the compatibility branch:
    ``snapshot-v1`` for SNAPSHOT and ``full-v1`` otherwise, the legacy contract
    hash, ``NON_RESUMABLE``, and ``LEGACY_RUN_RESUME_UNSAFE``. That is also the
    predicate ``claim_queued_run_for_execution`` requires. The hash is 64
    lowercase hex (``ck_task7_runs_pipeline_contract_hash``).
    """
    version, contract_hash, resume_state, resume_error = _resolve_run_contract_identity(
        mode=mode,
        pipeline_contract_version=None,
        pipeline_contract_hash=None,
        pipeline_resume_state=None,
        pipeline_resume_error_code=None,
    )
    return {
        "pipeline_contract_version": version,
        "pipeline_contract_hash": contract_hash,
        "pipeline_resume_state": resume_state,
        "pipeline_resume_error_code": resume_error,
    }


def legacy_step_contract_params() -> dict[str, str]:
    """Return the contract identity a default ``RunStep`` persists.

    ``RunStep`` field defaults are what the orchestrator writes for a non-strict
    step. The step version is ``legacy-opaque-v1``, which is not the run's
    ``full-v1`` / ``snapshot-v1``. Semantic version and hash match
    ``contract_registry`` legacy steps. Both hashes are 64 lowercase hex.
    """
    fields = RunStep.model_fields
    return {
        "step_pipeline_contract_version": str(fields["pipeline_contract_version"].default),
        "step_pipeline_contract_hash": str(fields["pipeline_contract_hash"].default),
        "semantic_rule_version": str(fields["semantic_rule_version"].default),
        "semantic_rule_hash": str(fields["semantic_rule_hash"].default),
    }


def unbound_source_binding_params() -> dict[str, str]:
    """Return the staging binding ``PostgresDocumentsRepository`` inserts.

    ``source_binding_state`` is ``UNBOUND`` and both lineage ids stay null, which
    is the unbound arm of ``ck_task7_document_artifacts_binding_state``,
    ``ck_task7_documents_binding_state``, and
    ``ck_task7_document_spans_binding_state`` (spans also leave
    ``parse_attempt_id`` null). The generated ``source_lineage_object_kind``
    column is not inserted.
    """
    return {"source_binding_state": _UNBOUND_SOURCE_BINDING_STATE}


def set_seed_tenant(connection: Connection, tenant_id: str) -> None:
    """Set transaction-local ``idis.tenant_id`` for definer-owned guards.

    Migration 0038 guard functions are ``SECURITY DEFINER`` and owned by the
    NOLOGIN non-superuser ``idis_task7_guard_function_owner``. They read
    RLS tables with this setting. Admin SQL still bypasses RLS; the guard
    does not. Equivalent to ``set_tenant_local``.
    """

    set_tenant_local(connection, str(tenant_id))


def canonical_locator_sha256(locator: Mapping[str, Any]) -> str:
    """Return the locator digest ``validate_live_source_span_identity`` recomputes."""

    encoded = json.dumps(
        dict(locator),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def bind_frozen_run_source(
    connection: Connection,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str | None,
    document_ids: Sequence[str],
    span_rows: Sequence[Any] = (),
) -> Any:
    """Freeze one production source package on the caller's open transaction.

    Inputs are the tenant, deal, optional run, the document ids that must be
    bound, and optional span rows (``FrozenSpanSeed``). A span row replaces
    one unbound span's type, locator, text, and content hash. Locators must
    already be planner-valid; this function does not invent them.

    Output is a ``FrozenRunSourceIds`` value: package id, file entry ids,
    parse attempt ids, manifest sha256, and document ids.

    Stages the package and files through ``PostgresDataRoomPackagesRepository``
    and the parse attempt through ``PostgresDocumentsRepository
    .create_runtime_parse_attempt``, then calls
    ``PostgresTask7SourceFreezeRepository.observe_source_package`` and
    ``freeze_observed_package`` on this connection. Does not commit. When
    ``run_id`` is set, stores ``RunSource(type="deal_documents")`` on
    ``runs.source``. When every selected span is already BOUND to one package,
    returns those ids without writing another package.     Seeds that never reach
    ``require_frozen_run_source`` stay on the unbound insert path.
    The body is ``_bound_source``; span staging and the OPEN package are
    ``_bound_source_stage``.
    """

    from tests.slice100_task6._bound_source import bind_frozen_run_source as _bind

    return _bind(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        document_ids=document_ids,
        span_rows=span_rows,
    )


def task6_head_seed_params(*, mode: str = "FULL", include_step: bool = False) -> dict[str, Any]:
    """Return bind values for a head-schema run seed, plus a step when requested."""
    params: dict[str, Any] = {
        **legacy_run_contract_params(mode=mode),
        **unbound_source_binding_params(),
    }
    if include_step:
        params.update(legacy_step_contract_params())
    return params


def corrupt_durable_rows_out_of_band(
    connection: Connection,
    statement: str,
    params: Mapping[str, Any] | None = None,
    *,
    table: str = "document_spans",
) -> None:
    """Apply one restore-style write, then turn the table's guards back on.

    ``trg_task7_document_spans_source_bound`` executes
    ``idis_task7_current_source_bound_v1`` and raises
    ``TASK7_SOURCE_FREEZE_FENCED`` on any ordinary update of a BOUND span.
    A content or locator change that gets past that trigger is still rejected
    at commit by ``ctrg_task7_document_spans_source_exact_one``
    (``idis_task7_current_source_exact_one_v1``). That pending event also
    makes ``ENABLE TRIGGER`` fail in the same transaction, so both triggers
    are disabled for this statement only. ``session_replication_role = replica``
    would also skip foreign keys and every other table, so the named triggers
    are the narrower switch. ``run_steps`` names only
    ``ctrg_task7_run_steps_receipt_complete``
    (``idis_task7_current_receipt_equality_v1``), which raises
    ``CONSUMER_RECEIPT_INCOMPLETE`` at commit. It is disabled before the
    statement, so that deferred event is never queued.
    ``trg_task7_run_steps_contract_immutable`` stays on: ``result_summary``
    is not one of the columns it compares. The caller commits, then runs
    production code with the triggers firing. The body is ``_out_of_band``.
    """

    from tests.slice100_task6._out_of_band import (
        corrupt_durable_rows_out_of_band as _corrupt,
    )

    _corrupt(connection, statement, params, table=table)


def insert_bound_extra_span(
    connection: Connection,
    *,
    document_id: str,
    span_id: str,
) -> None:
    """Insert one extra BOUND span that the 0038 census can accept.

    ``trg_task7_document_spans_source_bound`` is BEFORE UPDATE OR DELETE, so
    it does not block this INSERT. The deferred exact-one census does. The
    new root id is the id ``idis_task7_source_projection_v1`` computes for
    the new span, using the document's existing bound span's package, parse
    attempt, and manifest hash. Tenant context is set from that document
    before the write. The body is ``_out_of_band``.
    """

    from tests.slice100_task6._out_of_band import insert_bound_extra_span as _insert

    _insert(connection, document_id=document_id, span_id=span_id)
