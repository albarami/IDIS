"""One-attempt, size-bounded HTTPS transport for provider probe operations."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx

from idis.services.enrichment.task9.probe_contracts import (
    PROBE_MAX_RESPONSE_BYTES,
    PROBE_TIMEOUT_SECONDS,
)

_REDIRECT_STATUS_CODES: Final = frozenset({301, 302, 303, 307, 308})


class ProbeTransportFailureReason(StrEnum):
    """Safe reason categories that contain no remote or secret-controlled text."""

    HTTPS_REQUIRED = "https_required"
    HOST_NOT_APPROVED = "host_not_approved"
    REDIRECT_LOCATION_MISSING = "redirect_location_missing"
    REDIRECT_LOCATION_INVALID = "redirect_location_invalid"
    REDIRECT_HOST_BLOCKED = "redirect_host_blocked"
    REDIRECT_LIMIT_EXCEEDED = "redirect_limit_exceeded"
    HTTP_STATUS_BLOCKED = "http_status_blocked"
    CONTENT_TYPE_BLOCKED = "content_type_blocked"
    RESPONSE_TOO_LARGE = "response_too_large"
    TIMEOUT = "timeout"
    CONNECT_OR_TLS_ERROR = "connect_or_tls_error"
    REQUEST_BUILD_INVALID = "request_build_invalid"
    TRANSPORT_ERROR = "transport_error"


class ProbeTransportFailure(RuntimeError):
    """Typed, secret-safe transport denial."""

    code = "ENRICHMENT_PREFLIGHT_BLOCKED"

    def __init__(self, reason: ProbeTransportFailureReason) -> None:
        self.reason = reason
        super().__init__(f"{self.code}:{reason.value}")


@dataclass(frozen=True, slots=True)
class BoundedProbeResponse:
    """Bounded bytes and safe response metadata returned for normalization."""

    status_code: int
    media_type: str
    body: bytes
    response_sha256: str
    redirect_count: int


@dataclass(frozen=True, slots=True)
class ProbeDeadline:
    """Opaque monotonic deadline shared by every operation in one provider probe."""

    expires_at_monotonic: float


class BoundedProbeTransport:
    """Execute one operation with no retry and at most one same-host redirect."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._monotonic = monotonic

    def start_deadline(self) -> ProbeDeadline:
        """Create one immutable 15-second deadline for a complete provider probe."""

        return ProbeDeadline(self._monotonic_now() + PROBE_TIMEOUT_SECONDS)

    def execute(
        self,
        *,
        method: str,
        url: str,
        approved_host: str,
        expected_media_types: tuple[str, ...],
        headers: httpx.Headers | dict[str, str] | None = None,
        content: bytes | None = None,
        deadline: ProbeDeadline | None = None,
    ) -> BoundedProbeResponse:
        """Execute a bounded operation; exception details and URLs never enter failures."""

        normalized_host = self._validated_approved_host(approved_host)
        self._validate_target(url, normalized_host, redirect=False)
        if (
            not isinstance(method, str)
            or not method
            or method != method.strip()
            or not isinstance(expected_media_types, tuple)
            or not expected_media_types
            or any(
                not isinstance(media_type, str)
                or not media_type
                or media_type != media_type.strip().lower()
                for media_type in expected_media_types
            )
        ):
            raise ProbeTransportFailure(ProbeTransportFailureReason.REQUEST_BUILD_INVALID)
        if deadline is None:
            expires_at = self._monotonic_now() + PROBE_TIMEOUT_SECONDS
            request_timeout = float(PROBE_TIMEOUT_SECONDS)
        else:
            if not isinstance(deadline, ProbeDeadline):
                raise ProbeTransportFailure(ProbeTransportFailureReason.REQUEST_BUILD_INVALID)
            expires_at = deadline.expires_at_monotonic
            request_timeout = self._remaining_seconds(expires_at)
        try:
            request = self._client.build_request(
                method,
                url,
                headers=headers,
                content=content,
                timeout=request_timeout,
            )
        except Exception:
            raise ProbeTransportFailure(ProbeTransportFailureReason.REQUEST_BUILD_INVALID) from None
        redirect_count = 0

        while True:
            response = self._send_once(request)
            try:
                remaining_seconds = self._remaining_seconds(expires_at)
            except ProbeTransportFailure:
                response.close()
                raise
            if response.status_code not in _REDIRECT_STATUS_CODES:
                break
            if redirect_count == 1:
                response.close()
                raise ProbeTransportFailure(ProbeTransportFailureReason.REDIRECT_LIMIT_EXCEEDED)
            location = response.headers.get("location")
            response.close()
            if not location:
                raise ProbeTransportFailure(ProbeTransportFailureReason.REDIRECT_LOCATION_MISSING)
            try:
                redirected_url = urljoin(str(request.url), location)
                self._validate_target(redirected_url, normalized_host, redirect=True)
                request = self._client.build_request(
                    method,
                    redirected_url,
                    headers=headers,
                    content=content,
                    timeout=remaining_seconds,
                )
            except ProbeTransportFailure:
                raise
            except Exception:
                raise ProbeTransportFailure(
                    ProbeTransportFailureReason.REDIRECT_LOCATION_INVALID
                ) from None
            redirect_count += 1

        try:
            if not 200 <= response.status_code < 300:
                raise ProbeTransportFailure(ProbeTransportFailureReason.HTTP_STATUS_BLOCKED)
            media_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
            if media_type not in expected_media_types:
                raise ProbeTransportFailure(ProbeTransportFailureReason.CONTENT_TYPE_BLOCKED)
            content_length = response.headers.get("content-length")
            if content_length is not None and _declared_size_exceeds_bound(content_length):
                raise ProbeTransportFailure(ProbeTransportFailureReason.RESPONSE_TOO_LARGE)
            body = self._read_bounded(response, deadline=expires_at)
            return BoundedProbeResponse(
                status_code=response.status_code,
                media_type=media_type,
                body=body,
                response_sha256=hashlib.sha256(body).hexdigest(),
                redirect_count=redirect_count,
            )
        finally:
            response.close()

    def _send_once(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._client.send(request, stream=True, follow_redirects=False)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
            raise ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT) from None
        except httpx.ConnectError:
            raise ProbeTransportFailure(ProbeTransportFailureReason.CONNECT_OR_TLS_ERROR) from None
        except httpx.RemoteProtocolError as exc:
            # HTTPX parses Location even with follow_redirects=False.  Preserve its
            # typed InvalidURL signal without copying remote-controlled details.
            reason = (
                ProbeTransportFailureReason.REDIRECT_LOCATION_INVALID
                if isinstance(exc.__context__, httpx.InvalidURL)
                else ProbeTransportFailureReason.TRANSPORT_ERROR
            )
            raise ProbeTransportFailure(reason) from None
        except httpx.HTTPError:
            raise ProbeTransportFailure(ProbeTransportFailureReason.TRANSPORT_ERROR) from None
        except Exception:
            raise ProbeTransportFailure(ProbeTransportFailureReason.TRANSPORT_ERROR) from None

    @staticmethod
    def _validate_target(url: str, approved_host: str, *, redirect: bool) -> None:
        if not isinstance(url, str):
            reason = (
                ProbeTransportFailureReason.REDIRECT_LOCATION_INVALID
                if redirect
                else ProbeTransportFailureReason.HOST_NOT_APPROVED
            )
            raise ProbeTransportFailure(reason)
        try:
            parsed = urlsplit(url)
            _validated_port = parsed.port
        except (TypeError, UnicodeError, ValueError):
            reason = (
                ProbeTransportFailureReason.REDIRECT_LOCATION_INVALID
                if redirect
                else ProbeTransportFailureReason.HOST_NOT_APPROVED
            )
            raise ProbeTransportFailure(reason) from None
        if parsed.scheme.lower() != "https":
            raise ProbeTransportFailure(ProbeTransportFailureReason.HTTPS_REQUIRED)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host != approved_host or parsed.username is not None or parsed.password is not None:
            reason = (
                ProbeTransportFailureReason.REDIRECT_HOST_BLOCKED
                if redirect
                else ProbeTransportFailureReason.HOST_NOT_APPROVED
            )
            raise ProbeTransportFailure(reason)

    @staticmethod
    def _validated_approved_host(approved_host: object) -> str:
        if (
            not isinstance(approved_host, str)
            or not approved_host
            or approved_host != approved_host.strip()
            or any(character.isspace() or ord(character) < 32 for character in approved_host)
            or any(character in approved_host for character in "/:@[]")
        ):
            raise ProbeTransportFailure(ProbeTransportFailureReason.HOST_NOT_APPROVED)
        normalized = approved_host.lower().rstrip(".")
        if not normalized or ".." in normalized:
            raise ProbeTransportFailure(ProbeTransportFailureReason.HOST_NOT_APPROVED)
        return normalized

    def _read_bounded(self, response: httpx.Response, *, deadline: float) -> bytes:
        chunks: list[bytes] = []
        byte_count = 0
        try:
            for chunk in response.iter_bytes():
                self._remaining_seconds(deadline)
                byte_count += len(chunk)
                if byte_count > PROBE_MAX_RESPONSE_BYTES:
                    raise ProbeTransportFailure(ProbeTransportFailureReason.RESPONSE_TOO_LARGE)
                chunks.append(chunk)
            self._remaining_seconds(deadline)
        except ProbeTransportFailure:
            raise
        except httpx.TimeoutException:
            raise ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProbeTransportFailure(ProbeTransportFailureReason.TRANSPORT_ERROR) from None
        except Exception:
            raise ProbeTransportFailure(ProbeTransportFailureReason.TRANSPORT_ERROR) from None
        return b"".join(chunks)

    def ensure_before_deadline(self, deadline: ProbeDeadline) -> None:
        """Fail closed when connector normalization consumed the shared deadline."""

        if not isinstance(deadline, ProbeDeadline):
            raise ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT)
        self._remaining_seconds(deadline.expires_at_monotonic)

    def _remaining_seconds(self, deadline: float) -> float:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise ProbeTransportFailure(ProbeTransportFailureReason.REQUEST_BUILD_INVALID)
        remaining = deadline - self._monotonic_now()
        if remaining <= 0:
            raise ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT)
        return remaining

    def _monotonic_now(self) -> float:
        try:
            value = self._monotonic()
        except Exception:
            raise ProbeTransportFailure(ProbeTransportFailureReason.REQUEST_BUILD_INVALID) from None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ProbeTransportFailure(ProbeTransportFailureReason.REQUEST_BUILD_INVALID)
        return float(value)


def _declared_size_exceeds_bound(raw_content_length: str) -> bool:
    try:
        declared = int(raw_content_length)
    except ValueError:
        return True
    return declared < 0 or declared > PROBE_MAX_RESPONSE_BYTES
