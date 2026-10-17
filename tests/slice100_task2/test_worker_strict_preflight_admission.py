"""Task 2 RED/green proof for strict worker-side admission revalidation.

The API consumes a reciprocal, one-use proof when it creates the run.  The
canonical production worker must independently re-read that durable link after
claim commit, in a fresh tenant-scoped transaction, before it composes any
provider context.

These are hermetic admission-mechanics tests, not canonical FULL or native
producer acceptance evidence. The globally inactive contract is not activated.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import idis.pipeline.worker as worker_module
from idis.persistence.repositories.strict_preflight_proofs import StrictPreflightProofError
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.worker import (
    PipelineWorker,
    WorkerLeaseConfig,
    build_postgres_claimed_run_runtime,
)
from idis.runtime.profile import StrictRuntimeProfile
from idis.services.runs.orchestrator import RunContext

TENANT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
DEAL_ID = "33333333-3333-4333-8333-333333333333"
PROOF_ID = "44444444-4444-4444-8444-444444444444"


class _Connection:
    def __init__(self, number: int, events: list[str]) -> None:
        self.number = number
        self.events = events
        self.active = False


class _ConnectionFactory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.connections: list[_Connection] = []

    @contextmanager
    def __call__(self) -> Any:
        connection = _Connection(len(self.connections) + 1, self.events)
        connection.active = True
        self.connections.append(connection)
        self.events.append(f"open:{connection.number}")
        try:
            yield connection
        finally:
            connection.active = False
            self.events.append(f"close:{connection.number}")


class _ProofRepository:
    def __init__(
        self,
        *,
        connection: _Connection,
        events: list[str],
        profile_hmac: str,
        error: StrictPreflightProofError | None = None,
        graph_error: StrictPreflightProofError | None = None,
    ) -> None:
        self._connection = connection
        self._events = events
        self._profile_hmac = profile_hmac
        self._error = error
        self._graph_error = graph_error

    def require_consumed_for_run(self, **binding: str) -> None:
        assert self._connection.active is True
        assert binding == {
            "proof_id": PROOF_ID,
            "run_id": RUN_ID,
            "deal_id": DEAL_ID,
            "strict_profile_generation": "acceptance-v1",
            "strict_profile_hmac": self._profile_hmac,
        }
        self._events.append("proof:reciprocal-consumed")
        if self._error is not None:
            raise self._error

    def require_task7_admission_graph_for_run(self, *, deal_id: str, run_id: str) -> None:
        assert self._connection.active is True
        assert (deal_id, run_id) == (DEAL_ID, RUN_ID)
        self._events.append("graph:verified")
        if self._graph_error is not None:
            raise self._graph_error


def _claimed_run() -> dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "deal_id": DEAL_ID,
        "mode": "FULL",
        "status": "RUNNING",
        "strict_preflight_proof_id": PROOF_ID,
        "lease_owner_id": "55555555-5555-4555-8555-555555555555",
        "lease_fencing_token": 7,
        "pipeline_contract_version": "full-strict-v21",
        "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
        "pipeline_resume_state": "RESUMABLE",
    }


def _context(**kwargs: Any) -> RunContext:
    return RunContext(
        run_id=str(kwargs["run_data"]["run_id"]),
        tenant_id=str(kwargs["tenant_id"]),
        deal_id=str(kwargs["run_data"]["deal_id"]),
        mode=str(kwargs["run_data"]["mode"]),
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
    )


def test_production_builder_revalidates_consumed_proof_in_fresh_closed_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(
        tmp_path,
        "worker",
        additions={
            "IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID,
            "IDIS_WORKER_TENANT_IDS": TENANT_ID,
        },
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    events: list[str] = []
    factory = _ConnectionFactory(events)

    def set_rls(connection: _Connection, tenant_id: str) -> None:
        assert connection.active is True
        assert tenant_id == TENANT_ID
        events.append(f"rls:{connection.number}")

    def proof_repository(connection: _Connection, tenant_id: str) -> _ProofRepository:
        assert connection.active is True
        assert tenant_id == TENANT_ID
        events.append(f"proof-repository:{connection.number}")
        return _ProofRepository(
            connection=connection,
            events=events,
            profile_hmac=profile.base_profile_hmac,
        )

    def build_default_context(**kwargs: Any) -> RunContext:
        bootstrap = kwargs["db_conn"]
        assert bootstrap.active is True
        events.append(f"provider-context:{bootstrap.number}")
        return _context(**kwargs)

    monkeypatch.setattr(worker_module, "set_tenant_local", set_rls)
    monkeypatch.setattr(
        worker_module,
        "get_strict_preflight_proofs_repository",
        proof_repository,
    )
    monkeypatch.setattr(worker_module, "_default_run_context_factory", build_default_context)

    runtime = build_postgres_claimed_run_runtime(
        tenant_id=TENANT_ID,
        run_data=_claimed_run(),
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
        audit_sink=object(),
        connection_factory=factory,
        strict_profile=profile,
    )

    assert runtime.context.run_id == RUN_ID
    assert events == [
        "open:1",
        "rls:1",
        "proof-repository:1",
        "proof:reciprocal-consumed",
        "proof-repository:1",
        "graph:verified",
        "close:1",
        "open:2",
        "rls:2",
        "provider-context:2",
        "close:2",
    ]
    assert all(connection.active is False for connection in factory.connections)


def test_production_builder_blocks_provider_composition_on_proof_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(
        tmp_path,
        "worker",
        additions={
            "IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID,
            "IDIS_WORKER_TENANT_IDS": TENANT_ID,
        },
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    events: list[str] = []
    factory = _ConnectionFactory(events)
    mismatch = StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

    monkeypatch.setattr(
        worker_module,
        "set_tenant_local",
        lambda connection, tenant_id: events.append(f"rls:{connection.number}:{tenant_id}"),
    )
    monkeypatch.setattr(
        worker_module,
        "get_strict_preflight_proofs_repository",
        lambda connection, _tenant_id: _ProofRepository(
            connection=connection,
            events=events,
            profile_hmac=profile.base_profile_hmac,
            error=mismatch,
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "_default_run_context_factory",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider composition must not precede strict proof validation")
        ),
    )

    with pytest.raises(StrictPreflightProofError) as exc_info:
        build_postgres_claimed_run_runtime(
            tenant_id=TENANT_ID,
            run_data=_claimed_run(),
            lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
            audit_sink=object(),
            connection_factory=factory,
            strict_profile=profile,
        )

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH"
    assert events == [
        "open:1",
        f"rls:1:{TENANT_ID}",
        "proof:reciprocal-consumed",
        "close:1",
    ]
    assert len(factory.connections) == 1
    assert factory.connections[0].active is False


def _install_worker_admission_mechanics(
    monkeypatch: pytest.MonkeyPatch,
    profile: StrictRuntimeProfile,
    events: list[str],
    *,
    graph_error: StrictPreflightProofError | None = None,
) -> None:
    def proof_repository(connection: _Connection, tenant_id: str) -> _ProofRepository:
        assert tenant_id == TENANT_ID
        return _ProofRepository(
            connection=connection,
            events=events,
            profile_hmac=profile.base_profile_hmac,
            graph_error=graph_error,
        )

    def corpus(**_kwargs: object) -> list[object]:
        events.append("corpus")
        return []

    def report(**_kwargs: object) -> SimpleNamespace:
        events.append("report")
        return SimpleNamespace(may_proceed=True)

    def context(**kwargs: Any) -> RunContext:
        events.append("provider-context")
        return _context(**kwargs)

    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: events.append("rls"))
    monkeypatch.setattr(worker_module, "get_strict_preflight_proofs_repository", proof_repository)
    monkeypatch.setattr(worker_module, "_load_worker_preflight_corpus", corpus)
    monkeypatch.setattr(worker_module, "build_strict_full_live_admission_report", report)
    monkeypatch.setattr(worker_module, "_default_run_context_factory", context)


def _check_admission_seam(
    seam: str,
    *,
    run_data: dict[str, Any],
    profile: StrictRuntimeProfile | None,
    factory: _ConnectionFactory,
    events: list[str],
) -> object:
    if seam == "production":
        return build_postgres_claimed_run_runtime(
            tenant_id=TENANT_ID,
            run_data=run_data,
            lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
            audit_sink=object(),
            connection_factory=factory,
            strict_profile=profile,
        )
    worker = PipelineWorker(
        tenant_ids=[TENANT_ID],
        strict_profile=profile,
        execution_service_factory=lambda **_kwargs: pytest.fail("service must not be composed"),
    )
    connection = _Connection(0, events)
    connection.active = True
    return worker._verify_claimed_run_preflight(
        conn=connection,
        tenant_id=TENANT_ID,
        run_data=run_data,
    )


@pytest.mark.parametrize("seam", ("production", "injected"))
@pytest.mark.parametrize(
    ("version", "contract_hash", "resume_state", "error_code"),
    [
        *(
            (version, "0" * 64, "NON_RESUMABLE", "PIPELINE_CONTRACT_UNAVAILABLE")
            for version in (
                "full-v1",
                "snapshot-v1",
                None,
                "full-strict-v2",
                "full-strict-v16",
                "full-strict-v17",
                "full-strict-v18",
                "full-strict-v19",
                "full-strict-v20",
                "full-strict-future",
            )
        ),
        ("full-strict-v21", "0" * 64, "RESUMABLE", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        ("full-strict-v21", None, "RESUMABLE", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        *(
            (
                "full-strict-v21",
                FULL_STRICT_V21_CONTRACT_HASH,
                state,
                "PIPELINE_CONTRACT_HASH_MISMATCH",
            )
            for state in ("NON_RESUMABLE", "HISTORICAL_TERMINAL", None)
        ),
    ],
)
def test_strict_full_rejects_unsafe_claim_before_consumed_proof_or_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
    version: str | None,
    contract_hash: str | None,
    resume_state: str | None,
    error_code: str,
) -> None:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(
        tmp_path,
        "worker",
        additions={"IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID, "IDIS_WORKER_TENANT_IDS": TENANT_ID},
    )
    events: list[str] = []
    factory = _ConnectionFactory(events)
    _install_worker_admission_mechanics(monkeypatch, profile, events)
    run_data = _claimed_run()
    run_data.update(
        pipeline_contract_version=version,
        pipeline_contract_hash=contract_hash,
        pipeline_resume_state=resume_state,
    )

    with pytest.raises(RuntimeError, match=f"^{error_code}$"):
        _check_admission_seam(
            seam, run_data=run_data, profile=profile, factory=factory, events=events
        )

    assert events == []
    assert factory.connections == []


def test_injected_current_strict_full_checks_graph_before_corpus_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(
        tmp_path,
        "worker",
        additions={"IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID, "IDIS_WORKER_TENANT_IDS": TENANT_ID},
    )
    events: list[str] = []
    factory = _ConnectionFactory(events)
    _install_worker_admission_mechanics(monkeypatch, profile, events)

    _check_admission_seam(
        "injected", run_data=_claimed_run(), profile=profile, factory=factory, events=events
    )

    assert events == ["proof:reciprocal-consumed", "graph:verified", "corpus", "report"]
    assert factory.connections == []


@pytest.mark.parametrize("seam", ("production", "injected"))
def test_current_strict_full_rejects_graph_failure_before_corpus_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(
        tmp_path,
        "worker",
        additions={"IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID, "IDIS_WORKER_TENANT_IDS": TENANT_ID},
    )
    events: list[str] = []
    factory = _ConnectionFactory(events)
    _install_worker_admission_mechanics(
        monkeypatch,
        profile,
        events,
        graph_error=StrictPreflightProofError("STEP_REHYDRATION_FAILED"),
    )

    with pytest.raises(StrictPreflightProofError, match="^STEP_REHYDRATION_FAILED$"):
        _check_admission_seam(
            seam, run_data=_claimed_run(), profile=profile, factory=factory, events=events
        )

    assert "proof:reciprocal-consumed" in events
    assert "graph:verified" in events
    assert "corpus" not in events
    assert "report" not in events
    assert "provider-context" not in events
    assert all(connection.active is False for connection in factory.connections)


@pytest.mark.parametrize("seam", ("production", "injected"))
@pytest.mark.parametrize(
    ("strict", "mode"), ((False, "FULL"), (False, "SNAPSHOT"), (True, "SNAPSHOT"))
)
def test_legacy_compatibility_outside_strict_full_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str, strict: bool, mode: str
) -> None:
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(
        tmp_path,
        "worker",
        additions={"IDIS_ACCEPTANCE_TENANT_ID": TENANT_ID, "IDIS_WORKER_TENANT_IDS": TENANT_ID},
    )
    events: list[str] = []
    factory = _ConnectionFactory(events)
    _install_worker_admission_mechanics(monkeypatch, profile, events)
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    run_data = _claimed_run()
    run_data.update(
        mode=mode,
        pipeline_contract_version="full-v1",
        pipeline_contract_hash="0" * 64,
        pipeline_resume_state="NON_RESUMABLE",
    )

    _check_admission_seam(
        seam,
        run_data=run_data,
        profile=profile if strict else None,
        factory=factory,
        events=events,
    )

    assert "proof:reciprocal-consumed" not in events
    assert "graph:verified" not in events
    assert "corpus" not in events
    assert "report" not in events
    assert ("provider-context" in events) == (seam == "production")


@pytest.mark.parametrize("seam", ("production", "injected"))
@pytest.mark.parametrize("version", ("full-v1", "full-strict-v21"))
def test_strict_full_missing_profile_precedes_new_admission_guard(
    monkeypatch: pytest.MonkeyPatch, seam: str, version: str
) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    events: list[str] = []
    factory = _ConnectionFactory(events)
    run_data = _claimed_run()
    run_data["pipeline_contract_version"] = version

    with pytest.raises(RuntimeError, match="^STRICT_PROFILE_LOAD_FAILED$"):
        _check_admission_seam(seam, run_data=run_data, profile=None, factory=factory, events=events)

    assert events == []
    assert factory.connections == []
