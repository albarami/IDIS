"""Shared hermetic fixtures for Slice100 Task 2 governance RED tests."""

from __future__ import annotations

import dataclasses
import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ACCEPTANCE_TENANT_ID = "10000000-0000-4000-8000-000000000001"
CANARY_TENANT_ID = "10000000-0000-4000-8000-000000000002"
ACCEPTANCE_SECRET = "slice100-acceptance-secret-value"
CANARY_SECRET = "slice100-canary-secret-value"
GOVERNANCE_SECRET = "slice100-governance-secret-value"
SERVICE_REGION = "qa-doha-1"
SIGNER_ID = "slice100-governance-signer-v1"

RIGHTS_SCHEMA_VERSION = "enrichment-rights-approvals-v1"
BUDGET_SCHEMA_VERSION = "provider-budget-policy-v1"

EXPECTED_BUDGET_COMPONENT_IDS = frozenset(
    {
        "anthropic",
        "openai_embeddings",
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
    }
)


def require_module(name: str) -> ModuleType:
    """Import one Task 2 module or turn its absence into an intentional RED failure."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"TASK2_API_MISSING: {name}: {exc}", pytrace=False)


def require_attr(module: ModuleType, name: str) -> Any:
    """Resolve one planned Task 2 API or produce a focused RED failure."""
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(f"TASK2_API_MISSING: {module.__name__}.{name}", pytrace=False)
    return value


def error_code(exc: BaseException) -> str | None:
    """Read the stable code from a typed Task 2 failure."""
    code = getattr(exc, "code", None)
    return str(code) if code is not None else None


def report_reason_codes(report: object) -> list[str]:
    """Read a secret-safe readiness report without constraining its model library."""
    codes = getattr(report, "reason_codes", None)
    if codes is None and isinstance(report, Mapping):
        codes = report.get("reason_codes")
    assert codes is not None, "strict auth report must expose reason_codes"
    return [str(code) for code in codes]


def report_may_proceed(report: object) -> bool:
    """Read the strict admission decision from a model or mapping."""
    value = getattr(report, "may_proceed", None)
    if value is None and isinstance(report, Mapping):
        value = report.get("may_proceed")
    assert isinstance(value, bool), "strict auth report must expose boolean may_proceed"
    return value


def safe_render(value: object) -> str:
    """Render a report for leakage assertions without assuming Pydantic/dataclass."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json.dumps(model_dump(mode="json"), sort_keys=True)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json.dumps(dataclasses.asdict(value), sort_keys=True, default=str)
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True, default=str)
    return repr(value)


def acceptance_record() -> dict[str, object]:
    return {
        "tenant_id": ACCEPTANCE_TENANT_ID,
        "actor_id": "slice100-acceptance-service",
        "name": "Slice100 Acceptance Service",
        "timezone": "Asia/Qatar",
        "data_region": SERVICE_REGION,
        "roles": ["INTEGRATION_SERVICE"],
    }


def canary_record() -> dict[str, object]:
    return {
        "tenant_id": CANARY_TENANT_ID,
        "actor_id": "slice100-isolation-canary",
        "name": "Slice100 Isolation Canary",
        "timezone": "Asia/Qatar",
        "data_region": SERVICE_REGION,
        "roles": ["INTEGRATION_SERVICE"],
    }


def governance_record() -> dict[str, object]:
    return {
        "tenant_id": ACCEPTANCE_TENANT_ID,
        "actor_id": "slice100-governance-admin",
        "name": "Slice100 Governance Admin",
        "timezone": "Asia/Qatar",
        "data_region": SERVICE_REGION,
        "roles": ["ADMIN"],
    }


def strict_auth_env() -> dict[str, str]:
    registry = {
        ACCEPTANCE_SECRET: acceptance_record(),
        CANARY_SECRET: canary_record(),
        GOVERNANCE_SECRET: governance_record(),
    }
    return {
        "IDIS_API_KEYS_JSON": json.dumps(registry, sort_keys=True),
        "IDIS_REAL_EXAMPLE_API_KEY": ACCEPTANCE_SECRET,
        "IDIS_ISOLATION_CANARY_API_KEY": CANARY_SECRET,
        "IDIS_GOVERNANCE_ADMIN_API_KEY": GOVERNANCE_SECRET,
        "IDIS_ACCEPTANCE_TENANT_ID": ACCEPTANCE_TENANT_ID,
        "IDIS_ISOLATION_CANARY_TENANT_ID": CANARY_TENANT_ID,
        "IDIS_SERVICE_REGION": SERVICE_REGION,
        "IDIS_UI_DEV_AUTH_BYPASS": "false",
    }


def rights_payload() -> dict[str, object]:
    common = {
        "environment": "PROD",
        "status": "APPROVED",
        "attribution_policy_version": "attribution-v1",
        "no_store_policy_version": "no-store-v1",
        "approved_at": "2026-07-15T00:00:00Z",
        "expires_at": "2027-07-15T00:00:00Z",
        "revoked_at": None,
    }
    rows: list[dict[str, object]] = [
        {
            **common,
            "provider_id": "finnhub",
            "terms_version_hash": "1" * 64,
            "rights_class": "RED",
            "license_scope": "commercial_prod",
            "evidence_hash": "a" * 64,
            "credential_version_id": "20000000-0000-4000-8000-000000000001",
        },
        {
            **common,
            "provider_id": "fmp",
            "terms_version_hash": "2" * 64,
            "rights_class": "RED",
            "license_scope": "commercial_prod",
            "evidence_hash": "b" * 64,
            "credential_version_id": "20000000-0000-4000-8000-000000000002",
        },
        {
            **common,
            "provider_id": "wayback",
            "terms_version_hash": "3" * 64,
            "rights_class": "YELLOW",
            "license_scope": "attribution_redistribution",
            "evidence_hash": "c" * 64,
            "credential_version_id": None,
        },
        {
            **common,
            "provider_id": "google_news_rss",
            "terms_version_hash": "4" * 64,
            "rights_class": "YELLOW",
            "license_scope": "attribution_redistribution",
            "evidence_hash": "d" * 64,
            "credential_version_id": None,
        },
    ]
    return {"tenant_id": ACCEPTANCE_TENANT_ID, "approvals": rows}


def budget_payload() -> dict[str, object]:
    components: dict[str, dict[str, object]] = {
        component_id: {"max_calls": 4, "max_usd": "1.00"}
        for component_id in EXPECTED_BUDGET_COMPONENT_IDS
    }
    components["anthropic"] = {
        "max_calls": 8,
        "max_usd": "20.00",
        "max_input_tokens": 100_000,
        "max_output_tokens": 25_000,
    }
    components["openai_embeddings"] = {
        "max_calls": 8,
        "max_usd": "5.00",
        "max_embedding_tokens": 100_000,
    }
    return {
        "tenant_id": ACCEPTANCE_TENANT_ID,
        "policy_version": "slice100-acceptance-v1",
        "currency": "USD",
        "total_max_usd": "100.00",
        "components": components,
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
