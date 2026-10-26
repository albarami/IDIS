"""RED-first concrete PostgreSQL port for Task 7 lifecycle composition."""

from __future__ import annotations

import importlib
import inspect
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from idis.models.run_step import RunStep, StepName, StepStatus
from idis.pipeline.task7_semantics import NativeOutputEvidence
from idis.pipeline.task7_usage import USAGE_ROUTE_REGISTRY, ProducerRefV1


def _module() -> ModuleType:
    try:
        return importlib.import_module("idis.persistence.repositories.task7_completion")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.persistence.repositories.task7_completion":
            raise
        pytest.fail("concrete Task 7 PostgreSQL port is missing", pytrace=False)


def _scope() -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=("46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"),
        pipeline_resume_state="RESUMABLE",
        lease_owner_id="44444444-4444-4444-8444-444444444444",
        lease_fencing_token=7,
    )


def _step(*, summary: dict[str, object] | None = None) -> RunStep:
    return RunStep(
        step_id="55555555-5555-4555-8555-555555555555",
        tenant_id=_scope().tenant_id,
        run_id=_scope().run_id,
        step_name=StepName.DATA_ROOM_INVENTORY_PACKAGE,
        step_order=0,
        status=StepStatus.COMPLETED,
        result_summary=summary or {},
        pipeline_contract_version=_scope().pipeline_contract_version,
        pipeline_contract_hash=_scope().pipeline_contract_hash,
        semantic_rule_version="2",
        semantic_rule_hash="a" * 64,
    )


def test_concrete_port_implements_the_complete_service_protocol() -> None:
    module = _module()
    expected = {
        "establish_tenant_rls",
        "lock_run_for_step_completion",
        "renew_live_lease",
        "lock_run_step",
        "lock_existing_native_rows",
        "validate_step_predecessors",
        "persist_output_roots",
        "corroborate_persisted_native_outputs",
        "persist_step_outputs",
        "persist_applicability_decisions",
        "persist_new_outgoing_edges",
        "derive_and_persist_usage_receipts",
        "prove_incoming_receipt_equality",
        "mark_step_completed",
        "append_step_completion_audit",
        "load_resumable_run",
        "list_canonical_steps",
        "validate_completed_step_graph",
        "lock_run_for_finalization",
        "derive_contract_edge_map",
        "load_persisted_edge_map",
        "derive_receipt_edge_map",
        "derive_usage_evidence_map",
        "load_contract_cardinality_equations",
        "validate_native_and_subrole_roots",
        "mark_run_succeeded",
        "append_run_finalization_audit",
    }
    actual = {
        name
        for name, value in inspect.getmembers(
            module.PostgresTask7CompletionPort,
            inspect.isfunction,
        )
        if not name.startswith("_") and callable(value)
    }

    assert expected <= actual


def test_run_lock_and_lease_queries_use_database_time_and_exact_scope() -> None:
    source = inspect.getsource(_module())
    normalized = " ".join(source.upper().split())

    assert "TENANT_ID = CAST(:TENANT_ID AS UUID)" in normalized
    assert "DEAL_ID = CAST(:DEAL_ID AS UUID)" in normalized
    assert "RUN_ID = CAST(:RUN_ID AS UUID)" in normalized
    assert "LEASE_EXPIRES_AT > STATEMENT_TIMESTAMP()" in normalized
    assert "FOR UPDATE" in normalized
    assert "DATETIME.NOW" not in normalized
    assert "UTCNOW" not in normalized


def test_predecessor_and_resume_queries_are_total_ordered_and_read_only() -> None:
    source = inspect.getsource(_module())
    normalized = " ".join(source.upper().split())

    assert "STEP_ORDER < :STEP_ORDER" in normalized
    assert "STATUS = 'COMPLETED'" in normalized
    assert "ORDER BY STEP_ORDER, STEP_ID" in normalized
    assert "RUN_OUTPUT_OBJECTS" in normalized
    assert "RUN_STEP_OUTPUTS" in normalized
    assert "RUN_EXPECTED_CONSUMER_EDGES" in normalized
    assert "RUN_CONSUMER_RECEIPTS" in normalized
    assert "USAGE_EVIDENCE_SHA256" in normalized


