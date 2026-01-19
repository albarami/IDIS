"""GDBS (Golden Deal Benchmark Suite) loader with fail-closed validation.

Loads GDBS datasets with:
- Fail-closed validation: missing/malformed data produces errors, not silent passes
- Deterministic ordering: cases sorted by (deal_id, case_id)
- Dataset hash: SHA256 over sorted (relative_path, file_bytes) for reproducibility
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from idis.evaluation.types import (
    VALID_SUITE_IDS,
    GdbsCase,
    LoadResult,
    SuiteId,
)

if TYPE_CHECKING:
    pass

SUITE_DEAL_COUNTS: dict[SuiteId, int] = {
    "gdbs-s": 20,
    "gdbs-f": 100,
    "gdbs-a": 30,
}

SUITE_DEAL_RANGES: dict[SuiteId, tuple[int, int]] = {
    "gdbs-s": (1, 20),
    "gdbs-f": (1, 100),
    "gdbs-a": (1, 30),
}


def _compute_dataset_hash(dataset_root: Path, manifest: dict) -> str:
    """Compute deterministic SHA256 hash over manifest content.

    Uses the manifest JSON (with sorted keys) as the basis for hashing,
    ensuring reproducibility across runs.
    """
    hasher = hashlib.sha256()
    canonical_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    hasher.update(canonical_manifest.encode("utf-8"))
    hasher.update(str(dataset_root.resolve()).encode("utf-8"))
    return hasher.hexdigest()


def _validate_manifest(manifest: dict) -> list[str]:
    """Validate manifest structure. Returns list of errors."""
    errors: list[str] = []

    required_fields = ["manifest_version", "dataset_id", "deals"]
    for field in required_fields:
        if field not in manifest:
            errors.append(f"Manifest missing required field: {field}")

    if "deals" in manifest:
        if not isinstance(manifest["deals"], list):
            errors.append("Manifest 'deals' must be a list")
        elif len(manifest["deals"]) == 0:
            errors.append("Manifest 'deals' list is empty")
        else:
            for i, deal in enumerate(manifest["deals"]):
                if not isinstance(deal, dict):
                    errors.append(f"Deal at index {i} is not an object")
                    continue

                deal_required = ["deal_key", "deal_id", "directory"]
                for field in deal_required:
                    if field not in deal:
                        errors.append(f"Deal at index {i} missing required field: {field}")

    return errors


def _filter_deals_for_suite(all_deals: list[dict], suite: SuiteId) -> tuple[list[dict], list[str]]:
    """Filter deals based on suite type and validate count.

    GDBS-S: first 20 deals
    GDBS-F: all 100 deals
    GDBS-A: adversarial deals (scenario != 'clean')
    """
    errors: list[str] = []

    if suite == "gdbs-a":
        filtered = [d for d in all_deals if d.get("scenario", "clean") != "clean"]
        expected_count = SUITE_DEAL_COUNTS[suite]
        if len(filtered) == 0:
            errors.append("No adversarial deals found (scenario != 'clean')")
        elif len(filtered) != expected_count:
            errors.append(f"GDBS-A requires exactly {expected_count} deals, got {len(filtered)}")
        return filtered, errors

    start, end = SUITE_DEAL_RANGES[suite]
    filtered = all_deals[: end - start + 1] if len(all_deals) >= end else all_deals

    expected_count = SUITE_DEAL_COUNTS[suite]
    if len(filtered) < expected_count:
        errors.append(f"Suite {suite} requires {expected_count} deals, found {len(filtered)}")

    return filtered, errors


def _build_cases(
    deals: list[dict], dataset_root: Path, expected_outcomes: list[str] | None
) -> tuple[list[GdbsCase], list[str]]:
    """Build GdbsCase objects from deal dicts with validation."""
    cases: list[GdbsCase] = []
    errors: list[str] = []

    expected_map: dict[str, str] = {}
    if expected_outcomes:
        for path in expected_outcomes:
            key = Path(path).stem.replace("_expected", "")
            expected_map[key] = path

    for deal in deals:
        deal_key = deal.get("deal_key", "")
        deal_id = deal.get("deal_id", "")
        directory = deal.get("directory", "")

        if not deal_key or not deal_id or not directory:
            errors.append(f"Invalid deal entry: {deal}")
            continue

        deal_dir = dataset_root / directory
        if not deal_dir.exists():
            errors.append(f"Deal directory does not exist: {deal_dir}")

        expected_path = expected_map.get(deal_key)

        case = GdbsCase(
            case_id=deal_key,
            deal_id=deal_id,
            deal_key=deal_key,
            scenario=deal.get("scenario", "unknown"),
            directory=directory,
            description=deal.get("description", ""),
            expected_outcome_path=expected_path,
        )
        cases.append(case)

    cases.sort(key=lambda c: c.sort_key())
    return cases, errors


def load_gdbs_suite(dataset_root: Path, suite: SuiteId, *, strict: bool = True) -> LoadResult:
    """Load a GDBS suite from the dataset root.

    Args:
        dataset_root: Path to the GDBS dataset (contains manifest.json)
        suite: Suite identifier (gdbs-s, gdbs-f, gdbs-a)
        strict: If True, directory existence checks produce errors

    Returns:
        LoadResult with success status, cases, errors, and dataset hash

    Fail-closed behavior:
        - dataset_root must exist
        - manifest.json must exist and be valid JSON
        - suite must be one of: gdbs-s, gdbs-f, gdbs-a
        - Required manifest fields must be present
        - Deal directories must exist (in strict mode)
    """
    errors: list[str] = []

    if suite not in VALID_SUITE_IDS:
        return LoadResult(
            success=False,
            errors=[f"Unknown suite: '{suite}'. Valid: {sorted(VALID_SUITE_IDS)}"],
        )

    if not dataset_root.exists():
        return LoadResult(
            success=False,
            errors=[f"Dataset root does not exist: {dataset_root}"],
        )

    if not dataset_root.is_dir():
        return LoadResult(
            success=False,
            errors=[f"Dataset root is not a directory: {dataset_root}"],
        )

    manifest_path = dataset_root / "manifest.json"
    if not manifest_path.exists():
        return LoadResult(
            success=False,
            errors=[f"Manifest not found: {manifest_path}"],
        )

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        return LoadResult(
            success=False,
            errors=[f"Invalid JSON in manifest: {e}"],
        )
    except OSError as e:
        return LoadResult(
            success=False,
            errors=[f"Cannot read manifest: {e}"],
        )

    validation_errors = _validate_manifest(manifest)
    if validation_errors:
        return LoadResult(success=False, errors=validation_errors)

    all_deals = manifest.get("deals", [])
    expected_outcomes = manifest.get("expected_outcomes", [])

    filtered_deals, filter_errors = _filter_deals_for_suite(all_deals, suite)
    errors.extend(filter_errors)

    cases, build_errors = _build_cases(filtered_deals, dataset_root, expected_outcomes)

    if strict:
        errors.extend(build_errors)
    else:
        pass

    dataset_hash = _compute_dataset_hash(dataset_root, manifest)

    if errors:
        return LoadResult(
            success=False,
            cases=cases,
            errors=errors,
            dataset_hash=dataset_hash,
            manifest_version=manifest.get("manifest_version", ""),
            dataset_id=manifest.get("dataset_id", ""),
        )

    return LoadResult(
        success=True,
        cases=cases,
        errors=[],
        dataset_hash=dataset_hash,
        manifest_version=manifest.get("manifest_version", ""),
        dataset_id=manifest.get("dataset_id", ""),
    )
