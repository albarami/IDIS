"""Slice 56 strict runtime foundation tests."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import idis.api.routes.runs as runs_route
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation.real_example_run_harness import (
    RealExampleFullRunHarnessOptions,
    run_real_example_full_run_harness,
)
from idis.persistence import db
from idis.persistence.repositories.run_steps import clear_run_steps_store
from idis.persistence.repositories.runs import InMemoryRunsRepository
from idis.services.runs.execution import RunExecutionResult
from idis.services.runs.strict_full_live import (
    REQUIRED_STRICT_COMPONENTS,
    build_strict_full_live_readiness_report,
)
from tests._cir04c3_required_frozen_preview import (
    RequiredFrozenPreview,
    required_frozen_preview,
)
from tests.abac_seed import seed_deal_access

TENANT_ID = "11111111-1111-1111-1111-111111111111"
API_KEY = "slice56-test-key"
STRICT_API_BASE_URL = "http://127.0.0.1:8000"
STRICT_PREFLIGHT_PROOF_ID = "00000000-0000-4000-8000-000000000056"
COMMERCIAL_DD_VERSION_RECORD_ID = "10000000-0000-4000-8000-000000000056"
FINANCIAL_DD_VERSION_RECORD_ID = "20000000-0000-4000-8000-000000000056"


def _full_run_request(**values: Any) -> dict[str, Any]:
    return {
        "mode": "FULL",
        "methodology_selection": {
            "commercial_dd_version_record_id": COMMERCIAL_DD_VERSION_RECORD_ID,
            "financial_dd_version_record_id": FINANCIAL_DD_VERSION_RECORD_ID,
        },
        **values,
    }


class _LegacyMethodologySelectionsRepository:
    """Minimal recorder keeping Slice56 assertions on strict runtime behavior."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    def create_run_selection(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(dict(kwargs))
        return {"selection_id": "30000000-0000-4000-8000-000000000056"}


@pytest.fixture(autouse=True)
def _admit_fictional_methodology_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> _LegacyMethodologySelectionsRepository:
    repository = _LegacyMethodologySelectionsRepository()
    monkeypatch.setattr(
        "idis.api.routes.runs._validate_full_methodology_selection",
        lambda **_kwargs: repository,
    )
    return repository


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeApiClient:
    def __init__(self) -> None:
        self.created_deals = 0
        self.uploads: list[dict[str, Any]] = []
        self.run_requests: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        if url == "/v1/deals":
            self.created_deals += 1
            return _FakeResponse(201, {"deal_id": "deal-private-1"})
        if url == "/v1/deals/deal-private-1/documents/upload":
            self.uploads.append(
                {"headers": headers or {}, "params": params or {}, "content": content or b""}
            )
            return _FakeResponse(
                201,
                {
                    "document_id": f"document-{len(self.uploads)}",
                    "parse_status": "PARSED",
                },
            )
        if url == "/v1/deals/deal-private-1/runs":
            self.run_requests.append(json or {})
            return _FakeResponse(202, {"status": "SUCCEEDED", "steps": []})
        raise AssertionError(f"unexpected URL: {url}")


class _StrictBlockRunsRepository:
    def __init__(self) -> None:
        self.create_calls = 0

    def deal_exists(self, deal_id: str) -> bool:
        return deal_id == "deal-strict-pre-created"

    def has_active_run(self, deal_id: str) -> bool:
        return False

    def create(self, **_kwargs: Any) -> dict[str, Any]:
        self.create_calls += 1
        raise AssertionError("strict full-live gate must run before runs_repo.create")


def setup_function() -> None:
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


def teardown_function() -> None:
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


def _create_hermetic_strict_app(monkeypatch: Any) -> Any:
    """Compose the legacy in-memory app before restoring request-time strict inputs."""
    with monkeypatch.context() as composition_env:
        composition_env.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
        composition_env.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
        return create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")


