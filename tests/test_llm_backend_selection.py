"""Tests for LLM backend selection — env-driven client/runner wiring.

Verifies:
- Default env → deterministic clients (no network)
- IDIS_EXTRACT_BACKEND=anthropic + missing key → fail-closed ValueError
- IDIS_DEBATE_BACKEND=anthropic + missing key → fail-closed ValueError
- Explicit deterministic selection works
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestExtractionBackendSelection:
    """Tests for _build_extraction_llm_client env-driven selection."""

    def test_default_env_returns_deterministic(self) -> None:
        """Unset IDIS_EXTRACT_BACKEND defaults to DeterministicLLMClient."""
        from idis.api.routes.runs import _build_extraction_llm_client
        from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

        env = {k: v for k, v in os.environ.items() if k != "IDIS_EXTRACT_BACKEND"}
        with patch.dict(os.environ, env, clear=True):
            client = _build_extraction_llm_client()

        assert isinstance(client, DeterministicLLMClient)

    def test_explicit_deterministic_returns_deterministic(self) -> None:
        """IDIS_EXTRACT_BACKEND=deterministic returns DeterministicLLMClient."""
        from idis.api.routes.runs import _build_extraction_llm_client
        from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

        with patch.dict(os.environ, {"IDIS_EXTRACT_BACKEND": "deterministic"}, clear=False):
            client = _build_extraction_llm_client()

        assert isinstance(client, DeterministicLLMClient)

    def test_anthropic_backend_missing_key_fails_closed(self) -> None:
        """IDIS_EXTRACT_BACKEND=anthropic without ANTHROPIC_API_KEY raises ValueError."""
        from idis.api.routes.runs import _build_extraction_llm_client

        env = {
            "IDIS_EXTRACT_BACKEND": "anthropic",
        }
        env_clean = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env_clean.update(env)
        with (
            patch.dict(os.environ, env_clean, clear=True),
            pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
        ):
            _build_extraction_llm_client()

    def test_anthropic_backend_with_key_returns_anthropic_client(self) -> None:
        """IDIS_EXTRACT_BACKEND=anthropic with key returns AnthropicLLMClient."""
        from idis.api.routes.runs import _build_extraction_llm_client
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        env = {
            "IDIS_EXTRACT_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant-test-fake-key-for-unit-test",
        }
        with patch.dict(os.environ, env, clear=False):
            client = _build_extraction_llm_client()

        assert isinstance(client, AnthropicLLMClient)


class TestDebateBackendSelection:
    """Tests for _build_debate_role_runners env-driven selection."""

    def test_default_env_returns_deterministic_runners(self) -> None:
        """Unset IDIS_DEBATE_BACKEND defaults to deterministic RoleRunners."""
        from idis.api.routes.runs import _build_debate_role_runners
        from idis.debate.orchestrator import RoleRunners
        from idis.debate.roles.advocate import AdvocateRole

        env = {k: v for k, v in os.environ.items() if k != "IDIS_DEBATE_BACKEND"}
        with patch.dict(os.environ, env, clear=True):
            runners = _build_debate_role_runners()

        assert isinstance(runners, RoleRunners)
        assert isinstance(runners.advocate, AdvocateRole)

    def test_explicit_deterministic_returns_deterministic_runners(self) -> None:
        """IDIS_DEBATE_BACKEND=deterministic returns deterministic RoleRunners."""
        from idis.api.routes.runs import _build_debate_role_runners
        from idis.debate.orchestrator import RoleRunners
        from idis.debate.roles.advocate import AdvocateRole

        with patch.dict(os.environ, {"IDIS_DEBATE_BACKEND": "deterministic"}, clear=False):
            runners = _build_debate_role_runners()

        assert isinstance(runners, RoleRunners)
        assert isinstance(runners.advocate, AdvocateRole)

    def test_anthropic_backend_missing_key_fails_closed(self) -> None:
        """IDIS_DEBATE_BACKEND=anthropic without ANTHROPIC_API_KEY raises ValueError."""
        from idis.api.routes.runs import _build_debate_role_runners

        env = {
            "IDIS_DEBATE_BACKEND": "anthropic",
        }
        env_clean = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env_clean.update(env)
        with (
            patch.dict(os.environ, env_clean, clear=True),
            pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
        ):
            _build_debate_role_runners()

    def test_anthropic_backend_with_key_returns_llm_runners(self) -> None:
        """IDIS_DEBATE_BACKEND=anthropic with key returns LLMRoleRunner instances."""
        from idis.api.routes.runs import _build_debate_role_runners
        from idis.debate.orchestrator import RoleRunners
        from idis.debate.roles.llm_role_runner import LLMRoleRunner

        env = {
            "IDIS_DEBATE_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant-test-fake-key-for-unit-test",
        }
        with patch.dict(os.environ, env, clear=False):
            runners = _build_debate_role_runners()

        assert isinstance(runners, RoleRunners)
        assert isinstance(runners.advocate, LLMRoleRunner)
        assert isinstance(runners.sanad_breaker, LLMRoleRunner)
        assert isinstance(runners.contradiction_finder, LLMRoleRunner)
        assert isinstance(runners.risk_officer, LLMRoleRunner)
        assert isinstance(runners.arbiter, LLMRoleRunner)
