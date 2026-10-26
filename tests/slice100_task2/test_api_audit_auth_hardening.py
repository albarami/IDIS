"""Slice100 Task 2 RED: strict API services share durable, atomic audit authority."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import InMemoryAuditSink, JsonlFileAuditSink

_TENANT_ID = "10000000-0000-4000-8000-000000000002"


@pytest.mark.parametrize(
    ("route_module_name", "service_module_name", "factory_name", "service_name"),
    [
        (
            "idis.api.routes.claims",
            "idis.services.claims.service",
            "_get_claim_service",
            "ClaimService",
        ),
        (
            "idis.api.routes.sanad",
            "idis.api.routes.sanad",
            "_get_sanad_service",
            "SanadService",
        ),
        (
            "idis.api.routes.defects",
            "idis.api.routes.defects",
            "_get_defect_service",
            "DefectService",
        ),
    ],
)
def test_api_mutation_service_factory_threads_application_audit_sink(
    route_module_name: str,
    service_module_name: str,
    factory_name: str,
    service_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim, Sanad, and defect routes must not construct a second audit authority."""
    route_module = importlib.import_module(route_module_name)
    service_module = importlib.import_module(service_module_name)
    connection = object()
    sink = PostgresAuditSink()
    calls: list[dict[str, Any]] = []
    sentinel = object()

    def construct(**kwargs: Any) -> object:
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(service_module, service_name, construct)
    request = SimpleNamespace(
        state=SimpleNamespace(db_conn=connection),
        app=SimpleNamespace(state=SimpleNamespace(audit_sink=sink)),
    )

    result = getattr(route_module, factory_name)(request, _TENANT_ID)

    assert result is sentinel
    assert calls == [
        {
            "tenant_id": _TENANT_ID,
            "db_conn": connection,
            "audit_sink": sink,
        }
    ]


class _RecordingPostgresAuditSink(PostgresAuditSink):
    def __init__(self) -> None:
        self.transactional: list[tuple[object, dict[str, Any]]] = []
        self.standalone: list[dict[str, Any]] = []

    def emit_in_tx(self, conn: Any, event: dict[str, Any]) -> None:
        self.transactional.append((conn, event))

    def emit(self, event: dict[str, Any]) -> None:
        self.standalone.append(event)


class _ConnectionProbe:
    """Minimal SQLAlchemy-connection shape needed by repository constructors."""

    def execute(self, *_args: Any, **_kwargs: Any) -> object:
        return object()


@pytest.mark.parametrize(
    "service_and_emitter",
    [
        pytest.param(
            (
                lambda conn, sink: importlib.import_module(
                    "idis.services.claims.service"
                ).ClaimService(_TENANT_ID, db_conn=conn, audit_sink=sink),
                lambda service: service._emit_audit_event("claim.updated", "claim-1"),
            ),
            id="claim",
        ),
        pytest.param(
            (
                lambda conn, sink: importlib.import_module(
                    "idis.services.sanad.service"
                ).SanadService(_TENANT_ID, db_conn=conn, audit_sink=sink),
                lambda service: service._emit_audit_event("sanad.updated", "sanad-1"),
            ),
            id="sanad",
        ),
        pytest.param(
            (
                lambda conn, sink: importlib.import_module(
                    "idis.services.defects.service"
                ).DefectService(_TENANT_ID, db_conn=conn, audit_sink=sink),
                lambda service: service._emit_audit_event("defect.updated", "defect-1"),
            ),
            id="defect",
        ),
    ],
)
def test_postgres_backed_mutation_service_emits_audit_in_same_transaction(
    service_and_emitter: tuple[
        Callable[[object, _RecordingPostgresAuditSink], Any],
        Callable[[Any], None],
    ],
) -> None:
    """A durable service mutation and its audit record share the request transaction."""
    build_service, emit = service_and_emitter
    connection = _ConnectionProbe()
    sink = _RecordingPostgresAuditSink()
    service = build_service(connection, sink)

    emit(service)

    assert len(sink.transactional) == 1
    assert sink.transactional[0][0] is connection
    assert sink.transactional[0][1]["tenant_id"] == _TENANT_ID
    assert sink.standalone == []


@pytest.mark.parametrize("sink_kind", ["memory", "jsonl"])
def test_strict_prompt_governance_rejects_explicit_non_durable_sink(
    sink_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit test sinks cannot bypass strict prompt-governance durability."""
    from idis.services.prompts.versioning import PromptVersioningService

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    sink = (
        InMemoryAuditSink()
        if sink_kind == "memory"
        else JsonlFileAuditSink(str(tmp_path / "prompt-audit.jsonl"))
    )

    with pytest.raises(RuntimeError, match="AUDIT"):
        PromptVersioningService(
            registry=object(),  # type: ignore[arg-type]
            audit_sink=sink,
            tenant_id=_TENANT_ID,
        )


@pytest.mark.parametrize(
    "raw_registry",
    [
        (
            '{"key":{"tenant_id":"tenant-a","actor_id":"actor-a",'
            '"actor_id":"actor-b","name":"Acceptance","timezone":"Asia/Qatar",'
            '"data_region":"me-south-1","roles":["ADMIN"]}}'
        ),
        (
            '{"key":{"tenant_id":"tenant-a","actor_id":"actor-a",'
            '"name":"Acceptance","timezone":NaN,"data_region":"me-south-1",'
            '"roles":["ADMIN"]}}'
        ),
    ],
    ids=["duplicate-nested-member", "nonfinite-constant"],
)
def test_api_registry_rejects_noncanonical_json_as_one_invalid_document(
    raw_registry: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime auth and startup diagnostics apply strict-auth's JSON parser rules."""
    from idis.api import auth

    monkeypatch.setenv(auth.IDIS_API_KEYS_ENV, raw_registry)

    assert auth._load_api_key_registry() == {}
    report = auth.validate_api_key_registry_config()
    assert report.status == "invalid_json"
    assert report.valid_entry_count == 0
    assert report.malformed_entry_count == 0
    assert report.reason_codes == [f"{auth.IDIS_API_KEYS_ENV}_INVALID_JSON"]
