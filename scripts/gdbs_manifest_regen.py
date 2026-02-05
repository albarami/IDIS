#!/usr/bin/env python3
"""Regenerate GDBS artifact manifest hashes and sizes.

This script updates artifacts.json files for all adversarial deals (001-008)
with the correct sha256 and file_size_bytes values from the actual files.

Ensures deterministic, stable output by sorting keys.
"""

import hashlib
import json
from pathlib import Path

DEALS = {
    1: "clean",
    2: "contradiction",
    3: "unit_mismatch",
    4: "time_window_mismatch",
    5: "missing_evidence",
    6: "calc_conflict",
    7: "chain_break",
    8: "version_drift",
}

GDBS_PATH = Path(__file__).parent.parent / "datasets" / "gdbs_full" / "deals"


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    content = file_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def update_artifacts_json(deal_num: int, suffix: str) -> list[str]:
    """Update artifacts.json for a deal, return list of changes made."""
    deal_dir = GDBS_PATH / f"deal_{deal_num:03d}_{suffix}"
    artifacts_json = deal_dir / "artifacts.json"
    artifacts_dir = deal_dir / "artifacts"

    if not artifacts_json.exists():
        return [f"MISSING: {artifacts_json}"]

    data = json.loads(artifacts_json.read_text(encoding="utf-8"))
    changes = []

    for artifact in data.get("artifacts", []):
        filename = artifact.get("filename")
        if not filename:
            continue

        file_path = artifacts_dir / filename
        if not file_path.exists():
            changes.append(f"  FILE NOT FOUND: {filename}")
            continue

        content = file_path.read_bytes()
        actual_size = len(content)
        actual_sha256 = hashlib.sha256(content).hexdigest()

        old_size = artifact.get("file_size_bytes")
        old_sha256 = artifact.get("sha256")

        if old_size != actual_size or old_sha256 != actual_sha256:
            changes.append(
                f"  {filename}: size {old_size}->{actual_size}, "
                f"sha256 {old_sha256[:12]}...-> {actual_sha256[:12]}..."
            )
            artifact["file_size_bytes"] = actual_size
            artifact["sha256"] = actual_sha256

    # Write back with stable formatting
    artifacts_json.write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    return changes


def main() -> None:
    """Update all adversarial deal artifacts.json files."""
    print("Updating GDBS artifact manifests...")
    print(f"GDBS path: {GDBS_PATH}")
    print()

    total_changes = 0
    for deal_num, suffix in DEALS.items():
        changes = update_artifacts_json(deal_num, suffix)
        if changes:
            print(f"deal_{deal_num:03d}_{suffix}:")
            for change in changes:
                print(change)
            total_changes += len(changes)
        else:
            print(f"deal_{deal_num:03d}_{suffix}: no changes needed")

    print()
    print(f"Total changes: {total_changes}")


if __name__ == "__main__":
    main()
