"""Slice70 bounded synthetic non-strict FULL execution rehearsal tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from idis.models.run_step import FULL_STEPS, StepName


def _fake_upload_and_execution_result(
    **_kwargs: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {
            "uploaded_case_count": 1,
            "uploaded_document_count": 2,
            "artifact_types": ["FIN_MODEL", "PITCH_DECK"],
            "artifact_formats": [".pdf", ".xlsx"],
            "uploaded_documents": [
                {
                    "document_id": "synthetic-doc-1",
                    "doc_type": "PITCH_DECK",
                    "format": ".pdf",
                    "sha256": "0" * 64,
                    "status": "PARSED",
                },
                {
                    "document_id": "synthetic-doc-2",
                    "doc_type": "FINANCIAL_MODEL",
                    "format": ".xlsx",
                    "sha256": "1" * 64,
                    "status": "PARSED",
                },
            ],
        },
        {
            "enabled": True,
            "status": "completed",
            "reason_code": None,
            "http_status_code": 202,
            "run_created": True,
            "run_id": "synthetic-run-id",
            "strict_full_live_required": False,
            "terminal": True,
            "run_status": "SUCCEEDED",
            "status_run_status": "SUCCEEDED",
            "completed_step_count": len(FULL_STEPS),
            "completed_step_names": [step.value for step in FULL_STEPS],
            "expected_completed_step_count": len(FULL_STEPS),
            "expected_completed_step_names": [step.value for step in FULL_STEPS],
            "failed_step_names": [],
        },
        {
            "status": "not_created",
            "verified": False,
            "reason_code": "SAME_RUN_PACKAGE_SURFACES_NOT_CREATED",
            "listed_deliverable_count_for_run": 0,
            "manifest_http_status_code": 404,
            "downloaded_artifact_count": 0,
        },
    )


def test_synthetic_full_execution_rehearsal_runs_one_case_through_all_full_steps(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Bounded rehearsal should prove non-strict FULL execution, not package export."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    def fail_if_package_helper_called(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("same-run package surfaces are absent; do not call verifier")

    monkeypatch.setattr(rehearsal, "verify_package_surfaces", fail_if_package_helper_called)

    report = rehearsal.build_bounded_synthetic_full_execution_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_execution=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["strict_global_may_proceed"] is False
    assert report["approval_evidence"] is False
    assert report["api_upload_rehearsal"]["uploaded_case_count"] == 1
    assert report["api_upload_rehearsal"]["uploaded_document_count"] == 2

    execution = report["full_execution_rehearsal"]
    assert execution["enabled"] is True
    assert execution["http_status_code"] == 202
    assert execution["run_created"] is True
    assert execution["run_status"] == "SUCCEEDED"
    assert execution["status_run_status"] == "SUCCEEDED"
    assert execution["terminal"] is True
    assert execution["completed_step_count"] == len(FULL_STEPS) == 28
    assert execution["completed_step_names"] == [step.value for step in FULL_STEPS]
    assert execution["failed_step_names"] == []
    assert StepName.DELIVERABLES.value in execution["completed_step_names"]

    package = report["package_surface_verification"]
    assert report["package_surface_status"] == "not_created"
    assert package == {
        "status": "not_created",
        "verified": False,
        "reason_code": "SAME_RUN_PACKAGE_SURFACES_NOT_CREATED",
        "listed_deliverable_count_for_run": 0,
        "manifest_http_status_code": 404,
        "downloaded_artifact_count": 0,
    }
    assert "package_surface_verified" not in report
    assert "vc-ready" not in serialized.lower()
    assert report["real_example_not_run"] is True


def test_synthetic_full_execution_rehearsal_is_non_strict_and_not_runtime_proof(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Execution rehearsal must not clear strict readiness or claim live runtime proof."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    monkeypatch.setattr(
        rehearsal,
        "_upload_and_execute_non_strict_full_via_api",
        _fake_upload_and_execution_result,
    )

    report = rehearsal.build_bounded_synthetic_full_execution_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_execution=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["runtime_proof_required"] is True
    assert report["strict_global_may_proceed"] is False
    assert report["full_execution_rehearsal"]["strict_full_live_required"] is False
    assert "runtime_proof_claim" not in serialized
    assert 'strict_global_may_proceed": true' not in serialized


def test_synthetic_full_execution_rehearsal_rejects_missing_execution_opt_in() -> None:
    """Non-strict execution rehearsal must require explicit execution permission."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    try:
        rehearsal.build_bounded_synthetic_full_execution_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=False,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_EXECUTION_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("execution rehearsal accepted missing execution opt-in")


