"""Materialize the bounded, inactive full-strict-v24 reviewed successor.

The only artifact inputs are the exact frozen full-strict-v21 bytes and the
exact frozen FDD formula-policy-v2 bytes.  Rejected v22/v23 artifacts are not
read, imported, or accepted as derivation parents.  The already-reviewed v22
delta implementation is reused only as a source-pinned pure helper operating
on the v21 object supplied here.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Never

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V21_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_POLICY_V2_ARTIFACT = _REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
_POLICY_V2_REVIEWER_MAP = (
    _REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_V22_HELPER = _SCRIPT_DIR / "materialize_full_strict_v22_preimage.py"
_OUTPUT = _REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"

_V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_V21_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
_POLICY_V2_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_POLICY_V2_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_V2_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_POLICY_V2_REVIEWER_MAP_RELATIVE_PATH = (
    "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_POLICY_V2_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"
_V22_POLICY_SHA256 = "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2"
_V22_HELPER_SHA256 = "78add79548bd4ef47f26290f63325f4b0744f659cfcc0f8c47deb30ebc282c29"

_V24 = "full-strict-v24"
_V24_SCHEMA = "full-strict-v24-reviewed-preimage-v1"
_STRUCTURAL_SCHEMA = "task8-v24-structural-admission-v1"
_STRUCTURAL_DOMAIN = "task8-v24-structural-admission-v1"
_CONSERVATION_DOMAIN = "task8-v24-approved-semantics-conservation-v1"
_CONSERVATION_MANIFEST_SHA256 = "635035b2e2a9e88c4cf1a90a1fb4023197f0c0df0bb84e181db2daefddfb36de"
_GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
_GRAPH_PRODUCER_AUTHORITY_DOMAIN = "task8-v24-graph-producer-authority-v1"
_GRAPH_EXTERNAL_INPUT_DOMAIN = "task8-v24-graph-allowed-external-input-v1"
_GRAPH_EXTERNAL_INPUT_TUPLE_SHA256 = (
    "4349d00ff2611a60c695cb4f5b1349eda00041c4b255afed75aab9cf54fee9a4"
)
_POLICY_BINDING_DOMAIN = "task8-v24-policy-binding-v1"
_DASHBOARD_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
_DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
_DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
_OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
_DASHBOARD_ROW_NAME_TUPLE = (
    "methodology-truth-dashboard-row-v2",
    "tenant_id",
    "deal_id",
    "run_id",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "coverage_record_id",
    "methodology_version_record_id",
    "methodology_question_record_id",
    "question_payload_hash",
)
_ACYCLIC_DERIVATION_ORDER = (
    "dashboard_row_id",
    "dashboard_root_link_ids",
    "root_projection_sha256",
    "row_payload_sha256",
    "native_identity_sha256_and_native_output_sha256",
    "output_object_id",
)
_PROJECTION_RECIPE = (
    'H(["methodology-truth-dashboard-root-projection-v2",tenant_id,deal_id,'
    "run_id,dashboard_row_id,coverage_record_id,row_disposition,count,"
    "ORDERED[(root_kind,root_ordinal,root_output_object_id,"
    "root_output_object_kind,dashboard_root_link_id)]])"
)
_ROW_PAYLOAD_RECIPE = [
    "financial-diligence-row-v2",
    "$identity_and_trace_without_self_output",
    "$disposition",
    "$disposition_reason_code",
    "$result_records",
    "$answer_values",
    "$task_gaps",
    "$calculations",
    "$root_projection_sha256",
]
_PROJECTION_V1_PREFIX = (
    'preimage:=\'["methodology-truth-dashboard-root-projection-v1","\'||'
    "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
    "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
    "dashboard_row.run_output_object_id::text||'\",\"'||"
    "dashboard_row.coverage_record_id"
)
_PROJECTION_V2_PREFIX = (
    'preimage:=\'["methodology-truth-dashboard-root-projection-v2","\'||'
    "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
    "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
    "dashboard_row.coverage_record_id"
)
_STRICT_PERIOD_EXPRESSION = "period_start<period_end AND as_of_date XOR (period_start,period_end)"
_SOURCE_NORMALIZATION_EXPRESSION = "normalized_decimal=source_decimal"
_POSITIVE_MAGNITUDE_EXPRESSION = (
    "sign_transform='POSITIVE_MAGNITUDE' => decimal(source_decimal)>=0 AND "
    "decimal(normalized_decimal)>=0"
)
_DASHBOARD_CENSUS_CASES = (
    "COVERAGE_CENSUS_NOT_100",
    "DASHBOARD_ROW_CENSUS_NOT_100",
    "COVERAGE_SET_MISSING",
    "COVERAGE_SET_EXTRA",
)
_APPROVED_DELTA_MANIFEST = (
    (1, "ADD_SEPARATE_FDD_DETERMINISTIC_CALCULATION_SCHEMA", "SEMANTIC_V22"),
    (2, "ADD_FDD_SIGNED_RATIO_FACTUAL_SCHEMA", "SEMANTIC_V22"),
    (3, "BIND_VERSION_AWARE_FDD_SELECTORS", "SEMANTIC_V22"),
    (4, "REPLACE_DASHBOARD_MIXED_RESULT_TRUTH_TABLE", "SEMANTIC_V22"),
    (5, "ADD_PARTIAL_NATIVE_ROOT_AND_ERASURE_REFUSAL", "SEMANTIC_V22"),
    (6, "BIND_EXACT_POLICY_ARTIFACT_OUTPUT_HASH_CONTRACTS", "SEMANTIC_V22"),
    (7, "ADD_ACYCLIC_DASHBOARD_IDENTITY_CONTRACT", "SECOND_GATE_SEMANTIC"),
    (8, "DERIVE_DASHBOARD_SQL_FROM_FROZEN_RECIPES", "MECHANICAL_CLOSURE"),
    (9, "ENFORCE_STRICT_PERIOD_ORDERING", "MECHANICAL_CORRECTION"),
    (10, "ENFORCE_POSITIVE_MAGNITUDE_NONNEGATIVE", "MECHANICAL_CORRECTION"),
    (11, "PRESERVE_NUMERIC_SOURCE_NORMALIZATION", "MECHANICAL_CORRECTION"),
    (12, "BIND_FDD_FORMULA_POLICY_V2", "POLICY_BINDING"),
)
_CONSERVATION_MANIFEST = (
    (
        1,
        "STRICT_PERIOD_ORDERING",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddCalcInputsV1",
            "economic-coordinate-date-order-v1",
        ),
        _STRICT_PERIOD_EXPRESSION,
    ),
    (
        2,
        "POSITIVE_MAGNITUDE_NONNEGATIVE",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddNumericBindingV1",
            "positive-magnitude-source-nonnegative-v1",
        ),
        _POSITIVE_MAGNITUDE_EXPRESSION,
    ),
    (
        3,
        "SOURCE_NORMALIZATION_PRESERVED",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddNumericBindingV1",
            "numeric-binding-source-normalization-v1",
        ),
        _SOURCE_NORMALIZATION_EXPRESSION,
    ),
    (
        4,
        "ERASURE_0039_TO_0038_EXACT_RESTORATION",
        (
            "DERIVED_RESTORATION_TUPLE",
            "task8_fdd_contract",
            "erasure_refusal_contract",
            "downgrade_sql_utf8_hex_tuple",
        ),
        "EXACT_V21_ERASURE_BODY_PLUS_ACL_PLUS_DASHBOARD_REVOKE",
    ),
    (
        5,
        "DASHBOARD_100_OF_100_CENSUS_GUARD",
        ("SQL_AUTHORITY", "dashboard-root-equation-v2"),
        (
            *_DASHBOARD_CENSUS_CASES,
            "WITH dashboard_scopes AS",
            "incomplete_scopes AS",
            "TWO_LITERAL_100_COMPARISONS",
            "FOUR_SET_DIFFERENCE_COMPARISONS",
        ),
    ),
    (
        6,
        "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT",
        ("IDENTITY_CHAIN", "methodology-truth-dashboard-row-v2"),
        _ACYCLIC_DERIVATION_ORDER,
    ),
    (
        7,
        "FDD_FORMULA_POLICY_V2_BINDING",
        ("POLICY_BINDING", "task8_fdd_contract", "policy_binding"),
        (
            "fdd-formula-policy-v2",
            _POLICY_V2_ARTIFACT_SHA256,
            _POLICY_V2_SHA256,
            _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
            _POLICY_V2_REVIEWER_MAP_SHA256,
        ),
    ),
)
_STRUCTURAL_FIELDS = (
    "section_schema",
    "parent_provenance",
    "approved_delta_manifest",
    "approved_semantics_conservation_manifest",
    "approved_semantics_conservation_sha256",
    "identity_hash_dependency_contract",
    "dashboard_sql_recipe_contract",
    "expected_sha256",
)
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_SQL_ROW_DOMAIN = "full-strict-v22-sql-authority-row-v1"
_SQL_REGISTRY_DOMAIN = "full-strict-v22-sql-authority-registry-v1"
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GRAMMAR_OPERATOR_TOKENS = {
    "CJ",
    "H",
    "ORDERED",
    "SCALAR",
    "SHA256",
    "TUPLE",
    "lowercase_hex",
}


class _RecipeGrammarError(ValueError):
    """Internal sentinel for a recipe outside the reviewed closed grammar."""


class _RecipeParser:
    """Fully consuming parser for the reviewed digest-recipe expression grammar."""

    def __init__(self, recipe: str) -> None:
        self._recipe = recipe
        self._position = 0

    def parse(self) -> None:
        self._expression()
        self._whitespace()
        if self._position != len(self._recipe):
            raise _RecipeGrammarError

    def _whitespace(self) -> None:
        while self._position < len(self._recipe) and self._recipe[self._position] in " \t\r\n":
            self._position += 1

    def _peek(self) -> str | None:
        self._whitespace()
        if self._position == len(self._recipe):
            return None
        return self._recipe[self._position]

    def _consume(self, expected: str) -> None:
        self._whitespace()
        if not self._recipe.startswith(expected, self._position):
            raise _RecipeGrammarError
        self._position += len(expected)

    def _identifier(self) -> str:
        self._whitespace()
        match = _IDENTIFIER_RE.match(self._recipe, self._position)
        if match is None:
            raise _RecipeGrammarError
        self._position = match.end()
        return match.group(0)

    def _json_string(self) -> None:
        self._whitespace()
        try:
            value, end = json.JSONDecoder().raw_decode(self._recipe, self._position)
        except json.JSONDecodeError as exc:
            raise _RecipeGrammarError from exc
        if not isinstance(value, str):
            raise _RecipeGrammarError
        self._position = end

    def _sequence(self, opening: str, closing: str, *, allow_empty: bool) -> None:
        self._consume(opening)
        if self._peek() == closing:
            if not allow_empty:
                raise _RecipeGrammarError
            self._consume(closing)
            return
        self._expression()
        while self._peek() == ",":
            self._consume(",")
            if self._peek() == closing:
                raise _RecipeGrammarError
            self._expression()
        self._consume(closing)

    def _unary_call(self) -> None:
        self._consume("(")
        if self._peek() == ")":
            raise _RecipeGrammarError
        self._expression()
        self._consume(")")

    def _lowercase_hex_chain(self) -> None:
        self._consume("(")
        if self._identifier() != "SHA256":
            raise _RecipeGrammarError
        self._consume("(")
        if self._identifier() != "CJ":
            raise _RecipeGrammarError
        self._unary_call()
        self._consume(")")
        self._consume(")")

    def _float8send_chain(self) -> None:
        self._consume("(")
        if self._identifier() != "float8send":
            raise _RecipeGrammarError
        self._consume("(")
        argument = self._identifier()
        if argument in _GRAMMAR_OPERATOR_TOKENS or argument in {"hex", "float8send"}:
            raise _RecipeGrammarError
        self._consume(")")
        self._consume(")")

    def _named_expression(self) -> None:
        name = self._identifier()
        next_token = self._peek()
        if name == "ORDERED":
            self._consume("[")
            if self._peek() == "]":
                raise _RecipeGrammarError
            self._expression()
            self._consume("]")
            return
        if name in {"H", "CJ", "SCALAR"}:
            self._unary_call()
            return
        if name == "TUPLE":
            self._sequence("(", ")", allow_empty=False)
            return
        if name == "lowercase_hex":
            self._lowercase_hex_chain()
            return
        if name == "hex":
            self._float8send_chain()
            return
        if name in {"SHA256", "float8send"} or next_token in {"(", "["}:
            raise _RecipeGrammarError

    def _expression(self) -> None:
        token = self._peek()
        if token is None:
            raise _RecipeGrammarError
        if token == '"':
            self._json_string()
            return
        if token == "[":
            self._sequence("[", "]", allow_empty=True)
            return
        if token == "(":
            self._sequence("(", ")", allow_empty=False)
            return
        if token.isalpha() or token == "_":
            self._named_expression()
            return
        raise _RecipeGrammarError


_GRAPH_PRODUCER_AUTHORITY_TUPLE: tuple[tuple[str, ...], ...] = tuple(
    tuple(row)
    for row in json.loads(
        r"""[
  [
    "fdd_formula_policy:#/digest_recipe_registry/policy_hash_preimage",
    "fdd_formula_policy",
    "CONTRACT_DEFINITION",
    "/digest_recipe_registry/policy_hash_preimage",
    "CONTRACT_DECLARATION",
    "policy_hash_preimage",
    "#"
  ],
  [
    "fdd_formula_policy:#/financial_output_schema/calculation_set_preimage_tuple",
    "fdd_formula_policy",
    "CONTRACT_DEFINITION",
    "/financial_output_schema/calculation_set_preimage_tuple",
    "CONTRACT_DECLARATION",
    "calculation_set_preimage_tuple",
    "#"
  ],
  [
    "fdd_formula_policy:#/financial_output_schema/dashboard_row_uuid_authority/name_preimage",
    "fdd_formula_policy",
    "CONTRACT_DEFINITION",
    "FDD_POLICY_DASHBOARD_ROW_AUTHORITY",
    "POLICY_UUID_AUTHORITY",
    "policy_dashboard_row_id_authority",
    "#"
  ],
  [
    "fdd_formula_policy:#/financial_output_schema/row_payload_preimage",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "row_payload_sha256",
    "#"
  ],
  [
    "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple",
    "fdd_formula_policy",
    "CONTRACT_DEFINITION",
    "/financial_output_schema/row_set_preimage_tuple",
    "CONTRACT_DECLARATION",
    "row_set_preimage_tuple",
    "#"
  ],
  [
    "fdd_formula_policy:#/formula_registry/formula_hash_preimage_field_tuple",
    "fdd_formula_policy",
    "CONTRACT_DEFINITION",
    "/formula_registry/formula_hash_preimage_field_tuple",
    "CONTRACT_DECLARATION",
    "formula_hash_preimage_field_tuple",
    "#"
  ],
  [
    "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
    "fdd_formula_policy",
    "CONTRACT_DEFINITION",
    "/methodology_transition/successor_version_id_preimage",
    "CONTRACT_DECLARATION",
    "successor_version_id_preimage",
    "#"
  ],
  [
    "full_strict:#/applicability_registry/rows/0/hash_preimage",
    "full_strict",
    "CONTRACT_DEFINITION",
    "ALWAYS",
    "APPLICABILITY_HASH",
    "always_sha256",
    "#"
  ],
  [
    "full_strict:#/applicability_registry/rows/1/hash_preimage",
    "full_strict",
    "CONTRACT_DEFINITION",
    "STEP5_APPLICABLE",
    "APPLICABILITY_HASH",
    "step5_applicable_sha256",
    "#"
  ],
  [
    "full_strict:#/applicability_registry/rows/2/hash_preimage",
    "full_strict",
    "CONTRACT_DEFINITION",
    "STEP16_PROVIDER_APPLICABLE",
    "APPLICABILITY_HASH",
    "step16_provider_applicable_sha256",
    "#"
  ],
  [
    "full_strict:#/applicability_registry/rows/3/hash_preimage",
    "full_strict",
    "CONTRACT_DEFINITION",
    "EXTERNAL_CONTEXT_INDEX_ALLOWED",
    "APPLICABILITY_HASH",
    "external_context_index_allowed_sha256",
    "#"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/0",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "H",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/1",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "schema_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/10",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "provider_outcome_native_output_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/11",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "baseline_parent_authority_registry_hash",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/12",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "fdd_formula_policy_v2_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/13",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "task8_fdd_successor_contract_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/14",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "task8_v24_approved_semantics_conservation_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/2",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "usage_path_route_hash",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/3",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "usage_extractor_registry_hash",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/4",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "task7_erasure_schedule_hash",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/5",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "pipeline_contract_hash",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/6",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "identity_identifier_projection_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/7",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "scoring_dimension_projection_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/8",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "root_projection_sha256",
    "#/1"
  ],
  [
    "full_strict:#/digest_recipe_registry/rows/9",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "enrichment_provider_outcome_sha256",
    "#/1"
  ],
  [
    "full_strict:#/future_child_authority_registry/rows/0/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "company_identity_identifiers",
    "FUTURE_CHILD_UUID_AUTHORITY",
    "company_identity_identifiers_uuid",
    "#"
  ],
  [
    "full_strict:#/future_child_authority_registry/rows/1/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "scoring_output_dimensions",
    "FUTURE_CHILD_UUID_AUTHORITY",
    "scoring_output_dimensions_uuid",
    "#"
  ],
  [
    "full_strict:#/future_child_authority_registry/rows/2/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "generic_extraction_claim_links",
    "FUTURE_CHILD_UUID_AUTHORITY",
    "generic_extraction_claim_links_uuid",
    "#"
  ],
  [
    "full_strict:#/future_child_authority_registry/rows/3/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "generic_extraction_evidence_links",
    "FUTURE_CHILD_UUID_AUTHORITY",
    "generic_extraction_evidence_links_uuid",
    "#"
  ],
  [
    "full_strict:#/future_child_authority_registry/rows/4/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "methodology_truth_dashboard_root_links",
    "FUTURE_CHILD_UUID_AUTHORITY",
    "methodology_truth_dashboard_root_links_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/0/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "company_identity_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "company_identity_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/1/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "generic_extraction_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "generic_extraction_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/10/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "scoring_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "scoring_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/11/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "layer2_readiness_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "layer2_readiness_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/12/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "layer2_role_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "layer2_role_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/13/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "deliverable_artifact_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "deliverable_artifact_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/14/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "step_receipt_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "step_receipt_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/2/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "graph_result_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "graph_result_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/3/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "rag_index_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "rag_index_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/4/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "rag_query_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "rag_query_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/5/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "conflict_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "conflict_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/6/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "evidence_trust_court_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "evidence_trust_court_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/7/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "validated_evidence_package_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "validated_evidence_package_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/8/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "debate_role_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "debate_role_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/future_reference_authority_registry/rows/9/uuid_name_tuple_schema",
    "full_strict",
    "CONTRACT_DEFINITION",
    "analysis_agent_input_references",
    "FUTURE_REFERENCE_UUID_AUTHORITY",
    "analysis_agent_input_references_uuid",
    "#"
  ],
  [
    "full_strict:#/task8_fdd_contract/partial_exact_one_contract/dashboard_native_identity_preimage",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "native_identity_sha256",
    "#"
  ],
  [
    "full_strict:#/task8_fdd_contract/partial_exact_one_contract/dashboard_native_output_preimage",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "native_output_sha256",
    "#"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/0",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::step_id",
    "STEP_ID",
    "step_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/1",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::source_lineage_object_id",
    "SOURCE_LINEAGE_OBJECT_ID",
    "source_lineage_object_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/10",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::manifest_reference_id",
    "MANIFEST_REFERENCE_ID",
    "manifest_reference_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/11",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::identity_identifier_id",
    "IDENTITY_IDENTIFIER_ID",
    "identity_identifier_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/12",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::scoring_dimension_id",
    "SCORING_DIMENSION_ID",
    "scoring_dimension_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/13",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::generic_claim_link_id",
    "GENERIC_CLAIM_LINK_ID",
    "generic_claim_link_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/14",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::generic_evidence_link_id",
    "GENERIC_EVIDENCE_LINK_ID",
    "generic_evidence_link_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/15",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "dashboard_root_link_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/16",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::erasure_attempt_id",
    "ERASURE_ATTEMPT_ID",
    "erasure_attempt_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/17",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::erasure_audit_event_id",
    "ERASURE_AUDIT_EVENT_ID",
    "erasure_audit_event_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/18",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "dashboard_row_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/2",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::parse_attempt_id",
    "PARSE_ATTEMPT_ID",
    "parse_attempt_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/3",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::binding_id",
    "BINDING_ID",
    "binding_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/4",
    "full_strict",
    "RUNTIME_VALUE",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "output_object_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/5",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::step_output_id",
    "STEP_OUTPUT_ID",
    "step_output_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/6",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::applicability_decision_id",
    "APPLICABILITY_DECISION_ID",
    "applicability_decision_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/7",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::expected_edge_id",
    "EXPECTED_EDGE_ID",
    "expected_edge_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/8",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::receipt_id",
    "RECEIPT_ID",
    "receipt_id",
    "#/2"
  ],
  [
    "full_strict:#/uuid_namespace_registry/rows/9",
    "full_strict",
    "RUNTIME_VALUE",
    "UUID::future_input_reference_id",
    "FUTURE_INPUT_REFERENCE_ID",
    "future_input_reference_id",
    "#/2"
  ]
]"""
    )
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v24 materialization error."""


