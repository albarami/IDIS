"""Pydantic models for structured due diligence methodology registries."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MethodologyType(StrEnum):
    """Supported due diligence methodology families."""

    FINANCIAL_DD = "financial_dd"
    COMMERCIAL_DD = "commercial_dd"


class MethodologyBaseModel(BaseModel):
    """Base model with deterministic serialization settings."""

    model_config = ConfigDict(extra="ignore")


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

    @field_validator("source_type", "source_name", "source_hash", "sheet_or_section")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


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
    required_evidence: list[RequiredEvidence]
    target_document_categories: list[str]
    required_calculations: list[RequiredCalculation] = Field(default_factory=list)
    assigned_agents: list[AssignedAgent]
    red_flag_rules: list[RedFlagRule] = Field(default_factory=list)
    report_mapping: ReportMapping
    validation_requirements: list[str]
    source_trace: MethodologySourceTrace

    @field_validator(
        "methodology_id",
        "methodology_version_id",
        "methodology_question_id",
        "section",
        "question_text",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

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
    def _non_empty_list(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("list must not be empty")
        return value

    @field_validator("target_document_categories", "validation_requirements")
    @classmethod
    def _list_items_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned


class MethodologyVersion(MethodologyBaseModel):
    """Versioned collection of methodology questions."""

    methodology_id: str
    methodology_version_id: str
    methodology_type: MethodologyType
    version_label: str
    source_hash: str
    source_name: str | None = None
    questions: list[MethodologyQuestion]

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
        return self


class MethodologyRegistry(MethodologyBaseModel):
    """Top-level methodology registry."""

    methodology_id: str
    methodology_type: MethodologyType
    versions: list[MethodologyVersion]

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
    def _versions_not_empty(cls, value: list[MethodologyVersion]) -> list[MethodologyVersion]:
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
