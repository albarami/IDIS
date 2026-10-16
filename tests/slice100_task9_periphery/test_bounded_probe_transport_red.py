"""RED-first tests for the injected, bounded provider probe HTTP transport."""

from __future__ import annotations

import math
from collections.abc import Iterator

import httpx
import pytest

from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeTransport,
    ProbeDeadline,
    ProbeTransportFailure,
    ProbeTransportFailureReason,
)
from idis.services.enrichment.task9.probe_contracts import PROBE_MAX_RESPONSE_BYTES


class _ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


class _UnexpectedFailureStream(httpx.SyncByteStream):
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def __iter__(self) -> Iterator[bytes]:
        raise RuntimeError(self._marker)
        yield b"unreachable"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, follow_redirects=True)


def test_success_uses_one_streamed_attempt_with_pinned_timeout_and_redirects_off() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_ChunkStream((b'{"id":', b"1}")),
        )

    with _client(httpx.MockTransport(handler)) as client:
        result = BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/v1/item",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )

    assert result.body == b'{"id":1}'
    assert result.status_code == 200
    assert result.redirect_count == 0
    assert len(calls) == 1
    assert calls[0].extensions["timeout"] == {
        "connect": 15.0,
        "read": 15.0,
        "write": 15.0,
        "pool": 15.0,
    }


@pytest.mark.parametrize(
    ("url", "approved_host", "reason"),
    [
        ("http://probe.example/v1", "probe.example", ProbeTransportFailureReason.HTTPS_REQUIRED),
        (
            "https://unapproved.example/v1",
            "probe.example",
            ProbeTransportFailureReason.HOST_NOT_APPROVED,
        ),
    ],
)
def test_invalid_initial_target_fails_before_transport(
    url: str,
    approved_host: str,
    reason: ProbeTransportFailureReason,
) -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200)

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url=url,
            approved_host=approved_host,
            expected_media_types=("application/json",),
        )
    assert exc_info.value.reason is reason
    assert call_count == 0


def test_one_same_host_redirect_is_followed_manually() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok":true}',
        )

    with _client(httpx.MockTransport(handler)) as client:
        result = BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/start",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    assert paths == ["/start", "/final"]
    assert result.redirect_count == 1


def test_redirect_cannot_reset_the_single_operation_deadline() -> None:
    calls = 0
    clock_values = iter((100.0, 115.1))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "/final"})

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client, monotonic=lambda: next(clock_values)).execute(
            method="GET",
            url="https://probe.example/start",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    assert exc_info.value.reason is ProbeTransportFailureReason.TIMEOUT
    assert calls == 1


def test_deadline_expiry_at_response_eof_cannot_return_success() -> None:
    clock_values = iter((0.0, 1.0, 2.0, 16.0))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_ChunkStream((b'{"ok":true}',)),
        )

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client, monotonic=lambda: next(clock_values)).execute(
            method="GET",
            url="https://probe.example/value",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )

    assert exc_info.value.reason is ProbeTransportFailureReason.TIMEOUT


def test_initial_request_build_error_is_typed_and_secret_safe() -> None:
    marker = "credential-marker-never-report"
    invalid_header = f"{marker}-\udcff"

    with (
        _client(httpx.MockTransport(lambda request: httpx.Response(200))) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/value",
            approved_host="probe.example",
            expected_media_types=("application/json",),
            headers={"authorization": invalid_header},
        )

    assert exc_info.value.reason is ProbeTransportFailureReason.REQUEST_BUILD_INVALID
    assert marker not in str(exc_info.value)


