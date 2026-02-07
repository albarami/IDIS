"""Tests for DebateContext injection into LLMRoleRunner — Task B.

Validates that:
1. DebateContext content appears in the user message string.
2. Claim IDs in context match what's serialized.
3. LLMRoleRunner still works when context=None (backward compat).
4. Long claim text is truncated to MAX_CLAIM_TEXT_LENGTH.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from idis.debate.roles.llm_role_runner import (
    MAX_CLAIM_TEXT_LENGTH,
    DebateContext,
    LLMRoleRunner,
)
from idis.models.debate import DebateRole, DebateState


def _make_state(
    *,
    round_number: int = 1,
    tenant_id: str = "00000000-0000-0000-0000-tenant000001",
    deal_id: str = "00000000-0000-0000-0000-deal00000001",
) -> DebateState:
    """Create a minimal DebateState for testing."""
    return DebateState(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_registry_ref="claims://test",
        sanad_graph_ref="sanad://test",
        round_number=round_number,
    )


def _make_context(
    *,
    num_claims: int = 3,
    claim_text_override: str | None = None,
) -> DebateContext:
    """Create a DebateContext with test data."""
    claims = []
    for i in range(num_claims):
        text = claim_text_override if claim_text_override else f"Claim {i} text"
        claims.append(
            {
                "claim_id": f"00000000-0000-0000-0000-claim{i:08d}",
                "claim_text": text,
                "claim_class": "financial",
                "sanad_grade": ["A", "B", "C", "D"][i % 4],
                "source_doc": f"doc_{i}.pdf",
                "confidence": 0.7 + i * 0.05,
            }
        )
    return DebateContext(
        deal_name="TestCo",
        deal_sector="Fintech",
        deal_stage="Series A",
        deal_summary="A fintech startup focused on payments.",
        claims=claims,
        calc_results=[
            {
                "calc_id": "00000000-0000-0000-0000-calc00000001",
                "calc_name": "burn_rate",
                "result_value": "150000",
                "input_claim_ids": ["00000000-0000-0000-0000-claim00000000"],
            }
        ],
        conflicts=[
            {
                "claim_id_a": "00000000-0000-0000-0000-claim00000000",
                "claim_id_b": "00000000-0000-0000-0000-claim00000001",
                "conflict_type": "contradiction",
                "description": "Revenue figures conflict between pitch deck and financials.",
            }
        ],
    )


def _make_mock_llm_client() -> MagicMock:
    """Create a mock LLM client."""
    return MagicMock()


def _make_runner(
    *,
    context: DebateContext | None = None,
    role: DebateRole = DebateRole.ADVOCATE,
) -> LLMRoleRunner:
    """Create an LLMRoleRunner with a mock client."""
    return LLMRoleRunner(
        role=role,
        llm_client=_make_mock_llm_client(),
        system_prompt="You are a test agent.",
        context=context,
    )


class TestDebateContextSerializedInUserMessage:
    """Verify DebateContext content appears in the user message string."""

    def test_deal_overview_in_prompt(self) -> None:
        """Deal name, sector, stage, and summary appear in the user message."""
        ctx = _make_context()
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "## DEAL OVERVIEW" in prompt
        assert "TestCo" in prompt
        assert "Fintech" in prompt
        assert "Series A" in prompt
        assert "A fintech startup focused on payments." in prompt

    def test_claim_registry_in_prompt(self) -> None:
        """Claim IDs, texts, grades, and table headers appear in the prompt."""
        ctx = _make_context(num_claims=2)
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "## CLAIM REGISTRY" in prompt
        assert "2 claims extracted" in prompt
        assert "| claim_id |" in prompt
        assert "00000000-0000-0000-0000-claim00000000" in prompt
        assert "00000000-0000-0000-0000-claim00000001" in prompt
        assert "Claim 0 text" in prompt
        assert "Claim 1 text" in prompt

    def test_conflicts_in_prompt(self) -> None:
        """Conflicts section appears with claim IDs and description."""
        ctx = _make_context()
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "## CONFLICTS DETECTED (1)" in prompt
        assert "Revenue figures conflict" in prompt

    def test_calc_results_in_prompt(self) -> None:
        """Calc results section appears with calc IDs and values."""
        ctx = _make_context()
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "## CALC RESULTS (1)" in prompt
        assert "burn_rate" in prompt
        assert "150000" in prompt

    def test_debate_state_in_prompt(self) -> None:
        """Debate state section always appears in the prompt."""
        ctx = _make_context()
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "## DEBATE STATE" in prompt
        assert "Round: 1" in prompt
        assert state.deal_id in prompt

    def test_empty_calc_results_message(self) -> None:
        """When no calc results, a fallback message appears."""
        ctx = _make_context()
        ctx.calc_results = []
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "no deterministic calculations" in prompt


class TestClaimIdsInContextMatchState:
    """Verify claim_ids in context are the same ones serialized in the prompt."""

    def test_all_claim_ids_present(self) -> None:
        """Every claim_id from the context appears in the serialized prompt."""
        ctx = _make_context(num_claims=5)
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        for claim in ctx.claims:
            assert claim["claim_id"] in prompt, f"Claim ID {claim['claim_id']} not found in prompt"

    def test_no_extra_claim_ids(self) -> None:
        """Only claim_ids from context appear, not fabricated ones."""
        ctx = _make_context(num_claims=2)
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "claim00000002" not in prompt


class TestNoContextStillWorks:
    """LLMRoleRunner still works when context=None (backward compat)."""

    def test_prompt_without_context(self) -> None:
        """User prompt is generated without context sections."""
        runner = _make_runner(context=None)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert "## DEBATE STATE" in prompt
        assert "## DEAL OVERVIEW" not in prompt
        assert "## CLAIM REGISTRY" not in prompt

    def test_runner_instantiation_without_context(self) -> None:
        """LLMRoleRunner can be created without passing context at all."""
        runner = LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=_make_mock_llm_client(),
            system_prompt="Test prompt.",
        )
        assert runner._context is None


class TestLongClaimTextTruncated:
    """Claims over MAX_CLAIM_TEXT_LENGTH chars are truncated."""

    def test_long_text_truncated(self) -> None:
        """Claim text exceeding MAX_CLAIM_TEXT_LENGTH is cut off."""
        long_text = "A" * (MAX_CLAIM_TEXT_LENGTH + 100)
        ctx = _make_context(num_claims=1, claim_text_override=long_text)
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert long_text not in prompt
        truncated = long_text[:MAX_CLAIM_TEXT_LENGTH]
        assert truncated in prompt

    def test_short_text_not_truncated(self) -> None:
        """Claim text within MAX_CLAIM_TEXT_LENGTH is preserved fully."""
        short_text = "Short claim text"
        ctx = _make_context(num_claims=1, claim_text_override=short_text)
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        assert short_text in prompt


class TestClaimSortOrder:
    """Claims are sorted by sanad_grade, worst first (D, C, B, A)."""

    def test_worst_grade_first(self) -> None:
        """D-grade claims appear before A-grade claims in the serialized prompt."""
        ctx = _make_context(num_claims=4)
        runner = _make_runner(context=ctx)
        state = _make_state()

        prompt = runner._build_user_prompt(state)

        d_pos = prompt.index("claim00000003")  # D grade (i=3, grade index 3%4=3 -> D)
        a_pos = prompt.index("claim00000000")  # A grade (i=0, grade index 0%4=0 -> A)
        assert d_pos < a_pos, "D-grade claim should appear before A-grade claim"
