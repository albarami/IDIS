"""Promoted-prompts strict-readiness policy (Slice99 Task 2).

Under ``IDIS_REQUIRE_PROMOTED_PROMPTS=1`` (literal "1" only - fail-safe default off), strict
full-live readiness must block before run creation unless EVERY prompt id the runtime stamps
into step provenance resolves through the governed promoted pointer
(``prompts/registry.prod.json``) at the exact version the runtime uses, with artifact status
PROD.

This module never promotes prompts and never fabricates evaluation evidence: it only evaluates
the governed state on disk and reports safe reason codes (prompt ids, versions, statuses - no
filesystem paths, no content, no secrets).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from idis.services.prompts.registry import (
    PromptArtifact,
    PromptStatus,
    RegistryPointer,
)

IDIS_REQUIRE_PROMOTED_PROMPTS_ENV = "IDIS_REQUIRE_PROMOTED_PROMPTS"

# The canonical runtime prompt surface: the ids the API stamps into extraction/debate/scoring
# step provenance (kept in sync with idis.api.routes.runs constants by test).
RUNTIME_PROMPT_IDS: tuple[str, ...] = (
    "EXTRACT_CLAIMS_V1",
    "DEBATE_ADVOCATE_V1",
    "DEBATE_SANAD_BREAKER_V1",
    "DEBATE_CONTRADICTION_FINDER_V1",
    "DEBATE_RISK_OFFICER_V1",
    "DEBATE_ARBITER_V1",
    "scoring_agent",
)


def is_promoted_prompts_required(env: Mapping[str, str] | None) -> bool:
    """Whether the promoted-prompts policy is enabled: the LITERAL "1" only (default off)."""
    if env is None:
        return False
    return str(env.get(IDIS_REQUIRE_PROMOTED_PROMPTS_ENV, "")).strip() == "1"


@dataclass(frozen=True, slots=True)
class PromotedPromptsPolicyResult:
    """Outcome of the promoted-prompts policy evaluation."""

    may_proceed: bool
    checked: int
    findings: list[dict[str, Any]] = field(default_factory=list)


def _default_prompts_root() -> Path:
    """Locate <repo>/prompts by walking up from this module to pyproject.toml."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "prompts"
        current = current.parent
    return Path("prompts")


def _finding(code: str, detail: str, prompt_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "detail": detail, "prompt_id": prompt_id}


def _load_registry_entries(prompts_root: Path, findings: list[dict[str, Any]]) -> dict[str, Any]:
    registry_path = prompts_root / "registry.yaml"
    if not registry_path.is_file():
        findings.append(_finding("PROMPT_REGISTRY_INVALID", "prompts/registry.yaml not found"))
        return {}
    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        findings.append(
            _finding("PROMPT_REGISTRY_INVALID", "prompts/registry.yaml is not valid YAML")
        )
        return {}
    entries = data.get("prompts")
    if not isinstance(entries, dict):
        findings.append(
            _finding("PROMPT_REGISTRY_INVALID", "prompts/registry.yaml has no 'prompts' mapping")
        )
        return {}
    return entries


def _load_prod_pointer(prompts_root: Path, findings: list[dict[str, Any]]) -> dict[str, str] | None:
    """Load the governed prod pointer; None means it is missing/invalid (already reported)."""
    pointer_path = prompts_root / "registry.prod.json"
    if not pointer_path.is_file():
        findings.append(
            _finding(
                "PROMOTED_POINTER_MISSING",
                "prompts/registry.prod.json not found: no prompt has been promoted to prod",
            )
        )
        return None
    try:
        pointer = RegistryPointer.model_validate(
            json.loads(pointer_path.read_text(encoding="utf-8"))
        )
    except Exception:
        findings.append(
            _finding(
                "PROMOTED_POINTER_INVALID",
                "prompts/registry.prod.json is not a valid registry pointer",
            )
        )
        return None
    if pointer.env != "prod":
        findings.append(
            _finding(
                "PROMOTED_POINTER_INVALID",
                f"prompts/registry.prod.json declares env '{pointer.env}', expected 'prod'",
            )
        )
        return None
    return dict(pointer.prompts)


def _load_artifact(prompts_root: Path, entry: Mapping[str, Any]) -> PromptArtifact | None:
    """Load the governed artifact via the registry entry's explicit paths; None if invalid."""
    repo_root = prompts_root.parent
    metadata_rel = entry.get("metadata_path")
    file_rel = entry.get("file_path")
    if not metadata_rel or not file_rel:
        return None
    metadata_path = Path(metadata_rel)
    prompt_path = Path(file_rel)
    if not metadata_path.is_absolute():
        metadata_path = repo_root / metadata_path
    if not prompt_path.is_absolute():
        prompt_path = repo_root / prompt_path
    if not metadata_path.is_file() or not prompt_path.is_file():
        return None
    try:
        return PromptArtifact.model_validate(json.loads(metadata_path.read_text(encoding="utf-8")))
    except Exception:
        return None


def evaluate_promoted_prompts_policy(
    *,
    prompts_root: Path | None = None,
    runtime_prompt_ids: Sequence[str] = RUNTIME_PROMPT_IDS,
) -> PromotedPromptsPolicyResult:
    """Evaluate the promoted-prompts policy against the governed prompt tree.

    Fail-closed: any runtime prompt that is unregistered, invalid, unpromoted, promoted at a
    different version than the runtime stamps, or not PROD produces a blocking finding.
    """
    root = prompts_root if prompts_root is not None else _default_prompts_root()
    findings: list[dict[str, Any]] = []

    entries = _load_registry_entries(root, findings)
    pointer_prompts = _load_prod_pointer(root, findings)

    for prompt_id in runtime_prompt_ids:
        entry = entries.get(prompt_id)
        if entry is None or not isinstance(entry, Mapping):
            findings.append(
                _finding(
                    "PROMPT_UNREGISTERED",
                    f"runtime prompt '{prompt_id}' is not registered in prompts/registry.yaml",
                    prompt_id,
                )
            )
            continue

        version = str(entry.get("version", ""))
        artifact = _load_artifact(root, entry)
        if artifact is None:
            findings.append(
                _finding(
                    "PROMPT_ARTIFACT_INVALID",
                    f"runtime prompt '{prompt_id}@{version}' has a missing or invalid artifact",
                    prompt_id,
                )
            )
            continue

        if pointer_prompts is not None:
            promoted_version = pointer_prompts.get(prompt_id)
            if promoted_version is None:
                findings.append(
                    _finding(
                        "PROMPT_NOT_PROMOTED",
                        f"runtime prompt '{prompt_id}' has no prod promotion",
                        prompt_id,
                    )
                )
                continue
            if promoted_version != version:
                findings.append(
                    _finding(
                        "PROMPT_VERSION_MISMATCH",
                        (
                            f"runtime prompt '{prompt_id}' stamps version {version} but prod "
                            f"promotes {promoted_version}"
                        ),
                        prompt_id,
                    )
                )
                continue

        if artifact.status != PromptStatus.PROD:
            findings.append(
                _finding(
                    "PROMPT_STATUS_NOT_PROD",
                    (
                        f"runtime prompt '{prompt_id}@{version}' has status "
                        f"{artifact.status.value}, PROD required"
                    ),
                    prompt_id,
                )
            )

    return PromotedPromptsPolicyResult(
        may_proceed=not findings,
        checked=len(runtime_prompt_ids),
        findings=findings,
    )
