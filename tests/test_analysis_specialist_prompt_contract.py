"""Prompt contract guard tests for specialist analysis agents — Phase 8.B / 8.C-1 / 8.C-2.

Asserts that all prompt files contain all required AgentReport schema keys,
a JSON marker, and Muhāsibī metacognitive discipline keywords.
If a prompt file is missing, the test fails with a clear message.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_ROOT = Path(__file__).resolve().parents[1] / "prompts"

_FINANCIAL_PROMPT_PATH = _PROMPTS_ROOT / "financial_agent" / "1.0.0" / "prompt.md"
_MARKET_PROMPT_PATH = _PROMPTS_ROOT / "market_agent" / "1.0.0" / "prompt.md"
_TECHNICAL_PROMPT_PATH = _PROMPTS_ROOT / "technical_agent" / "1.0.0" / "prompt.md"
_TERMS_PROMPT_PATH = _PROMPTS_ROOT / "terms_agent" / "1.0.0" / "prompt.md"
_TEAM_PROMPT_PATH = _PROMPTS_ROOT / "team_agent" / "1.0.0" / "prompt.md"
_RISK_OFFICER_PROMPT_PATH = _PROMPTS_ROOT / "risk_officer_agent" / "1.0.0" / "prompt.md"
_HISTORIAN_PROMPT_PATH = _PROMPTS_ROOT / "historian_agent" / "1.0.0" / "prompt.md"
_SECTOR_SPECIALIST_PROMPT_PATH = _PROMPTS_ROOT / "sector_specialist_agent" / "1.0.0" / "prompt.md"

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


class TestTechnicalPromptContract:
    """Technical agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _TECHNICAL_PROMPT_PATH.exists(), (
            f"Technical agent prompt missing: {_TECHNICAL_PROMPT_PATH}"
        )

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_TECHNICAL_PROMPT_PATH)
        assert key in content, f"Technical agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_TECHNICAL_PROMPT_PATH)
        assert "```json" in content, (
            "Technical agent prompt missing JSON code block marker (```json)"
        )

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_TECHNICAL_PROMPT_PATH)
        assert "provider_id" in content, (
            "Technical agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_TECHNICAL_PROMPT_PATH)
        assert "source_id" in content, (
            "Technical agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_TECHNICAL_PROMPT_PATH)
        assert keyword in content, (
            f"Technical agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )


class TestTermsPromptContract:
    """Terms agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _TERMS_PROMPT_PATH.exists(), f"Terms agent prompt missing: {_TERMS_PROMPT_PATH}"

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_TERMS_PROMPT_PATH)
        assert key in content, f"Terms agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_TERMS_PROMPT_PATH)
        assert "```json" in content, "Terms agent prompt missing JSON code block marker (```json)"

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_TERMS_PROMPT_PATH)
        assert "provider_id" in content, (
            "Terms agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_TERMS_PROMPT_PATH)
        assert "source_id" in content, (
            "Terms agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_TERMS_PROMPT_PATH)
        assert keyword in content, (
            f"Terms agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )


class TestTeamPromptContract:
    """Team agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _TEAM_PROMPT_PATH.exists(), f"Team agent prompt missing: {_TEAM_PROMPT_PATH}"

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_TEAM_PROMPT_PATH)
        assert key in content, f"Team agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_TEAM_PROMPT_PATH)
        assert "```json" in content, "Team agent prompt missing JSON code block marker (```json)"

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_TEAM_PROMPT_PATH)
        assert "provider_id" in content, (
            "Team agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_TEAM_PROMPT_PATH)
        assert "source_id" in content, (
            "Team agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_TEAM_PROMPT_PATH)
        assert keyword in content, (
            f"Team agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )


class TestRiskOfficerPromptContract:
    """Risk Officer agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _RISK_OFFICER_PROMPT_PATH.exists(), (
            f"Risk Officer agent prompt missing: {_RISK_OFFICER_PROMPT_PATH}"
        )

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_RISK_OFFICER_PROMPT_PATH)
        assert key in content, f"Risk Officer agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_RISK_OFFICER_PROMPT_PATH)
        assert "```json" in content, (
            "Risk Officer agent prompt missing JSON code block marker (```json)"
        )

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_RISK_OFFICER_PROMPT_PATH)
        assert "provider_id" in content, (
            "Risk Officer agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_RISK_OFFICER_PROMPT_PATH)
        assert "source_id" in content, (
            "Risk Officer agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_RISK_OFFICER_PROMPT_PATH)
        assert keyword in content, (
            f"Risk Officer agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )


class TestHistorianPromptContract:
    """Historian agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _HISTORIAN_PROMPT_PATH.exists(), (
            f"Historian agent prompt missing: {_HISTORIAN_PROMPT_PATH}"
        )

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_HISTORIAN_PROMPT_PATH)
        assert key in content, f"Historian agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_HISTORIAN_PROMPT_PATH)
        assert "```json" in content, (
            "Historian agent prompt missing JSON code block marker (```json)"
        )

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_HISTORIAN_PROMPT_PATH)
        assert "provider_id" in content, (
            "Historian agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_HISTORIAN_PROMPT_PATH)
        assert "source_id" in content, (
            "Historian agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_HISTORIAN_PROMPT_PATH)
        assert keyword in content, (
            f"Historian agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )


class TestSectorSpecialistPromptContract:
    """Sector Specialist agent prompt must contain all required schema keys."""

    def test_prompt_file_exists(self) -> None:
        assert _SECTOR_SPECIALIST_PROMPT_PATH.exists(), (
            f"Sector Specialist agent prompt missing: {_SECTOR_SPECIALIST_PROMPT_PATH}"
        )

    @pytest.mark.parametrize("key", _REQUIRED_KEYS)
    def test_prompt_contains_required_key(self, key: str) -> None:
        content = _read_prompt(_SECTOR_SPECIALIST_PROMPT_PATH)
        assert key in content, f"Sector Specialist agent prompt missing required key: '{key}'"

    def test_prompt_contains_json_marker(self) -> None:
        content = _read_prompt(_SECTOR_SPECIALIST_PROMPT_PATH)
        assert "```json" in content, (
            "Sector Specialist agent prompt missing JSON code block marker (```json)"
        )

    def test_prompt_mentions_provider_id(self) -> None:
        content = _read_prompt(_SECTOR_SPECIALIST_PROMPT_PATH)
        assert "provider_id" in content, (
            "Sector Specialist agent prompt missing enrichment provenance key: provider_id"
        )

    def test_prompt_mentions_source_id(self) -> None:
        content = _read_prompt(_SECTOR_SPECIALIST_PROMPT_PATH)
        assert "source_id" in content, (
            "Sector Specialist agent prompt missing enrichment provenance key: source_id"
        )

    @pytest.mark.parametrize("keyword", _MUHASIBI_KEYWORDS)
    def test_prompt_contains_muhasibi_keyword(self, keyword: str) -> None:
        content = _read_prompt(_SECTOR_SPECIALIST_PROMPT_PATH)
        assert keyword in content, (
            f"Sector Specialist agent prompt missing Muh\u0101sib\u012b keyword: '{keyword}'"
        )
