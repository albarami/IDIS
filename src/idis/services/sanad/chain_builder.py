"""Sanad chain builder — constructs transmission chains for extracted claims.

Builds an acyclic, monotonic-timestamp chain per claim:
  INGEST → EXTRACT → [NORMALIZE] (if deduped)

Fail-closed: refuses to build a chain when evidence is missing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

NODE_TYPE_INGEST = "INGEST"
NODE_TYPE_EXTRACT = "EXTRACT"
NODE_TYPE_NORMALIZE = "NORMALIZE"

ACTOR_TYPE_SYSTEM = "SYSTEM"
ACTOR_ID_INGESTION = "idis_ingestion"
ACTOR_ID_EXTRACTOR = "idis_extractor"
ACTOR_ID_NORMALIZER = "idis_normalizer"


class ChainBuildError(Exception):
    """Raised when chain construction fails (fail-closed)."""

    def __init__(self, claim_id: str, reason: str) -> None:
        self.claim_id = claim_id
        self.reason = reason
        super().__init__(f"Chain build failed for claim {claim_id}: {reason}")


def _make_node(
    *,
    node_type: str,
    actor_type: str,
    actor_id: str,
    prev_node_id: str | None,
    timestamp: str,
    input_refs: list[dict[str, Any]],
    output_refs: list[dict[str, Any]],
    confidence: float | None = None,
) -> dict[str, Any]:
    """Create a single transmission-chain node.

    Args:
        node_type: Type of processing step.
        actor_type: Actor category (SYSTEM, HUMAN, etc.).
        actor_id: Identifier of the actor.
        prev_node_id: ID of the preceding node (None for root).
        timestamp: ISO-8601 timestamp.
        input_refs: References consumed by this node.
        output_refs: References produced by this node.
        confidence: Optional confidence score.

    Returns:
        Node dict ready for persistence.
    """
    node: dict[str, Any] = {
        "node_id": str(uuid.uuid4()),
        "node_type": node_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "prev_node_id": prev_node_id,
        "timestamp": timestamp,
        "input_refs": input_refs,
        "output_refs": output_refs,
    }
    if confidence is not None:
        node["confidence"] = confidence
    return node


def build_sanad_chain(
    *,
    tenant_id: str,
    deal_id: str,
    claim_id: str,
    evidence_items: list[dict[str, Any]],
    extraction_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build a Sanad transmission chain for one extracted claim.

    Chain structure: INGEST → EXTRACT → [NORMALIZE].
    Timestamps are monotonically increasing.
    Each node links to its predecessor via prev_node_id.

    Args:
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.
        claim_id: Claim UUID.
        evidence_items: Evidence dicts linked to the claim. Must not be empty.
        extraction_metadata: Dict with extraction context. Key ``deduped``
            (bool) triggers insertion of a NORMALIZE node.

    Returns:
        Dict with ``sanad_id``, ``tenant_id``, ``deal_id``, ``claim_id``,
        ``primary_evidence_id``, ``transmission_chain``, and ``created_at``.

    Raises:
        ChainBuildError: If evidence_items is empty (fail-closed).
    """
    if not evidence_items:
        raise ChainBuildError(claim_id, "No evidence items — cannot build chain")

    primary_evidence = evidence_items[0]
    primary_evidence_id = str(
        primary_evidence.get("evidence_id", primary_evidence.get("source_span_id", ""))
    )

    base_ts = datetime.now(UTC)
    chain: list[dict[str, Any]] = []

    # --- Node 1: INGEST ---
    ingest_ts = base_ts.isoformat().replace("+00:00", "Z")
    ingest_node = _make_node(
        node_type=NODE_TYPE_INGEST,
        actor_type=ACTOR_TYPE_SYSTEM,
        actor_id=ACTOR_ID_INGESTION,
        prev_node_id=None,
        timestamp=ingest_ts,
        input_refs=[{"evidence_id": primary_evidence_id}],
        output_refs=[{"claim_id": claim_id}],
    )
    chain.append(ingest_node)

    # --- Node 2: EXTRACT ---
    extract_ts_dt = base_ts.replace(microsecond=base_ts.microsecond + 1)
    extract_ts = extract_ts_dt.isoformat().replace("+00:00", "Z")
    extract_confidence = extraction_metadata.get("confidence")
    extract_node = _make_node(
        node_type=NODE_TYPE_EXTRACT,
        actor_type=ACTOR_TYPE_SYSTEM,
        actor_id=ACTOR_ID_EXTRACTOR,
        prev_node_id=ingest_node["node_id"],
        timestamp=extract_ts,
        input_refs=[{"evidence_id": primary_evidence_id}],
        output_refs=[{"claim_id": claim_id}],
        confidence=float(extract_confidence) if extract_confidence is not None else None,
    )
    chain.append(extract_node)

    # --- Optional Node 3: NORMALIZE (when claim was merged/deduped) ---
    if extraction_metadata.get("deduped", False):
        normalize_ts_dt = extract_ts_dt.replace(microsecond=extract_ts_dt.microsecond + 1)
        normalize_ts = normalize_ts_dt.isoformat().replace("+00:00", "Z")
        normalize_node = _make_node(
            node_type=NODE_TYPE_NORMALIZE,
            actor_type=ACTOR_TYPE_SYSTEM,
            actor_id=ACTOR_ID_NORMALIZER,
            prev_node_id=extract_node["node_id"],
            timestamp=normalize_ts,
            input_refs=[{"claim_id": claim_id}],
            output_refs=[{"claim_id": claim_id}],
        )
        chain.append(normalize_node)

    return {
        "sanad_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "claim_id": claim_id,
        "primary_evidence_id": primary_evidence_id,
        "transmission_chain": chain,
        "created_at": base_ts.isoformat().replace("+00:00", "Z"),
    }
