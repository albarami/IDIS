"""Executable PostgreSQL contracts for the admitted Task-7 v7 SQL authorities.

Every test in this module installs the reviewed full-strict-v7 *literal* SQL bytes into
a minimal disposable PostgreSQL database and exercises the authority through ordinary
SQL. The fixtures intentionally do not reproduce the constraints being tested.

The final section-20.3 grading, erasure, and child-projection rulings are exercised
here with the same literal-authority installation path as the earlier guards.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import struct
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import DisposablePostgres, disposable_postgres
from tests.slice100_task7._postgres_support import (
    provision_exact_erasure_principals,
)

from idis.methodology.models import MethodologyQuestion
from idis.models.methodology_extraction import (
    MethodologyFactualOutput,
    MethodologyGapFinding,
    ValidatedDispatchedMethodologyResult,
    canonical_json_bytes,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v7_reviewed_preimage_v1.json"

_TENANT = "10000000-0000-0000-0000-000000000001"
_OTHER_TENANT = "10000000-0000-0000-0000-000000000002"
_DEAL = "20000000-0000-0000-0000-000000000001"
_OTHER_DEAL = "20000000-0000-0000-0000-000000000002"
_RUN = "30000000-0000-0000-0000-000000000001"
_OTHER_RUN = "30000000-0000-0000-0000-000000000002"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _authority_sql(authority_id: str) -> tuple[str, ...]:
    section = _artifact().get("sql_authority_registry")
    assert isinstance(section, dict), "v7 omits sql_authority_registry"
    rows = section.get("rows")
    assert isinstance(rows, list)
    matches = [row for row in rows if row.get("authority_id") == authority_id]
    assert len(matches) == 1, f"expected one {authority_id} authority"
    authority = matches[0]
    encoded = authority.get("body_sql_utf8_hex_tuple")
    assert isinstance(encoded, list) and encoded
    bodies = tuple(bytes.fromhex(statement).decode("utf-8") for statement in encoded)
    if authority.get("authority_kind") != "CHECK":
        return bodies
    targets = authority.get("target_tuple")
    assert isinstance(targets, list) and len(targets) == len(bodies)
    statements: list[str] = []
    for target, body in zip(targets, bodies, strict=True):
        assert isinstance(target, list) and len(target) == 2
        table, constraint = target
        assert all(
            isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]*", value)
            for value in (table, constraint)
        )
        statements.append(f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} CHECK ({body})")
    return tuple(statements)


def _authority_row(authority_id: str) -> dict[str, Any]:
    section = _artifact().get("sql_authority_registry")
    assert isinstance(section, dict), "v7 omits sql_authority_registry"
    rows = section.get("rows")
    assert isinstance(rows, list)
    matches = [row for row in rows if row.get("authority_id") == authority_id]
    assert len(matches) == 1, f"expected one {authority_id} authority"
    row = matches[0]
    assert isinstance(row, dict)
    return row


class _LazyDisposablePostgres:
    """Open disposable PostgreSQL lazily after the v7 literal authority is loaded."""

    def __init__(self) -> None:
        self._context: AbstractContextManager[DisposablePostgres] | None = None
        self._database: DisposablePostgres | None = None

    def _open(self) -> DisposablePostgres:
        if self._database is None:
            self._context = disposable_postgres()
            self._database = self._context.__enter__()
        return self._database

    @property
    def admin_engine(self) -> Any:
        return self._open().admin_engine

    @property
    def app_engine(self) -> Any:
        return self._open().app_engine

    @property
    def role_name(self) -> str:
        return self._open().role_name

    def close(self) -> None:
        if self._context is not None:
            self._context.__exit__(None, None, None)
            self._context = None
            self._database = None


GuardDatabase = DisposablePostgres | _LazyDisposablePostgres


@pytest.fixture
def guard_database() -> Iterator[_LazyDisposablePostgres]:
    database = _LazyDisposablePostgres()
    try:
        yield database
    finally:
        database.close()


def _install(
    database: GuardDatabase,
    authority_id: str,
    schema_sql: tuple[str, ...],
) -> None:
    statements = _authority_sql(authority_id)
    with database.admin_engine.begin() as connection:
        for statement in schema_sql:
            connection.exec_driver_sql(statement)
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{database.role_name}"')
        connection.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f'TO "{database.role_name}"'
        )


def _flush(connection: Connection) -> None:
    connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")


def _assert_rejection_signature(
    error: DBAPIError,
    *,
    authority_id: str,
    object_markers: set[str],
) -> None:
    """Require the authority's stable SQLSTATE and marker, never any DB failure."""

    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    assert sqlstate == "23514", (
        f"{authority_id} rejection used unrelated SQLSTATE {sqlstate!r}: {original}"
    )
    authority_marker = "TASK7_SQL_AUTHORITY_REJECTED:" + authority_id.removesuffix(
        "-v1"
    ).upper().replace("-", "_")
    rendered = str(original).upper()
    assert authority_marker in rendered or any(marker in rendered for marker in object_markers), (
        f"{authority_id} rejection omitted its stable marker; expected "
        f"{authority_marker!r} or one of {sorted(object_markers)!r}, got {original!s}"
    )


def _assert_authority_rejection(error: DBAPIError, authority_id: str) -> None:
    """Prove the semantic authority rejected the row, not an incidental DB layer."""

    row = _authority_row(authority_id)
    targets = row.get("target_tuple")
    assert isinstance(targets, list) and targets
    object_markers = {
        str(target[1]).upper()
        for target in targets
        if isinstance(target, list) and len(target) == 2
    }
    _assert_rejection_signature(
        error,
        authority_id=authority_id,
        object_markers=object_markers,
    )


def _expect_rejected(
    connection: Connection,
    authority_id: str,
    statements: tuple[str, ...],
) -> None:
    try:
        with connection.begin_nested():
            for statement in statements:
                connection.exec_driver_sql(statement)
            _flush(connection)
    except DBAPIError as exc:
        _assert_authority_rejection(exc, authority_id)
        return
    pytest.fail(f"{authority_id} admitted an illegal direct-SQL row")


def _raw_insert(database: GuardDatabase, statements: tuple[str, ...]) -> None:
    with database.app_engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        _flush(connection)


def _raw_expect_rejected(
    database: GuardDatabase,
    authority_id: str,
    statements: tuple[str, ...],
) -> None:
    try:
        _raw_insert(database, statements)
    except DBAPIError as exc:
        _assert_authority_rejection(exc, authority_id)
        return
    pytest.fail(f"{authority_id} admitted an illegal application-role direct-SQL row")


def test_guard_rejection_harness_requires_authority_sqlstate_and_marker() -> None:
    """The RED harness must not accept an arbitrary database failure as enforcement."""

    helper = globals().get("_assert_authority_rejection")
    signature_helper = globals().get("_assert_rejection_signature")
    assert callable(helper), "guard harness omits authority-specific rejection validation"
    assert callable(signature_helper), "guard harness omits stable rejection-signature validation"
    source = inspect.getsource(helper) + inspect.getsource(signature_helper)
    assert "sqlstate" in source.lower()
    assert "TASK7_SQL_AUTHORITY_REJECTED" in source


def _synthetic_db_error(*, sqlstate: str, message: str) -> DBAPIError:
    original_type = type(
        "SyntheticTask7DatabaseError",
        (Exception,),
        {"sqlstate": sqlstate},
    )
    return DBAPIError("fixture statement", {}, original_type(message), False)


def test_guard_rejection_signature_rejects_incidental_database_errors() -> None:
    marker = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
    _assert_rejection_signature(
        _synthetic_db_error(sqlstate="23514", message=marker),
        authority_id="dashboard-root-equation-v1",
        object_markers={"CTRG_V2_DASHBOARD_ROOT_EQUATION"},
    )

    with pytest.raises(AssertionError, match="unrelated SQLSTATE"):
        _assert_rejection_signature(
            _synthetic_db_error(sqlstate="23505", message=marker),
            authority_id="dashboard-root-equation-v1",
            object_markers={"CTRG_V2_DASHBOARD_ROOT_EQUATION"},
        )
    with pytest.raises(AssertionError, match="omitted its stable marker"):
        _assert_rejection_signature(
            _synthetic_db_error(
                sqlstate="23514",
                message="unrelated check constraint on the same fixture table",
            ),
            authority_id="dashboard-root-equation-v1",
            object_markers={"CTRG_V2_DASHBOARD_ROOT_EQUATION"},
        )


_NEGATIVE_ZERO_BYPASS_SCHEMA = (
    """
    CREATE TABLE scoring_output_dimensions (
        scoring_dimension_id UUID PRIMARY KEY,
        score DOUBLE PRECISION NOT NULL,
        confidence DOUBLE PRECISION NOT NULL
    )
    """,
)


def test_scoring_binary64_negative_zero_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
) -> None:
    """The application role cannot disable the normalization trigger for raw SQL."""

    _install(
        guard_database,
        "scoring-binary64-negative-zero-v1",
        _NEGATIVE_ZERO_BYPASS_SCHEMA,
    )
    try:
        with guard_database.app_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL session_replication_role='replica'")
    except DBAPIError as exc:
        original = exc.orig
        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        assert sqlstate == "42501"
        assert "SESSION_REPLICATION_ROLE" in str(original).upper()
        return
    pytest.fail("application role bypassed the negative-zero normalization trigger")


_ROOT_SQL = """
CREATE TABLE run_output_objects (
    output_object_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    deal_id UUID NOT NULL,
    run_id UUID NOT NULL,
    output_class TEXT NOT NULL,
    output_kind TEXT NOT NULL,
    native_identity_sha256 TEXT,
    native_output_sha256 TEXT
)
"""


_DASHBOARD_SCHEMA = (
    _ROOT_SQL,
    """
    CREATE TABLE methodology_questions (
        methodology_question_record_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        methodology_version_record_id UUID NOT NULL,
        methodology_version_id TEXT NOT NULL,
        methodology_id TEXT NOT NULL,
        methodology_question_id TEXT NOT NULL,
        methodology_type TEXT NOT NULL,
        question_ordinal INTEGER NOT NULL,
        section TEXT NOT NULL,
        source_sheet_or_section TEXT NOT NULL,
        source_row_number INTEGER,
        source_anchor_cell TEXT,
        source_sha256 TEXT NOT NULL,
        semantic_row_hash TEXT NOT NULL,
        question_payload_hash TEXT NOT NULL,
        question_payload JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_coverage_records (
        coverage_record_id TEXT PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        selection_id UUID NOT NULL,
        methodology_version_record_id UUID NOT NULL,
        methodology_version_id TEXT NOT NULL,
        methodology_question_record_id UUID NOT NULL,
        methodology_question_id TEXT NOT NULL,
        methodology_type TEXT NOT NULL,
        status TEXT NOT NULL,
        reason_code TEXT,
        evidence_links JSONB NOT NULL,
        answer JSONB,
        conflict_ids TEXT[] NOT NULL,
        defect_ids TEXT[] NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_extraction_tasks (
        task_record_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        selection_id UUID NOT NULL,
        coverage_record_id TEXT NOT NULL,
        methodology_version_record_id UUID NOT NULL,
        methodology_version_id TEXT NOT NULL,
        methodology_question_record_id UUID NOT NULL,
        methodology_question_id TEXT NOT NULL,
        methodology_type TEXT NOT NULL,
        extraction_task_id TEXT NOT NULL,
        document_id UUID NOT NULL,
        document_classification_id TEXT NOT NULL,
        document_classification_sha256 TEXT NOT NULL,
        planner_contract_version TEXT NOT NULL,
        task_contract_version TEXT NOT NULL,
        task_payload JSONB NOT NULL,
        task_input_sha256 TEXT NOT NULL,
        candidate_span_set_sha256 TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_extraction_results (
        result_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        task_record_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        task_input_sha256 TEXT NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL,
        outcome TEXT NOT NULL,
        result_payload JSONB NOT NULL,
        result_sha256 TEXT NOT NULL,
        searched_document_ids UUID[] NOT NULL,
        searched_span_ids UUID[] NOT NULL,
        searched_scope_sha256 TEXT NOT NULL,
        provider_provenance_sha256 TEXT NOT NULL,
        gap_finding JSONB
    )
    """,
    """
    CREATE TABLE methodology_result_factual_outputs (
        factual_output_record_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        result_id UUID NOT NULL,
        task_record_id UUID NOT NULL,
        output_ordinal INTEGER NOT NULL,
        factual_output_id UUID NOT NULL,
        typed_output_payload JSONB NOT NULL,
        typed_output_sha256 TEXT NOT NULL,
        accepted_source_span_ids UUID[] NOT NULL,
        accepted_locator_sha256s TEXT[] NOT NULL,
        accepted_locator_set_sha256 TEXT NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_result_evidence_links (
        evidence_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        result_id UUID NOT NULL,
        factual_output_record_id UUID NOT NULL,
        claim_link_id UUID NOT NULL,
        evidence_id UUID NOT NULL,
        document_id UUID NOT NULL,
        source_span_id UUID NOT NULL,
        locator_sha256 TEXT NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_result_sanad_links (
        sanad_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        result_id UUID NOT NULL,
        factual_output_record_id UUID NOT NULL,
        claim_link_id UUID NOT NULL,
        sanad_id UUID NOT NULL,
        primary_evidence_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_result_defect_links (
        defect_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        result_id UUID NOT NULL,
        factual_output_record_id UUID NOT NULL,
        defect_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE deterministic_calculations (
        calc_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        calc_type TEXT NOT NULL,
        inputs JSONB NOT NULL,
        formula_hash TEXT NOT NULL,
        code_version TEXT NOT NULL,
        output JSONB NOT NULL,
        reproducibility_hash TEXT NOT NULL,
        run_id UUID NOT NULL,
        coverage_record_id TEXT NOT NULL,
        methodology_question_record_id UUID NOT NULL,
        producer_step_name TEXT NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_truth_dashboard_rows (
        dashboard_row_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        coverage_record_id TEXT NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED,
        run_output_object_kind TEXT GENERATED ALWAYS AS
            ('METHODOLOGY_TRUTH_DASHBOARD_ROW') STORED,
        lease_owner_id UUID NOT NULL,
        lease_fencing_token BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
        root_projection_sha256 TEXT NOT NULL,
        row_disposition TEXT NOT NULL,
        row_payload_sha256 TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_truth_dashboard_root_links (
        dashboard_root_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        dashboard_row_id UUID NOT NULL,
        dashboard_output_object_id UUID NOT NULL,
        root_kind TEXT NOT NULL,
        root_ordinal INTEGER NOT NULL,
        root_output_object_id UUID NOT NULL,
        root_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED,
        root_output_object_kind TEXT NOT NULL,
        lease_owner_id UUID NOT NULL,
        lease_fencing_token BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
    )
    """,
)


def _task5_dashboard_question_payload(*, required: bool) -> dict[str, Any]:
    question = MethodologyQuestion.model_validate(
        {
            "methodology_id": "fictional_commercial_dd",
            "methodology_version_id": "fictional-commercial-dd-v1",
            "methodology_question_id": "mq_fictional_dashboard_001",
            "methodology_type": "commercial_dd",
            "section": "Fictional dashboard section",
            "sheet_or_source_section": "Fictional dashboard section",
            "question_text": "What does the admitted fictional evidence establish?",
            "required_evidence": [
                {
                    "evidence_type": "fictional_document",
                    "description": "Admitted fictional documentary support",
                    "min_count": 1,
                }
            ],
            "target_document_categories": ["fictional_document"],
            "required_calculations": [{"calc_type": "FICTIONAL_BRIDGE", "required": required}],
            "assigned_agents": [
                {
                    "role": "fictional_financial_agent",
                    "responsibility": "Assess the admitted fictional evidence",
                }
            ],
            "red_flag_rules": [],
            "report_mapping": {
                "report_section": "Fictional dashboard section",
                "report_subsection": "Fictional dashboard result",
            },
            "validation_requirements": ["cite_admitted_span"],
            "source_trace": {
                "source_type": "project_owned_fixture",
                "source_name": "fictional-dashboard-methodology-v1",
                "source_hash": _HASH_A,
                "sheet_or_section": "Fictional dashboard section",
            },
        }
    )
    payload = question.model_dump(mode="json")
    assert isinstance(payload, dict)
    return payload


