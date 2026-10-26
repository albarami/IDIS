"""Immutable v8 locator nodes executing the current full-strict-v9 SQL.

The historical path and node names are hash-bound by the reviewed artifact.
Each node delegates to the pre-fixture v9 harness, which executes the current
v9 candidate/frozen SQL over base schema revision 0037.
"""

from __future__ import annotations

from tests.slice100_task7._v9_current_guard_census_support import (
    assert_authority_case,
)


def test_current_source_exact_one_v1_accepts_legal() -> None:
    """Exercise all v9 source-exact-one targets through their legal path."""

    assert_authority_case("current-source-exact-one-v1", "LEGAL")


def test_current_source_exact_one_v1_rejects_illegal() -> None:
    """Exercise all v9 source-exact-one targets through their illegal path."""

    assert_authority_case("current-source-exact-one-v1", "ILLEGAL")


def test_current_source_exact_one_v1_rejects_direct_sql() -> None:
    """Exercise all v9 source-exact-one targets through direct SQL."""

    assert_authority_case("current-source-exact-one-v1", "DIRECT_SQL")


def test_current_output_exact_one_v1_accepts_legal() -> None:
    """Exercise all v9 output-exact-one targets through their legal path."""

    assert_authority_case("current-output-exact-one-v1", "LEGAL")


def test_current_output_exact_one_v1_rejects_illegal() -> None:
    """Exercise all v9 output-exact-one targets through their illegal path."""

    assert_authority_case("current-output-exact-one-v1", "ILLEGAL")


def test_current_output_exact_one_v1_rejects_direct_sql() -> None:
    """Exercise all v9 output-exact-one targets through direct SQL."""

    assert_authority_case("current-output-exact-one-v1", "DIRECT_SQL")


def test_current_receipt_equality_v1_accepts_legal() -> None:
    """Exercise all v9 receipt-equality targets through their legal path."""

    assert_authority_case("current-receipt-equality-v1", "LEGAL")


def test_current_receipt_equality_v1_rejects_illegal() -> None:
    """Exercise all v9 receipt-equality targets through their illegal path."""

    assert_authority_case("current-receipt-equality-v1", "ILLEGAL")


def test_current_receipt_equality_v1_rejects_direct_sql() -> None:
    """Exercise all v9 receipt-equality targets through direct SQL."""

    assert_authority_case("current-receipt-equality-v1", "DIRECT_SQL")


def test_current_input_binding_admission_v1_accepts_legal() -> None:
    """Exercise the v9 input-binding admission target through its legal path."""

    assert_authority_case("current-input-binding-admission-v1", "LEGAL")


def test_current_input_binding_admission_v1_rejects_illegal() -> None:
    """Exercise the v9 input-binding admission target through its illegal path."""

    assert_authority_case("current-input-binding-admission-v1", "ILLEGAL")


def test_current_input_binding_admission_v1_rejects_direct_sql() -> None:
    """Exercise the v9 input-binding admission target through direct SQL."""

    assert_authority_case(
        "current-input-binding-admission-v1",
        "DIRECT_SQL",
    )


def test_current_append_only_lease_v1_accepts_legal() -> None:
    """Exercise all v9 append-only/lease targets through their legal path."""

    assert_authority_case("current-append-only-lease-v1", "LEGAL")


def test_current_append_only_lease_v1_rejects_illegal() -> None:
    """Exercise all v9 append-only/lease targets through their illegal path."""

    assert_authority_case("current-append-only-lease-v1", "ILLEGAL")


def test_current_append_only_lease_v1_rejects_direct_sql() -> None:
    """Exercise all v9 append-only/lease targets through direct SQL."""

    assert_authority_case("current-append-only-lease-v1", "DIRECT_SQL")


def test_current_source_bound_v1_accepts_legal() -> None:
    """Exercise all v9 source-bound targets through their legal path."""

    assert_authority_case("current-source-bound-v1", "LEGAL")


