"""Tests for CRITICAL EVIDENCE LINK RULE presence in all 8 analysis agent prompts.

Verifies that each prompt contains the evidence link rule text that instructs
the LLM to include at least one evidence link on every Risk object.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_ANALYSIS_AGENTS = [
    "financial_agent",
    "market_agent",
    "technical_agent",
    "terms_agent",
    "team_agent",
    "risk_officer_agent",
    "historian_agent",
    "sector_specialist_agent",
]

_REQUIRED_TOKENS = [
    "CRITICAL EVIDENCE LINK RULE",
    "claim_ids",
    "REJECTED by the validator",
    "questions_for_founder",
]


@pytest.mark.parametrize("agent_name", _ANALYSIS_AGENTS)
class TestPromptEvidenceLinkRule:
    """Verify each analysis agent prompt contains the evidence link rule."""

    def _load_prompt(self, agent_name: str) -> str:
        """Load prompt text for a given agent."""
        path = _PROJECT_ROOT / "prompts" / agent_name / "1.0.0" / "prompt.md"
        assert path.exists(), f"Prompt file missing: {path}"
        return path.read_text(encoding="utf-8")

    def test_prompt_contains_evidence_link_rule(self, agent_name: str) -> None:
        """Prompt must contain the CRITICAL EVIDENCE LINK RULE block."""
        prompt = self._load_prompt(agent_name)
        assert "CRITICAL EVIDENCE LINK RULE" in prompt

    def test_prompt_contains_required_tokens(self, agent_name: str) -> None:
        """Prompt must contain all required evidence rule tokens."""
        prompt = self._load_prompt(agent_name)
        for token in _REQUIRED_TOKENS:
            assert token in prompt, f"Prompt for {agent_name} missing required token: {token!r}"

    def test_prompt_evidence_rule_mentions_risk(self, agent_name: str) -> None:
        """Evidence link rule must mention Risk objects."""
        prompt = self._load_prompt(agent_name)
        rule_start = prompt.index("CRITICAL EVIDENCE LINK RULE")
        rule_section = prompt[rule_start : rule_start + 500]
        assert "Risk" in rule_section
