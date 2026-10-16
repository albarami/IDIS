"""GL-P01 version admission and byte-identical legacy regression tests.

Only synthetic existing fixtures are used. The golden was captured before any
GL-P01 production edit, at d36beb34f7bea631473bd34598f852cd95c88f6b.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from idis.methodology.models import (
    FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID,
    MethodologyQuestion,
    MethodologyType,
)
from idis.models import extraction_task as task_models
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskPlanningResult,
)
from idis.persistence.repositories.methodology_extraction import _canonicalize_planned_task
from idis.services.extraction.task_planner import InMemoryExtractionTaskPlanner
from tests.test_extraction_task_models import _answer_schema
from tests.test_extraction_task_models import _span as _model_span
from tests.test_extraction_task_planner import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    _classification,
    _question,
    _registry,
    _span,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests/fixtures/go_live_legacy_extraction_task_v1.json"
POLICY_ID = FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID
FORMULA = {
    "formula_ordinal": 1,
    "formula_type": "FDD_EBITDA_BRIDGE",
    "required": True,
    "scope": "p_and_l_control",
}


def _plan(question: MethodologyQuestion, *, ready: bool = True):
    return InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([question]),
        classifications=[_classification()] if ready else [],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )


def _policy_question(*, calculations=None) -> MethodologyQuestion:
    payload = _question().model_dump(mode="json")
    payload["methodology_version_id"] = POLICY_ID
    payload["required_calculations"] = [dict(FORMULA)] if calculations is None else calculations
    return MethodologyQuestion.model_validate(payload)


def _legacy_observations() -> dict[str, object]:
    """Capture complete schemas and serialized outputs, not self-derived hashes."""
    observations: dict[str, object] = {
        "model_fixture_expected_answer_json": _answer_schema().model_dump_json(),
        "model_fixture_span_json": _model_span().model_dump_json(),
        "schemas": {
            model.__name__: json.dumps(
                model.model_json_schema(), sort_keys=True, separators=(",", ":")
            )
            for model in (ExpectedAnswerSchema, ExtractionTask, ExtractionTaskPlanningResult)
        },
    }
    for family in MethodologyType:
        question = _question(
            methodology_type=family,
            target_categories=[
                "financial_schedule_model"
                if family is MethodologyType.FINANCIAL_DD
                else "business_plan_assumptions"
            ],
        )
        for ready in (True, False):
            result = _plan(question, ready=ready)
            task = result.tasks[0]
            observations[f"{family.value}_{'ready' if ready else 'blocked'}"] = {
                "task_id": task.extraction_task_id,
                "task_json": task.to_deterministic_json(),
                "result_json": result.model_dump_json(),
                "canonical_repository_json": _canonicalize_planned_task(
                    task
                ).to_deterministic_json(),
            }
    return observations


def test_legacy_complete_schemas_and_serialized_tasks_remain_byte_identical() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert _legacy_observations() == golden["observations"]


@pytest.mark.parametrize("ready", [True, False])
def test_policy_v2_planning_preserves_all_four_fields(ready: bool) -> None:
    result = _plan(_policy_question(), ready=ready)
    assert type(result).__name__ == "FddPolicyV2ExtractionTaskPlanningResult"
    task = result.tasks[0]
    assert type(task).__name__ == "FddPolicyV2ExtractionTask"
    assert type(task.expected_answer_schema).__name__ == "FddPolicyV2ExpectedAnswerSchema"
    assert task.expected_answer_schema.model_dump(mode="json")["required_calculations"] == [FORMULA]
    assert task.methodology_version_id == POLICY_ID


def test_policy_v2_persisted_task_round_trip_preserves_exact_shape() -> None:
    task = _plan(_policy_question()).tasks[0]
    restored = _canonicalize_planned_task(json.loads(task.to_deterministic_json()))
    assert type(restored) is type(task)
    assert restored.to_deterministic_json() == task.to_deterministic_json()


def test_legacy_constructor_rejects_policy_version_even_without_calculations() -> None:
    payload = _plan(_question()).tasks[0].model_dump(mode="json")
    payload["methodology_version_id"] = POLICY_ID
    payload["expected_answer_schema"]["required_calculations"] = []
    with pytest.raises(ValidationError):
        ExtractionTask.model_validate(payload)


def test_explicit_decoder_exists_without_broad_schema_union() -> None:
    decoder = getattr(task_models, "decode_extraction_task", None)
    assert callable(decoder), "GL-P01 needs an explicit outer-version decoder"


def _decoder():
    decoder = getattr(task_models, "decode_extraction_task", None)
    assert callable(decoder), "GL-P01 needs an explicit outer-version decoder"
    return decoder


def _policy_payload(*, calculations=None) -> dict:
    payload = _plan(_question()).tasks[0].model_dump(mode="json")
    payload["methodology_version_id"] = POLICY_ID
    payload["expected_answer_schema"]["required_calculations"] = (
        [dict(FORMULA)] if calculations is None else calculations
    )
    return payload


@pytest.mark.parametrize("calculations", [[FORMULA], []])
def test_decoder_routes_only_by_exact_outer_version(calculations: list) -> None:
    task = _decoder()(_policy_payload(calculations=calculations))
    assert type(task).__name__ == "FddPolicyV2ExtractionTask"
    assert (
        task.expected_answer_schema.model_dump(mode="json")["required_calculations"] == calculations
    )
    assert type(_decoder()(_plan(_question()).tasks[0].model_dump(mode="json"))) is ExtractionTask


@pytest.mark.parametrize(
    "version",
    ["financial_dd_v1", "unknown", POLICY_ID + "x", POLICY_ID[:-1], None],
)
def test_policy_shape_rejects_wrong_or_missing_outer_version(version: str | None) -> None:
    payload = _policy_payload()
    if version is None:
        del payload["methodology_version_id"]
    else:
        payload["methodology_version_id"] = version
    with pytest.raises(ValueError):
        _decoder()(payload)


@pytest.mark.parametrize(
    "calculations",
    [
        [{"calc_type": "revenue_growth", "required": True}],
        [FORMULA, {"calc_type": "revenue_growth", "required": True}],
        [{**FORMULA, "calc_type": "revenue_growth"}],
        [{**FORMULA, "unexpected": "value"}],
        [{key: value for key, value in FORMULA.items() if key != "required"}],
        [{**FORMULA, "formula_ordinal": "1"}],
        [{**FORMULA, "formula_ordinal": True}],
        [{**FORMULA, "required": "true"}],
        [{**FORMULA, "formula_type": "EBITDA"}],
        [{**FORMULA, "scope": "unknown"}],
        "not-a-list",
        1,
    ],
)
def test_policy_decoder_rejects_mixed_extra_missing_and_coerced_shapes(calculations) -> None:
    with pytest.raises(ValueError):
        _decoder()(_policy_payload(calculations=calculations))


@pytest.mark.parametrize("version", ["financial_dd_v1", POLICY_ID + "x"])
def test_direct_policy_constructor_rejects_other_versions_even_empty(version: str) -> None:
    cls = getattr(task_models, "FddPolicyV2ExtractionTask", None)
    assert cls is not None, "GL-P01 needs a disjoint policy task"
    payload = _policy_payload(calculations=[])
    payload["methodology_version_id"] = version
    with pytest.raises(ValidationError):
        cls.model_validate(payload)


def test_decoder_rejects_constructed_wrong_version_and_subclass_instances() -> None:
    decoder = _decoder()
    legacy = _plan(_question()).tasks[0]
    bypass = legacy.model_copy(update={"methodology_version_id": POLICY_ID})
    with pytest.raises(ValueError):
        decoder(bypass)

    class LegacySubclass(ExtractionTask):
        pass

    with pytest.raises(ValueError):
        decoder(LegacySubclass.model_validate(legacy.model_dump(mode="json")))


def test_decoder_rejects_bypassed_nested_requirement_validation() -> None:
    from idis.methodology.models import FddFormulaRequirement

    decoder = _decoder()
    task = decoder(_policy_payload())
    bad_requirement = FddFormulaRequirement.model_construct(**{**FORMULA, "required": "true"})
    schema = task.expected_answer_schema.model_copy(
        update={"required_calculations": [bad_requirement]}
    )
    with warnings.catch_warnings(record=True) as emitted, pytest.raises(ValueError):
        decoder(task.model_copy(update={"expected_answer_schema": schema}))
    assert emitted == [], "rejected requirement data must not escape through serializer warnings"


def test_decoder_and_repository_reject_nested_requirement_subclasses() -> None:
    from idis.methodology.models import FddFormulaRequirement

    class RequirementSubclass(FddFormulaRequirement):
        pass

    task = _decoder()(_policy_payload())
    schema = task.expected_answer_schema.model_copy(
        update={"required_calculations": [RequirementSubclass.model_validate(FORMULA)]}
    )
    bypass = task.model_copy(update={"expected_answer_schema": schema})
    with pytest.raises(ValueError):
        _decoder()(bypass)
    with pytest.raises(RuntimeError, match="^METHODOLOGY_TASK_COVERAGE_INCOMPLETE$"):
        _canonicalize_planned_task(bypass)


def test_repository_rejects_constructed_wrong_task_family_even_empty() -> None:
    legacy = _plan(_question()).tasks[0]
    bypass = legacy.model_copy(
        update={
            "methodology_version_id": POLICY_ID,
            "expected_answer_schema": legacy.expected_answer_schema.model_copy(
                update={"required_calculations": []}
            ),
        }
    )
    with pytest.raises(RuntimeError, match="^METHODOLOGY_TASK_COVERAGE_INCOMPLETE$"):
        _canonicalize_planned_task(bypass)


@pytest.mark.parametrize("field", ["methodology_type", "methodology_id"])
def test_policy_version_does_not_admit_commercial_identity(field: str) -> None:
    payload = _policy_payload()
    payload[field] = "commercial_dd"
    with pytest.raises(ValueError):
        _decoder()(payload)


def test_persisted_version_tampering_fails_closed_with_safe_code() -> None:
    payload = _policy_payload()
    payload["methodology_version_id"] = "financial_dd_v1"
    with pytest.raises(RuntimeError, match="^METHODOLOGY_TASK_COVERAGE_INCOMPLETE$"):
        _canonicalize_planned_task(payload)


def test_legacy_calculation_consumer_rejects_policy_v2_before_calc_type_access() -> None:
    from idis.services.runs.methodology_deterministic_calculation_helpers import (
        requested_calculations,
    )

    task = _decoder()(_policy_payload())
    with pytest.raises(ValueError, match="^METHODOLOGY_FDD_POLICY_V2_CALCULATION_UNSUPPORTED$"):
        requested_calculations([task])


def test_legacy_calculation_consumer_remains_unchanged() -> None:
    from idis.services.runs.methodology_deterministic_calculation_helpers import (
        requested_calculations,
    )

    task = _plan(_question()).tasks[0]
    assert [
        (item.extraction_task_id, requirement.calc_type)
        for item, requirement in requested_calculations([task])
    ] == [(task.extraction_task_id, "revenue_growth")]


def _worker_inputs():
    from idis.persistence.repositories.methodology_extraction import (
        DurableMethodologyExtractionTaskRecord,
    )

    question_payload = _policy_question().model_dump(mode="json")
    question_payload["source_trace"]["source_anchor_cell"] = "A2"
    question = MethodologyQuestion.model_validate(question_payload)
    payload = _plan(question).tasks[0].model_dump(mode="json")
    document_id = "99999999-9999-4999-8999-999999999999"
    span_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    source_text = " \tSynthetic source: EBITDA operands.\r\n"
    source_hash = hashlib.sha256(source_text.encode()).hexdigest()
    payload["document_id"] = document_id
    payload["source_spans"] = [
        {
            "document_id": document_id,
            "span_id": span_id,
            "evidence_tags": ["schedule"],
            "locator": {"sheet": "P&L", "cell": "B2"},
            "content_hash": source_hash,
            "text_excerpt": source_text,
        }
    ]
    task = _decoder()(payload)
    record = DurableMethodologyExtractionTaskRecord(
        task_record_id="66666666-6666-4666-8666-666666666666",
        selection_id="44444444-4444-4444-8444-444444444444",
        coverage_record_id="88888888-8888-4888-8888-888888888888",
        methodology_version_record_id="17171717-1717-4171-8171-171717171717",
        methodology_question_record_id="16161616-1616-4161-8161-161616161616",
        task_input_sha256="a" * 64,
        candidate_span_set_sha256="b" * 64,
        task=task,
    )
    version = SimpleNamespace(
        methodology_version_record_id=record.methodology_version_record_id,
        methodology_version_id=POLICY_ID,
        version_hash="c" * 64,
        source_sha256=question.source_trace.source_hash,
    )
    return {
        "task_record": record,
        "question": question,
        "version_record": version,
        "methodology_question_sha256": "d" * 64,
        "live_spans": [
            {
                "document_id": document_id,
                "span_id": span_id,
                "content_hash": source_hash,
                "locator": {"sheet": "P&L", "cell": "B2"},
                "text_excerpt": source_text,
            }
        ],
    }


def test_production_worker_request_and_prepare_preserve_policy_fields_without_dispatch() -> None:
    from idis.pipeline.worker import _build_methodology_extractor_request
    from idis.services.extraction.methodology_task_extractor import (
        ANTHROPIC_STANDARD_PRICE_CATALOG,
        AnthropicMethodologyTaskExtractor,
    )
    from tests.slice100_task6.test_runtime_durable_callbacks_red import _production_prompt_artifact

    class NoDispatchTransport:
        backend = "hermetic"
        calls = 0

        def send(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("GL-P01 must not dispatch")

    inputs = _worker_inputs()
    request = _build_methodology_extractor_request(**inputs)
    assert list(request.required_calculations) == [FORMULA]
    assert request.expected_answer_schema["required_calculations"] == [FORMULA]
    transport = NoDispatchTransport()
    extractor = AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=_production_prompt_artifact(),
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    prepared = extractor.prepare(request, budget_policy_id="hermetic")
    wire = json.loads(prepared.billable_payload)
    document = json.loads(wire["messages"][0]["content"][0]["text"])
    assert document["required_calculations"] == [FORMULA]
    assert document["expected_answer_schema"]["required_calculations"] == [FORMULA]
    assert document["question_text"] == inputs["question"].question_text
    assert document["candidate_spans"][0]["text"] == inputs["live_spans"][0]["text_excerpt"]
    assert transport.calls == 0


def test_production_worker_rejects_version_mismatch_before_prepare() -> None:
    from idis.pipeline.worker import _build_methodology_extractor_request

    inputs = _worker_inputs()
    inputs["version_record"].methodology_version_id = "financial_dd_v1"
    with pytest.raises(RuntimeError, match="^METHODOLOGY_TASK_COVERAGE_INCOMPLETE$"):
        _build_methodology_extractor_request(**inputs)


def test_abbreviated_legacy_request_remains_admitted() -> None:
    from idis.models.methodology_extraction import MethodologyTaskExtractorRequest
    from tests.slice100_task6._support import extractor_request_kwargs

    payload = extractor_request_kwargs()
    request = MethodologyTaskExtractorRequest.model_validate(payload)
    assert request.expected_answer_schema == {"answer_type": "numeric"}
    assert request.required_calculations == ({"calc_type": "fictional_bridge"},)


def test_policy_and_legacy_cdd_tasks_share_only_the_existing_safe_run_summary() -> None:
    from idis.models.extraction_task import ExtractionTaskPlanningRunResult

    policy = _decoder()(_policy_payload())
    cdd = _plan(
        _question(
            methodology_type=MethodologyType.COMMERCIAL_DD,
            target_categories=["business_plan_assumptions"],
            question_id="mq_commercial_dd_synthetic_0001",
        )
    ).tasks[0]
    result = ExtractionTaskPlanningRunResult.from_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=[cdd, policy],
    )
    assert result.summary.total_tasks == 2
    assert result.task_ids == [cdd.extraction_task_id, policy.extraction_task_id]
    assert {task.methodology_version_id for task in result.tasks} == {
        "commercial_dd_v1",
        POLICY_ID,
    }
    assert "required_calculations" not in json.dumps(result.to_run_step_summary())


def test_policy_expected_answer_revalidates_constructed_instances() -> None:
    from idis.methodology.models import FddFormulaRequirement

    cls = getattr(task_models, "FddPolicyV2ExpectedAnswerSchema", None)
    assert cls is not None
    valid = _decoder()(_policy_payload()).expected_answer_schema
    bypass = valid.model_copy(
        update={
            "required_calculations": [
                FddFormulaRequirement.model_construct(**{**FORMULA, "formula_ordinal": "1"})
            ]
        }
    )
    with warnings.catch_warnings(record=True) as emitted, pytest.raises(ValidationError):
        cls.model_validate(bypass)
    assert emitted == [], "rejected requirement data must not escape through serializer warnings"


def test_policy_task_does_not_default_a_missing_calculation_requirement_list() -> None:
    payload = _policy_payload()
    del payload["expected_answer_schema"]["required_calculations"]
    with pytest.raises(ValueError):
        _decoder()(payload)


def test_expected_answer_models_are_outside_all_68_frozen_snapshots() -> None:
    artifact = json.loads(
        (ROOT / "contracts/full_strict_v24_reviewed_preimage_v1.json").read_text(encoding="utf-8")
    )
    rows = artifact["schema_registry"]["rows"]
    assert len(rows) == 68
    names = ("ExpectedAnswerSchema", "ExtractionTask", "RequiredCalculation", "MethodologyQuestion")
    for row in rows:
        encoded = json.dumps(row["normalized_schema_snapshot"], sort_keys=True)
        for name in names:
            assert f'"{name}"' not in encoded
            assert f"/$defs/{name}" not in encoded
    extraction = [row for row in rows if row.get("schema_id") == "methodology-extraction-task"]
    assert len(extraction) == 1
    assert "MethodologyTaskExtractorRequest" in json.dumps(extraction[0])


def test_golden_artifact_is_pinned_to_before_edit_baseline() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["baseline_commit"] == "d36beb34f7bea631473bd34598f852cd95c88f6b"
    assert golden["pydantic_version"] == "2.12.5"
    raw = json.dumps(golden["observations"], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(raw).hexdigest() == golden["observations_sha256"]