def test_current_source_bound_v1_rejects_illegal() -> None:
    """Exercise all v9 source-bound targets through their illegal path."""

    assert_authority_case("current-source-bound-v1", "ILLEGAL")


def test_current_source_bound_v1_rejects_direct_sql() -> None:
    """Exercise all v9 source-bound targets through direct SQL."""

    assert_authority_case("current-source-bound-v1", "DIRECT_SQL")


def test_current_output_bound_v1_accepts_legal() -> None:
    """Exercise all v9 output-bound targets through their legal path."""

    assert_authority_case("current-output-bound-v1", "LEGAL")


def test_current_output_bound_v1_rejects_illegal() -> None:
    """Exercise all v9 output-bound targets through their illegal path."""

    assert_authority_case("current-output-bound-v1", "ILLEGAL")


def test_current_output_bound_v1_rejects_direct_sql() -> None:
    """Exercise all v9 output-bound targets through direct SQL."""

    assert_authority_case("current-output-bound-v1", "DIRECT_SQL")


def test_current_runs_contract_immutable_v1_accepts_legal() -> None:
    """Exercise the v9 run-contract target through its legal path."""

    assert_authority_case("current-runs-contract-immutable-v1", "LEGAL")


def test_current_runs_contract_immutable_v1_rejects_illegal() -> None:
    """Exercise the v9 run-contract target through its illegal path."""

    assert_authority_case("current-runs-contract-immutable-v1", "ILLEGAL")


def test_current_runs_contract_immutable_v1_rejects_direct_sql() -> None:
    """Exercise the v9 run-contract target through direct SQL."""

    assert_authority_case(
        "current-runs-contract-immutable-v1",
        "DIRECT_SQL",
    )


def test_current_run_steps_contract_immutable_v1_accepts_legal() -> None:
    """Exercise the v9 run-step-contract target through its legal path."""

    assert_authority_case(
        "current-run-steps-contract-immutable-v1",
        "LEGAL",
    )


def test_current_run_steps_contract_immutable_v1_rejects_illegal() -> None:
    """Exercise the v9 run-step-contract target through its illegal path."""

    assert_authority_case(
        "current-run-steps-contract-immutable-v1",
        "ILLEGAL",
    )


def test_current_run_steps_contract_immutable_v1_rejects_direct_sql() -> None:
    """Exercise the v9 run-step-contract target through direct SQL."""

    assert_authority_case(
        "current-run-steps-contract-immutable-v1",
        "DIRECT_SQL",
    )


def test_current_task5_coverage_replacement_v1_accepts_legal() -> None:
    """Exercise the v9 Task-5 replacement through its legal path."""

    assert_authority_case("current-task5-coverage-replacement-v1", "LEGAL")


def test_current_task5_coverage_replacement_v1_rejects_illegal() -> None:
    """Exercise the v9 Task-5 replacement through its illegal path."""

    assert_authority_case(
        "current-task5-coverage-replacement-v1",
        "ILLEGAL",
    )


def test_current_task5_coverage_replacement_v1_rejects_direct_sql() -> None:
    """Exercise the v9 Task-5 replacement through direct SQL."""

    assert_authority_case(
        "current-task5-coverage-replacement-v1",
        "DIRECT_SQL",
    )


def test_current_task6_immutable_replacement_v1_accepts_legal() -> None:
    """Exercise all v9 Task-6 replacements through their legal path."""

    assert_authority_case("current-task6-immutable-replacement-v1", "LEGAL")


def test_current_task6_immutable_replacement_v1_rejects_illegal() -> None:
    """Exercise all v9 Task-6 replacements through their illegal path."""

    assert_authority_case(
        "current-task6-immutable-replacement-v1",
        "ILLEGAL",
    )


def test_current_task6_immutable_replacement_v1_rejects_direct_sql() -> None:
    """Exercise all v9 Task-6 replacements through direct SQL."""

    assert_authority_case(
        "current-task6-immutable-replacement-v1",
        "DIRECT_SQL",
    )