@pytest.mark.parametrize(
    ("locations", "reason"),
    [
        (("https://other.example/final",), ProbeTransportFailureReason.REDIRECT_HOST_BLOCKED),
        (("/again", "/third"), ProbeTransportFailureReason.REDIRECT_LIMIT_EXCEEDED),
        (("http://probe.example/final",), ProbeTransportFailureReason.HTTPS_REQUIRED),
    ],
)
def test_unsafe_or_repeated_redirect_fails_closed(
    locations: tuple[str, ...],
    reason: ProbeTransportFailureReason,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        location = locations[min(calls, len(locations) - 1)]
        calls += 1
        return httpx.Response(302, headers={"location": location})

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/start",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    assert exc_info.value.reason is reason


@pytest.mark.parametrize(
    "location",
    [
        "https://[credential-marker",
        "https://probe.example:credential-marker/final",
    ],
)
def test_malformed_remote_redirect_location_is_typed_and_secret_safe(location: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": location})

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/start",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    assert exc_info.value.reason is ProbeTransportFailureReason.REDIRECT_LOCATION_INVALID
    assert "credential-marker" not in str(exc_info.value)
    assert calls == 1


def test_streaming_stops_at_overflow_by_one() -> None:
    chunks = (b"a" * PROBE_MAX_RESPONSE_BYTES, b"b")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_ChunkStream(chunks),
        )

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/large",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    assert exc_info.value.reason is ProbeTransportFailureReason.RESPONSE_TOO_LARGE


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            httpx.Response(503, headers={"content-type": "application/json"}),
            ProbeTransportFailureReason.HTTP_STATUS_BLOCKED,
        ),
        (
            httpx.Response(200, headers={"content-type": "text/plain"}, content=b"secret-body"),
            ProbeTransportFailureReason.CONTENT_TYPE_BLOCKED,
        ),
    ],
)
def test_http_and_content_type_failures_are_typed_and_secret_safe(
    response: httpx.Response,
    reason: ProbeTransportFailureReason,
) -> None:
    marker = "credential-marker-never-report"

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url=f"https://probe.example/v1?api_key={marker}",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    rendered = str(exc_info.value)
    assert exc_info.value.code == "ENRICHMENT_PREFLIGHT_BLOCKED"
    assert exc_info.value.reason is reason
    assert marker not in rendered
    assert "secret-body" not in rendered


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout("credential-marker"), ProbeTransportFailureReason.TIMEOUT),
        (httpx.ConnectError("credential-marker"), ProbeTransportFailureReason.CONNECT_OR_TLS_ERROR),
    ],
)
def test_network_exception_details_never_enter_failure(
    error: httpx.HTTPError,
    reason: ProbeTransportFailureReason,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/v1",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )
    assert exc_info.value.reason is reason
    assert "credential-marker" not in str(exc_info.value)


def test_unexpected_send_exception_is_wrapped_by_common_secret_safe_contract() -> None:
    marker = "credential-marker-never-report"

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(marker)

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/v1",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )

    assert exc_info.value.reason is ProbeTransportFailureReason.TRANSPORT_ERROR
    assert marker not in str(exc_info.value)


def test_unexpected_stream_exception_is_wrapped_by_common_secret_safe_contract() -> None:
    marker = "credential-marker-never-report"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_UnexpectedFailureStream(marker),
        )

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client).execute(
            method="GET",
            url="https://probe.example/v1",
            approved_host="probe.example",
            expected_media_types=("application/json",),
        )

    assert exc_info.value.reason is ProbeTransportFailureReason.TRANSPORT_ERROR
    assert marker not in str(exc_info.value)


class _SecretMarkerValue:
    def __str__(self) -> str:
        return "credential-marker-never-report"

    def lower(self) -> str:
        raise RuntimeError("credential-marker-never-report")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"url": _SecretMarkerValue()}, ProbeTransportFailureReason.HOST_NOT_APPROVED),
        ({"approved_host": _SecretMarkerValue()}, ProbeTransportFailureReason.HOST_NOT_APPROVED),
        ({"method": _SecretMarkerValue()}, ProbeTransportFailureReason.REQUEST_BUILD_INVALID),
        (
            {"expected_media_types": _SecretMarkerValue()},
            ProbeTransportFailureReason.REQUEST_BUILD_INVALID,
        ),
        ({"deadline": _SecretMarkerValue()}, ProbeTransportFailureReason.REQUEST_BUILD_INVALID),
        (
            {"deadline": ProbeDeadline("credential-marker-never-report")},
            ProbeTransportFailureReason.REQUEST_BUILD_INVALID,
        ),
        ({"deadline": ProbeDeadline(math.nan)}, ProbeTransportFailureReason.REQUEST_BUILD_INVALID),
        ({"approved_host": " probe.example"}, ProbeTransportFailureReason.HOST_NOT_APPROVED),
    ],
)
def test_malformed_prevalidation_types_fail_with_stable_secret_safe_reasons(
    overrides: dict[str, object],
    reason: ProbeTransportFailureReason,
) -> None:
    kwargs: dict[str, object] = {
        "method": "GET",
        "url": "https://probe.example/v1",
        "approved_host": "probe.example",
        "expected_media_types": ("application/json",),
        "deadline": ProbeDeadline(15.0),
    }
    kwargs.update(overrides)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "application/json"}, json={})

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeTransportFailure) as exc_info,
    ):
        BoundedProbeTransport(client, monotonic=lambda: 0.0).execute(  # type: ignore[arg-type]
            **kwargs
        )

    assert exc_info.value.reason is reason
    assert "credential-marker" not in str(exc_info.value)
    assert calls == 0
