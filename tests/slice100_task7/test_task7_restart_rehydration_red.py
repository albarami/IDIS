"""RED-first proof for all 27 strict restart cut points."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.run_cir04_restart_reference_dev_migration import (
    upgrade_disposable_to_dev_0039,
)
from sqlalchemy import text
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals
from tests.slice100_task7.test_task7_source_freeze_postgres_red import (
    _seed_open_source,
)

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.run_step import RunStep, StepStatus
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.persistence.repositories.strict_preflight_proofs import (
    PostgresStrictPreflightProofsRepository,
    StrictPreflightBinding,
)
from idis.persistence.repositories.task7_completion import (
    PostgresTask7CompletionPort,
)
from idis.persistence.repositories.task7_run_graph import (
    PostgresTask7RunGraphRepository,
    RunOutputObjectRecord,
    RunStepOutputRecord,
)
from idis.persistence.repositories.task7_source_freeze import freeze_source_package
from idis.pipeline import contract_registry
from idis.pipeline.task7_completion import (
    Task7CompletionError,
    Task7CompletionFinalizationService,
    Task7LeaseScope,
    Task7StepCompletionCommand,
)
from idis.pipeline.task7_lifecycle import (
    STEP_REHYDRATION_FAILED,
    Task7LifecycleError,
    rehydrate_strict_run,
)
from idis.pipeline.task7_semantics import STEP_SEMANTIC_REGISTRY
from idis.pipeline.worker import _lease_transition_audit_emitter

TENANT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
CONTRACT_HASH = contract_registry.FULL_STRICT_V21_CONTRACT_HASH


def _steps(*, completed_count: int, next_status: StepStatus = StepStatus.PENDING) -> list[RunStep]:
    rows: list[RunStep] = []
    for spec in contract_registry.PIPELINE_CONTRACTS["full-strict-v21"].steps:
        status = StepStatus.COMPLETED if spec.step_order < completed_count else StepStatus.PENDING
        if spec.step_order == completed_count and completed_count < 28:
            status = next_status
        rows.append(
            RunStep(
                step_id=f"step-{spec.step_order:02d}",
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                step_name=spec.step_name,
                step_order=spec.step_order,
                status=status,
                pipeline_contract_version="full-strict-v21",
                pipeline_contract_hash=CONTRACT_HASH,
                semantic_rule_version=spec.semantic_rule_version,
                semantic_rule_hash=spec.semantic_rule_hash,
            )
        )
    return rows


@pytest.mark.parametrize("cut", range(1, 28))
def test_every_restart_cut_rehydrates_only_the_completed_prefix(cut: int) -> None:
    validated: list[int] = []

    plan = rehydrate_strict_run(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        steps=tuple(reversed(_steps(completed_count=cut))),
        validate_completed=lambda step: validated.append(step.step_order) or True,
    )

    assert plan.completed_count == cut
    assert plan.next_step_order == cut
    assert plan.finalization_required is False
    assert validated == list(range(cut))


def test_running_cut_is_replayed_but_completed_prefix_stays_read_only() -> None:
    plan = rehydrate_strict_run(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        steps=_steps(completed_count=9, next_status=StepStatus.RUNNING),
        validate_completed=lambda _step: True,
    )

    assert plan.completed_count == 9
    assert plan.next_step_order == 9
    assert plan.replay_uncompleted_boundary is True


def test_all_28_completed_requires_finalization_and_no_replay() -> None:
    plan = rehydrate_strict_run(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        steps=_steps(completed_count=28),
        validate_completed=lambda _step: True,
    )

    assert plan.completed_count == 28
    assert plan.next_step_order is None
    assert plan.finalization_required is True
    assert plan.replay_uncompleted_boundary is False


@pytest.mark.parametrize(
    "mutation",
    ("missing", "completed_gap", "contract_drift", "failed", "validator_rejected"),
)
def test_rehydration_rejects_any_incomplete_or_semantically_unproved_graph(
    mutation: str,
) -> None:
    rows = _steps(completed_count=5)

    def validator(_step: RunStep) -> bool:
        return True

    if mutation == "missing":
        rows.pop(10)
    elif mutation == "completed_gap":
        rows[8].status = StepStatus.COMPLETED
    elif mutation == "contract_drift":
        rows[3] = rows[3].model_copy(update={"semantic_rule_hash": "a" * 64})
    elif mutation == "failed":
        rows[5].status = StepStatus.FAILED
    else:

        def validator(step: RunStep) -> bool:
            return step.step_order != 2

    with pytest.raises(Task7LifecycleError) as exc_info:
        rehydrate_strict_run(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            pipeline_contract_version="full-strict-v21",
            pipeline_contract_hash=CONTRACT_HASH,
            pipeline_resume_state="RESUMABLE",
            steps=rows,
            validate_completed=validator,
        )

    assert exc_info.value.code == STEP_REHYDRATION_FAILED


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _active_contract() -> Any:
    return contract_registry.PIPELINE_CONTRACTS["full-strict-v21"]


def _seed_durable_restart_run(
    database: DisposablePostgres,
    *,
    label: str,
) -> Task7LeaseScope:
    contract = _active_contract()
    with database.admin_engine.begin() as connection:
        source_fixture = _seed_open_source(connection, label=f"restart-{label}")
    tenant_id = source_fixture["tenant_id"]
    deal_id = source_fixture["deal_id"]
    package_id = source_fixture["package_id"]
    freeze_source_package(
        engine=database.app_engine,
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
    )

    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-restart:{label}:run"))
    proof_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-restart:{label}:proof"))
    proof_field_hash = _canonical_sha256(["task7-restart-proof-field-v1", label])
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, tenant_id)
        corpus_hash = str(
            connection.execute(
                text(
                    """
                    SELECT corpus_receipt_sha256
                    FROM source_lineage_objects
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND corpus_receipt_id=CAST(:package_id AS uuid)
                      AND object_kind='PACKAGE'
                      AND write_origin='RUNTIME'
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "package_id": package_id,
                },
            ).scalar_one()
        )
        binding = StrictPreflightBinding(
            tenant_id=tenant_id,
            deal_id=deal_id,
            corpus_hash=corpus_hash,
            strict_profile_generation="bootstrap-v1",
            strict_profile_hmac=proof_field_hash,
            pipeline_contract_version="full-strict-v21",
            pipeline_contract_hash=contract.contract_hash,
            expected_consumer_edge_rule_hash=proof_field_hash,
            static_decision_set_hash=proof_field_hash,
            methodology_version_hashes=(proof_field_hash,),
            promoted_pointer_hash=proof_field_hash,
            provider_receipt_set_hash=proof_field_hash,
            dependency_receipt_set_hash=proof_field_hash,
        )
        # The restart proof enters through both production composition roots:
        # source freeze first, then atomic strict-proof admission of all six roots.
        connection.execute(
            text(
                """
                INSERT INTO strict_preflight_proofs (
                    proof_id,tenant_id,deal_id,corpus_hash,
                    strict_profile_generation,strict_profile_hmac,
                    pipeline_contract_version,pipeline_contract_hash,
                    expected_consumer_edge_rule_hash,static_decision_set_hash,
                    methodology_version_hashes,promoted_pointer_hash,
                    provider_receipt_set_hash,dependency_receipt_set_hash,
                    binding_hash,expires_at
                ) VALUES (
                    CAST(:proof_id AS uuid),CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid),:corpus_hash,:strict_profile_generation,
                    :strict_profile_hmac,:pipeline_contract_version,
                    :pipeline_contract_hash,:expected_consumer_edge_rule_hash,
                    :static_decision_set_hash,
                    CAST(:methodology_version_hashes AS jsonb),
                    :promoted_pointer_hash,:provider_receipt_set_hash,
                    :dependency_receipt_set_hash,:binding_hash,
                    statement_timestamp()+interval '1 hour'
                )
                """
            ),
            {
                **binding.__dict__,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "corpus_hash": corpus_hash,
                "proof_id": proof_id,
                "methodology_version_hashes": json.dumps([proof_field_hash]),
                "binding_hash": binding.digest(),
            },
        )
        # Task 7 proves restart mechanics before its separately gated global activation.
        admission_registry = {
            "full-strict-v21": replace(
                contract,
                state=contract_registry.PipelineContractState.ACTIVE,
            )
        }
        PostgresStrictPreflightProofsRepository(
            connection,
            tenant_id,
            pipeline_contract_registry=admission_registry,
        ).consume_and_admit_contract_run(
            proof_id=proof_id,
            expected_binding=binding,
            run_id=run_id,
            deal_id=deal_id,
            mode="FULL",
            idempotency_key=f"task7-restart-{label}",
            source=None,
            created_by_actor_id="task7-hermetic-worker",
            created_by_actor_type="SERVICE",
            selected_source_package_ids=(package_id,),
        )
    return Task7LeaseScope(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=contract.contract_hash,
        pipeline_resume_state="RESUMABLE",
        lease_owner_id="00000000-0000-4000-8000-000000000001",
        lease_fencing_token=1,
    )


