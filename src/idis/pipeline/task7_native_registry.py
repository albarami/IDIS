"""Closed native-output authority registry for the frozen full-strict-v21 contract.

The registry is intentionally code-owned.  It exposes reviewed future authorities
as inactive metadata without claiming that their Tasks 8--11 tables exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

STEP_OUTPUT_MISSING: Final = "STEP_OUTPUT_MISSING"


class NativeRegistryError(RuntimeError):
    """Stable fail-closed native-authority lookup error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NativeAuthorityState(StrEnum):
    """Installation state of a reviewed native authority."""

    CURRENT = "CURRENT"
    FUTURE_INACTIVE = "FUTURE_INACTIVE"


@dataclass(frozen=True, slots=True)
class NativeAuthoritySpec:
    """One reviewed ``(producer step, output kind)`` authority coordinate."""

    step_name: str
    output_kind: str
    relation_name: str
    state: NativeAuthorityState
    schema_id: str
    schema_version: str
    schema_sha256: str


@dataclass(frozen=True, slots=True)
class _AuthorityTemplate:
    relation_name: str
    state: NativeAuthorityState
    schema_id: str
    schema_version: str
    schema_sha256: str


_CURRENT = NativeAuthorityState.CURRENT
_FUTURE = NativeAuthorityState.FUTURE_INACTIVE

