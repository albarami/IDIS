"""Pydantic models for structured due diligence methodology registries."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MethodologyType(StrEnum):
    """Supported due diligence methodology families."""

    FINANCIAL_DD = "financial_dd"
    COMMERCIAL_DD = "commercial_dd"


class MethodologyBaseModel(BaseModel):
    """Base model with deterministic serialization settings."""

    model_config = ConfigDict(extra="ignore", frozen=True)


_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RequiredEvidence(MethodologyBaseModel):
    """Evidence required to answer a methodology question."""

    evidence_type: str
    description: str
    min_count: int = Field(default=1, ge=1)

    @field_validator("evidence_type", "description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class RequiredCalculation(MethodologyBaseModel):
    """Calculation requirement for a methodology question."""

    calc_type: str
    required: bool = True

    @field_validator("calc_type")
    @classmethod
    def _calc_type_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calc_type must not be blank")
        return value.strip()


class AssignedAgent(MethodologyBaseModel):
    """Agent role assignment for a methodology question."""

    role: str
    responsibility: str

    @field_validator("role", "responsibility")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class RedFlagRule(MethodologyBaseModel):
    """Structured red-flag rule tied to a question."""

    rule_id: str
    description: str
    severity: str

    @field_validator("rule_id", "description", "severity")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class ReportMapping(MethodologyBaseModel):
    """Mapping from question to future report location."""

    report_section: str
    report_subsection: str | None = None

    @field_validator("report_section")
    @classmethod
    def _section_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("report_section must not be blank")
        return value.strip()


class MethodologySourceTrace(MethodologyBaseModel):
    """Trace metadata from methodology source artifact/template."""

    source_type: str
    source_name: str
    source_hash: str
    sheet_or_section: str
    row_number: int | None = None
    source_anchor_cell: str | None = None

    @property
    def anchor_cell(self) -> str | None:
        """Return the legacy-safe name for the persisted source anchor cell."""
        return self.source_anchor_cell

    @field_validator("source_type", "source_name", "source_hash", "sheet_or_section")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_hash")
    @classmethod
    def _source_hash_is_lowercase_sha256(cls, value: str) -> str:
        if _LOWERCASE_SHA256.fullmatch(value) is None:
            raise ValueError("source_hash must be a lowercase SHA-256")
        return value

    @field_validator("row_number")
    @classmethod
    def _row_number_is_physical_excel_row(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("row_number must be a positive physical row")
        return value

    @field_validator("source_anchor_cell")
    @classmethod
    def _source_anchor_cell_is_a1_coordinate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", cleaned) is None:
            raise ValueError("source_anchor_cell must be an uppercase A1 cell coordinate")
        return cleaned


class MethodologyQuestion(MethodologyBaseModel):
    """A stable, auditable due diligence methodology question."""

    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    methodology_type: MethodologyType
    section: str
    sheet_or_source_section: str | None = None
    source_row_number: int | None = None
    term: str | None = None
    nature: str | None = None
    line_item: str | None = None
    question_text: str
    required_evidence: tuple[RequiredEvidence, ...]
    target_document_categories: tuple[str, ...]
    required_calculations: tuple[RequiredCalculation, ...] = ()
    assigned_agents: tuple[AssignedAgent, ...]
    red_flag_rules: tuple[RedFlagRule, ...] = ()
    report_mapping: ReportMapping
    validation_requirements: tuple[str, ...]
    source_trace: MethodologySourceTrace

    @field_validator(
        "methodology_id",
        "methodology_version_id",
        "methodology_question_id",
        "section",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("question_text")
    @classmethod
    def _question_text_not_blank_and_exact(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question_text must not be blank")
        return value

    @field_validator("methodology_question_id")
    @classmethod
    def _stable_id_format(cls, value: str) -> str:
        if not value.startswith("mq_"):
            raise ValueError("methodology_question_id must start with mq_")
        return value

    @field_validator(
        "required_evidence",
        "target_document_categories",
        "assigned_agents",
        "validation_requirements",
    )
    @classmethod
    def _non_empty_list(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("list must not be empty")
        return value

    @field_validator("target_document_categories", "validation_requirements")
    @classmethod
    def _list_items_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _source_trace_is_complete_and_consistent(self) -> MethodologyQuestion:
        if (
            self.methodology_type == MethodologyType.FINANCIAL_DD
            and self.source_trace.source_type == "operator_workbook"
        ):
            if self.source_row_number is None or self.source_trace.row_number is None:
                raise ValueError("financial_dd question requires Excel row trace")
            if not self.sheet_or_source_section:
                raise ValueError("financial_dd question requires sheet row trace")
        if self.source_row_number != self.source_trace.row_number:
            raise ValueError("source row trace must match question row")
        if (
            self.sheet_or_source_section is not None
            and self.sheet_or_source_section != self.source_trace.sheet_or_section
        ):
            raise ValueError("source sheet trace must match question sheet")
        return self


class MethodologyVersion(MethodologyBaseModel):
    """Versioned collection of methodology questions."""

    methodology_id: str
    methodology_version_id: str
    methodology_type: MethodologyType
    version_label: str
    source_hash: str
    source_name: str | None = None
    questions: tuple[MethodologyQuestion, ...]

    @field_validator(
        "methodology_id",
        "methodology_version_id",
        "version_label",
        "source_hash",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_hash")
    @classmethod
    def _source_hash_is_lowercase_sha256(cls, value: str) -> str:
        if _LOWERCASE_SHA256.fullmatch(value) is None:
            raise ValueError("source_hash must be a lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _question_ids_are_unique(self) -> MethodologyVersion:
        if not self.questions:
            raise ValueError("questions must not be empty")
        ids = [question.methodology_question_id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate methodology_question_id")
        for question in self.questions:
            if question.methodology_id != self.methodology_id:
                raise ValueError("question methodology_id must match version")
            if question.methodology_version_id != self.methodology_version_id:
                raise ValueError("question methodology_version_id must match version")
            if question.methodology_type != self.methodology_type:
                raise ValueError("question methodology_type must match version")
            if (
                question.source_trace.source_type in {"operator_workbook", "checked_in_template"}
                and question.source_trace.source_hash != self.source_hash
            ):
                raise ValueError("question source_hash must match version source_hash")
        return self


class MethodologyRegistry(MethodologyBaseModel):
    """Top-level methodology registry."""

    methodology_id: str
    methodology_type: MethodologyType
    versions: tuple[MethodologyVersion, ...]

    @property
    def current_version(self) -> MethodologyVersion:
        """Return the latest registered version."""
        return self.versions[-1]

    @property
    def registry_hash(self) -> str:
        """Hash of deterministic registry payload excluding this computed hash."""
        payload = self.model_dump(mode="json", exclude={"registry_hash"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_deterministic_json(self) -> str:
        """Serialize registry deterministically for audit/version control."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    @field_validator("methodology_id")
    @classmethod
    def _methodology_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("methodology_id must not be blank")
        return value.strip()

    @field_validator("versions")
    @classmethod
    def _versions_not_empty(
        cls, value: tuple[MethodologyVersion, ...]
    ) -> tuple[MethodologyVersion, ...]:
        if not value:
            raise ValueError("versions must not be empty")
        return value

    @model_validator(mode="after")
    def _version_identity_is_consistent(self) -> MethodologyRegistry:
        version_ids = [version.methodology_version_id for version in self.versions]
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("duplicate methodology_version_id")
        for version in self.versions:
            if version.methodology_id != self.methodology_id:
                raise ValueError("version methodology_id must match registry")
            if version.methodology_type != self.methodology_type:
                raise ValueError("version methodology_type must match registry")
        return self
