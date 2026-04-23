"""Deprecated GDBS-demo PipelineExecutor (Sprint 2, Task 10).

This module previously held a GDBS-backed demo execution path that was
driven by the background worker. It inserted into `claims` / `sanads`
columns that no longer exist in the current schema
(`metric_type`, `time_period`, `evidence_summary`, `grade`), so it was
broken under Postgres for any real deal.

The live worker path now drives `RunOrchestrator` directly
(see `src/idis/pipeline/worker.py`). This module is retained only for
import backward compatibility of external tooling; the class raises
loudly if it is ever instantiated again so it cannot be silently
re-wired into the live path.
"""

from __future__ import annotations

from typing import Any


class PipelineExecutor:  # pragma: no cover - intentional shim
    """Deprecated shim that raises on use.

    The previous implementation pulled GDBS synthetic data into the
    production `claims` and `sanads` tables using columns that the
    current migrations do not define. The worker no longer routes
    through this class; the real pipeline is driven by `RunOrchestrator`
    via `PipelineWorker._process_queued_runs`.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "PipelineExecutor is deprecated. The worker now drives "
            "RunOrchestrator directly; see src/idis/pipeline/worker.py. "
            "If you are seeing this from a test, update it to monkeypatch "
            "RunOrchestrator or the orchestrator step callables instead."
        )
