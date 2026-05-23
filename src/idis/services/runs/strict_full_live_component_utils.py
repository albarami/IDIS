"""Reusable strict full-live component readiness constructors."""

from __future__ import annotations

from collections.abc import Mapping

from idis.services.runs.strict_full_live_models import (
    StrictComponentReadiness,
    StrictComponentStatus,
)


def graph_evidence_layer() -> StrictComponentReadiness:
    """Report Neo4j graph code as present but not FULL-wired."""
    return StrictComponentReadiness(
        component_name="graph_evidence_layer",
        status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
        blocker_message=(
            "GraphProjectionService and Neo4j repository code exist, but FULL does not call "
            "the graph projection or graph retrieval paths."
        ),
        required_env_vars=["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"],
        required_services=["Neo4j"],
        evidence="src/idis/persistence/graph_consistency.py:GraphProjectionService",
        may_proceed=False,
        mode="code-exists-but-not-wired",
        provenance={"provider": "neo4j", "fallback": "none"},
    )


def product_export_bundle() -> StrictComponentReadiness:
    """Report VC export as present but not product-wired."""
    return StrictComponentReadiness(
        component_name="product_export_bundle",
        status=StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED,
        blocker_message=(
            "Product export primitives exist, but strict VC export is not product-wired from "
            "strict-live run outputs."
        ),
        required_env_vars=[],
        required_services=["product deliverable export storage/path"],
        evidence=(
            "src/idis/deliverables/exporter.py; docs/architecture/strict_full_live_readiness.md"
        ),
        may_proceed=False,
        mode="code-exists-but-not-wired",
        provenance={"provider": "product-export", "fallback": "none"},
    )


def not_implemented(
    component_name: str,
    blocker_message: str,
    evidence: str,
) -> StrictComponentReadiness:
    """Build a not-implemented strict blocker."""
    return StrictComponentReadiness(
        component_name=component_name,
        status=StrictComponentStatus.NOT_IMPLEMENTED,
        blocker_message=blocker_message,
        required_env_vars=[],
        required_services=[],
        evidence=evidence,
        may_proceed=False,
        mode="not-implemented",
        provenance={"provider": "none", "fallback": "none"},
    )


def live(
    component_name: str,
    evidence: str,
    *,
    provenance: Mapping[str, str] | None = None,
) -> StrictComponentReadiness:
    """Build a live strict component."""
    return StrictComponentReadiness(
        component_name=component_name,
        status=StrictComponentStatus.LIVE_WIRED_AND_USED,
        blocker_message="",
        required_env_vars=[],
        required_services=[],
        evidence=evidence,
        may_proceed=True,
        mode="live",
        provenance=dict(provenance or {"provider": "deterministic", "fallback": "none"}),
    )
