"""Slice99 reviewer remediation (RED-first) - fixes for the two Important review findings.

R1 (/metrics tenant leakage): webhook delivery counters must be GLOBAL aggregates - no
    tenant_id label, no tenant UUID, no tenant-specific volume data is scrapeable from the
    unauthenticated /metrics endpoint. Driven through the REAL dispatcher recording path.
R1b: ``render_prometheus_text`` escapes label values per the Prometheus exposition spec
    (backslash, double-quote, newline) as defense in depth.
R2 (CI-enforced recovery evidence): a dedicated ``backup-restore-drill`` CI job runs the
    Postgres restore drill (IDIS_REQUIRE_POSTGRES=1, postgresql+psycopg2 URLs) and the
    ``release-gate`` job depends on it - release promotion cannot pass without recovery proof.
R3 (misleading CI ladder): the evaluation-harness job must not carry the dead
    ``exit_code=$?`` ladder (GitHub's ``bash -e`` aborts before capture); the drift command
    fails the step directly.

PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.observability.metrics import (
    increment_counter,
    render_prometheus_text,
    reset_metrics,
)
from idis.services.webhooks.dispatcher import WebhookDispatcher

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_DISPATCHER_SOURCE = _REPO_ROOT / "src" / "idis" / "services" / "webhooks" / "dispatcher.py"

_TENANT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _record_real_webhook_outcome(tenant_id: str) -> None:
    """Drive the REAL dispatcher metrics/audit recording path (production code, stub row)."""
    fake_self = SimpleNamespace(_resolve_audit_sink=lambda: InMemoryAuditSink())
    row = SimpleNamespace(
        webhook_id="wh-remediation-1",
        event_id="ev-remediation-1",
        event_type="run.completed",
        attempt_id="attempt-remediation-1",
    )
    WebhookDispatcher._record_outcome(
        fake_self,  # type: ignore[arg-type]
        row,
        tenant_id,
        datetime.now(UTC),
        "succeeded",
        200,
        1,
    )


# ---------------------------------------------------------------------------
# R1: no tenant labels / UUIDs on the scrape surface
# ---------------------------------------------------------------------------


def test_real_dispatcher_metrics_carry_no_tenant_label() -> None:
    reset_metrics()

    _record_real_webhook_outcome(_TENANT_UUID)

    rendered = render_prometheus_text()
    assert "webhook_delivery_attempts_total" in rendered, "the counter itself must still exist"
    assert "tenant_id=" not in rendered, "webhook counters must be global (no tenant label)"
    assert _TENANT_UUID not in rendered, "no tenant UUID may reach the scrape surface"


def test_metrics_endpoint_exposes_no_uuid_or_tenant_labels() -> None:
    reset_metrics()
    client = TestClient(create_app(service_region="us-east-1"), raise_server_exceptions=False)
    assert client.get("/health").status_code == 200
    _record_real_webhook_outcome(_TENANT_UUID)

    body = client.get("/metrics").text

    assert "tenant_id=" not in body
    assert not _UUID_PATTERN.search(body), f"UUID leaked into /metrics: {body[:400]}"


def test_dispatcher_source_never_builds_tenant_labels() -> None:
    """Source pin: the leak pattern must not return (labels dict keyed by tenant_id)."""
    source = _DISPATCHER_SOURCE.read_text(encoding="utf-8")
    assert '{"tenant_id"' not in source, (
        "the dispatcher must not label delivery counters by tenant_id"
    )


# ---------------------------------------------------------------------------
# R1b: Prometheus label-value escaping
# ---------------------------------------------------------------------------


def test_render_prometheus_text_escapes_label_values() -> None:
    reset_metrics()
    increment_counter("escape_probe_total", labels={"k": 'a"b\\c\nd'})

    rendered = render_prometheus_text()

    assert 'k="a\\"b\\\\c\\nd"' in rendered, (
        f"label values must be escaped per the Prometheus exposition spec, got: {rendered!r}"
    )


# ---------------------------------------------------------------------------
# R2: CI-enforced backup/restore drill wired into the release gate
# ---------------------------------------------------------------------------


def _ci_jobs() -> dict[str, Any]:
    return dict(yaml.safe_load(_CI_PATH.read_text(encoding="utf-8"))["jobs"])


def _job_text(job: dict[str, Any]) -> str:
    chunks: list[str] = []
    for step in job.get("steps", []):
        run = step.get("run")
        if run:
            chunks.append(str(run))
        env = step.get("env")
        if env:
            chunks.append(str(env))
    if job.get("env"):
        chunks.append(str(job["env"]))
    return "\n".join(chunks)


def test_ci_has_backup_restore_drill_job() -> None:
    jobs = _ci_jobs()
    assert "backup-restore-drill" in jobs, "a dedicated CI job must run the Postgres restore drill"
    text = _job_text(jobs["backup-restore-drill"])

    assert "tests/test_slice99_backup_restore_postgres.py" in text
    assert "IDIS_REQUIRE_POSTGRES" in text, "the drill must be REQUIRED, not skippable"
    assert "postgresql+psycopg2://" in text, "the drill must use psycopg2 driver URLs"
    services = jobs["backup-restore-drill"].get("services", {})
    assert "postgres" in services, "the drill job needs its own disposable Postgres service"


def test_release_gate_needs_the_backup_restore_drill() -> None:
    jobs = _ci_jobs()
    needs = jobs["release-gate"].get("needs", [])
    needs_set = {needs} if isinstance(needs, str) else set(needs)
    assert "backup-restore-drill" in needs_set, (
        "release promotion must not pass without recovery proof"
    )


# ---------------------------------------------------------------------------
# R3: the dead evaluation-harness exit-code ladder is gone
# ---------------------------------------------------------------------------


def test_evaluation_harness_has_no_dead_exit_code_ladder() -> None:
    jobs = _ci_jobs()
    text = _job_text(jobs["evaluation-harness"])
    assert "exit_code=$?" not in text, (
        "the exit-code ladder is dead under bash -e; the drift command must fail the step directly"
    )
