"""Slice100 Task 1 RED contracts for enqueue-only API and canonical worker ownership."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import idis.api.main as api_main
import idis.api.routes.runs as runs_route
import idis.pipeline.worker as worker_module
import idis.services.runs.execution as execution_module
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.persistence.repositories.runs import get_runs_repository
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.worker import PipelineWorker
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import OrchestratorResult, RunContext, RunOrchestrator
from tests.abac_seed import seed_deal_access

TENANT_ID = "11111111-1111-1111-1111-111111111111"
API_KEY = "slice100-task1-key"
COMMERCIAL_DD_VERSION_RECORD_ID = "10000000-0000-4000-8000-000000000001"
FINANCIAL_DD_VERSION_RECORD_ID = "20000000-0000-4000-8000-000000000002"


def _full_run_request(**values: Any) -> dict[str, Any]:
    return {
        "mode": "FULL",
        "methodology_selection": {
            "commercial_dd_version_record_id": COMMERCIAL_DD_VERSION_RECORD_ID,
            "financial_dd_version_record_id": FINANCIAL_DD_VERSION_RECORD_ID,
        },
        **values,
    }


class _LegacyMethodologySelectionsRepository:
    """Minimal recorder keeping legacy tests focused on their original boundary."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    def create_run_selection(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(dict(kwargs))
        return {"selection_id": "30000000-0000-4000-8000-000000000003"}


@pytest.fixture(autouse=True)
def _admit_fictional_methodology_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> _LegacyMethodologySelectionsRepository:
    repository = _LegacyMethodologySelectionsRepository()
    monkeypatch.setattr(
        runs_route,
        "_validate_full_methodology_selection",
        lambda **_kwargs: repository,
    )
    return repository


class _TransactionalInMemoryAuditSink(InMemoryAuditSink):
    """In-memory sink exposing the durable sink's in-transaction seam."""

    def emit_in_tx(self, _conn: object, event: dict[str, Any]) -> None:
        self.emit(event)


class _HermeticPostgresAuditSink(PostgresAuditSink):
    """Production-shaped recorder for Task 1 tests that never exercise audit storage."""

    # Task 2 legitimately rejects in-memory authorities in strict composition; these Task 1
    # tests retain a no-network recorder while satisfying the production sink type boundary.
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def emit_in_tx(self, _conn: object, event: dict[str, Any]) -> None:
        self.emit(event)


class _PassingStrictReport:
    may_proceed = True


class _ProofRepo:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def consume_and_admit_contract_run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return get_runs_repository(None, TENANT_ID).create(
            run_id=str(kwargs["run_id"]),
            deal_id=str(kwargs["deal_id"]),
            mode=str(kwargs["mode"]),
            idempotency_key=kwargs.get("idempotency_key"),
            source=kwargs.get("source"),
            created_by_actor_id=kwargs.get("created_by_actor_id"),
            created_by_actor_type=kwargs.get("created_by_actor_type"),
        )


def _api_keys() -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": TENANT_ID,
            "actor_id": "slice100-task1-actor",
            "name": "Slice100 Task1",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        }
    }


def _reviewed_task1_profile(tmp_path: Path, command: str) -> Any:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    canary_key = "slice100-task1-canary-key"
    admin_key = "slice100-task1-admin-key"
    canary_tenant = "22222222-2222-4222-8222-222222222222"
    registry = {
        **_api_keys(),
        canary_key: {
            "tenant_id": canary_tenant,
            "actor_id": "slice100-task1-canary",
            "name": "Slice100 Task1 Canary",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["INTEGRATION_SERVICE"],
        },
        admin_key: {
            "tenant_id": TENANT_ID,
            "actor_id": "slice100-task1-admin",
            "name": "Slice100 Task1 Admin",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ADMIN"],
        },
    }
    return _reviewed_profile(
        tmp_path,
        command,
        additions={
            "IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID,
            "IDIS_ISOLATION_CANARY_TENANT_ID": canary_tenant,
            "IDIS_API_KEYS_JSON": json.dumps(registry),
            "IDIS_REAL_EXAMPLE_API_KEY": API_KEY,
            "IDIS_ISOLATION_CANARY_API_KEY": canary_key,
            "IDIS_GOVERNANCE_ADMIN_API_KEY": admin_key,
            "IDIS_SERVICE_REGION": "us-east-1",
            "IDIS_WORKER_TENANT_IDS": TENANT_ID,
        },
    )


