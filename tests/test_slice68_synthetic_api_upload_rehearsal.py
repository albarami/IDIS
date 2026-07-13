"""Slice68 bounded synthetic API upload rehearsal tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal
from tests.abac_seed import seed_deal_access

# actor_id from the rehearsal's own api-keys config (see _upload_selected_gdbs_cases_via_api).
_SYNTHETIC_UPLOAD_ACTOR_ID = "slice68-synthetic-api-upload"


@pytest.fixture(autouse=True)
def _seed_synthetic_deal_access(monkeypatch: Any) -> None:
    """Grant the synthetic rehearsal actor a deal assignment right after each deal is created.

    The bounded rehearsal creates its deal inside the shared production helper and then uploads to
    the deal-scoped /documents/upload endpoint, which after the Slice98 Task 2.5 ABAC fix is
    deny-by-default. Deal creation is not reachable from the test to seed inline, so we observe the
    /v1/deals 201 response through the same TestClient.post seam the rehearsal tests already use and
    seed via the SAME default store the middleware consults (get_deal_assignment_store, Task 2.6).
    Test-only: no production change, no policy weakening, no side store. Tests that wrap
    TestClient.post themselves capture this seeding wrapper as their original and delegate to it, so
    seeding still happens.
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
                _SYNTHETIC_UPLOAD_ACTOR_ID,
            )
        return response

    monkeypatch.setattr(rehearsal.TestClient, "post", seeding_post)


def test_synthetic_api_upload_rehearsal_requires_explicit_max_cases() -> None:
    """API upload rehearsal must not default to the full GDBS-F corpus."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        SyntheticRehearsalScopeError,
        build_bounded_synthetic_api_upload_rehearsal,
    )

    try:
        build_bounded_synthetic_api_upload_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=None,
            allow_synthetic_api_upload=True,
        )
    except SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_MAX_CASES_REQUIRED" in str(exc)
    else:
        raise AssertionError("API upload rehearsal accepted missing max_cases")


def test_synthetic_api_upload_rehearsal_requires_explicit_upload_permission() -> None:
    """Upload must be opt-in, even for synthetic data."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        SyntheticRehearsalScopeError,
        build_bounded_synthetic_api_upload_rehearsal,
    )

    try:
        build_bounded_synthetic_api_upload_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=False,
        )
    except SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_API_UPLOAD_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("API upload rehearsal ran without explicit upload permission")


def test_synthetic_api_upload_rehearsal_rejects_non_gdbs_root() -> None:
    """The API rehearsal must stay scoped to repo-local GDBS-F only."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        SyntheticRehearsalScopeError,
        build_bounded_synthetic_api_upload_rehearsal,
    )

    try:
        build_bounded_synthetic_api_upload_rehearsal(
            dataset_root=Path("tests/fixtures/gdbs_mini"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
        )
    except SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_GDBS_ONLY" in str(exc)
    else:
        raise AssertionError("non-GDBS root was accepted")


def test_synthetic_api_upload_rehearsal_uploads_via_public_upload_only(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Bounded rehearsal should upload synthetic bytes through /documents/upload only."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    requests: list[tuple[str, str]] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        requests.append(("POST", url))
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    report = rehearsal.build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        object_store_base_dir=tmp_path / "objects",
    )

    assert requests
    assert any(method == "POST" and url == "/v1/deals" for method, url in requests)
    assert all("documents/upload" in url or url == "/v1/deals" for _method, url in requests)
    assert not any(url.endswith("/documents") for _method, url in requests)
    assert not any("/runs" in url for _method, url in requests)
    assert report["api_upload_rehearsal"]["uploaded_document_count"] == 2
    assert report["run_attempt"]["status"] == "not_run"
    assert report["package_surface_verification"]["status"] == "not_run"


def test_synthetic_api_upload_rehearsal_reports_safe_uploaded_document_summaries(
    tmp_path: Path,
) -> None:
    """Uploaded document summaries should expose only safe IDs/types/formats/SHA/statuses."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_upload_rehearsal,
    )

    report = build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["strict_global_may_proceed"] is False
    assert report["approval_evidence"] is False
    upload_report = report["api_upload_rehearsal"]
    assert upload_report["enabled"] is True
    assert upload_report["requested_case_count"] == 1
    assert upload_report["uploaded_case_count"] == 1
    assert upload_report["uploaded_document_count"] == 2
    assert upload_report["selected_case_ids"] == ["deal_001"]
    assert upload_report["artifact_types"] == ["FIN_MODEL", "PITCH_DECK"]
    assert upload_report["artifact_formats"] == [".pdf", ".xlsx"]
    assert len(upload_report["uploaded_documents"]) == 2
    assert all(
        set(item) == {"document_id", "doc_type", "format", "sha256", "status"}
        for item in upload_report["uploaded_documents"]
    )
    assert all(len(item["sha256"]) == 64 for item in upload_report["uploaded_documents"])
    assert {item["status"] for item in upload_report["uploaded_documents"]} == {"PARSED"}
    assert "bounded_inspection" not in report
    assert "executed_case_count" not in serialized
    assert "runtime_rehearsal" not in serialized
    assert "C:\\Projects" not in serialized
    assert "datasets/gdbs_full" not in serialized
    assert "file://datasets/gdbs_full" not in serialized
    assert "datasets/gdbs_full/deals" not in serialized
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


