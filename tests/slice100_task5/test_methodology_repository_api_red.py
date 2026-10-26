"""RED public seams for Task 5 durable methodology import/freeze/select."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from idis.api.auth import IDIS_API_KEYS_ENV, TenantContext
from idis.api.errors import IdisHttpError
from idis.api.policy import ADMIN_ONLY, ALL_ROLES, POLICY_RULES
from idis.api.routes import runs as runs_route
from idis.api.routes.runs import StartRunRequest
from idis.audit.sink import InMemoryAuditSink


def _repository_module():  # type: ignore[no-untyped-def]
    try:
        return importlib.import_module("idis.persistence.repositories.methodologies")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Task 5 durable methodology repository is missing: {exc}")


def _api_module():  # type: ignore[no-untyped-def]
    try:
        return importlib.import_module("idis.api.routes.methodology_versions")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Task 5 methodology API is missing: {exc}")


def test_repository_exposes_the_complete_import_freeze_select_coverage_protocol() -> None:
    module = _repository_module()
    repository_type = module.PostgresMethodologiesRepository
    required_parameters = {
        "import_registry": {
            "registry",
            "source_type",
            "source_alias",
            "source_byte_count",
            "source_contract_hash",
            "source_trace_digest",
            "imported_by_actor_id",
            "import_audit_event_id",
        },
        "freeze_version": {
            "methodology_version_record_id",
            "expected_source_sha256",
            "frozen_by_actor_id",
            "freeze_audit_event_id",
        },
        "get_frozen_version": {"methodology_version_record_id"},
        "create_run_selection": {
            "deal_id",
            "run_id",
            "commercial_dd_version_record_id",
            "financial_dd_version_record_id",
            "created_by_actor_id",
            "selection_audit_event_id",
        },
        "load_run_selection": {"tenant_id", "deal_id", "run_id"},
        "load_selected_registries": {"selection"},
        "insert_coverage_records": {"records"},
        "list_coverage_records": {"deal_id", "run_id"},
    }

    for method_name, expected in required_parameters.items():
        method = getattr(repository_type, method_name)
        actual = set(inspect.signature(method).parameters) - {"self"}
        assert expected <= actual, method_name


def test_repository_factory_has_no_in_memory_or_missing_connection_fallback() -> None:
    module = _repository_module()

    with pytest.raises(Exception) as raised:
        module.get_methodologies_repository(
            None,
            "11111111-1111-4111-8111-111111111111",
        )

    assert getattr(raised.value, "code", None) == "METHODOLOGY_COVERAGE_INCOMPLETE"


def test_full_start_run_requires_both_distinct_methodology_version_record_ids() -> None:
    valid = StartRunRequest.model_validate(
        {
            "mode": "FULL",
            "methodology_selection": {
                "commercial_dd_version_record_id": "11111111-1111-4111-8111-111111111111",
                "financial_dd_version_record_id": "22222222-2222-4222-8222-222222222222",
            },
        }
    )
    assert str(valid.methodology_selection.commercial_dd_version_record_id) == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert str(valid.methodology_selection.financial_dd_version_record_id) == (
        "22222222-2222-4222-8222-222222222222"
    )

    invalid_selections = (
        None,
        {"commercial_dd_version_record_id": "11111111-1111-4111-8111-111111111111"},
        {"financial_dd_version_record_id": "22222222-2222-4222-8222-222222222222"},
        {
            "commercial_dd_version_record_id": "11111111-1111-4111-8111-111111111111",
            "financial_dd_version_record_id": "11111111-1111-4111-8111-111111111111",
        },
    )
    for selection in invalid_selections:
        body: dict[str, object] = {"mode": "FULL"}
        if selection is not None:
            body["methodology_selection"] = selection
        with pytest.raises(ValidationError):
            StartRunRequest.model_validate(body)


def test_snapshot_request_does_not_fabricate_a_methodology_selection() -> None:
    request = StartRunRequest.model_validate({"mode": "SNAPSHOT"})

    assert request.methodology_selection is None


def _request_with_json(body: dict[str, Any]) -> Request:
    encoded = json.dumps(body).encode("utf-8")
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/deals/22222222-2222-4222-8222-222222222222/runs",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
            "app": SimpleNamespace(state=SimpleNamespace(extractor_configured=True)),
        },
        receive,
    )


@pytest.mark.parametrize(
    "selection",
    (
        None,
        {"commercial_dd_version_record_id": "11111111-1111-4111-8111-111111111111"},
        {"financial_dd_version_record_id": "22222222-2222-4222-8222-222222222222"},
    ),
)
def test_start_run_rejects_absent_or_one_sided_selection_before_repository_mutation(
    monkeypatch: pytest.MonkeyPatch,
    selection: dict[str, str] | None,
) -> None:
    repository_calls = 0

    def forbidden_repository_call(*_args: object, **_kwargs: object) -> None:
        nonlocal repository_calls
        repository_calls += 1
        raise AssertionError("run repository reached before methodology selection validation")

    monkeypatch.setattr(runs_route, "get_runs_repository", forbidden_repository_call)
    body: dict[str, Any] = {"mode": "FULL"}
    if selection is not None:
        body["methodology_selection"] = selection
    tenant = TenantContext(
        tenant_id="11111111-1111-4111-8111-111111111111",
        actor_id="task5-api-test",
        name="Task5 API test",
        timezone="UTC",
        data_region="QA",
        roles=frozenset({"ADMIN"}),
    )

    with pytest.raises(IdisHttpError) as raised:
        asyncio.run(
            runs_route.start_run(
                "22222222-2222-4222-8222-222222222222",
                _request_with_json(body),
                tenant,
            )
        )

    assert raised.value.code == "INVALID_REQUEST"
    assert repository_calls == 0


def test_methodology_api_models_accept_only_server_selected_imports_and_safe_metadata() -> None:
    module = _api_module()

    financial = module.MethodologyVersionImportRequest.model_validate(
        {"methodology_type": "financial_dd"}
    )
    assert financial.methodology_type.value == "financial_dd"
    for extra in (
        {"path": "C:/private/workbook.xlsx"},
        {"source_bytes": "private"},
        {"questions": []},
        {"source_name": "private.xlsx"},
    ):
        with pytest.raises(ValidationError):
            module.MethodologyVersionImportRequest.model_validate(
                {"methodology_type": "financial_dd", **extra}
            )

    assert set(module.MethodologyVersionSafeResponse.model_fields) == {
        "methodology_version_record_id",
        "methodology_version_id",
        "methodology_type",
        "version_label",
        "source_sha256",
        "source_byte_count",
        "source_contract_hash",
        "source_trace_digest",
        "registry_hash",
        "version_hash",
        "question_count",
        "source_section_count",
        "header_row",
        "status",
        "imported_at",
        "frozen_at",
    }


def test_methodology_api_route_and_policy_surface_is_exact() -> None:
    module = _api_module()
    route_inventory = {
        (method, route.path, route.operation_id)
        for route in module.router.routes
        for method in route.methods
    }

    assert route_inventory == {
        ("POST", "/v1/methodology-versions/import", "importMethodologyVersion"),
        (
            "POST",
            "/v1/methodology-versions/{methodology_version_record_id}/freeze",
            "freezeMethodologyVersion",
        ),
        ("GET", "/v1/methodology-versions", "listMethodologyVersions"),
    }
    assert POLICY_RULES["importMethodologyVersion"].allowed_roles == ADMIN_ONLY
    assert POLICY_RULES["importMethodologyVersion"].is_mutation is True
    assert POLICY_RULES["freezeMethodologyVersion"].allowed_roles == ADMIN_ONLY
    assert POLICY_RULES["freezeMethodologyVersion"].is_mutation is True
    assert POLICY_RULES["listMethodologyVersions"].allowed_roles == ALL_ROLES
    assert POLICY_RULES["listMethodologyVersions"].is_mutation is False


def test_production_composition_root_exposes_the_exact_methodology_openapi_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A router seam alone is not acceptance evidence: the real app must compose it."""
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    from idis.api.main import create_app

    schema = create_app(service_region="me-south-1").openapi()
    paths = schema["paths"]

    assert paths["/v1/methodology-versions/import"]["post"]["operationId"] == (
        "importMethodologyVersion"
    )
    assert (
        paths["/v1/methodology-versions/{methodology_version_record_id}/freeze"]["post"][
            "operationId"
        ]
        == "freezeMethodologyVersion"
    )
    assert paths["/v1/methodology-versions"]["get"]["operationId"] == ("listMethodologyVersions")
    import_schema = schema["components"]["schemas"]["MethodologyVersionImportRequest"]
    assert import_schema["additionalProperties"] is False
    assert import_schema["required"] == ["methodology_type"]


