"""RED contracts for migration-0038's closed legacy-run classification.

Section 7.3 defines five exhaustive partitions.  Classification must finish
under the migration lock before any persistent catalog or row mutation; it may
never infer a strict contract or manufacture strict evidence for legacy runs.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _REPO_ROOT
    / "src"
    / "idis"
    / "persistence"
    / "migrations"
    / "versions"
    / "0038_full_strict_v16_receipts_and_edges.py"
)

_TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
_RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
_OTHER_RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
_LEGACY_SEMANTIC_VERSION = "legacy-step-semantic-v1"
_LEGACY_SEMANTIC_HASH = "4e743bd1264e407e40e48404a6ba081c111b1426bb798b09fb471085b98a18af"

# Literal pre-0038 mode registries.  SNAPSHOT deliberately retains the global
# legacy orders 0..4 and 17..19; it is not renumbered to a contiguous prefix.
_SNAPSHOT_REGISTRY = (
    (0, "DATA_ROOM_INVENTORY_PACKAGE"),
    (1, "DATA_ROOM_INGESTION_HANDOFF"),
    (2, "INGEST_CHECK"),
    (3, "DOCUMENT_PREFLIGHT"),
    (4, "METHODOLOGY_COVERAGE_INIT"),
    (17, "EXTRACT"),
    (18, "GRADE"),
    (19, "CALC"),
)
_FULL_REGISTRY = (
    (0, "DATA_ROOM_INVENTORY_PACKAGE"),
    (1, "DATA_ROOM_INGESTION_HANDOFF"),
    (2, "INGEST_CHECK"),
    (3, "DOCUMENT_PREFLIGHT"),
    (4, "METHODOLOGY_COVERAGE_INIT"),
    (5, "METHODOLOGY_EXTRACTION_TASK_PLANNING"),
    (6, "METHODOLOGY_EXTRACTION_TASK_EXECUTION"),
    (7, "METHODOLOGY_CLAIM_MATERIALIZATION"),
    (8, "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION"),
    (9, "METHODOLOGY_SANAD_CREATION_LINKING_GRADING"),
    (10, "METHODOLOGY_DETERMINISTIC_CALCULATION"),
    (11, "METHODOLOGY_TRUTH_DASHBOARD"),
    (12, "METHODOLOGY_EVIDENCE_TRUST_COURT"),
    (13, "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE"),
    (14, "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN"),
    (15, "METHODOLOGY_COMPANY_IDENTITY_PACKAGE"),
    (16, "METHODOLOGY_LAYER2_READINESS_PACKAGE"),
    (17, "EXTRACT"),
    (18, "GRADE"),
    (19, "CALC"),
    (20, "GRAPH_EVIDENCE"),
    (21, "RAG_EVIDENCE"),
    (22, "ENRICHMENT"),
    (23, "DEBATE"),
    (24, "LAYER2_IC_CHALLENGE"),
    (25, "ANALYSIS"),
    (26, "SCORING"),
    (27, "DELIVERABLES"),
)


def _migration_module() -> ModuleType:
    name = "slice100_task7_migration_0038_legacy_classification"
    spec = importlib.util.spec_from_file_location(name, _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(*, mode: str, status: str) -> dict[str, object]:
    return {
        "tenant_id": _TENANT_ID,
        "run_id": _RUN_ID,
        "mode": mode,
        "status": status,
    }


def _steps(
    registry: Sequence[tuple[int, str]],
    *,
    status: str = "PENDING",
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "tenant_id": _TENANT_ID,
            "run_id": _RUN_ID,
            "step_id": UUID(int=index + 1),
            "step_name": step_name,
            "step_order": step_order,
            "status": status,
        }
        for index, (step_order, step_name) in enumerate(registry)
    )


def _field(value: object, field: str) -> Any:
    if isinstance(value, Mapping):
        return value[field]
    return getattr(value, field)


def _classify(
    module: ModuleType,
    *,
    run: Mapping[str, object],
    steps: Sequence[Mapping[str, object]],
) -> object:
    classifier = getattr(module, "_classify_legacy_run", None)
    assert callable(classifier), "TASK7_LEGACY_CLASSIFIER_MISSING"
    return classifier(run=run, steps=tuple(steps))


def _assert_classification(
    result: object,
    *,
    version: str,
    resume_state: str,
    error_code: str | None,
    source_steps: Sequence[Mapping[str, object]],
) -> None:
    assert _field(result, "pipeline_contract_version") == version
    assert _field(result, "pipeline_resume_state") == resume_state
    assert _field(result, "pipeline_resume_error_code") == error_code
    assert not str(_field(result, "pipeline_contract_version")).startswith("full-strict-")

    classified_steps = tuple(_field(result, "steps"))
    assert len(classified_steps) == len(source_steps)
    for source, classified in zip(source_steps, classified_steps, strict=True):
        assert _field(classified, "step_id") == source["step_id"]
        assert _field(classified, "step_name") == source["step_name"]
        assert _field(classified, "step_order") == source["step_order"]
        assert _field(classified, "semantic_rule_version") == _LEGACY_SEMANTIC_VERSION
        assert _field(classified, "semantic_rule_hash") == _LEGACY_SEMANTIC_HASH


@pytest.mark.parametrize(
    ("mode", "registry", "expected_version"),
    [
        ("SNAPSHOT", _SNAPSHOT_REGISTRY, "snapshot-v1"),
        ("FULL", _FULL_REGISTRY, "full-v1"),
    ],
)
def test_exact_succeeded_legacy_run_is_historical_terminal_and_retains_step_ids(
    mode: str,
    registry: tuple[tuple[int, str], ...],
    expected_version: str,
) -> None:
    module = _migration_module()
    steps = _steps(registry, status="COMPLETED")

    result = _classify(
        module,
        run=_run(mode=mode, status="SUCCEEDED"),
        steps=steps,
    )

    _assert_classification(
        result,
        version=expected_version,
        resume_state="HISTORICAL_TERMINAL",
        error_code=None,
        source_steps=steps,
    )


@pytest.mark.parametrize("run_status", ["FAILED", "CANCELLED"])
def test_exact_complete_failed_or_cancelled_legacy_run_is_historical_terminal(
    run_status: str,
) -> None:
    module = _migration_module()
    steps = _steps(_SNAPSHOT_REGISTRY)

    result = _classify(
        module,
        run=_run(mode="SNAPSHOT", status=run_status),
        steps=steps,
    )

    _assert_classification(
        result,
        version="snapshot-v1",
        resume_state="HISTORICAL_TERMINAL",
        error_code=None,
        source_steps=steps,
    )


@pytest.mark.parametrize("run_status", ["FAILED", "CANCELLED"])
def test_valid_terminal_subset_is_non_resumable_without_invented_steps(
    run_status: str,
) -> None:
    module = _migration_module()
    steps = _steps(_FULL_REGISTRY[:7])

    result = _classify(
        module,
        run=_run(mode="FULL", status=run_status),
        steps=steps,
    )

    _assert_classification(
        result,
        version="full-v1",
        resume_state="NON_RESUMABLE",
        error_code="LEGACY_RUN_RESUME_UNSAFE",
        source_steps=steps,
    )


@pytest.mark.parametrize(
    "steps",
    [
        (),
        _steps(_SNAPSHOT_REGISTRY[:3]),
        _steps(_SNAPSHOT_REGISTRY, status="PENDING"),
    ],
)
def test_every_queued_legacy_shape_is_non_resumable(
    steps: tuple[dict[str, object], ...],
) -> None:
    module = _migration_module()

    result = _classify(
        module,
        run=_run(mode="SNAPSHOT", status="QUEUED"),
        steps=steps,
    )

    _assert_classification(
        result,
        version="snapshot-v1",
        resume_state="NON_RESUMABLE",
        error_code="LEGACY_RUN_RESUME_UNSAFE",
        source_steps=steps,
    )


def _mutated_preflight_case(mutation: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    run = _run(mode="SNAPSHOT", status="FAILED")
    steps = list(_steps(_SNAPSHOT_REGISTRY))
    if mutation == "running_run":
        run["status"] = "RUNNING"
    elif mutation == "unknown_name":
        steps[2]["step_name"] = "UNREVIEWED_STEP"
    elif mutation == "duplicate_order":
        steps[2]["step_order"] = steps[1]["step_order"]
    elif mutation == "negative_order":
        steps[2]["step_order"] = -1
    elif mutation == "name_order_mismatch":
        steps[2]["step_order"] = 3
        steps[3]["step_order"] = 2
    elif mutation == "cross_run":
        steps[2]["run_id"] = _OTHER_RUN_ID
    elif mutation == "auxiliary_wrong_order":
        steps.append(
            {
                "tenant_id": _TENANT_ID,
                "run_id": _RUN_ID,
                "step_id": UUID(int=999),
                "step_name": "RUN_LIFECYCLE",
                "step_order": 27,
                "status": "COMPLETED",
            }
        )
    elif mutation == "succeeded_incomplete_set":
        run["status"] = "SUCCEEDED"
        steps.pop()
    elif mutation == "succeeded_noncompleted_step":
        run["status"] = "SUCCEEDED"
        steps[-1]["status"] = "FAILED"
    elif mutation == "nonrunning_run_with_running_step":
        steps[-1]["status"] = "RUNNING"
    else:  # pragma: no cover - test construction guard
        raise AssertionError(f"unknown mutation: {mutation}")
    return run, steps


@pytest.mark.parametrize(
    "mutation",
    [
        "running_run",
        "unknown_name",
        "duplicate_order",
        "negative_order",
        "name_order_mismatch",
        "cross_run",
        "auxiliary_wrong_order",
        "succeeded_incomplete_set",
        "succeeded_noncompleted_step",
        "nonrunning_run_with_running_step",
    ],
)
def test_incoherent_legacy_state_aborts_classification_before_backfill(
    mutation: str,
) -> None:
    module = _migration_module()
    run, steps = _mutated_preflight_case(mutation)

    with pytest.raises((ValueError, RuntimeError)):
        _classify(module, run=run, steps=steps)


def test_exact_auxiliary_order_is_accepted_but_never_counted_as_canonical() -> None:
    module = _migration_module()
    canonical = list(_steps(_SNAPSHOT_REGISTRY, status="COMPLETED"))
    canonical.append(
        {
            "tenant_id": _TENANT_ID,
            "run_id": _RUN_ID,
            "step_id": UUID(int=999),
            "step_name": "RUN_LIFECYCLE",
            "step_order": 28,
            "status": "COMPLETED",
        }
    )

    result = _classify(
        module,
        run=_run(mode="SNAPSHOT", status="SUCCEEDED"),
        steps=canonical,
    )

    _assert_classification(
        result,
        version="snapshot-v1",
        resume_state="HISTORICAL_TERMINAL",
        error_code=None,
        source_steps=canonical,
    )
    assert _field(result, "canonical_step_count") == 8


def test_upgrade_validates_legacy_runs_before_catalog_or_row_mutation() -> None:
    module = _migration_module()
    source = inspect.getsource(module.upgrade)

    validation = source.index("_validate_legacy_runs_before_ddl")
    altered_columns = source.index("_add_altered_columns_and_backfill")
    reviewed_tables = source.index("_create_reviewed_tables")
    assert validation < altered_columns
    assert validation < reviewed_tables

    backfill_source = inspect.getsource(module._add_altered_columns_and_backfill)
    assert "THEN 'HISTORICAL_TERMINAL' ELSE 'NON_RESUMABLE'" not in backfill_source
    assert "pipeline_contract_version='legacy-opaque-v1'" not in backfill_source
