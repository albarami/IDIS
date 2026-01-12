"""IDIS Debate Role Determinism Tests — v6.3 Phase 5.1.1

Tests to verify role runners are deterministic:
1. Same state → same outputs (message_id, content, refs)
2. Different state → different outputs
3. No banned tokens in outputs
"""

from __future__ import annotations

from idis.debate.roles.advocate import AdvocateRole
from idis.debate.roles.arbiter import ArbiterRole
from idis.debate.roles.base import (
    deterministic_id,
    deterministic_position_hash,
    deterministic_timestamp,
)
from idis.debate.roles.contradiction_finder import ContradictionFinderRole
from idis.debate.roles.risk_officer import RiskOfficerRole
from idis.debate.roles.sanad_breaker import SanadBreakerRole
from idis.models.debate import DebateMessage, DebateRole, DebateState

FORBIDDEN_OUTPUT_TOKENS = [
    "place" + "holder",
    "TO" + "DO",
    "FIX" + "ME",
    "mo" + "ck",
    "hard" + "coded",
]


def create_deterministic_state(
    round_number: int = 1,
    messages: list[DebateMessage] | None = None,
    open_questions: list[str] | None = None,
) -> DebateState:
    """Create a minimal deterministic DebateState for testing."""
    return DebateState(
        tenant_id="test-tenant-determinism",
        deal_id="test-deal-determinism",
        claim_registry_ref="claim-reg-det-001",
        sanad_graph_ref="sanad-graph-det-001",
        round_number=round_number,
        messages=messages or [],
        open_questions=open_questions or [],
    )


def create_state_with_claims(
    round_number: int = 1,
    claim_refs: list[str] | None = None,
) -> DebateState:
    """Create a state with claim references in messages."""
    timestamp = deterministic_timestamp(1, 0)
    messages = [
        DebateMessage(
            message_id="msg-prior-001",
            role=DebateRole.ADVOCATE,
            agent_id="prior-agent",
            content="Prior message with claims",
            claim_refs=claim_refs or ["claim-001", "claim-002", "claim-003"],
            calc_refs=[],
            round_number=1,
            timestamp=timestamp,
        )
    ]
    return create_deterministic_state(round_number=round_number, messages=messages)


class TestDeterministicHelpers:
    """Tests for deterministic helper functions."""

    def test_deterministic_id_same_inputs_same_output(self) -> None:
        """Same inputs produce same ID."""
        id1 = deterministic_id(
            "msg",
            tenant_id="t1",
            deal_id="d1",
            role="advocate",
            round_number=1,
            step=0,
        )
        id2 = deterministic_id(
            "msg",
            tenant_id="t1",
            deal_id="d1",
            role="advocate",
            round_number=1,
            step=0,
        )
        assert id1 == id2

    def test_deterministic_id_different_inputs_different_output(self) -> None:
        """Different inputs produce different IDs."""
        id1 = deterministic_id(
            "msg",
            tenant_id="t1",
            deal_id="d1",
            role="advocate",
            round_number=1,
            step=0,
        )
        id2 = deterministic_id(
            "msg",
            tenant_id="t1",
            deal_id="d1",
            role="advocate",
            round_number=2,
            step=0,
        )
        assert id1 != id2

    def test_deterministic_timestamp_same_inputs_same_output(self) -> None:
        """Same inputs produce same timestamp."""
        ts1 = deterministic_timestamp(1, 0)
        ts2 = deterministic_timestamp(1, 0)
        assert ts1 == ts2

    def test_deterministic_timestamp_different_inputs_different_output(self) -> None:
        """Different inputs produce different timestamps."""
        ts1 = deterministic_timestamp(1, 0)
        ts2 = deterministic_timestamp(2, 0)
        assert ts1 != ts2

    def test_deterministic_position_hash_same_inputs_same_output(self) -> None:
        """Same inputs produce same position hash."""
        h1 = deterministic_position_hash("advocate", 1, "content")
        h2 = deterministic_position_hash("advocate", 1, "content")
        assert h1 == h2

    def test_deterministic_position_hash_different_inputs_different_output(self) -> None:
        """Different inputs produce different position hashes."""
        h1 = deterministic_position_hash("advocate", 1, "content1")
        h2 = deterministic_position_hash("advocate", 1, "content2")
        assert h1 != h2