def test_strict_env_source_uses_process_over_dotenv_without_leaking_values(tmp_path: Path) -> None:
    secret_from_dotenv = "DOTENV_SECRET_SHOULD_NOT_LEAK"
    secret_from_process = "PROCESS_SECRET_SHOULD_NOT_LEAK"
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text(
        "\n".join(
            [
                "ANTHROPIC_API_KEY=DOTENV_SECRET_SHOULD_NOT_LEAK",
                "IDIS_EXTRACT_BACKEND=deterministic",
                "IDIS_ANTHROPIC_MODEL_EXTRACT=dotenv-model",
                "IDIS_DATABASE_URL=postgresql://dotenv-secret/db",
                "IDIS_OBJECT_STORE_BACKEND=filesystem",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_full_live_readiness_report(
        env={
            "ANTHROPIC_API_KEY": secret_from_process,
            "IDIS_EXTRACT_BACKEND": "anthropic",
            IDIS_API_KEYS_ENV: json.dumps(_api_keys()),
        },
        dotenv_path=dotenv_path,
    )

    dumped = report.model_dump(mode="json")
    env_sources = dumped["env_sources"]
    assert env_sources["ANTHROPIC_API_KEY"] == "process"
    assert env_sources["IDIS_ANTHROPIC_MODEL_EXTRACT"] == "dotenv"
    assert env_sources["IDIS_DEBATE_BACKEND"] == "missing"
    encoded = json.dumps(dumped, sort_keys=True)
    assert secret_from_dotenv not in encoded
    assert secret_from_process not in encoded
    assert "postgresql://dotenv-secret/db" not in encoded


def test_dotenv_can_explicitly_activate_strict_mode(tmp_path: Path) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text("IDIS_REQUIRE_FULL_LIVE=1\n", encoding="utf-8")

    from idis.services.runs.strict_full_live import is_strict_full_live_required

    assert is_strict_full_live_required(env={}, dotenv_path=dotenv_path) is True


def test_env_sources_only_exposes_tracked_keys(tmp_path: Path) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text("PRIVATE_CUSTOM_SECRET_NAME=value\n", encoding="utf-8")

    report = build_strict_full_live_readiness_report(
        env={"PRIVATE_PROCESS_SECRET_NAME": "value"},
        dotenv_path=dotenv_path,
    )

    env_sources = report.model_dump(mode="json")["env_sources"]
    assert "PRIVATE_CUSTOM_SECRET_NAME" not in env_sources
    assert "PRIVATE_PROCESS_SECRET_NAME" not in env_sources


def test_env_inventory_never_exposes_values_urls_hosts_usernames_or_lengths(
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text(
        "\n".join(
            [
                "IDIS_DATABASE_URL=postgresql://private_user@secret-host.supabase.co/db",
                "SUPABASE_URL=https://secret-project-ref.supabase.co",
                "ANTHROPIC_API_KEY=sk-ant-secret-with-identifying-length",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_full_live_readiness_report(
        env={"NEO4J_USERNAME": "private_graph_user"},
        dotenv_path=dotenv_path,
    )

    encoded = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "postgresql://",
        "private_user",
        "secret-host",
        "secret-project-ref",
        "supabase.co",
        "sk-ant-secret",
        "identifying-length",
        "private_graph_user",
        "length",
    ):
        assert forbidden not in encoded


def test_strict_inventory_contains_every_required_component_and_truth_table_fields() -> None:
    report = build_strict_full_live_readiness_report(env={})
    dumped = report.model_dump(mode="json")
    inventory = dumped["component_inventory"]

    required_component_set = {
        "API FULL path",
        "worker path",
        "private harness path",
        "parsers",
        "OCR",
        "MP4/STT",
        "Anthropic extraction",
        "Anthropic debate",
        "Anthropic analysis",
        "Anthropic scoring",
        "enrichment public providers",
        "enrichment BYOL providers",
        "Supabase database",
        "Supabase Auth",
        "Supabase Storage",
        "Supabase Vectors/RAG",
        "Postgres/RLS",
        "object storage",
        "audit sink",
        "calculation engine",
        "CalcSanad",
        "Neo4j graph projection",
        "graph retrieval",
        "pgvector/RAG",
        "Layer 1 debate",
        "Layer 2 IC challenge",
        "deliverable generation",
        "product export",
        "UI/API download",
        "real_example gate",
    }
    assert set(REQUIRED_STRICT_COMPONENTS) == required_component_set
    assert {item["component_name"] for item in inventory} == required_component_set
    assert [item["component_name"] for item in inventory] == list(REQUIRED_STRICT_COMPONENTS)
    required_fields = {
        "component_name",
        "exists_in_code",
        "full_wired",
        "config_present",
        "health_check_status",
        "output_visible",
        "blocker",
        "implementation_slice",
        "evidence_files",
    }
    for item in inventory:
        assert required_fields.issubset(item)
        assert isinstance(item["evidence_files"], list)
        assert item["implementation_slice"].startswith("Slice ")


def test_current_slice56_blockers_are_truthful() -> None:
    report = build_strict_full_live_readiness_report(
        env={},
        binary_resolver=lambda _name: None,
        preflight_corpus=[
            {
                "document_id": "doc-media",
                "document_name": "redacted.mp4",
                "doc_type": "MEDIA",
                "metadata": {"parser_reason_codes": ["ocr_required"]},
            }
        ],
    )
    inventory = {item.component_name: item for item in report.component_inventory}

    assert inventory["OCR"].exists_in_code is True
    assert inventory["OCR"].output_visible is False
    assert inventory["MP4/STT"].exists_in_code is True
    assert inventory["MP4/STT"].full_wired is False
    assert inventory["enrichment BYOL providers"].full_wired is False
    assert inventory["Neo4j graph projection"].full_wired is False
    assert inventory["pgvector/RAG"].exists_in_code is True
    assert inventory["pgvector/RAG"].full_wired is False
    assert inventory["product export"].full_wired is False


def test_unconfigured_and_contract_only_inventory_rows_block() -> None:
    report = build_strict_full_live_readiness_report(env={})

    assert "Postgres/RLS" in report.blocking_components
    assert "parsers" in report.blocking_components


def test_strict_api_block_response_includes_inventory_and_no_env_values(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_secret = "STRICT_DOTENV_SECRET_SHOULD_NOT_LEAK"
    dotenv_path.write_text(f"ANTHROPIC_API_KEY={dotenv_secret}\n", encoding="utf-8")
    process_secret = "STRICT_PROCESS_SECRET_SHOULD_NOT_LEAK"
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_STRICT_DOTENV_PATH", str(dotenv_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", process_secret)
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))

    app = _create_hermetic_strict_app(monkeypatch)
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Strict Deal", "company_name": "StrictCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_ID, deal_id, "slice56-actor")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    run_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json=_full_run_request(strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID),
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert run_resp.status_code == 409
    body = run_resp.json()
    report = body["details"]["strict_full_live"]
    assert body["code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert "run_id" not in body
    # Operator-safe shape preserves structured blockers without inventory/env-source leakage.
    assert report["may_proceed"] is False
    assert "blocking_components" in report
    assert "blocker_count" in report
    encoded = json.dumps(body, sort_keys=True)
    assert dotenv_secret not in encoded
    assert process_secret not in encoded
    assert str(dotenv_path) not in encoded
    assert "Highly sensitive raw revenue sentence" not in encoded
    for forbidden in (
        "C:\\Projects\\",
        "secret-board-pack",
        "customer-secret-extension",
        "strict-vars",
        "STRICT_DOTENV_SECRET",
        "STRICT_PROCESS_SECRET",
    ):
        assert forbidden not in encoded


def test_strict_api_can_be_activated_from_explicit_dotenv(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / "strict-vars"
    dotenv_path.write_text("IDIS_REQUIRE_FULL_LIVE=1\n", encoding="utf-8")
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
    monkeypatch.setenv("IDIS_STRICT_DOTENV_PATH", str(dotenv_path))
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))

    app = _create_hermetic_strict_app(monkeypatch)
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Strict Dotenv Deal", "company_name": "DotenvCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_ID, deal_id, "slice56-actor")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    run_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json=_full_run_request(strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID),
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert run_resp.status_code == 409
    assert run_resp.json()["code"] == "STRICT_FULL_LIVE_BLOCKED"


def test_strict_api_blocks_before_run_create_or_step_records(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    repo = _StrictBlockRunsRepository()
    run_step_factory_calls = 0

    def fake_steps_factory(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal run_step_factory_calls
        run_step_factory_calls += 1
        raise AssertionError("strict full-live gate must run before run step repository access")

    import idis.api.routes.runs as runs_module

    monkeypatch.setattr(runs_module, "get_runs_repository", lambda *_args: repo)
    monkeypatch.setattr(runs_module, "get_run_steps_repository", fake_steps_factory)

    app = _create_hermetic_strict_app(monkeypatch)
    app.state.deal_documents = {
        "deal-strict-pre-created": [_preflight_doc(deal_id="deal-strict-pre-created")]
    }
    client = TestClient(app)

    run_resp = client.post(
        "/v1/deals/deal-strict-pre-created/runs",
        json=_full_run_request(strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID),
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert run_resp.status_code == 409
    assert repo.create_calls == 0
    assert run_step_factory_calls == 0


def test_private_harness_strict_block_includes_inventory_before_upload_without_leaks(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    secret_dir = root / "Private Secret Data Room"
    secret_dir.mkdir(parents=True)
    (secret_dir / "secret-board-pack.pdf").write_bytes(b"%PDF-1.4\nPRIVATE SECRET\n%%EOF")

    def fail_if_external_client_is_built(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict readiness must block before constructing the HTTP client")

    monkeypatch.setattr(
        "idis.evaluation.real_example_run_harness._external_api_client",
        fail_if_external_client_is_built,
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            deal_stage="SERIES_B",
            root=root,
            api_base_url=STRICT_API_BASE_URL,
            require_full_live=True,
            strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID,
        )
    )

    assert summary["status"] == "blocked"
    assert summary["run"]["attempted"] is False
    assert "component_inventory" in summary["strict_full_live"]
    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    assert "Private Secret Data Room" not in encoded
    assert "secret-board-pack" not in encoded
    assert "PRIVATE SECRET" not in encoded
    assert summary["counts_by_extension"] == {"pdf": 1}


def test_private_harness_strict_summary_buckets_private_unknown_suffixes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "private.customer-secret-extension").write_bytes(b"PRIVATE")

    def fail_if_external_client_is_built(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict readiness must block before constructing the HTTP client")

    monkeypatch.setattr(
        "idis.evaluation.real_example_run_harness._external_api_client",
        fail_if_external_client_is_built,
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            deal_stage="SERIES_B",
            root=root,
            api_base_url=STRICT_API_BASE_URL,
            require_full_live=True,
            strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID,
        )
    )

    assert summary["counts_by_extension"] == {"unsupported_or_unknown": 1}
    encoded = json.dumps(summary, sort_keys=True)
    assert "customer-secret-extension" not in encoded


def test_non_strict_api_run_still_reaches_execution_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _unset_non_strict_runtime(monkeypatch)
    with required_frozen_preview(monkeypatch) as preview:
        client, tenant_id = _non_strict_postgres_client(
            tmp_path=tmp_path, monkeypatch=monkeypatch, preview=preview
        )
        try:
            deal_id = _create_deal(client, tenant_id)
            upload = _upload_parsed_pdf(client, deal_id)
            uploaded_id = str(upload["document_id"])
            posted_manifest_sha256 = _remember_posted_manifest_sha256(client)
            package_id = preview.package_public_uploads(
                client,
                api_key=API_KEY,
                tenant_id=tenant_id,
                deal_id=deal_id,
                uploads=[upload],
                seal=False,
            )
            assert len(posted_manifest_sha256) == 1
            _assert_package_open_unbound(preview, tenant_id, deal_id, package_id)
            run_ids, corpora = _install_capturing_execution(monkeypatch)
            admission_calls = _strict_admission_calls(monkeypatch)
            run_resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json=_full_run_request(),
                headers={"X-IDIS-API-Key": API_KEY},
            )
            assert run_resp.status_code == 202, run_resp.text
            body = run_resp.json()
            assert body["run_id"]
            assert run_ids == [body["run_id"]]
            assert corpora == [[uploaded_id]]
            assert admission_calls == []
            rows = preview.package_rows(tenant_id, deal_id, package_id)
            assert rows
            assert all(
                row["status"] == "SEALED"
                and row["source_binding_state"] == "BOUND"
                and row["file_binding"] == "BOUND"
                and row["document_binding"] == "BOUND"
                and row["parse_attempt_id"] is not None
                for row in rows
            )
            assert _manifest_sha256(rows[0]["package_row"]) == posted_manifest_sha256[0]
        finally:
            client.close()


def test_non_strict_api_run_without_source_package_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unset_non_strict_runtime(monkeypatch)
    for name in (
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_DATABASE_ERASURE_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_REQUIRE_POSTGRES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    run_ids, _corpora = _install_capturing_execution(monkeypatch)
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app, raise_server_exceptions=False)
    try:
        deal_id = _create_deal(client, TENANT_ID)
        app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]
        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json=_full_run_request(),
            headers={"X-IDIS-API-Key": API_KEY},
        )
        _assert_source_package_refused(run_resp)
        assert run_ids == []
        stored, _cursor = InMemoryRunsRepository(TENANT_ID).list_by_deal(deal_id=deal_id, limit=10)
        assert stored == []
    finally:
        client.close()


def test_non_strict_api_run_with_unpackaged_document_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _unset_non_strict_runtime(monkeypatch)
    with required_frozen_preview(monkeypatch) as preview:
        client, tenant_id = _non_strict_postgres_client(
            tmp_path=tmp_path, monkeypatch=monkeypatch, preview=preview
        )
        try:
            deal_id = _create_deal(client, tenant_id)
            upload = _upload_parsed_pdf(client, deal_id)
            run_ids, _corpora = _install_capturing_execution(monkeypatch)
            run_resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json=_full_run_request(),
                headers={"X-IDIS-API-Key": API_KEY},
            )
            _assert_source_package_refused(run_resp)
            assert run_ids == []
            assert _deal_run_count(preview, tenant_id, deal_id) == 0
            assert _document_binding(preview, tenant_id, str(upload["document_id"])) == "UNBOUND"
        finally:
            client.close()


def test_non_strict_private_harness_keeps_existing_extension_summary(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "public-safe.pdf").write_bytes(b"%PDF-1.4\nSAFE\n%%EOF")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(deal_stage="SERIES_B", root=root, api_client=client)
    )

    assert summary["status"] == "succeeded"
    assert len(client.uploads) == 1
    assert len(client.run_requests) == 1
    assert summary["counts_by_extension"] == {".pdf": 1}


