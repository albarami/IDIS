"""Canonical method/path policy for the Task-4 durable-ingestion API scope.

The bootstrap API and its external gate share this standard-library-only policy.
Server composition consumes :data:`TASK4_SERVER_ROUTE_INVENTORY`; the gate additionally
binds each call to the owner or isolation-canary client and to buffered versus
streaming transport.  Dynamic identifiers must be canonical lowercase UUID strings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

TASK4_INGESTION_SCOPE = "task4-ingestion-v1"
TASK4_OWNER_SCOPE = "owner"
TASK4_CANARY_SCOPE = "canary"
TASK4_BUFFERED_TRANSPORT = "buffered"
TASK4_STREAM_TRANSPORT = "stream"

_HEALTH = "/health"
_READINESS = "/v1/strict-readiness"
_DEALS = "/v1/deals"
_DEAL = "/v1/deals/{deal_id}"
_UPLOAD = "/v1/deals/{deal_id}/documents/upload"
_DEAL_DOCUMENT = "/v1/deals/{deal_id}/documents/{document_id}"
_PACKAGES = "/v1/deals/{deal_id}/data-room-packages"
_PACKAGE = "/v1/deals/{deal_id}/data-room-packages/{package_id}"
_INTEGRITY = "/v1/documents/{doc_id}/content-integrity"
_SPANS = "/v1/documents/{doc_id}/spans"
_CONTENT = "/v1/documents/{doc_id}/content"
_DOCUMENT = "/v1/documents/{doc_id}"

# This is the complete server surface.  Keep it as explicit method/template pairs:
# omitting the canary-only document GET/DELETE routes would turn their expected 404s
# into router misses rather than real tenant-isolation denials.
TASK4_SERVER_ROUTE_INVENTORY = frozenset(
    {
        ("GET", _HEALTH),
        ("GET", _READINESS),
        ("POST", _DEALS),
        ("GET", _DEAL),
        ("POST", _UPLOAD),
        ("GET", _DEAL_DOCUMENT),
        ("POST", _PACKAGES),
        ("GET", _PACKAGE),
        ("GET", _INTEGRITY),
        ("GET", _SPANS),
        ("GET", _CONTENT),
        ("GET", _DOCUMENT),
        ("DELETE", _DOCUMENT),
    }
)

TASK4_OWNER_CLIENT_CALLS = frozenset(
    {
        (TASK4_BUFFERED_TRANSPORT, "GET", _HEALTH),
        (TASK4_BUFFERED_TRANSPORT, "GET", _READINESS),
        (TASK4_BUFFERED_TRANSPORT, "POST", _DEALS),
        (TASK4_BUFFERED_TRANSPORT, "GET", _DEAL),
        (TASK4_BUFFERED_TRANSPORT, "POST", _UPLOAD),
        (TASK4_BUFFERED_TRANSPORT, "GET", _DEAL_DOCUMENT),
        (TASK4_BUFFERED_TRANSPORT, "POST", _PACKAGES),
        (TASK4_BUFFERED_TRANSPORT, "GET", _PACKAGE),
        (TASK4_BUFFERED_TRANSPORT, "GET", _INTEGRITY),
        (TASK4_BUFFERED_TRANSPORT, "GET", _SPANS),
        (TASK4_STREAM_TRANSPORT, "GET", _CONTENT),
    }
)

TASK4_CANARY_CLIENT_CALLS = frozenset(
    {
        (TASK4_BUFFERED_TRANSPORT, "GET", _PACKAGE),
        (TASK4_BUFFERED_TRANSPORT, "GET", _DEAL_DOCUMENT),
        (TASK4_BUFFERED_TRANSPORT, "GET", _INTEGRITY),
        (TASK4_BUFFERED_TRANSPORT, "GET", _SPANS),
        (TASK4_BUFFERED_TRANSPORT, "GET", _DOCUMENT),
        (TASK4_STREAM_TRANSPORT, "GET", _CONTENT),
        (TASK4_BUFFERED_TRANSPORT, "POST", _PACKAGES),
        (TASK4_BUFFERED_TRANSPORT, "POST", _UPLOAD),
        (TASK4_BUFFERED_TRANSPORT, "DELETE", _DOCUMENT),
    }
)

_CLIENT_CALLS: Mapping[str, frozenset[tuple[str, str, str]]] = {
    TASK4_OWNER_SCOPE: TASK4_OWNER_CLIENT_CALLS,
    TASK4_CANARY_SCOPE: TASK4_CANARY_CLIENT_CALLS,
}
_CANONICAL_UUID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)


def _compile_template(template: str) -> re.Pattern[str]:
    segments: list[str] = []
    for segment in template.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            segments.append(f"(?:{_CANONICAL_UUID_PATTERN})")
        else:
            segments.append(re.escape(segment))
    return re.compile("^" + "/".join(segments) + "$")


_CLIENT_MATCHERS = {
    scope: tuple(
        (transport, method, _compile_template(template))
        for transport, method, template in sorted(calls)
    )
    for scope, calls in _CLIENT_CALLS.items()
}
_SERVER_MATCHERS = tuple(
    (method, _compile_template(template))
    for method, template in sorted(TASK4_SERVER_ROUTE_INVENTORY)
)
_OWNER_UPLOAD_PATTERN = _compile_template(_UPLOAD)


def _validate_static_contract() -> None:
    if len(TASK4_SERVER_ROUTE_INVENTORY) != 13:
        raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
    for calls in _CLIENT_CALLS.values():
        for transport, method, template in calls:
            if (method, template) not in TASK4_SERVER_ROUTE_INVENTORY:
                raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
            expected_transport = (
                TASK4_STREAM_TRANSPORT if template == _CONTENT else TASK4_BUFFERED_TRANSPORT
            )
            if transport != expected_transport:
                raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
    client_route_union = {
        (method, template)
        for calls in _CLIENT_CALLS.values()
        for _transport, method, template in calls
    }
    if client_route_union != TASK4_SERVER_ROUTE_INVENTORY:
        raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")


_validate_static_contract()


def task4_client_call_allowed(
    *,
    client_scope: str,
    transport: str,
    method: str,
    path: str,
) -> bool:
    """Return whether one raw call is in the exact client-specific contract."""
    if (
        client_scope not in _CLIENT_MATCHERS
        or transport not in {TASK4_BUFFERED_TRANSPORT, TASK4_STREAM_TRANSPORT}
        or method not in {"GET", "POST", "DELETE"}
        or not isinstance(path, str)
        or not path.startswith("/")
    ):
        return False
    return any(
        admitted_transport == transport
        and admitted_method == method
        and pattern.fullmatch(path) is not None
        for admitted_transport, admitted_method, pattern in _CLIENT_MATCHERS[client_scope]
    )


def task4_server_call_allowed(*, method: str, path: str) -> bool:
    """Return whether one decoded HTTP method/path is in the server inventory."""

    if method not in {"GET", "POST", "DELETE"} or not isinstance(path, str):
        return False
    return any(
        admitted_method == method and pattern.fullmatch(path) is not None
        for admitted_method, pattern in _SERVER_MATCHERS
    )


def is_task4_owner_upload_call(
    *,
    client_scope: str,
    transport: str,
    method: str,
    path: str,
) -> bool:
    """Return true only for the owner's exact buffered document-upload POST."""
    return (
        client_scope == TASK4_OWNER_SCOPE
        and transport == TASK4_BUFFERED_TRANSPORT
        and method == "POST"
        and _OWNER_UPLOAD_PATTERN.fullmatch(path) is not None
    )


__all__ = [
    "TASK4_SERVER_ROUTE_INVENTORY",
    "TASK4_BUFFERED_TRANSPORT",
    "TASK4_CANARY_CLIENT_CALLS",
    "TASK4_CANARY_SCOPE",
    "TASK4_INGESTION_SCOPE",
    "TASK4_OWNER_CLIENT_CALLS",
    "TASK4_OWNER_SCOPE",
    "TASK4_STREAM_TRANSPORT",
    "is_task4_owner_upload_call",
    "task4_client_call_allowed",
    "task4_server_call_allowed",
]
