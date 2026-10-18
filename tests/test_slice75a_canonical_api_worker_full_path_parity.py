"""Slice75A RED tests for canonical API/worker FULL path parity.

These tests intentionally define target behavior for Slice75A and are expected
to fail before production implementation is added.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.models.run_source import RunSource
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.persistence.repositories.strict_preflight_proofs import StrictPreflightProofError
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.worker import (
    InvalidRunSourceMetadataError,
    InvalidRunSourceSelectionError,
    PipelineWorker,
    WorkerLeaseConfig,
    _default_execution_service_factory,
    _default_run_context_factory,
)
from idis.services.runs.execution import RunExecutionResult
from tests._cir04c3_required_frozen_preview import (
    CapturingRequiredPostgresAuditSink,
    required_frozen_preview,
)
from tests.abac_seed import seed_deal_access

API_KEY = "slice75a-red-key"
TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
LEASE_CONFIG = WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20)
COMMERCIAL_DD_VERSION_RECORD_ID = "10000000-0000-4000-8000-000000000075"
FINANCIAL_DD_VERSION_RECORD_ID = "20000000-0000-4000-8000-000000000075"


def _full_strict_v21_seed() -> dict[str, str]:
    return {
        "pipeline_contract_version": "full-strict-v21",
        "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
        "pipeline_resume_state": "RESUMABLE",
    }


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
    """Minimal recorder preserving the legacy API/worker parity focus."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    def create_run_selection(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(dict(kwargs))
        return {"selection_id": "30000000-0000-4000-8000-000000000075"}


@pytest.fixture(autouse=True)
def _admit_fictional_methodology_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> _LegacyMethodologySelectionsRepository:
    repository = _LegacyMethodologySelectionsRepository()
    monkeypatch.setattr(
        "idis.api.routes.runs._validate_full_methodology_selection",
        lambda **_kwargs: repository,
    )
    return repository


class _TransactionalInMemoryAuditSink(InMemoryAuditSink):
    """In-memory audit double supporting the durable in-transaction seam."""

    def emit_in_tx(self, _conn: object, event: dict[str, Any]) -> None:
        self.emit(event)


