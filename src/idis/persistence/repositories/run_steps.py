"""In-memory RunStep repository — tenant-scoped persistence for step ledger.

Mirrors the pattern of InMemoryDealsRepository: module-level dict store,
tenant filtering on all reads, and a clear function for test teardown.
"""

from __future__ import annotations

from typing import Any

from idis.models.run_step import RunStep, StepName

_run_steps_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by step_id."""


class InMemoryRunStepsRepository:
    """Tenant-scoped in-memory repository for RunStep records.

    All reads filter by tenant_id so cross-tenant access returns empty
    results (no existence oracle).

    Args:
        tenant_id: Tenant UUID string for scoping.
    """

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context.

        Args:
            tenant_id: Tenant UUID for isolation.
        """
        self._tenant_id = tenant_id

    def create(self, step: RunStep) -> RunStep:
        """Persist a new RunStep record.

        Args:
            step: RunStep instance to store.

        Returns:
            The stored RunStep.

        Raises:
            ValueError: If step.tenant_id does not match repository tenant.
        """
        if step.tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in RunStep creation")
        _run_steps_store[step.step_id] = step.model_dump()
        return step

    def get_by_run_id(self, run_id: str) -> list[RunStep]:
        """Return all steps for a run, ordered by step_order.

        Only returns steps belonging to the repository tenant.
        Cross-tenant run_ids silently return empty list (no existence leak).

        Args:
            run_id: Run UUID to query.

        Returns:
            List of RunStep sorted by step_order ascending.
        """
        steps = [
            RunStep.model_validate(data)
            for data in _run_steps_store.values()
            if data["run_id"] == run_id and data["tenant_id"] == self._tenant_id
        ]
        steps.sort(key=lambda s: s.step_order)
        return steps

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None:
        """Get a specific step by run_id and step_name.

        Returns None for cross-tenant access (no existence leak).

        Args:
            run_id: Run UUID.
            step_name: Canonical step name.

        Returns:
            RunStep if found and tenant matches, else None.
        """
        for data in _run_steps_store.values():
            if (
                data["run_id"] == run_id
                and data["step_name"] == step_name.value
                and data["tenant_id"] == self._tenant_id
            ):
                return RunStep.model_validate(data)
        return None

    def update(self, step: RunStep) -> RunStep:
        """Update an existing RunStep record.

        Args:
            step: RunStep with updated fields.

        Returns:
            The updated RunStep.

        Raises:
            ValueError: If step.tenant_id does not match repository tenant.
            KeyError: If step_id not found in store.
        """
        if step.tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in RunStep update")
        if step.step_id not in _run_steps_store:
            raise KeyError(f"RunStep {step.step_id} not found")
        _run_steps_store[step.step_id] = step.model_dump()
        return step


def clear_run_steps_store() -> None:
    """Clear the in-memory run steps store. For testing only."""
    _run_steps_store.clear()
