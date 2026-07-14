"""Slice99 acceptance capstone - release gate + CI-wiring truth + governance pins (RED-first).

Parses the ACTUAL ``.github/workflows/ci.yml`` (yaml-structured run steps, never echo text)
and the real release tooling to prove the Slice99 governance surface is wired:

1. Release manifest completeness: ``release_manifest.json`` must carry real sha256 sections
   for source, schemas, OpenAPI, Dockerfile, K8s, and Terraform - a missing/none section
   fails closed (``manifest_completeness_errors`` + ``--require-complete``).
2. evaluation-harness runs the drift-gated GDBS command (``--baseline`` with the pinned
   gdbs_mini baseline).
3. The check job runs ``python -m idis prompts validate``.
4. The check job runs the contract-lock verification command.
5. A ``release-gate`` job exists and depends on EVERY other CI job.
6. ``prompt.version.*`` emitters follow the compliance core-audit convention.
7. Migration linearity (single head) and the audit-contract surface (validator/schema
   resource-type parity including ``prompt``) stay pinned.

PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

_REQUIRED_CHECKSUM_SECTIONS = (
    "source",
    "schemas",
    "openapi",
    "dockerfile",
    "kubernetes",
    "terraform",
)


def _ci_jobs() -> dict[str, Any]:
    data = yaml.safe_load(_CI_PATH.read_text(encoding="utf-8"))
    return dict(data["jobs"])


def _job_run_commands(job: dict[str, Any]) -> list[str]:
    """Non-echo command lines from a job's run steps (the ACTUAL invocations)."""
    commands: list[str] = []
    for step in job.get("steps", []):
        run = step.get("run")
        if not run:
            continue
        for line in str(run).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "echo ")):
                commands.append(stripped)
    return commands


def _load_release_build() -> Any:
    spec = importlib.util.spec_from_file_location(
        "release_build", _REPO_ROOT / "scripts" / "release_build.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. release manifest completeness (fail-closed)
# ---------------------------------------------------------------------------


class TestReleaseManifestCompleteness:
    def test_manifest_carries_all_required_checksum_sections(self) -> None:
        module = _load_release_build()
        manifest = module.generate_manifest(_REPO_ROOT)

        checksums = manifest["checksums"]
        for section in _REQUIRED_CHECKSUM_SECTIONS:
            assert section in checksums, f"manifest must include checksum section: {section}"
            value = checksums[section]
            assert isinstance(value, str) and len(value) == 64, (
                f"checksum section '{section}' must be a real sha256, got {value!r}"
            )

    def test_completeness_check_passes_on_real_manifest(self) -> None:
        module = _load_release_build()
        manifest = module.generate_manifest(_REPO_ROOT)

        assert module.manifest_completeness_errors(manifest) == []

    def test_completeness_check_fails_closed_on_missing_or_none_section(self) -> None:
        module = _load_release_build()
        manifest = module.generate_manifest(_REPO_ROOT)

        broken = copy.deepcopy(manifest)
        broken["checksums"]["terraform"] = "none"
        errors = module.manifest_completeness_errors(broken)
        assert any("terraform" in error for error in errors)

        gutted = copy.deepcopy(manifest)
        del gutted["checksums"]["openapi"]
        errors = module.manifest_completeness_errors(gutted)
        assert any("openapi" in error for error in errors)

    def test_cli_require_complete_flag(self, tmp_path: Path) -> None:
        module = _load_release_build()
        out = tmp_path / "release_manifest.json"

        exit_code = module.main(["--output", str(out), "--require-complete"])

        assert exit_code == 0, "the real repo must produce a complete manifest"
        manifest = json.loads(out.read_text(encoding="utf-8"))
        assert set(_REQUIRED_CHECKSUM_SECTIONS) <= set(manifest["checksums"])


# ---------------------------------------------------------------------------
# 2-5. CI wiring truth (parsed from the actual workflow structure)
# ---------------------------------------------------------------------------


class TestCiWiring:
    def test_evaluation_harness_runs_drift_gated_gdbs_command(self) -> None:
        commands = _job_run_commands(_ci_jobs()["evaluation-harness"])
        drift_gated = [
            command
            for command in commands
            if "python -m idis test gdbs-s" in command
            and "--dataset tests/fixtures/gdbs_mini" in command
            and "--baseline tests/fixtures/gdbs_baseline/gdbs_mini_gdbs_s_baseline.json" in command
        ]
        assert drift_gated, (
            f"evaluation-harness must run the drift-gated GDBS command, got: {commands}"
        )

    def test_check_job_runs_prompts_validate(self) -> None:
        commands = _job_run_commands(_ci_jobs()["check"])
        assert any("python -m idis prompts validate" in command for command in commands), (
            "the check job must run the prompt governance validation"
        )

    def test_check_job_runs_contract_lock_verification(self) -> None:
        commands = _job_run_commands(_ci_jobs()["check"])
        assert any("scripts/contract_lock.py verify" in command for command in commands), (
            "the check job must run the contract-lock verification command"
        )

    def test_release_gate_job_depends_on_every_other_job(self) -> None:
        jobs = _ci_jobs()
        assert "release-gate" in jobs, "a release-gate job must aggregate the CI gates"

        needs = jobs["release-gate"].get("needs", [])
        needs_set = {needs} if isinstance(needs, str) else set(needs)
        other_jobs = set(jobs) - {"release-gate"}
        missing = other_jobs - needs_set
        assert not missing, f"release-gate must need every CI job; missing: {sorted(missing)}"

    def test_release_gate_enforces_manifest_completeness(self) -> None:
        commands = _job_run_commands(_ci_jobs()["release-gate"])
        assert any(
            "scripts/release_build.py" in command and "--require-complete" in command
            for command in commands
        ), "release-gate must regenerate and completeness-check the release manifest"


# ---------------------------------------------------------------------------
# 6. prompt.version.* emitters follow the core-audit convention
# ---------------------------------------------------------------------------


class TestPromptGovernanceConvention:
    def test_prompt_version_events_are_schema_valid_internal_posts(self, tmp_path: Path) -> None:
        from idis.audit.sink import InMemoryAuditSink
        from idis.services.prompts.registry import PromptRegistry
        from idis.services.prompts.versioning import (
            Approval,
            ApprovalRole,
            GateResult,
            PromotionRequest,
            PromptVersioningService,
            RetireRequest,
            RollbackRequest,
        )
        from idis.validators.audit_event_validator import validate_audit_event

        for version in ("1.0.0", "1.1.0"):
            artifact_dir = tmp_path / "capstone_prompt" / version
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "prompt.md").write_text("# capstone\nBody.\n", encoding="utf-8")
            (artifact_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "prompt_id": "capstone_prompt",
                        "name": "capstone_prompt",
                        "version": version,
                        "status": "DRAFT",
                        "owner": "governance/capstone",
                        "created_at": "2026-07-14T00:00:00Z",
                        "updated_at": "2026-07-14T00:00:00Z",
                        "risk_class": "LOW",
                        "validation_gates_required": [1],
                        "evaluation_results_ref": "evals/capstone/results.json",
                    }
                ),
                encoding="utf-8",
            )

        sink = InMemoryAuditSink()
        service = PromptVersioningService(PromptRegistry(tmp_path), audit_sink=sink)
        service.promote(
            PromotionRequest(
                prompt_id="capstone_prompt",
                new_version="1.1.0",
                env="dev",
                actor="capstone",
                reason="capstone pin",
                approvals=[Approval(approver_id="owner", role=ApprovalRole.OWNER)],
                evaluation_results_ref="evals/capstone/results.json",
                evaluation_results_sha256="b" * 64,
                gate_results=[GateResult(gate=1, passed=True)],
            )
        )
        service.rollback(
            RollbackRequest(
                prompt_id="capstone_prompt",
                rollback_target_version="1.0.0",
                env="dev",
                actor="capstone",
                reason="capstone pin",
            )
        )
        service.retire(
            RetireRequest(
                prompt_id="capstone_prompt",
                version="1.1.0",
                actor="capstone",
                reason="capstone pin",
            )
        )

        assert [event["event_type"] for event in sink.events] == [
            "prompt.version.promoted",
            "prompt.version.rolledback",
            "prompt.version.retired",
        ]
        for event in sink.events:
            result = validate_audit_event(event)
            assert result.passed, f"{event['event_type']} failed validation: {result.errors}"
            assert event["request"]["method"] == "POST"
            assert str(event["request"]["path"]).startswith("/internal/prompts/")
            assert set(event["payload"].keys()) <= {"safe", "hashes", "refs"}


# ---------------------------------------------------------------------------
# 7. migration linearity + audit-contract surface stay pinned
# ---------------------------------------------------------------------------


class TestFoundationPins:
    def test_migrations_have_a_single_linear_head(self) -> None:
        import os

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        import idis.persistence.migrations as migrations_pkg

        config = Config()
        config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
        script = ScriptDirectory.from_config(config)

        heads = script.get_heads()
        assert len(heads) == 1, f"migration chain must stay linear, heads: {heads}"

    def test_audit_resource_type_surface_parity_includes_prompt(self) -> None:
        from idis.validators.audit_event_validator import VALID_RESOURCE_TYPES

        schema = json.loads(
            (_REPO_ROOT / "schemas" / "audit_event.schema.json").read_text(encoding="utf-8")
        )
        enum = set(schema["properties"]["resource"]["properties"]["resource_type"]["enum"])

        assert "prompt" in enum and "prompt" in VALID_RESOURCE_TYPES
        assert enum == set(VALID_RESOURCE_TYPES)