@dataclass
class _RunsRepoStub:
    """Simple tenant-scoped queue repository stub for worker tests."""

    queued: list[dict[str, Any]]
    complete_calls: list[tuple[str, str, str | None]]
    claim_calls: int = 0
    reclaim_calls: int = 0
    lease_fencing_token: int = 0
    last_claimed: dict[str, Any] | None = None

    def requeue_expired_worker_leases(
        self,
        *,
        limit: int = 1,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        assert limit == 1
        assert lease_audit_emitter is not None
        self.reclaim_calls += 1
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
        self.claim_calls += 1
        if not self.queued or self.last_claimed is not None:
            return []
        self.lease_fencing_token += 1
        self.last_claimed = {
            **self.queued[0],
            "status": "RUNNING",
            "lease_owner_id": lease_owner_id,
            "lease_fencing_token": self.lease_fencing_token,
            "lease_expires_at": "2099-01-01T00:01:00Z",
            "last_heartbeat_at": "2099-01-01T00:00:00Z",
        }
        lease_audit_emitter(
            db_conn=None,
            code="WORKER_LEASE_CLAIMED",
            run_id=self.last_claimed["run_id"],
            deal_id=self.last_claimed["deal_id"],
            lease_owner_id=lease_owner_id,
            new_fencing_token=self.lease_fencing_token,
            lease_expires_at=self.last_claimed["lease_expires_at"],
        )
        return [self.last_claimed]

    def count_queued_runs(self) -> int:
        return int(bool(self.queued) and self.last_claimed is None)

    def try_mark_running(self, run_id: str) -> bool:
        return True

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.complete_calls.append((run_id, status, finished_at))


class _BlockingStrictReport:
    may_proceed = False

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        assert mode == "json"
        return {
            "required": True,
            "may_proceed": False,
            "blocker_count": 1,
            "blocking_components": ["live_llm_model_clients"],
            "components": [],
        }


def _create_hermetic_strict_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Compose injected in-memory API dependencies, then restore strict request inputs."""
    with monkeypatch.context() as composition_env:
        composition_env.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
        composition_env.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
        return create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")


def _reviewed_worker_profile(tmp_path: Path) -> Any:
    """Build a real reviewed worker profile for legacy strict worker behavior tests."""
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    return _reviewed_profile(
        tmp_path,
        "worker",
        additions={
            "IDIS_ACCEPTANCE_TENANT_ID": TENANT_A,
            "IDIS_WORKER_TENANT_IDS": TENANT_A,
        },
    )


@dataclass
class _ExactProofRepository:
    """Record the two proof reads and reject every other identity."""

    proof_id: str
    run_id: str
    deal_id: str
    generation: str
    profile_hmac: str
    events: list[str]

    def require_consumed_for_run(self, **kwargs: object) -> None:
        self.events.append("require_consumed_for_run")
        expected = {
            "proof_id": self.proof_id,
            "run_id": self.run_id,
            "deal_id": self.deal_id,
            "strict_profile_generation": self.generation,
            "strict_profile_hmac": self.profile_hmac,
        }
        if kwargs != expected:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

    def require_task7_admission_graph_for_run(self, **kwargs: object) -> None:
        self.events.append("require_task7_admission_graph_for_run")
        if kwargs != {"deal_id": self.deal_id, "run_id": self.run_id}:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")


def _proof_repository_factory(
    repository: _ExactProofRepository,
) -> Callable[..., _ExactProofRepository]:
    def factory(_conn: Any, tenant_id: str) -> _ExactProofRepository:
        if tenant_id != TENANT_A:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID")
        return repository

    return factory


def _exact_proof_repository(
    *,
    proof_id: str,
    run_id: str,
    deal_id: str,
    profile: Any,
    events: list[str],
) -> _ExactProofRepository:
    return _ExactProofRepository(
        proof_id=proof_id,
        run_id=run_id,
        deal_id=deal_id,
        generation=profile.generation,
        profile_hmac=profile.base_profile_hmac,
        events=events,
    )


def _frozen_selected_loader(documents: list[dict[str, Any]]) -> Callable[..., list[dict[str, Any]]]:
    """Validate the stored source, then refuse an absent selected id."""

    def load(
        *,
        db_conn: Any,
        tenant_id: str,
        run_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        del db_conn, tenant_id
        try:
            source = RunSource.model_validate(run_data.get("source"))
        except ValidationError as exc:
            raise InvalidRunSourceMetadataError("invalid run source metadata") from exc
        selected = list(source.document_ids)
        present = {str(document["document_id"]) for document in documents}
        if any(document_id not in present for document_id in selected):
            raise InvalidRunSourceSelectionError(
                "run source selected unfrozen or missing documents"
            )
        chosen = set(selected)
        return [document for document in documents if str(document["document_id"]) in chosen]

    return load


def _present_preflight_doc(*, deal_id: str, document_id: str) -> dict[str, Any]:
    document = _preflight_doc(deal_id=deal_id)
    document["document_id"] = document_id
    document["spans"][0]["document_id"] = document_id
    return document


def _assert_proof_called_before_block(events: list[str]) -> None:
    assert events.index("require_consumed_for_run") < events.index(
        "require_task7_admission_graph_for_run"
    )
    assert events.index("require_task7_admission_graph_for_run") < events.index("strict_block")


def _record_real_strict_block(events: list[str]) -> Callable[..., Any]:
    from idis.services.runs.strict_full_live import (
        build_strict_full_live_admission_report as real_report,
    )

    def wrapped(**kwargs: Any) -> Any:
        events.append("strict_block")
        return real_report(**kwargs)

    return wrapped


def test_worker_full_strict_gate_blocks_before_run_execution_service_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Worker FULL strict preflight must block before canonical execution service."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = _RunsRepoStub(
        queued=[
            {
                **_full_strict_v21_seed(),
                "run_id": "run-full-1",
                "tenant_id": TENANT_A,
                "deal_id": "deal-1",
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-1"]},
                "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000210",
            }
        ],
        complete_calls=[],
    )
    execution_service = MagicMock()
    execution_service.audit_sink = InMemoryAuditSink()
    profile = _reviewed_worker_profile(tmp_path)
    events: list[str] = []
    repository = _exact_proof_repository(
        proof_id="00000000-0000-4000-8000-000000000210",
        run_id="run-full-1",
        deal_id="deal-1",
        profile=profile,
        events=events,
    )

    def blocking_report(**_kwargs: Any) -> _BlockingStrictReport:
        events.append("strict_block")
        return _BlockingStrictReport()

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
        lease_config=LEASE_CONFIG,
        strict_profile=profile,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=_TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.get_strict_preflight_proofs_repository",
            _proof_repository_factory(repository),
        ),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            _frozen_selected_loader(
                [_present_preflight_doc(deal_id="deal-1", document_id="doc-1")]
            ),
        ),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            side_effect=blocking_report,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        pytest.raises(RuntimeError, match="STRICT_FULL_LIVE_BLOCKED"),
    ):
        asyncio.run(worker._process_queued_runs())

    _assert_proof_called_before_block(events)
    assert execution_service.execute_preclaimed.call_count == 0
    assert runs_repo.complete_calls == []
    assert runs_repo.last_claimed is not None
    assert runs_repo.last_claimed["status"] == "RUNNING"
    assert runs_repo.last_claimed["lease_fencing_token"] == 1


def test_api_and_worker_share_strict_readiness_helper_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """API and worker strict FULL paths should call one shared readiness helper."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    api_calls: list[dict[str, Any]] = []
    worker_calls: list[dict[str, Any]] = []
    events: list[str] = []

    def record_api_strict_call(**kwargs: Any) -> _BlockingStrictReport:
        api_calls.append(kwargs)
        return _BlockingStrictReport()

    def record_worker_strict_call(**kwargs: Any) -> _BlockingStrictReport:
        events.append("strict_block")
        worker_calls.append(kwargs)
        return _BlockingStrictReport()

    app = _create_hermetic_strict_app(monkeypatch)
    app.state.deal_documents = {}
    client = TestClient(app)
    create_resp = client.post(
        "/v1/deals",
        json={"name": "Slice75A Strict", "company_name": "StrictCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_A, deal_id, "actor-a")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    with patch(
        "idis.api.routes.runs.build_strict_full_live_admission_report",
        side_effect=record_api_strict_call,
    ):
        api_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json=_full_run_request(
                strict_preflight_proof_id="00000000-0000-4000-8000-000000000201"
            ),
            headers={"X-IDIS-API-Key": API_KEY},
        )
    assert api_resp.status_code == 409

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                **_full_strict_v21_seed(),
                "run_id": "run-full-2",
                "tenant_id": TENANT_A,
                "deal_id": deal_id,
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-strict"]},
                "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000211",
            }
        ],
        complete_calls=[],
    )
    profile = _reviewed_worker_profile(tmp_path)
    repository = _exact_proof_repository(
        proof_id="00000000-0000-4000-8000-000000000211",
        run_id="run-full-2",
        deal_id=deal_id,
        profile=profile,
        events=events,
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: MagicMock(
            audit_sink=InMemoryAuditSink(),
            execute_preclaimed=MagicMock(),
        ),
        run_context_factory=lambda **_kwargs: MagicMock(),
        lease_config=LEASE_CONFIG,
        strict_profile=profile,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=_TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.get_strict_preflight_proofs_repository",
            _proof_repository_factory(repository),
        ),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            _frozen_selected_loader([_preflight_doc(deal_id=deal_id)]),
        ),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            side_effect=record_worker_strict_call,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        pytest.raises(RuntimeError, match="STRICT_FULL_LIVE_BLOCKED"),
    ):
        asyncio.run(worker._process_queued_runs())

    _assert_proof_called_before_block(events)
    assert len(api_calls) == 1
    assert len(worker_calls) == 1
    api_call = api_calls[0]
    worker_call = worker_calls[0]
    assert api_call["tenant_id"] == worker_call["tenant_id"] == TENANT_A
    assert api_call["strict_dotenv_path"] == worker_call["strict_dotenv_path"]
    assert _selected_preflight_document_ids(
        api_call["preflight_corpus"]
    ) == _selected_preflight_document_ids(worker_call["preflight_corpus"])
    assert _content_safe_preflight_shape(
        api_call["preflight_corpus"]
    ) == _content_safe_preflight_shape(worker_call["preflight_corpus"])


