"""Focused v18 guard-owner and erasure-runner readiness mutation contracts."""

from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from tests.slice100_task7.test_erasure_executor_profile_readiness_red import (
    _ERASURE_URL,
    _ready_facts,
)


def _module() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_readiness")


def _check(module: ModuleType, facts: object) -> object:
    return module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )


def test_v18_current48_future38_runner86_tripwire_is_exact_and_detects_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    current = module.TASK7_V18_CURRENT_ERASURE_RELATIONS
    future = module.TASK7_V18_FUTURE_ERASURE_RELATIONS
    complete = module.TASK7_V18_COMPLETE_ERASURE_RELATIONS

    assert (len(current), len(future), len(complete)) == (48, 38, 86)
    assert len(set(current)) == 48
    assert len(set(future)) == 38
    assert len(set(complete)) == 86
    assert set(current).isdisjoint(future)
    assert set(current) | set(future) == set(complete)
    assert tuple(item for item in complete if item in set(current)) == current
    assert module.task7_v18_erasure_runner_split_is_valid() is True

    monkeypatch.setattr(
        module,
        "TASK7_V18_CURRENT_ERASURE_RELATIONS",
        current[:-1],
    )
    assert module.task7_v18_erasure_runner_split_is_valid() is False


def test_v18_runtime_tripwires_are_transcribed_from_the_frozen_artifact() -> None:
    module = _module()
    artifact_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "full_strict_v18_reviewed_preimage_v1.json"
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    erasure = artifact["erasure_execution_registry"]["rows"][0]
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]

    assert tuple(erasure["current_relation_tuple"]) == (module.TASK7_V18_CURRENT_ERASURE_RELATIONS)
    assert tuple(erasure["future_relation_tuple"]) == (module.TASK7_V18_FUTURE_ERASURE_RELATIONS)
    assert tuple(erasure["complete_relation_tuple"]) == (
        module.TASK7_V18_COMPLETE_ERASURE_RELATIONS
    )
    assert (
        tuple(
            sorted(
                row["function_signature"]
                for row in acl["function_rows"]
                if row["install_scope"] == "CURRENT_0038"
            )
        )
        == module.TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES
    )
    assert (
        tuple(
            sorted(
                row["function_signature"]
                for row in acl["function_rows"]
                if row["install_scope"] == "FUTURE_INACTIVE"
            )
        )
        == module.TASK7_V18_FUTURE_GUARD_ROUTINE_SIGNATURES
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda module, facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                role=replace(facts.guard_owner.role, can_login=True),
            ),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, membership_count=1),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, owned_relation_count=1),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, owned_routine_count=23),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, schema_create=True),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                schema_direct_usage_acl_count=0,
            ),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, schema_unexpected_acl_count=1),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, table_select_privilege_count=1),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, column_select_acl_count=423),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, column_select_acl_sha256="0" * 64),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, force_rls_relation_count=35),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                current_routine_signatures=(
                    *module.TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES[:-1],
                    "public.unreviewed_guard()",
                ),
            ),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                current_routine_posture_mismatch_count=1,
            ),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                external_helper_signature="public.wrong(uuid,uuid)",
            ),
        ),
        lambda module, facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                future_routine_signatures_present=(
                    module.TASK7_V18_FUTURE_GUARD_ROUTINE_SIGNATURES[0],
                ),
            ),
        ),
        lambda module, facts: replace(
            facts,
            runner_contract=replace(
                facts.runner_contract,
                delete_relation_tuple=module.TASK7_V18_COMPLETE_ERASURE_RELATIONS,
            ),
        ),
        lambda module, facts: replace(
            facts,
            runner_contract=replace(
                facts.runner_contract,
                result_relation_tuple=module.TASK7_V18_CURRENT_ERASURE_RELATIONS[:-1],
            ),
        ),
        lambda module, facts: replace(
            facts,
            runner_contract=replace(
                facts.runner_contract,
                future_relation_tuple_present=(module.TASK7_V18_FUTURE_ERASURE_RELATIONS[0],),
            ),
        ),
    ],
)
def test_v18_readiness_rejects_guard_owner_or_runner_posture_drift(
    mutation: object,
) -> None:
    module = _module()
    facts = mutation(module, _ready_facts(module))

    result = _check(module, facts)

    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                application_direct_execute_count=1,
            ),
        ),
        lambda facts: replace(
            facts,
            guard_owner=replace(facts.guard_owner, public_direct_execute_count=1),
        ),
        lambda facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                unexpected_execute_grant_count=1,
            ),
        ),
        lambda facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                external_helper_application_execute=True,
            ),
        ),
        lambda facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                external_helper_public_execute=True,
            ),
        ),
        lambda facts: replace(
            facts,
            guard_owner=replace(
                facts.guard_owner,
                external_helper_guard_owner_execute=False,
            ),
        ),
    ],
)
def test_v18_readiness_rejects_direct_helper_execution_edges(
    mutation: object,
) -> None:
    module = _module()
    facts = mutation(_ready_facts(module))

    result = _check(module, facts)

    assert result.code == "ERASURE_EXECUTOR_UNAUTHORIZED"
