"""GL-12 inactive v28 stage runtime binding controls (1-3).

Pure source-level controls over fictional literals: the reviewed inactive
28-step contract and named component projection, the explicit strict proof
V2 binding/digest branch, and the flat Task 7 stage-input identity plus the
stage-arm carrier projection.  No runtime, database, provider, network,
registry activation or durable-admission claims.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import replace
from uuid import UUID, uuid5

import pytest

from idis.analysis.scoring import stage_admission as sa
from idis.persistence.repositories import strict_preflight_proofs as spp
from idis.pipeline import stage_runtime_binding as srb
from idis.pipeline.contract_registry import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PIPELINE_CONTRACT_UNAVAILABLE,
    PIPELINE_CONTRACTS,
    PipelineContractError,
    PipelineContractSpec,
    PipelineContractState,
    PipelineStepSpec,
)
from idis.pipeline.task7_uuid import derive_task7_v28_candidate_uuid

_TENANT = "11111111-1111-4111-8111-111111111111"
_DEAL = "22222222-2222-4222-8222-222222222222"
_RUN = "33333333-3333-4333-8333-333333333333"
_PROOF = "44444444-4444-4444-8444-444444444444"
_CONTRACT_VERSION = "full-strict-v28"
_CODEC_CONTRACT_HASH = "3" * 64
_BASE_CONTRACT_HASH = "28fa5a009d723d76de4c515e9131445d83bd3b9662621d292aff17c47f2ee4fa"
_PROPOSAL_SHA256 = "5666e2f4643defc149ed780f33af1e8e80bfbc8bd18d509731a2bb5ff26c256b"
_VECTORS_SHA256 = "cea8bae8f10329bdb887146bf800c0193cf69d2e8108d60bc582658208985409"

_SELECTION_SHA256 = "25a102bc19268154fd25c200b94191ee859e5c889650286a7c502599c06b8a86"
_PROOF_V1_DIGEST = "caa908b2ae962481c2b572b12c1713d35b57217e5213cdcd01ae63accac2b859"
_PROOF_V2_DIGEST = "67b0b48db82325585246432d065eadc4d50bc29a9d7d234832e5e96fb6365eb7"
_ADMISSION_SHA256 = "e5930e2166f18c6ca209baf78f79bebcf07196c67f689eff5115ec23775097be"
_FLAT_IDENTITY_SHA256 = "5f71bea7653d8f7e6d92eb5daaada48af9abbe199ead72ba529cccc9f3cdf5be"
_STAGE_BINDING_NAMESPACE = UUID("591d30bc-4dce-479b-9164-5f4020c311ae")

_SELECTION_POSITIVE_V1_LITERAL = (
    '["idis-stage-selection-v1",{"deal_id":"22222222-2222-4222-8222-222222222222","pack_schem'
    'a_version":"stage-pack-canonical-v1","pack_sha256":"2f3a18292e47d2b0e588c53cdcdbd795324d'
    'da947d59376bd85bc062642449ae","pack_stage":"GROWTH","raw_stage":"LATER","resolution_poli'
    'cy_sha256":"6792f54951941fb4fe892fdf7e662cf982649e420309d7effb07be00da446e0b","resolutio'
    'n_policy_version":"gl12-stage-policy-a-v1","resolved_stage":"GROWTH","schema_version":"s'
    'tage-selection-v1","tenant_id":"11111111-1111-4111-8111-111111111111"}]'
)
_PROOF_V1_LITERAL = (
    '{"corpus_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","deal_'
    'id":"22222222-2222-4222-8222-222222222222","dependency_receipt_set_hash":"aaaaaaaaaaaaaa'
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","expected_consumer_edge_rule_hash":"'
    '4444444444444444444444444444444444444444444444444444444444444444","methodology_version_h'
    'ashes":["6666666666666666666666666666666666666666666666666666666666666666","777777777777'
    '7777777777777777777777777777777777777777777777777777"],"pipeline_contract_hash":"3333333'
    '333333333333333333333333333333333333333333333333333333333","pipeline_contract_version":"'
    'full-strict-v28","promoted_pointer_hash":"8888888888888888888888888888888888888888888888'
    '888888888888888888","provider_receipt_set_hash":"999999999999999999999999999999999999999'
    '9999999999999999999999999","static_decision_set_hash":"555555555555555555555555555555555'
    '5555555555555555555555555555555","strict_profile_generation":"synthetic-generation-1","s'
    'trict_profile_hmac":"2222222222222222222222222222222222222222222222222222222222222222","'
    'tenant_id":"11111111-1111-4111-8111-111111111111"}'
)
_PROOF_V2_LITERAL = (
    '["idis-strict-preflight-binding-v2",{"corpus_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaa","deal_id":"22222222-2222-4222-8222-222222222222","dependen'
    'cy_receipt_set_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"expected_consumer_edge_rule_hash":"4444444444444444444444444444444444444444444444444444'
    '444444444444","methodology_version_hashes":["6666666666666666666666666666666666666666666'
    '666666666666666666666","7777777777777777777777777777777777777777777777777777777777777777'
    '"],"pipeline_contract_hash":"33333333333333333333333333333333333333333333333333333333333'
    '33333","pipeline_contract_version":"full-strict-v28","promoted_pointer_hash":"8888888888'
    '888888888888888888888888888888888888888888888888888888","provider_receipt_set_hash":"999'
    '9999999999999999999999999999999999999999999999999999999999999","stage_selection_sha256":'
    '"25a102bc19268154fd25c200b94191ee859e5c889650286a7c502599c06b8a86","static_decision_set_'
    'hash":"5555555555555555555555555555555555555555555555555555555555555555","strict_profile'
    '_generation":"synthetic-generation-1","strict_profile_hmac":"222222222222222222222222222'
    '2222222222222222222222222222222222222","tenant_id":"11111111-1111-4111-8111-111111111111'
    '"}]'
)
_ADMISSION_POSITIVE_V1_LITERAL = (
    '["idis-run-stage-admission-v1",{"binding_sha256":"67b0b48db82325585246432d065eadc4d50bc2'
    '9a9d7d234832e5e96fb6365eb7","deal_id":"22222222-2222-4222-8222-222222222222","mode":"FUL'
    'L","pipeline_contract_hash":"33333333333333333333333333333333333333333333333333333333333'
    '33333","pipeline_contract_version":"full-strict-v28","run_id":"33333333-3333-4333-8333-3'
    '33333333333","schema_version":"run-stage-admission-v1","selection":{"deal_id":"22222222-'
    '2222-4222-8222-222222222222","pack_schema_version":"stage-pack-canonical-v1","pack_sha25'
    '6":"2f3a18292e47d2b0e588c53cdcdbd795324dda947d59376bd85bc062642449ae","pack_stage":"GROW'
    'TH","raw_stage":"LATER","resolution_policy_sha256":"6792f54951941fb4fe892fdf7e662cf98264'
    '9e420309d7effb07be00da446e0b","resolution_policy_version":"gl12-stage-policy-a-v1","reso'
    'lved_stage":"GROWTH","schema_version":"stage-selection-v1","tenant_id":"11111111-1111-41'
    '11-8111-111111111111"},"stage_selection_sha256":"25a102bc19268154fd25c200b94191ee859e5c8'
    '89650286a7c502599c06b8a86","strict_preflight_proof_id":"44444444-4444-4444-8444-44444444'
    '4444","tenant_id":"11111111-1111-4111-8111-111111111111"}]'
)
_FLAT_IDENTITY_LITERAL = (
    '["idis-stage-admission-input-identity-v1","11111111-1111-4111-8111-111111111111","222222'
    '22-2222-4222-8222-222222222222","33333333-3333-4333-8333-333333333333","e5930e2166f18c6c'
    'a209baf78f79bebcf07196c67f689eff5115ec23775097be"]'
)
_STAGE_ENVELOPE_LITERAL = (
    '["idis-stage-envelope-v1",{"pack_sha256":"2f3a18292e47d2b0e588c53cdcdbd795324dda947d5937'
    '6bd85bc062642449ae","raw_stage":"LATER","resolution_policy_sha256":"6792f54951941fb4fe89'
    '2fdf7e662cf982649e420309d7effb07be00da446e0b","resolved_stage":"GROWTH","stage_admission'
    '_sha256":"e5930e2166f18c6ca209baf78f79bebcf07196c67f689eff5115ec23775097be","stage_selec'
    'tion_sha256":"25a102bc19268154fd25c200b94191ee859e5c889650286a7c502599c06b8a86"}]'
)
_STAGE_RUN_REPLAY_LITERAL = (
    '["idis-stage-run-replay-v1",{"deal_id":"22222222-2222-4222-8222-222222222222","pipeline_'
    'contract_hash":"3333333333333333333333333333333333333333333333333333333333333333","pipel'
    'ine_contract_version":"full-strict-v28","request_payload_sha256":"dddddddddddddddddddddd'
    'dddddddddddddddddddddddddddddddddddddddddd","run_id":"33333333-3333-4333-8333-3333333333'
    '33","run_stage_admission_sha256":"e5930e2166f18c6ca209baf78f79bebcf07196c67f689eff5115ec'
    '23775097be","schema_version":"stage-aware-run-replay-v1","tenant_id":"11111111-1111-4111'
    '-8111-111111111111"}]'
)

_EXPECTED_STEP_ROWS: tuple[tuple[int, str, str], ...] = (
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

_EXPECTED_COMPONENTS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
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

_CARRIER_VALUE_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "deal_id",
    "run_id",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "corpus_receipt_id",
    "source_lineage_object_id",
    "source_object_kind",
    "source_disposition",
    "binding_purpose",
    "selected_source_version",
    "selected_identity_sha256",
    "selected_content_sha256",
    "corpus_receipt_sha256",
)


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized[unicodedata.normalize("NFC", key)] = _normalize(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _task7_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _normalize(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _selection() -> sa.StageSelectionV1:
    return sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage="LATER",
        policy=sa.stage_resolution_policy_a(),
        packs=sa.installed_stage_pack_registry(),
    )


def _binding_v1(contract_hash: str = _CODEC_CONTRACT_HASH) -> spp.StrictPreflightBinding:
    return spp.StrictPreflightBinding(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        corpus_hash="a" * 64,
        strict_profile_generation="synthetic-generation-1",
        strict_profile_hmac="2" * 64,
        pipeline_contract_version=_CONTRACT_VERSION,
        pipeline_contract_hash=contract_hash,
        expected_consumer_edge_rule_hash="4" * 64,
        static_decision_set_hash="5" * 64,
        methodology_version_hashes=("6" * 64, "7" * 64),
        promoted_pointer_hash="8" * 64,
        provider_receipt_set_hash="9" * 64,
        dependency_receipt_set_hash="a" * 64,
    )


def _admission() -> sa.RunStageAdmissionV1:
    return sa.build_run_stage_admission(
        selection=_selection(),
        run_identity=sa.RunIdentityV1(
            tenant_id=_TENANT,
            deal_id=_DEAL,
            run_id=_RUN,
            pipeline_contract_version=_CONTRACT_VERSION,
            pipeline_contract_hash=_CODEC_CONTRACT_HASH,
        ),
        proof_identity=sa.StrictProofIdentityV1(
            strict_preflight_proof_id=_PROOF,
            binding_sha256=_PROOF_V2_DIGEST,
        ),
    )


def _binding(
    contract: PipelineContractSpec,
) -> srb.InactiveStageRuntimeBinding:
    return srb.InactiveStageRuntimeBinding(
        base_candidate_contract=contract,
        authority_amendment_proposal_sha256=_PROPOSAL_SHA256,
        literal_vectors_sha256=_VECTORS_SHA256,
        base_component_identities=_EXPECTED_COMPONENTS,
        governed_successor_contract_hash=None,
    )


def _carrier_values() -> dict[str, object]:
    return {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "pipeline_contract_version": _CONTRACT_VERSION,
        "pipeline_contract_hash": _CODEC_CONTRACT_HASH,
        "corpus_receipt_id": None,
        "source_lineage_object_id": None,
        "source_object_kind": "RUN_STAGE_ADMISSION",
        "source_disposition": "ADMITTED_METADATA",
        "binding_purpose": "STAGE_ADMISSION",
        "selected_source_version": "run-stage-admission-v1",
        "selected_identity_sha256": _FLAT_IDENTITY_SHA256,
        "selected_content_sha256": _ADMISSION_SHA256,
        "corpus_receipt_sha256": None,
    }


def test_inactive_stage_runtime_binding_is_exact() -> None:
    """Assert the reviewed inactive 28-step contract and named components."""
    binding = srb.inactive_stage_runtime_binding()
    spec = binding.base_candidate_contract

    assert spec.contract_version == _CONTRACT_VERSION
    assert spec.contract_hash == _BASE_CONTRACT_HASH
    assert spec.state is PipelineContractState.INACTIVE
    assert spec.deterministic_step_ids is True
    assert spec.required_cardinality == 28
    assert spec.edge_manifest_hash == (
        "5fc0263dea7b9ae92a27f36ebdbbb2201e52da08bc8940b4ad5a15fd8ddf378b"
    )
    assert spec.future_authority_hash == (
        "073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd"
    )

    assert len(spec.steps) == 28
    for index, (order, name, rule_hash) in enumerate(_EXPECTED_STEP_ROWS):
        step = spec.steps[index]
        assert type(step) is PipelineStepSpec
        assert step.step_order == order == index
        assert step.step_name == name
        assert step.semantic_rule_version == "2"
        assert step.semantic_rule_hash == rule_hash
        assert step.auxiliary is False
    assert {step.step_name for step in spec.steps}.isdisjoint(
        {"STAGE_ADMISSION", "RUN_STAGE_ADMISSION"},
    )

    assert binding.base_component_identities == _EXPECTED_COMPONENTS
    assert [name for name, _ in binding.base_component_identities] == [
        name for name, _ in _EXPECTED_COMPONENTS
    ]
    assert binding.authority_amendment_proposal_sha256 == _PROPOSAL_SHA256
    assert binding.literal_vectors_sha256 == _VECTORS_SHA256
    assert binding.governed_successor_contract_hash is None
    assert _CONTRACT_VERSION not in PIPELINE_CONTRACTS

    checked = srb.validate_inactive_stage_runtime_binding(binding)
    assert checked is binding

    with pytest.raises(PipelineContractError) as governed:
        binding.require_governed_successor_contract()
    assert governed.value.code == PIPELINE_CONTRACT_UNAVAILABLE

    non_tuple_spec = replace(spec)
    object.__setattr__(non_tuple_spec, "steps", list(spec.steps))
    mutations: tuple[PipelineContractSpec, ...] = (
        replace(spec, steps=spec.steps[:1] + spec.steps[:1] + spec.steps[2:]),
        replace(spec, steps=(replace(spec.steps[0], step_order=False),) + spec.steps[1:]),
        replace(spec, steps=spec.steps[:-1]),
        replace(spec, steps=spec.steps + (spec.steps[0],)),
        replace(
            spec,
            steps=spec.steps[:5] + (replace(spec.steps[5], step_name="EXTRACT"),) + spec.steps[6:],
        ),
        replace(
            spec,
            steps=(replace(spec.steps[0], semantic_rule_hash="0" * 64),) + spec.steps[1:],
        ),
        replace(spec, steps=(replace(spec.steps[0], auxiliary=True),) + spec.steps[1:]),
        non_tuple_spec,
        replace(spec, contract_hash="0" * 64),
        replace(spec, state=PipelineContractState.ACTIVE),
        replace(spec, required_cardinality=0),
        replace(spec, contract_version="full-strict-v21"),
    )
    for mutated in mutations:
        with pytest.raises(PipelineContractError) as refused:
            srb.validate_inactive_stage_runtime_binding(_binding(mutated))
        assert refused.value.code == PIPELINE_CONTRACT_HASH_MISMATCH

    provenance_mutations = (
        replace(binding, authority_amendment_proposal_sha256="0" * 64),
        replace(binding, literal_vectors_sha256="0" * 64),
        replace(
            binding,
            base_component_identities=tuple(
                (name, (("expected_sha256", "0" * 64),) if fields else fields)
                for name, fields in _EXPECTED_COMPONENTS
            ),
        ),
        replace(binding, governed_successor_contract_hash="0" * 64),
    )
    for mutated_binding in provenance_mutations:
        with pytest.raises(PipelineContractError) as refused:
            srb.validate_inactive_stage_runtime_binding(mutated_binding)
        assert refused.value.code == PIPELINE_CONTRACT_HASH_MISMATCH
        with pytest.raises(PipelineContractError) as authority:
            mutated_binding.require_governed_successor_contract()
        assert authority.value.code == PIPELINE_CONTRACT_HASH_MISMATCH

    with pytest.raises(PipelineContractError):
        srb.validate_inactive_stage_runtime_binding(spec)
    with pytest.raises(PipelineContractError):
        srb.validate_inactive_stage_runtime_binding(None)


def test_proof_v2_binds_selection_and_preserves_v1() -> None:
    """Pin the explicit V2 branch against the declared literal oracle."""
    selection = _selection()
    assert selection.canonical_bytes() == _SELECTION_POSITIVE_V1_LITERAL.encode("ascii")
    assert len(selection.canonical_bytes()) == 511
    assert selection.canonical_sha256() == _SELECTION_SHA256

    binding_v1 = _binding_v1()
    assert binding_v1.digest() == _PROOF_V1_DIGEST

    binding_v2 = spp.build_strict_preflight_binding_v2(
        binding=binding_v1,
        selection=selection,
    )
    assert type(binding_v2) is spp.StrictPreflightBindingV2
    assert binding_v2.stage_selection_sha256 == _SELECTION_SHA256
    assert binding_v2.digest() == _PROOF_V2_DIGEST
    assert json.dumps(
        ["idis-strict-preflight-binding-v2", json.loads(_PROOF_V2_LITERAL)[1]],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") == _PROOF_V2_LITERAL.encode("utf-8")
    assert len(_PROOF_V2_LITERAL.encode("utf-8")) == 1235
    assert hashlib.sha256(_PROOF_V2_LITERAL.encode("utf-8")).hexdigest() == _PROOF_V2_DIGEST
    assert len(_PROOF_V1_LITERAL.encode("utf-8")) == 1106
    assert hashlib.sha256(_PROOF_V1_LITERAL.encode("utf-8")).hexdigest() == _PROOF_V1_DIGEST

    other_selection = sa.capture_stage_selection(
        tenant_id="55555555-5555-4555-8555-555555555555",
        deal_id=_DEAL,
        raw_stage="LATER",
        policy=sa.stage_resolution_policy_a(),
        packs=sa.installed_stage_pack_registry(),
    )
    binding_v2_other = spp.build_strict_preflight_binding_v2(
        binding=replace(binding_v1, tenant_id="55555555-5555-4555-8555-555555555555"),
        selection=other_selection,
    )
    assert binding_v2_other.stage_selection_sha256 != _SELECTION_SHA256
    assert binding_v2_other.digest() != _PROOF_V2_DIGEST
    assert binding_v1.digest() == _PROOF_V1_DIGEST

    foreign_scope = sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id="66666666-6666-4666-8666-666666666666",
        raw_stage="LATER",
        policy=sa.stage_resolution_policy_a(),
        packs=sa.installed_stage_pack_registry(),
    )
    with pytest.raises(spp.StrictPreflightProofError) as scope_refused:
        spp.build_strict_preflight_binding_v2(
            binding=binding_v1,
            selection=foreign_scope,
        )
    assert scope_refused.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH"

    fields = {
        "binding_schema_version": "strict-preflight-binding-v2",
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "corpus_hash": "a" * 64,
        "strict_profile_generation": "synthetic-generation-1",
        "strict_profile_hmac": "2" * 64,
        "pipeline_contract_version": _CONTRACT_VERSION,
        "pipeline_contract_hash": _CODEC_CONTRACT_HASH,
        "expected_consumer_edge_rule_hash": "4" * 64,
        "static_decision_set_hash": "5" * 64,
        "methodology_version_hashes": ["6" * 64, "7" * 64],
        "promoted_pointer_hash": "8" * 64,
        "provider_receipt_set_hash": "9" * 64,
        "dependency_receipt_set_hash": "a" * 64,
        "stage_selection_sha256": _SELECTION_SHA256,
    }
    read = spp.strict_preflight_binding_v2_from_fields(dict(fields))
    assert read == binding_v2

    refusals: tuple[dict[str, object], ...] = (
        {key: value for key, value in fields.items() if key != "binding_schema_version"},
        {**fields, "binding_schema_version": None},
        {**fields, "binding_schema_version": "strict-preflight-binding-v1"},
        {**fields, "binding_schema_version": "strict-preflight-binding-v3"},
        {**fields, "binding_schema_version": True},
        {**fields, "unexpected_member": "0" * 64},
        {key: value for key, value in fields.items() if key != "corpus_hash"},
        {**fields, "corpus_hash": None},
        {**fields, "corpus_hash": "A" * 64},
        {**fields, "corpus_hash": 123},
        {**fields, "corpus_hash": True},
        {**fields, "strict_profile_generation": 7},
        {**fields, "stage_selection_sha256": None},
        {**fields, "stage_selection_sha256": _SELECTION_SHA256.upper()},
        {**fields, "stage_selection_sha256": 25},
        {**fields, "tenant_id": "not-a-uuid"},
        {**fields, "tenant_id": None},
        {**fields, "methodology_version_hashes": "6" * 64},
        {**fields, "methodology_version_hashes": ["6" * 64, 7]},
        {**fields, "methodology_version_hashes": ["6" * 64, None]},
        {**fields, "methodology_version_hashes": None},
    )
    for bad_fields in refusals:
        with pytest.raises(spp.StrictPreflightProofError) as refused:
            spp.strict_preflight_binding_v2_from_fields(bad_fields)
        assert refused.value.code == "STRICT_PREFLIGHT_PROOF_INVALID"


def test_stage_input_identity_uses_task7_flat_recipe() -> None:
    """Pin the flat Task 7 identity and the 17-field carrier projection."""
    admission = _admission()
    assert admission.canonical_bytes() == _ADMISSION_POSITIVE_V1_LITERAL.encode("ascii")
    assert len(admission.canonical_bytes()) == 1114
    assert admission.canonical_sha256() == _ADMISSION_SHA256

    identity = srb.stage_admission_input_identity(admission)
    assert identity.preimage == (
        "idis-stage-admission-input-identity-v1",
        _TENANT,
        _DEAL,
        _RUN,
        _ADMISSION_SHA256,
    )
    assert identity.canonical_bytes == _FLAT_IDENTITY_LITERAL.encode("utf-8")
    assert len(identity.canonical_bytes) == 226
    assert identity.identity_sha256 == _FLAT_IDENTITY_SHA256
    assert identity.canonical_bytes != _STAGE_ENVELOPE_LITERAL.encode("utf-8")
    assert identity.canonical_bytes != _STAGE_RUN_REPLAY_LITERAL.encode("utf-8")
    assert len(_STAGE_ENVELOPE_LITERAL.encode("utf-8")) == 433
    assert len(_STAGE_RUN_REPLAY_LITERAL.encode("utf-8")) == 549
    with pytest.raises(sa.StageAdmissionError):
        srb.stage_admission_input_identity(_STAGE_ENVELOPE_LITERAL.encode("utf-8"))

    values = _carrier_values()
    name_preimage = ["run-input-binding-v2", *(values[field] for field in _CARRIER_VALUE_FIELDS)]
    assert len(name_preimage) == 15
    expected_name_sha = _task7_sha256(name_preimage)
    expected_uuid = uuid5(_STAGE_BINDING_NAMESPACE, expected_name_sha)

    derived = derive_task7_v28_candidate_uuid(
        candidate_contract_version=_CONTRACT_VERSION,
        candidate_contract_hash=_CODEC_CONTRACT_HASH,
        id_kind="stage_admission_binding_id",
        values=dict(values),
    )
    assert derived.name_hash == expected_name_sha
    assert derived.value == expected_uuid
    assert derived.value.version == 5

    digest_preimage = [
        "run-input-binding-digest-v2",
        *(values[field] for field in _CARRIER_VALUE_FIELDS),
    ]
    assert len(digest_preimage) == 15
    expected_binding_sha256 = _task7_sha256(digest_preimage)

    projection = srb.stage_admission_binding_projection(
        admission=admission,
        binding_uuid=derived,
    )
    assert tuple(projection) == (
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
    assert dict(projection) == {
        "binding_id": str(expected_uuid),
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "source_lineage_object_id": None,
        "source_object_kind": "RUN_STAGE_ADMISSION",
        "source_disposition": "ADMITTED_METADATA",
        "binding_purpose": "STAGE_ADMISSION",
        "corpus_receipt_id": None,
        "selected_source_version": "run-stage-admission-v1",
        "selected_identity_sha256": _FLAT_IDENTITY_SHA256,
        "selected_content_sha256": _ADMISSION_SHA256,
        "corpus_receipt_sha256": None,
        "pipeline_contract_version": _CONTRACT_VERSION,
        "pipeline_contract_hash": _CODEC_CONTRACT_HASH,
        "binding_sha256": expected_binding_sha256,
        "stage_admission_sha256": _ADMISSION_SHA256,
    }

    inactive_binding = srb.inactive_stage_runtime_binding()
    with pytest.raises(PipelineContractError) as wrong_context:
        srb.stage_admission_binding_uuid(binding=inactive_binding, admission=admission)
    assert wrong_context.value.code == PIPELINE_CONTRACT_HASH_MISMATCH

    inactive_admission = sa.build_run_stage_admission(
        selection=_selection(),
        run_identity=sa.RunIdentityV1(
            tenant_id=_TENANT,
            deal_id=_DEAL,
            run_id=_RUN,
            pipeline_contract_version=_CONTRACT_VERSION,
            pipeline_contract_hash=_BASE_CONTRACT_HASH,
        ),
        proof_identity=sa.StrictProofIdentityV1(
            strict_preflight_proof_id=_PROOF,
            binding_sha256=_PROOF_V2_DIGEST,
        ),
    )
    inactive_derived = srb.stage_admission_binding_uuid(
        binding=inactive_binding,
        admission=inactive_admission,
    )
    inactive_admission_literal = _ADMISSION_POSITIVE_V1_LITERAL.replace(
        '"pipeline_contract_hash":"' + _CODEC_CONTRACT_HASH + '"',
        '"pipeline_contract_hash":"' + _BASE_CONTRACT_HASH + '"',
    )
    assert inactive_admission_literal != _ADMISSION_POSITIVE_V1_LITERAL
    inactive_canonical = inactive_admission_literal.encode("ascii")
    assert len(inactive_canonical) == 1114
    assert inactive_admission.canonical_bytes() == inactive_canonical
    inactive_content_sha256 = hashlib.sha256(inactive_canonical).hexdigest()
    inactive_identity_sha256 = _task7_sha256(
        [
            "idis-stage-admission-input-identity-v1",
            _TENANT,
            _DEAL,
            _RUN,
            inactive_content_sha256,
        ],
    )
    inactive_values = {
        **values,
        "pipeline_contract_hash": _BASE_CONTRACT_HASH,
        "selected_identity_sha256": inactive_identity_sha256,
        "selected_content_sha256": inactive_content_sha256,
    }
    expected_inactive_sha = _task7_sha256(
        ["run-input-binding-v2", *(inactive_values[f] for f in _CARRIER_VALUE_FIELDS)],
    )
    assert inactive_derived.name_hash == expected_inactive_sha
    assert inactive_derived.value == uuid5(_STAGE_BINDING_NAMESPACE, expected_inactive_sha)