def _validated_dashboard_result(
    *,
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"],
    question_id: str,
) -> ValidatedDispatchedMethodologyResult:
    factual_output = MethodologyFactualOutput.model_validate(
        {
            "subject": "Fictional Company",
            "methodology_question_record_id": question_id,
            "predicate": "fictional_revenue",
            "claim_class": "FINANCIAL_METRIC",
            "value": {"type": "monetary", "amount": "12.00", "currency": "USD"},
            "period": {"kind": "as_of", "date": "2026-06-30"},
            "unit": "USD",
            "claim_text": "Fictional revenue was USD 12.",
            "accepted_source_span_ids": ["00000000-0000-0000-0000-000000000201"],
            "accepted_locator_sha256s": [_HASH_B],
        }
    )
    gap_finding = (
        MethodologyGapFinding(
            code="GOVERNED_EVIDENCE_GAP",
            finding="No accepted evidence matched the governed search scope.",
        )
        if outcome == "EVIDENCE_NOT_FOUND"
        else None
    )
    return ValidatedDispatchedMethodologyResult.from_terminal_payload(
        dispatched_call_sha256=_HASH_B,
        task_input_sha256=_HASH_A,
        outcome=outcome,
        searched_document_ids=("00000000-0000-0000-0000-000000000101",),
        searched_span_ids=("00000000-0000-0000-0000-000000000201",),
        searched_scope_sha256=_HASH_A,
        factual_outputs=(factual_output,) if outcome == "EVIDENCE_FOUND" else (),
        gap_finding=gap_finding,
    )


def _dashboard_result_seed_values(
    *,
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"],
    question_id: str,
    searched_document_ids: tuple[str, ...],
    searched_span_ids: tuple[str, ...],
    searched_scope_sha256: str,
    include_factual: bool,
    result_payload_override: object | None,
    gap_finding_override: object | None,
) -> tuple[object, object | None, MethodologyFactualOutput | None]:
    """Start from a real Task-6 result and then apply one named corruption."""

    validated = _validated_dashboard_result(outcome=outcome, question_id=question_id)
    payload = validated.model_dump(mode="json")
    factual_output = validated.factual_outputs[0] if validated.factual_outputs else None
    if include_factual and factual_output is None:
        found = _validated_dashboard_result(outcome="EVIDENCE_FOUND", question_id=question_id)
        factual_output = found.factual_outputs[0]
    payload["factual_outputs"] = (
        [factual_output.model_dump(mode="json")] if include_factual and factual_output else []
    )
    payload["searched_document_ids"] = list(searched_document_ids)
    payload["searched_span_ids"] = list(searched_span_ids)
    payload["searched_scope_sha256"] = searched_scope_sha256

    persisted_gap: object | None = payload["gap_finding"]
    if gap_finding_override is not None:
        payload["gap_finding"] = gap_finding_override
        persisted_gap = gap_finding_override

    if result_payload_override is not None:
        return result_payload_override, persisted_gap, factual_output if include_factual else None

    # Hash through the same typed nested representation used by production
    # validation. Keep deliberately invalid outer fields byte-for-byte so the
    # RED case is rejected for that field rather than a stale fixture hash.
    typed_factual_outputs = tuple(
        MethodologyFactualOutput.model_validate(item) for item in payload["factual_outputs"]
    )
    typed_gap_finding: object | None = payload["gap_finding"]
    if isinstance(typed_gap_finding, dict) and typed_gap_finding:
        typed_gap_finding = MethodologyGapFinding.model_validate(typed_gap_finding)
    draft = ValidatedDispatchedMethodologyResult.model_construct(
        **{
            **payload,
            "factual_outputs": typed_factual_outputs,
            "gap_finding": typed_gap_finding,
        }
    )
    payload["validated_result_sha256"] = draft.expected_validated_result_sha256()
    return payload, persisted_gap, factual_output if include_factual else None


def _seed_dashboard_parent(
    connection: Connection,
    *,
    suffix: int,
    question_payload: object,
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"],
    result_payload: object | None = None,
    gap_finding: object | None = None,
    searched_document_ids: tuple[str, ...] = ("00000000-0000-0000-0000-000000000101",),
    searched_span_ids: tuple[str, ...] = ("00000000-0000-0000-0000-000000000201",),
    searched_scope_sha256: str = _HASH_A,
    provider_provenance_sha256: str = _HASH_A,
    result_tenant: str = _TENANT,
    result_deal: str = _DEAL,
    result_run: str = _RUN,
    task_coverage_id: str | None = None,
    factual_child: bool | None = None,
    calculation_child: bool = False,
    adverse_child: bool = False,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]:
    question_id = str(uuid.UUID(int=100 + suffix))
    task_id = str(uuid.UUID(int=200 + suffix))
    result_id = str(uuid.UUID(int=300 + suffix))
    result_root = str(uuid.UUID(int=400 + suffix))
    factual_record_id = str(uuid.UUID(int=900 + suffix))
    factual_root = str(uuid.UUID(int=1000 + suffix))
    evidence_link_id = str(uuid.UUID(int=1100 + suffix))
    evidence_root = str(uuid.UUID(int=1200 + suffix))
    sanad_link_id = str(uuid.UUID(int=1300 + suffix))
    sanad_root = str(uuid.UUID(int=1400 + suffix))
    calculation_id = str(uuid.UUID(int=1500 + suffix))
    calculation_root = str(uuid.UUID(int=1600 + suffix))
    methodology_version_record_id = str(uuid.UUID(int=1700 + suffix))
    selection_id = str(uuid.UUID(int=1800 + suffix))
    attempt_id = str(uuid.UUID(int=1900 + suffix))
    claim_link_id = str(uuid.UUID(int=2000 + suffix))
    evidence_id = str(uuid.UUID(int=2100 + suffix))
    sanad_id = str(uuid.UUID(int=2200 + suffix))
    defect_link_id = str(uuid.UUID(int=2300 + suffix))
    defect_root = str(uuid.UUID(int=2400 + suffix))
    defect_id = str(uuid.UUID(int=2500 + suffix))
    coverage_id = f"mc_{suffix:024x}"
    include_factual = outcome == "EVIDENCE_FOUND" if factual_child is None else factual_child
    result_payload, governed_gap, factual_output = _dashboard_result_seed_values(
        outcome=outcome,
        question_id=question_id,
        searched_document_ids=searched_document_ids,
        searched_span_ids=searched_span_ids,
        searched_scope_sha256=searched_scope_sha256,
        include_factual=include_factual,
        result_payload_override=result_payload,
        gap_finding_override=gap_finding,
    )
    factual_id = (
        str(factual_output.factual_output_id)
        if factual_output is not None
        else str(uuid.UUID(int=900 + suffix))
    )
    assert isinstance(question_payload, dict)
    question_payload_sha256 = hashlib.sha256(canonical_json_bytes(question_payload)).hexdigest()
    persisted_result_sha256 = (
        str(result_payload.get("validated_result_sha256", _HASH_A))
        if isinstance(result_payload, dict)
        else _HASH_A
    )
    task_document_id = "00000000-0000-0000-0000-000000000101"
    task_payload = {
        "coverage_record_id": task_coverage_id or coverage_id,
        "methodology_question_record_id": question_id,
        "document_id": task_document_id,
    }
    connection.execute(
        text(
            """
            INSERT INTO methodology_questions (
                methodology_question_record_id,tenant_id,
                methodology_version_record_id,methodology_version_id,
                methodology_id,methodology_question_id,methodology_type,
                question_ordinal,section,source_sheet_or_section,
                source_row_number,source_anchor_cell,source_sha256,
                semantic_row_hash,question_payload_hash,question_payload
            ) VALUES (
                CAST(:question AS uuid),CAST(:tenant AS uuid),
                CAST(:version_record AS uuid),:version_id,:methodology_id,
                :question_semantic_id,:methodology_type,1,:section,:source_section,
                NULL,NULL,:hash,:hash,:question_payload_hash,CAST(:payload AS jsonb)
            );
            INSERT INTO methodology_coverage_records (
                coverage_record_id,tenant_id,deal_id,run_id,selection_id,
                methodology_version_record_id,methodology_version_id,
                methodology_question_record_id,methodology_question_id,
                methodology_type,status,reason_code,evidence_links,answer,
                conflict_ids,defect_ids
            ) VALUES (
                :coverage,CAST(:tenant AS uuid),CAST(:deal AS uuid),
                CAST(:run AS uuid),CAST(:selection AS uuid),
                CAST(:version_record AS uuid),:version_id,CAST(:question AS uuid),
                :question_semantic_id,:methodology_type,'not_started',NULL,
                '[]'::jsonb,NULL,ARRAY[]::text[],ARRAY[]::text[]
            );
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
                CAST(:run AS uuid),CAST(:selection AS uuid),:task_coverage,
                CAST(:version_record AS uuid),:version_id,CAST(:question AS uuid),
                :question_semantic_id,:methodology_type,:extraction_task_id,
                CAST(:task_document AS uuid),'fictional_document',:hash,
                'methodology-planner-contract-v1','methodology-task-contract-v1',
                CAST(:task_payload AS jsonb),:hash,:hash,'READY'
            );
            INSERT INTO run_output_objects VALUES (
                CAST(:root AS uuid),CAST(:result_tenant AS uuid),CAST(:result_deal AS uuid),
                CAST(:result_run AS uuid),'NATIVE_ROW','METHODOLOGY_EXTRACTION_RESULT',:hash,:hash
            );
            INSERT INTO methodology_extraction_results (
                result_id,tenant_id,deal_id,run_id,task_record_id,attempt_id,
                task_input_sha256,
                run_output_object_id,run_output_object_class,run_output_object_kind,
                outcome,result_payload,result_sha256,searched_document_ids,searched_span_ids,
                searched_scope_sha256,provider_provenance_sha256,gap_finding
            ) VALUES (
                CAST(:result AS uuid),CAST(:result_tenant AS uuid),
                CAST(:result_deal AS uuid),CAST(:result_run AS uuid),
                CAST(:task AS uuid),CAST(:attempt AS uuid),:hash,
                CAST(:root AS uuid),'NATIVE_ROW',
                'METHODOLOGY_EXTRACTION_RESULT',:outcome,
                CAST(:result_payload AS jsonb),:result_sha256,CAST(:documents AS uuid[]),
                CAST(:spans AS uuid[]),:scope_hash,:provider_hash,
                CAST(:gap_finding AS jsonb)
            )
            """
        ),
        {
            "question": question_id,
            "task": task_id,
            "result": result_id,
            "root": result_root,
            "coverage": coverage_id,
            "selection": selection_id,
            "version_record": methodology_version_record_id,
            "version_id": question_payload["methodology_version_id"],
            "methodology_id": question_payload["methodology_id"],
            "question_semantic_id": question_payload["methodology_question_id"],
            "methodology_type": question_payload["methodology_type"],
            "section": question_payload["section"],
            "source_section": question_payload["source_trace"]["sheet_or_section"],
            "tenant": _TENANT,
            "deal": _DEAL,
            "run": _RUN,
            "payload": json.dumps(question_payload, separators=(",", ":")),
            "question_payload_hash": question_payload_sha256,
            "task_coverage": task_coverage_id or coverage_id,
            "extraction_task_id": f"et_dashboard_{suffix:04d}",
            "task_document": task_document_id,
            "task_payload": json.dumps(task_payload, separators=(",", ":")),
            "attempt": attempt_id,
            "outcome": outcome,
            "result_payload": json.dumps(result_payload, separators=(",", ":")),
            "result_sha256": persisted_result_sha256,
            "documents": list(searched_document_ids),
            "spans": list(searched_span_ids),
            "scope_hash": searched_scope_sha256,
            "provider_hash": provider_provenance_sha256,
            "gap_finding": (
                None if governed_gap is None else json.dumps(governed_gap, separators=(",", ":"))
            ),
            "result_tenant": result_tenant,
            "result_deal": result_deal,
            "result_run": result_run,
            "hash": _HASH_A,
        },
    )
    persisted_factual_root: str | None = None
    persisted_evidence_root: str | None = None
    persisted_sanad_root: str | None = None
    persisted_calculation_root: str | None = None
    persisted_adverse_root: str | None = None
    if include_factual:
        assert factual_output is not None
        factual_payload = factual_output.model_dump(mode="json")
        typed_output_sha256 = hashlib.sha256(canonical_json_bytes(factual_payload)).hexdigest()
        accepted_spans = list(factual_output.accepted_source_span_ids)
        accepted_locators = list(factual_output.accepted_locator_sha256s)
        locator_set_sha256 = hashlib.sha256(
            canonical_json_bytes(sorted(zip(accepted_spans, accepted_locators, strict=True)))
        ).hexdigest()
        _seed_simple_root(
            connection,
            factual_root,
            "METHODOLOGY_FACTUAL_OUTPUT",
            tenant=result_tenant,
            deal=result_deal,
            run=result_run,
        )
        _seed_simple_root(
            connection,
            evidence_root,
            "METHODOLOGY_EVIDENCE_LINK",
            tenant=result_tenant,
            deal=result_deal,
            run=result_run,
        )
        _seed_simple_root(
            connection,
            sanad_root,
            "METHODOLOGY_SANAD_LINK",
            tenant=result_tenant,
            deal=result_deal,
            run=result_run,
        )
        connection.execute(
            text(
                "INSERT INTO methodology_result_factual_outputs ("
                "factual_output_record_id,tenant_id,deal_id,run_id,result_id,"
                "task_record_id,output_ordinal,factual_output_id,typed_output_payload,"
                "typed_output_sha256,accepted_source_span_ids,accepted_locator_sha256s,"
                "accepted_locator_set_sha256,run_output_object_id,"
                "run_output_object_class,run_output_object_kind) VALUES "
                "(CAST(:factual_record AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "CAST(:run AS uuid),CAST(:result AS uuid),CAST(:task AS uuid),1,"
                "CAST(:factual_output AS uuid),CAST(:factual_payload AS jsonb),:typed_hash,"
                "CAST(:accepted_spans AS uuid[]),CAST(:accepted_locators AS text[]),"
                ":locator_set_hash,CAST(:factual_root AS uuid),'NATIVE_ROW',"
                "'METHODOLOGY_FACTUAL_OUTPUT');"
                "INSERT INTO methodology_result_evidence_links ("
                "evidence_link_id,tenant_id,deal_id,run_id,result_id,"
                "factual_output_record_id,claim_link_id,evidence_id,document_id,"
                "source_span_id,locator_sha256,run_output_object_id,"
                "run_output_object_class,run_output_object_kind) VALUES "
                "(CAST(:evidence_link AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "CAST(:run AS uuid),CAST(:result AS uuid),CAST(:factual_record AS uuid),"
                "CAST(:claim_link AS uuid),CAST(:evidence AS uuid),CAST(:document AS uuid),"
                "CAST(:source_span AS uuid),:locator_hash,CAST(:evidence_root AS uuid),"
                "'NATIVE_ROW','METHODOLOGY_EVIDENCE_LINK');"
                "INSERT INTO methodology_result_sanad_links ("
                "sanad_link_id,tenant_id,deal_id,run_id,result_id,"
                "factual_output_record_id,claim_link_id,sanad_id,primary_evidence_id,"
                "run_output_object_id,run_output_object_class,run_output_object_kind) VALUES "
                "(CAST(:sanad_link AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "CAST(:run AS uuid),CAST(:result AS uuid),CAST(:factual_record AS uuid),"
                "CAST(:claim_link AS uuid),CAST(:sanad AS uuid),CAST(:evidence AS uuid),"
                "CAST(:sanad_root AS uuid),'NATIVE_ROW','METHODOLOGY_SANAD_LINK')"
            ),
            {
                "factual_record": factual_record_id,
                "factual_output": factual_id,
                "factual_payload": json.dumps(factual_payload, separators=(",", ":")),
                "typed_hash": typed_output_sha256,
                "accepted_spans": accepted_spans,
                "accepted_locators": accepted_locators,
                "locator_set_hash": locator_set_sha256,
                "factual_root": factual_root,
                "evidence_link": evidence_link_id,
                "evidence_root": evidence_root,
                "sanad_link": sanad_link_id,
                "sanad_root": sanad_root,
                "claim_link": claim_link_id,
                "evidence": evidence_id,
                "sanad": sanad_id,
                "document": task_document_id,
                "source_span": accepted_spans[0],
                "locator_hash": accepted_locators[0],
                "tenant": result_tenant,
                "deal": result_deal,
                "run": result_run,
                "result": result_id,
                "task": task_id,
            },
        )
        persisted_factual_root = factual_root
        persisted_evidence_root = evidence_root
        persisted_sanad_root = sanad_root
        if adverse_child:
            _seed_simple_root(
                connection,
                defect_root,
                "METHODOLOGY_DEFECT_LINK",
                tenant=result_tenant,
                deal=result_deal,
                run=result_run,
            )
            connection.execute(
                text(
                    "INSERT INTO methodology_result_defect_links ("
                    "defect_link_id,tenant_id,deal_id,run_id,result_id,"
                    "factual_output_record_id,defect_id,run_output_object_id,"
                    "run_output_object_class,run_output_object_kind) VALUES ("
                    "CAST(:defect_link AS uuid),CAST(:tenant AS uuid),"
                    "CAST(:deal AS uuid),CAST(:run AS uuid),CAST(:result AS uuid),"
                    "CAST(:factual_record AS uuid),CAST(:defect AS uuid),"
                    "CAST(:root AS uuid),'NATIVE_ROW','METHODOLOGY_DEFECT_LINK')"
                ),
                {
                    "defect_link": defect_link_id,
                    "tenant": result_tenant,
                    "deal": result_deal,
                    "run": result_run,
                    "result": result_id,
                    "factual_record": factual_record_id,
                    "defect": defect_id,
                    "root": defect_root,
                },
            )
            persisted_adverse_root = defect_root
    if calculation_child:
        _seed_simple_root(
            connection,
            calculation_root,
            "METHODOLOGY_CALCULATION",
            tenant=result_tenant,
            deal=result_deal,
            run=result_run,
        )
        connection.execute(
            text(
                "INSERT INTO deterministic_calculations ("
                "calc_id,tenant_id,deal_id,calc_type,inputs,formula_hash,code_version,"
                "output,reproducibility_hash,run_id,producer_step_name,"
                "coverage_record_id,methodology_question_record_id,"
                "run_output_object_id,run_output_object_class,run_output_object_kind) VALUES "
                "(CAST(:calc AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "'FICTIONAL_BRIDGE',CAST(:inputs AS jsonb),:hash,'fixture-code-v1',"
                "CAST(:output AS jsonb),:hash,CAST(:run AS uuid),"
                "'METHODOLOGY_DETERMINISTIC_CALCULATION',:coverage,"
                "CAST(:question AS uuid),CAST(:root AS uuid),"
                "'NATIVE_ROW','METHODOLOGY_CALCULATION')"
            ),
            {
                "calc": calculation_id,
                "tenant": result_tenant,
                "deal": result_deal,
                "run": result_run,
                "coverage": coverage_id,
                "question": question_id,
                "root": calculation_root,
                "inputs": json.dumps({"fictional_input": "12.00"}, separators=(",", ":")),
                "output": json.dumps({"fictional_output": "12.00"}, separators=(",", ":")),
                "hash": _HASH_A,
            },
        )
        persisted_calculation_root = calculation_root
    return (
        coverage_id,
        result_id,
        result_root,
        question_id,
        task_id,
        persisted_factual_root,
        persisted_evidence_root,
        persisted_sanad_root,
        persisted_calculation_root,
        persisted_adverse_root,
    )


