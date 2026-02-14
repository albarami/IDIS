"""Regression tests for Postgres persistence path wiring.

Verifies that when db_conn is provided, the pipeline step functions
construct Postgres-aware repositories and services instead of falling
back to in-memory stores.

These tests use mocking to verify constructor calls without requiring
a real Postgres connection.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class TestRetrieveClaimsForDebateWiring:
    """_retrieve_claims_for_debate uses correct repos based on db_conn."""

    def test_uses_inmemory_when_db_conn_is_none(self) -> None:
        """Without db_conn, InMemory repos are used."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        result = _retrieve_claims_for_debate(TENANT, [], db_conn=None)
        assert result == []

    def test_uses_postgres_when_db_conn_provided(self) -> None:
        """With db_conn, Postgres ClaimsRepository is constructed."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        mock_conn = MagicMock()
        mock_claims_cls = MagicMock()
        mock_claims_cls.return_value = MagicMock(get=MagicMock(return_value=None))
        mock_sanads_cls = MagicMock()
        mock_sanads_cls.return_value = MagicMock()

        with (
            patch(
                "idis.persistence.repositories.claims.ClaimsRepository",
                mock_claims_cls,
            ),
            patch(
                "idis.persistence.repositories.claims.SanadsRepository",
                mock_sanads_cls,
            ),
        ):
            _retrieve_claims_for_debate(TENANT, ["claim-1"], db_conn=mock_conn)

            mock_claims_cls.assert_called_once_with(mock_conn, TENANT)
            mock_sanads_cls.assert_called_once_with(mock_conn, TENANT)


class TestAutoGradeDbConnWiring:
    """auto_grade_claims_for_run passes db_conn to repos/services."""

    def test_defaults_to_inmemory_when_db_conn_is_none(self) -> None:
        """Without db_conn, InMemory repos are used (existing behavior)."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.services.sanad.auto_grade import auto_grade_claims_for_run

        result = auto_grade_claims_for_run(
            run_id="run-1",
            tenant_id=TENANT,
            deal_id="deal-1",
            created_claim_ids=[],
            audit_sink=InMemoryAuditSink(),
            db_conn=None,
        )
        assert result.graded_count == 0
        assert result.failed_count == 0

    def test_passes_db_conn_to_sanad_service(self) -> None:
        """With db_conn, SanadService receives it."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.services.sanad.auto_grade import auto_grade_claims_for_run

        mock_conn = MagicMock()
        sink = InMemoryAuditSink()

        with patch("idis.services.sanad.auto_grade.SanadService") as mock_sanad_svc:
            mock_sanad_svc.return_value = MagicMock()

            auto_grade_claims_for_run(
                run_id="run-1",
                tenant_id=TENANT,
                deal_id="deal-1",
                created_claim_ids=[],
                audit_sink=sink,
                db_conn=mock_conn,
            )

            mock_sanad_svc.assert_called_once_with(
                tenant_id=TENANT, db_conn=mock_conn, audit_sink=sink
            )

    def test_passes_db_conn_to_defect_service(self) -> None:
        """With db_conn, DefectService receives it."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.services.sanad.auto_grade import auto_grade_claims_for_run

        mock_conn = MagicMock()
        sink = InMemoryAuditSink()

        with patch("idis.services.sanad.auto_grade.DefectService") as mock_defect_svc:
            mock_defect_svc.return_value = MagicMock()

            auto_grade_claims_for_run(
                run_id="run-1",
                tenant_id=TENANT,
                deal_id="deal-1",
                created_claim_ids=[],
                audit_sink=sink,
                db_conn=mock_conn,
            )

            mock_defect_svc.assert_called_once_with(
                tenant_id=TENANT, db_conn=mock_conn, audit_sink=sink
            )

    def test_passes_db_conn_to_evidence_factory(self) -> None:
        """With db_conn, get_evidence_repository is called with conn."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.services.sanad.auto_grade import auto_grade_claims_for_run

        mock_conn = MagicMock()
        sink = InMemoryAuditSink()

        with patch("idis.services.sanad.auto_grade.get_evidence_repository") as mock_ev_factory:
            mock_ev_factory.return_value = MagicMock()

            auto_grade_claims_for_run(
                run_id="run-1",
                tenant_id=TENANT,
                deal_id="deal-1",
                created_claim_ids=[],
                audit_sink=sink,
                db_conn=mock_conn,
            )

            mock_ev_factory.assert_called_once_with(mock_conn, TENANT)


class TestClaimsUpdateGrade:
    """update_grade method works for both InMemory and Postgres repos."""

    def test_inmemory_update_grade(self) -> None:
        """InMemory update_grade modifies the stored claim."""
        from idis.persistence.repositories.claims import (
            InMemoryClaimsRepository,
            clear_claims_in_memory_store,
        )

        clear_claims_in_memory_store()
        repo = InMemoryClaimsRepository(TENANT)
        repo.create(
            claim_id="c1",
            deal_id="d1",
            claim_class="FINANCIAL",
            claim_text="Revenue is $10M",
            predicate=None,
            value=None,
            sanad_id=None,
            claim_grade="D",
            corroboration={"level": "AHAD", "independent_chain_count": 1},
            claim_verdict="UNVERIFIED",
            claim_action="VERIFY",
            defect_ids=[],
            materiality="MEDIUM",
            ic_bound=False,
            primary_span_id=None,
        )

        repo.update_grade("c1", claim_grade="B", sanad_id="s1")

        claim = repo.get("c1")
        assert claim is not None
        assert claim["claim_grade"] == "B"
        assert claim["sanad_id"] == "s1"

        clear_claims_in_memory_store()

    def test_inmemory_update_grade_partial(self) -> None:
        """InMemory update_grade can update only grade or only sanad_id."""
        from idis.persistence.repositories.claims import (
            InMemoryClaimsRepository,
            clear_claims_in_memory_store,
        )

        clear_claims_in_memory_store()
        repo = InMemoryClaimsRepository(TENANT)
        repo.create(
            claim_id="c2",
            deal_id="d1",
            claim_class="FINANCIAL",
            claim_text="Revenue is $10M",
            predicate=None,
            value=None,
            sanad_id=None,
            claim_grade="D",
            corroboration={"level": "AHAD", "independent_chain_count": 1},
            claim_verdict="UNVERIFIED",
            claim_action="VERIFY",
            defect_ids=[],
            materiality="MEDIUM",
            ic_bound=False,
            primary_span_id=None,
        )

        repo.update_grade("c2", claim_grade="A")
        claim = repo.get("c2")
        assert claim is not None
        assert claim["claim_grade"] == "A"
        assert claim["sanad_id"] is None

        clear_claims_in_memory_store()

    def test_inmemory_update_grade_cross_tenant_noop(self) -> None:
        """Cross-tenant update_grade is silently ignored."""
        from idis.persistence.repositories.claims import (
            InMemoryClaimsRepository,
            clear_claims_in_memory_store,
        )

        clear_claims_in_memory_store()
        other_tenant = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        repo_a = InMemoryClaimsRepository(TENANT)
        repo_a.create(
            claim_id="c3",
            deal_id="d1",
            claim_class="FINANCIAL",
            claim_text="Revenue is $10M",
            predicate=None,
            value=None,
            sanad_id=None,
            claim_grade="D",
            corroboration={"level": "AHAD", "independent_chain_count": 1},
            claim_verdict="UNVERIFIED",
            claim_action="VERIFY",
            defect_ids=[],
            materiality="MEDIUM",
            ic_bound=False,
            primary_span_id=None,
        )

        repo_b = InMemoryClaimsRepository(other_tenant)
        repo_b.update_grade("c3", claim_grade="A")

        claim = repo_a.get("c3")
        assert claim is not None
        assert claim["claim_grade"] == "D"

        clear_claims_in_memory_store()

    def test_inmemory_update_grade_missing_claim_noop(self) -> None:
        """update_grade on non-existent claim is silently ignored."""
        from idis.persistence.repositories.claims import (
            InMemoryClaimsRepository,
            clear_claims_in_memory_store,
        )

        clear_claims_in_memory_store()
        repo = InMemoryClaimsRepository(TENANT)
        repo.update_grade("nonexistent", claim_grade="A")

        clear_claims_in_memory_store()


class TestSnapshotExtractionDbConn:
    """_run_snapshot_extraction passes db_conn to ClaimService."""

    def test_passes_db_conn_to_claim_service(self) -> None:
        """ClaimService is constructed with db_conn when provided."""
        mock_conn = MagicMock()

        with (
            patch("idis.services.claims.service.ClaimService") as mock_claim_svc,
            patch("idis.api.routes.runs._get_extraction_prompt", return_value="prompt"),
            patch(
                "idis.api.routes.runs._get_extraction_output_schema",
                return_value={},
            ),
            patch("idis.api.routes.runs._build_extraction_llm_client"),
            patch("idis.services.extraction.pipeline.ExtractionPipeline.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                status="COMPLETED",
                created_claim_ids=["c1"],
                chunk_count=1,
                unique_claim_count=1,
                conflict_count=0,
            )

            from idis.api.routes.runs import _run_snapshot_extraction

            _run_snapshot_extraction(
                run_id="r1",
                tenant_id=TENANT,
                deal_id="d1",
                documents=[{"doc_type": "TERM_SHEET", "spans": []}],
                db_conn=mock_conn,
            )

            mock_claim_svc.assert_called_once()
            call_kwargs = mock_claim_svc.call_args
            assert call_kwargs.kwargs.get("db_conn") is mock_conn

    def test_db_conn_none_when_not_provided(self) -> None:
        """ClaimService gets db_conn=None when no connection."""
        with (
            patch("idis.services.claims.service.ClaimService") as mock_claim_svc,
            patch("idis.api.routes.runs._get_extraction_prompt", return_value="prompt"),
            patch(
                "idis.api.routes.runs._get_extraction_output_schema",
                return_value={},
            ),
            patch("idis.api.routes.runs._build_extraction_llm_client"),
            patch("idis.services.extraction.pipeline.ExtractionPipeline.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                status="COMPLETED",
                created_claim_ids=[],
                chunk_count=0,
                unique_claim_count=0,
                conflict_count=0,
            )

            from idis.api.routes.runs import _run_snapshot_extraction

            _run_snapshot_extraction(
                run_id="r1",
                tenant_id=TENANT,
                deal_id="d1",
                documents=[{"doc_type": "TERM_SHEET", "spans": []}],
            )

            mock_claim_svc.assert_called_once()
            call_kwargs = mock_claim_svc.call_args
            assert call_kwargs.kwargs.get("db_conn") is None


class TestAutoGradeDbConnBackwardCompat:
    """auto_grade_claims_for_run backward-compatible without db_conn."""

    def test_works_without_db_conn_kwarg(self) -> None:
        """Callers that don't pass db_conn still work (defaults to None)."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.services.sanad.auto_grade import auto_grade_claims_for_run

        result = auto_grade_claims_for_run(
            run_id="run-1",
            tenant_id=TENANT,
            deal_id="deal-1",
            created_claim_ids=[],
            audit_sink=InMemoryAuditSink(),
        )
        assert result.graded_count == 0


class TestMigration0011RlsPolicy:
    """Migration 0011 uses correct RLS setting name."""

    def test_uses_idis_tenant_id_setting(self) -> None:
        """RLS policy references idis.tenant_id, not app.current_tenant."""
        from pathlib import Path

        migration_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "idis"
            / "persistence"
            / "migrations"
            / "versions"
            / "0011_enrichment_credentials.py"
        )
        content = migration_path.read_text(encoding="utf-8")

        assert "idis.tenant_id" in content
        assert "app.current_tenant" not in content
