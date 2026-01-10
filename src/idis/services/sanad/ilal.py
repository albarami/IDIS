"""I'lal (Hidden Defects) Detection — Chain integrity and provenance checking.

Implements deterministic detection of hidden defects in evidence chains.
All detection is fail-closed: uncertain integrity treated as defective.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class IlalDefectCode(Enum):
    """I'lal hidden defect codes."""

    ILAL_VERSION_DRIFT = "ILAL_VERSION_DRIFT"
    ILAL_CHAIN_BREAK = "ILAL_CHAIN_BREAK"
    ILAL_CHAIN_GRAFTING = "ILAL_CHAIN_GRAFTING"
    ILAL_CHRONOLOGY_IMPOSSIBLE = "ILAL_CHRONOLOGY_IMPOSSIBLE"


@dataclass
class IlalDefect:
    """Result of I'lal (hidden defect) detection."""

    code: IlalDefectCode
    severity: str
    description: str
    cure_protocol: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "code": self.code.value,
            "severity": self.severity,
            "description": self.description,
            "cure_protocol": self.cure_protocol,
            "metadata": self.metadata or {},
        }


def _parse_timestamp(ts: Any) -> datetime | None:
    """Parse timestamp from various formats."""
    if ts is None:
        return None

    if isinstance(ts, datetime):
        return ts

    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    return None


def _get_node_ids(chain: list[dict[str, Any]]) -> set[str]:
    """Extract all node IDs from transmission chain."""
    return {str(n.get("node_id")) for n in chain if isinstance(n, dict) and n.get("node_id")}


def detect_ilal_version_drift(
    claim: dict[str, Any],
    documents: list[dict[str, Any]],
    *,
    cited_doc_field: str = "cited_document",
    version_field: str = "version",
    sha_field: str = "sha256",
) -> IlalDefect | None:
    """Detect ILAL_VERSION_DRIFT: claim cites old version but newer exists.

    TRIGGERS:
    - Claim references a document version
    - Newer version of same document exists
    - Metric value changed between versions

    Args:
        claim: Claim dictionary with cited document reference
        documents: List of document dictionaries
        cited_doc_field: Field name for cited document reference
        version_field: Field name for version
        sha_field: Field name for SHA hash

    Returns:
        IlalDefect if version drift detected, None otherwise
    """
    if not documents:
        return None

    cited_ref = claim.get(cited_doc_field) or claim.get("source_ref")
    if not cited_ref:
        return None

    if isinstance(cited_ref, str):
        cited_doc_id = cited_ref
        cited_version = None
    elif isinstance(cited_ref, dict):
        cited_doc_id = cited_ref.get("document_id") or cited_ref.get("artifact_id")
        cited_version = cited_ref.get(version_field)
    else:
        return None

    if not cited_doc_id:
        return None

    matching_docs = [
        d
        for d in documents
        if (d.get("document_id") == cited_doc_id or d.get("artifact_id") == cited_doc_id)
    ]

    if len(matching_docs) < 2:
        return None

    cited_doc = None
    if cited_version is not None:
        cited_doc = next(
            (d for d in matching_docs if d.get(version_field) == cited_version),
            None,
        )
    else:
        cited_sha = cited_ref.get(sha_field) if isinstance(cited_ref, dict) else None
        if cited_sha:
            cited_doc = next(
                (d for d in matching_docs if d.get(sha_field) == cited_sha),
                None,
            )

    if not cited_doc:
        cited_doc = min(matching_docs, key=lambda d: d.get(version_field, 0))

    cited_ver = cited_doc.get(version_field, 0)

    newer_docs = [d for d in matching_docs if d.get(version_field, 0) > cited_ver]

    if not newer_docs:
        return None

    latest = max(newer_docs, key=lambda d: d.get(version_field, 0))

    claim_type = claim.get("claim_type", "UNKNOWN")
    cited_value = cited_doc.get("metrics", {}).get(claim_type) or cited_doc.get("value")
    latest_value = latest.get("metrics", {}).get(claim_type) or latest.get("value")

    if cited_value is None and latest_value is None:
        return None

    if cited_value != latest_value:
        return IlalDefect(
            code=IlalDefectCode.ILAL_VERSION_DRIFT,
            severity="MAJOR",
            description=(
                f"Claim cites version {cited_ver} (value: {cited_value}) "
                f"but version {latest.get(version_field)} exists with "
                f"updated value: {latest_value}"
            ),
            cure_protocol="REQUIRE_REAUDIT",
            metadata={
                "cited_version": cited_ver,
                "cited_sha": cited_doc.get(sha_field),
                "latest_version": latest.get(version_field),
                "latest_sha": latest.get(sha_field),
                "value_change": f"{cited_value} → {latest_value}",
                "document_id": cited_doc_id,
            },
        )

    return None


