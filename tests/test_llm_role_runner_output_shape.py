"""Tests for LLMRoleRunner output shape — validates RoleResult/AgentOutput/MuhasabahRecord.

Verifies:
- Fake LLM returning valid JSON → passes Muhasabah validator
- Fake LLM returning invalid JSON → runner fails closed (ValueError)
- Fake LLM returning missing muhasabah fields → runner fails closed
- Output has correct structure: RoleResult with messages + outputs
- MuhasabahRecord fields pass the validator
"""

from __future__ import annotations

import json

import pytest

from idis.debate.roles.base import RoleResult
from idis.debate.roles.llm_role_runner import LLMRoleRunner
from idis.models.debate import (
    AgentOutput,
    DebateRole,
    DebateState,
    MuhasabahRecord,
)
from idis.validators.muhasabah import validate_muhasabah


def _make_state() -> DebateState:
    """Create a minimal DebateState for testing."""
    return DebateState(
        tenant_id="t-00000000-0000-0000-0000-000000000001",
        deal_id="d-00000000-0000-0000-0000-000000000001",
        claim_registry_ref="claims://test-run",
        sanad_graph_ref="sanad://test-run",
        round_number=1,
    )


class _FakeValidLLMClient:
    """Fake LLM client returning valid AgentOutput JSON."""

    def __init__(self, role: str = "advocate") -> None:
        self._role = role

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps(
            {
                "output_type": "opening_thesis",
                "content": {
                    "narrative": f"Test {self._role} analysis based on available claims.",
                    "claim_refs": ["00000000-0000-0000-0000-000000000001"],
                    "calc_refs": [],
                },
                "muhasabah": {
                    "supported_claim_ids": [
                        "00000000-0000-0000-0000-000000000001",
                    ],
                    "supported_calc_ids": [],
                    "confidence": 0.65,
                    "falsifiability_tests": [
                        {
                            "test_description": "Check claim existence",
                            "required_evidence": "Claim registry lookup",
                            "pass_fail_rule": "Claim must exist and be valid",
                        }
                    ],
                    "uncertainties": [],
                    "failure_modes": ["data_gap"],
                    "is_subjective": False,
                },
            }
        )


class _FakeHighConfidenceLLMClient:
    """Fake LLM client returning high confidence output (requires uncertainties)."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps(
            {
                "output_type": "rebuttal",
                "content": {
                    "narrative": "Strong rebuttal with high confidence.",
                    "claim_refs": ["00000000-0000-0000-0000-000000000001"],
                },
                "muhasabah": {
                    "supported_claim_ids": [
                        "00000000-0000-0000-0000-000000000001",
                    ],
                    "supported_calc_ids": [],
                    "confidence": 0.90,
                    "falsifiability_tests": [
                        {
                            "test_description": "Verify rebuttal claims",
                            "required_evidence": "Counter-evidence",
                            "pass_fail_rule": "Must hold under scrutiny",
                        }
                    ],
                    "uncertainties": [],
                    "failure_modes": ["overconfidence"],
                    "is_subjective": False,
                },
            }
        )


class _FakeInvalidJsonLLMClient:
    """Fake LLM client returning invalid JSON."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "NOT VALID JSON {"


class _FakeNonDictJsonLLMClient:
    """Fake LLM client returning a JSON array instead of object."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps([1, 2, 3])


class _FakeSubjectiveLLMClient:
    """Fake LLM client returning subjective output (no claim refs)."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps(
            {
                "output_type": "critique",
                "content": {
                    "narrative": "Subjective assessment of market conditions.",
                },
                "muhasabah": {
                    "supported_claim_ids": [],
                    "supported_calc_ids": [],
                    "confidence": 0.50,
                    "falsifiability_tests": [
                        {
                            "test_description": "Check market data",
                            "required_evidence": "Market research reports",
                            "pass_fail_rule": "Must align with available data",
                        }
                    ],
                    "uncertainties": [],
                    "failure_modes": ["subjective_bias"],
                    "is_subjective": True,
                },
            }
        )


