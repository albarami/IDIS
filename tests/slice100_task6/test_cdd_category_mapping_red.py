"""Task-6 RED contracts for the governed Commercial-DD category map."""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import inspect
import json
import subprocess
import sys
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from idis.contracts import build_lock_document
from idis.methodology.importers.cdd_json import import_cdd_template
from idis.models.document_classification import CddDocumentCategory, DocumentClassificationSource
from idis.persistence.repositories.methodologies import methodology_question_payload_hashes
from idis.services.documents import classifier as classifier_module
from idis.services.documents.classifier import DocumentDescriptor, classify_document
from idis.services.extraction.task_planner import _normalized_cdd_targets

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CDD_TEMPLATE = _REPO_ROOT / "src" / "idis" / "methodology" / "templates" / "commercial_dd_v1.json"
_MAP_RELATIVE = "src/idis/methodology/mappings/commercial_dd_v1_question_categories.json"
_SCHEMA_RELATIVE = "src/idis/methodology/mappings/commercial_dd_v1_question_categories.schema.json"
_EXPECTED_CANONICAL_MAP_SHA256 = "bc5753df9c8ab7a6d6a56cd02185061b1a4eb36c111387349c0a4370d30d097e"

_PRODUCTIVE_CDD_CATEGORIES = tuple(
    sorted(
        category
        for category in CddDocumentCategory
        if category not in {CddDocumentCategory.UNKNOWN, CddDocumentCategory.UNSUPPORTED}
    )
)


def _mapping_module() -> object:
    module_name = "idis.methodology.category_mapping"
    assert importlib.util.find_spec(module_name) is not None, (
        "the governed CDD question-category loader is absent"
    )
    return importlib.import_module(module_name)


def _descriptor(filename: str, spans: list[str] | None = None) -> DocumentDescriptor:
    return DocumentDescriptor(
        tenant_id="11111111-1111-1111-1111-111111111111",
        deal_id="22222222-2222-2222-2222-222222222222",
        document_id=f"doc-{filename}",
        filename=filename,
        file_size_bytes=1,
        parsed_span_texts=spans or [],
    )


def test_actual_cdd_artifact_requires_a_governed_question_aware_map() -> None:
    registry = import_cdd_template(_CDD_TEMPLATE)
    legacy_empty = tuple(
        question.methodology_question_id
        for question in registry.current_version.questions
        if not _normalized_cdd_targets(question)
    )
    assert legacy_empty == (
        "mq_commercial_dd_customer_quality_0001",
        "mq_commercial_dd_gtm_model_0001",
        "mq_commercial_dd_distribution_0001",
        "mq_commercial_dd_pmf_0001",
        "mq_commercial_dd_technology_diff_0001",
        "mq_commercial_dd_management_0001",
        "mq_commercial_dd_risks_0001",
    )

    module = _mapping_module()
    governed = module.load_commercial_dd_question_category_map()
    expected_inventory = tuple(
        zip(
            (question.methodology_question_id for question in registry.current_version.questions),
            methodology_question_payload_hashes(registry),
            strict=True,
        )
    )

    assert governed.methodology_source_sha256 == registry.current_version.source_hash
    assert governed.imported_registry_sha256 == registry.registry_hash
    assert (
        tuple(
            (entry.methodology_question_id, entry.methodology_question_sha256)
            for entry in governed.questions
        )
        == expected_inventory
    )
    assert all(entry.categories for entry in governed.questions)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("go to market", CddDocumentCategory.GTM_DISTRIBUTION),
        ("gtm", CddDocumentCategory.GTM_DISTRIBUTION),
        ("distribution channel", CddDocumentCategory.GTM_DISTRIBUTION),
        ("channel performance", CddDocumentCategory.GTM_DISTRIBUTION),
        ("sales plan", CddDocumentCategory.GTM_DISTRIBUTION),
        ("marketing report", CddDocumentCategory.GTM_DISTRIBUTION),
        ("management team", CddDocumentCategory.MANAGEMENT_TEAM),
        ("founder profile", CddDocumentCategory.MANAGEMENT_TEAM),
        ("leadership team", CddDocumentCategory.MANAGEMENT_TEAM),
        ("executive team", CddDocumentCategory.MANAGEMENT_TEAM),
        ("management reference", CddDocumentCategory.MANAGEMENT_TEAM),
        ("commercial risk", CddDocumentCategory.COMMERCIAL_RISKS),
        ("commercial risks", CddDocumentCategory.COMMERCIAL_RISKS),
        ("risk register", CddDocumentCategory.COMMERCIAL_RISKS),
    ],
)
def test_classifier_v2_reaches_new_cdd_categories_with_narrow_positive_phrases(
    phrase: str,
    expected: CddDocumentCategory,
) -> None:
    classification = classify_document(_descriptor(f"synthetic {phrase}.pdf"))

    assert classification.cdd_category == expected