def _seed_simple_root(
    connection: Connection,
    root: str,
    kind: str,
    *,
    tenant: str = _TENANT,
    deal: str = _DEAL,
    run: str = _RUN,
) -> None:
    connection.execute(
        text(
            "INSERT INTO run_output_objects VALUES "
            "(CAST(:root AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
            "CAST(:run AS uuid),'NATIVE_ROW',:kind,:hash,:hash)"
        ),
        {
            "root": root,
            "tenant": tenant,
            "deal": deal,
            "run": run,
            "kind": kind,
            "hash": _HASH_A,
        },
    )


def _dashboard_insert_sql(
    row_id: str,
    coverage_id: str,
    result_root: str | None,
    disposition: str,
    evidence: str | None,
    sanad: str | None,
    calculation: str | None,
    adverse: str | None = None,
    *,
    tenant: str = _TENANT,
    deal: str = _DEAL,
    run: str = _RUN,
) -> str:
    dashboard_output = str(uuid.uuid5(uuid.NAMESPACE_OID, f"task7-dashboard-output:{row_id}"))
    roots = tuple(
        sorted(
            (
                (kind, 0, root_id, output_kind)
                for kind, root_id, output_kind in (
                    ("RESULT", result_root, "METHODOLOGY_EXTRACTION_RESULT"),
                    ("EVIDENCE", evidence, "METHODOLOGY_EVIDENCE_LINK"),
                    ("SANAD", sanad, "METHODOLOGY_SANAD_LINK"),
                    ("CALCULATION", calculation, "METHODOLOGY_CALCULATION"),
                    ("ADVERSE_CONTRADICTION", adverse, "METHODOLOGY_DEFECT_LINK"),
                )
                if root_id is not None
            ),
            key=lambda row: (row[0], row[1], row[2]),
        )
    )
    link_rows = tuple(
        (
            kind,
            ordinal,
            root_id,
            output_kind,
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_OID,
                    f"task7-dashboard-root-link:{row_id}:{kind}:{ordinal}:{root_id}",
                )
            ),
        )
        for kind, ordinal, root_id, output_kind in roots
    )
    projection_preimage = json.dumps(
        [
            "methodology-truth-dashboard-root-projection-v1",
            tenant,
            deal,
            run,
            row_id,
            dashboard_output,
            coverage_id,
            disposition,
            len(link_rows),
            [
                [kind, ordinal, root_id, output_kind, link_id]
                for kind, ordinal, root_id, output_kind, link_id in link_rows
            ],
        ],
        separators=(",", ":"),
    )
    projection_sha256 = hashlib.sha256(projection_preimage.encode("utf-8")).hexdigest()
    lease_owner = str(uuid.uuid5(uuid.NAMESPACE_OID, f"task7-dashboard-lease:{row_id}"))
    statements = [
        "INSERT INTO run_output_objects VALUES "
        f"('{dashboard_output}'::uuid,'{tenant}'::uuid,'{deal}'::uuid,"
        f"'{run}'::uuid,'NATIVE_ROW','METHODOLOGY_TRUTH_DASHBOARD_ROW',"
        f"'{_HASH_A}','{_HASH_A}')",
        "INSERT INTO methodology_truth_dashboard_rows ("
        "dashboard_row_id,tenant_id,deal_id,run_id,coverage_record_id,"
        "run_output_object_id,lease_owner_id,lease_fencing_token,"
        "root_projection_sha256,row_disposition,row_payload_sha256) VALUES "
        f"('{row_id}'::uuid,'{tenant}'::uuid,'{deal}'::uuid,'{run}'::uuid,"
        f"'{coverage_id}','{dashboard_output}'::uuid,'{lease_owner}'::uuid,1,"
        f"'{projection_sha256}','{disposition}','{_HASH_A}')",
    ]
    statements.extend(
        "INSERT INTO methodology_truth_dashboard_root_links ("
        "dashboard_root_link_id,tenant_id,deal_id,run_id,dashboard_row_id,"
        "dashboard_output_object_id,root_kind,root_ordinal,root_output_object_id,"
        "root_output_object_kind,lease_owner_id,lease_fencing_token) VALUES "
        f"('{link_id}'::uuid,'{tenant}'::uuid,'{deal}'::uuid,'{run}'::uuid,"
        f"'{row_id}'::uuid,'{dashboard_output}'::uuid,'{kind}',{ordinal},"
        f"'{root_id}'::uuid,'{output_kind}','{lease_owner}'::uuid,1)"
        for kind, ordinal, root_id, output_kind, link_id in link_rows
    )
    return ";".join(statements)


def test_dashboard_fixture_schema_covers_every_declared_parent_relation() -> None:
    rendered = "\n".join(_DASHBOARD_SCHEMA).lower()
    expected = {
        "methodology_coverage_records",
        "methodology_questions",
        "methodology_extraction_tasks",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
        "deterministic_calculations",
        "run_output_objects",
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    }
    missing = {relation for relation in expected if f"create table {relation}" not in rendered}
    assert not missing, f"dashboard fixture omits declared parent relations: {sorted(missing)!r}"


def test_dashboard_parent_projection_preserves_task5_task6_lineage_columns() -> None:
    required = {
        "methodology_questions": {
            "methodology_version_record_id",
            "methodology_version_id",
            "methodology_id",
            "methodology_question_id",
            "methodology_type",
            "source_sha256",
            "semantic_row_hash",
            "question_payload_hash",
            "question_payload",
        },
        "methodology_coverage_records": {
            "selection_id",
            "methodology_version_record_id",
            "methodology_question_record_id",
            "methodology_question_id",
            "methodology_type",
        },
        "methodology_extraction_tasks": {
            "selection_id",
            "coverage_record_id",
            "methodology_version_record_id",
            "methodology_version_id",
            "methodology_question_record_id",
            "methodology_question_id",
            "methodology_type",
            "task_input_sha256",
            "status",
        },
        "methodology_extraction_results": {
            "attempt_id",
            "task_input_sha256",
            "result_sha256",
            "searched_scope_sha256",
            "provider_provenance_sha256",
            "run_output_object_id",
        },
        "methodology_result_factual_outputs": {
            "task_record_id",
            "output_ordinal",
            "factual_output_id",
            "typed_output_payload",
            "typed_output_sha256",
            "accepted_source_span_ids",
            "accepted_locator_sha256s",
            "accepted_locator_set_sha256",
            "run_output_object_id",
        },
        "methodology_result_evidence_links": {
            "claim_link_id",
            "evidence_id",
            "document_id",
            "source_span_id",
            "locator_sha256",
            "run_output_object_id",
        },
        "methodology_result_sanad_links": {
            "claim_link_id",
            "sanad_id",
            "primary_evidence_id",
            "run_output_object_id",
        },
        "deterministic_calculations": {
            "calc_type",
            "inputs",
            "formula_hash",
            "code_version",
            "output",
            "reproducibility_hash",
            "run_id",
            "producer_step_name",
            "run_output_object_id",
        },
    }
    for table, columns in required.items():
        statement = next(
            sql.lower() for sql in _DASHBOARD_SCHEMA if f"create table {table}" in sql.lower()
        )
        missing = {column for column in columns if column not in statement}
        assert not missing, f"{table} projection omits production columns: {sorted(missing)!r}"


@pytest.mark.parametrize("required", (False, True), ids=("optional", "required"))
def test_dashboard_question_fixture_round_trips_task5_model(required: bool) -> None:
    payload = _task5_dashboard_question_payload(required=required)

    question = MethodologyQuestion.model_validate(payload)

    assert question.required_calculations
    assert question.required_calculations[0].calc_type == "FICTIONAL_BRIDGE"
    assert question.required_calculations[0].required is required


@pytest.mark.parametrize(
    "outcome",
    ("EVIDENCE_NOT_FOUND", "EVIDENCE_FOUND"),
    ids=("gap", "found"),
)
def test_dashboard_result_fixture_round_trips_task6_model(
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"],
) -> None:
    result = _validated_dashboard_result(
        outcome=outcome,
        question_id=str(uuid.UUID(int=191)),
    )
    payload = result.model_dump(mode="json")

    assert ValidatedDispatchedMethodologyResult.model_validate(payload) == result
    assert "provider_provenance_sha256" not in payload
    if outcome == "EVIDENCE_FOUND":
        assert len(result.factual_outputs) == 1
        assert isinstance(result.factual_outputs[0], MethodologyFactualOutput)
    else:
        assert result.factual_outputs == ()


def test_dashboard_seed_payload_mutates_a_production_valid_baseline_explicitly() -> None:
    question_id = str(uuid.UUID(int=193))
    legal_payload, legal_gap, legal_factual = _dashboard_result_seed_values(
        outcome="EVIDENCE_NOT_FOUND",
        question_id=question_id,
        searched_document_ids=("00000000-0000-0000-0000-000000000101",),
        searched_span_ids=("00000000-0000-0000-0000-000000000201",),
        searched_scope_sha256=_HASH_A,
        include_factual=False,
        result_payload_override=None,
        gap_finding_override=None,
    )
    assert ValidatedDispatchedMethodologyResult.model_validate(legal_payload)
    assert legal_gap == legal_payload["gap_finding"]
    assert legal_factual is None

    corrupted_payload, corrupted_gap, corrupted_factual = _dashboard_result_seed_values(
        outcome="EVIDENCE_NOT_FOUND",
        question_id=question_id,
        searched_document_ids=(),
        searched_span_ids=("00000000-0000-0000-0000-000000000201",),
        searched_scope_sha256=_HASH_A,
        include_factual=False,
        result_payload_override=None,
        gap_finding_override=None,
    )
    with pytest.raises(ValueError, match="positive searched scope"):
        ValidatedDispatchedMethodologyResult.model_validate(corrupted_payload)
    assert corrupted_gap == legal_gap
    assert corrupted_factual is None
    derived = {"searched_document_ids", "validated_result_sha256"}
    assert {key: value for key, value in corrupted_payload.items() if key not in derived} == {
        key: value for key, value in legal_payload.items() if key not in derived
    }


@pytest.mark.parametrize(
    "outcome,include_factual",
    (
        pytest.param("EVIDENCE_NOT_FOUND", False, id="gap"),
        pytest.param("EVIDENCE_FOUND", True, id="found"),
    ),
)
def test_dashboard_persisted_payloads_round_trip_production_validation(
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"],
    include_factual: bool,
) -> None:
    """Legal seed payloads must survive the same typed round-trip as durable Task 6."""

    production = _validated_dashboard_result(
        outcome=outcome,
        question_id=str(uuid.UUID(int=194)),
    )
    payload, _gap, _factual = _dashboard_result_seed_values(
        outcome=outcome,
        question_id=str(uuid.UUID(int=194)),
        searched_document_ids=("00000000-0000-0000-0000-000000000101",),
        searched_span_ids=("00000000-0000-0000-0000-000000000201",),
        searched_scope_sha256=_HASH_A,
        include_factual=include_factual,
        result_payload_override=None,
        gap_finding_override=None,
    )

    persisted_payload = json.loads(json.dumps(payload, separators=(",", ":")))
    assert persisted_payload == payload
    validated = ValidatedDispatchedMethodologyResult.model_validate(persisted_payload)
    assert validated.validated_result_sha256 == production.validated_result_sha256
    assert validated.validated_result_sha256 == payload["validated_result_sha256"]