_AUTHORITY_TEMPLATES: Final[dict[str, _AuthorityTemplate]] = {
    "coverage": _AuthorityTemplate(
        "methodology_coverage_records",
        _CURRENT,
        "methodology-coverage-record",
        "task7-methodology-coverage-record-v1",
        "983c0716f9b80f572433040a023a1f9dbbec0bdb372a61818300de219cefe287",
    ),
    "task": _AuthorityTemplate(
        "methodology_extraction_tasks",
        _CURRENT,
        "methodology-extraction-task",
        "task7-methodology-extraction-task-v1",
        "a761f6986ea221b231966b23ebcd178fdca249db826a91e8cd2936ab6f898cac",
    ),
    "result": _AuthorityTemplate(
        "methodology_extraction_results",
        _CURRENT,
        "methodology-extraction-result",
        "task7-methodology-extraction-result-v1",
        "68960b23ccad4d36b956cf10da785d57785537211c351f95a3933ca3b7075a33",
    ),
    "factual": _AuthorityTemplate(
        "methodology_result_factual_outputs",
        _CURRENT,
        "methodology-factual-output",
        "task7-methodology-factual-output-v1",
        "9a7c8e2c64bb6511ab5aa6721808bb4f07bcb131e24869dc066c9179371c18df",
    ),
    "methodology_claim": _AuthorityTemplate(
        "methodology_result_claim_links",
        _CURRENT,
        "methodology-link-records",
        "task7-methodology-link-records-v1",
        "013579b01df2c3ec0bfcac0b6a07882f2f3aea4acb313f892296f599facf4a3a",
    ),
    "methodology_evidence": _AuthorityTemplate(
        "methodology_result_evidence_links",
        _CURRENT,
        "methodology-link-records",
        "task7-methodology-link-records-v1",
        "013579b01df2c3ec0bfcac0b6a07882f2f3aea4acb313f892296f599facf4a3a",
    ),
    "methodology_sanad": _AuthorityTemplate(
        "methodology_result_sanad_links",
        _CURRENT,
        "methodology-link-records",
        "task7-methodology-link-records-v1",
        "013579b01df2c3ec0bfcac0b6a07882f2f3aea4acb313f892296f599facf4a3a",
    ),
    "methodology_defect": _AuthorityTemplate(
        "methodology_result_defect_links",
        _CURRENT,
        "methodology-link-records",
        "task7-methodology-link-records-v1",
        "013579b01df2c3ec0bfcac0b6a07882f2f3aea4acb313f892296f599facf4a3a",
    ),
    "calculation": _AuthorityTemplate(
        "deterministic_calculations",
        _CURRENT,
        "deterministic-calculation",
        "task7-deterministic-calculation-v1",
        "7b027e03a494d1e383193c60f0b12f0293af8dbb87b31e38c79cdd883837ece9",
    ),
    "court_finding": _AuthorityTemplate(
        "evidence_trust_findings",
        _CURRENT,
        "layer1-native-rows",
        "task7-layer1-native-rows-v1",
        "a2f55b3254933125230e78d92db67a5e961c6965cabb3689fe9c0e66d5483ffc",
    ),
    "muhasabah": _AuthorityTemplate(
        "muhasabah_records",
        _CURRENT,
        "layer1-native-rows",
        "task7-layer1-native-rows-v1",
        "a2f55b3254933125230e78d92db67a5e961c6965cabb3689fe9c0e66d5483ffc",
    ),
    "vep": _AuthorityTemplate(
        "validated_evidence_packages",
        _CURRENT,
        "layer1-native-rows",
        "task7-layer1-native-rows-v1",
        "a2f55b3254933125230e78d92db67a5e961c6965cabb3689fe9c0e66d5483ffc",
    ),
    "debate": _AuthorityTemplate(
        "debate_sessions",
        _CURRENT,
        "layer1-native-rows",
        "task7-layer1-native-rows-v1",
        "a2f55b3254933125230e78d92db67a5e961c6965cabb3689fe9c0e66d5483ffc",
    ),
    "layer2_challenge": _AuthorityTemplate(
        "layer2_ic_challenges",
        _CURRENT,
        "layer2-native-rows",
        "task7-layer2-native-rows-v1",
        "1a8594cdf68e982c6f1b01d654b606b502971572aacbeae995d6a11e8a5016c0",
    ),
    "layer2_finding": _AuthorityTemplate(
        "layer2_ic_findings",
        _CURRENT,
        "layer2-native-rows",
        "task7-layer2-native-rows-v1",
        "1a8594cdf68e982c6f1b01d654b606b502971572aacbeae995d6a11e8a5016c0",
    ),
    "deliverable": _AuthorityTemplate(
        "deliverables",
        _CURRENT,
        "deliverable-artifact",
        "task7-deliverable-artifact-v1",
        "d2733fcd57c5f5bc84029ea9c54613bc66b1522d327ea12b01b1b90fe8700ea5",
    ),
    "deliverable_manifest": _AuthorityTemplate(
        "deliverable_manifest_references",
        _CURRENT,
        "deliverable-artifact",
        "task7-deliverable-artifact-v1",
        "d2733fcd57c5f5bc84029ea9c54613bc66b1522d327ea12b01b1b90fe8700ea5",
    ),
    "truth_dashboard": _AuthorityTemplate(
        "methodology_truth_dashboard_rows",
        _FUTURE,
        "truth-dashboard-native-v1",
        "1",
        "b997f264cae7a0e99cd165c4ebcc7155b5ac5ea431fc20ec4d563e05c70eb56b",
    ),
    "identity": _AuthorityTemplate(
        "company_identity_outputs",
        _FUTURE,
        "company-identity-native-v1",
        "1",
        "8e694605dbe06f4838002bcadccc8dc03ab322dba24a69fa35c63c677696af50",
    ),
    "provider_outcome": _AuthorityTemplate(
        "enrichment_provider_outcomes",
        _FUTURE,
        "provider-outcome-native-v1",
        "1",
        "e82b16fe368d0fee55780243e66732bf2fa8a6a40fa70ad0d3c5d1886d4c2afe",
    ),
    "provider_fact": _AuthorityTemplate(
        "enrichment_normalized_facts",
        _FUTURE,
        "provider-fact-native-v1",
        "1",
        "6b7e13956ba7f72dddee5eb9bdf30a9e46f934753a9e2123b8e51328ac13cb1d",
    ),
    "graph": _AuthorityTemplate(
        "graph_projection_results",
        _FUTURE,
        "graph-native-v1",
        "1",
        "e3c3bcd8000b082ff346d0fd0fabd2427ac9542b223d8308f7d966abf5f60f2b",
    ),
    "rag_index": _AuthorityTemplate(
        "rag_index_results",
        _FUTURE,
        "rag-index-native-v1",
        "1",
        "809ea82ef3bcd294cf0a13cb8ddadf9f8a16c493c0b139c89edd0fe5eaa80675",
    ),
    "rag_query": _AuthorityTemplate(
        "rag_query_results",
        _FUTURE,
        "rag-query-native-v1",
        "1",
        "9d599a7bdfeb42f38f41bd5e2d396f6193c9581771e26190a7778fdb37524dbc",
    ),
    "conflict_plan": _AuthorityTemplate(
        "external_intelligence_conflict_plans",
        _FUTURE,
        "conflict-plan-native-v1",
        "1",
        "4c7b46456ac55d37baf069f684293e388e9ce762d3cbf22eba11571f5334c050",
    ),
    "conflict_execution": _AuthorityTemplate(
        "external_intelligence_conflict_executions",
        _FUTURE,
        "conflict-execution-native-v1",
        "1",
        "d29041538004370cd4c88f5874e807883f582c817aac195ce0b4c7e234b900f3",
    ),
    "conflict_finding": _AuthorityTemplate(
        "external_intelligence_conflict_findings",
        _FUTURE,
        "conflict-finding-native-v1",
        "1",
        "bf7eaaa637b6daaab4ebce1c4b6b73bb20b6f89bcb5a43f7356ece82295e587d",
    ),
    "vep_reference": _AuthorityTemplate(
        "validated_evidence_package_references",
        _FUTURE,
        "vep-content-reference-native-v1",
        "1",
        "344ef6f1d588fa2f7e071f8e9e15541a955aec54ed86bdc44abbcb897dbcad0b",
    ),
    "debate_role": _AuthorityTemplate(
        "debate_role_outputs",
        _FUTURE,
        "debate-role-native-v1",
        "1",
        "057f37e081207a0c4fd59a114324b8b148b279c5f432cdda853ccd65ff3d5fe5",
    ),
    "analysis_role": _AuthorityTemplate(
        "analysis_agent_outputs",
        _FUTURE,
        "analysis-role-native-v1",
        "1",
        "7744a28711c432dfd18b3ee5120ae96c0876cdc66b9fe935486f8f61f412b616",
    ),
    "scoring": _AuthorityTemplate(
        "scoring_outputs",
        _FUTURE,
        "scorecard-native-v1",
        "1",
        "a622d81b5c631601859b7d7818da578fc2b409af14b7f48cfc7ef4daa734e862",
    ),
    "readiness": _AuthorityTemplate(
        "layer2_readiness_outputs",
        _FUTURE,
        "layer2-readiness-native-v1",
        "1",
        "5a81fdb22f571fe6ba7dbf4a57b494736878e3c95bbc5bc5cc5bbf4b2e4752b4",
    ),
    "layer2_role": _AuthorityTemplate(
        "layer2_role_outputs",
        _FUTURE,
        "layer2-role-native-v1",
        "1",
        "2cba3c7ee9c5033403a54ff0947c6766293c525c2d5f61ce75d086ff163fe975",
    ),
    "generic": _AuthorityTemplate(
        "generic_extraction_outputs",
        _FUTURE,
        "generic-extraction-native-v1",
        "1",
        "8579cbdf877f1c61dddb090770f165004c7554f06b6faa0ba6493ff2d515e644",
    ),
    "generic_claim": _AuthorityTemplate(
        "generic_extraction_claim_links",
        _FUTURE,
        "generic-extraction-claim-links-v1",
        "1",
        "b568840083d07f173f8edbfe3c03c5d4602aa6660a2a3a25ce092b6aae633718",
    ),
    "generic_evidence": _AuthorityTemplate(
        "generic_extraction_evidence_links",
        _FUTURE,
        "generic-extraction-evidence-links-v1",
        "1",
        "f7dac17361310e6fbeefc08b2d36d5ff2e4a6314add143a0ece1dde546ed8c17",
    ),
    "grading": _AuthorityTemplate(
        "run_claim_grading_links",
        _FUTURE,
        "grading-link-native-v1",
        "1",
        "c0651811863b0faa38473f0f2aebe6dc9e71acb7e9b764d8aec054d4c83ca66b",
    ),
}

