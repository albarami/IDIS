"""Prompt registry tree validation - `python -m idis prompts validate` (Slice99 Task 1).

Validates the ACTUAL runtime prompt surface: ``prompts/registry.yaml`` (the index the runtime
provenance reads) plus every on-disk artifact (``prompt.md`` + ``metadata.json`` validated through
the governed ``PromptArtifact`` model).

Fail-closed policy:
- A MATERIALIZED entry (any artifact file on disk) must be fully valid: parseable metadata,
  registry/metadata consistency, strict SemVer, resolvable schema refs, non-empty prompt body.
  Any problem is an ERROR (exit code 2).
- A DECLARED-only entry (registry row, nothing on disk yet) is a WARNING, not a fabricated pass.
- Missing evaluation evidence (``evaluation_results_ref`` that cannot be found locally) is a
  WARNING: honest-but-unproven, never invented (Slice99 Q3 decision).
- An on-disk prompt family with NO registry entry is ungoverned: ERROR.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from idis.services.prompts.registry import (
    PromptArtifact,
    PromptStatus,
    RiskClass,
    validate_semver,
)

_VALID_STATUSES = {s.value for s in PromptStatus}
_VALID_RISK_CLASSES = {r.value for r in RiskClass}


def _finding(code: str, detail: str, prompt_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "detail": detail, "prompt_id": prompt_id}


def _resolve(repo_root: Path, rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else repo_root / p


def _family_under_prompts_root(repo_root: Path, prompts_root: Path, rel: str) -> str | None:
    """Return the prompt family directory name a registry path points into, if any."""
    try:
        resolved = _resolve(repo_root, rel).resolve()
        relative = resolved.relative_to(prompts_root.resolve())
    except (OSError, ValueError):
        return None
    return relative.parts[0] if relative.parts else None


def _validate_entry(
    prompt_id: str,
    entry: Any,
    *,
    repo_root: Path,
    prompts_root: Path,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    covered_families: set[str],
) -> None:
    if not isinstance(entry, dict):
        errors.append(_finding("ENTRY_INVALID", "registry entry is not a mapping", prompt_id))
        return

    declared_id = entry.get("id")
    if declared_id is not None and declared_id != prompt_id:
        errors.append(
            _finding(
                "CONSISTENCY_MISMATCH",
                f"registry key '{prompt_id}' != entry id '{declared_id}'",
                prompt_id,
            )
        )

    version = entry.get("version")
    if not isinstance(version, str) or not validate_semver(version):
        errors.append(
            _finding(
                "INVALID_SEMVER",
                f"version must be strict SemVer MAJOR.MINOR.PATCH, got: {version!r}",
                prompt_id,
            )
        )
        return

    status = entry.get("status")
    if status is not None and status not in _VALID_STATUSES:
        errors.append(
            _finding("ENTRY_INVALID", f"status must be one of {sorted(_VALID_STATUSES)}", prompt_id)
        )

    risk_class = entry.get("risk_class")
    if risk_class is not None and risk_class not in _VALID_RISK_CLASSES:
        errors.append(
            _finding(
                "ENTRY_INVALID",
                f"risk_class must be one of {sorted(_VALID_RISK_CLASSES)}",
                prompt_id,
            )
        )

    file_path = entry.get("file_path")
    metadata_path = entry.get("metadata_path")
    if not file_path or not metadata_path:
        errors.append(
            _finding("ENTRY_INVALID", "file_path and metadata_path are required", prompt_id)
        )
        return

    for rel in (file_path, metadata_path):
        family = _family_under_prompts_root(repo_root, prompts_root, rel)
        if family:
            covered_families.add(family)

    prompt_file = _resolve(repo_root, file_path)
    metadata_file = _resolve(repo_root, metadata_path)
    prompt_exists = prompt_file.is_file()
    metadata_exists = metadata_file.is_file()

    if not prompt_exists and not metadata_exists:
        warnings.append(
            _finding(
                "DECLARED_NOT_MATERIALIZED",
                "registry entry has no on-disk artifact yet (declared intention)",
                prompt_id,
            )
        )
        return

    if not prompt_exists or not metadata_exists:
        missing = file_path if not prompt_exists else metadata_path
        errors.append(
            _finding("ARTIFACT_MISSING", f"partially materialized: missing {missing}", prompt_id)
        )
        return

    if not prompt_file.read_text(encoding="utf-8").strip():
        errors.append(_finding("ARTIFACT_MISSING", "prompt.md is empty", prompt_id))

    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        artifact = PromptArtifact.model_validate(metadata)
    except Exception as e:
        errors.append(_finding("METADATA_INVALID", f"metadata.json invalid: {e}", prompt_id))
        return

    if artifact.prompt_id != prompt_id:
        errors.append(
            _finding(
                "CONSISTENCY_MISMATCH",
                f"metadata prompt_id '{artifact.prompt_id}' != registry key '{prompt_id}'",
                prompt_id,
            )
        )
    if artifact.version != version:
        errors.append(
            _finding(
                "CONSISTENCY_MISMATCH",
                f"metadata version '{artifact.version}' != registry version '{version}'",
                prompt_id,
            )
        )
    if status is not None and artifact.status.value != status:
        errors.append(
            _finding(
                "CONSISTENCY_MISMATCH",
                f"metadata status '{artifact.status.value}' != registry status '{status}'",
                prompt_id,
            )
        )
    if risk_class is not None and artifact.risk_class.value != risk_class:
        errors.append(
            _finding(
                "CONSISTENCY_MISMATCH",
                f"metadata risk_class '{artifact.risk_class.value}' != registry '{risk_class}'",
                prompt_id,
            )
        )

    # The governed artifact (metadata.json) must never point at a nonexistent contract: ERROR.
    for label, ref in (
        ("metadata input_schema_ref", artifact.input_schema_ref),
        ("metadata output_schema_ref", artifact.output_schema_ref),
    ):
        if ref and not _resolve(repo_root, ref).is_file():
            errors.append(
                _finding("SCHEMA_REF_MISSING", f"{label} does not exist: {ref}", prompt_id)
            )

    # The registry.yaml index may declare intended (not yet authored) schema contracts: WARN,
    # keeping the gap visible without fabricating schema files.
    for label, ref in (
        ("registry input_schema", entry.get("input_schema")),
        ("registry output_schema", entry.get("output_schema")),
    ):
        if ref and not _resolve(repo_root, ref).is_file():
            warnings.append(
                _finding(
                    "SCHEMA_REF_DECLARED_MISSING",
                    f"{label} declared but not authored yet: {ref}",
                    prompt_id,
                )
            )

    eval_ref = artifact.evaluation_results_ref
    if "://" in eval_ref:
        warnings.append(
            _finding(
                "EVAL_EVIDENCE_MISSING",
                f"evaluation evidence is an external ref (not locally verifiable): {eval_ref}",
                prompt_id,
            )
        )
    elif not _resolve(repo_root, eval_ref).is_file():
        warnings.append(
            _finding(
                "EVAL_EVIDENCE_MISSING",
                f"evaluation evidence not found: {eval_ref}",
                prompt_id,
            )
        )


def _on_disk_families(prompts_root: Path) -> set[str]:
    """Prompt family directories that actually contain artifact files."""
    families: set[str] = set()
    if not prompts_root.is_dir():
        return families
    for family_dir in prompts_root.iterdir():
        if not family_dir.is_dir():
            continue
        for version_dir in family_dir.iterdir():
            if not version_dir.is_dir():
                continue
            if (version_dir / "prompt.md").is_file() or (version_dir / "metadata.json").is_file():
                families.add(family_dir.name)
                break
    return families


def validate_prompt_tree(prompts_root: Path, repo_root: Path) -> dict[str, Any]:
    """Validate registry.yaml + on-disk prompt artifacts. Fail-closed; see module docstring."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    covered_families: set[str] = set()
    prompts_checked = 0

    registry_path = prompts_root / "registry.yaml"
    if not registry_path.is_file():
        errors.append(_finding("REGISTRY_MISSING", f"registry not found: {registry_path}"))
    else:
        try:
            data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            data = {}
            errors.append(_finding("REGISTRY_INVALID", f"registry.yaml is not valid YAML: {e}"))

        entries = data.get("prompts")
        if entries is None or not isinstance(entries, dict):
            errors.append(_finding("REGISTRY_INVALID", "registry.yaml has no 'prompts' mapping"))
            entries = {}

        for prompt_id in sorted(entries.keys()):
            prompts_checked += 1
            _validate_entry(
                prompt_id,
                entries[prompt_id],
                repo_root=repo_root,
                prompts_root=prompts_root,
                errors=errors,
                warnings=warnings,
                covered_families=covered_families,
            )

    for family in sorted(_on_disk_families(prompts_root) - covered_families):
        errors.append(
            _finding(
                "UNREGISTERED_ARTIFACT",
                f"on-disk prompt family '{family}' has no registry.yaml entry (ungoverned)",
            )
        )

    return {
        "ok": not errors,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prompts_checked": prompts_checked,
        "errors": errors,
        "warnings": warnings,
    }
