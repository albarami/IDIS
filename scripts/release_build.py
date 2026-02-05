#!/usr/bin/env python3
"""IDIS Release Build Script.

Generates immutable build artifact metadata including:
- SHA256 checksums for reproducibility
- Version stamps
- Git commit information
- Build timestamps

Usage:
    python scripts/release_build.py [--output release_manifest.json]

Exit codes:
    0 - Success
    1 - Build error
    2 - Validation error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Version from pyproject.toml
IDIS_VERSION = "6.3.0"


def get_git_info() -> dict[str, str | None]:
    """Get git commit information."""
    info: dict[str, str | None] = {
        "commit_sha": None,
        "commit_short": None,
        "branch": None,
        "tag": None,
        "dirty": None,
    }

    try:
        # Get full commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        info["commit_sha"] = result.stdout.strip()
        info["commit_short"] = info["commit_sha"][:8] if info["commit_sha"] else None

        # Get branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        info["branch"] = result.stdout.strip()

        # Get tag if exists
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            info["tag"] = result.stdout.strip()

        # Check if working directory is dirty
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        info["dirty"] = "true" if result.stdout.strip() else "false"

    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Warning: Could not get git information", file=sys.stderr)

    return info


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def compute_directory_hash(directory: Path, extensions: list[str] | None = None) -> str:
    """Compute deterministic hash of a directory's contents.

    Args:
        directory: Path to directory
        extensions: Optional list of file extensions to include (e.g., ['.py', '.yaml'])

    Returns:
        SHA256 hash of sorted file contents
    """
    sha256_hash = hashlib.sha256()
    files = []

    for root, _dirs, filenames in os.walk(directory):
        for filename in filenames:
            file_path = Path(root) / filename
            if extensions is None or file_path.suffix in extensions:
                # Use relative path for determinism
                rel_path = file_path.relative_to(directory)
                files.append((str(rel_path), file_path))

    # Sort by relative path for deterministic ordering
    files.sort(key=lambda x: x[0])

    for rel_path, file_path in files:
        # Include path in hash for structure integrity
        sha256_hash.update(rel_path.encode("utf-8"))
        sha256_hash.update(b"\x00")  # Separator
        with open(file_path, "rb") as f:
            sha256_hash.update(f.read())
        sha256_hash.update(b"\x00")  # Separator

    return sha256_hash.hexdigest()


def compute_source_hash(repo_root: Path) -> str:
    """Compute hash of source code for reproducibility verification."""
    src_dir = repo_root / "src"
    if not src_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {src_dir}")
    return compute_directory_hash(src_dir, extensions=[".py"])


def compute_schema_hash(repo_root: Path) -> str:
    """Compute hash of JSON schemas."""
    schema_dir = repo_root / "schemas"
    if not schema_dir.exists():
        return "none"
    return compute_directory_hash(schema_dir, extensions=[".json"])


def compute_openapi_hash(repo_root: Path) -> str:
    """Compute hash of OpenAPI specification."""
    openapi_file = repo_root / "openapi" / "IDIS_OpenAPI_v6_3.yaml"
    if not openapi_file.exists():
        return "none"
    return compute_file_hash(openapi_file)


def compute_docker_hash(repo_root: Path) -> str:
    """Compute hash of Dockerfile."""
    dockerfile = repo_root / "Dockerfile"
    if not dockerfile.exists():
        return "none"
    return compute_file_hash(dockerfile)


def compute_k8s_hash(repo_root: Path) -> str:
    """Compute hash of Kubernetes manifests."""
    k8s_dir = repo_root / "deploy" / "k8s"
    if not k8s_dir.exists():
        return "none"
    return compute_directory_hash(k8s_dir, extensions=[".yaml", ".yml"])


def compute_terraform_hash(repo_root: Path) -> str:
    """Compute hash of Terraform configuration."""
    tf_dir = repo_root / "deploy" / "terraform"
    if not tf_dir.exists():
        return "none"
    return compute_directory_hash(tf_dir, extensions=[".tf"])


def generate_manifest(repo_root: Path) -> dict[str, Any]:
    """Generate the complete release manifest.

    Args:
        repo_root: Path to repository root

    Returns:
        Release manifest dictionary
    """
    git_info = get_git_info()

    manifest: dict[str, Any] = {
        "version": IDIS_VERSION,
        "build_timestamp": datetime.now(UTC).isoformat(),
        "git": {
            "commit": git_info["commit_sha"],
            "commit_short": git_info["commit_short"],
            "branch": git_info["branch"],
            "tag": git_info["tag"],
            "dirty": git_info["dirty"] == "true",
        },
        "checksums": {
            "source": compute_source_hash(repo_root),
            "schemas": compute_schema_hash(repo_root),
            "openapi": compute_openapi_hash(repo_root),
            "dockerfile": compute_docker_hash(repo_root),
            "kubernetes": compute_k8s_hash(repo_root),
            "terraform": compute_terraform_hash(repo_root),
        },
        "artifacts": {
            "docker_image": f"idis:{IDIS_VERSION}",
            "docker_image_sha": None,  # Set after docker build
        },
        "metadata": {
            "python_version": (
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            "platform": sys.platform,
            "builder": os.environ.get("USER", os.environ.get("USERNAME", "unknown")),
            "ci": os.environ.get("CI", "false") == "true",
            "ci_run_id": os.environ.get("GITHUB_RUN_ID"),
            "ci_run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        },
    }

    # Compute overall manifest hash (excluding the manifest_hash field itself)
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["manifest_hash"] = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()

    return manifest


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate the generated manifest.

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    # Check required fields
    required_fields = ["version", "build_timestamp", "git", "checksums", "manifest_hash"]
    for field in required_fields:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    # Check git info
    if manifest.get("git", {}).get("dirty"):
        errors.append("Warning: Working directory has uncommitted changes")

    # Check checksums are computed
    checksums = manifest.get("checksums", {})
    if checksums.get("source") == "none":
        errors.append("Source checksum not computed")

    return errors


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate IDIS release build manifest")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("release_manifest.json"),
        help="Output file path (default: release_manifest.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only, do not write manifest",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output except errors",
    )

    args = parser.parse_args()

    # Find repository root
    repo_root = Path(__file__).parent.parent.resolve()

    if not args.quiet:
        print(f"IDIS Release Build Script v{IDIS_VERSION}")
        print(f"Repository root: {repo_root}")
        print()

    try:
        manifest = generate_manifest(repo_root)
    except Exception as e:
        print(f"Error generating manifest: {e}", file=sys.stderr)
        return 1

    # Validate
    errors = validate_manifest(manifest)
    warnings = [e for e in errors if e.startswith("Warning:")]
    hard_errors = [e for e in errors if not e.startswith("Warning:")]

    if warnings and not args.quiet:
        for warning in warnings:
            print(f"  {warning}")

    if hard_errors:
        print("Validation errors:", file=sys.stderr)
        for error in hard_errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    if args.check:
        if not args.quiet:
            print("Manifest validation passed")
            print(f"  Version: {manifest['version']}")
            print(f"  Commit: {manifest['git']['commit_short']}")
            print(f"  Source hash: {manifest['checksums']['source'][:16]}...")
        return 0

    # Write manifest
    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")

    if not args.quiet:
        print(f"Manifest written to: {output_path}")
        print()
        print("Build Information:")
        print(f"  Version:       {manifest['version']}")
        print(f"  Commit:        {manifest['git']['commit_short']}")
        print(f"  Branch:        {manifest['git']['branch']}")
        print(f"  Timestamp:     {manifest['build_timestamp']}")
        print()
        print("Checksums:")
        for name, checksum in manifest["checksums"].items():
            if checksum and checksum != "none":
                print(f"  {name}: {checksum[:16]}...")
        print()
        print(f"Manifest hash: {manifest['manifest_hash'][:16]}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
