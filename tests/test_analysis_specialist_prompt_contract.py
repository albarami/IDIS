"""Prompt contract guard tests for specialist analysis agents — Phase 8.B.

Asserts that both prompt files contain all required AgentReport schema keys,
a JSON marker, and Muḥāsibī metacognitive discipline keywords.
If a prompt file is missing, the test fails with a clear message.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_ROOT = Path(__file__).resolve().parents[1] / "prompts"

_FINANCIAL_PROMPT_PATH = _PROMPTS_ROOT / "financial_agent" / "1.0.0" / "prompt.md"
_MARKET_PROMPT_PATH = _PROMPTS_ROOT / "market_agent" / "1.0.0" / "prompt.md"

_REQUIRED_KEYS = [
    "supported_claim_ids",
    "supported_calc_ids",
    "analysis_sections",
    "risks",
    "questions_for_founder",
    "confidence",
    "confidence_justification",
    "muhasabah",
]

_MUHASIBI_KEYWORDS = [
    "nafs_check",
    "Muj\u0101hada",
    "insight_type",
    "conventional",
    "deal_specific",
    "contradictory",
]


def _read_prompt(path: Path) -> str:
    """Read prompt file content, failing clearly if missing.

    Args:
        path: Path to the prompt file.

    Returns:
        Prompt text content.
    """
    if not path.exists():
        pytest.fail(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


class TestFinancialPromptContract:
    """Financial agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _FINANCIAL_PROMPT_PATH.exists(), (
            f"Financial agent prompt missing: {_FINANCIAL_PROMPT_PATH}"
        )

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_FINANCIAL_PROMPT_PATH)
        assert key in content, f"Financial agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_FINANCIAL_PROMPT_PATH)
        assert "```json" in content, (
            "Financial agent prompt missing JSON code block marker (```json)"
        )

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_FINANCIAL_PROMPT_PATH)
        assert "provider_id" in content, (
            "Financial agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_FINANCIAL_PROMPT_PATH)
        assert "source_id" in content, (
            "Financial agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_FINANCIAL_PROMPT_PATH)
        assert keyword in content, (
            f"Financial agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )


class TestMarketPromptContract:
    """Market agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _MARKET_PROMPT_PATH.exists(), f"Market agent prompt missing: {_MARKET_PROMPT_PATH}"

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_MARKET_PROMPT_PATH)
        assert key in content, f"Market agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_MARKET_PROMPT_PATH)
        assert "```json" in content, "Market agent prompt missing JSON code block marker (```json)"

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_MARKET_PROMPT_PATH)
        assert "provider_id" in content, (
            "Market agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_MARKET_PROMPT_PATH)
        assert "source_id" in content, (
            "Market agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_MARKET_PROMPT_PATH)
        assert keyword in content, (
            f"Market agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )
