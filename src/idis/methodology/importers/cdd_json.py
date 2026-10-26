"""Deterministic source binding for the checked-in Commercial DD template."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import ValidationError

from idis.methodology.ids import normalize_text
from idis.methodology.models import MethodologyRegistry, MethodologyType

CDD_SOURCE_ALIAS = "commercial-dd-template-v1"
EXPECTED_CDD_QUESTION_COUNT = 21


class CDDImporterError(ValueError):
    """Raised when checked-in CDD bytes cannot be source-bound safely."""

    code = "CDD_IMPORT_INVALID"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "CDD_IMPORT_INVALID"


def import_cdd_template(
    template: bytes | Path | str | BinaryIO,
) -> MethodologyRegistry:
    """Load CDD JSON while rebinding all provenance to the raw artifact bytes."""
    raw_bytes, source_path = _read_template(template)
    artifact_hash = hashlib.sha256(raw_bytes).hexdigest()
    payload = _parse_payload(raw_bytes)

    try:
        _require_registry_shape(payload)
        bound_payload = _bind_source(payload, artifact_hash=artifact_hash)
        registry = MethodologyRegistry.model_validate(bound_payload)
        _require_bound_registry(registry, artifact_hash=artifact_hash)
    except CDDImporterError:
        raise
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise CDDImporterError("invalid checked-in CDD template") from exc

    if source_path is not None:
        try:
            after_bytes = source_path.read_bytes()
        except OSError as exc:
            raise CDDImporterError("CDD source changed during import") from exc
        if after_bytes != raw_bytes or hashlib.sha256(after_bytes).hexdigest() != artifact_hash:
            raise CDDImporterError("CDD source changed during import")

    return registry


def _read_template(
    template: bytes | Path | str | BinaryIO,
) -> tuple[bytes, Path | None]:
    if isinstance(template, bytes):
        return template, None
    if isinstance(template, (Path, str)):
        source_path = Path(template)
        try:
            return source_path.read_bytes(), source_path
        except OSError as exc:
            raise CDDImporterError("invalid checked-in CDD source") from exc
    try:
        raw_bytes = template.read()
    except Exception as exc:
        raise CDDImporterError("invalid checked-in CDD source") from exc
    if not isinstance(raw_bytes, bytes):
        raise CDDImporterError("invalid checked-in CDD source")
    return raw_bytes, None


def _parse_payload(raw_bytes: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CDDImporterError("invalid checked-in CDD template") from exc
    if not isinstance(payload, dict):
        raise CDDImporterError("invalid checked-in CDD template")
    return payload


def _require_registry_shape(payload: dict[str, Any]) -> None:
    if payload.get("methodology_id") != "commercial_dd":
        raise CDDImporterError("invalid CDD methodology identity")
    if payload.get("methodology_type") != MethodologyType.COMMERCIAL_DD.value:
        raise CDDImporterError("invalid CDD methodology type")

    versions = payload.get("versions")
    if not isinstance(versions, list) or len(versions) != 1:
        raise CDDImporterError("invalid CDD version inventory")
    version = versions[0]
    if not isinstance(version, dict):
        raise CDDImporterError("invalid CDD version")
    if version.get("methodology_id") != "commercial_dd":
        raise CDDImporterError("invalid CDD version identity")
    if version.get("methodology_type") != MethodologyType.COMMERCIAL_DD.value:
        raise CDDImporterError("invalid CDD version type")

    questions = version.get("questions")
    if not isinstance(questions, list) or len(questions) != EXPECTED_CDD_QUESTION_COUNT:
        raise CDDImporterError(
            f"invalid CDD question count: expected {EXPECTED_CDD_QUESTION_COUNT}"
        )
    if not all(isinstance(question, dict) for question in questions):
        raise CDDImporterError("invalid CDD question")
    _require_unique_questions(questions)


def _require_unique_questions(questions: list[dict[str, Any]]) -> None:
    seen_ids: set[str] = set()
    seen_rows: set[tuple[str, ...]] = set()
    for question in questions:
        question_id = question.get("methodology_question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise CDDImporterError("invalid CDD question identity")
        if question_id in seen_ids:
            raise CDDImporterError("duplicate methodology_question_id")
        seen_ids.add(question_id)

        semantic_row = tuple(
            normalize_text(_optional_text(question.get(field)))
            for field in (
                "section",
                "sheet_or_source_section",
                "term",
                "nature",
                "line_item",
                "question_text",
            )
        )
        if semantic_row in seen_rows:
            raise CDDImporterError("duplicate CDD methodology row")
        seen_rows.add(semantic_row)


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CDDImporterError("invalid CDD methodology row")
    return value


def _bind_source(payload: dict[str, Any], *, artifact_hash: str) -> dict[str, Any]:
    bound = deepcopy(payload)
    version = bound["versions"][0]
    version["source_hash"] = artifact_hash
    version["source_name"] = CDD_SOURCE_ALIAS
    for question in version["questions"]:
        source_trace = question.get("source_trace")
        if not isinstance(source_trace, dict):
            raise CDDImporterError("invalid CDD source trace")
        source_trace["source_type"] = "checked_in_template"
        source_trace["source_name"] = CDD_SOURCE_ALIAS
        source_trace["source_hash"] = artifact_hash
    return bound


def _require_bound_registry(
    registry: MethodologyRegistry,
    *,
    artifact_hash: str,
) -> None:
    version = registry.current_version
    if len(version.questions) != EXPECTED_CDD_QUESTION_COUNT:
        raise CDDImporterError("invalid CDD question count")
    if version.source_hash != artifact_hash or version.source_name != CDD_SOURCE_ALIAS:
        raise CDDImporterError("invalid CDD source binding")
    if any(
        question.source_trace.source_type != "checked_in_template"
        or question.source_trace.source_name != CDD_SOURCE_ALIAS
        or question.source_trace.source_hash != artifact_hash
        for question in version.questions
    ):
        raise CDDImporterError("invalid CDD question source binding")


__all__ = [
    "CDDImporterError",
    "CDD_SOURCE_ALIAS",
    "EXPECTED_CDD_QUESTION_COUNT",
    "import_cdd_template",
]