def test_dashboard_invalid_found_mutation_rehashes_typed_factual_output_without_masking() -> None:
    """An explicit invalid field survives while nested facts use production coercion."""

    payload, _gap, _factual = _dashboard_result_seed_values(
        outcome="EVIDENCE_FOUND",
        question_id=str(uuid.UUID(int=195)),
        searched_document_ids=(),
        searched_span_ids=("00000000-0000-0000-0000-000000000201",),
        searched_scope_sha256=_HASH_A,
        include_factual=True,
        result_payload_override=None,
        gap_finding_override=None,
    )
    assert payload["searched_document_ids"] == []
    typed_facts = tuple(
        MethodologyFactualOutput.model_validate(item) for item in payload["factual_outputs"]
    )
    typed_draft = ValidatedDispatchedMethodologyResult.model_construct(
        **{**payload, "factual_outputs": typed_facts}
    )
    assert payload["validated_result_sha256"] == typed_draft.expected_validated_result_sha256()
    with pytest.raises(ValueError, match="positive searched scope"):
        ValidatedDispatchedMethodologyResult.model_validate(payload)


def test_dashboard_parent_seed_consumes_production_result_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []

    def fake_result_seed_values(**kwargs: Any) -> tuple[object, object | None, None]:
        calls.append(kwargs)
        return {"fixture": "production-result"}, None, None

    class RecordingConnection:
        def execute(self, _statement: object, values: dict[str, Any]) -> None:
            persisted.append(values)

    monkeypatch.setitem(globals(), "_dashboard_result_seed_values", fake_result_seed_values)

    _seed_dashboard_parent(
        RecordingConnection(),  # type: ignore[arg-type]
        suffix=94,
        question_payload=_task5_dashboard_question_payload(required=False),
        outcome="EVIDENCE_NOT_FOUND",
    )

    assert len(calls) == 1
    assert json.loads(persisted[0]["result_payload"]) == {"fixture": "production-result"}


def test_dashboard_illegal_question_cases_mutate_task5_valid_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[bool] = []
    seeded: list[dict[str, Any]] = []

    def fake_question_payload(*, required: bool) -> dict[str, Any]:
        built.append(required)
        return {
            "required_calculations": [{"calc_type": "FICTIONAL_BRIDGE", "required": required}],
            "baseline_marker": "task5-valid",
        }

    def fake_seed(*_args: object, **kwargs: Any) -> tuple[str, ...]:
        seeded.append(kwargs)
        return (
            "mc_000000000000000000000004",
            str(uuid.UUID(int=304)),
            str(uuid.UUID(int=404)),
            str(uuid.UUID(int=104)),
            str(uuid.UUID(int=204)),
            str(uuid.UUID(int=1004)),
            str(uuid.UUID(int=1204)),
            str(uuid.UUID(int=1404)),
            None,
            None,
        )

    monkeypatch.setitem(globals(), "_task5_dashboard_question_payload", fake_question_payload)
    monkeypatch.setitem(globals(), "_seed_dashboard_parent", fake_seed)
    monkeypatch.setitem(globals(), "_dashboard_insert_sql", lambda *_args, **_kwargs: "fixture SQL")

    _prepare_dashboard_illegal_case(object(), "required-calculations-missing")  # type: ignore[arg-type]

    assert built == [False]
    assert seeded[0]["question_payload"] == {"baseline_marker": "task5-valid"}


def test_dashboard_gap_fixture_matches_task6_structured_result_shape(
    guard_database: DisposablePostgres,
) -> None:
    with guard_database.admin_engine.begin() as connection:
        for statement in _DASHBOARD_SCHEMA:
            connection.exec_driver_sql(statement)
        parent = _seed_dashboard_parent(
            connection,
            suffix=91,
            question_payload=_task5_dashboard_question_payload(required=False),
            outcome="EVIDENCE_NOT_FOUND",
        )
        row = (
            connection.execute(
                text(
                    "SELECT result_payload,gap_finding,searched_document_ids,searched_span_ids,"
                    "searched_scope_sha256,provider_provenance_sha256 "
                    "FROM methodology_extraction_results WHERE result_id=CAST(:result AS uuid)"
                ),
                {"result": parent[1]},
            )
            .mappings()
            .one()
        )

    validated = ValidatedDispatchedMethodologyResult.model_validate(row["result_payload"])
    assert row["gap_finding"] == {
        "code": "GOVERNED_EVIDENCE_GAP",
        "finding": "No accepted evidence matched the governed search scope.",
    }
    assert validated.outcome == "EVIDENCE_NOT_FOUND"
    assert validated.gap_finding is not None
    assert validated.gap_finding.model_dump(mode="json") == row["gap_finding"]
    assert list(validated.searched_document_ids) == [
        str(item) for item in row["searched_document_ids"]
    ]
    assert list(validated.searched_span_ids) == [str(item) for item in row["searched_span_ids"]]
    assert validated.searched_scope_sha256 == row["searched_scope_sha256"]
    assert "provider_provenance_sha256" not in row["result_payload"]
    assert row["provider_provenance_sha256"] == _HASH_A


def test_dashboard_answered_fixture_has_same_result_lineage_roots(
    guard_database: DisposablePostgres,
) -> None:
    with guard_database.admin_engine.begin() as connection:
        for statement in _DASHBOARD_SCHEMA:
            connection.exec_driver_sql(statement)
        parent = _seed_dashboard_parent(
            connection,
            suffix=92,
            question_payload=_task5_dashboard_question_payload(required=False),
            outcome="EVIDENCE_FOUND",
        )
        factual_count = connection.execute(
            text(
                "SELECT count(*) FROM methodology_result_factual_outputs "
                "WHERE result_id=CAST(:result AS uuid)"
            ),
            {"result": parent[1]},
        ).scalar_one()
        assert factual_count == 1
        lineage = (
            connection.execute(
                text(
                    "SELECT r.result_payload,f.typed_output_payload,"
                    "f.run_output_object_id AS factual_root,"
                    "e.run_output_object_id AS evidence_root,"
                    "s.run_output_object_id AS sanad_root "
                    "FROM methodology_extraction_results r "
                    "JOIN methodology_result_factual_outputs f USING "
                    "(tenant_id,deal_id,run_id,result_id) "
                    "JOIN methodology_result_evidence_links e USING "
                    "(tenant_id,deal_id,run_id,result_id,factual_output_record_id) "
                    "JOIN methodology_result_sanad_links s USING "
                    "(tenant_id,deal_id,run_id,result_id,factual_output_record_id) "
                    "WHERE f.result_id=CAST(:result AS uuid)"
                ),
                {"result": parent[1]},
            )
            .mappings()
            .one()
        )

    validated = ValidatedDispatchedMethodologyResult.model_validate(lineage["result_payload"])
    typed_output = MethodologyFactualOutput.model_validate(lineage["typed_output_payload"])
    assert validated.outcome == "EVIDENCE_FOUND"
    assert validated.factual_outputs == (typed_output,)
    assert all(lineage[key] for key in ("factual_root", "evidence_root", "sanad_root"))


_DASHBOARD_LEGAL_CASES = (
    pytest.param("GAP", False, id="gap-no-calculation"),
    pytest.param("ANSWERED", False, id="answered-no-calculation"),
    pytest.param("ANSWERED", True, id="answered-required-calculation"),
    pytest.param("RED_FLAG", False, id="red-flag-no-calculation"),
    pytest.param("RED_FLAG", True, id="red-flag-required-calculation"),
)


@pytest.mark.parametrize("disposition,required", _DASHBOARD_LEGAL_CASES)
def test_dashboard_root_equation_accepts_legal_row(
    guard_database: DisposablePostgres,
    disposition: str,
    required: bool,
) -> None:
    _install(guard_database, "dashboard-root-equation-v1", _DASHBOARD_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        parent = _seed_dashboard_parent(
            connection,
            suffix=1,
            question_payload=_task5_dashboard_question_payload(required=required),
            outcome="EVIDENCE_NOT_FOUND" if disposition == "GAP" else "EVIDENCE_FOUND",
            calculation_child=required,
            adverse_child=disposition == "RED_FLAG",
        )
        evidence = parent[6]
        sanad = parent[7]
        calculation = parent[8]
        connection.exec_driver_sql(
            _dashboard_insert_sql(
                str(uuid.UUID(int=501)),
                parent[0],
                parent[2],
                disposition,
                evidence,
                sanad,
                calculation,
                parent[9],
            )
        )
        _flush(connection)


_DASHBOARD_ILLEGAL_CASES = (
    pytest.param("answered-missing-result", id="answered-missing-result"),
    pytest.param("answered-missing-evidence", id="answered-missing-evidence"),
    pytest.param("answered-missing-sanad", id="answered-missing-sanad"),
    pytest.param("red-flag-missing-result", id="red-flag-missing-result"),
    pytest.param("red-flag-missing-evidence", id="red-flag-missing-evidence"),
    pytest.param("red-flag-missing-sanad", id="red-flag-missing-sanad"),
    pytest.param("answered-required-missing-calculation", id="answered-missing-calculation"),
    pytest.param("red-flag-required-missing-calculation", id="red-flag-missing-calculation"),
    pytest.param("answered-unrequired-extra-calculation", id="answered-extra-calculation"),
    pytest.param("red-flag-unrequired-extra-calculation", id="red-flag-extra-calculation"),
    pytest.param("gap-missing-result", id="gap-missing-result"),
    pytest.param("gap-extra-evidence", id="gap-extra-evidence"),
    pytest.param("gap-extra-sanad", id="gap-extra-sanad"),
    pytest.param("gap-extra-calculation", id="gap-extra-calculation"),
    pytest.param("gap-factual-child", id="gap-factual-child"),
    pytest.param("gap-wrong-outcome", id="gap-wrong-outcome"),
    pytest.param("gap-empty-result-payload", id="gap-empty-result-payload"),
    pytest.param("gap-empty-gap-finding", id="gap-empty-gap-finding"),
    pytest.param("gap-empty-document-search", id="gap-empty-document-search"),
    pytest.param("gap-empty-span-search", id="gap-empty-span-search"),
    pytest.param("gap-invalid-scope-hash", id="gap-invalid-scope-hash"),
    pytest.param("gap-invalid-provider-hash", id="gap-invalid-provider-hash"),
    pytest.param("required-calculations-missing", id="required-calculations-missing"),
    pytest.param("required-calculations-not-array", id="required-calculations-not-array"),
    pytest.param("required-calculation-not-object", id="required-calculation-not-object"),
    pytest.param("required-key-missing", id="required-key-missing"),
    pytest.param("required-value-not-boolean", id="required-value-not-boolean"),
    pytest.param("result-cross-tenant", id="result-cross-tenant"),
    pytest.param("result-cross-deal", id="result-cross-deal"),
    pytest.param("result-cross-run", id="result-cross-run"),
    pytest.param("result-cross-coverage", id="result-cross-coverage"),
)


def _prepare_dashboard_illegal_case(connection: Connection, case: str) -> str:
    disposition = "GAP"
    if case.startswith("answered-"):
        disposition = "ANSWERED"
    elif case.startswith("red-flag-"):
        disposition = "RED_FLAG"
    elif case.startswith("required-"):
        disposition = "ANSWERED"

    required = "required-missing-calculation" in case
    question_payload = _task5_dashboard_question_payload(required=required)
    if case == "required-calculations-missing":
        question_payload.pop("required_calculations")
    elif case == "required-calculations-not-array":
        question_payload["required_calculations"] = {}
    elif case == "required-calculation-not-object":
        question_payload["required_calculations"] = [False]
    elif case == "required-key-missing":
        question_payload["required_calculations"] = [{"calc_type": "FICTIONAL_BRIDGE"}]
    elif case == "required-value-not-boolean":
        question_payload["required_calculations"] = [
            {"calc_type": "FICTIONAL_BRIDGE", "required": "true"}
        ]

    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"] = (
        "EVIDENCE_NOT_FOUND" if disposition == "GAP" else "EVIDENCE_FOUND"
    )
    if case == "gap-wrong-outcome":
        outcome = "EVIDENCE_FOUND"
    result_payload: object | None = {} if case == "gap-empty-result-payload" else None
    gap_finding: object | None = {} if case == "gap-empty-gap-finding" else None
    documents = (
        () if case == "gap-empty-document-search" else ("00000000-0000-0000-0000-000000000101",)
    )
    spans = () if case == "gap-empty-span-search" else ("00000000-0000-0000-0000-000000000201",)
    result_tenant = _OTHER_TENANT if case == "result-cross-tenant" else _TENANT
    result_deal = _OTHER_DEAL if case == "result-cross-deal" else _DEAL
    result_run = _OTHER_RUN if case == "result-cross-run" else _RUN
    parent = _seed_dashboard_parent(
        connection,
        suffix=4,
        question_payload=question_payload,
        outcome=outcome,
        result_payload=result_payload,
        gap_finding=gap_finding,
        searched_document_ids=documents,
        searched_span_ids=spans,
        searched_scope_sha256="bad" if case == "gap-invalid-scope-hash" else _HASH_A,
        provider_provenance_sha256=("bad" if case == "gap-invalid-provider-hash" else _HASH_A),
        result_tenant=result_tenant,
        result_deal=result_deal,
        result_run=result_run,
        task_coverage_id=(
            "mc_ffffffffffffffffffffffff" if case == "result-cross-coverage" else None
        ),
        factual_child=(
            True if case == "gap-factual-child" else False if case == "gap-wrong-outcome" else None
        ),
        calculation_child="extra-calculation" in case,
        adverse_child=disposition == "RED_FLAG",
    )

    evidence = parent[6]
    sanad = parent[7]
    calculation = parent[8]
    if disposition == "GAP":
        # Preserve the legal GAP root equation unless this case targets one root.
        evidence = None
        sanad = None
    if case == "gap-extra-evidence":
        evidence = str(uuid.UUID(int=605))
        _seed_simple_root(connection, evidence, "METHODOLOGY_EVIDENCE_LINK")
    if case == "gap-extra-sanad":
        sanad = str(uuid.UUID(int=705))
        _seed_simple_root(connection, sanad, "METHODOLOGY_SANAD_LINK")

    result_root: str | None = parent[2]
    if case in {"answered-missing-result", "red-flag-missing-result", "gap-missing-result"}:
        result_root = None
    if case in {"answered-missing-evidence", "red-flag-missing-evidence"}:
        evidence = None
    if case in {"answered-missing-sanad", "red-flag-missing-sanad"}:
        sanad = None
    return _dashboard_insert_sql(
        str(uuid.UUID(int=504)),
        parent[0],
        result_root,
        disposition,
        evidence,
        sanad,
        calculation,
        parent[9],
    )


@pytest.mark.parametrize(
    "case,expected_outcome,expected_factual_child",
    (
        ("gap-factual-child", "EVIDENCE_NOT_FOUND", True),
        ("gap-wrong-outcome", "EVIDENCE_FOUND", False),
    ),
)
def test_dashboard_gap_corruption_fixtures_do_not_mask_with_nonnull_roots(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_outcome: str,
    expected_factual_child: bool,
) -> None:
    seed_arguments: dict[str, Any] = {}
    dashboard_arguments: dict[str, Any] = {}

    def fake_seed(*_args: object, **kwargs: Any) -> tuple[str, ...]:
        seed_arguments.update(kwargs)
        return (
            "mc_000000000000000000000004",
            str(uuid.UUID(int=304)),
            str(uuid.UUID(int=404)),
            str(uuid.UUID(int=104)),
            str(uuid.UUID(int=204)),
            str(uuid.UUID(int=1004)),
            str(uuid.UUID(int=1204)),
            str(uuid.UUID(int=1404)),
            None,
            None,
        )

    def fake_dashboard_insert(*args: object, **_kwargs: object) -> str:
        (
            _,
            _,
            _,
            disposition,
            evidence,
            sanad,
            calculation,
            adverse,
        ) = args
        dashboard_arguments.update(
            disposition=disposition,
            evidence=evidence,
            sanad=sanad,
            calculation=calculation,
            adverse=adverse,
        )
        return "fixture SQL"

    monkeypatch.setitem(globals(), "_seed_dashboard_parent", fake_seed)
    monkeypatch.setitem(globals(), "_dashboard_insert_sql", fake_dashboard_insert)

    _prepare_dashboard_illegal_case(object(), case)  # type: ignore[arg-type]

    assert seed_arguments["outcome"] == expected_outcome
    assert seed_arguments["factual_child"] is expected_factual_child
    assert dashboard_arguments == {
        "disposition": "GAP",
        "evidence": None,
        "sanad": None,
        "calculation": None,
        "adverse": None,
    }


