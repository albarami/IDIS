"""GL-12 inactive stage identity codec — ``stage-canonical-json-v1``.

Pure engineering preparation implementing the independently reviewed GL-12
stage preservation design-02: strict closed typed stage selection, immutable
full-pack capture and run-stage admission values plus one exact versioned
canonical byte adapter/digest.

This module is deliberately UNWIRED: no caller, package export, registry,
config, API enum, migration or runtime path references it. It is not a
durable admission authority and changes no existing resolver, pack or hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal, Self, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StrictFloat,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from idis.analysis.scoring.models import (
    STAGE_SPELLINGS,
    RoutingAction,
    ScoreBand,
    ScoreDimension,
    Stage,
    _stage_lookup_key,
)
from idis.analysis.scoring.stage_packs import StagePack, get_stage_pack

STAGE_CANONICAL_JSON_V1 = "stage-canonical-json-v1"

DOMAIN_STAGE_RESOLUTION_POLICY_V1 = "idis-stage-resolution-policy-v1"
DOMAIN_STAGE_PACK_V1 = "idis-stage-pack-v1"
DOMAIN_STAGE_SELECTION_V1 = "idis-stage-selection-v1"
DOMAIN_RUN_STAGE_ADMISSION_V1 = "idis-run-stage-admission-v1"

SCHEMA_STAGE_RESOLUTION_POLICY_V1 = "stage-resolution-policy-v1"
SCHEMA_STAGE_PACK_CANONICAL_V1 = "stage-pack-canonical-v1"
SCHEMA_STAGE_SELECTION_V1 = "stage-selection-v1"
SCHEMA_RUN_STAGE_ADMISSION_V1 = "run-stage-admission-v1"

POLICY_A_VERSION = "gl12-stage-policy-a-v1"
POLICY_A_LOOKUP_RECIPE = "python-str-casefold-split-join-v1"
POLICY_A_SHA256 = "6792f54951941fb4fe892fdf7e662cf982649e420309d7effb07be00da446e0b"

# Code-owned expected digests of the five installed public stage packs under
# stage-canonical-json-v1. These pin existing public values; they are not new
# weights and install no methodology change.
EXPECTED_STAGE_PACK_SHA256: dict[Stage, str] = {
    Stage.PRE_SEED: "2264602661e24fbcb5b2a5ae227708490d16ab68eaac639621468913fc086c18",
    Stage.SEED: "49a1d3351da9365acd272a487a2af8ae40b753489f26714ea991c422337622e2",
    Stage.SERIES_A: "32f32c655a38fcf60a52c2eb4c4f4948bcbd40b997a6513beae75e32a858589c",
    Stage.SERIES_B: "730f3e6546daf6df468d6d9a248c2cbe81a2b960e3a3a02bee3774a75ce06a62",
    Stage.GROWTH: "2f3a18292e47d2b0e588c53cdcdbd795324dda947d59376bd85bc062642449ae",
}

STAGE_UNKNOWN = "STAGE_UNKNOWN"
STAGE_ADMISSION_INVALID = "STAGE_ADMISSION_INVALID"
STAGE_RESOLUTION_POLICY_MISMATCH = "STAGE_RESOLUTION_POLICY_MISMATCH"
STAGE_PACK_MISMATCH = "STAGE_PACK_MISMATCH"
STAGE_PACK_UNAVAILABLE = "STAGE_PACK_UNAVAILABLE"

_CANONICAL_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
_FLOAT8_BE_RE = re.compile(r"[0-9a-f]{16}")
_FLOAT8_BE_KEY = "float8_be"

_DIMENSION_KEYS = frozenset(dimension.value for dimension in ScoreDimension)
_THRESHOLD_KEYS = frozenset({"HIGH", "MEDIUM"})
_BAND_KEYS = frozenset(band.value for band in ScoreBand)
_ROUTING_VALUES = frozenset(action.value for action in RoutingAction)
_PACK_BODY_KEYS = frozenset(
    {"band_thresholds", "routing_by_band", "schema_version", "stage", "weights"}
)

_K = TypeVar("_K")
_V = TypeVar("_V")

_PACK_KEYSETS: Mapping[str, frozenset[str]] = {
    "weights": _DIMENSION_KEYS,
    "band_thresholds": _THRESHOLD_KEYS,
    "routing_by_band": _BAND_KEYS,
}


def _finite_pack_float(value: object) -> object:
    """Refuse non-float pack entries before StrictFloat coercion (int/Decimal)."""
    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError("pack numeric entries must be finite binary64 floats")
    return value


def _bool_flag(value: object) -> object:
    """Refuse numeric literals before Literal bool coercion (0/1)."""
    if not isinstance(value, bool):
        raise ValueError("policy flags must be actual booleans")
    return value


_PackFloat = Annotated[StrictFloat, BeforeValidator(_finite_pack_float)]
_BoolFlag = BeforeValidator(_bool_flag)


class StageAdmissionError(Exception):
    """Code-only stage admission failure. ``str`` of the error is the code."""

    def __init__(self, code: str) -> None:
        """Store the safe categorical code."""
        self.code = code
        super().__init__(code)


class NativeIntakeStageLabel(StrEnum):
    """Exact existing native API stage intake labels, in declared order."""

    SEED = "SEED"
    SERIES_A = "SERIES_A"
    SERIES_B = "SERIES_B"
    GROWTH = "GROWTH"
    LATER = "LATER"
    OTHER = "OTHER"


def _raw_text(value: object) -> str:
    if not isinstance(value, str) or any(
        unicodedata.category(character) == "Cs" for character in value
    ):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return value


def _float8_be(value: float) -> dict[str, str]:
    if not math.isfinite(value):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    normalized = 0.0 if value == 0.0 else value
    return {_FLOAT8_BE_KEY: struct.pack(">d", normalized).hex()}


def _unwrap_float8(value: object) -> float:
    if not isinstance(value, Mapping) or set(value) != {_FLOAT8_BE_KEY}:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    encoded = value[_FLOAT8_BE_KEY]
    if not isinstance(encoded, str) or not _FLOAT8_BE_RE.fullmatch(encoded):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    number = float(struct.unpack(">d", bytes.fromhex(encoded))[0])
    negative_zero = number == 0.0 and math.copysign(1.0, number) < 0.0
    if not math.isfinite(number) or negative_zero:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return number


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        return _float8_be(value)
    if isinstance(value, str):
        return _raw_text(value)
    if isinstance(value, Mapping):
        return {_raw_text(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise StageAdmissionError(STAGE_ADMISSION_INVALID)


def stage_canonical_json_v1_bytes(domain_tag: str, body: Mapping[str, object]) -> bytes:
    """Encode ``[domain_tag, body]`` into exact canonical ASCII bytes."""
    _raw_text(domain_tag)
    if not isinstance(body, Mapping):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    transformed = _canonical_value(dict(body))
    try:
        return json.dumps(
            [domain_tag, transformed],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc


def stage_canonical_json_v1_sha256(domain_tag: str, body: Mapping[str, object]) -> str:
    """Return the lowercase SHA-256 hex of the canonical bytes."""
    return hashlib.sha256(stage_canonical_json_v1_bytes(domain_tag, body)).hexdigest()


def stage_canonical_json_v1_decode(domain_tag: str, data: bytes) -> dict[str, object]:
    """Decode canonical bytes, requiring exact domain and byte round-trip."""
    if not isinstance(data, (bytes, bytearray)):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    try:
        text = bytes(data).decode("ascii")
    except UnicodeDecodeError as exc:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 2
        or payload[0] != domain_tag
        or not isinstance(payload[1], dict)
        or stage_canonical_json_v1_bytes(domain_tag, payload[1]) != bytes(data)
    ):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return payload[1]


def _canonical_uuid_field(value: str | None) -> str | None:
    if value is None or _CANONICAL_UUID_RE.fullmatch(value):
        return value
    raise ValueError("expected canonical lowercase hyphenated UUID string")


def _sha256_hex_field(value: str | None) -> str | None:
    if value is None or _SHA256_HEX_RE.fullmatch(value):
        return value
    raise ValueError("expected 64 lowercase hex characters")


def _nonempty_field(value: str) -> str:
    if value:
        return value
    raise ValueError("expected non-empty string")


def _expect_map(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return value


def _closed_keys(body: Mapping[str, object], expected: frozenset[str]) -> None:
    if set(body) != expected:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)


_UuidStr = Annotated[StrictStr, AfterValidator(_canonical_uuid_field)]
_OptUuidStr = Annotated[StrictStr | None, AfterValidator(_canonical_uuid_field)]
_ShaStr = Annotated[StrictStr, AfterValidator(_sha256_hex_field)]
_OptShaStr = Annotated[StrictStr | None, AfterValidator(_sha256_hex_field)]
_NonemptyStr = Annotated[StrictStr, AfterValidator(_nonempty_field)]


def _body_value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, _StageCanonicalModel):
        return value.canonical_body()
    if isinstance(value, Mapping):
        return {key: _body_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_body_value(item) for item in value]
    return value


class _StageCanonicalModel(BaseModel):
    """Closed frozen base for the four versioned canonical models."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    _domain: ClassVar[str]
    _body_fields: ClassVar[frozenset[str] | None] = None

    def canonical_domain(self) -> str:
        """Return the exact versioned domain tag for this model."""
        return self._domain

    def canonical_body(self) -> dict[str, object]:
        """Return the exact closed JSON body used for the preimage."""
        names = self._body_fields or type(self).model_fields.keys()
        return {name: _body_value(getattr(self, name)) for name in names}

    def canonical_bytes(self) -> bytes:
        """Return ``stage_canonical_json_v1_bytes(domain, body)``."""
        return stage_canonical_json_v1_bytes(self.canonical_domain(), self.canonical_body())

    def canonical_sha256(self) -> str:
        """Return the lowercase SHA-256 hex of ``canonical_bytes``."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class StageResolutionPolicyV1(_StageCanonicalModel):
    """Closed approved stage-resolution policy object (policy A)."""

    schema_version: Literal["stage-resolution-policy-v1"] = "stage-resolution-policy-v1"
    policy_version: StrictStr
    lookup_recipe: Literal["python-str-casefold-split-join-v1"] = (
        "python-str-casefold-split-join-v1"
    )
    spellings: Mapping[StrictStr, Stage]
    native_intake_stages: tuple[NativeIntakeStageLabel, ...]
    native_pre_seed_admitted: Annotated[Literal[False], _BoolFlag] = False
    missing_or_unknown_full: Literal["STAGE_UNKNOWN"] = "STAGE_UNKNOWN"
    retain_raw_stage: Annotated[Literal[True], _BoolFlag] = True

    @field_validator("spellings")
    @classmethod
    def _normalized_keys(cls, value: Mapping[str, Stage]) -> Mapping[str, Stage]:
        for key in value:
            if not key or _stage_lookup_key(key) != key:
                raise ValueError("spellings keys must be normalized lookup keys")
        return MappingProxyType(dict(value))

    @field_validator("native_intake_stages")
    @classmethod
    def _complete_intake(
        cls, value: tuple[NativeIntakeStageLabel, ...]
    ) -> tuple[NativeIntakeStageLabel, ...]:
        if len(value) != len(NativeIntakeStageLabel) or set(value) != set(NativeIntakeStageLabel):
            raise ValueError("native_intake_stages must list each label exactly once")
        return value

    _domain: ClassVar[str] = DOMAIN_STAGE_RESOLUTION_POLICY_V1


class ImmutableStagePack(_StageCanonicalModel):
    """Immutable captured stage pack: canonical bytes plus scalar tuples."""

    _domain: ClassVar[str] = DOMAIN_STAGE_PACK_V1
    _body_fields: ClassVar[frozenset[str] | None] = _PACK_BODY_KEYS

    schema_version: Literal["stage-pack-canonical-v1"] = "stage-pack-canonical-v1"
    stage: Stage
    weights: tuple[tuple[StrictStr, _PackFloat], ...]
    band_thresholds: tuple[tuple[StrictStr, _PackFloat], ...]
    routing_by_band: tuple[tuple[StrictStr, StrictStr], ...]
    canonical_preimage: bytes
    pack_sha256: _ShaStr

    @field_validator("weights", "band_thresholds", "routing_by_band")
    @classmethod
    def _sorted_tuples(
        cls, value: tuple[tuple[str, object], ...]
    ) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(value))

    @field_validator("weights", "band_thresholds", "routing_by_band")
    @classmethod
    def _closed_keyset(
        cls, value: tuple[tuple[str, object], ...], info: ValidationInfo
    ) -> tuple[tuple[str, object], ...]:
        expected = _PACK_KEYSETS.get(info.field_name) if info.field_name else None
        if expected is None:
            raise ValueError("unexpected pack map field")
        keys = [key for key, _ in value]
        if len(keys) != len(expected) or frozenset(keys) != expected:
            raise ValueError(f"{info.field_name} must contain each expected key exactly once")
        if info.field_name == "routing_by_band" and any(
            action not in _ROUTING_VALUES for _, action in value
        ):
            raise ValueError("routing_by_band values must be existing routing actions")
        return value

    @model_validator(mode="after")
    def _consistent_preimage(self) -> Self:
        if self.canonical_bytes() != self.canonical_preimage:
            raise ValueError("canonical_preimage does not match captured members")
        digest = hashlib.sha256(self.canonical_preimage).hexdigest()
        if digest != self.pack_sha256:
            raise ValueError("pack_sha256 does not match canonical_preimage")
        return self

    def canonical_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage.value,
            "weights": dict(self.weights),
            "band_thresholds": dict(self.band_thresholds),
            "routing_by_band": dict(self.routing_by_band),
        }

    def to_stage_pack(self) -> StagePack:
        """Hydrate a fresh private execution pack from the verified bytes."""
        try:
            return StagePack(
                stage=self.stage,
                weights={ScoreDimension(key): value for key, value in self.weights},
                band_thresholds=dict(self.band_thresholds),
                routing_by_band={
                    ScoreBand(key): RoutingAction(value) for key, value in self.routing_by_band
                },
            )
        except ValueError as exc:
            raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc


class StageSelectionV1(_StageCanonicalModel):
    """Closed typed stage selection value (stage-selection-v1)."""

    schema_version: Literal["stage-selection-v1"] = "stage-selection-v1"
    tenant_id: _UuidStr
    deal_id: _UuidStr
    raw_stage: StrictStr
    resolved_stage: Stage
    resolution_policy_version: StrictStr
    resolution_policy_sha256: _ShaStr
    pack_stage: Stage
    pack_schema_version: Literal["stage-pack-canonical-v1"] = "stage-pack-canonical-v1"
    pack_sha256: _ShaStr

    @field_validator("raw_stage")
    @classmethod
    def _raw_preserved(cls, value: str) -> str:
        if not value:
            raise ValueError("raw_stage must not be empty")
        if any(unicodedata.category(character) == "Cs" for character in value):
            raise ValueError("raw_stage must not contain lone surrogates")
        return value

    @model_validator(mode="after")
    def _pack_matches_resolution(self) -> Self:
        if self.pack_stage != self.resolved_stage:
            raise ValueError("pack_stage must equal resolved_stage")
        return self

    _domain: ClassVar[str] = DOMAIN_STAGE_SELECTION_V1


class RunStageAdmissionV1(_StageCanonicalModel):
    """Closed typed run stage admission value (run-stage-admission-v1)."""

    schema_version: Literal["run-stage-admission-v1"] = "run-stage-admission-v1"
    tenant_id: _UuidStr
    deal_id: _UuidStr
    run_id: _UuidStr
    mode: Literal["FULL"] = "FULL"
    selection: StageSelectionV1
    stage_selection_sha256: _ShaStr
    pipeline_contract_version: _NonemptyStr
    pipeline_contract_hash: _ShaStr
    strict_preflight_proof_id: _OptUuidStr
    binding_sha256: _OptShaStr

    @model_validator(mode="after")
    def _consistent_admission(self) -> Self:
        if (self.strict_preflight_proof_id is None) != (self.binding_sha256 is None):
            raise ValueError("proof fields must be both null or both present")
        if self.tenant_id != self.selection.tenant_id or self.deal_id != self.selection.deal_id:
            raise ValueError("admission scope must match selection scope")
        if self.stage_selection_sha256 != self.selection.canonical_sha256():
            raise ValueError("stage_selection_sha256 must match selection digest")
        return self

    _domain: ClassVar[str] = DOMAIN_RUN_STAGE_ADMISSION_V1


class RunIdentityV1(BaseModel):
    """Typed run identity input for building an admission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: _UuidStr
    deal_id: _UuidStr
    run_id: _UuidStr
    pipeline_contract_version: _NonemptyStr
    pipeline_contract_hash: _ShaStr