def test_production_api_returns_safe_400_for_missing_full_methodology_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic validator context must never make the error envelope unserializable."""
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                "task5-analyst-key": {
                    "tenant_id": "11111111-1111-4111-8111-111111111111",
                    "actor_id": "task5-analyst",
                    "name": "Task 5 analyst",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                }
            }
        ),
    )
    from idis.api.main import create_app

    client = TestClient(
        create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1"),
        raise_server_exceptions=False,
    )
    response = client.post(
        "/v1/deals/22222222-2222-4222-8222-222222222222/runs",
        headers={"X-IDIS-API-Key": "task5-analyst-key"},
        json={"mode": "FULL"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


def test_production_api_import_resolves_the_source_server_side_and_binds_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise import through auth, OpenAPI, RBAC, route composition, and audit middleware."""
    module = _api_module()
    captured: dict[str, Any] = {}

    class Repository:
        def import_registry(self, **kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            registry = kwargs["registry"]
            now = datetime.now(UTC)
            return SimpleNamespace(
                methodology_version_record_id="33333333-3333-4333-8333-333333333333",
                methodology_version_id=registry.current_version.methodology_version_id,
                methodology_type=registry.methodology_type,
                version_label=registry.current_version.version_label,
                source_sha256=registry.current_version.source_hash,
                source_byte_count=kwargs["source_byte_count"],
                source_contract_hash=kwargs["source_contract_hash"],
                source_trace_digest=kwargs["source_trace_digest"],
                registry_hash=registry.registry_hash,
                version_hash="a" * 64,
                question_count=len(registry.current_version.questions),
                source_section_count=len(
                    {q.source_trace.sheet_or_section for q in registry.current_version.questions}
                ),
                header_row=None,
                status="IMPORTED",
                imported_at=now,
                frozen_at=None,
            )

    monkeypatch.setattr(module, "get_methodologies_repository", lambda _conn, _tenant: Repository())
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                "task5-admin-key": {
                    "tenant_id": "11111111-1111-4111-8111-111111111111",
                    "actor_id": "task5-admin",
                    "name": "Task 5 admin",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ADMIN"],
                }
            }
        ),
    )
    from idis.api.main import create_app

    audit_sink = InMemoryAuditSink()
    client = TestClient(create_app(audit_sink=audit_sink, service_region="me-south-1"))
    response = client.post(
        "/v1/methodology-versions/import",
        headers={"X-IDIS-API-Key": "task5-admin-key"},
        json={"methodology_type": "commercial_dd"},
    )

    assert response.status_code == 201
    assert response.json()["question_count"] == 21
    assert "source_alias" not in response.json()
    assert "source_name" not in response.json()
    assert captured["source_type"] == "checked_in_template"
    assert captured["source_alias"] == "commercial-dd-template-v1"
    assert captured["imported_by_actor_id"] == "task5-admin"
    event = audit_sink.events[-1]
    assert event["event_type"] == "methodology.version.imported"
    assert event["event_id"] == captured["import_audit_event_id"]


