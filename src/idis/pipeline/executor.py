"""Pipeline executor for IDIS runs.

Implements the core pipeline execution logic for processing deals:
1. Document ingestion and parsing
2. Claim extraction with Sanad chains
3. Defect detection
4. Deliverable generation

For Phase 6.3 demo, uses GDBS synthetic data to populate claims/sanads.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from idis.testing.gdbs_loader import GDBSDeal, GDBSLoader

logger = logging.getLogger(__name__)


class PipelineExecutor:
    """Executes pipeline runs for deals."""

    def __init__(self, db_conn: Any, gdbs_path: str | None = None) -> None:
        """Initialize executor.

        Args:
            db_conn: Database connection for persistence.
            gdbs_path: Path to GDBS dataset (optional, for loading synthetic claims).
        """
        self._db_conn = db_conn
        self._gdbs_path = gdbs_path
        self._gdbs_deals: dict[str, GDBSDeal] = {}

        if gdbs_path:
            loader = GDBSLoader(gdbs_path)
            dataset = loader.load()
            for deal in dataset.deals:
                self._gdbs_deals[deal.company_name] = deal

    async def execute_run(self, run_id: str, deal_id: str, mode: str, tenant_id: str) -> None:
        """Execute a pipeline run.

        Args:
            run_id: UUID of the run to execute.
            deal_id: UUID of the deal being processed.
            mode: Run mode (SNAPSHOT or FULL).
            tenant_id: Tenant UUID.
        """
        logger.info(
            f"Starting pipeline run {run_id} for deal {deal_id} in mode {mode}",
            extra={"run_id": run_id, "deal_id": deal_id, "mode": mode},
        )

        try:
            # Update status to RUNNING
            await self._update_run_status(run_id, "RUNNING")

            # Get deal info
            deal_name = await self._get_deal_name(deal_id, tenant_id)

            # Load claims from GDBS if available
            if deal_name in self._gdbs_deals:
                gdbs_deal = self._gdbs_deals[deal_name]
                await self._load_gdbs_claims(run_id, deal_id, tenant_id, gdbs_deal)
            else:
                logger.warning(f"Deal {deal_name} not found in GDBS, skipping claim loading")

            # Update status to SUCCEEDED
            await self._update_run_status(run_id, "SUCCEEDED")

            logger.info(
                f"Pipeline run {run_id} completed successfully",
                extra={"run_id": run_id, "deal_id": deal_id},
            )

        except Exception as e:
            logger.error(
                f"Pipeline run {run_id} failed: {e}",
                extra={"run_id": run_id, "deal_id": deal_id, "error": str(e)},
                exc_info=True,
            )
            await self._update_run_status(run_id, "FAILED")
            raise

    async def _update_run_status(self, run_id: str, status: str) -> None:
        """Update run status in database.

        Args:
            run_id: UUID of the run.
            status: New status (RUNNING, SUCCEEDED, FAILED).
        """
        from sqlalchemy import text

        now = datetime.now(UTC)

        if status == "RUNNING":
            query = text("""
                UPDATE runs
                SET status = :status, started_at = :started_at
                WHERE run_id = :run_id
            """)
            await asyncio.to_thread(
                self._db_conn.execute,
                query,
                {"status": status, "started_at": now, "run_id": run_id},
            )
        elif status in ("SUCCEEDED", "FAILED"):
            query = text("""
                UPDATE runs
                SET status = :status, finished_at = :finished_at
                WHERE run_id = :run_id
            """)
            await asyncio.to_thread(
                self._db_conn.execute,
                query,
                {"status": status, "finished_at": now, "run_id": run_id},
            )

    async def _get_deal_name(self, deal_id: str, tenant_id: str) -> str:
        """Get deal name from database.

        Args:
            deal_id: UUID of the deal.
            tenant_id: Tenant UUID.

        Returns:
            Deal name.
        """
        from sqlalchemy import text

        query = text("""
            SELECT name
            FROM deals
            WHERE deal_id = :deal_id AND tenant_id = :tenant_id
        """)
        result = await asyncio.to_thread(
            self._db_conn.execute, query, {"deal_id": deal_id, "tenant_id": tenant_id}
        )
        row = await asyncio.to_thread(result.fetchone)

        if not row:
            raise ValueError(f"Deal {deal_id} not found for tenant {tenant_id}")

        return str(row[0])

    async def _load_gdbs_claims(
        self, run_id: str, deal_id: str, tenant_id: str, gdbs_deal: GDBSDeal
    ) -> None:
        """Load GDBS claims into database for demo purposes.

        Args:
            run_id: UUID of the run.
            deal_id: UUID of the deal.
            tenant_id: Tenant UUID.
            gdbs_deal: GDBS deal with synthetic claims.
        """
        logger.info(
            f"Loading {len(gdbs_deal.claims)} claims from GDBS for deal {deal_id}",
            extra={"run_id": run_id, "deal_id": deal_id},
        )

        for claim in gdbs_deal.claims:
            claim_text = claim.get("raw_text", claim.get("claim_text", ""))
            metric_type = claim.get("metric_type", "UNKNOWN")
            value = claim.get("value")
            time_period = claim.get("time_period")

            claim_id = await self._create_claim(
                deal_id=deal_id,
                tenant_id=tenant_id,
                claim_text=claim_text,
                metric_type=metric_type,
                metric_value=str(value) if value else None,
                time_period=time_period,
            )

            # Create basic Sanad for each claim
            await self._create_sanad(
                claim_id=claim_id,
                tenant_id=tenant_id,
                evidence_summary=f"Evidence from GDBS dataset for {metric_type}",
                grade="B",  # Default grade for synthetic data
            )

        await asyncio.to_thread(self._db_conn.commit)

    async def _create_claim(
        self,
        deal_id: str,
        tenant_id: str,
        claim_text: str,
        metric_type: str,
        metric_value: str | None,
        time_period: str | None,
    ) -> str:
        """Create a claim record.

        Args:
            deal_id: UUID of the deal.
            tenant_id: Tenant UUID.
            claim_text: Raw claim text.
            metric_type: Type of metric (ARR, MRR, etc.).
            metric_value: Numeric value if applicable.
            time_period: Time period for the claim.

        Returns:
            UUID of created claim.
        """
        import uuid

        from sqlalchemy import text

        claim_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        query = text("""
            INSERT INTO claims (
                claim_id, tenant_id, deal_id, claim_text,
                metric_type, metric_value, time_period,
                created_at
            ) VALUES (:claim_id, :tenant_id, :deal_id, :claim_text,
                      :metric_type, :metric_value, :time_period, :created_at)
        """)

        await asyncio.to_thread(
            self._db_conn.execute,
            query,
            {
                "claim_id": claim_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "claim_text": claim_text,
                "metric_type": metric_type,
                "metric_value": metric_value,
                "time_period": time_period,
                "created_at": now,
            },
        )

        return claim_id

    async def _create_sanad(
        self,
        claim_id: str,
        tenant_id: str,
        evidence_summary: str,
        grade: str,
    ) -> str:
        """Create a Sanad record.

        Args:
            claim_id: UUID of the claim this Sanad supports.
            tenant_id: Tenant UUID.
            evidence_summary: Summary of supporting evidence.
            grade: Sanad grade (A/B/C/D).

        Returns:
            UUID of created Sanad.
        """
        import uuid

        from sqlalchemy import text

        sanad_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        query = text("""
            INSERT INTO sanads (
                sanad_id, tenant_id, claim_id,
                evidence_summary, grade, created_at
            ) VALUES (:sanad_id, :tenant_id, :claim_id,
                      :evidence_summary, :grade, :created_at)
        """)

        await asyncio.to_thread(
            self._db_conn.execute,
            query,
            {
                "sanad_id": sanad_id,
                "tenant_id": tenant_id,
                "claim_id": claim_id,
                "evidence_summary": evidence_summary,
                "grade": grade,
                "created_at": now,
            },
        )

        return sanad_id
