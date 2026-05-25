"""Infrastructure wiring baseline tests for Phase 2.0."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import collect_wiring_inventory

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_and_docker_postgres_are_wired_but_supabase_is_target_only() -> None:
    """Postgres is real runtime wiring; Supabase is only a possible Postgres target."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["postgres"].status == "WIRED"
    assert inventory["docker_postgres"].status == "WIRED"
    assert inventory["supabase"].status == "CONFIG_ONLY"
    assert any("no Supabase SDK" in item for item in inventory["supabase"].gaps)


def test_neo4j_projection_is_partially_full_wired_but_health_gated() -> None:
    """Neo4j graph code is FULL-wired only through the Slice61 gated graph step."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    neo4j = inventory["neo4j_graph"]

    assert neo4j.status == "PARTIAL"
    assert any("GraphProjectionService" in item for item in neo4j.evidence)
    assert any("not proven live" in item.lower() or "health" in item.lower() for item in neo4j.gaps)


def test_redis_and_pgvector_are_configured_without_runtime_use() -> None:
    """Redis is config-only; pgvector foundation exists but FULL RAG is not wired."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["redis"].status == "CONFIG_ONLY"
    assert any("not consumed by runtime code" in item for item in inventory["redis"].gaps)
    assert inventory["rag_vector_retrieval"].status == "PARTIAL"
    assert any(
        "FULL run indexing step" in item or "product bundle" in item
        for item in inventory["rag_vector_retrieval"].gaps
    )


def test_object_storage_is_filesystem_wired_only() -> None:
    """Object storage should validate the filesystem path without claiming cloud storage."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    storage = inventory["object_storage"]

    assert storage.status == "WIRED"
    assert any("FilesystemObjectStore" in item for item in storage.evidence)
    assert any("Supabase storage is not wired" in item for item in storage.gaps)
