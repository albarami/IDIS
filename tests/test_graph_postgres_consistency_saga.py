"""Tests for Graph-DB + Postgres dual-write consistency saga.

Phase POST-5.2: Tests for DualWriteSagaExecutor ensuring both stores
either both succeed or both roll back.
"""

from __future__ import annotations

from typing import Any

from idis.persistence.saga import (
    DualWriteConsistencyError,
    DualWriteSagaExecutor,
    GraphWriteStep,
    PostgresWriteStep,
    SagaResult,
    SagaStatus,
    SagaStepStatus,
    create_claim_dual_write_saga,
    create_sanad_dual_write_saga,
)


class TestSagaStepExecution:
    """Tests for individual saga step execution."""

    def test_postgres_step_executes(self) -> None:
        """Test that PostgresWriteStep executes correctly."""
        executed = []

        def execute_fn(ctx: dict[str, Any]) -> str:
            executed.append("execute")
            return "record-001"

        def compensate_fn(ctx: dict[str, Any], result: str) -> None:
            executed.append(f"compensate:{result}")

        step = PostgresWriteStep("test_step", execute_fn, compensate_fn)
        result = step.execute({})

        assert result == "record-001"
        assert executed == ["execute"]

    def test_graph_step_executes(self) -> None:
        """Test that GraphWriteStep executes correctly."""
        executed = []

        def execute_fn(ctx: dict[str, Any]) -> str:
            executed.append("execute")
            return "node-001"

        def compensate_fn(ctx: dict[str, Any], result: str) -> None:
            executed.append(f"compensate:{result}")

        step = GraphWriteStep("test_step", execute_fn, compensate_fn)
        result = step.execute({})

        assert result == "node-001"
        assert executed == ["execute"]

    def test_step_compensation(self) -> None:
        """Test that step compensation works."""
        compensated = []

        def execute_fn(ctx: dict[str, Any]) -> str:
            return "record-001"

        def compensate_fn(ctx: dict[str, Any], result: str) -> None:
            compensated.append(result)

        step = PostgresWriteStep("test_step", execute_fn, compensate_fn)
        step.compensate({}, "record-001")

        assert compensated == ["record-001"]


class TestDualWriteSagaSuccess:
    """Tests for successful dual-write saga execution."""

    def test_both_steps_succeed(self) -> None:
        """Test that saga completes when both steps succeed."""
        executed = []

        def pg_execute(ctx: dict[str, Any]) -> str:
            executed.append("pg_execute")
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("pg_compensate")

        def graph_execute(ctx: dict[str, Any]) -> str:
            executed.append("graph_execute")
            return "graph-001"

        def graph_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_compensate")

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("pg_insert", pg_execute, pg_compensate)
            .add_graph_step("graph_insert", graph_execute, graph_compensate)
        )

        result = saga.execute()

        assert result.is_success
        assert result.status == SagaStatus.COMPLETED
        assert executed == ["pg_execute", "graph_execute"]
        # No compensation should have been called
        assert "pg_compensate" not in executed
        assert "graph_compensate" not in executed

    def test_saga_result_contains_step_results(self) -> None:
        """Test that saga result contains per-step results."""

        def pg_execute(ctx: dict[str, Any]) -> str:
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        saga = DualWriteSagaExecutor("saga-001").add_postgres_step(
            "pg_insert", pg_execute, pg_compensate
        )

        result = saga.execute()

        assert len(result.step_results) == 1
        assert result.step_results[0].step_name == "pg_insert"
        assert result.step_results[0].status == SagaStepStatus.COMPLETED
        assert result.step_results[0].result == "pg-001"


class TestDualWriteSagaFailure:
    """Tests for saga failure and compensation."""

    def test_postgres_fails_no_compensation_needed(self) -> None:
        """Test that if Postgres fails first, no compensation is needed."""
        executed = []

        def pg_execute(ctx: dict[str, Any]) -> str:
            executed.append("pg_execute")
            raise RuntimeError("Postgres connection failed")

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("pg_compensate")

        def graph_execute(ctx: dict[str, Any]) -> str:
            executed.append("graph_execute")
            return "graph-001"

        def graph_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_compensate")

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("pg_insert", pg_execute, pg_compensate)
            .add_graph_step("graph_insert", graph_execute, graph_compensate)
        )

        result = saga.execute()

        assert not result.is_success
        assert result.status == SagaStatus.COMPENSATED
        # Postgres failed, graph never executed, no compensation needed
        assert executed == ["pg_execute"]

    def test_graph_fails_postgres_compensated(self) -> None:
        """Test that if Graph fails, Postgres is compensated."""
        executed = []

        def pg_execute(ctx: dict[str, Any]) -> str:
            executed.append("pg_execute")
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append(f"pg_compensate:{result}")

        def graph_execute(ctx: dict[str, Any]) -> str:
            executed.append("graph_execute")
            raise RuntimeError("Graph connection failed")

        def graph_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_compensate")

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("pg_insert", pg_execute, pg_compensate)
            .add_graph_step("graph_insert", graph_execute, graph_compensate)
        )

        result = saga.execute()

        assert not result.is_success
        assert result.is_compensated
        assert result.status == SagaStatus.COMPENSATED
        # Postgres executed, graph failed, postgres compensated
        assert executed == ["pg_execute", "graph_execute", "pg_compensate:pg-001"]

    def test_multiple_steps_all_compensated(self) -> None:
        """Test that all completed steps are compensated on failure."""
        executed = []

        def step1_execute(ctx: dict[str, Any]) -> str:
            executed.append("step1_execute")
            return "s1-001"

        def step1_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("step1_compensate")

        def step2_execute(ctx: dict[str, Any]) -> str:
            executed.append("step2_execute")
            return "s2-001"

        def step2_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("step2_compensate")

        def step3_execute(ctx: dict[str, Any]) -> str:
            executed.append("step3_execute")
            raise RuntimeError("Step 3 failed")

        def step3_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("step3_compensate")

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("step1", step1_execute, step1_compensate)
            .add_postgres_step("step2", step2_execute, step2_compensate)
            .add_postgres_step("step3", step3_execute, step3_compensate)
        )

        result = saga.execute()

        assert result.is_compensated
        # Steps 1 and 2 completed, step 3 failed
        # Compensation should be in reverse order: step2, step1
        assert executed == [
            "step1_execute",
            "step2_execute",
            "step3_execute",
            "step2_compensate",
            "step1_compensate",
        ]


class TestCompensationFailure:
    """Tests for compensation failure scenarios."""

    def test_compensation_failure_reported(self) -> None:
        """Test that compensation failure is properly reported."""
        executed = []

        def pg_execute(ctx: dict[str, Any]) -> str:
            executed.append("pg_execute")
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("pg_compensate")
            raise RuntimeError("Compensation also failed!")

        def graph_execute(ctx: dict[str, Any]) -> str:
            executed.append("graph_execute")
            raise RuntimeError("Graph failed")

        def graph_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_compensate")

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("pg_insert", pg_execute, pg_compensate)
            .add_graph_step("graph_insert", graph_execute, graph_compensate)
        )

        result = saga.execute()

        assert not result.is_success
        assert not result.is_compensated
        assert result.status == SagaStatus.COMPENSATION_FAILED

    def test_all_compensations_attempted(self) -> None:
        """Test that all compensations are attempted even if some fail."""
        executed = []

        def step1_execute(ctx: dict[str, Any]) -> str:
            return "s1"

        def step1_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("step1_compensate")

        def step2_execute(ctx: dict[str, Any]) -> str:
            return "s2"

        def step2_compensate(ctx: dict[str, Any], result: str) -> None:
            executed.append("step2_compensate")
            raise RuntimeError("Step 2 compensation failed")

        def step3_execute(ctx: dict[str, Any]) -> str:
            raise RuntimeError("Step 3 failed")

        def step3_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("step1", step1_execute, step1_compensate)
            .add_postgres_step("step2", step2_execute, step2_compensate)
            .add_postgres_step("step3", step3_execute, step3_compensate)
        )

        saga.execute()

        # Both compensations should have been attempted
        assert "step2_compensate" in executed
        assert "step1_compensate" in executed


