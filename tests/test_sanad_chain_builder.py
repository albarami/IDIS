"""Tests for Sanad chain builder [P3-T01].

Three load-bearing tests:
- Happy path: INGEST → EXTRACT chain produced
- Fail-closed: empty evidence raises ChainBuildError
- NORMALIZE node inserted when extraction_metadata["deduped"] is True
"""

from __future__ import annotations

import pytest

from idis.services.sanad.chain_builder import (
    NODE_TYPE_EXTRACT,
    NODE_TYPE_INGEST,
    NODE_TYPE_NORMALIZE,
    ChainBuildError,
    build_sanad_chain,
)

TENANT_ID = "tenant-chain-test"
DEAL_ID = "deal-chain-test"
CLAIM_ID = "claim-chain-test"


def _make_evidence(evidence_id: str = "ev-001") -> list[dict[str, str]]:
    """Build a minimal evidence list."""
    return [{"evidence_id": evidence_id, "source_span_id": "span-001"}]


class TestBuildSanadChain:
    """Tests for build_sanad_chain."""

    def test_happy_path_ingest_extract(self) -> None:
        """INGEST → EXTRACT chain is built for a claim with evidence."""
        result = build_sanad_chain(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            claim_id=CLAIM_ID,
            evidence_items=_make_evidence(),
            extraction_metadata={"deduped": False},
        )

        assert result["tenant_id"] == TENANT_ID
        assert result["deal_id"] == DEAL_ID
        assert result["claim_id"] == CLAIM_ID
        assert result["primary_evidence_id"] == "ev-001"
        assert result["sanad_id"]
        assert result["created_at"]

        chain = result["transmission_chain"]
        assert len(chain) == 2

        ingest_node = chain[0]
        assert ingest_node["node_type"] == NODE_TYPE_INGEST
        assert ingest_node["prev_node_id"] is None

        extract_node = chain[1]
        assert extract_node["node_type"] == NODE_TYPE_EXTRACT
        assert extract_node["prev_node_id"] == ingest_node["node_id"]

        # Monotonic timestamps
        assert extract_node["timestamp"] > ingest_node["timestamp"]

    def test_fail_closed_on_empty_evidence(self) -> None:
        """Empty evidence_items raises ChainBuildError (fail-closed)."""
        with pytest.raises(ChainBuildError) as exc_info:
            build_sanad_chain(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                claim_id=CLAIM_ID,
                evidence_items=[],
                extraction_metadata={},
            )

        assert exc_info.value.claim_id == CLAIM_ID
        assert "No evidence" in exc_info.value.reason

    def test_normalize_node_when_deduped(self) -> None:
        """NORMALIZE node inserted between EXTRACT and terminal when deduped=True."""
        result = build_sanad_chain(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            claim_id=CLAIM_ID,
            evidence_items=_make_evidence(),
            extraction_metadata={"deduped": True},
        )

        chain = result["transmission_chain"]
        assert len(chain) == 3

        node_types = [n["node_type"] for n in chain]
        assert node_types == [NODE_TYPE_INGEST, NODE_TYPE_EXTRACT, NODE_TYPE_NORMALIZE]

        # prev_node_id linkage
        assert chain[0]["prev_node_id"] is None
        assert chain[1]["prev_node_id"] == chain[0]["node_id"]
        assert chain[2]["prev_node_id"] == chain[1]["node_id"]

        # Monotonic timestamps
        assert chain[1]["timestamp"] > chain[0]["timestamp"]
        assert chain[2]["timestamp"] > chain[1]["timestamp"]