def test_worker_strict_block_metadata_is_leakage_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A strict block stays safe while its committed lease awaits explicit reclaim."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    secret = "postgresql://user:secret@localhost:5432/idis"

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                **_full_strict_v21_seed(),
                "run_id": "run-full-3",
                "tenant_id": TENANT_A,
                "deal_id": "deal-3",
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-3"]},
                "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000212",
            }
        ],
        complete_calls=[],
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    execution_service = MagicMock(
        audit_sink=InMemoryAuditSink(),
        execute_preclaimed=MagicMock(
            return_value=RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                steps=[],
            )
        ),
    )
    profile = _reviewed_worker_profile(tmp_path)
    events: list[str] = []
    repository = _exact_proof_repository(
        proof_id="00000000-0000-4000-8000-000000000212",
        run_id="run-full-3",
        deal_id="deal-3",
        profile=profile,
        events=events,
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
        lease_config=LEASE_CONFIG,
        strict_profile=profile,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=_TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.get_strict_preflight_proofs_repository",
            _proof_repository_factory(repository),
        ),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            _frozen_selected_loader(
                [_present_preflight_doc(deal_id="deal-3", document_id="doc-3")]
            ),
        ),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            side_effect=_record_real_strict_block(events),
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        pytest.raises(RuntimeError, match="STRICT_FULL_LIVE_BLOCKED") as exc_info,
    ):
        asyncio.run(worker._process_queued_runs())

    _assert_proof_called_before_block(events)
    assert execution_service.execute_preclaimed.call_count == 0
    assert runs_repo.complete_calls == []
    assert runs_repo.last_claimed is not None
    assert runs_repo.last_claimed["status"] == "RUNNING"
    assert run_steps_repo.get_by_run_id("run-full-3") == []
    assert secret not in str(exc_info.value)