class IdentityHashDependencyError(MaterializationError):
    """Stable failure for the whole-artifact identity/hash graph."""


def _fail(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _graph_fail(code: str, detail: str | None = None) -> Never:
    raise IdentityHashDependencyError(code if detail is None else f"{code}:{detail}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_native(value: object) -> Any:
    """Round-trip literal tuples into the JSON-native artifact value domain."""

    return json.loads(_canonical_bytes(value))


def _render(artifact: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(artifact), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")


def _decode(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def validate_frozen_parent_bytes(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != _V21_ARTIFACT_SHA256:
        _fail("TASK8_V24_PARENT_MUST_BE_FROZEN_V21")
    value = _decode(raw, "TASK8_V24_PARENT_MUST_BE_FROZEN_V21")
    if (
        _render(value) != raw
        or value.get("metadata", {}).get("pipeline_contract_version") != "full-strict-v21"
    ):
        _fail("TASK8_V24_PARENT_MUST_BE_FROZEN_V21")
    return value


def _validate_policy_bytes(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != _POLICY_V2_ARTIFACT_SHA256:
        _fail("TASK8_V24_POLICY_MUST_BE_FROZEN_V2")
    value = _decode(raw, "TASK8_V24_POLICY_MUST_BE_FROZEN_V2")
    if (
        _render(value) != raw
        or value.get("metadata", {}).get("policy_id") != "fdd-formula-policy-v2"
        or value.get("digest_recipe_registry", {}).get("fdd_formula_policy_v2_sha256")
        != _POLICY_V2_SHA256
    ):
        _fail("TASK8_V24_POLICY_MUST_BE_FROZEN_V2")
    return value


def _validate_reviewer_map_bytes(raw: bytes) -> bytes:
    if hashlib.sha256(raw).hexdigest() != _POLICY_V2_REVIEWER_MAP_SHA256:
        _fail("TASK8_V24_REVIEWER_MAP_BYTES_DRIFT")
    return raw


def _load_v22_helper() -> ModuleType:
    try:
        raw = _V22_HELPER.read_bytes()
    except OSError as exc:
        raise MaterializationError("TASK8_V24_APPROVED_DELTA_HELPER_MISSING") from exc
    if hashlib.sha256(raw).hexdigest() != _V22_HELPER_SHA256:
        _fail("TASK8_V24_APPROVED_DELTA_HELPER_DRIFT")
    spec = importlib.util.spec_from_file_location("_task8_v24_v22_delta_helper", _V22_HELPER)
    if spec is None or spec.loader is None:
        _fail("TASK8_V24_APPROVED_DELTA_HELPER_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _replace_tree(value: object, replacements: Mapping[str, str]) -> object:
    if isinstance(value, dict):
        return {
            str(_replace_tree(key, replacements)): _replace_tree(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_tree(child, replacements) for child in value]
    if isinstance(value, str):
        result = value
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result
    return value


def _approved_delta_from_v21(v21: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Execute the reviewed six-delta helper on v21, never on v22 bytes."""

    helper = _load_v22_helper()
    policy_alias = copy.deepcopy(dict(policy))
    policy_alias["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"] = _POLICY_V2_SHA256
    fixture_alias = {"fdd_formula_policy_v1_sha256": _POLICY_V2_SHA256}
    artifact = helper._build_v22_core(  # noqa: SLF001 - pinned reviewed pure helper
        copy.deepcopy(dict(v21)),
        policy_alias,
        policy_artifact_sha256=_POLICY_V2_ARTIFACT_SHA256,
        policy_fixture=fixture_alias,
        policy_fixture_sha256=_POLICY_V2_FIXTURE_SHA256,
    )
    if not isinstance(artifact, dict):
        _fail("TASK8_V24_APPROVED_DELTA_HELPER_INVALID")
    return artifact


def _authority(artifact: Mapping[str, Any], authority_id: str) -> Mapping[str, Any]:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == authority_id
    ]
    if len(matches) != 1:
        _fail("TASK8_V24_SQL_AUTHORITY_CARDINALITY_INVALID", authority_id)
    return matches[0]


def _decode_sql(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    try:
        values = row[field]
        if not isinstance(values, list):
            _fail("TASK8_V24_SQL_HEX_INVALID")
        return tuple(bytes.fromhex(value).decode("utf-8") for value in values)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK8_V24_SQL_HEX_INVALID") from exc


def _without_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "expected_sha256"}


def _function_body(statement: str) -> str:
    body = statement.partition(" AS $$")[2].rsplit("$$", 1)[0]
    if not body:
        _fail("TASK8_V24_SQL_FUNCTION_BODY_INVALID")
    return body


def _rehash_acl_row(row: dict[str, Any]) -> None:
    row["expected_sha256"] = hashlib.sha256(
        _ACL_FUNCTION_HASH_DOMAIN + _canonical_bytes(_without_hash(row))
    ).hexdigest()


def _rehash_acl_registry(registry: dict[str, Any]) -> None:
    registry["expected_sha256"] = _canonical_sha256(
        [
            _ACL_REGISTRY_HASH_DOMAIN,
            registry["guard_owner_role"],
            registry["function_rows"],
            registry["current_install_function_signature_tuple"],
            registry["current_column_grant_sql_utf8_hex_tuple"],
            registry["current_column_revoke_sql_utf8_hex_tuple"],
            registry["retained_0037_catalog_projection_tuple"],
            registry["retained_0037_trigger_catalog_projection_tuple"],
            registry["retained_0037_catalog_preflight_sql_utf8_hex"],
            registry["installation_sql_utf8_hex_tuple"],
            registry["downgrade_authority"],
        ]
    )


def _projection_recipe(artifact: Mapping[str, Any]) -> str:
    matches = [
        row[1]
        for row in artifact["digest_recipe_registry"]["rows"]
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    ]
    if len(matches) != 1:
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    return str(matches[0])


def _dashboard_function(artifact: Mapping[str, Any]) -> str:
    statements = _decode_sql(
        _authority(artifact, "dashboard-root-equation-v2"), "body_sql_utf8_hex_tuple"
    )
    matches = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()")
        or statement.startswith(
            "CREATE OR REPLACE FUNCTION public.idis_task8_dashboard_root_equation_v2()"
        )
    ]
    if len(matches) != 1:
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    return matches[0]


def _expected_source_recipe_bindings(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[list[str]]:
    return [
        [
            "full_strict",
            "methodology_truth_dashboard_root_projection_sha256",
            _canonical_sha256(_projection_recipe(artifact)),
        ],
        [
            "fdd_formula_policy",
            "#/financial_output_schema/row_payload_preimage",
            _canonical_sha256(policy["financial_output_schema"]["row_payload_preimage"]),
        ],
    ]


def _policy_binding_sha256(binding: Mapping[str, Any]) -> str:
    ordered = [
        binding.get("policy_id"),
        binding.get("artifact_sha256"),
        binding.get("policy_sha256"),
        binding.get("reviewer_map_relative_path"),
        binding.get("reviewer_map_sha256"),
    ]
    if any(not isinstance(value, str) or not value for value in ordered):
        _fail("TASK8_V24_POLICY_BINDING_INVALID")
    return _canonical_sha256([_POLICY_BINDING_DOMAIN, ordered])


def _expected_dashboard_projection_sql(projection_recipe: object) -> str:
    """Render the complete governed SQL segment from the frozen recipe fields."""

    if not isinstance(projection_recipe, str):
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    match = re.fullmatch(
        r'H\(\["(?P<version>[a-z0-9-]+)",'
        r"(?P<row_fields>[a-z0-9_,]+),"
        r"ORDERED\[\((?P<root_fields>[a-z0-9_,]+)\)\]\]\)",
        projection_recipe,
    )
    if match is None:
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    row_fields = tuple(match.group("row_fields").split(","))
    root_fields = tuple(match.group("root_fields").split(","))
    if row_fields != (
        "tenant_id",
        "deal_id",
        "run_id",
        "dashboard_row_id",
        "coverage_record_id",
        "row_disposition",
        "count",
    ) or root_fields != (
        "root_kind",
        "root_ordinal",
        "root_output_object_id",
        "root_output_object_kind",
        "dashboard_root_link_id",
    ):
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    tenant, deal, run, dashboard_row, coverage, disposition, _count = row_fields
    root_kind, root_ordinal, root_object, root_object_kind, root_link = root_fields
    order_fields = tuple(field for field in root_fields if field != root_object_kind)
    return (
        f'preimage:=\'["{match.group("version")}","\''
        f"||dashboard_row.{tenant}::text||'\",\"'"
        f"||dashboard_row.{deal}::text||'\",\"'"
        f"||dashboard_row.{run}::text||'\",\"'"
        f"||dashboard_row.{dashboard_row}::text||'\",\"'"
        f"||dashboard_row.{coverage}||'\",\"'"
        f"||dashboard_row.{disposition}||'\",'"
        "||link_count::text||',['; first_link:=true; FOR root_link IN SELECT "
        f"{','.join(root_fields)} FROM public.methodology_truth_dashboard_root_links "
        f"WHERE {tenant}=dashboard_row.{tenant} AND {deal}=dashboard_row.{deal} "
        f"AND {run}=dashboard_row.{run} AND {dashboard_row}=dashboard_row.{dashboard_row} "
        f"ORDER BY {','.join(order_fields)} LOOP IF NOT first_link THEN "
        "preimage:=preimage||','; END IF; first_link:=false; "
        f"preimage:=preimage||'[\"'||root_link.{root_kind}||'\",'"
        f"||root_link.{root_ordinal}::text||',\"'"
        f"||root_link.{root_object}::text||'\",\"'"
        f"||root_link.{root_object_kind}||'\",\"'"
        f"||root_link.{root_link}::text||'\"]'; END LOOP; "
        "preimage:=preimage||']]'; "
    )


def validate_dashboard_sql_recipe_derivation(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> None:
    projection = _projection_recipe(artifact)
    row_payload = policy.get("financial_output_schema", {}).get("row_payload_preimage")
    if any(
        forbidden in str(value)
        for forbidden in ("run_output_object_id", "dashboard_output_object_id")
        for value in (projection, row_payload)
    ):
        _fail("TASK8_V24_SUPERSEDED_RECIPE_FIELD")
    if projection != _PROJECTION_RECIPE or row_payload != _ROW_PAYLOAD_RECIPE:
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    statement = _dashboard_function(artifact)
    body = _function_body(statement)
    try:
        projection_sql = body[body.index("preimage:=") : body.index("actual_hash:=")]
    except ValueError as exc:
        raise MaterializationError("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT") from exc
    if any(
        forbidden in projection_sql
        for forbidden in ("run_output_object_id", "dashboard_output_object_id")
    ):
        _fail("TASK8_V24_SUPERSEDED_RECIPE_FIELD")
    expected_projection_sql = _expected_dashboard_projection_sql(projection)
    if projection_sql != expected_projection_sql or "root_projection_sha256" not in body:
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    section = artifact.get("task8_structural_admission")
    if not isinstance(section, Mapping):
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")
    contract = section.get("dashboard_sql_recipe_contract")
    expected = {
        "authority_id": "dashboard-root-equation-v2",
        "projection_recipe_id": "methodology_truth_dashboard_root_projection_sha256",
        "projection_recipe_version": "methodology-truth-dashboard-root-projection-v2",
        "row_payload_policy_pointer": "#/financial_output_schema/row_payload_preimage",
        "row_payload_recipe_version": "financial-diligence-row-v2",
        "forbidden_self_field_tuple": [
            "run_output_object_id",
            "dashboard_output_object_id",
        ],
        "derivation_order_tuple": list(_ACYCLIC_DERIVATION_ORDER),
        "source_recipe_binding_tuple": _expected_source_recipe_bindings(artifact, policy),
        "governed_projection_sql_sha256": hashlib.sha256(
            expected_projection_sql.encode("utf-8")
        ).hexdigest(),
    }
    if contract != expected:
        _fail("TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT")


def _install_projection_v2_sql(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    row = next(
        item for item in section["rows"] if item["authority_id"] == "dashboard-root-equation-v2"
    )
    statements = list(_decode_sql(row, "body_sql_utf8_hex_tuple"))
    function_ordinal = next(
        (
            ordinal
            for ordinal, statement in enumerate(statements)
            if statement.startswith(
                "CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()"
            )
        ),
        -1,
    )
    if function_ordinal < 0 or statements[function_ordinal].count(_PROJECTION_V1_PREFIX) != 1:
        _fail("TASK8_V24_DASHBOARD_SQL_V21_DELTA_SHAPE_INVALID")
    statement = statements[function_ordinal]
    old_body = _function_body(statement)
    try:
        projection_start = statement.index("preimage:=")
        projection_end = statement.index("actual_hash:=", projection_start)
    except ValueError as exc:
        raise MaterializationError("TASK8_V24_DASHBOARD_SQL_V21_DELTA_SHAPE_INVALID") from exc
    statements[function_ordinal] = (
        statement[:projection_start]
        + _expected_dashboard_projection_sql(_PROJECTION_RECIPE)
        + statement[projection_end:]
    )
    new_body = _function_body(statements[function_ordinal])
    old_hash = hashlib.sha256(old_body.encode("utf-8")).hexdigest()
    new_hash = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
    row["body_sql_utf8_hex_tuple"] = [statement.encode("utf-8").hex() for statement in statements]
    row["expected_sha256"] = _canonical_sha256([_SQL_ROW_DOMAIN, _without_hash(row)])

    acl_registry = section["guard_function_acl_registry"]
    acl_matches = [
        item
        for item in acl_registry["function_rows"]
        if item["authority_id"] == "dashboard-root-equation-v2"
    ]
    if len(acl_matches) != 1:
        _fail("TASK8_V24_DASHBOARD_SQL_ACL_CLOSURE_INVALID")
    acl = acl_matches[0]
    replacements = 0
    for field in ("direct_read_column_tuple", "transitive_read_column_tuple"):
        for relation in acl[field]:
            evidence_rows = relation[2]
            for evidence in evidence_rows:
                if evidence == ["SQL_BODY_SHA256", old_hash]:
                    evidence[1] = new_hash
                    replacements += 1
    if replacements == 0:
        _fail("TASK8_V24_DASHBOARD_SQL_ACL_CLOSURE_INVALID")
    _rehash_acl_row(acl)
    _rehash_acl_registry(acl_registry)
    section["expected_sha256"] = _canonical_sha256(
        [_SQL_REGISTRY_DOMAIN, section["rows"], acl_registry]
    )


def _schema_constraint(artifact: Mapping[str, Any], model_name: str, constraint_id: str) -> str:
    matches = [
        row
        for row in artifact["task8_fdd_contract"]["fdd_schema_contracts"]
        if row["schema_id"] == "fdd-deterministic-calculation"
    ]
    if len(matches) != 1:
        _fail("TASK8_V24_FDD_SCHEMA_CONTRACT_INVALID")
    snapshot = matches[0]["normalized_schema_snapshot"]
    target_map = dict(snapshot["targets"])
    locations = [snapshot["$defs"][model_name]]
    if model_name in target_map:
        locations.append(target_map[model_name])
    values: list[str] = []
    for location in locations:
        rows = location.get("x-idis-cross-field-constraint-tuple", [])
        found = [row[1] for row in rows if row[0] == constraint_id]
        if len(found) != 1:
            _fail("TASK8_V24_FDD_SCHEMA_CONSTRAINT_INVALID", constraint_id)
        values.append(found[0])
    if len(set(values)) != 1:
        _fail("TASK8_V24_FDD_SCHEMA_CONSTRAINT_INVALID", constraint_id)
    return values[0]


def _rehash_fdd_schemas(artifact: dict[str, Any], helper: ModuleType) -> None:
    contracts = artifact["task8_fdd_contract"]["fdd_schema_contracts"]
    helper._validate_executable_schema_contracts(tuple(contracts))  # noqa: SLF001
    rows = artifact["schema_registry"]["rows"]
    rows_by_key = {(row["schema_id"], row["schema_version"]): row for row in rows}
    for contract in contracts:
        schema_id = contract["schema_id"]
        schema_version = contract["schema_version"]
        authority_locator = f"task8-literal:{schema_id}:{schema_version}"
        collection_paths = [["$", schema_id, schema_version]]
        expected = _canonical_sha256(
            [
                "typed-schema-definition-v1",
                schema_id,
                schema_version,
                authority_locator,
                contract["normalized_schema_snapshot"],
                collection_paths,
            ]
        )
        contract["expected_schema_sha256"] = expected
        registry_row = rows_by_key[(schema_id, schema_version)]
        registry_row["normalized_schema_snapshot"] = copy.deepcopy(
            contract["normalized_schema_snapshot"]
        )
        registry_row["expected_schema_sha256"] = expected
    artifact["schema_registry"]["expected_sha256"] = _canonical_sha256(
        ["full-strict-v24-schema-registry-v1", rows]
    )


def _expected_erasure_downgrade(v21: Mapping[str, Any]) -> tuple[str, ...]:
    row = v21["erasure_execution_registry"]["rows"][0]
    old_body = _decode_sql(row, "current_body_sql_utf8_hex_tuple")
    old_acl = _decode_sql(row, "current_acl_sql_utf8_hex_tuple")
    if len(old_body) != 1:
        _fail("TASK8_V24_V21_ERASURE_RESTORATION_INVALID")
    body = old_body[0].replace(
        "CREATE FUNCTION public.idis_task7_execute_approved_erasure(",
        "CREATE OR REPLACE FUNCTION public.idis_task7_execute_approved_erasure(",
        1,
    )
    return (
        body,
        *old_acl,
        "REVOKE SELECT (tenant_id,deal_id) ON TABLE "
        "public.methodology_truth_dashboard_rows FROM idis_erasure_function_owner",
    )


def _semantic_drift(code: str) -> Never:
    _fail("TASK8_V24_APPROVED_SEMANTIC_DRIFT", code)


def _row_ordinal(
    rows: Sequence[Mapping[str, Any] | Sequence[Any]],
    *,
    field: int | str,
    value: object,
) -> int:
    matches = [
        ordinal
        for ordinal, row in enumerate(rows)
        if (
            isinstance(field, int)
            and isinstance(row, Sequence)
            and not isinstance(row, (str, bytes))
            and len(row) > field
            and row[field] == value
        )
        or (isinstance(field, str) and isinstance(row, Mapping) and row.get(field) == value)
    ]
    if len(matches) != 1:
        _fail("TASK8_V24_CONSERVATION_SELECTOR_INVALID", str(value))
    return matches[0]


def _conservation_receipt_selectors(
    artifact: Mapping[str, Any],
) -> dict[str, list[list[str]]]:
    contracts = artifact["task8_fdd_contract"]["fdd_schema_contracts"]
    fdd_ordinal = _row_ordinal(
        contracts,
        field="schema_id",
        value="fdd-deterministic-calculation",
    )
    sql_ordinal = _row_ordinal(
        artifact["sql_authority_registry"]["rows"],
        field="authority_id",
        value="dashboard-root-equation-v2",
    )
    uuid_rows = artifact["uuid_namespace_registry"]["rows"]
    digest_rows = artifact["digest_recipe_registry"]["rows"]
    uuid_ordinals = {
        name: _row_ordinal(uuid_rows, field=0, value=name)
        for name in ("dashboard_row_id", "dashboard_root_link_id", "output_object_id")
    }
    projection_ordinal = _row_ordinal(
        digest_rows,
        field=0,
        value="methodology_truth_dashboard_root_projection_sha256",
    )
    snapshot = contracts[fdd_ordinal]["normalized_schema_snapshot"]

    def constraint_selectors(model_name: str, constraint_id: str) -> list[list[str]]:
        definition_rows = snapshot["$defs"][model_name]["x-idis-cross-field-constraint-tuple"]
        definition_ordinal = _row_ordinal(
            definition_rows,
            field=0,
            value=constraint_id,
        )
        base = f"#/task8_fdd_contract/fdd_schema_contracts/{fdd_ordinal}/normalized_schema_snapshot"
        selectors = [
            [
                "full_strict",
                (
                    f"{base}/$defs/{model_name}/"
                    "x-idis-cross-field-constraint-tuple/"
                    f"{definition_ordinal}"
                ),
                "CANONICAL_JSON_SHA256",
            ]
        ]
        target_matches = [
            ordinal
            for ordinal, row in enumerate(snapshot["targets"])
            if isinstance(row, list) and len(row) == 2 and row[0] == model_name
        ]
        if len(target_matches) > 1:
            _fail("TASK8_V24_CONSERVATION_SELECTOR_INVALID", model_name)
        if target_matches:
            target_ordinal = target_matches[0]
            target_rows = snapshot["targets"][target_ordinal][1][
                "x-idis-cross-field-constraint-tuple"
            ]
            target_constraint_ordinal = _row_ordinal(
                target_rows,
                field=0,
                value=constraint_id,
            )
            selectors.append(
                [
                    "full_strict",
                    (
                        f"{base}/targets/{target_ordinal}/1/"
                        "x-idis-cross-field-constraint-tuple/"
                        f"{target_constraint_ordinal}"
                    ),
                    "CANONICAL_JSON_SHA256",
                ]
            )
        return selectors

    return {
        "STRICT_PERIOD_ORDERING": constraint_selectors(
            "FddCalcInputsV1",
            "economic-coordinate-date-order-v1",
        ),
        "POSITIVE_MAGNITUDE_NONNEGATIVE": constraint_selectors(
            "FddNumericBindingV1",
            "positive-magnitude-source-nonnegative-v1",
        ),
        "SOURCE_NORMALIZATION_PRESERVED": constraint_selectors(
            "FddNumericBindingV1",
            "numeric-binding-source-normalization-v1",
        ),
        "ERASURE_0039_TO_0038_EXACT_RESTORATION": [
            [
                "full_strict",
                "#/task8_fdd_contract/erasure_refusal_contract",
                "CANONICAL_JSON_SHA256",
            ]
        ],
        "DASHBOARD_100_OF_100_CENSUS_GUARD": [
            [
                "full_strict",
                f"#/sql_authority_registry/rows/{sql_ordinal}",
                "CANONICAL_JSON_SHA256",
            ]
        ],
        "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT": [
            *[
                [
                    "full_strict",
                    f"#/uuid_namespace_registry/rows/{uuid_ordinals[name]}",
                    "CANONICAL_JSON_SHA256",
                ]
                for name in (
                    "dashboard_row_id",
                    "dashboard_root_link_id",
                    "output_object_id",
                )
            ],
            [
                "full_strict",
                f"#/digest_recipe_registry/rows/{projection_ordinal}",
                "CANONICAL_JSON_SHA256",
            ],
            [
                "full_strict",
                "#/task8_fdd_contract/partial_exact_one_contract",
                "CANONICAL_JSON_SHA256",
            ],
            [
                "full_strict",
                "#/task8_structural_admission/identity_hash_dependency_contract",
                "CANONICAL_JSON_SHA256",
            ],
            [
                "full_strict",
                "#/task8_structural_admission/dashboard_sql_recipe_contract",
                "CANONICAL_JSON_SHA256",
            ],
            [
                "fdd_formula_policy",
                "#/financial_output_schema/dashboard_row_uuid_authority",
                "CANONICAL_JSON_SHA256",
            ],
            [
                "fdd_formula_policy",
                "#/financial_output_schema/row_payload_preimage",
                "CANONICAL_JSON_SHA256",
            ],
        ],
        "FDD_FORMULA_POLICY_V2_BINDING": [
            [
                "full_strict",
                "#/task8_fdd_contract/policy_binding",
                "CANONICAL_JSON_SHA256",
            ],
            ["fdd_formula_policy", "#", "CANONICAL_JSON_SHA256"],
            [
                "reviewer_map",
                _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
                "RAW_FILE_SHA256",
            ],
        ],
    }


def _conservation_receipt_digest(
    *,
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
    source_name: str,
    locator: str,
    digest_kind: str,
) -> str:
    if source_name == "reviewer_map":
        if locator != _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH or digest_kind != "RAW_FILE_SHA256":
            _fail("TASK8_V24_CONSERVATION_SELECTOR_INVALID")
        return hashlib.sha256(reviewer_map_raw).hexdigest()
    if digest_kind != "CANONICAL_JSON_SHA256":
        _fail("TASK8_V24_CONSERVATION_SELECTOR_INVALID")
    document = artifact if source_name == "full_strict" else policy
    if source_name not in {"full_strict", "fdd_formula_policy"}:
        _fail("TASK8_V24_CONSERVATION_SELECTOR_INVALID")
    try:
        value = _relative_pointer_value(document, locator)
    except IdentityHashDependencyError as exc:
        raise MaterializationError("TASK8_V24_CONSERVATION_SELECTOR_INVALID") from exc
    return _canonical_sha256(value)


def _build_conservation_manifest(
    *,
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> list[list[Any]]:
    selectors = _conservation_receipt_selectors(artifact)
    manifest: list[list[Any]] = []
    for ordinal, semantic_id, selector, expected in _json_native(_CONSERVATION_MANIFEST):
        receipts = [
            [
                *receipt,
                _conservation_receipt_digest(
                    artifact=artifact,
                    policy=policy,
                    reviewer_map_raw=reviewer_map_raw,
                    source_name=receipt[0],
                    locator=receipt[1],
                    digest_kind=receipt[2],
                ),
            ]
            for receipt in selectors[semantic_id]
        ]
        manifest.append([ordinal, semantic_id, selector, expected, receipts])
    return manifest


def validate_approved_semantics_conservation(
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    reviewer_map_raw: bytes,
) -> None:
    section = artifact.get("task8_structural_admission")
    if not isinstance(section, Mapping):
        _fail("TASK8_V24_APPROVED_SEMANTIC_MISSING", "STRUCTURAL_ADMISSION")
    manifest = section.get("approved_semantics_conservation_manifest")
    expected_base = _json_native(_CONSERVATION_MANIFEST)
    present_ids = (
        {str(row[1]) for row in manifest if isinstance(row, list) and len(row) >= 2}
        if isinstance(manifest, list)
        else set()
    )
    for _ordinal, semantic_id, _selector, _value in _CONSERVATION_MANIFEST:
        if semantic_id not in present_ids:
            _fail("TASK8_V24_APPROVED_SEMANTIC_MISSING", semantic_id)
    if not isinstance(manifest, list) or len(manifest) != len(expected_base):
        _fail("TASK8_V24_APPROVED_SEMANTIC_MANIFEST_DRIFT")
    rows_by_id = {str(row[1]): row for row in manifest if isinstance(row, list) and len(row) == 5}
    if len(rows_by_id) != len(expected_base) or any(
        rows_by_id.get(str(expected[1]), [])[:4] != expected for expected in expected_base
    ):
        _fail("TASK8_V24_APPROVED_SEMANTIC_MANIFEST_DRIFT")
    manifest_sha256 = _canonical_sha256([_CONSERVATION_DOMAIN, manifest])
    if (
        section.get("approved_semantics_conservation_sha256") != manifest_sha256
        or manifest_sha256 != _CONSERVATION_MANIFEST_SHA256
    ):
        _fail("TASK8_V24_APPROVED_SEMANTIC_MANIFEST_DRIFT")

    if (
        _schema_constraint(artifact, "FddCalcInputsV1", "economic-coordinate-date-order-v1")
        != _STRICT_PERIOD_EXPRESSION
    ):
        _semantic_drift("STRICT_PERIOD_ORDERING")
    if (
        _schema_constraint(
            artifact,
            "FddNumericBindingV1",
            "positive-magnitude-source-nonnegative-v1",
        )
        != _POSITIVE_MAGNITUDE_EXPRESSION
    ):
        _semantic_drift("POSITIVE_MAGNITUDE_NONNEGATIVE")
    if (
        _schema_constraint(
            artifact,
            "FddNumericBindingV1",
            "numeric-binding-source-normalization-v1",
        )
        != _SOURCE_NORMALIZATION_EXPRESSION
    ):
        _semantic_drift("SOURCE_NORMALIZATION_PRESERVED")
    refusal = artifact["task8_fdd_contract"]["erasure_refusal_contract"]
    if _decode_sql(refusal, "downgrade_sql_utf8_hex_tuple") != _expected_erasure_downgrade(
        validate_frozen_parent_bytes(_V21_ARTIFACT.read_bytes())
    ):
        _semantic_drift("ERASURE_0039_TO_0038_EXACT_RESTORATION")
    dashboard = _authority(artifact, "dashboard-root-equation-v2")
    body = _function_body(_dashboard_function(artifact))
    dashboard_valid = (
        set(_DASHBOARD_CENSUS_CASES).issubset(dashboard["illegal_red_case_tuple"])
        and "WITH dashboard_scopes AS" in body
        and "incomplete_scopes AS" in body
        and body.count("<>100") >= 2
        and body.count(" EXCEPT SELECT ") >= 4
    )
    if not dashboard_valid:
        _semantic_drift("DASHBOARD_100_OF_100_CENSUS_GUARD")
    sql_contract = section.get("dashboard_sql_recipe_contract", {})
    if tuple(sql_contract.get("derivation_order_tuple", ())) != _ACYCLIC_DERIVATION_ORDER:
        _semantic_drift("ACYCLIC_DASHBOARD_IDENTITY_CONTRACT")
    binding = artifact["task8_fdd_contract"]["policy_binding"]
    actual_binding = (
        binding.get("policy_id"),
        binding.get("artifact_sha256"),
        binding.get("policy_sha256"),
        binding.get("reviewer_map_relative_path"),
        binding.get("reviewer_map_sha256"),
        binding.get("binding_sha256"),
    )
    if actual_binding != (
        "fdd-formula-policy-v2",
        _POLICY_V2_ARTIFACT_SHA256,
        _POLICY_V2_SHA256,
        _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
        _POLICY_V2_REVIEWER_MAP_SHA256,
        _policy_binding_sha256(binding),
    ) or policy.get("digest_recipe_registry", {}).get("fdd_formula_policy_v2_sha256") != (
        _POLICY_V2_SHA256
    ):
        _semantic_drift("FDD_FORMULA_POLICY_V2_BINDING")

    expected_selectors = _conservation_receipt_selectors(artifact)
    for semantic_id, row in rows_by_id.items():
        receipts = row[4]
        selectors = expected_selectors[semantic_id]
        if (
            not isinstance(receipts, list)
            or len(receipts) != len(selectors)
            or any(
                not isinstance(receipt, list) or len(receipt) != 4 or receipt[:3] != selector
                for receipt, selector in zip(receipts, selectors, strict=True)
            )
        ):
            _fail("TASK8_V24_APPROVED_SEMANTIC_MANIFEST_DRIFT")
        for source_name, locator, digest_kind, expected_sha256 in receipts:
            actual_sha256 = _conservation_receipt_digest(
                artifact=artifact,
                policy=policy,
                reviewer_map_raw=reviewer_map_raw,
                source_name=source_name,
                locator=locator,
                digest_kind=digest_kind,
            )
            if actual_sha256 != expected_sha256:
                _fail("TASK8_V24_APPROVED_SEMANTIC_BYTE_DRIFT", semantic_id)


def _json_pointer(parts: Sequence[str | int]) -> str:
    return "#/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _walk_declarations(
    value: object,
    *,
    artifact_name: str,
    path: tuple[str | int, ...] = (),
) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            lowered = key.lower()
            if "preimage" in lowered or "uuid_name_tuple" in lowered:
                result.add(f"{artifact_name}:{_json_pointer(child_path)}")
            result.update(
                _walk_declarations(
                    child,
                    artifact_name=artifact_name,
                    path=child_path,
                )
            )
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            result.update(
                _walk_declarations(
                    child,
                    artifact_name=artifact_name,
                    path=(*path, ordinal),
                )
            )
    return result


def _discover_sources(full_strict: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, ...]:
    pointers = _walk_declarations(full_strict, artifact_name="full_strict")
    pointers.update(_walk_declarations(policy, artifact_name="fdd_formula_policy"))
    for section_name, width in (
        ("uuid_namespace_registry", 3),
        ("digest_recipe_registry", 2),
    ):
        rows = full_strict.get(section_name, {}).get("rows", [])
        if not isinstance(rows, list):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        for ordinal, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != width:
                _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
            pointers.add(f"full_strict:#/{section_name}/rows/{ordinal}")
    return tuple(sorted(pointers))


def _pointer_value(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> object:
    artifact_name, encoded = pointer.split(":#/", 1)
    value: object = full_strict if artifact_name == "full_strict" else policy
    for raw in encoded.split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    return value


def _relative_pointer_value(value: object, pointer: str) -> object:
    if pointer == "#":
        return value
    if not pointer.startswith("#/"):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    current = value
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]  # type: ignore[index]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise IdentityHashDependencyError(
                "TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID"
            ) from exc
    return current


def _validate_recipe_grammar(producer_row: Sequence[str], value: object) -> None:
    """Reject executable digest forms outside the reviewed closed grammar."""

    # The dependency-payload selector is the artifact-declared grammar class:
    # every reviewed digest recipe selects list item 1, including runtime outputs.
    if producer_row[6] != "#/1":
        return
    recipe = _relative_pointer_value(value, producer_row[6])
    if not isinstance(recipe, str):
        _graph_fail("TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID")
    try:
        _RecipeParser(recipe).parse()
    except _RecipeGrammarError:
        _graph_fail("TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID")


def _producer_node(row: Sequence[str]) -> list[str]:
    return [row[1], row[2], row[3], row[4], row[5], row[0]]


def _recipe_identifiers(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return set(_IDENTIFIER_RE.findall(value)) - _GRAMMAR_OPERATOR_TOKENS


def _node_id(node: Sequence[object]) -> str:
    return _canonical_bytes(list(node[:5])).decode("utf-8")


def _flatten(value: object, path: str = "$") -> tuple[tuple[str, object], ...]:
    if isinstance(value, dict):
        return tuple(
            item for key in sorted(value) for item in _flatten(value[key], f"{path}.{key}")
        )
    if isinstance(value, list):
        return tuple(
            item
            for ordinal, child in enumerate(value)
            for item in _flatten(child, f"{path}[{ordinal}]")
        )
    return ((path, value),)


def _leaf_authority(token: object) -> str:
    rendered = str(token)
    if rendered == "pipeline_contract_hash":
        return "PINNED_CONTRACT_INPUT"
    if rendered == "pipeline_contract_version":
        return "VERSIONED_CONTRACT_INPUT"
    if rendered in {"root_output_object_id", "root_output_object_kind"}:
        return "FOREIGN_ROOT_INPUT"
    if rendered.startswith("$"):
        return "BOUND_RUNTIME_INPUT"
    if token is None or isinstance(token, (bool, int)):
        return "CONTRACT_LITERAL"
    return "SCALAR_OR_FOREIGN_INPUT"


def _cycle(adjacency: Mapping[str, set[str]]) -> tuple[str, ...] | None:
    complete: set[str] = set()
    active: list[str] = []
    positions: dict[str, int] = {}

    def visit(node: str) -> tuple[str, ...] | None:
        if node in positions:
            start = positions[node]
            return (*active[start:], node)
        if node in complete:
            return None
        positions[node] = len(active)
        active.append(node)
        for dependency in sorted(adjacency.get(node, set())):
            result = visit(dependency)
            if result is not None:
                return result
        active.pop()
        positions.pop(node)
        complete.add(node)
        return None

    for node in sorted(adjacency):
        result = visit(node)
        if result is not None:
            return result
    return None


def _derive_allowed_external_input_tuple(
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
    producer_rows: Sequence[Sequence[str]],
) -> list[list[str]]:
    producer_symbols = {row[5] for row in producer_rows}
    rows: set[tuple[str, str, str, str]] = set()
    for row in producer_rows:
        pointer = row[0]
        value = _pointer_value(pointer, full_strict_artifact, policy_artifact)
        _validate_recipe_grammar(row, value)
        payload = _relative_pointer_value(value, row[6])
        for token_path, scalar in _flatten(payload):
            for identifier in _recipe_identifiers(scalar):
                if identifier not in producer_symbols:
                    rows.add(
                        (
                            pointer,
                            token_path,
                            identifier,
                            _leaf_authority(identifier),
                        )
                    )
    return sorted((list(row) for row in rows), key=_canonical_bytes)


def _build_identity_hash_dependency_contract(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    producer_rows = [list(row) for row in _GRAPH_PRODUCER_AUTHORITY_TUPLE]
    external_rows = _derive_allowed_external_input_tuple(
        full_strict_artifact=artifact,
        policy_artifact=policy,
        producer_rows=producer_rows,
    )
    external_sha256 = _canonical_sha256([_GRAPH_EXTERNAL_INPUT_DOMAIN, external_rows])
    if external_sha256 != _GRAPH_EXTERNAL_INPUT_TUPLE_SHA256:
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_DRIFT")
    return {
        "schema_version": _GRAPH_SCHEMA,
        "source_discovery": "WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS",
        "recipe_grammar_tuple": ["H", "CJ", "ORDERED", "TUPLE", "SCALAR"],
        "producer_authority_schema": [
            "source_pointer",
            "artifact_name",
            "layer",
            "scope",
            "output_kind",
            "symbol",
            "dependency_payload_pointer",
        ],
        "producer_authority_tuple": producer_rows,
        "producer_authority_sha256": _canonical_sha256(
            [_GRAPH_PRODUCER_AUTHORITY_DOMAIN, producer_rows]
        ),
        "allowed_external_input_schema": [
            "source_pointer",
            "token_path",
            "symbol",
            "authority",
        ],
        "allowed_external_input_tuple": external_rows,
        "allowed_external_input_sha256": external_sha256,
        "dashboard_special_case_allowed": False,
        "synthetic_symbol_special_case_allowed": False,
        "v23_non_dashboard_cycle_disposition": "SYNTHETIC_GUARD_BYPASS_MUTATION_ONLY",
    }


def _graph_contract(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    section = artifact.get("task8_structural_admission")
    contract = (
        section.get("identity_hash_dependency_contract") if isinstance(section, Mapping) else None
    )
    if not isinstance(contract, Mapping) or contract.get("schema_version") != _GRAPH_SCHEMA:
        _graph_fail("TASK8_IDENTITY_HASH_AUTHORITY_MANIFEST_MISSING")
    return contract


def derive_identity_hash_dependency_graph(
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the general graph without domain-specific dependency branches."""

    pointers = _discover_sources(full_strict_artifact, policy_artifact)
    contract = _graph_contract(full_strict_artifact)
    raw_producer_rows = contract.get("producer_authority_tuple")
    if not isinstance(raw_producer_rows, list):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    producer_rows: list[list[str]] = []
    for row in raw_producer_rows:
        if (
            not isinstance(row, list)
            or len(row) != 7
            or any(not isinstance(value, str) or not value for value in row)
        ):
            _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
        producer_rows.append(row)
    manifest_pointers = [row[0] for row in producer_rows]
    if len(set(manifest_pointers)) != len(manifest_pointers):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    symbols = [row[5] for row in producer_rows]
    if len(set(symbols)) != len(symbols):
        _graph_fail("TASK8_IDENTITY_HASH_DUPLICATE_PRODUCER")
    if tuple(sorted(manifest_pointers)) != pointers:
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_CENSUS_DRIFT")
    producer_manifest = [list(row) for row in _GRAPH_PRODUCER_AUTHORITY_TUPLE]
    if producer_rows != producer_manifest or contract.get(
        "producer_authority_sha256"
    ) != _canonical_sha256([_GRAPH_PRODUCER_AUTHORITY_DOMAIN, producer_rows]):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_DRIFT")

    raw_external_rows = contract.get("allowed_external_input_tuple")
    if not isinstance(raw_external_rows, list):
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_INVALID")
    external_rows: list[tuple[str, str, str, str]] = []
    for row in raw_external_rows:
        if (
            not isinstance(row, list)
            or len(row) != 4
            or any(not isinstance(value, str) or not value for value in row)
        ):
            _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_INVALID")
        external_rows.append((row[0], row[1], row[2], row[3]))
    external_sha256 = _canonical_sha256(
        [_GRAPH_EXTERNAL_INPUT_DOMAIN, [list(row) for row in external_rows]]
    )
    if (
        len(set(external_rows)) != len(external_rows)
        or contract.get("allowed_external_input_sha256") != external_sha256
        or external_sha256 != _GRAPH_EXTERNAL_INPUT_TUPLE_SHA256
    ):
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_DRIFT")
    allowed_external = set(external_rows)

    nodes = [_producer_node(row) for row in producer_rows]
    values: dict[str, object] = {}
    rows_by_pointer = {row[0]: row for row in producer_rows}
    for pointer in pointers:
        value = _pointer_value(pointer, full_strict_artifact, policy_artifact)
        _validate_recipe_grammar(rows_by_pointer[pointer], value)
        values[pointer] = value

    by_id: dict[str, list[str]] = {}
    producers: dict[str, list[str]] = {}
    for node in nodes:
        node_id = _node_id(node)
        if node_id in by_id:
            _graph_fail("TASK8_IDENTITY_HASH_CONFLICTING_PRODUCER", node[4])
        by_id[node_id] = node
        producers[node[4]] = node

    edges: set[tuple[str, str, str]] = set()
    leaves: set[tuple[str, str, str, str, str]] = set()
    consumed_external: set[tuple[str, str, str, str]] = set()
    for producer_row, node in zip(producer_rows, nodes, strict=True):
        derived_id = _node_id(node)
        pointer = node[5]
        payload = _relative_pointer_value(values[pointer], producer_row[6])
        for token_path, scalar in _flatten(payload):
            identifiers = _recipe_identifiers(scalar)
            if not identifiers:
                leaves.add(
                    (
                        derived_id,
                        token_path,
                        str(scalar),
                        _leaf_authority(scalar),
                        pointer,
                    )
                )
                continue
            for identifier in sorted(identifiers):
                receipt = f"{pointer}:{token_path}#{identifier}"
                dependency = producers.get(identifier)
                if dependency is not None:
                    dependency_id = _node_id(dependency)
                    if dependency_id == derived_id:
                        _graph_fail("TASK8_IDENTITY_HASH_CYCLE", node[4])
                    edges.add((derived_id, dependency_id, receipt))
                    continue
                external = (
                    pointer,
                    token_path,
                    identifier,
                    _leaf_authority(identifier),
                )
                if external not in allowed_external:
                    _graph_fail(
                        "TASK8_IDENTITY_HASH_GOVERNED_REFERENCE_UNRESOLVED",
                        identifier,
                    )
                consumed_external.add(external)
                leaves.add(
                    (
                        derived_id,
                        f"{token_path}#{identifier}",
                        identifier,
                        external[3],
                        pointer,
                    )
                )

    if consumed_external != allowed_external:
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_ORPHANED")

    adjacency = {node_id: set() for node_id in by_id}
    for derived, dependency, _receipt in edges:
        adjacency[derived].add(dependency)
    cycle = _cycle(adjacency)
    if cycle is not None:
        _graph_fail("TASK8_IDENTITY_HASH_CYCLE", "->".join(cycle))

    node_tuple = sorted(nodes, key=_canonical_bytes)
    edge_tuple = sorted((list(row) for row in edges), key=_canonical_bytes)
    leaf_tuple = sorted((list(row) for row in leaves), key=_canonical_bytes)
    source_pointer_tuple = list(pointers)
    graph = {
        "schema_version": _GRAPH_SCHEMA,
        "node_tuple": node_tuple,
        "edge_tuple": edge_tuple,
        "leaf_tuple": leaf_tuple,
        "source_pointer_tuple": source_pointer_tuple,
        "counts": {
            "node_count": len(node_tuple),
            "edge_count": len(edge_tuple),
            "leaf_count": len(leaf_tuple),
            "source_pointer_count": len(source_pointer_tuple),
        },
    }
    graph["graph_sha256"] = _canonical_sha256(
        [
            _GRAPH_SCHEMA,
            node_tuple,
            edge_tuple,
            leaf_tuple,
            source_pointer_tuple,
        ]
    )
    return graph


def _uuid5(namespace: str, value: object) -> str:
    name = hashlib.sha256(_canonical_bytes(value)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), name))


def derive_dashboard_identity_vector(**inputs: Any) -> dict[str, object]:
    tenant_id = str(inputs["tenant_id"])
    deal_id = str(inputs["deal_id"])
    run_id = str(inputs["run_id"])
    pipeline_version = str(inputs["pipeline_contract_version"])
    pipeline_hash = str(inputs["pipeline_contract_hash"])
    coverage_id = str(inputs["coverage_record_id"])
    row_id = _uuid5(
        _DASHBOARD_ROW_NAMESPACE,
        [
            "methodology-truth-dashboard-row-v2",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            coverage_id,
            inputs["methodology_version_record_id"],
            inputs["methodology_question_record_id"],
            inputs["question_payload_hash"],
        ],
    )
    links: list[list[object]] = []
    for root in sorted(
        inputs["roots"],
        key=lambda item: (
            str(item["root_kind"]),
            int(item["root_ordinal"]),
            str(item["root_output_object_id"]),
        ),
    ):
        link_id = _uuid5(
            _DASHBOARD_ROOT_LINK_NAMESPACE,
            [
                "methodology-truth-dashboard-root-link-v1",
                tenant_id,
                deal_id,
                run_id,
                row_id,
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
            ],
        )
        links.append(
            [
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
                root["root_output_object_kind"],
                link_id,
            ]
        )
    projection = _canonical_sha256(
        [
            "methodology-truth-dashboard-root-projection-v2",
            tenant_id,
            deal_id,
            run_id,
            row_id,
            coverage_id,
            inputs["row_disposition"],
            len(links),
            links,
        ]
    )
    row_payload = _canonical_sha256(
        [
            "financial-diligence-row-v2",
            [*inputs["row_trace_prefix"], coverage_id, row_id],
            inputs["row_disposition"],
            inputs["disposition_reason_code"],
            inputs["result_records"],
            inputs["answer_values"],
            inputs["task_gaps"],
            inputs["calculations"],
            projection,
        ]
    )
    native_identity = _canonical_sha256(
        ["native-identity-v1", tenant_id, deal_id, run_id, _DASHBOARD_KIND, [row_id]]
    )
    native_output = _canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            _DASHBOARD_KIND,
            [row_id],
            [coverage_id, projection, inputs["row_disposition"], row_payload],
        ]
    )
    output_object_id = _uuid5(
        _OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            "NATIVE_ROW",
            _DASHBOARD_KIND,
            native_identity,
            native_output,
        ],
    )
    return {
        "dashboard_row_id": row_id,
        "root_link_tuple": links,
        "root_projection_sha256": projection,
        "row_payload_sha256": row_payload,
        "native_identity_sha256": native_identity,
        "native_output_sha256": native_output,
        "output_object_id": output_object_id,
    }


def _retarget_to_v24(
    artifact: dict[str, Any],
    policy: Mapping[str, Any],
    v21: Mapping[str, Any],
) -> dict[str, Any]:
    replaced = _replace_tree(
        artifact,
        {
            "full-strict-v22": _V24,
            "fdd-formula-policy-v1": "fdd-formula-policy-v2",
            "fdd_formula_policy_v1": "fdd_formula_policy_v2",
            _V22_POLICY_SHA256: _POLICY_V2_SHA256,
        },
    )
    if not isinstance(replaced, dict):
        _fail("TASK8_V24_APPROVED_DELTA_INVALID")
    artifact = replaced

    metadata = artifact["metadata"]
    try:
        design_value = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK8_V24_REVIEWED_DESIGN_INVALID") from exc
    if not isinstance(design_value, list):
        _fail("TASK8_V24_REVIEWED_DESIGN_INVALID")
    design_value[0] = "full-strict-v24-reviewed-design-literal-v1"
    design_value[1] = ["pipeline_contract_version", _V24]
    design_value.append(
        [
            "v24_parent_provenance",
            [
                "parent_pipeline_contract_version",
                "full-strict-v21",
                "parent_artifact_sha256",
                _V21_ARTIFACT_SHA256,
                "parent_pipeline_contract_hash",
                _V21_CONTRACT_SHA256,
            ],
        ]
    )
    design_value.append(
        ["v24_approved_delta_manifest", [list(row) for row in _APPROVED_DELTA_MANIFEST]]
    )
    rendered_design = _canonical_bytes(design_value)
    metadata["artifact_schema"] = _V24_SCHEMA
    metadata["pipeline_contract_version"] = _V24
    metadata["append_frozen"] = True
    metadata["activation_status"] = "INACTIVE"
    metadata["reviewed_design_sha256"] = hashlib.sha256(rendered_design).hexdigest()
    metadata["reviewed_design_utf8_hex"] = rendered_design.hex()
    for name in ("task8_fdd_contract", "task8_structural_admission"):
        if name not in metadata["section_order"]:
            metadata["section_order"].append(name)

    rows = artifact["digest_recipe_registry"]["rows"]
    by_name = {row[0]: row for row in rows}
    by_name["pipeline_contract_hash"][1] = (
        '["pipeline-contract-v24","full-strict-v24",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    by_name["methodology_truth_dashboard_root_projection_sha256"][1] = _PROJECTION_RECIPE
    policy_row = by_name.get("fdd_formula_policy_v2_sha256")
    if policy_row is None:
        _fail("TASK8_V24_POLICY_DIGEST_AUTHORITY_INVALID")
    policy_row[1] = 'H(["fdd-formula-policy-v2",ORDERED[component_hashes]])'
    by_name["task8_fdd_successor_contract_sha256"][1] = (
        'H(["full-strict-v24-task8-fdd-contract-v2",contract])'
    )
    rows.append(
        [
            "task8_v24_approved_semantics_conservation_sha256",
            'H(["task8-v24-approved-semantics-conservation-v1",manifest])',
        ]
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()

    uuid_rows = artifact["uuid_namespace_registry"]["rows"]
    if any(row[0] == "dashboard_row_id" for row in uuid_rows):
        _fail("TASK8_V24_DASHBOARD_ROW_UUID_AUTHORITY_INVALID")
    uuid_rows.append(
        ["dashboard_row_id", _DASHBOARD_ROW_NAMESPACE, list(_DASHBOARD_ROW_NAME_TUPLE)]
    )
    artifact["uuid_namespace_registry"]["expected_sha256"] = _canonical_sha256(
        ["task7-uuid-namespace-registry-v1", uuid_rows]
    )

    binding = artifact["task8_fdd_contract"]["policy_binding"]
    binding.update(
        {
            "policy_id": "fdd-formula-policy-v2",
            "artifact_relative_path": "contracts/fdd_formula_policy_v2.json",
            "artifact_sha256": _POLICY_V2_ARTIFACT_SHA256,
            "policy_sha256": _POLICY_V2_SHA256,
            "fixture_relative_path": (
                "tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json"
            ),
            "fixture_sha256": _POLICY_V2_FIXTURE_SHA256,
            "fixture_policy_sha256": _POLICY_V2_SHA256,
            "reviewer_map_relative_path": _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
            "reviewer_map_sha256": _POLICY_V2_REVIEWER_MAP_SHA256,
        }
    )
    binding["binding_sha256"] = _policy_binding_sha256(binding)
    artifact["task8_fdd_contract"]["activation_assertions"][
        "migration_consumable_before_second_review"
    ] = False

    tuple_rows = artifact["tuple_schema_registry"]["rows"]
    tuple_rows.append(["Task8V24StructuralAdmissionV1", list(_STRUCTURAL_FIELDS)])
    return artifact


def _structural_section(
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "section_schema": _STRUCTURAL_SCHEMA,
        "parent_provenance": {
            "parent_pipeline_contract_version": "full-strict-v21",
            "parent_artifact_sha256": _V21_ARTIFACT_SHA256,
            "parent_pipeline_contract_hash": _V21_CONTRACT_SHA256,
            "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
        },
        "approved_delta_manifest": [list(row) for row in _APPROVED_DELTA_MANIFEST],
        "identity_hash_dependency_contract": _build_identity_hash_dependency_contract(
            artifact, policy
        ),
        "dashboard_sql_recipe_contract": {
            "authority_id": "dashboard-root-equation-v2",
            "projection_recipe_id": "methodology_truth_dashboard_root_projection_sha256",
            "projection_recipe_version": "methodology-truth-dashboard-root-projection-v2",
            "row_payload_policy_pointer": "#/financial_output_schema/row_payload_preimage",
            "row_payload_recipe_version": "financial-diligence-row-v2",
            "forbidden_self_field_tuple": [
                "run_output_object_id",
                "dashboard_output_object_id",
            ],
            "derivation_order_tuple": list(_ACYCLIC_DERIVATION_ORDER),
            "source_recipe_binding_tuple": _expected_source_recipe_bindings(artifact, policy),
            "governed_projection_sql_sha256": hashlib.sha256(
                _expected_dashboard_projection_sql(_PROJECTION_RECIPE).encode("utf-8")
            ).hexdigest(),
        },
    }
    artifact_view = dict(artifact)
    artifact_view["task8_structural_admission"] = section
    conservation = _build_conservation_manifest(
        artifact=artifact_view,
        policy=policy,
        reviewer_map_raw=reviewer_map_raw,
    )
    section["approved_semantics_conservation_manifest"] = conservation
    section["approved_semantics_conservation_sha256"] = _canonical_sha256(
        [_CONSERVATION_DOMAIN, conservation]
    )
    if section["approved_semantics_conservation_sha256"] != _CONSERVATION_MANIFEST_SHA256:
        _fail("TASK8_V24_APPROVED_SEMANTIC_MANIFEST_DRIFT")
    section["expected_sha256"] = _canonical_sha256([_STRUCTURAL_DOMAIN, copy.deepcopy(section)])
    return section


def _rehash_task8_section(artifact: dict[str, Any]) -> None:
    section = artifact["task8_fdd_contract"]
    section["expected_sha256"] = _canonical_sha256(
        [
            "full-strict-v24-task8-fdd-contract-v2",
            {key: value for key, value in section.items() if key != "expected_sha256"},
        ]
    )


def _assert_invariance(v21: Mapping[str, Any], v24: Mapping[str, Any]) -> None:
    for section in (
        "semantic_step_registry",
        "edge_rule_registry",
        "usage_route_registry",
        "usage_extractor_registry",
        "provider_registry",
        "rights_index_policy_registry",
        "erasure_baseline",
        "erasure_materialized",
    ):
        if v24[section] != v21[section]:
            _fail("TASK8_V24_INVARIANCE_DRIFT", section)
    expected_counts = (28, 48, 1_756, 19, 18)
    actual_counts = (
        len(v24["semantic_step_registry"]["rows"]),
        len(v24["edge_rule_registry"]["rows"]),
        len(v24["usage_route_registry"]["rows"]),
        len(v24["usage_extractor_registry"]["rows"]),
        len(v24["provider_registry"]["rows"]),
    )
    if actual_counts != expected_counts:
        _fail("TASK8_V24_INVARIANCE_DRIFT", "COUNTS")


def build_v24_from_parent_raw(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    v21 = validate_frozen_parent_bytes(parent_artifact_raw)
    policy = _validate_policy_bytes(policy_artifact_raw)
    reviewer_map_raw = _validate_reviewer_map_bytes(reviewer_map_raw)
    helper = _load_v22_helper()
    artifact = _approved_delta_from_v21(v21, policy)
    artifact = _retarget_to_v24(artifact, policy, v21)
    _install_projection_v2_sql(artifact)
    _rehash_fdd_schemas(artifact, helper)
    _rehash_task8_section(artifact)
    artifact["task8_structural_admission"] = _structural_section(
        artifact,
        policy,
        reviewer_map_raw=reviewer_map_raw,
    )
    _assert_invariance(v21, artifact)
    validate_dashboard_sql_recipe_derivation(artifact, policy)
    validate_approved_semantics_conservation(
        artifact,
        policy,
        reviewer_map_raw=reviewer_map_raw,
    )
    derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    return artifact


def validate_frozen_artifact(
    artifact_raw: bytes,
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    artifact = _decode(artifact_raw, "TASK8_V24_CONTRACT_ARTIFACT_INVALID")
    if _render(artifact) != artifact_raw:
        _fail("TASK8_V24_CONTRACT_ARTIFACT_INVALID")
    expected = build_v24_from_parent_raw(
        parent_artifact_raw=parent_artifact_raw,
        policy_artifact_raw=policy_artifact_raw,
        reviewer_map_raw=reviewer_map_raw,
    )
    if artifact != expected:
        _fail("TASK8_V24_EXACT_DIFF_INVALID")
    return artifact


def frozen_bytes(candidate: Mapping[str, Any]) -> bytes:
    if (
        candidate.get("metadata", {}).get("pipeline_contract_version") != _V24
        or candidate.get("metadata", {}).get("append_frozen") is not True
        or candidate.get("metadata", {}).get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V24_CONTRACT_ARTIFACT_INVALID")
    return _render(candidate)


def _read_exact(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise MaterializationError(code) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_OUTPUT)
    args = parser.parse_args(argv)
    try:
        candidate = build_v24_from_parent_raw(
            parent_artifact_raw=_read_exact(_V21_ARTIFACT, "TASK8_V24_PARENT_MUST_BE_FROZEN_V21"),
            policy_artifact_raw=_read_exact(
                _POLICY_V2_ARTIFACT, "TASK8_V24_POLICY_MUST_BE_FROZEN_V2"
            ),
            reviewer_map_raw=_read_exact(
                _POLICY_V2_REVIEWER_MAP,
                "TASK8_V24_REVIEWER_MAP_BYTES_DRIFT",
            ),
        )
        output = frozen_bytes(candidate)
        if args.output.exists():
            if args.output.read_bytes() != output:
                _fail("TASK8_V24_NO_CLOBBER")
        else:
            args.output.write_bytes(output)
    except (MaterializationError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "activation_status": "INACTIVE",
                "artifact_sha256": hashlib.sha256(output).hexdigest(),
                "parent_artifact_sha256": _V21_ARTIFACT_SHA256,
                "pipeline_contract_version": _V24,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "IdentityHashDependencyError",
    "MaterializationError",
    "build_v24_from_parent_raw",
    "derive_dashboard_identity_vector",
    "derive_identity_hash_dependency_graph",
    "frozen_bytes",
    "main",
    "validate_approved_semantics_conservation",
    "validate_dashboard_sql_recipe_derivation",
    "validate_frozen_artifact",
    "validate_frozen_parent_bytes",
]
