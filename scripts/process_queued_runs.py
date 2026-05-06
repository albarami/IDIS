"""Manual script to process queued runs through the canonical worker."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from idis.pipeline.worker import PipelineWorker, get_worker_tenant_ids

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


async def process_all_queued() -> None:
    """Process queued runs using PipelineWorker and RunExecutionService."""
    tenant_ids = get_worker_tenant_ids()
    if not tenant_ids:
        print("No worker tenant scope configured. Set IDIS_WORKER_TENANT_IDS.")
        return

    worker = PipelineWorker(poll_interval=0, tenant_ids=tenant_ids)
    total_processed = 0
    while True:
        processed = await worker._process_queued_runs()
        if processed == 0:
            break
        total_processed += processed

    print(f"Processed {total_processed} queued run(s)")


if __name__ == "__main__":
    asyncio.run(process_all_queued())
