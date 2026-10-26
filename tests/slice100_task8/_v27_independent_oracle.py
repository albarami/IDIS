"""Test-owned oracle for the independent full-strict-v27 verifier.

The verifier under test is deliberately isolated from every production
materialization path.  This module reads only the exact hash-pinned staged
candidate bytes after the materializer is frozen; all expected identities, contract
domains, requirement vectors, and dashboard identity derivations below are
literal or independently implemented test authorities.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unicodedata
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from . import _task8_void_contract_hash_evidence as void_evidence

REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
CANONICAL_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v27_reviewed_preimage_v1.json"
STAGED_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v27-candidate"
    / "full_strict_v27_reviewed_preimage_v1.json"
)
CANONICAL_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v27_contract_hash_v1.json"
)
INDEPENDENT_VERIFIER = REPO_ROOT / "scripts" / "verify_full_strict_v27_fixture_independently.py"
GOLDEN_VECTOR = (
    REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "full_strict_v27_independent_golden_v1.json"
)

PIPELINE_VERSION = "full-strict-v27"
COMPONENT_DOMAIN = "full-strict-v27-component-v1"
PIPELINE_DOMAIN = "full-strict-v27-pipeline-contract-v1"
GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
CONSERVATION_SCHEMA = "task8-v27-approved-semantics-conservation-v1"
PARENT_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
PARENT_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
ARTIFACT_SHA256 = "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
ARTIFACT_SIZE_BYTES = 7_076_204
CONTRACT_SHA256 = "5221e622a40c44b4f2fe38e53e0208a7e1dba5358278d75624a0cc1c40ef0985"
TASK8_CONTRACT_SHA256 = "95ebd92b1c8c9602932df89dc3f9b3ec5221f3d1c0f7f5814f82da5ac10d570b"
STRUCTURAL_SHA256 = "ca32f92b07e0e2d059f6a9c6a08166a8ae4ef2fa88a137e685ae2b0b7e0c4ae4"
PRODUCER_SHA256 = "eb485dbe9cd7bdfa235dfcfff0d24e763b5db15c76942465c4c434a4530410c4"
EXTERNAL_SHA256 = "f1995db4b6e5a6a2f34de4d47c522b1d82025ab4c165f4af62a31b47e2ae7164"
GRAPH_SHA256 = "a951b96575f5c2927c59185f20378fde6ce019c207b77510a1033f4947649e3c"
CONSERVATION_SHA256 = "32a06ca41841c196ae76ad3d33b3d15da4dec8a247343b9a629390da8128475d"
POLICY_BINDING_SHA256 = "9e41ea12908a5c37542f6156d4bbf22dc19d59ac172d096a84fc55c4db83d2ff"
REQUIREMENT_LITERAL_SHA256 = "6e4d37a54c719ca21ba1c4dfd3d90e4804acc5d1e3d7b540791325aecc8d0a43"
EQUATION_ROW_SHA256 = "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
EQUATION_BODY_SHA256 = "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7"
FIXTURE_SHA256 = "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
APPLICATION_INVENTORY_DOMAIN = (
    "task8-v27-post-0038-plus-scaffold-public-ordinary-table-inventory-v1"
)
APPLICATION_INVENTORY_SHA256 = "e06330cdf5eabf5e1f95da4e66537de60476460ff9af6c0ebd7fd920a5ae6c39"
PHYSICAL_CATALOG_DOMAIN = (
    "task8-v27-post-0038-plus-scaffold-public-ordinary-table-physical-catalog-v1"
)
PHYSICAL_CATALOG_SHA256 = "85f9d8162c0241f88cf560e83689ce1ea7baff6c0de9a6cdddda512ac4df27f6"
DASHBOARD_RELATION_TUPLE = (
    "methodology_truth_dashboard_rows",
    "methodology_truth_dashboard_root_links",
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
EXCLUDED_APPLICATION_RELATION_TUPLE = ("alembic_version",)
PHYSICAL_PUBLIC_ORDINARY_RELATION_TUPLE = tuple(
    sorted((*SUCCESSOR_APPLICATION_RELATION_TUPLE, *EXCLUDED_APPLICATION_RELATION_TUPLE))
)
TOP_LEVEL_DIGEST_INVENTORY = (
    ("#/activation_assertions", "09636e225468fa726286b8b075404504c78ca45d93cb0ff7728a92a64c696dd5"),
    (
        "#/applicability_registry",
        "b480564d5c75cb7a48ab00eea42eeaa89e2bc17f4fe5a9443236a0d3623dabb6",
    ),
    (
        "#/baseline_parent_authority_registry",
        "36320b2bdd6ffe165a8af2ff6da8ca9a7884f634a60aaabc9a85ab7fa05ad792",
    ),
    (
        "#/canonicalization_contract",
        "c610c8b3bf4b8e19ffdf4db4230a5351271e822f316fd1897d13d29835040d6c",
    ),
    (
        "#/consumer_selector_registry",
        "679b68ecec2d8cc645d966c2b6f57920a994c2f17a13c9d87c61353befb3082b",
    ),
    ("#/current_ddl_registry", "68df8944cd9bdda6294832675ec08bf98c38f1a2a1189758cc2f6b7d31b3be5b"),
    ("#/deliverable_registry", "8e058e325eef60fa89639dcb898a003ba42d69335eac2d0d9ed97e985e092a75"),
    (
        "#/digest_recipe_registry",
        "ca666328b4e4eccd96946fecf4935b01e03b61926b95eacffc9945b0eabff7ef",
    ),
    ("#/edge_rule_registry", "9d8acc4ef18a4e928a7108ce84dbe1ae30599fa0a56054d8b7880f9a8d985884"),
    ("#/erasure_baseline", "067532baa954e12abbe630ebf1f260a737048da6d4776fb2a5cae5485618d44d"),
    (
        "#/erasure_execution_registry",
        "580968ff8ce62b2940da32604e5fec9a8f34e29d81292ef204315c75d6dfbdff",
    ),
    ("#/erasure_materialized", "60a385781e3eeac0b1dfd505632975e9529a7f8f7120246fc99ea01f7537cfa4"),
    (
        "#/future_child_authority_registry",
        "0cfc9124c4707777ed587fb5a520484c50d2c02addfc51255ac237f213488acc",
    ),
    (
        "#/future_native_authority_registry",
        "7b26c5e2e69911191b7948957112acf58e855e42a4a87868120ad0e02a4feaaf",
    ),
    (
        "#/future_reference_authority_registry",
        "c11abd81be74988d26cdee57bbe8fca2e5665ba7e1fee05833c17ea068d5e245",
    ),
    ("#/metadata", "ac0cbcee579f3b65e2c0fc6676c84b400c062c83dce37547e0e5a2978c18a574"),
    (
        "#/producer_selector_registry",
        "bf87e29d1560fa7355131afae0443c48e93b59982259f7b3e10dac659a8011a2",
    ),
    ("#/provider_registry", "43371023cdc4b39dd60d45a4d38d3100cc7fab1ef73b7cdca9462fb046eda086"),
    (
        "#/rights_index_policy_registry",
        "9c3b759b3f7d1b7ccca4568de18c12dba754214675154bc0b97f5fa598106d39",
    ),
    ("#/role_registry", "0e11e8758dda0846d9dfe8db6a67f00ec30a0cd146d5ba9ccf3567376ebc0934"),
    ("#/schema_registry", "d331c02114afbb196ac9faac92853e5012bc6e0af7d05762ebb6c97e4c0f36c1"),
    (
        "#/semantic_step_registry",
        "c3f88439d006dbf86bd53e93be509f01f65c255574993d65fcba467a516f68f0",
    ),
    (
        "#/sql_authority_registry",
        "55a3b3ce956ceae5439ab4322dd301525ab24c22e74a56fea2653236b0fadf54",
    ),
    ("#/task8_fdd_contract", "f0a6ba990d564cae070dea57988ef74a8e14bd338781cd437df146d4caee5790"),
    (
        "#/task8_structural_admission",
        "b270da628f6d8441d1356ffcca99d681dc9fa7fad476a05dda7b1ad58bab7485",
    ),
    ("#/tuple_schema_registry", "9eefebfa74ebbabdf25b20cc04d6cd16a7d876dad8c6fefd2ec2482cab0f68ab"),
    (
        "#/usage_extractor_registry",
        "78c515afec20a0b790a48a90fbbbd9c71a5c9db523599d9912abc6b8b0c65176",
    ),
    ("#/usage_route_registry", "c97e0903e546cfe800520a25f40c11556f5bd25aa9d88e23a6c0279b4e152899"),
    (
        "#/uuid_namespace_registry",
        "db692ff203ede78a3e7fecc85af680c7a02cc7b86be8713133d125813b89260c",
    ),
)
POLICY_VERSION_ID = (
    "financial_dd:a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352:"
    "fdd-formula-policy-v2:2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
)
STABLE_DASHBOARD_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"

EXPECTED_REQUIREMENT_TUPLE = (
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

VOID_IDENTITIES = void_evidence.TASK8_VOID_CONTRACT_HASHES

DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
DASHBOARD_OUTPUT_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"

_FORBIDDEN_IMPORT_PREFIXES = (
    "idis",
    "importlib",
    "runpy",
    "subprocess",
    "scripts",
    "tests",
)
_FORBIDDEN_SOURCE_MARKERS = (
    "materialize_full_strict",
    "generate_task8_contract_fixtures",
    "publish_task8_contract_artifact_gate",
    "verify_full_strict_v",
    "_independent_oracle",
    "__import__(",
)


class V27IndependentOracleError(AssertionError):
    """Privacy-safe test failure carrying only a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V27IndependentOracleError(code)


def _normalize(value: object) -> object:
    if isinstance(value, float):
        require(math.isfinite(value), "TASK8_V27_ORACLE_BINARY64_NONFINITE")
        return 0 if value == 0.0 else value
    if isinstance(value, str):
        require(unicodedata.normalize("NFC", value) == value, "TASK8_V27_ORACLE_UNICODE_NOT_NFC")
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            require(isinstance(key, str), "TASK8_V27_ORACLE_NON_STRING_KEY")
            result[key] = _normalize(item)
        return result
    raise V27IndependentOracleError("TASK8_V27_ORACLE_VALUE_TYPE_INVALID")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def top_level_digest_inventory(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((f"#/{key}", canonical_sha256(value)) for key, value in artifact.items()))


def render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def candidate_bytes() -> tuple[bytes, bytes, bytes, bytes]:
    """Return the exact frozen staged candidate and three frozen authorities."""

    require(STAGED_ARTIFACT.is_file(), "TASK8_V27_STAGED_ARTIFACT_MISSING")
    artifact_raw = STAGED_ARTIFACT.read_bytes()
    require(
        hashlib.sha256(artifact_raw).hexdigest() == ARTIFACT_SHA256,
        "TASK8_V27_STAGED_ARTIFACT_IDENTITY_DRIFT",
    )
    return (
        PARENT_ARTIFACT.read_bytes(),
        POLICY_ARTIFACT.read_bytes(),
        REVIEWER_MAP.read_bytes(),
        artifact_raw,
    )