@pytest.mark.parametrize("generic", ["team", "risk", "channel", "management presentation"])
def test_classifier_v2_does_not_activate_new_categories_for_generic_tokens(generic: str) -> None:
    classification = classify_document(_descriptor(f"synthetic {generic}.pdf"))

    assert classification.cdd_category not in {
        CddDocumentCategory.GTM_DISTRIBUTION,
        CddDocumentCategory.MANAGEMENT_TEAM,
        CddDocumentCategory.COMMERCIAL_RISKS,
    }


def test_classifier_v2_all_productive_cdd_categories_are_reachable() -> None:
    fixtures = (
        _descriptor("synthetic financial model.xlsx"),
        _descriptor("synthetic customer contract.docx"),
        _descriptor("synthetic market research.pdf"),
        _descriptor("synthetic product technology.pdf"),
        _descriptor("synthetic competitor analysis.pdf"),
        _descriptor("synthetic sales pipeline.xlsx"),
        _descriptor("synthetic go to market.pdf"),
        _descriptor("synthetic management team.pdf"),
        _descriptor("synthetic commercial risk.pdf"),
    )
    reachable: set[CddDocumentCategory] = set()
    for fixture in fixtures:
        classification = classify_document(fixture)
        reachable.add(classification.cdd_category)
        reachable.update(classification.secondary_cdd_categories)
        assert classification.secondary_cdd_categories == sorted(
            set(classification.secondary_cdd_categories)
        )

    assert tuple(sorted(reachable)) == _PRODUCTIVE_CDD_CATEGORIES


def test_classifier_and_governed_map_share_one_versioned_reachability_contract() -> None:
    governed = _mapping_module().load_commercial_dd_question_category_map()

    assert governed.classifier_version == classifier_module.CDD_DOCUMENT_CLASSIFIER_VERSION
    assert (
        governed.classifier_reachable_categories
        == classifier_module.CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES
    )
    assert (
        governed.classifier_reachable_categories_sha256
        == classifier_module.CDD_DOCUMENT_CLASSIFIER_REACHABLE_CATEGORIES_SHA256
    )


@pytest.mark.parametrize(
    "descriptor",
    [
        _descriptor("management").model_copy(update={"title": "team"}),
        _descriptor(
            "synthetic.pdf",
            ["This identifies an operational risk", "register controls are elsewhere"],
        ),
    ],
    ids=("filename-title", "separate-spans"),
)
def test_classifier_v2_never_fabricates_a_phrase_across_descriptor_fields(
    descriptor: DocumentDescriptor,
) -> None:
    assert classify_document(descriptor).cdd_category == CddDocumentCategory.UNKNOWN