def _unset_non_strict_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)


def _non_strict_postgres_client(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preview: RequiredFrozenPreview,
) -> tuple[TestClient, str]:
    tenant_id = str(uuid.uuid4())
    preview.add_tenant(tenant_id, "me-south-1")
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys(tenant_id)))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    clear_deals_store()
    clear_document_store()
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    return TestClient(app, raise_server_exceptions=False), tenant_id


def _create_deal(client: TestClient, tenant_id: str) -> str:
    response = client.post(
        "/v1/deals",
        json={"name": "Non Strict Deal", "company_name": "NonStrictCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert response.status_code == 201, response.text
    deal_id = str(response.json()["deal_id"])
    seed_deal_access(tenant_id, deal_id, "slice56-actor")
    return deal_id


def _upload_parsed_pdf(client: TestClient, deal_id: str) -> dict[str, Any]:
    pdf = _public_pdf_bytes()
    response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": "company-999-document-00001.pdf",
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "source_system": "api-upload",
        },
        content=pdf,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parse_status"] == "PARSED"
    uuid.UUID(str(body["document_id"]))
    return body


def _public_pdf_bytes() -> bytes:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.fail("Required public PDF fixture dependency is unavailable")
    buffer = io.BytesIO()
    pdf_canvas = canvas.Canvas(buffer, pagesize=letter)
    pdf_canvas.drawString(72, 750, "PUBLIC SYNTHETIC CALLER PREVIEW")
    pdf_canvas.drawString(72, 735, "Revenue was $5M in 2024.")
    pdf_canvas.save()
    return buffer.getvalue()


def _remember_posted_manifest_sha256(client: TestClient) -> list[str]:
    posted: list[str] = []
    original_post = client.post

    def post(url: str, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            manifest = payload.get("source_manifest")
            if isinstance(manifest, dict) and "manifest_sha256" in manifest:
                posted.append(str(manifest["manifest_sha256"]))
        return original_post(url, *args, **kwargs)

    client.post = post  # type: ignore[method-assign]
    return posted


def _install_capturing_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], list[list[str]]]:
    run_ids: list[str] = []
    corpora: list[list[str]] = []

    class CapturingRunExecutionService:
        def __init__(self, **kwargs: object) -> None:
            self.audit_sink = kwargs["audit_sink"]

        def execute(self, ctx: object) -> RunExecutionResult:
            run_ids.append(str(ctx.run_id))  # type: ignore[attr-defined]
            corpus = ctx.preflight_corpus  # type: ignore[attr-defined]
            corpora.append([str(document["document_id"]) for document in corpus])
            return RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )

    monkeypatch.setattr(runs_route, "RunExecutionService", CapturingRunExecutionService)
    return run_ids, corpora