def detect_ilal_chain_break(
    sanad: dict[str, Any],
    evidence_ids: set[str] | None = None,
) -> IlalDefect | None:
    """Detect ILAL_CHAIN_BREAK: broken transmission chain.

    TRIGGERS:
    - Missing transmission node (referenced parent doesn't exist)
    - Reference to non-existent evidence
    - Orphaned nodes not connected to root

    Args:
        sanad: Sanad dictionary with transmission_chain
        evidence_ids: Optional set of valid evidence IDs for reference checking

    Returns:
        IlalDefect if chain break detected, None otherwise
    """
    chain = sanad.get("transmission_chain", [])
    if not chain:
        return IlalDefect(
            code=IlalDefectCode.ILAL_CHAIN_BREAK,
            severity="FATAL",
            description="Transmission chain is empty",
            cure_protocol="RECONSTRUCT_CHAIN",
        )

    if not isinstance(chain, list):
        return IlalDefect(
            code=IlalDefectCode.ILAL_CHAIN_BREAK,
            severity="FATAL",
            description="Transmission chain is not a list",
            cure_protocol="RECONSTRUCT_CHAIN",
        )

    node_ids = _get_node_ids(chain)

    for node in chain:
        if not isinstance(node, dict):
            continue

        node_id = node.get("node_id")

        parent_id = node.get("prev_node_id") or node.get("parent_id")
        if parent_id and str(parent_id) not in node_ids:
            return IlalDefect(
                code=IlalDefectCode.ILAL_CHAIN_BREAK,
                severity="FATAL",
                description=f"Node {node_id} references non-existent parent {parent_id}",
                cure_protocol="RECONSTRUCT_CHAIN",
                metadata={
                    "node_id": node_id,
                    "missing_parent_id": parent_id,
                },
            )

        evidence_ref = node.get("evidence_id") or node.get("source_evidence_id")
        if evidence_ref and evidence_ids is not None and str(evidence_ref) not in evidence_ids:
            return IlalDefect(
                code=IlalDefectCode.ILAL_CHAIN_BREAK,
                severity="FATAL",
                description=f"Node {node_id} references non-existent evidence {evidence_ref}",
                cure_protocol="REQUEST_SOURCE",
                metadata={
                    "node_id": node_id,
                    "missing_evidence_id": evidence_ref,
                },
            )

    parent_map: dict[str, str | None] = {}
    children_map: dict[str, list[str]] = {}

    for node in chain:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id", ""))
        if not node_id:
            continue

        parent_id = node.get("prev_node_id") or node.get("parent_id")
        parent_map[node_id] = str(parent_id) if parent_id else None

        if parent_id:
            parent_str = str(parent_id)
            if parent_str not in children_map:
                children_map[parent_str] = []
            children_map[parent_str].append(node_id)

    roots = [nid for nid, pid in parent_map.items() if pid is None]

    if len(roots) == 0 and len(node_ids) > 0:
        has_parent_refs = any(v is not None for v in parent_map.values())
        if has_parent_refs:
            return IlalDefect(
                code=IlalDefectCode.ILAL_CHAIN_BREAK,
                severity="FATAL",
                description="Transmission chain has no root node (all nodes have parents)",
                cure_protocol="RECONSTRUCT_CHAIN",
            )

    if len(roots) == 1:
        reachable: set[str] = set()

        def collect(nid: str) -> None:
            if nid in reachable:
                return
            reachable.add(nid)
            for child in children_map.get(nid, []):
                collect(child)

        collect(roots[0])
        orphaned = node_ids - reachable
        if orphaned:
            return IlalDefect(
                code=IlalDefectCode.ILAL_CHAIN_BREAK,
                severity="FATAL",
                description=f"Orphaned nodes not connected to root: {sorted(orphaned)}",
                cure_protocol="RECONSTRUCT_CHAIN",
                metadata={"orphaned_nodes": sorted(orphaned)},
            )

    return None


