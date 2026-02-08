"""Enrichment framework domain models.

Defines the adapter contract types per IDIS_Enrichment_Connector_Framework_v0_1.md §3:
- EnrichmentRequest, EnrichmentContext, EnrichmentResult
- RightsClass, CachePolicy, EntityType, EnrichmentPurpose, EnrichmentStatus
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class RightsClass(StrEnum):
    """Data rights classification per licensing matrix.

    GREEN: safe to ship, caching allowed with reasonable TTL.
    YELLOW: ship with attribution; constrain caching/redistribution.
    RED: do not ship without commercial terms or BYOL.
    """

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class EntityType(StrEnum):
    """Entity types supported by enrichment connectors."""

    COMPANY = "COMPANY"
    PERSON = "PERSON"
    DEAL = "DEAL"


class EnrichmentPurpose(StrEnum):
    """Purpose of the enrichment request."""

    SANAD_EVIDENCE = "SANAD_EVIDENCE"
    KYC = "KYC"
    DUE_DILIGENCE = "DUE_DILIGENCE"


class EnrichmentStatus(StrEnum):
    """Result status of an enrichment fetch."""

    HIT = "HIT"
    MISS = "MISS"
    ERROR = "ERROR"
    BLOCKED_RIGHTS = "BLOCKED_RIGHTS"
    BLOCKED_MISSING_BYOL = "BLOCKED_MISSING_BYOL"


class CachePolicyConfig(BaseModel):
    """Cache policy configuration for a connector.

    Attributes:
        ttl_seconds: Time-to-live for cached results. 0 means no-store.
        no_store: If True, do not persist to Postgres/Redis. Only in-memory
            per-request memoization allowed.
    """

    ttl_seconds: int = Field(ge=0, default=86400)
    no_store: bool = False


class EnrichmentQuery(BaseModel):
    """Structured query identifiers for enrichment lookup."""

    cik: str | None = None
    company_name: str | None = None
    ticker: str | None = None
    lei: str | None = None
    jurisdiction: str | None = None


class EnrichmentRequest(BaseModel):
    """Request to an enrichment connector per spec §3.

    Attributes:
        tenant_id: UUID of the requesting tenant.
        entity_type: Type of entity being enriched.
        query: Structured identifiers for lookup.
        requested_fields: Optional list of specific fields to fetch.
        purpose: Purpose of the enrichment request.
    """

    tenant_id: str
    entity_type: EntityType
    query: EnrichmentQuery
    requested_fields: list[str] | None = None
    purpose: EnrichmentPurpose = EnrichmentPurpose.DUE_DILIGENCE


class EnrichmentContext(BaseModel):
    """Execution context for a connector fetch per spec §3.

    Attributes:
        timeout_seconds: Maximum time for the provider call.
        max_retries: Maximum retry attempts on transient failure.
        request_id: Correlation ID for tracing/audit.
        trace_id: OpenTelemetry trace ID (optional).
        byol_credentials: Decrypted BYOL credentials (if applicable).
    """

    timeout_seconds: float = 30.0
    max_retries: int = 1
    request_id: str = ""
    trace_id: str | None = None
    byol_credentials: dict[str, str] | None = None


class EnrichmentProvenance(BaseModel):
    """Provenance metadata for an enrichment result.

    Attributes:
        source_id: Provider identifier.
        retrieved_at: Timestamp of data retrieval.
        rights_class: Rights classification of the source.
        raw_ref_hash: SHA256 hash of raw response (for audit, not the raw data).
        identifiers_used: Query identifiers that produced this result.
    """

    source_id: str
    retrieved_at: datetime
    rights_class: RightsClass
    raw_ref_hash: str
    identifiers_used: dict[str, str] = Field(default_factory=dict)


class EnrichmentResult(BaseModel):
    """Result of an enrichment fetch per spec §3.

    Attributes:
        status: Outcome status of the fetch.
        normalized: Stable-schema normalized data (empty dict on non-HIT).
        provenance: Source metadata and audit trail.
        raw: Optional raw payload (only if policy allows; must not leak secrets).
        expires_at: Cache expiry timestamp (derived from cache policy).
    """

    status: EnrichmentStatus
    normalized: dict[str, Any] = Field(default_factory=dict)
    provenance: EnrichmentProvenance | None = None
    raw: dict[str, Any] | None = None
    expires_at: datetime | None = None


@runtime_checkable
class EnrichmentConnector(Protocol):
    """Adapter contract for enrichment connectors per spec §3.

    Every connector MUST implement this protocol.
    """

    @property
    def provider_id(self) -> str:
        """Unique identifier for this provider."""
        ...

    @property
    def rights_class(self) -> RightsClass:
        """Rights classification (GREEN/YELLOW/RED)."""
        ...

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """Cache policy configuration for this provider."""
        ...

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        """Execute enrichment fetch against the external provider.

        Args:
            request: Enrichment request with tenant, entity, and query details.
            ctx: Execution context with timeouts, retries, and credentials.

        Returns:
            EnrichmentResult with status, normalized data, and provenance.
        """
        ...