def _strict_admission_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = runs_route.build_strict_full_live_admission_report

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(runs_route, "build_strict_full_live_admission_report", spy)
    return calls


def _assert_package_open_unbound(
    preview: RequiredFrozenPreview,
    tenant_id: str,
    deal_id: str,
    package_id: str,
) -> None:
    with preview.engine.begin() as conn:
        db.set_tenant_local(conn, tenant_id)
        status, binding = conn.execute(
            text(
                "SELECT status, source_binding_state FROM data_room_packages "
                "WHERE package_id = CAST(:package AS uuid) "
                "AND deal_id = CAST(:deal AS uuid)"
            ),
            {"package": package_id, "deal": deal_id},
        ).one()
    assert status == "OPEN"
    assert binding == "UNBOUND"


def _deal_run_count(preview: RequiredFrozenPreview, tenant_id: str, deal_id: str) -> int:
    with preview.engine.begin() as conn:
        db.set_tenant_local(conn, tenant_id)
        count = conn.execute(
            text("SELECT count(*) FROM runs WHERE deal_id = CAST(:deal AS uuid)"),
            {"deal": deal_id},
        ).scalar_one()
    return int(count)


def _document_binding(preview: RequiredFrozenPreview, tenant_id: str, document_id: str) -> str:
    with preview.engine.begin() as conn:
        db.set_tenant_local(conn, tenant_id)
        binding = conn.execute(
            text(
                "SELECT source_binding_state FROM documents "
                "WHERE document_id = CAST(:document AS uuid)"
            ),
            {"document": document_id},
        ).scalar_one()
    return str(binding)


