#!/usr/bin/env python3
"""Gate 3: GDBS-F Evaluation Harness (>=95% pass rate)

Phase 6 Release Gate:
- Run full pipeline on GDBS adversarial dataset (100 deals)
- Measure debate completion rate
- Measure Muḥāsabah gate pass rate
- Require >=95% completion with valid outputs

BLOCKED STATUS:
This gate is currently BLOCKED because full end-to-end pipeline execution
with debate is not yet operational. The following components exist but are
not integrated into a working E2E flow:

✅ Implemented:
- GDBS dataset loader (datasets/gdbs_full/)
- Claim/Sanad models and services
- Debate orchestrator and roles
- Muḥāsabah validator
- Deliverables generator
- Background worker infrastructure

❌ Missing for E2E:
- Document ingestion + claim extraction pipeline
- Claim → Sanad chain building automation
- Debate → Deliverable pipeline orchestration
- Runs that execute full debate + deliverable generation

This script provides the evaluation framework. When pipeline is complete,
run: python scripts/gates/gate_3_gdbs_f.py --execute

For now, this script documents the gate structure and exits with BLOCKED status.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
GDBS_PATH = REPO_ROOT / "datasets" / "gdbs_full"
RESULTS_PATH = REPO_ROOT / "docs" / "gates"


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

    # Check for E2E pipeline components (these are NOT complete)
    pipeline_blockers = [
        "Document ingestion pipeline not integrated with claim extraction",
        "Claim extraction service not operational",
        "Sanad chain building not automated (only manual test scripts exist)",
        "Debate execution not integrated with deliverable generation",
        "No /v1/deals/{dealId}/runs endpoint that executes full pipeline",
    ]

    # Gate 3 is BLOCKED until pipeline is complete
    blockers.extend(pipeline_blockers)

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


def run_gate_3_execute() -> dict:
    """Execute Gate 3 evaluation on GDBS-F dataset.

    This will be implemented when pipeline is operational.
    """
    # Placeholder for actual execution
    raise NotImplementedError(
        "Gate 3 execution not yet implemented. "
        "Pipeline integration required. See --status for blockers."
    )


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

    args = parser.parse_args()

    # Default to status check
    if not args.execute and not args.status:
        args.status = True

    if args.execute:
        try:
            report = run_gate_3_execute()
            output_path = (
                RESULTS_PATH / f"gate_3_result_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
            )
            output_path.write_text(json.dumps(report, indent=2))

            print("\n✅ Gate 3 PASSED")
            print(f"Results: {output_path}")
            return 0
        except NotImplementedError as e:
            print(f"\n❌ Gate 3 execution blocked: {e}")
            args.status = True  # Fall through to status

    if args.status:
        report = run_gate_3_blocked()
        output_path = RESULTS_PATH / "gate_3_blocked_status.json"
        output_path.write_text(json.dumps(report, indent=2))

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

        # Return exit code 2 to indicate "blocked" (not failure, but not success)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
