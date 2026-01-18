"""Manual script to process queued runs for testing."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import text

from idis.persistence.db import get_app_engine
from idis.pipeline.executor import PipelineExecutor

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


async def process_all_queued() -> None:
    """Process all queued runs."""
    engine = get_app_engine()

    gdbs_path = Path(__file__).parent.parent / "datasets" / "gdbs_full"

    with engine.connect() as conn:
        # Find queued runs
        result = conn.execute(
            text("""
                SELECT run_id, deal_id, mode, tenant_id
                FROM runs
                WHERE status = 'QUEUED'
                ORDER BY created_at ASC
            """)
        )

        runs = result.fetchall()

        if not runs:
            print("No queued runs found")
            return

        print(f"Found {len(runs)} queued runs")

        for row in runs:
            run_id, deal_id, mode, tenant_id = row

            print(f"\nProcessing run {run_id} for deal {deal_id}")

            try:
                # Set tenant context
                conn.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
                    {"tenant_id": tenant_id},
                )

                # Execute run
                executor = PipelineExecutor(conn, gdbs_path=str(gdbs_path))
                await executor.execute_run(run_id, deal_id, mode, tenant_id)

                conn.commit()
                print(f"✅ Completed run {run_id}")

            except Exception as e:
                conn.rollback()
                print(f"❌ Failed run {run_id}: {e}")
                import traceback

                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(process_all_queued())
