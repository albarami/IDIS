"""Slice95 Task 3 — reviewer strict-readiness read-model + safe GET endpoint (G1 / DEC-D).

Adds a reviewer-facing GET /v1/strict-readiness that projects the internal
StrictFullLiveReadinessReport to a SAFE review shape (component modes + blockers +
env-var names only) — deliberately excluding evidence file:line, env_sources, free-text
blocker_message, the component_inventory truth table, and provider matrices.

Injected fakes only — no real LLM; the endpoint routes through config-only health checkers with
the object-store probe disabled, so a reviewer GET makes no live provider calls (proven by
test_strict_readiness_makes_no_live_provider_calls). No DB, no migration (DEC-E). PYTHONPATH is
pinned to this worktree's src for every run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.openapi_loader import load_openapi_spec
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.pgvector_health import PgvectorHealthCheck

_TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_API_KEY = "test-key-readiness-reviewer"
_KEYS = {
    _API_KEY: {
        "tenant_id": _TENANT_ID,
        "actor_id": "actor-readiness",
        "name": "Readiness Reviewer",
        "timezone": "UTC",
        "data_region": "us-east-1",
        "roles": ["ADMIN"],
    }
}

_TOP_FIELDS = {"required", "may_proceed", "blocker_count", "blocking_components", "components"}
_COMPONENT_FIELDS = {
    "component_name",
    "status",
    "may_proceed",
    "required_env_vars",
    "required_services",
}
_INTERNAL_FIELDS = (
    "evidence",
    "env_sources",
    "blocker_message",
    "component_inventory",
    "evidence_files",
    "byol_providers",
    "enrichment_provider_matrix",
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_KEYS))
    return TestClient(create_app(service_region="us-east-1"))


def test_strict_readiness_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/strict-readiness").status_code == 401


def test_strict_readiness_returns_safe_review(client: TestClient) -> None:
    resp = client.get("/v1/strict-readiness", headers={"X-IDIS-API-Key": _API_KEY})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == _TOP_FIELDS
    assert isinstance(body["may_proceed"], bool)
    assert isinstance(body["blocker_count"], int)
    assert isinstance(body["components"], list) and body["components"]
    for component in body["components"]:
        assert set(component) == _COMPONENT_FIELDS
        assert isinstance(component["required_env_vars"], list)

    # The exact-key checks above already prove no internal field is present as a key.
    # Belt-and-suspenders on values: no source path / file:line evidence leaks (component
    # names like "rag_evidence_retrieval" are SAFE identifiers; the excluded `evidence` field
    # carried file:line references, which must never appear).
    encoded = json.dumps(body)
    for leak in (".py:", "src/idis", "src\\idis"):
        assert leak not in encoded


def test_strict_readiness_review_model_excludes_internal_fields() -> None:
    from idis.api.routes.readiness import (
        StrictReadinessComponentReview,
        StrictReadinessReview,
    )

    assert set(StrictReadinessReview.model_fields) == _TOP_FIELDS
    assert set(StrictReadinessComponentReview.model_fields) == _COMPONENT_FIELDS
    for internal in _INTERNAL_FIELDS:
        assert internal not in StrictReadinessReview.model_fields
        assert internal not in StrictReadinessComponentReview.model_fields


def test_readiness_schemas_forbid_extra_in_static_and_generated_spec() -> None:
    # DEC-F: the static YAML is the source of truth, so it must forbid extra fields exactly like
    # the runtime models (extra='forbid' => additionalProperties: false in the generated FastAPI
    # schema). Otherwise the spec permits fields the runtime rejects, weakening the contract.
    static = load_openapi_spec()["components"]["schemas"]
    generated = create_app().openapi()["components"]["schemas"]
    for schema_name in ("StrictReadinessReview", "StrictReadinessComponentReview"):
        assert generated[schema_name]["additionalProperties"] is False
        assert static[schema_name].get("additionalProperties") is False, (
            f"static yaml {schema_name} must set additionalProperties: false to match runtime"
        )


def test_strict_readiness_makes_no_live_provider_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Reviewer contract: GET /v1/strict-readiness inspects config/wiring MODES only. It must never
    # open a live Neo4j/Postgres connection, call the embedding provider, or touch the object store
    # (billing / latency / DoS / side effects). Configure a full-live-looking env so the *real*
    # checkers WOULD attempt those live calls, then prove the endpoint routes through
    # config-only checkers instead — the live probes stay untouched.
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_KEYS))
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.invalid:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("IDIS_ENABLE_VECTOR_SEARCH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("IDIS_EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("IDIS_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("IDIS_EMBEDDING_DIMENSIONS", str(VECTOR_EMBEDDING_DIMENSIONS))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path))
    # NB: IDIS_DATABASE_URL is deliberately NOT set. It would engage the separate, intended db_tx
    # request-transaction middleware (a real connection for every /v1 request → 503 on an
    # unreachable host), which is orthogonal to this endpoint's readiness-probe contract. The
    # pgvector readiness probe is still covered: the pre-fix report resolves check_pgvector_health
    # unconditionally, so the tracker below fires regardless of IDIS_DATABASE_URL.

    called: list[str] = []

    def _tracker(name: str, result: object) -> Callable[..., object]:
        def _record(*_args: object, **_kwargs: object) -> object:
            called.append(name)
            return result

        return _record

    # The live probes, bound where the readiness report resolves them. Each returns a benign valid
    # value so a regressed (live-calling) path still yields 200 — the recorded name is what proves a
    # live probe was invoked. A config-only endpoint records nothing.
    monkeypatch.setattr(
        "idis.services.runs.strict_full_live.check_neo4j_health",
        _tracker("neo4j", Neo4jHealthCheck.failed()),
    )
    monkeypatch.setattr(
        "idis.services.runs.strict_full_live.check_embedding_health",
        _tracker("embedding", EmbeddingHealthCheck.failed()),
    )
    monkeypatch.setattr(
        "idis.services.runs.strict_full_live.check_pgvector_health",
        _tracker("pgvector", PgvectorHealthCheck.failed()),
    )
    monkeypatch.setattr(
        "idis.storage.defaults.build_configured_product_export_object_store",
        _tracker("object_store", None),
    )

    client = TestClient(create_app(service_region="us-east-1"))
    resp = client.get("/v1/strict-readiness", headers={"X-IDIS-API-Key": _API_KEY})

    assert resp.status_code == 200
    assert set(resp.json()) == _TOP_FIELDS
    assert called == [], f"strict-readiness GET invoked live provider probes: {called}"


def test_strict_readiness_opens_no_db_connection_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reviewer contract: /v1/strict-readiness is config/read-model inspection and MUST work when the
    # database is down. DBTransactionMiddleware opens a live Postgres connection per /v1 request
    # when IDIS_DATABASE_URL is configured (before the handler runs), so without an exemption a
    # reviewer GET 503s (DATABASE_UNAVAILABLE) on any DB outage. Set the URL and fail the connection
    # opener if it is ever reached — the endpoint must still return 200 and open nothing.
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_KEYS))
    monkeypatch.setenv("IDIS_DATABASE_URL", "postgresql+psycopg2://u:p@127.0.0.1:1/idis")

    opened: list[str] = []

    def _fail_open_connection() -> tuple[object, object]:
        opened.append("db")
        raise AssertionError("strict-readiness must not open a DB connection")

    monkeypatch.setattr("idis.api.middleware.db_tx._open_connection", _fail_open_connection)

    client = TestClient(create_app(service_region="us-east-1"))
    resp = client.get("/v1/strict-readiness", headers={"X-IDIS-API-Key": _API_KEY})

    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == _TOP_FIELDS
    assert opened == [], "strict-readiness opened a DB connection via DBTransactionMiddleware"


def test_strict_readiness_required_env_vars_are_names_only(client: TestClient) -> None:
    # Requirement tokens like "IDIS_EXTRACT_BACKEND=anthropic" / "IDIS_OCR_ENABLED=1" must be
    # normalized to bare NAMES so no required VALUE (anthropic, filesystem, 1, ...) surfaces.
    body = client.get("/v1/strict-readiness", headers={"X-IDIS-API-Key": _API_KEY}).json()
    seen_any = False
    for component in body["components"]:
        for name in component["required_env_vars"]:
            seen_any = True
            assert "=" not in name, f"required_env_var is not a bare name: {name!r}"
            assert name == name.strip() and name, f"required_env_var not a clean token: {name!r}"
    assert seen_any, "expected at least one component to declare required env-var names"
