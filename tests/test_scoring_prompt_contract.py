"""Scoring prompt contract guard tests — Phase 9.

Asserts that the scoring agent prompt contains all required literal tokens,
a JSON marker, dimension names, and Muhāsibī metacognitive discipline keywords.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_ROOT = Path(__file__).resolve().parents[1] / "prompts"
_SCORING_PROMPT_PATH = _PROMPTS_ROOT / "scoring_agent" / "1.0.0" / "prompt.md"

_REQUIRED_TOKENS = [
    "supported_claim_ids",
    "supported_calc_ids",
    "confidence",
    "confidence_justification",
    "muhasabah",
]

_REQUIRED_DIMENSIONS = [
    "MARKET_ATTRACTIVENESS",
    "TEAM_QUALITY",
    "PRODUCT_DEFENSIBILITY",
    "TRACTION_VELOCITY",
    "FUND_THESIS_FIT",
    "CAPITAL_EFFICIENCY",
    "SCALABILITY",
    "RISK_PROFILE",
]

_MUHASIBI_KEYWORDS = [
    "nafs_check",
    "Muj\u0101hada",
    "insight_type",
    "conventional",
    "deal_specific",
    "contradictory",
]


def _read_prompt() -> str:
    """Read scoring prompt file content, failing clearly if missing.

    Returns:
        Prompt text content.
    """
    if not _SCORING_PROMPT_PATH.exists():
        pytest.fail(f"Scoring prompt file not found: {_SCORING_PROMPT_PATH}")
    return _SCORING_PROMPT_PATH.read_text(encoding="utf-8")


class TestScoringPromptContract:
    """Scoring agent prompt must contain all required tokens and dimensions."""

    def test_prompt_file_exists(self) -> None:
        assert _SCORING_PROMPT_PATH.exists(), (
            f"Scoring agent prompt missing: {_SCORING_PROMPT_PATH}"
        )

    @pytest.mark.parametrize("token", _REQUIRED_TOKENS)
    def test_prompt_contains_required_token(self, token: str) -> None:
        content = _read_prompt()
        assert token in content, f"Scoring agent prompt missing required token: '{token}'"

    @pytest.mark.parametrize("dimension", _REQUIRED_DIMENSIONS)
    def test_prompt_contains_dimension(self, dimension: str) -> None:
        content = _read_prompt()
        assert dimension in content, f"Scoring agent prompt missing dimension: '{dimension}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt()
        assert "```json" in content, "Scoring agent prompt missing JSON code block marker (```json)"

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt()
        assert "provider_id" in content, (
            "Scoring agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt()
        assert "source_id" in content, (
            "Scoring agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt()
        assert keyword in content, (
            f"Scoring agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )
