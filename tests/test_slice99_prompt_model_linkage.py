"""Slice99 Task 2 - runtime prompt/model linkage lock + promoted-prompts strict policy (RED-first).

Pins four contracts:

1. LINKAGE LOCK: the prompt ids/versions the runtime stamps into extraction/debate/scoring step
   provenance resolve to registered, materialized, valid artifacts in the governed prompt tree,
   and the canonical runtime prompt surface (``RUNTIME_PROMPT_IDS``) stays in sync with the
   constants the API actually uses.
2. POLICY (flag ON): under ``IDIS_REQUIRE_PROMOTED_PROMPTS=1`` the strict full-live readiness
   report gains a blocking ``prompt_governance`` component unless EVERY runtime prompt id
   resolves through the governed promoted pointer (``prompts/registry.prod.json``) at the exact
   version the runtime stamps, with artifact status PROD. Safe reason codes only.
3. POLICY (blockers): missing pointer, unregistered/invalid artifacts, non-promoted prompts,
   version mismatches, and non-PROD statuses each block with a distinct safe code.
4. DEFAULT OFF: with the flag unset (or any value other than the literal "1"), the readiness
   report is byte-identical to current behavior - no new component, no new blockers.

No prompts are promoted and no evaluation evidence is fabricated by this test module.
PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from idis.services.prompts.promotion_policy import (
    IDIS_REQUIRE_PROMOTED_PROMPTS_ENV,
    RUNTIME_PROMPT_IDS,
    evaluate_promoted_prompts_policy,
    is_promoted_prompts_required,
)
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers: governed tmp prompt tree + promoted pointer
# ---------------------------------------------------------------------------


def _write_governed_prompt(
    repo_root: Path,
    prompt_id: str,
    version: str,
    *,
    status: str = "PROD",
) -> None:
    d = repo_root / "prompts" / prompt_id / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.md").write_text(f"# {prompt_id}\nBody.\n", encoding="utf-8")
    meta = {
        "prompt_id": prompt_id,
        "name": prompt_id,
        "version": version,
        "status": status,
        "owner": "governance/test",
        "created_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T00:00:00Z",
        "risk_class": "LOW",
        "validation_gates_required": [1],
        "evaluation_results_ref": f"evals/{prompt_id}/{version}/results.json",
    }
    (d / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _write_registry_yaml(repo_root: Path, entries: dict[str, dict[str, str]]) -> None:
    lines = ['registry:\n  version: "1.0.0"\n  updated_at: "2026-07-14"\nprompts:\n']
    for pid, entry in entries.items():
        lines.append(
            f"  {pid}:\n"
            f'    id: "{pid}"\n'
            f'    name: "{pid}"\n'
            f'    version: "{entry["version"]}"\n'
            f"    status: {entry.get('status', 'PROD')}\n"
            f"    risk_class: LOW\n"
            f'    file_path: "prompts/{pid}/{entry["version"]}/prompt.md"\n'
            f'    metadata_path: "prompts/{pid}/{entry["version"]}/metadata.json"\n'
        )
    prompts_root = repo_root / "prompts"
    prompts_root.mkdir(parents=True, exist_ok=True)
    (prompts_root / "registry.yaml").write_text("".join(lines), encoding="utf-8")


def _write_prod_pointer(repo_root: Path, prompts: dict[str, str]) -> None:
    pointer = {
        "env": "prod",
        "updated_at": "2026-07-14T00:00:00Z",
        "prompts": prompts,
    }
    (repo_root / "prompts" / "registry.prod.json").write_text(
        json.dumps(pointer, indent=2), encoding="utf-8"
    )


def _governed_tree(
    repo_root: Path,
    *,
    status: str = "PROD",
    pointer_prompts: dict[str, str] | None = None,
    with_pointer: bool = True,
) -> tuple[str, ...]:
    """One-prompt governed tree; returns the runtime id tuple to evaluate against."""
    _write_governed_prompt(repo_root, "alpha", "1.0.0", status=status)
    _write_registry_yaml(repo_root, {"alpha": {"version": "1.0.0", "status": status}})
    if with_pointer:
        _write_prod_pointer(
            repo_root,
            pointer_prompts if pointer_prompts is not None else {"alpha": "1.0.0"},
        )
    return ("alpha",)


def _codes(result: Any) -> set[str]:
    return {f["code"] for f in result.findings}


# ---------------------------------------------------------------------------
# 1. linkage lock: runtime-stamped prompt ids/versions resolve to governed artifacts
# ---------------------------------------------------------------------------


def test_canonical_runtime_prompt_surface_matches_api_constants() -> None:
    from idis.api.routes import runs as runs_module

    expected = {
        runs_module._EXTRACTION_PROMPT_ID,
        runs_module._SCORING_PROMPT_ID,
        *runs_module._DEBATE_PROMPT_IDS,
    }
    assert set(RUNTIME_PROMPT_IDS) == expected, (
        "RUNTIME_PROMPT_IDS must equal the prompt ids the API stamps into provenance; "
        f"policy-only={sorted(set(RUNTIME_PROMPT_IDS) - expected)}, "
        f"api-only={sorted(expected - set(RUNTIME_PROMPT_IDS))}"
    )


def test_runtime_stamped_versions_match_registry_and_artifacts_are_valid() -> None:
    """The (id, version) pairs the runtime stamps must resolve in the governed tree."""
    from idis.api.routes import runs as runs_module
    from idis.services.prompts.validate_cli import validate_prompt_tree

    registry = yaml.safe_load(
        (_REPO_ROOT / "prompts" / "registry.yaml").read_text(encoding="utf-8")
    )
    entries = registry["prompts"]

    for prompt_id in RUNTIME_PROMPT_IDS:
        assert prompt_id in entries, f"runtime prompt '{prompt_id}' not registered"

    # extraction stamps the registry.yaml version at runtime
    stamped_extraction = runs_module._prompt_registry_version(runs_module._EXTRACTION_PROMPT_ID)
    assert stamped_extraction == entries[runs_module._EXTRACTION_PROMPT_ID]["version"]

    # scoring stamps a fixed version constant: it must match the governed registry version
    scoring_registry_version = entries[runs_module._SCORING_PROMPT_ID]["version"]
    assert scoring_registry_version == runs_module._SCORING_PROMPT_VERSION

    # debate stamps the registry version of the arbiter family
    stamped_debate = runs_module._prompt_registry_version(runs_module._DEBATE_PROMPT_VERSION_ID)
    assert stamped_debate == entries[runs_module._DEBATE_PROMPT_VERSION_ID]["version"]

    # and every runtime artifact is materialized + valid (no errors attributed to them)
    report = validate_prompt_tree(_REPO_ROOT / "prompts", _REPO_ROOT)
    failed = {e.get("prompt_id") for e in report["errors"]}
    assert not (set(RUNTIME_PROMPT_IDS) & failed), (
        f"runtime prompts failed governance validation: {sorted(set(RUNTIME_PROMPT_IDS) & failed)}"
    )


def test_extraction_and_role_provenance_stamp_prompt_and_model() -> None:
    """Provenance builders must stamp prompt_id + prompt_version + model (safe linkage)."""
    from idis.api.routes import runs as runs_module

    selection = runs_module.ExtractorClientSelection(
        backend="deterministic", model=None, max_tokens=1024
    )
    extraction = runs_module._build_extraction_provenance(
        selection=selection,
        strict_live_extraction_required=False,
        client=object(),
    )
    assert extraction["prompt_id"] == runs_module._EXTRACTION_PROMPT_ID
    assert extraction["prompt_version"] == runs_module._prompt_registry_version(
        runs_module._EXTRACTION_PROMPT_ID
    )
    assert "model" in extraction and "provider" in extraction

    role = runs_module._build_role_client_provenance(
        selection=selection,
        prompt_id=runs_module._SCORING_PROMPT_ID,
        prompt_version=runs_module._SCORING_PROMPT_VERSION,
        strict_live_debate_backend_required=False,
        client=object(),
    )
    assert role["prompt_id"] == runs_module._SCORING_PROMPT_ID
    assert role["prompt_version"] == runs_module._SCORING_PROMPT_VERSION
    assert "model" in role and "provider" in role


# ---------------------------------------------------------------------------
# 2. flag parsing: literal "1" only (fail-safe default off)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        (" 1 ", True),
        ("0", False),
        ("", False),
        ("true", False),
        ("yes", False),
        ("on", False),
    ],
)
def test_flag_requires_literal_one(value: str, expected: bool) -> None:
    assert is_promoted_prompts_required({IDIS_REQUIRE_PROMOTED_PROMPTS_ENV: value}) is expected


def test_flag_absent_is_off() -> None:
    assert is_promoted_prompts_required({}) is False


# ---------------------------------------------------------------------------
# 3. policy evaluation (governed tmp trees; safe reason codes)
# ---------------------------------------------------------------------------


def test_policy_passes_when_all_prompts_promoted_prod_and_versions_match(tmp_path: Path) -> None:
    runtime_ids = _governed_tree(tmp_path, status="PROD")

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    assert result.may_proceed is True
    assert result.findings == []


def test_policy_blocks_when_prod_pointer_missing(tmp_path: Path) -> None:
    runtime_ids = _governed_tree(tmp_path, status="PROD", with_pointer=False)

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    assert result.may_proceed is False
    assert "PROMOTED_POINTER_MISSING" in _codes(result)


def test_policy_blocks_unpromoted_prompt(tmp_path: Path) -> None:
    runtime_ids = _governed_tree(tmp_path, status="PROD", pointer_prompts={})

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    assert result.may_proceed is False
    assert "PROMPT_NOT_PROMOTED" in _codes(result)


def test_policy_blocks_version_mismatch(tmp_path: Path) -> None:
    """Pointer promoting a version other than the one the runtime stamps must block."""
    runtime_ids = _governed_tree(tmp_path, status="PROD", pointer_prompts={"alpha": "2.0.0"})

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    assert result.may_proceed is False
    assert "PROMPT_VERSION_MISMATCH" in _codes(result)


def test_policy_blocks_draft_status_even_when_promoted(tmp_path: Path) -> None:
    runtime_ids = _governed_tree(tmp_path, status="DRAFT")

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    assert result.may_proceed is False
    assert "PROMPT_STATUS_NOT_PROD" in _codes(result)


def test_policy_blocks_unregistered_runtime_prompt(tmp_path: Path) -> None:
    _governed_tree(tmp_path, status="PROD")

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=("alpha", "ghost_prompt"),
    )

    assert result.may_proceed is False
    assert "PROMPT_UNREGISTERED" in _codes(result)


def test_policy_blocks_invalid_artifact(tmp_path: Path) -> None:
    """A registered+promoted prompt whose metadata.json is broken must block safely."""
    runtime_ids = _governed_tree(tmp_path, status="PROD")
    meta = tmp_path / "prompts" / "alpha" / "1.0.0" / "metadata.json"
    meta.write_text("{ not json", encoding="utf-8")

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    assert result.may_proceed is False
    assert "PROMPT_ARTIFACT_INVALID" in _codes(result)


def test_policy_findings_are_safe_no_paths_or_secrets(tmp_path: Path) -> None:
    runtime_ids = _governed_tree(tmp_path, status="DRAFT", with_pointer=False)

    result = evaluate_promoted_prompts_policy(
        prompts_root=tmp_path / "prompts",
        runtime_prompt_ids=runtime_ids,
    )

    encoded = json.dumps(result.findings)
    assert str(tmp_path) not in encoded, "findings must not leak filesystem paths"
    assert "\\\\" not in encoded and ":/" not in encoded and ":\\\\" not in encoded


# ---------------------------------------------------------------------------
# 4. strict-readiness wiring: flag ON blocks the admission report; flag OFF unchanged
# ---------------------------------------------------------------------------


def test_flag_on_blocks_strict_readiness_report_on_real_tree() -> None:
    """Nothing is promoted in this repo (per Q3 no evidence exists), so under the flag the
    real tree MUST block the same admission report run creation consults."""
    report = build_strict_full_live_readiness_report(
        env={IDIS_REQUIRE_PROMOTED_PROMPTS_ENV: "1"},
        probe_object_store=False,
    )

    component = report.component("prompt_governance")
    assert component.may_proceed is False
    assert "prompt_governance" in report.blocking_components
    assert report.may_proceed is False
    assert IDIS_REQUIRE_PROMOTED_PROMPTS_ENV in " ".join(component.required_env_vars)
    # safe blocker: codes/ids only, no filesystem paths
    assert ":\\" not in component.blocker_message and "C:/" not in component.blocker_message


def test_flag_off_leaves_strict_readiness_report_unchanged() -> None:
    baseline = build_strict_full_live_readiness_report(env={}, probe_object_store=False)
    explicit_off = build_strict_full_live_readiness_report(
        env={IDIS_REQUIRE_PROMOTED_PROMPTS_ENV: "0"},
        probe_object_store=False,
    )

    for report in (baseline, explicit_off):
        with pytest.raises(KeyError):
            report.component("prompt_governance")
        assert "prompt_governance" not in report.blocking_components

    assert baseline.blocking_components == explicit_off.blocking_components
    assert [c.component_name for c in baseline.components] == [
        c.component_name for c in explicit_off.components
    ]