def test_step_completion_persists_the_semantic_result_summary() -> None:
    source = inspect.getsource(_module().PostgresTask7CompletionPort.mark_step_completed)
    normalized = " ".join(source.upper().split())

    assert "RESULT_SUMMARY = CAST(:RESULT_SUMMARY AS JSONB)" in normalized


def test_terminal_success_uses_the_existing_runs_finished_at_column() -> None:
    source = inspect.getsource(_module().PostgresTask7CompletionPort.mark_run_succeeded)
    normalized = " ".join(source.upper().split())

    assert "FINISHED_AT = STATEMENT_TIMESTAMP()" in normalized
    assert "COMPLETED_AT" not in normalized


def test_required_usage_authorities_derive_from_route_registry() -> None:
    module = _module()
    expected = frozenset(route.native_reference_relation for route in USAGE_ROUTE_REGISTRY)

    assert module.required_usage_reference_relations() == expected
    assert len(expected) == 15


def test_actual_missing_future_authority_has_the_locked_unavailable_code() -> None:
    module = _module()
    error = module.Task7PostgresPortError(module.PIPELINE_CONTRACT_UNAVAILABLE)

    assert error.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert str(error) == "PIPELINE_CONTRACT_UNAVAILABLE"


def test_port_has_no_external_io_or_ambient_configuration() -> None:
    source = inspect.getsource(_module())
    assert all(
        token not in source
        for token in (
            "os.environ",
            "httpx",
            "requests",
            "boto3",
            "subprocess",
            "Path(",
            "open(",
        )
    )


def test_constructor_requires_all_transaction_local_dependencies() -> None:
    module = _module()
    parameters = inspect.signature(module.PostgresTask7CompletionPort).parameters

    assert set(parameters) == {
        "connection",
        "tenant_id",
        "audit_sink",
        "lease_seconds",
    }
    assert parameters["connection"].default is inspect.Parameter.empty
    assert parameters["tenant_id"].default is inspect.Parameter.empty
    assert parameters["audit_sink"].default is inspect.Parameter.empty
    assert parameters["lease_seconds"].default is inspect.Parameter.empty


def test_native_locking_is_scoped_to_sorted_run_rows_not_whole_tables() -> None:
    source = inspect.getsource(_module().PostgresTask7CompletionPort.lock_existing_native_rows)
    normalized = " ".join(source.upper().split())

    assert "LOCK TABLE" not in normalized
    assert "TENANT_ID = CAST(:TENANT_ID AS UUID)" in normalized
    assert "DEAL_ID = CAST(:DEAL_ID AS UUID)" in normalized
    assert "RUN_ID = CAST(:RUN_ID AS UUID)" in normalized
    assert "ORDER BY" in normalized
    assert "FOR UPDATE" in normalized


def test_new_outgoing_edge_persistence_is_delta_only_and_never_repairs_prior_steps() -> None:
    source = inspect.getsource(_module().PostgresTask7CompletionPort.persist_new_outgoing_edges)

    assert "list_run_expected_consumer_edges" in source
    assert "_current_step_unlocked_edge_ids" in source
    assert "_stored_edge_matches" in source
    assert "materialization_origin" not in source


def test_final_usage_hashes_are_reextracted_not_copied_from_receipts() -> None:
    source = inspect.getsource(_module().PostgresTask7CompletionPort.derive_usage_evidence_map)

    assert "extract_usage_evidence" in source
    assert "_load_usage_occurrences" in source
    assert "result[key] = receipt.usage_evidence_sha256" not in source


