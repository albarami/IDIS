"""RED proof for the Task-6 sibling of the v14 BEFORE/generated-column defect."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v14_reviewed_preimage_v1.json"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v14_current_guard_census.py"
_SCAFFOLD_PATH = _REPO_ROOT / "scripts" / "_task7_v14_census_scaffold.py"
_AUTHORITY_ID = "current-task6-immutable-replacement-v1"
_LEGAL_ATTACH_TARGETS = (
    (
        "methodology_extraction_results",
        "trg_methodology_extraction_results_immutable",
        "METHODOLOGY_EXTRACTION_RESULT",
    ),
    (
        "methodology_result_factual_outputs",
        "trg_methodology_result_factual_outputs_immutable",
        "METHODOLOGY_FACTUAL_OUTPUT",
    ),
)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _failure(runner: ModuleType, exc: BaseException) -> tuple[str, str]:
    cause = exc.__cause__
    assert isinstance(cause, DBAPIError)
    marker = str(cause.orig).splitlines()[0].strip()
    return (
        str(runner._sqlstate(cause)),
        marker,
    )


def test_v14_task6_legal_output_root_attach_sees_final_generated_shape() -> None:
    """Both legal UPDATE targets must accept; v12 bytes currently reject both."""

    runner = _load(_RUNNER_PATH, "_task7_v14_task6_before_red_runner")
    scaffold = _load(_SCAFFOLD_PATH, "_task7_v14_task6_before_red_scaffold")
    artifact = cast(dict[str, Any], json.loads(_ARTIFACT_PATH.read_bytes()))
    authority = next(
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == _AUTHORITY_ID
    )
    assert len(authority["target_tuple"]) == 8
    authority_targets = {tuple(target) for target in authority["target_tuple"]}
    assert {
        (table_name, guard_name) for table_name, guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS
    }.issubset(authority_targets)
    assert set(runner._TASK6_OUTPUT_ROOT_UPDATE_TARGETS) == {
        table for table, _guard, _expected_kind in _LEGAL_ATTACH_TARGETS
    }

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        with database.admin_engine.begin() as admin:
            provision_exact_erasure_principals(admin)
            scaffold.install_artifact_sql_scaffold(
                admin,
                artifact,
                application_role=database.role_name,
            )
            runner._grant_external_erasure_helper_read_closure(admin)
            scaffold.disable_all_current_user_triggers(admin, artifact)
            relation_names = tuple(
                dict.fromkeys(
                    [
                        *(
                            str(row["table_name"])
                            for row in artifact["current_ddl_registry"]["rows"]
                        ),
                        "run_methodology_selections",
                        "methodology_questions",
                    ]
                )
            )
            runner._V11._ENGINE._drop_non_guard_constraints(
                admin,
                relation_names=relation_names,
            )
            runner._V11._ENGINE._disable_target_relations(
                admin,
                tuple(
                    (
                        _AUTHORITY_ID,
                        table_name,
                        guard_name,
                    )
                    for table_name, guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS
                ),
            )
            columns = cast(
                dict[str, dict[str, tuple[str, bool]]],
                runner._V11._ENGINE._column_inventory(admin),
            )
            admin.exec_driver_sql(
                """
                CREATE FUNCTION public.idis_task7_v14_before_generated_probe()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.run_output_object_class IS NULL
                     AND NEW.run_output_object_kind IS NULL THEN
                    RAISE EXCEPTION 'TASK7_V14_PROBE_BOTH_GENERATED_NULL'
                      USING ERRCODE='40001';
                  END IF;
                  RAISE EXCEPTION 'TASK7_V14_PROBE_GENERATED_VALUE_UNEXPECTED'
                    USING ERRCODE='40001';
                END
                $$
                """
            )
            for table_name, _guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS:
                admin.exec_driver_sql(
                    "CREATE TRIGGER aaa_task7_v14_before_generated_probe "
                    f"BEFORE UPDATE ON public.{table_name} FOR EACH ROW "
                    "EXECUTE FUNCTION public.idis_task7_v14_before_generated_probe()"
                )

        probe_results: list[tuple[str, str, str]] = []
        for table_name, guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS:
            try:
                runner._run_one_case_as_app(
                    database=database,
                    artifact=artifact,
                    columns=columns,
                    authority_id=_AUTHORITY_ID,
                    table_name=table_name,
                    guard_name=guard_name,
                    case_kind="LEGAL",
                    trigger_event="UPDATE",
                )
            except runner.CensusError as exc:
                probe_results.append((table_name, *_failure(runner, exc)))

        with database.admin_engine.begin() as admin:
            for table_name, _guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS:
                admin.exec_driver_sql(
                    f"DROP TRIGGER aaa_task7_v14_before_generated_probe ON public.{table_name}"
                )
            admin.exec_driver_sql("DROP FUNCTION public.idis_task7_v14_before_generated_probe()")

        actual_results: list[tuple[str, str, str, tuple[tuple[Any, ...], ...]]] = []
        for table_name, guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS:
            try:
                _outcome, metrics = runner._run_one_case_as_app(
                    database=database,
                    artifact=artifact,
                    columns=columns,
                    authority_id=_AUTHORITY_ID,
                    table_name=table_name,
                    guard_name=guard_name,
                    case_kind="LEGAL",
                    trigger_event="UPDATE",
                )
                actual_results.append(
                    (
                        table_name,
                        "ACCEPTED",
                        "NONE",
                        tuple(metrics["task6_generated_value_projection_tuple"]),
                    )
                )
            except runner.CensusError as exc:
                actual_results.append((table_name, *_failure(runner, exc), ()))

    assert probe_results == [
        (
            table_name,
            "40001",
            "TASK7_V14_PROBE_BOTH_GENERATED_NULL",
        )
        for table_name, _guard_name, _expected_kind in _LEGAL_ATTACH_TARGETS
    ]
    assert actual_results == [
        (
            table_name,
            "ACCEPTED",
            "NONE",
            ((table_name, True, "NATIVE_ROW", expected_kind),),
        )
        for table_name, _guard_name, expected_kind in _LEGAL_ATTACH_TARGETS
    ]