def load_verifier() -> ModuleType:
    require(INDEPENDENT_VERIFIER.is_file(), "TASK8_V27_INDEPENDENT_VERIFIER_NOT_IMPLEMENTED")
    spec = importlib.util.spec_from_file_location(
        "_task8_v27_independent_verifier_red",
        INDEPENDENT_VERIFIER,
    )
    if spec is None or spec.loader is None:
        raise V27IndependentOracleError("TASK8_V27_INDEPENDENT_VERIFIER_IMPORT_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    prior_suppression = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(spec.name, None)
        raise V27IndependentOracleError("TASK8_V27_INDEPENDENT_VERIFIER_IMPORT_INVALID") from exc
    finally:
        sys.dont_write_bytecode = prior_suppression
    return module


def assert_verifier_source_is_independent(raw: bytes) -> None:
    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source)
    except (UnicodeError, SyntaxError) as exc:
        raise V27IndependentOracleError("TASK8_V27_INDEPENDENT_SOURCE_INVALID") from exc
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            require(node.func.id not in {"exec", "eval"}, "TASK8_V27_INDEPENDENT_COUPLED")
    for module_name in imported:
        require(
            not any(
                module_name == prefix or module_name.startswith(f"{prefix}.")
                for prefix in _FORBIDDEN_IMPORT_PREFIXES
            ),
            "TASK8_V27_INDEPENDENT_COUPLED",
        )
    lowered = source.lower()
    for marker in _FORBIDDEN_SOURCE_MARKERS:
        require(marker.lower() not in lowered, "TASK8_V27_INDEPENDENT_COUPLED")


