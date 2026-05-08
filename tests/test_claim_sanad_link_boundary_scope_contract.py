"""Strict non-scope tests for Phase 2.9 Claim-Sanad link boundary."""

from __future__ import annotations

import inspect


def test_claim_sanad_link_boundary_uses_only_sanctioned_service_path() -> None:
    import idis.services.methodology.claim_sanad_link_boundary as boundary
    import idis.services.methodology.claim_sanad_link_boundary_support as support

    source = inspect.getsource(boundary) + inspect.getsource(support)
    forbidden = [
        "ClaimsRepository",
        "InMemoryClaimsRepository",
        "SanadsRepository",
        "InMemorySanadsRepository",
        "persistence.repositories",
        "auto_grade_claims_for_run",
        "update_grade",
        "SanadService",
        "InMemoryMethodologyCoverageService",
        "apply_decisions_in_memory",
        "FastAPI",
        "APIRouter",
        "neo4j",
        "redis",
        "pgvector",
        "requests",
        "httpx",
        "ic_bound=True",
        "claim_verdict=VERIFIED",
        "claim_action=NONE",
    ]

    for token in forbidden:
        assert token not in source


def test_claim_sanad_link_boundary_calls_claim_service_update_only() -> None:
    import idis.services.methodology.claim_sanad_link_boundary as boundary
    import idis.services.methodology.claim_sanad_link_boundary_support as support

    source = inspect.getsource(boundary) + inspect.getsource(support)

    assert "UpdateClaimInput" in source
    assert ".update(" in source
    assert ".create(" not in source
    assert "claim_grade=" not in source
    assert "claim_verdict=" not in source
    assert "claim_action=" not in source
    assert "ic_bound=" not in source
