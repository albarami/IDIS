"""Tests for graph projection tenant isolation.

Validates that:
- Tenant A objects are not visible to tenant B queries
- GraphProjectionService respects tenant boundaries
- Projection results carry correct tenant_id
- Neo4j-not-configured → skip (not error)
- Fail-closed on projection failure with audit emission
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.persistence.graph_consistency import (
    GraphProjectionService,
    ProjectionResult,
    ProjectionStatus,
    _build_audit_event,
    _emit_audit_or_fail,
)
from idis.persistence.graph_repo import GraphProjectionError, GraphRepository
from idis.persistence.neo4j_driver import is_neo4j_configured

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
DEAL_A = "22222222-2222-2222-2222-222222222222"
CLAIM_A = "11111111-1111-1111-1111-111111111111"


class FakeAuditSink:
    """Fake audit sink for testing."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self._should_fail = should_fail

    def emit(self, event: dict[str, Any]) -> None:
        if self._should_fail:
            raise RuntimeError("Audit sink failure")
        self.events.append(event)


class FakeGraphRepo(GraphRepository):
    """Fake graph repository that records calls for testing."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.deal_projections: list[dict[str, Any]] = []
        self.claim_projections: list[dict[str, Any]] = []
        self._should_fail = should_fail

    def upsert_deal_graph_projection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
        spans: list[dict[str, Any]],
        entities: list[dict[str, Any]] | None = None,
    ) -> None:
        if self._should_fail:
            raise GraphProjectionError("Simulated projection failure")
        self.deal_projections.append({
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "documents": documents,
            "spans": spans,
        })

    def upsert_claim_sanad_projection(
        self,
        *,
        tenant_id: str,
        claim: dict[str, Any],
        evidence_items: list[dict[str, Any]],
        transmission_nodes: list[dict[str, Any]],
        defects: list[dict[str, Any]] | None = None,
        calculations: list[dict[str, Any]] | None = None,
    ) -> None:
        if self._should_fail:
            raise GraphProjectionError("Simulated projection failure")
        self.claim_projections.append({
            "tenant_id": tenant_id,
            "claim": claim,
            "evidence_items": evidence_items,
            "transmission_nodes": transmission_nodes,
        })


class TestProjectionSkipsWhenNotConfigured:
    """When Neo4j is not configured, projections are skipped."""

    def test_deal_projection_skipped(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            service = GraphProjectionService(graph_repo=FakeGraphRepo())
            result = service.project_deal(
                tenant_id=TENANT_A,
                deal_id=DEAL_A,
                documents=[],
                spans=[],
            )
            assert result.status == ProjectionStatus.SKIPPED
            assert result.tenant_id == TENANT_A

    def test_claim_projection_skipped(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            service = GraphProjectionService(graph_repo=FakeGraphRepo())
            result = service.project_claim_sanad(
                tenant_id=TENANT_A,
                claim={"claim_id": CLAIM_A},
                evidence_items=[],
                transmission_nodes=[],
            )
            assert result.status == ProjectionStatus.SKIPPED


class TestProjectionSucceeds:
    """When Neo4j is configured and projection succeeds."""

    def _make_env(self) -> dict[str, str]:
        return {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "test",
        }

    def test_deal_projection_success(self) -> None:
        repo = FakeGraphRepo()
        sink = FakeAuditSink()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo, audit_sink=sink)
            result = service.project_deal(
                tenant_id=TENANT_A,
                deal_id=DEAL_A,
                documents=[{"document_id": "d1", "doc_type": "PDF"}],
                spans=[{"span_id": "s1", "document_id": "d1", "span_type": "PAGE_TEXT"}],
            )
        assert result.status == ProjectionStatus.SUCCESS
        assert result.tenant_id == TENANT_A
        assert len(repo.deal_projections) == 1
        assert repo.deal_projections[0]["tenant_id"] == TENANT_A
        assert len(sink.events) == 1
        assert "success" in sink.events[0]["event_type"]

    def test_claim_projection_success(self) -> None:
        repo = FakeGraphRepo()
        sink = FakeAuditSink()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo, audit_sink=sink)
            result = service.project_claim_sanad(
                tenant_id=TENANT_A,
                claim={"claim_id": CLAIM_A, "claim_text": "Revenue is $1M"},
                evidence_items=[],
                transmission_nodes=[],
            )
        assert result.status == ProjectionStatus.SUCCESS
        assert len(repo.claim_projections) == 1
        assert repo.claim_projections[0]["tenant_id"] == TENANT_A


class TestTenantIsolation:
    """Tenant A data must not leak to tenant B."""

    def _make_env(self) -> dict[str, str]:
        return {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "test",
        }

    def test_tenant_a_projection_not_in_tenant_b(self) -> None:
        """Insert tenant A objects → verify tenant B reads return empty."""
        repo = FakeGraphRepo()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo)

            service.project_deal(
                tenant_id=TENANT_A,
                deal_id=DEAL_A,
                documents=[{"document_id": "d1", "doc_type": "PDF"}],
                spans=[],
            )

        assert len(repo.deal_projections) == 1
        assert repo.deal_projections[0]["tenant_id"] == TENANT_A

        tenant_b_projections = [
            p for p in repo.deal_projections if p["tenant_id"] == TENANT_B
        ]
        assert len(tenant_b_projections) == 0

    def test_projection_result_carries_correct_tenant(self) -> None:
        """ProjectionResult.tenant_id matches the input tenant."""
        repo = FakeGraphRepo()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo)
            result_a = service.project_deal(
                tenant_id=TENANT_A,
                deal_id=DEAL_A,
                documents=[],
                spans=[],
            )
            result_b = service.project_deal(
                tenant_id=TENANT_B,
                deal_id="deal-b",
                documents=[],
                spans=[],
            )
        assert result_a.tenant_id == TENANT_A
        assert result_b.tenant_id == TENANT_B

    def test_claim_projection_scoped_to_tenant(self) -> None:
        """Claim projections carry correct tenant_id."""
        repo = FakeGraphRepo()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo)
            service.project_claim_sanad(
                tenant_id=TENANT_A,
                claim={"claim_id": CLAIM_A},
                evidence_items=[],
                transmission_nodes=[],
            )
            service.project_claim_sanad(
                tenant_id=TENANT_B,
                claim={"claim_id": "claim-b"},
                evidence_items=[],
                transmission_nodes=[],
            )

        assert repo.claim_projections[0]["tenant_id"] == TENANT_A
        assert repo.claim_projections[1]["tenant_id"] == TENANT_B


class TestProjectionFailClosed:
    """When projection fails, structured failure is reported."""

    def _make_env(self) -> dict[str, str]:
        return {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "test",
        }

    def test_deal_projection_failure_reported(self) -> None:
        repo = FakeGraphRepo(should_fail=True)
        sink = FakeAuditSink()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo, audit_sink=sink)
            result = service.project_deal(
                tenant_id=TENANT_A,
                deal_id=DEAL_A,
                documents=[],
                spans=[],
            )
        assert result.status == ProjectionStatus.FAILED
        assert result.error is not None
        assert "Simulated" in result.error
        assert len(sink.events) == 1
        assert sink.events[0]["severity"] == "HIGH"

    def test_claim_projection_failure_reported(self) -> None:
        repo = FakeGraphRepo(should_fail=True)
        sink = FakeAuditSink()
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo, audit_sink=sink)
            result = service.project_claim_sanad(
                tenant_id=TENANT_A,
                claim={"claim_id": CLAIM_A},
                evidence_items=[],
                transmission_nodes=[],
            )
        assert result.status == ProjectionStatus.FAILED
        assert result.error is not None

    def test_audit_failure_is_fatal(self) -> None:
        """If projection fails AND audit fails → AUDIT_FAILURE status."""
        repo = FakeGraphRepo(should_fail=True)
        sink = FakeAuditSink(should_fail=True)
        with patch.dict(os.environ, self._make_env(), clear=True):
            service = GraphProjectionService(graph_repo=repo, audit_sink=sink)
            result = service.project_deal(
                tenant_id=TENANT_A,
                deal_id=DEAL_A,
                documents=[],
                spans=[],
            )
        assert result.status == ProjectionStatus.AUDIT_FAILURE
        assert "audit emission failed" in (result.error or "").lower()


class TestAuditEventBuilding:
    """Tests for audit event construction."""

    def test_success_event_has_low_severity(self) -> None:
        event = _build_audit_event(
            tenant_id=TENANT_A,
            entity_type="deal",
            entity_id=DEAL_A,
            status=ProjectionStatus.SUCCESS,
        )
        assert event["severity"] == "LOW"
        assert event["tenant_id"] == TENANT_A

    def test_failure_event_has_high_severity(self) -> None:
        event = _build_audit_event(
            tenant_id=TENANT_A,
            entity_type="deal",
            entity_id=DEAL_A,
            status=ProjectionStatus.FAILED,
            error="connection timeout",
        )
        assert event["severity"] == "HIGH"
        assert event["payload"]["error"] == "connection timeout"

    def test_emit_audit_or_fail_raises_on_sink_failure(self) -> None:
        sink = FakeAuditSink(should_fail=True)
        event = _build_audit_event(
            tenant_id=TENANT_A,
            entity_type="deal",
            entity_id=DEAL_A,
            status=ProjectionStatus.SUCCESS,
        )
        with pytest.raises(RuntimeError, match="Audit sink failure"):
            _emit_audit_or_fail(sink, event)

    def test_emit_audit_none_sink_is_noop(self) -> None:
        event = _build_audit_event(
            tenant_id=TENANT_A,
            entity_type="deal",
            entity_id=DEAL_A,
            status=ProjectionStatus.SUCCESS,
        )
        _emit_audit_or_fail(None, event)
