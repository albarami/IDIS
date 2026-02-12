"""Tests for RunOrchestrator._sanitize_for_json result summary sanitization.

Covers:
- Plain JSON-safe dicts pass through unchanged
- Pydantic BaseModel instances are converted via model_dump
- Nested Pydantic models inside lists are converted
- UUID objects are converted to strings
- Empty dicts and None values are preserved
- Datetime objects are converted to ISO format strings
- Complete step with AnalysisBundle does not raise
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.runs.orchestrator import RunOrchestrator

TENANT = "11111111-1111-1111-1111-111111111111"


class _DummyModel(BaseModel):
    """Minimal Pydantic model for testing."""

    x: int = 1
    label: str = "test"


class _NestedModel(BaseModel):
    """Pydantic model with nested structure for testing."""

    items: list[_DummyModel] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class TestSanitizeForJson:
    """Tests for _sanitize_for_json result summary sanitization."""

    def test_plain_dict_passes_through(self) -> None:
        """Plain JSON-safe dict is returned unchanged."""
        data: dict[str, Any] = {"count": 5, "ids": ["a", "b"], "nested": {"x": 1}}
        result = RunOrchestrator._sanitize_for_json(data)
        assert result == data
        json.dumps(result)

    def test_pydantic_model_converted(self) -> None:
        """Pydantic BaseModel in result is converted via model_dump."""
        model = _DummyModel(x=42, label="hello")
        data: dict[str, Any] = {"model": model, "count": 3}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["model"], dict)
        assert result["model"]["x"] == 42
        assert result["model"]["label"] == "hello"
        assert result["count"] == 3
        json.dumps(result)

    def test_nested_pydantic_in_list(self) -> None:
        """Pydantic models nested inside lists are converted."""
        data: dict[str, Any] = {"items": [_DummyModel(), _DummyModel(x=2)]}
        result = RunOrchestrator._sanitize_for_json(data)
        assert all(isinstance(item, dict) for item in result["items"])
        assert result["items"][0]["x"] == 1
        assert result["items"][1]["x"] == 2
        json.dumps(result)

    def test_deeply_nested_pydantic(self) -> None:
        """Deeply nested Pydantic model (model containing list of models)."""
        nested = _NestedModel(
            items=[_DummyModel(x=10), _DummyModel(x=20)],
            meta={"key": "value"},
        )
        data: dict[str, Any] = {"bundle": nested}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["bundle"], dict)
        assert len(result["bundle"]["items"]) == 2
        json.dumps(result)

    def test_uuid_converted_to_string(self) -> None:
        """UUID objects are converted to strings."""
        uid = uuid.uuid4()
        data: dict[str, Any] = {"id": uid}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["id"], str)
        assert result["id"] == str(uid)
        json.dumps(result)

    def test_datetime_converted_to_isoformat(self) -> None:
        """Datetime objects are converted to ISO format strings."""
        now = datetime.now(UTC)
        data: dict[str, Any] = {"ts": now}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["ts"], str)
        assert result["ts"] == now.isoformat()
        json.dumps(result)

    def test_empty_dict_passes_through(self) -> None:
        """Empty dict is handled."""
        assert RunOrchestrator._sanitize_for_json({}) == {}

    def test_none_values_preserved(self) -> None:
        """None values pass through."""
        data: dict[str, Any] = {"x": None}
        result = RunOrchestrator._sanitize_for_json(data)
        assert result["x"] is None
        json.dumps(result)

    def test_primitives_pass_through(self) -> None:
        """Primitive types pass through unchanged."""
        assert RunOrchestrator._sanitize_for_json(42) == 42
        assert RunOrchestrator._sanitize_for_json(3.14) == 3.14
        assert RunOrchestrator._sanitize_for_json("hello") == "hello"
        assert RunOrchestrator._sanitize_for_json(True) is True
        assert RunOrchestrator._sanitize_for_json(None) is None

    def test_tuple_converted_to_list(self) -> None:
        """Tuples are converted to lists during sanitization."""
        data: dict[str, Any] = {"ids": (1, 2, 3)}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["ids"], list)
        assert result["ids"] == [1, 2, 3]
        json.dumps(result)


class TestSanitizeWithRealModels:
    """Tests using actual IDIS Pydantic models from the analysis pipeline."""

    def test_analysis_bundle_serializable(self) -> None:
        """AnalysisBundle Pydantic model in result is JSON-serializable."""
        from idis.analysis.models import AnalysisBundle

        bundle = AnalysisBundle(
            deal_id="deal-1",
            tenant_id=TENANT,
            run_id="run-1",
            reports=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        data: dict[str, Any] = {"_analysis_bundle": bundle, "agent_count": 0}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["_analysis_bundle"], dict)
        assert result["_analysis_bundle"]["deal_id"] == "deal-1"
        json.dumps(result)

    def test_scorecard_serializable(self) -> None:
        """Scorecard Pydantic model in result is JSON-serializable."""
        from idis.analysis.models import AnalysisMuhasabahRecord
        from idis.analysis.scoring.models import (
            DimensionScore,
            RoutingAction,
            ScoreBand,
            Scorecard,
            ScoreDimension,
            Stage,
        )

        muhasabah = AnalysisMuhasabahRecord(
            agent_id="test-agent",
            output_id="test-output",
            supported_claim_ids=["claim-1"],
            evidence_summary="Strong evidence from financials",
            counter_hypothesis="Market may contract",
            confidence=0.8,
            confidence_justification="Well-supported by data",
            timestamp="2026-01-01T00:00:00Z",
        )
        dim_scores = {
            dim: DimensionScore(
                dimension=dim,
                score=0.75,
                rationale="Test rationale for scoring",
                supported_claim_ids=["claim-1"],
                supported_calc_ids=["calc-1"],
                confidence=0.8,
                confidence_justification="Well-supported by data",
                muhasabah=muhasabah,
            )
            for dim in ScoreDimension
        }
        scorecard = Scorecard(
            stage=Stage.SERIES_A,
            dimension_scores=dim_scores,
            composite_score=75.0,
            score_band=ScoreBand.HIGH,
            routing=RoutingAction.INVEST,
        )
        data: dict[str, Any] = {"_scorecard": scorecard, "composite_score": 75.0}
        result = RunOrchestrator._sanitize_for_json(data)
        assert isinstance(result["_scorecard"], dict)
        assert result["_scorecard"]["composite_score"] == 75.0
        json.dumps(result)


class TestCompleteStepWithPydanticResult:
    """Integration test: _complete_step with Pydantic model in result."""

    def test_complete_step_does_not_raise(self) -> None:
        """_complete_step succeeds when result contains a Pydantic model."""
        repo = InMemoryRunStepsRepository(TENANT)
        orchestrator = RunOrchestrator(
            audit_sink=InMemoryAuditSink(),
            run_steps_repo=repo,
        )

        step = RunStep(
            step_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            run_id=str(uuid.uuid4()),
            step_name=StepName.ANALYSIS,
            step_order=7,
            status=StepStatus.RUNNING,
            started_at=datetime.now(UTC).isoformat(),
        )
        repo.create(step)

        model = _DummyModel(x=99, label="analysis")
        result: dict[str, Any] = {
            "_analysis_bundle": model,
            "agent_count": 1,
            "report_ids": [str(uuid.uuid4())],
        }

        orchestrator._complete_step(step, result)

        assert step.status == StepStatus.COMPLETED
        assert isinstance(step.result_summary["_analysis_bundle"], dict)
        assert step.result_summary["_analysis_bundle"]["x"] == 99
        json.dumps(step.result_summary)
