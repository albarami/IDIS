"""Tests for IDIS Evaluation Harness (Phase 7.3).

Tests cover:
- Fail-closed behavior for missing datasets and unknown suites
- Stable ordering of cases in reports
- Correct exit code semantics (0=PASS, 1=FAIL, 2=BLOCKED)
- Execute mode with unreachable endpoints returns BLOCKED
- JSON output schema validation
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from idis.evaluation.benchmarks.gdbs import load_gdbs_suite
from idis.evaluation.harness import (
    format_summary,
    get_exit_code,
    run_suite,
)
from idis.evaluation.types import (
    VALID_SUITE_IDS,
    ExitCode,
    GateStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "gdbs_mini"


class TestExitCodeSemantics:
    """Test exit code conventions: 0=PASS, 1=FAIL, 2=BLOCKED."""

    def test_gate_status_pass_maps_to_exit_0(self) -> None:
        assert GateStatus.PASS.to_exit_code() == ExitCode.PASS
        assert ExitCode.PASS.value == 0

    def test_gate_status_fail_maps_to_exit_1(self) -> None:
        assert GateStatus.FAIL.to_exit_code() == ExitCode.FAIL
        assert ExitCode.FAIL.value == 1

    def test_gate_status_blocked_maps_to_exit_2(self) -> None:
        assert GateStatus.BLOCKED.to_exit_code() == ExitCode.BLOCKED
        assert ExitCode.BLOCKED.value == 2


class TestGdbsLoaderFailClosed:
    """Test fail-closed behavior for dataset validation."""

    def test_missing_dataset_root_returns_error(self) -> None:
        """Missing dataset root must produce FAIL, not silent pass."""
        result = load_gdbs_suite(Path("/nonexistent/path"), "gdbs-s")
        assert result.success is False
        assert len(result.errors) > 0
        assert "does not exist" in result.errors[0]

    def test_unknown_suite_returns_error(self) -> None:
        """Unknown suite identifier must produce FAIL."""
        result = load_gdbs_suite(FIXTURES_DIR, "gdbs-x")  # type: ignore[arg-type]
        assert result.success is False
        assert "Unknown suite" in result.errors[0]

    def test_valid_suite_ids(self) -> None:
        """Verify valid suite IDs are recognized."""
        assert "gdbs-s" in VALID_SUITE_IDS
        assert "gdbs-f" in VALID_SUITE_IDS
        assert "gdbs-a" in VALID_SUITE_IDS

    def test_missing_manifest_returns_error(self, tmp_path: Path) -> None:
        """Dataset without manifest.json must produce FAIL."""
        result = load_gdbs_suite(tmp_path, "gdbs-s")
        assert result.success is False
        assert "Manifest not found" in result.errors[0]

    def test_malformed_json_manifest_returns_error(self, tmp_path: Path) -> None:
        """Malformed manifest.json must produce FAIL."""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("{ invalid json }", encoding="utf-8")

        result = load_gdbs_suite(tmp_path, "gdbs-s")
        assert result.success is False
        assert "Invalid JSON" in result.errors[0]

    def test_manifest_missing_required_fields_returns_error(self, tmp_path: Path) -> None:
        """Manifest missing required fields must produce FAIL."""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text('{"version": "1.0"}', encoding="utf-8")

        result = load_gdbs_suite(tmp_path, "gdbs-s")
        assert result.success is False
        assert any("missing required field" in e for e in result.errors)


class TestGdbsLoaderSuccess:
    """Test successful loading of valid datasets."""

    def test_load_gdbs_mini_succeeds(self) -> None:
        """gdbs_mini fixture should load successfully."""
        result = load_gdbs_suite(FIXTURES_DIR, "gdbs-s")
        assert result.success is True
        assert len(result.cases) == 20
        assert result.dataset_hash != ""
        assert result.manifest_version == "1.0.0"

    def test_cases_sorted_deterministically(self) -> None:
        """Cases must be sorted by (deal_id, case_id) for stable ordering."""
        result = load_gdbs_suite(FIXTURES_DIR, "gdbs-s")
        assert result.success is True

        deal_ids = [c.deal_id for c in result.cases]
        assert deal_ids == sorted(deal_ids), "Cases must be sorted by deal_id"

    def test_dataset_hash_is_deterministic(self) -> None:
        """Same dataset must produce same hash across runs."""
        result1 = load_gdbs_suite(FIXTURES_DIR, "gdbs-s")
        result2 = load_gdbs_suite(FIXTURES_DIR, "gdbs-s")

        assert result1.dataset_hash == result2.dataset_hash


class TestHarnessValidateMode:
    """Test harness in validate mode (dataset check only)."""

    def test_validate_mode_with_valid_dataset_returns_pass(self) -> None:
        """Valid dataset should return PASS in validate mode."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate")

        assert result.status == GateStatus.PASS
        assert result.mode == "validate"
        assert len(result.cases) == 20
        assert get_exit_code(result) == 0

    def test_validate_mode_with_missing_dataset_returns_fail(self) -> None:
        """Missing dataset should return FAIL with exit code 1."""
        result = run_suite(Path("/nonexistent"), "gdbs-s", mode="validate")

        assert result.status == GateStatus.FAIL
        assert len(result.errors) > 0
        assert get_exit_code(result) == 1

    def test_validate_mode_with_unknown_suite_returns_fail(self) -> None:
        """Unknown suite should return FAIL with exit code 1."""
        result = run_suite(FIXTURES_DIR, "gdbs-x", mode="validate")  # type: ignore[arg-type]

        assert result.status == GateStatus.FAIL
        assert get_exit_code(result) == 1

    def test_validate_mode_writes_output_file(self, tmp_path: Path) -> None:
        """Output file should be written when --out is specified."""
        out_path = tmp_path / "report.json"
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate", out_path=out_path)

        assert result.status == GateStatus.PASS
        assert out_path.exists()

        with open(out_path, encoding="utf-8") as f:
            report = json.load(f)

        assert report["status"] == "PASS"
        assert report["suite_id"] == "gdbs-s"
        assert report["mode"] == "validate"


