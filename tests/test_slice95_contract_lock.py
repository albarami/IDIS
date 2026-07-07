"""Slice95 Task 9 — contract lock: static OpenAPI YAML vs generated FastAPI schemas (DEC-B).

Locks the Slice95 reviewer read-model schemas so the source-of-truth YAML cannot drift from the
runtime Pydantic response models — required fields, property sets, and additionalProperties —
and pins the debate rounds safe-shape (rounds items reference the DebateRoundSummary summary).

Injected fakes only — no real LLM, no database. PYTHONPATH is pinned to this worktree's src.
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.api.main import create_app
from idis.api.openapi_loader import load_openapi_spec

# Slice95 review surfaces that must stay locked between the static YAML and the runtime models.
_SCHEMAS = [
    "StrictReadinessReview",
    "StrictReadinessComponentReview",
    "RunListItem",
    "PaginatedRunList",
    "DebateSession",
    "DebateRoundSummary",
    "HumanGate",
    "HumanGateAction",
    "Override",
    "RunStatus",
    "RunStepResponse",
]


@pytest.fixture(scope="module")
def schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    generated = create_app().openapi()["components"]["schemas"]
    static = load_openapi_spec()["components"]["schemas"]
    return generated, static


@pytest.mark.parametrize("name", _SCHEMAS)
def test_slice95_schema_static_matches_generated(
    name: str, schemas: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    generated, static = schemas
    assert name in generated, f"{name} missing from generated FastAPI schema"
    assert name in static, f"{name} missing from static OpenAPI YAML"
    gen = generated[name]
    sta = static[name]
    assert set(sta.get("required", [])) == set(gen.get("required", [])), (
        f"{name} required drift: static={sorted(sta.get('required', []))} "
        f"generated={sorted(gen.get('required', []))}"
    )
    assert set(sta.get("properties", {})) == set(gen.get("properties", {})), (
        f"{name} properties drift: static={sorted(sta.get('properties', {}))} "
        f"generated={sorted(gen.get('properties', {}))}"
    )
    assert sta.get("additionalProperties") == gen.get("additionalProperties"), (
        f"{name} additionalProperties drift: static={sta.get('additionalProperties')} "
        f"generated={gen.get('additionalProperties')}"
    )


def _rounds_items_ref(schema: dict[str, Any]) -> str:
    return str(schema["DebateSession"]["properties"]["rounds"]["items"].get("$ref", ""))


def test_debate_session_rounds_reference_safe_summary(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    # The debate rounds must be the typed safe summary in BOTH the static YAML and the runtime
    # schema — never an untyped additionalProperties object that could carry raw content.
    generated, static = schemas
    assert _rounds_items_ref(generated).endswith("DebateRoundSummary")
    assert _rounds_items_ref(static).endswith("DebateRoundSummary")