def test_cardinality_equations_do_not_reuse_the_edge_deriver() -> None:
    source = inspect.getsource(
        _module().PostgresTask7CompletionPort.load_contract_cardinality_equations
    )

    assert "_derive_edges" not in source
    assert "_independent_producer_census" in source


def test_completed_graph_validation_ignores_result_summary_as_authority() -> None:
    source = inspect.getsource(_module().PostgresTask7CompletionPort.validate_completed_step_graph)

    assert "result_summary" not in source
    assert "_rederive_completed_step_graph" in source
    assert "_rederive_usage_evidence_for_step" in source


def _native_authority_port(
    *,
    helper_rows: tuple[object, ...],
) -> tuple[Any, Any, NativeOutputEvidence]:
    module = _module()
    native = NativeOutputEvidence(
        output_kind="GENERIC_EXTRACTION_OUTPUT",
        output_object_id=UUID(int=123),
        native_identity_sha256="1" * 64,
        native_output_sha256="2" * 64,
        persisted=True,
    )
    root = SimpleNamespace(
        output_object_id=str(native.output_object_id),
        output_class="NATIVE_ROW",
        output_kind=native.output_kind,
        native_identity_sha256=native.native_identity_sha256,
        native_output_sha256=native.native_output_sha256,
    )
    step_row = SimpleNamespace(
        step_id=_step().step_id,
        output_object_id=root.output_object_id,
    )

    class Graph:
        def list_run_output_objects(self, **_kwargs: object) -> tuple[object, ...]:
            return (root,)

        def list_run_step_outputs(self, **_kwargs: object) -> tuple[object, ...]:
            return (step_row,)

    class Result:
        def fetchall(self) -> tuple[object, ...]:
            return helper_rows

    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def execute(self, statement: object, params: dict[str, object]) -> Result:
            self.calls.append((str(statement), dict(params)))
            return Result()

    connection = Connection()
    port = object.__new__(module.PostgresTask7CompletionPort)
    port._connection = connection
    port._graph = Graph()
    return port, connection, native


def test_native_authority_helper_is_static_exact_and_typed_for_completion() -> None:
    helper = {
        "output_kind": "GENERIC_EXTRACTION_OUTPUT",
        "expected_native_identity_sha256": "1" * 64,
        "expected_native_output_sha256": "2" * 64,
    }
    port, connection, native = _native_authority_port(helper_rows=(helper,))

    port.corroborate_persisted_native_outputs(
        scope=_scope(),
        step=_step(),
        expected_native_outputs=(native,),
    )

    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "public.idis_task7_current_native_hashes_v1" in sql
    assert set(params) == {
        "tenant_id",
        "deal_id",
        "run_id",
        "output_object_id",
    }
    assert params["output_object_id"] == str(native.output_object_id)


@pytest.mark.parametrize(
    ("phase", "helper_rows", "expected_code"),
    (
        ("completion", (), "STEP_OUTPUT_MISSING"),
        (
            "completion",
            (
                {
                    "output_kind": "GENERIC_EXTRACTION_OUTPUT",
                    "expected_native_identity_sha256": "1" * 64,
                    "expected_native_output_sha256": "2" * 64,
                },
                {
                    "output_kind": "GENERIC_EXTRACTION_OUTPUT",
                    "expected_native_identity_sha256": "1" * 64,
                    "expected_native_output_sha256": "2" * 64,
                },
            ),
            "STEP_OUTPUT_MISSING",
        ),
        (
            "completion",
            (
                {
                    "output_kind": "GENERIC_EXTRACTION_OUTPUT",
                    "expected_native_identity_sha256": "3" * 64,
                    "expected_native_output_sha256": "2" * 64,
                },
            ),
            "STEP_OUTPUT_MISSING",
        ),
        ("resume", (), "STEP_REHYDRATION_FAILED"),
    ),
)
def test_native_authority_helper_fails_closed_by_boundary_phase(
    phase: str,
    helper_rows: tuple[object, ...],
    expected_code: str,
) -> None:
    module = _module()
    port, _connection, native = _native_authority_port(helper_rows=helper_rows)
    kwargs: dict[str, object] = {"scope": _scope(), "step": _step()}
    if phase == "completion":
        kwargs["expected_native_outputs"] = (native,)

    with pytest.raises(module.Task7PostgresPortError) as caught:
        port.corroborate_persisted_native_outputs(**kwargs)

    assert caught.value.code == expected_code


