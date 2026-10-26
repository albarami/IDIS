"""Manual script to process queued runs through the canonical worker."""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from idis.pipeline.worker import (
    PipelineWorker,
    get_worker_tenant_ids,
    load_worker_lease_config,
)
from idis.runtime.profile import (
    StrictProfileError,
    StrictRuntimeProfile,
    require_runtime_profile,
)
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
    require_strict_process_role,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


async def process_all_queued(
    *,
    watch: bool = False,
    poll_seconds: float = 2.0,
    worker_factory: Callable[..., Any] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    strict_profile: StrictRuntimeProfile | None = None,
) -> None:
    """Drain queued runs once, or continuously poll through PipelineWorker."""
    ambient_strict = is_strict_full_live_required(
        env=os.environ,
        dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV),
    )
    if strict_profile is None and ambient_strict:
        raise RuntimeError("STRICT_PROFILE_LOAD_FAILED")
    if strict_profile is not None:
        try:
            require_runtime_profile(strict_profile, command="worker")
        except StrictProfileError as exc:
            raise RuntimeError(exc.code) from exc
    runtime_env = strict_profile.values if strict_profile is not None else os.environ
    require_strict_process_role(
        "worker",
        env=runtime_env,
        dotenv_path=(
            None if strict_profile is not None else os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
        ),
    )
    if watch and poll_seconds <= 0:
        raise ValueError("--poll-seconds must be greater than zero in watch mode")

    lease_config = load_worker_lease_config(env=runtime_env, strict=True)
    tenant_ids = get_worker_tenant_ids(env=runtime_env)
    if not tenant_ids:
        print("No worker tenant scope configured. Set IDIS_WORKER_TENANT_IDS.")
        return

    factory = worker_factory or PipelineWorker
    should_stop = stop_requested or (lambda: False)
    sleep_fn = sleep or asyncio.sleep
    if strict_profile is not None:
        worker = factory(
            poll_interval=poll_seconds if watch else 0,
            tenant_ids=tenant_ids,
            lease_config=lease_config,
            strict_profile=strict_profile,
        )
    else:
        worker = factory(
            poll_interval=poll_seconds if watch else 0,
            tenant_ids=tenant_ids,
            lease_config=lease_config,
        )
    total_processed = 0
    while True:
        if watch and should_stop():
            break
        processed = await worker._process_queued_runs()
        total_processed += processed

        if watch:
            if should_stop():
                break
            await sleep_fn(poll_seconds)
            continue

        if processed == 0:
            break

    print(f"Processed {total_processed} queued run(s)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Continuously poll the queue")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="Seconds between queue polls in watch mode",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(process_all_queued(watch=args.watch, poll_seconds=args.poll_seconds))
