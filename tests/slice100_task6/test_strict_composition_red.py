"""RED tests proving strict composition cannot reach synthetic methodology seams."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import idis.pipeline.worker as worker_module
from idis.services.extraction.task_executor import InMemoryMethodologyExtractionTaskExecutor
from idis.services.extraction.task_planner import InMemoryExtractionTaskPlanner
from idis.services.runs.methodology_claim_materialization import (
    InMemoryRunMethodologyClaimMaterializationService,
)
from idis.services.runs.methodology_evidence_item_materialization import (
    InMemoryRunMethodologyEvidenceItemMaterializationService,
)
from idis.services.runs.methodology_extraction_task_execution import (
    InMemoryRunMethodologyExtractionTaskExecutionService,
)
from idis.services.runs.methodology_sanad_creation_linking_grading import (
    InMemoryRunMethodologySanadCreationLinkingGradingService,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError
from tests.slice100_task6._support import DEAL_ID, RUN_ID, TENANT_ID
from tests.test_extraction_task_planner import _classification, _registry, _span

_MISSING_METADATA = object()
_ACTIVE_RUN_STEP_ID = "66666666-6666-4666-8666-666666666666"
_STRICT_COMPOSITION_STAGES = (
    ("planning", False),
    ("planning_rehydrate", False),
    ("execution", True),
    ("execution_rehydrate", True),
    ("claim_materialization", False),
    ("claim_rehydrate", False),
    ("evidence_materialization", False),
    ("evidence_rehydrate", False),
    ("sanad_materialization", False),
    ("sanad_rehydrate", False),
)


class _ExpectedCallbackInvocation(Exception):
    """Sentinel proving strict validation admitted a callback before result handling."""


def _callback_with_metadata(
    *,
    persistence_backend: object = _MISSING_METADATA,
    external_calls_enabled: object = _MISSING_METADATA,
) -> tuple[Any, dict[str, bool]]:
    invocations = {"callback": False, "rehydrate": False}

    def callback(**_kwargs: Any) -> None:
        invocations["callback"] = True
        raise _ExpectedCallbackInvocation

    def rehydrate(**_kwargs: Any) -> None:
        invocations["rehydrate"] = True
        raise _ExpectedCallbackInvocation

    if persistence_backend is not _MISSING_METADATA:
        callback.persistence_backend = persistence_backend  # type: ignore[attr-defined]
    if external_calls_enabled is not _MISSING_METADATA:
        callback.external_calls_enabled = external_calls_enabled  # type: ignore[attr-defined]
    callback.rehydrate = rehydrate  # type: ignore[attr-defined]
    return callback, invocations


def _invoke_strict_composition_stage(stage: str, callback: Any) -> None:
    context = _strict_context(
        active_run_step_id=_ACTIVE_RUN_STEP_ID,
        document_preflight_summary={"contract_version": "document-preflight-v2"},
    )
    if stage == "planning":
        context.methodology_extraction_task_planning_fn = callback
        RunOrchestrator._execute_methodology_extraction_task_planning(  # type: ignore[arg-type]
            object(), context, {}
        )
        return
    if stage == "planning_rehydrate":
        context.methodology_extraction_tasks = []
        context.methodology_extraction_task_planning_fn = callback
        RunOrchestrator._rehydrate_methodology_extraction_tasks(  # type: ignore[arg-type]
            object(), context, {}
        )
        return
    if stage == "execution":
        context.methodology_extraction_task_execution_fn = callback
        RunOrchestrator._execute_methodology_extraction_task_execution(  # type: ignore[arg-type]
            object(), context
        )
        return
    if stage == "execution_rehydrate":
        context.methodology_extraction_task_execution_fn = callback
        RunOrchestrator._rehydrate_methodology_extraction_execution(  # type: ignore[arg-type]
            object(), context, {}
        )
        return
    if stage == "claim_materialization":
        context.methodology_extraction_execution_result = object()  # type: ignore[assignment]
        context.methodology_claim_materialization_fn = callback
        RunOrchestrator._execute_methodology_claim_materialization(  # type: ignore[arg-type]
            object(), context
        )
        return
    if stage == "claim_rehydrate":
        context.methodology_claim_materialization_fn = callback
        RunOrchestrator._rehydrate_methodology_materialized_claims(  # type: ignore[arg-type]
            object(), context, {}
        )
        return
    if stage == "evidence_materialization":
        context.methodology_materialized_claims = []
        context.methodology_evidence_item_materialization_fn = callback
        RunOrchestrator._execute_methodology_evidence_item_materialization(  # type: ignore[arg-type]
            object(), context
        )
        return
    if stage == "evidence_rehydrate":
        context.methodology_evidence_item_materialization_fn = callback
        RunOrchestrator._rehydrate_methodology_evidence_items(  # type: ignore[arg-type]
            object(), context, {}
        )
        return
    if stage == "sanad_materialization":
        context.methodology_materialized_claims = []
        context.methodology_evidence_items = []
        context.methodology_sanad_creation_linking_grading_fn = callback
        RunOrchestrator._execute_methodology_sanad_creation_linking_grading(  # type: ignore[arg-type]
            object(), context
        )
        return
    if stage == "sanad_rehydrate":
        context.methodology_sanad_creation_linking_grading_fn = callback
        RunOrchestrator._rehydrate_methodology_sanads(  # type: ignore[arg-type]
            object(), context, {}
        )
        return
    raise AssertionError(f"unknown strict composition stage: {stage}")


def _ready_task() -> Any:
    return (
        InMemoryExtractionTaskPlanner()
        .plan_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_registry=_registry(),
            classifications=[_classification()],
            source_spans_by_document_id={"doc-financial-model": [_span()]},
        )
        .tasks[0]
    )


def _strict_context(**overrides: Any) -> RunContext:
    values: dict[str, Any] = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "mode": "FULL",
        "documents": [],
        "extract_fn": lambda **_kwargs: {},
        "grade_fn": lambda **_kwargs: {},
        "strict_resume_validation_required": True,
        "methodology_extraction_tasks": [_ready_task()],
    }
    values.update(overrides)
    return RunContext(**values)


@pytest.mark.parametrize(("stage", "external_calls_enabled"), _STRICT_COMPOSITION_STAGES)
@pytest.mark.parametrize(
    "persistence_backend",
    (
        pytest.param(_MISSING_METADATA, id="missing"),
        pytest.param(None, id="none"),
        pytest.param("sqlite", id="sqlite"),
        pytest.param("unknown", id="unknown"),
        pytest.param("in_memory", id="in-memory"),
        pytest.param("hermetic", id="hermetic"),
    ),
)
def test_strict_composition_rejects_every_non_postgres_backend_before_invocation(
    stage: str,
    external_calls_enabled: bool,
    persistence_backend: object,
) -> None:
    callback, invocations = _callback_with_metadata(
        persistence_backend=persistence_backend,
        external_calls_enabled=external_calls_enabled,
    )

    with pytest.raises(RunStepBlockedError) as raised:
        _invoke_strict_composition_stage(stage, callback)

    assert raised.value.code == "STRICT_METHODOLOGY_COMPOSITION_REQUIRED"
    assert not any(invocations.values()), "invalid callback was invoked before rejection"


@pytest.mark.parametrize(("stage", "required_external_mode"), _STRICT_COMPOSITION_STAGES)
@pytest.mark.parametrize(
    "invalid_mode",
    ("missing", "none", "opposite", "stringified", "integer"),
)
def test_strict_composition_requires_exact_external_call_mode_before_invocation(
    stage: str,
    required_external_mode: bool,
    invalid_mode: str,
) -> None:
    external_calls_enabled: object = {
        "missing": _MISSING_METADATA,
        "none": None,
        "opposite": not required_external_mode,
        "stringified": str(required_external_mode).lower(),
        "integer": int(required_external_mode),
    }[invalid_mode]
    callback, invocations = _callback_with_metadata(
        persistence_backend="postgresql",
        external_calls_enabled=external_calls_enabled,
    )

    with pytest.raises(RunStepBlockedError) as raised:
        _invoke_strict_composition_stage(stage, callback)

    assert raised.value.code == "STRICT_METHODOLOGY_COMPOSITION_REQUIRED"
    assert not any(invocations.values()), "wrong-mode callback was invoked before rejection"


@pytest.mark.parametrize(("stage", "external_calls_enabled"), _STRICT_COMPOSITION_STAGES)
def test_strict_composition_invokes_callbacks_with_exact_production_metadata(
    stage: str,
    external_calls_enabled: bool,
) -> None:
    callback, invocations = _callback_with_metadata(
        persistence_backend="postgresql",
        external_calls_enabled=external_calls_enabled,
    )

    if stage.endswith("_rehydrate"):

        def valid_rehydrate(**_kwargs: Any) -> Any:
            invocations["rehydrate"] = True
            if stage == "planning_rehydrate":
                return [object()]
            if stage == "execution_rehydrate":
                return object(), object()
            if stage in {"claim_rehydrate", "evidence_rehydrate"}:
                return object(), []
            return object(), [], [], [], []

        callback.rehydrate = valid_rehydrate  # type: ignore[attr-defined]
        try:
            _invoke_strict_composition_stage(stage, callback)
        except RunStepBlockedError as exc:
            pytest.fail(
                f"valid {stage} callback was rejected before invocation: "
                f"code={exc.code}; invocations={invocations}",
                pytrace=False,
            )
        assert invocations == {"callback": False, "rehydrate": True}
        return

    with pytest.raises(_ExpectedCallbackInvocation):
        _invoke_strict_composition_stage(stage, callback)

    assert sum(invocations.values()) == 1


def test_strict_execution_rejects_extractor_none_before_synthetic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_synthetic(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict execution constructed the synthetic executor")

    monkeypatch.setattr(
        InMemoryRunMethodologyExtractionTaskExecutionService,
        "__init__",
        forbidden_synthetic,
    )

    with pytest.raises(RunStepBlockedError) as raised:
        RunOrchestrator._execute_methodology_extraction_task_execution(  # type: ignore[arg-type]
            object(), _strict_context()
        )

    assert raised.value.code == "STRICT_METHODOLOGY_COMPOSITION_REQUIRED"


def test_strict_execution_rejects_in_memory_external_calls_disabled_callback() -> None:
    service = InMemoryRunMethodologyExtractionTaskExecutionService(
        executor=InMemoryMethodologyExtractionTaskExecutor()
    )
    assert service._executor.persistence_backend == "in_memory"
    assert service._executor.external_calls_enabled is False

    def synthetic_callback(**kwargs: Any) -> Any:
        return service.run(**kwargs, extractor=None)

    # Preserve the false-success attributes on the callback object itself: this is
    # the strict composition seam, not a unit test of the legacy service.
    synthetic_callback.persistence_backend = "in_memory"  # type: ignore[attr-defined]
    synthetic_callback.external_calls_enabled = False  # type: ignore[attr-defined]
    context = _strict_context(methodology_extraction_task_execution_fn=synthetic_callback)

    with pytest.raises(RunStepBlockedError) as raised:
        RunOrchestrator._execute_methodology_extraction_task_execution(  # type: ignore[arg-type]
            object(), context
        )

    assert raised.value.code == "STRICT_METHODOLOGY_COMPOSITION_REQUIRED"


@pytest.mark.parametrize(
    ("method_name", "service_cls", "context_overrides"),
    [
        (
            "_execute_methodology_claim_materialization",
            InMemoryRunMethodologyClaimMaterializationService,
            {"methodology_extraction_execution_result": object()},
        ),
        (
            "_execute_methodology_evidence_item_materialization",
            InMemoryRunMethodologyEvidenceItemMaterializationService,
            {"methodology_materialized_claims": []},
        ),
        (
            "_execute_methodology_sanad_creation_linking_grading",
            InMemoryRunMethodologySanadCreationLinkingGradingService,
            {"methodology_materialized_claims": [], "methodology_evidence_items": []},
        ),
    ],
)
def test_strict_materialization_never_constructs_in_memory_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    service_cls: type[Any],
    context_overrides: dict[str, Any],
) -> None:
    def forbidden_synthetic(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(f"strict composition constructed {service_cls.__name__}")

    monkeypatch.setattr(service_cls, "__init__", forbidden_synthetic)
    method = getattr(RunOrchestrator, method_name)

    with pytest.raises(RunStepBlockedError) as raised:
        method(object(), _strict_context(**context_overrides))

    assert raised.value.code == "STRICT_METHODOLOGY_COMPOSITION_REQUIRED"


def test_worker_strict_composition_threads_all_five_durable_task6_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    callback_names = (
        "methodology_extraction_task_planning_fn",
        "methodology_extraction_task_execution_fn",
        "methodology_claim_materialization_fn",
        "methodology_evidence_item_materialization_fn",
        "methodology_sanad_creation_linking_grading_fn",
    )
    callbacks = {name: (lambda **_kwargs: None) for name in callback_names}

    @contextmanager
    def connection_factory():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: True)
    monkeypatch.setattr(
        worker_module,
        "build_postgres_methodology_coverage_callbacks",
        lambda **_kwargs: (lambda **_values: None, lambda **_values: []),
    )
    monkeypatch.setattr(
        worker_module,
        "build_postgres_methodology_extraction_callbacks",
        lambda **_kwargs: dict(callbacks),
        raising=False,
    )
    monkeypatch.setattr(worker_module, "_load_worker_preflight_corpus", lambda **_kwargs: [])
    monkeypatch.setattr(worker_module, "_load_worker_deal_metadata", lambda **_kwargs: {})
    monkeypatch.setattr(
        "idis.services.runs.steps.extraction_ready_documents_from_preflight_corpus",
        lambda _corpus: [],
    )
    monkeypatch.setattr(
        "idis.services.ingestion.no_substantive_content_attestation."
        "load_document_no_substantive_content_attestation",
        lambda **_kwargs: None,
    )

    def capture_context(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr("idis.services.runs.steps.build_run_context", capture_context)

    worker_module._default_run_context_factory(
        db_conn=object(),
        provider_db_conn=object(),
        tenant_id=TENANT_ID,
        run_data={
            "run_id": RUN_ID,
            "deal_id": DEAL_ID,
            "mode": "FULL",
            "status": "RUNNING",
            "lease_owner_id": "55555555-5555-4555-8555-555555555555",
            "lease_fencing_token": 7,
        },
        audit_sink=object(),
        lease_config=worker_module.WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
        connection_factory=connection_factory,
        strict_profile=SimpleNamespace(values={}),  # type: ignore[arg-type]
    )

    for name in callback_names:
        assert captured[name] is callbacks[name], name


def test_real_worker_callback_builder_resolves_all_five_postgres_composition_roots() -> None:
    @contextmanager
    def connection_factory():  # type: ignore[no-untyped-def]
        yield object()

    callbacks = worker_module.build_postgres_methodology_extraction_callbacks(
        connection_factory=connection_factory,
        tenant_id=TENANT_ID,
        lease_owner_id="55555555-5555-4555-8555-555555555555",
        lease_fencing_token=7,
        audit_sink=object(),
        strict_profile=None,
    )

    assert set(callbacks) == {
        "methodology_extraction_task_planning_fn",
        "methodology_extraction_task_execution_fn",
        "methodology_claim_materialization_fn",
        "methodology_evidence_item_materialization_fn",
        "methodology_sanad_creation_linking_grading_fn",
    }
    for name, callback in callbacks.items():
        assert callback.persistence_backend == "postgresql", name  # type: ignore[attr-defined]
        assert callable(callback.rehydrate), name  # type: ignore[attr-defined]
    assert callbacks["methodology_extraction_task_execution_fn"].external_calls_enabled is True  # type: ignore[attr-defined]
    assert all(
        callback.external_calls_enabled is False  # type: ignore[attr-defined]
        for name, callback in callbacks.items()
        if name != "methodology_extraction_task_execution_fn"
    )


@pytest.mark.parametrize(
    ("callback_name", "wrapper_name"),
    (
        (
            "methodology_claim_materialization_fn",
            "_materialize_postgres_methodology_claims",
        ),
        (
            "methodology_evidence_item_materialization_fn",
            "_materialize_postgres_methodology_evidence",
        ),
        (
            "methodology_sanad_creation_linking_grading_fn",
            "_materialize_postgres_methodology_sanads",
        ),
    ),
)
def test_real_worker_materialization_callback_invokes_its_postgres_composition_root(
    monkeypatch: pytest.MonkeyPatch,
    callback_name: str,
    wrapper_name: str,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    @contextmanager
    def connection_factory():  # type: ignore[no-untyped-def]
        yield object()

    def composition_root(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(worker_module, wrapper_name, composition_root)
    audit_sink = object()
    callbacks = worker_module.build_postgres_methodology_extraction_callbacks(
        connection_factory=connection_factory,
        tenant_id=TENANT_ID,
        lease_owner_id="55555555-5555-4555-8555-555555555555",
        lease_fencing_token=7,
        audit_sink=audit_sink,
        strict_profile=None,
    )

    result = callbacks[callback_name](
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        run_step_id="66666666-6666-4666-8666-666666666666",
    )

    assert result is sentinel
    assert captured["connection_factory"] is connection_factory
    assert captured["lease_owner_id"] == "55555555-5555-4555-8555-555555555555"
    assert captured["lease_fencing_token"] == 7
    assert captured["audit_sink"] is audit_sink
    assert captured["tenant_id"] == TENANT_ID
    assert captured["deal_id"] == DEAL_ID
    assert captured["run_id"] == RUN_ID
