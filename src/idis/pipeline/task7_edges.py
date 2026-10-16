"""Deterministic expected-edge derivation and three-way receipt equality."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, NoReturn, Protocol, cast
from uuid import UUID

from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.task7_producer_selectors_generated import (
    PIPELINE_CONTRACT_HASH as _SELECTOR_CONTRACT_HASH,
)
from idis.pipeline.task7_producer_selectors_generated import (
    PIPELINE_CONTRACT_VERSION as _SELECTOR_CONTRACT_VERSION,
)
from idis.pipeline.task7_producer_selectors_generated import (
    PRODUCER_SELECTOR_CANONICAL_JSON_SHA256,
    PRODUCER_SELECTOR_CANONICAL_JSON_ZLIB,
    PRODUCER_SELECTOR_ROW_COUNT,
)
from idis.pipeline.task7_producer_selectors_generated import (
    PRODUCER_SELECTOR_REGISTRY_SHA256 as _GENERATED_SELECTOR_REGISTRY_SHA256,
)
from idis.pipeline.task7_usage import USAGE_ROUTE_REGISTRY, ProducerRefV1
from idis.pipeline.task7_uuid import Task7UuidError, derive_task7_uuid

CONSUMER_RECEIPT_INCOMPLETE: Final = "CONSUMER_RECEIPT_INCOMPLETE"
PIPELINE_CONTRACT_HASH_MISMATCH: Final = "PIPELINE_CONTRACT_HASH_MISMATCH"

_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EDGE_RULE_AUTHORITY_B85: Final = (
    "c-pO5*=`%h7W@}K=b_Ki+aoQFEaFn6KvGVOARv1E|4+EpO>ME_n2`lTvd9^Eif5^%{pX+Nw@-^PbUN%F_E*b"
    "kz5TjukGuW1)9U(eeK@Rl+fToJB7A*+1KxVrUhTHm>mzPjZSZ=v`F-{6@OXb9o3&T5(MMOUW^YK6jfOPTP`R"
    "cqO}(`ej5uE-ktr<JHF6<4`{}RW{-g<xpTOO&w%5Cxn;$`Yy!<m@WJ~Q-qbb2CzB$*LkD*BO%`pB@wjsA>Y^"
    "CU=7B;n&P_q{A>lqkNVDhS5%WA**b~+rF`={p|mb=yVxW2+o_YcRPqAER_T2#GflHgMA0(3^mw^pbmBc1|N4"
    "J<`-Eh%zNF+@$-TBU(XCMtb}>U!C%zb^aL=go53FL?abdb3^~UrP7)H!p)~j19FEGE8csdZ|K@fCQED!I<15"
    "YwccADJ@8rXw`+p&01Tt@dK((sP?&~_v?E=-7fE{T_%qQILihPsj=7If>$5W$fOv<pryB#Jh5^>H!YL1q^?D"
    "qh^-7jb^>zGfL>MZ=`pX1d0C-)P{kBAAxCPvubOJ`&9>NDW0q(_Y#D8~-Dd1X%SEjzxF@8}dEjyrm&XIYEQj"
    "OixI5kNmz&M{%kA-9wbCx3a8_%hI1(?UnUh7!lK5zadhi`(>Ct1}Y%18>?6h%kQL~K$lb@Kv3zA=KU*(90bG"
    "F`8j81IqLmhSVYE#aUgQVt@OTEWnHL-E6ODtmnY@h856H+`~?M8>bEVEFX)pm=z{Rq_R{(jkBpLSa;E`Hvuu"
    "U4P23CFQESL;W985?b%FK?bcp#oITWL!2}ZDEj>?WtOqn@Xov(M&GQmuf0Xmq92+Z1Jj9QC}UH<G@VjZ<w!F"
    "huhD))&BZ@%}qXdiaGmiYj@EpN@S_}pjNmLcje{)EclF}<CgG#4na9aTjFdkO+_Gjf%Orq(4A?;I-fImw&Gi"
    "r1j_9tV!lBB)>TkRD%zW)YBSJus0EhA8MQilOj8bsPsi2af1_B-AIsIlD>D1g4omh#4jNHRV=;`Su%2u)8aO"
    "u_T>?_hpkqnZFxV%O#)JWIg1xB|@ze<taPQ$^Gq?9@vs&N1tGT9tZt*o10yILU)n0=Y%fUpct<~aN2Op#1y="
    "Wp#S|U`OS(~qI0-6#*dWJD;_;r1~Y@wcq)pqs1sq>vIIfH?Bu?NcCgW0@^K!_p*IDm#8X9nRqZyMOlfMvv|G"
    ")S*aJ)kKFWMA+A+xsC!h;xGNNa$Zr>K*7K)z*!V93kemtMPCjnZP9+p|yk$(Q%s->*Kk3QxeFXVGKNX%kg%1"
    "eY#q0uHJ>J^^}@6JG#>$KqkdlwZ~)){73Yqi8Z}Dt09Lfrm4#=!VWa)(1|muDG20WgZXvh@HJPkg&)=kybqu"
    ";*W8TriZO>)IM!||RE#)bXE|bXEA`}DOll^b(Ub)WPsrBW<Fd!IPS?BNw}<0?S-o%WA>snYOu+*TTv)T9FN4"
    "vtu7Dw`h^v@=*F&w`0xVR)?Dqg0kSW*mcujqv_!ip-a2wZdqedUPOVDkqrez`2t;bF&#Wq&9MEDPgufi(zVj"
    ")`=Rt4r#&fGS|fDJh9xL;izPhj3N#m$#bZVhi<1j&X{3{)Kp$<WS@fl5FX9flwK2XZj3LL`dD?!mA<dxjlP-"
    "eVUaVbkMIAz*rj1UO&ztLyiPJV+;*00$I}lUA_yp>xtC%>-*rO5I}tN(?mul7x;XTPVI1<FGb75lsPLKEs%7"
    "`rcJ^Ek(gc_plTMaq<C7*+3ZHp-%x~SyG2iVaQW5T5W_mQD{8&x1|&&oXPGir!UKP+0Q3`%)~P?Q1sFbToLt"
    "_U1c!4aSGD(88t&|1Li8CI~Y0FRC8nG6$}=UpILw>)3g2qxDOb4ZxGZ<?gWlNddiR-=y#F=8T23!$biJ$f`$"
    "Z{!h#kVM$W_Rh5*i;2q)#UJq^M?4pE-wb~glI|MZ}z*Z*D8LEO*3{6^d{idsze9_cUGhR##0aBqb<U_d(<ge"
    "c=@tu8TaN=8<#E~2>jjXpVrr#`TI!M{7+;$bM;;o<J(BRpTetg?*GB1jnnN>*4QvDSC#Vh~~ah&N0L5}(*ft"
    "S)<)5`3!=5NuOF_kL;w`xj&%(Q~Y!&#4fm4GtGx2NY)-Ax~61oq`IuF3=#89uiCuLqamlL4qZadd|&LBRIUY"
    "X7_Ns+TAUv+TGo=kz2Ny*xNgRztCO6J`!vN{)&P?JgjyxgGMYkH78hKK!?zSVIW`PF|CM%HN`rv_X%xE1jiR"
    ">H-q$^Hus&#U?ELp(=&`(1g)?MCX+>@yzi}Ksvum8LWPdB>|oFV*2RiYF@nO3fIPK9%y-B>qNfN^p#7dm$OJ"
    "}iF)bn88_vT^76Zw}s9^vR@{MxQ$`ZhU++kLlpL-xvCxiwo-{1ZV2|iuF$d9nVM_ck8E7_4>$U@|i1YG7GGm"
    "K-Y0_)LB2?;)csUp7C#spL1r+&#)3*pmQHGhQaa!tj+bAhJER4QZu<^wr2iwOwSfn6BspuubBg20D)glR*zw"
    "jO(1i_TWclnjx_T0vQVy;i{&m@x!nBxbJYP;8;9Ag92jHAcuoTo7TC$^Z#h38l>n$0W#O>L#wK7NWCOKSp(N"
    "twz9egw802CSY=)PjHU#k1*fto%8Uu;9P2=qNtHcreZD7Q^{RtR*|U>Vh5q#fWD9a(!^J{T!60n6g>j5<wPY"
    "Xg5l`VtEtA#T$d2#ObNw=%7sU)0x!UqfO3mVd8(I8_80ep?#E^OZ|LkiP#}{!SR>>(rfdW!b;u}CRS2vD#VT"
    "^BO(3{lk*Jf07l6-8ay|*13LqZn9`?t7LNwmKtXTNO%^*n9IGc4jCe$kUF*@8GGV(b6Lq=|n?W|qs8QTeWj_"
    "SdkPdJnIC7ix4_iw?BH!ou=bR^Vf5t(`)vlUYsr?XI|-Xv#Kp?LB&ju>3wq7W|&#tv~*5JAtCnKUmE^!a&pT"
    ";8I2eE%Xq#V0K}q9mc!CdN-Ta^zFi5u)ro$}K4r5Q0{erIWLP2Xh!((gZZwUCjw8bMx)6KD>qWeC0x(8b>~O"
    "l7ambKoa$)`cez7fY577h)D34Ft%WPp8~WH&asx(d*d>XXD6pCoeo#K{d)W59~|xDh0FB0V|tY<KD(OS$iO<"
    "ldKquK=UAkXI4jvX<WJC;XLw88NG-&Kh(2dXC!H(kd|6$ux69!${NiTyZQ1klVfNFN%TQsKorH`kb%mrrM6l"
    "4vm6~j7F$8oEN-R>d#oH$3V4QQMjhFF!Y^;;eRUU!NB>q;HA1_{ZNC1OUu*N=Bg2vQ5a$L3O6!ytaPsnIjVG"
    "Rhl#`@9K4y}}I5t?RD(n;s4r`7(r9%f<I3PxsqvmBnn<mZR&HGIO|a`zc#^>%&#^B`_R2)%)mMCTa>h6uC6&"
    "^m<yke+51FwRGW%{)%VpfyUvTZ5t|vPtS{zx&^akAO$Y$hZ_r?G0iQDMIve$zF0R)eMiQ$wxut3|?bwpe|P("
    "78sj>pfcf2R#*2bF}ykl{b{QEmGee|3UW}C7!~z!UKzzp5KDLmaaaaZK=~mHLEW>HUL&>`?%H#PN}m5$WzxR"
    "BGXBrUY4e<D;(T8&aRPfyp2rz21T$b&dluMj_}3obyi{yQ3{kKN_|P+=D%KnEN;-Z%S9i*QhVP8*vvA_^N4)"
    "s4)n4D-Jsf9K-sFdy^|BdfU*G>@_}%N}kJD<i+5Nse9gan1kek7lV~llJj})k2K$_|B!;~G;6P!{m)U}e68d"
    "y6%qsg)2+o!+&`aj#czL@"
)


class EdgeDerivationError(RuntimeError):
    """Stable fail-closed edge derivation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EdgeRuleSpec:
    """One admitted expected-edge rule projection."""

    rule_ordinal: int
    rule_id: str
    rule_version: str
    usage_kind: str
    predicate_kind: str
    predicate_id: str
    predicate_version: str
    rule_sha256: str


