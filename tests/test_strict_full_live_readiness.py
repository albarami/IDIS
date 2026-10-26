"""Strict full-live readiness gate tests for Slice 54."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import idis.evaluation.real_example_run_harness as harness_module
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.evaluation.real_example_run_harness import (
    RealExampleFullRunHarnessOptions,
    run_real_example_full_run_harness,
)
from idis.persistence.repositories.run_steps import clear_run_steps_store
from tests.abac_seed import seed_deal_access
from tests.strict_runtime_test_support import (
    RecordingPostgresAuditSink,
    reviewed_strict_api_profile,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
API_KEY = "slice54-test-key"
STRICT_API_BASE_URL = "http://127.0.0.1:8000"
STRICT_PREFLIGHT_PROOF_ID = "00000000-0000-4000-8000-000000000054"
COMMERCIAL_DD_VERSION_RECORD_ID = "10000000-0000-4000-8000-000000000054"
FINANCIAL_DD_VERSION_RECORD_ID = "20000000-0000-4000-8000-000000000054"


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
    """Minimal recorder preserving strict-readiness assertions."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    def create_run_selection(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(dict(kwargs))
        return {"selection_id": "30000000-0000-4000-8000-000000000054"}


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


class _FakeExternalApiClient(_FakeApiClient):
    """Canonical external path: enqueue first, then observe worker completion."""

    def __init__(self) -> None:
        super().__init__()
        self.run_gets: list[str] = []
        self._run_statuses = iter(("QUEUED", "RUNNING", "SUCCEEDED"))

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        if url == "/v1/deals/deal-private-1/runs":
            self.run_requests.append(dict(kwargs.get("json") or {}))
            return _FakeResponse(
                202,
                {"run_id": "run-private-1", "status": "QUEUED", "steps": []},
            )
        return super().post(url, **kwargs)

    def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        if url != "/v1/runs/run-private-1":
            raise AssertionError(f"unexpected URL: {url}")
        self.run_gets.append(url)
        status = next(self._run_statuses)
        return _FakeResponse(
            200,
            {
                "run_id": "run-private-1",
                "status": status,
                "mode": "FULL",
                "steps": [] if status != "SUCCEEDED" else [{"status": "COMPLETED"}],
            },
        )


def setup_function() -> None:
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


def teardown_function() -> None:
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


def test_strict_report_rejects_deterministic_fallbacks_and_missing_anthropic_env() -> None:
    """Strict report must make deterministic model fallback impossible to call full-live."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    report = build_strict_full_live_readiness_report(env={})

    assert report.may_proceed is False
    extraction = report.component("supported_parsers_extraction")
    live_llm = report.component("live_llm_model_clients")
    analysis = report.component("agent_analysis")
    debate = report.component("debate_layer_1")
    scoring = report.component("scoring")

    assert extraction.status == StrictComponentStatus.MISSING_CREDENTIALS
    assert live_llm.status == StrictComponentStatus.MISSING_CREDENTIALS
    assert analysis.status == StrictComponentStatus.MISSING_CREDENTIALS
    assert debate.status == StrictComponentStatus.MISSING_CREDENTIALS
    assert scoring.status == StrictComponentStatus.MISSING_CREDENTIALS
    assert "IDIS_EXTRACT_BACKEND=anthropic" in extraction.required_env_vars
    assert "IDIS_DEBATE_BACKEND=anthropic" in debate.required_env_vars
    assert "ANTHROPIC_API_KEY" in live_llm.required_env_vars
    assert extraction.may_proceed is False


def test_strict_report_blocks_missing_durable_runtime_env() -> None:
    """Strict full-live must not silently accept in-memory/local runtime fallback."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    report = build_strict_full_live_readiness_report(env={})
    runtime = report.component("durable_runtime")

    assert runtime.status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert runtime.required_env_vars == [
        "IDIS_DATABASE_URL",
        "IDIS_API_KEYS_JSON",
        "IDIS_OBJECT_STORE_BACKEND",
    ]
    assert runtime.may_proceed is False


def test_strict_report_treats_api_keys_json_as_canonical_runtime_config() -> None:
    """Strict runtime config should follow auth's canonical API key registry env."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://configured/db",
            "IDIS_API_KEYS_JSON": "{}",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        }
    )
    runtime = report.component("durable_runtime")

    assert runtime.status == StrictComponentStatus.LIVE_WIRED_AND_USED
    assert runtime.required_env_vars == []
    assert runtime.may_proceed is True


def test_strict_report_lists_ocr_media_rag_graph_and_layer2_blockers() -> None:
    """Strict report must classify known product blockers without claiming wiring."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    report = build_strict_full_live_readiness_report(
        preflight_corpus=[
            {
                "document_id": "doc-ocr",
                "document_name": "redacted-media.mp4",
                "doc_type": "MEDIA",
                "metadata": {
                    "parser_requires_ocr": True,
                    "parser_reason_codes": ["ocr_required"],
                },
            }
        ],
        env={},
        binary_resolver=lambda _name: None,
    )

    assert report.component("ocr").status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert report.component("mp4_stt").status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert (
        report.component("rag_evidence_retrieval").status
        == StrictComponentStatus.MISSING_CREDENTIALS
    )
    assert report.component("graph_evidence_layer").status == (
        StrictComponentStatus.MISSING_CREDENTIALS
    )
    assert report.component("debate_layer_2_ic_challenge").status == (
        StrictComponentStatus.MISSING_CREDENTIALS
    )
    assert "OCR-required documents" in report.component("ocr").blocker_message
    assert "MP4 files are present" in report.component("mp4_stt").blocker_message
    assert "ffmpeg" in report.component("mp4_stt").required_services
    assert "GraphProjectionService" in report.component("graph_evidence_layer").evidence


