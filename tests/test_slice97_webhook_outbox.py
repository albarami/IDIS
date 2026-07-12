"""Slice97 Task 2 — durable webhook outbox repository (in-memory + logic).

RED-first. The outbox is the durable delivery queue for lifecycle-event webhooks: idempotent
enqueue on ``(webhook_id, event_id)``, due-ordered claim of pending attempts, retryable/terminal
transitions, and pruning of terminal rows. The Postgres twin (RLS + ``FOR UPDATE SKIP LOCKED`` +
unique-index idempotency) is proven env-gated in ``test_slice97_webhook_outbox_postgres.py``.
PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from idis.persistence.repositories.webhook_outbox import (
    InMemoryWebhookOutboxRepository,
    WebhookOutboxRecord,
)

_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_WEBHOOK = "wwwwwwww-wwww-wwww-wwww-wwwwwwwwwwww"
_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)


def _enqueue(
    repo: InMemoryWebhookOutboxRepository,
    *,
    tenant: str,
    event_id: str,
    webhook: str = _WEBHOOK,
    now: datetime = _T0,
) -> bool:
    return repo.enqueue(
        webhook_id=webhook,
        tenant_id=tenant,
        event_id=event_id,
        event_type="run.completed",
        payload={"status": "COMPLETED", "artifact_count": 2},
        now=now,
    )


def test_enqueue_is_idempotent_on_webhook_and_event() -> None:
    repo = InMemoryWebhookOutboxRepository()
    event_id = str(uuid.uuid4())
    assert _enqueue(repo, tenant=_TENANT_A, event_id=event_id) is True
    assert _enqueue(repo, tenant=_TENANT_A, event_id=event_id) is False  # duplicate -> no new row
    assert len(repo.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10)) == 1
    # same event, different webhook subscription -> its own delivery row
    assert _enqueue(repo, tenant=_TENANT_A, event_id=event_id, webhook=str(uuid.uuid4())) is True
    assert len(repo.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10)) == 2


def test_claim_due_returns_due_pending_in_order_and_excludes_succeeded() -> None:
    repo = InMemoryWebhookOutboxRepository()
    e1, e2, e3 = (str(uuid.uuid4()) for _ in range(3))
    _enqueue(repo, tenant=_TENANT_A, event_id=e1, now=_T0)
    _enqueue(repo, tenant=_TENANT_A, event_id=e2, now=_T0 + timedelta(minutes=1))
    _enqueue(repo, tenant=_TENANT_A, event_id=e3, now=_T0 + timedelta(minutes=5))

    due = repo.claim_due(tenant_id=_TENANT_A, now=_T0 + timedelta(minutes=1), limit=10)
    assert [r.event_id for r in due] == [e1, e2]  # due, ordered by next_attempt_at; e3 future
    assert all(isinstance(r, WebhookOutboxRecord) for r in due)

    repo.mark_succeeded(
        tenant_id=_TENANT_A, attempt_id=due[0].attempt_id, now=_T0 + timedelta(minutes=2)
    )
    due2 = repo.claim_due(tenant_id=_TENANT_A, now=_T0 + timedelta(minutes=5), limit=10)
    assert [r.event_id for r in due2] == [e2, e3]  # succeeded row excluded


def test_mark_failed_keeps_row_retryable_until_exhausted() -> None:
    repo = InMemoryWebhookOutboxRepository()
    event_id = str(uuid.uuid4())
    _enqueue(repo, tenant=_TENANT_A, event_id=event_id, now=_T0)
    attempt_id = repo.claim_due(tenant_id=_TENANT_A, now=_T0, limit=1)[0].attempt_id

    repo.mark_failed(
        tenant_id=_TENANT_A,
        attempt_id=attempt_id,
        next_attempt_at=_T0 + timedelta(minutes=1),
        last_error="503",
        now=_T0,
    )
    assert repo.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10) == []  # rescheduled, not yet due
    retried = repo.claim_due(tenant_id=_TENANT_A, now=_T0 + timedelta(minutes=1), limit=10)
    assert len(retried) == 1 and retried[0].attempt_count == 1 and retried[0].last_error == "503"
    assert retried[0].status == "pending"  # still retryable

    repo.mark_exhausted(
        tenant_id=_TENANT_A, attempt_id=attempt_id, last_error="503", now=_T0 + timedelta(minutes=1)
    )
    assert repo.claim_due(tenant_id=_TENANT_A, now=_T0 + timedelta(hours=1), limit=10) == []


def test_claim_due_is_tenant_scoped() -> None:
    repo = InMemoryWebhookOutboxRepository()
    _enqueue(repo, tenant=_TENANT_A, event_id=str(uuid.uuid4()))
    assert len(repo.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10)) == 1
    assert repo.claim_due(tenant_id=_TENANT_B, now=_T0, limit=10) == []  # other tenant sees nothing


def test_delete_terminal_reclaims_only_terminal_rows_for_tenant() -> None:
    repo = InMemoryWebhookOutboxRepository()
    e_done, e_pending = str(uuid.uuid4()), str(uuid.uuid4())
    _enqueue(repo, tenant=_TENANT_A, event_id=e_done)
    _enqueue(repo, tenant=_TENANT_A, event_id=e_pending)
    done_attempt = next(
        r.attempt_id
        for r in repo.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10)
        if r.event_id == e_done
    )
    repo.mark_succeeded(tenant_id=_TENANT_A, attempt_id=done_attempt, now=_T0)

    removed = repo.delete_terminal(tenant_id=_TENANT_A, older_than=_T0 + timedelta(days=1))
    assert removed == 1  # only the succeeded (terminal) row
    survivors = repo.claim_due(tenant_id=_TENANT_A, now=_T0 + timedelta(days=2), limit=10)
    assert [r.event_id for r in survivors] == [e_pending]  # pending row untouched