def test_worker_missing_selected_document_fails_closed_with_invalid_run_source_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing selected docs fail closed before any preclaimed execution begins."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                **_full_strict_v21_seed(),
                "run_id": "run-full-missing-source",
                "tenant_id": TENANT_A,
                "deal_id": "deal-missing-source",
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["missing-doc"]},
                "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000213",
            }
        ],
        complete_calls=[],
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    execution_service = MagicMock(
        audit_sink=InMemoryAuditSink(),
        execute_preclaimed=MagicMock(
            return_value=RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                steps=[],
            )
        ),
    )
    profile = _reviewed_worker_profile(tmp_path)
    events: list[str] = []
    repository = _exact_proof_repository(
        proof_id="00000000-0000-4000-8000-000000000213",
        run_id="run-full-missing-source",
        deal_id="deal-missing-source",
        profile=profile,
        events=events,
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
        lease_config=LEASE_CONFIG,
        strict_profile=profile,
    )
    strict_helper = MagicMock(return_value=_BlockingStrictReport())

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=_TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.get_strict_preflight_proofs_repository",
            _proof_repository_factory(repository),
        ),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            _frozen_selected_loader([_preflight_doc(deal_id="deal-missing-source")]),
        ),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            strict_helper,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        pytest.raises(InvalidRunSourceSelectionError) as exc_info,
    ):
        asyncio.run(worker._process_queued_runs())

    assert events == [
        "require_consumed_for_run",
        "require_task7_admission_graph_for_run",
    ]
    assert execution_service.execute_preclaimed.call_count == 0
    assert strict_helper.call_count == 0
    assert runs_repo.complete_calls == []
    assert runs_repo.last_claimed is not None
    assert runs_repo.last_claimed["status"] == "RUNNING"
    assert run_steps_repo.get_by_run_id("run-full-missing-source") == []
    encoded_error = str(exc_info.value)
    assert "raw_text" not in encoded_error
    assert "object_key" not in encoded_error
    assert "postgresql://" not in encoded_error.lower()
    assert "missing-doc" not in encoded_error


