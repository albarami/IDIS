"""Neo4j graph database driver for IDIS Sanad graph projection.

Provides fail-closed Neo4j connectivity with tenant isolation enforcement.
Postgres remains the source of truth; Neo4j is the graph projection layer.

Environment Variables:
    NEO4J_URI: Neo4j connection URI (bolt:// or neo4j+s://)
    NEO4J_USERNAME: Neo4j username
    NEO4J_PASSWORD: Neo4j password
    NEO4J_DATABASE: Explicit database used by every session

Design Requirements (v6.3 Data Model §4):
    - No Tenant node; tenant isolation via tenant_id property on every node
    - All queries must filter by tenant_id as first-match constraint
    - Fail-closed on partial configuration
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from neo4j import Driver, GraphDatabase, Session
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

NEO4J_URI_ENV = "NEO4J_URI"
NEO4J_USERNAME_ENV = "NEO4J_USERNAME"
NEO4J_PASSWORD_ENV = "NEO4J_PASSWORD"
NEO4J_DATABASE_ENV = "NEO4J_DATABASE"

_driver: Driver | None = None


class Neo4jConfigError(Exception):
    """Raised when Neo4j configuration is missing or incomplete.

    Fail-closed: operations requiring Neo4j must not proceed
    without valid, complete configuration.
    """


class Neo4jHealthStatus(StrEnum):
    """Safe Neo4j health status values."""

    HEALTHY = "healthy"
    MISSING_CREDENTIALS = "missing_credentials"
    FAILED = "failed"


class Neo4jHealthCheck(BaseModel):
    """Sanitized Neo4j health-check result."""

    model_config = ConfigDict(extra="forbid")

    status: Neo4jHealthStatus
    config_present: bool
    missing_env_vars: list[str] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def healthy(cls) -> Neo4jHealthCheck:
        """Return a successful health result."""
        return cls(
            status=Neo4jHealthStatus.HEALTHY,
            config_present=True,
            missing_env_vars=[],
        )

    @classmethod
    def missing(cls, *, missing_env_vars: list[str]) -> Neo4jHealthCheck:
        """Return a missing/partial configuration result."""
        return cls(
            status=Neo4jHealthStatus.MISSING_CREDENTIALS,
            config_present=False,
            missing_env_vars=missing_env_vars,
            error="Neo4j configuration is incomplete.",
        )

    @classmethod
    def failed(cls) -> Neo4jHealthCheck:
        """Return a sanitized failed health result."""
        return cls(
            status=Neo4jHealthStatus.FAILED,
            config_present=True,
            missing_env_vars=[],
            error="Neo4j health check failed.",
        )


class Neo4jDatabaseProbe(BaseModel):
    """Secret-safe proof that the configured database supports a write/read/delete cycle."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    write_proven: bool
    read_proven: bool
    delete_proven: bool
    error_code: str | None = None


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
        raise Neo4jConfigError(f"{NEO4J_URI_ENV} is not set. Neo4j is not configured.")

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


def _configured_database(env: Mapping[str, str] | None = None) -> str:
    """Return the configured Neo4j database or fail before opening a session."""
    values = os.environ if env is None else env
    database = str(values.get(NEO4J_DATABASE_ENV, "")).strip()
    if not database:
        raise Neo4jConfigError(f"NEO4J_DATABASE_NOT_WIRED: {NEO4J_DATABASE_ENV} must be nonblank")
    return database


def check_neo4j_health(
    *,
    env: Mapping[str, str] | None = None,
    driver_factory: Callable[..., Driver] | None = None,
) -> Neo4jHealthCheck:
    """Check Neo4j connectivity without exposing connection details.

    Args:
        env: Environment mapping to inspect. Defaults to ``os.environ``.
        driver_factory: Injectable driver constructor for unit tests.

    Returns:
        Sanitized health-check result.
    """
    values = os.environ if env is None else env
    required = [
        NEO4J_URI_ENV,
        NEO4J_USERNAME_ENV,
        NEO4J_PASSWORD_ENV,
        NEO4J_DATABASE_ENV,
    ]
    missing = [key for key in required if not str(values.get(key, "")).strip()]
    if missing:
        return Neo4jHealthCheck.missing(missing_env_vars=missing)

    uri = str(values[NEO4J_URI_ENV])
    username = str(values[NEO4J_USERNAME_ENV])
    password = str(values[NEO4J_PASSWORD_ENV])
    database = _configured_database(values)
    make_driver = driver_factory or GraphDatabase.driver
    driver = None
    try:
        driver = make_driver(
            uri,
            auth=(username, password),
            max_connection_lifetime=3600,
            max_connection_pool_size=5,
            connection_acquisition_timeout=10,
        )
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run("RETURN 1 AS ok").consume()
    except Exception:
        return Neo4jHealthCheck.failed()
    finally:
        if driver is not None:
            driver.close()
    return Neo4jHealthCheck.healthy()


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


def get_session(*, database: str | None = None) -> Session:
    """Get a Neo4j session from the singleton driver.

    Args:
        database: Optional assertion of the configured Neo4j database name.

    Returns:
        Neo4j Session instance.

    Raises:
        Neo4jConfigError: If Neo4j is not configured.
    """
    configured_database = _configured_database()
    if database is not None and database.strip() != configured_database:
        raise Neo4jConfigError(
            "NEO4J_DATABASE_NOT_WIRED: requested database does not match configured database"
        )
    driver = get_neo4j_driver()
    return driver.session(database=configured_database)