def test_v9_native_authority_is_one_static_postgres_port_not_a_python_recipe() -> None:
    """The database helper is the only NK/P recipe once v9 publishes its API."""
    method = getattr(
        _module().PostgresTask7CompletionPort,
        "corroborate_persisted_native_outputs",
        None,
    )
    assert callable(method)
    source = inspect.getsource(method)
    static_sql = getattr(_module(), "_CURRENT_NATIVE_HASHES_SQL", "")
    normalized = " ".join(source.split())

    assert "self._connection.execute" in source
    assert "text(_CURRENT_NATIVE_HASHES_SQL)" in source
    assert "public.idis_task7_current_native_hashes_v1" in static_sql
    assert "native_identity_sha256" in source
    assert "native_output_sha256" in source
    assert "NATIVE_OUTPUT_REGISTRY" not in source
    assert "derive_task7_uuid" not in source
    assert "json.dumps" not in source
    assert "FROM {" not in normalized
    assert ".format(" not in source


def test_v9_completion_corroborates_after_root_and_native_before_step_output() -> None:
    """Reviewed FK order: root -> native/root-bind -> SQL proof -> step output."""
    from idis.pipeline.task7_completion import Task7CompletionFinalizationService

    source = inspect.getsource(Task7CompletionFinalizationService.complete_step)
    root_index = source.index("port.persist_output_roots")
    native_index = source.index("staged.persist")
    proof_index = source.index("port.corroborate_persisted_native_outputs")
    step_output_index = source.index("port.persist_step_outputs")
    completion_index = source.index("port.mark_step_completed")

    assert root_index < native_index < proof_index < step_output_index < completion_index


def test_v9_resume_and_finalization_share_independent_sql_corroboration() -> None:
    """Both immutable read paths must rederive NK/P instead of trusting roots."""
    port_type = _module().PostgresTask7CompletionPort
    graph_source = inspect.getsource(port_type._rederive_completed_step_graph)
    resume_source = inspect.getsource(port_type.validate_completed_step_graph)
    final_source = inspect.getsource(port_type.validate_native_and_subrole_roots)

    assert "corroborate_persisted_native_outputs" in graph_source
    assert "_rederive_completed_step_graph" in resume_source
    assert "validate_completed_step_graph" in final_source
    assert "result_summary" not in graph_source


def test_poisoned_result_summary_cannot_change_completed_graph_validation() -> None:
    module = _module()
    port = object.__new__(module.PostgresTask7CompletionPort)
    calls: list[str] = []
    port._rederive_completed_step_graph = MethodType(  # type: ignore[method-assign]
        lambda _self, **_kwargs: calls.append("graph"),
        port,
    )
    port._rederive_usage_evidence_for_step = MethodType(  # type: ignore[method-assign]
        lambda _self, **_kwargs: calls.append("usage"),
        port,
    )

    clean = port.validate_completed_step_graph(scope=_scope(), step=_step())
    poisoned = port.validate_completed_step_graph(
        scope=_scope(),
        step=_step(
            summary={
                "native_outputs": ["forged"],
                "receipt_hash": "0" * 64,
                "status": "COMPLETED",
            }
        ),
    )

    assert clean is True
    assert poisoned is True
    assert calls == ["graph", "usage", "graph", "usage"]