class TestSagaContext:
    """Tests for saga context passing between steps."""

    def test_context_shared_between_steps(self) -> None:
        """Test that context is shared between saga steps."""

        def pg_execute(ctx: dict[str, Any]) -> str:
            ctx["pg_id"] = "pg-001"
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        def graph_execute(ctx: dict[str, Any]) -> str:
            # Graph step should see pg_id from postgres step
            assert ctx.get("pg_id") == "pg-001"
            ctx["graph_id"] = "graph-001"
            return "graph-001"

        def graph_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("pg_insert", pg_execute, pg_compensate)
            .add_graph_step("graph_insert", graph_execute, graph_compensate)
        )

        result = saga.execute({"tenant_id": "tenant-001"})

        assert result.is_success

    def test_initial_context_passed(self) -> None:
        """Test that initial context is passed to steps."""
        received_context = {}

        def pg_execute(ctx: dict[str, Any]) -> str:
            received_context.update(ctx)
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        saga = DualWriteSagaExecutor("saga-001").add_postgres_step(
            "pg_insert", pg_execute, pg_compensate
        )

        saga.execute({"tenant_id": "tenant-001", "deal_id": "deal-001"})

        assert received_context["tenant_id"] == "tenant-001"
        assert received_context["deal_id"] == "deal-001"


class TestHelperFunctions:
    """Tests for saga helper functions."""

    def test_create_claim_dual_write_saga(self) -> None:
        """Test creating a claim dual-write saga."""
        executed = []

        saga = create_claim_dual_write_saga(
            saga_id="claim-saga-001",
            postgres_insert=lambda ctx: (executed.append("pg"), "pg-001")[1],
            postgres_delete=lambda ctx, r: executed.append(f"pg_del:{r}"),
            graph_insert=lambda ctx: (executed.append("graph"), "graph-001")[1],
            graph_delete=lambda ctx, r: executed.append(f"graph_del:{r}"),
        )

        result = saga.execute({"claim_id": "claim-001"})

        assert result.is_success
        assert executed == ["pg", "graph"]

    def test_create_sanad_dual_write_saga(self) -> None:
        """Test creating a Sanad dual-write saga."""
        executed = []

        saga = create_sanad_dual_write_saga(
            saga_id="sanad-saga-001",
            postgres_insert=lambda ctx: (executed.append("pg"), "pg-001")[1],
            postgres_delete=lambda ctx, r: executed.append(f"pg_del:{r}"),
            graph_insert=lambda ctx: (executed.append("graph"), "graph-001")[1],
            graph_delete=lambda ctx, r: executed.append(f"graph_del:{r}"),
        )

        result = saga.execute({"sanad_id": "sanad-001"})

        assert result.is_success


class TestDualWriteConsistencyError:
    """Tests for DualWriteConsistencyError."""

    def test_error_from_saga_result(self) -> None:
        """Test creating error from saga result."""

        def pg_execute(ctx: dict[str, Any]) -> str:
            return "pg-001"

        def pg_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        def graph_execute(ctx: dict[str, Any]) -> str:
            raise RuntimeError("Graph failed")

        def graph_compensate(ctx: dict[str, Any], result: str) -> None:
            pass

        saga = (
            DualWriteSagaExecutor("saga-001")
            .add_postgres_step("pg", pg_execute, pg_compensate)
            .add_graph_step("graph", graph_execute, graph_compensate)
        )

        result = saga.execute()
        error = DualWriteConsistencyError(result)

        assert "saga-001" in str(error)
        assert "compensated" in str(error)
        assert error.saga_result == result


class TestSagaResultProperties:
    """Tests for SagaResult properties."""

    def test_is_success_property(self) -> None:
        """Test is_success property."""
        result = SagaResult(saga_id="test", status=SagaStatus.COMPLETED)
        assert result.is_success is True

        result = SagaResult(saga_id="test", status=SagaStatus.FAILED)
        assert result.is_success is False

    def test_is_compensated_property(self) -> None:
        """Test is_compensated property."""
        result = SagaResult(saga_id="test", status=SagaStatus.COMPENSATED)
        assert result.is_compensated is True

        result = SagaResult(saga_id="test", status=SagaStatus.COMPLETED)
        assert result.is_compensated is False


