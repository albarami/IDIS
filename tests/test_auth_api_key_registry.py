"""Focused unit tests for `_load_api_key_registry` diagnostics and `.env.example` shape.

Sprint 1 Wave 1, Task 1: proves that
  * the exact `.env.example` IDIS_API_KEYS_JSON value loads into a non-empty
    registry and resolves to a full TenantContext,
  * malformed entries (non-dict values, shape mismatches) no longer disappear
    silently — a warning is emitted and the entry is dropped.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import (
    IDIS_API_KEYS_ENV,
    ApiKeyRecord,
    _constant_time_lookup,
    _load_api_key_registry,
    _normalize_roles,
)
from idis.api.main import create_app


ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / ".env.example"


def _extract_env_example_api_keys_json() -> str:
    """Return the IDIS_API_KEYS_JSON value verbatim from .env.example.

    The value is a single line (`KEY=<json>`), so this is a straightforward
    line-prefix match. Failing this returns a skip-triggering assertion so the
    test surfaces the real cause rather than a cryptic JSON error.
    """
    assert ENV_EXAMPLE_PATH.exists(), f".env.example not found at {ENV_EXAMPLE_PATH}"
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{IDIS_API_KEYS_ENV}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{IDIS_API_KEYS_ENV}= line missing from .env.example")


class TestEnvExampleRegistryLoadsCleanly:
    """The exact value shipped in .env.example must produce a usable registry."""

    def test_env_example_value_is_valid_json_object(self) -> None:
        raw = _extract_env_example_api_keys_json()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert len(parsed) >= 1

    def test_env_example_loads_non_empty_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = _extract_env_example_api_keys_json()
        monkeypatch.setenv(IDIS_API_KEYS_ENV, raw)

        registry = _load_api_key_registry()

        assert registry, ".env.example must produce at least one valid API key record"
        assert all(isinstance(r, ApiKeyRecord) for r in registry.values())

    def test_env_example_key_resolves_to_full_tenant_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = _extract_env_example_api_keys_json()
        monkeypatch.setenv(IDIS_API_KEYS_ENV, raw)

        registry = _load_api_key_registry()
        api_key, record = next(iter(registry.items()))

        # Lookup round-trip must still find the record.
        matched = _constant_time_lookup(api_key, registry)
        assert matched is record

        # Every field required by TenantContext is populated and non-empty.
        assert record.tenant_id
        assert record.actor_id
        assert record.name
        assert record.timezone
        assert record.data_region
        assert record.roles, "example record must declare at least one role"

        # Roles must normalize (i.e. be drawn from ALL_ROLES) — fail-closed if not.
        normalized = _normalize_roles(record.roles)
        assert normalized, "normalized roles must be non-empty"


class TestEnvExampleKeyResolvesThroughRoute:
    """End-to-end proof: the .env.example-style record resolves a real
    TenantContext via GET /v1/tenants/me through the actual auth path.
    """

    def test_get_tenants_me_returns_full_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = _extract_env_example_api_keys_json()
        monkeypatch.setenv(IDIS_API_KEYS_ENV, raw)

        parsed = json.loads(raw)
        api_key, expected = next(iter(parsed.items()))

        client = TestClient(create_app())
        response = client.get(
            "/v1/tenants/me",
            headers={"X-IDIS-API-Key": api_key},
        )

        assert response.status_code == 200, response.text
        body = response.json()

        # Every field required by the task is present in the resolved context.
        assert body["tenant_id"] == expected["tenant_id"]
        assert body["actor_id"] == expected["actor_id"]
        assert body["data_region"] == expected["data_region"]
        assert body["timezone"] == expected["timezone"]
        assert body["name"] == expected["name"]

        # roles must survive serialization and match the example's declared roles.
        assert set(body["roles"]) == set(expected["roles"])


class TestMalformedEntriesWarn:
    """Malformed registry entries must warn and be rejected, not silently skipped."""

    def test_non_dict_value_emits_warning_and_is_dropped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bad_payload = json.dumps({"tenant-local-dev": "test-key-not-real"})
        monkeypatch.setenv(IDIS_API_KEYS_ENV, bad_payload)

        with caplog.at_level(logging.WARNING, logger="idis.api.auth"):
            registry = _load_api_key_registry()

        assert registry == {}, "non-dict values must not produce a registry entry"

        warning_texts = [rec.getMessage() for rec in caplog.records]
        # Per-entry warning pointing at the non-dict value.
        assert any(
            "non-dict value" in msg and IDIS_API_KEYS_ENV in msg for msg in warning_texts
        ), f"expected per-entry non-dict warning; got: {warning_texts!r}"
        # Summary warning pointing at total-reject case so ops sees the symptom.
        assert any(
            "zero valid API key records" in msg for msg in warning_texts
        ), f"expected zero-records summary warning; got: {warning_texts!r}"

    def test_non_dict_warning_does_not_leak_full_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        secret = "very-long-secret-key-should-not-appear-in-logs"
        bad_payload = json.dumps({secret: "still-wrong-shape"})
        monkeypatch.setenv(IDIS_API_KEYS_ENV, bad_payload)

        with caplog.at_level(logging.WARNING, logger="idis.api.auth"):
            _load_api_key_registry()

        all_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert secret not in all_text, "full API key must not appear in logs"
        # Mask form: first 4 chars + "...(len=N)".
        assert re.search(rf"{re.escape(secret[:4])}\.\.\.\(len={len(secret)}\)", all_text), (
            f"expected masked identifier in warning; got: {all_text!r}"
        )

    def test_invalid_record_shape_emits_validation_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Missing required fields (tenant_id, name, etc.) — ApiKeyRecord fails.
        bad_payload = json.dumps(
            {"short-key": {"tenant_id": "t1", "actor_id": "a1"}}
        )
        monkeypatch.setenv(IDIS_API_KEYS_ENV, bad_payload)

        with caplog.at_level(logging.WARNING, logger="idis.api.auth"):
            registry = _load_api_key_registry()

        assert registry == {}
        warning_texts = [rec.getMessage() for rec in caplog.records]
        assert any("failed ApiKeyRecord validation" in msg for msg in warning_texts), (
            f"expected validation-failure warning; got: {warning_texts!r}"
        )
