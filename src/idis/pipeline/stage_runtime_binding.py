"""GL-12 inactive full-strict-v28 stage runtime binding.

Code-owned reviewed base step tuple, named base component projection and the
flat Task 7 stage-input identity helpers for the dedicated stage admission
prerequisite.  This module is an explicitly invoked engineering prerequisite
only: the contract is INACTIVE, it is not registered with admission, queue,
worker, retry, recovery or producer callers, and the final governed successor
contract hash remains unavailable.  Callers requiring the complete governed
graph or authority fail closed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from idis.analysis.scoring.stage_admission import (
    STAGE_ADMISSION_INVALID,
    RunStageAdmissionV1,
    StageAdmissionError,
)
from idis.pipeline.contract_registry import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PIPELINE_CONTRACT_UNAVAILABLE,
    PipelineContractError,
    PipelineContractSpec,
    PipelineContractState,
    PipelineStepSpec,
    validate_admission_step_rows,
)
from idis.pipeline.task7_uuid import (
    Task7DerivedUuid,
    _canonical_json_bytes,
    derive_task7_v28_candidate_uuid,
    task7_v28_candidate_namespace_registry,
)

FULL_STRICT_V28_CONTRACT_VERSION: Final = "full-strict-v28"
FULL_STRICT_V28_BASE_CONTRACT_HASH: Final = (
    "28fa5a009d723d76de4c515e9131445d83bd3b9662621d292aff17c47f2ee4fa"
)
AUTHORITY_AMENDMENT02_PROPOSAL_SHA256: Final = (
    "5666e2f4643defc149ed780f33af1e8e80bfbc8bd18d509731a2bb5ff26c256b"
)
AUTHORITY_AMENDMENT02_VECTORS_SHA256: Final = (
    "cea8bae8f10329bdb887146bf800c0193cf69d2e8108d60bc582658208985409"
)

_STAGE_INPUT_IDENTITY_DOMAIN: Final = "idis-stage-admission-input-identity-v1"
_STAGE_BINDING_ID_KIND: Final = "stage_admission_binding_id"
_STAGE_SOURCE_OBJECT_KIND: Final = "RUN_STAGE_ADMISSION"
_STAGE_SOURCE_DISPOSITION: Final = "ADMITTED_METADATA"
_STAGE_BINDING_PURPOSE: Final = "STAGE_ADMISSION"
_STAGE_SOURCE_VERSION: Final = "run-stage-admission-v1"
_STAGE_CARRIER_DIGEST_DOMAIN: Final = "run-input-binding-digest-v2"
_EDGE_MANIFEST_SHA256: Final = "5fc0263dea7b9ae92a27f36ebdbbb2201e52da08bc8940b4ad5a15fd8ddf378b"
_CURRENT_DDL_REGISTRY_SHA256: Final = (
    "073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd"
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

_INACTIVE_V28_STEP_ROWS: Final[tuple[tuple[int, str, str], ...]] = (
    (
        0,
        "DATA_ROOM_INVENTORY_PACKAGE",
        "7fb7c7d9f4af3bc9c0f224aa9a868dc20c3f77cc22c35f0d2f5f4b4bf3e9ef06",
    ),
    (
        1,
        "DATA_ROOM_INGESTION_HANDOFF",
        "0e61d997dc89050ebaf2d72b3459bc41a64cde650893f3b36414fa371105fa1a",
    ),
    (2, "INGEST_CHECK", "6634007f7e2d2ac83631d9bb386450de73bd30ca1e8ee5529de10cba9994df03"),
    (3, "DOCUMENT_PREFLIGHT", "af64299ed8f2cdac1c9cf2e1fa7ce8f585df6d8dbe9dfd8980dcdc238b16e7a7"),
    (
        4,
        "METHODOLOGY_COVERAGE_INIT",
        "5b157c8329a0a1c229f6ffb3ec2cfc0c0be1c6fb85f0dc0a669883aa1eb75dbc",
    ),
    (
        5,
        "METHODOLOGY_EXTRACTION_TASK_PLANNING",
        "07e0980ebfa04d3aae5042ec39b07200cacc87cc5f8ad40db6338faa56bb38af",
    ),
    (
        6,
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "0550f5d7335852771e830ae9f376a9633813a24fd7afc677c32c45ee1446dded",
    ),
    (
        7,
        "METHODOLOGY_CLAIM_MATERIALIZATION",
        "63ae11aa758eea76bfa4931cc35ea6bd2b42a827d58b7f058f000db2e439792c",
    ),
    (
        8,
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION",
        "7ca82a99ce4bf6782d76b17fdc8434bf98fc92e1c4626bcf2121004bed63f94e",
    ),
    (
        9,
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING",
        "ca4f4b8c77d274999f9593829ff76cbe0839e05f82082a37d8554cc1bff2dd52",
    ),
    (
        10,
        "METHODOLOGY_DETERMINISTIC_CALCULATION",
        "2db75b6695f26cfbf7f09ef0a440da557c15eb9b960be86d78f62f70412617ed",
    ),
    (
        11,
        "METHODOLOGY_TRUTH_DASHBOARD",
        "29a48e733fe07251ed0bb509311ddfbb36936876913c84ce149e79cd5336eea9",
    ),
    (12, "EXTRACT", "ba078b98792baa4f5656ebdd21659a02f659a8e666a6f53b2e094c6c666c065f"),
    (13, "GRADE", "c24aa9f98b6a0ff2d405cfe916bdcf3fe3082e20685d671bfbf5b62853060e35"),
    (14, "CALC", "8158acc63c864ef44cfc4ba7e622268e603ccad36f77729f3e09483ecf3e17ef"),
    (
        15,
        "METHODOLOGY_COMPANY_IDENTITY_PACKAGE",
        "3c591896fcae85db3d541beed32442a3824dcbd7df7568794e35181cb61c030e",
    ),
    (16, "ENRICHMENT", "0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6"),
    (17, "GRAPH_EVIDENCE", "11d0a5a8557433a21994ac042a5d248ec98b1310c4374df66d3ebb1705262298"),
    (18, "RAG_EVIDENCE", "de1aa5c89992b1eec574448a6f857d419782126544b556ae177f8fa8356508f9"),
    (
        19,
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "5b3564d3cc1874e209477d733527dba5a4cbaf59b5ef4f4b308b5ca902f2f3c8",
    ),
    (
        20,
        "METHODOLOGY_EVIDENCE_TRUST_COURT",
        "7cc67598d4fc5e6e49e3bd927deca66227b7be2f9a2a80bc1bd735bb243e5cbf",
    ),
    (
        21,
        "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE",
        "29883670e0e8512905cb08e979f77b2b5cc158667f7db082518b937272fb4b94",
    ),
    (22, "DEBATE", "ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83"),
    (23, "ANALYSIS", "aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa"),
    (24, "SCORING", "0c9c74af82ccd30eb683a40aedbef32c1877618a415d3e330a08039029045177"),
    (
        25,
        "METHODOLOGY_LAYER2_READINESS_PACKAGE",
        "49930fbde3c063c9b02a3501506fa6364d482857635745be255ce186206dd69c",
    ),
    (26, "LAYER2_IC_CHALLENGE", "3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e"),
    (27, "DELIVERABLES", "672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d"),
)

_BASE_COMPONENT_IDENTITIES: Final[tuple[tuple[str, tuple[tuple[str, str], ...]], ...]] = (
    (
        "uuid_namespace_registry",
        (("expected_sha256", "d170ee0b211898f8ba59d52addbbdcf9ff78a96e47039829a10a1e8d3728c0a9"),),
    ),
    (
        "schema_registry",
        (("expected_sha256", "7dba098193e93febc53c93de75c0bbe8462080834c755b84d8e9a9dbfc02f553"),),
    ),
    (
        "semantic_step_registry",
        (
            (
                "expected_vector_sha256",
                "ee473f158a9e48dff845c7e498e8bd3eadde20ea68eeb698e7c3482c158182f6",
            ),
        ),
    ),
    (
        "current_ddl_registry",
        (("expected_sha256", "073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd"),),
    ),
    (
        "baseline_parent_authority_registry",
        (("expected_sha256", "3269dd9af23b720a63c90568e30c1a56641d452a6f10d980e099cb1295dd01fc"),),
    ),
    (
        "future_native_authority_registry",
        (("expected_sha256", "b1aa5401cd61d38fc1a776e0eab947ae87cde6a280adf1777ad7c126de35bb58"),),
    ),
    (
        "future_reference_authority_registry",
        (("expected_sha256", "27644e20a4360076cad3a711ca7c379c565cfa3a807bf60c985c11e1272e7c91"),),
    ),
    (
        "future_child_authority_registry",
        (("expected_sha256", "c8e1d79650d79a463d8eedfc1607088fe01384ba3e86757fabba7180de4a51ed"),),
    ),
    (
        "sql_authority_registry",
        (("expected_sha256", "2f4a56ad527e96bcf5072d3f04dc27a457ae9d0e93ffe7aa31b679d561a82bf8"),),
    ),
    (
        "erasure_execution_registry",
        (("expected_sha256", "ebd3550aa9ff7e99636be51be45b12e501f35528b978c08b5b886a11c247da7d"),),
    ),
    (
        "provider_registry",
        (("expected_sha256", "dbc2c3a42cbe336a7b8f237c46e90f87ca92e52a0ab37ff958564042b8766f4b"),),
    ),
    (
        "rights_index_policy_registry",
        (("expected_sha256", "ad041ac654596094915408f0448220284ff63cb3d57df0042b0d02769f5cb28e"),),
    ),
    (
        "role_registry",
        (("expected_sha256", "e24fe48de85c035a09ae037fbdce7e24f301ea90dd64922d4d57600933419f49"),),
    ),
    (
        "deliverable_registry",
        (("expected_sha256", "50e3716d51f18aca3ab1d676e2fb3313027039604603ff47ea0fa10ab2c6eff4"),),
    ),
    (
        "producer_selector_registry",
        (
            ("expected_sha256", "771bf3552c9132d575464c7e2fd2c9960ed50c2fc74be8171a0c0f7ecfdbce6b"),
            (
                "expected_combined_sha256",
                "18785726ff8420e347495bf403adf3a61fdae59b64a61e4083beae2818858a47",
            ),
        ),
    ),
    (
        "consumer_selector_registry",
        (("expected_sha256", "2be86672d118a142870b727985994a9e908d5cafe13da2489c007bed94826806"),),
    ),
    (
        "applicability_registry",
        (("expected_sha256", "bc1b042e0af957761491f293458f038247a9939302bb6a84c1957259554d0555"),),
    ),
    (
        "edge_rule_registry",
        (
            (
                "expected_manifest_sha256",
                "5fc0263dea7b9ae92a27f36ebdbbb2201e52da08bc8940b4ad5a15fd8ddf378b",
            ),
        ),
    ),
    (
        "usage_route_registry",
        (("expected_sha256", "4c31e9f0e12d22748318a628fec9dc3358c2cc675ee90b116643a5f6b730f077"),),
    ),
    (
        "usage_extractor_registry",
        (("expected_sha256", "4bd0e3241c48582789138a2e5d7179a8ff21f2b6af138bb4bf0690f7eafbfdb3"),),
    ),
    (
        "erasure_baseline",
        (("expected_sha256", "b8d41d1c967252f69ea162ca2d5e2ad8fdd19a240bdc659f6dadaff86fd30033"),),
    ),
    (
        "erasure_materialized",
        (("expected_sha256", "87cf4253d09f722ede64de549d50ba16555cd72239970ea4479c4a85d9275ca2"),),
    ),
)


def _inactive_v28_steps() -> tuple[PipelineStepSpec, ...]:
    return tuple(
        PipelineStepSpec(
            step_order=order,
            step_name=name,
            semantic_rule_version="2",
            semantic_rule_hash=rule_hash,
            auxiliary=False,
        )
        for order, name, rule_hash in _INACTIVE_V28_STEP_ROWS
    )


def inactive_full_strict_v28_contract() -> PipelineContractSpec:
    """Return the reviewed inactive full-strict-v28 base contract."""
    return PipelineContractSpec(
        contract_version=FULL_STRICT_V28_CONTRACT_VERSION,
        steps=_inactive_v28_steps(),
        state=PipelineContractState.INACTIVE,
        contract_hash=FULL_STRICT_V28_BASE_CONTRACT_HASH,
        deterministic_step_ids=True,
        required_cardinality=28,
        edge_manifest_hash=_EDGE_MANIFEST_SHA256,
        future_authority_hash=_CURRENT_DDL_REGISTRY_SHA256,
    )


@dataclass(frozen=True, slots=True)
class InactiveStageRuntimeBinding:
    """Immutable explicit inactive v28 binding plus provenance pins.

    ``governed_successor_contract_hash`` stays ``None``: the final governed
    contract hash is unavailable and no substitute value is bound here.
    """

    base_candidate_contract: PipelineContractSpec
    authority_amendment_proposal_sha256: str
    literal_vectors_sha256: str
    base_component_identities: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    governed_successor_contract_hash: str | None

    def require_governed_successor_contract(self) -> str:
        """Fail closed: the final governed successor hash is unavailable."""
        validate_inactive_stage_runtime_binding(self)
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            "governed successor contract hash is unavailable",
        )


def inactive_stage_runtime_binding() -> InactiveStageRuntimeBinding:
    """Return the fixed inactive v28 binding and its provenance pins."""
    return InactiveStageRuntimeBinding(
        base_candidate_contract=inactive_full_strict_v28_contract(),
        authority_amendment_proposal_sha256=AUTHORITY_AMENDMENT02_PROPOSAL_SHA256,
        literal_vectors_sha256=AUTHORITY_AMENDMENT02_VECTORS_SHA256,
        base_component_identities=_BASE_COMPONENT_IDENTITIES,
        governed_successor_contract_hash=None,
    )


def _require_binding_member(value: object) -> None:
    if type(value) is not str or not value:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 binding member is not a non-empty string",
        )


def _require_step_row(step: object, ordinal: int, names: set[str]) -> None:
    def _refuse() -> None:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 step rows differ from the reviewed contract",
        )

    if type(step) is not PipelineStepSpec:
        _refuse()
        return
    if type(step.step_order) is not int or step.step_order != ordinal:
        _refuse()
    if type(step.step_name) is not str or not step.step_name or step.step_name in names:
        _refuse()
    names.add(step.step_name)
    if type(step.semantic_rule_version) is not str or not step.semantic_rule_version:
        _refuse()
    if (
        type(step.semantic_rule_hash) is not str
        or _SHA256_RE.fullmatch(step.semantic_rule_hash) is None
    ):
        _refuse()
    if type(step.auxiliary) is not bool or step.auxiliary:
        _refuse()


def validate_inactive_stage_runtime_binding(
    binding: object,
) -> InactiveStageRuntimeBinding:
    """Require the exact reviewed inactive v28 binding; refuse any mutation."""
    if type(binding) is not InactiveStageRuntimeBinding:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 runtime binding differs from the reviewed prerequisite",
        )
    spec = binding.base_candidate_contract
    if type(spec) is not PipelineContractSpec:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 runtime binding differs from the reviewed prerequisite",
        )
    _require_binding_member(spec.contract_version)
    if spec.contract_version != FULL_STRICT_V28_CONTRACT_VERSION:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 runtime binding differs from the reviewed prerequisite",
        )
    _require_binding_member(spec.contract_hash)
    if spec.contract_hash != FULL_STRICT_V28_BASE_CONTRACT_HASH:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 runtime binding differs from the reviewed prerequisite",
        )
    if (
        spec.state is not PipelineContractState.INACTIVE
        or type(spec.deterministic_step_ids) is not bool
        or spec.deterministic_step_ids is not True
        or type(spec.required_cardinality) is not int
        or spec.required_cardinality != 28
        or spec.edge_manifest_hash != _EDGE_MANIFEST_SHA256
        or spec.future_authority_hash != _CURRENT_DDL_REGISTRY_SHA256
    ):
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 runtime binding differs from the reviewed prerequisite",
        )
    if (
        binding.authority_amendment_proposal_sha256 != AUTHORITY_AMENDMENT02_PROPOSAL_SHA256
        or binding.literal_vectors_sha256 != AUTHORITY_AMENDMENT02_VECTORS_SHA256
        or binding.base_component_identities != _BASE_COMPONENT_IDENTITIES
        or binding.governed_successor_contract_hash is not None
    ):
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 binding provenance differs from the reviewed prerequisite",
        )
    steps = spec.steps
    if type(steps) is not tuple or len(steps) != 28:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 step rows differ from the reviewed contract",
        )
    names: set[str] = set()
    for ordinal, step in enumerate(steps):
        _require_step_row(step, ordinal, names)
    validate_admission_step_rows(
        contract=inactive_full_strict_v28_contract(),
        rows=steps,
    )
    return binding


@dataclass(frozen=True, slots=True)
class StageAdmissionInputIdentity:
    """Flat Task 7 stage-input identity preimage and its digest."""

    preimage: tuple[str, str, str, str, str]
    canonical_bytes: bytes
    identity_sha256: str


def stage_admission_input_identity(
    admission: object,
) -> StageAdmissionInputIdentity:
    """Project the flat Task 7 stage-input identity from a genuine admission."""
    if type(admission) is not RunStageAdmissionV1:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    preimage = (
        _STAGE_INPUT_IDENTITY_DOMAIN,
        admission.tenant_id,
        admission.deal_id,
        admission.run_id,
        admission.canonical_sha256(),
    )
    canonical = _canonical_json_bytes(list(preimage))
    return StageAdmissionInputIdentity(
        preimage=preimage,
        canonical_bytes=canonical,
        identity_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _stage_binding_value_fields() -> tuple[str, ...]:
    registry = task7_v28_candidate_namespace_registry(
        candidate_contract_version=FULL_STRICT_V28_CONTRACT_VERSION,
    )
    for spec in registry:
        if spec.id_kind == _STAGE_BINDING_ID_KIND:
            return spec.name_tuple_schema[1:]
    raise StageAdmissionError(STAGE_ADMISSION_INVALID)


_STAGE_CARRIER_VALUE_FIELDS: Final = _stage_binding_value_fields()


def _stage_carrier_values(admission: RunStageAdmissionV1) -> dict[str, object]:
    identity = stage_admission_input_identity(admission)
    return {
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "pipeline_contract_version": admission.pipeline_contract_version,
        "pipeline_contract_hash": admission.pipeline_contract_hash,
        "corpus_receipt_id": None,
        "source_lineage_object_id": None,
        "source_object_kind": _STAGE_SOURCE_OBJECT_KIND,
        "source_disposition": _STAGE_SOURCE_DISPOSITION,
        "binding_purpose": _STAGE_BINDING_PURPOSE,
        "selected_source_version": _STAGE_SOURCE_VERSION,
        "selected_identity_sha256": identity.identity_sha256,
        "selected_content_sha256": admission.canonical_sha256(),
        "corpus_receipt_sha256": None,
    }


def stage_admission_binding_uuid(
    *,
    binding: InactiveStageRuntimeBinding,
    admission: RunStageAdmissionV1,
) -> Task7DerivedUuid:
    """Derive the stage-arm carrier UUID under the inactive v28 context."""
    checked = validate_inactive_stage_runtime_binding(binding)
    spec = checked.base_candidate_contract
    if type(admission) is not RunStageAdmissionV1:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    contract_hash = spec.contract_hash
    if type(contract_hash) is not str:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "inactive v28 runtime binding differs from the reviewed prerequisite",
        )
    if (
        admission.pipeline_contract_version != spec.contract_version
        or admission.pipeline_contract_hash != contract_hash
    ):
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "stage admission contract differs from the inactive v28 context",
        )
    return derive_task7_v28_candidate_uuid(
        candidate_contract_version=spec.contract_version,
        candidate_contract_hash=contract_hash,
        id_kind=_STAGE_BINDING_ID_KIND,
        values=_stage_carrier_values(admission),
    )


_STAGE_CARRIER_PROJECTION_FIELDS: Final[tuple[str, ...]] = (
    "binding_id",
    "tenant_id",
    "deal_id",
    "run_id",
    "source_lineage_object_id",
    "source_object_kind",
    "source_disposition",
    "binding_purpose",
    "corpus_receipt_id",
    "selected_source_version",
    "selected_identity_sha256",
    "selected_content_sha256",
    "corpus_receipt_sha256",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "binding_sha256",
    "stage_admission_sha256",
)


def stage_admission_binding_projection(
    *,
    admission: RunStageAdmissionV1,
    binding_uuid: Task7DerivedUuid,
) -> Mapping[str, str | None]:
    """Project the 17-member stage-arm carrier row for scoped comparison."""
    if type(admission) is not RunStageAdmissionV1:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    if type(binding_uuid) is not Task7DerivedUuid or binding_uuid.id_kind != _STAGE_BINDING_ID_KIND:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    values = _stage_carrier_values(admission)
    identity = stage_admission_input_identity(admission)
    admission_sha256 = admission.canonical_sha256()
    binding_sha256 = hashlib.sha256(
        _canonical_json_bytes(
            [
                _STAGE_CARRIER_DIGEST_DOMAIN,
                *(values[field] for field in _STAGE_CARRIER_VALUE_FIELDS),
            ],
        ),
    ).hexdigest()
    return MappingProxyType(
        {
            "binding_id": str(binding_uuid.value),
            "tenant_id": admission.tenant_id,
            "deal_id": admission.deal_id,
            "run_id": admission.run_id,
            "source_lineage_object_id": None,
            "source_object_kind": _STAGE_SOURCE_OBJECT_KIND,
            "source_disposition": _STAGE_SOURCE_DISPOSITION,
            "binding_purpose": _STAGE_BINDING_PURPOSE,
            "corpus_receipt_id": None,
            "selected_source_version": _STAGE_SOURCE_VERSION,
            "selected_identity_sha256": identity.identity_sha256,
            "selected_content_sha256": admission_sha256,
            "corpus_receipt_sha256": None,
            "pipeline_contract_version": admission.pipeline_contract_version,
            "pipeline_contract_hash": admission.pipeline_contract_hash,
            "binding_sha256": binding_sha256,
            "stage_admission_sha256": admission_sha256,
        },
    )
