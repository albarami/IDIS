"""Tests for Neo4j driver fail-closed behavior.

Validates that:
- Partial config (URI set, missing password) fails closed
- Missing config returns not-configured
- tenant_id enforcement on execute_read/execute_write
- Schema constants match Data Model §4.1/§4.2
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from idis.persistence.neo4j_driver import (
    VALID_EDGE_TYPES,
    VALID_NODE_LABELS,
    EdgeType,
    Neo4jConfigError,
    NodeLabel,
    _validate_config,
    execute_read,
    execute_write,
    is_neo4j_configured,
    reset_driver_for_testing,
)


class TestIsNeo4jConfigured:
    """Tests for is_neo4j_configured()."""

    def test_not_configured_when_uri_missing(self) -> None:
        """NEO4J_URI not set → not configured."""
        with patch.dict(os.environ, {}, clear=True):
            assert is_neo4j_configured() is False

    def test_configured_when_uri_set(self) -> None:
        """NEO4J_URI set → configured (regardless of creds)."""
        with patch.dict(os.environ, {"NEO4J_URI": "bolt://localhost:7687"}, clear=True):
            assert is_neo4j_configured() is True


class TestValidateConfig:
    """Tests for _validate_config() fail-closed behavior."""

    def test_missing_uri_raises(self) -> None:
        """No URI → Neo4jConfigError."""
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(Neo4jConfigError, match="NEO4J_URI is not set"),
        ):
            _validate_config()

    def test_uri_set_missing_username_raises(self) -> None:
        """URI set, username missing → fail-closed."""
        env = {"NEO4J_URI": "bolt://localhost:7687", "NEO4J_PASSWORD": "secret"}
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(Neo4jConfigError, match="NEO4J_USERNAME"),
        ):
            _validate_config()

    def test_uri_set_missing_password_raises(self) -> None:
        """URI set, password missing → fail-closed."""
        env = {"NEO4J_URI": "bolt://localhost:7687", "NEO4J_USERNAME": "neo4j"}
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(Neo4jConfigError, match="NEO4J_PASSWORD"),
        ):
            _validate_config()

    def test_uri_set_missing_both_creds_raises(self) -> None:
        """URI set, both creds missing → fail-closed with both listed."""
        env = {"NEO4J_URI": "bolt://localhost:7687"}
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(Neo4jConfigError, match="incomplete"),
        ):
            _validate_config()

    def test_complete_config_returns_tuple(self) -> None:
        """All three vars set → returns (uri, username, password)."""
        env = {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "testpass",
        }
        with patch.dict(os.environ, env, clear=True):
            uri, username, password = _validate_config()
            assert uri == "bolt://localhost:7687"
            assert username == "neo4j"
            assert password == "testpass"


class TestTenantIdEnforcement:
    """Tests for tenant_id enforcement on query execution."""

    def test_execute_read_requires_tenant_id(self) -> None:
        """execute_read without tenant_id → ValueError."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            execute_read("MATCH (n) RETURN n", {"claim_id": "c1"})

    def test_execute_write_requires_tenant_id(self) -> None:
        """execute_write without tenant_id → ValueError."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            execute_write("CREATE (n:Test)", {"claim_id": "c1"})


class TestSchemaConstants:
    """Tests that schema constants match Data Model §4.1/§4.2 exactly."""

    def test_node_labels_match_spec(self) -> None:
        """Node labels must match §4.1 verbatim."""
        expected = {
            "Deal",
            "Document",
            "Span",
            "EvidenceItem",
            "Claim",
            "TransmissionNode",
            "Agent",
            "Calculation",
            "Defect",
            "Entity",
            "Market",
            "Sector",
        }
        assert expected == VALID_NODE_LABELS

    def test_no_tenant_node_label(self) -> None:
        """No Tenant node per spec — tenant isolation via property."""
        assert "Tenant" not in VALID_NODE_LABELS

    def test_edge_types_match_spec(self) -> None:
        """Edge types must match §4.2 verbatim."""
        expected = {
            "HAS_DOCUMENT",
            "HAS_SPAN",
            "SUPPORTED_BY",
            "HAS_SANAD_STEP",
            "INPUT",
            "OUTPUT",
            "HAS_DEFECT",
            "DERIVED_FROM",
            "MENTIONED_IN",
            "COMPETES_WITH",
            "IN_SECTOR",
        }
        assert expected == VALID_EDGE_TYPES

    def test_no_invented_edge_types(self) -> None:
        """No HAS_CLAIM/HAS_SANAD or other invented edges."""
        invented = {"HAS_CLAIM", "HAS_SANAD", "BELONGS_TO", "OWNS"}
        assert VALID_EDGE_TYPES.isdisjoint(invented)

    def test_node_label_enum_count(self) -> None:
        """Exactly 12 node labels per §4.1."""
        assert len(NodeLabel) == 12

    def test_edge_type_enum_count(self) -> None:
        """Exactly 11 edge types per §4.2."""
        assert len(EdgeType) == 11


class TestResetDriverForTesting:
    """Tests for test utility."""

    def test_reset_when_no_driver(self) -> None:
        """reset_driver_for_testing is safe when no driver exists."""
        reset_driver_for_testing()