def test_api_and_worker_full_paths_produce_same_strict_block_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """API and leased worker FULL paths align on the stable strict-block reason."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))

    app = _create_hermetic_strict_app(monkeypatch)
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Slice75A Parity", "company_name": "ParityCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_A, deal_id, "actor-a")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    api_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json=_full_run_request(strict_preflight_proof_id="00000000-0000-4000-8000-000000000202"),
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert api_resp.status_code == 409
    assert api_resp.json()["code"] == "STRICT_FULL_LIVE_BLOCKED"

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                **_full_strict_v21_seed(),
                "run_id": "run-full-4",
                "tenant_id": TENANT_A,
                "deal_id": deal_id,
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-strict"]},
                "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000214",
            }
        ],
        complete_calls=[],
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    execution_service = MagicMock(
        audit_sink=InMemoryAuditSink(),
        execute_preclaimed=MagicMock(
            return_value=RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                steps=[],
            )
        ),
    )
    profile = _reviewed_worker_profile(tmp_path)
    events: list[str] = []
    repository = _exact_proof_repository(
        proof_id="00000000-0000-4000-8000-000000000214",
        run_id="run-full-4",
        deal_id=deal_id,
        profile=profile,
        events=events,
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
        lease_config=LEASE_CONFIG,
        strict_profile=profile,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=_TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.get_strict_preflight_proofs_repository",
            _proof_repository_factory(repository),
        ),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            _frozen_selected_loader([_preflight_doc(deal_id=deal_id)]),
        ),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            side_effect=_record_real_strict_block(events),
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        pytest.raises(RuntimeError, match="STRICT_FULL_LIVE_BLOCKED") as exc_info,
    ):
        asyncio.run(worker._process_queued_runs())

    _assert_proof_called_before_block(events)
    assert execution_service.execute_preclaimed.call_count == 0
    assert runs_repo.complete_calls == []
    assert runs_repo.last_claimed is not None
    assert runs_repo.last_claimed["status"] == "RUNNING"
    assert run_steps_repo.get_by_run_id("run-full-4") == []
    assert getattr(exc_info.value, "code", str(exc_info.value)) == api_resp.json()["code"]


def test_worker_respects_tenant_scope_and_does_not_claim_other_tenant() -> None:
    """Worker should only poll configured tenants and never claim other tenants' rows."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    repo_a = _RunsRepoStub(queued=[], complete_calls=[])
    repo_b = _RunsRepoStub(
        queued=[{"run_id": "run-b", "tenant_id": TENANT_B, "deal_id": "deal-b", "mode": "FULL"}],
        complete_calls=[],
    )

    def select_repo(_conn: Any, tenant_id: str) -> _RunsRepoStub:
        return repo_a if tenant_id == TENANT_A else repo_b

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: MagicMock(
            audit_sink=InMemoryAuditSink(),
            execute_preclaimed=MagicMock(),
        ),
        run_context_factory=lambda **_kwargs: MagicMock(),
        lease_config=LEASE_CONFIG,
    )
    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", side_effect=select_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=_TransactionalInMemoryAuditSink(),
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert repo_a.claim_calls == 1
    assert repo_a.reclaim_calls == 1
    assert repo_b.claim_calls == 0
    assert repo_b.reclaim_calls == 0