def test_full_run_persists_selection_after_run_creation_in_the_request_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    order: list[str] = []
    captured: dict[str, Any] = {}

    class RunsRepository:
        def deal_exists(self, _deal_id: str) -> bool:
            return True

        def has_active_run(self, _deal_id: str) -> bool:
            return False

    class ProofRepository:
        def consume_and_admit_contract_run(self, **kwargs: Any) -> dict[str, Any]:
            order.append("run")
            return {"run_id": kwargs["run_id"], "status": "QUEUED"}

    class MethodologiesRepository:
        def create_run_selection(self, **kwargs: Any) -> None:
            assert order == ["run"]
            order.append("selection")
            captured.update(kwargs)

    methodologies = MethodologiesRepository()
    monkeypatch.setattr(
        runs_route,
        "get_runs_repository",
        lambda conn, _tenant: (
            RunsRepository() if conn is connection else pytest.fail("wrong request connection")
        ),
    )
    monkeypatch.setattr(
        runs_route,
        "_validate_full_methodology_selection",
        lambda **_kwargs: methodologies,
    )
    # RunSource.document_ids has min_length=1 (src/idis/models/run_source.py).
    # The frozen reload is stubbed with the freeze seam: this test isolates
    # admission/selection ordering and does not open a database connection.
    preflight_corpus = [{"document_id": "44444444-4444-4444-8444-444444444444"}]
    monkeypatch.setattr(
        runs_route,
        "_gather_preflight_corpus",
        lambda *_args, **_kwargs: preflight_corpus,
    )
    monkeypatch.setattr(
        runs_route,
        "_reload_frozen_preflight_corpus",
        lambda **_kwargs: list(preflight_corpus),
    )
    monkeypatch.setattr(
        runs_route, "_extraction_ready_documents_from_preflight_corpus", lambda _corpus: []
    )
    monkeypatch.setattr(runs_route, "is_strict_full_live_required", lambda **_kwargs: True)
    monkeypatch.setattr(
        runs_route,
        "build_strict_full_live_admission_report",
        lambda **_kwargs: SimpleNamespace(may_proceed=True),
    )
    monkeypatch.setattr(
        runs_route,
        "resolve_strict_preflight_binding",
        lambda **_kwargs: object(),
    )
    # Task 7 owns source-package freezing; this Task 5 test isolates the shared
    # transaction ordering between run admission and methodology selection.
    monkeypatch.setattr(
        runs_route,
        "_freeze_task7_selected_source_packages",
        lambda **_kwargs: ("33333333-3333-4333-8333-333333333333",),
    )
    monkeypatch.setattr(
        runs_route,
        "get_strict_preflight_proofs_repository",
        lambda conn, _tenant: (
            ProofRepository() if conn is connection else pytest.fail("wrong request connection")
        ),
    )
    request = _request_with_json(
        {
            "mode": "FULL",
            "strict_preflight_proof_id": "proof-task5",
            "methodology_selection": {
                "commercial_dd_version_record_id": "11111111-1111-4111-8111-111111111111",
                "financial_dd_version_record_id": "22222222-2222-4222-8222-222222222222",
            },
        }
    )
    request.state.db_conn = connection
    tenant = TenantContext(
        tenant_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        actor_id="task5-run-actor",
        name="Task 5 run actor",
        timezone="UTC",
        data_region="QA",
        roles=frozenset({"ADMIN"}),
    )

    result = asyncio.run(
        runs_route.start_run(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            request,
            tenant,
        )
    )

    assert result.status == "QUEUED"
    assert order == ["run", "selection"]
    assert captured["deal_id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert captured["run_id"] == result.run_id
    assert captured["created_by_actor_id"] == "task5-run-actor"
    assert request.state.audit_event_id == captured["selection_audit_event_id"]