def _strict_client(
    monkeypatch: pytest.MonkeyPatch,
    proof_repo: _ProofRepo,
    tmp_path: Path,
) -> tuple[TestClient, str]:
    # This is the hermetic API contract twin. Keep its repositories in memory even when a
    # production-shaped DSN is briefly required by Task 2's strict composition boundary.
    monkeypatch.setattr("idis.persistence.db.is_postgres_configured", lambda: False)
    monkeypatch.setattr(
        "idis.persistence.repositories.runs.is_postgres_configured",
        lambda: False,
    )
    monkeypatch.setenv(
        "IDIS_DATABASE_URL",
        "postgresql+psycopg2://task1-hermetic.invalid/idis",
    )
    monkeypatch.delenv("IDIS_DATABASE_ADMIN_URL", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_POSTGRES", raising=False)
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "api")
    clear_deals_store()
    clear_runs_store()
    monkeypatch.setattr(
        runs_route,
        "build_strict_full_live_admission_report",
        lambda **_kwargs: _PassingStrictReport(),
    )
    monkeypatch.setattr(
        runs_route,
        "get_strict_preflight_proofs_repository",
        lambda _conn, _tenant_id: proof_repo,
        raising=False,
    )
    monkeypatch.setattr(
        runs_route,
        "resolve_strict_preflight_binding",
        lambda **_kwargs: {"binding_hash": "a" * 64},
        raising=False,
    )
    # Source-package freezing is covered by Task 7 composition tests; this legacy
    # hermetic fixture remains scoped to proof consumption and queued admission.
    monkeypatch.setattr(
        runs_route,
        "_freeze_task7_selected_source_packages",
        lambda **_kwargs: ("00000000-0000-4000-8000-000000000701",),
    )

    class ForbiddenExecutionService:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("strict API must not construct RunExecutionService")

    monkeypatch.setattr(runs_route, "RunExecutionService", ForbiddenExecutionService)
    monkeypatch.setattr(
        runs_route,
        "build_run_context",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict API must not build an inline run context")
        ),
    )

    app = create_app(
        audit_sink=_HermeticPostgresAuditSink(),
        service_region="us-east-1",
        strict_profile=_reviewed_task1_profile(tmp_path / "profile", "api"),
    )
    # The DSN is composition-only in this hermetic test; removing it restores the explicitly
    # monkeypatched in-memory ABAC/repository path before any request is issued.
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    app.state.deal_documents = {}
    client = TestClient(app, raise_server_exceptions=False)
    create_response = client.post(
        "/v1/deals",
        json={"name": "Task1", "company_name": "Task1 Company"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_response.status_code == 201
    deal_id = str(create_response.json()["deal_id"])
    seed_deal_access(TENANT_ID, deal_id, "slice100-task1-actor")
    app.state.deal_documents[deal_id] = [
        {
            "document_id": "document-1",
            "doc_type": "PDF",
            "document_name": "document.pdf",
            "spans": [
                {
                    "span_id": "span-1",
                    "text_excerpt": "safe",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]
    return client, deal_id


def test_strict_full_create_returns_queued_without_inline_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proof_repo = _ProofRepo()
    client, deal_id = _strict_client(monkeypatch, proof_repo, tmp_path)
    proof_id = "00000000-0000-4000-8000-000000000101"

    response = client.post(
        f"/v1/deals/{deal_id}/runs",
        json=_full_run_request(strict_preflight_proof_id=proof_id),
        headers={
            "X-IDIS-API-Key": API_KEY,
            "Idempotency-Key": f"task1-create-{uuid.uuid4()}",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["steps"] == []
    assert body["block_reason"] is None
    assert get_runs_repository(None, TENANT_ID).get(body["run_id"])["status"] == "QUEUED"
    assert len(proof_repo.calls) == 1
    assert proof_repo.calls[0]["proof_id"] == proof_id


def test_strict_full_create_requires_preflight_proof_before_run_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proof_repo = _ProofRepo()
    client, deal_id = _strict_client(monkeypatch, proof_repo, tmp_path)

    response = client.post(
        f"/v1/deals/{deal_id}/runs",
        json=_full_run_request(),
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "STRICT_PREFLIGHT_PROOF_REQUIRED"
    assert proof_repo.calls == []


@pytest.mark.parametrize(
    "code",
    [
        "STRICT_PREFLIGHT_PROOF_INVALID",
        "STRICT_PREFLIGHT_PROOF_EXPIRED",
        "STRICT_PREFLIGHT_PROOF_MISMATCH",
        "STRICT_PREFLIGHT_PROOF_ALREADY_CONSUMED",
    ],
)
def test_strict_proof_failures_are_stable_and_create_no_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    code: str,
) -> None:
    proof_module = __import__(
        "idis.persistence.repositories.strict_preflight_proofs",
        fromlist=["StrictPreflightProofError"],
    )
    proof_repo = _ProofRepo(error=proof_module.StrictPreflightProofError(code))
    client, deal_id = _strict_client(monkeypatch, proof_repo, tmp_path)

    response = client.post(
        f"/v1/deals/{deal_id}/runs",
        json=_full_run_request(strict_preflight_proof_id="00000000-0000-4000-8000-000000000102"),
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409
    assert response.json()["code"] == code
    assert get_runs_repository(None, TENANT_ID).has_active_run(deal_id) is False


def test_claimed_postgres_run_includes_strict_preflight_proof_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.persistence.repositories.runs as runs_module

    proof_id = "00000000-0000-4000-8000-000000000111"
    row = SimpleNamespace(
        run_id="00000000-0000-4000-8000-000000000112",
        tenant_id=TENANT_ID,
        deal_id="00000000-0000-4000-8000-000000000113",
        mode="FULL",
        status="QUEUED",
        started_at="2026-07-15T00:00:00Z",
        finished_at=None,
        source=None,
        created_at="2026-07-15T00:00:00Z",
        cancel_requested_at=None,
        created_by_actor_id="origin-actor",
        created_by_actor_type="HUMAN",
        strict_preflight_proof_id=proof_id,
    )

    class Result:
        def fetchall(self) -> list[Any]:
            return [row]

    class Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: Any, _params: Any = None) -> Result:
            self.statements.append(str(statement))
            return Result()

    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = Connection()
    repo = runs_module.PostgresRunsRepository(connection, TENANT_ID)

    claimed = repo.claim_queued_runs()

    assert claimed[0]["strict_preflight_proof_id"] == proof_id
    assert "strict_preflight_proof_id" in connection.statements[-1]


def test_durable_proof_verifier_requires_exact_reciprocal_consumed_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.persistence.repositories.strict_preflight_proofs as proof_module

    proof_id = "00000000-0000-4000-8000-000000000114"
    run_id = "00000000-0000-4000-8000-000000000115"
    deal_id = "00000000-0000-4000-8000-000000000116"
    statements: list[str] = []

    class Result:
        def __init__(self, row: object | None) -> None:
            self._row = row

        def fetchone(self) -> object | None:
            return self._row

    class Connection:
        def __init__(self, row: object | None) -> None:
            self.row = row

        def execute(self, statement: Any, _params: Any = None) -> Result:
            statements.append(str(statement))
            return Result(self.row)

    monkeypatch.setattr(proof_module, "set_tenant_local", lambda *_args: None)
    repo = proof_module.PostgresStrictPreflightProofsRepository(Connection(object()), TENANT_ID)

    repo.require_consumed_for_run(proof_id=proof_id, run_id=run_id, deal_id=deal_id)

    sql = statements[-1]
    assert "p.consumed_at IS NOT NULL" in sql
    assert "p.consumed_by_run_id = :run_id" in sql
    assert "r.strict_preflight_proof_id = p.proof_id" in sql
    assert "r.tenant_id = p.tenant_id" in sql
    assert "r.deal_id = p.deal_id" in sql

    mismatch_repo = proof_module.PostgresStrictPreflightProofsRepository(
        Connection(None), TENANT_ID
    )
    with pytest.raises(proof_module.StrictPreflightProofError) as exc_info:
        mismatch_repo.require_consumed_for_run(
            proof_id=proof_id,
            run_id=run_id,
            deal_id=deal_id,
        )
    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH"


class _QueuedStrictRunsRepo:
    def __init__(self, run_data: dict[str, Any]) -> None:
        self.run_data = run_data
        self.failed: list[tuple[str, str]] = []
        self.status = "QUEUED"
        self.lease_fencing_token = 0

    def requeue_expired_worker_leases(
        self,
        *,
        limit: int = 1,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        assert limit == 1
        assert lease_audit_emitter is not None
        return []

    def claim_queued_runs(
        self,
        *,
        limit: int,
        lease_owner_id: str,
        lease_seconds: int,
        lease_audit_emitter: Callable[..., None],
    ) -> list[dict[str, Any]]:
        assert limit == 1
        assert lease_owner_id
        assert lease_seconds > 0
        self.status = "RUNNING"
        self.lease_fencing_token += 1
        self.run_data.update(
            status=self.status,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=self.lease_fencing_token,
            lease_expires_at="2099-01-01T00:01:00Z",
            last_heartbeat_at="2099-01-01T00:00:00Z",
        )
        lease_audit_emitter(
            db_conn=None,
            code="WORKER_LEASE_CLAIMED",
            run_id=self.run_data["run_id"],
            deal_id=self.run_data["deal_id"],
            lease_owner_id=lease_owner_id,
            new_fencing_token=self.lease_fencing_token,
            lease_expires_at=self.run_data["lease_expires_at"],
        )
        return [self.run_data]

    def count_queued_runs(self) -> int:
        return int(self.status == "QUEUED")

    def try_complete_active(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> bool:
        assert finished_at is not None
        self.failed.append((run_id, status))
        return True


def test_strict_worker_missing_proof_fails_before_readiness_or_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    run_id = "00000000-0000-4000-8000-000000000117"
    run_repo = _QueuedStrictRunsRepo(
        {
            "run_id": run_id,
            "tenant_id": TENANT_ID,
            "deal_id": "00000000-0000-4000-8000-000000000118",
            "mode": "FULL",
            "source": {"type": "deal_documents", "document_ids": ["document-1"]},
            "strict_preflight_proof_id": None,
            # Hermetic current-contract mechanics, not canonical FULL admission evidence.
            "pipeline_contract_version": "full-strict-v21",
            "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
            "pipeline_resume_state": "RESUMABLE",
        }
    )
    steps_repo = InMemoryRunStepsRepository(TENANT_ID)
    connection = MagicMock()

    class Engine:
        class Context:
            def __enter__(self) -> Any:
                return connection

            def __exit__(self, *_args: Any) -> None:
                return None

        def connect(self) -> Context:
            return self.Context()

    monkeypatch.setattr(worker_module, "get_app_engine", lambda: Engine())
    monkeypatch.setattr(worker_module, "get_runs_repository", lambda *_args: run_repo)
    monkeypatch.setattr(worker_module, "get_run_steps_repository", lambda *_args: steps_repo)
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(
        worker_module,
        "_default_worker_audit_sink",
        lambda: _TransactionalInMemoryAuditSink(),
    )
    monkeypatch.setattr(
        worker_module,
        "build_strict_full_live_admission_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness must not run without a linked proof")
        ),
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        strict_profile=_reviewed_task1_profile(tmp_path / "profile", "worker"),
        execution_service_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("execution service must not be built without a linked proof")
        ),
    )

    with pytest.raises(worker_module.StrictPreflightProofError) as exc_info:
        asyncio.run(worker._process_queued_runs())

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_REQUIRED"
    assert run_repo.status == "RUNNING"
    assert run_repo.failed == []
    assert steps_repo.get_by_run_id(run_id) == []
    assert connection.commit.call_count == 2


def test_strict_worker_proof_mismatch_fails_before_readiness_or_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import idis.persistence.repositories.strict_preflight_proofs as proof_module

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    run_id = "00000000-0000-4000-8000-000000000119"
    run_repo = _QueuedStrictRunsRepo(
        {
            "run_id": run_id,
            "tenant_id": TENANT_ID,
            "deal_id": "00000000-0000-4000-8000-000000000120",
            "mode": "FULL",
            "source": {"type": "deal_documents", "document_ids": ["document-1"]},
            "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000121",
            # Hermetic current-contract mechanics, not canonical FULL admission evidence.
            "pipeline_contract_version": "full-strict-v21",
            "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
            "pipeline_resume_state": "RESUMABLE",
        }
    )
    steps_repo = InMemoryRunStepsRepository(TENANT_ID)
    connection = MagicMock()

    class Engine:
        class Context:
            def __enter__(self) -> Any:
                return connection

            def __exit__(self, *_args: Any) -> None:
                return None

        def connect(self) -> Context:
            return self.Context()

    monkeypatch.setattr(worker_module, "get_app_engine", lambda: Engine())
    monkeypatch.setattr(worker_module, "get_runs_repository", lambda *_args: run_repo)
    monkeypatch.setattr(worker_module, "get_run_steps_repository", lambda *_args: steps_repo)
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(
        worker_module,
        "_default_worker_audit_sink",
        lambda: _TransactionalInMemoryAuditSink(),
    )
    monkeypatch.setattr(
        worker_module,
        "_verify_worker_strict_preflight_proof",
        lambda **_kwargs: (_ for _ in ()).throw(
            proof_module.StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        worker_module,
        "build_strict_full_live_admission_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness must not run after proof mismatch")
        ),
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        strict_profile=_reviewed_task1_profile(tmp_path / "profile", "worker"),
        execution_service_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("execution service must not be built after proof mismatch")
        ),
    )

    with pytest.raises(proof_module.StrictPreflightProofError) as exc_info:
        asyncio.run(worker._process_queued_runs())

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH"
    assert run_repo.status == "RUNNING"
    assert run_repo.failed == []
    assert steps_repo.get_by_run_id(run_id) == []
    assert connection.commit.call_count == 2


@pytest.mark.parametrize("role", [None, "", "worker", "api,worker", "combined"])
def test_strict_api_uses_reviewed_role_despite_ambient_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str | None,
) -> None:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv(
        "IDIS_DATABASE_URL",
        "postgresql+psycopg2://task1-hermetic.invalid/idis",
    )
    if role is None:
        monkeypatch.delenv("IDIS_PROCESS_ROLE", raising=False)
    else:
        monkeypatch.setenv("IDIS_PROCESS_ROLE", role)
    monkeypatch.setattr("idis.persistence.db.is_postgres_configured", lambda: False)
    app = create_app(
        audit_sink=_HermeticPostgresAuditSink(),
        service_region="us-east-1",
        strict_profile=_reviewed_task1_profile(tmp_path / "profile", "api"),
    )

    asyncio.run(app.router.on_startup[0]())


def test_strict_api_role_starts_no_in_process_pipeline_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "api")
    monkeypatch.setenv(
        "IDIS_DATABASE_URL",
        "postgresql+psycopg2://task1-hermetic.invalid/idis",
    )
    monkeypatch.setattr("idis.persistence.db.is_postgres_configured", lambda: True)
    start_pipeline = AsyncMock()
    monkeypatch.setattr(api_main, "start_worker", start_pipeline)
    monkeypatch.setattr(api_main, "start_webhook_dispatcher_worker", AsyncMock())
    monkeypatch.setattr(
        "idis.services.compliance.janitor.start_compliance_janitor_worker",
        AsyncMock(),
    )
    app = create_app(
        audit_sink=_HermeticPostgresAuditSink(),
        service_region="us-east-1",
        strict_profile=_reviewed_task1_profile(tmp_path / "profile", "api"),
    )

    asyncio.run(app.router.on_startup[0]())

    start_pipeline.assert_not_awaited()


@pytest.mark.parametrize("role", [None, "", "api", "api,worker", "combined"])
def test_strict_worker_uses_reviewed_role_despite_ambient_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str | None,
) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    if role is None:
        monkeypatch.delenv("IDIS_PROCESS_ROLE", raising=False)
    else:
        monkeypatch.setenv("IDIS_PROCESS_ROLE", role)
    worker = PipelineWorker(
        tenant_ids=[TENANT_ID],
        poll_interval=0,
        strict_profile=_reviewed_task1_profile(tmp_path / "profile", "worker"),
    )

    async def exercise_worker_lifecycle() -> None:
        try:
            await worker.start()
        finally:
            await worker.stop()

    asyncio.run(exercise_worker_lifecycle())


class _ClaimRepo:
    def __init__(self) -> None:
        self.events: list[str] = []

    def try_mark_running(self, _run_id: str) -> bool:
        self.events.append("claimed")
        return True

    def try_complete_running(self, _run_id: str, **_kwargs: Any) -> bool:
        self.events.append("completed")
        return True

    def get(self, _run_id: str) -> dict[str, Any]:
        return {"status": "RUNNING"}


def _minimal_context() -> RunContext:
    return RunContext(
        run_id="00000000-0000-4000-8000-000000000103",
        tenant_id=TENANT_ID,
        deal_id="00000000-0000-4000-8000-000000000104",
        mode="SNAPSHOT",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
    )


def test_worker_claim_audit_is_fail_closed_and_precedes_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ClaimRepo()
    events = repo.events

    class Orchestrator:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(self, _ctx: RunContext) -> OrchestratorResult:
            events.append("orchestrated")
            return OrchestratorResult(status="SUCCEEDED")

    monkeypatch.setattr(execution_module, "RunOrchestrator", Orchestrator)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=repo,
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
        claim_identity={
            "worker_instance_id": "00000000-0000-4000-8000-000000000105",
            "worker_claim_id": "00000000-0000-4000-8000-000000000106",
        },
        claim_audit_emitter=lambda _ctx, _identity: events.append("claim_audited"),
        after_claim_commit=lambda: events.append("claim_committed"),
    )

    result = service.execute(_minimal_context())

    assert result.status == "SUCCEEDED"
    assert events[:4] == ["claimed", "claim_audited", "claim_committed", "orchestrated"]