class TestHarnessExecuteMode:
    """Test harness in execute mode (attempts API calls)."""

    def test_execute_mode_without_base_url_returns_blocked(self) -> None:
        """Execute mode without base_url should return BLOCKED."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="execute", base_url=None)

        assert result.status == GateStatus.BLOCKED
        assert "base_url not provided" in result.blockers[0]
        assert get_exit_code(result) == 2

    def test_execute_mode_with_unreachable_url_returns_blocked(self) -> None:
        """Execute mode with unreachable URL should return BLOCKED."""
        result = run_suite(
            FIXTURES_DIR,
            "gdbs-s",
            mode="execute",
            base_url="http://127.0.0.1:9",  # Port 9 is discard protocol, should fail
        )

        assert result.status == GateStatus.BLOCKED
        assert len(result.blockers) > 0
        # Blocker should describe the connection failure, not static stub strings
        assert any("connect" in b.lower() or "timeout" in b.lower() for b in result.blockers)
        assert get_exit_code(result) == 2

    def test_execute_mode_blockers_are_populated(self) -> None:
        """BLOCKED result should have actionable blocker describing connectivity issue."""
        result = run_suite(
            FIXTURES_DIR,
            "gdbs-s",
            mode="execute",
            base_url="http://127.0.0.1:9",
        )

        assert result.status == GateStatus.BLOCKED
        assert len(result.blockers) == 1
        # Single blocker from _check_api_availability, not old static stub list
        assert "connect" in result.blockers[0].lower() or "timeout" in result.blockers[0].lower()


class TestSuiteResultSerialization:
    """Test deterministic JSON serialization of results."""

    def test_result_to_dict_has_required_fields(self) -> None:
        """JSON output must have all required top-level fields."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate")
        data = result.to_dict()

        required_fields = [
            "suite_id",
            "status",
            "mode",
            "started_at",
            "finished_at",
            "dataset_hash",
            "cases",
            "errors",
            "blockers",
            "metrics",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_result_json_is_deterministic(self) -> None:
        """Same result should produce identical JSON across serializations."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate")

        json1 = json.dumps(result.to_dict(), sort_keys=True)
        json2 = json.dumps(result.to_dict(), sort_keys=True)

        assert json1 == json2

    def test_cases_sorted_in_output(self) -> None:
        """Cases in JSON output must be sorted deterministically."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate")
        data = result.to_dict()

        case_ids = [(c["deal_id"], c["case_id"]) for c in data["cases"]]
        assert case_ids == sorted(case_ids)