@dataclass(frozen=True, slots=True)
class ProducerSelectorSpec:
    """One generated concrete selector instance from the admitted authority."""

    rule_ordinal: int
    rule_id: str
    rule_version: str
    selector_ordinal: int
    parameter_tuple: tuple[tuple[str, object], ...]
    selector_ast: tuple[object, ...]
    selector_sha256: str
    base_selector_id: str
    relations: tuple[str, ...]
    joins: tuple[tuple[str, str], ...]
    projection: tuple[str, ...]
    predicate_ast: tuple[object, ...]
    order_by: tuple[str, ...]
    producer_condition: str

    @property
    def producer_type(self) -> str:
        return self.projection[0]

    @property
    def producer_id_column(self) -> str:
        return self.projection[1]

    @property
    def producer_identity_hash_column(self) -> str:
        return self.projection[2]

    @property
    def producer_value_hash_column(self) -> str:
        return self.projection[3]

    @property
    def producer_outcome_column(self) -> str | None:
        return self.projection[4] if len(self.projection) == 5 else None


@dataclass(frozen=True, slots=True)
class ProducerSelectionRequest:
    """Exact tenant/deal/run scope for all reviewed producer selectors."""

    tenant_id: UUID
    deal_id: UUID
    run_id: UUID
    pipeline_contract_version: str = _FULL_STRICT_V21_VERSION
    pipeline_contract_hash: str = FULL_STRICT_V21_CONTRACT_HASH


