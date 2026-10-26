"""Exact, secret-safe authentication binding for Slice100 strict admission."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

AUTH_CONFIG_BLOCKED = "AUTH_CONFIG_BLOCKED"
AUTH_ACCEPTANCE_KEY_MISMATCH = "AUTH_ACCEPTANCE_KEY_MISMATCH"
AUTH_CANARY_KEY_MISMATCH = "AUTH_CANARY_KEY_MISMATCH"
GOVERNANCE_ADMIN_UNAUTHORIZED = "GOVERNANCE_ADMIN_UNAUTHORIZED"
RESIDENCY_SERVICE_REGION_UNSET = "RESIDENCY_SERVICE_REGION_UNSET"

_API_KEY_RECORD_FIELDS = frozenset(
    {"tenant_id", "actor_id", "name", "timezone", "data_region", "roles"}
)


@dataclass(frozen=True, slots=True)
class StrictAuthBindingReport:
    """Secret-free result of validating the three reviewed API-key bindings."""

    may_proceed: bool
    reason_codes: tuple[str, ...]
    configured_entry_count: int


class _InvalidRegistry(ValueError):
    """Internal marker for duplicate or otherwise invalid JSON."""


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidRegistry("duplicate JSON member")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise _InvalidRegistry("non-finite JSON number")


def _parse_registry(raw: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, _InvalidRegistry, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _valid_uuid(value: str) -> bool:
    try:
        # Strict auth identifiers must use one canonical lowercase spelling so
        # case aliases cannot make one PostgreSQL tenant appear to be two.
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _record_matches(actual: object, expected: Mapping[str, object]) -> bool:
    if not isinstance(actual, dict) or frozenset(actual) != _API_KEY_RECORD_FIELDS:
        return False
    return actual == dict(expected)


def _append_code(codes: list[str], code: str) -> None:
    if code not in codes:
        codes.append(code)


def validate_strict_auth_binding(env: Mapping[str, str]) -> StrictAuthBindingReport:
    """Validate the exact acceptance, canary, and governance-admin key records.

    The supplied mapping is read only. Neither key values nor registry content are
    retained in the returned report or emitted to logs.
    """

    codes: list[str] = []
    service_region = str(env.get("IDIS_SERVICE_REGION", ""))
    if not service_region:
        _append_code(codes, RESIDENCY_SERVICE_REGION_UNSET)

    if env.get("IDIS_UI_DEV_AUTH_BYPASS") != "false" or "IDIS_DEV_API_KEY" in env:
        _append_code(codes, AUTH_CONFIG_BLOCKED)

    acceptance_tenant = str(env.get("IDIS_ACCEPTANCE_TENANT_ID", ""))
    canary_tenant = str(env.get("IDIS_ISOLATION_CANARY_TENANT_ID", ""))
    if not _valid_uuid(acceptance_tenant):
        _append_code(codes, AUTH_ACCEPTANCE_KEY_MISMATCH)
    if not _valid_uuid(canary_tenant) or canary_tenant == acceptance_tenant:
        _append_code(codes, AUTH_CANARY_KEY_MISMATCH)

    raw_registry = str(env.get("IDIS_API_KEYS_JSON", ""))
    registry = _parse_registry(raw_registry) if raw_registry else None
    configured_entry_count = len(registry) if registry is not None else 0
    if registry is None or len(registry) != 3:
        _append_code(codes, AUTH_CONFIG_BLOCKED)

    acceptance_secret = str(env.get("IDIS_REAL_EXAMPLE_API_KEY", ""))
    canary_secret = str(env.get("IDIS_ISOLATION_CANARY_API_KEY", ""))
    governance_secret = str(env.get("IDIS_GOVERNANCE_ADMIN_API_KEY", ""))

    if not acceptance_secret:
        _append_code(codes, AUTH_ACCEPTANCE_KEY_MISMATCH)
    if not canary_secret or canary_secret == acceptance_secret:
        _append_code(codes, AUTH_CANARY_KEY_MISMATCH)
    if not governance_secret or governance_secret in {acceptance_secret, canary_secret}:
        _append_code(codes, GOVERNANCE_ADMIN_UNAUTHORIZED)

    if registry is not None:
        expected_acceptance: dict[str, object] = {
            "tenant_id": acceptance_tenant,
            "actor_id": "slice100-acceptance-service",
            "name": "Slice100 Acceptance Service",
            "timezone": "Asia/Qatar",
            "data_region": service_region,
            "roles": ["INTEGRATION_SERVICE"],
        }
        expected_canary: dict[str, object] = {
            "tenant_id": canary_tenant,
            "actor_id": "slice100-isolation-canary",
            "name": "Slice100 Isolation Canary",
            "timezone": "Asia/Qatar",
            "data_region": service_region,
            "roles": ["INTEGRATION_SERVICE"],
        }
        expected_governance: dict[str, object] = {
            "tenant_id": acceptance_tenant,
            "actor_id": "slice100-governance-admin",
            "name": "Slice100 Governance Admin",
            "timezone": "Asia/Qatar",
            "data_region": service_region,
            "roles": ["ADMIN"],
        }
        if not _record_matches(registry.get(acceptance_secret), expected_acceptance):
            _append_code(codes, AUTH_ACCEPTANCE_KEY_MISMATCH)
        if not _record_matches(registry.get(canary_secret), expected_canary):
            _append_code(codes, AUTH_CANARY_KEY_MISMATCH)
        if not _record_matches(registry.get(governance_secret), expected_governance):
            _append_code(codes, GOVERNANCE_ADMIN_UNAUTHORIZED)

        if any(
            not isinstance(record, dict) or frozenset(record) != _API_KEY_RECORD_FIELDS
            for record in registry.values()
        ):
            _append_code(codes, AUTH_CONFIG_BLOCKED)

    return StrictAuthBindingReport(
        may_proceed=not codes,
        reason_codes=tuple(codes),
        configured_entry_count=configured_entry_count,
    )


__all__ = [
    "AUTH_ACCEPTANCE_KEY_MISMATCH",
    "AUTH_CANARY_KEY_MISMATCH",
    "AUTH_CONFIG_BLOCKED",
    "GOVERNANCE_ADMIN_UNAUTHORIZED",
    "RESIDENCY_SERVICE_REGION_UNSET",
    "StrictAuthBindingReport",
    "validate_strict_auth_binding",
]