@pytest.mark.parametrize("case", _DASHBOARD_ILLEGAL_CASES)
def test_dashboard_root_equation_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "dashboard-root-equation-v1", _DASHBOARD_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "dashboard-root-equation-v1",
            (_prepare_dashboard_illegal_case(connection, case),),
        )


@pytest.mark.parametrize(
    "case",
    (
        "answered-required-missing-calculation",
        "red-flag-missing-evidence",
        "gap-wrong-outcome",
        "required-value-not-boolean",
        "result-cross-run",
    ),
)
def test_dashboard_root_equation_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "dashboard-root-equation-v1", _DASHBOARD_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        insert_sql = _prepare_dashboard_illegal_case(connection, case)
    _raw_expect_rejected(
        guard_database,
        "dashboard-root-equation-v1",
        (insert_sql,),
    )


_NAMESPACE_TABLES = (
    ("graph_projection_results", "graph_result_id"),
    ("rag_index_results", "rag_index_result_id"),
    ("rag_query_results", "rag_query_result_id"),
)

_NAMESPACE_SCHEMA = tuple(
    f"""
    CREATE TABLE {table} (
        {primary_key} UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        namespace TEXT NOT NULL,
        provider_id TEXT,
        fact_domain TEXT
        {", rag_index_result_id UUID" if table == "rag_query_results" else ""}
    )
    """
    for table, primary_key in _NAMESPACE_TABLES
)

_NAMESPACE_INVALID_ROWS = (
    pytest.param("external_context", None, "commercial_diligence", id="external-null-provider"),
    pytest.param("external_context", "sec_edgar", None, id="external-null-domain"),
    pytest.param(
        "external_context",
        "not_a_provider",
        "commercial_diligence",
        id="external-unknown-provider",
    ),
    pytest.param("external_context", "sec_edgar", "unknown_domain", id="external-unknown-domain"),
    pytest.param("internal_evidence", "sec_edgar", None, id="internal-nonnull-provider"),
    pytest.param(
        "internal_evidence",
        None,
        "commercial_diligence",
        id="internal-nonnull-domain",
    ),
    pytest.param("unknown_namespace", None, None, id="unknown-namespace"),
)


def _namespace_insert(
    table: str,
    primary_key: str,
    row_id: int,
    namespace: str,
    provider: str | None,
    domain: str | None,
) -> str:
    parent = ",rag_index_result_id" if table == "rag_query_results" else ""
    parent_value = ",NULL" if table == "rag_query_results" else ""
    provider_sql = "NULL" if provider is None else f"'{provider}'"
    domain_sql = "NULL" if domain is None else f"'{domain}'"
    return (
        f"INSERT INTO {table} ({primary_key},tenant_id,deal_id,run_id,namespace,"
        f"provider_id,fact_domain{parent}) VALUES ('{uuid.UUID(int=row_id)}'::uuid,"
        f"'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,'{namespace}',"
        f"{provider_sql},{domain_sql}{parent_value})"
    )


def test_graph_rag_namespace_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(guard_database, "graph-rag-namespace-v1", _NAMESPACE_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        for offset, (table, pk) in enumerate(_NAMESPACE_TABLES):
            connection.exec_driver_sql(
                _namespace_insert(table, pk, 1000 + offset, "internal_evidence", None, None)
            )
            connection.exec_driver_sql(
                _namespace_insert(
                    table,
                    pk,
                    1010 + offset,
                    "external_context",
                    "semantic_scholar",
                    "commercial_diligence",
                )
            )
        _flush(connection)


@pytest.mark.parametrize("table,primary_key", _NAMESPACE_TABLES, ids=lambda value: value)
@pytest.mark.parametrize("namespace,provider,domain", _NAMESPACE_INVALID_ROWS)
def test_graph_rag_namespace_rejects_illegal_row(
    guard_database: DisposablePostgres,
    table: str,
    primary_key: str,
    namespace: str,
    provider: str | None,
    domain: str | None,
) -> None:
    _install(guard_database, "graph-rag-namespace-v1", _NAMESPACE_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "graph-rag-namespace-v1",
            (
                _namespace_insert(
                    table,
                    primary_key,
                    1020,
                    namespace,
                    provider,
                    domain,
                ),
            ),
        )


@pytest.mark.parametrize("table,primary_key", _NAMESPACE_TABLES, ids=lambda value: value)
def test_graph_rag_namespace_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    table: str,
    primary_key: str,
) -> None:
    _install(guard_database, "graph-rag-namespace-v1", _NAMESPACE_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "graph-rag-namespace-v1",
        (
            _namespace_insert(
                table,
                primary_key,
                1030,
                "internal_evidence",
                "sec_edgar",
                None,
            ),
        ),
    )


_RAG_PARENT_SCHEMA = (
    """
    CREATE TABLE rag_index_results (
        rag_index_result_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL, namespace TEXT NOT NULL,
        provider_id TEXT, fact_domain TEXT
    )
    """,
    """
    CREATE TABLE rag_query_results (
        rag_query_result_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL, rag_index_result_id UUID NOT NULL,
        namespace TEXT NOT NULL, provider_id TEXT, fact_domain TEXT
    )
    """,
)


def _rag_parent_rows(
    *,
    seed: int,
    parent_namespace: str,
    parent_provider: str | None,
    parent_domain: str | None,
    child_namespace: str,
    child_provider: str | None,
    child_domain: str | None,
    child_tenant: str = _TENANT,
    child_deal: str = _DEAL,
    child_run: str = _RUN,
) -> tuple[str, str]:
    parent = str(uuid.UUID(int=1100 + seed * 2))
    child = str(uuid.UUID(int=1101 + seed * 2))
    parent_provider_sql = "NULL" if parent_provider is None else f"'{parent_provider}'"
    parent_domain_sql = "NULL" if parent_domain is None else f"'{parent_domain}'"
    provider = "NULL" if child_provider is None else f"'{child_provider}'"
    domain = "NULL" if child_domain is None else f"'{child_domain}'"
    return (
        "INSERT INTO rag_index_results VALUES "
        f"('{parent}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
        f"'{parent_namespace}',{parent_provider_sql},{parent_domain_sql})",
        "INSERT INTO rag_query_results VALUES "
        f"('{child}'::uuid,'{child_tenant}'::uuid,"
        f"'{child_deal}'::uuid,'{child_run}'::uuid,'{parent}'::uuid,'{child_namespace}',"
        f"{provider},{domain})",
    )


def _rag_external_rows(seed: int, **overrides: str | None) -> tuple[str, str]:
    values: dict[str, str | None] = {
        "parent_namespace": "external_context",
        "parent_provider": "sec_edgar",
        "parent_domain": "commercial_diligence",
        "child_namespace": "external_context",
        "child_provider": "sec_edgar",
        "child_domain": "commercial_diligence",
        "child_tenant": _TENANT,
        "child_deal": _DEAL,
        "child_run": _RUN,
    }
    values.update(overrides)
    return _rag_parent_rows(seed=seed, **values)  # type: ignore[arg-type]


_RAG_COORDINATE_MISMATCHES = (
    pytest.param("child_namespace", "internal_evidence", id="namespace"),
    pytest.param("child_provider", "perplexity", id="provider"),
    pytest.param("child_domain", "financial_diligence", id="domain"),
    pytest.param("child_tenant", _OTHER_TENANT, id="tenant"),
    pytest.param("child_deal", _OTHER_DEAL, id="deal"),
    pytest.param("child_run", _OTHER_RUN, id="run"),
)


def test_rag_query_parent_coordinate_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(guard_database, "rag-query-parent-coordinate-v1", _RAG_PARENT_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        external_rows = _rag_external_rows(1)
        internal_rows = _rag_parent_rows(
            seed=2,
            parent_namespace="internal_evidence",
            parent_provider=None,
            parent_domain=None,
            child_namespace="internal_evidence",
            child_provider=None,
            child_domain=None,
        )
        for statement in (*external_rows, *internal_rows):
            connection.exec_driver_sql(statement)
        _flush(connection)


@pytest.mark.parametrize("field,value", _RAG_COORDINATE_MISMATCHES)
def test_rag_query_parent_coordinate_rejects_illegal_row(
    guard_database: DisposablePostgres,
    field: str,
    value: str,
) -> None:
    _install(guard_database, "rag-query-parent-coordinate-v1", _RAG_PARENT_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "rag-query-parent-coordinate-v1",
            _rag_external_rows(3, **{field: value}),
        )


@pytest.mark.parametrize("field,value", _RAG_COORDINATE_MISMATCHES)
def test_rag_query_parent_coordinate_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    field: str,
    value: str,
) -> None:
    _install(guard_database, "rag-query-parent-coordinate-v1", _RAG_PARENT_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "rag-query-parent-coordinate-v1",
        _rag_external_rows(4, **{field: value}),
    )


_READINESS_SCHEMA = (
    """
    CREATE TABLE layer2_readiness_outputs (
        readiness_output_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL, status TEXT NOT NULL,
        required_projection_sha256 TEXT NOT NULL,
        observed_projection_sha256 TEXT NOT NULL,
        blocker_count INTEGER NOT NULL, deferred_count INTEGER NOT NULL,
        default_count INTEGER NOT NULL
    )
    """,
)


def _readiness_insert(
    row_id: int,
    *,
    status: str = "READY",
    observed: str = _HASH_A,
    blockers: int = 0,
    deferred: int = 0,
    defaults: int = 0,
) -> str:
    return (
        "INSERT INTO layer2_readiness_outputs VALUES "
        f"('{uuid.UUID(int=row_id)}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_RUN}'::uuid,'{status}','{_HASH_A}','{observed}',{blockers},{deferred},{defaults})"
    )


_READINESS_INVALID_ROWS = (
    pytest.param({"status": "PARTIAL"}, id="status"),
    pytest.param({"blockers": 1}, id="blocker-count"),
    pytest.param({"deferred": 1}, id="deferred-count"),
    pytest.param({"defaults": 1}, id="default-count"),
    pytest.param({"observed": _HASH_B}, id="projection-hash"),
)


def test_layer2_readiness_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(guard_database, "layer2-readiness-v1", _READINESS_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        connection.exec_driver_sql(_readiness_insert(1201))
        _flush(connection)


@pytest.mark.parametrize("overrides", _READINESS_INVALID_ROWS)
def test_layer2_readiness_rejects_illegal_row(
    guard_database: DisposablePostgres,
    overrides: dict[str, str | int],
) -> None:
    _install(guard_database, "layer2-readiness-v1", _READINESS_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "layer2-readiness-v1",
            (_readiness_insert(1202, **overrides),),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("overrides", _READINESS_INVALID_ROWS)
def test_layer2_readiness_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    overrides: dict[str, str | int],
) -> None:
    _install(guard_database, "layer2-readiness-v1", _READINESS_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "layer2-readiness-v1",
        (_readiness_insert(1203, **overrides),),  # type: ignore[arg-type]
    )


_EXTRACT_SCHEMA = (
    """
    CREATE TABLE run_step_outputs (
        step_output_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        step_name TEXT NOT NULL, subrole TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE generic_extraction_outputs (
        generic_output_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        rag_context_step_output_id UUID NOT NULL
    )
    """,
)


def _extract_rows(
    *,
    step: str = "EXTRACT",
    subrole: str = "rag_context",
    parent_tenant: str = _TENANT,
    parent_deal: str = _DEAL,
    parent_run: str = _RUN,
    child_tenant: str = _TENANT,
    child_deal: str = _DEAL,
    child_run: str = _RUN,
) -> tuple[str, str]:
    parent = "00000000-0000-0000-0000-000000001301"
    return (
        "INSERT INTO run_step_outputs VALUES "
        f"('{parent}'::uuid,'{parent_tenant}'::uuid,'{parent_deal}'::uuid,"
        f"'{parent_run}'::uuid,"
        f"'{step}','{subrole}')",
        "INSERT INTO generic_extraction_outputs VALUES "
        f"('00000000-0000-0000-0000-000000001302'::uuid,'{child_tenant}'::uuid,"
        f"'{child_deal}'::uuid,'{child_run}'::uuid,'{parent}'::uuid)",
    )


_EXTRACT_COORDINATE_MISMATCHES = (
    pytest.param({"step": "RAG_EVIDENCE"}, id="step"),
    pytest.param({"subrole": "internal_evidence"}, id="subrole"),
    pytest.param({"child_tenant": _OTHER_TENANT}, id="tenant"),
    pytest.param({"child_deal": _OTHER_DEAL}, id="deal"),
    pytest.param({"child_run": _OTHER_RUN}, id="run"),
)


def test_extract_rag_context_coordinate_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(guard_database, "extract-rag-context-coordinate-v1", _EXTRACT_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        for statement in _extract_rows():
            connection.exec_driver_sql(statement)
        _flush(connection)


@pytest.mark.parametrize("overrides", _EXTRACT_COORDINATE_MISMATCHES)
def test_extract_rag_context_coordinate_rejects_illegal_row(
    guard_database: DisposablePostgres,
    overrides: dict[str, str],
) -> None:
    _install(guard_database, "extract-rag-context-coordinate-v1", _EXTRACT_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "extract-rag-context-coordinate-v1",
            _extract_rows(**overrides),
        )


@pytest.mark.parametrize("overrides", _EXTRACT_COORDINATE_MISMATCHES)
def test_extract_rag_context_coordinate_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    overrides: dict[str, str],
) -> None:
    _install(guard_database, "extract-rag-context-coordinate-v1", _EXTRACT_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "extract-rag-context-coordinate-v1",
        _extract_rows(**overrides),
    )


def _muhasabah_schema(table: str, primary_key: str, step: str, kind: str) -> tuple[str, ...]:
    return (
        """
        CREATE TABLE muhasabah_records (
            record_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL, run_id UUID NOT NULL,
            source_step VARCHAR(50) NOT NULL, agent_id VARCHAR(255) NOT NULL,
            output_id VARCHAR(255) NOT NULL, run_output_object_kind TEXT NOT NULL,
            CONSTRAINT uq_test_muhasabah_parent UNIQUE (
                tenant_id,deal_id,run_id,record_id,source_step,agent_id,
                output_id,run_output_object_kind
            )
        )
        """,
        f"""
        CREATE TABLE {table} (
            {primary_key} UUID PRIMARY KEY,
            tenant_id UUID NOT NULL, deal_id UUID NOT NULL, run_id UUID NOT NULL,
            role TEXT NOT NULL, muhasabah_record_id UUID NOT NULL,
            muhasabah_source_step VARCHAR(50) GENERATED ALWAYS AS ('{step}') STORED,
            muhasabah_agent_id VARCHAR(255) GENERATED ALWAYS AS (role::varchar(255)) STORED,
            muhasabah_output_id VARCHAR(255)
                GENERATED ALWAYS AS ({primary_key}::text) STORED,
            muhasabah_output_kind TEXT GENERATED ALWAYS AS ('{kind}') STORED
        )
        """,
    )


def _muhasabah_rows(
    table: str,
    primary_key: str,
    step: str,
    kind: str,
    role: str,
    *,
    case: str = "legal",
) -> tuple[str, ...]:
    output_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{table}-output"))
    record_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{table}-record"))
    record_tenant = _OTHER_TENANT if case == "tenant" else _TENANT
    record_deal = _OTHER_DEAL if case == "deal" else _DEAL
    record_run = _OTHER_RUN if case == "run" else _RUN
    record_step = "WRONG_STEP" if case == "step" else step
    record_agent = "wrong_agent" if case == "agent" else role
    record_output = (
        str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{table}-wrong-output"))
        if case == "output"
        else output_id
    )
    record_kind = "WRONG_MUHASABAH" if case == "kind" else kind
    native_record_id = (
        str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{table}-wrong-record"))
        if case == "record-id"
        else record_id
    )
    duplicate_record_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{table}-duplicate-record"))
    rows = [
        "INSERT INTO muhasabah_records VALUES "
        f"('{record_id}'::uuid,'{record_tenant}'::uuid,'{record_deal}'::uuid,"
        f"'{record_run}'::uuid,'{record_step}','{record_agent}','{record_output}',"
        f"'{record_kind}')"
    ]
    if case == "duplicate":
        rows.append(
            "INSERT INTO muhasabah_records VALUES "
            f"('{duplicate_record_id}'::uuid,"
            f"'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
            f"'{step}','{role}','{output_id}','{kind}')"
        )
    rows.append(
        f"INSERT INTO {table} ({primary_key},tenant_id,deal_id,run_id,role,"
        "muhasabah_record_id) VALUES "
        f"('{output_id}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
        f"'{role}','{native_record_id}'::uuid)"
    )
    return tuple(rows)


_MUHASABAH_ILLEGAL_CASES = tuple(
    pytest.param(case, id=case)
    for case in (
        "duplicate",
        "record-id",
        "step",
        "agent",
        "output",
        "kind",
        "tenant",
        "deal",
        "run",
    )
)


def _muhasabah_legal(
    database: DisposablePostgres,
    authority: str,
    table: str,
    primary_key: str,
    step: str,
    kind: str,
    role: str,
) -> None:
    _install(database, authority, _muhasabah_schema(table, primary_key, step, kind))
    with database.admin_engine.begin() as connection:
        for statement in _muhasabah_rows(table, primary_key, step, kind, role):
            connection.exec_driver_sql(statement)
        _flush(connection)


def _muhasabah_illegal(
    database: DisposablePostgres,
    authority: str,
    table: str,
    primary_key: str,
    step: str,
    kind: str,
    role: str,
    case: str,
) -> None:
    _install(database, authority, _muhasabah_schema(table, primary_key, step, kind))
    with database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            authority,
            _muhasabah_rows(
                table,
                primary_key,
                step,
                kind,
                role,
                case=case,
            ),
        )


