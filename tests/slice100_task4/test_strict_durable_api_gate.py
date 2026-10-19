"""Task 4 contract for the strict external-HTTP durable-ingestion proof.

The acceptance entry point must use the already-approved strict-launcher flags.  These
tests inject local protocol fakes only to prove orchestration and secret-safe reduction;
the production entry point itself constructs a real external ``httpx.Client``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from idis.evaluation import real_example_gate as gate
from idis.parsers import ocr


def _strict_source_render_plan(page_number: int) -> dict[str, object]:
    """Return the v2 plan expected before the strict renderer observes pixels."""

    return {
        "page": page_number,
        "schema_version": ocr.OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        "ladder_version": ocr.OCR_DPI_LADDER_VERSION,
        "configured_dpi": 200,
        "chosen_dpi": 200,
        "projected_width": 1_700,
        "projected_height": 2_200,
        "projected_pixel_count": 3_740_000,
        "expected_render_width": 1_700,
        "expected_render_height": 2_200,
        "expected_render_pixel_count": 3_740_000,
        "poppler_version": ocr.PINNED_POPPLER_VERSION,
        "poppler_runtime_identity_sha256": "8" * 64,
        "expected_dimension_policy_id": ocr.POPPLER_EXPECTED_DIMENSION_POLICY_ID,
        "expected_dimension_policy_sha256": ocr.POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
        "comparison_mode": ocr.POPPLER_EXACT_COMPARISON_MODE,
        "tolerance_pixels": 0,
        "tolerance_used": False,
        "max_pixel_count": ocr.MAX_OCR_IMAGE_PIXELS,
    }


def _strict_env() -> dict[str, str]:
    return {
        "IDIS_ACCEPTANCE_API_BASE_URL": "http://127.0.0.1:8000",
        "IDIS_REAL_EXAMPLE_API_KEY": "acceptance-secret-value",
        "IDIS_ISOLATION_CANARY_API_KEY": "canary-secret-value",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
        "IDIS_MEDIA_TIMEOUT_SECONDS": "900",
        "IDIS_INGEST_MAX_BYTES": "536870912",
        "IDIS_RATE_LIMIT_INTEGRATION_RPM": "1200",
        "IDIS_RATE_LIMIT_BURST_MULTIPLIER": "2",
    }


def test_existing_strict_launcher_flags_are_accepted_and_routed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[Any] = []
    reviewed_profile = object()
    monkeypatch.setattr(
        gate,
        "_run_durable_ingestion_cli",
        lambda args, *, strict_profile: observed.append((args, strict_profile)) or 0,
        raising=False,
    )

    result = gate.main(
        [
            "--root",
            str(tmp_path / "source"),
            "--api-base-url",
            "http://127.0.0.1:8000",
            "--api-key-env",
            "IDIS_REAL_EXAMPLE_API_KEY",
            "--canary-api-key-env",
            "IDIS_ISOLATION_CANARY_API_KEY",
            "--require-full-live",
            "--prepare-data-rooms",
            "--preflight-only",
            "--verify-tenant-isolation",
            "--company-mode",
            "isolated",
            "--expected-company-count",
            "2",
            "--expected-file-counts",
            "58,209",
            "--expected-total-files",
            "267",
            "--safe-report-dir",
            str(tmp_path / ".local_reports"),
            "--prepare-ledger",
            str(tmp_path / ".local_reports" / "prepare-ledger.jsonl"),
            "--no-secret-output",
        ],
        strict_profile=reviewed_profile,
    )

    assert result == 0
    assert len(observed) == 1
    args, received_profile = observed[0]
    assert received_profile is reviewed_profile
    assert args.prepare_data_rooms is True
    assert args.preflight_only is True
    assert args.verify_only is False
    assert args.verify_tenant_isolation is True
    assert args.api_base_url == "http://127.0.0.1:8000"
    gate._validate_durable_cli_mode(args)


def test_restart_and_isolation_flags_route_without_upload_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[Any] = []
    reviewed_profile = object()
    monkeypatch.setattr(
        gate,
        "_run_durable_ingestion_cli",
        lambda args, *, strict_profile: observed.append((args, strict_profile)) or 0,
        raising=False,
    )

    result = gate.main(
        [
            "--api-base-url",
            "http://127.0.0.1:8000",
            "--api-key-env",
            "IDIS_REAL_EXAMPLE_API_KEY",
            "--canary-api-key-env",
            "IDIS_ISOLATION_CANARY_API_KEY",
            "--require-full-live",
            "--verify-only",
            "--verify-restart-survival",
            "--verify-tenant-isolation",
            "--safe-report-dir",
            str(tmp_path / ".local_reports"),
            "--resume-ledger",
            str(tmp_path / ".local_reports" / "prepare-ledger.jsonl"),
            "--no-secret-output",
        ],
        strict_profile=reviewed_profile,
    )

    assert result == 0
    assert len(observed) == 1
    args, received_profile = observed[0]
    assert received_profile is reviewed_profile
    assert args.prepare_data_rooms is False
    assert args.verify_only is True
    assert args.verify_restart_survival is True
    assert args.verify_tenant_isolation is True
    assert args.company_mode == "isolated"
    gate._validate_durable_cli_mode(args)


def test_external_http_settings_are_finite_config_derived_and_secret_bound() -> None:
    settings = gate._build_durable_api_settings(
        env=_strict_env(),
        api_base_url="http://127.0.0.1:8000",
        api_key_env="IDIS_REAL_EXAMPLE_API_KEY",
        canary_api_key_env="IDIS_ISOLATION_CANARY_API_KEY",
    )

    assert settings.api_key == "acceptance-secret-value"
    assert settings.canary_api_key == "canary-secret-value"
    assert settings.connect_timeout_seconds == 10.0
    assert settings.write_timeout_seconds == 64.0
    assert settings.read_timeout_seconds == 930.0
    assert settings.pool_timeout_seconds == 10.0
    assert settings.request_interval_seconds == pytest.approx(0.05)
    assert all(
        math.isfinite(value) and value > 0
        for value in (
            settings.connect_timeout_seconds,
            settings.write_timeout_seconds,
            settings.read_timeout_seconds,
            settings.pool_timeout_seconds,
            settings.request_interval_seconds,
        )
    )
    assert "secret" not in repr(settings).lower()


def test_durable_gate_rejects_direct_invocation_without_reviewed_profile() -> None:
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._require_task4_gate_profile(None)

    assert exc_info.value.code == "STRICT_FULL_LIVE_BLOCKED"


def test_direct_durable_cli_fails_before_transport_without_launcher_profile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = gate.main(
        [
            "--root",
            str(tmp_path / "source"),
            "--api-base-url",
            "http://127.0.0.1:8000",
            "--api-key-env",
            "IDIS_REAL_EXAMPLE_API_KEY",
            "--canary-api-key-env",
            "IDIS_ISOLATION_CANARY_API_KEY",
            "--require-full-live",
            "--prepare-data-rooms",
            "--preflight-only",
            "--company-mode",
            "isolated",
            "--expected-file-counts",
            "58,209",
            "--safe-report-dir",
            str(tmp_path / ".local_reports"),
            "--prepare-ledger",
            str(tmp_path / ".local_reports" / "prepare-ledger.jsonl"),
            "--no-secret-output",
        ]
    )

    assert result == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "STRICT_FULL_LIVE_BLOCKED"


def test_durable_gate_requires_task4_bootstrap_generation_and_hashes_profile_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "require_runtime_profile", lambda _profile, *, command: None)
    profile = SimpleNamespace(
        command="gate",
        # The approved Task-4 amendment makes durable ingestion bootstrap-only;
        # final acceptance generation remains reserved for the full-live gate.
        generation="bootstrap-v1",
        process_role=None,
        base_profile_hmac="a" * 64,
        values={"IDIS_REQUIRE_PROMOTED_PROMPTS": "0"},
    )

    profile_hash = gate._require_task4_gate_profile(profile)

    assert profile_hash == hashlib.sha256(("a" * 64).encode("ascii")).hexdigest()
    profile.generation = "acceptance-v1"
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._require_task4_gate_profile(profile)
    assert exc_info.value.code == "STRICT_FULL_LIVE_BLOCKED"


def test_local_ocr_media_and_real_inference_are_required_before_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")

    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        unavailable,
    )
    monkeypatch.setattr(
        gate,
        "check_ocr_health",
        lambda **_kwargs: SimpleNamespace(status="healthy", enabled=True),
    )
    monkeypatch.setattr(
        gate,
        "check_media_health",
        lambda **_kwargs: SimpleNamespace(status="healthy", enabled=True),
    )
    monkeypatch.setattr(
        gate,
        "check_media_inference_health",
        lambda **_kwargs: SimpleNamespace(
            status="healthy",
            may_proceed=True,
            fixture_sha256=gate.PINNED_STT_FIXTURE_SHA256,
            manifest_sha256=gate.PINNED_STT_MANIFEST_SHA256,
            model_path_sha256="d" * 64,
            model_files_sha256="e" * 64,
            segment_count=1,
            first_start_ms=0,
            final_end_ms=2000,
        ),
    )
    monkeypatch.setattr(
        gate,
        "check_media_frame_ocr_health",
        lambda **_kwargs: SimpleNamespace(
            status="healthy",
            may_proceed=True,
            fixture_sha256=gate.PINNED_FRAME_OCR_FIXTURE_SHA256,
            manifest_sha256=gate.PINNED_FRAME_OCR_MANIFEST_SHA256,
            config_sha256="f" * 64,
            receipt_schema_version="media-frame-ocr-receipt-v1",
            sampling_policy="periodic-plus-scenes-v1",
            duration_ms=3000,
            scheduled_sample_count=1,
            completed_sample_count=1,
            nonempty_sample_count=0,
            evidence_span_count=0,
            schedule_sha256="1" * 64,
        ),
    )

    digest = gate._require_task4_local_runtime_readiness(env={})

    assert len(digest) == 64
    monkeypatch.setattr(
        gate,
        "check_ocr_health",
        lambda **_kwargs: SimpleNamespace(status="missing_dependencies", enabled=True),
    )
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._require_task4_local_runtime_readiness(env={})
    assert exc_info.value.code == "STRICT_FULL_LIVE_BLOCKED"

    monkeypatch.setattr(
        gate,
        "check_ocr_health",
        lambda **_kwargs: SimpleNamespace(status="healthy", enabled=True),
    )
    monkeypatch.setattr(
        gate,
        "check_media_frame_ocr_health",
        lambda **_kwargs: SimpleNamespace(
            status="failed",
            may_proceed=False,
            error_code="MEDIA_FRAME_OCR_PROBE_FAILED",
        ),
    )
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._require_task4_local_runtime_readiness(env={})
    assert exc_info.value.code == "STRICT_FULL_LIVE_BLOCKED"


@pytest.mark.parametrize(
    ("api_key_env", "canary_api_key_env", "expected_code"),
    [
        ("UNREVIEWED_API_KEY", "IDIS_ISOLATION_CANARY_API_KEY", "AUTH_ACCEPTANCE_KEY_MISMATCH"),
        ("IDIS_REAL_EXAMPLE_API_KEY", "UNREVIEWED_CANARY_KEY", "AUTH_CANARY_KEY_MISMATCH"),
    ],
)
def test_external_http_settings_reject_unapproved_secret_names(
    api_key_env: str,
    canary_api_key_env: str,
    expected_code: str,
) -> None:
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._build_durable_api_settings(
            env=_strict_env(),
            api_base_url="http://127.0.0.1:8000",
            api_key_env=api_key_env,
            canary_api_key_env=canary_api_key_env,
        )
    assert exc_info.value.code == expected_code


def test_deterministic_request_pacer_uses_admitted_sustained_rpm() -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = gate._DeterministicRequestPacer(
        interval_seconds=60.0 / 1200,
        clock=lambda: now[0],
        sleeper=sleep,
    )
    pacer.wait()
    pacer.wait()
    now[0] += 0.1
    pacer.wait()

    assert sleeps == [pytest.approx(0.05)]


def test_burst_capacity_does_not_increase_sustained_gate_rate() -> None:
    baseline = _strict_env()
    expanded = {**baseline, "IDIS_RATE_LIMIT_BURST_MULTIPLIER": "7"}

    baseline_settings = gate._build_durable_api_settings(
        env=baseline,
        api_base_url="http://127.0.0.1:8000",
        api_key_env="IDIS_REAL_EXAMPLE_API_KEY",
        canary_api_key_env="IDIS_ISOLATION_CANARY_API_KEY",
    )
    expanded_settings = gate._build_durable_api_settings(
        env=expanded,
        api_base_url="http://127.0.0.1:8000",
        api_key_env="IDIS_REAL_EXAMPLE_API_KEY",
        canary_api_key_env="IDIS_ISOLATION_CANARY_API_KEY",
    )

    assert baseline_settings.request_interval_seconds == expanded_settings.request_interval_seconds


def _terminal_row(*, private_token: str, terminal_status: str = "SUCCEEDED") -> dict[str, Any]:
    return {
        "company_alias": "company-001",
        "deal_id": "10000000-0000-4000-8000-000000000001",
        "source_sequence": 0,
        "generated_upload_name": "company-001-document-00001.pdf",
        "document_id": "20000000-0000-4000-8000-000000000001",
        "doc_id": "30000000-0000-4000-8000-000000000001",
        "content_sha256": "a" * 64,
        "size_bytes": 42,
        "extension": "pdf",
        "parsed_doc_type": "PDF",
        "terminal_status": terminal_status,
        "file_status": "SUPPORTED",
        "support_status": "supported",
        "triage_status": "ready",
        "parse_status": "PARSED",
        "reason_codes": ["pdf_parse_completed"],
        "warning_codes": [],
        "error_codes": [],
        "ocr_performed": False,
        "media_transcription_performed": False,
        "bounded_streaming_verified": False,
        "private_path": private_token,
        "text_excerpt": private_token,
        "durable_object_integrity": {
            "schema_version": "durable-object-integrity-v1",
            "content_sha256": "a" * 64,
            "size_bytes": 42,
            "uri_sha256": "b" * 64,
            "version_id": "40000000-0000-4000-8000-000000000001",
            "backend": "filesystem-v1",
            "streaming_verified": True,
        },
        "durable_spans": [
            {
                "span_id": "50000000-0000-4000-8000-000000000001",
                "document_id": "20000000-0000-4000-8000-000000000001",
                "span_type": "PAGE_TEXT",
                "locator": {"locator_sha256": "c" * 64},
                "content_hash": "d" * 64,
                "text_excerpt": private_token,
            }
        ],
    }


def test_reduced_digest_omits_private_values_but_binds_durable_identities() -> None:
    first = gate._canonical_reduced_terminal_rows_sha256(
        [_terminal_row(private_token=r"C:\private\alpha.pdf")]
    )
    changed_private = _terminal_row(private_token=r"D:\secret\beta.pdf")
    changed_private["generated_upload_name"] = "private-company-name.pdf"
    second = gate._canonical_reduced_terminal_rows_sha256([changed_private])
    changed_identity = _terminal_row(private_token="anything")
    changed_identity["deal_id"] = "10000000-0000-4000-8000-000000000009"
    changed_identity["document_id"] = "20000000-0000-4000-8000-000000000009"
    changed_identity["doc_id"] = "30000000-0000-4000-8000-000000000009"
    changed_identity["durable_object_integrity"]["version_id"] = (
        "40000000-0000-4000-8000-000000000009"
    )
    changed_identity["durable_spans"][0]["span_id"] = "50000000-0000-4000-8000-000000000009"
    changed_identity_digest = gate._canonical_reduced_terminal_rows_sha256([changed_identity])
    changed_state = gate._canonical_reduced_terminal_rows_sha256(
        [_terminal_row(private_token="anything", terminal_status="PARTIAL")]
    )

    assert first == second
    assert first != changed_identity_digest
    assert first != changed_state


def test_restart_equivalence_requires_zero_uploads_and_identical_frozen_digests() -> None:
    baseline = {
        "phase": "prepared",
        "restart_survival_verified": False,
        "previous_api_boot_sha256": None,
        "api_boot_sha256": "9" * 64,
        "source_tree_fingerprint": "a" * 64,
        "preparation_ledger_sha256": "b" * 64,
        "terminal_rows_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "code_sha256": "e" * 64,
        "reconciliation_sha256": "1" * 64,
        "profile_hmac_sha256": "2" * 64,
        "local_runtime_readiness_sha256": "3" * 64,
        "authenticated_download_receipts_sha256": "4" * 64,
        "tenant_isolation_receipt_sha256": "5" * 64,
    }
    current = {**baseline, "api_boot_sha256": "8" * 64}
    gate._verify_restart_equivalence(
        baseline=baseline,
        current=current,
        upload_post_count=0,
    )

    with pytest.raises(gate.DurableIngestionGateError) as no_restart_error:
        gate._verify_restart_equivalence(
            baseline=baseline,
            current=dict(baseline),
            upload_post_count=0,
        )
    assert no_restart_error.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"

    with pytest.raises(gate.DurableIngestionGateError) as upload_error:
        gate._verify_restart_equivalence(
            baseline=baseline,
            current=current,
            upload_post_count=1,
        )
    assert upload_error.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"

    drifted = dict(current)
    drifted["terminal_rows_sha256"] = "f" * 64
    with pytest.raises(gate.DurableIngestionGateError) as drift_error:
        gate._verify_restart_equivalence(
            baseline=baseline,
            current=drifted,
            upload_post_count=0,
        )
    assert drift_error.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_v6_restart_equivalence_freezes_terminal_snapshot_component_digests() -> None:
    components = {
        section: hashlib.sha256(section.encode("ascii")).hexdigest()
        for section in ("documents", "artifacts", "spans", "censuses", "audit_counts")
    }
    top_digest = gate._canonical_snapshot_sha256(
        {
            "schema_version": "task4-terminal-snapshot-digests-v1",
            "component_sha256": components,
        }
    )
    baseline = {
        "schema_version": "slice100-task4-durable-ingestion-proof-v6",
        "phase": "prepared",
        "restart_survival_verified": False,
        "previous_api_boot_sha256": None,
        "api_boot_sha256": hashlib.sha256(b"first-api-boot").hexdigest(),
        "source_tree_fingerprint": hashlib.sha256(b"source-tree").hexdigest(),
        "preparation_ledger_sha256": hashlib.sha256(b"ledger").hexdigest(),
        "terminal_rows_sha256": top_digest,
        "terminal_snapshot_digests": {
            "schema_version": "task4-terminal-snapshot-digests-v1",
            "terminal_rows_sha256": top_digest,
            "component_sha256": components,
        },
        "plan_sha256": hashlib.sha256(b"plan").hexdigest(),
        "code_sha256": hashlib.sha256(b"code").hexdigest(),
        "reconciliation_sha256": hashlib.sha256(b"reconciliation").hexdigest(),
        "profile_hmac_sha256": hashlib.sha256(b"profile").hexdigest(),
        "local_runtime_readiness_sha256": hashlib.sha256(b"readiness").hexdigest(),
        "no_substantive_content_attestation_sha256": hashlib.sha256(b"attestation").hexdigest(),
        "confirmed_no_substantive_content_receipts_sha256": hashlib.sha256(
            b"terminal-receipts"
        ).hexdigest(),
        "authenticated_download_receipts_sha256": hashlib.sha256(b"downloads").hexdigest(),
        "tenant_isolation_receipt_sha256": hashlib.sha256(b"isolation").hexdigest(),
    }
    current = {
        **copy.deepcopy(baseline),
        "api_boot_sha256": hashlib.sha256(b"second-api-boot").hexdigest(),
    }
    gate._verify_restart_equivalence(
        baseline=baseline,
        current=current,
        upload_post_count=0,
    )

    drifted = copy.deepcopy(current)
    drifted["terminal_snapshot_digests"]["component_sha256"]["spans"] = hashlib.sha256(
        b"drifted-spans"
    ).hexdigest()

    with pytest.raises(gate.DurableIngestionGateError) as component_drift:
        gate._verify_restart_equivalence(
            baseline=baseline,
            current=drifted,
            upload_post_count=0,
        )
    assert component_drift.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"

    malformed_baseline = copy.deepcopy(baseline)
    malformed_current = copy.deepcopy(current)
    malformed_baseline["terminal_snapshot_digests"]["component_sha256"].pop("spans")
    malformed_current["terminal_snapshot_digests"]["component_sha256"].pop("spans")
    with pytest.raises(gate.DurableIngestionGateError) as malformed:
        gate._verify_restart_equivalence(
            baseline=malformed_baseline,
            current=malformed_current,
            upload_post_count=0,
        )
    assert malformed.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_current_terminal_reconciliation_cannot_downgrade_when_v5_fields_are_missing() -> None:
    current = {
        "schema_version": gate.TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION,
        **dict.fromkeys(
            gate._TERMINAL_RECONCILIATION_V4_REQUIRED_KEYS
            | gate._TERMINAL_RECONCILIATION_V5_REQUIRED_KEYS
        ),
    }
    assert (
        gate._proof_schema_version_for_terminal_reconciliation(current)
        == gate.TASK4_DURABLE_PROOF_SCHEMA_VERSION
    )

    for missing_key in gate._TERMINAL_RECONCILIATION_V5_REQUIRED_KEYS:
        incomplete = dict(current)
        incomplete.pop(missing_key)
        with pytest.raises(gate.DurableIngestionGateError) as caught:
            gate._proof_schema_version_for_terminal_reconciliation(incomplete)
        assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"

    prior = {
        "schema_version": gate.TERMINAL_INGESTION_PRIOR_RECONCILIATION_SCHEMA_VERSION,
        **dict.fromkeys(gate._TERMINAL_RECONCILIATION_V4_REQUIRED_KEYS),
    }
    assert gate._proof_schema_version_for_terminal_reconciliation(prior) == (
        "slice100-task4-durable-ingestion-proof-v4"
    )
    prior["pdf_page_disposition_censuses_sha256"] = "a" * 64
    with pytest.raises(gate.DurableIngestionGateError):
        gate._proof_schema_version_for_terminal_reconciliation(prior)


def test_api_boot_identity_is_hashed_and_uuid4_bound() -> None:
    boot_id = "10000000-0000-4000-8000-000000000001"

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {
                "status": "ok",
                "time": "2026-07-16T00:00:00Z",
                "version": "6.3",
                "boot_id": boot_id,
            }

    class Client:
        def get(self, url: str, **kwargs: Any) -> Response:
            assert url == "/health"
            assert kwargs["headers"] == {"X-IDIS-API-Key": "acceptance-secret-value"}
            return Response()

    result = gate._api_boot_sha256(
        api_client=Client(),
        api_key="acceptance-secret-value",
    )

    assert result == hashlib.sha256(boot_id.encode("ascii")).hexdigest()


def test_external_api_profile_identity_must_match_the_gate_profile() -> None:
    expected = "a" * 64

    class Response:
        status_code = 200

        def __init__(self, profile_hash: str | None) -> None:
            self._profile_hash = profile_hash

        def json(self) -> dict[str, Any]:
            return {"profile_hmac_sha256": self._profile_hash}

    class Client:
        def __init__(self, profile_hash: str | None) -> None:
            self._profile_hash = profile_hash

        def get(self, url: str, **kwargs: Any) -> Response:
            assert url == "/v1/strict-readiness"
            assert kwargs["headers"] == {"X-IDIS-API-Key": "acceptance-secret-value"}
            return Response(self._profile_hash)

    gate._require_api_profile_match(
        api_client=Client(expected),
        api_key="acceptance-secret-value",
        expected_profile_hmac_sha256=expected,
    )
    for mismatched in ("b" * 64, None):
        with pytest.raises(gate.DurableIngestionGateError) as exc_info:
            gate._require_api_profile_match(
                api_client=Client(mismatched),
                api_key="acceptance-secret-value",
                expected_profile_hmac_sha256=expected,
            )
        assert exc_info.value.code == "STRICT_FULL_LIVE_BLOCKED"


def test_authenticated_download_proof_verifies_one_exact_object_per_company() -> None:
    tenant_id = "00000000-0000-4000-8000-000000000001"
    payloads = (b"fictional alpha", b"fictional beta")
    companies: list[Any] = []
    rows: list[dict[str, Any]] = []
    responses: dict[str, tuple[bytes, dict[str, str]]] = {}
    for index, payload in enumerate(payloads, start=1):
        alias = f"company-{index:03d}"
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = f"20000000-0000-4000-8000-{index:012d}"
        deal_id = f"10000000-0000-4000-8000-{index:012d}"
        object_version = f"30000000-0000-4000-8000-{index:012d}"
        source = SimpleNamespace(sequence=0, size_bytes=len(payload), content_sha256=digest)
        companies.append(SimpleNamespace(company_alias=alias, files=(source,)))
        rows.append(
            {
                "company_alias": alias,
                "source_sequence": 0,
                "doc_id": artifact_id,
                "deal_id": deal_id,
                "durable_object_integrity": {
                    "schema_version": "durable-object-integrity-v1",
                    "content_sha256": digest,
                    "size_bytes": len(payload),
                    "version_id": object_version,
                    "streaming_verified": True,
                },
            }
        )
        responses[f"/v1/documents/{artifact_id}/content"] = (
            payload,
            {
                "content-type": "application/octet-stream",
                "content-length": str(len(payload)),
                "x-idis-content-size": str(len(payload)),
                "x-idis-content-sha256": digest,
                "x-idis-object-version": object_version,
                "x-idis-stream-contract": "durable-object-stream-v1",
                "X-IDIS-RateLimit-Limit": "2400",
                "x-idis-ratelimit-remaining": "2399",
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            },
        )

    class Response:
        status_code = 200

        def __init__(self, payload: bytes, headers: dict[str, str]) -> None:
            self._payload = payload
            self.headers = httpx.Headers(headers)

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self, chunk_size: int) -> Any:
            assert 0 < chunk_size <= 1024 * 1024
            for offset in range(0, len(self._payload), 3):
                yield self._payload[offset : offset + 3]

        def json(self) -> dict[str, Any]:
            raise AssertionError("streaming proof must not parse JSON")

        @property
        def content(self) -> bytes:
            raise AssertionError("streaming proof must not materialize response.content")

    class Client:
        def stream(self, method: str, url: str, **kwargs: Any) -> Response:
            assert method == "GET"
            assert kwargs["headers"] == {"X-IDIS-API-Key": "acceptance-secret-value"}
            payload, headers = responses[url]
            return Response(payload, headers)

        def get(self, _url: str, **_kwargs: Any) -> Response:
            raise AssertionError("authenticated download proof must use streaming transport")

    proof = gate._verify_authenticated_downloads(
        api_client=Client(),
        api_key="acceptance-secret-value",
        acceptance_tenant_id=tenant_id,
        source_companies=companies,
        terminal_rows=rows,
    )

    assert proof["company_count"] == 2
    assert len(proof["receipts_sha256"]) == 64

    first_path = next(iter(responses))
    payload, drifted_headers = responses[first_path]
    responses[first_path] = (
        payload,
        {**drifted_headers, "x-idis-object-version": "90000000-0000-4000-8000-000000000009"},
    )
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._verify_authenticated_downloads(
            api_client=Client(),
            api_key="acceptance-secret-value",
            acceptance_tenant_id=tenant_id,
            source_companies=companies,
            terminal_rows=rows,
        )
    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"

    responses[first_path] = (
        payload,
        {**drifted_headers, "X-IDIS-Unreviewed": "1"},
    )
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._verify_authenticated_downloads(
            api_client=Client(),
            api_key="acceptance-secret-value",
            acceptance_tenant_id=tenant_id,
            source_companies=companies,
            terminal_rows=rows,
        )
    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def _valid_download_header_pairs() -> list[tuple[str, str]]:
    return [
        ("Content-Type", "application/octet-stream"),
        ("Content-Length", "17"),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-IDIS-Content-SHA256", "a" * 64),
        ("X-IDIS-Content-Size", "17"),
        ("X-IDIS-Object-Version", "30000000-0000-4000-8000-000000000001"),
        ("X-IDIS-Stream-Contract", "durable-object-stream-v1"),
        ("X-IDIS-RateLimit-Limit", "20"),
        ("X-IDIS-RateLimit-Remaining", "20"),
    ]


def test_durable_download_header_contract_is_case_insensitive_and_bounded() -> None:
    mixed_case_headers = httpx.Headers(
        [(name.swapcase(), value) for name, value in _valid_download_header_pairs()]
    )

    headers = gate._validated_durable_download_headers(mixed_case_headers)

    assert headers["x-idis-ratelimit-limit"] == "20"
    assert headers["x-idis-ratelimit-remaining"] == "20"


@pytest.mark.parametrize("header_name", ["X-IDIS-RateLimit-Limit", "X-IDIS-RateLimit-Remaining"])
@pytest.mark.parametrize(
    "invalid_value",
    ["", " 1", "+1", "-1", "01", "1.0", "1e3", "1_0", "1,1", "١"],
)
def test_durable_download_header_contract_rejects_noncanonical_rate_values(
    header_name: str,
    invalid_value: str,
) -> None:
    headers = httpx.Headers(
        [
            (name, invalid_value if name == header_name else value)
            for name, value in _valid_download_header_pairs()
        ],
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        gate._validated_durable_download_headers(headers)


@pytest.mark.parametrize(
    "mutated_pairs",
    [
        [*(pair for pair in _valid_download_header_pairs() if pair[0] != "X-IDIS-RateLimit-Limit")],
        [
            *(
                pair
                for pair in _valid_download_header_pairs()
                if pair[0] != "X-IDIS-RateLimit-Remaining"
            )
        ],
        [*_valid_download_header_pairs(), ("X-IDIS-Unreviewed", "1")],
        [*_valid_download_header_pairs(), ("x-idis-ratelimit-limit", "20")],
    ],
    ids=("missing-limit", "missing-remaining", "unknown-idis", "case-variant-duplicate"),
)
def test_durable_download_header_contract_rejects_set_or_duplicate_drift(
    mutated_pairs: list[tuple[str, str]],
) -> None:
    with pytest.raises(ValueError):
        gate._validated_durable_download_headers(httpx.Headers(mutated_pairs))


def test_durable_download_header_contract_rejects_remaining_above_limit() -> None:
    headers = httpx.Headers(
        [
            (name, "21" if name == "X-IDIS-RateLimit-Remaining" else value)
            for name, value in _valid_download_header_pairs()
        ]
    )

    with pytest.raises(ValueError):
        gate._validated_durable_download_headers(headers)


class _NotFoundResponse:
    status_code = 404

    def __enter__(self) -> _NotFoundResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    @staticmethod
    def json() -> dict[str, str]:
        return {"code": "NOT_FOUND"}


class _IsolationClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _NotFoundResponse:
        self.calls.append(("GET", url, kwargs))
        return _NotFoundResponse()

    def post(self, url: str, **kwargs: Any) -> _NotFoundResponse:
        self.calls.append(("POST", url, kwargs))
        return _NotFoundResponse()

    def stream(self, method: str, url: str, **kwargs: Any) -> _NotFoundResponse:
        self.calls.append((method, url, kwargs))
        return _NotFoundResponse()

    def delete(self, url: str, **kwargs: Any) -> _NotFoundResponse:
        self.calls.append(("DELETE", url, kwargs))
        return _NotFoundResponse()


def test_tenant_isolation_checks_read_and_write_surfaces_for_each_company() -> None:
    client = _IsolationClient()
    preparation = {
        "companies": [
            {
                "company_alias": "company-001",
                "deal_id": "10000000-0000-4000-8000-000000000001",
                "package_id": "20000000-0000-4000-8000-000000000001",
            },
            {
                "company_alias": "company-002",
                "deal_id": "10000000-0000-4000-8000-000000000002",
                "package_id": "20000000-0000-4000-8000-000000000002",
            },
        ]
    }
    rows = [
        {
            "company_alias": "company-001",
            "document_id": "30000000-0000-4000-8000-000000000001",
            "doc_id": "40000000-0000-4000-8000-000000000001",
        },
        {
            "company_alias": "company-002",
            "document_id": "30000000-0000-4000-8000-000000000002",
            "doc_id": "40000000-0000-4000-8000-000000000002",
        },
    ]

    receipt = gate._verify_tenant_isolation(
        api_client=client,
        canary_api_key="canary-secret-value",
        preparation_summary=preparation,
        terminal_rows=rows,
    )

    assert receipt == {
        "status": "passed",
        "company_count": 2,
        "denial_count": 18,
        "surface_count": 9,
    }
    assert len(client.calls) == 18
    assert all(
        kwargs["headers"]["X-IDIS-API-Key"] == "canary-secret-value"
        for _, _, kwargs in client.calls
    )
    assert sum(url.endswith("/content-integrity") for _, url, _ in client.calls) == 2
    assert sum(url.endswith("/content") for _, url, _ in client.calls) == 2
    assert sum(url.endswith("/spans") for _, url, _ in client.calls) == 2
    assert (
        sum(
            method == "GET" and url.startswith("/v1/documents/") and url.count("/") == 3
            for method, url, _ in client.calls
        )
        == 2
    )
    write_calls = [call for call in client.calls if call[0] == "POST"]
    assert len(write_calls) == 4
    assert sum(url.endswith("/data-room-packages") for _, url, _ in write_calls) == 2
    assert sum(url.endswith("/documents/upload") for _, url, _ in write_calls) == 2
    upload_calls = [call for call in write_calls if call[1].endswith("/documents/upload")]
    assert all(call[2]["content"] == b"isolation canary" for call in upload_calls)
    assert all(
        call[2]["params"]["sha256"] == hashlib.sha256(b"isolation canary").hexdigest()
        for call in upload_calls
    )
    assert all(call[2]["params"]["doc_type"] == "DATA_ROOM_FILE" for call in upload_calls)
    delete_calls = [call for call in client.calls if call[0] == "DELETE"]
    assert len(delete_calls) == 2
    assert all(url.startswith("/v1/documents/") for _, url, _ in delete_calls)


def test_safe_receipt_writer_never_serializes_rows_ids_excerpts_paths_or_secrets(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / ".local_reports" / "task4-durable-ingestion-proof.json"
    isolation = {
        "status": "passed",
        "company_count": 2,
        "denial_count": 18,
        "surface_count": 9,
        "post_mutation_state_verified": True,
    }
    attestation_summary = {
        "schema_version": "document-no-substantive-content-attestation-v1",
        "status": "verified",
        "entry_count": 1,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "payload_sha256": "6" * 64,
        "envelope_sha256": "7" * 64,
        "public_key_sha256": "8" * 64,
        "signature_verified": True,
    }
    receipt = {
        "schema_version": "slice100-task4-durable-ingestion-proof-v3",
        "status": "ready",
        "phase": "prepared",
        "total_file_count": 267,
        "total_bytes": gate.DEFAULT_EXPECTED_TOTAL_BYTES,
        "bytes_by_company": {
            "company-001": gate.DEFAULT_EXPECTED_COMPANY_BYTES[0],
            "company-002": gate.DEFAULT_EXPECTED_COMPANY_BYTES[1],
        },
        "source_tree_fingerprint": "a" * 64,
        "preparation_ledger_sha256": "b" * 64,
        "terminal_rows_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "code_sha256": "e" * 64,
        "reconciliation_sha256": "f" * 64,
        "profile_hmac_sha256": "1" * 64,
        "local_runtime_readiness_sha256": "2" * 64,
        "no_substantive_content_attestation": attestation_summary,
        "no_substantive_content_attestation_sha256": gate._canonical_json_sha256(
            attestation_summary
        ),
        "confirmed_no_substantive_content_receipts_sha256": "9" * 64,
        "confirmed_no_substantive_content_variant_counts": {
            "blank": 0,
            "illegible": 0,
            "watermark_only": 1,
        },
        "api_boot_sha256": "3" * 64,
        "previous_api_boot_sha256": None,
        "authenticated_download_company_count": 2,
        "authenticated_download_receipts_sha256": "4" * 64,
        "tenant_isolation_receipt_sha256": gate._canonical_json_sha256(isolation),
        "parser_counts": {
            "parsed_file_count": 266,
            "parsed_span_bearing_file_count": 266,
            "confirmed_no_substantive_content_file_count": 1,
            "durable_span_file_count": 266,
            "durable_object_file_count": 267,
            "ocr_file_count": 44,
            "pdf_ocr_file_count": 42,
            "stt_file_count": 8,
            "media_dual_receipt_file_count": 8,
            "stt_positive_file_count": 5,
            "frame_ocr_positive_file_count": 3,
            "large_streamed_file_count": 3,
        },
        "upload_post_count": 267,
        "tenant_isolation": isolation,
        "restart_survival_verified": False,
    }
    gate._write_safe_durable_ingestion_receipt(report_path, receipt)

    raw = report_path.read_text(encoding="utf-8")
    assert json.loads(raw) == receipt
    for forbidden in (
        "deal_id",
        "document_id",
        "doc_id",
        "span_id",
        "text_excerpt",
        "generated_upload_name",
        "private_path",
        "acceptance-secret-value",
        "canary-secret-value",
    ):
        assert forbidden not in raw

    receipt["parser_counts"]["private_parser_detail"] = 1
    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._write_safe_durable_ingestion_receipt(report_path, receipt)
    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_attestation_variant",
        "missing_variant_count",
        "variant_total_mismatch",
    ],
)
def test_safe_receipt_rejects_incomplete_no_substantive_content_variant_proof(
    tmp_path: Path,
    mutation: str,
) -> None:
    isolation = {
        "status": "passed",
        "company_count": 2,
        "denial_count": 18,
        "surface_count": 9,
        "post_mutation_state_verified": True,
    }
    attestation_summary = {
        "schema_version": "document-no-substantive-content-attestation-v1",
        "status": "verified",
        "entry_count": 1,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "payload_sha256": "6" * 64,
        "envelope_sha256": "7" * 64,
        "public_key_sha256": "8" * 64,
        "signature_verified": True,
    }
    variant_counts = {"blank": 0, "illegible": 0, "watermark_only": 1}
    receipt: dict[str, Any] = {
        "schema_version": "slice100-task4-durable-ingestion-proof-v3",
        "status": "ready",
        "phase": "prepared",
        "total_file_count": 267,
        "total_bytes": gate.DEFAULT_EXPECTED_TOTAL_BYTES,
        "bytes_by_company": {
            "company-001": gate.DEFAULT_EXPECTED_COMPANY_BYTES[0],
            "company-002": gate.DEFAULT_EXPECTED_COMPANY_BYTES[1],
        },
        "source_tree_fingerprint": "a" * 64,
        "preparation_ledger_sha256": "b" * 64,
        "terminal_rows_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "code_sha256": "e" * 64,
        "reconciliation_sha256": "f" * 64,
        "profile_hmac_sha256": "1" * 64,
        "local_runtime_readiness_sha256": "2" * 64,
        "no_substantive_content_attestation": attestation_summary,
        "no_substantive_content_attestation_sha256": gate._canonical_json_sha256(
            attestation_summary
        ),
        "confirmed_no_substantive_content_receipts_sha256": "9" * 64,
        "confirmed_no_substantive_content_variant_counts": variant_counts,
        "api_boot_sha256": "3" * 64,
        "previous_api_boot_sha256": None,
        "authenticated_download_company_count": 2,
        "authenticated_download_receipts_sha256": "4" * 64,
        "tenant_isolation_receipt_sha256": gate._canonical_json_sha256(isolation),
        "parser_counts": {
            "parsed_file_count": 266,
            "parsed_span_bearing_file_count": 266,
            "confirmed_no_substantive_content_file_count": 1,
            "durable_span_file_count": 266,
            "durable_object_file_count": 267,
            "ocr_file_count": 44,
            "pdf_ocr_file_count": 42,
            "stt_file_count": 8,
            "media_dual_receipt_file_count": 8,
            "stt_positive_file_count": 5,
            "frame_ocr_positive_file_count": 3,
            "large_streamed_file_count": 3,
        },
        "upload_post_count": 267,
        "tenant_isolation": isolation,
        "restart_survival_verified": False,
    }
    if mutation == "missing_attestation_variant":
        del attestation_summary["variant"]
        receipt["no_substantive_content_attestation_sha256"] = gate._canonical_json_sha256(
            attestation_summary
        )
    elif mutation == "missing_variant_count":
        del variant_counts["blank"]
    else:
        variant_counts["watermark_only"] = 0

    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._write_safe_durable_ingestion_receipt(
            tmp_path / f"{mutation}.json",
            receipt,
        )

    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_external_client_uses_real_httpx_boundary_with_all_finite_timeout_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = gate._build_durable_api_settings(
        env=_strict_env(),
        api_base_url="http://127.0.0.1:8000",
        api_key_env="IDIS_REAL_EXAMPLE_API_KEY",
        canary_api_key_env="IDIS_ISOLATION_CANARY_API_KEY",
    )
    captured: dict[str, Any] = {}

    class FakeHttpxClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            captured["closed"] = True

    fake_httpx = SimpleNamespace(Client=FakeHttpxClient, Timeout=lambda **kwargs: kwargs)
    monkeypatch.setattr(gate, "_import_httpx", lambda: fake_httpx, raising=False)

    client = gate._PacedExternalApiClient(
        settings=settings,
        api_key=settings.api_key,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    client.close()

    assert captured["base_url"] == "http://127.0.0.1:8000"
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["timeout"] == {
        "connect": 10.0,
        "write": 64.0,
        "read": 930.0,
        "pool": 10.0,
    }
    assert captured["closed"] is True


def test_external_client_rejects_absolute_request_urls_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = gate._build_durable_api_settings(
        env=_strict_env(),
        api_base_url="http://127.0.0.1:8000",
        api_key_env="IDIS_REAL_EXAMPLE_API_KEY",
        canary_api_key_env="IDIS_ISOLATION_CANARY_API_KEY",
    )
    transported: list[str] = []

    class FakeHttpxClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def get(self, url: str, **_kwargs: Any) -> None:
            transported.append(url)

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        gate,
        "_import_httpx",
        lambda: SimpleNamespace(Client=FakeHttpxClient, Timeout=lambda **kwargs: kwargs),
    )
    client = gate._PacedExternalApiClient(
        settings=settings,
        api_key=settings.api_key,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        client.get(
            "http://untrusted.invalid/v1/documents",
            headers={"X-IDIS-API-Key": settings.api_key},
        )
    client.close()

    assert exc_info.value.code == "API_ACCEPTANCE_PATH_NONCANONICAL"
    assert transported == []


def test_external_client_opens_admitted_httpx_stream_without_buffered_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = gate._build_durable_api_settings(
        env=_strict_env(),
        api_base_url="http://127.0.0.1:8000",
        api_key_env="IDIS_REAL_EXAMPLE_API_KEY",
        canary_api_key_env="IDIS_ISOLATION_CANARY_API_KEY",
    )
    calls: list[tuple[str, str, dict[str, Any]]] = []

    class FakeStream:
        def __enter__(self) -> FakeStream:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeHttpxClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def stream(self, method: str, url: str, **kwargs: Any) -> FakeStream:
            calls.append((method, url, kwargs))
            return FakeStream()

        def get(self, _url: str, **_kwargs: Any) -> None:
            raise AssertionError("stream transport fell back to buffered get")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        gate,
        "_import_httpx",
        lambda: SimpleNamespace(Client=FakeHttpxClient, Timeout=lambda **kwargs: kwargs),
    )
    client = gate._PacedExternalApiClient(
        settings=settings,
        api_key=settings.api_key,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    with client.stream(
        "GET",
        "/v1/documents/44444444-4444-4444-4444-444444444444/content",
        headers={"X-IDIS-API-Key": settings.api_key},
    ):
        pass
    client.close()

    assert calls == [
        (
            "GET",
            "/v1/documents/44444444-4444-4444-4444-444444444444/content",
            {"headers": {"X-IDIS-API-Key": settings.api_key}},
        )
    ]


def test_task4_code_digest_binds_every_critical_runtime_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def digest(path: Path) -> str:
        observed.append(path.as_posix())
        return hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()

    monkeypatch.setattr(gate, "_file_sha256", digest)

    result = gate._task4_code_sha256()

    assert len(result) == 64
    for suffix in (
        "scripts/invoke_strict_native_process.ps1",
        "scripts/strict_process_identity.psm1",
        "src/idis/api/auth.py",
        "src/idis/api/middleware/audit.py",
        "src/idis/api/middleware/db_tx.py",
        "src/idis/api/middleware/idempotency.py",
        "src/idis/api/routes/data_room_packages.py",
        "src/idis/api/routes/documents.py",
        "src/idis/api/routes/readiness.py",
        "src/idis/api/middleware/upload_staging.py",
        "src/idis/audit/postgres_sink.py",
        "src/idis/evaluation/real_example_gate.py",
        "src/idis/evaluation/real_example_terminal_reconciliation.py",
        "src/idis/parsers/media.py",
        "src/idis/parsers/video_frame_ocr.py",
        "src/idis/services/ingestion/service.py",
        "src/idis/storage/filesystem_store.py",
        "src/idis/runtime/strict_launcher.py",
        "src/idis/persistence/migrations/versions/0035_allow_native_text_document_types.py",
    ):
        assert any(path.endswith(suffix) for path in observed)


def test_durable_workflow_prepares_collects_and_reconciles_before_reducing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    formats = [
        *("pdf" for _ in range(223)),
        *("xlsx" for _ in range(27)),
        *("mp4" for _ in range(8)),
        *("docx" for _ in range(3)),
        *("png" for _ in range(2)),
        *("pptx" for _ in range(2)),
        "html",
        "txt",
    ]
    sources = [
        SimpleNamespace(
            extension=extension,
            size_bytes=(60 * 1024 * 1024 if index < 3 else 1024),
        )
        for index, extension in enumerate(formats)
    ]
    companies = (
        SimpleNamespace(
            company_alias="company-001",
            file_count=58,
            total_bytes=gate.DEFAULT_EXPECTED_COMPANY_BYTES[0],
            files=tuple(sources[:58]),
        ),
        SimpleNamespace(
            company_alias="company-002",
            file_count=209,
            total_bytes=gate.DEFAULT_EXPECTED_COMPANY_BYTES[1],
            files=tuple(sources[58:]),
        ),
    )
    contract = SimpleNamespace(
        companies=companies,
        source_tree_fingerprint="a" * 64,
        total_bytes=gate.DEFAULT_EXPECTED_TOTAL_BYTES,
    )
    events: list[str] = []

    class ApiClient:
        upload_post_count = 0

        def __init__(self) -> None:
            self.boot_id = uuid.uuid4()

        def get(self, url: str, **_kwargs: Any) -> Any:
            if url == "/health":
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "time": "2026-07-20T00:00:00Z",
                        "version": "6.3",
                        "boot_id": str(self.boot_id),
                    },
                )
            if url == "/v1/strict-readiness":
                return httpx.Response(200, json={"profile_hmac_sha256": "4" * 64})
            raise AssertionError(f"unexpected orchestration GET: {url}")

    api_client = ApiClient()

    def prepare(**kwargs: Any) -> dict[str, Any]:
        events.append("prepare")
        assert kwargs["api_client"] is api_client
        api_client.upload_post_count += 267
        ledger_path = Path(kwargs["ledger_path"])
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text("fixture-ledger\n", encoding="utf-8")
        return {
            "source_tree_fingerprint": "a" * 64,
            "companies": [],
        }

    provenance = {
        "schema_version": "faster-whisper-runtime-provenance-v2",
        "adapter": "faster-whisper",
        "adapter_contract_version": "idis-faster-whisper-adapter-v1",
        "runtime_package_version": "1.0",
        "model_source": "local",
        "model_files_sha256": "b" * 64,
        "cpu_threads": 4,
        "num_workers": 1,
        "config_sha256": "c" * 64,
    }
    terminal_rows: list[dict[str, Any]] = []
    for index in range(8):
        row = _terminal_row(private_token=f"private-fixture-{index}")
        document_id = str(uuid.UUID(int=2_000 + index))
        content_sha256 = hashlib.sha256(f"media-{index}".encode("ascii")).hexdigest()
        row.update(
            {
                "company_alias": "company-001" if index < 4 else "company-002",
                "deal_id": str(uuid.UUID(int=1_000 + index // 4)),
                "source_sequence": index,
                "document_id": document_id,
                "doc_id": str(uuid.UUID(int=3_000 + index)),
                "content_sha256": content_sha256,
                "extension": "mp4",
                "parsed_doc_type": "VIDEO",
                "reason_codes": ["media_evidence_completed"],
                "media_transcription_performed": True,
                "media_frame_ocr_performed": True,
                gate.MEDIA_RUNTIME_PROVENANCE_KEY: provenance,
            }
        )
        row["durable_object_integrity"].update(
            {
                "content_sha256": content_sha256,
                "version_id": str(uuid.UUID(int=4_000 + index)),
            }
        )
        row["durable_spans"] = [
            {
                "span_id": str(uuid.UUID(int=5_000 + index)),
                "document_id": document_id,
                "span_type": "TRANSCRIPT",
                "locator": {
                    "source": "media_transcript",
                    "start_ms": index * 1_000,
                    "end_ms": (index + 1) * 1_000,
                },
                "content_hash": hashlib.sha256(f"media-span-{index}".encode("ascii")).hexdigest(),
            }
        ]
        terminal_rows.append(row)

    def collect(**kwargs: Any) -> list[dict[str, Any]]:
        events.append("collect")
        assert kwargs["preparation_summary"]["source_tree_fingerprint"] == "a" * 64
        return terminal_rows

    def reconcile(**kwargs: Any) -> dict[str, Any]:
        events.append("reconcile")
        assert kwargs["expected_total_files"] == 267
        assert kwargs["expected_company_counts"] == {"company-001": 58, "company-002": 209}
        assert kwargs["expected_format_counts"] == gate.TASK4_EXPECTED_FORMAT_COUNTS
        assert kwargs["expected_ocr_files"] == 44
        assert kwargs["expected_stt_files"] == 8
        assert kwargs["expected_large_files"] == 3
        assert kwargs["terminal_rows"] is terminal_rows
        assert len(kwargs["expected_pdf_ocr_required_pages_by_content_sha256"]) == 42
        assert len(kwargs["expected_pdf_ocr_render_admissions_by_content_sha256"]) == 42
        return {
            "schema_version": "safe-terminal-reconciliation-v3",
            "status": "ready",
            "parsed_file_count": 267,
            "parsed_span_bearing_file_count": 267,
            "confirmed_no_substantive_content_file_count": 0,
            "confirmed_no_substantive_content_receipts_sha256": gate._canonical_json_sha256([]),
            "confirmed_no_substantive_content_variant_counts": {
                "blank": 0,
                "illegible": 0,
                "watermark_only": 0,
            },
            "durable_span_file_count": 267,
            "durable_object_file_count": 267,
            "ocr_file_count": 44,
            "pdf_ocr_file_count": 42,
            "stt_file_count": 8,
            "media_dual_receipt_file_count": 8,
            "stt_positive_file_count": 5,
            "frame_ocr_positive_file_count": 4,
            "large_streamed_file_count": 3,
        }

    monkeypatch.setattr(gate, "build_two_company_source_contract", lambda **_kwargs: contract)
    monkeypatch.setattr(gate, "prepare_two_company_source_packages", prepare)
    monkeypatch.setattr(gate, "collect_durable_terminal_rows", collect)
    monkeypatch.setattr(
        gate,
        "inspect_pdf_ocr_requirements",
        lambda _contract: SimpleNamespace(
            required_content_sha256s=frozenset(f"{i:064x}" for i in range(42)),
            required_page_count=284,
            required_pages_by_content_sha256={
                f"{i:064x}": tuple(range(1, 2 if i < 2 else 10 if i == 41 else 8))
                for i in range(42)
            },
            page_count_by_content_sha256={
                f"{i:064x}": 1 if i < 2 else 9 if i == 41 else 7 for i in range(42)
            },
            render_admissions_by_content_sha256={
                f"{i:064x}": tuple(
                    _strict_source_render_plan(page)
                    for page in range(1, 2 if i < 2 else 10 if i == 41 else 8)
                )
                for i in range(42)
            },
        ),
    )
    monkeypatch.setattr(gate, "reconcile_terminal_ingestion", reconcile)

    def authenticated_downloads(**kwargs: Any) -> dict[str, Any]:
        safe_rows = [
            {
                "company_alias": row["company_alias"],
                "source_sequence": row["source_sequence"],
                "content_sha256": row["content_sha256"],
            }
            for row in kwargs["terminal_rows"]
        ]
        return {
            "company_count": len({row["company_alias"] for row in safe_rows}),
            "receipts_sha256": gate._canonical_json_sha256(safe_rows),
        }

    monkeypatch.setattr(
        gate,
        "_verify_authenticated_downloads",
        authenticated_downloads,
    )
    monkeypatch.setattr(
        gate,
        "_verify_tenant_isolation",
        lambda **_kwargs: {
            "status": "passed",
            "company_count": 2,
            "denial_count": 18,
            "surface_count": 9,
        },
    )

    receipt = gate._execute_durable_ingestion_proof(
        root=tmp_path / "source",
        ledger_path=tmp_path / ".local_reports" / "prepare-ledger.jsonl",
        expected_company_count=2,
        expected_file_counts=(58, 209),
        expected_total_files=267,
        ingest_max_bytes=536_870_912,
        api_client=api_client,
        api_key="acceptance-secret-value",
        canary_client=object(),
        canary_api_key="canary-secret-value",
        verify_tenant_isolation=True,
        profile_hmac_sha256="4" * 64,
        local_runtime_readiness_sha256="5" * 64,
        acceptance_tenant_id="00000000-0000-4000-8000-000000000001",
    )

    assert events == ["prepare", "collect", "reconcile", "collect"]
    assert receipt["total_file_count"] == 267
    assert receipt["upload_post_count"] == 267
    assert receipt["terminal_rows_sha256"] == gate._canonical_reduced_terminal_rows_sha256(
        terminal_rows
    )
    # This legacy-v3 orchestration seam proves the real top digest without backporting v6 fields.
    assert (
        receipt["api_boot_sha256"]
        == hashlib.sha256(str(api_client.boot_id).encode("ascii")).hexdigest()
    )
    assert receipt["authenticated_download_company_count"] == 2
    assert (
        receipt["authenticated_download_receipts_sha256"]
        == authenticated_downloads(terminal_rows=terminal_rows)["receipts_sha256"]
    )
    assert receipt["tenant_isolation"]["denial_count"] == 18
    assert receipt["tenant_isolation"]["post_mutation_state_verified"] is True
    assert len(receipt["tenant_isolation_receipt_sha256"]) == 64
    assert "companies" not in receipt
    assert "terminal_rows" not in receipt
