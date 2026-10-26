"""Checked-in, question-aware Commercial-DD document-category mapping."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Never

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from idis.models.document_classification import CddDocumentCategory

METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE = "METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE"
METHODOLOGY_CDD_CATEGORY_MAP_INVALID = "METHODOLOGY_CDD_CATEGORY_MAP_INVALID"
METHODOLOGY_CDD_CATEGORY_MAP_DRIFT = "METHODOLOGY_CDD_CATEGORY_MAP_DRIFT"

CDD_DOCUMENT_CLASSIFIER_VERSION = "cdd-document-classifier-v2"
CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES = tuple(
    sorted(
        category
        for category in CddDocumentCategory
        if category not in {CddDocumentCategory.UNKNOWN, CddDocumentCategory.UNSUPPORTED}
    )
)
CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES_SHA256 = (
    "2cfd5b237c4d078e59ac11d0f3d8320f44cb22f5f1cfc90e6c9f8da3b7aede8b"
)
CDD_QUESTION_CATEGORY_MAP_CANONICAL_SHA256 = (
    "bc5753df9c8ab7a6d6a56cd02185061b1a4eb36c111387349c0a4370d30d097e"
)

_SCHEMA_VERSION = "cdd-question-category-map-v1"
_MAPPING_VERSION = "1.0.0"
_METHODOLOGY_ID = "commercial_dd"
_METHODOLOGY_VERSION_ID = "commercial_dd:v1"
_METHODOLOGY_SOURCE_SHA256 = "57cfec98436514cae717e359fa872504a7b9e1115c7bc12a4561e0989c4281fc"
_IMPORTED_REGISTRY_SHA256 = "13f38ef7afa9e96828ff723d32b650a16a9d4817f4dd6b5fab1bf0aafcdde2db"
_RESOURCE_PACKAGE = "idis.methodology.mappings"
_MAP_RESOURCE_NAME = "commercial_dd_v1_question_categories.json"
_SCHEMA_RESOURCE_NAME = "commercial_dd_v1_question_categories.schema.json"

_EXPECTED_QUESTION_ROWS: tuple[tuple[str, str, tuple[CddDocumentCategory, ...]], ...] = (
    (
        "mq_commercial_dd_market_size_0001",
        "18474c481d0838b1c34d29b1a4aaf5605cb3ddd9c4dd6f3501e5bbb00c95a959",
        (CddDocumentCategory.MARKET_RESEARCH,),
    ),
    (
        "mq_commercial_dd_market_growth_0001",
        "de9e1b3eaf54b393532edf363f3c64fb5823d2ecaadec9e772a8a18f120cba32",
        (
            CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            CddDocumentCategory.MARKET_RESEARCH,
        ),
    ),
    (
        "mq_commercial_dd_market_structure_0001",
        "67b27481016265bf8d7d54a36eab0cfdab998d76023b45ad45641871324db042",
        (CddDocumentCategory.MARKET_RESEARCH,),
    ),
    (
        "mq_commercial_dd_customer_segments_0001",
        "1a7f3226ec294946d7ca53cc3761447e522e644467ca28761e0391eb6bc57d43",
        (CddDocumentCategory.CUSTOMER_EVIDENCE, CddDocumentCategory.SALES_PIPELINE),
    ),
    (
        "mq_commercial_dd_customer_concentration_0001",
        "c9954d46179cc99869afd10aac77017ab42e64738ee38531acdcf23c3683b8dd",
        (
            CddDocumentCategory.COMMERCIAL_CONTRACTS,
            CddDocumentCategory.CUSTOMER_EVIDENCE,
        ),
    ),
    (
        "mq_commercial_dd_customer_quality_0001",
        "bb9c37eab2366d18432bb49f299ba8f6eb92fb04c3e23122f23b21589d6707c5",
        (CddDocumentCategory.CUSTOMER_EVIDENCE,),
    ),
    (
        "mq_commercial_dd_churn_retention_0001",
        "9b9789c0797a69333ed1d7f996ecd2141033e53c6f054875004c8eff123d8d30",
        (CddDocumentCategory.CUSTOMER_EVIDENCE, CddDocumentCategory.SALES_PIPELINE),
    ),
    (
        "mq_commercial_dd_sales_pipeline_0001",
        "5f42c38ecd89a1c68817697a3c3282bb536fe42f89161a4c523e22db4f895692",
        (CddDocumentCategory.SALES_PIPELINE,),
    ),
    (
        "mq_commercial_dd_pricing_0001",
        "d7381dacc2a10f398f3b51a356f81fb974a353c8aaf5d1f932aa8b0dfb04d879",
        (
            CddDocumentCategory.COMMERCIAL_CONTRACTS,
            CddDocumentCategory.PRICING_UNIT_ECONOMICS,
            CddDocumentCategory.SALES_PIPELINE,
        ),
    ),
    (
        "mq_commercial_dd_unit_economics_0001",
        "b80f2c01100bee0fbb8e7a3da7944d645b8e8f2f4450f1d1c765730b1b38155d",
        (
            CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            CddDocumentCategory.PRICING_UNIT_ECONOMICS,
            CddDocumentCategory.SALES_PIPELINE,
        ),
    ),
    (
        "mq_commercial_dd_gtm_model_0001",
        "0b76f17579db7b0343234bab5e08c76eae7d6fc3aa77c4c1fb8f2c9a031dfb40",
        (CddDocumentCategory.GTM_DISTRIBUTION, CddDocumentCategory.SALES_PIPELINE),
    ),
    (
        "mq_commercial_dd_distribution_0001",
        "84ab5f5d1df51072651e1effe93816b0c8df0b9e0c02590779881addb83147a0",
        (CddDocumentCategory.GTM_DISTRIBUTION, CddDocumentCategory.SALES_PIPELINE),
    ),
    (
        "mq_commercial_dd_competition_0001",
        "7d7e4c1d35c6ab44665328f4795f720f487ae28f2b71c01ef4b55dfa15af79b1",
        (
            CddDocumentCategory.COMPETITIVE_LANDSCAPE,
            CddDocumentCategory.MARKET_RESEARCH,
        ),
    ),
    (
        "mq_commercial_dd_pmf_0001",
        "3f8ea9336a3089dea137b29463d473a8fc1533892afcf375916bd38546b75899",
        (CddDocumentCategory.CUSTOMER_EVIDENCE, CddDocumentCategory.PRODUCT_TECHNOLOGY),
    ),
    (
        "mq_commercial_dd_technology_diff_0001",
        "1d2427268a998484b7aaf628b8fa362e211530f499f2f57c3349f96b0fb692e3",
        (CddDocumentCategory.PRODUCT_TECHNOLOGY,),
    ),
    (
        "mq_commercial_dd_contracts_0001",
        "1b28bf4b1f1c02ea4f7eb80e7352d94cb7970d357b3fc2284d3a5680a2cdf8e7",
        (CddDocumentCategory.COMMERCIAL_CONTRACTS,),
    ),
    (
        "mq_commercial_dd_revenue_drivers_0001",
        "c735b3e004982272cc2cefc4fc63850a4258c2699044f91ae9c064fdb048af88",
        (
            CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            CddDocumentCategory.SALES_PIPELINE,
        ),
    ),
    (
        "mq_commercial_dd_expansion_0001",
        "76e68bf3b2ff762f248e286ce70b47b160ba879a87522cfacc2ef44eb3310dc5",
        (CddDocumentCategory.CUSTOMER_EVIDENCE, CddDocumentCategory.SALES_PIPELINE),
    ),
    (
        "mq_commercial_dd_management_0001",
        "a9e19f3ed7c0205d3de431a8e7b470162b428dd975fd346064c135d49457e1ce",
        (CddDocumentCategory.MANAGEMENT_TEAM,),
    ),
    (
        "mq_commercial_dd_risks_0001",
        "aa0fe543ce2ade65fe5752b8e1098b785ee1cc52cbc4ac9d795741d9dfd679da",
        (CddDocumentCategory.COMMERCIAL_RISKS,),
    ),
    (
        "mq_commercial_dd_growth_assumptions_0001",
        "fa69888afc7a327593623cd15567e42657744de15f5745c2c5bc7bce960d54e4",
        (CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,),
    ),
)


class CommercialDDCategoryMapError(RuntimeError):
    """Secret-safe mapping admission failure with a stable code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise CommercialDDCategoryMapError(code)