def _muhasabah_direct(
    database: DisposablePostgres,
    authority: str,
    table: str,
    primary_key: str,
    step: str,
    kind: str,
    role: str,
    case: str,
) -> None:
    _install(database, authority, _muhasabah_schema(table, primary_key, step, kind))
    _raw_expect_rejected(
        database,
        authority,
        _muhasabah_rows(
            table,
            primary_key,
            step,
            kind,
            role,
            case=case,
        ),
    )


def test_debate_muhasabah_exact_one_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _muhasabah_legal(
        guard_database,
        "debate-muhasabah-exact-one-v1",
        "debate_role_outputs",
        "debate_role_output_id",
        "DEBATE",
        "DEBATE_MUHASABAH",
        "optimist",
    )


@pytest.mark.parametrize("case", _MUHASABAH_ILLEGAL_CASES)
def test_debate_muhasabah_exact_one_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _muhasabah_illegal(
        guard_database,
        "debate-muhasabah-exact-one-v1",
        "debate_role_outputs",
        "debate_role_output_id",
        "DEBATE",
        "DEBATE_MUHASABAH",
        "optimist",
        case,
    )


@pytest.mark.parametrize("case", _MUHASABAH_ILLEGAL_CASES)
def test_debate_muhasabah_exact_one_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _muhasabah_direct(
        guard_database,
        "debate-muhasabah-exact-one-v1",
        "debate_role_outputs",
        "debate_role_output_id",
        "DEBATE",
        "DEBATE_MUHASABAH",
        "optimist",
        case,
    )


def test_analysis_muhasabah_exact_one_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _muhasabah_legal(
        guard_database,
        "analysis-muhasabah-exact-one-v1",
        "analysis_agent_outputs",
        "analysis_output_id",
        "ANALYSIS",
        "ANALYSIS_MUHASABAH",
        "financial_agent",
    )


@pytest.mark.parametrize("case", _MUHASABAH_ILLEGAL_CASES)
def test_analysis_muhasabah_exact_one_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _muhasabah_illegal(
        guard_database,
        "analysis-muhasabah-exact-one-v1",
        "analysis_agent_outputs",
        "analysis_output_id",
        "ANALYSIS",
        "ANALYSIS_MUHASABAH",
        "financial_agent",
        case,
    )


@pytest.mark.parametrize("case", _MUHASABAH_ILLEGAL_CASES)
def test_analysis_muhasabah_exact_one_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _muhasabah_direct(
        guard_database,
        "analysis-muhasabah-exact-one-v1",
        "analysis_agent_outputs",
        "analysis_output_id",
        "ANALYSIS",
        "ANALYSIS_MUHASABAH",
        "financial_agent",
        case,
    )


def test_layer2_muhasabah_exact_one_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _muhasabah_legal(
        guard_database,
        "layer2-muhasabah-exact-one-v1",
        "layer2_role_outputs",
        "layer2_role_output_id",
        "LAYER2_IC_CHALLENGE",
        "LAYER2_MUHASABAH",
        "ic_advocate",
    )


@pytest.mark.parametrize("case", _MUHASABAH_ILLEGAL_CASES)
def test_layer2_muhasabah_exact_one_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _muhasabah_illegal(
        guard_database,
        "layer2-muhasabah-exact-one-v1",
        "layer2_role_outputs",
        "layer2_role_output_id",
        "LAYER2_IC_CHALLENGE",
        "LAYER2_MUHASABAH",
        "ic_advocate",
        case,
    )


@pytest.mark.parametrize("case", _MUHASABAH_ILLEGAL_CASES)
def test_layer2_muhasabah_exact_one_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _muhasabah_direct(
        guard_database,
        "layer2-muhasabah-exact-one-v1",
        "layer2_role_outputs",
        "layer2_role_output_id",
        "LAYER2_IC_CHALLENGE",
        "LAYER2_MUHASABAH",
        "ic_advocate",
        case,
    )


_GRADING_AUTHORITY = "grading-dispatch-and-consistency-v1"
_GRADING_METHOD_CLAIM_ROOT = "00000000-0000-0000-0000-000000001801"
_GRADING_GENERIC_CLAIM_ROOT = "00000000-0000-0000-0000-000000001802"
_GRADING_METHOD_GRADE_ROOT = "00000000-0000-0000-0000-000000001803"
_GRADING_GENERIC_GRADE_ROOT = "00000000-0000-0000-0000-000000001804"
_GRADING_CLAIM = "00000000-0000-0000-0000-000000001805"
_GRADING_SANAD = "00000000-0000-0000-0000-000000001806"
_GRADING_METHOD_CLAIM_LINK = "00000000-0000-0000-0000-000000001807"
_GRADING_GENERIC_CLAIM_LINK = "00000000-0000-0000-0000-000000001808"
_GRADING_METHOD_SANAD_LINK = "00000000-0000-0000-0000-000000001809"
_GRADING_METHOD_SANAD_ROOT = "00000000-0000-0000-0000-000000001810"


_GRADING_SCHEMA = (
    _ROOT_SQL,
    """
    CREATE TABLE claims (
        claim_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE sanads (
        sanad_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        claim_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_result_claim_links (
        claim_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL,
        claim_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE generic_extraction_claim_links (
        generic_claim_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        generic_output_id UUID NOT NULL,
        generic_output_object_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL,
        claim_ordinal INTEGER NOT NULL,
        claim_id UUID NOT NULL,
        canonical_key_sha256 TEXT NOT NULL,
        reused_existing_claim BOOLEAN NOT NULL
    )
    """,
    """
    CREATE TABLE methodology_result_sanad_links (
        sanad_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        claim_link_id UUID NOT NULL,
        sanad_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE run_claim_grading_links (
        grading_link_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        source_claim_output_object_id UUID NOT NULL,
        source_claim_output_class TEXT NOT NULL,
        source_claim_output_kind TEXT NOT NULL,
        source_claim_step_name TEXT NOT NULL,
        claim_id UUID NOT NULL,
        methodology_sanad_link_id UUID,
        sanad_id UUID NOT NULL,
        grade TEXT NOT NULL,
        grading_sha256 TEXT NOT NULL
    )
    """,
)


def _grading_rows(*, divergent_semantic_tuple: bool = False) -> tuple[str, ...]:
    second_grade = "B" if divergent_semantic_tuple else "A"
    second_hash = _HASH_B if divergent_semantic_tuple else _HASH_A
    return (
        _root_insert(
            _GRADING_METHOD_CLAIM_ROOT,
            "NATIVE_ROW",
            "METHODOLOGY_CLAIM_LINK",
        ),
        _root_insert(
            _GRADING_GENERIC_CLAIM_ROOT,
            "NATIVE_ROW",
            "METHODOLOGY_CLAIM_LINK",
        ),
        _root_insert(
            _GRADING_METHOD_GRADE_ROOT,
            "NATIVE_ROW",
            "RUN_CLAIM_GRADING_LINK",
        ),
        _root_insert(
            _GRADING_GENERIC_GRADE_ROOT,
            "NATIVE_ROW",
            "RUN_CLAIM_GRADING_LINK",
        ),
        _root_insert(
            _GRADING_METHOD_SANAD_ROOT,
            "NATIVE_ROW",
            "METHODOLOGY_SANAD_LINK",
        ),
        f"INSERT INTO claims VALUES ('{_GRADING_CLAIM}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid)",
        "INSERT INTO sanads VALUES "
        f"('{_GRADING_SANAD}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_GRADING_CLAIM}'::uuid)",
        "INSERT INTO methodology_result_claim_links VALUES "
        f"('{_GRADING_METHOD_CLAIM_LINK}'::uuid,'{_TENANT}'::uuid,"
        f"'{_DEAL}'::uuid,'{_RUN}'::uuid,'{_GRADING_METHOD_CLAIM_ROOT}'::uuid,"
        f"'NATIVE_ROW','METHODOLOGY_CLAIM_LINK','{_GRADING_CLAIM}'::uuid)",
        "INSERT INTO generic_extraction_claim_links VALUES "
        f"('{_GRADING_GENERIC_CLAIM_LINK}'::uuid,'{_TENANT}'::uuid,"
        f"'{_DEAL}'::uuid,'{_RUN}'::uuid,'{uuid.UUID(int=1810)}'::uuid,"
        f"'{uuid.UUID(int=1811)}'::uuid,'{_GRADING_GENERIC_CLAIM_ROOT}'::uuid,"
        f"'NATIVE_ROW','METHODOLOGY_CLAIM_LINK',0,'{_GRADING_CLAIM}'::uuid,"
        f"'{_HASH_A}',true)",
        "INSERT INTO methodology_result_sanad_links VALUES "
        f"('{_GRADING_METHOD_SANAD_LINK}'::uuid,'{_TENANT}'::uuid,"
        f"'{_DEAL}'::uuid,'{_RUN}'::uuid,'{_GRADING_METHOD_CLAIM_LINK}'::uuid,"
        f"'{_GRADING_SANAD}'::uuid,'{_GRADING_METHOD_SANAD_ROOT}'::uuid,"
        "'NATIVE_ROW','METHODOLOGY_SANAD_LINK')",
        "INSERT INTO run_claim_grading_links VALUES "
        f"('{uuid.UUID(int=1813)}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_RUN}'::uuid,'{_GRADING_METHOD_GRADE_ROOT}'::uuid,"
        f"'{_GRADING_METHOD_CLAIM_ROOT}'::uuid,'NATIVE_ROW',"
        f"'METHODOLOGY_CLAIM_LINK','METHODOLOGY_CLAIM_MATERIALIZATION',"
        f"'{_GRADING_CLAIM}'::uuid,'{_GRADING_METHOD_SANAD_LINK}'::uuid,"
        f"'{_GRADING_SANAD}'::uuid,'A','{_HASH_A}')",
        "INSERT INTO run_claim_grading_links VALUES "
        f"('{uuid.UUID(int=1814)}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_RUN}'::uuid,'{_GRADING_GENERIC_GRADE_ROOT}'::uuid,"
        f"'{_GRADING_GENERIC_CLAIM_ROOT}'::uuid,'NATIVE_ROW',"
        f"'METHODOLOGY_CLAIM_LINK','EXTRACT','{_GRADING_CLAIM}'::uuid,NULL,"
        f"'{_GRADING_SANAD}'::uuid,'{second_grade}','{second_hash}')",
    )


def test_grading_dispatch_and_consistency_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    """Distinct source roots may reuse a claim only with one semantic grade tuple."""

    _install(guard_database, _GRADING_AUTHORITY, _GRADING_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        for statement in _grading_rows():
            connection.exec_driver_sql(statement)
        _flush(connection)


def test_grading_dispatch_and_consistency_rejects_illegal_row(
    guard_database: DisposablePostgres,
) -> None:
    """Two individually valid grades cannot diverge for one canonical claim/run."""

    _install(guard_database, _GRADING_AUTHORITY, _GRADING_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            _GRADING_AUTHORITY,
            _grading_rows(divergent_semantic_tuple=True),
        )


def test_grading_dispatch_and_consistency_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
) -> None:
    """The application role cannot insert divergent physical grade projections."""

    _install(guard_database, _GRADING_AUTHORITY, _GRADING_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        _GRADING_AUTHORITY,
        _grading_rows(divergent_semantic_tuple=True),
    )


_ERASURE_AUTHORITY = "approved-erasure-append-only-v1"


def _erasure_target_tables() -> tuple[str, ...]:
    targets = _authority_row(_ERASURE_AUTHORITY)["target_tuple"]
    assert isinstance(targets, list) and targets
    tables: list[str] = []
    for target in targets:
        assert isinstance(target, list) and len(target) == 2
        table, trigger_name = target
        assert isinstance(table, str) and table.replace("_", "").isalnum()
        assert isinstance(trigger_name, str) and trigger_name.replace("_", "").isalnum()
        tables.append(table)
    return tuple(tables)


def _install_erasure_append_only_guard(database: GuardDatabase) -> str:
    """Install the FUTURE38 guard authority; runner bytes live in a separate registry."""

    tables = _erasure_target_tables()
    statements = _authority_sql(_ERASURE_AUTHORITY)
    guard_statements = statements
    assert guard_statements
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
        connection.exec_driver_sql(
            """
            CREATE TABLE erasure_requests (
                request_id UUID PRIMARY KEY,
                tenant_id UUID NOT NULL,
                deal_id UUID NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        for table in tables:
            connection.exec_driver_sql(
                f"""
                CREATE TABLE {table} (
                    task7_test_row_id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL,
                    deal_id UUID NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
        for statement in guard_statements:
            connection.exec_driver_sql(statement)
        # Mirror the reviewed production ACL so PostgreSQL reaches the guard's
        # stable rejection instead of an incidental owner-privilege error.
        connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner")
        connection.exec_driver_sql(
            "GRANT SELECT (request_id,tenant_id,deal_id,status) "
            "ON TABLE public.erasure_requests "
            "TO idis_erasure_function_owner"
        )
        connection.exec_driver_sql(
            "GRANT UPDATE (request_id) "
            "ON TABLE public.erasure_requests "
            "TO idis_erasure_function_owner"
        )
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{database.role_name}"')
        connection.exec_driver_sql(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f'TO "{database.role_name}"'
        )
    return tables[0]


def _erasure_insert(table: str) -> str:
    return (
        f"INSERT INTO {table} VALUES ('{uuid.UUID(int=1901)}'::uuid,"
        f"'{_TENANT}'::uuid,'{_DEAL}'::uuid,'original')"
    )


def test_approved_erasure_append_only_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    """Normal evidence creation remains legal under the append-only authority."""

    table = _install_erasure_append_only_guard(guard_database)
    with guard_database.admin_engine.begin() as connection:
        connection.exec_driver_sql(_erasure_insert(table))
        _flush(connection)


def test_approved_erasure_append_only_rejects_illegal_row(
    guard_database: DisposablePostgres,
) -> None:
    """A direct mutation without the dedicated erasure callable is rejected."""

    table = _install_erasure_append_only_guard(guard_database)
    with guard_database.admin_engine.begin() as connection:
        connection.exec_driver_sql(_erasure_insert(table))
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            _ERASURE_AUTHORITY,
            (f"UPDATE {table} SET payload='changed'",),
        )


def test_approved_erasure_append_only_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
) -> None:
    """A forged transaction-local marker grants the application role no authority."""

    table = _install_erasure_append_only_guard(guard_database)
    with guard_database.admin_engine.begin() as connection:
        connection.exec_driver_sql(_erasure_insert(table))
    _raw_expect_rejected(
        guard_database,
        _ERASURE_AUTHORITY,
        (
            "SELECT set_config('idis.erasure_context','forged-by-app-role',true)",
            f"DELETE FROM {table}",
        ),
    )


