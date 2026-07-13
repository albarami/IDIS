"""Slice69 bounded synthetic API run-attempt rehearsal tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal
from tests.abac_seed import seed_deal_access

# actor_id from the rehearsal's own api-keys config
# (see _upload_and_attempt_strict_blocked_run_via_api).
_SYNTHETIC_RUN_ACTOR_ID = "slice69-synthetic-api-run"


@pytest.fixture(autouse=True)
def _seed_synthetic_deal_access(monkeypatch: Any) -> None:
    """Grant the synthetic rehearsal actor a deal assignment right after each deal is created.

    The bounded run rehearsal creates its deal inside the shared production helper and then uploads
    to /documents/upload and posts to /runs - both deal-scoped and, after the Slice98 Task 2.5 ABAC
    fix, deny-by-default. Deal creation is not reachable from the test to seed inline, so we observe
    the /v1/deals 201 response through the same TestClient.post seam the rehearsal tests already use
    and seed via the SAME default store the middleware consults (get_deal_assignment_store, Task
    2.6). Test-only: no production change, no policy weakening, no side store. The run itself is
    still expected to be strict-blocked (409 STRICT_FULL_LIVE_BLOCKED) once ABAC passes.
    """
    original_post = rehearsal.TestClient.post

    def seeding_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        response = original_post(self, url, *args, **kwargs)
        if url == "/v1/deals" and response.status_code == 201:
            try:
                deal_id = response.json()["deal_id"]
            except (ValueError, KeyError, TypeError):
                return response
            seed_deal_access(
                rehearsal.SYNTHETIC_API_REHEARSAL_TENANT_ID,
                deal_id,
                _SYNTHETIC_RUN_ACTOR_ID,
            )
        return response

    monkeypatch.setattr(rehearsal.TestClient, "post", seeding_post)


def test_synthetic_api_run_rehearsal_requires_explicit_run_permission() -> None:
    """Run-attempt rehearsal must not upload or run without explicit run opt-in."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    try:
        rehearsal.build_bounded_synthetic_api_run_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            allow_synthetic_run=False,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_RUN_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("run rehearsal accepted missing allow_synthetic_run")


def test_synthetic_api_run_rehearsal_requires_max_cases_before_api_posts(
    monkeypatch: Any,
) -> None:
    """Missing max_cases should fail before any public API post is attempted."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[str] = []

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append(url)
        return rehearsal.TestClient.post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    try:
        rehearsal.build_bounded_synthetic_api_run_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=None,
            allow_synthetic_api_upload=True,
            allow_synthetic_run=True,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_MAX_CASES_REQUIRED" in str(exc)
    else:
        raise AssertionError("run rehearsal accepted missing max_cases")
    assert posts == []


def test_synthetic_api_run_rehearsal_requires_upload_permission_before_api_posts(
    monkeypatch: Any,
) -> None:
    """Missing upload opt-in should fail before any public API post is attempted."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[str] = []

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append(url)
        return rehearsal.TestClient.post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    try:
        rehearsal.build_bounded_synthetic_api_run_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=False,
            allow_synthetic_run=True,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_API_UPLOAD_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("run rehearsal accepted missing upload opt-in")
    assert posts == []


def test_synthetic_api_run_rehearsal_rejects_multi_case_before_upload_or_run_posts(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Slice69 run attempts are intentionally one-case only."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[str] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append(url)
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    try:
        rehearsal.build_bounded_synthetic_api_run_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=2,
            allow_synthetic_api_upload=True,
            allow_synthetic_run=True,
            object_store_base_dir=tmp_path / "objects",
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_RUN_SINGLE_CASE_ONLY" in str(exc)
    else:
        raise AssertionError("run rehearsal accepted multiple cases")
    assert not any("/documents/upload" in url or url.endswith("/runs") for url in posts)


def test_synthetic_api_run_rehearsal_uses_public_run_api_with_deal_documents(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Run attempt should use public /runs with durable uploaded document IDs only."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[tuple[str, Any]] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append((url, kwargs.get("json")))
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    report = rehearsal.build_bounded_synthetic_api_run_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_run=True,
        object_store_base_dir=tmp_path / "objects",
    )

    run_posts = [(url, payload) for url, payload in posts if url.endswith("/runs")]
    assert len(run_posts) == 1
    run_url, run_payload = run_posts[0]
    uploaded_ids = [
        item["document_id"] for item in report["api_upload_rehearsal"]["uploaded_documents"]
    ]
    assert run_url.startswith("/v1/deals/")
    assert run_payload == {
        "mode": "FULL",
        "source": {
            "type": "deal_documents",
            "document_ids": uploaded_ids,
        },
    }
    assert all(
        "/documents/upload" in url or url == "/v1/deals" or url.endswith("/runs")
        for url, _payload in posts
    )
    assert not any("file://" in json.dumps(payload or {}) for _url, payload in posts)


def test_synthetic_api_run_rehearsal_reports_strict_block_without_run_creation(
    tmp_path: Path,
) -> None:
    """First Slice69 path should report strict FULL blocking, not runtime proof."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_run_rehearsal,
    )

    report = build_bounded_synthetic_api_run_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_run=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["strict_global_may_proceed"] is False
    assert report["approval_evidence"] is False
    assert report["api_upload_rehearsal"]["uploaded_document_count"] == 2
    assert report["run_attempt"] == {
        "enabled": True,
        "status": "blocked",
        "reason_code": "STRICT_FULL_LIVE_BLOCKED",
        "http_status_code": 409,
        "run_created": False,
    }
    assert report["strict_runtime_blocked_reason_code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert "run_id" not in report["run_attempt"]
    assert report["package_surface_verification"] == {
        "status": "not_run",
        "verified": False,
    }
    assert "package_surface_verified" not in report
    assert "vc-ready" not in serialized.lower()
    assert report["runtime_proof_required"] is True
    assert "runtime_proof_claim" not in serialized


def test_synthetic_api_run_rehearsal_does_not_claim_block_when_run_is_accepted(
    monkeypatch: Any,
) -> None:
    """Unexpected run acceptance should be reported as failure, not strict-block proof."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    def fake_upload_and_run(**_kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {
                "uploaded_case_count": 1,
                "uploaded_document_count": 1,
                "artifact_types": ["Financials"],
                "artifact_formats": [".pdf"],
                "uploaded_documents": [
                    {
                        "document_id": "synthetic-doc-id",
                        "doc_type": "FINANCIAL",
                        "format": ".pdf",
                        "sha256": "0" * 64,
                        "status": "PARSED",
                    }
                ],
            },
            {
                "enabled": True,
                "status": "failed_safe",
                "reason_code": "SYNTHETIC_RUN_UNEXPECTEDLY_ACCEPTED",
                "http_status_code": 202,
                "run_created": True,
            },
        )

    monkeypatch.setattr(
        rehearsal,
        "_upload_and_attempt_strict_blocked_run_via_api",
        fake_upload_and_run,
    )

    report = rehearsal.build_bounded_synthetic_api_run_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_run=True,
    )

    assert report["run_attempt"]["status"] == "failed_safe"
    assert report["run_attempt"]["reason_code"] == "SYNTHETIC_RUN_UNEXPECTEDLY_ACCEPTED"
    assert report["run_attempt"]["run_created"] is True
    assert "strict_runtime_blocked_reason_code" not in report