_COORDINATE_ROWS: Final[tuple[tuple[str, str, str], ...]] = (
    ("METHODOLOGY_COVERAGE_INIT", "METHODOLOGY_COVERAGE_IDENTITY", "coverage"),
    (
        "METHODOLOGY_EXTRACTION_TASK_PLANNING",
        "METHODOLOGY_EXTRACTION_TASK",
        "task",
    ),
    (
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "METHODOLOGY_EXTRACTION_RESULT",
        "result",
    ),
    (
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "METHODOLOGY_FACTUAL_OUTPUT",
        "factual",
    ),
    (
        "METHODOLOGY_CLAIM_MATERIALIZATION",
        "METHODOLOGY_CLAIM_LINK",
        "methodology_claim",
    ),
    (
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION",
        "METHODOLOGY_EVIDENCE_LINK",
        "methodology_evidence",
    ),
    (
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING",
        "METHODOLOGY_SANAD_LINK",
        "methodology_sanad",
    ),
    (
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING",
        "METHODOLOGY_DEFECT_LINK",
        "methodology_defect",
    ),
    (
        "METHODOLOGY_DETERMINISTIC_CALCULATION",
        "METHODOLOGY_CALCULATION",
        "calculation",
    ),
    (
        "METHODOLOGY_TRUTH_DASHBOARD",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
        "truth_dashboard",
    ),
    ("EXTRACT", "GENERIC_EXTRACTION_OUTPUT", "generic"),
    ("EXTRACT", "METHODOLOGY_CLAIM_LINK", "generic_claim"),
    ("EXTRACT", "METHODOLOGY_EVIDENCE_LINK", "generic_evidence"),
    ("GRADE", "RUN_CLAIM_GRADING_LINK", "grading"),
    ("CALC", "CALCULATION", "calculation"),
    (
        "METHODOLOGY_COMPANY_IDENTITY_PACKAGE",
        "COMPANY_IDENTITY_OUTPUT",
        "identity",
    ),
    ("ENRICHMENT", "ENRICHMENT_PROVIDER_OUTCOME", "provider_outcome"),
    ("ENRICHMENT", "ENRICHMENT_FACT", "provider_fact"),
    ("GRAPH_EVIDENCE", "GRAPH_PROJECTION_RESULT", "graph"),
    ("RAG_EVIDENCE", "RAG_INDEX_RESULT", "rag_index"),
    ("RAG_EVIDENCE", "RAG_QUERY_RESULT", "rag_query"),
    (
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "CONFLICT_PLAN",
        "conflict_plan",
    ),
    (
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "CONFLICT_EXECUTION",
        "conflict_execution",
    ),
    (
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "CONFLICT_FINDING",
        "conflict_finding",
    ),
    (
        "METHODOLOGY_EVIDENCE_TRUST_COURT",
        "EVIDENCE_TRUST_FINDING",
        "court_finding",
    ),
    (
        "METHODOLOGY_EVIDENCE_TRUST_COURT",
        "COURT_MUHASABAH",
        "muhasabah",
    ),
    (
        "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE",
        "VALIDATED_EVIDENCE_PACKAGE",
        "vep",
    ),
    (
        "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE",
        "VEP_CONTENT_REFERENCE",
        "vep_reference",
    ),
    ("DEBATE", "DEBATE_SESSION", "debate"),
    ("DEBATE", "DEBATE_ROLE_OUTPUT", "debate_role"),
    ("DEBATE", "DEBATE_MUHASABAH", "muhasabah"),
    ("ANALYSIS", "ANALYSIS_AGENT_OUTPUT", "analysis_role"),
    ("ANALYSIS", "ANALYSIS_MUHASABAH", "muhasabah"),
    ("SCORING", "SCORING_OUTPUT", "scoring"),
    (
        "METHODOLOGY_LAYER2_READINESS_PACKAGE",
        "LAYER2_READINESS_OUTPUT",
        "readiness",
    ),
    ("LAYER2_IC_CHALLENGE", "LAYER2_CHALLENGE", "layer2_challenge"),
    ("LAYER2_IC_CHALLENGE", "LAYER2_FINDING", "layer2_finding"),
    ("LAYER2_IC_CHALLENGE", "LAYER2_ROLE_OUTPUT", "layer2_role"),
    ("LAYER2_IC_CHALLENGE", "LAYER2_MUHASABAH", "muhasabah"),
    ("DELIVERABLES", "DELIVERABLE_ARTIFACT", "deliverable"),
    (
        "DELIVERABLES",
        "DELIVERABLE_MANIFEST_REFERENCE",
        "deliverable_manifest",
    ),
)