def test_worker_claim_audit_failure_never_executes_or_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ClaimRepo()
    events = repo.events

    class ForbiddenOrchestrator:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("orchestrator must not start after claim-audit failure")

    monkeypatch.setattr(execution_module, "RunOrchestrator", ForbiddenOrchestrator)

    def fail_audit(_ctx: RunContext, _identity: dict[str, str]) -> None:
        events.append("claim_audit_failed")
        raise AuditSinkError("claim audit unavailable")

    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=repo,
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
        claim_identity={
            "worker_instance_id": "00000000-0000-4000-8000-000000000107",
            "worker_claim_id": "00000000-0000-4000-8000-000000000108",
        },
        claim_audit_emitter=fail_audit,
        after_claim_commit=lambda: events.append("claim_committed"),
    )

    with pytest.raises(AuditSinkError):
        service.execute(_minimal_context())

    assert events == ["claimed", "claim_audit_failed"]


def test_default_worker_factory_generates_claim_identity_without_new_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingService:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(worker_module, "RunExecutionService", CapturingService)
    monkeypatch.setattr(worker_module, "_default_worker_audit_sink", lambda: InMemoryAuditSink())
    monkeypatch.setattr(worker_module, "get_runs_repository", lambda *_args: object())
    monkeypatch.setattr(worker_module, "get_run_steps_repository", lambda *_args: object())

    worker_module._default_execution_service_factory(db_conn=object(), tenant_id=TENANT_ID)

    identity = captured["claim_identity"]
    uuid.UUID(identity["worker_instance_id"])
    uuid.UUID(identity["worker_claim_id"])
    assert callable(captured["claim_audit_emitter"])