class TestFormatSummary:
    """Test human-readable summary formatting."""

    def test_format_summary_includes_key_info(self) -> None:
        """Summary should include suite, mode, status, and hash."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate")
        summary = format_summary(result)

        assert "gdbs-s" in summary
        assert "validate" in summary
        assert "PASS" in summary

    def test_format_summary_shows_errors_when_present(self) -> None:
        """Summary should show errors for failed results."""
        result = run_suite(Path("/nonexistent"), "gdbs-s", mode="validate")
        summary = format_summary(result)

        assert "FAIL" in summary
        assert "Error" in summary or "error" in summary.lower()


class TestCLIIntegration:
    """Test CLI command integration."""

    def test_cli_validate_mode_exit_0_on_pass(self) -> None:
        """CLI should exit 0 on successful validation."""
        from idis.cli import main

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "report.json"
            exit_code = main(
                ["test", "gdbs-s", "--dataset", str(FIXTURES_DIR), "--out", str(out_path)]
            )
            assert exit_code == 0

    def test_cli_missing_dataset_exit_1(self) -> None:
        """CLI should exit 1 on missing dataset."""
        from idis.cli import main

        exit_code = main(["test", "gdbs-s", "--dataset", "/nonexistent"])
        assert exit_code == 1

    def test_cli_execute_unreachable_exit_2(self) -> None:
        """CLI should exit 2 on blocked execution."""
        from idis.cli import main

        exit_code = main(
            [
                "test",
                "gdbs-s",
                "--dataset",
                str(FIXTURES_DIR),
                "--execute",
                "--base-url",
                "http://127.0.0.1:9",
            ]
        )
        assert exit_code == 2


class TestAdversarialSuite:
    """Test GDBS-A adversarial suite filtering and count enforcement."""

    def test_gdbs_a_requires_30_deals_fail_closed(self, tmp_path: Path) -> None:
        """GDBS-A must fail-closed if count != 30 (per spec)."""
        # Create a dataset with only 5 adversarial deals (less than required 30)
        manifest = {
            "manifest_version": "1.0.0",
            "dataset_id": "test-gdbs-a-short",
            "deals": [
                {
                    "deal_key": f"deal_{i:03d}",
                    "deal_id": f"00000000-0000-0000-0000-{i:012d}",
                    "directory": f"deals/deal_{i:03d}",
                    "scenario": "adversarial",
                }
                for i in range(1, 6)  # Only 5 deals, not 30
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        # Create deal directories
        for i in range(1, 6):
            deal_dir = tmp_path / f"deals/deal_{i:03d}"
            deal_dir.mkdir(parents=True, exist_ok=True)
            (deal_dir / "deal.json").write_text("{}", encoding="utf-8")

        result = load_gdbs_suite(tmp_path, "gdbs-a")

        # Must FAIL because count != 30
        assert result.success is False
        assert any("30" in err and "5" in err for err in result.errors)

    def test_gdbs_a_succeeds_with_exactly_30_deals(self, tmp_path: Path) -> None:
        """GDBS-A must succeed when exactly 30 adversarial deals present."""
        # Create a dataset with exactly 30 adversarial deals
        manifest = {
            "manifest_version": "1.0.0",
            "dataset_id": "test-gdbs-a-full",
            "deals": [
                {
                    "deal_key": f"deal_{i:03d}",
                    "deal_id": f"00000000-0000-0000-0000-{i:012d}",
                    "directory": f"deals/deal_{i:03d}",
                    "scenario": "adversarial",
                }
                for i in range(1, 31)  # Exactly 30 deals
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        # Create deal directories
        for i in range(1, 31):
            deal_dir = tmp_path / f"deals/deal_{i:03d}"
            deal_dir.mkdir(parents=True, exist_ok=True)
            (deal_dir / "deal.json").write_text("{}", encoding="utf-8")

        result = load_gdbs_suite(tmp_path, "gdbs-a")

        # Must PASS with exactly 30 deals
        assert result.success is True
        assert len(result.cases) == 30

    def test_gdbs_a_filters_non_clean_scenarios(self, tmp_path: Path) -> None:
        """GDBS-A should only include deals with scenario != 'clean'."""
        # Create mixed dataset: 30 adversarial + 10 clean
        deals = []
        for i in range(1, 31):
            deals.append(
                {
                    "deal_key": f"deal_{i:03d}",
                    "deal_id": f"00000000-0000-0000-0000-{i:012d}",
                    "directory": f"deals/deal_{i:03d}",
                    "scenario": "adversarial",
                }
            )
        for i in range(31, 41):
            deals.append(
                {
                    "deal_key": f"deal_{i:03d}",
                    "deal_id": f"00000000-0000-0000-0000-{i:012d}",
                    "directory": f"deals/deal_{i:03d}",
                    "scenario": "clean",
                }
            )

        manifest = {
            "manifest_version": "1.0.0",
            "dataset_id": "test-gdbs-a-mixed",
            "deals": deals,
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        # Create deal directories
        for i in range(1, 41):
            deal_dir = tmp_path / f"deals/deal_{i:03d}"
            deal_dir.mkdir(parents=True, exist_ok=True)
            (deal_dir / "deal.json").write_text("{}", encoding="utf-8")

        result = load_gdbs_suite(tmp_path, "gdbs-a")

        # Should only include adversarial deals (30), not clean ones
        assert result.success is True
        assert len(result.cases) == 30
        for case in result.cases:
            assert case.scenario != "clean"

    def test_gdbs_a_with_gdbs_mini_fails_count_check(self) -> None:
        """gdbs_mini has only 2 adversarial deals, must fail GDBS-A count check."""
        result = load_gdbs_suite(FIXTURES_DIR, "gdbs-a")

        # gdbs_mini has only 2 adversarial deals, GDBS-A requires 30
        assert result.success is False
        assert any("30" in err for err in result.errors)


class TestExecuteCaseContract:
    """Test _execute_case contract for API-driven case execution."""

    def test_execute_case_returns_fail_on_connection_error(self) -> None:
        """_execute_case with unreachable URL returns FAIL with connection error."""
        from idis.evaluation.harness import _execute_case
        from idis.evaluation.types import CaseStatus, GdbsCase

        case = GdbsCase(
            case_id="test-conn-fail",
            deal_id="00000000-0000-0000-0000-000000000001",
            deal_key="test_conn_fail",
            scenario="clean",
            directory="deals/nonexistent",
            description="Connection failure test",
        )

        result = _execute_case(
            case,
            base_url="http://127.0.0.1:9",
            api_key=None,
            dataset_root=FIXTURES_DIR,
        )

        assert result.status == CaseStatus.FAIL
        assert len(result.errors) > 0
        assert any("Connect" in e for e in result.errors)
        assert result.execution_time_ms is not None

    def test_execute_case_returns_case_result_with_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_execute_case populates metrics on successful HTTP flow."""
        import httpx

        from idis.evaluation.harness import _execute_case
        from idis.evaluation.types import CaseStatus, GdbsCase

        case = GdbsCase(
            case_id="test-metrics",
            deal_id="00000000-0000-0000-0000-000000000002",
            deal_key="test_metrics",
            scenario="clean",
            directory="deals/deal_001",
            description="Metrics test",
        )

        call_count = 0

        def mock_send(
            self: httpx.Client, request: httpx.Request, **kwargs: object
        ) -> httpx.Response:  # type: ignore[override]
            nonlocal call_count
            call_count += 1
            url = str(request.url)

            if url.endswith("/v1/deals") and request.method == "POST":
                return httpx.Response(
                    status_code=201,
                    json={"deal_id": "aaa-bbb-ccc"},
                    request=request,
                )
            if "/runs" in url and request.method == "POST":
                return httpx.Response(
                    status_code=202,
                    json={
                        "run_id": "run-123",
                        "status": "SUCCEEDED",
                        "steps": [],
                    },
                    request=request,
                )
            return httpx.Response(status_code=404, request=request)

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        result = _execute_case(
            case,
            base_url="http://fake-host:9999",
            api_key="test-key",
            dataset_root=FIXTURES_DIR,
        )

        assert result.status == CaseStatus.PASS
        assert result.metrics["run_id"] == "run-123"
        assert result.metrics["run_status"] == "SUCCEEDED"

    def test_execute_case_captures_exception_as_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_execute_case wraps unexpected exceptions as FAIL with error info."""
        import httpx

        from idis.evaluation.harness import _execute_case
        from idis.evaluation.types import CaseStatus, GdbsCase

        case = GdbsCase(
            case_id="test-exc",
            deal_id="00000000-0000-0000-0000-000000000003",
            deal_key="test_exc",
            scenario="clean",
            directory="deals/nonexistent",
            description="Exception capture test",
        )

        def mock_send(
            self: httpx.Client, request: httpx.Request, **kwargs: object
        ) -> httpx.Response:  # type: ignore[override]
            raise RuntimeError("Simulated unexpected failure")

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        result = _execute_case(
            case,
            base_url="http://fake-host:9999",
            api_key=None,
            dataset_root=FIXTURES_DIR,
        )

        assert result.status == CaseStatus.FAIL
        assert any("RuntimeError" in e for e in result.errors)
        assert any("Simulated unexpected failure" in e for e in result.errors)
        assert result.execution_time_ms is not None


class TestExecuteCaseRunStatusClassification:
    """Test run-status classification after DB alignment (SUCCEEDED/FAILED)."""

    def test_execute_case_blocked_via_block_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FAILED + block_reason → CaseStatus.BLOCKED with reason in blockers."""
        import httpx

        from idis.evaluation.harness import _execute_case
        from idis.evaluation.types import CaseStatus, GdbsCase

        case = GdbsCase(
            case_id="test-blocked",
            deal_id="00000000-0000-0000-0000-000000000010",
            deal_key="test_blocked",
            scenario="clean",
            directory="deals/deal_001",
            description="Blocked via block_reason test",
        )

        def mock_send(
            self: httpx.Client, request: httpx.Request, **kwargs: object
        ) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/v1/deals") and request.method == "POST":
                return httpx.Response(
                    status_code=201,
                    json={"deal_id": "aaa-bbb-ccc"},
                    request=request,
                )
            if "/runs" in url and request.method == "POST":
                return httpx.Response(
                    status_code=202,
                    json={
                        "run_id": "run-blocked-1",
                        "status": "FAILED",
                        "block_reason": "UNIMPLEMENTED_STEP:XYZ",
                        "steps": [],
                    },
                    request=request,
                )
            return httpx.Response(status_code=404, request=request)

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        result = _execute_case(
            case,
            base_url="http://fake-host:9999",
            api_key="test-key",
            dataset_root=FIXTURES_DIR,
        )

        assert result.status == CaseStatus.BLOCKED
        assert "UNIMPLEMENTED_STEP:XYZ" in result.blockers
        assert result.metrics["run_status"] == "FAILED"

    def test_execute_case_failed_without_block_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FAILED without block_reason → CaseStatus.FAIL."""
        import httpx

        from idis.evaluation.harness import _execute_case
        from idis.evaluation.types import CaseStatus, GdbsCase

        case = GdbsCase(
            case_id="test-plain-fail",
            deal_id="00000000-0000-0000-0000-000000000011",
            deal_key="test_plain_fail",
            scenario="clean",
            directory="deals/deal_001",
            description="Plain failure test",
        )

        def mock_send(
            self: httpx.Client, request: httpx.Request, **kwargs: object
        ) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/v1/deals") and request.method == "POST":
                return httpx.Response(
                    status_code=201,
                    json={"deal_id": "aaa-bbb-ccc"},
                    request=request,
                )
            if "/runs" in url and request.method == "POST":
                return httpx.Response(
                    status_code=202,
                    json={
                        "run_id": "run-fail-1",
                        "status": "FAILED",
                        "steps": [
                            {"step_name": "EXTRACT", "status": "FAILED", "error": "LLM timeout"},
                        ],
                    },
                    request=request,
                )
            return httpx.Response(status_code=404, request=request)

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        result = _execute_case(
            case,
            base_url="http://fake-host:9999",
            api_key="test-key",
            dataset_root=FIXTURES_DIR,
        )

        assert result.status == CaseStatus.FAIL
        assert any("EXTRACT" in e for e in result.errors)
        assert result.metrics["run_status"] == "FAILED"

    def test_execute_case_unknown_status_fail_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown run status → CaseStatus.FAIL with unknown_run_status blocker."""
        import httpx

        from idis.evaluation.harness import _execute_case
        from idis.evaluation.types import CaseStatus, GdbsCase

        case = GdbsCase(
            case_id="test-unknown-status",
            deal_id="00000000-0000-0000-0000-000000000012",
            deal_key="test_unknown_status",
            scenario="clean",
            directory="deals/deal_001",
            description="Unknown status fail-closed test",
        )

        def mock_send(
            self: httpx.Client, request: httpx.Request, **kwargs: object
        ) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/v1/deals") and request.method == "POST":
                return httpx.Response(
                    status_code=201,
                    json={"deal_id": "aaa-bbb-ccc"},
                    request=request,
                )
            if "/runs" in url and request.method == "POST":
                return httpx.Response(
                    status_code=202,
                    json={
                        "run_id": "run-weird-1",
                        "status": "BANANA",
                        "steps": [],
                    },
                    request=request,
                )
            return httpx.Response(status_code=404, request=request)

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        result = _execute_case(
            case,
            base_url="http://fake-host:9999",
            api_key="test-key",
            dataset_root=FIXTURES_DIR,
        )

        assert result.status == CaseStatus.FAIL
        assert any("unknown_run_status:BANANA" in e for e in result.errors)
        assert result.metrics["run_status"] == "BANANA"


class TestMetrics:
    """Test metrics calculation in results."""

    def test_validate_mode_has_case_metrics(self) -> None:
        """Validate mode should include case pass/fail counts."""
        result = run_suite(FIXTURES_DIR, "gdbs-s", mode="validate")

        assert "cases_total" in result.metrics
        assert "cases_passed" in result.metrics
        assert "cases_failed" in result.metrics
        assert result.metrics["cases_total"] == 20
        assert result.metrics["cases_passed"] == 20
        assert result.metrics["cases_failed"] == 0