_IDENTITY_PROJECTION_AUTHORITY = "identity-identifier-projection-v1"
_IDENTITY_OUTPUT = "00000000-0000-0000-0000-000000001901"
_IDENTITY_OUTPUT_ROOT = "00000000-0000-0000-0000-000000001902"
_IDENTITY_IDENTIFIER_ROWS = (
    (
        "domain",
        _HASH_B,
        "00000000-0000-0000-0000-000000001903",
        _HASH_A,
    ),
    (
        "registration_number",
        _HASH_A,
        "00000000-0000-0000-0000-000000001904",
        _HASH_B,
    ),
)


_IDENTITY_PROJECTION_SCHEMA = (
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    """
    CREATE TABLE company_identity_outputs (
        identity_output_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        identifier_projection_sha256 TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE company_identity_identifiers (
        identity_identifier_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        identity_output_id UUID NOT NULL,
        identity_output_object_id UUID NOT NULL,
        identifier_kind TEXT NOT NULL,
        identifier_hash TEXT NOT NULL,
        verification_provenance_sha256 TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'VERIFIED'
    )
    """,
)


def _identity_projection_sha256(
    rows: tuple[tuple[str, str, str, str], ...],
) -> str:
    ordered = sorted(rows, key=lambda row: (row[0], row[1], row[2]))
    preimage = [
        "company-identity-identifier-projection-v1",
        _TENANT,
        _DEAL,
        _RUN,
        _IDENTITY_OUTPUT,
        _IDENTITY_OUTPUT_ROOT,
        len(ordered),
        [list(row) for row in ordered],
    ]
    return hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


def _identity_projection_rows(*, corrupt_child: bool = False) -> tuple[str, ...]:
    persisted_hash = _identity_projection_sha256(_IDENTITY_IDENTIFIER_ROWS)
    rows = list(_IDENTITY_IDENTIFIER_ROWS)
    if corrupt_child:
        kind, _identifier_hash, row_id, provenance = rows[0]
        rows[0] = (kind, "c" * 64, row_id, provenance)
    statements = [
        "INSERT INTO company_identity_outputs VALUES "
        f"('{_IDENTITY_OUTPUT}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_RUN}'::uuid,'{_IDENTITY_OUTPUT_ROOT}'::uuid,'{persisted_hash}')"
    ]
    # Reverse insertion proves the guard owns the approved deterministic ordering.
    for kind, identifier_hash, row_id, provenance in reversed(rows):
        statements.append(
            "INSERT INTO company_identity_identifiers ("
            "identity_identifier_id,tenant_id,deal_id,run_id,identity_output_id,"
            "identity_output_object_id,identifier_kind,identifier_hash,"
            "verification_provenance_sha256) VALUES "
            f"('{row_id}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
            f"'{_IDENTITY_OUTPUT}'::uuid,'{_IDENTITY_OUTPUT_ROOT}'::uuid,'{kind}',"
            f"'{identifier_hash}','{provenance}')"
        )
    return tuple(statements)


def test_identity_identifier_projection_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(
        guard_database,
        _IDENTITY_PROJECTION_AUTHORITY,
        _IDENTITY_PROJECTION_SCHEMA,
    )
    with guard_database.admin_engine.begin() as connection:
        for statement in _identity_projection_rows():
            connection.exec_driver_sql(statement)
        _flush(connection)


def test_identity_identifier_projection_rejects_illegal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(
        guard_database,
        _IDENTITY_PROJECTION_AUTHORITY,
        _IDENTITY_PROJECTION_SCHEMA,
    )
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            _IDENTITY_PROJECTION_AUTHORITY,
            _identity_projection_rows(corrupt_child=True),
        )


def test_identity_identifier_projection_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
) -> None:
    _install(
        guard_database,
        _IDENTITY_PROJECTION_AUTHORITY,
        _IDENTITY_PROJECTION_SCHEMA,
    )
    _raw_expect_rejected(
        guard_database,
        _IDENTITY_PROJECTION_AUTHORITY,
        _identity_projection_rows(corrupt_child=True),
    )


_SCORING_PROJECTION_AUTHORITY = "scoring-dimension-projection-v1"
_SCORING_OUTPUT = "00000000-0000-0000-0000-000000002001"
_SCORING_OUTPUT_ROOT = "00000000-0000-0000-0000-000000002002"
_SCORING_DIMENSION_KEYS = (
    "MARKET_ATTRACTIVENESS",
    "TEAM_QUALITY",
    "PRODUCT_DEFENSIBILITY",
    "TRACTION_VELOCITY",
    "FUND_THESIS_FIT",
    "CAPITAL_EFFICIENCY",
    "SCALABILITY",
    "RISK_PROFILE",
)


_SCORING_PROJECTION_SCHEMA = (
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    """
    CREATE TABLE scoring_outputs (
        scoring_output_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        dimension_projection_sha256 TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE scoring_output_dimensions (
        scoring_dimension_id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL,
        run_id UUID NOT NULL,
        scoring_output_id UUID NOT NULL,
        scoring_output_object_id UUID NOT NULL,
        dimension_ordinal SMALLINT NOT NULL,
        dimension_key TEXT NOT NULL,
        score DOUBLE PRECISION NOT NULL,
        confidence DOUBLE PRECISION NOT NULL,
        rationale_sha256 TEXT NOT NULL,
        confidence_justification_sha256 TEXT NOT NULL,
        support_projection_sha256 TEXT NOT NULL,
        muhasabah_projection_sha256 TEXT NOT NULL
    )
    """,
)


def _scoring_dimension_rows() -> tuple[tuple[int, str, float, float, str], ...]:
    return tuple(
        (
            ordinal,
            key,
            (ordinal + 1) / 10,
            (18 - ordinal) / 20,
            str(uuid.UUID(int=2003 + ordinal)),
        )
        for ordinal, key in enumerate(_SCORING_DIMENSION_KEYS)
    )


def _scoring_projection_sha256(
    rows: tuple[tuple[int, str, float, float, str], ...],
) -> str:
    ordered_rows = [
        [
            ordinal,
            key,
            struct.pack("!d", score).hex(),
            struct.pack("!d", confidence).hex(),
            _HASH_A,
            _HASH_B,
            _HASH_A,
            _HASH_B,
            row_id,
        ]
        for ordinal, key, score, confidence, row_id in sorted(rows)
    ]
    preimage = [
        "scoring-dimension-projection-v1",
        _TENANT,
        _DEAL,
        _RUN,
        _SCORING_OUTPUT,
        _SCORING_OUTPUT_ROOT,
        len(ordered_rows),
        ordered_rows,
    ]
    return hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


def _scoring_projection_rows(*, corrupt_child: bool = False) -> tuple[str, ...]:
    dimensions = list(_scoring_dimension_rows())
    persisted_hash = _scoring_projection_sha256(tuple(dimensions))
    if corrupt_child:
        ordinal, key, score, confidence, row_id = dimensions[3]
        dimensions[3] = (ordinal, key, score + 0.01, confidence, row_id)
    statements = [
        "INSERT INTO scoring_outputs VALUES "
        f"('{_SCORING_OUTPUT}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_RUN}'::uuid,'{_SCORING_OUTPUT_ROOT}'::uuid,'{persisted_hash}')"
    ]
    for ordinal, key, score, confidence, row_id in reversed(dimensions):
        statements.append(
            "INSERT INTO scoring_output_dimensions VALUES "
            f"('{row_id}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
            f"'{_SCORING_OUTPUT}'::uuid,'{_SCORING_OUTPUT_ROOT}'::uuid,{ordinal},"
            f"'{key}',{score!r}::double precision,{confidence!r}::double precision,"
            f"'{_HASH_A}','{_HASH_B}','{_HASH_A}','{_HASH_B}')"
        )
    return tuple(statements)


def test_scoring_dimension_projection_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(
        guard_database,
        _SCORING_PROJECTION_AUTHORITY,
        _SCORING_PROJECTION_SCHEMA,
    )
    with guard_database.admin_engine.begin() as connection:
        for statement in _scoring_projection_rows():
            connection.exec_driver_sql(statement)
        _flush(connection)


def test_scoring_dimension_projection_rejects_illegal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(
        guard_database,
        _SCORING_PROJECTION_AUTHORITY,
        _SCORING_PROJECTION_SCHEMA,
    )
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            _SCORING_PROJECTION_AUTHORITY,
            _scoring_projection_rows(corrupt_child=True),
        )


def test_scoring_dimension_projection_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
) -> None:
    _install(
        guard_database,
        _SCORING_PROJECTION_AUTHORITY,
        _SCORING_PROJECTION_SCHEMA,
    )
    _raw_expect_rejected(
        guard_database,
        _SCORING_PROJECTION_AUTHORITY,
        _scoring_projection_rows(corrupt_child=True),
    )


_NATIVE_BRANCHES = (
    (
        "methodology_truth_dashboard_rows",
        "dashboard_row_id",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
        "METHODOLOGY_TRUTH_DASHBOARD",
        "__step__",
    ),
    (
        "company_identity_outputs",
        "identity_output_id",
        "COMPANY_IDENTITY_OUTPUT",
        "METHODOLOGY_COMPANY_IDENTITY_PACKAGE",
        "__step__",
    ),
    (
        "enrichment_provider_outcomes",
        "provider_outcome_id",
        "ENRICHMENT_PROVIDER_OUTCOME",
        "ENRICHMENT",
        "sec_edgar",
    ),
    (
        "enrichment_normalized_facts",
        "fact_id",
        "ENRICHMENT_FACT",
        "ENRICHMENT",
        "sec_edgar",
    ),
    (
        "graph_projection_results",
        "graph_result_id",
        "GRAPH_PROJECTION_RESULT",
        "GRAPH_EVIDENCE",
        "internal_evidence",
    ),
    (
        "rag_index_results",
        "rag_index_result_id",
        "RAG_INDEX_RESULT",
        "RAG_EVIDENCE",
        "internal_evidence",
    ),
    (
        "rag_query_results",
        "rag_query_result_id",
        "RAG_QUERY_RESULT",
        "RAG_EVIDENCE",
        "internal_evidence",
    ),
    (
        "external_intelligence_conflict_plans",
        "conflict_plan_id",
        "CONFLICT_PLAN",
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "__step__",
    ),
    (
        "external_intelligence_conflict_executions",
        "conflict_execution_id",
        "CONFLICT_EXECUTION",
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "__step__",
    ),
    (
        "external_intelligence_conflict_findings",
        "conflict_finding_id",
        "CONFLICT_FINDING",
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "__step__",
    ),
    (
        "validated_evidence_package_references",
        "vep_reference_id",
        "VEP_CONTENT_REFERENCE",
        "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE",
        "__step__",
    ),
    (
        "debate_role_outputs",
        "debate_role_output_id",
        "DEBATE_ROLE_OUTPUT",
        "DEBATE",
        "advocate",
    ),
    (
        "analysis_agent_outputs",
        "analysis_output_id",
        "ANALYSIS_AGENT_OUTPUT",
        "ANALYSIS",
        "financial_agent",
    ),
    ("scoring_outputs", "scoring_output_id", "SCORING_OUTPUT", "SCORING", "__step__"),
    (
        "layer2_readiness_outputs",
        "readiness_output_id",
        "LAYER2_READINESS_OUTPUT",
        "METHODOLOGY_LAYER2_READINESS_PACKAGE",
        "__step__",
    ),
    (
        "layer2_role_outputs",
        "layer2_role_output_id",
        "LAYER2_ROLE_OUTPUT",
        "LAYER2_IC_CHALLENGE",
        "ic_advocate",
    ),
    (
        "generic_extraction_outputs",
        "generic_output_id",
        "GENERIC_EXTRACTION_OUTPUT",
        "EXTRACT",
        "__step__",
    ),
    (
        "run_claim_grading_links",
        "grading_link_id",
        "RUN_CLAIM_GRADING_LINK",
        "GRADE",
        "__step__",
    ),
    (
        "generic_extraction_claim_links",
        "generic_claim_link_id",
        "METHODOLOGY_CLAIM_LINK",
        "EXTRACT",
        "__step__",
    ),
    (
        "generic_extraction_evidence_links",
        "generic_evidence_link_id",
        "METHODOLOGY_EVIDENCE_LINK",
        "EXTRACT",
        "__step__",
    ),
)

_NATIVE_ROOT_SCHEMA = (
    _ROOT_SQL,
    """
    CREATE TABLE run_step_outputs (
        step_output_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        output_object_id UUID NOT NULL, step_name TEXT NOT NULL,
        subrole TEXT NOT NULL, output_kind TEXT NOT NULL,
        output_ordinal INTEGER NOT NULL
    )
    """,
    *tuple(
        f"""
        CREATE TABLE {table} (
            {primary_key} UUID PRIMARY KEY, tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL, run_id UUID NOT NULL,
            run_output_object_id UUID NOT NULL,
            run_output_object_class TEXT NOT NULL,
            run_output_object_kind TEXT NOT NULL,
            provider_id TEXT, fact_domain TEXT, namespace TEXT, role TEXT,
            check_kind TEXT, subject_key TEXT
        )
        """
        for table, primary_key, _kind, _step, _subrole in _NATIVE_BRANCHES
    ),
)


def _root_insert(root: str, output_class: str, kind: str) -> str:
    return (
        "INSERT INTO run_output_objects VALUES "
        f"('{root}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
        f"'{output_class}','{kind}','{_HASH_A}','{_HASH_A}')"
    )


def _step_output_insert(
    root: str,
    step: str,
    subrole: str,
    kind: str,
    *,
    seed: int,
) -> str:
    return (
        "INSERT INTO run_step_outputs VALUES "
        f"('{uuid.UUID(int=1400 + seed)}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,"
        f"'{_RUN}'::uuid,'{root}'::uuid,'{step}','{subrole}','{kind}',0)"
    )


def _native_insert(
    table: str,
    primary_key: str,
    row_id: str,
    root: str,
    kind: str,
    *,
    subrole: str,
) -> str:
    provider = f"'{subrole}'" if table.startswith("enrichment_") else "NULL"
    namespace = (
        f"'{subrole}'"
        if table in {"graph_projection_results", "rag_index_results", "rag_query_results"}
        else "NULL"
    )
    role = (
        f"'{subrole}'"
        if table in {"debate_role_outputs", "analysis_agent_outputs", "layer2_role_outputs"}
        else "NULL"
    )
    fact_domain = "'commercial_diligence'" if table == "enrichment_normalized_facts" else "NULL"
    check_kind = (
        "'identity_conflict'" if table.startswith("external_intelligence_conflict_") else "NULL"
    )
    subject_key = "'subject-001'" if table.startswith("external_intelligence_conflict_") else "NULL"
    return (
        f"INSERT INTO {table} ({primary_key},tenant_id,deal_id,run_id,"
        "run_output_object_id,run_output_object_class,run_output_object_kind,"
        "provider_id,fact_domain,namespace,role,check_kind,subject_key) VALUES "
        f"('{row_id}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
        f"'{root}'::uuid,'NATIVE_ROW','{kind}',{provider},"
        f"{fact_domain},{namespace},{role},{check_kind},{subject_key})"
    )


