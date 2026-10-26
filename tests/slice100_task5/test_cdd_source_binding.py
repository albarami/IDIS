"""Task 5 RED contracts for the checked-in CDD source binding."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from idis.methodology.importers.cdd_json import (
    CDD_SOURCE_ALIAS,
    CDDImporterError,
    import_cdd_template,
)
from idis.methodology.models import MethodologyType

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "idis"
    / "methodology"
    / "templates"
    / "commercial_dd_v1.json"
)
EXPECTED_ARTIFACT_SHA256 = "57cfec98436514cae717e359fa872504a7b9e1115c7bc12a4561e0989c4281fc"
LEGACY_EMBEDDED_SHA256 = "c" * 64


def _template_payload() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(TEMPLATE_PATH.read_text(encoding="utf-8")),
    )


def _encoded(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_checked_in_cdd_binds_exact_questions_to_raw_artifact_hash() -> None:
    """The raw checked-in bytes, not embedded legacy metadata, are the trust root."""
    raw = TEMPLATE_PATH.read_bytes()
    externally_computed_hash = hashlib.sha256(raw).hexdigest()

    registry = import_cdd_template(TEMPLATE_PATH)
    version = registry.current_version

    assert externally_computed_hash == EXPECTED_ARTIFACT_SHA256
    assert externally_computed_hash != LEGACY_EMBEDDED_SHA256
    assert registry.methodology_id == "commercial_dd"
    assert registry.methodology_type == MethodologyType.COMMERCIAL_DD
    assert len(version.questions) == 21
    assert version.source_hash == externally_computed_hash
    assert version.source_name == CDD_SOURCE_ALIAS
    assert ".json" not in CDD_SOURCE_ALIAS
    assert "/" not in CDD_SOURCE_ALIAS
    assert "\\" not in CDD_SOURCE_ALIAS
    assert all(
        question.source_trace.source_type == "checked_in_template"
        and question.source_trace.source_name == CDD_SOURCE_ALIAS
        and question.source_trace.source_hash == externally_computed_hash
        for question in version.questions
    )
    assert LEGACY_EMBEDDED_SHA256 not in registry.to_deterministic_json()


def test_checked_in_cdd_registry_hash_is_canonical_and_deterministic() -> None:
    first = import_cdd_template(TEMPLATE_PATH)
    second = import_cdd_template(TEMPLATE_PATH.read_bytes())

    assert first.registry_hash == second.registry_hash
    assert first.to_deterministic_json() == second.to_deterministic_json()
    assert len(first.registry_hash) == 64


def test_checked_in_cdd_path_is_byte_stable_across_import(tmp_path: Path) -> None:
    source = tmp_path / "operator-visible-name-must-not-escape.json"
    source.write_bytes(TEMPLATE_PATH.read_bytes())
    before_bytes = source.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()

    registry = import_cdd_template(source)

    after_bytes = source.read_bytes()
    assert after_bytes == before_bytes
    assert hashlib.sha256(after_bytes).hexdigest() == before_hash
    assert str(source) not in registry.to_deterministic_json()
    assert source.name not in registry.to_deterministic_json()


def test_checked_in_cdd_rejects_source_mutation_during_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "cdd.json"
    original = TEMPLATE_PATH.read_bytes()
    source.write_bytes(original)
    actual_read_bytes = Path.read_bytes
    calls = 0

    def changing_read_bytes(path: Path) -> bytes:
        nonlocal calls
        if path == source:
            calls += 1
            return original if calls == 1 else original + b"\n"
        return actual_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)

    with pytest.raises(CDDImporterError) as exc_info:
        import_cdd_template(source)

    assert exc_info.value.code == "CDD_IMPORT_INVALID"
    assert "changed during import" in str(exc_info.value)


def test_byte_drift_changes_the_computed_artifact_hash() -> None:
    original = TEMPLATE_PATH.read_bytes()
    byte_drift = original + b"\n"

    original_registry = import_cdd_template(original)
    drifted_registry = import_cdd_template(byte_drift)

    assert original_registry.current_version.source_hash == hashlib.sha256(original).hexdigest()
    assert drifted_registry.current_version.source_hash == hashlib.sha256(byte_drift).hexdigest()
    assert (
        drifted_registry.current_version.source_hash
        != original_registry.current_version.source_hash
    )


def test_semantic_drift_changes_the_canonical_registry_hash() -> None:
    payload = _template_payload()
    drifted = copy.deepcopy(payload)
    versions = drifted["versions"]
    assert isinstance(versions, list)
    questions = versions[0]["questions"]
    questions[0]["question_text"] = "A semantically different commercial DD question?"

    original_registry = import_cdd_template(_encoded(payload))
    drifted_registry = import_cdd_template(_encoded(drifted))

    assert drifted_registry.registry_hash != original_registry.registry_hash


@pytest.mark.parametrize("question_count", [20, 22])
def test_checked_in_cdd_rejects_wrong_question_count(question_count: int) -> None:
    payload = _template_payload()
    questions = payload["versions"][0]["questions"]
    if question_count == 20:
        questions.pop()
    else:
        extra = copy.deepcopy(questions[-1])
        extra["methodology_question_id"] = "mq_commercial_dd_extra_0001"
        extra["question_text"] = "A distinct extra methodology question?"
        questions.append(extra)

    with pytest.raises(CDDImporterError) as exc_info:
        import_cdd_template(_encoded(payload))

    assert exc_info.value.code == "CDD_IMPORT_INVALID"


def test_checked_in_cdd_rejects_duplicate_semantic_rows() -> None:
    payload = _template_payload()
    questions = payload["versions"][0]["questions"]
    duplicate = copy.deepcopy(questions[0])
    duplicate["methodology_question_id"] = "mq_commercial_dd_duplicate_0001"
    questions[-1] = duplicate

    with pytest.raises(CDDImporterError) as exc_info:
        import_cdd_template(_encoded(payload))

    assert exc_info.value.code == "CDD_IMPORT_INVALID"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update(methodology_id="financial_dd"),
        lambda payload: payload.update(methodology_type="financial_dd"),
        lambda payload: payload.update(versions=[]),
        lambda payload: payload["versions"].append(copy.deepcopy(payload["versions"][0])),
        lambda payload: payload["versions"][0]["questions"][0].update(question_text=""),
    ],
)
def test_checked_in_cdd_malformed_contract_fails_with_stable_safe_error(
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    payload = _template_payload()
    mutator(payload)

    with pytest.raises(CDDImporterError) as exc_info:
        import_cdd_template(_encoded(payload))

    assert exc_info.value.code == "CDD_IMPORT_INVALID"
    assert str(TEMPLATE_PATH) not in str(exc_info.value)