class ProducerSelectorRowLoader(Protocol):
    """Transaction-local selector executor supplied by the PostgreSQL port."""

    def __call__(
        self,
        spec: ProducerSelectorSpec,
        **scope: object,
    ) -> Sequence[Mapping[str, object]]: ...


@dataclass(frozen=True, slots=True)
class ImmutableProducerFact:
    """One typed immutable producer row selected for a concrete rule."""

    rule_id: str
    producer_condition: str
    producer: ProducerRefV1
    external_context_index_allowed: bool | None = None
    applicability_subject_key: str | None = None


@dataclass(frozen=True, slots=True)
class ApplicabilityDecision:
    """Append-only governed applicability decision used by conditional rules."""

    applicability_decision_id: UUID
    predicate_id: str
    subject_key: str
    outcome: str
    contract_rule_id: str
    contract_rule_version: str
    contract_rule_hash: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class EdgeDerivationRequest:
    """Complete immutable inputs to expected-edge expansion."""

    tenant_id: UUID
    deal_id: UUID
    run_id: UUID
    pipeline_contract_version: str
    pipeline_contract_hash: str
    producers: tuple[ImmutableProducerFact, ...]
    applicability_decisions: tuple[ApplicabilityDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpectedConsumerEdge:
    """One deterministic expected consumer edge."""

    expected_edge_id: UUID
    tenant_id: UUID
    deal_id: UUID
    run_id: UUID
    pipeline_contract_version: str
    pipeline_contract_hash: str
    route_ordinal: int
    producer_condition: str
    producer: ProducerRefV1
    consumer_step_name: str
    consumer_subrole: str
    usage_kind: str
    applicability_decision_id: UUID | None
    contract_rule_id: str
    contract_rule_version: str
    contract_rule_hash: str
    edge_tuple_sha256: str


@dataclass(frozen=True, slots=True)
class ConsumerReceiptEvidence:
    """Minimal independently reloaded receipt evidence used by equality."""

    expected_edge_id: UUID
    edge_tuple_sha256: str
    consumer_step_name: str
    consumer_subrole: str
    usage_evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ThreeWayEqualityProof:
    """Safe aggregate proof that derivation, rows, and receipts are identical."""

    edge_count: int
    receipt_count: int
    proof_sha256: str


def _normalize_json_value(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", str(key))
            if normalized_key in normalized:
                raise ValueError("mapping keys collide after NFC normalization")
            normalized[normalized_key] = _normalize_json_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    return value


def _canonical_hash(value: object) -> str:
    raw = json.dumps(
        _normalize_json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _freeze_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in sorted(value.items())}
        )
    return value


def _fail(
    message: str,
    *,
    code: str = CONSUMER_RECEIPT_INCOMPLETE,
) -> NoReturn:
    raise EdgeDerivationError(code, message)


def _edge_rule_from_row(row: list[object]) -> EdgeRuleSpec:
    if (
        len(row) != 8
        or not isinstance(row[0], int)
        or isinstance(row[0], bool)
        or not all(isinstance(value, str) for value in row[1:])
    ):
        raise RuntimeError("TASK7_EDGE_RULE_REGISTRY_INVALID")
    return EdgeRuleSpec(
        rule_ordinal=row[0],
        rule_id=cast(str, row[1]),
        rule_version=cast(str, row[2]),
        usage_kind=cast(str, row[3]),
        predicate_kind=cast(str, row[4]),
        predicate_id=cast(str, row[5]),
        predicate_version=cast(str, row[6]),
        rule_sha256=cast(str, row[7]),
    )


def _load_edge_rules() -> Mapping[str, EdgeRuleSpec]:
    try:
        raw = zlib.decompress(base64.b85decode(_EDGE_RULE_AUTHORITY_B85.encode("ascii")))
        rows = cast(list[list[object]], json.loads(raw))
        specs = tuple(_edge_rule_from_row(row) for row in rows)
    except (TypeError, ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise RuntimeError("TASK7_EDGE_RULE_REGISTRY_UNAVAILABLE") from exc
    if (
        len(specs) != 48
        or tuple(row.rule_ordinal for row in specs) != tuple(range(48))
        or len({row.rule_id for row in specs}) != 48
        or any(_HASH_RE.fullmatch(row.rule_sha256) is None for row in specs)
    ):
        raise RuntimeError("TASK7_EDGE_RULE_REGISTRY_INVALID")
    registry = MappingProxyType({row.rule_id: row for row in specs})
    for route in USAGE_ROUTE_REGISTRY:
        rule = registry.get(route.rule_id)
        if rule is None or route.usage_kind != rule.usage_kind:
            raise RuntimeError("TASK7_EDGE_USAGE_ROUTE_REGISTRY_MISMATCH")
    return registry


EDGE_RULE_REGISTRY: Final = _load_edge_rules()


def _selector_instance_from_rows(
    *,
    row: Mapping[str, object],
    instance: Mapping[str, object],
) -> ProducerSelectorSpec:
    try:
        rule_ordinal = row["rule_ordinal"]
        rule_id = row["rule_id"]
        rule_version = row["version"]
        base_selector_id = row["base_selector_id"]
        selector_ordinal = instance["ordinal"]
        parameter_tuple_raw = instance["parameter_tuple"]
        selector_ast_raw = instance["selector_ast"]
        selector_sha256 = instance["expected_sha256"]
    except KeyError as exc:
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID") from exc
    if (
        type(rule_ordinal) is not int
        or type(selector_ordinal) is not int
        or not all(
            isinstance(value, str)
            for value in (
                rule_id,
                rule_version,
                base_selector_id,
                selector_sha256,
            )
        )
        or not isinstance(parameter_tuple_raw, list)
        or not isinstance(selector_ast_raw, list)
        or len(selector_ast_raw) != 3
        or selector_ast_raw[0] != "concrete-producer-selector-v2"
        or not isinstance(selector_ast_raw[1], list)
        or not isinstance(selector_ast_raw[2], list)
    ):
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
    body = selector_ast_raw[1]
    condition = selector_ast_raw[2]
    if (
        len(body) != 9
        or body[0] != "producer-selector-spec-v2"
        or body[1] != base_selector_id
        or body[2] != rule_version
        or len(condition) != 2
        or condition[0] != "producer_condition"
        or not isinstance(condition[1], str)
        or not all(isinstance(item, list) for item in (body[3], body[4], body[5]))
        or not all(isinstance(item, list) for item in (body[6], body[7], body[8]))
        or len(body[6]) not in {4, 5}
        or body[6][0] not in {"INPUT_BINDING", "OUTPUT_OBJECT"}
        or not all(isinstance(item, str) for item in body[4])
        or not all(isinstance(item, str) for item in (*body[6], *body[8]))
        or _HASH_RE.fullmatch(cast(str, selector_sha256)) is None
        or _canonical_hash(["producer-selector-v2", selector_ast_raw]) != selector_sha256
    ):
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
    parameters: list[tuple[str, object]] = []
    for parameter in parameter_tuple_raw:
        if (
            not isinstance(parameter, list)
            or len(parameter) != 2
            or not isinstance(parameter[0], str)
        ):
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
        parameters.append((parameter[0], _freeze_json(parameter[1])))
    joins: list[tuple[str, str]] = []
    for join in body[5]:
        if (
            not isinstance(join, list)
            or len(join) != 2
            or not all(isinstance(item, str) for item in join)
        ):
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
        joins.append((join[0], join[1]))
    frozen_ast = _freeze_json(selector_ast_raw)
    frozen_predicate = _freeze_json(body[7])
    if not isinstance(frozen_ast, tuple) or not isinstance(frozen_predicate, tuple):
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
    return ProducerSelectorSpec(
        rule_ordinal=rule_ordinal,
        rule_id=cast(str, rule_id),
        rule_version=cast(str, rule_version),
        selector_ordinal=selector_ordinal,
        parameter_tuple=tuple(parameters),
        selector_ast=frozen_ast,
        selector_sha256=cast(str, selector_sha256),
        base_selector_id=cast(str, base_selector_id),
        relations=tuple(cast(list[str], body[4])),
        joins=tuple(joins),
        projection=tuple(cast(list[str], body[6])),
        predicate_ast=frozen_predicate,
        order_by=tuple(cast(list[str], body[8])),
        producer_condition=condition[1],
    )


def _load_producer_selector_registry() -> tuple[ProducerSelectorSpec, ...]:
    if (
        _SELECTOR_CONTRACT_VERSION != _FULL_STRICT_V21_VERSION
        or _SELECTOR_CONTRACT_HASH != FULL_STRICT_V21_CONTRACT_HASH
        or PRODUCER_SELECTOR_ROW_COUNT != 48
        or _GENERATED_SELECTOR_REGISTRY_SHA256
        != "3b23e5bfdee6fe78762412eec2e37430559f00833a698b17c14809876ca2d4f4"
    ):
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_UNAVAILABLE")
    try:
        canonical = zlib.decompress(PRODUCER_SELECTOR_CANONICAL_JSON_ZLIB)
        if hashlib.sha256(canonical).hexdigest() != PRODUCER_SELECTOR_CANONICAL_JSON_SHA256:
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_HASH_MISMATCH")
        rows = json.loads(canonical)
    except (TypeError, ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_UNAVAILABLE") from exc
    if not isinstance(rows, list) or len(rows) != PRODUCER_SELECTOR_ROW_COUNT:
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")

    specs: list[ProducerSelectorSpec] = []
    row_projection: list[list[object]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
        instances_raw = row.get("instances")
        if not isinstance(instances_raw, list) or not instances_raw:
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
        instances: list[ProducerSelectorSpec] = []
        for instance in instances_raw:
            if not isinstance(instance, dict):
                raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
            instances.append(_selector_instance_from_rows(row=row, instance=instance))
        if tuple(spec.selector_ordinal for spec in instances) != tuple(range(len(instances))):
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_INVALID")
        expected_row_hash = row.get("expected_sha256")
        derived_row_hash = _canonical_hash(
            [
                "producer-selector-rule-v2",
                row.get("rule_id"),
                [
                    [
                        spec.selector_ordinal,
                        [
                            [key, _normalize_json_value(value)]
                            for key, value in spec.parameter_tuple
                        ],
                        spec.selector_sha256,
                    ]
                    for spec in instances
                ],
            ]
        )
        if expected_row_hash != derived_row_hash:
            raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_HASH_MISMATCH")
        specs.extend(instances)
        row_projection.append(
            [
                row.get("rule_id"),
                row.get("version"),
                expected_row_hash,
            ]
        )
    if (
        len(specs) != 235
        or tuple(row.rule_ordinal for row in specs)
        != tuple(
            ordinal
            for ordinal, source in enumerate(rows)
            for _ in cast(list[object], source["instances"])
        )
        or _canonical_hash(["task7-producer-selector-registry-v1", row_projection])
        != _GENERATED_SELECTOR_REGISTRY_SHA256
    ):
        raise RuntimeError("TASK7_PRODUCER_SELECTOR_REGISTRY_HASH_MISMATCH")
    return tuple(specs)


PRODUCER_SELECTOR_REGISTRY_SHA256: Final = _GENERATED_SELECTOR_REGISTRY_SHA256
PRODUCER_SELECTOR_REGISTRY: Final = _load_producer_selector_registry()


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(f"{label} is not a lowercase SHA-256")
    return value


def _validate_producer(producer: ProducerRefV1) -> None:
    if producer.producer_type not in {"INPUT_BINDING", "OUTPUT_OBJECT"}:
        _fail("producer type is outside the reviewed contract")
    if not isinstance(producer.producer_id, UUID):
        _fail("producer identifier is not a UUID")
    _validate_hash(producer.producer_identity_sha256, "producer identity hash")
    _validate_hash(producer.producer_value_sha256, "producer value hash")


def _predicate_contains(
    value: object,
    expected: tuple[object, ...],
) -> bool:
    if value == expected:
        return True
    if isinstance(value, tuple):
        return any(_predicate_contains(item, expected) for item in value)
    return False


def _selector_subject_key(spec: ProducerSelectorSpec) -> str | None:
    for key, value in spec.parameter_tuple:
        if key == "subject_key" and isinstance(value, str) and value:
            return value
    return None


def derive_rule_bound_producers(
    request: ProducerSelectionRequest,
    *,
    row_loader: ProducerSelectorRowLoader,
) -> tuple[ImmutableProducerFact, ...]:
    """Execute every generated selector and admit only its typed projection."""

    if (
        request.pipeline_contract_version != _FULL_STRICT_V21_VERSION
        or request.pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
    ):
        _fail(
            "producer selection is outside the reviewed contract",
            code=PIPELINE_CONTRACT_HASH_MISMATCH,
        )
    if not all(
        isinstance(value, UUID) for value in (request.tenant_id, request.deal_id, request.run_id)
    ):
        _fail("producer selection scope contains a non-UUID identifier")

    facts: list[ImmutableProducerFact] = []
    for spec in PRODUCER_SELECTOR_REGISTRY:
        rows = row_loader(
            spec,
            tenant_id=request.tenant_id,
            deal_id=request.deal_id,
            run_id=request.run_id,
            pipeline_contract_version=request.pipeline_contract_version,
            pipeline_contract_hash=request.pipeline_contract_hash,
        )
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            _fail("producer selector returned an untyped row collection")
        for row in rows:
            if not isinstance(row, Mapping):
                _fail("producer selector returned a non-mapping row")
            producer_id = row.get(spec.producer_id_column)
            identity_hash = row.get(spec.producer_identity_hash_column)
            value_hash = row.get(spec.producer_value_hash_column)
            if (
                not isinstance(producer_id, UUID)
                or not isinstance(identity_hash, str)
                or not isinstance(value_hash, str)
                or _HASH_RE.fullmatch(identity_hash) is None
                or _HASH_RE.fullmatch(value_hash) is None
            ):
                _fail("producer selector returned a malformed projection")
            external_context_index_allowed: bool | None = None
            if _predicate_contains(
                spec.predicate_ast,
                (
                    "EQ",
                    "enrichment_provider_outcomes.external_context_index_allowed",
                    True,
                ),
            ):
                external_context_index_allowed = True
            producer = ProducerRefV1(
                producer_type=spec.producer_type,
                producer_id=producer_id,
                producer_identity_sha256=identity_hash,
                producer_value_sha256=value_hash,
            )
            _validate_producer(producer)
            facts.append(
                ImmutableProducerFact(
                    rule_id=spec.rule_id,
                    producer_condition=spec.producer_condition,
                    producer=producer,
                    external_context_index_allowed=(external_context_index_allowed),
                    applicability_subject_key=_selector_subject_key(spec),
                )
            )

    ordered = tuple(sorted(facts, key=_producer_sort_key))
    if len(set(ordered)) != len(ordered):
        _fail("producer selector returned a duplicate immutable fact")
    return ordered


def _producer_sort_key(row: ImmutableProducerFact) -> tuple[object, ...]:
    return (
        EDGE_RULE_REGISTRY[row.rule_id].rule_ordinal,
        row.producer.producer_type,
        row.producer.producer_id.bytes,
        row.producer_condition,
        row.producer.producer_identity_sha256,
        row.producer.producer_value_sha256,
    )


def _validate_decision(decision: ApplicabilityDecision) -> None:
    if (
        not isinstance(decision.applicability_decision_id, UUID)
        or decision.outcome not in {"APPLICABLE", "NOT_APPLICABLE"}
        or not decision.subject_key
    ):
        _fail("applicability decision is malformed")
    _validate_hash(decision.contract_rule_hash, "decision rule hash")
    _validate_hash(decision.evidence_sha256, "decision evidence hash")


def _decision_for(
    *,
    request: EdgeDerivationRequest,
    producer: ImmutableProducerFact,
    rule: EdgeRuleSpec,
) -> ApplicabilityDecision:
    if not producer.applicability_subject_key:
        _fail("conditional producer lacks an applicability subject")
    matches = tuple(
        decision
        for decision in request.applicability_decisions
        if decision.predicate_id == rule.predicate_id
        and decision.subject_key == producer.applicability_subject_key
        and decision.contract_rule_id == rule.rule_id
    )
    if len(matches) != 1:
        _fail("conditional producer lacks exactly one matching decision")
    decision = matches[0]
    _validate_decision(decision)
    if (
        decision.contract_rule_version != rule.rule_version
        or decision.contract_rule_hash != rule.rule_sha256
    ):
        _fail("applicability decision is not bound to the expected-edge rule")
    return decision


def _edge_for(
    *,
    request: EdgeDerivationRequest,
    producer: ImmutableProducerFact,
    route_ordinal: int,
    decision_id: UUID | None,
    rule: EdgeRuleSpec,
) -> ExpectedConsumerEdge:
    route = USAGE_ROUTE_REGISTRY[route_ordinal]
    edge_tuple_sha256 = _canonical_hash(
        [
            "expected-edge-tuple-digest-v1",
            str(request.tenant_id),
            str(request.deal_id),
            str(request.run_id),
            request.pipeline_contract_version,
            request.pipeline_contract_hash,
            producer.producer.producer_type,
            str(producer.producer.producer_id),
            producer.producer.producer_identity_sha256,
            producer.producer.producer_value_sha256,
            route.consumer_step_name,
            route.consumer_subrole,
            route.usage_kind,
            str(decision_id) if decision_id is not None else None,
            rule.rule_id,
            rule.rule_version,
            rule.rule_sha256,
        ]
    )
    try:
        expected_edge_id = derive_task7_uuid(
            id_kind="expected_edge_id",
            values={
                "tenant_id": str(request.tenant_id),
                "deal_id": str(request.deal_id),
                "run_id": str(request.run_id),
                "pipeline_contract_version": request.pipeline_contract_version,
                "pipeline_contract_hash": request.pipeline_contract_hash,
                "producer_type": producer.producer.producer_type,
                "producer_id": str(producer.producer.producer_id),
                "consumer_step": route.consumer_step_name,
                "consumer_subrole": route.consumer_subrole,
                "usage_kind": route.usage_kind,
                "decision_id_or_null": (str(decision_id) if decision_id is not None else None),
                "rule_id": rule.rule_id,
                "rule_version": rule.rule_version,
                "rule_hash": rule.rule_sha256,
            },
        ).value
    except Task7UuidError as exc:
        raise EdgeDerivationError(
            CONSUMER_RECEIPT_INCOMPLETE,
            "expected-edge UUID derivation failed",
        ) from exc
    return ExpectedConsumerEdge(
        expected_edge_id=expected_edge_id,
        tenant_id=request.tenant_id,
        deal_id=request.deal_id,
        run_id=request.run_id,
        pipeline_contract_version=request.pipeline_contract_version,
        pipeline_contract_hash=request.pipeline_contract_hash,
        route_ordinal=route.route_ordinal,
        producer_condition=producer.producer_condition,
        producer=producer.producer,
        consumer_step_name=route.consumer_step_name,
        consumer_subrole=route.consumer_subrole,
        usage_kind=route.usage_kind,
        applicability_decision_id=decision_id,
        contract_rule_id=rule.rule_id,
        contract_rule_version=rule.rule_version,
        contract_rule_hash=rule.rule_sha256,
        edge_tuple_sha256=edge_tuple_sha256,
    )


def derive_expected_edges(
    request: EdgeDerivationRequest,
) -> tuple[ExpectedConsumerEdge, ...]:
    """Expand immutable producers over only their literal reviewed routes."""

    if (
        request.pipeline_contract_version != _FULL_STRICT_V21_VERSION
        or request.pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
    ):
        _fail(
            "edge derivation is not bound to the reviewed full-strict-v21 contract",
            code=PIPELINE_CONTRACT_HASH_MISMATCH,
        )
    if not all(
        isinstance(value, UUID) for value in (request.tenant_id, request.deal_id, request.run_id)
    ):
        _fail("edge derivation scope contains a non-UUID identifier")
    for decision in request.applicability_decisions:
        _validate_decision(decision)

    producers = tuple(request.producers)
    for producer in producers:
        if producer.rule_id not in EDGE_RULE_REGISTRY:
            _fail("producer names an unavailable expected-edge rule")
        if not producer.producer_condition:
            _fail("producer condition is absent")
        _validate_producer(producer.producer)
    if len(set(producers)) != len(producers):
        _fail("edge derivation contains a duplicate producer fact")

    edges: list[ExpectedConsumerEdge] = []
    for producer in sorted(producers, key=_producer_sort_key):
        rule = EDGE_RULE_REGISTRY[producer.rule_id]
        route_ordinals = tuple(
            route.route_ordinal
            for route in USAGE_ROUTE_REGISTRY
            if route.rule_id == rule.rule_id
            and route.producer_condition == producer.producer_condition
        )
        if not route_ordinals:
            _fail("producer condition has no literal reviewed consumer route")

        decision_id: UUID | None = None
        if rule.predicate_kind == "APPEND_ONLY_APPLICABILITY_DECISION":
            decision = _decision_for(request=request, producer=producer, rule=rule)
            if decision.outcome == "NOT_APPLICABLE":
                continue
            decision_id = decision.applicability_decision_id
        elif rule.predicate_kind == "IMMUTABLE_PROVIDER_OUTCOME_FIELD":
            if not isinstance(producer.external_context_index_allowed, bool):
                _fail("E048 producer lacks its immutable rights-derived index flag")
            if not producer.external_context_index_allowed:
                continue
        elif rule.predicate_kind != "UNCONDITIONAL":
            _fail("expected-edge predicate kind is unavailable")

        edges.extend(
            _edge_for(
                request=request,
                producer=producer,
                route_ordinal=route_ordinal,
                decision_id=decision_id,
                rule=rule,
            )
            for route_ordinal in route_ordinals
        )

    ordered = tuple(
        sorted(
            edges,
            key=lambda edge: (
                EDGE_RULE_REGISTRY[edge.contract_rule_id].rule_ordinal,
                edge.producer.producer_type,
                edge.producer.producer_id.bytes,
                edge.route_ordinal,
                edge.expected_edge_id.bytes,
            ),
        )
    )
    if len({edge.expected_edge_id for edge in ordered}) != len(ordered) or len(
        {edge.edge_tuple_sha256 for edge in ordered}
    ) != len(ordered):
        _fail("edge expansion produced a duplicate deterministic identity")
    return ordered


def _edge_map(
    rows: Sequence[ExpectedConsumerEdge],
    *,
    label: str,
) -> dict[str, ExpectedConsumerEdge]:
    result: dict[str, ExpectedConsumerEdge] = {}
    for row in rows:
        if (
            row.pipeline_contract_version != _FULL_STRICT_V21_VERSION
            or row.pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
            or not all(
                isinstance(value, UUID) for value in (row.tenant_id, row.deal_id, row.run_id)
            )
            or row.route_ordinal < 0
            or row.route_ordinal >= len(USAGE_ROUTE_REGISTRY)
        ):
            _fail(f"{label} edge is outside the reviewed contract")
        rule = EDGE_RULE_REGISTRY.get(row.contract_rule_id)
        route = USAGE_ROUTE_REGISTRY[row.route_ordinal]
        if (
            rule is None
            or row.contract_rule_version != rule.rule_version
            or row.contract_rule_hash != rule.rule_sha256
            or row.producer_condition != route.producer_condition
            or row.contract_rule_id != route.rule_id
            or row.consumer_step_name != route.consumer_step_name
            or row.consumer_subrole != route.consumer_subrole
            or row.usage_kind != route.usage_kind
        ):
            _fail(f"{label} edge does not match its literal rule and route")
        _validate_producer(row.producer)
        rederived = _edge_for(
            request=EdgeDerivationRequest(
                tenant_id=row.tenant_id,
                deal_id=row.deal_id,
                run_id=row.run_id,
                pipeline_contract_version=row.pipeline_contract_version,
                pipeline_contract_hash=row.pipeline_contract_hash,
                producers=(),
            ),
            producer=ImmutableProducerFact(
                rule_id=row.contract_rule_id,
                producer_condition=row.producer_condition,
                producer=row.producer,
            ),
            route_ordinal=row.route_ordinal,
            decision_id=row.applicability_decision_id,
            rule=rule,
        )
        if rederived != row:
            _fail(f"{label} edge fails independent hash or UUID rederivation")
        _validate_hash(row.edge_tuple_sha256, f"{label} edge tuple hash")
        if row.edge_tuple_sha256 in result:
            _fail(f"{label} contains a duplicate edge tuple")
        result[row.edge_tuple_sha256] = row
    return result


def assert_three_way_equality(
    *,
    derived: Sequence[ExpectedConsumerEdge],
    persisted: Sequence[ExpectedConsumerEdge],
    receipts: Sequence[ConsumerReceiptEvidence],
    expected_usage_evidence: Mapping[UUID, str],
) -> ThreeWayEqualityProof:
    """Require exact derived = persisted = receipt equality, including usage hashes."""

    derived_map = _edge_map(derived, label="derived")
    persisted_map = _edge_map(persisted, label="persisted")
    if derived_map != persisted_map:
        _fail("derived and persisted expected-edge maps differ")

    derived_by_id = {edge.expected_edge_id: edge for edge in derived_map.values()}
    if len(derived_by_id) != len(derived_map):
        _fail("derived edges contain a duplicate expected-edge identifier")
    if set(expected_usage_evidence) != set(derived_by_id):
        _fail("usage evidence keys differ from expected-edge keys")
    for usage_hash in expected_usage_evidence.values():
        _validate_hash(usage_hash, "expected usage evidence hash")

    receipt_map: dict[UUID, ConsumerReceiptEvidence] = {}
    for receipt in receipts:
        if receipt.expected_edge_id in receipt_map:
            _fail("receipts contain a duplicate expected-edge identifier")
        _validate_hash(receipt.edge_tuple_sha256, "receipt edge tuple hash")
        _validate_hash(receipt.usage_evidence_sha256, "receipt usage evidence hash")
        receipt_map[receipt.expected_edge_id] = receipt
    if set(receipt_map) != set(derived_by_id):
        _fail("receipt keys differ from expected-edge keys")

    proof_rows: list[list[str]] = []
    for edge_id in sorted(derived_by_id, key=lambda value: value.bytes):
        edge = derived_by_id[edge_id]
        receipt = receipt_map[edge_id]
        usage_hash = expected_usage_evidence[edge_id]
        if (
            receipt.edge_tuple_sha256 != edge.edge_tuple_sha256
            or receipt.consumer_step_name != edge.consumer_step_name
            or receipt.consumer_subrole != edge.consumer_subrole
            or receipt.usage_evidence_sha256 != usage_hash
        ):
            _fail("receipt evidence differs from its exact expected edge")
        proof_rows.append([str(edge_id), edge.edge_tuple_sha256, usage_hash])

    return ThreeWayEqualityProof(
        edge_count=len(derived_by_id),
        receipt_count=len(receipt_map),
        proof_sha256=_canonical_hash(["task7-three-way-equality-proof-v1", proof_rows]),
    )
