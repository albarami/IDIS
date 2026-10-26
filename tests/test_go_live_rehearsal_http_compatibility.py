"""CIR-02: safe failure paths using the installed ASGI client, never a live API."""

from __future__ import annotations

import json
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import httpx
import httpx2
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

_SENSITIVE = "synthetic-body-credential-and-private-source-marker"
_STAGES = {
    "create": ("deals", "SYNTHETIC_API_DEAL_CREATE_FAILED"),
    "upload": ("deals/deal/documents/upload", "SYNTHETIC_API_UPLOAD_FAILED"),
    "status": ("runs/run", "SYNTHETIC_FULL_EXECUTION_STATUS_FAILED"),
    "list": ("deals/deal/deliverables", "SYNTHETIC_PACKAGE_LIST_FAILED"),
    "manifest": (
        "deals/deal/runs/run/product-bundle/manifest",
        "SYNTHETIC_PACKAGE_MANIFEST_FAILED",
    ),
    "download": ("deliverables/deliverable/content", ""),
}


def _app(failure_stage: str) -> FastAPI:
    app = FastAPI()

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    def endpoint(path: str, request: Request) -> JSONResponse:
        if path == _STAGES[failure_stage][0]:
            return JSONResponse({"detail": _SENSITIVE}, status_code=400)
        if path == "deals":
            return JSONResponse({"deal_id": "deal"}, status_code=201)
        if path == "deals/deal/runs":
            return JSONResponse({"run_id": "run"}, status_code=202)
        if path == "deals/deal/deliverables":
            return JSONResponse(
                {
                    "items": [
                        {
                            "run_id": "run",
                            "deliverable_id": "deliverable",
                            "deliverable_type": "IC_MEMO",
                            "format": "pdf",
                        }
                    ]
                }
            )
        if path.endswith("/manifest"):
            return JSONResponse(
                {
                    "run_id": "run",
                    "deal_id": "deal",
                    "artifact_count": 1,
                    "artifacts": [
                        {
                            "deliverable_id": "deliverable",
                            "type": "IC_MEMO",
                            "format": "pdf",
                            "sha256": "0" * 64,
                        }
                    ],
                }
            )
        raise AssertionError(f"Unexpected synthetic endpoint: {request.method} {path}")

    return app


@contextmanager
def _client(
    stage: str, response_family: Literal["current", "legacy"]
) -> Iterator[TestClient | httpx.Client]:
    """Keep the actual ASGI application while exercising either HTTP response family."""
    with TestClient(_app(stage), base_url="http://testserver.invalid") as asgi_client:
        if response_family == "current":
            yield asgi_client
            return

        def handle(request: httpx.Request) -> httpx.Response:
            response = asgi_client.request(
                request.method,
                str(request.url),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code, headers=dict(response.headers), content=response.content
            )

        with httpx.Client(
            transport=httpx.MockTransport(handle), base_url="http://testserver.invalid"
        ) as legacy_client:
            yield legacy_client


def _upload(client: TestClient | httpx.Client) -> None:
    rehearsal._upload_selected_gdbs_cases_core_with_client(
        client=client,
        dataset_root=Path("datasets/gdbs_full"),
        selected_cases=[SimpleNamespace(case_id="deal_001", directory="deals/deal_001_clean")],
        deal_stage="SEED",
    )


@pytest.mark.parametrize("stage", ["create", "upload", "status", "list", "manifest"])
@pytest.mark.parametrize("response_family", ["current", "legacy"])
def test_http_client_failures_are_sanitized(
    stage: str, response_family: Literal["current", "legacy"]
) -> None:
    with (
        _client(stage, response_family) as client,
        pytest.raises(rehearsal.SyntheticRehearsalScopeError) as caught,
    ):
        if stage in {"create", "upload"}:
            _upload(client)
        elif stage == "status":
            rehearsal._execute_non_strict_full_run_with_client(
                client=client, deal_id="deal", document_ids=["document"]
            )
        else:
            rehearsal._verified_same_run_package_surface_status(
                client=client, deal_id="deal", run_id="run"
            )
    assert str(caught.value) == f"{_STAGES[stage][1]}:status_code=400"
    _assert_safe_exception(caught.value)