def test_classifier_v2_established_primary_survives_new_category_collisions() -> None:
    descriptor = _descriptor(
        "synthetic financial model.xlsx",
        ["commercial risk"],
    ).model_copy(
        update={
            "title": "management team",
            "artifact_doc_type": "go to market",
        }
    )

    classification = classify_document(descriptor)

    assert classification.cdd_category == CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS
    assert classification.secondary_cdd_categories == [
        CddDocumentCategory.COMMERCIAL_RISKS,
        CddDocumentCategory.GTM_DISTRIBUTION,
        CddDocumentCategory.MANAGEMENT_TEAM,
        CddDocumentCategory.PRICING_UNIT_ECONOMICS,
    ]
    assert classification.reason_codes[0] == "filename_financial_model"


@pytest.mark.parametrize(
    ("phrase", "expected", "reason_code"),
    [
        ("go to market", CddDocumentCategory.GTM_DISTRIBUTION, "span_gtm_distribution"),
        ("management team", CddDocumentCategory.MANAGEMENT_TEAM, "span_management_team"),
        ("commercial risk", CddDocumentCategory.COMMERCIAL_RISKS, "span_commercial_risks"),
    ],
)
def test_classifier_v2_span_only_positive_has_span_provenance(
    phrase: str,
    expected: CddDocumentCategory,
    reason_code: str,
) -> None:
    classification = classify_document(_descriptor("synthetic.pdf", [phrase]))

    assert classification.cdd_category == expected
    assert classification.evidence[0].source == DocumentClassificationSource.SPAN
    assert classification.evidence[0].reason_code == reason_code
    assert classification.reason_codes[0] == reason_code


@pytest.mark.parametrize(
    ("field", "phrase", "expected", "source", "reason_code"),
    [
        (
            "filename",
            "synthetic go to market.pdf",
            CddDocumentCategory.GTM_DISTRIBUTION,
            DocumentClassificationSource.RULE,
            "filename_gtm_distribution",
        ),
        (
            "title",
            "management team",
            CddDocumentCategory.MANAGEMENT_TEAM,
            DocumentClassificationSource.ARTIFACT_METADATA,
            "title_management_team",
        ),
        (
            "title",
            "commercial risk",
            CddDocumentCategory.COMMERCIAL_RISKS,
            DocumentClassificationSource.ARTIFACT_METADATA,
            "title_commercial_risks",
        ),
    ],
)
def test_classifier_v2_filename_and_title_provenance_is_source_accurate(
    field: str,
    phrase: str,
    expected: CddDocumentCategory,
    source: DocumentClassificationSource,
    reason_code: str,
) -> None:
    descriptor = _descriptor("synthetic.pdf")
    descriptor = descriptor.model_copy(update={field: phrase})

    classification = classify_document(descriptor)

    assert classification.cdd_category == expected
    assert classification.evidence[0].source == source
    assert classification.evidence[0].reason_code == reason_code
    assert classification.reason_codes[0] == reason_code


def test_cdd_map_and_schema_are_contract_locked() -> None:
    lock = build_lock_document(_REPO_ROOT)

    assert _MAP_RELATIVE in lock["files"]
    assert _SCHEMA_RELATIVE in lock["files"]
    committed = json.loads((_REPO_ROOT / "contracts" / "contract_lock.json").read_text("utf-8"))
    assert committed["files"][_MAP_RELATIVE] == lock["files"][_MAP_RELATIVE]
    assert committed["files"][_SCHEMA_RELATIVE] == lock["files"][_SCHEMA_RELATIVE]


def _map_payload() -> dict[str, Any]:
    return json.loads((_REPO_ROOT / _MAP_RELATIVE).read_text(encoding="utf-8"))