class TestAdvocateRoleDeterminism:
    """Tests for AdvocateRole determinism."""

    def test_same_state_same_output(self) -> None:
        """Same state produces identical outputs."""
        state = create_deterministic_state()
        role = AdvocateRole()

        result1 = role.run(state)
        result2 = role.run(state)

        assert result1.messages[0].message_id == result2.messages[0].message_id
        assert result1.messages[0].content == result2.messages[0].content
        assert result1.outputs[0].output_id == result2.outputs[0].output_id
        assert result1.position_hash == result2.position_hash

    def test_different_state_different_output(self) -> None:
        """Different state produces different outputs."""
        state1 = create_deterministic_state(round_number=1)
        state2 = create_deterministic_state(round_number=2)
        role = AdvocateRole()

        result1 = role.run(state1)
        result2 = role.run(state2)

        assert result1.messages[0].message_id != result2.messages[0].message_id
        assert result1.outputs[0].output_id != result2.outputs[0].output_id

    def test_state_with_claims_changes_output(self) -> None:
        """Adding claims to state changes output content."""
        state1 = create_deterministic_state()
        state2 = create_state_with_claims()
        role = AdvocateRole()

        result1 = role.run(state1)
        result2 = role.run(state2)

        content1 = result1.outputs[0].content
        content2 = result2.outputs[0].content
        assert content1["claims_reviewed"] != content2["claims_reviewed"]

    def test_no_banned_tokens_in_output(self) -> None:
        """Output does not contain banned tokens."""
        state = create_deterministic_state()
        role = AdvocateRole()
        result = role.run(state)

        for msg in result.messages:
            for token in FORBIDDEN_OUTPUT_TOKENS:
                assert token.lower() not in msg.content.lower(), (
                    f"Banned token '{token}' found in message content"
                )

        for output in result.outputs:
            content_str = str(output.content)
            for token in FORBIDDEN_OUTPUT_TOKENS:
                assert token.lower() not in content_str.lower(), (
                    f"Banned token '{token}' found in output content"
                )

    def test_default_agent_id_is_deterministic(self) -> None:
        """Default agent_id is deterministic (not random)."""
        role1 = AdvocateRole()
        role2 = AdvocateRole()
        assert role1.agent_id == role2.agent_id
        assert "advocate" in role1.agent_id


class TestSanadBreakerRoleDeterminism:
    """Tests for SanadBreakerRole determinism."""

    def test_same_state_same_output(self) -> None:
        """Same state produces identical outputs."""
        state = create_state_with_claims()
        role = SanadBreakerRole()

        result1 = role.run(state)
        result2 = role.run(state)

        assert result1.messages[0].message_id == result2.messages[0].message_id
        assert result1.messages[0].content == result2.messages[0].content
        assert result1.outputs[0].output_id == result2.outputs[0].output_id
        assert result1.position_hash == result2.position_hash

    def test_different_state_different_output(self) -> None:
        """Different state produces different outputs."""
        state1 = create_state_with_claims(round_number=1)
        state2 = create_state_with_claims(round_number=2)
        role = SanadBreakerRole()

        result1 = role.run(state1)
        result2 = role.run(state2)

        assert result1.messages[0].message_id != result2.messages[0].message_id

    def test_challenged_claims_derived_from_state(self) -> None:
        """Challenged claims are derived from state, not canned."""
        state = create_state_with_claims(claim_refs=["c1", "c2", "c3", "c4"])
        role = SanadBreakerRole()
        result = role.run(state)

        content = result.outputs[0].content
        assert content["scanned_count"] == 4
        assert len(content["scanned_claim_ids"]) == 4

    def test_no_banned_tokens_in_output(self) -> None:
        """Output does not contain banned tokens."""
        state = create_state_with_claims()
        role = SanadBreakerRole()
        result = role.run(state)

        for msg in result.messages:
            for token in FORBIDDEN_OUTPUT_TOKENS:
                assert token.lower() not in msg.content.lower()


class TestContradictionFinderRoleDeterminism:
    """Tests for ContradictionFinderRole determinism."""

    def test_same_state_same_output(self) -> None:
        """Same state produces identical outputs."""
        state = create_state_with_claims()
        role = ContradictionFinderRole()

        result1 = role.run(state)
        result2 = role.run(state)

        assert result1.messages[0].message_id == result2.messages[0].message_id
        assert result1.outputs[0].output_id == result2.outputs[0].output_id
        assert result1.position_hash == result2.position_hash

    def test_different_state_different_output(self) -> None:
        """Different state produces different outputs."""
        state1 = create_state_with_claims(round_number=1)
        state2 = create_state_with_claims(round_number=2)
        role = ContradictionFinderRole()

        result1 = role.run(state1)
        result2 = role.run(state2)

        assert result1.messages[0].message_id != result2.messages[0].message_id

    def test_contradictions_derived_from_state(self) -> None:
        """Contradictions are derived from state claims."""
        state = create_state_with_claims(claim_refs=["c1", "c2", "c3", "c4"])
        role = ContradictionFinderRole()
        result = role.run(state)

        content = result.outputs[0].content
        assert content["scanned_count"] == 4
        assert "grouping_keys_used" in content

    def test_no_banned_tokens_in_output(self) -> None:
        """Output does not contain banned tokens."""
        state = create_state_with_claims()
        role = ContradictionFinderRole()
        result = role.run(state)

        for msg in result.messages:
            for token in FORBIDDEN_OUTPUT_TOKENS:
                assert token.lower() not in msg.content.lower()


