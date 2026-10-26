"""RED contracts for canonical UUID materialization and typed result links."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from tests.slice100_task6._support import (
    DEAL_ID,
    DOCUMENT_ID,
    RESULT_ID,
    RUN_ID,
    SHA_A,
    SPAN_ID,
    TENANT_ID,
    factual_output_kwargs,
    require_symbol,
    terminal_response_kwargs,
)


def _identity(*, subject: str, predicate: str, amount: str) -> object:
    identity_cls = require_symbol(
        "idis.models.methodology_extraction", "CanonicalMethodologyClaimIdentity"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    output = output_cls(
        **factual_output_kwargs(
            subject=subject,
            predicate=predicate,
            value={"type": "monetary", "amount": amount, "currency": "USD"},
        )
    )
    return identity_cls.from_factual_output(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        factual_output=output,
    )


def test_canonical_claim_identity_is_normalized_deterministic_uuid5() -> None:
    first = _identity(
        subject="  FICTIONAL\u00a0Company ",
        predicate=" Fictional  Recurring Revenue ",
        amount="1200.00",
    )
    equivalent = _identity(
        subject="fictional company",
        predicate="fictional recurring revenue",
        amount="1200",
    )

    claim_uuid = uuid.UUID(str(first.claim_id))
    assert claim_uuid.version == 5
    assert first.claim_id == equivalent.claim_id
    assert first.normalization_rule_version == "claim-normalization-v1"
    assert first.normalized_subject == "fictional company"
    assert first.normalized_predicate == "fictional recurring revenue"
    assert first.normalized_period == "as_of:2026-06-30"
    assert first.normalized_unit == "USD"
    assert len(first.value_sha256) == 64


def test_different_typed_value_gets_a_distinct_canonical_claim_uuid() -> None:
    first = _identity(
        subject="Fictional Company",
        predicate="fictional recurring revenue",
        amount="1200",
    )
    changed = _identity(
        subject="Fictional Company",
        predicate="fictional recurring revenue",
        amount="1201",
    )

    assert first.value_sha256 != changed.value_sha256
    assert first.claim_id != changed.claim_id


def test_range_period_normalization_uses_the_pinned_slash_delimiter() -> None:
    identity_cls = require_symbol(
        "idis.models.methodology_extraction", "CanonicalMethodologyClaimIdentity"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    output = output_cls(
        **factual_output_kwargs(
            period={
                "kind": "range",
                "start_date": "2026-01-01",
                "end_date": "2026-06-30",
            }
        )
    )

    identity = identity_cls.from_factual_output(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        factual_output=output,
    )

    assert identity.normalized_period == "range:2026-01-01/2026-06-30"


@pytest.mark.parametrize(
    ("value", "declared_unit", "expected_unit"),
    (
        ({"type": "monetary", "amount": "1200", "currency": "USD"}, "usd", "USD"),
        ({"type": "percentage", "value": "0.25"}, "percent", "ratio"),
        ({"type": "date", "value": "2026-06-30"}, "calendar day", "date"),
        ({"type": "boolean", "value": True}, "yes/no", "boolean"),
        ({"type": "text", "value": "Fictional narrative"}, "narrative", "text"),
        ({"type": "count", "value": 12, "unit": "Head  Count"}, "people", "head count"),
        (
            {"type": "range", "min_value": "1", "max_value": "2", "unit": "USD  per seat"},
            "wrong",
            "usd per seat",
        ),
        (
            {
                "type": "table_row",
                "contract_version": "methodology-table-contract-v1",
                "columns": {
                    "row_label": "FY2026",
                    "metric": "Revenue",
                    "value": {"type": "monetary", "amount": "1200", "currency": "USD"},
                    "unit": "USD",
                    "period": {"kind": "as_of", "date": "2026-06-30"},
                },
            },
            "rows",
            "table_row",
        ),
    ),
)
def test_typed_value_owns_the_pinned_canonical_unit(
    value: dict[str, object], declared_unit: str, expected_unit: str
) -> None:
    identity_cls = require_symbol(
        "idis.models.methodology_extraction", "CanonicalMethodologyClaimIdentity"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    output = output_cls(
        **factual_output_kwargs(
            value=value,
            unit=declared_unit,
        )
    )

    identity = identity_cls.from_factual_output(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        factual_output=output,
    )

    assert output.unit == expected_unit
    assert identity.normalized_unit == expected_unit


def test_factual_output_id_is_deterministic_and_content_bound() -> None:
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    first = output_cls(**factual_output_kwargs())
    equivalent = output_cls(**factual_output_kwargs())
    changed = output_cls(
        **factual_output_kwargs(value={"type": "monetary", "amount": "1201", "currency": "USD"})
    )
    namespace = require_symbol(
        "idis.models.methodology_extraction", "METHODOLOGY_FACTUAL_OUTPUT_NAMESPACE"
    )

    assert isinstance(namespace, uuid.UUID)
    assert first.factual_output_id
    assert first.factual_output_id == equivalent.factual_output_id
    assert first.factual_output_id != changed.factual_output_id
    assert uuid.UUID(str(first.factual_output_id)).version == 5


def test_boolean_and_table_row_values_have_deterministic_typed_identities() -> None:
    table_contract_version = require_symbol(
        "idis.models.methodology_extraction", "METHODOLOGY_TABLE_CONTRACT_VERSION"
    )
    table_columns = require_symbol(
        "idis.models.methodology_extraction", "METHODOLOGY_TABLE_COLUMNS"
    )
    assert table_contract_version == "methodology-table-contract-v1"
    assert table_columns == ("row_label", "metric", "value", "unit", "period")
    identity_cls = require_symbol(
        "idis.models.methodology_extraction", "CanonicalMethodologyClaimIdentity"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    boolean_output = output_cls(
        **factual_output_kwargs(
            predicate="fictional_control_enabled",
            value={"type": "boolean", "value": True},
            unit="boolean",
        )
    )
    table_output = output_cls(
        **factual_output_kwargs(
            predicate="fictional_revenue_row",
            value={
                "type": "table_row",
                "contract_version": table_contract_version,
                "columns": {
                    "row_label": "Revenue",
                    "metric": "recurring_revenue",
                    "value": {"type": "monetary", "amount": "1200", "currency": "USD"},
                    "unit": "USD",
                    "period": {"kind": "as_of", "date": "2026-06-30"},
                },
            },
            unit="table_row",
        )
    )
    reordered_table = output_cls(
        **factual_output_kwargs(
            predicate="fictional_revenue_row",
            value={
                "type": "table_row",
                "contract_version": table_contract_version,
                "columns": {
                    "period": {"date": "2026-06-30", "kind": "as_of"},
                    "unit": "USD",
                    "value": {"currency": "USD", "amount": "1200", "type": "monetary"},
                    "metric": "recurring_revenue",
                    "row_label": "Revenue",
                },
            },
            unit="table_row",
        )
    )
    other_question_table = output_cls(
        **factual_output_kwargs(
            methodology_question_record_id="17171717-1717-4171-8171-171717171717",
            predicate="fictional_revenue_row",
            value={
                "type": "table_row",
                "contract_version": table_contract_version,
                "columns": {
                    "row_label": "Revenue",
                    "metric": "recurring_revenue",
                    "value": {"type": "monetary", "amount": "1200", "currency": "USD"},
                    "unit": "USD",
                    "period": {"kind": "as_of", "date": "2026-06-30"},
                },
            },
            unit="table_row",
        )
    )

    boolean_identity = identity_cls.from_factual_output(
        tenant_id=TENANT_ID, deal_id=DEAL_ID, factual_output=boolean_output
    )
    table_identity = identity_cls.from_factual_output(
        tenant_id=TENANT_ID, deal_id=DEAL_ID, factual_output=table_output
    )
    reordered_identity = identity_cls.from_factual_output(
        tenant_id=TENANT_ID, deal_id=DEAL_ID, factual_output=reordered_table
    )

    assert uuid.UUID(str(boolean_identity.claim_id)).version == 5
    assert uuid.UUID(str(table_identity.claim_id)).version == 5
    assert table_identity.claim_id == reordered_identity.claim_id
    assert table_output.factual_output_id == reordered_table.factual_output_id
    assert table_output.factual_output_id != other_question_table.factual_output_id

    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    duplicate_values = terminal_response_kwargs()
    duplicate_values["factual_outputs"] = [table_output, reordered_table]
    with pytest.raises(ValueError, match="duplicate"):
        response_cls(**duplicate_values)

    with pytest.raises(ValueError):
        output_cls(
            **factual_output_kwargs(
                predicate="fictional_bad_row",
                value={
                    "type": "table_row",
                    "contract_version": table_contract_version,
                    "columns": {
                        "row_label": "Revenue",
                        "metric": "recurring_revenue",
                        "value": {"type": "text", "value": "1200"},
                        "unit": "USD",
                        "period": {"kind": "as_of", "date": "2026-06-30"},
                        "invented_column": "forbidden",
                    },
                },
                unit="table_row",
            )
        )


@pytest.mark.parametrize("coerced", ["true", "false", 1, 0])
def test_boolean_methodology_values_reject_non_json_boolean_coercions(coerced: object) -> None:
    boolean_cls = require_symbol("idis.models.value_structs", "BooleanValue")

    with pytest.raises(ValueError):
        boolean_cls(value=coerced)


def test_claim_evidence_and_sanad_links_use_typed_foreign_keys() -> None:
    factual_record_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultFactualOutputRecord"
    )
    claim_link_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultClaimLink"
    )
    evidence_link_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultEvidenceLink"
    )
    sanad_link_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultSanadLink"
    )
    defect_link_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultDefectLink"
    )
    claim_link_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    claim_id = "cccccccc-cccc-5ccc-8ccc-cccccccccccc"
    evidence_link_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    evidence_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    sanad_link_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    sanad_id = "12121212-1212-4121-8121-121212121212"
    coverage_record_cls = require_symbol(
        "idis.models.methodology_coverage", "MethodologyCoverageRecord"
    )
    coverage_id = coverage_record_cls(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_id="commercial_dd",
        methodology_version_id="commercial_dd:frozen-v1",
        methodology_question_id="mq_commercial_dd_fictional_0001",
        methodology_type="commercial_dd",
        section="Fictional commercial section",
    ).coverage_record_id
    assert coverage_id.startswith("mc_") and len(coverage_id) == 27
    defect_link_id = "14141414-1414-4141-8141-141414141414"
    defect_id = "15151515-1515-4151-8151-151515151515"
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    factual_output = output_cls(**factual_output_kwargs())
    factual_record = factual_record_cls.from_factual_output(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        result_id=RESULT_ID,
        factual_output=factual_output,
    )
    factual_output_id = factual_output.factual_output_id
    assert uuid.UUID(str(factual_record.factual_output_record_id))
    assert factual_record.factual_output_id == factual_output_id
    assert factual_record.result_id == RESULT_ID

    claim_link = claim_link_cls(
        claim_link_id=claim_link_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        result_id=RESULT_ID,
        factual_output_record_id=factual_record.factual_output_record_id,
        coverage_record_id=coverage_id,
        factual_output_id=factual_output_id,
        claim_id=claim_id,
        canonical_key_sha256=SHA_A,
        reused_existing_claim=False,
    )
    evidence_link = evidence_link_cls(
        evidence_link_id=evidence_link_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        result_id=RESULT_ID,
        factual_output_record_id=factual_record.factual_output_record_id,
        claim_link_id=claim_link.claim_link_id,
        factual_output_id=factual_output_id,
        evidence_id=evidence_id,
        document_id=DOCUMENT_ID,
        source_span_id=SPAN_ID,
    )
    sanad_link = sanad_link_cls(
        sanad_link_id=sanad_link_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        result_id=RESULT_ID,
        factual_output_record_id=factual_record.factual_output_record_id,
        claim_link_id=claim_link.claim_link_id,
        factual_output_id=factual_output_id,
        sanad_id=sanad_id,
        primary_evidence_id=evidence_link.evidence_id,
        grade="B",
        defect_ids=(),
    )
    defect_link = defect_link_cls(
        defect_link_id=defect_link_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        result_id=RESULT_ID,
        factual_output_record_id=factual_record.factual_output_record_id,
        sanad_link_id=sanad_link.sanad_link_id,
        defect_id=defect_id,
    )

    assert set(claim_link.model_dump()) == {
        "claim_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "coverage_record_id",
        "factual_output_id",
        "claim_id",
        "canonical_key_sha256",
        "reused_existing_claim",
    }
    assert set(evidence_link.model_dump()) == {
        "evidence_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "claim_link_id",
        "factual_output_id",
        "evidence_id",
        "document_id",
        "source_span_id",
    }
    assert set(sanad_link.model_dump()) == {
        "sanad_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "claim_link_id",
        "factual_output_id",
        "sanad_id",
        "primary_evidence_id",
        "grade",
        "defect_ids",
    }
    assert set(defect_link.model_dump()) == {
        "defect_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "sanad_link_id",
        "defect_id",
    }

    for record_id in (
        claim_link.claim_id,
        evidence_link.evidence_id,
        sanad_link.sanad_id,
        sanad_link.primary_evidence_id,
        defect_link.defect_id,
    ):
        uuid.UUID(str(record_id))
    assert evidence_link.source_span_id == SPAN_ID
    assert sanad_link.primary_evidence_id == evidence_link.evidence_id


def test_defect_link_requires_sanad_link_and_rejects_legacy_fields() -> None:
    defect_link_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultDefectLink"
    )
    base = {
        "defect_link_id": "14141414-1414-4141-8141-141414141414",
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "result_id": RESULT_ID,
        "factual_output_record_id": "16161616-1616-4161-8161-161616161616",
        "sanad_link_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "defect_id": "15151515-1515-4151-8151-151515151515",
    }

    with pytest.raises(ValueError):
        defect_link_cls(**{key: value for key, value in base.items() if key != "sanad_link_id"})
    with pytest.raises(ValueError):
        defect_link_cls(
            **base,
            claim_link_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            factual_output_id="17171717-1717-4171-8171-171717171717",
        )


def test_methodology_link_identity_matches_the_existing_repository_uuid5_contract() -> None:
    generate_link_id = require_symbol(
        "idis.models.methodology_extraction", "generate_methodology_result_link_id"
    )
    logical_record = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "result_id": RESULT_ID,
        "factual_output_record_id": "16161616-1616-4161-8161-161616161616",
        "coverage_record_id": "mc_" + "a" * 24,
        "factual_output_id": "17171717-1717-4171-8171-171717171717",
        "claim_id": "cccccccc-cccc-5ccc-8ccc-cccccccccccc",
        "canonical_key_sha256": SHA_A,
        "reused_existing_claim": False,
    }
    derived_fields = {
        "selection_id": "18181818-1818-4181-8181-181818181818",
        "methodology_version_record_id": "19191919-1919-4191-8191-191919191919",
        "methodology_question_record_id": "20202020-2020-4202-8202-202020202020",
    }
    legacy_identity_payload = {
        **{key: value for key, value in logical_record.items() if key != "factual_output_id"},
        **derived_fields,
    }
    digest = hashlib.sha256(
        json.dumps(
            legacy_identity_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    expected = str(
        uuid.uuid5(
            uuid.UUID("6c0ed11d-42cf-5746-aada-f54543224172"),
            f"methodology_result_claim_links:{digest}",
        )
    )

    assert (
        generate_link_id(
            table="methodology_result_claim_links",
            logical_record=logical_record,
            derived_fields=derived_fields,
        )
        == expected
    )

    with pytest.raises(ValueError):
        generate_link_id(
            table="methodology_result_claim_links",
            logical_record={**logical_record, "unknown": "forbidden"},
            derived_fields=derived_fields,
        )


@pytest.mark.parametrize(
    ("method_name", "method_args"),
    (
        ("persist_claim_links", {}),
        (
            "persist_evidence_links",
            {"task_record_id": "21212121-2121-4212-8212-212121212121"},
        ),
        ("persist_sanad_links", {}),
        ("persist_defect_links", {}),
    ),
)
def test_link_repositories_reject_unknown_or_missing_fields_before_any_sql(
    method_name: str, method_args: dict[str, str]
) -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    error_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "MethodologyExtractionRepositoryError",
    )

    class SqlMustNotRun:
        allow_tenant_binding = True

        def execute(self, *_args: object, **_kwargs: object) -> object:
            if self.allow_tenant_binding:
                return object()
            raise AssertionError("link shape validation must happen before SQL")

    connection = SqlMustNotRun()
    repository = repository_cls(connection, TENANT_ID)
    connection.allow_tenant_binding = False
    with pytest.raises(error_cls):
        getattr(repository, method_name)(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id="22222222-2222-4222-8222-222222222222",
            lease_fencing_token=1,
            records=[{"invented": "forbidden"}],
            **method_args,
        )


@pytest.mark.parametrize(
    "invalid_coverage_record_id",
    (
        "13131313-1313-4131-8131-131313131313",
        "mc_" + "a" * 23,
        "mc_" + "A" * 24,
        "mc_" + "g" * 24,
    ),
)
def test_claim_link_rejects_ids_outside_the_task5_coverage_domain(
    invalid_coverage_record_id: str,
) -> None:
    claim_link_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyResultClaimLink"
    )

    with pytest.raises(ValueError):
        claim_link_cls(
            claim_link_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            result_id=RESULT_ID,
            factual_output_record_id="16161616-1616-4161-8161-161616161616",
            coverage_record_id=invalid_coverage_record_id,
            factual_output_id="17171717-1717-4171-8171-171717171717",
            claim_id="cccccccc-cccc-5ccc-8ccc-cccccccccccc",
            canonical_key_sha256=SHA_A,
            reused_existing_claim=False,
        )


def test_evidence_not_found_has_no_canonical_claim_evidence_or_sanad_links() -> None:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    links_for_result = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "empty_links_for_evidence_not_found",
    )
    response = response_cls(**terminal_response_kwargs(outcome="EVIDENCE_NOT_FOUND"))

    factual_records, claim_links, evidence_links, sanad_links, defect_links = links_for_result(
        response
    )

    assert factual_records == ()
    assert claim_links == ()
    assert evidence_links == ()
    assert sanad_links == ()
    assert defect_links == ()


def test_main_evidence_union_is_sorted_deduplicated_and_restart_stable() -> None:
    merge = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "merge_main_evidence_claim_ids",
    )
    methodology = [
        "11111111-1111-5111-8111-111111111111",
        "22222222-2222-5222-8222-222222222222",
    ]
    generic_new = [
        "33333333-3333-5333-8333-333333333333",
        "11111111-1111-5111-8111-111111111111",
    ]
    reused = ["22222222-2222-5222-8222-222222222222"]

    first = merge(
        methodology_claim_ids=methodology,
        generic_new_claim_ids=generic_new,
        reused_claim_ids=reused,
    )
    rehydrated = merge(
        methodology_claim_ids=list(reversed(methodology)),
        generic_new_claim_ids=list(reversed(generic_new)),
        reused_claim_ids=list(reversed(reused)),
    )

    assert first == rehydrated == tuple(sorted(set(methodology + generic_new + reused)))


@pytest.mark.parametrize("missing", ["factual_output", "claim", "evidence", "sanad", "defect"])
def test_factual_result_cannot_complete_with_a_missing_canonical_link(missing: str) -> None:
    validate = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "validate_canonical_materialization_completion",
    )
    error_cls = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "CanonicalMethodologyMaterializationError",
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    output_id = output_cls(**factual_output_kwargs()).factual_output_id
    defect_id = "15151515-1515-4151-8151-151515151515"
    links = {
        "factual_output": [output_id],
        "claim": [output_id],
        "evidence": [output_id],
        "sanad": [output_id],
        "defect": [(output_id, defect_id)],
    }
    links[missing] = []

    with pytest.raises(error_cls) as raised:
        validate(
            factual_output_ids=[output_id],
            persisted_factual_output_ids=links["factual_output"],
            claim_link_output_ids=links["claim"],
            evidence_link_output_ids=links["evidence"],
            sanad_link_output_ids=links["sanad"],
            accepted_source_locator_pairs_by_output={output_id: ((SPAN_ID, SHA_A),)},
            evidence_link_source_locator_pairs_by_output={output_id: ((SPAN_ID, SHA_A),)},
            expected_defect_ids_by_output={output_id: (defect_id,)},
            defect_link_pairs=links["defect"],
            rejected_count=0,
        )

    assert raised.value.code == "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE"


def test_evidence_link_locator_set_must_exactly_equal_the_factual_output_set() -> None:
    validate = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "validate_canonical_materialization_completion",
    )
    error_cls = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "CanonicalMethodologyMaterializationError",
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    output_id = output_cls(**factual_output_kwargs()).factual_output_id

    with pytest.raises(error_cls) as raised:
        validate(
            factual_output_ids=[output_id],
            persisted_factual_output_ids=[output_id],
            claim_link_output_ids=[output_id],
            evidence_link_output_ids=[output_id],
            sanad_link_output_ids=[output_id],
            accepted_source_locator_pairs_by_output={output_id: ((SPAN_ID, SHA_A),)},
            evidence_link_source_locator_pairs_by_output={output_id: ()},
            expected_defect_ids_by_output={output_id: ()},
            defect_link_pairs=[],
            rejected_count=0,
        )

    assert raised.value.code == "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE"


def test_exact_canonical_set_allows_an_explicit_zero_defect_output() -> None:
    validate = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "validate_canonical_materialization_completion",
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    output_id = output_cls(**factual_output_kwargs()).factual_output_id

    validated = validate(
        factual_output_ids=[output_id],
        persisted_factual_output_ids=[output_id],
        claim_link_output_ids=[output_id],
        evidence_link_output_ids=[output_id],
        sanad_link_output_ids=[output_id],
        accepted_source_locator_pairs_by_output={output_id: ((SPAN_ID, SHA_A),)},
        evidence_link_source_locator_pairs_by_output={output_id: ((SPAN_ID, SHA_A),)},
        expected_defect_ids_by_output={output_id: ()},
        defect_link_pairs=[],
        rejected_count=0,
    )

    assert validated == (output_id,)


def test_canonical_completion_rejects_duplicate_defect_ownership_across_outputs() -> None:
    validate = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "validate_canonical_materialization_completion",
    )
    error_cls = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "CanonicalMethodologyMaterializationError",
    )
    output_ids = ("factual-a", "factual-b")
    defect_id = "defect-shared"
    locator_pairs = dict.fromkeys(output_ids, ((SPAN_ID, SHA_A),))

    with pytest.raises(error_cls):
        validate(
            factual_output_ids=output_ids,
            persisted_factual_output_ids=output_ids,
            claim_link_output_ids=output_ids,
            evidence_link_output_ids=output_ids,
            sanad_link_output_ids=output_ids,
            accepted_source_locator_pairs_by_output=locator_pairs,
            evidence_link_source_locator_pairs_by_output=locator_pairs,
            expected_defect_ids_by_output=dict.fromkeys(output_ids, (defect_id,)),
            defect_link_pairs=tuple((output_id, defect_id) for output_id in output_ids),
            rejected_count=0,
        )
