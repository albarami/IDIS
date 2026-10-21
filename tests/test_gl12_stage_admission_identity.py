"""GL-12 inactive stage identity codec tests.

Pure stage-canonical-json-v1 codec coverage using only fictional vectors and
the already-public installed pack values. No private paths, oracles, network,
providers or runtime activation. These tests do not prove queue/retry/DB
behavior and the codec is not a durable admission authority.

The six canonical constants below are exact byte-for-byte pinned preimage
literals; they must stay verbatim single-line strings regardless of length.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import struct
from decimal import Decimal

import pytest
from pydantic import ValidationError

from idis.analysis.scoring import stage_admission as sa
from idis.analysis.scoring.models import (
    STAGE_SPELLINGS,
    RoutingAction,
    ScoreBand,
    ScoreDimension,
    Stage,
    resolve_stage,
)
from idis.analysis.scoring.stage_packs import StagePack, get_stage_pack

_TENANT = "00000000-0000-0000-0000-000000000001"
_DEAL = "00000000-0000-0000-0000-000000000002"
_RUN = "00000000-0000-0000-0000-000000000003"
_PROOF = "00000000-0000-0000-0000-000000000004"
_CONTRACT_VERSION = "stage-aware-full-proposed"
_CONTRACT_HASH = "f" * 64
_BINDING = "e" * 64

_POLICY_A_SHA256 = "6792f54951941fb4fe892fdf7e662cf982649e420309d7effb07be00da446e0b"
_FICTIONAL_PACK_SHA256 = "9a2c13cf02031c528564ad65381658234aba8f5216e8de3624aed8e34630ec7d"
_SELECTION_LATER_SHA256 = "0e1ac3ba6c8160e5f22adde30c57397d03bdd68cd96f58769cd8f3d44574a667"
_ADMISSION_NONSTRICT_SHA256 = "54821aab246f151f11c736eb9bea16057de9aea3c54f537cf88b9823dceae433"
_ADMISSION_STRICT_SHA256 = "da4d29b56471b92d470f1d309e07f80a9ca8abdd455c047422b4a036c3c868bb"
_UNICODE_PROBE_SHA256 = "35c9a9e58352d93a63699d10832d970b4e66dfb3ed27d77f5526c6dcb81b9f0f"

_EXPECTED_PACK_SHA256 = {
    Stage.PRE_SEED: "2264602661e24fbcb5b2a5ae227708490d16ab68eaac639621468913fc086c18",
    Stage.SEED: "49a1d3351da9365acd272a487a2af8ae40b753489f26714ea991c422337622e2",
    Stage.SERIES_A: "32f32c655a38fcf60a52c2eb4c4f4948bcbd40b997a6513beae75e32a858589c",
    Stage.SERIES_B: "730f3e6546daf6df468d6d9a248c2cbe81a2b960e3a3a02bee3774a75ce06a62",
    Stage.GROWTH: "2f3a18292e47d2b0e588c53cdcdbd795324dda947d59376bd85bc062642449ae",
}

_POLICY_A_CANONICAL = r'["idis-stage-resolution-policy-v1",{"lookup_recipe":"python-str-casefold-split-join-v1","missing_or_unknown_full":"STAGE_UNKNOWN","native_intake_stages":["SEED","SERIES_A","SERIES_B","GROWTH","LATER","OTHER"],"native_pre_seed_admitted":false,"policy_version":"gl12-stage-policy-a-v1","retain_raw_stage":true,"schema_version":"stage-resolution-policy-v1","spellings":{"a":"SERIES_A","b":"SERIES_B","growth":"GROWTH","later":"GROWTH","pre seed":"PRE_SEED","pre-seed":"PRE_SEED","pre_seed":"PRE_SEED","preseed":"PRE_SEED","seed":"SEED","series a":"SERIES_A","series b":"SERIES_B","series-a":"SERIES_A","series-b":"SERIES_B","series_a":"SERIES_A","series_b":"SERIES_B"}}]'
_FICTIONAL_PACK_CANONICAL = r'["idis-stage-pack-v1",{"band_thresholds":{"HIGH":{"float8_be":"4054000000000000"},"MEDIUM":{"float8_be":"404e000000000000"}},"routing_by_band":{"HIGH":"INVEST","LOW":"DECLINE","MEDIUM":"HOLD"},"schema_version":"stage-pack-canonical-v1","stage":"GROWTH","weights":{"CAPITAL_EFFICIENCY":{"float8_be":"3fc0000000000000"},"FUND_THESIS_FIT":{"float8_be":"3fc0000000000000"},"MARKET_ATTRACTIVENESS":{"float8_be":"3fd0000000000000"},"PRODUCT_DEFENSIBILITY":{"float8_be":"3fc0000000000000"},"RISK_PROFILE":{"float8_be":"0000000000000000"},"SCALABILITY":{"float8_be":"3fc0000000000000"},"TEAM_QUALITY":{"float8_be":"3fc0000000000000"},"TRACTION_VELOCITY":{"float8_be":"3fc0000000000000"}}}]'
_SELECTION_LATER_CANONICAL = r'["idis-stage-selection-v1",{"deal_id":"00000000-0000-0000-0000-000000000002","pack_schema_version":"stage-pack-canonical-v1","pack_sha256":"9a2c13cf02031c528564ad65381658234aba8f5216e8de3624aed8e34630ec7d","pack_stage":"GROWTH","raw_stage":"LATER","resolution_policy_sha256":"6792f54951941fb4fe892fdf7e662cf982649e420309d7effb07be00da446e0b","resolution_policy_version":"gl12-stage-policy-a-v1","resolved_stage":"GROWTH","schema_version":"stage-selection-v1","tenant_id":"00000000-0000-0000-0000-000000000001"}]'
_ADMISSION_NONSTRICT_CANONICAL = r'["idis-run-stage-admission-v1",{"binding_sha256":null,"deal_id":"00000000-0000-0000-0000-000000000002","mode":"FULL","pipeline_contract_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","pipeline_contract_version":"stage-aware-full-proposed","run_id":"00000000-0000-0000-0000-000000000003","schema_version":"run-stage-admission-v1","selection":{"deal_id":"00000000-0000-0000-0000-000000000002","pack_schema_version":"stage-pack-canonical-v1","pack_sha256":"9a2c13cf02031c528564ad65381658234aba8f5216e8de3624aed8e34630ec7d","pack_stage":"GROWTH","raw_stage":"LATER","resolution_policy_sha256":"6792f54951941fb4fe892fdf7e662cf982649e420309d7effb07be00da446e0b","resolution_policy_version":"gl12-stage-policy-a-v1","resolved_stage":"GROWTH","schema_version":"stage-selection-v1","tenant_id":"00000000-0000-0000-0000-000000000001"},"stage_selection_sha256":"0e1ac3ba6c8160e5f22adde30c57397d03bdd68cd96f58769cd8f3d44574a667","strict_preflight_proof_id":null,"tenant_id":"00000000-0000-0000-0000-000000000001"}]'
_ADMISSION_STRICT_CANONICAL = r'["idis-run-stage-admission-v1",{"binding_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","deal_id":"00000000-0000-0000-0000-000000000002","mode":"FULL","pipeline_contract_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","pipeline_contract_version":"stage-aware-full-proposed","run_id":"00000000-0000-0000-0000-000000000003","schema_version":"run-stage-admission-v1","selection":{"deal_id":"00000000-0000-0000-0000-000000000002","pack_schema_version":"stage-pack-canonical-v1","pack_sha256":"9a2c13cf02031c528564ad65381658234aba8f5216e8de3624aed8e34630ec7d","pack_stage":"GROWTH","raw_stage":"LATER","resolution_policy_sha256":"6792f54951941fb4fe892fdf7e662cf982649e420309d7effb07be00da446e0b","resolution_policy_version":"gl12-stage-policy-a-v1","resolved_stage":"GROWTH","schema_version":"stage-selection-v1","tenant_id":"00000000-0000-0000-0000-000000000001"},"stage_selection_sha256":"0e1ac3ba6c8160e5f22adde30c57397d03bdd68cd96f58769cd8f3d44574a667","strict_preflight_proof_id":"00000000-0000-0000-0000-000000000004","tenant_id":"00000000-0000-0000-0000-000000000001"}]'
_UNICODE_PROBE_CANONICAL = r'["idis-stage-canonical-text-probe-v1",{"composed":"\u00e9","decomposed":"e\u0301","text":"a\t\n\\\"z"}]'

_LEGACY_SPELLINGS: dict[str, Stage] = dict(STAGE_SPELLINGS)
_POLICY_SPELLINGS: dict[str, Stage] = {**_LEGACY_SPELLINGS, "later": Stage.GROWTH}

_NATIVE_LABEL_CASES: tuple[tuple[str, Stage | None], ...] = (
    ("SEED", Stage.SEED),
    ("SERIES_A", Stage.SERIES_A),
    ("SERIES_B", Stage.SERIES_B),
    ("GROWTH", Stage.GROWTH),
    ("LATER", Stage.GROWTH),
    ("OTHER", None),
)

_UNKNOWN_RAW_STAGES: tuple[object, ...] = (
    None,
    "",
    "   ",
    "series c",
    "growth equity",
    "LATER_STAGE",
    "other",
    12,
    {},
)


def _fictional_pack() -> StagePack:
    """Fictional test-only pack: 0.25 + six 0.125 + -0.0; NOT an installed pack."""
    return StagePack(
        stage=Stage.GROWTH,
        weights={
            ScoreDimension.MARKET_ATTRACTIVENESS: 0.25,
            ScoreDimension.TEAM_QUALITY: 0.125,
            ScoreDimension.PRODUCT_DEFENSIBILITY: 0.125,
            ScoreDimension.TRACTION_VELOCITY: 0.125,
            ScoreDimension.FUND_THESIS_FIT: 0.125,
            ScoreDimension.CAPITAL_EFFICIENCY: 0.125,
            ScoreDimension.SCALABILITY: 0.125,
            ScoreDimension.RISK_PROFILE: -0.0,
        },
        band_thresholds={"HIGH": 80.0, "MEDIUM": 60.0},
        routing_by_band={
            ScoreBand.HIGH: RoutingAction.INVEST,
            ScoreBand.MEDIUM: RoutingAction.HOLD,
            ScoreBand.LOW: RoutingAction.DECLINE,
        },
    )


def _fictional_registry() -> sa.StagePackRegistry:
    return sa.StagePackRegistry({Stage.GROWTH: sa.capture_stage_pack(_fictional_pack())})


def _later_selection() -> sa.StageSelectionV1:
    return sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage="LATER",
        policy=sa.stage_resolution_policy_a(),
        packs=_fictional_registry(),
    )


def _run_identity() -> sa.RunIdentityV1:
    return sa.RunIdentityV1(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        run_id=_RUN,
        pipeline_contract_version=_CONTRACT_VERSION,
        pipeline_contract_hash=_CONTRACT_HASH,
    )


def _strict_proof() -> sa.StrictProofIdentityV1:
    return sa.StrictProofIdentityV1(
        strict_preflight_proof_id=_PROOF,
        binding_sha256=_BINDING,
    )


def _selection_kwargs() -> dict[str, object]:
    return {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "raw_stage": "LATER",
        "resolved_stage": Stage.GROWTH,
        "resolution_policy_version": sa.POLICY_A_VERSION,
        "resolution_policy_sha256": _POLICY_A_SHA256,
        "pack_stage": Stage.GROWTH,
        "pack_sha256": _FICTIONAL_PACK_SHA256,
    }


def _admission_kwargs() -> dict[str, object]:
    return {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "selection": _later_selection(),
        "stage_selection_sha256": _SELECTION_LATER_SHA256,
        "pipeline_contract_version": _CONTRACT_VERSION,
        "pipeline_contract_hash": _CONTRACT_HASH,
        "strict_preflight_proof_id": None,
        "binding_sha256": None,
    }


def test_policy_a_exact_canonical_bytes_and_digest() -> None:
    policy = sa.stage_resolution_policy_a()
    assert policy.canonical_bytes() == _POLICY_A_CANONICAL.encode("ascii")
    assert len(policy.canonical_bytes()) == 667
    assert policy.canonical_sha256() == _POLICY_A_SHA256


def test_policy_a_spellings_is_frozen_15_key_closed_table() -> None:
    policy = sa.stage_resolution_policy_a()
    assert dict(policy.spellings) == _POLICY_SPELLINGS
    assert len(policy.spellings) == 15
    with pytest.raises(TypeError):
        policy.spellings["late"] = Stage.SEED  # type: ignore[index]
    assert policy.lookup_recipe == "python-str-casefold-split-join-v1"
    assert policy.native_pre_seed_admitted is False
    assert policy.missing_or_unknown_full == "STAGE_UNKNOWN"
    assert policy.retain_raw_stage is True
    assert [s.value for s in policy.native_intake_stages] == [
        "SEED",
        "SERIES_A",
        "SERIES_B",
        "GROWTH",
        "LATER",
        "OTHER",
    ]


def test_policy_alias_does_not_change_global_resolver() -> None:
    sa.stage_resolution_policy_a()
    assert STAGE_SPELLINGS == _LEGACY_SPELLINGS
    assert len(STAGE_SPELLINGS) == 14
    assert resolve_stage("LATER") is None
    assert resolve_stage("later") is None
    assert resolve_stage("OTHER") is None
    assert resolve_stage("seed") is Stage.SEED
    assert resolve_stage("growth") is Stage.GROWTH


def test_policy_a_reordered_spellings_same_digest() -> None:
    reordered = dict(reversed(list(_POLICY_SPELLINGS.items())))
    policy = sa.StageResolutionPolicyV1(
        policy_version=sa.POLICY_A_VERSION,
        spellings=reordered,
        native_intake_stages=tuple(sa.NativeIntakeStageLabel),
    )
    assert policy.canonical_sha256() == _POLICY_A_SHA256


def test_policy_tamper_changes_identity_and_capture_refuses() -> None:
    tampered = sa.StageResolutionPolicyV1(
        policy_version=sa.POLICY_A_VERSION,
        spellings={**_POLICY_SPELLINGS, "later": Stage.SEED},
        native_intake_stages=tuple(sa.NativeIntakeStageLabel),
    )
    assert tampered.canonical_sha256() != _POLICY_A_SHA256
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.capture_stage_selection(
            tenant_id=_TENANT,
            deal_id=_DEAL,
            raw_stage="LATER",
            policy=tampered,
            packs=_fictional_registry(),
        )
    assert exc.value.code == sa.STAGE_RESOLUTION_POLICY_MISMATCH


def test_fictional_pack_exact_canonical_bytes_and_digest() -> None:
    captured = sa.capture_stage_pack(_fictional_pack())
    assert captured.canonical_preimage == _FICTIONAL_PACK_CANONICAL.encode("ascii")
    assert len(captured.canonical_preimage) == 681
    assert captured.pack_sha256 == _FICTIONAL_PACK_SHA256


@pytest.mark.parametrize("stage", list(Stage))
def test_five_installed_packs_exact_oracle_digests(stage: Stage) -> None:
    captured = sa.capture_stage_pack(get_stage_pack(stage))
    assert captured.pack_sha256 == _EXPECTED_PACK_SHA256[stage]
    assert captured.stage is stage


def test_installed_registry_verifies_expected_hashes() -> None:
    registry = sa.installed_stage_pack_registry()
    assert set(registry.stages()) == set(Stage)
    for stage in Stage:
        pack = registry.get(stage)
        assert pack is not None
        assert pack.pack_sha256 == _EXPECTED_PACK_SHA256[stage]


@pytest.mark.parametrize("stage", list(Stage))
def test_pack_capture_conserves_existing_values(stage: Stage) -> None:
    source = get_stage_pack(stage)
    hydrated = sa.capture_stage_pack(source).to_stage_pack()
    assert hydrated.stage is source.stage
    assert hydrated.weights == source.weights
    assert hydrated.band_thresholds == source.band_thresholds
    assert hydrated.routing_by_band == source.routing_by_band


def test_captured_pack_unaffected_by_source_dict_mutation() -> None:
    source = _fictional_pack()
    captured = sa.capture_stage_pack(source)
    source.weights[ScoreDimension.RISK_PROFILE] = 0.99
    source.band_thresholds["HIGH"] = 1.0
    source.routing_by_band[ScoreBand.LOW] = RoutingAction.INVEST
    assert captured.pack_sha256 == _FICTIONAL_PACK_SHA256
    assert captured.canonical_preimage == _FICTIONAL_PACK_CANONICAL.encode("ascii")
    hydrated = captured.to_stage_pack()
    assert hydrated.weights[ScoreDimension.RISK_PROFILE] == 0.0
    assert hydrated.band_thresholds["HIGH"] == 80.0
    assert hydrated.routing_by_band[ScoreBand.LOW] is RoutingAction.DECLINE


def test_pack_single_member_change_changes_identity() -> None:
    changed = _fictional_pack().model_copy(
        update={"weights": {**_fictional_pack().weights, ScoreDimension.RISK_PROFILE: 1e-9}}
    )
    assert sa.capture_stage_pack(changed).pack_sha256 != _FICTIONAL_PACK_SHA256


def test_pack_signed_zero_and_order_equivalences() -> None:
    positive = _fictional_pack().model_copy(
        update={
            "weights": dict(
                reversed(
                    [
                        *(
                            (d, w)
                            for d, w in _fictional_pack().weights.items()
                            if d is not ScoreDimension.RISK_PROFILE
                        ),
                        (ScoreDimension.RISK_PROFILE, 0.0),
                    ]
                )
            )
        }
    )
    assert sa.capture_stage_pack(positive).pack_sha256 == _FICTIONAL_PACK_SHA256


def test_capture_pack_rejects_noncanonical_members() -> None:
    bad_weights = StagePack.model_construct(
        stage=Stage.GROWTH,
        weights={**_fictional_pack().weights, ScoreDimension.RISK_PROFILE: 1},
        band_thresholds={"HIGH": 80.0, "MEDIUM": 60.0},
        routing_by_band=_fictional_pack().routing_by_band,
    )
    with pytest.raises(sa.StageAdmissionError):
        sa.capture_stage_pack(bad_weights)
    missing_dim = dict(_fictional_pack().weights)
    missing_dim.pop(ScoreDimension.RISK_PROFILE)
    incomplete = StagePack.model_construct(
        stage=Stage.GROWTH,
        weights=missing_dim,
        band_thresholds={"HIGH": 80.0, "MEDIUM": 60.0},
        routing_by_band=_fictional_pack().routing_by_band,
    )
    with pytest.raises(sa.StageAdmissionError):
        sa.capture_stage_pack(incomplete)
    bad_band = StagePack.model_construct(
        stage=Stage.GROWTH,
        weights=_fictional_pack().weights,
        band_thresholds={"HIGH": 80.0, "LOW": 60.0},
        routing_by_band=_fictional_pack().routing_by_band,
    )
    with pytest.raises(sa.StageAdmissionError):
        sa.capture_stage_pack(bad_band)


def test_registry_rejects_mismatched_stage_key() -> None:
    captured = sa.capture_stage_pack(_fictional_pack())
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.StagePackRegistry({Stage.SEED: captured})
    assert exc.value.code == sa.STAGE_PACK_MISMATCH


def test_capture_selection_pack_unavailable_is_code_only() -> None:
    empty = sa.StagePackRegistry({})
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.capture_stage_selection(
            tenant_id=_TENANT,
            deal_id=_DEAL,
            raw_stage="SEED",
            policy=sa.stage_resolution_policy_a(),
            packs=empty,
        )
    assert exc.value.code == sa.STAGE_PACK_UNAVAILABLE


def test_selection_later_exact_canonical_bytes_and_digest() -> None:
    selection = _later_selection()
    assert selection.raw_stage == "LATER"
    assert selection.resolved_stage is Stage.GROWTH
    assert selection.pack_stage is Stage.GROWTH
    assert selection.pack_sha256 == _FICTIONAL_PACK_SHA256
    assert selection.resolution_policy_sha256 == _POLICY_A_SHA256
    assert selection.canonical_bytes() == _SELECTION_LATER_CANONICAL.encode("ascii")
    assert len(selection.canonical_bytes()) == 511
    assert selection.canonical_sha256() == _SELECTION_LATER_SHA256


@pytest.mark.parametrize(("label", "expected"), _NATIVE_LABEL_CASES)
def test_capture_selection_for_native_intake_labels(label: str, expected: Stage | None) -> None:
    kwargs = {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "raw_stage": label,
        "policy": sa.stage_resolution_policy_a(),
        "packs": sa.installed_stage_pack_registry(),
    }
    if expected is None:
        with pytest.raises(sa.StageAdmissionError) as exc:
            sa.capture_stage_selection(**kwargs)  # type: ignore[arg-type]
        assert exc.value.code == sa.STAGE_UNKNOWN
        return
    selection = sa.capture_stage_selection(**kwargs)  # type: ignore[arg-type]
    assert selection.raw_stage == label
    assert selection.resolved_stage is expected
    assert selection.pack_sha256 == _EXPECTED_PACK_SHA256[expected]


@pytest.mark.parametrize(("spelling", "expected"), list(_LEGACY_SPELLINGS.items()))
def test_capture_selection_for_each_legacy_spelling(spelling: str, expected: Stage) -> None:
    selection = sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage=spelling,
        policy=sa.stage_resolution_policy_a(),
        packs=sa.installed_stage_pack_registry(),
    )
    assert selection.resolved_stage is expected
    assert selection.pack_sha256 == _EXPECTED_PACK_SHA256[expected]


@pytest.mark.parametrize("raw", ["PRE_SEED", "pre-seed", "PreSeed", "pre seed", "  PRE SEED  "])
def test_internal_pre_seed_spellings_resolve(raw: str) -> None:
    selection = sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage=raw,
        policy=sa.stage_resolution_policy_a(),
        packs=sa.installed_stage_pack_registry(),
    )
    assert selection.resolved_stage is Stage.PRE_SEED
    assert selection.pack_sha256 == _EXPECTED_PACK_SHA256[Stage.PRE_SEED]


def test_raw_label_preserved_exactly() -> None:
    selection = sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage="  Later  ",
        policy=sa.stage_resolution_policy_a(),
        packs=_fictional_registry(),
    )
    assert selection.raw_stage == "  Later  "
    assert selection.resolved_stage is Stage.GROWTH
    body = json.loads(selection.canonical_bytes().decode("ascii"))[1]
    assert body["raw_stage"] == "  Later  "


@pytest.mark.parametrize("raw", _UNKNOWN_RAW_STAGES)
def test_unknown_missing_ineligible_fail_code_only(raw: object) -> None:
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.capture_stage_selection(
            tenant_id=_TENANT,
            deal_id=_DEAL,
            raw_stage=raw,  # type: ignore[arg-type]
            policy=sa.stage_resolution_policy_a(),
            packs=sa.installed_stage_pack_registry(),
        )
    assert exc.value.code == sa.STAGE_UNKNOWN
    assert str(exc.value) == sa.STAGE_UNKNOWN


def test_selection_model_rejects_extra_missing_and_bad_values() -> None:
    kwargs = _selection_kwargs()
    with pytest.raises(ValidationError):
        sa.StageSelectionV1(**{**kwargs, "extra": 1})  # type: ignore[arg-type]
    for key in ("tenant_id", "raw_stage", "pack_sha256"):
        with pytest.raises(ValidationError):
            sa.StageSelectionV1(**{k: v for k, v in kwargs.items() if k != key})  # type: ignore[arg-type]
    for bad in (
        {"tenant_id": "00000000-0000-0000-0000-00000000000Z"},
        {"tenant_id": "00000000000000000000000000000001"},
        {"tenant_id": "{00000000-0000-0000-0000-000000000001}"},
        {"tenant_id": "00000000-0000-0000-0000-00000000000A"},
        {"pack_sha256": "a" * 63},
        {"pack_sha256": "A" * 64},
        {"tenant_id": True},
        {"raw_stage": ""},
        {"resolved_stage": "growth"},
        {"resolved_stage": "LATER"},
    ):
        with pytest.raises(ValidationError):
            sa.StageSelectionV1(**{**kwargs, **bad})  # type: ignore[arg-type]


def test_selection_model_rejects_pack_stage_mismatch() -> None:
    with pytest.raises(ValidationError):
        sa.StageSelectionV1(**{**_selection_kwargs(), "pack_stage": Stage.SEED})  # type: ignore[arg-type]


def test_selection_decode_roundtrip_and_wrong_domain() -> None:
    selection = _later_selection()
    decoded = sa.decode_stage_selection_v1(selection.canonical_bytes())
    assert decoded == selection
    assert decoded.canonical_sha256() == _SELECTION_LATER_SHA256
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_stage_selection_v1(
            sa.stage_canonical_json_v1_bytes(
                sa.DOMAIN_RUN_STAGE_ADMISSION_V1, selection.canonical_body()
            )
        )


def test_selection_single_member_change_changes_identity() -> None:
    body = _later_selection().canonical_body()
    changed = dict(body)
    changed["resolved_stage"] = "SEED"
    assert (
        sa.stage_canonical_json_v1_sha256(sa.DOMAIN_STAGE_SELECTION_V1, changed)
        != _SELECTION_LATER_SHA256
    )


def test_validate_stage_selection_returns_immutable_pack() -> None:
    selection = _later_selection()
    pack = sa.validate_stage_selection(
        selection=selection,
        policy=sa.stage_resolution_policy_a(),
        packs=_fictional_registry(),
    )
    assert pack.pack_sha256 == _FICTIONAL_PACK_SHA256


def test_validate_stage_selection_detects_tamper() -> None:
    wrong_hash = sa.StageSelectionV1(
        **{**_selection_kwargs(), "pack_sha256": "0" * 64}  # type: ignore[arg-type]
    )
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.validate_stage_selection(
            selection=wrong_hash,
            policy=sa.stage_resolution_policy_a(),
            packs=_fictional_registry(),
        )
    assert exc.value.code == sa.STAGE_PACK_MISMATCH
    lying = sa.StageSelectionV1(
        **{**_selection_kwargs(), "raw_stage": "SEED", "resolved_stage": Stage.GROWTH}  # type: ignore[arg-type]
    )
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.validate_stage_selection(
            selection=lying,
            policy=sa.stage_resolution_policy_a(),
            packs=_fictional_registry(),
        )
    assert exc.value.code == sa.STAGE_ADMISSION_INVALID


def test_nonstrict_admission_exact_canonical_bytes_and_digest() -> None:
    admission = sa.build_run_stage_admission(
        selection=_later_selection(), run_identity=_run_identity()
    )
    assert admission.strict_preflight_proof_id is None
    assert admission.binding_sha256 is None
    assert admission.canonical_bytes() == _ADMISSION_NONSTRICT_CANONICAL.encode("ascii")
    assert len(admission.canonical_bytes()) == 1028
    assert admission.canonical_sha256() == _ADMISSION_NONSTRICT_SHA256


def test_strict_admission_exact_canonical_bytes_and_digest() -> None:
    admission = sa.build_run_stage_admission(
        selection=_later_selection(),
        run_identity=_run_identity(),
        proof_identity=_strict_proof(),
    )
    assert admission.strict_preflight_proof_id == _PROOF
    assert admission.binding_sha256 == _BINDING
    assert admission.canonical_bytes() == _ADMISSION_STRICT_CANONICAL.encode("ascii")
    assert len(admission.canonical_bytes()) == 1124
    assert admission.canonical_sha256() == _ADMISSION_STRICT_SHA256


def test_admission_scope_mismatch_rejected() -> None:
    foreign = _run_identity().model_copy(update={"tenant_id": _PROOF})
    with pytest.raises(sa.StageAdmissionError) as exc:
        sa.build_run_stage_admission(selection=_later_selection(), run_identity=foreign)
    assert exc.value.code == sa.STAGE_ADMISSION_INVALID


def test_admission_proof_pair_both_or_neither() -> None:
    kwargs = _admission_kwargs()
    with pytest.raises(ValidationError):
        sa.RunStageAdmissionV1(
            **{**kwargs, "strict_preflight_proof_id": _PROOF}  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        sa.RunStageAdmissionV1(**{**kwargs, "binding_sha256": _BINDING})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        sa.RunStageAdmissionV1(**{k: v for k, v in kwargs.items() if k != "binding_sha256"})  # type: ignore[arg-type]


def test_admission_decode_roundtrip_and_rejects() -> None:
    admission = sa.build_run_stage_admission(
        selection=_later_selection(),
        run_identity=_run_identity(),
        proof_identity=_strict_proof(),
    )
    decoded = sa.decode_run_stage_admission_v1(admission.canonical_bytes())
    assert decoded == admission
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_run_stage_admission_v1(admission.canonical_bytes() + b"\n")
    body = admission.canonical_body()
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_run_stage_admission_v1(
            sa.stage_canonical_json_v1_bytes(sa.DOMAIN_RUN_STAGE_ADMISSION_V1, {**body, "extra": 1})
        )
    no_pair = {
        key: value
        for key, value in body.items()
        if key not in {"strict_preflight_proof_id", "binding_sha256"}
    }
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_run_stage_admission_v1(
            sa.stage_canonical_json_v1_bytes(sa.DOMAIN_RUN_STAGE_ADMISSION_V1, no_pair)
        )
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_run_stage_admission_v1(
            sa.stage_canonical_json_v1_bytes(sa.DOMAIN_STAGE_SELECTION_V1, body)
        )
    bad_scope = dict(body)
    bad_scope["tenant_id"] = _PROOF
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_run_stage_admission_v1(
            sa.stage_canonical_json_v1_bytes(sa.DOMAIN_RUN_STAGE_ADMISSION_V1, bad_scope)
        )


def test_canonical_decode_rejects_duplicate_keys_and_noncanonical_forms() -> None:
    dup = b'["idis-stage-selection-v1",{"a":null,"a":null}]'
    with pytest.raises(sa.StageAdmissionError):
        sa.stage_canonical_json_v1_decode(sa.DOMAIN_STAGE_SELECTION_V1, dup)
    selection = _later_selection()
    pretty = json.dumps(
        [sa.DOMAIN_STAGE_SELECTION_V1, selection.canonical_body()], indent=2
    ).encode("ascii")
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_stage_selection_v1(pretty)


def test_frozen_models_reject_mutation() -> None:
    selection = _later_selection()
    admission = sa.build_run_stage_admission(
        selection=_later_selection(), run_identity=_run_identity()
    )
    with pytest.raises(ValidationError):
        selection.raw_stage = "SEED"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        admission.run_id = _PROOF  # type: ignore[misc]
    captured = sa.capture_stage_pack(_fictional_pack())
    with pytest.raises(ValidationError):
        captured.stage = Stage.SEED  # type: ignore[misc]


def test_unicode_probe_exact_canonical_bytes_and_digest() -> None:
    body = {"decomposed": "é", "composed": "é", "text": 'a\t\n\\"z'}
    data = sa.stage_canonical_json_v1_bytes("idis-stage-canonical-text-probe-v1", body)
    assert data == _UNICODE_PROBE_CANONICAL.encode("ascii")
    assert len(data) == 103
    import hashlib

    assert hashlib.sha256(data).hexdigest() == _UNICODE_PROBE_SHA256


def test_composed_decomposed_and_raw_text_stay_distinct() -> None:
    composed = sa.stage_canonical_json_v1_bytes("d", {"t": "é"})
    decomposed = sa.stage_canonical_json_v1_bytes("d", {"t": "é"})
    assert composed != decomposed
    decoded = sa.stage_canonical_json_v1_decode("d", decomposed)
    assert decoded["t"] == "é"
    assert len(decoded["t"]) == 2


def test_lone_surrogate_rejected() -> None:
    with pytest.raises(sa.StageAdmissionError):
        sa.stage_canonical_json_v1_bytes("d", {"t": "\ud800"})
    with pytest.raises(ValidationError):
        sa.StageSelectionV1(**{**_selection_kwargs(), "raw_stage": "\ud800"})  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_float_rejected(bad: float) -> None:
    with pytest.raises(sa.StageAdmissionError):
        sa.stage_canonical_json_v1_bytes("d", {"w": bad})
    pack = _fictional_pack().model_copy(
        update={"weights": {**_fictional_pack().weights, ScoreDimension.RISK_PROFILE: bad}}
    )
    with pytest.raises(sa.StageAdmissionError):
        sa.capture_stage_pack(pack)


def test_alternate_numeric_encodings_rejected() -> None:
    with pytest.raises(sa.StageAdmissionError):
        sa.stage_canonical_json_v1_bytes("d", {"w": 1})
    for bad_weight in (1, True):
        pack = StagePack.model_construct(
            stage=Stage.GROWTH,
            weights={
                **_fictional_pack().weights,
                ScoreDimension.RISK_PROFILE: bad_weight,  # type: ignore[dict-item]
            },
            band_thresholds={"HIGH": 80.0, "MEDIUM": 60.0},
            routing_by_band=_fictional_pack().routing_by_band,
        )
        with pytest.raises(sa.StageAdmissionError):
            sa.capture_stage_pack(pack)
    tampered = _FICTIONAL_PACK_CANONICAL.replace(
        '"RISK_PROFILE":{"float8_be":"0000000000000000"}', '"RISK_PROFILE":1'
    )
    with pytest.raises(sa.StageAdmissionError):
        sa.decode_immutable_stage_pack(tampered.encode("ascii"))


def test_float8_be_wrapper_semantics() -> None:
    encoded = sa.stage_canonical_json_v1_bytes("d", {"w": 80.0})
    assert encoded == b'["d",{"w":{"float8_be":"4054000000000000"}}]'
    assert struct.pack(">d", 80.0).hex() == "4054000000000000"
    negative = sa.stage_canonical_json_v1_bytes("d", {"w": -0.0})
    positive = sa.stage_canonical_json_v1_bytes("d", {"w": 0.0})
    assert negative == positive == b'["d",{"w":{"float8_be":"0000000000000000"}}]'
    wrapper = {"w": {"float8_be": "4054000000000000"}}
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        sa.decode_immutable_stage_pack(
            sa.stage_canonical_json_v1_bytes(sa.DOMAIN_STAGE_PACK_V1, wrapper)  # type: ignore[arg-type]
        )


def test_existing_globals_unchanged_after_codec_use() -> None:
    sa.stage_resolution_policy_a()
    sa.installed_stage_pack_registry()
    _later_selection()
    assert STAGE_SPELLINGS == _LEGACY_SPELLINGS
    assert resolve_stage("LATER") is None
    growth = get_stage_pack(Stage.GROWTH)
    assert growth.weights[ScoreDimension.RISK_PROFILE] == pytest.approx(0.30)
    assert growth.band_thresholds == {"HIGH": 75.0, "MEDIUM": 55.0}
    assert growth.routing_by_band[ScoreBand.HIGH] is RoutingAction.INVEST


# correction02 regressions: ordinary ImmutableStagePack construction must close
# all three pack maps before dict conversion, and incoming pack scalars must be
# already-float while policy flags must be actual booleans.


def _growth_pack() -> sa.ImmutableStagePack:
    return sa.capture_stage_pack(get_stage_pack(Stage.GROWTH))


def _pack_preimage(
    weights: tuple[tuple[str, float], ...],
    band_thresholds: tuple[tuple[str, float], ...],
    routing_by_band: tuple[tuple[str, str], ...],
) -> bytes:
    # The model sorts tuples before dict conversion; mirror that documented
    # step so duplicate members yield a preimage consistent with what the
    # constructor would canonicalize (the identity-sharing hole under test).
    body = {
        "schema_version": "stage-pack-canonical-v1",
        "stage": "GROWTH",
        "weights": dict(sorted(weights)),
        "band_thresholds": dict(sorted(band_thresholds)),
        "routing_by_band": dict(sorted(routing_by_band)),
    }
    return sa.stage_canonical_json_v1_bytes(sa.DOMAIN_STAGE_PACK_V1, body)


def _constructed_pack(
    *,
    weights: tuple[tuple[str, float], ...],
    band_thresholds: tuple[tuple[str, float], ...],
    routing_by_band: tuple[tuple[str, str], ...],
) -> sa.ImmutableStagePack:
    preimage = _pack_preimage(weights, band_thresholds, routing_by_band)
    return sa.ImmutableStagePack(
        stage=Stage.GROWTH,
        weights=weights,
        band_thresholds=band_thresholds,
        routing_by_band=routing_by_band,
        canonical_preimage=preimage,
        pack_sha256=hashlib.sha256(preimage).hexdigest(),
    )


@pytest.mark.parametrize(
    "case",
    [
        "weights_missing_dimension",
        "weights_extra_key",
        "weights_duplicate_key",
        "thresholds_missing_medium",
        "thresholds_extra_low",
        "thresholds_duplicate_high",
        "routing_missing_low",
        "routing_extra_band",
        "routing_duplicate_band",
        "routing_unknown_action",
    ],
)
def test_pack_constructor_rejects_nonclosed_maps(case: str) -> None:
    good = _growth_pack()
    weights = good.weights
    thresholds = good.band_thresholds
    routing = good.routing_by_band
    if case == "weights_missing_dimension":
        weights = tuple(item for item in weights if item[0] != "RISK_PROFILE")
    elif case == "weights_extra_key":
        weights = (*weights, ("EXTRA_DIMENSION", 0.1))
    elif case == "weights_duplicate_key":
        weights = (*weights, ("RISK_PROFILE", 0.2))
    elif case == "thresholds_missing_medium":
        thresholds = (("HIGH", 75.0),)
    elif case == "thresholds_extra_low":
        thresholds = (*thresholds, ("LOW", 40.0))
    elif case == "thresholds_duplicate_high":
        thresholds = (*thresholds, ("HIGH", 70.0))
    elif case == "routing_missing_low":
        routing = tuple(item for item in routing if item[0] != "LOW")
    elif case == "routing_extra_band":
        routing = (*routing, ("NONE", "HOLD"))
    elif case == "routing_duplicate_band":
        routing = (*routing, ("HIGH", "HOLD"))
    else:
        routing = (("HIGH", "INVEST"), ("MEDIUM", "HOLD"), ("LOW", "UNKNOWN"))
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        _constructed_pack(weights=weights, band_thresholds=thresholds, routing_by_band=routing)


def test_pack_constructor_accepts_reordered_closed_maps() -> None:
    good = _growth_pack()
    reordered = _constructed_pack(
        weights=tuple(reversed(good.weights)),
        band_thresholds=tuple(reversed(good.band_thresholds)),
        routing_by_band=tuple(reversed(good.routing_by_band)),
    )
    assert reordered == good
    assert reordered.canonical_bytes() == good.canonical_bytes()
    assert reordered.pack_sha256 == _EXPECTED_PACK_SHA256[Stage.GROWTH]
    assert reordered.to_stage_pack().weights == get_stage_pack(Stage.GROWTH).weights


@pytest.mark.parametrize(
    "incoming",
    [0, Decimal("0"), True, "0.0"],
    ids=["int", "Decimal", "bool", "str"],
)
def test_pack_constructor_rejects_nonfloat_weight(incoming: object) -> None:
    good = _growth_pack()
    weights = tuple(
        (key, incoming if key == "SCALABILITY" else value) for key, value in good.weights
    )
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        sa.ImmutableStagePack(
            stage=good.stage,
            weights=weights,  # type: ignore[arg-type]
            band_thresholds=good.band_thresholds,
            routing_by_band=good.routing_by_band,
            canonical_preimage=good.canonical_preimage,
            pack_sha256=good.pack_sha256,
        )


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
)
def test_pack_constructor_rejects_nonfinite_weight(bad: float) -> None:
    good = _growth_pack()
    weights = tuple((key, bad if key == "SCALABILITY" else value) for key, value in good.weights)
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        sa.ImmutableStagePack(
            stage=good.stage,
            weights=weights,
            band_thresholds=good.band_thresholds,
            routing_by_band=good.routing_by_band,
            canonical_preimage=good.canonical_preimage,
            pack_sha256=good.pack_sha256,
        )


def test_pack_constructor_rejects_int_threshold() -> None:
    good = _growth_pack()
    thresholds = tuple((key, 75 if key == "HIGH" else value) for key, value in good.band_thresholds)
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        sa.ImmutableStagePack(
            stage=good.stage,
            weights=good.weights,
            band_thresholds=thresholds,  # type: ignore[arg-type]
            routing_by_band=good.routing_by_band,
            canonical_preimage=good.canonical_preimage,
            pack_sha256=good.pack_sha256,
        )


@pytest.mark.parametrize(
    "field,value",
    [("native_pre_seed_admitted", 0), ("retain_raw_stage", 1)],
    ids=["numeric_false", "numeric_true"],
)
def test_policy_rejects_numeric_boolean_flags(field: str, value: int) -> None:
    good = sa.stage_resolution_policy_a()
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        sa.StageResolutionPolicyV1(
            policy_version=sa.POLICY_A_VERSION,
            spellings=dict(good.spellings),
            native_intake_stages=good.native_intake_stages,
            **{field: value},  # type: ignore[arg-type]
        )


def test_policy_flag_semantics_unchanged() -> None:
    policy = sa.stage_resolution_policy_a()
    assert policy.native_pre_seed_admitted is False
    assert policy.retain_raw_stage is True
    assert policy.canonical_sha256() == _POLICY_A_SHA256


def test_registry_selection_chain_never_accepts_invalid_pack() -> None:
    good = _growth_pack()
    duplicate = (*good.weights, ("RISK_PROFILE", 0.2))
    with pytest.raises((ValidationError, sa.StageAdmissionError)):
        _constructed_pack(
            weights=duplicate,
            band_thresholds=good.band_thresholds,
            routing_by_band=good.routing_by_band,
        )
    policy = sa.stage_resolution_policy_a()
    registry = sa.StagePackRegistry({Stage.GROWTH: good})
    selection = sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage="LATER",
        policy=policy,
        packs=registry,
    )
    returned = sa.validate_stage_selection(selection=selection, policy=policy, packs=registry)
    assert returned is good
    assert returned.to_stage_pack() == get_stage_pack(Stage.GROWTH)
