"""Scope guardrail tests for Phase 2.8 Sanad creation boundary."""

from __future__ import annotations

import inspect


def test_boundary_service_does_not_import_or_call_forbidden_integrations() -> None:
    import idis.services.methodology.sanad_creation_boundary as boundary
    import idis.services.methodology.sanad_creation_boundary_results as results
    import idis.services.methodology.sanad_creation_boundary_support as support

    source = inspect.getsource(boundary) + inspect.getsource(results) + inspect.getsource(support)
    forbidden = [
        "EvidenceRepo",
        "ClaimsRepository",
        "auto_grade_claims_for_run",
        "ClaimService",
        "InMemoryMethodologyCoverageService",
        "apply_decisions_in_memory",
        "persistence.repositories",
        "ic_bound=True",
        "VERIFIED",
        "claim_action",
        "FastAPI",
        "APIRouter",
        "neo4j",
        "redis",
        "pgvector",
        "requests",
        "httpx",
    ]

    for token in forbidden:
        assert token not in source