def test_api_queue_race_returns_deterministic_run_already_claimed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """API contract: synchronous claim owner returns RUN_ALREADY_CLAIMED on race loss."""
    tenant_id = str(uuid4())
    api_keys = _api_keys()
    api_keys[API_KEY]["tenant_id"] = tenant_id
    with required_frozen_preview(monkeypatch) as preview_db:
        preview_db.add_tenant(tenant_id, "me-south-1")
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
        monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
        app = create_app(
            audit_sink=CapturingRequiredPostgresAuditSink(InMemoryAuditSink()),
            service_region="me-south-1",
        )
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Slice75A Race", "company_name": "RaceCo"},
            headers={"X-IDIS-API-Key": API_KEY},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        seed_deal_access(tenant_id, deal_id, "actor-a")
        package_id = preview_db.prepare_pdf(tenant_id, deal_id, tmp_path / "race-pkg")
        document_id = str(preview_db.package_rows(tenant_id, deal_id, package_id)[0]["document_id"])

        with patch(
            "idis.services.runs.execution.RunExecutionService.execute",
            return_value=RunExecutionResult(claimed=False, status="NOT_CLAIMED"),
        ):
            run_resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json=_full_run_request(
                    source={"type": "deal_documents", "document_ids": [document_id]}
                ),
                headers={"X-IDIS-API-Key": API_KEY},
            )

        assert run_resp.status_code == 409
        body = run_resp.json()
        assert body["code"] == "RUN_ALREADY_CLAIMED"


def test_worker_uses_durable_audit_sink_when_db_configured_or_explicit_blocker() -> None:
    """Worker default service should not silently use in-memory audit in DB mode."""
    service = _default_execution_service_factory(
        db_conn=MagicMock(),
        tenant_id=TENANT_A,
    )
    assert not isinstance(service.audit_sink, InMemoryAuditSink)


def test_worker_default_audit_setup_fails_closed_when_durable_sink_unavailable() -> None:
    """Worker default service must not silently fall back to in-memory audit."""
    with (
        patch(
            "idis.audit.postgres_sink.PostgresAuditSink",
            side_effect=RuntimeError("postgres audit unavailable"),
        ),
        pytest.raises(RuntimeError, match="WORKER_AUDIT_SINK_UNAVAILABLE"),
    ):
        _default_execution_service_factory(
            db_conn=MagicMock(),
            tenant_id=TENANT_A,
        )


def _frozen_corpus_or_missing_source(**kwargs: Any) -> list[dict[str, Any]]:
    """Return the corpus, refusing only when a selected id is absent."""
    corpus = [_preflight_doc(deal_id="deal-context")]
    selected = [str(document_id) for document_id in (kwargs.get("selected_document_ids") or ())]
    present = {str(document["document_id"]) for document in corpus}
    if any(document_id not in present for document_id in selected):
        raise ValueError("FROZEN_SOURCE_REQUIRED")
    if not selected:
        return list(corpus)
    selected_ids = set(selected)
    return [document for document in corpus if str(document["document_id"]) in selected_ids]


