"""Slice93 Task 2 — migration 0022 + safe-shape durable rows + Layer-2 twin repositories.

DEC-B (locked): persist the SAFE shape of the Layer-2 IC challenge output — IDs, categories
(finding_type/severity), counts, and reference id lists only; never claim text, transcripts,
prompt text, or raw model output. Mirrors the Slice92 Layer-1 durability template (RLS NULLIF
policy, Postgres/InMemory twin, deterministic idempotent upserts).

Id-shape gate (verified in Task 1): the challenge_id is a bare UUID5 (-> UUID column) but the
finding_id is a prefixed / LLM-supplied string (``layer2-finding-…``, -> VARCHAR), so the
findings table keys on the composite (tenant_id, run_id, finding_id) — a bare finding_id key
could collide across tenants/runs, and the column must be text, not UUID.

Scope boundary: NO step wiring, NO memo/QA feed, NO strict provenance — later tasks.
The Postgres roundtrip is env-gated (skips without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL;
runs in the postgres-integration CI job where `alembic upgrade head` applies 0022).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from idis.models.layer2_ic_challenge import (
    Layer2ICChallengeFinding,
    Layer2ICChallengeRecord,
    Layer2ICChallengeStatus,
    deterministic_layer2_ic_challenge_id,
)

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"
DEAL_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "22222222-2222-2222-2222-222222222222"
OTHER_RUN_ID = "44444444-4444-4444-4444-444444444444"
DEBATE_ID = "55555555-5555-5555-5555-555555555555"
CLAIM_ID = "claim_mth_0123456789abcdef01234567"
CALC_ID = "calc-1"
# Prefixed / LLM-supplied — NOT a UUID (the Task 1 id-shape gate).
FINDING_ID = "layer2-finding-abc12345"

_MIGRATION = Path("src/idis/persistence/migrations/versions/0022_layer2_ic_challenge_durability.py")

CHALLENGE_ID = deterministic_layer2_ic_challenge_id(
    tenant_id=TENANT_ID,
    deal_id=DEAL_ID,
    run_id=RUN_ID,
    debate_id=DEBATE_ID,
    claim_ids=[CLAIM_ID],
    calc_ids=[CALC_ID],
)

_CHALLENGE_SAFE_SUMMARY_KEYS = {
    "claim_ids",
    "calc_ids",
    "graph_ref_ids",
    "rag_ref_ids",
    "enrichment_ref_ids",
    "finding_ids",
    "finding_count",
    "unresolved_question_count",
    "muhasabah_passed",
    "by_finding_type",
    "by_severity",
    "by_category",
    "stage_emphasis",
}


def _finding(**overrides: Any) -> Layer2ICChallengeFinding:
    kwargs: dict[str, Any] = {
        "finding_id": FINDING_ID,
        "finding_type": "market_risk",
        "severity": "high",
        "category": "market_risk",
        "supported_claim_ids": [CLAIM_ID],
        "supported_calc_ids": [CALC_ID],
        "graph_ref_ids": ["graph-ref-1"],
        "rag_ref_ids": ["rag-ref-1"],
        "enrichment_ref_ids": ["enrich-ref-1"],
    }
    kwargs.update(overrides)
    return Layer2ICChallengeFinding(**kwargs)


def _record(**overrides: Any) -> Layer2ICChallengeRecord:
    kwargs: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "layer2_challenge_id": CHALLENGE_ID,
        "source_debate_id": DEBATE_ID,
        "status": Layer2ICChallengeStatus.COMPLETED,
        "claim_ids": [CLAIM_ID],
        "calc_ids": [CALC_ID],
        "graph_ref_ids": ["graph-ref-1"],
        "rag_ref_ids": ["rag-ref-1"],
        "enrichment_ref_ids": ["enrich-ref-1"],
        "findings": [_finding()],
        "unresolved_question_count": 2,
        "muhasabah_passed": True,
    }
    kwargs.update(overrides)
    return Layer2ICChallengeRecord(**kwargs)


@pytest.fixture(autouse=True)
def _clear_store() -> None:
    from idis.persistence.repositories.layer2_challenge import (
        clear_in_memory_layer2_challenge_store,
    )

    clear_in_memory_layer2_challenge_store()


# --- Migration 0022: two tables, RLS, composite finding key, text finding_id ---


def test_migration_0022_layer2_tables_with_rls_and_keys() -> None:
    assert _MIGRATION.exists()
    source = _MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0022"' in source
    assert 'down_revision = "0021"' in source
    for table in ("layer2_ic_challenges", "layer2_ic_findings"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in source
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in source
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY tenant_isolation_{table} ON {table}" in source
        assert f"DROP TABLE IF EXISTS {table} CASCADE" in source
    assert "NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL" in source
    # challenge_id is a bare UUID5 -> UUID PK.
    assert "challenge_id UUID PRIMARY KEY" in source
    # finding_id is prefixed / LLM-supplied -> VARCHAR, keyed compositely with tenant+run.
    assert "finding_id VARCHAR(255) NOT NULL" in source
    assert "PRIMARY KEY (tenant_id, run_id, finding_id)" in source
    # status enum values are lowercase.
    assert "status IN ('completed', 'blocked')" in source
    # Durable challenge category is a safe NOT NULL text column (Task 6).
    assert "category VARCHAR(40) NOT NULL" in source


# --- Safe-shape rows: whitelist conversion from the existing in-memory records ---


def test_challenge_row_from_record_whitelists_safe_summary() -> None:
    from idis.models.layer2_durability import Layer2ChallengeRow

    row = Layer2ChallengeRow.from_record(_record())
    assert row.tenant_id == TENANT_ID
    assert row.deal_id == DEAL_ID
    assert row.run_id == RUN_ID
    assert row.challenge_id == CHALLENGE_ID
    assert row.source_debate_id == DEBATE_ID
    assert row.status == "completed"
    assert set(row.safe_summary) == _CHALLENGE_SAFE_SUMMARY_KEYS
    assert row.safe_summary["finding_ids"] == [FINDING_ID]
    assert row.safe_summary["finding_count"] == 1
    assert row.safe_summary["unresolved_question_count"] == 2
    assert row.safe_summary["by_finding_type"] == {"market_risk": 1}
    assert row.safe_summary["by_severity"] == {"high": 1}
    # Durable category taxonomy + scorecard-safe stage emphasis (Task 6).
    assert row.safe_summary["by_category"] == {"market_risk": 1}
    stage_emphasis = row.safe_summary["stage_emphasis"]
    assert stage_emphasis["stage"] == "SEED"
    assert "market_risk" in stage_emphasis["weighted_by_category"]


def test_finding_row_from_finding_keeps_safe_fields_only() -> None:
    from idis.models.layer2_durability import Layer2FindingRow

    row = Layer2FindingRow.from_finding(
        _finding(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        challenge_id=CHALLENGE_ID,
    )
    assert row.finding_id == FINDING_ID  # prefixed string preserved
    assert row.challenge_id == CHALLENGE_ID
    assert row.finding_type == "market_risk"
    assert row.severity == "high"
    assert row.category == "market_risk"  # durable category preserved (Task 6)
    assert row.supported_claim_ids == [CLAIM_ID]
    assert row.supported_calc_ids == [CALC_ID]
    assert row.graph_ref_ids == ["graph-ref-1"]
    assert row.rag_ref_ids == ["rag-ref-1"]
    assert row.enrichment_ref_ids == ["enrich-ref-1"]


# --- InMemory twin: idempotent upserts, tenant scoping, composite finding key ---


def test_in_memory_repo_upsert_idempotent_and_composite_finding_key() -> None:
    from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
    from idis.persistence.repositories.layer2_challenge import (
        InMemoryLayer2ChallengeRepository,
    )

    repo = InMemoryLayer2ChallengeRepository(TENANT_ID)
    challenge_row = Layer2ChallengeRow.from_record(_record())
    finding_row = Layer2FindingRow.from_finding(
        _finding(), tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID, challenge_id=CHALLENGE_ID
    )
    for _ in range(2):
        repo.upsert_challenge(challenge_row)
        repo.upsert_finding(finding_row)

    assert [c["challenge_id"] for c in repo.list_challenges(run_id=RUN_ID)] == [CHALLENGE_ID]
    assert [f["finding_id"] for f in repo.list_findings(run_id=RUN_ID)] == [FINDING_ID]

    # Same finding_id under a DIFFERENT run survives independently (composite key).
    other = Layer2FindingRow.from_finding(
        _finding(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=OTHER_RUN_ID,
        challenge_id=CHALLENGE_ID,
    )
    repo.upsert_finding(other)
    first = repo.list_findings(run_id=RUN_ID)
    second = repo.list_findings(run_id=OTHER_RUN_ID)
    assert [f["finding_id"] for f in first] == [FINDING_ID]
    assert [f["finding_id"] for f in second] == [FINDING_ID]
    assert first[0]["run_id"] == RUN_ID
    assert second[0]["run_id"] == OTHER_RUN_ID

    # Another tenant sees nothing.
    assert InMemoryLayer2ChallengeRepository(OTHER_TENANT_ID).list_challenges(run_id=RUN_ID) == []


def test_finding_row_preserves_category_value_from_enum() -> None:
    from idis.models.layer2_durability import Layer2FindingRow
    from idis.models.layer2_ic_challenge import Layer2ChallengeCategory

    finding = _finding(category=Layer2ChallengeCategory.TEAM_RISK)
    row = Layer2FindingRow.from_finding(
        finding, tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID, challenge_id=CHALLENGE_ID
    )
    # The durable row carries the bounded category *value* (never the enum object / free text).
    assert row.category == Layer2ChallengeCategory.TEAM_RISK.value
    assert row.category == "team_risk"


def test_in_memory_repo_roundtrip_preserves_finding_category() -> None:
    from idis.models.layer2_durability import Layer2FindingRow
    from idis.persistence.repositories.layer2_challenge import (
        InMemoryLayer2ChallengeRepository,
    )

    repo = InMemoryLayer2ChallengeRepository(TENANT_ID)
    repo.upsert_finding(
        Layer2FindingRow.from_finding(
            _finding(category="team_risk"),
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            challenge_id=CHALLENGE_ID,
        )
    )
    rows = repo.list_findings(run_id=RUN_ID)
    assert [row["category"] for row in rows] == ["team_risk"]


def test_in_memory_repo_rejects_tenant_mismatch() -> None:
    from idis.models.layer2_durability import Layer2ChallengeRow
    from idis.persistence.repositories.layer2_challenge import (
        InMemoryLayer2ChallengeRepository,
    )

    repo = InMemoryLayer2ChallengeRepository(OTHER_TENANT_ID)
    with pytest.raises(ValueError):
        repo.upsert_challenge(Layer2ChallengeRow.from_record(_record()))


def test_get_layer2_challenge_repository_falls_back_in_memory() -> None:
    from idis.persistence.repositories.layer2_challenge import (
        InMemoryLayer2ChallengeRepository,
        get_layer2_challenge_repository,
    )

    assert isinstance(
        get_layer2_challenge_repository(None, TENANT_ID), InMemoryLayer2ChallengeRepository
    )


# --- Postgres roundtrip (env-gated; both tables; production-shaped ids) ---


def test_postgres_layer2_repo_roundtrip(app_engine: Any, migrated_db: Any) -> None:
    from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
    from idis.persistence.repositories.layer2_challenge import (
        PostgresLayer2ChallengeRepository,
    )

    challenge_row = Layer2ChallengeRow.from_record(_record())
    finding_row = Layer2FindingRow.from_finding(
        _finding(), tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID, challenge_id=CHALLENGE_ID
    )
    with app_engine.begin() as conn:
        repo = PostgresLayer2ChallengeRepository(conn, TENANT_ID)
        for _ in range(2):  # idempotent
            assert repo.upsert_challenge(challenge_row)["challenge_id"] == CHALLENGE_ID
            assert repo.upsert_finding(finding_row)["finding_id"] == FINDING_ID
        challenges = repo.list_challenges(run_id=RUN_ID)
        findings = repo.list_findings(run_id=RUN_ID)
        assert [c["challenge_id"] for c in challenges] == [CHALLENGE_ID]
        assert challenges[0]["safe_summary"]["by_finding_type"] == {"market_risk": 1}
        assert [f["finding_id"] for f in findings] == [FINDING_ID]
        assert findings[0]["finding_type"] == "market_risk"
        assert findings[0]["category"] == "market_risk"  # durable category roundtrips (Task 6)
        assert challenges[0]["safe_summary"]["by_category"] == {"market_risk": 1}
        assert findings[0]["supported_claim_ids"] == [CLAIM_ID]

    # RLS: a different tenant sees neither table's rows.
    with app_engine.begin() as conn:
        other = PostgresLayer2ChallengeRepository(conn, OTHER_TENANT_ID)
        assert other.list_challenges(run_id=RUN_ID) == []
        assert other.list_findings(run_id=RUN_ID) == []
