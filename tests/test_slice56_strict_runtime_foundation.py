"""Slice 56 strict runtime foundation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation.real_example_run_harness import (
    RealExampleFullRunHarnessOptions,
    run_real_example_full_run_harness,
)
from idis.persistence.repositories.run_steps import clear_run_steps_store
from idis.services.runs.strict_full_live import (
    REQUIRED_STRICT_COMPONENTS,
    build_strict_full_live_readiness_report,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
API_KEY = "slice56-test-key"


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

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Strict Deal", "company_name": "StrictCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    run_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json={"mode": "FULL"},
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

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Strict Dotenv Deal", "company_name": "DotenvCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    run_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json={"mode": "FULL"},
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

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {
        "deal-strict-pre-created": [_preflight_doc(deal_id="deal-strict-pre-created")]
    }
    client = TestClient(app)

    run_resp = client.post(
        "/v1/deals/deal-strict-pre-created/runs",
        json={"mode": "FULL"},
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert run_resp.status_code == 409
    assert repo.create_calls == 0
    assert run_step_factory_calls == 0


def test_private_harness_strict_block_includes_inventory_before_upload_without_leaks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    secret_dir = root / "Private Secret Data Room"
    secret_dir.mkdir(parents=True)
    (secret_dir / "secret-board-pack.pdf").write_bytes(b"%PDF-1.4\nPRIVATE SECRET\n%%EOF")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=client,
            require_full_live=True,
        )
    )

    assert summary["status"] == "blocked"
    assert client.uploads == []
    assert client.run_requests == []
    assert "component_inventory" in summary["strict_full_live"]
    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    assert "Private Secret Data Room" not in encoded
    assert "secret-board-pack" not in encoded
    assert "PRIVATE SECRET" not in encoded
    assert summary["counts_by_extension"] == {"pdf": 1}


def test_private_harness_strict_summary_buckets_private_unknown_suffixes(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "private.customer-secret-extension").write_bytes(b"PRIVATE")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=client,
            require_full_live=True,
        )
    )

    assert summary["counts_by_extension"] == {"unsupported_or_unknown": 1}
    encoded = json.dumps(summary, sort_keys=True)
    assert "customer-secret-extension" not in encoded


def test_non_strict_api_run_still_reaches_execution_service(monkeypatch: Any) -> None:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Non Strict Deal", "company_name": "NonStrictCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    run_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json={"mode": "FULL"},
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert run_resp.status_code == 202
    assert run_resp.json()["run_id"]


def test_non_strict_private_harness_keeps_existing_extension_summary(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "public-safe.pdf").write_bytes(b"%PDF-1.4\nSAFE\n%%EOF")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["status"] == "succeeded"
    assert len(client.uploads) == 1
    assert len(client.run_requests) == 1
    assert summary["counts_by_extension"] == {".pdf": 1}


def _api_keys() -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": TENANT_ID,
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
