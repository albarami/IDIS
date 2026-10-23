"""Immutable pipeline-contract registry and strict-v21 admission gate.

Frozen ``full-strict-v16``, ``full-strict-v17``, ``full-strict-v18``, and
``full-strict-v20`` remain immutable historical evidence, but all are
superseded and can never be selected for admission.  The reviewed
``full-strict-v21`` artifact is never read at runtime; the values below are
code-owned copies of its admitted fixture.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from idis.models.run_step import FULL_STEPS, SNAPSHOT_STEPS, StepName

PIPELINE_CONTRACT_UNAVAILABLE: Final = "PIPELINE_CONTRACT_UNAVAILABLE"
PIPELINE_CONTRACT_HASH_MISMATCH: Final = "PIPELINE_CONTRACT_HASH_MISMATCH"

FULL_STRICT_V16_CONTRACT_HASH: Final = (
    "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
)
FULL_STRICT_V16_ARTIFACT_SHA256: Final = (
    "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
)
FULL_STRICT_V16_FIXTURE_SHA256: Final = (
    "999dafef745feaa56448e0a84ebed3aff37729c3d85f98267ae72a19d702ab79"
)
_FULL_STRICT_V16_VERSION: Final = "full-strict-v16"
FULL_STRICT_V17_CONTRACT_HASH: Final = (
    "1e01b33daebb1d619808a3d5388cacfad605cddc9f9cefcedae2d0fb75e0102e"
)
FULL_STRICT_V17_ARTIFACT_SHA256: Final = (
    "d6218f7a48f2170099b461ee152410852de9e0f29cfe78fb71627b968c1465e1"
)
FULL_STRICT_V17_FIXTURE_SHA256: Final = (
    "d7539e693ee92831644f2e230054816a2cfcad77e706d5652460df0cc47c341b"
)
_FULL_STRICT_V17_VERSION: Final = "full-strict-v17"
FULL_STRICT_V18_CONTRACT_HASH: Final = (
    "ad8e0a8fd012af34f34d8fd4b66643d2d31a8a4f436dd3a8021af9a188da378c"
)
FULL_STRICT_V18_ARTIFACT_SHA256: Final = (
    "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
)
FULL_STRICT_V18_FIXTURE_SHA256: Final = (
    "9efef72429745ac8ccce91f4aea541a1bb958638e83bf08c9eccbc5eabd03a0c"
)
_FULL_STRICT_V18_VERSION: Final = "full-strict-v18"
FULL_STRICT_V20_CONTRACT_HASH: Final = (
    "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"
)
FULL_STRICT_V20_ARTIFACT_SHA256: Final = (
    "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
)
FULL_STRICT_V20_FIXTURE_SHA256: Final = (
    "b8ba48b067a52ab5015ae1edca4943597670e1e2b828ccb640fccbda1b1207e3"
)
_FULL_STRICT_V20_VERSION: Final = "full-strict-v20"
FULL_STRICT_V21_CONTRACT_HASH: Final = (
    "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
)
FULL_STRICT_V21_ARTIFACT_SHA256: Final = (
    "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
)
FULL_STRICT_V21_FIXTURE_SHA256: Final = (
    "da79338f41e0555d1304c162e249246086db26b20421626dd9c0d51690fd7773"
)
_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_SUPERSEDED_CONTRACT_VERSIONS: Final = frozenset(
    {
        _FULL_STRICT_V16_VERSION,
        _FULL_STRICT_V17_VERSION,
        _FULL_STRICT_V18_VERSION,
        _FULL_STRICT_V20_VERSION,
    }
)
_LEGACY_SEMANTIC_VERSION: Final = "legacy-step-semantic-v1"
_LEGACY_SEMANTIC_HASH: Final = "4e743bd1264e407e40e48404a6ba081c111b1426bb798b09fb471085b98a18af"


class PipelineContractError(RuntimeError):
    """Stable fail-closed pipeline-contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PipelineContractState(StrEnum):
    """Admission state of a code-owned contract."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True, slots=True)
class PipelineStepSpec:
    """One canonical step in a versioned pipeline contract."""

    step_order: int
    step_name: str
    semantic_rule_version: str
    semantic_rule_hash: str
    auxiliary: bool = False


@dataclass(frozen=True, slots=True)
class PipelineContractSpec:
    """Immutable code-owned pipeline contract."""

    contract_version: str
    steps: tuple[PipelineStepSpec, ...]
    state: PipelineContractState
    contract_hash: str | None
    deterministic_step_ids: bool
    required_cardinality: int
    edge_manifest_hash: str | None = None
    future_authority_hash: str | None = None

    @property
    def version(self) -> str:
        """Return the registry key represented by this contract."""

        return self.contract_version


_STRICT_STEP_ROWS: Final[tuple[tuple[int, str, str], ...]] = (
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
    (
        2,
        "INGEST_CHECK",
        "6634007f7e2d2ac83631d9bb386450de73bd30ca1e8ee5529de10cba9994df03",
    ),
    (
        3,
        "DOCUMENT_PREFLIGHT",
        "af64299ed8f2cdac1c9cf2e1fa7ce8f585df6d8dbe9dfd8980dcdc238b16e7a7",
    ),
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
    (
        12,
        "EXTRACT",
        "ba078b98792baa4f5656ebdd21659a02f659a8e666a6f53b2e094c6c666c065f",
    ),
    (
        13,
        "GRADE",
        "c24aa9f98b6a0ff2d405cfe916bdcf3fe3082e20685d671bfbf5b62853060e35",
    ),
    (
        14,
        "CALC",
        "8158acc63c864ef44cfc4ba7e622268e603ccad36f77729f3e09483ecf3e17ef",
    ),
    (
        15,
        "METHODOLOGY_COMPANY_IDENTITY_PACKAGE",
        "3c591896fcae85db3d541beed32442a3824dcbd7df7568794e35181cb61c030e",
    ),
    (
        16,
        "ENRICHMENT",
        "0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6",
    ),
    (
        17,
        "GRAPH_EVIDENCE",
        "11d0a5a8557433a21994ac042a5d248ec98b1310c4374df66d3ebb1705262298",
    ),
    (
        18,
        "RAG_EVIDENCE",
        "de1aa5c89992b1eec574448a6f857d419782126544b556ae177f8fa8356508f9",
    ),
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
    (
        22,
        "DEBATE",
        "ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83",
    ),
    (
        23,
        "ANALYSIS",
        "aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa",
    ),
    (
        24,
        "SCORING",
        "0c9c74af82ccd30eb683a40aedbef32c1877618a415d3e330a08039029045177",
    ),
    (
        25,
        "METHODOLOGY_LAYER2_READINESS_PACKAGE",
        "49930fbde3c063c9b02a3501506fa6364d482857635745be255ce186206dd69c",
    ),
    (
        26,
        "LAYER2_IC_CHALLENGE",
        "3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e",
    ),
    (
        27,
        "DELIVERABLES",
        "672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d",
    ),
)

_FULL_STRICT_V16_STEPS: Final = tuple(
    PipelineStepSpec(
        step_order=step_order,
        step_name=step_name,
        semantic_rule_version="2",
        semantic_rule_hash=semantic_rule_hash,
    )
    for step_order, step_name, semantic_rule_hash in _STRICT_STEP_ROWS
)


def _legacy_steps(steps: Sequence[StepName]) -> tuple[PipelineStepSpec, ...]:
    return tuple(
        PipelineStepSpec(
            step_order=step_order,
            step_name=step_name.value,
            semantic_rule_version=_LEGACY_SEMANTIC_VERSION,
            semantic_rule_hash=_LEGACY_SEMANTIC_HASH,
        )
        for step_order, step_name in enumerate(steps)
    )


_FULL_STRICT_V16_SPEC: Final = PipelineContractSpec(
    contract_version=_FULL_STRICT_V16_VERSION,
    steps=_FULL_STRICT_V16_STEPS,
    state=PipelineContractState.INACTIVE,
    contract_hash=FULL_STRICT_V16_CONTRACT_HASH,
    deterministic_step_ids=True,
    required_cardinality=28,
    edge_manifest_hash="bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014",
    future_authority_hash="073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd",
)

_FULL_STRICT_V17_SPEC: Final = PipelineContractSpec(
    contract_version=_FULL_STRICT_V17_VERSION,
    steps=_FULL_STRICT_V16_STEPS,
    state=PipelineContractState.INACTIVE,
    contract_hash=FULL_STRICT_V17_CONTRACT_HASH,
    deterministic_step_ids=True,
    required_cardinality=28,
    edge_manifest_hash="bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014",
    future_authority_hash="073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd",
)

_FULL_STRICT_V18_SPEC: Final = PipelineContractSpec(
    contract_version=_FULL_STRICT_V18_VERSION,
    steps=_FULL_STRICT_V16_STEPS,
    state=PipelineContractState.INACTIVE,
    contract_hash=FULL_STRICT_V18_CONTRACT_HASH,
    deterministic_step_ids=True,
    required_cardinality=28,
    edge_manifest_hash="bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014",
    future_authority_hash="073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd",
)

_FULL_STRICT_V20_SPEC: Final = PipelineContractSpec(
    contract_version=_FULL_STRICT_V20_VERSION,
    steps=_FULL_STRICT_V16_STEPS,
    state=PipelineContractState.INACTIVE,
    contract_hash=FULL_STRICT_V20_CONTRACT_HASH,
    deterministic_step_ids=True,
    required_cardinality=28,
    edge_manifest_hash="bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014",
    future_authority_hash="073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd",
)

_FULL_STRICT_V21_SPEC: Final = PipelineContractSpec(
    contract_version=_FULL_STRICT_V21_VERSION,
    steps=_FULL_STRICT_V16_STEPS,
    state=PipelineContractState.INACTIVE,
    contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
    deterministic_step_ids=True,
    required_cardinality=28,
    edge_manifest_hash="bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014",
    future_authority_hash="073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd",
)

PIPELINE_CONTRACTS: Final[Mapping[str, PipelineContractSpec]] = MappingProxyType(
    {
        "snapshot-v1": PipelineContractSpec(
            contract_version="snapshot-v1",
            steps=_legacy_steps(SNAPSHOT_STEPS),
            state=PipelineContractState.ACTIVE,
            contract_hash=None,
            deterministic_step_ids=False,
            required_cardinality=8,
        ),
        "full-v1": PipelineContractSpec(
            contract_version="full-v1",
            steps=_legacy_steps(FULL_STEPS),
            state=PipelineContractState.ACTIVE,
            contract_hash=None,
            deterministic_step_ids=False,
            required_cardinality=28,
        ),
        _FULL_STRICT_V16_VERSION: _FULL_STRICT_V16_SPEC,
        _FULL_STRICT_V17_VERSION: _FULL_STRICT_V17_SPEC,
        _FULL_STRICT_V18_VERSION: _FULL_STRICT_V18_SPEC,
        _FULL_STRICT_V20_VERSION: _FULL_STRICT_V20_SPEC,
        _FULL_STRICT_V21_VERSION: _FULL_STRICT_V21_SPEC,
    }
)

_COMPONENT_HASH_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    (
        "applicability_registry_hash",
        "bc1b042e0af957761491f293458f038247a9939302bb6a84c1957259554d0555",
    ),
    (
        "baseline_erasure_schedule_hash",
        "b8d41d1c967252f69ea162ca2d5e2ad8fdd19a240bdc659f6dadaff86fd30033",
    ),
    (
        "baseline_parent_authority_registry_hash",
        "3269dd9af23b720a63c90568e30c1a56641d452a6f10d980e099cb1295dd01fc",
    ),
    (
        "consumer_selector_registry_hash",
        "c1ee5fec499eb759776fd6da7be6445de0282d3fedddf3d3e69bfb9c6174917e",
    ),
    (
        "deliverable_registry_hash",
        "50e3716d51f18aca3ab1d676e2fb3313027039604603ff47ea0fa10ab2c6eff4",
    ),
    (
        "edge_manifest_hash",
        "bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014",
    ),
    (
        "erasure_execution_registry_hash",
        "bc919d96e9b9e568c6bd1432c06515109ae37dc14995b8bdabee1a543a859635",
    ),
    (
        "future_child_authority_registry_hash",
        "c8e1d79650d79a463d8eedfc1607088fe01384ba3e86757fabba7180de4a51ed",
    ),
    (
        "future_native_authority_registry_hash",
        "4574d90bc8f397bdb9a667b05a5d9d80e00788c790d4988d675aca782b08e904",
    ),
    (
        "future_reference_authority_registry_hash",
        "a93c20010a2ddd9295f0e608aa79b63b2c7e6fcf9398512732326259db636c49",
    ),
    ("pipeline_contract_hash", FULL_STRICT_V16_CONTRACT_HASH),
    (
        "producer_selector_registry_hash",
        "3b23e5bfdee6fe78762412eec2e37430559f00833a698b17c14809876ca2d4f4",
    ),
    (
        "provider_registry_hash",
        "dbc2c3a42cbe336a7b8f237c46e90f87ca92e52a0ab37ff958564042b8766f4b",
    ),
    (
        "reviewed_preimage_artifact_hash",
        "3ff61f6159db272b93f56024d77cf6028c65aaafafa1c39d01702f299cf619d2",
    ),
    (
        "rights_index_policy_registry_hash",
        "ad041ac654596094915408f0448220284ff63cb3d57df0042b0d02769f5cb28e",
    ),
    (
        "role_registry_hash",
        "e24fe48de85c035a09ae037fbdce7e24f301ea90dd64922d4d57600933419f49",
    ),
    (
        "schema_registry_hash",
        "811649e787cade6c36916b4901600522179d580434df4dab5da75ba10de61948",
    ),
    (
        "selector_registry_hash",
        "6790e8934378503acf48667f9fcc9b68780cc5c2e6cd640e4e143ae6fde5ec6a",
    ),
    (
        "semantic_step_vector_hash",
        "ee473f158a9e48dff845c7e498e8bd3eadde20ea68eeb698e7c3482c158182f6",
    ),
    (
        "sql_authority_registry_hash",
        "408e0491e2df00a8badf9c9d4d2db0baaa7da9f7478ed6a59f1a90f5a3d95bad",
    ),
    (
        "task7_ddl_registry_hash",
        "073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd",
    ),
    (
        "task7_erasure_schedule_hash",
        "87cf4253d09f722ede64de549d50ba16555cd72239970ea4479c4a85d9275ca2",
    ),
    (
        "usage_extractor_registry_hash",
        "e6542bb692bb4819712bc95bfdd23c2f66f7fd21fa15e0b7881057ff81eeeccc",
    ),
    (
        "usage_path_route_hash",
        "96cbe37825fca73179b8077d7a81fb235aaf057aff405342bac18b7cf365c616",
    ),
    (
        "uuid_namespace_registry_hash",
        "28899dde2a2e0626cb4594e70dbff619d21de25210a288f335c815a861f84198",
    ),
)

_SECTION_HASHES: Final[tuple[tuple[int, str, str], ...]] = (
    (0, "metadata", "87073381c1dfbe4fc3bc5c3b761c98f03e99c4524e33c56dd4fcf6a76c6adad2"),
    (
        1,
        "canonicalization_contract",
        "c610c8b3bf4b8e19ffdf4db4230a5351271e822f316fd1897d13d29835040d6c",
    ),
    (
        2,
        "tuple_schema_registry",
        "feee55d0eeeeb769d4fe7eb657b676b22b04cc608188af4e2f0f949d05c0d8d0",
    ),
    (
        3,
        "digest_recipe_registry",
        "f4e84ef05830ba8de11a97dee944e6d6ff3e0f82388e8564935c66c961923c5a",
    ),
    (
        4,
        "uuid_namespace_registry",
        "5dee7847237118201608ffd4aef6a99c2a7778d8154505990759ad138af5627f",
    ),
    (
        5,
        "schema_registry",
        "2d7d48e246a45be060904163392101ad12dd96fb93d0f8dc9ea38eb50dcf84bb",
    ),
    (
        6,
        "semantic_step_registry",
        "c3f88439d006dbf86bd53e93be509f01f65c255574993d65fcba467a516f68f0",
    ),
    (
        7,
        "current_ddl_registry",
        "68df8944cd9bdda6294832675ec08bf98c38f1a2a1189758cc2f6b7d31b3be5b",
    ),
    (
        8,
        "baseline_parent_authority_registry",
        "36320b2bdd6ffe165a8af2ff6da8ca9a7884f634a60aaabc9a85ab7fa05ad792",
    ),
    (
        9,
        "future_native_authority_registry",
        "b76bcdb35e31e94b16ce536d8dde88fea94939c6649d893c18a679626fbc9d91",
    ),
    (
        10,
        "future_reference_authority_registry",
        "c11abd81be74988d26cdee57bbe8fca2e5665ba7e1fee05833c17ea068d5e245",
    ),
    (
        11,
        "future_child_authority_registry",
        "7d6087b28aa28ad3fbaadb40a74c42a998d78839cb01fff887c888f6ed047dbd",
    ),
    (
        12,
        "sql_authority_registry",
        "5b27a4b93815a86fce1659d3a4c2f1188c624604169907983fdb90cb85b3c7d6",
    ),
    (
        13,
        "erasure_execution_registry",
        "278dfae809445df7e21c8cea5fc640d86f2ee88cfd71e191a7d996cc626357f6",
    ),
    (
        14,
        "provider_registry",
        "43371023cdc4b39dd60d45a4d38d3100cc7fab1ef73b7cdca9462fb046eda086",
    ),
    (
        15,
        "rights_index_policy_registry",
        "9c3b759b3f7d1b7ccca4568de18c12dba754214675154bc0b97f5fa598106d39",
    ),
    (
        16,
        "role_registry",
        "0e11e8758dda0846d9dfe8db6a67f00ec30a0cd146d5ba9ccf3567376ebc0934",
    ),
    (
        17,
        "deliverable_registry",
        "8e058e325eef60fa89639dcb898a003ba42d69335eac2d0d9ed97e985e092a75",
    ),
    (
        18,
        "producer_selector_registry",
        "bf87e29d1560fa7355131afae0443c48e93b59982259f7b3e10dac659a8011a2",
    ),
    (
        19,
        "consumer_selector_registry",
        "679b68ecec2d8cc645d966c2b6f57920a994c2f17a13c9d87c61353befb3082b",
    ),
    (
        20,
        "applicability_registry",
        "b480564d5c75cb7a48ab00eea42eeaa89e2bc17f4fe5a9443236a0d3623dabb6",
    ),
    (
        21,
        "edge_rule_registry",
        "9d8acc4ef18a4e928a7108ce84dbe1ae30599fa0a56054d8b7880f9a8d985884",
    ),
    (
        22,
        "usage_route_registry",
        "c97e0903e546cfe800520a25f40c11556f5bd25aa9d88e23a6c0279b4e152899",
    ),
    (
        23,
        "usage_extractor_registry",
        "78c515afec20a0b790a48a90fbbbd9c71a5c9db523599d9912abc6b8b0c65176",
    ),
    (
        24,
        "erasure_baseline",
        "067532baa954e12abbe630ebf1f260a737048da6d4776fb2a5cae5485618d44d",
    ),
    (
        25,
        "erasure_materialized",
        "60a385781e3eeac0b1dfd505632975e9529a7f8f7120246fc99ea01f7537cfa4",
    ),
    (
        26,
        "activation_assertions",
        "b5607834108cae3655b9de1e9439cfc338964739d05faca4116eca2d0981d6ce",
    ),
)

_AUTHORITY_ROW_COUNTS: Final[tuple[tuple[str, int], ...]] = (
    ("current_ddl_registry", 34),
    ("baseline_parent_authority_registry", 4),
    ("future_native_authority_registry", 18),
    ("future_reference_authority_registry", 15),
    ("future_child_authority_registry", 5),
    ("semantic_step_registry", 28),
    ("schema_registry", 62),
    ("producer_selector_registry", 48),
    ("consumer_selector_registry", 48),
    ("edge_rule_registry", 48),
    ("usage_route_registry", 1_756),
)
_REQUIRED_AUTHORITY_SECTIONS: Final = frozenset(
    {
        "current_ddl_registry",
        "baseline_parent_authority_registry",
        "future_native_authority_registry",
        "future_reference_authority_registry",
        "future_child_authority_registry",
    }
)


@dataclass(frozen=True, slots=True)
class PipelineActivationCensus:
    """Immutable activation census compared with the reviewed v16 authority."""

    contract_version: str
    contract_hash: str
    artifact_sha256: str
    fixture_sha256: str
    component_hashes: Mapping[str, str]
    section_hashes: tuple[tuple[int, str, str], ...]
    semantic_step_count: int
    edge_rule_count: int
    usage_route_count: int
    usage_extractor_count: int
    current_ddl_table_count: int
    future_native_table_count: int
    future_reference_table_count: int
    future_child_table_count: int
    _authority_row_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_hashes",
            MappingProxyType(dict(self.component_hashes)),
        )
        object.__setattr__(self, "section_hashes", tuple(self.section_hashes))
        object.__setattr__(self, "_authority_row_counts", tuple(self._authority_row_counts))

    def without_authority(
        self,
        *,
        section_name: str,
        row_ordinal: int,
    ) -> PipelineActivationCensus:
        """Return a census representing one absent authority row."""

        counts = dict(self._authority_row_counts)
        _validate_authority_coordinate(counts, section_name, row_ordinal)
        counts[section_name] -= 1
        ordered_counts = tuple((name, counts[name]) for name, _ in self._authority_row_counts)
        return replace(self, _authority_row_counts=ordered_counts)

    def with_replaced_authority_byte(
        self,
        *,
        section_name: str,
        row_ordinal: int,
        byte_ordinal: int,
        replacement: int,
    ) -> PipelineActivationCensus:
        """Return a census representing a present row with one changed byte."""

        counts = dict(self._authority_row_counts)
        _validate_authority_coordinate(counts, section_name, row_ordinal)
        if byte_ordinal < 0:
            raise ValueError("byte_ordinal must be non-negative")
        if not 0 <= replacement <= 0xFF:
            raise ValueError("replacement must be one byte")

        changed: list[tuple[int, str, str]] = []
        for ordinal, name, section_hash in self.section_hashes:
            if name == section_name:
                drift_preimage = json.dumps(
                    [section_hash, row_ordinal, byte_ordinal, replacement],
                    separators=(",", ":"),
                ).encode("ascii")
                section_hash = hashlib.sha256(drift_preimage).hexdigest()
            changed.append((ordinal, name, section_hash))
        return replace(self, section_hashes=tuple(changed))


def _validate_authority_coordinate(
    counts: Mapping[str, int],
    section_name: str,
    row_ordinal: int,
) -> None:
    try:
        count = counts[section_name]
    except KeyError as exc:
        raise ValueError(f"unknown authority section: {section_name}") from exc
    if not 0 <= row_ordinal < count:
        raise ValueError(f"row ordinal outside {section_name}: {row_ordinal}")


FULL_STRICT_V16_ACTIVATION_CENSUS_V1: Final = PipelineActivationCensus(
    contract_version=_FULL_STRICT_V16_VERSION,
    contract_hash=FULL_STRICT_V16_CONTRACT_HASH,
    artifact_sha256=FULL_STRICT_V16_ARTIFACT_SHA256,
    fixture_sha256=FULL_STRICT_V16_FIXTURE_SHA256,
    component_hashes=MappingProxyType(dict(_COMPONENT_HASH_PAIRS)),
    section_hashes=_SECTION_HASHES,
    semantic_step_count=28,
    edge_rule_count=48,
    usage_route_count=1_756,
    usage_extractor_count=19,
    current_ddl_table_count=34,
    future_native_table_count=18,
    future_reference_table_count=15,
    future_child_table_count=5,
    _authority_row_counts=_AUTHORITY_ROW_COUNTS,
)


@dataclass(frozen=True, slots=True)
class PipelineActivationReceipt:
    """Proof that an exact reviewed activation census was verified."""

    contract_version: str
    contract_hash: str
    artifact_sha256: str
    fixture_sha256: str
    census_sha256: str


def _census_sha256(census: PipelineActivationCensus) -> str:
    payload = [
        "pipeline-activation-census-v1",
        census.contract_version,
        census.contract_hash,
        census.artifact_sha256,
        census.fixture_sha256,
        list(census.component_hashes.items()),
        census.section_hashes,
        census.semantic_step_count,
        census.edge_rule_count,
        census.usage_route_count,
        census.usage_extractor_count,
        census.current_ddl_table_count,
        census.future_native_table_count,
        census.future_reference_table_count,
        census.future_child_table_count,
        census._authority_row_counts,
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _activation_receipt(census: PipelineActivationCensus) -> PipelineActivationReceipt:
    return PipelineActivationReceipt(
        contract_version=census.contract_version,
        contract_hash=census.contract_hash,
        artifact_sha256=census.artifact_sha256,
        fixture_sha256=census.fixture_sha256,
        census_sha256=_census_sha256(census),
    )


def verify_full_strict_v16_activation_census(
    actual: PipelineActivationCensus,
) -> PipelineActivationReceipt:
    """Verify availability and exact bytes before a separate activation review."""

    expected = FULL_STRICT_V16_ACTIVATION_CENSUS_V1
    if not isinstance(actual, PipelineActivationCensus):
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            "full-strict-v16 activation census is unavailable",
        )
    if actual.contract_version != expected.contract_version:
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            "full-strict-v16 activation census has an unavailable contract version",
        )

    actual_sections = {name for _, name, _ in actual.section_hashes}
    if not _REQUIRED_AUTHORITY_SECTIONS.issubset(actual_sections):
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            "full-strict-v16 activation census is missing a required authority section",
        )

    expected_counts = dict(expected._authority_row_counts)
    actual_counts = dict(actual._authority_row_counts)
    if any(
        name not in actual_counts or actual_counts[name] < required
        for name, required in expected_counts.items()
    ):
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            "full-strict-v16 activation census is missing required authority rows",
        )
    required_table_counts = (
        (actual.current_ddl_table_count, expected.current_ddl_table_count),
        (actual.future_native_table_count, expected.future_native_table_count),
        (actual.future_reference_table_count, expected.future_reference_table_count),
        (actual.future_child_table_count, expected.future_child_table_count),
    )
    if any(actual_count < expected_count for actual_count, expected_count in required_table_counts):
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            "full-strict-v16 activation census is missing required table authority",
        )

    if actual != expected:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "full-strict-v16 activation census differs from reviewed bytes",
        )
    return _activation_receipt(actual)


def activate_full_strict_v16(
    *,
    registry: Mapping[str, PipelineContractSpec],
    census_receipt: PipelineActivationReceipt,
    separately_reviewed: bool,
) -> Mapping[str, PipelineContractSpec]:
    """Reject activation of the immutable superseded v16 contract."""

    _ = registry, census_receipt, separately_reviewed
    raise PipelineContractError(
        PIPELINE_CONTRACT_UNAVAILABLE,
        "full-strict-v16 is superseded and unavailable",
    )


def require_pipeline_contract_for_admission(
    *,
    registry: Mapping[str, PipelineContractSpec],
    contract_version: str,
    proof_contract_hash: str,
) -> PipelineContractSpec:
    """Resolve an active contract and bind strict admission to its proof hash."""

    if contract_version in _SUPERSEDED_CONTRACT_VERSIONS:
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            f"pipeline contract is superseded: {contract_version}",
        )
    try:
        contract = registry[contract_version]
    except KeyError as exc:
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            f"pipeline contract is unavailable: {contract_version}",
        ) from exc
    if contract.state is not PipelineContractState.ACTIVE:
        raise PipelineContractError(
            PIPELINE_CONTRACT_UNAVAILABLE,
            f"pipeline contract is inactive: {contract_version}",
        )
    if contract.contract_hash is not None and proof_contract_hash != contract.contract_hash:
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            f"pipeline contract proof hash differs: {contract_version}",
        )
    return contract


def validate_admission_step_rows(
    *,
    contract: PipelineContractSpec,
    rows: Sequence[PipelineStepSpec],
) -> tuple[PipelineStepSpec, ...]:
    """Require exact canonical rows before admission DML is allowed."""

    candidate = tuple(rows)
    if (
        len(candidate) != contract.required_cardinality
        or any(step.auxiliary for step in candidate)
        or candidate != contract.steps
    ):
        raise PipelineContractError(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            f"admission step rows differ from {contract.contract_version}",
        )
    return candidate
