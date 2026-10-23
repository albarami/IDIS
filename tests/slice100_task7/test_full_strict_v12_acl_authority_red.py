"""RED contracts for the reviewed full-strict-v12 ACL authority ruling."""

from __future__ import annotations

import tests.slice100_task7._void_contract_hash_evidence as void_evidence


def test_v11_candidate_identifiers_are_permanently_blocklisted() -> None:
    """The held v11 bytes become documentary evidence only under v12."""

    v11_identifiers = {
        void_evidence.VOID_V11_ARTIFACT_SHA256,
        void_evidence.VOID_V11_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        void_evidence.VOID_V11_PROSPECTIVE_CONTRACT_SHA256,
    }
    assert len(v11_identifiers) == 3
    assert v11_identifiers.issubset(set(void_evidence.VOID_CONTRACT_HASHES))