def test_completed_graph_rederivation_rejects_an_extra_root_coordinate() -> None:
    module = _module()
    scope = _scope()
    spec = module.STEP_SEMANTIC_REGISTRY[StepName.DATA_ROOM_INVENTORY_PACKAGE.value]
    step = _step().model_copy(
        update={
            "semantic_rule_version": spec.semantic_rule_version,
            "semantic_rule_hash": spec.semantic_rule_hash,
        }
    )
    port = object.__new__(module.PostgresTask7CompletionPort)
    aggregate_identity = "1" * 64
    aggregate_value = "2" * 64
    aggregate_id = str(
        port._derive_output_id(
            scope=scope,
            output_class="STEP_RECEIPT",
            output_kind=spec.aggregate_receipt_kind,
            identity_sha256=aggregate_identity,
            value_sha256=aggregate_value,
        )
    )
    aggregate_step_output_id = str(
        port._derive_step_output_id(
            scope=scope,
            step=step,
            subrole="__step__",
            output_kind=spec.aggregate_receipt_kind,
            ordinal=0,
            output_object_id=aggregate_id,
        )
    )
    root = SimpleNamespace(
        output_object_id=aggregate_id,
        output_class="STEP_RECEIPT",
        output_kind=spec.aggregate_receipt_kind,
        output_schema_version=spec.semantic_rule_version,
        native_identity_sha256=aggregate_identity,
        native_output_sha256=aggregate_value,
        pipeline_contract_version=scope.pipeline_contract_version,
        pipeline_contract_hash=scope.pipeline_contract_hash,
        write_origin="RUNTIME",
    )
    row = SimpleNamespace(
        step_output_id=aggregate_step_output_id,
        output_object_id=aggregate_id,
        step_id=step.step_id,
        step_name=step.step_name.value,
        subrole="__step__",
        output_kind=spec.aggregate_receipt_kind,
        output_ordinal=0,
        semantic_rule_version=spec.semantic_rule_version,
        semantic_rule_hash=spec.semantic_rule_hash,
        summary_sha256="3" * 64,
    )

    class Graph:
        def __init__(self) -> None:
            self.outputs: tuple[object, ...] = (row,)
            self.roots: tuple[object, ...] = (root,)

        def list_run_step_outputs(self, **_kwargs: object) -> tuple[object, ...]:
            return self.outputs

        def list_run_output_objects(self, **_kwargs: object) -> tuple[object, ...]:
            return self.roots

        def list_run_expected_consumer_edges(
            self,
            **_kwargs: object,
        ) -> tuple[object, ...]:
            return ()

        def list_run_applicability_decisions(
            self,
            **_kwargs: object,
        ) -> tuple[object, ...]:
            return ()

        def list_run_consumer_receipts(
            self,
            **_kwargs: object,
        ) -> tuple[object, ...]:
            return ()

    graph = Graph()
    port._graph = graph
    port._derive_edges = MethodType(  # type: ignore[method-assign]
        lambda _self, _scope: (),
        port,
    )
    port.derive_usage_evidence_map = MethodType(  # type: ignore[method-assign]
        lambda _self, **_kwargs: {},
        port,
    )

    assert port.validate_completed_step_graph(scope=scope, step=step) is True

    graph.outputs = (
        row,
        SimpleNamespace(
            **{
                **vars(row),
                "step_output_id": str(UUID(int=99)),
                "subrole": "unexpected",
            }
        ),
    )
    assert port.validate_completed_step_graph(scope=scope, step=step) is False


