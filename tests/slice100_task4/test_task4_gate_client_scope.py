"""RED method/path/transport contract for Task-4 external API clients."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from idis.evaluation import real_example_gate as gate

_OWNER_KEY = "fictional-task4-owner-key"
_CANARY_KEY = "fictional-task4-canary-key"
_DEAL_ID = "11111111-1111-4111-8111-111111111111"
_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
_PACKAGE_ID = "33333333-3333-4333-8333-333333333333"
_ARTIFACT_ID = "44444444-4444-4444-8444-444444444444"

# These are literal client contracts, intentionally independent of future production constants.
_OWNER_ALLOWED = (
    ("buffered", "GET", "/health"),
    ("buffered", "GET", "/v1/strict-readiness"),
    ("buffered", "POST", "/v1/deals"),
    ("buffered", "GET", f"/v1/deals/{_DEAL_ID}"),
    ("buffered", "POST", f"/v1/deals/{_DEAL_ID}/documents/upload"),
    ("buffered", "GET", f"/v1/deals/{_DEAL_ID}/documents/{_DOCUMENT_ID}"),
    ("buffered", "POST", f"/v1/deals/{_DEAL_ID}/data-room-packages"),
    (
        "buffered",
        "GET",
        f"/v1/deals/{_DEAL_ID}/data-room-packages/{_PACKAGE_ID}",
    ),
    ("buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}/content-integrity"),
    ("buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}/spans"),
    ("stream", "GET", f"/v1/documents/{_ARTIFACT_ID}/content"),
)

_CANARY_ALLOWED = (
    (
        "buffered",
        "GET",
        f"/v1/deals/{_DEAL_ID}/data-room-packages/{_PACKAGE_ID}",
    ),
    ("buffered", "GET", f"/v1/deals/{_DEAL_ID}/documents/{_DOCUMENT_ID}"),
    ("buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}/content-integrity"),
    ("buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}/spans"),
    ("buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}"),
    ("stream", "GET", f"/v1/documents/{_ARTIFACT_ID}/content"),
    ("buffered", "POST", f"/v1/deals/{_DEAL_ID}/data-room-packages"),
    ("buffered", "POST", f"/v1/deals/{_DEAL_ID}/documents/upload"),
    ("buffered", "DELETE", f"/v1/documents/{_ARTIFACT_ID}"),
)

_DENIED_CALLS = (
    ("owner", "buffered", "GET", "/metrics"),
    ("owner", "buffered", "GET", "/v1/audit/events"),
    ("owner", "buffered", "GET", "/v1/deals"),
    ("owner", "buffered", "POST", f"/v1/deals/{_DEAL_ID}/runs"),
    ("owner", "buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}"),
    ("owner", "buffered", "DELETE", f"/v1/documents/{_ARTIFACT_ID}"),
    ("owner", "buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}/content"),
    ("owner", "stream", "POST", f"/v1/documents/{_ARTIFACT_ID}/content"),
    ("canary", "buffered", "GET", "/health"),
    ("canary", "buffered", "GET", "/v1/strict-readiness"),
    ("canary", "buffered", "POST", "/v1/deals"),
    ("canary", "buffered", "GET", f"/v1/deals/{_DEAL_ID}"),
    ("canary", "buffered", "GET", f"/v1/documents/{_ARTIFACT_ID}/content"),
    ("canary", "stream", "DELETE", f"/v1/documents/{_ARTIFACT_ID}"),
    ("owner", "buffered", "GET", f"/v1/deals/{_DEAL_ID}/"),
    ("owner", "buffered", "GET", "/v1/deals/not-a-canonical-uuid"),
    ("owner", "buffered", "GET", f"/v1/deals/{_DEAL_ID}%2fdocuments"),
)


class _FakeResponse:
    status_code = 200

    @staticmethod
    def json() -> dict[str, object]:
        return {}


class _FakeStream(_FakeResponse):
    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _settings() -> Any:
    return gate._DurableApiSettings(
        api_base_url="http://127.0.0.1:8000",
        api_key=_OWNER_KEY,
        canary_api_key=_CANARY_KEY,
        connect_timeout_seconds=10.0,
        write_timeout_seconds=64.0,
        read_timeout_seconds=930.0,
        pool_timeout_seconds=10.0,
        request_interval_seconds=0.001,
    )


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_scope: str,
) -> tuple[Any, list[tuple[str, str, str]], str]:
    calls: list[tuple[str, str, str]] = []

    class FakeHttpxClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
            calls.append(("buffered", "GET", url))
            return _FakeResponse()

        def post(self, url: str, **_kwargs: Any) -> _FakeResponse:
            calls.append(("buffered", "POST", url))
            return _FakeResponse()

        def delete(self, url: str, **_kwargs: Any) -> _FakeResponse:
            calls.append(("buffered", "DELETE", url))
            return _FakeResponse()

        def stream(self, method: str, url: str, **_kwargs: Any) -> _FakeStream:
            calls.append(("stream", method, url))
            return _FakeStream()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        gate,
        "_import_httpx",
        lambda: SimpleNamespace(Client=FakeHttpxClient, Timeout=lambda **kwargs: kwargs),
    )
    settings = _settings()
    key = settings.api_key if client_scope == "owner" else settings.canary_api_key
    constructor = inspect.signature(gate._PacedExternalApiClient).parameters
    kwargs: dict[str, Any] = {
        "settings": settings,
        "clock": lambda: 0.0,
        "sleeper": lambda _seconds: None,
    }
    if "api_key" in constructor:
        kwargs["api_key"] = key
    if "client_scope" in constructor:
        kwargs["client_scope"] = client_scope
    elif "scope" in constructor:
        kwargs["scope"] = client_scope
    client = gate._PacedExternalApiClient(**kwargs)
    return client, calls, key


def _request_kwargs(*, method: str, path: str, key: str) -> dict[str, Any]:
    headers = {"X-IDIS-API-Key": key}
    if method == "POST" and path == "/v1/deals":
        return {"headers": headers, "json": {"name": "deal-001", "company_name": "company-001"}}
    if method == "POST" and path.endswith("/documents/upload"):
        return {
            "headers": {**headers, "Content-Type": "application/octet-stream"},
            "params": {
                "filename": "source-0001.pdf",
                "doc_type": "DATA_ROOM_FILE",
                "source_system": "slice100-strict-source-preparation",
                "sha256": "a" * 64,
            },
            "content": b"fictional",
        }
    if method == "POST" and path.endswith("/data-room-packages"):
        return {"headers": headers, "json": {"document_ids": [_DOCUMENT_ID]}}
    return {"headers": headers}


def _invoke(
    client: Any,
    *,
    transport: str,
    method: str,
    path: str,
    key: str,
) -> None:
    kwargs = _request_kwargs(method=method, path=path, key=key)
    if transport == "stream":
        with client.stream(method, path, **kwargs):
            return
    if method == "GET":
        client.get(path, **kwargs)
    elif method == "POST":
        client.post(path, **kwargs)
    elif method == "DELETE":
        client.delete(path, **kwargs)
    else:  # pragma: no cover - the literal tables above contain only reviewed methods.
        raise AssertionError(f"unexpected test method: {method}")


@pytest.mark.parametrize(
    ("client_scope", "allowed"),
    (("owner", _OWNER_ALLOWED), ("canary", _CANARY_ALLOWED)),
)
def test_task4_client_scope_admits_only_its_literal_positive_census(
    monkeypatch: pytest.MonkeyPatch,
    client_scope: str,
    allowed: tuple[tuple[str, str, str], ...],
) -> None:
    client, calls, key = _build_client(monkeypatch, client_scope=client_scope)
    try:
        for transport, method, path in allowed:
            _invoke(
                client,
                transport=transport,
                method=method,
                path=path,
                key=key,
            )
    finally:
        client.close()

    assert calls == list(allowed)


@pytest.mark.parametrize(
    ("client_scope", "transport", "method", "path"),
    _DENIED_CALLS,
    ids=[
        "owner-metrics",
        "owner-audit",
        "owner-deal-list",
        "owner-run-create",
        "owner-canary-get",
        "owner-canary-delete",
        "owner-buffered-content",
        "owner-stream-post",
        "canary-health",
        "canary-readiness",
        "canary-deal-create",
        "canary-deal-read",
        "canary-buffered-content",
        "canary-stream-delete",
        "trailing-slash",
        "malformed-uuid",
        "encoded-separator",
    ],
)
def test_task4_client_scope_rejects_unapproved_calls_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    client_scope: str,
    transport: str,
    method: str,
    path: str,
) -> None:
    client, calls, key = _build_client(monkeypatch, client_scope=client_scope)
    try:
        with pytest.raises(gate.DurableIngestionGateError) as exc_info:
            _invoke(
                client,
                transport=transport,
                method=method,
                path=path,
                key=key,
            )
    finally:
        client.close()

    assert exc_info.value.code == "API_ACCEPTANCE_PATH_NONCANONICAL"
    assert calls == []