def test_synthetic_api_run_rehearsal_marks_accepted_run_as_created() -> None:
    """202 Accepted is the run-created risk path and must not be hidden."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        _attempt_strict_blocked_run_with_client,
    )

    class FakeResponse:
        status_code = 202

        def json(self) -> dict[str, str]:
            return {"run_id": "unexpected-run-id"}

    class FakeClient:
        def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            return FakeResponse()

    run_attempt = _attempt_strict_blocked_run_with_client(
        client=cast(Any, FakeClient()),
        deal_id="synthetic-deal-id",
        document_ids=["synthetic-doc-id"],
    )

    assert run_attempt == {
        "enabled": True,
        "status": "failed_safe",
        "reason_code": "SYNTHETIC_RUN_UNEXPECTEDLY_ACCEPTED",
        "http_status_code": 202,
        "run_created": True,
    }
    assert "run_id" not in run_attempt


def test_synthetic_api_run_rehearsal_isolates_ambient_run_env(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Ambient local env must not make strict run pass, hit DB, or load private dotenv."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_run_rehearsal,
    )

    ambient_database_url = "postgresql://slice69:secret@127.0.0.1:1/idis"
    ambient_api_keys = json.dumps(
        {
            "ambient-key": {
                "tenant_id": "99999999-9999-4999-8999-999999999999",
                "actor_id": "ambient-actor",
                "name": "Ambient",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": ["ADMIN"],
            }
        }
    )
    private_dotenv = tmp_path / "private.env"
    private_dotenv.write_text(
        "IDIS_REQUIRE_FULL_LIVE=0\n"
        "IDIS_DATABASE_URL=postgresql://private:secret@127.0.0.1:1/private\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.setenv("IDIS_STRICT_DOTENV_PATH", str(private_dotenv))
    monkeypatch.setenv("IDIS_DATABASE_URL", ambient_database_url)
    monkeypatch.setenv("IDIS_API_KEYS", ambient_api_keys)
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "ambient-objects"))

    report = build_bounded_synthetic_api_run_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_run=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["run_attempt"]["reason_code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert os.environ["IDIS_REQUIRE_FULL_LIVE"] == "0"
    assert os.environ["IDIS_STRICT_DOTENV_PATH"] == str(private_dotenv)
    assert os.environ["IDIS_DATABASE_URL"] == ambient_database_url
    assert os.environ["IDIS_API_KEYS"] == ambient_api_keys
    assert os.environ["IDIS_OBJECT_STORE_BACKEND"] == "s3"
    assert os.environ["IDIS_OBJECT_STORE_BASE_DIR"] == str(tmp_path / "ambient-objects")
    assert "private.env" not in serialized
    assert "ambient-key" not in serialized
    assert "secret" not in serialized.lower()


def test_synthetic_api_run_rehearsal_report_has_no_private_surface_leakage(
    tmp_path: Path,
) -> None:
    """Run rehearsal report should remain reason-code style and leakage-safe."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_run_rehearsal,
    )

    report = build_bounded_synthetic_api_run_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_run=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert "C:\\Projects" not in serialized
    assert "datasets/gdbs_full" not in serialized
    assert "file://datasets/gdbs_full" not in serialized
    assert "pitch_deck.pdf" not in serialized
    assert "financials.xlsx" not in serialized
    assert "object_key" not in serialized
    assert "raw_text" not in serialized
    assert "text_excerpt" not in serialized
    assert "spans" not in serialized
    assert "prompt_transcript" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized.lower()
    assert "secret" not in serialized.lower()
