"""Public fail-closed propagation for the bounded PDF OCR admission code."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from idis.api.routes.documents import (
    _safe_ingestion_error_details,
    _terminal_upload_error_code,
)
from idis.evaluation.real_example_source_package import (
    SourcePackageContractError,
    _validated_api_response,
)
from idis.parsers.base import ParseErrorCode


@dataclass(frozen=True)
class _Response:
    status_code: int
    payload: object

    def json(self) -> Any:
        return self.payload


def _parse_failure(parse_code: str) -> SimpleNamespace:
    return SimpleNamespace(
        code=SimpleNamespace(value="parse_failed"),
        message="private parser message",
        details={
            "parse_error_code": parse_code,
            "private_runtime_detail": "must-not-cross-the-route",
        },
    )


def test_parser_and_upload_route_preserve_exact_ocr_image_too_large_code() -> None:
    assert ParseErrorCode.OCR_IMAGE_TOO_LARGE.value == "ocr_image_too_large"
    failure = _parse_failure(ParseErrorCode.OCR_IMAGE_TOO_LARGE.value)

    assert _terminal_upload_error_code([failure]) == "ocr_image_too_large"
    assert _safe_ingestion_error_details([failure]) == [
        {
            "code": "ocr_image_too_large",
            "message": "Document ingestion failed",
        }
    ]


def test_durable_preparation_preserves_only_admitted_oversize_code() -> None:
    response = _Response(
        status_code=422,
        payload={
            "code": "ocr_image_too_large",
            "message": "private server message",
            "details": {"private": "must-not-cross-the-client"},
        },
    )

    with pytest.raises(SourcePackageContractError) as caught:
        _validated_api_response(response, expected_status=201)

    assert caught.value.code == "ocr_image_too_large"
    assert str(caught.value) == (
        "The durable upload rejected an OCR page above the admitted image bound"
    )
    assert "private" not in str(caught.value)


def test_durable_preparation_still_masks_unadmitted_server_codes() -> None:
    response = _Response(
        status_code=422,
        payload={
            "code": "ARBITRARY_SERVER_DETAIL",
            "message": "private server message",
        },
    )

    with pytest.raises(SourcePackageContractError) as caught:
        _validated_api_response(response, expected_status=201)

    assert caught.value.code == "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE"
    assert "private server message" not in str(caught.value)