class StrictProofIdentityV1(BaseModel):
    """Typed strict proof identity input for building an admission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strict_preflight_proof_id: _UuidStr
    binding_sha256: _ShaStr


class StagePackRegistry:
    """Code-owned registry of captured immutable pack identities."""

    __slots__ = ("_packs",)

    def __init__(self, packs: Mapping[Stage, ImmutableStagePack]) -> None:
        mapping = dict(packs)
        for stage, pack in mapping.items():
            if (
                not isinstance(stage, Stage)
                or not isinstance(pack, ImmutableStagePack)
                or pack.stage is not stage
            ):
                raise StageAdmissionError(STAGE_PACK_MISMATCH)
        self._packs = mapping

    def get(self, stage: Stage) -> ImmutableStagePack | None:
        """Return the captured pack for ``stage`` or None when absent."""
        return self._packs.get(stage)

    def stages(self) -> tuple[Stage, ...]:
        """Return the registered stages."""
        return tuple(self._packs)


def _pack_float_map(
    values: Mapping[_K, _V], expected: frozenset[str]
) -> tuple[tuple[str, float], ...]:
    items: list[tuple[str, float]] = []
    for key, value in values.items():
        if not isinstance(key, str) or str(key) not in expected:
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        if not isinstance(value, float) or not math.isfinite(value):
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        items.append((str(key), value))
    if {key for key, _ in items} != expected:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return tuple(sorted(items))


def _pack_routing_map(values: Mapping[_K, _V]) -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = []
    for key, value in values.items():
        if not isinstance(key, str) or str(key) not in _BAND_KEYS:
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        if not isinstance(value, (str, RoutingAction)) or str(value) not in _ROUTING_VALUES:
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        items.append((str(key), str(value)))
    if {key for key, _ in items} != _BAND_KEYS:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return tuple(sorted(items))


def _decode_float_map(value: object, expected: frozenset[str]) -> tuple[tuple[str, float], ...]:
    unwrapped = {_raw_text(key): _unwrap_float8(item) for key, item in _expect_map(value).items()}
    return _pack_float_map(unwrapped, expected)


def capture_stage_pack(pack: StagePack) -> ImmutableStagePack:
    """Capture an existing validated pack into immutable canonical identity."""
    if not isinstance(pack, StagePack) or not isinstance(pack.stage, Stage):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    weights = _pack_float_map(pack.weights, _DIMENSION_KEYS)
    thresholds = _pack_float_map(pack.band_thresholds, _THRESHOLD_KEYS)
    routing = _pack_routing_map(pack.routing_by_band)
    body = {
        "schema_version": SCHEMA_STAGE_PACK_CANONICAL_V1,
        "stage": pack.stage.value,
        "weights": dict(weights),
        "band_thresholds": dict(thresholds),
        "routing_by_band": dict(routing),
    }
    preimage = stage_canonical_json_v1_bytes(DOMAIN_STAGE_PACK_V1, body)
    return ImmutableStagePack(
        stage=pack.stage,
        weights=weights,
        band_thresholds=thresholds,
        routing_by_band=routing,
        canonical_preimage=preimage,
        pack_sha256=hashlib.sha256(preimage).hexdigest(),
    )


def installed_stage_pack_registry() -> StagePackRegistry:
    """Capture the five installed public packs and verify expected digests."""
    captured: dict[Stage, ImmutableStagePack] = {}
    for stage in Stage:
        pack = capture_stage_pack(get_stage_pack(stage))
        if pack.pack_sha256 != EXPECTED_STAGE_PACK_SHA256[stage]:
            raise StageAdmissionError(STAGE_PACK_MISMATCH)
        captured[stage] = pack
    return StagePackRegistry(captured)


def stage_resolution_policy_a() -> StageResolutionPolicyV1:
    """Build policy A from the closed spellings plus the approved LATER alias."""
    spellings = dict(STAGE_SPELLINGS)
    spellings["later"] = Stage.GROWTH
    policy = StageResolutionPolicyV1(
        policy_version=POLICY_A_VERSION,
        spellings=spellings,
        native_intake_stages=tuple(NativeIntakeStageLabel),
    )
    if policy.canonical_sha256() != POLICY_A_SHA256:
        raise StageAdmissionError(STAGE_RESOLUTION_POLICY_MISMATCH)
    return policy


def _require_policy_a(policy: StageResolutionPolicyV1) -> None:
    if (
        not isinstance(policy, StageResolutionPolicyV1)
        or policy.policy_version != POLICY_A_VERSION
        or policy.canonical_sha256() != POLICY_A_SHA256
    ):
        raise StageAdmissionError(STAGE_RESOLUTION_POLICY_MISMATCH)


def _resolve_with_policy(raw_stage: object, policy: StageResolutionPolicyV1) -> Stage:
    code = policy.missing_or_unknown_full
    if not isinstance(raw_stage, str):
        raise StageAdmissionError(code)
    key = _stage_lookup_key(raw_stage)
    if not key:
        raise StageAdmissionError(code)
    resolved = policy.spellings.get(key)
    if resolved is None:
        raise StageAdmissionError(code)
    return resolved


def _require_registry(packs: StagePackRegistry) -> StagePackRegistry:
    if not isinstance(packs, StagePackRegistry):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return packs


def capture_stage_selection(
    *,
    tenant_id: str,
    deal_id: str,
    raw_stage: str,
    policy: StageResolutionPolicyV1,
    packs: StagePackRegistry,
) -> StageSelectionV1:
    """Capture a validated selection; ineligible stages fail code-only."""
    _require_policy_a(policy)
    registry = _require_registry(packs)
    resolved = _resolve_with_policy(raw_stage, policy)
    pack = registry.get(resolved)
    if pack is None:
        raise StageAdmissionError(STAGE_PACK_UNAVAILABLE)
    if pack.stage is not resolved:
        raise StageAdmissionError(STAGE_PACK_MISMATCH)
    return StageSelectionV1(
        tenant_id=tenant_id,
        deal_id=deal_id,
        raw_stage=raw_stage,
        resolved_stage=resolved,
        resolution_policy_version=policy.policy_version,
        resolution_policy_sha256=policy.canonical_sha256(),
        pack_stage=resolved,
        pack_sha256=pack.pack_sha256,
    )


def validate_stage_selection(
    *,
    selection: StageSelectionV1,
    policy: StageResolutionPolicyV1,
    packs: StagePackRegistry,
) -> ImmutableStagePack:
    """Re-verify a selection and return its immutable executable pack."""
    _require_policy_a(policy)
    registry = _require_registry(packs)
    if not isinstance(selection, StageSelectionV1):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    if (
        selection.resolution_policy_version != policy.policy_version
        or selection.resolution_policy_sha256 != policy.canonical_sha256()
    ):
        raise StageAdmissionError(STAGE_RESOLUTION_POLICY_MISMATCH)
    resolved = _resolve_with_policy(selection.raw_stage, policy)
    if resolved is not selection.resolved_stage:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    pack = registry.get(resolved)
    if pack is None:
        raise StageAdmissionError(STAGE_PACK_UNAVAILABLE)
    if pack.stage is not resolved or pack.pack_sha256 != selection.pack_sha256:
        raise StageAdmissionError(STAGE_PACK_MISMATCH)
    selection.canonical_sha256()
    return pack


def build_run_stage_admission(
    *,
    selection: StageSelectionV1,
    run_identity: RunIdentityV1,
    proof_identity: StrictProofIdentityV1 | None = None,
) -> RunStageAdmissionV1:
    """Build a scoped admission; strict proof pair is all-or-nothing."""
    if (
        not isinstance(selection, StageSelectionV1)
        or not isinstance(run_identity, RunIdentityV1)
        or (proof_identity is not None and not isinstance(proof_identity, StrictProofIdentityV1))
        or run_identity.tenant_id != selection.tenant_id
        or run_identity.deal_id != selection.deal_id
    ):
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return RunStageAdmissionV1(
        tenant_id=run_identity.tenant_id,
        deal_id=run_identity.deal_id,
        run_id=run_identity.run_id,
        selection=selection,
        stage_selection_sha256=selection.canonical_sha256(),
        pipeline_contract_version=run_identity.pipeline_contract_version,
        pipeline_contract_hash=run_identity.pipeline_contract_hash,
        strict_preflight_proof_id=(
            None if proof_identity is None else proof_identity.strict_preflight_proof_id
        ),
        binding_sha256=None if proof_identity is None else proof_identity.binding_sha256,
    )


def _decode_body(domain: str, expected: frozenset[str], data: bytes) -> dict[str, object]:
    body = stage_canonical_json_v1_decode(domain, data)
    _closed_keys(body, expected)
    return body


def _construct(model: type[BaseModel], body: Mapping[str, object]) -> BaseModel:
    try:
        return model(**dict(body))
    except (ValueError, TypeError) as exc:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc


_POLICY_BODY_KEYS = frozenset(StageResolutionPolicyV1.model_fields)
_SELECTION_BODY_KEYS = frozenset(StageSelectionV1.model_fields)
_ADMISSION_BODY_KEYS = frozenset(RunStageAdmissionV1.model_fields)


def decode_stage_resolution_policy_v1(data: bytes) -> StageResolutionPolicyV1:
    """Decode canonical bytes into a policy model, fail-closed."""
    body = _decode_body(DOMAIN_STAGE_RESOLUTION_POLICY_V1, _POLICY_BODY_KEYS, data)
    policy = _construct(StageResolutionPolicyV1, body)
    assert isinstance(policy, StageResolutionPolicyV1)
    return policy


def decode_immutable_stage_pack(data: bytes) -> ImmutableStagePack:
    """Decode canonical bytes into an immutable pack, fail-closed."""
    body = _decode_body(DOMAIN_STAGE_PACK_V1, _PACK_BODY_KEYS, data)
    try:
        return ImmutableStagePack(
            stage=Stage(_raw_text(body["stage"])),
            weights=_decode_float_map(body["weights"], _DIMENSION_KEYS),
            band_thresholds=_decode_float_map(body["band_thresholds"], _THRESHOLD_KEYS),
            routing_by_band=_pack_routing_map(_expect_map(body["routing_by_band"])),
            canonical_preimage=bytes(data),
            pack_sha256=hashlib.sha256(bytes(data)).hexdigest(),
        )
    except (ValueError, TypeError) as exc:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc


def decode_stage_selection_v1(data: bytes) -> StageSelectionV1:
    """Decode canonical bytes into a selection model, fail-closed."""
    body = _decode_body(DOMAIN_STAGE_SELECTION_V1, _SELECTION_BODY_KEYS, data)
    selection = _construct(StageSelectionV1, body)
    assert isinstance(selection, StageSelectionV1)
    return selection


def decode_run_stage_admission_v1(data: bytes) -> RunStageAdmissionV1:
    """Decode canonical bytes into an admission model, fail-closed."""
    body = _decode_body(DOMAIN_RUN_STAGE_ADMISSION_V1, _ADMISSION_BODY_KEYS, data)
    _closed_keys(_expect_map(body["selection"]), _SELECTION_BODY_KEYS)
    admission = _construct(RunStageAdmissionV1, body)
    assert isinstance(admission, RunStageAdmissionV1)
    return admission
