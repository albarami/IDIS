"""Independent literals and helpers for the full-strict-v27 RED suites.

The oracle owns the count-domain distinction.  It never imports the v27
materializer at module import time and never treats a rejected v22-v26
artifact as a derivation parent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from . import _task8_void_contract_hash_evidence as void_evidence

REPO_ROOT = Path(__file__).resolve().parents[2]
V21_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY_V2_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
POLICY_V2_REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
V27_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v27_preimage.py"
V27_STAGED_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v27-candidate"
    / "full_strict_v27_reviewed_preimage_v1.json"
)
V27_CANONICAL_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v27_reviewed_preimage_v1.json"
V27_CANONICAL_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v27_contract_hash_v1.json"
)

V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
V21_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
POLICY_V2_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_V2_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
POLICY_V2_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"

V27_VERSION = "full-strict-v27"
V27_SCHEMA = "full-strict-v27-reviewed-preimage-v1"
V27_STRUCTURAL_SCHEMA = "task8-v27-structural-admission-v1"
V27_INVENTORY_DOMAIN = "task8-v27-post-0038-plus-scaffold-public-ordinary-table-inventory-v1"
V26_HISTORICAL_INVENTORY_DOMAIN = (
    "task8-v26-post-0038-plus-scaffold-public-ordinary-table-inventory-v1"
)
V27_PHYSICAL_CATALOG_DOMAIN = (
    "task8-v27-post-0038-plus-scaffold-public-ordinary-table-physical-catalog-v1"
)
V27_INVENTORY_SHA256 = "e06330cdf5eabf5e1f95da4e66537de60476460ff9af6c0ebd7fd920a5ae6c39"
V26_HISTORICAL_INVENTORY_SHA256 = "d614152392965a2a124a5e3ceba2029f8965928ff5884f05347ec7c61e44de24"
V27_PHYSICAL_CATALOG_SHA256 = "85f9d8162c0241f88cf560e83689ce1ea7baff6c0de9a6cdddda512ac4df27f6"

DASHBOARD_RELATION_TUPLE = (
    "methodology_truth_dashboard_root_links",
    "methodology_truth_dashboard_rows",
)
SUCCESSOR_APPLICATION_RELATION_TUPLE = (
    "audit_events",
    "break_glass_grants",
    "byok_policies",
    "calc_sanads",
    "claims",
    "data_room_package_files",
    "data_room_packages",
    "deal_assignments",
    "deals",
    "debate_sessions",
    "defects",
    "deliverable_manifest_references",
    "deliverables",
    "deterministic_calculations",
    "document_artifacts",
    "document_parse_attempts",
    "document_spans",
    "documents",
    "enrichment_credentials",
    "enrichment_rights_approvals",
    "erasure_requests",
    "evidence_items",
    "evidence_trust_findings",
    "group_memberships",
    "groups",
    "human_gate_actions",
    "human_gates",
    "idempotency_records",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "legal_holds",
    "methodology_coverage_records",
    "methodology_extraction_attempts",
    "methodology_extraction_results",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_questions",
    "methodology_result_claim_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_factual_outputs",
    "methodology_result_sanad_links",
    "methodology_truth_dashboard_root_links",
    "methodology_truth_dashboard_rows",
    "methodology_versions",
    "muhasabah_records",
    "overrides",
    "provider_budget_policies",
    "provider_budget_usage",
    "run_applicability_decisions",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "run_input_bindings",
    "run_methodology_selections",
    "run_output_objects",
    "run_step_outputs",
    "run_steps",
    "runs",
    "sanads",
    "source_lineage_objects",
    "strict_preflight_proofs",
    "tenants",
    "validated_evidence_packages",
    "vector_embeddings",
    "webhook_delivery_attempts",
    "webhooks",
)
POST_0038_APPLICATION_RELATION_TUPLE = tuple(
    relation
    for relation in SUCCESSOR_APPLICATION_RELATION_TUPLE
    if relation not in DASHBOARD_RELATION_TUPLE
)
PHYSICAL_PUBLIC_ORDINARY_RELATION_TUPLE = tuple(
    sorted((*SUCCESSOR_APPLICATION_RELATION_TUPLE, "alembic_version"))
)
EXCLUDED_APPLICATION_RELATION_TUPLE = ("alembic_version",)

EXPECTED_POLICY_REQUIREMENT_TUPLE = (
    (1, "GROSS_MARGIN", "line_item"),
    (2, "FDD_EBITDA_BRIDGE", "p_and_l_control"),
    (3, "FDD_EBIT_BRIDGE", "p_and_l_control"),
    (4, "FDD_TAX_RECONCILIATION", "p_and_l_control"),
    (5, "FDD_NET_INCOME_BRIDGE", "p_and_l_control"),
    (6, "FDD_WORKING_CAPITAL_MOVEMENT", "cash_flow_control"),
    (7, "FDD_FREE_CASH_FLOW_CASH_BRIDGE", "cash_flow_control"),
    (8, "FDD_DEBT_ROLL_FORWARD", "debt_control"),
    (9, "FDD_ASSET_TIE_OUT", "sheet_control"),
    (10, "FDD_LIABILITY_TIE_OUT", "sheet_control"),
)
V27_REQUIREMENT_LITERAL_SHA256 = "6e4d37a54c719ca21ba1c4dfd3d90e4804acc5d1e3d7b540791325aecc8d0a43"
V27_ARTIFACT_SHA256 = "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
V27_ARTIFACT_SIZE_BYTES = 7_076_204
V27_CANDIDATE_CONTRACT_SHA256 = "5221e622a40c44b4f2fe38e53e0208a7e1dba5358278d75624a0cc1c40ef0985"
V27_TASK8_CONTRACT_SHA256 = "95ebd92b1c8c9602932df89dc3f9b3ec5221f3d1c0f7f5814f82da5ac10d570b"
V27_STRUCTURAL_SHA256 = "ca32f92b07e0e2d059f6a9c6a08166a8ae4ef2fa88a137e685ae2b0b7e0c4ae4"
V27_GRAPH_PRODUCER_SHA256 = "eb485dbe9cd7bdfa235dfcfff0d24e763b5db15c76942465c4c434a4530410c4"
V27_GRAPH_EXTERNAL_SHA256 = "f1995db4b6e5a6a2f34de4d47c522b1d82025ab4c165f4af62a31b47e2ae7164"
V27_GRAPH_SHA256 = "a951b96575f5c2927c59185f20378fde6ce019c207b77510a1033f4947649e3c"
V27_CONSERVATION_SHA256 = "32a06ca41841c196ae76ad3d33b3d15da4dec8a247343b9a629390da8128475d"
V27_POLICY_BINDING_SHA256 = "9e41ea12908a5c37542f6156d4bbf22dc19d59ac172d096a84fc55c4db83d2ff"
V27_REVIEWED_DESIGN_SHA256 = "94ad58d5199b1473be5f920114153bc2724102165552f3ec9e78f595c8d9fe4f"
EQUATION_V3_ROW_SHA256 = "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
EQUATION_V3_BODY_SHA256 = "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7"
FROZEN_SCHEMA_SNAPSHOT_INVENTORY_SHA256 = (
    "0e821d06ac0d8829b146c9555e428ea0368e6ed901aab2a657a3246dadf833d7"
)

INVARIANT_SECTION_SHA256 = {
    "edge_rule_registry": "9d8acc4ef18a4e928a7108ce84dbe1ae30599fa0a56054d8b7880f9a8d985884",
    "erasure_baseline": "067532baa954e12abbe630ebf1f260a737048da6d4776fb2a5cae5485618d44d",
    "erasure_materialized": "60a385781e3eeac0b1dfd505632975e9529a7f8f7120246fc99ea01f7537cfa4",
    "provider_registry": "43371023cdc4b39dd60d45a4d38d3100cc7fab1ef73b7cdca9462fb046eda086",
    "rights_index_policy_registry": (
        "9c3b759b3f7d1b7ccca4568de18c12dba754214675154bc0b97f5fa598106d39"
    ),
    "semantic_step_registry": ("c3f88439d006dbf86bd53e93be509f01f65c255574993d65fcba467a516f68f0"),
    "sql_authority_registry": ("55a3b3ce956ceae5439ab4322dd301525ab24c22e74a56fea2653236b0fadf54"),
    "usage_extractor_registry": (
        "78c515afec20a0b790a48a90fbbbd9c71a5c9db523599d9912abc6b8b0c65176"
    ),
    "usage_route_registry": ("c97e0903e546cfe800520a25f40c11556f5bd25aa9d88e23a6c0279b4e152899"),
    "uuid_namespace_registry": ("db692ff203ede78a3e7fecc85af680c7a02cc7b86be8713133d125813b89260c"),
}

VOID_V26_ACTIVE_IDENTITY_BLOCKLIST = tuple(void_evidence.VOID_V26_CANDIDATE_IDENTIFIERS)

EXPECTED_APPROVED_DELTA_MANIFEST = (
    (1, "ADD_SEPARATE_FDD_DETERMINISTIC_CALCULATION_SCHEMA", "SEMANTIC_TASK8_APPROVED"),
    (2, "ADD_FDD_SIGNED_RATIO_FACTUAL_SCHEMA", "SEMANTIC_TASK8_APPROVED"),
    (3, "BIND_VERSION_AWARE_FDD_SELECTORS", "SEMANTIC_TASK8_APPROVED"),
    (4, "REPLACE_DASHBOARD_MIXED_RESULT_TRUTH_TABLE", "SEMANTIC_TASK8_APPROVED"),
    (5, "ADD_PARTIAL_NATIVE_ROOT_AND_ERASURE_REFUSAL", "SEMANTIC_TASK8_APPROVED"),
    (6, "BIND_EXACT_POLICY_ARTIFACT_OUTPUT_HASH_CONTRACTS", "SEMANTIC_TASK8_APPROVED"),
    (7, "ADD_ACYCLIC_DASHBOARD_IDENTITY_CONTRACT", "SECOND_GATE_SEMANTIC"),
    (8, "DERIVE_DASHBOARD_SQL_FROM_FROZEN_RECIPES", "MECHANICAL_CLOSURE"),
    (9, "ENFORCE_STRICT_PERIOD_ORDERING", "MECHANICAL_CORRECTION"),
    (10, "ENFORCE_POSITIVE_MAGNITUDE_NONNEGATIVE", "MECHANICAL_CORRECTION"),
    (11, "PRESERVE_NUMERIC_SOURCE_NORMALIZATION", "MECHANICAL_CORRECTION"),
    (12, "BIND_FDD_FORMULA_POLICY_V2", "POLICY_BINDING"),
    (
        13,
        "REPLACE_DASHBOARD_REQUIREMENT_BINDING_WITH_VERSION_DISCRIMINATED_V3",
        "SEMANTIC_REVIEWED_EQUATION_V3",
    ),
    (
        14,
        "CLASSIFY_NONPRODUCER_RUNTIME_BINDINGS_AS_BOUND_INPUTS",
        "MECHANICAL_EVIDENCE_CLOSURE",
    ),
    (
        15,
        "CORRECT_INSTALLED_RLS_COUNT_DOMAIN_TO_POST_0038_PLUS_SCAFFOLD",
        "MECHANICAL_ADMISSION_LITERAL_CORRECTION",
    ),
)


class V27OracleError(AssertionError):
    """Stable test-oracle failure without private payload rendering."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V27OracleError(code)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def render_artifact(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def load_json(path: Path, code: str) -> dict[str, Any]:
    require(path.is_file(), code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V27OracleError(code) from exc
    require(isinstance(value, dict), code)
    return cast(dict[str, Any], value)


def load_materializer() -> ModuleType:
    require(V27_MATERIALIZER.is_file(), "TASK8_V27_MATERIALIZER_NOT_IMPLEMENTED")
    spec = importlib.util.spec_from_file_location("_task8_v27_materializer_red", V27_MATERIALIZER)
    if spec is None or spec.loader is None:
        raise V27OracleError("TASK8_V27_MATERIALIZER_IMPORT_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise V27OracleError("TASK8_V27_MATERIALIZER_IMPORT_INVALID") from exc
    return module


def build_candidate() -> tuple[dict[str, Any], ModuleType]:
    materializer = load_materializer()
    candidate = materializer.build_v27_from_parent_raw(
        parent_artifact_raw=V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=POLICY_V2_REVIEWER_MAP.read_bytes(),
    )
    require(isinstance(candidate, dict), "TASK8_V27_CANDIDATE_INVALID")
    return cast(dict[str, Any], candidate), materializer


def dashboard_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    ]
    require(len(matches) == 1, "TASK8_V27_DASHBOARD_AUTHORITY_CARDINALITY_INVALID")
    return cast(Mapping[str, Any], matches[0])


def dashboard_function_body(artifact: Mapping[str, Any]) -> str:
    row = dashboard_authority(artifact)
    statements = tuple(
        bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]
    )
    matches = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    ]
    require(len(matches) == 1, "TASK8_V27_DASHBOARD_FUNCTION_CARDINALITY_INVALID")
    body = matches[0].partition(" AS $$")[2].rsplit("$$", 1)[0]
    require(bool(body), "TASK8_V27_DASHBOARD_FUNCTION_BODY_INVALID")
    return body