def independent_component_hashes(artifact: Mapping[str, Any]) -> dict[str, str]:
    components = {
        f"{section}_hash": canonical_sha256([COMPONENT_DOMAIN, section, copy.deepcopy(value)])
        for section, value in artifact.items()
    }
    task8 = artifact.get("task8_fdd_contract")
    require(
        isinstance(task8, Mapping) and isinstance(task8.get("expected_sha256"), str),
        "TASK8_V27_ORACLE_TASK8_COMPONENT_INVALID",
    )
    task8 = cast(Mapping[str, Any], task8)
    components["task8_fdd_contract_hash"] = str(task8["expected_sha256"])
    return components


def independent_contract_hash(
    artifact: Mapping[str, Any], artifact_raw: bytes
) -> tuple[dict[str, str], str]:
    components = independent_component_hashes(artifact)
    ordered = [[name, digest] for name, digest in components.items()]
    contract_hash = canonical_sha256(
        [PIPELINE_DOMAIN, PIPELINE_VERSION, hashlib.sha256(artifact_raw).hexdigest(), ordered]
    )
    result = dict(components)
    result["pipeline_contract_hash"] = contract_hash
    return result, contract_hash


def independent_graph_hash(graph: Mapping[str, Any]) -> tuple[str, dict[str, int]]:
    require(graph.get("schema_version") == GRAPH_SCHEMA, "TASK8_V27_ORACLE_GRAPH_SCHEMA_INVALID")
    node_tuple = graph.get("node_tuple")
    edge_tuple = graph.get("edge_tuple")
    leaf_tuple = graph.get("leaf_tuple")
    source_pointer_tuple = graph.get("source_pointer_tuple")
    require(
        all(
            isinstance(value, list)
            for value in (node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple)
        ),
        "TASK8_V27_ORACLE_GRAPH_SHAPE_INVALID",
    )
    node_tuple = cast(list[Any], node_tuple)
    edge_tuple = cast(list[Any], edge_tuple)
    leaf_tuple = cast(list[Any], leaf_tuple)
    source_pointer_tuple = cast(list[Any], source_pointer_tuple)
    counts = {
        "node_count": len(node_tuple),
        "edge_count": len(edge_tuple),
        "leaf_count": len(leaf_tuple),
        "source_pointer_count": len(source_pointer_tuple),
    }
    digest = canonical_sha256(
        [GRAPH_SCHEMA, node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple]
    )
    require(graph.get("counts") == counts, "TASK8_V27_ORACLE_GRAPH_COUNT_INVALID")
    require(graph.get("graph_sha256") == digest, "TASK8_V27_ORACLE_GRAPH_HASH_INVALID")
    return digest, counts


