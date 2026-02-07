"""Integration tests for Phase 7.A — Postgres persistence cutover.

Covers:
- Memory fallback works when IDIS_DATABASE_URL is unset
- Runs repository: create, get, update_status, cross-tenant isolation
- RunSteps repository: create, get_by_run_id ordering, result_summary, retry_count
- Evidence repository: create, get, get_by_claim, cross-tenant isolation
- Factory functions return correct implementation based on config
- Cross-tenant reads return None/empty (no existence oracle)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.models.run_step import RunStep, StepName, StepStatus
from idis.persistence.repositories.claims import (
    InMemoryEvidenceRepository,
    clear_evidence_in_memory_store,
)
from idis.persistence.repositories.evidence import get_evidence_repository
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
    get_run_steps_repository,
)
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    clear_in_memory_runs_store,
    get_runs_repository,
)

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _clean_stores() -> None:
    """Reset all in-memory stores before each test."""
    clear_in_memory_runs_store()
    clear_run_steps_store()
    clear_evidence_in_memory_store()
    yield  # type: ignore[misc]
    clear_in_memory_runs_store()
    clear_run_steps_store()
    clear_evidence_in_memory_store()


class TestMemoryFallbackWhenPostgresUnset:
    """Memory fallback works when IDIS_DATABASE_URL is unset."""

    def test_runs_factory_returns_in_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Factory returns InMemoryRunsRepository when no DB configured."""
        monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
        repo = get_runs_repository(conn=None, tenant_id=TENANT_A)
        assert isinstance(repo, InMemoryRunsRepository)

    def test_run_steps_factory_returns_in_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Factory returns InMemoryRunStepsRepository when no DB configured."""
        monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
        repo = get_run_steps_repository(conn=None, tenant_id=TENANT_A)
        assert isinstance(repo, InMemoryRunStepsRepository)

    def test_evidence_factory_returns_in_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Factory returns InMemoryEvidenceRepository when no DB configured."""
        monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
        repo = get_evidence_repository(conn=None, tenant_id=TENANT_A)
        assert isinstance(repo, InMemoryEvidenceRepository)


class TestInMemoryRunsRepository:
    """InMemoryRunsRepository CRUD and tenant isolation."""

    def test_create_and_get(self) -> None:
        """Create a run and retrieve it."""
        repo = InMemoryRunsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())

        created = repo.create(run_id=run_id, deal_id=deal_id, mode="SNAPSHOT")
        assert created["run_id"] == run_id
        assert created["status"] == "QUEUED"
        assert created["tenant_id"] == TENANT_A

        fetched = repo.get(run_id)
        assert fetched is not None
        assert fetched["run_id"] == run_id

    def test_update_status(self) -> None:
        """Update run status and finished_at."""
        repo = InMemoryRunsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        repo.create(run_id=run_id, deal_id=str(uuid.uuid4()), mode="SNAPSHOT")

        repo.update_status(run_id, status="SUCCEEDED", finished_at="2026-02-07T18:00:00Z")
        fetched = repo.get(run_id)
        assert fetched is not None
        assert fetched["status"] == "SUCCEEDED"
        assert fetched["finished_at"] == "2026-02-07T18:00:00Z"

    def test_cross_tenant_get_returns_none(self) -> None:
        """Cross-tenant read returns None (no existence oracle)."""
        repo_a = InMemoryRunsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        repo_a.create(run_id=run_id, deal_id=str(uuid.uuid4()), mode="SNAPSHOT")

        repo_b = InMemoryRunsRepository(TENANT_B)
        assert repo_b.get(run_id) is None


