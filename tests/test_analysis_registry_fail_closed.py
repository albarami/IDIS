"""Tests for analysis agent registry fail-closed behavior."""

from __future__ import annotations

import pytest

from idis.analysis.models import AgentReport, AnalysisContext
from idis.analysis.registry import AgentNotRegisteredError, AnalysisAgentRegistry


class _StubAgent:
    """Minimal stub agent for registry tests."""

    def __init__(self, agent_id: str, agent_type: str = "stub_agent") -> None:
        self._agent_id = agent_id
        self._agent_type = agent_type

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def agent_type(self) -> str:
        return self._agent_type

    def run(self, ctx: AnalysisContext) -> AgentReport:
        raise NotImplementedError("Stub agent")


class TestRegistryFailClosed:
    """Registry must raise on unknown agent (fail-closed)."""

    def test_unknown_agent_raises(self) -> None:
        registry = AnalysisAgentRegistry()
        with pytest.raises(AgentNotRegisteredError, match="not-registered"):
            registry.get("not-registered")

    def test_unknown_agent_error_carries_agent_id(self) -> None:
        registry = AnalysisAgentRegistry()
        with pytest.raises(AgentNotRegisteredError) as exc_info:
            registry.get("ghost-agent")
        assert exc_info.value.agent_id == "ghost-agent"

    def test_empty_registry_raises(self) -> None:
        registry = AnalysisAgentRegistry()
        with pytest.raises(AgentNotRegisteredError):
            registry.get("any-id")


class TestRegistryHappyPath:
    """Registry register/get/list operations."""

    def test_register_and_get(self) -> None:
        registry = AnalysisAgentRegistry()
        agent = _StubAgent("agent-1")
        registry.register(agent)
        assert registry.get("agent-1") is agent

    def test_duplicate_registration_raises(self) -> None:
        registry = AnalysisAgentRegistry()
        agent = _StubAgent("agent-1")
        registry.register(agent)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(agent)

    def test_len(self) -> None:
        registry = AnalysisAgentRegistry()
        assert len(registry) == 0
        registry.register(_StubAgent("a1"))
        assert len(registry) == 1
        registry.register(_StubAgent("a2"))
        assert len(registry) == 2


class TestRegistryDeterministicOrdering:
    """Agents must be returned sorted by (agent_type, agent_id)."""

    def test_deterministic_ordering(self) -> None:
        registry = AnalysisAgentRegistry()
        registry.register(_StubAgent("z-agent", "beta_type"))
        registry.register(_StubAgent("a-agent", "beta_type"))
        registry.register(_StubAgent("m-agent", "alpha_type"))

        agents = registry.list_agents()
        keys = [(a.agent_type, a.agent_id) for a in agents]
        assert keys == [
            ("alpha_type", "m-agent"),
            ("beta_type", "a-agent"),
            ("beta_type", "z-agent"),
        ]

    def test_ordering_stable_across_calls(self) -> None:
        registry = AnalysisAgentRegistry()
        for i in range(5):
            registry.register(_StubAgent(f"agent-{i}", f"type-{4 - i}"))

        first = [(a.agent_type, a.agent_id) for a in registry.list_agents()]
        second = [(a.agent_type, a.agent_id) for a in registry.list_agents()]
        assert first == second