@pytest.mark.parametrize("stage", ["list", "manifest"])
@pytest.mark.parametrize("response_family", ["current", "legacy"])
def test_package_verifier_list_and_manifest_failures_are_sanitized(
    stage: str, response_family: Literal["current", "legacy"]
) -> None:
    with (
        _client(stage, response_family) as client,
        pytest.raises(rehearsal.SyntheticRehearsalScopeError) as caught,
    ):
        rehearsal.verify_package_surfaces(
            client=client,
            api_key="synthetic-key",
            tenant_id="tenant",
            deal_id="deal",
            run_id="run",
            export_summary={"artifact_count": 1},
        )
    assert str(caught.value) == f"{_STAGES[stage][1]}:status_code=400"
    _assert_safe_exception(caught.value)


@pytest.mark.parametrize("same_run", [False, True])
@pytest.mark.parametrize("response_family", ["current", "legacy"])
def test_http_client_download_failures_remain_safe_reports(
    same_run: bool, response_family: Literal["current", "legacy"]
) -> None:
    with _client("download", response_family) as client:
        if same_run:
            report = rehearsal._verified_same_run_package_surface_status(
                client=client, deal_id="deal", run_id="run"
            )
            assert report["verified"] is False
        else:
            report = rehearsal.verify_package_surfaces(
                client=client,
                api_key="synthetic-key",
                tenant_id="tenant",
                deal_id="deal",
                run_id="run",
                export_summary={"artifact_count": 1},
            )
            assert report["package_surface_verified"] is False
    assert report["download_failures"] == [{"artifact": "IC_MEMO:pdf", "status_code": 400}]
    assert report["downloaded_artifact_count"] == 0
    assert _SENSITIVE not in json.dumps(report)
    assert "testserver.invalid" not in json.dumps(report)


@pytest.mark.parametrize("response_family", ["current", "legacy"])
def test_client_raised_upload_error_is_sanitized(
    response_family: Literal["current", "legacy"],
) -> None:
    def reject_upload(response: httpx.Response | httpx2.Response) -> None:
        if "documents/upload" in str(response.request.url):
            response.raise_for_status()

    with _client("upload", response_family) as client:
        client.params = {"credential": _SENSITIVE}
        client.event_hooks["response"].append(reject_upload)
        with pytest.raises(rehearsal.SyntheticRehearsalScopeError) as caught:
            _upload(client)
    assert str(caught.value) == "SYNTHETIC_API_UPLOAD_FAILED:status_code=400"
    _assert_safe_exception(caught.value)


def test_legacy_httpx_response_error_remains_sanitized() -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("GET", "http://testserver.invalid/?credential=" + _SENSITIVE),
        json={"detail": _SENSITIVE},
    )
    with pytest.raises(rehearsal.SyntheticRehearsalScopeError) as caught:
        rehearsal._raise_safe_api_error(response, reason_code="SYNTHETIC_API_UPLOAD_FAILED")
    assert str(caught.value) == "SYNTHETIC_API_UPLOAD_FAILED:status_code=400"
    _assert_safe_exception(caught.value)


@pytest.mark.parametrize("status", [200, 204])
def test_current_response_success_is_not_rejected(status: int) -> None:
    response = httpx2.Response(status, request=httpx2.Request("GET", "http://testserver.invalid"))
    rehearsal._raise_safe_api_error(response, reason_code="SYNTHETIC_API_UPLOAD_FAILED")


def test_unrelated_response_error_is_not_swallowed() -> None:
    class BrokenResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            raise ValueError("programming failure")

    with pytest.raises(ValueError, match="programming failure"):
        rehearsal._raise_safe_api_error(BrokenResponse(), reason_code="SYNTHETIC_TEST")


def _assert_safe_exception(error: Exception) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert _SENSITIVE not in rendered
    assert "testserver.invalid" not in rendered
    assert "filename=" not in rendered
    assert "sha256=" not in rendered
    assert error.__suppress_context__ is True
