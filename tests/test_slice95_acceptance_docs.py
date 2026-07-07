"""Slice95 Task 10 — acceptance + doc reconciliation pins.

Pins the readiness-doc post-Slice95 banner and the plan status/as-built to the shipped review
experience: reviewer readiness + run-list endpoints, debate safe-shape, contracts locked by
tests, and the explicit boundaries (injected fakes, no real LLM, no migration, no private text).

The end-to-end acceptance itself is proven by the existing Slice95 tests — the backend contract
lock (``test_slice95_contract_lock``), the readiness / run-list endpoint tests, the review-surface
characterization, and the UI component/contract tests — so this file only locks the reconciliation.
"""

from __future__ import annotations

from pathlib import Path

_READINESS_DOC = Path("docs/architecture/strict_full_live_readiness.md")
_PLAN = Path("docs/plans/2026-07-07-slice95-api-ui-review-experience.md")


def test_readiness_doc_reconciled_post_slice95() -> None:
    doc = _READINESS_DOC.read_text(encoding="utf-8")
    # A post-Slice95 banner reconciles the API/UI review experience.
    assert "post-Slice95" in doc
    assert "strict-readiness" in doc  # reviewer readiness GET endpoint
    # The reviewer readiness GET is config-only inspection, not a live-connectivity proof — and it
    # is exempt from the request DB transaction, so it stays available when the database is down.
    assert "config-only inspection" in doc.lower()
    assert "database is down" in doc.lower()
    assert "DebateRoundSummary" in doc  # debate safe-shape hardening
    assert "locked by tests" in doc.lower()  # A2: contracts locked
    # Explicit boundaries.
    assert "injected fakes" in doc.lower()
    assert "no migration" in doc.lower()
    assert "no real anthropic" in doc.lower()
    assert "raw evidence" in doc.lower()
    # Prior banners + the frozen Slice-53 census are preserved.
    assert "post-Slice94" in doc
    assert "post-Slice93" in doc
    assert "Slice-53 census" in doc


def test_slice95_plan_reconciled_to_as_built() -> None:
    plan = _PLAN.read_text(encoding="utf-8")
    assert "post-Slice95" in plan
    assert "acceptance met" in plan.lower()  # Status updated to the completed as-built state
    assert "injected fakes" in plan.lower()
    assert "no migration" in plan.lower()