def test_worker_default_run_context_factory_uses_validated_preflight_source_path() -> None:
    """Worker context build must reuse selected-source fail-closed validation."""
    build_run_context = MagicMock(return_value=MagicMock())
    with (
        patch(
            "idis.services.runs.steps.load_document_preflight_corpus_for_deal",
            side_effect=_frozen_corpus_or_missing_source,
        ),
        patch("idis.services.runs.steps.build_run_context", build_run_context),
        pytest.raises(InvalidRunSourceSelectionError),
    ):
        _default_run_context_factory(
            db_conn=MagicMock(),
            tenant_id=TENANT_A,
            run_data={
                "run_id": "run-context-missing-source",
                "deal_id": "deal-context",
                "mode": "FULL",
                "pipeline_contract_version": "full-v1",
                "pipeline_contract_hash": (
                    "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"
                ),
                "pipeline_resume_state": "NON_RESUMABLE",
                "source": {"type": "deal_documents", "document_ids": ["missing-doc"]},
            },
            audit_sink=MagicMock(),
        )

    assert build_run_context.call_count == 0


def test_worker_default_run_context_factory_builds_context_when_selected_id_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selected id that is present in the corpus must build a run context."""
    build_run_context = MagicMock(return_value=MagicMock(name="present-source-context"))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    with (
        patch(
            "idis.services.runs.steps.load_document_preflight_corpus_for_deal",
            side_effect=_frozen_corpus_or_missing_source,
        ),
        patch("idis.services.runs.steps.build_run_context", build_run_context),
        patch(
            "idis.pipeline.worker._load_worker_deal_metadata",
            return_value={"company_name": "Synthetic"},
        ),
    ):
        context = _default_run_context_factory(
            db_conn=MagicMock(),
            tenant_id=TENANT_A,
            run_data={
                "run_id": "run-context-present-source",
                "deal_id": "deal-context",
                "mode": "FULL",
                "pipeline_contract_version": "full-v1",
                "pipeline_contract_hash": (
                    "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"
                ),
                "pipeline_resume_state": "NON_RESUMABLE",
                "source": {"type": "deal_documents", "document_ids": ["doc-strict"]},
            },
            audit_sink=MagicMock(),
        )

    assert context is build_run_context.return_value
    assert build_run_context.call_count == 1
    documents = build_run_context.call_args.kwargs["documents"]
    assert [document["document_id"] for document in documents] == ["doc-strict"]


def test_pipeline_executor_is_not_public_production_execution_path() -> None:
    """Legacy PipelineExecutor should not remain publicly exported for production use."""
    import idis.pipeline as pipeline_module

    assert "PipelineExecutor" not in getattr(pipeline_module, "__all__", [])
    assert not hasattr(pipeline_module, "PipelineExecutor")


def _api_keys() -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": TENANT_A,
            "actor_id": "actor-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        }
    }


def _preflight_doc(*, deal_id: str) -> dict[str, Any]:
    return {
        "tenant_id": TENANT_A,
        "deal_id": deal_id,
        "document_id": "doc-strict",
        "doc_id": "artifact-doc-strict",
        "doc_type": "DOCX",
        "parse_status": "PARSED",
        "document_name": "doc-strict.docx",
        "sha256": "a" * 64,
        "uri": "deals/doc-strict.docx",
        "metadata": {},
        "source_metadata": {},
        "spans": [
            {
                "span_id": "span-strict",
                "tenant_id": TENANT_A,
                "deal_id": deal_id,
                "document_id": "doc-strict",
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "Highly sensitive raw revenue sentence",
                "content_hash": "b" * 64,
            }
        ],
    }


def _selected_preflight_document_ids(preflight_corpus: Any) -> list[str]:
    corpus = preflight_corpus or []
    return sorted(str(doc.get("document_id", "")) for doc in corpus)


def _content_safe_preflight_shape(preflight_corpus: Any) -> list[dict[str, Any]]:
    corpus = preflight_corpus or []
    return [
        {
            "document_id": doc.get("document_id"),
            "doc_id": doc.get("doc_id"),
            "doc_type": doc.get("doc_type"),
            "parse_status": doc.get("parse_status"),
            "document_name": doc.get("document_name"),
            "span_ids": sorted(str(span.get("span_id", "")) for span in (doc.get("spans") or [])),
        }
        for doc in corpus
    ]