def _encoded(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _load_with_bytes(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> object:
    module = _mapping_module()
    map_path = (_REPO_ROOT / _MAP_RELATIVE).resolve()
    real_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path.resolve() == map_path:
            return raw
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return module.load_commercial_dd_question_category_map()


def _load_error(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> BaseException:
    try:
        _load_with_bytes(monkeypatch, raw)
    except BaseException as exc:  # noqa: BLE001 - the contract asserts the public stable code
        return exc
    pytest.fail("tampered governed category map was accepted")


def test_map_schema_is_strict_and_accepts_only_the_production_artifact() -> None:
    schema_path = _REPO_ROOT / _SCHEMA_RELATIVE
    assert schema_path.is_file(), "the strict mapping schema is absent"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(_map_payload())) == []
    assert schema["additionalProperties"] is False
    assert schema["properties"]["questions"]["items"]["additionalProperties"] is False


def test_map_loader_is_fixed_path_immutable_and_code_hash_pinned() -> None:
    module = _mapping_module()
    loader = module.load_commercial_dd_question_category_map
    assert tuple(inspect.signature(loader).parameters) == ()

    governed = loader()
    assert governed.canonical_payload_sha256 == _EXPECTED_CANONICAL_MAP_SHA256
    assert module.CDD_QUESTION_CATEGORY_MAP_CANONICAL_SHA256 == _EXPECTED_CANONICAL_MAP_SHA256
    with pytest.raises(FrozenInstanceError):
        governed.mapping_version = "changed"


def test_normal_built_wheel_loads_identical_packaged_map_and_schema(tmp_path: Path) -> None:
    wheel_path = tmp_path / "idis-6.3.0-py3-none-any.whl"
    package_root = _REPO_ROOT / "src" / "idis"
    entries = {
        path.relative_to(package_root.parent).as_posix(): path.read_bytes()
        for path in package_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    dist_info = "idis-6.3.0.dist-info"
    entries[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: idis-stdlib-wheel-smoke\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n"
    )
    entries[f"{dist_info}/METADATA"] = b"Metadata-Version: 2.1\nName: idis\nVersion: 6.3.0\n"
    record_lines = []
    for relative, content in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        record_lines.append(f"{relative},sha256={digest},{len(content)}")
    record_relative = f"{dist_info}/RECORD"
    entries[record_relative] = ("\n".join([*record_lines, f"{record_relative},,"]) + "\n").encode()
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for relative, content in sorted(entries.items()):
            wheel.writestr(relative, content)

    probe = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import hashlib,importlib.resources,json,sys;"
                f"sys.path.insert(0,{str(wheel_path)!r});"
                "import idis;"
                "from idis.methodology.category_mapping import "
                "load_commercial_dd_question_category_map;"
                "root=importlib.resources.files('idis.methodology.mappings');"
                "mapping=root.joinpath('commercial_dd_v1_question_categories.json').read_bytes();"
                "schema=root.joinpath('commercial_dd_v1_question_categories.schema.json').read_bytes();"
                "governed=load_commercial_dd_question_category_map();"
                "print(json.dumps({'module_file':idis.__file__,"
                "'mapping_sha256':hashlib.sha256(mapping).hexdigest(),"
                "'schema_sha256':hashlib.sha256(schema).hexdigest(),"
                "'canonical_sha256':governed.canonical_payload_sha256},sort_keys=True))"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    evidence = json.loads(probe.stdout)
    packaged_dir = _REPO_ROOT / "src" / "idis" / "methodology" / "mappings"
    source_map = packaged_dir / "commercial_dd_v1_question_categories.json"
    source_schema = packaged_dir / "commercial_dd_v1_question_categories.schema.json"
    assert str(evidence["module_file"]).lower().startswith(str(wheel_path).lower())
    assert evidence == {
        "module_file": evidence["module_file"],
        "mapping_sha256": hashlib.sha256(source_map.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(source_schema.read_bytes()).hexdigest(),
        "canonical_sha256": _EXPECTED_CANONICAL_MAP_SHA256,
    }


def test_map_loader_unavailable_is_a_stable_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _mapping_module()
    map_path = (_REPO_ROOT / _MAP_RELATIVE).resolve()
    real_read_bytes = Path.read_bytes

    def unavailable(path: Path) -> bytes:
        if path.resolve() == map_path:
            raise OSError("private host path must not escape")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unavailable)
    with pytest.raises(module.CommercialDDCategoryMapError) as exc_info:
        module.load_commercial_dd_question_category_map()
    assert exc_info.value.code == "METHODOLOGY_CDD_CATEGORY_MAP_UNAVAILABLE"
    assert "private host path" not in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"{not-json",
        (
            b'{"schema_version":"cdd-question-category-map-v1",'
            b'"schema_version":"cdd-question-category-map-v1"}'
        ),
        _encoded({**_map_payload(), "unexpected": True}),
        _encoded({**_map_payload(), "questions": []}),
        _encoded(
            {
                **_map_payload(),
                "questions": [
                    {
                        **_map_payload()["questions"][0],
                        "categories": ["not_a_cdd_category"],
                    },
                    *_map_payload()["questions"][1:],
                ],
            }
        ),
        _encoded(
            {
                **_map_payload(),
                "questions": [
                    {**_map_payload()["questions"][0], "categories": []},
                    *_map_payload()["questions"][1:],
                ],
            }
        ),
        _encoded(
            {
                **_map_payload(),
                "questions": [
                    {
                        **_map_payload()["questions"][0],
                        "categories": ["market_research", "market_research"],
                    },
                    *_map_payload()["questions"][1:],
                ],
            }
        ),
    ],
    ids=(
        "non-utf8",
        "malformed-json",
        "duplicate-json-key",
        "extra-root-field",
        "empty-question-inventory",
        "unknown-category",
        "empty-category-set",
        "duplicate-category",
    ),
)
def test_map_loader_rejects_invalid_shape_with_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    error = _load_error(monkeypatch, raw)
    assert getattr(error, "code", None) == "METHODOLOGY_CDD_CATEGORY_MAP_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "cdd-question-category-map-v2"),
        ("mapping_version", "1.0.1"),
        ("methodology_id", "financial_dd"),
        ("methodology_version_id", "commercial_dd:v2"),
        ("methodology_source_sha256", "a" * 64),
        ("imported_registry_sha256", "b" * 64),
        ("classifier_version", "cdd-document-classifier-v1"),
        ("classifier_reachable_categories_sha256", "c" * 64),
    ],
)
def test_map_loader_rejects_governance_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    payload = _map_payload()
    payload[field] = value

    error = _load_error(monkeypatch, _encoded(payload))

    assert getattr(error, "code", None) == "METHODOLOGY_CDD_CATEGORY_MAP_DRIFT"