def execute_read(
    query: str,
    parameters: dict[str, Any],
    *,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """Execute a read query with tenant_id enforcement.

    Args:
        query: Cypher query string.
        parameters: Query parameters (must include tenant_id).
        database: Optional assertion of the configured Neo4j database name.

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
    database: str | None = None,
) -> list[dict[str, Any]]:
    """Execute a write query with tenant_id enforcement.

    Args:
        query: Cypher query string.
        parameters: Query parameters (must include tenant_id).
        database: Optional assertion of the configured Neo4j database name.

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


def _run_database_probe_query(
    *,
    driver: Driver,
    database: str,
    query: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Execute one autocommit probe query in the explicitly configured database."""
    with driver.session(database=database) as session:
        record = session.run(query, parameters).single()
    return {} if record is None else dict(record)


def probe_neo4j_database(
    *,
    env: Mapping[str, str] | None = None,
    driver_factory: Callable[..., Driver] | None = None,
    probe_namespace: str,
) -> Neo4jDatabaseProbe:
    """Prove write/read/delete access in the exact configured Neo4j database.

    The result deliberately contains neither connection configuration nor the probe marker.  A
    best-effort cleanup runs after any partial cycle so a failed readiness probe does not leave a
    durable probe node behind.
    """
    values = os.environ if env is None else env
    required = [NEO4J_URI_ENV, NEO4J_USERNAME_ENV, NEO4J_PASSWORD_ENV]
    if any(not str(values.get(key, "")).strip() for key in required):
        return Neo4jDatabaseProbe(
            passed=False,
            write_proven=False,
            read_proven=False,
            delete_proven=False,
            error_code="GRAPH_HEALTH_BLOCKED",
        )

    try:
        database = _configured_database(values)
    except Neo4jConfigError:
        return Neo4jDatabaseProbe(
            passed=False,
            write_proven=False,
            read_proven=False,
            delete_proven=False,
            error_code="NEO4J_DATABASE_NOT_WIRED",
        )

    marker = probe_namespace.strip()
    if not marker:
        return Neo4jDatabaseProbe(
            passed=False,
            write_proven=False,
            read_proven=False,
            delete_proven=False,
            error_code="GRAPH_HEALTH_BLOCKED",
        )

    uri = str(values[NEO4J_URI_ENV])
    username = str(values[NEO4J_USERNAME_ENV])
    password = str(values[NEO4J_PASSWORD_ENV])
    make_driver = driver_factory or GraphDatabase.driver
    driver: Driver | None = None
    write_attempted = False
    write_proven = False
    read_proven = False
    delete_proven = False
    parameters = {
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "probe_marker": marker,
    }
    try:
        driver = make_driver(
            uri,
            auth=(username, password),
            max_connection_lifetime=3600,
            max_connection_pool_size=5,
            connection_acquisition_timeout=10,
        )
        driver.verify_connectivity()

        # Autocommit may persist MERGE before result decoding raises, so cleanup is
        # keyed to attempted execution rather than a successfully decoded receipt.
        write_attempted = True
        write_row = _run_database_probe_query(
            driver=driver,
            database=database,
            query=(
                "MERGE (probe:Entity {tenant_id: $tenant_id, probe_marker: $probe_marker}) "
                "SET probe.entity_type = 'idis_readiness_probe' "
                "RETURN probe.probe_marker AS probe_marker"
            ),
            parameters=parameters,
        )
        write_proven = write_row.get("probe_marker") == marker

        read_row = _run_database_probe_query(
            driver=driver,
            database=database,
            query=(
                "MATCH (probe:Entity {tenant_id: $tenant_id, probe_marker: $probe_marker}) "
                "RETURN probe.probe_marker AS probe_marker, count(probe) AS count"
            ),
            parameters=parameters,
        )
        read_proven = read_row.get("probe_marker") == marker and int(read_row.get("count", 0)) >= 1

        delete_row = _run_database_probe_query(
            driver=driver,
            database=database,
            query=(
                "MATCH (probe:Entity {tenant_id: $tenant_id, probe_marker: $probe_marker}) "
                "WITH collect(probe) AS probes "
                "FOREACH (node IN probes | DELETE node) "
                "RETURN size(probes) AS deleted_count"
            ),
            parameters=parameters,
        )
        delete_proven = int(delete_row.get("deleted_count", 0)) >= 1
    except Exception:
        return Neo4jDatabaseProbe(
            passed=False,
            write_proven=write_proven,
            read_proven=read_proven,
            delete_proven=delete_proven,
            error_code="GRAPH_HEALTH_BLOCKED",
        )
    finally:
        if driver is not None:
            if write_attempted and not delete_proven:
                try:
                    _run_database_probe_query(
                        driver=driver,
                        database=database,
                        query=(
                            "MATCH (probe:Entity {tenant_id: $tenant_id, "
                            "probe_marker: $probe_marker}) DELETE probe RETURN true AS deleted"
                        ),
                        parameters=parameters,
                    )
                except Exception:
                    logger.warning("Neo4j readiness probe cleanup failed")
            driver.close()

    passed = write_proven and read_proven and delete_proven
    return Neo4jDatabaseProbe(
        passed=passed,
        write_proven=write_proven,
        read_proven=read_proven,
        delete_proven=delete_proven,
        error_code=None if passed else "GRAPH_HEALTH_BLOCKED",
    )


def reset_driver_for_testing() -> None:
    """Reset the singleton driver. For test use only."""
    global _driver  # noqa: PLW0603
    if _driver is not None:
        _driver.close()
    _driver = None