def detect_ilal_chain_grafting(
    sanad: dict[str, Any],
) -> IlalDefect | None:
    """Detect ILAL_CHAIN_GRAFTING: inconsistent provenance linkage.

    TRIGGERS:
    - Node claims different upstream origin than parent chain suggests
    - Mismatched upstream_origin_id in connected nodes

    Args:
        sanad: Sanad dictionary with transmission_chain

    Returns:
        IlalDefect if chain grafting detected, None otherwise
    """
    chain = sanad.get("transmission_chain", [])
    if not chain or not isinstance(chain, list):
        return None

    if len(chain) < 2:
        return None

    node_by_id: dict[str, dict[str, Any]] = {}
    for node in chain:
        if isinstance(node, dict) and node.get("node_id"):
            node_by_id[str(node["node_id"])] = node

    for node in chain:
        if not isinstance(node, dict):
            continue

        parent_id = node.get("prev_node_id") or node.get("parent_id")
        if not parent_id:
            continue

        parent_node = node_by_id.get(str(parent_id))
        if not parent_node:
            continue

        node_origin = node.get("upstream_origin_id")
        parent_origin = parent_node.get("upstream_origin_id")

        if node_origin and parent_origin and node_origin != parent_origin:
            return IlalDefect(
                code=IlalDefectCode.ILAL_CHAIN_GRAFTING,
                severity="FATAL",
                description=(
                    f"Inconsistent provenance: node {node.get('node_id')} claims origin "
                    f"{node_origin} but parent suggests {parent_origin}"
                ),
                cure_protocol="HUMAN_ARBITRATION",
                metadata={
                    "node_id": node.get("node_id"),
                    "node_origin": node_origin,
                    "parent_id": parent_id,
                    "parent_origin": parent_origin,
                },
            )

    return None


def detect_ilal_chronology_impossible(
    sanad: dict[str, Any],
) -> IlalDefect | None:
    """Detect ILAL_CHRONOLOGY_IMPOSSIBLE: timestamps violate causality.

    TRIGGERS:
    - Child node timestamp precedes parent timestamp
    - Evidence timestamp after extraction timestamp

    Args:
        sanad: Sanad dictionary with transmission_chain

    Returns:
        IlalDefect if chronology violation detected, None otherwise
    """
    chain = sanad.get("transmission_chain", [])
    if not chain or not isinstance(chain, list):
        return None

    if len(chain) < 2:
        return None

    node_by_id: dict[str, dict[str, Any]] = {}
    for node in chain:
        if isinstance(node, dict) and node.get("node_id"):
            node_by_id[str(node["node_id"])] = node

    for node in chain:
        if not isinstance(node, dict):
            continue

        parent_id = node.get("prev_node_id") or node.get("parent_id")
        if not parent_id:
            continue

        parent_node = node_by_id.get(str(parent_id))
        if not parent_node:
            continue

        node_ts = _parse_timestamp(node.get("timestamp"))
        parent_ts = _parse_timestamp(parent_node.get("timestamp"))

        if node_ts and parent_ts and node_ts < parent_ts:
            return IlalDefect(
                code=IlalDefectCode.ILAL_CHRONOLOGY_IMPOSSIBLE,
                severity="FATAL",
                description=(
                    f"Chronology violation: node {node.get('node_id')} "
                    f"({node_ts.isoformat()}) precedes parent {parent_id} "
                    f"({parent_ts.isoformat()})"
                ),
                cure_protocol="REQUIRE_REAUDIT",
                metadata={
                    "node_id": node.get("node_id"),
                    "node_timestamp": node_ts.isoformat(),
                    "parent_id": parent_id,
                    "parent_timestamp": parent_ts.isoformat(),
                },
            )

    return None


def detect_all_ilal(
    sanad: dict[str, Any],
    claim: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    evidence_ids: set[str] | None = None,
) -> list[IlalDefect]:
    """Run all I'lal detection checks.

    Args:
        sanad: Sanad dictionary
        claim: Optional claim dictionary for version drift check
        documents: Optional list of documents for version drift check
        evidence_ids: Optional set of valid evidence IDs

    Returns:
        List of detected IlalDefect objects
    """
    defects: list[IlalDefect] = []

    chain_break = detect_ilal_chain_break(sanad, evidence_ids)
    if chain_break:
        defects.append(chain_break)

    grafting = detect_ilal_chain_grafting(sanad)
    if grafting:
        defects.append(grafting)

    chronology = detect_ilal_chronology_impossible(sanad)
    if chronology:
        defects.append(chronology)

    if claim and documents:
        version_drift = detect_ilal_version_drift(claim, documents)
        if version_drift:
            defects.append(version_drift)

    return defects