@pytest.mark.parametrize(
    "mutation",
    [
        "question-order",
        "question-id",
        "question-hash",
        "question-category",
        "question-category-order",
        "reachable-set",
        "reachable-set-order",
    ],
)
def test_map_loader_rejects_question_category_or_reachability_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    payload = _map_payload()
    if mutation == "question-order":
        payload["questions"][0], payload["questions"][1] = (
            payload["questions"][1],
            payload["questions"][0],
        )
    elif mutation == "question-id":
        payload["questions"][0]["methodology_question_id"] = "mq_commercial_dd_other_0001"
    elif mutation == "question-hash":
        payload["questions"][0]["methodology_question_sha256"] = "d" * 64
    elif mutation == "question-category":
        payload["questions"][0]["categories"] = ["customer_evidence"]
    elif mutation == "question-category-order":
        payload["questions"][1]["categories"] = list(
            reversed(payload["questions"][1]["categories"])
        )
    elif mutation == "reachable-set":
        payload["classifier_reachable_categories"] = payload["classifier_reachable_categories"][:-1]
    else:
        payload["classifier_reachable_categories"] = list(
            reversed(payload["classifier_reachable_categories"])
        )

    error = _load_error(monkeypatch, _encoded(payload))

    assert getattr(error, "code", None) == "METHODOLOGY_CDD_CATEGORY_MAP_DRIFT"