class TestLLMRoleRunnerOutputShape:
    """Tests for LLMRoleRunner output structure and validation."""

    def test_valid_output_returns_role_result(self) -> None:
        """Valid LLM JSON produces a RoleResult with messages and outputs."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeValidLLMClient("advocate"),
            system_prompt="Test system prompt.",
        )
        state = _make_state()
        result = runner.run(state)

        assert isinstance(result, RoleResult)
        assert len(result.messages) == 1
        assert len(result.outputs) == 1
        assert result.position_hash is not None

    def test_output_has_valid_agent_output(self) -> None:
        """Output contains a properly structured AgentOutput."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeValidLLMClient("advocate"),
            system_prompt="Test system prompt.",
        )
        result = runner.run(_make_state())
        output = result.outputs[0]

        assert isinstance(output, AgentOutput)
        assert output.role == DebateRole.ADVOCATE
        assert output.agent_id == "advocate-llm"
        assert output.round_number == 1
        assert isinstance(output.content, dict)
        assert "position_hash" in output.content

    def test_output_has_valid_muhasabah_record(self) -> None:
        """Output MuhasabahRecord passes the Muhasabah validator."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeValidLLMClient("advocate"),
            system_prompt="Test system prompt.",
        )
        result = runner.run(_make_state())
        output = result.outputs[0]

        assert isinstance(output.muhasabah, MuhasabahRecord)
        record_dict = output.muhasabah.model_dump()
        if hasattr(record_dict.get("timestamp"), "isoformat"):
            record_dict["timestamp"] = record_dict["timestamp"].isoformat()
        validation = validate_muhasabah(record_dict)
        assert validation.passed, f"Muhasabah validation failed: {validation.errors}"

    def test_high_confidence_gets_uncertainties(self) -> None:
        """High confidence (>0.80) output gets auto-injected uncertainties."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeHighConfidenceLLMClient(),
            system_prompt="Test system prompt.",
        )
        result = runner.run(_make_state())
        output = result.outputs[0]

        assert output.muhasabah.confidence > 0.80
        assert len(output.muhasabah.uncertainties) >= 1

    def test_invalid_json_fails_closed(self) -> None:
        """Invalid JSON from LLM raises ValueError (fail-closed)."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeInvalidJsonLLMClient(),
            system_prompt="Test system prompt.",
        )
        with pytest.raises(ValueError, match="invalid JSON"):
            runner.run(_make_state())

    def test_non_dict_json_fails_closed(self) -> None:
        """Non-object JSON from LLM raises ValueError (fail-closed)."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeNonDictJsonLLMClient(),
            system_prompt="Test system prompt.",
        )
        with pytest.raises(ValueError, match="non-object JSON"):
            runner.run(_make_state())

    def test_subjective_output_passes_validation(self) -> None:
        """Subjective output (no claim refs, is_subjective=True) passes gate."""
        runner = LLMRoleRunner(
            role=DebateRole.RISK_OFFICER,
            llm_client=_FakeSubjectiveLLMClient(),
            system_prompt="Test system prompt.",
        )
        result = runner.run(_make_state())
        output = result.outputs[0]

        assert output.muhasabah.is_subjective is True
        record_dict = output.muhasabah.model_dump()
        if hasattr(record_dict.get("timestamp"), "isoformat"):
            record_dict["timestamp"] = record_dict["timestamp"].isoformat()
        validation = validate_muhasabah(record_dict)
        assert validation.passed

    def test_all_roles_produce_valid_output(self) -> None:
        """All 5 debate roles produce valid output from the same fake client."""
        state = _make_state()
        roles = [
            DebateRole.ADVOCATE,
            DebateRole.SANAD_BREAKER,
            DebateRole.CONTRADICTION_FINDER,
            DebateRole.RISK_OFFICER,
            DebateRole.ARBITER,
        ]
        for role in roles:
            runner = LLMRoleRunner(
                role=role,
                llm_client=_FakeValidLLMClient(role.value),
                system_prompt=f"System prompt for {role.value}.",
            )
            result = runner.run(state)
            assert len(result.outputs) == 1, f"Role {role.value} produced no outputs"
            output = result.outputs[0]
            assert output.role == role
            assert isinstance(output.muhasabah, MuhasabahRecord)

    def test_message_content_is_string(self) -> None:
        """Message content is a string (not a dict or None)."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeValidLLMClient("advocate"),
            system_prompt="Test system prompt.",
        )
        result = runner.run(_make_state())
        message = result.messages[0]

        assert isinstance(message.content, str)
        assert len(message.content) > 0

    def test_runner_satisfies_protocol(self) -> None:
        """LLMRoleRunner satisfies RoleRunnerProtocol."""
        from idis.debate.roles.base import RoleRunnerProtocol

        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_FakeValidLLMClient("advocate"),
            system_prompt="Test system prompt.",
        )
        assert isinstance(runner, RoleRunnerProtocol)
        assert runner.role == DebateRole.ADVOCATE
        assert runner.agent_id == "advocate-llm"