def test_synthetic_api_upload_rehearsal_replaces_dataset_path_with_safe_label(
    tmp_path: Path,
) -> None:
    """The upload report should identify the corpus without path-like dataset roots."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_upload_rehearsal,
    )

    report = build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["dataset"]["dataset_id"] == "gdbs-f"
    assert "dataset_root" not in report["dataset"]
    assert "datasets/gdbs_full" not in serialized


def test_synthetic_api_upload_rehearsal_isolates_ambient_object_store_backend(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Ambient S3 backend config must not escape into synthetic upload rehearsal."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_upload_rehearsal,
    )

    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "s3")

    report = build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        object_store_base_dir=tmp_path / "objects",
    )

    assert report["api_upload_rehearsal"]["uploaded_document_count"] == 2
    assert {item["status"] for item in report["api_upload_rehearsal"]["uploaded_documents"]} == {
        "PARSED"
    }
    assert os.environ["IDIS_OBJECT_STORE_BACKEND"] == "s3"


def test_synthetic_api_upload_rehearsal_isolates_ambient_database_url(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Ambient Postgres config must not make synthetic upload touch durable DB paths."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_upload_rehearsal,
    )

    ambient_database_url = "postgresql://slice68:secret@127.0.0.1:1/idis"
    monkeypatch.setenv("IDIS_DATABASE_URL", ambient_database_url)

    report = build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        object_store_base_dir=tmp_path / "objects",
    )

    assert report["api_upload_rehearsal"]["uploaded_document_count"] == 2
    assert report["run_attempt"]["status"] == "not_run"
    assert os.environ["IDIS_DATABASE_URL"] == ambient_database_url


def test_synthetic_api_upload_rehearsal_upload_failures_are_safe(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """HTTP upload failures should not expose URL query params or filenames."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    original_post = rehearsal.TestClient.post

    def failing_upload(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        if "documents/upload" not in url:
            return original_post(self, url, *args, **kwargs)
        request = httpx.Request(
            "POST",
            "http://testserver/v1/deals/deal/documents/upload?filename=pitch_deck.pdf&sha256="
            + ("a" * 64),
        )
        response = httpx.Response(400, request=request, json={"code": "BAD_REQUEST"})
        response.raise_for_status()
        raise AssertionError("unreachable")

    monkeypatch.setattr(rehearsal.TestClient, "post", failing_upload)

    try:
        rehearsal.build_bounded_synthetic_api_upload_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            object_store_base_dir=tmp_path / "objects",
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        message = str(exc)
        assert "SYNTHETIC_API_UPLOAD_FAILED" in message
        assert "pitch_deck.pdf" not in message
        assert "filename" not in message
        assert "sha256" not in message
        assert "http://testserver" not in message
    else:
        raise AssertionError("unsafe upload failure was not blocked")


def test_synthetic_api_upload_rehearsal_does_not_upload_when_not_allowed_even_if_strict_blocks(
    monkeypatch: Any,
) -> None:
    """Strict blockers plus disabled upload should not hit upload APIs."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    attempted_posts: list[str] = []

    def forbidden_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        attempted_posts.append(url)
        raise AssertionError("API upload must not be attempted")

    monkeypatch.setattr(rehearsal.TestClient, "post", forbidden_post)

    try:
        rehearsal.build_bounded_synthetic_api_upload_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=False,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_API_UPLOAD_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("API upload rehearsal ran without explicit upload permission")

    assert attempted_posts == []


def test_synthetic_api_upload_rehearsal_never_claims_package_verification_without_running_it(
    tmp_path: Path,
) -> None:
    """Upload-only rehearsal must not imply package-surface verification."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_upload_rehearsal,
    )

    report = build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        object_store_base_dir=tmp_path / "objects",
    )

    assert report["package_surface_verification"] == {
        "status": "not_run",
        "verified": False,
    }
    assert "package_surface_verified" not in report