class TestInMemoryRunStepsRepository:
    """InMemoryRunStepsRepository CRUD, ordering, and tenant isolation."""

    def _make_step(
        self,
        run_id: str,
        step_name: StepName,
        step_order: int,
        *,
        retry_count: int = 0,
        result_summary: dict[str, Any] | None = None,
    ) -> RunStep:
        """Helper to create a RunStep."""
        return RunStep(
            step_id=str(uuid.uuid4()),
            run_id=run_id,
            tenant_id=TENANT_A,
            step_name=step_name,
            step_order=step_order,
            status=StepStatus.COMPLETED,
            started_at="2026-02-07T18:00:00Z",
            finished_at="2026-02-07T18:01:00Z",
            retry_count=retry_count,
            result_summary=result_summary or {},
        )

    def test_create_and_get_by_run_id_preserves_order(self) -> None:
        """Steps are returned sorted by step_order."""
        repo = InMemoryRunStepsRepository(TENANT_A)
        run_id = str(uuid.uuid4())

        step_calc = self._make_step(run_id, StepName.CALC, 3)
        step_extract = self._make_step(run_id, StepName.EXTRACT, 1)
        step_ingest = self._make_step(run_id, StepName.INGEST_CHECK, 0)

        repo.create(step_calc)
        repo.create(step_extract)
        repo.create(step_ingest)

        steps = repo.get_by_run_id(run_id)
        assert len(steps) == 3
        assert [s.step_order for s in steps] == [0, 1, 3]
        assert [s.step_name for s in steps] == [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.CALC,
        ]

    def test_result_summary_preserved(self) -> None:
        """result_summary survives create/get round-trip."""
        repo = InMemoryRunStepsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        summary = {"created_claim_ids": ["c1", "c2"], "chunk_count": 5}
        step = self._make_step(run_id, StepName.EXTRACT, 1, result_summary=summary)
        repo.create(step)

        steps = repo.get_by_run_id(run_id)
        assert len(steps) == 1
        assert steps[0].result_summary == summary

    def test_retry_count_preserved(self) -> None:
        """retry_count survives create/update round-trip."""
        repo = InMemoryRunStepsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        step = self._make_step(run_id, StepName.EXTRACT, 1, retry_count=3)
        repo.create(step)

        fetched = repo.get_step(run_id, StepName.EXTRACT)
        assert fetched is not None
        assert fetched.retry_count == 3

        fetched.retry_count = 4
        repo.update(fetched)
        updated = repo.get_step(run_id, StepName.EXTRACT)
        assert updated is not None
        assert updated.retry_count == 4

    def test_cross_tenant_get_by_run_id_returns_empty(self) -> None:
        """Cross-tenant read returns empty list (no existence leak)."""
        repo_a = InMemoryRunStepsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        step = self._make_step(run_id, StepName.INGEST_CHECK, 0)
        repo_a.create(step)

        repo_b = InMemoryRunStepsRepository(TENANT_B)
        assert repo_b.get_by_run_id(run_id) == []

    def test_cross_tenant_get_step_returns_none(self) -> None:
        """Cross-tenant get_step returns None (no existence leak)."""
        repo_a = InMemoryRunStepsRepository(TENANT_A)
        run_id = str(uuid.uuid4())
        step = self._make_step(run_id, StepName.INGEST_CHECK, 0)
        repo_a.create(step)

        repo_b = InMemoryRunStepsRepository(TENANT_B)
        assert repo_b.get_step(run_id, StepName.INGEST_CHECK) is None