def _build_registry() -> dict[tuple[str, str], NativeAuthoritySpec]:
    registry: dict[tuple[str, str], NativeAuthoritySpec] = {}
    for step_name, output_kind, template_key in _COORDINATE_ROWS:
        template = _AUTHORITY_TEMPLATES[template_key]
        coordinate = (step_name, output_kind)
        if coordinate in registry:
            raise RuntimeError(f"duplicate native authority coordinate: {coordinate!r}")
        registry[coordinate] = NativeAuthoritySpec(
            step_name=step_name,
            output_kind=output_kind,
            relation_name=template.relation_name,
            state=template.state,
            schema_id=template.schema_id,
            schema_version=template.schema_version,
            schema_sha256=template.schema_sha256,
        )
    return registry


NATIVE_OUTPUT_REGISTRY: Final = MappingProxyType(_build_registry())


def require_native_authority(
    step_name: str,
    output_kind: str,
) -> NativeAuthoritySpec:
    """Return a reviewed authority or fail closed on an unknown coordinate."""
    authority = NATIVE_OUTPUT_REGISTRY.get((step_name, output_kind))
    if authority is None:
        raise NativeRegistryError(
            STEP_OUTPUT_MISSING,
            f"no reviewed native authority exists for {step_name}/{output_kind}",
        )
    return authority


def require_current_native_authority(
    step_name: str,
    output_kind: str,
) -> NativeAuthoritySpec:
    """Require that a reviewed authority is installed in the current schema."""
    authority = require_native_authority(step_name, output_kind)
    if authority.state is not NativeAuthorityState.CURRENT:
        raise NativeRegistryError(
            STEP_OUTPUT_MISSING,
            f"native authority {step_name}/{output_kind} remains future-inactive",
        )
    return authority
