"""Slice96 Task 8 — docs reconciliation pins.

RED-first. Pins the post-Slice96 readiness-doc banner, the runtime-reliability architecture note
(Redis / rate-limit + worker-role decision), and the plan status/as-built to the shipped runtime
controls: duplicate-run guard, Redis-backed rate limiting, provider budget hard cap, idempotency
TTL cleanup, cooperative cancellation, and safe-shape observability.

The acceptance itself is proven by the composition capstone (``test_slice96_acceptance_capstone``)
and each Task's own tests; this file only locks the doc reconciliation. PYTHONPATH pinned to src.
"""

from __future__ import annotations

from pathlib import Path

_READINESS_DOC = Path("docs/architecture/strict_full_live_readiness.md")
_ARCH_NOTE = Path("docs/architecture/slice96_runtime_reliability.md")
_PLAN = Path("docs/plans/2026-07-07-slice96-queue-retry-idempotency-rate-limits.md")


def test_readiness_doc_reconciled_post_slice96() -> None:
    doc = _READINESS_DOC.read_text(encoding="utf-8")
    assert "post-Slice96" in doc  # a post-Slice96 banner reconciles the runtime controls
    # the six landed runtime controls, by stable code / seam / env:
    assert "RUN_ALREADY_ACTIVE" in doc  # duplicate-run guard (DEC-D)
    assert "RateLimitStore" in doc  # Redis-backed cross-replica rate limiting (DEC-A)
    assert "PROVIDER_BUDGET_EXCEEDED" in doc  # provider budget hard cap (DEC-C)
    assert "IDIS_IDEMPOTENCY_TTL_DAYS" in doc  # idempotency TTL cleanup (DEC-E)
    assert "RUN_CANCELLED" in doc  # cooperative mid-run cancellation (G7)
    assert "observab" in doc.lower()  # safe-shape observability (G5)
    # prior banners + the frozen Slice-53 census are preserved.
    assert "post-Slice95" in doc
    assert "post-Slice94" in doc
    assert "Slice-53 census" in doc


def test_architecture_note_records_redis_and_worker_decisions() -> None:
    note = _ARCH_NOTE.read_text(encoding="utf-8")
    # DEC-A: Redis-backed rate-limit store behind an injectable seam, in-memory default.
    assert "Redis" in note and "RateLimitStore" in note
    assert "in-memory" in note.lower()  # in-memory default / hermetic fallback
    # DEC-B queue model + DEC-F worker role are recorded.
    assert "Postgres" in note and "poll" in note.lower()  # keep Postgres-polling queue
    assert "worker" in note.lower()
    # DEC-C provider budget is durable (Postgres), not in-memory-only for go-live.
    assert "provider_budget_usage" in note or "PROVIDER_BUDGET_EXCEEDED" in note


def test_slice96_plan_reconciled_to_as_built() -> None:
    plan = _PLAN.read_text(encoding="utf-8")
    assert "post-Slice96" in plan
    assert "acceptance met" in plan.lower()  # Status updated to the completed as-built state
    assert "RUN_ALREADY_ACTIVE" in plan
    assert "PROVIDER_BUDGET_EXCEEDED" in plan
    assert "RUN_CANCELLED" in plan
