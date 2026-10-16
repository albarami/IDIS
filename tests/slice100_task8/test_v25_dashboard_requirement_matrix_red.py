"""RED-first v3 dashboard requirement admission and SQL derivation matrix."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

import pytest

from . import _v25_oracle as oracle


def _policy_requirement(
    ordinal: int = 1,
    formula_type: str = "GROSS_MARGIN",
    scope: str = "line_item",
    *,
    required: bool = True,
) -> dict[str, object]:
    return {
        "formula_ordinal": ordinal,
        "formula_type": formula_type,
        "required": required,
        "scope": scope,
    }


def _legacy_requirement(
    calc_type: str = "GROSS_MARGIN", *, required: bool = True
) -> dict[str, object]:
    return {"calc_type": calc_type, "required": required}


def _materializer() -> Any:
    return oracle.load_materializer()


def _assert_rejected(
    *,
    methodology_version_id: str,
    joined_methodology_version_id: str | None = None,
    requirements: object,
    calculations: tuple[str, ...] = (),
) -> None:
    joined = joined_methodology_version_id or methodology_version_id
    with pytest.raises(oracle.V25OracleError, match=oracle.V25_STABLE_CODE):
        oracle.admit_requirement_vector(
            methodology_version_id=methodology_version_id,
            joined_methodology_version_id=joined,
            required_calculations=requirements,
            calculation_types=calculations,
        )
    materializer = _materializer()
    with pytest.raises(
        materializer.MaterializationError,
        match=oracle.V25_STABLE_CODE,
    ):
        materializer.validate_dashboard_requirement_vector(
            methodology_version_id=methodology_version_id,
            joined_methodology_version_id=joined,
            required_calculations=requirements,
            calculation_types=calculations,
        )


@pytest.mark.parametrize(
    ("case_id", "methodology_version_id", "requirements", "calculations"),
    (
        (
            "LEGACY_FOUR_FIELD",
            "financial_dd:legacy",
            [_policy_requirement()],
            ("GROSS_MARGIN",),
        ),
        (
            "POLICY_V2_TWO_FIELD",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [_legacy_requirement()],
            ("GROSS_MARGIN",),
        ),
        (
            "UNKNOWN_FORMULA_TYPE",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [_policy_requirement(formula_type="UNKNOWN")],
            ("UNKNOWN",),
        ),
        (
            "ORDINAL_DRIFT",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [_policy_requirement(ordinal=2)],
            ("GROSS_MARGIN",),
        ),
        (
            "SCOPE_DRIFT",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [_policy_requirement(scope="sheet_control")],
            ("GROSS_MARGIN",),
        ),
        (
            "UNDECLARED_FDD_CALCULATION_ROOT",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [],
            ("GROSS_MARGIN",),
        ),
        (
            "MIXED_SHAPES",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [_policy_requirement(), _legacy_requirement()],
            ("GROSS_MARGIN",),
        ),
        (
            "POLICY_V2_EXTRA_FIELD",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [{**_policy_requirement(), "calc_type": "GROSS_MARGIN"}],
            ("GROSS_MARGIN",),
        ),
        (
            "POLICY_V2_MISSING_FIELD",
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [{key: value for key, value in _policy_requirement().items() if key != "scope"}],
            ("GROSS_MARGIN",),
        ),
        (
            "LEGACY_EXTRA_FIELD",
            "financial_dd:legacy",
            [{**_legacy_requirement(), "scope": "line_item"}],
            ("GROSS_MARGIN",),
        ),
        (
            "LEGACY_MISSING_FIELD",
            "financial_dd:legacy",
            [{"calc_type": "GROSS_MARGIN"}],
            ("GROSS_MARGIN",),
        ),
    ),
)
def test_v25_negative_requirement_matrix_rejects_before_dashboard_derivation(
    case_id: str,
    methodology_version_id: str,
    requirements: object,
    calculations: tuple[str, ...],
) -> None:
    del case_id
    _assert_rejected(
        methodology_version_id=methodology_version_id,
        requirements=requirements,
        calculations=calculations,
    )


def test_v25_cross_version_join_rejects_before_dashboard_derivation() -> None:
    _assert_rejected(
        methodology_version_id=oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
        joined_methodology_version_id="financial_dd:legacy",
        requirements=[_policy_requirement()],
        calculations=("GROSS_MARGIN",),
    )


def test_v25_duplicate_policy_tuple_rejects_before_dashboard_derivation() -> None:
    duplicated = [_policy_requirement(), _policy_requirement()]
    _assert_rejected(
        methodology_version_id=oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
        requirements=duplicated,
        calculations=("GROSS_MARGIN",),
    )
    artifact, _materializer_instance = oracle.build_candidate()
    body = oracle.dashboard_function_body(artifact)
    contract = artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    uniqueness_guard = (
        "GROUP BY item->>'formula_ordinal',item->>'formula_type',item->>'scope' HAVING count(*)<>1"
    )
    oracle.require(
        "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE" in contract["negative_case_tuple"],
        "TASK8_V25_DUPLICATE_REQUIREMENT_CASE_MISSING",
    )
    oracle.require_private_contains(
        uniqueness_guard,
        body,
        "TASK8_V25_DUPLICATE_REQUIREMENT_SQL_GUARD_MISSING",
    )
    oracle.require(
        body.index(uniqueness_guard) < body.index("preimage:="),
        "TASK8_V25_DUPLICATE_REQUIREMENT_GUARD_TOO_LATE",
    )


def test_v25_policy_empty_and_singleton_subsets_remain_legal() -> None:
    materializer = _materializer()
    vectors: tuple[
        tuple[list[dict[str, object]], tuple[str, ...]],
        ...,
    ] = (
        ([], ()),
        ([_policy_requirement()], ("GROSS_MARGIN",)),
    )
    for requirements, calculations in vectors:
        oracle.admit_requirement_vector(
            methodology_version_id=oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            joined_methodology_version_id=oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            required_calculations=requirements,
            calculation_types=calculations,
        )
        materializer.validate_dashboard_requirement_vector(
            methodology_version_id=oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            joined_methodology_version_id=oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            required_calculations=requirements,
            calculation_types=calculations,
        )


def test_v25_legal_legacy_and_policy_v2_vectors_are_admitted() -> None:
    vectors = (
        (
            "financial_dd:legacy",
            [_legacy_requirement("BURN_RATE")],
            ("BURN_RATE",),
        ),
        (
            oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
            [
                _policy_requirement(ordinal=ordinal, formula_type=formula_type, scope=scope)
                for ordinal, formula_type, scope in oracle.EXPECTED_POLICY_REQUIREMENT_TUPLE
            ],
            tuple(row[1] for row in oracle.EXPECTED_POLICY_REQUIREMENT_TUPLE),
        ),
    )
    materializer = _materializer()
    for version, requirements, calculations in vectors:
        oracle.admit_requirement_vector(
            methodology_version_id=version,
            joined_methodology_version_id=version,
            required_calculations=requirements,
            calculation_types=calculations,
        )
        materializer.validate_dashboard_requirement_vector(
            methodology_version_id=version,
            joined_methodology_version_id=version,
            required_calculations=requirements,
            calculation_types=calculations,
        )


def test_v25_sql_is_derived_from_literal_contract_and_guards_before_projection() -> None:
    artifact, _loaded = oracle.build_candidate()
    policy = oracle.load_policy_v2()
    materializer = _materializer()
    body = oracle.dashboard_function_body(artifact)
    contract = artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    expected_guard = materializer.render_dashboard_requirement_guard(contract)
    oracle.require(
        body.count(expected_guard) == 1,
        "TASK8_V25_REQUIREMENT_GUARD_CARDINALITY_INVALID",
    )
    oracle.require(
        body.index(expected_guard) < body.index("preimage:="),
        "TASK8_V25_REQUIREMENT_GUARD_TOO_LATE",
    )
    oracle.require_private_equal(
        contract["policy_v2_requirement_literal_tuple"],
        [list(row) for row in oracle.EXPECTED_POLICY_REQUIREMENT_TUPLE],
        "TASK8_V25_REQUIREMENT_LITERAL_TUPLE_INVALID",
    )
    oracle.require(
        body.count("item->>'formula_type'=c.calc_type") == 3,
        "TASK8_V25_FORMULA_TYPE_COMPARISON_COUNT_INVALID",
    )
    oracle.require(
        body.count("item->>'calc_type'=c.calc_type") == 3,
        "TASK8_V25_LEGACY_TYPE_COMPARISON_COUNT_INVALID",
    )
    oracle.require(
        body.count("c.calc_type") == 6,
        "TASK8_V25_CALCULATION_TYPE_READ_COUNT_INVALID",
    )
    oracle.require_private_contains(
        "item->>'formula_ordinal'=approved.formula_ordinal::text",
        body,
        "TASK8_V25_FORMULA_ORDINAL_PREDICATE_MISSING",
    )
    oracle.require_private_not_contains(
        "to_jsonb(approved.formula_ordinal)",
        body,
        "TASK8_V25_FORMULA_ORDINAL_COERCION_FORBIDDEN",
    )
    oracle.require(
        body.count("q.methodology_version_id=c.methodology_version_id") == 2,
        "TASK8_V25_CROSS_VERSION_JOIN_COUNT_INVALID",
    )
    oracle.require_private_contains(
        "dashboard_row.methodology_version_id IS DISTINCT FROM ",
        body,
        "TASK8_V25_VERSION_DISCRIMINATOR_MISSING",
    )
    oracle.require_private_contains(
        oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
        body,
        "TASK8_V25_POLICY_VERSION_LITERAL_MISSING",
    )
    # The exact discriminator legitimately embeds the policy hash.  It may
    # appear only as part of that frozen methodology-version literal, never as
    # an independently discovered runtime policy authority.
    oracle.require(
        body.count(oracle.POLICY_V2_SHA256) == body.count(oracle.POLICY_V2_METHODOLOGY_VERSION_ID),
        "TASK8_V25_POLICY_HASH_LITERAL_COUNT_INVALID",
    )
    oracle.require_private_not_contains(
        "fdd_formula_policy_v2.json",
        body,
        "TASK8_V25_RUNTIME_POLICY_READ_FORBIDDEN",
    )
    oracle.require_private_not_contains(
        "COALESCE",
        body,
        "TASK8_V25_SQL_COALESCE_FORBIDDEN",
    )
    oracle.require_private_not_contains(
        "formula_type' AS calc_type",
        body,
        "TASK8_V25_TYPE_ALIAS_FORBIDDEN",
    )
    materializer.validate_dashboard_requirement_contract(artifact, policy)


def test_v25_calculation_column_reads_and_other_calc_type_bodies_are_invariant() -> None:
    v24 = oracle.load_v24()
    v25, _materializer_instance = oracle.build_candidate()
    v24_rows = v24["sql_authority_registry"]["rows"]
    v25_rows = v25["sql_authority_registry"]["rows"]

    def containing(rows: list[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        for row in rows:
            statements = oracle.decoded_sql(row)
            if any("calc_type" in statement for statement in statements):
                result[str(row["authority_id"])] = statements
        return result

    before = containing(v24_rows)
    after = containing(v25_rows)
    oracle.require_private_equal(
        sorted(before),
        sorted(
            {
                "dashboard-root-equation-v2",
                "current-output-exact-one-v1",
                "dashboard-partial-native-root-exact-one-v1",
            }
        ),
        "TASK8_V25_BASE_CALC_TYPE_AUTHORITY_SET_INVALID",
    )
    oracle.require_private_equal(
        sorted(after),
        sorted(
            {
                "dashboard-root-equation-v3",
                "current-output-exact-one-v1",
                "dashboard-partial-native-root-exact-one-v1",
            }
        ),
        "TASK8_V25_CALC_TYPE_AUTHORITY_SET_INVALID",
    )
    for authority_id in (
        "current-output-exact-one-v1",
        "dashboard-partial-native-root-exact-one-v1",
    ):
        oracle.require_private_equal(
            after[authority_id],
            before[authority_id],
            "TASK8_V25_CALC_TYPE_BODY_DRIFT",
        )

    expected_statement_hashes = {
        "dashboard-root-equation-v2": (
            "010a7b178b0c67609e4441b7517a8987c37f41b584b0b55960ae0363ce018b47"
        ),
        "current-output-exact-one-v1": (
            "27e4b72e010e7f181f1cfa27828e0680c99647f8a76ec537c4b293e98656d373"
        ),
        "dashboard-partial-native-root-exact-one-v1": (
            "2f8bed4c8d6d581579bec8b256b6623b706144ccdffd410c2ff412172e0cb4e0"
        ),
    }
    for authority_id, expected_hash in expected_statement_hashes.items():
        statements = before[authority_id]
        matches = [statement for statement in statements if "calc_type" in statement]
        oracle.require(
            len(matches) == 1,
            "TASK8_V25_CALC_TYPE_STATEMENT_CARDINALITY_INVALID",
        )
        oracle.require(
            hashlib.sha256(matches[0].encode("utf-8")).hexdigest() == expected_hash,
            "TASK8_V25_CALC_TYPE_STATEMENT_HASH_DRIFT",
        )

    downgrade = v24["task8_fdd_contract"]["partial_exact_one_contract"][
        "downgrade_sql_utf8_hex_tuple"
    ]
    downgrade_matches = [
        bytes.fromhex(value).decode("utf-8")
        for value in downgrade
        if "calc_type" in bytes.fromhex(value).decode("utf-8")
    ]
    oracle.require(
        len(downgrade_matches) == 1,
        "TASK8_V25_DOWNGRADE_CALC_TYPE_CARDINALITY_INVALID",
    )
    oracle.require(
        hashlib.sha256(downgrade_matches[0].encode("utf-8")).hexdigest()
        == "36fafe80ba3c9168bd7e0da666838a00527a85562cb8e8ea0289e22f225eaf4d",
        "TASK8_V25_DOWNGRADE_CALC_TYPE_HASH_DRIFT",
    )

    dashboard = oracle.dashboard_function_body(v25)
    oracle.require(
        dashboard.count("c.calc_type") == 6,
        "TASK8_V25_CALCULATION_TYPE_READ_COUNT_INVALID",
    )

    def calc_acl_statements(artifact: Mapping[str, Any]) -> list[str]:
        registry = artifact["sql_authority_registry"]["guard_function_acl_registry"]
        return [
            statement
            for value in (
                *registry["current_column_grant_sql_utf8_hex_tuple"],
                *registry["current_column_revoke_sql_utf8_hex_tuple"],
                *registry["installation_sql_utf8_hex_tuple"],
                *registry["downgrade_authority"]["column_select_revoke_sql_utf8_hex_tuple"],
                *registry["downgrade_authority"]["ordered_sql_utf8_hex_tuple"],
                *registry["guard_owner_role"]["acl_sql_utf8_hex_tuple"],
            )
            if "calc_type" in (statement := bytes.fromhex(value).decode("utf-8"))
        ]

    observed_calc_acl = calc_acl_statements(v25)
    oracle.require_private_equal(
        observed_calc_acl,
        calc_acl_statements(v24),
        "TASK8_V25_CALC_TYPE_ACL_DRIFT",
    )
    oracle.require(
        len(observed_calc_acl) == 8,
        "TASK8_V25_CALC_TYPE_ACL_COUNT_INVALID",
    )
    oracle.require(
        all("deterministic_calculations" in statement for statement in observed_calc_acl),
        "TASK8_V25_CALC_TYPE_ACL_RELATION_INVALID",
    )


def test_v25_acl_adds_only_question_payload_to_guard_owner_select_closure() -> None:
    v24 = oracle.load_v24()
    v25, _materializer_instance = oracle.build_candidate()
    old = v24["sql_authority_registry"]["guard_function_acl_registry"]
    new = v25["sql_authority_registry"]["guard_function_acl_registry"]
    oracle.require_private_equal(
        old["guard_owner_role"]["select_relation_tuple"],
        new["guard_owner_role"]["select_relation_tuple"],
        "TASK8_V25_GUARD_OWNER_RELATION_TUPLE_DRIFT",
    )
    old_columns = dict(old["guard_owner_role"]["select_relation_column_tuple"])
    new_columns = dict(new["guard_owner_role"]["select_relation_column_tuple"])
    oracle.require_private_equal(
        sorted(old_columns),
        sorted(new_columns),
        "TASK8_V25_GUARD_OWNER_RELATION_SET_DRIFT",
    )
    for relation in old_columns:
        if relation == "methodology_questions":
            oracle.require_private_equal(
                new_columns[relation],
                sorted([*old_columns[relation], "question_payload"]),
                "TASK8_V25_QUESTION_PAYLOAD_ACL_CLOSURE_INVALID",
            )
        else:
            oracle.require_private_equal(
                new_columns[relation],
                old_columns[relation],
                "TASK8_V25_UNRELATED_OWNER_COLUMN_ACL_DRIFT",
            )
    oracle.require(
        "methodology_version_id" in new_columns["methodology_questions"],
        "TASK8_V25_METHODOLOGY_VERSION_ACL_MISSING",
    )

    direct = next(
        row for row in new["function_rows"] if row["authority_id"] == "dashboard-root-equation-v3"
    )["direct_read_column_tuple"]
    question_columns = next(
        columns for relation, columns, _evidence in direct if relation == "methodology_questions"
    )
    oracle.require_private_equal(
        question_columns,
        sorted(question_columns),
        "TASK8_V25_DIRECT_QUESTION_COLUMNS_NOT_SORTED",
    )
    oracle.require(
        "question_payload" in question_columns,
        "TASK8_V25_DIRECT_QUESTION_PAYLOAD_READ_MISSING",
    )
    oracle.require(
        "methodology_version_id" in question_columns,
        "TASK8_V25_DIRECT_METHODOLOGY_VERSION_READ_MISSING",
    )


def test_v25_contract_rejects_literal_or_sql_drift_with_stable_generation_codes() -> None:
    artifact, _materializer_instance = oracle.build_candidate()
    policy = oracle.load_policy_v2()
    materializer = _materializer()
    mutations: list[tuple[str, Any]] = []

    wrong_literal = copy.deepcopy(artifact)
    wrong_literal["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"][
        "policy_v2_requirement_literal_tuple"
    ][0][2] = "sheet_control"
    mutations.append(("TASK8_V25_REQUIREMENT_LITERAL_DRIFT", wrong_literal))

    runtime_read = copy.deepcopy(artifact)
    runtime_read["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"][
        "runtime_policy_read_allowed"
    ] = True
    mutations.append(("TASK8_V25_RUNTIME_POLICY_DISCOVERY_FORBIDDEN", runtime_read))

    for code, candidate in mutations:
        with pytest.raises(materializer.MaterializationError, match=code):
            materializer.validate_dashboard_requirement_contract(candidate, policy)