def _claim(
    database: DisposablePostgres,
    *,
    scope: Task7LeaseScope,
    owner_id: str,
) -> dict[str, Any]:
    with database.app_engine.begin() as connection:
        claimed = PostgresRunsRepository(connection, scope.tenant_id).claim_queued_runs(
            limit=1,
            lease_owner_id=owner_id,
            lease_seconds=120,
            lease_audit_emitter=_lease_transition_audit_emitter(
                audit_sink=PostgresAuditSink(),
                tenant_id=scope.tenant_id,
            ),
        )
        assert len(claimed) == 1
        return claimed[0]


def _step_completion_event(
    *,
    scope: Task7LeaseScope,
    step: RunStep,
    summary_sha256: str,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": now,
        "tenant_id": scope.tenant_id,
        "actor": {"actor_type": "SERVICE", "actor_id": "task7-hermetic-producer"},
        "request": {
            "request_id": scope.run_id,
            "method": "POST",
            "path": f"/v1/runs/{scope.run_id}",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "deal",
            "resource_id": scope.deal_id,
            "deal_id": scope.deal_id,
        },
        "event_type": "deal.run.step.completed",
        "severity": "LOW",
        "summary": f"Task 7 durable restart boundary {step.step_order}",
        "payload": {
            "safe": {
                "run_id": scope.run_id,
                "step_id": step.step_id,
                "step_name": step.step_name.value,
                "lease_fencing_token": scope.lease_fencing_token,
                "result_summary_sha256": summary_sha256,
            },
            "hashes": [summary_sha256],
            "refs": [],
        },
    }


