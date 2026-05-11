"""Guard tests for run-step schema capacity assumptions."""

from __future__ import annotations

from idis.models.run_step import StepName

RUN_STEP_NAME_DB_MAX_LENGTH = 100


def test_all_step_names_fit_run_steps_schema_capacity() -> None:
    """All canonical step names must fit the Postgres run_steps.step_name width."""
    longest_step_name = max(StepName, key=lambda step: len(step.value))

    assert longest_step_name is StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
    assert len(StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN.value) <= (
        RUN_STEP_NAME_DB_MAX_LENGTH
    )
    assert all(len(step.value) <= RUN_STEP_NAME_DB_MAX_LENGTH for step in StepName)