def test_edge_delta_inserts_only_currently_unlocked_rows_and_never_repairs_gaps() -> None:
    module = _module()
    prior = SimpleNamespace(expected_edge_id=UUID(int=1))
    current = SimpleNamespace(expected_edge_id=UUID(int=2))

    class Graph:
        def __init__(self, persisted: tuple[object, ...]) -> None:
            self.persisted = persisted
            self.inserted: list[object] = []

        def list_run_expected_consumer_edges(self, **_kwargs: object) -> tuple[object, ...]:
            return self.persisted

        def insert_run_expected_consumer_edge(self, row: object) -> None:
            self.inserted.append(row)

    port = object.__new__(module.PostgresTask7CompletionPort)
    graph = Graph((SimpleNamespace(expected_edge_id=str(prior.expected_edge_id)),))
    port._graph = graph
    port._derive_edges = MethodType(  # type: ignore[method-assign]
        lambda _self, _scope: (prior, current),
        port,
    )
    port._stored_edge_matches = MethodType(  # type: ignore[method-assign]
        lambda _self, _stored, expected: expected is prior,
        port,
    )
    port._current_step_unlocked_edge_ids = MethodType(  # type: ignore[method-assign]
        lambda _self, **_kwargs: frozenset({str(current.expected_edge_id)}),
        port,
    )
    port._edge_record = MethodType(  # type: ignore[method-assign]
        lambda _self, edge, _params: edge,
        port,
    )

    port.persist_new_outgoing_edges(scope=_scope(), step=_step())

    assert graph.inserted == [current]

    graph.persisted = ()
    port._current_step_unlocked_edge_ids = MethodType(  # type: ignore[method-assign]
        lambda _self, **_kwargs: frozenset(),
        port,
    )
    with pytest.raises(module.Task7PostgresPortError) as gap:
        port.persist_new_outgoing_edges(scope=_scope(), step=_step())
    assert gap.value.code == "CONSUMER_RECEIPT_INCOMPLETE"


def test_usage_finalization_ignores_the_stored_receipt_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    producer = ProducerRefV1(
        producer_type="INPUT_BINDING",
        producer_id=UUID(int=20),
        producer_identity_sha256="1" * 64,
        producer_value_sha256="2" * 64,
    )
    route = USAGE_ROUTE_REGISTRY[0]
    edge = SimpleNamespace(
        expected_edge_id=UUID(int=21),
        edge_tuple_sha256="3" * 64,
        route_ordinal=route.route_ordinal,
        usage_kind=route.usage_kind,
        consumer_step_name=route.consumer_step_name,
        consumer_subrole=route.consumer_subrole,
        producer=producer,
    )
    consumer = SimpleNamespace(
        step_output_id=str(UUID(int=22)),
        output_object_id=str(UUID(int=23)),
        step_name=edge.consumer_step_name,
        subrole=edge.consumer_subrole,
    )
    root = SimpleNamespace(
        output_object_id=consumer.output_object_id,
        native_identity_sha256="4" * 64,
        native_output_sha256="5" * 64,
    )
    receipt = SimpleNamespace(
        expected_edge_id=str(edge.expected_edge_id),
        consumer_step_output_id=consumer.step_output_id,
        consumer_output_object_id=root.output_object_id,
        consumer_step_name=edge.consumer_step_name,
        consumer_subrole=edge.consumer_subrole,
        usage_evidence_sha256="6" * 64,
    )

    class Graph:
        def list_run_step_outputs(self, **_kwargs: object) -> tuple[object, ...]:
            return (consumer,)

        def list_run_output_objects(self, **_kwargs: object) -> tuple[object, ...]:
            return (root,)

        def list_run_consumer_receipts(self, **_kwargs: object) -> tuple[object, ...]:
            return (receipt,)

    port = object.__new__(module.PostgresTask7CompletionPort)
    port._graph = Graph()
    port._derive_edges = MethodType(  # type: ignore[method-assign]
        lambda _self, _scope: (edge,),
        port,
    )
    port._load_usage_occurrences = MethodType(  # type: ignore[method-assign]
        lambda _self, **_kwargs: (),
        port,
    )
    monkeypatch.setattr(
        module,
        "extract_usage_evidence",
        lambda _request: SimpleNamespace(usage_evidence_sha256="7" * 64),
    )

    result = port.derive_usage_evidence_map(scope=_scope())

    assert result == {edge.edge_tuple_sha256: "7" * 64}
    assert result[edge.edge_tuple_sha256] != receipt.usage_evidence_sha256