def _complete_hermetic_step(
    database: DisposablePostgres,
    *,
    scope: Task7LeaseScope,
    step_order: int,
) -> None:
    with database.app_engine.begin() as connection:
        steps = PostgresRunStepsRepository(connection, scope.tenant_id)
        step = steps.get_by_run_id(scope.run_id)[step_order]
        assert step.status is StepStatus.PENDING
        spec = _active_contract().steps[step_order]
        semantic_spec = STEP_SEMANTIC_REGISTRY[spec.step_name]
        port = PostgresTask7CompletionPort(
            connection,
            scope.tenant_id,
            PostgresAuditSink(),
            120,
        )
        port.establish_tenant_rls(scope=scope)
        identity_sha256 = _canonical_sha256(
            ["task7-restart-step-receipt-identity-v1", scope.run_id, step_order]
        )
        output_sha256 = _canonical_sha256(["task7-restart-step-receipt-output-v1", identity_sha256])
        output_object_id = str(
            port._derive_output_id(
                scope=scope,
                output_class="STEP_RECEIPT",
                output_kind=semantic_spec.aggregate_receipt_kind,
                identity_sha256=identity_sha256,
                value_sha256=output_sha256,
            )
        )
        step_output_id = str(
            port._derive_step_output_id(
                scope=scope,
                step=step,
                subrole="__step__",
                output_kind=semantic_spec.aggregate_receipt_kind,
                ordinal=0,
                output_object_id=output_object_id,
            )
        )
        summary = {
            "status": "COMPLETED",
            "hermetic_step_order": step_order,
            "producer_kind": "TASK7_RESTART_PROOF",
        }
        summary_sha256 = _canonical_sha256(summary)
        graph = PostgresTask7RunGraphRepository(connection, scope.tenant_id)
        graph.insert_run_output_object(
            RunOutputObjectRecord(
                output_object_id=output_object_id,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                run_id=scope.run_id,
                output_class="STEP_RECEIPT",
                output_kind=semantic_spec.aggregate_receipt_kind,
                output_schema_version=spec.semantic_rule_version,
                native_identity_sha256=identity_sha256,
                native_output_sha256=output_sha256,
                pipeline_contract_version=scope.pipeline_contract_version,
                pipeline_contract_hash=scope.pipeline_contract_hash,
                write_origin="RUNTIME",
                lease_owner_id=scope.lease_owner_id,
                lease_fencing_token=scope.lease_fencing_token,
            )
        )
        graph.insert_run_step_output(
            RunStepOutputRecord(
                step_output_id=step_output_id,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                run_id=scope.run_id,
                step_id=step.step_id,
                output_object_id=output_object_id,
                step_name=step.step_name.value,
                subrole="__step__",
                output_kind=semantic_spec.aggregate_receipt_kind,
                output_ordinal=0,
                semantic_rule_version=spec.semantic_rule_version,
                semantic_rule_hash=spec.semantic_rule_hash,
                summary_sha256=summary_sha256,
                lease_owner_id=scope.lease_owner_id,
                lease_fencing_token=scope.lease_fencing_token,
            )
        )
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        steps.update(
            step.model_copy(
                update={
                    "status": StepStatus.COMPLETED,
                    "started_at": finished_at,
                    "finished_at": finished_at,
                    "result_summary": summary,
                }
            ),
            lease_owner_id=scope.lease_owner_id,
            lease_fencing_token=scope.lease_fencing_token,
        )
        PostgresAuditSink().emit_in_tx(
            connection,
            _step_completion_event(
                scope=scope,
                step=step,
                summary_sha256=summary_sha256,
            ),
        )


