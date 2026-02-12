#!/usr/bin/env python3
"""Gate 3: GDBS-F Evaluation Harness (>=95% pass rate)

Phase 6 Release Gate:
- Run full pipeline on GDBS adversarial dataset (100 deals)
- Measure debate completion rate
- Measure Muḥāsabah gate pass rate
- Require >=95% completion with valid outputs

Usage:
    python scripts/gates/gate_3_gdbs_f.py --status   # Check prerequisites
    python scripts/gates/gate_3_gdbs_f.py --execute   # Run full gate evaluation
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

GDBS_PATH = REPO_ROOT / "datasets" / "gdbs_full"
RESULTS_PATH = REPO_ROOT / "docs" / "gates"

GATE3_PORT = 8777
GATE3_BASE_URL = f"http://127.0.0.1:{GATE3_PORT}"
GATE3_API_KEY = "gate3-harness-key"
PASS_RATE_THRESHOLD = 0.95
SERVER_STARTUP_TIMEOUT_S = 30
SERVER_HEALTH_POLL_INTERVAL_S = 0.5

logger = logging.getLogger(__name__)


def check_prerequisites() -> tuple[bool, list[str]]:
    """Check if all prerequisites for Gate 3 are met.

    Returns:
        (ready, blockers) - True if ready to execute, False with list of blockers.
    """
    blockers = []

    # Check GDBS dataset exists
    if not GDBS_PATH.exists():
        blockers.append("GDBS dataset not found at datasets/gdbs_full/")

    # Check for required components (these exist)
    required_modules = [
        "src/idis/models/claim.py",
        "src/idis/models/sanad.py",
        "src/idis/debate/orchestrator.py",
        "src/idis/validators/muhasabah.py",
        "src/idis/deliverables/screening.py",
    ]

    for module in required_modules:
        if not (REPO_ROOT / module).exists():
            blockers.append(f"Required module missing: {module}")

    # Check orchestrator has all 9 pipeline steps
    try:
        from idis.models.run_step import FULL_STEPS, StepName

        if len(FULL_STEPS) < 9:
            blockers.append(f"Orchestrator FULL_STEPS has {len(FULL_STEPS)} steps, need >= 9")
        required_step_names = {"ENRICHMENT", "ANALYSIS", "SCORING", "DELIVERABLES"}
        available_step_names = set(StepName.__members__)
        missing_steps = required_step_names - available_step_names
        if missing_steps:
            blockers.append(f"StepName missing members: {sorted(missing_steps)}")
    except ImportError as exc:
        blockers.append(f"Cannot import orchestrator step definitions: {exc}")

    # Check harness is importable
    try:
        from idis.evaluation.harness import run_suite  # noqa: F401
    except ImportError as exc:
        blockers.append(f"Cannot import evaluation harness: {exc}")

    return len(blockers) == 0, blockers


def run_gate_3_blocked() -> dict:
    """Generate blocked status report for Gate 3."""
    ready, blockers = check_prerequisites()

    report = {
        "gate": "Gate 3: GDBS-F Evaluation",
        "status": "BLOCKED",
        "timestamp": datetime.now(UTC).isoformat(),
        "ready_to_execute": ready,
        "blockers": blockers,
        "requirements": {
            "dataset": "GDBS-F (100 adversarial deals)",
            "required_completion_rate": 0.95,
            "required_muhasabah_pass_rate": 0.95,
            "metrics": [
                "debate_completion_rate",
                "muhasabah_pass_rate",
                "deliverable_generation_rate",
                "no_free_facts_violations",
            ],
        },
        "next_steps": [
            "Complete document ingestion + claim extraction pipeline",
            "Integrate Sanad chain building into pipeline",
            "Wire debate orchestration to deliverable generation",
            "Implement /v1/deals/{dealId}/runs full execution endpoint",
            "Run: python scripts/gates/gate_3_gdbs_f.py --execute",
        ],
    }

    return report


def _wait_for_server(base_url: str, timeout_s: float) -> bool:
    """Poll the server health endpoint until it responds or timeout.

    Args:
        base_url: Server base URL.
        timeout_s: Maximum seconds to wait.

    Returns:
        True if server became available, False on timeout.
    """
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(SERVER_HEALTH_POLL_INTERVAL_S)
    return False


def _start_gate3_server(shared_store_dir: Path) -> subprocess.Popen[bytes]:
    """Start the Gate 3 API server as a subprocess.

    Args:
        shared_store_dir: Absolute path to the shared filesystem store.

    Returns:
        The Popen handle for the server process.
    """
    server_script = REPO_ROOT / "scripts" / "gates" / "start_gate3_server.py"
    cmd = [
        sys.executable,
        str(server_script),
        str(shared_store_dir),
        "--port",
        str(GATE3_PORT),
    ]
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


def _stop_server(proc: subprocess.Popen[bytes]) -> None:
    """Gracefully stop the server process.

    Args:
        proc: The server Popen handle.
    """
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _write_exit_code(results_dir: Path, exit_code: int) -> None:
    """Write exit_code.txt artifact."""
    print("Writing exit_code.txt...", flush=True)
    (results_dir / "exit_code.txt").write_text(str(exit_code), encoding="utf-8")


def _write_deal_results_csv(results_dir: Path, suite_result: Any) -> None:
    """Write deal_results.csv artifact from suite result."""
    print("Writing deal_results.csv...", flush=True)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "case_id",
            "deal_id",
            "status",
            "execution_time_ms",
            "documents_ingested",
            "run_status",
            "errors",
        ]
    )
    for case in sorted(suite_result.cases, key=lambda c: (c.deal_id, c.case_id)):
        writer.writerow(
            [
                case.case_id,
                case.deal_id,
                case.status.value,
                case.execution_time_ms or "",
                case.metrics.get("documents_ingested", ""),
                case.metrics.get("run_status", ""),
                "|".join(case.errors[:3]) if case.errors else "",
            ]
        )
    (results_dir / "deal_results.csv").write_text(output.getvalue(), encoding="utf-8")


def _write_failures_json(results_dir: Path, suite_result: Any) -> None:
    """Write failures.json artifact with categorized failures."""
    print("Writing failures.json...", flush=True)
    failures: list[dict[str, Any]] = []
    for case in suite_result.cases:
        if case.status.value not in ("PASS",):
            failures.append(
                {
                    "case_id": case.case_id,
                    "deal_id": case.deal_id,
                    "status": case.status.value,
                    "errors": case.errors,
                    "blockers": case.blockers,
                    "metrics": {k: str(v) for k, v in case.metrics.items()},
                }
            )

    categories: dict[str, int] = {}
    for f in failures:
        for err in f["errors"]:
            if "NO_INGESTED_DOCUMENTS" in err:
                categories["NO_INGESTED_DOCUMENTS"] = categories.get("NO_INGESTED_DOCUMENTS", 0) + 1
            elif "ConnectError" in err or "connect" in err.lower():
                categories["CONNECTION_ERROR"] = categories.get("CONNECTION_ERROR", 0) + 1
            elif "500" in err:
                categories["SERVER_ERROR"] = categories.get("SERVER_ERROR", 0) + 1
            else:
                categories["OTHER"] = categories.get("OTHER", 0) + 1

    payload = {
        "total_failures": len(failures),
        "categories": dict(sorted(categories.items())),
        "failures": sorted(failures, key=lambda f: f["case_id"]),
    }
    (results_dir / "failures.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _write_gate3_summary(results_dir: Path, suite_result: Any, gate_status: str) -> None:
    """Write gate3_summary.md artifact."""
    print("Writing gate3_summary.md...", flush=True)
    total = len(suite_result.cases)
    passed = len([c for c in suite_result.cases if c.status.value == "PASS"])
    failed = len([c for c in suite_result.cases if c.status.value == "FAIL"])
    blocked = len([c for c in suite_result.cases if c.status.value == "BLOCKED"])
    pass_rate = passed / total if total > 0 else 0.0

    lines = [
        "# Gate 3: GDBS-F Evaluation Summary",
        "",
        f"**Status:** {gate_status}",
        f"**Timestamp:** {datetime.now(UTC).isoformat()}",
        f"**Pass Rate Threshold:** {PASS_RATE_THRESHOLD:.0%}",
        "",
        "## Results",
        "",
        f"- **Total Cases:** {total}",
        f"- **Passed:** {passed}",
        f"- **Failed:** {failed}",
        f"- **Blocked:** {blocked}",
        f"- **Pass Rate:** {pass_rate:.1%}",
        "",
        "## Metrics",
        "",
    ]
    for k, v in sorted(suite_result.metrics.items()):
        lines.append(f"- **{k}:** {v}")

    if suite_result.errors:
        lines.extend(["", "## Top Errors", ""])
        for err in suite_result.errors[:10]:
            lines.append(f"- {err}")

    lines.append("")
    (results_dir / "gate3_summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_gate_3_execute(
    case_limit: int | None = None,
    http_timeout: float | None = None,
) -> dict[str, Any]:
    """Execute Gate 3 evaluation on GDBS-F dataset.

    Creates a shared store directory, starts the Gate 3 server,
    runs the evaluation harness with document seeding, and produces
    all required artifacts.

    Args:
        case_limit: Optional max number of deals to execute.
        http_timeout: Optional per-request HTTP timeout in seconds.

    Returns:
        Gate result dict with status, metrics, and artifact paths.
    """
    ready, blockers = check_prerequisites()
    if not ready:
        return {
            "gate": "Gate 3: GDBS-F Evaluation",
            "status": "BLOCKED",
            "timestamp": datetime.now(UTC).isoformat(),
            "blockers": blockers,
        }

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    results_dir = RESULTS_PATH / f"gate3_run_{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)

    log_path = results_dir / "gate3.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.DEBUG)

    shared_store_dir = Path(tempfile.mkdtemp(prefix="gate3_store_"))
    store_dir_file = results_dir / "gate3_shared_store_dir.txt"
    store_dir_file.write_text(str(shared_store_dir), encoding="utf-8")
    logger.info("Shared store directory: %s", shared_store_dir)

    server_proc = _start_gate3_server(shared_store_dir)
    logger.info("Started Gate 3 server (pid=%d) on port %d", server_proc.pid, GATE3_PORT)

    try:
        if not _wait_for_server(GATE3_BASE_URL, SERVER_STARTUP_TIMEOUT_S):
            logger.error("Server did not become healthy within %ds", SERVER_STARTUP_TIMEOUT_S)
            _write_exit_code(results_dir, 2)
            return {
                "gate": "Gate 3: GDBS-F Evaluation",
                "status": "BLOCKED",
                "timestamp": datetime.now(UTC).isoformat(),
                "blockers": ["Gate 3 server failed to start"],
            }

        logger.info("Server healthy. Starting harness execution.")
        print("Server healthy. Starting harness...", flush=True)

        from idis.evaluation.harness import run_suite

        def _restart_server() -> None:
            """Kill and restart the gate 3 server to avoid GIL starvation."""
            nonlocal server_proc
            _stop_server(server_proc)
            server_proc = _start_gate3_server(shared_store_dir)
            if not _wait_for_server(GATE3_BASE_URL, SERVER_STARTUP_TIMEOUT_S):
                logger.error("Server failed to restart")

        suite_result = run_suite(
            dataset_root=GDBS_PATH,
            suite="gdbs-f",
            mode="execute",
            base_url=GATE3_BASE_URL,
            api_key=GATE3_API_KEY,
            out_path=results_dir / "suite_result.json",
            shared_store_dir=shared_store_dir,
            case_limit=case_limit,
            http_timeout=http_timeout,
            pre_case_fn=_restart_server,
        )

        total = len(suite_result.cases)
        print(f"Harness returned {total} cases. Writing artifacts...", flush=True)
        passed = len([c for c in suite_result.cases if c.status.value == "PASS"])
        pass_rate = passed / total if total > 0 else 0.0
        gate_passed = pass_rate >= PASS_RATE_THRESHOLD
        gate_status = "PASS" if gate_passed else "FAIL"

        _write_deal_results_csv(results_dir, suite_result)
        _write_failures_json(results_dir, suite_result)
        _write_gate3_summary(results_dir, suite_result, gate_status)

        exit_code = 0 if gate_passed else 1
        _write_exit_code(results_dir, exit_code)

        logger.info(
            "Gate 3 complete: status=%s pass_rate=%.1f%% (%d/%d)",
            gate_status,
            pass_rate * 100,
            passed,
            total,
        )

        return {
            "gate": "Gate 3: GDBS-F Evaluation",
            "status": gate_status,
            "timestamp": datetime.now(UTC).isoformat(),
            "pass_rate": pass_rate,
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "threshold": PASS_RATE_THRESHOLD,
            "results_dir": str(results_dir),
            "shared_store_dir": str(shared_store_dir),
            "metrics": suite_result.metrics,
        }

    finally:
        _stop_server(server_proc)
        logger.info("Gate 3 server stopped.")
        root_logger.removeHandler(file_handler)
        file_handler.close()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Gate 3: GDBS-F Evaluation Harness")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute Gate 3 evaluation (requires operational pipeline)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current gate status and blockers",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of deals to execute (for diagnostic runs)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        help="Per-request HTTP timeout in seconds (default: 1800)",
    )

    args = parser.parse_args()

    if not args.execute and not args.status:
        args.status = True

    if args.execute:
        RESULTS_PATH.mkdir(parents=True, exist_ok=True)
        report = run_gate_3_execute(
            case_limit=args.limit,
            http_timeout=args.timeout,
        )
        status = report.get("status", "UNKNOWN")

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_path = RESULTS_PATH / f"gate_3_result_{timestamp}.json"
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        results_dir = report.get("results_dir", "")
        pass_rate = report.get("pass_rate", 0.0)

        print(f"\nGate 3: {status}")
        print(f"Pass rate: {pass_rate:.1%}" if isinstance(pass_rate, float) else "")
        print(f"Results: {results_dir}")
        print(f"Report: {output_path}")

        if status == "PASS":
            return 0
        if status == "BLOCKED":
            return 2
        return 1

    if args.status:
        RESULTS_PATH.mkdir(parents=True, exist_ok=True)
        report = run_gate_3_blocked()
        output_path = RESULTS_PATH / "gate_3_blocked_status.json"
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        print("\n" + "=" * 70)
        print("Gate 3: GDBS-F Evaluation - BLOCKED STATUS")
        print("=" * 70)
        print(f"\nStatus: {report['status']}")
        print(f"Ready to Execute: {report['ready_to_execute']}")
        print(f"\nBlockers ({len(report['blockers'])}):")
        for i, blocker in enumerate(report["blockers"], 1):
            print(f"  {i}. {blocker}")

        print("\nNext Steps:")
        for i, step in enumerate(report["next_steps"], 1):
            print(f"  {i}. {step}")

        print(f"\nStatus saved to: {output_path}")
        print("=" * 70)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