def _policy_entry(
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


def requirement_vectors() -> tuple[tuple[str, bool, dict[str, object]], ...]:
    """Hand-written legal and nine-negative requirement vectors."""

    legacy_version = "financial_dd:legacy-frozen-v1"
    legal: tuple[tuple[str, bool, dict[str, object]], ...] = (
        (
            "legacy_empty",
            True,
            {
                "methodology_version_id": legacy_version,
                "joined_methodology_version_id": legacy_version,
                "required_calculations": [],
                "calculation_types": [],
            },
        ),
        (
            "legacy_singleton",
            True,
            {
                "methodology_version_id": legacy_version,
                "joined_methodology_version_id": legacy_version,
                "required_calculations": [{"calc_type": "GROSS_MARGIN", "required": True}],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
        (
            "policy_empty",
            True,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [],
                "calculation_types": [],
            },
        ),
        (
            "policy_singleton",
            True,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [_policy_entry()],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
    )
    negative: tuple[tuple[str, bool, dict[str, object]], ...] = (
        (
            "legacy_four_field",
            False,
            {
                "methodology_version_id": legacy_version,
                "joined_methodology_version_id": legacy_version,
                "required_calculations": [_policy_entry()],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
        (
            "policy_two_field",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [{"calc_type": "GROSS_MARGIN", "required": True}],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
        (
            "unknown_formula_type",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [_policy_entry(formula_type="UNKNOWN")],
                "calculation_types": ["UNKNOWN"],
            },
        ),
        (
            "ordinal_drift",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [_policy_entry(ordinal=2)],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
        (
            "scope_drift",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [_policy_entry(scope="sheet_control")],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
        (
            "undeclared_calculation_root",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [_policy_entry()],
                "calculation_types": ["GROSS_MARGIN", "FDD_EBITDA_BRIDGE"],
            },
        ),
        (
            "mixed_shapes",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [
                    _policy_entry(),
                    {"calc_type": "FDD_EBITDA_BRIDGE", "required": True},
                ],
                "calculation_types": ["GROSS_MARGIN", "FDD_EBITDA_BRIDGE"],
            },
        ),
        (
            "cross_version_join",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": legacy_version,
                "required_calculations": [_policy_entry()],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
        (
            "duplicate_tuple",
            False,
            {
                "methodology_version_id": POLICY_VERSION_ID,
                "joined_methodology_version_id": POLICY_VERSION_ID,
                "required_calculations": [_policy_entry(), _policy_entry(required=False)],
                "calculation_types": ["GROSS_MARGIN"],
            },
        ),
    )
    return (*legal, *negative)


def uuid5_from_canonical_name(namespace: str, value: object) -> str:
    name_sha256 = hashlib.sha256(canonical_json(value)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), name_sha256))


def derive_dashboard_identity_vector(**inputs: Any) -> dict[str, object]:
    tenant_id = str(inputs["tenant_id"])
    deal_id = str(inputs["deal_id"])
    run_id = str(inputs["run_id"])
    pipeline_version = str(inputs["pipeline_contract_version"])
    pipeline_hash = str(inputs["pipeline_contract_hash"])
    coverage_record_id = str(inputs["coverage_record_id"])
    row_id = uuid5_from_canonical_name(
        DASHBOARD_ROW_NAMESPACE,
        [
            "methodology-truth-dashboard-row-v2",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            coverage_record_id,
            inputs["methodology_version_record_id"],
            inputs["methodology_question_record_id"],
            inputs["question_payload_hash"],
        ],
    )
    root_links: list[list[object]] = []
    roots = sorted(
        inputs["roots"],
        key=lambda item: (
            str(item["root_kind"]),
            int(item["root_ordinal"]),
            str(item["root_output_object_id"]),
        ),
    )
    for root in roots:
        link_id = uuid5_from_canonical_name(
            DASHBOARD_ROOT_LINK_NAMESPACE,
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
        root_links.append(
            [
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
                root["root_output_object_kind"],
                link_id,
            ]
        )
    projection_hash = canonical_sha256(
        [
            "methodology-truth-dashboard-root-projection-v2",
            tenant_id,
            deal_id,
            run_id,
            row_id,
            coverage_record_id,
            inputs["row_disposition"],
            len(root_links),
            root_links,
        ]
    )
    payload_hash = canonical_sha256(
        [
            "financial-diligence-row-v2",
            [*inputs["row_trace_prefix"], coverage_record_id, row_id],
            inputs["row_disposition"],
            inputs["disposition_reason_code"],
            inputs["result_records"],
            inputs["answer_values"],
            inputs["task_gaps"],
            inputs["calculations"],
            projection_hash,
        ]
    )
    native_identity_hash = canonical_sha256(
        ["native-identity-v1", tenant_id, deal_id, run_id, DASHBOARD_OUTPUT_KIND, [row_id]]
    )
    native_output_hash = canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            DASHBOARD_OUTPUT_KIND,
            [row_id],
            [coverage_record_id, projection_hash, inputs["row_disposition"], payload_hash],
        ]
    )
    output_object_id = uuid5_from_canonical_name(
        OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            "NATIVE_ROW",
            DASHBOARD_OUTPUT_KIND,
            native_identity_hash,
            native_output_hash,
        ],
    )
    return {
        "dashboard_row_id": row_id,
        "root_link_tuple": root_links,
        "root_projection_sha256": projection_hash,
        "row_payload_sha256": payload_hash,
        "native_identity_sha256": native_identity_hash,
        "native_output_sha256": native_output_hash,
        "output_object_id": output_object_id,
    }


def load_golden_vector() -> dict[str, Any]:
    require(GOLDEN_VECTOR.is_file(), "TASK8_V27_GOLDEN_VECTOR_MISSING")
    try:
        value = json.loads(GOLDEN_VECTOR.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V27IndependentOracleError("TASK8_V27_GOLDEN_VECTOR_INVALID") from exc
    require(isinstance(value, dict), "TASK8_V27_GOLDEN_VECTOR_INVALID")
    require(
        value.get("schema_version") == "task8-v27-independent-golden-v1"
        and value.get("derivation_authority") == "HAND_WRITTEN_INDEPENDENT_VECTOR",
        "TASK8_V27_GOLDEN_VECTOR_INVALID",
    )
    return cast(dict[str, Any], value)


def verifier_error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    require(isinstance(code, str), "TASK8_V27_INDEPENDENT_ERROR_CODE_MISSING")
    return cast(str, code)


__all__ = [
    "ARTIFACT_SHA256",
    "CANONICAL_ARTIFACT",
    "CANONICAL_FIXTURE",
    "CONSERVATION_SHA256",
    "CONTRACT_SHA256",
    "FIXTURE_SHA256",
    "GOLDEN_VECTOR",
    "GRAPH_SHA256",
    "INDEPENDENT_VERIFIER",
    "PARENT_ARTIFACT",
    "PARENT_ARTIFACT_SHA256",
    "POLICY_ARTIFACT",
    "POLICY_BINDING_SHA256",
    "POLICY_VERSION_ID",
    "REVIEWER_MAP",
    "STABLE_DASHBOARD_CODE",
    "VOID_IDENTITIES",
    "V27IndependentOracleError",
    "assert_verifier_source_is_independent",
    "candidate_bytes",
    "canonical_sha256",
    "derive_dashboard_identity_vector",
    "independent_component_hashes",
    "independent_contract_hash",
    "independent_graph_hash",
    "load_golden_vector",
    "load_verifier",
    "render",
    "require",
    "requirement_vectors",
    "verifier_error_code",
    "void_evidence",
]
