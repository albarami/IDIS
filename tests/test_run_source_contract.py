"""Tests for Slice 19 production run-source contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idis.models.run_source import RunSource, RunSourceType


def test_deal_documents_source_requires_non_empty_document_ids() -> None:
    source = RunSource.model_validate(
        {"type": "deal_documents", "document_ids": ["doc-2", "doc-1", "doc-2"]}
    )

    assert source.type == RunSourceType.DEAL_DOCUMENTS
    assert source.document_ids == ["doc-2", "doc-1"]
    assert source.to_storage_dict() == {
        "type": "deal_documents",
        "document_ids": ["doc-2", "doc-1"],
    }


def test_run_source_rejects_path_like_fields() -> None:
    with pytest.raises(ValidationError):
        RunSource.model_validate(
            {
                "type": "deal_documents",
                "document_ids": ["doc-1"],
                "data_room_root_path": "C:/unsafe/data-room",
            }
        )


def test_run_source_rejects_path_like_document_id_values() -> None:
    with pytest.raises(ValidationError):
        RunSource.model_validate(
            {"type": "deal_documents", "document_ids": ["C:/data-room/model.xlsx"]}
        )


def test_run_source_rejects_unknown_source_types() -> None:
    with pytest.raises(ValidationError):
        RunSource.model_validate({"type": "local_folder", "document_ids": ["doc-1"]})