def test_synthetic_full_execution_rehearsal_requires_max_cases_before_api_posts(
    monkeypatch: Any,
) -> None:
    """Missing max_cases should fail before upload or run API posts."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[str] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append(url)
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    try:
        rehearsal.build_bounded_synthetic_full_execution_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=None,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=True,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_MAX_CASES_REQUIRED" in str(exc)
    else:
        raise AssertionError("execution rehearsal accepted missing max_cases")
    assert not any("/documents/upload" in url or url.endswith("/runs") for url in posts)


def test_synthetic_full_execution_rehearsal_requires_upload_opt_in_before_api_posts(
    monkeypatch: Any,
) -> None:
    """Missing upload opt-in should fail before upload or run API posts."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[str] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append(url)
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    try:
        rehearsal.build_bounded_synthetic_full_execution_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=False,
            allow_synthetic_execution=True,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_API_UPLOAD_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("execution rehearsal accepted missing upload opt-in")
    assert not any("/documents/upload" in url or url.endswith("/runs") for url in posts)


def test_synthetic_full_execution_rehearsal_rejects_multi_case_before_api_posts(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Slice70 is intentionally bounded to one synthetic FULL execution."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    posts: list[str] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        posts.append(url)
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)

    try:
        rehearsal.build_bounded_synthetic_full_execution_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=2,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=True,
            object_store_base_dir=tmp_path / "objects",
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_EXECUTION_SINGLE_CASE_ONLY" in str(exc)
    else:
        raise AssertionError("execution rehearsal accepted multiple cases")
    assert not any("/documents/upload" in url or url.endswith("/runs") for url in posts)


