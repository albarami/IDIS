"""Slice95 Task 2 — debate transcript safe-shape contract + hardening (G5 / DEC-C).

The debate rounds passthrough was an untyped ``list[dict[str, Any]]`` (a latent leak risk).
This pins the typed safe-shape round summary and the hardening that raw agent-reasoning keys
(content / message / text / prompt / raw...) can never serialize through GET /v1/debate/{id}.

No real debate orchestrator wiring this slice (DEC-C). Injected fakes only — no LLM, no DB.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from idis.api.routes.debate import DebateRoundSummary, DebateSession

_SESSION = {
    "debate_id": "11111111-1111-1111-1111-111111111111",
    "deal_id": "22222222-2222-2222-2222-222222222222",
    "protocol_version": "v1",
    "created_at": "2026-01-01T00:00:00Z",
}


def test_round_summary_accepts_safe_fields_only() -> None:
    summary = DebateRoundSummary(
        round_number=1, role="arbiter", claim_refs=["claim-a"], calc_refs=["calc-a"]
    )
    assert set(summary.model_dump()) == {"round_number", "role", "claim_refs", "calc_refs"}


@pytest.mark.parametrize("private_key", ["content", "message", "text", "prompt", "raw_output"])
def test_round_summary_rejects_raw_content_keys(private_key: str) -> None:
    # extra='forbid' — a round carrying raw agent reasoning is rejected, never serialized.
    with pytest.raises(ValidationError):
        DebateRoundSummary(round_number=1, role="advocate", **{private_key: "raw agent reasoning"})


def test_debate_session_serializes_safe_shape_only() -> None:
    session = DebateSession(
        rounds=[DebateRoundSummary(round_number=1, role="arbiter", claim_refs=["claim-a"])],
        **_SESSION,
    )
    encoded = json.dumps(session.model_dump(mode="json"))
    for private in ("content", "message", "transcript", "prompt", "raw"):
        assert private not in encoded


def test_debate_session_empty_rounds_still_valid() -> None:
    # The stub path (rounds=[]) stays valid — no orchestrator wiring this slice.
    session = DebateSession(rounds=[], **_SESSION)
    assert session.rounds == []