@pytest.mark.parametrize(
    "table,primary_key,kind,step,subrole",
    (*_NATIVE_BRANCHES, pytest.param(None, None, "INGEST_RECEIPT", "INGEST_CHECK", "__step__")),
    ids=(*tuple(row[0] for row in _NATIVE_BRANCHES), "step-receipt"),
)
def test_native_root_exact_one_accepts_legal_row(
    guard_database: DisposablePostgres,
    table: str | None,
    primary_key: str | None,
    kind: str,
    step: str,
    subrole: str,
) -> None:
    _install(guard_database, "native-root-exact-one-v1", _NATIVE_ROOT_SCHEMA)
    root = "00000000-0000-0000-0000-000000001401"
    output_class = "STEP_RECEIPT" if table is None else "NATIVE_ROW"
    with guard_database.admin_engine.begin() as connection:
        connection.exec_driver_sql(_root_insert(root, output_class, kind))
        connection.exec_driver_sql(_step_output_insert(root, step, subrole, kind, seed=1))
        if table is not None and primary_key is not None:
            connection.exec_driver_sql(
                _native_insert(
                    table,
                    primary_key,
                    "00000000-0000-0000-0000-000000001403",
                    root,
                    kind,
                    subrole=subrole,
                )
            )
        _flush(connection)


_NATIVE_INVALID_CASES = tuple(
    pytest.param(case, id=case)
    for case in (
        "missing-child",
        "duplicate-child",
        "orphan-child",
        "wrong-table-dispatch",
        "wrong-step-dispatch",
        "missing-step-output",
        "step-receipt-with-child",
    )
)


def _native_invalid_rows(case: str) -> tuple[str, ...]:
    root = "00000000-0000-0000-0000-000000001404"
    child_one = "00000000-0000-0000-0000-000000001405"
    child_two = "00000000-0000-0000-0000-000000001406"
    rows: list[str] = []
    if case != "orphan-child":
        output_class = "STEP_RECEIPT" if case == "step-receipt-with-child" else "NATIVE_ROW"
        kind = "INGEST_RECEIPT" if case == "step-receipt-with-child" else "COMPANY_IDENTITY_OUTPUT"
        rows.append(_root_insert(root, output_class, kind))
        if case != "missing-step-output":
            step = (
                "EXTRACT"
                if case == "wrong-step-dispatch"
                else "METHODOLOGY_COMPANY_IDENTITY_PACKAGE"
            )
            rows.append(_step_output_insert(root, step, "__step__", kind, seed=4))
    if case not in {"missing-child"}:
        table = "scoring_outputs" if case == "wrong-table-dispatch" else "company_identity_outputs"
        primary_key = "scoring_output_id" if table == "scoring_outputs" else "identity_output_id"
        child_kind = "COMPANY_IDENTITY_OUTPUT"
        rows.append(
            _native_insert(
                table,
                primary_key,
                child_one,
                root,
                child_kind,
                subrole="__step__",
            )
        )
    if case == "duplicate-child":
        rows.append(
            _native_insert(
                "company_identity_outputs",
                "identity_output_id",
                child_two,
                root,
                "COMPANY_IDENTITY_OUTPUT",
                subrole="__step__",
            )
        )
    return tuple(rows)


@pytest.mark.parametrize("case", _NATIVE_INVALID_CASES)
def test_native_root_exact_one_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "native-root-exact-one-v1", _NATIVE_ROOT_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "native-root-exact-one-v1",
            _native_invalid_rows(case),
        )


@pytest.mark.parametrize("case", _NATIVE_INVALID_CASES)
def test_native_root_exact_one_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "native-root-exact-one-v1", _NATIVE_ROOT_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "native-root-exact-one-v1",
        _native_invalid_rows(case),
    )


_GENERIC_SCHEMA = (
    _ROOT_SQL,
    """
    CREATE TABLE run_step_outputs (
        step_output_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        output_object_id UUID NOT NULL, step_name TEXT NOT NULL,
        subrole TEXT NOT NULL, output_kind TEXT NOT NULL,
        output_ordinal INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE generic_extraction_outputs (
        generic_output_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE claims (
        claim_id UUID PRIMARY KEY, tenant_id UUID NOT NULL, deal_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE evidence_items (
        evidence_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, claim_id UUID NOT NULL,
        document_id UUID NOT NULL, source_span_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE document_spans (
        span_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, document_id UUID NOT NULL
    )
    """,
    """
    CREATE TABLE generic_extraction_claim_links (
        generic_claim_link_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        generic_output_id UUID NOT NULL, generic_output_object_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL, claim_ordinal INTEGER NOT NULL,
        claim_id UUID NOT NULL, canonical_key_sha256 TEXT NOT NULL,
        reused_existing_claim BOOLEAN NOT NULL
    )
    """,
    """
    CREATE TABLE generic_extraction_evidence_links (
        generic_evidence_link_id UUID PRIMARY KEY, tenant_id UUID NOT NULL,
        deal_id UUID NOT NULL, run_id UUID NOT NULL,
        generic_output_id UUID NOT NULL, generic_output_object_id UUID NOT NULL,
        run_output_object_id UUID NOT NULL,
        run_output_object_class TEXT NOT NULL,
        run_output_object_kind TEXT NOT NULL,
        generic_claim_link_id UUID NOT NULL, claim_id UUID NOT NULL,
        evidence_id UUID NOT NULL, document_id UUID NOT NULL,
        source_span_id UUID NOT NULL, locator_sha256 TEXT NOT NULL,
        evidence_ordinal INTEGER NOT NULL
    )
    """,
)


_GENERIC_OUTPUT_ROOT = "00000000-0000-0000-0000-000000001701"
_GENERIC_CLAIM_ROOT = "00000000-0000-0000-0000-000000001702"
_GENERIC_OUTPUT = "00000000-0000-0000-0000-000000001703"
_GENERIC_CLAIM = "00000000-0000-0000-0000-000000001704"
_GENERIC_CLAIM_LINK = "00000000-0000-0000-0000-000000001705"
_GENERIC_EVIDENCE_ROOT = "00000000-0000-0000-0000-000000001711"
_GENERIC_EVIDENCE_LINK = "00000000-0000-0000-0000-000000001712"
_GENERIC_EVIDENCE = "00000000-0000-0000-0000-000000001713"
_GENERIC_DOCUMENT = "00000000-0000-0000-0000-000000001714"
_GENERIC_SPAN = "00000000-0000-0000-0000-000000001715"
_GENERIC_OTHER = "00000000-0000-0000-0000-000000001799"


_GENERIC_CLAIM_INVALID_CASES = tuple(
    pytest.param(case, id=case)
    for case in (
        "producer-step",
        "tenant-scope",
        "deal-scope",
        "run-scope",
        "generic-parent-id",
        "generic-parent-root",
        "claim-root-id",
        "claim-root-class",
        "claim-root-kind",
        "claim-missing",
        "claim-tenant",
        "claim-deal",
    )
)


def _generic_claim_rows(*, case: str = "legal") -> tuple[str, ...]:
    link_tenant = _OTHER_TENANT if case == "tenant-scope" else _TENANT
    link_deal = _OTHER_DEAL if case == "deal-scope" else _DEAL
    link_run = _OTHER_RUN if case == "run-scope" else _RUN
    generic_output_id = _GENERIC_OTHER if case == "generic-parent-id" else _GENERIC_OUTPUT
    generic_output_root = _GENERIC_OTHER if case == "generic-parent-root" else _GENERIC_OUTPUT_ROOT
    claim_root = _GENERIC_OTHER if case == "claim-root-id" else _GENERIC_CLAIM_ROOT
    root_class = "STEP_RECEIPT" if case == "claim-root-class" else "NATIVE_ROW"
    root_kind = (
        "METHODOLOGY_EVIDENCE_LINK" if case == "claim-root-kind" else "METHODOLOGY_CLAIM_LINK"
    )
    claim_id = _GENERIC_OTHER if case == "claim-missing" else _GENERIC_CLAIM
    claim_tenant = _OTHER_TENANT if case == "claim-tenant" else _TENANT
    claim_deal = _OTHER_DEAL if case == "claim-deal" else _DEAL
    producer_step = "GRADE" if case == "producer-step" else "EXTRACT"
    return (
        _root_insert(_GENERIC_OUTPUT_ROOT, "NATIVE_ROW", "GENERIC_EXTRACTION_OUTPUT"),
        _root_insert(_GENERIC_CLAIM_ROOT, "NATIVE_ROW", "METHODOLOGY_CLAIM_LINK"),
        _step_output_insert(
            _GENERIC_CLAIM_ROOT,
            producer_step,
            "__step__",
            "METHODOLOGY_CLAIM_LINK",
            seed=7,
        ),
        "INSERT INTO generic_extraction_outputs VALUES "
        f"('{_GENERIC_OUTPUT}'::uuid,'{_TENANT}'::uuid,'{_DEAL}'::uuid,'{_RUN}'::uuid,"
        f"'{_GENERIC_OUTPUT_ROOT}'::uuid)",
        "INSERT INTO claims VALUES "
        f"('{_GENERIC_CLAIM}'::uuid,'{claim_tenant}'::uuid,'{claim_deal}'::uuid)",
        "INSERT INTO generic_extraction_claim_links VALUES "
        f"('{_GENERIC_CLAIM_LINK}'::uuid,'{link_tenant}'::uuid,'{link_deal}'::uuid,"
        f"'{link_run}'::uuid,'{generic_output_id}'::uuid,'{generic_output_root}'::uuid,"
        f"'{claim_root}'::uuid,'{root_class}','{root_kind}',0,'{claim_id}'::uuid,"
        f"'{_HASH_A}',false)",
    )


def test_generic_claim_projection_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(guard_database, "generic-claim-projection-v1", _GENERIC_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        for statement in _generic_claim_rows():
            connection.exec_driver_sql(statement)
        _flush(connection)


@pytest.mark.parametrize("case", _GENERIC_CLAIM_INVALID_CASES)
def test_generic_claim_projection_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "generic-claim-projection-v1", _GENERIC_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "generic-claim-projection-v1",
            _generic_claim_rows(case=case),
        )


@pytest.mark.parametrize("case", _GENERIC_CLAIM_INVALID_CASES)
def test_generic_claim_projection_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "generic-claim-projection-v1", _GENERIC_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "generic-claim-projection-v1",
        _generic_claim_rows(case=case),
    )


_GENERIC_EVIDENCE_INVALID_CASES = tuple(
    pytest.param(case, id=case)
    for case in (
        "producer-step",
        "tenant-scope",
        "deal-scope",
        "run-scope",
        "generic-parent-id",
        "generic-parent-root",
        "evidence-root-id",
        "evidence-root-class",
        "evidence-root-kind",
        "claim-parent-id",
        "claim-id",
        "evidence-id",
        "evidence-tenant",
        "evidence-deal",
        "document-id",
        "span-id",
        "span-tenant",
        "span-deal",
        "span-document",
    )
)


def _generic_evidence_rows(*, case: str = "legal") -> tuple[str, ...]:
    rows = list(_generic_claim_rows())
    link_tenant = _OTHER_TENANT if case == "tenant-scope" else _TENANT
    link_deal = _OTHER_DEAL if case == "deal-scope" else _DEAL
    link_run = _OTHER_RUN if case == "run-scope" else _RUN
    generic_output_id = _GENERIC_OTHER if case == "generic-parent-id" else _GENERIC_OUTPUT
    generic_output_root = _GENERIC_OTHER if case == "generic-parent-root" else _GENERIC_OUTPUT_ROOT
    evidence_root = _GENERIC_OTHER if case == "evidence-root-id" else _GENERIC_EVIDENCE_ROOT
    root_class = "STEP_RECEIPT" if case == "evidence-root-class" else "NATIVE_ROW"
    root_kind = (
        "METHODOLOGY_CLAIM_LINK" if case == "evidence-root-kind" else "METHODOLOGY_EVIDENCE_LINK"
    )
    claim_parent = _GENERIC_OTHER if case == "claim-parent-id" else _GENERIC_CLAIM_LINK
    claim_id = _GENERIC_OTHER if case == "claim-id" else _GENERIC_CLAIM
    evidence_id = _GENERIC_OTHER if case == "evidence-id" else _GENERIC_EVIDENCE
    document_id = _GENERIC_OTHER if case == "document-id" else _GENERIC_DOCUMENT
    span_id = _GENERIC_OTHER if case == "span-id" else _GENERIC_SPAN
    evidence_tenant = _OTHER_TENANT if case == "evidence-tenant" else _TENANT
    evidence_deal = _OTHER_DEAL if case == "evidence-deal" else _DEAL
    span_tenant = _OTHER_TENANT if case == "span-tenant" else _TENANT
    span_deal = _OTHER_DEAL if case == "span-deal" else _DEAL
    span_document = _GENERIC_OTHER if case == "span-document" else _GENERIC_DOCUMENT
    producer_step = "GRADE" if case == "producer-step" else "EXTRACT"
    rows.extend(
        (
            _root_insert(
                _GENERIC_EVIDENCE_ROOT,
                "NATIVE_ROW",
                "METHODOLOGY_EVIDENCE_LINK",
            ),
            _step_output_insert(
                _GENERIC_EVIDENCE_ROOT,
                producer_step,
                "__step__",
                "METHODOLOGY_EVIDENCE_LINK",
                seed=8,
            ),
            "INSERT INTO document_spans VALUES "
            f"('{_GENERIC_SPAN}'::uuid,'{span_tenant}'::uuid,'{span_deal}'::uuid,"
            f"'{span_document}'::uuid)",
            "INSERT INTO evidence_items VALUES "
            f"('{_GENERIC_EVIDENCE}'::uuid,'{evidence_tenant}'::uuid,"
            f"'{evidence_deal}'::uuid,'{_GENERIC_CLAIM}'::uuid,"
            f"'{_GENERIC_DOCUMENT}'::uuid,'{_GENERIC_SPAN}'::uuid)",
            "INSERT INTO generic_extraction_evidence_links VALUES "
            f"('{_GENERIC_EVIDENCE_LINK}'::uuid,'{link_tenant}'::uuid,'{link_deal}'::uuid,"
            f"'{link_run}'::uuid,'{generic_output_id}'::uuid,'{generic_output_root}'::uuid,"
            f"'{evidence_root}'::uuid,'{root_class}','{root_kind}','{claim_parent}'::uuid,"
            f"'{claim_id}'::uuid,'{evidence_id}'::uuid,'{document_id}'::uuid,"
            f"'{span_id}'::uuid,'{_HASH_A}',0)",
        )
    )
    return tuple(rows)


def test_generic_evidence_projection_accepts_legal_row(
    guard_database: DisposablePostgres,
) -> None:
    _install(guard_database, "generic-evidence-projection-v1", _GENERIC_SCHEMA)
    with guard_database.admin_engine.begin() as connection:
        for statement in _generic_evidence_rows():
            connection.exec_driver_sql(statement)
        _flush(connection)


@pytest.mark.parametrize("case", _GENERIC_EVIDENCE_INVALID_CASES)
def test_generic_evidence_projection_rejects_illegal_row(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "generic-evidence-projection-v1", _GENERIC_SCHEMA)
    with guard_database.admin_engine.connect() as connection, connection.begin():
        _expect_rejected(
            connection,
            "generic-evidence-projection-v1",
            _generic_evidence_rows(case=case),
        )


@pytest.mark.parametrize("case", _GENERIC_EVIDENCE_INVALID_CASES)
def test_generic_evidence_projection_rejects_direct_sql_bypass(
    guard_database: DisposablePostgres,
    case: str,
) -> None:
    _install(guard_database, "generic-evidence-projection-v1", _GENERIC_SCHEMA)
    _raw_expect_rejected(
        guard_database,
        "generic-evidence-projection-v1",
        _generic_evidence_rows(case=case),
    )