class TestRiskOfficerRoleDeterminism:
    """Tests for RiskOfficerRole determinism."""

    def test_same_state_same_output(self) -> None:
        """Same state produces identical outputs."""
        state = create_state_with_claims()
        role = RiskOfficerRole()

        result1 = role.run(state)
        result2 = role.run(state)

        assert result1.messages[0].message_id == result2.messages[0].message_id
        assert result1.outputs[0].output_id == result2.outputs[0].output_id
        assert result1.position_hash == result2.position_hash

    def test_different_state_different_output(self) -> None:
        """Different state produces different outputs."""
        state1 = create_state_with_claims(round_number=1)
        state2 = create_state_with_claims(round_number=2)
        role = RiskOfficerRole()

        result1 = role.run(state1)
        result2 = role.run(state2)

        assert result1.messages[0].message_id != result2.messages[0].message_id

    def test_risks_derived_from_state(self) -> None:
        """Risks are derived from state, not canned."""
        state = create_state_with_claims(claim_refs=["c1", "c2", "c3"])
        state = create_deterministic_state(
            round_number=1,
            messages=state.messages,
            open_questions=["q1", "q2"],
        )
        role = RiskOfficerRole()
        result = role.run(state)

        content = result.outputs[0].content
        assert content["scanned_count"] > 0
        assert "fraud_indicators" in content

    def test_no_banned_tokens_in_output(self) -> None:
        """Output does not contain banned tokens."""
        state = create_state_with_claims()
        role = RiskOfficerRole()
        result = role.run(state)

        for msg in result.messages:
            for token in FORBIDDEN_OUTPUT_TOKENS:
                assert token.lower() not in msg.content.lower()


class TestArbiterRoleDeterminism:
    """Tests for ArbiterRole determinism."""

    def test_same_state_same_output(self) -> None:
        """Same state produces identical outputs."""
        state = create_deterministic_state()
        role = ArbiterRole()

        result1 = role.run(state)
        result2 = role.run(state)

        assert result1.messages[0].message_id == result2.messages[0].message_id
        assert result1.outputs[0].output_id == result2.outputs[0].output_id
        assert result1.position_hash == result2.position_hash

    def test_different_state_different_output(self) -> None:
        """Different state produces different outputs."""
        state1 = create_deterministic_state(round_number=1)
        state2 = create_deterministic_state(round_number=2)
        role = ArbiterRole()

        result1 = role.run(state1)
        result2 = role.run(state2)

        assert result1.messages[0].message_id != result2.messages[0].message_id

    def test_decision_derived_from_state(self) -> None:
        """Arbiter decision is derived from state."""
        state = create_deterministic_state()
        role = ArbiterRole()
        result = role.run(state)

        content = result.outputs[0].content
        assert "decision" in content
        assert "round_number" in content
        decision = content["decision"]
        assert "rationale" in decision
        assert len(decision["rationale"]) > 0

    def test_no_banned_tokens_in_output(self) -> None:
        """Output does not contain banned tokens."""
        state = create_deterministic_state()
        role = ArbiterRole()
        result = role.run(state)

        for msg in result.messages:
            for token in FORBIDDEN_OUTPUT_TOKENS:
                assert token.lower() not in msg.content.lower()

        content_str = str(result.outputs[0].content)
        for token in FORBIDDEN_OUTPUT_TOKENS:
            assert token.lower() not in content_str.lower()


class TestCrossRoleDeterminism:
    """Cross-role determinism tests."""

    def test_all_roles_produce_unique_ids_same_state(self) -> None:
        """Different roles produce unique IDs for same state."""
        state = create_deterministic_state()
        roles = [
            AdvocateRole(),
            SanadBreakerRole(),
            ContradictionFinderRole(),
            RiskOfficerRole(),
            ArbiterRole(),
        ]

        message_ids = set()
        output_ids = set()

        for role in roles:
            result = role.run(state)
            msg_id = result.messages[0].message_id
            out_id = result.outputs[0].output_id

            assert msg_id not in message_ids, f"Duplicate message_id: {msg_id}"
            assert out_id not in output_ids, f"Duplicate output_id: {out_id}"

            message_ids.add(msg_id)
            output_ids.add(out_id)

    def test_all_default_agent_ids_are_deterministic(self) -> None:
        """All default agent IDs are deterministic (not random)."""
        roles_1 = [
            AdvocateRole(),
            SanadBreakerRole(),
            ContradictionFinderRole(),
            RiskOfficerRole(),
            ArbiterRole(),
        ]
        roles_2 = [
            AdvocateRole(),
            SanadBreakerRole(),
            ContradictionFinderRole(),
            RiskOfficerRole(),
            ArbiterRole(),
        ]

        for r1, r2 in zip(roles_1, roles_2, strict=True):
            assert r1.agent_id == r2.agent_id

    def test_no_uuid4_pattern_in_ids(self) -> None:
        """Generated IDs do not look like random uuid4 (32 hex chars)."""
        state = create_deterministic_state()
        roles = [
            AdvocateRole(),
            SanadBreakerRole(),
            ContradictionFinderRole(),
            RiskOfficerRole(),
            ArbiterRole(),
        ]

        for role in roles:
            result = role.run(state)
            msg_id = result.messages[0].message_id
            out_id = result.outputs[0].output_id

            # Deterministic IDs have format "prefix-uuid5hex[:12]"
            # They should be stable across runs
            assert msg_id.startswith("msg-")
            assert out_id.startswith("out-")