def metadata_design(artifact: Mapping[str, Any]) -> list[object]:
    try:
        value = json.loads(bytes.fromhex(artifact["metadata"]["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise V27OracleError("TASK8_V27_REVIEWED_DESIGN_INVALID") from exc
    require(isinstance(value, list), "TASK8_V27_REVIEWED_DESIGN_INVALID")
    return cast(list[object], value)


def named_design_value(artifact: Mapping[str, Any], row_name: str, key: str) -> object:
    rows = [
        row
        for row in metadata_design(artifact)
        if isinstance(row, list) and len(row) == 2 and row[0] == row_name
    ]
    require(len(rows) == 1, "TASK8_V27_REVIEWED_DESIGN_ROW_INVALID")
    values = rows[0][1]
    require(isinstance(values, list), "TASK8_V27_REVIEWED_DESIGN_ROW_INVALID")
    matches = [values[index + 1] for index in range(0, len(values), 2) if values[index] == key]
    require(len(matches) == 1, "TASK8_V27_REVIEWED_DESIGN_KEY_INVALID")
    return matches[0]


def walk_strings(value: object) -> tuple[str, ...]:
    result: list[str] = []

    def walk(current: object) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                result.append(str(key))
                walk(child)
        elif isinstance(current, list):
            for child in current:
                walk(child)
        elif isinstance(current, str):
            result.append(current)

    walk(value)
    return tuple(result)


__all__ = [name for name in globals() if name.isupper()] + [
    "V27OracleError",
    "build_candidate",
    "canonical_bytes",
    "canonical_sha256",
    "dashboard_authority",
    "dashboard_function_body",
    "load_json",
    "load_materializer",
    "metadata_design",
    "named_design_value",
    "render_artifact",
    "require",
    "walk_strings",
]