def _step_semantics(database: DisposablePostgres, scope: Task7LeaseScope) -> tuple[Any, ...]:
    with database.admin_engine.connect() as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT step_order,step_name,status,retry_count,result_summary
                    FROM run_steps
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND run_id=CAST(:run_id AS uuid)
                    ORDER BY step_order,step_id
                    """
                ),
                {"tenant_id": scope.tenant_id, "run_id": scope.run_id},
            )
        )


def _task7_graph_digest(database: DisposablePostgres, scope: Task7LeaseScope) -> str:
    sections: list[tuple[str, list[str]]] = []
    with database.admin_engine.connect() as connection:
        for relation, order_by in (
            ("run_output_objects", "output_object_id"),
            ("run_step_outputs", "step_output_id"),
            ("run_expected_consumer_edges", "expected_edge_id"),
            ("run_consumer_receipts", "receipt_id"),
            ("audit_events", "event_id"),
        ):
            rows = list(
                connection.execute(
                    text(
                        f"SELECT to_jsonb(row_value)::text "
                        f"FROM {relation} AS row_value "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                        + (
                            "AND request_id=:run_id "
                            if relation == "audit_events"
                            else "AND run_id=CAST(:run_id AS uuid) "
                        )
                        + f"ORDER BY {order_by}"
                    ),
                    {
                        "tenant_id": scope.tenant_id,
                        "run_id": scope.run_id,
                    },
                ).scalars()
            )
            sections.append((relation, rows))
    return _canonical_sha256(sections)


def test_real_postgres_all_27_restart_cuts_reclaim_and_fence_without_repair() -> None:
    """Prove the non-circular lease/repository restart layer on real PostgreSQL."""
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            provision_exact_erasure_principals(connection)
        migrate(database.admin_engine, "0038")
        upgrade_disposable_to_dev_0039(database)
        grant_application_access(database)
        with database.app_engine.connect() as connection:
            posture = connection.execute(
                text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            ).one()
            assert tuple(posture) == (False, False)

        for cut in range(1, 28):
            first_owner = str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-restart:{cut}:worker-a"))
            second_owner = str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-restart:{cut}:worker-b"))
            seeded = _seed_durable_restart_run(database, label=f"recovered-{cut}")
            first_claim = _claim(
                database,
                scope=seeded,
                owner_id=first_owner,
            )
            assert first_claim["lease_fencing_token"] == 1
            worker_a = replace(
                seeded,
                lease_owner_id=first_owner,
                lease_fencing_token=1,
            )
            for step_order in range(cut):
                _complete_hermetic_step(
                    database,
                    scope=worker_a,
                    step_order=step_order,
                )

            with database.admin_engine.connect() as connection:
                pending_before = connection.execute(
                    text(
                        """
                        SELECT to_jsonb(row_value)::text
                        FROM run_steps AS row_value
                        WHERE tenant_id=CAST(:tenant_id AS uuid)
                          AND run_id=CAST(:run_id AS uuid)
                          AND step_order=:step_order
                        """
                    ),
                    {
                        "tenant_id": worker_a.tenant_id,
                        "run_id": worker_a.run_id,
                        "step_order": cut,
                    },
                ).scalar_one()
            assert json.loads(pending_before)["status"] == "PENDING"

            with database.admin_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE runs SET lease_expires_at="
                        "statement_timestamp()-interval '1 second' "
                        "WHERE run_id=CAST(:run_id AS uuid)"
                    ),
                    {"run_id": worker_a.run_id},
                )
            with database.app_engine.begin() as connection:
                requeued = PostgresRunsRepository(
                    connection,
                    worker_a.tenant_id,
                ).requeue_expired_worker_leases(
                    limit=1,
                    lease_audit_emitter=_lease_transition_audit_emitter(
                        audit_sink=PostgresAuditSink(),
                        tenant_id=worker_a.tenant_id,
                    ),
                )
                assert len(requeued) == 1
                assert requeued[0]["lease_fencing_token"] == 2
            second_claim = _claim(
                database,
                scope=worker_a,
                owner_id=second_owner,
            )
            assert second_claim["lease_fencing_token"] == 3
            worker_b = replace(
                seeded,
                lease_owner_id=second_owner,
                lease_fencing_token=3,
            )

            before_zombie = _task7_graph_digest(database, worker_b)
            stale_service = Task7CompletionFinalizationService(
                connection_factory=database.app_engine.begin,
                port_factory=lambda connection, tenant_id: PostgresTask7CompletionPort(
                    connection,
                    tenant_id,
                    PostgresAuditSink(),
                    120,
                ),
            )
            stale_step = _steps(completed_count=cut)[cut]
            with pytest.raises(Task7CompletionError) as fenced:
                stale_service.complete_step(
                    Task7StepCompletionCommand(
                        scope=worker_a,
                        step_id=stale_step.step_id,
                        step_name=stale_step.step_name.value,
                        semantic_input=SimpleNamespace(),  # type: ignore[arg-type]
                        staged_native_writes=(),
                    )
                )
            assert fenced.value.code == "WORKER_LEASE_FENCED"
            assert _task7_graph_digest(database, worker_b) == before_zombie

            with database.app_engine.begin() as connection:
                port = PostgresTask7CompletionPort(
                    connection,
                    worker_b.tenant_id,
                    PostgresAuditSink(),
                    120,
                )
                port.establish_tenant_rls(scope=worker_b)
                assert port.load_resumable_run(scope=worker_b)["lease_fencing_token"] == 3
                rows = tuple(port.list_canonical_steps(scope=worker_b))
                plan = rehydrate_strict_run(
                    tenant_id=worker_b.tenant_id,
                    run_id=worker_b.run_id,
                    pipeline_contract_version=worker_b.pipeline_contract_version,
                    pipeline_contract_hash=worker_b.pipeline_contract_hash,
                    pipeline_resume_state=worker_b.pipeline_resume_state,
                    steps=rows,
                    validate_completed=lambda completed: (
                        completed.result_summary.get("producer_kind") == "TASK7_RESTART_PROOF"
                    ),
                )
                assert plan.completed_count == cut
                assert plan.next_step_order == cut
                assert port.validate_step_predecessors(
                    scope=worker_b,
                    step=rows[cut],
                )

            with database.admin_engine.connect() as connection:
                pending_after = connection.execute(
                    text(
                        """
                        SELECT to_jsonb(row_value)::text
                        FROM run_steps AS row_value
                        WHERE tenant_id=CAST(:tenant_id AS uuid)
                          AND run_id=CAST(:run_id AS uuid)
                          AND step_order=:step_order
                        """
                    ),
                    {
                        "tenant_id": worker_b.tenant_id,
                        "run_id": worker_b.run_id,
                        "step_order": cut,
                    },
                ).scalar_one()
            assert pending_after == pending_before

            for step_order in range(cut, 28):
                _complete_hermetic_step(
                    database,
                    scope=worker_b,
                    step_order=step_order,
                )

            oracle_seeded = _seed_durable_restart_run(database, label=f"oracle-{cut}")
            oracle_owner = str(uuid.uuid5(uuid.NAMESPACE_URL, f"task7-restart:{cut}:oracle"))
            oracle_claim = _claim(
                database,
                scope=oracle_seeded,
                owner_id=oracle_owner,
            )
            oracle = replace(
                oracle_seeded,
                lease_owner_id=oracle_owner,
                lease_fencing_token=oracle_claim["lease_fencing_token"],
            )
            for step_order in range(28):
                _complete_hermetic_step(
                    database,
                    scope=oracle,
                    step_order=step_order,
                )
            assert _step_semantics(database, worker_b) == _step_semantics(
                database,
                oracle,
            )


def test_real_postgres_step_boundary_exception_rolls_back_graph_step_and_audit() -> None:
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            provision_exact_erasure_principals(connection)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        seeded = _seed_durable_restart_run(database, label="rollback")
        owner = str(uuid.uuid5(uuid.NAMESPACE_URL, "task7-restart:rollback:owner"))
        claim = _claim(database, scope=seeded, owner_id=owner)
        scope = replace(
            seeded,
            lease_owner_id=owner,
            lease_fencing_token=claim["lease_fencing_token"],
        )
        before = _task7_graph_digest(database, scope)
        with (
            pytest.raises(RuntimeError, match="INJECTED_BOUNDARY_ROLLBACK"),
            database.app_engine.begin() as connection,
        ):
            steps = PostgresRunStepsRepository(connection, scope.tenant_id)
            step = steps.get_by_run_id(scope.run_id)[0]
            steps.update(
                step.model_copy(
                    update={
                        "status": StepStatus.COMPLETED,
                        "result_summary": {"producer_kind": "SHOULD_ROLL_BACK"},
                    }
                ),
                lease_owner_id=scope.lease_owner_id,
                lease_fencing_token=scope.lease_fencing_token,
            )
            raise RuntimeError("INJECTED_BOUNDARY_ROLLBACK")
        assert _task7_graph_digest(database, scope) == before
        assert _step_semantics(database, scope)[0][2] == "PENDING"