class TestInMemoryEvidenceRepository:
    """InMemoryEvidenceRepository CRUD and tenant isolation."""

    def test_create_and_get(self) -> None:
        """Create evidence and retrieve it."""
        repo = InMemoryEvidenceRepository(TENANT_A)
        eid = str(uuid.uuid4())
        created = repo.create(
            evidence_id=eid,
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            claim_id=str(uuid.uuid4()),
            source_span_id=str(uuid.uuid4()),
            source_grade="B",
        )
        assert created["evidence_id"] == eid
        assert created["source_grade"] == "B"

        fetched = repo.get(eid)
        assert fetched is not None
        assert fetched["evidence_id"] == eid

    def test_get_by_claim(self) -> None:
        """get_by_claim returns all evidence for a claim."""
        repo = InMemoryEvidenceRepository(TENANT_A)
        claim_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())

        repo.create(
            evidence_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=deal_id,
            claim_id=claim_id,
            source_span_id=str(uuid.uuid4()),
        )
        repo.create(
            evidence_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=deal_id,
            claim_id=claim_id,
            source_span_id=str(uuid.uuid4()),
        )

        results = repo.get_by_claim(claim_id)
        assert len(results) == 2

    def test_cross_tenant_get_returns_none(self) -> None:
        """Cross-tenant read returns None (404 semantics)."""
        repo_a = InMemoryEvidenceRepository(TENANT_A)
        eid = str(uuid.uuid4())
        repo_a.create(
            evidence_id=eid,
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            claim_id=str(uuid.uuid4()),
            source_span_id=str(uuid.uuid4()),
        )

        repo_b = InMemoryEvidenceRepository(TENANT_B)
        assert repo_b.get(eid) is None

    def test_cross_tenant_get_by_claim_returns_empty(self) -> None:
        """Cross-tenant get_by_claim returns empty list."""
        repo_a = InMemoryEvidenceRepository(TENANT_A)
        claim_id = str(uuid.uuid4())
        repo_a.create(
            evidence_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            claim_id=claim_id,
            source_span_id=str(uuid.uuid4()),
        )

        repo_b = InMemoryEvidenceRepository(TENANT_B)
        assert repo_b.get_by_claim(claim_id) == []


class TestRunSurvivesRestartSemantics:
    """Simulate restart by creating data, clearing nothing, and reading back."""

    def test_run_record_survives(self) -> None:
        """Run data persists across repository instantiations (same process)."""
        run_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())

        repo1 = InMemoryRunsRepository(TENANT_A)
        repo1.create(run_id=run_id, deal_id=deal_id, mode="SNAPSHOT")

        repo2 = InMemoryRunsRepository(TENANT_A)
        fetched = repo2.get(run_id)
        assert fetched is not None
        assert fetched["run_id"] == run_id

    def test_run_steps_survive_with_result_summary(self) -> None:
        """RunStep data with result_summary persists across repo instantiations."""
        run_id = str(uuid.uuid4())
        summary = {"calc_ids": ["c1"], "reproducibility_hashes": ["h1"]}
        step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id=run_id,
            tenant_id=TENANT_A,
            step_name=StepName.CALC,
            step_order=3,
            status=StepStatus.COMPLETED,
            result_summary=summary,
        )

        repo1 = InMemoryRunStepsRepository(TENANT_A)
        repo1.create(step)

        repo2 = InMemoryRunStepsRepository(TENANT_A)
        steps = repo2.get_by_run_id(run_id)
        assert len(steps) == 1
        assert steps[0].result_summary == summary

    def test_deterministic_ordering_preserved(self) -> None:
        """step_order determines ordering across repo instantiations."""
        run_id = str(uuid.uuid4())

        repo1 = InMemoryRunStepsRepository(TENANT_A)
        for name, order in [
            (StepName.CALC, 3),
            (StepName.INGEST_CHECK, 0),
            (StepName.EXTRACT, 1),
        ]:
            repo1.create(
                RunStep(
                    step_id=str(uuid.uuid4()),
                    run_id=run_id,
                    tenant_id=TENANT_A,
                    step_name=name,
                    step_order=order,
                    status=StepStatus.COMPLETED,
                )
            )

        repo2 = InMemoryRunStepsRepository(TENANT_A)
        steps = repo2.get_by_run_id(run_id)
        assert [s.step_order for s in steps] == [0, 1, 3]

    def test_retry_tracking_preserved(self) -> None:
        """retry_count survives across repo instantiations."""
        run_id = str(uuid.uuid4())
        step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id=run_id,
            tenant_id=TENANT_A,
            step_name=StepName.EXTRACT,
            step_order=1,
            status=StepStatus.COMPLETED,
            retry_count=5,
        )

        repo1 = InMemoryRunStepsRepository(TENANT_A)
        repo1.create(step)

        repo2 = InMemoryRunStepsRepository(TENANT_A)
        fetched = repo2.get_step(run_id, StepName.EXTRACT)
        assert fetched is not None
        assert fetched.retry_count == 5
