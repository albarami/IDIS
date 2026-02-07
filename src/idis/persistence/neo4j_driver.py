"""Neo4j graph database driver for IDIS Sanad graph projection.

Provides fail-closed Neo4j connectivity with tenant isolation enforcement.
Postgres remains the source of truth; Neo4j is the graph projection layer.

Environment Variables:
    NEO4J_URI: Neo4j connection URI (bolt:// or neo4j+s://)
    NEO4J_USERNAME: Neo4j username
    NEO4J_PASSWORD: Neo4j password

Design Requirements (v6.3 Data Model §4):
    - No Tenant node; tenant isolation via tenant_id property on every node
    - All queries must filter by tenant_id as first-match constraint
    - Fail-closed on partial configuration
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from typing import Any

from neo4j import Driver, GraphDatabase, Session

logger = logging.getLogger(__name__)

NEO4J_URI_ENV = "NEO4J_URI"
NEO4J_USERNAME_ENV = "NEO4J_USERNAME"
NEO4J_PASSWORD_ENV = "NEO4J_PASSWORD"

_driver: Driver | None = None


class Neo4jConfigError(Exception):
    """Raised when Neo4j configuration is missing or incomplete.

    Fail-closed: operations requiring Neo4j must not proceed
    without valid, complete configuration.
    """


class NodeLabel(StrEnum):
    """Node labels from Data Model §4.1 (verbatim, no additions)."""

    DEAL = "Deal"
    DOCUMENT = "Document"
    SPAN = "Span"
    EVIDENCE_ITEM = "EvidenceItem"
    CLAIM = "Claim"
    TRANSMISSION_NODE = "TransmissionNode"
    AGENT = "Agent"
    CALCULATION = "Calculation"
    DEFECT = "Defect"
    ENTITY = "Entity"
    MARKET = "Market"
    SECTOR = "Sector"


class EdgeType(StrEnum):
    """Edge types from Data Model §4.2 (verbatim, no additions)."""

    HAS_DOCUMENT = "HAS_DOCUMENT"
    HAS_SPAN = "HAS_SPAN"
    SUPPORTED_BY = "SUPPORTED_BY"
    HAS_SANAD_STEP = "HAS_SANAD_STEP"
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    HAS_DEFECT = "HAS_DEFECT"
    DERIVED_FROM = "DERIVED_FROM"
    MENTIONED_IN = "MENTIONED_IN"
    COMPETES_WITH = "COMPETES_WITH"
    IN_SECTOR = "IN_SECTOR"


VALID_NODE_LABELS: frozenset[str] = frozenset(label.value for label in NodeLabel)
VALID_EDGE_TYPES: frozenset[str] = frozenset(edge.value for edge in EdgeType)


def is_neo4j_configured() -> bool:
    """Check if Neo4j is configured via environment.

    Neo4j is "configured" iff NEO4J_URI is set. If URI is set but
    credentials are incomplete, get_neo4j_driver() will fail closed.

    Returns:
        True if NEO4J_URI is set, False otherwise.
    """
    return bool(os.environ.get(NEO4J_URI_ENV))


def _validate_config() -> tuple[str, str, str]:
    """Validate and return Neo4j configuration from environment.

    Returns:
        Tuple of (uri, username, password).

    Raises:
        Neo4jConfigError: If configuration is incomplete.
    """
    uri = os.environ.get(NEO4J_URI_ENV, "")
    username = os.environ.get(NEO4J_USERNAME_ENV, "")
    password = os.environ.get(NEO4J_PASSWORD_ENV, "")

    if not uri:
        raise Neo4jConfigError(
            f"{NEO4J_URI_ENV} is not set. Neo4j is not configured."
        )

    missing = []
    if not username:
        missing.append(NEO4J_USERNAME_ENV)
    if not password:
        missing.append(NEO4J_PASSWORD_ENV)

    if missing:
        raise Neo4jConfigError(
            f"Neo4j URI is set but credentials are incomplete. "
            f"Missing: {', '.join(missing)}. "
            f"Fail-closed: refusing to connect with partial configuration."
        )

    return uri, username, password


def get_neo4j_driver() -> Driver:
    """Get or create the singleton Neo4j driver.

    Fail-closed: raises Neo4jConfigError if partially configured
    (URI set but missing username/password).

    Returns:
        Neo4j Driver instance.

    Raises:
        Neo4jConfigError: If configuration is incomplete.
    """
    global _driver  # noqa: PLW0603

    if _driver is not None:
        return _driver

    uri, username, password = _validate_config()

    _driver = GraphDatabase.driver(
        uri,
        auth=(username, password),
        max_connection_lifetime=3600,
        max_connection_pool_size=50,
        connection_acquisition_timeout=30,
    )

    logger.info("Neo4j driver created for %s", uri)
    return _driver


def close_neo4j_driver() -> None:
    """Close the singleton Neo4j driver if it exists."""
    global _driver  # noqa: PLW0603

    if _driver is not None:
        _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


def get_session(*, database: str = "neo4j") -> Session:
    """Get a Neo4j session from the singleton driver.

    Args:
        database: Neo4j database name.

    Returns:
        Neo4j Session instance.

    Raises:
        Neo4jConfigError: If Neo4j is not configured.
    """
    driver = get_neo4j_driver()
    return driver.session(database=database)


def execute_read(
    query: str,
    parameters: dict[str, Any],
    *,
    database: str = "neo4j",
) -> list[dict[str, Any]]:
    """Execute a read query with tenant_id enforcement.

    Args:
        query: Cypher query string.
        parameters: Query parameters (must include tenant_id).
        database: Neo4j database name.

    Returns:
        List of record dictionaries.

    Raises:
        Neo4jConfigError: If Neo4j is not configured.
        ValueError: If tenant_id is missing from parameters.
    """
    if "tenant_id" not in parameters:
        raise ValueError(
            "tenant_id is required in all Neo4j query parameters. "
            "Tenant isolation is enforced at the driver level."
        )

    with get_session(database=database) as session:
        result = session.run(query, parameters)
        return [dict(record) for record in result]


def execute_write(
    query: str,
    parameters: dict[str, Any],
    *,
    database: str = "neo4j",
) -> list[dict[str, Any]]:
    """Execute a write query with tenant_id enforcement.

    Args:
        query: Cypher query string.
        parameters: Query parameters (must include tenant_id).
        database: Neo4j database name.

    Returns:
        List of record dictionaries.

    Raises:
        Neo4jConfigError: If Neo4j is not configured.
        ValueError: If tenant_id is missing from parameters.
    """
    if "tenant_id" not in parameters:
        raise ValueError(
            "tenant_id is required in all Neo4j query parameters. "
            "Tenant isolation is enforced at the driver level."
        )

    with get_session(database=database) as session:
        result = session.run(query, parameters)
        return [dict(record) for record in result]


def reset_driver_for_testing() -> None:
    """Reset the singleton driver. For test use only."""
    global _driver  # noqa: PLW0603
    if _driver is not None:
        _driver.close()
    _driver = None
