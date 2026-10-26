"""RED contracts for Task 7 expected-edge derivation and usage extraction."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.task7_census import CENSUS_SCHEMA_REGISTRY
from idis.pipeline.task7_edges import (
    CONSUMER_RECEIPT_INCOMPLETE,
    EDGE_RULE_REGISTRY,
    ApplicabilityDecision,
    ConsumerReceiptEvidence,
    EdgeDerivationError,
    EdgeDerivationRequest,
    ImmutableProducerFact,
    assert_three_way_equality,
    derive_expected_edges,
)
from idis.pipeline.task7_usage import (
    USAGE_EXTRACTOR_REGISTRY,
    USAGE_ROUTE_REGISTRY,
    ConsumerReceiptRootV1,
    ProducerRefV1,
    ReferenceOccurrenceV1,
    UsageExtractionRequest,
    UsageExtractorError,
    extract_usage_evidence,
)
from idis.pipeline.task7_uuid import derive_task7_uuid

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
_DEAL_ID = UUID("22222222-2222-4222-8222-222222222222")
_RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
_PRODUCER_ID = UUID("44444444-4444-4444-8444-444444444444")
_DECISION_ID = UUID("55555555-5555-4555-8555-555555555555")
_STEP_OUTPUT_ID = UUID("66666666-6666-4666-8666-666666666666")
_OUTPUT_OBJECT_ID = UUID("77777777-7777-4777-8777-777777777777")
_IDENTITY_HASH = "a" * 64
_VALUE_HASH = "b" * 64
_EVIDENCE_HASH = "c" * 64


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _producer(
    *,
    rule_id: str,
    condition: str,
    external_context_index_allowed: bool | None = None,
    applicability_subject_key: str | None = None,
) -> ImmutableProducerFact:
    return ImmutableProducerFact(
        rule_id=rule_id,
        producer_condition=condition,
        producer=ProducerRefV1(
            producer_type="OUTPUT_OBJECT",
            producer_id=_PRODUCER_ID,
            producer_identity_sha256=_IDENTITY_HASH,
            producer_value_sha256=_VALUE_HASH,
        ),
        external_context_index_allowed=external_context_index_allowed,
        applicability_subject_key=applicability_subject_key,
    )


def _request(
    *producers: ImmutableProducerFact,
    decisions: tuple[ApplicabilityDecision, ...] = (),
) -> EdgeDerivationRequest:
    return EdgeDerivationRequest(
        tenant_id=_TENANT_ID,
        deal_id=_DEAL_ID,
        run_id=_RUN_ID,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
        producers=tuple(producers),
        applicability_decisions=decisions,
    )


def _decision(rule_id: str, rule_hash: str) -> ApplicabilityDecision:
    return ApplicabilityDecision(
        applicability_decision_id=_DECISION_ID,
        predicate_id="STEP5_APPLICABLE",
        subject_key="coverage-subject-1",
        outcome="APPLICABLE",
        contract_rule_id=rule_id,
        contract_rule_version="1",
        contract_rule_hash=rule_hash,
        evidence_sha256=_EVIDENCE_HASH,
    )


def _usage_request(edge_index: int = 0) -> UsageExtractionRequest:
    edge = derive_expected_edges(
        _request(_producer(rule_id="E001_SOURCE_INVENTORY_ADMISSION", condition="ALWAYS"))
    )[edge_index]
    route = USAGE_ROUTE_REGISTRY[edge.route_ordinal]
    schema = CENSUS_SCHEMA_REGISTRY[route.census_schema_id]
    consumer = ConsumerReceiptRootV1(
        step_output_id=_STEP_OUTPUT_ID,
        output_object_id=_OUTPUT_OBJECT_ID,
        native_identity_sha256="d" * 64,
        native_output_sha256="e" * 64,
    )
    occurrence = ReferenceOccurrenceV1(
        consumer_receipt_output_object_id=consumer.output_object_id,
        consumer_receipt_identity_sha256=consumer.native_identity_sha256,
        reference_schema_id=route.census_schema_id,
        reference_schema_version=schema.schema_version,
        reference_schema_sha256=schema.schema_sha256,
        reference_path=route.json_pointer,
        reference_ordinal=0,
        producer_ref=edge.producer,
    )
    return UsageExtractionRequest(
        tenant_id=_TENANT_ID,
        deal_id=_DEAL_ID,
        run_id=_RUN_ID,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
        expected_edge_id=edge.expected_edge_id,
        edge_tuple_sha256=edge.edge_tuple_sha256,
        route_ordinal=edge.route_ordinal,
        usage_kind=edge.usage_kind,
        consumer_step_name=edge.consumer_step_name,
        consumer_subrole=edge.consumer_subrole,
        producer=edge.producer,
        consumer_receipt=consumer,
        native_reference_relation=route.native_reference_relation,
        consumer_fk=route.consumer_fk,
        reference_kind=route.reference_kind,
        occurrences=(occurrence,),
    )


def _error_code(error: BaseException) -> str | None:
    return getattr(error, "code", None)


def test_edge_registry_is_the_exact_48_row_v5_projection() -> None:
    artifact_rows = _artifact()["edge_rule_registry"]["rows"]
    expected = tuple(
        (
            row["rule_ordinal"],
            row["rule_id"],
            row["rule_version"],
            row["usage_kind"],
            row["predicate_kind"],
            row["predicate_id"],
            row["predicate_version"],
            row["expected_rule_sha256"],
        )
        for row in artifact_rows
    )
    actual = tuple(
        (
            row.rule_ordinal,
            row.rule_id,
            row.rule_version,
            row.usage_kind,
            row.predicate_kind,
            row.predicate_id,
            row.predicate_version,
            row.rule_sha256,
        )
        for row in EDGE_RULE_REGISTRY.values()
    )

    assert len(actual) == 48
    assert actual == expected


def test_usage_route_registry_is_the_exact_1756_row_v5_authority() -> None:
    expected = tuple(tuple(row) for row in _artifact()["usage_route_registry"]["rows"])
    actual = tuple(route.as_artifact_tuple() for route in USAGE_ROUTE_REGISTRY)

    assert len(actual) == 1_756
    assert actual == expected
    assert tuple(route.route_ordinal for route in USAGE_ROUTE_REGISTRY) == tuple(range(1_756))


def test_usage_extractor_registry_is_the_exact_19_row_v5_projection() -> None:
    artifact_rows = _artifact()["usage_extractor_registry"]["rows"]
    expected = tuple(
        (
            row["usage_kind"],
            row["extractor_id"],
            row["extractor_version"],
            tuple(row["program"]),
            row["expected_accepted_consumer_sha256"],
            row["expected_sha256"],
        )
        for row in artifact_rows
    )
    actual = tuple(
        (
            row.usage_kind,
            row.extractor_id,
            row.extractor_version,
            row.program,
            row.accepted_consumer_sha256,
            row.extractor_sha256,
        )
        for row in USAGE_EXTRACTOR_REGISTRY.values()
    )

    assert len(actual) == 19
    assert actual == expected
    assert sum(len(row.accepted_route_ordinals) for row in USAGE_EXTRACTOR_REGISTRY.values()) == (
        1_756
    )


def test_e029_e048_split_conserves_routes_and_blocks_layer1_external_context() -> None:
    e029 = tuple(
        route for route in USAGE_ROUTE_REGISTRY if route.rule_id == "E029_PROVIDER_FACT_SEMANTIC"
    )
    e048 = tuple(
        route
        for route in USAGE_ROUTE_REGISTRY
        if route.rule_id == "E048_PROVIDER_FACT_EXTERNAL_CONTEXT"
    )

    assert len(USAGE_ROUTE_REGISTRY) == 1_756
    assert len(e029) == 219
    assert len(e048) == 50
    assert not {route.consumer_step_name for route in e029} & {"GRAPH_EVIDENCE", "RAG_EVIDENCE"}
    assert {route.consumer_step_name for route in e048} == {"GRAPH_EVIDENCE", "RAG_EVIDENCE"}
    assert not {route.consumer_step_name for route in (*e029, *e048)} & {
        "METHODOLOGY_EVIDENCE_TRUST_COURT",
        "DEBATE",
    }


def test_unconditional_derivation_is_deterministic_and_uuid_bound() -> None:
    request = _request(_producer(rule_id="E001_SOURCE_INVENTORY_ADMISSION", condition="ALWAYS"))
    forward = derive_expected_edges(request)
    reverse = derive_expected_edges(replace(request, producers=tuple(reversed(request.producers))))

    assert forward == reverse
    assert len(forward) == 1
    edge = forward[0]
    expected_uuid = derive_task7_uuid(
        id_kind="expected_edge_id",
        values={
            "tenant_id": str(_TENANT_ID),
            "deal_id": str(_DEAL_ID),
            "run_id": str(_RUN_ID),
            "pipeline_contract_version": "full-strict-v21",
            "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
            "producer_type": edge.producer.producer_type,
            "producer_id": str(edge.producer.producer_id),
            "consumer_step": edge.consumer_step_name,
            "consumer_subrole": edge.consumer_subrole,
            "usage_kind": edge.usage_kind,
            "decision_id_or_null": None,
            "rule_id": edge.contract_rule_id,
            "rule_version": edge.contract_rule_version,
            "rule_hash": edge.contract_rule_hash,
        },
    )
    assert edge.expected_edge_id == expected_uuid.value


def test_append_only_applicability_requires_a_typed_matching_decision() -> None:
    rule = EDGE_RULE_REGISTRY["E009_COVERAGE_TO_PLANNING"]
    producer = _producer(
        rule_id=rule.rule_id,
        condition="STEP5_APPLICABLE",
        applicability_subject_key="coverage-subject-1",
    )

    with pytest.raises(EdgeDerivationError) as missing:
        derive_expected_edges(_request(producer))
    assert _error_code(missing.value) == CONSUMER_RECEIPT_INCOMPLETE

    edges = derive_expected_edges(
        _request(producer, decisions=(_decision(rule.rule_id, rule.rule_sha256),))
    )
    assert edges
    assert {edge.applicability_decision_id for edge in edges} == {_DECISION_ID}

    not_applicable = replace(
        _decision(rule.rule_id, rule.rule_sha256),
        outcome="NOT_APPLICABLE",
    )
    assert derive_expected_edges(_request(producer, decisions=(not_applicable,))) == ()


def test_rights_restricted_fact_keeps_e029_and_suppresses_e048_only() -> None:
    base_condition = "provider_id=finnhub;fact_domain=commercial_diligence;parent_outcome=HIT"
    semantic_edges = derive_expected_edges(
        _request(
            _producer(
                rule_id="E029_PROVIDER_FACT_SEMANTIC",
                condition=base_condition,
                external_context_index_allowed=False,
            )
        )
    )
    index_edges = derive_expected_edges(
        _request(
            _producer(
                rule_id="E048_PROVIDER_FACT_EXTERNAL_CONTEXT",
                condition=f"{base_condition};external_context_index_allowed=true",
                external_context_index_allowed=False,
            )
        )
    )

    assert semantic_edges
    assert not {edge.consumer_step_name for edge in semantic_edges} & {
        "GRAPH_EVIDENCE",
        "RAG_EVIDENCE",
    }
    assert {
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "SCORING",
        "METHODOLOGY_LAYER2_READINESS_PACKAGE",
        "LAYER2_IC_CHALLENGE",
        "ANALYSIS",
    } <= {edge.consumer_step_name for edge in semantic_edges}
    assert index_edges == ()

    allowed = derive_expected_edges(
        _request(
            _producer(
                rule_id="E048_PROVIDER_FACT_EXTERNAL_CONTEXT",
                condition=f"{base_condition};external_context_index_allowed=true",
                external_context_index_allowed=True,
            )
        )
    )
    assert {edge.consumer_step_name for edge in allowed} == {
        "GRAPH_EVIDENCE",
        "RAG_EVIDENCE",
    }


def test_all_19_usage_extractors_dispatch_over_only_their_literal_routes() -> None:
    dispatch_keys: list[str] = []
    for usage_kind, extractor in USAGE_EXTRACTOR_REGISTRY.items():
        route = USAGE_ROUTE_REGISTRY[extractor.accepted_route_ordinals[0]]
        schema = CENSUS_SCHEMA_REGISTRY[route.census_schema_id]
        producer = ProducerRefV1(
            producer_type="OUTPUT_OBJECT",
            producer_id=_PRODUCER_ID,
            producer_identity_sha256=_IDENTITY_HASH,
            producer_value_sha256=_VALUE_HASH,
        )
        consumer = ConsumerReceiptRootV1(
            step_output_id=_STEP_OUTPUT_ID,
            output_object_id=_OUTPUT_OBJECT_ID,
            native_identity_sha256="d" * 64,
            native_output_sha256="e" * 64,
        )
        occurrence = ReferenceOccurrenceV1(
            consumer_receipt_output_object_id=consumer.output_object_id,
            consumer_receipt_identity_sha256=consumer.native_identity_sha256,
            reference_schema_id=route.census_schema_id,
            reference_schema_version=schema.schema_version,
            reference_schema_sha256=schema.schema_sha256,
            reference_path=route.json_pointer,
            reference_ordinal=0,
            producer_ref=producer,
        )
        result = extract_usage_evidence(
            UsageExtractionRequest(
                tenant_id=_TENANT_ID,
                deal_id=_DEAL_ID,
                run_id=_RUN_ID,
                pipeline_contract_version="full-strict-v21",
                pipeline_contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
                expected_edge_id=UUID(int=route.route_ordinal + 1),
                edge_tuple_sha256=_EVIDENCE_HASH,
                route_ordinal=route.route_ordinal,
                usage_kind=usage_kind,
                consumer_step_name=route.consumer_step_name,
                consumer_subrole=route.consumer_subrole,
                producer=producer,
                consumer_receipt=consumer,
                native_reference_relation=route.native_reference_relation,
                consumer_fk=route.consumer_fk,
                reference_kind=route.reference_kind,
                occurrences=(occurrence,),
            )
        )
        dispatch_keys.append(result.usage_kind)
        assert result.extractor_sha256 == extractor.extractor_sha256
        assert result.occurrences == (occurrence,)

    assert tuple(dispatch_keys) == tuple(USAGE_EXTRACTOR_REGISTRY)


@pytest.mark.parametrize("mutation", ["missing", "free_relation", "producer_hash"])
def test_usage_extractors_fail_closed_on_missing_or_free_references(mutation: str) -> None:
    request = _usage_request()
    if mutation == "missing":
        request = replace(request, occurrences=())
    elif mutation == "free_relation":
        request = replace(request, native_reference_relation="unreviewed_json_probe")
    else:
        bad = replace(
            request.occurrences[0],
            producer_ref=replace(
                request.producer,
                producer_value_sha256="0" * 64,
            ),
        )
        request = replace(request, occurrences=(bad,))

    with pytest.raises(UsageExtractorError) as caught:
        extract_usage_evidence(request)

    assert _error_code(caught.value) == CONSUMER_RECEIPT_INCOMPLETE


def test_three_way_equality_is_independent_and_usage_hash_bound() -> None:
    edge = derive_expected_edges(
        _request(_producer(rule_id="E001_SOURCE_INVENTORY_ADMISSION", condition="ALWAYS"))
    )[0]
    usage = extract_usage_evidence(_usage_request())
    receipt = ConsumerReceiptEvidence(
        expected_edge_id=edge.expected_edge_id,
        edge_tuple_sha256=edge.edge_tuple_sha256,
        consumer_step_name=edge.consumer_step_name,
        consumer_subrole=edge.consumer_subrole,
        usage_evidence_sha256=usage.usage_evidence_sha256,
    )

    proof = assert_three_way_equality(
        derived=(edge,),
        persisted=(replace(edge),),
        receipts=(receipt,),
        expected_usage_evidence={
            edge.expected_edge_id: usage.usage_evidence_sha256,
        },
    )
    assert proof.edge_count == proof.receipt_count == 1
    assert len(proof.proof_sha256) == 64

    with pytest.raises(EdgeDerivationError) as missing:
        assert_three_way_equality(
            derived=(edge,),
            persisted=(replace(edge),),
            receipts=(),
            expected_usage_evidence={
                edge.expected_edge_id: usage.usage_evidence_sha256,
            },
        )
    assert _error_code(missing.value) == CONSUMER_RECEIPT_INCOMPLETE

    with pytest.raises(EdgeDerivationError) as changed:
        assert_three_way_equality(
            derived=(edge,),
            persisted=(replace(edge, edge_tuple_sha256="0" * 64),),
            receipts=(receipt,),
            expected_usage_evidence={
                edge.expected_edge_id: usage.usage_evidence_sha256,
            },
        )
    assert _error_code(changed.value) == CONSUMER_RECEIPT_INCOMPLETE


def test_three_way_equality_rejects_a_consistently_forged_edge_tuple() -> None:
    edge = derive_expected_edges(
        _request(_producer(rule_id="E001_SOURCE_INVENTORY_ADMISSION", condition="ALWAYS"))
    )[0]
    usage = extract_usage_evidence(_usage_request())
    forged = replace(edge, edge_tuple_sha256="0" * 64)
    forged_receipt = ConsumerReceiptEvidence(
        expected_edge_id=forged.expected_edge_id,
        edge_tuple_sha256=forged.edge_tuple_sha256,
        consumer_step_name=forged.consumer_step_name,
        consumer_subrole=forged.consumer_subrole,
        usage_evidence_sha256=usage.usage_evidence_sha256,
    )

    with pytest.raises(EdgeDerivationError) as caught:
        assert_three_way_equality(
            derived=(forged,),
            persisted=(forged,),
            receipts=(forged_receipt,),
            expected_usage_evidence={
                forged.expected_edge_id: usage.usage_evidence_sha256,
            },
        )

    assert _error_code(caught.value) == CONSUMER_RECEIPT_INCOMPLETE