def _manifest_sha256(package_row: object) -> str:
    row: object = json.loads(package_row) if isinstance(package_row, str) else package_row
    if not isinstance(row, dict):
        raise AssertionError("stored package row is not an object")
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not isinstance(metadata, dict):
        raise AssertionError("stored package metadata is not an object")
    manifest = metadata["source_manifest"]
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    if not isinstance(manifest, dict):
        raise AssertionError("stored source manifest is not an object")
    return str(manifest["manifest_sha256"])


def _assert_source_package_refused(response: Any) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "SOURCE_PACKAGE_REQUIRED"
    assert body["message"] == (
        "Frozen source preparation or strict proof did not authorize this run"
    )
    assert body["details"] is None


def _api_keys(tenant_id: str = TENANT_ID) -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": tenant_id,
            "actor_id": "slice56-actor",
            "name": "Slice 56 Tenant",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        }
    }


def _preflight_doc(*, deal_id: str) -> dict[str, Any]:
    return {
        "tenant_id": TENANT_ID,
        "deal_id": deal_id,
        "document_id": "doc-strict",
        "doc_id": "artifact-doc-strict",
        "doc_type": "DOCX",
        "parse_status": "PARSED",
        "document_name": "doc-strict.docx",
        "sha256": "a" * 64,
        "uri": "deals/doc-strict.docx",
        "metadata": {},
        "source_metadata": {},
        "spans": [
            {
                "span_id": "span-strict",
                "tenant_id": TENANT_ID,
                "deal_id": deal_id,
                "document_id": "doc-strict",
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "Highly sensitive raw revenue sentence",
                "content_hash": "b" * 64,
            }
        ],
    }
