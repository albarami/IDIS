"""RED contracts for Slice100's exact acceptance/canary/admin auth binding."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from types import MappingProxyType

import pytest

from tests.slice100_task2._support import (
    ACCEPTANCE_SECRET,
    CANARY_SECRET,
    GOVERNANCE_SECRET,
    acceptance_record,
    canary_record,
    governance_record,
    report_may_proceed,
    report_reason_codes,
    require_attr,
    require_module,
    safe_render,
    strict_auth_env,
)


def _validate(env: Mapping[str, str]) -> object:
    module = require_module("idis.runtime.strict_auth")
    validate = require_attr(module, "validate_strict_auth_binding")
    return validate(env)


def _registry(env: Mapping[str, str]) -> dict[str, object]:
    parsed = json.loads(env["IDIS_API_KEYS_JSON"])
    assert isinstance(parsed, dict)
    return parsed


def _with_record_field(
    env: dict[str, str], secret: str, field: str, value: object
) -> dict[str, str]:
    changed = copy.deepcopy(env)
    registry = _registry(changed)
    record = registry[secret]
    assert isinstance(record, dict)
    record[field] = value
    changed["IDIS_API_KEYS_JSON"] = json.dumps(registry, sort_keys=True)
    return changed


def _assert_blocked(env: Mapping[str, str], code: str) -> object:
    report = _validate(env)
    assert report_may_proceed(report) is False
    assert code in report_reason_codes(report)
    return report


def test_exact_three_key_binding_is_admitted_without_exposing_key_values() -> None:
    env = strict_auth_env()

    report = _validate(MappingProxyType(env))

    assert report_may_proceed(report) is True
    assert report_reason_codes(report) == []
    rendered = safe_render(report)
    for secret in (ACCEPTANCE_SECRET, CANARY_SECRET, GOVERNANCE_SECRET):
        assert secret not in rendered
    assert env["IDIS_API_KEYS_JSON"] not in rendered


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("tenant_id", "10000000-0000-4000-8000-000000000099"),
        ("actor_id", "wrong-acceptance-actor"),
        ("name", "Wrong Acceptance Name"),
        ("timezone", "UTC"),
        ("data_region", "wrong-region"),
        ("roles", ["ADMIN"]),
        ("roles", ["INTEGRATION_SERVICE", "ADMIN"]),
    ],
)
def test_acceptance_key_requires_every_exact_record_field(field: str, wrong_value: object) -> None:
    env = _with_record_field(strict_auth_env(), ACCEPTANCE_SECRET, field, wrong_value)
    _assert_blocked(env, "AUTH_ACCEPTANCE_KEY_MISMATCH")


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("tenant_id", "10000000-0000-4000-8000-000000000001"),
        ("actor_id", "wrong-canary-actor"),
        ("name", "Wrong Canary Name"),
        ("timezone", "UTC"),
        ("data_region", "wrong-region"),
        ("roles", ["ADMIN"]),
        ("roles", ["INTEGRATION_SERVICE", "ADMIN"]),
    ],
)
def test_canary_key_requires_exact_fields_and_a_distinct_tenant(
    field: str, wrong_value: object
) -> None:
    env = _with_record_field(strict_auth_env(), CANARY_SECRET, field, wrong_value)
    _assert_blocked(env, "AUTH_CANARY_KEY_MISMATCH")


def test_canary_tenant_cannot_alias_acceptance_tenant_by_uuid_case() -> None:
    env = strict_auth_env()
    acceptance_tenant = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    canary_tenant = acceptance_tenant.upper()
    env["IDIS_ACCEPTANCE_TENANT_ID"] = acceptance_tenant
    env["IDIS_ISOLATION_CANARY_TENANT_ID"] = canary_tenant
    registry = _registry(env)
    for secret in (ACCEPTANCE_SECRET, GOVERNANCE_SECRET):
        record = registry[secret]
        assert isinstance(record, dict)
        record["tenant_id"] = acceptance_tenant
    canary = registry[CANARY_SECRET]
    assert isinstance(canary, dict)
    canary["tenant_id"] = canary_tenant
    env["IDIS_API_KEYS_JSON"] = json.dumps(registry, sort_keys=True)

    _assert_blocked(env, "AUTH_CANARY_KEY_MISMATCH")


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("tenant_id", "10000000-0000-4000-8000-000000000002"),
        ("actor_id", "wrong-governance-actor"),
        ("name", "Wrong Governance Name"),
        ("timezone", "UTC"),
        ("data_region", "wrong-region"),
        ("roles", ["INTEGRATION_SERVICE"]),
        ("roles", ["ADMIN", "AUDITOR"]),
    ],
)
def test_governance_key_requires_the_exact_admin_binding(field: str, wrong_value: object) -> None:
    env = _with_record_field(strict_auth_env(), GOVERNANCE_SECRET, field, wrong_value)
    _assert_blocked(env, "GOVERNANCE_ADMIN_UNAUTHORIZED")


@pytest.mark.parametrize(
    ("env_name", "code"),
    [
        ("IDIS_REAL_EXAMPLE_API_KEY", "AUTH_ACCEPTANCE_KEY_MISMATCH"),
        ("IDIS_ISOLATION_CANARY_API_KEY", "AUTH_CANARY_KEY_MISMATCH"),
        ("IDIS_GOVERNANCE_ADMIN_API_KEY", "GOVERNANCE_ADMIN_UNAUTHORIZED"),
    ],
)
def test_each_named_key_must_be_present_and_bound(env_name: str, code: str) -> None:
    env = strict_auth_env()
    del env[env_name]
    _assert_blocked(env, code)


def test_named_key_values_must_be_distinct() -> None:
    env = strict_auth_env()
    env["IDIS_ISOLATION_CANARY_API_KEY"] = ACCEPTANCE_SECRET
    _assert_blocked(env, "AUTH_CANARY_KEY_MISMATCH")


def test_registry_rejects_an_unplanned_fourth_entry() -> None:
    env = strict_auth_env()
    registry = _registry(env)
    registry["slice100-unplanned-secret"] = acceptance_record()
    env["IDIS_API_KEYS_JSON"] = json.dumps(registry, sort_keys=True)
    _assert_blocked(env, "AUTH_CONFIG_BLOCKED")


def test_registry_rejects_duplicate_json_members_even_when_values_match() -> None:
    env = strict_auth_env()
    raw = (
        "{"
        + ",".join(
            [
                f"{json.dumps(ACCEPTANCE_SECRET)}:{json.dumps(acceptance_record())}",
                f"{json.dumps(CANARY_SECRET)}:{json.dumps(canary_record())}",
                f"{json.dumps(GOVERNANCE_SECRET)}:{json.dumps(governance_record())}",
                f"{json.dumps(ACCEPTANCE_SECRET)}:{json.dumps(acceptance_record())}",
            ]
        )
        + "}"
    )
    env["IDIS_API_KEYS_JSON"] = raw
    _assert_blocked(env, "AUTH_CONFIG_BLOCKED")


def test_strict_registry_record_rejects_unknown_fields() -> None:
    env = _with_record_field(
        strict_auth_env(), ACCEPTANCE_SECRET, "unreviewed_field", "not-admitted"
    )
    _assert_blocked(env, "AUTH_CONFIG_BLOCKED")


@pytest.mark.parametrize("raw", ["", "not-json", "[]", "{}"])
def test_registry_must_be_a_valid_exact_nonempty_object(raw: str) -> None:
    env = strict_auth_env()
    env["IDIS_API_KEYS_JSON"] = raw
    _assert_blocked(env, "AUTH_CONFIG_BLOCKED")


@pytest.mark.parametrize("value", [None, "", "true", "1", "0", "FALSE "])
def test_dev_auth_bypass_must_be_literal_false(value: str | None) -> None:
    env = strict_auth_env()
    if value is None:
        del env["IDIS_UI_DEV_AUTH_BYPASS"]
    else:
        env["IDIS_UI_DEV_AUTH_BYPASS"] = value
    _assert_blocked(env, "AUTH_CONFIG_BLOCKED")


def test_dev_api_key_is_prohibited_even_when_acceptance_binding_is_valid() -> None:
    env = strict_auth_env()
    env["IDIS_DEV_API_KEY"] = "slice100-dev-secret-value"
    _assert_blocked(env, "AUTH_CONFIG_BLOCKED")


def test_service_region_is_required() -> None:
    env = strict_auth_env()
    del env["IDIS_SERVICE_REGION"]
    _assert_blocked(env, "RESIDENCY_SERVICE_REGION_UNSET")


def test_validation_does_not_mutate_the_supplied_mapping() -> None:
    env = strict_auth_env()
    before = copy.deepcopy(env)
    _validate(MappingProxyType(env))
    assert env == before


def test_blocked_report_and_logs_never_contain_auth_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = _with_record_field(
        strict_auth_env(), ACCEPTANCE_SECRET, "actor_id", "wrong-acceptance-actor"
    )

    report = _assert_blocked(env, "AUTH_ACCEPTANCE_KEY_MISMATCH")

    emitted = safe_render(report) + "\n" + caplog.text
    for secret in (ACCEPTANCE_SECRET, CANARY_SECRET, GOVERNANCE_SECRET):
        assert secret not in emitted
    assert env["IDIS_API_KEYS_JSON"] not in emitted