def test_worker_claim_proof_uses_durable_in_transaction_audit_path() -> None:
    connection = object()
    captured: list[tuple[object, dict[str, Any]]] = []

    class Sink:
        def emit_in_tx(self, conn: object, event: dict[str, Any]) -> None:
            captured.append((conn, event))

    identity = {
        "worker_instance_id": "00000000-0000-4000-8000-000000000105",
        "worker_claim_id": "00000000-0000-4000-8000-000000000106",
    }
    ctx = _minimal_context()
    ctx.created_by_actor_id = "originating-human"
    ctx.created_by_actor_type = "HUMAN"
    ctx.strict_preflight_proof_id = "00000000-0000-4000-8000-000000000110"
    worker_module._emit_worker_claim_audit_in_tx(
        db_conn=connection,
        audit_sink=Sink(),
        ctx=ctx,
        claim_identity=identity,
    )

    assert len(captured) == 1
    assert captured[0][0] is connection
    event = captured[0][1]
    assert event["event_type"] == "deal.run.claimed"
    assert event["actor"] == {
        "actor_type": "SERVICE",
        "actor_id": "idis-pipeline-worker",
    }
    assert event["payload"]["safe"]["worker_instance_id"] == identity["worker_instance_id"]
    assert event["payload"]["safe"]["worker_claim_id"] == identity["worker_claim_id"]
    assert event["payload"]["safe"]["strict_preflight_proof_id"] == ctx.strict_preflight_proof_id


