"""Production run-source contract models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["RunSource", "RunSourceType"]


class RunSourceType(StrEnum):
    """Supported production run source types."""

    DEAL_DOCUMENTS = "deal_documents"


class RunSource(BaseModel):
    """Validated source contract for production runs.

    Slice 19 only supports durable document rows already associated with the
    target deal. Local filesystem paths remain harness-only.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: RunSourceType
    document_ids: list[str] = Field(..., min_length=1)

    @field_validator("document_ids")
    @classmethod
    def _dedupe_document_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for document_id in value:
            normalized = document_id.strip()
            if not normalized:
                raise ValueError("document_ids must not contain blank values")
            if _looks_like_path_or_uri(normalized):
                raise ValueError("document_ids must be durable document IDs, not paths or URIs")
            if normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return deduped

    @model_validator(mode="after")
    def _validate_deal_documents(self) -> RunSource:
        if self.type != RunSourceType.DEAL_DOCUMENTS:
            raise ValueError("only deal_documents run sources are supported")
        return self

    def to_storage_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation for run persistence."""
        return {"type": self.type.value, "document_ids": list(self.document_ids)}


def _looks_like_path_or_uri(value: str) -> bool:
    return "://" in value or "/" in value or "\\" in value or (len(value) > 1 and value[1] == ":")