@dataclass(frozen=True, slots=True)
class CommercialDDQuestionCategoryEntry:
    """One exact imported CDD question and its governed category set."""

    methodology_question_id: str
    methodology_question_sha256: str
    categories: tuple[CddDocumentCategory, ...]


@dataclass(frozen=True, slots=True)
class CommercialDDQuestionCategoryMap:
    """Immutable typed representation of the governed CDD mapping artifact."""

    schema_version: str
    mapping_version: str
    methodology_id: str
    methodology_version_id: str
    methodology_source_sha256: str
    imported_registry_sha256: str
    classifier_version: str
    classifier_reachable_categories: tuple[CddDocumentCategory, ...]
    classifier_reachable_categories_sha256: str
    questions: tuple[CommercialDDQuestionCategoryEntry, ...]
    canonical_payload_sha256: str


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_and_validate_payload() -> dict[str, Any]:
    try:
        resource_root = files(_RESOURCE_PACKAGE)
        raw = resource_root.joinpath(_MAP_RESOURCE_NAME).read_bytes()
    except (ModuleNotFoundError, OSError):
        _fail(METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE)
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        schema = json.loads(
            resource_root.joinpath(_SCHEMA_RESOURCE_NAME).read_bytes().decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(payload, dict) or not isinstance(schema, dict):
            raise ValueError
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        SchemaError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        _fail(METHODOLOGY_CDD_CATEGORY_MAP_INVALID)
    return payload


def _to_typed_map(
    payload: dict[str, Any],
    *,
    canonical_payload_sha256: str,
) -> CommercialDDQuestionCategoryMap:
    try:
        return CommercialDDQuestionCategoryMap(
            schema_version=str(payload["schema_version"]),
            mapping_version=str(payload["mapping_version"]),
            methodology_id=str(payload["methodology_id"]),
            methodology_version_id=str(payload["methodology_version_id"]),
            methodology_source_sha256=str(payload["methodology_source_sha256"]),
            imported_registry_sha256=str(payload["imported_registry_sha256"]),
            classifier_version=str(payload["classifier_version"]),
            classifier_reachable_categories=tuple(
                CddDocumentCategory(value) for value in payload["classifier_reachable_categories"]
            ),
            classifier_reachable_categories_sha256=str(
                payload["classifier_reachable_categories_sha256"]
            ),
            questions=tuple(
                CommercialDDQuestionCategoryEntry(
                    methodology_question_id=str(item["methodology_question_id"]),
                    methodology_question_sha256=str(item["methodology_question_sha256"]),
                    categories=tuple(CddDocumentCategory(value) for value in item["categories"]),
                )
                for item in payload["questions"]
            ),
            canonical_payload_sha256=canonical_payload_sha256,
        )
    except (KeyError, TypeError, ValueError):
        _fail(METHODOLOGY_CDD_CATEGORY_MAP_INVALID)


def _validate_governed_identity(governed: CommercialDDQuestionCategoryMap) -> None:
    actual_rows = tuple(
        (
            entry.methodology_question_id,
            entry.methodology_question_sha256,
            entry.categories,
        )
        for entry in governed.questions
    )
    reachable_values = tuple(
        category.value for category in governed.classifier_reachable_categories
    )
    reachable_sha256 = _canonical_sha256(reachable_values)
    if (
        governed.schema_version != _SCHEMA_VERSION
        or governed.mapping_version != _MAPPING_VERSION
        or governed.methodology_id != _METHODOLOGY_ID
        or governed.methodology_version_id != _METHODOLOGY_VERSION_ID
        or governed.methodology_source_sha256 != _METHODOLOGY_SOURCE_SHA256
        or governed.imported_registry_sha256 != _IMPORTED_REGISTRY_SHA256
        or governed.classifier_version != CDD_DOCUMENT_CLASSIFIER_VERSION
        or governed.classifier_reachable_categories != CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES
        or reachable_sha256 != CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES_SHA256
        or governed.classifier_reachable_categories_sha256 != reachable_sha256
        or actual_rows != _EXPECTED_QUESTION_ROWS
        or governed.canonical_payload_sha256 != CDD_QUESTION_CATEGORY_MAP_CANONICAL_SHA256
    ):
        _fail(METHODOLOGY_CDD_CATEGORY_MAP_DRIFT)


def load_commercial_dd_question_category_map() -> CommercialDDQuestionCategoryMap:
    """Load and authenticate the one fixed package mapping; no fallback is permitted."""
    payload = _read_and_validate_payload()
    governed = _to_typed_map(payload, canonical_payload_sha256=_canonical_sha256(payload))
    _validate_governed_identity(governed)
    return governed


def validate_commercial_dd_question_category_map(
    governed: CommercialDDQuestionCategoryMap,
) -> CommercialDDQuestionCategoryMap:
    """Revalidate an injected typed map against every pinned governed invariant."""
    if not isinstance(governed, CommercialDDQuestionCategoryMap):
        _fail(METHODOLOGY_CDD_CATEGORY_MAP_INVALID)
    _validate_governed_identity(governed)
    return governed


__all__ = [
    "CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES",
    "CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES_SHA256",
    "CDD_DOCUMENT_CLASSIFIER_VERSION",
    "CDD_QUESTION_CATEGORY_MAP_CANONICAL_SHA256",
    "METHODOLOGY_CDD_CATEGORY_MAP_DRIFT",
    "METHODOLOGY_CDD_CATEGORY_MAP_INVALID",
    "METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE",
    "CommercialDDCategoryMapError",
    "CommercialDDQuestionCategoryEntry",
    "CommercialDDQuestionCategoryMap",
    "load_commercial_dd_question_category_map",
    "validate_commercial_dd_question_category_map",
]
