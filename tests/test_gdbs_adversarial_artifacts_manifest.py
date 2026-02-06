"""Regression test: adversarial deal generator must keep artifacts.json consistent.

Validates that running the adversarial artifact generation for deals 001-008
produces files whose sha256 and file_size_bytes match the corresponding
artifacts.json entries. This catches drift when reportlab/openpyxl output
changes but artifacts.json is not updated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

GDBS_PATH = Path(__file__).parent.parent / "datasets" / "gdbs_full"

ADVERSARIAL_DIRS = [
    "deal_001_clean",
    "deal_002_contradiction",
    "deal_003_unit_mismatch",
    "deal_004_time_window_mismatch",
    "deal_005_missing_evidence",
    "deal_006_calc_conflict",
    "deal_007_chain_break",
    "deal_008_version_drift",
]


def _compute_sha256(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("deal_idx", list(range(8)), ids=ADVERSARIAL_DIRS)
class TestAdversarialArtifactsManifestCurrent:
    """Fail-closed: every artifact referenced in artifacts.json must match on disk."""

    def test_artifact_size_matches_manifest(self, deal_idx: int) -> None:
        """File size on disk must equal file_size_bytes in artifacts.json."""
        deal_dir = GDBS_PATH / "deals" / ADVERSARIAL_DIRS[deal_idx]
        if not deal_dir.exists():
            pytest.skip(f"GDBS deal dir not found: {deal_dir}")

        artifacts_json = deal_dir / "artifacts.json"
        assert artifacts_json.exists(), f"Missing artifacts.json in {ADVERSARIAL_DIRS[deal_idx]}"

        data = json.loads(artifacts_json.read_text(encoding="utf-8"))
        artifacts = data.get("artifacts", [])
        assert artifacts, f"Empty artifacts list in {ADVERSARIAL_DIRS[deal_idx]}"

        for artifact in artifacts:
            filename = artifact["filename"]
            expected_size = artifact["file_size_bytes"]
            file_path = deal_dir / "artifacts" / filename
            assert file_path.exists(), f"{filename} missing on disk"

            actual_size = file_path.stat().st_size
            assert actual_size == expected_size, (
                f"{ADVERSARIAL_DIRS[deal_idx]}/{filename}: "
                f"artifacts.json says {expected_size} bytes, disk has {actual_size} bytes. "
                f"Re-run 'python scripts/generate_gdbs_full.py' to update."
            )

    def test_artifact_sha256_matches_manifest(self, deal_idx: int) -> None:
        """SHA256 on disk must equal sha256 in artifacts.json."""
        deal_dir = GDBS_PATH / "deals" / ADVERSARIAL_DIRS[deal_idx]
        if not deal_dir.exists():
            pytest.skip(f"GDBS deal dir not found: {deal_dir}")

        artifacts_json = deal_dir / "artifacts.json"
        assert artifacts_json.exists(), f"Missing artifacts.json in {ADVERSARIAL_DIRS[deal_idx]}"

        data = json.loads(artifacts_json.read_text(encoding="utf-8"))

        for artifact in data.get("artifacts", []):
            filename = artifact["filename"]
            expected_sha = artifact["sha256"]
            file_path = deal_dir / "artifacts" / filename
            assert file_path.exists(), f"{filename} missing on disk"

            actual_sha = _compute_sha256(file_path)
            assert actual_sha == expected_sha, (
                f"{ADVERSARIAL_DIRS[deal_idx]}/{filename}: "
                f"SHA256 mismatch — artifacts.json is stale. "
                f"Re-run 'python scripts/generate_gdbs_full.py' to update."
            )

    def test_all_manifest_files_exist_on_disk(self, deal_idx: int) -> None:
        """Every filename in artifacts.json must exist in the artifacts/ directory."""
        deal_dir = GDBS_PATH / "deals" / ADVERSARIAL_DIRS[deal_idx]
        if not deal_dir.exists():
            pytest.skip(f"GDBS deal dir not found: {deal_dir}")

        artifacts_json = deal_dir / "artifacts.json"
        assert artifacts_json.exists()

        data = json.loads(artifacts_json.read_text(encoding="utf-8"))

        for artifact in data.get("artifacts", []):
            filename = artifact["filename"]
            file_path = deal_dir / "artifacts" / filename
            assert file_path.exists(), (
                f"{ADVERSARIAL_DIRS[deal_idx]}/artifacts/{filename} referenced in "
                f"artifacts.json but missing on disk"
            )