def test_synthetic_full_execution_rehearsal_rejects_non_gdbs_root() -> None:
    """Full execution helper must stay scoped to the approved GDBS corpus."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    try:
        rehearsal.build_bounded_synthetic_full_execution_rehearsal(
            dataset_root=Path("real_example"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=True,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_GDBS_ONLY" in str(exc)
    else:
        raise AssertionError("execution rehearsal accepted non-GDBS root")


def test_synthetic_full_execution_rehearsal_report_has_no_private_surface_leakage(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Execution rehearsal report should stay reason-code and metadata only."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    monkeypatch.setattr(
        rehearsal,
        "_upload_and_execute_non_strict_full_via_api",
        _fake_upload_and_execution_result,
    )

    report = rehearsal.build_bounded_synthetic_full_execution_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_execution=True,
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


def test_synthetic_full_execution_rehearsal_isolates_ambient_runtime_env(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Ambient strict, DB, dotenv, and object-store env must be restored."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    class FakeTestClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeTestClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(rehearsal, "create_app", lambda **_kwargs: object())
    monkeypatch.setattr(rehearsal, "TestClient", FakeTestClient)
    monkeypatch.setattr(
        rehearsal,
        "_upload_selected_gdbs_cases_for_run_with_client",
        lambda **_kwargs: (
            {
                "uploaded_case_count": 1,
                "uploaded_document_count": 1,
                "artifact_types": ["PITCH_DECK"],
                "artifact_formats": [".pdf"],
                "uploaded_documents": [{"document_id": "synthetic-doc-id"}],
            },
            "synthetic-deal-id",
        ),
    )
    monkeypatch.setattr(
        rehearsal,
        "_execute_non_strict_full_run_with_client",
        lambda **_kwargs: _fake_upload_and_execution_result()[1],
    )
    monkeypatch.setattr(
        rehearsal,
        "_same_run_package_surface_status",
        lambda **_kwargs: _fake_upload_and_execution_result()[2],
    )

    ambient_database_url = "postgresql://slice70:secret@127.0.0.1:1/idis"
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
        "IDIS_REQUIRE_FULL_LIVE=1\n"
        "IDIS_DATABASE_URL=postgresql://private:secret@127.0.0.1:1/private\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_STRICT_DOTENV_PATH", str(private_dotenv))
    monkeypatch.setenv("IDIS_DATABASE_URL", ambient_database_url)
    monkeypatch.setenv("IDIS_API_KEYS", ambient_api_keys)
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "ambient-objects"))

    report = rehearsal.build_bounded_synthetic_full_execution_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_execution=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["full_execution_rehearsal"]["run_status"] == "SUCCEEDED"
    assert report["full_execution_rehearsal"]["strict_full_live_required"] is False
    assert os.environ["IDIS_REQUIRE_FULL_LIVE"] == "1"
    assert os.environ["IDIS_STRICT_DOTENV_PATH"] == str(private_dotenv)
    assert os.environ["IDIS_DATABASE_URL"] == ambient_database_url
    assert os.environ["IDIS_API_KEYS"] == ambient_api_keys
    assert os.environ["IDIS_OBJECT_STORE_BACKEND"] == "s3"
    assert os.environ["IDIS_OBJECT_STORE_BASE_DIR"] == str(tmp_path / "ambient-objects")
    assert "private.env" not in serialized
    assert "ambient-key" not in serialized
    assert "secret" not in serialized.lower()


def test_synthetic_full_execution_rehearsal_isolates_ambient_live_provider_env(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Ambient live model, vector, and enrichment env must not affect rehearsal."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    class FakeTestClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeTestClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    live_env = {
        "ANTHROPIC_API_KEY": "anthropic-secret-slice70",
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "IDIS_DEBATE_BACKEND": "anthropic",
        "OPENAI_API_KEY": "openai-secret-slice70",
        "IDIS_ENABLE_VECTOR_SEARCH": "1",
        "FINNHUB_API_KEY": "finnhub-secret-slice70",
        "FMP_API_KEY": "fmp-secret-slice70",
    }
    observed_during_execution: dict[str, str | None] = {}

    def fake_execute(**_kwargs: Any) -> dict[str, Any]:
        observed_during_execution.update({key: os.environ.get(key) for key in live_env})
        return _fake_upload_and_execution_result()[1]

    monkeypatch.setattr(rehearsal, "create_app", lambda **_kwargs: object())
    monkeypatch.setattr(rehearsal, "TestClient", FakeTestClient)
    monkeypatch.setattr(
        rehearsal,
        "_upload_selected_gdbs_cases_for_run_with_client",
        lambda **_kwargs: (
            {
                "uploaded_case_count": 1,
                "uploaded_document_count": 1,
                "artifact_types": ["PITCH_DECK"],
                "artifact_formats": [".pdf"],
                "uploaded_documents": [{"document_id": "synthetic-doc-id"}],
            },
            "synthetic-deal-id",
        ),
    )
    monkeypatch.setattr(rehearsal, "_execute_non_strict_full_run_with_client", fake_execute)
    monkeypatch.setattr(
        rehearsal,
        "_same_run_package_surface_status",
        lambda **_kwargs: _fake_upload_and_execution_result()[2],
    )
    for key, value in live_env.items():
        monkeypatch.setenv(key, value)

    report = rehearsal.build_bounded_synthetic_full_execution_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_execution=True,
        object_store_base_dir=tmp_path / "objects",
    )
    serialized = json.dumps(report, sort_keys=True).lower()

    assert observed_during_execution == {
        "ANTHROPIC_API_KEY": None,
        "IDIS_EXTRACT_BACKEND": None,
        "IDIS_DEBATE_BACKEND": None,
        "OPENAI_API_KEY": None,
        "IDIS_ENABLE_VECTOR_SEARCH": "0",
        "FINNHUB_API_KEY": None,
        "FMP_API_KEY": None,
    }
    for key, value in live_env.items():
        assert os.environ[key] == value
        assert key.lower() not in serialized
        if key != "IDIS_ENABLE_VECTOR_SEARCH":
            assert value.lower() not in serialized