def _completed_step() -> RunStep:
    return RunStep(
        step_id="00000000-0000-4000-8000-000000000109",
        run_id="00000000-0000-4000-8000-000000000103",
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        step_order=1,
        status=StepStatus.COMPLETED,
        started_at="2026-07-15T00:00:00Z",
        finished_at="2026-07-15T00:00:01Z",
        result_summary={"document_count": 1},
    )


def test_strict_completed_step_invokes_resume_validator_before_trusting_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = InMemoryRunStepsRepository(TENANT_ID)
    repo.create(_completed_step())
    observed: list[StepName] = []
    ctx = _minimal_context()
    ctx.strict_resume_validation_required = True

    def accept_completed_step(_ctx: RunContext, step: RunStep) -> bool:
        observed.append(step.step_name)
        return True

    ctx.completed_step_resume_validator = accept_completed_step
    monkeypatch.setattr(execution_module, "RunOrchestrator", RunOrchestrator)
    monkeypatch.setattr("idis.services.runs.orchestrator.SNAPSHOT_STEPS", [StepName.INGEST_CHECK])

    result = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=repo,
    ).execute(ctx)

    assert result.status == "SUCCEEDED"
    assert observed == [StepName.INGEST_CHECK]


def test_strict_completed_step_validator_none_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = InMemoryRunStepsRepository(TENANT_ID)
    repo.create(_completed_step())
    ctx = _minimal_context()
    ctx.strict_resume_validation_required = True
    ctx.completed_step_resume_validator = lambda _ctx, _step: None
    monkeypatch.setattr("idis.services.runs.orchestrator.SNAPSHOT_STEPS", [StepName.INGEST_CHECK])

    result = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=repo,
    ).execute(ctx)

    assert result.status == "FAILED"
    assert result.error_code == "STEP_SEMANTIC_VALIDATION_FAILED"
    assert repo.get_step(ctx.run_id, StepName.INGEST_CHECK).status == StepStatus.FAILED