def test_strict_api_full_run_fails_before_execution_and_returns_structured_blockers(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """`IDIS_REQUIRE_FULL_LIVE=1` must block before `RunExecutionService.execute`."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    profile = reviewed_strict_api_profile(tmp_path / "profile")
    api_key = profile.values["IDIS_REAL_EXAMPLE_API_KEY"]
    tenant_id = profile.values["IDIS_ACCEPTANCE_TENANT_ID"]
    app = create_app(
        audit_sink=RecordingPostgresAuditSink(),
        ingestion_service=object(),
        strict_profile=profile,
    )
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Strict Deal", "company_name": "StrictCo"},
        headers={"X-IDIS-API-Key": api_key},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(tenant_id, deal_id, "slice100-acceptance-service")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    def fail_if_executed(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict full-live gate must run before execution")

    with patch("idis.services.runs.execution.RunExecutionService.execute", fail_if_executed):
        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json=_full_run_request(strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID),
            headers={"X-IDIS-API-Key": api_key},
        )

    assert run_resp.status_code == 409
    body = run_resp.json()
    assert body["code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert "run_id" not in body
    report = body["details"]["strict_full_live"]
    assert report["may_proceed"] is False
    # Operator-safe shape: blocking component names only (no evidence/inventory paths).
    blocking = set(report["blocking_components"])
    assert {
        "live_llm_model_clients",
        "rag_evidence_retrieval",
        "graph_evidence_layer",
        "debate_layer_2_ic_challenge",
        "product_export_bundle",
    }.issubset(blocking)
    encoded = json.dumps(body, sort_keys=True)
    assert "Highly sensitive raw revenue sentence" not in encoded


def test_private_harness_require_full_live_blocks_before_upload_or_run_without_leaks(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Private harness strict mode must return safe blockers before upload/run execution."""
    root = tmp_path / "real_example"
    secret_dir = root / "Private Secret Data Room"
    secret_dir.mkdir(parents=True)
    (secret_dir / "secret-board-pack.pdf").write_bytes(b"%PDF-1.4\nPRIVATE SECRET\n%%EOF")
    (secret_dir / "secret-founder-demo.mp4").write_bytes(b"PRIVATE MEDIA SECRET")

    def fail_if_external_client_is_built(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict readiness must block before constructing the HTTP client")

    monkeypatch.setattr(harness_module, "_external_api_client", fail_if_external_client_is_built)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            deal_stage="SERIES_A",
            root=root,
            api_base_url=STRICT_API_BASE_URL,
            require_full_live=True,
            strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID,
        )
    )

    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "strict_full_live_preflight",
        "reason_code": "STRICT_FULL_LIVE_BLOCKED",
        "http_status": None,
    }
    assert summary["run"]["attempted"] is False
    assert summary["strict_full_live"]["may_proceed"] is False
    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    assert "Private Secret Data Room" not in encoded
    assert "secret-board-pack" not in encoded
    assert "secret-founder-demo" not in encoded
    assert "PRIVATE SECRET" not in encoded
    assert "PRIVATE MEDIA SECRET" not in encoded


def test_private_harness_require_full_live_proceeds_when_preflight_passes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Strict harness should proceed when readiness says all components may proceed."""
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "public-safe.pdf").write_bytes(b"%PDF-1.4\nSAFE\n%%EOF")
    client = _FakeExternalApiClient()

    monkeypatch.setattr(
        harness_module,
        "build_strict_full_live_readiness_report",
        lambda **_kwargs: _PassingStrictReport(),
    )

    @contextlib.contextmanager
    def external_client(
        base_url: str,
        _timeout_seconds: float | None = None,
    ) -> Iterator[_FakeExternalApiClient]:
        assert base_url == STRICT_API_BASE_URL
        yield client

    monkeypatch.setattr(harness_module, "_external_api_client", external_client)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            deal_stage="SERIES_A",
            root=root,
            api_base_url=STRICT_API_BASE_URL,
            require_full_live=True,
            strict_preflight_proof_id=STRICT_PREFLIGHT_PROOF_ID,
            run_poll_interval_seconds=0.001,
        )
    )

    assert summary["status"] == "succeeded"
    assert len(client.uploads) == 1
    assert len(client.run_requests) == 1
    assert client.run_gets == ["/v1/runs/run-private-1"] * 3
    assert client.run_requests == [
        {
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": ["document-1"],
            },
            "strict_preflight_proof_id": STRICT_PREFLIGHT_PROOF_ID,
        }
    ]


def test_private_harness_non_strict_behavior_still_uploads_and_runs(tmp_path: Path) -> None:
    """Non-strict private harness behavior must remain unchanged."""
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "public-safe.pdf").write_bytes(b"%PDF-1.4\nSAFE\n%%EOF")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(deal_stage="SERIES_A", root=root, api_client=client)
    )

    assert summary["status"] == "succeeded"
    assert len(client.uploads) == 1
    assert len(client.run_requests) == 1


class _PassingStrictReport:
    may_proceed = True

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "required": True,
            "may_proceed": True,
            "blocker_count": 0,
            "blocking_components": [],
            "components": [],
        }


def _api_keys() -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": TENANT_ID,
            "actor_id": "actor-a",
            "name": "Tenant A",
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