class TestEmptySaga:
    """Tests for edge cases."""

    def test_empty_saga_succeeds(self) -> None:
        """Test that an empty saga completes successfully."""
        saga = DualWriteSagaExecutor("empty-saga")
        result = saga.execute()

        assert result.is_success
        assert result.status == SagaStatus.COMPLETED
        assert len(result.step_results) == 0


class TestGraphProjectionSagaIntegration:
    """Tests for saga integration with graph projection (Phase 7.B).

    Validates:
    - Postgres write + Neo4j projection both succeed → COMPLETED
    - Postgres write succeeds + Neo4j projection fails → COMPENSATED + audit
    """

    def test_both_succeed(self) -> None:
        """Postgres + graph projection succeed → saga COMPLETED."""
        executed: list[str] = []

        def pg_insert(ctx: dict[str, Any]) -> str:
            executed.append("pg_insert")
            return "claim-pg-001"

        def pg_delete(ctx: dict[str, Any], result: str) -> None:
            executed.append(f"pg_delete:{result}")

        def graph_project(ctx: dict[str, Any]) -> str:
            executed.append("graph_project")
            return "claim-graph-001"

        def graph_delete(ctx: dict[str, Any], result: str) -> None:
            executed.append(f"graph_delete:{result}")

        saga = (
            DualWriteSagaExecutor("claim-projection-001")
            .add_postgres_step("postgres_claim_insert", pg_insert, pg_delete)
            .add_graph_step("graph_claim_projection", graph_project, graph_delete)
        )

        result = saga.execute({"tenant_id": "tenant-001"})

        assert result.is_success
        assert result.status == SagaStatus.COMPLETED
        assert executed == ["pg_insert", "graph_project"]

    def test_graph_fails_postgres_compensated(self) -> None:
        """Postgres succeeds, graph fails → saga COMPENSATED, Postgres rolled back."""
        executed: list[str] = []

        def pg_insert(ctx: dict[str, Any]) -> str:
            executed.append("pg_insert")
            return "claim-pg-001"

        def pg_delete(ctx: dict[str, Any], result: str) -> None:
            executed.append(f"pg_delete:{result}")

        def graph_project(ctx: dict[str, Any]) -> str:
            executed.append("graph_project")
            raise RuntimeError("Neo4j connection refused")

        def graph_delete(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_delete")

        saga = (
            DualWriteSagaExecutor("claim-projection-002")
            .add_postgres_step("postgres_claim_insert", pg_insert, pg_delete)
            .add_graph_step("graph_claim_projection", graph_project, graph_delete)
        )

        result = saga.execute({"tenant_id": "tenant-001"})

        assert not result.is_success
        assert result.is_compensated
        assert result.status == SagaStatus.COMPENSATED
        assert executed == [
            "pg_insert",
            "graph_project",
            "pg_delete:claim-pg-001",
        ]
        assert "Neo4j connection refused" in str(result.error)

    def test_saga_with_multiple_graph_steps(self) -> None:
        """Multi-step saga: PG + graph deal + graph claim."""
        executed: list[str] = []

        def pg_insert(ctx: dict[str, Any]) -> str:
            executed.append("pg_insert")
            return "pg-001"

        def pg_delete(ctx: dict[str, Any], result: str) -> None:
            executed.append("pg_delete")

        def graph_deal(ctx: dict[str, Any]) -> str:
            executed.append("graph_deal")
            return "deal-graph-001"

        def graph_deal_del(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_deal_del")

        def graph_claim(ctx: dict[str, Any]) -> str:
            executed.append("graph_claim")
            raise RuntimeError("Claim projection failed")

        def graph_claim_del(ctx: dict[str, Any], result: str) -> None:
            executed.append("graph_claim_del")

        saga = (
            DualWriteSagaExecutor("multi-projection-001")
            .add_postgres_step("pg", pg_insert, pg_delete)
            .add_graph_step("graph_deal", graph_deal, graph_deal_del)
            .add_graph_step("graph_claim", graph_claim, graph_claim_del)
        )

        result = saga.execute({"tenant_id": "tenant-001"})

        assert not result.is_success
        assert result.is_compensated
        assert executed == [
            "pg_insert",
            "graph_deal",
            "graph_claim",
            "graph_deal_del",
            "pg_delete",
        ]