def test_strict_completed_step_without_validator_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = InMemoryRunStepsRepository(TENANT_ID)
    repo.create(_completed_step())
    ctx = _minimal_context()
    ctx.strict_resume_validation_required = True
    monkeypatch.setattr("idis.services.runs.orchestrator.SNAPSHOT_STEPS", [StepName.INGEST_CHECK])

    result = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=repo,
    ).execute(ctx)

    assert result.status == "FAILED"
    assert result.error_code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert repo.get_step(ctx.run_id, StepName.INGEST_CHECK).status == StepStatus.FAILED


def test_worker_watch_reuses_pipeline_worker_without_test_only_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.process_queued_runs as process_module

    calls: list[str] = []
    monkeypatch.setenv("IDIS_WORKER_LEASE_SECONDS", "60")
    monkeypatch.setenv("IDIS_WORKER_HEARTBEAT_SECONDS", "20")

    class Worker:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["poll_interval"] == 0.01
            assert kwargs["lease_config"].lease_seconds == 60
            assert kwargs["lease_config"].heartbeat_seconds == 20

        async def _process_queued_runs(self) -> int:
            calls.append("poll")
            return 0

    monkeypatch.setattr(
        process_module,
        "get_worker_tenant_ids",
        lambda *, env: [TENANT_ID],
    )

    asyncio.run(
        process_module.process_all_queued(
            watch=True,
            poll_seconds=0.01,
            worker_factory=Worker,
            stop_requested=lambda: len(calls) >= 2,
            sleep=lambda _seconds: asyncio.sleep(0),
        )
    )

    assert calls == ["poll", "poll"]


def test_strict_worker_watch_rejects_api_reviewed_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import scripts.process_queued_runs as process_module

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "api")

    with pytest.raises(RuntimeError, match="STRICT_PROFILE_MISMATCH"):
        asyncio.run(
            process_module.process_all_queued(
                watch=True,
                poll_seconds=0.01,
                strict_profile=_reviewed_task1_profile(tmp_path / "profile", "api"),
                worker_factory=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("invalid worker role must fail before construction")
                ),
            )
        )
