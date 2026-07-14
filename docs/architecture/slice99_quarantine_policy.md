# Slice99 Task 4 - Module Quarantine Policy

## Policy

A QUARANTINED module is legacy code that stays in the tree (for history/migration reference)
but must never be imported, instantiated, or re-exported from any canonical runtime path.
Canonical runtime paths are `src/` and `scripts/`. Tests may reference quarantined names to
enforce the quarantine itself; prose mentions (docstrings, audit narratives, evidence strings)
that merely DESCRIBE the quarantine are allowed.

The single source of truth is the registry in `src/idis/quarantine.py`
(`QUARANTINED_MODULES`). A repo-wide guard (`iter_quarantine_violations`) scans the canonical
paths for import and instantiation forms and fails closed on any hit; it is enforced by
`tests/test_slice99_quarantine_policy.py`. The older per-file pins (Slice 70/75B
`test_process_queued_runs_canonical.py`, `test_slice75b_run_retry_resume_cancel_parity.py`)
remain in place as defense in depth.

Adding a quarantine entry requires: the module path, the primary symbol, a stated reason, and
an update to this doc. Removing one requires deleting the legacy module or explicitly
rehabilitating it through review.

## Quarantined modules

| Module | Symbol | Reason | Since |
| --- | --- | --- | --- |
| `idis.pipeline.executor` | `PipelineExecutor` | Legacy demo executor superseded by the canonical RunExecutionService / PipelineWorker path; it bypasses the run-step ledger, strict gates, and tenant-scoped persistence. | Slice 70 / Slice 75B |

## Explicit non-goal (Q2 decision)

Document/malware quarantine of uploaded files (runtime scanning, infected-file isolation,
quarantined-document workflows referenced by RB-02 and taxonomy `document.malware.detected`)
is a product feature and is NOT implemented in Slice99. This policy covers MODULE quarantine
only; the document/malware surface remains future work with its own slice.
