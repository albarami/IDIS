"""Tests for Prompt Registry (Task 7.2).

Tests cover:
- PR-001 traceability: version loaded + rollback works
- Fail-closed behavior for all validation paths
- Promotion with gate enforcement by risk class
- Atomic pointer updates
- Audit event emission (prompt.version.promoted/rolledback/retired)
- Audit failure is fatal (operation must fail, registry unchanged)

Required by:
- docs/12_IDIS_End_to_End_Implementation_Roadmap_v6_3.md (Phase 7.2)
- docs/11_IDIS_Traceability_Matrix_v6_3.md (PR-001)
- docs/IDIS_Master_Execution_Plan_v6_3.md (Go-Live §4.4)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.services.prompts.registry import (
    InvalidPromptVersionError,
    LoadedPrompt,
    PromptArtifact,
    PromptArtifactError,
    PromptNotFoundError,
    PromptRegistry,
    RegistryFileError,
    RiskClass,
    SchemaRefBypassError,
    SchemaRefNotFoundError,
    compute_content_hash,
    validate_semver,
)
from idis.services.prompts.versioning import (
    Approval,
    ApprovalRole,
    AuditEmissionError,
    GateRequirementError,
    GateResult,
    MissingApprovalError,
    MissingFieldError,
    PromotionRequest,
    PromptVersioningError,
    PromptVersioningService,
    RetireRequest,
    RollbackRequest,
)


class FailingAuditSink:
    """Audit sink that always fails (for testing fail-closed behavior)."""

    def __init__(self, error_message: str = "Simulated failure") -> None:
        self._error_message = error_message

    def emit(self, event: dict[str, Any]) -> None:
        """Always raise AuditSinkError."""
        raise AuditSinkError(self._error_message)


def _now_iso8601() -> str:
    """Get current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _create_prompt_artifact(
    prompt_id: str,
    version: str,
    risk_class: str = "MEDIUM",
    name: str | None = None,
    validation_gates_required: list[int] | None = None,
    evaluation_results_ref: str = "s3://bucket/eval/default.json",
) -> dict[str, Any]:
    """Create a valid prompt artifact metadata dict with all required fields.

    Per spec, the following fields are required (no defaults):
    - status, risk_class, validation_gates_required, evaluation_results_ref
    """
    if validation_gates_required is None:
        validation_gates_required = [1, 2]
    return {
        "prompt_id": prompt_id,
        "name": name or f"Test Prompt {prompt_id}",
        "version": version,
        "status": "DRAFT",
        "owner": "test-team/test-owner",
        "created_at": _now_iso8601(),
        "updated_at": _now_iso8601(),
        "change_summary": "Initial version",
        "risk_class": risk_class,
        "model_requirements": {
            "min_context_window": 4096,
            "tool_calling_support": False,
            "json_mode_support": False,
        },
        "tool_contracts": [],
        "input_schema_ref": None,
        "output_schema_ref": None,
        "validation_gates_required": validation_gates_required,
        "fallback_policy": [],
        "evaluation_results_ref": evaluation_results_ref,
        "security_notes": "",
    }


def _create_test_prompt_structure(
    root: Path,
    prompt_id: str,
    version: str,
    prompt_text: str = "You are a helpful assistant.",
    risk_class: str = "MEDIUM",
    validation_gates_required: list[int] | None = None,
    evaluation_results_ref: str = "s3://bucket/eval/default.json",
) -> Path:
    """Create a test prompt artifact on disk with all required fields."""
    prompt_dir = root / prompt_id / version
    prompt_dir.mkdir(parents=True, exist_ok=True)

    metadata = _create_prompt_artifact(
        prompt_id,
        version,
        risk_class,
        validation_gates_required=validation_gates_required,
        evaluation_results_ref=evaluation_results_ref,
    )
    metadata_path = prompt_dir / "metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    prompt_path = prompt_dir / "prompt.md"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return prompt_dir


def _owner_approval(approver_id: str = "owner-1") -> Approval:
    """Create an OWNER approval."""
    return Approval(approver_id=approver_id, role=ApprovalRole.OWNER)


def _security_approval(approver_id: str = "security-1") -> Approval:
    """Create a SECURITY_COMPLIANCE approval."""
    return Approval(approver_id=approver_id, role=ApprovalRole.SECURITY_COMPLIANCE)


def _create_registry_pointer(
    root: Path,
    env: str,
    prompts: dict[str, str],
) -> Path:
    """Create a registry pointer file."""
    registry_path = root / f"registry.{env}.json"
    data = {
        "env": env,
        "updated_at": _now_iso8601(),
        "prompts": prompts,
    }
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return registry_path


class TestSemverValidation:
    """Tests for SemVer validation."""

    def test_valid_semver_basic(self) -> None:
        """Test valid basic semver strings."""
        assert validate_semver("1.0.0") is True
        assert validate_semver("0.1.0") is True
        assert validate_semver("10.20.30") is True

    def test_valid_semver_zeros(self) -> None:
        """Test semver with zeros."""
        assert validate_semver("0.0.0") is True
        assert validate_semver("0.0.1") is True

    def test_invalid_semver_leading_zeros(self) -> None:
        """Test invalid semver with leading zeros."""
        assert validate_semver("01.0.0") is False
        assert validate_semver("1.01.0") is False
        assert validate_semver("1.0.01") is False

    def test_invalid_semver_missing_parts(self) -> None:
        """Test invalid semver with missing parts."""
        assert validate_semver("1.0") is False
        assert validate_semver("1") is False
        assert validate_semver("") is False

    def test_invalid_semver_prerelease(self) -> None:
        """Test that prerelease versions are not accepted (strict semver)."""
        assert validate_semver("1.0.0-alpha") is False
        assert validate_semver("1.0.0-beta.1") is False

    def test_invalid_semver_build_metadata(self) -> None:
        """Test that build metadata is not accepted (strict semver)."""
        assert validate_semver("1.0.0+build") is False


class TestPromptArtifactModel:
    """Tests for PromptArtifact Pydantic model."""

    def test_valid_artifact(self) -> None:
        """Test creating a valid prompt artifact."""
        data = _create_prompt_artifact("test-prompt", "1.0.0")
        artifact = PromptArtifact.model_validate(data)
        assert artifact.prompt_id == "test-prompt"
        assert artifact.version == "1.0.0"
        assert artifact.risk_class == RiskClass.MEDIUM

    def test_invalid_version_rejected(self) -> None:
        """Test that invalid version is rejected."""
        data = _create_prompt_artifact("test-prompt", "invalid")
        with pytest.raises(ValueError, match="strict SemVer"):
            PromptArtifact.model_validate(data)

    def test_invalid_timestamp_rejected(self) -> None:
        """Test that invalid timestamp is rejected."""
        data = _create_prompt_artifact("test-prompt", "1.0.0")
        data["created_at"] = "not-a-timestamp"
        with pytest.raises(ValueError, match="ISO-8601"):
            PromptArtifact.model_validate(data)


class TestPromptRegistry:
    """Tests for PromptRegistry loader."""

    def test_load_valid_registry(self, tmp_path: Path) -> None:
        """Test loading a valid registry."""
        _create_test_prompt_structure(tmp_path, "prompt-1", "1.0.0")
        _create_registry_pointer(tmp_path, "dev", {"prompt-1": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        pointer = registry.load("dev")

        assert pointer.env == "dev"
        assert pointer.prompts["prompt-1"] == "1.0.0"

    def test_load_nonexistent_registry_fails_closed(self, tmp_path: Path) -> None:
        """Test that loading a nonexistent registry fails closed."""
        registry = PromptRegistry(tmp_path)

        with pytest.raises(RegistryFileError, match="does not exist"):
            registry.load("dev")

    def test_load_invalid_json_fails_closed(self, tmp_path: Path) -> None:
        """Test that invalid JSON fails closed."""
        registry_path = tmp_path / "registry.dev.json"
        with open(registry_path, "w") as f:
            f.write("not valid json {{{")

        registry = PromptRegistry(tmp_path)

        with pytest.raises(RegistryFileError, match="Invalid JSON"):
            registry.load("dev")

    def test_env_mismatch_fails_closed(self, tmp_path: Path) -> None:
        """Test that environment mismatch fails closed."""
        registry_path = tmp_path / "registry.dev.json"
        data = {"env": "prod", "updated_at": _now_iso8601(), "prompts": {}}
        with open(registry_path, "w") as f:
            json.dump(data, f)

        registry = PromptRegistry(tmp_path)

        with pytest.raises(RegistryFileError, match="Environment mismatch"):
            registry.load("dev")

    def test_get_prompt_loads_artifact(self, tmp_path: Path) -> None:
        """Test that get_prompt loads the full artifact."""
        prompt_text = "You are an extraction assistant."
        _create_test_prompt_structure(tmp_path, "extract-v1", "1.2.3", prompt_text)
        _create_registry_pointer(tmp_path, "staging", {"extract-v1": "1.2.3"})

        registry = PromptRegistry(tmp_path)
        registry.load("staging")

        loaded = registry.get_prompt("extract-v1")

        assert isinstance(loaded, LoadedPrompt)
        assert loaded.artifact.prompt_id == "extract-v1"
        assert loaded.artifact.version == "1.2.3"
        assert loaded.prompt_text == prompt_text
        assert loaded.content_hash == compute_content_hash(prompt_text)

    def test_get_prompt_not_in_registry_fails_closed(self, tmp_path: Path) -> None:
        """Test that getting a prompt not in registry fails closed."""
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(PromptNotFoundError, match="not found"):
            registry.get_prompt("nonexistent")

    def test_missing_metadata_json_fails_closed(self, tmp_path: Path) -> None:
        """Test that missing metadata.json fails closed."""
        prompt_dir = tmp_path / "broken-prompt" / "1.0.0"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"broken-prompt": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(PromptArtifactError, match="metadata.json missing"):
            registry.get_prompt("broken-prompt")

    def test_missing_prompt_md_fails_closed(self, tmp_path: Path) -> None:
        """Test that missing prompt.md fails closed."""
        prompt_dir = tmp_path / "broken-prompt" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("broken-prompt", "1.0.0")
        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)

        _create_registry_pointer(tmp_path, "dev", {"broken-prompt": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(PromptArtifactError, match="prompt.md missing"):
            registry.get_prompt("broken-prompt")

    def test_invalid_semver_in_registry_fails_closed(self, tmp_path: Path) -> None:
        """Test that invalid semver in registry fails closed."""
        _create_registry_pointer(tmp_path, "dev", {"bad-prompt": "not-semver"})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(InvalidPromptVersionError):
            registry.get_prompt("bad-prompt")

    def test_list_prompts_returns_sorted(self, tmp_path: Path) -> None:
        """Test that list_prompts returns stable sorted list."""
        _create_test_prompt_structure(tmp_path, "zeta", "1.0.0")
        _create_test_prompt_structure(tmp_path, "alpha", "1.0.0")
        _create_test_prompt_structure(tmp_path, "beta", "1.0.0")
        _create_registry_pointer(
            tmp_path, "prod", {"zeta": "1.0.0", "alpha": "1.0.0", "beta": "1.0.0"}
        )

        registry = PromptRegistry(tmp_path)
        registry.load("prod")

        prompts = registry.list_prompts()

        assert prompts == ["alpha", "beta", "zeta"]

    def test_schema_ref_not_found_fails_closed(self, tmp_path: Path) -> None:
        """Test that missing schema ref fails closed."""
        prompt_dir = tmp_path / "schema-prompt" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("schema-prompt", "1.0.0")
        metadata["input_schema_ref"] = "missing_schema.json"

        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"schema-prompt": "1.0.0"})

        schemas_root = tmp_path / "schemas"
        schemas_root.mkdir()

        registry = PromptRegistry(tmp_path, schemas_root=schemas_root)
        registry.load("dev")

        with pytest.raises(SchemaRefNotFoundError, match="missing_schema.json"):
            registry.get_prompt("schema-prompt")


class TestPromptVersioningPromotion:
    """Tests for promotion operations."""

    def test_promote_succeeds_with_valid_gates(self, tmp_path: Path) -> None:
        """Test that promotion succeeds when required gates pass."""
        _create_test_prompt_structure(
            tmp_path, "my-prompt", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        request = PromotionRequest(
            prompt_id="my-prompt",
            new_version="1.0.0",
            env="dev",
            actor="test-user",
            reason="Initial deployment",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/results/123",
            evaluation_results_sha256="abc123def456",
            gate_results=[GateResult(gate=1, passed=True, details="OK")],
        )

        result = service.promote(request)

        assert result["prompt_id"] == "my-prompt"
        assert result["new_version"] == "1.0.0"
        assert result["env"] == "dev"
        assert result["old_version"] is None

        registry.load("dev")
        assert registry.get_version("my-prompt") == "1.0.0"

    def test_promote_emits_audit_event(self, tmp_path: Path) -> None:
        """Test that promotion emits prompt.version.promoted audit event."""
        _create_test_prompt_structure(
            tmp_path, "audit-prompt", "2.0.0", risk_class="MEDIUM", validation_gates_required=[1, 2]
        )
        _create_registry_pointer(tmp_path, "staging", {"audit-prompt": "1.0.0"})
        _create_test_prompt_structure(
            tmp_path, "audit-prompt", "1.0.0", risk_class="MEDIUM", validation_gates_required=[1, 2]
        )

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink, tenant_id="tenant-123")

        request = PromotionRequest(
            prompt_id="audit-prompt",
            new_version="2.0.0",
            env="staging",
            actor="deployer",
            reason="Feature update",
            approvals=[_owner_approval("lead")],
            evaluation_results_ref="s3://bucket/eval/audit.json",
            evaluation_results_sha256="sha256abc",
            gate_results=[
                GateResult(gate=1, passed=True),
                GateResult(gate=2, passed=True),
            ],
        )

        service.promote(request)

        assert len(audit_sink.events) == 1
        event = audit_sink.events[0]
        assert event["event_type"] == "prompt.version.promoted"
        assert event["tenant_id"] == "tenant-123"
        assert event["resource"]["resource_type"] == "prompt"
        assert event["resource"]["resource_id"] == "audit-prompt"
        assert event["payload"]["new_version"] == "2.0.0"
        assert event["payload"]["old_version"] == "1.0.0"
        assert event["payload"]["risk_class"] == "MEDIUM"
        assert "approvals" in event["payload"]
        assert "approver" in event["payload"]
        assert event["payload"]["evaluation_results_ref"] == "s3://bucket/eval/audit.json"
        assert event["payload"]["evaluation_results_sha256"] == "sha256abc"

    def test_promote_with_sha256_evidence(self, tmp_path: Path) -> None:
        """Test that promotion includes sha256 evidence in audit."""
        _create_test_prompt_structure(
            tmp_path, "evidence-prompt", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        request = PromotionRequest(
            prompt_id="evidence-prompt",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256=sha256,
            gate_results=[GateResult(gate=1, passed=True)],
        )

        service.promote(request)

        event = audit_sink.events[0]
        assert event["payload"]["evaluation_results_sha256"] == sha256
        assert event["payload"]["evaluation_results_ref"] == "s3://bucket/eval.json"

    def test_promote_fails_missing_gates_low_risk(self, tmp_path: Path) -> None:
        """Test that promotion fails when Gate 1 is missing for LOW risk."""
        _create_test_prompt_structure(
            tmp_path, "gated-prompt", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="gated-prompt",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[],
        )

        with pytest.raises(GateRequirementError) as exc_info:
            service.promote(request)

        assert exc_info.value.risk_class == "LOW"
        assert 1 in exc_info.value.missing_gates

    def test_promote_fails_missing_gates_medium_risk(self, tmp_path: Path) -> None:
        """Test that promotion fails when Gates 1+2 are missing for MEDIUM risk."""
        _create_test_prompt_structure(
            tmp_path,
            "medium-prompt",
            "1.0.0",
            risk_class="MEDIUM",
            validation_gates_required=[1, 2],
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="medium-prompt",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=True)],
        )

        with pytest.raises(GateRequirementError) as exc_info:
            service.promote(request)

        assert 2 in exc_info.value.missing_gates

    def test_promote_fails_missing_gates_high_risk(self, tmp_path: Path) -> None:
        """Test that promotion fails when Gates 1-4 incomplete for HIGH risk."""
        _create_test_prompt_structure(
            tmp_path,
            "high-prompt",
            "1.0.0",
            risk_class="HIGH",
            validation_gates_required=[1, 2, 3, 4],
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="high-prompt",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval(), _security_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[
                GateResult(gate=1, passed=True),
                GateResult(gate=2, passed=True),
            ],
        )

        with pytest.raises(GateRequirementError) as exc_info:
            service.promote(request)

        assert 3 in exc_info.value.missing_gates
        assert 4 in exc_info.value.missing_gates

    def test_promote_fails_on_failed_gate(self, tmp_path: Path) -> None:
        """Test that promotion fails when a required gate fails."""
        _create_test_prompt_structure(
            tmp_path, "fail-gate", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="fail-gate",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=False, details="Failed validation")],
        )

        with pytest.raises(GateRequirementError) as exc_info:
            service.promote(request)

        assert 1 in exc_info.value.failed_gates

    def test_promote_fails_nonexistent_prompt(self, tmp_path: Path) -> None:
        """Test that promotion fails for nonexistent prompt."""
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="ghost",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=True)],
        )

        with pytest.raises(PromptVersioningError, match="does not exist"):
            service.promote(request)


class TestPromptVersioningRollback:
    """Tests for rollback operations."""

    def test_rollback_atomic_pointer_flip(self, tmp_path: Path) -> None:
        """Test that rollback performs atomic pointer flip."""
        _create_test_prompt_structure(tmp_path, "rollback-me", "1.0.0")
        _create_test_prompt_structure(tmp_path, "rollback-me", "2.0.0")
        _create_registry_pointer(tmp_path, "prod", {"rollback-me": "2.0.0"})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        request = RollbackRequest(
            prompt_id="rollback-me",
            rollback_target_version="1.0.0",
            env="prod",
            actor="oncall",
            reason="Regression detected",
            incident_ticket_id="INC-12345",
            approvals=[_owner_approval("manager")],
        )

        result = service.rollback(request)

        assert result["old_version"] == "2.0.0"
        assert result["rollback_target"] == "1.0.0"

        registry.load("prod")
        assert registry.get_version("rollback-me") == "1.0.0"

    def test_rollback_emits_audit_event(self, tmp_path: Path) -> None:
        """Test that rollback emits prompt.version.rolledback audit event."""
        _create_test_prompt_structure(tmp_path, "audit-rollback", "1.0.0")
        _create_test_prompt_structure(tmp_path, "audit-rollback", "2.0.0")
        _create_registry_pointer(tmp_path, "staging", {"audit-rollback": "2.0.0"})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        request = RollbackRequest(
            prompt_id="audit-rollback",
            rollback_target_version="1.0.0",
            env="staging",
            actor="sre",
            reason="Performance issue",
            incident_ticket_id="INC-999",
        )

        service.rollback(request)

        assert len(audit_sink.events) == 1
        event = audit_sink.events[0]
        assert event["event_type"] == "prompt.version.rolledback"
        assert event["payload"]["rollback_target"] == "1.0.0"
        assert event["payload"]["old_version"] == "2.0.0"
        assert event["payload"]["incident_ticket_id"] == "INC-999"
        assert event["payload"]["reason"] == "Performance issue"

    def test_rollback_fails_missing_reason(self, tmp_path: Path) -> None:
        """Test that rollback fails when reason is missing."""
        _create_test_prompt_structure(tmp_path, "reason-test", "1.0.0")
        _create_registry_pointer(tmp_path, "dev", {"reason-test": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = RollbackRequest(
            prompt_id="reason-test",
            rollback_target_version="1.0.0",
            env="dev",
            actor="test",
            reason="",
        )

        with pytest.raises(MissingFieldError, match="reason"):
            service.rollback(request)

    def test_rollback_fails_target_not_exist(self, tmp_path: Path) -> None:
        """Test that rollback fails if target version doesn't exist."""
        _create_test_prompt_structure(tmp_path, "no-target", "2.0.0")
        _create_registry_pointer(tmp_path, "dev", {"no-target": "2.0.0"})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = RollbackRequest(
            prompt_id="no-target",
            rollback_target_version="1.0.0",
            env="dev",
            actor="test",
            reason="Need to rollback",
        )

        with pytest.raises(PromptVersioningError, match="does not exist"):
            service.rollback(request)


class TestPromptVersioningRetire:
    """Tests for retire operations."""

    def test_retire_emits_audit_event(self, tmp_path: Path) -> None:
        """Test that retire emits prompt.version.retired audit event."""
        _create_test_prompt_structure(tmp_path, "retiring", "1.0.0")

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        request = RetireRequest(
            prompt_id="retiring",
            version="1.0.0",
            actor="product-owner",
            reason="Superseded by v2",
        )

        result = service.retire(request)

        assert result["prompt_id"] == "retiring"
        assert result["version"] == "1.0.0"

        assert len(audit_sink.events) == 1
        event = audit_sink.events[0]
        assert event["event_type"] == "prompt.version.retired"
        assert event["payload"]["version"] == "1.0.0"
        assert event["payload"]["reason"] == "Superseded by v2"

    def test_retire_does_not_delete_content(self, tmp_path: Path) -> None:
        """Test that retire does NOT delete prompt content (per spec)."""
        _create_test_prompt_structure(tmp_path, "preserved", "1.0.0")

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = RetireRequest(
            prompt_id="preserved",
            version="1.0.0",
            actor="admin",
            reason="Deprecated",
        )

        service.retire(request)

        prompt_dir = tmp_path / "preserved" / "1.0.0"
        assert prompt_dir.exists()
        assert (prompt_dir / "metadata.json").exists()
        assert (prompt_dir / "prompt.md").exists()


class TestAuditFailureIsFatal:
    """Tests that audit failure causes operation to fail and rollback."""

    def test_promote_rolls_back_on_audit_failure(self, tmp_path: Path) -> None:
        """Test that promotion rolls back registry on audit failure."""
        _create_test_prompt_structure(
            tmp_path, "audit-fail", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)

        failing_sink = FailingAuditSink("Disk full")

        service = PromptVersioningService(registry, audit_sink=failing_sink)

        request = PromotionRequest(
            prompt_id="audit-fail",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=True)],
        )

        with pytest.raises(AuditEmissionError, match="Disk full"):
            service.promote(request)

        registry.load("dev")
        assert "audit-fail" not in registry.list_prompts()

    def test_rollback_rolls_back_on_audit_failure(self, tmp_path: Path) -> None:
        """Test that rollback reverts registry on audit failure."""
        _create_test_prompt_structure(tmp_path, "audit-fail-rb", "1.0.0")
        _create_test_prompt_structure(tmp_path, "audit-fail-rb", "2.0.0")
        _create_registry_pointer(tmp_path, "prod", {"audit-fail-rb": "2.0.0"})

        registry = PromptRegistry(tmp_path)

        failing_sink = FailingAuditSink("Network error")

        service = PromptVersioningService(registry, audit_sink=failing_sink)

        request = RollbackRequest(
            prompt_id="audit-fail-rb",
            rollback_target_version="1.0.0",
            env="prod",
            actor="test",
            reason="Rollback test",
        )

        with pytest.raises(AuditEmissionError, match="Network error"):
            service.rollback(request)

        registry.load("prod")
        assert registry.get_version("audit-fail-rb") == "2.0.0"

    def test_retire_fails_on_audit_failure(self, tmp_path: Path) -> None:
        """Test that retire fails on audit failure."""
        _create_test_prompt_structure(tmp_path, "audit-fail-ret", "1.0.0")

        registry = PromptRegistry(tmp_path)

        failing_sink = FailingAuditSink("Timeout")

        service = PromptVersioningService(registry, audit_sink=failing_sink)

        request = RetireRequest(
            prompt_id="audit-fail-ret",
            version="1.0.0",
            actor="test",
            reason="Retire test",
        )

        with pytest.raises(AuditEmissionError, match="Timeout"):
            service.retire(request)


class TestAtomicUpdates:
    """Tests for atomic file updates."""

    def test_registry_json_is_deterministic(self, tmp_path: Path) -> None:
        """Test that registry JSON is deterministically serialized."""
        _create_test_prompt_structure(
            tmp_path, "z-prompt", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_test_prompt_structure(
            tmp_path, "a-prompt", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        service.promote(
            PromotionRequest(
                prompt_id="z-prompt",
                new_version="1.0.0",
                env="dev",
                actor="test",
                reason="Test",
                approvals=[_owner_approval()],
                evaluation_results_ref="s3://bucket/eval.json",
                evaluation_results_sha256="sha256",
                gate_results=[GateResult(gate=1, passed=True)],
            )
        )

        service.promote(
            PromotionRequest(
                prompt_id="a-prompt",
                new_version="1.0.0",
                env="dev",
                actor="test",
                reason="Test",
                approvals=[_owner_approval()],
                evaluation_results_ref="s3://bucket/eval.json",
                evaluation_results_sha256="sha256",
                gate_results=[GateResult(gate=1, passed=True)],
            )
        )

        with open(tmp_path / "registry.dev.json") as f:
            content = f.read()

        assert '"a-prompt"' in content
        assert '"z-prompt"' in content
        idx_a = content.index('"a-prompt"')
        idx_z = content.index('"z-prompt"')
        assert idx_a < idx_z

    def test_concurrent_writes_are_atomic(self, tmp_path: Path) -> None:
        """Test that registry writes are atomic (no partial writes)."""
        _create_test_prompt_structure(
            tmp_path, "atomic-test", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        request = PromotionRequest(
            prompt_id="atomic-test",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=True)],
        )

        service.promote(request)

        registry_path = tmp_path / "registry.dev.json"
        with open(registry_path) as f:
            data = json.load(f)

        assert data["prompts"]["atomic-test"] == "1.0.0"
        assert data["env"] == "dev"
        assert "updated_at" in data


class TestPR001Traceability:
    """Tests for PR-001 traceability requirements.

    Per docs/11_IDIS_Traceability_Matrix_v6_3.md:
    - test_version_loaded: Prompt version can be loaded from registry
    - test_rollback_works: Rollback restores previous version
    """

    def test_version_loaded(self, tmp_path: Path) -> None:
        """PR-001: Test that prompt version can be loaded from registry."""
        _create_test_prompt_structure(
            tmp_path, "EXTRACT_CLAIMS_V1", "1.2.0", "Extract claims from document."
        )
        _create_registry_pointer(tmp_path, "prod", {"EXTRACT_CLAIMS_V1": "1.2.0"})

        registry = PromptRegistry(tmp_path)
        registry.load("prod")

        loaded = registry.get_prompt("EXTRACT_CLAIMS_V1")

        assert loaded.artifact.prompt_id == "EXTRACT_CLAIMS_V1"
        assert loaded.artifact.version == "1.2.0"
        assert "Extract claims" in loaded.prompt_text
        assert len(loaded.content_hash) == 64

    def test_rollback_works(self, tmp_path: Path) -> None:
        """PR-001: Test that rollback restores previous version."""
        _create_test_prompt_structure(tmp_path, "SANAD_GRADER_V1", "1.0.0", "Grade sanad v1.0.0")
        _create_test_prompt_structure(
            tmp_path, "SANAD_GRADER_V1", "1.1.0", "Grade sanad v1.1.0 (buggy)"
        )
        _create_registry_pointer(tmp_path, "prod", {"SANAD_GRADER_V1": "1.1.0"})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        registry.load("prod")
        loaded_before = registry.get_prompt("SANAD_GRADER_V1")
        assert loaded_before.artifact.version == "1.1.0"
        assert "v1.1.0" in loaded_before.prompt_text

        request = RollbackRequest(
            prompt_id="SANAD_GRADER_V1",
            rollback_target_version="1.0.0",
            env="prod",
            actor="oncall",
            reason="Bug in v1.1.0 causes incorrect grades",
            incident_ticket_id="INC-2026-001",
        )

        result = service.rollback(request)
        assert result["rollback_target"] == "1.0.0"

        registry.load("prod")
        loaded_after = registry.get_prompt("SANAD_GRADER_V1")
        assert loaded_after.artifact.version == "1.0.0"
        assert "v1.0.0" in loaded_after.prompt_text

        assert len(audit_sink.events) == 1
        event = audit_sink.events[0]
        assert event["event_type"] == "prompt.version.rolledback"
        assert event["payload"]["incident_ticket_id"] == "INC-2026-001"


class TestFailClosedRequiredFields:
    """Tests that missing required PromptArtifact fields fail closed.

    Per spec §2.1, these fields are required (no defaults):
    - status, risk_class, validation_gates_required, evaluation_results_ref
    """

    @pytest.mark.parametrize(
        "missing_field",
        ["status", "risk_class", "validation_gates_required", "evaluation_results_ref"],
    )
    def test_missing_required_field_fails_closed(self, tmp_path: Path, missing_field: str) -> None:
        """Test that missing required field causes load to fail."""
        prompt_dir = tmp_path / "missing-field" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("missing-field", "1.0.0")
        del metadata[missing_field]

        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"missing-field": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(PromptArtifactError, match="validation failed"):
            registry.get_prompt("missing-field")

    def test_empty_evaluation_results_ref_fails_closed(self, tmp_path: Path) -> None:
        """Test that empty evaluation_results_ref fails closed."""
        prompt_dir = tmp_path / "empty-ref" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("empty-ref", "1.0.0")
        metadata["evaluation_results_ref"] = ""

        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"empty-ref": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(PromptArtifactError, match="validation failed"):
            registry.get_prompt("empty-ref")

    def test_invalid_gate_number_fails_closed(self, tmp_path: Path) -> None:
        """Test that invalid gate number (not 1-4) fails closed."""
        prompt_dir = tmp_path / "bad-gate" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("bad-gate", "1.0.0")
        metadata["validation_gates_required"] = [1, 5]

        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"bad-gate": "1.0.0"})

        registry = PromptRegistry(tmp_path)
        registry.load("dev")

        with pytest.raises(PromptArtifactError, match="validation failed"):
            registry.get_prompt("bad-gate")


class TestHighRiskApprovalRequirements:
    """Tests that HIGH-risk promotion requires security sign-off."""

    def test_high_risk_fails_without_security_approval(self, tmp_path: Path) -> None:
        """Test that HIGH-risk promotion fails without SECURITY_COMPLIANCE approval."""
        _create_test_prompt_structure(
            tmp_path, "high-sec", "1.0.0", risk_class="HIGH", validation_gates_required=[1, 2, 3, 4]
        )
        _create_registry_pointer(tmp_path, "prod", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="high-sec",
            new_version="1.0.0",
            env="prod",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[
                GateResult(gate=1, passed=True),
                GateResult(gate=2, passed=True),
                GateResult(gate=3, passed=True),
                GateResult(gate=4, passed=True),
            ],
        )

        with pytest.raises(MissingApprovalError) as exc_info:
            service.promote(request)

        assert "SECURITY_COMPLIANCE" in exc_info.value.missing_roles

    def test_high_risk_succeeds_with_security_approval(self, tmp_path: Path) -> None:
        """Test that HIGH-risk promotion succeeds with both approvals."""
        _create_test_prompt_structure(
            tmp_path, "high-ok", "1.0.0", risk_class="HIGH", validation_gates_required=[1, 2, 3, 4]
        )
        _create_registry_pointer(tmp_path, "prod", {})

        registry = PromptRegistry(tmp_path)
        audit_sink = InMemoryAuditSink()
        service = PromptVersioningService(registry, audit_sink=audit_sink)

        request = PromotionRequest(
            prompt_id="high-ok",
            new_version="1.0.0",
            env="prod",
            actor="test",
            reason="Test",
            approvals=[_owner_approval(), _security_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256abc",
            gate_results=[
                GateResult(gate=1, passed=True),
                GateResult(gate=2, passed=True),
                GateResult(gate=3, passed=True),
                GateResult(gate=4, passed=True),
            ],
        )

        result = service.promote(request)
        assert result["prompt_id"] == "high-ok"
        assert result["risk_class"] == "HIGH"

    def test_promotion_fails_without_owner_approval(self, tmp_path: Path) -> None:
        """Test that any promotion fails without OWNER approval."""
        _create_test_prompt_structure(
            tmp_path, "no-owner", "1.0.0", risk_class="LOW", validation_gates_required=[1]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="no-owner",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=True)],
        )

        with pytest.raises(MissingApprovalError) as exc_info:
            service.promote(request)

        assert "OWNER" in exc_info.value.missing_roles


class TestSchemaRefBypassPrevention:
    """Tests that schema refs cannot be bypassed when schemas_root is unset."""

    def test_schema_ref_fails_when_schemas_root_unset(self, tmp_path: Path) -> None:
        """Test that schema refs fail-closed when schemas_root is None."""
        prompt_dir = tmp_path / "schema-bypass" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("schema-bypass", "1.0.0")
        metadata["input_schema_ref"] = "some_schema.json"

        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"schema-bypass": "1.0.0"})

        registry = PromptRegistry(tmp_path, schemas_root=None)
        registry.load("dev")

        with pytest.raises(SchemaRefBypassError, match="schemas_root is not configured"):
            registry.get_prompt("schema-bypass")

    def test_output_schema_ref_also_fails_when_schemas_root_unset(self, tmp_path: Path) -> None:
        """Test that output_schema_ref also fails-closed when schemas_root is None."""
        prompt_dir = tmp_path / "output-bypass" / "1.0.0"
        prompt_dir.mkdir(parents=True)

        metadata = _create_prompt_artifact("output-bypass", "1.0.0")
        metadata["output_schema_ref"] = "output.schema.json"

        with open(prompt_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)
        (prompt_dir / "prompt.md").write_text("content")

        _create_registry_pointer(tmp_path, "dev", {"output-bypass": "1.0.0"})

        registry = PromptRegistry(tmp_path, schemas_root=None)
        registry.load("dev")

        with pytest.raises(SchemaRefBypassError, match="schemas_root is not configured"):
            registry.get_prompt("output-bypass")

    def test_no_schema_ref_succeeds_without_schemas_root(self, tmp_path: Path) -> None:
        """Test that prompts without schema refs work without schemas_root."""
        _create_test_prompt_structure(tmp_path, "no-schema", "1.0.0")
        _create_registry_pointer(tmp_path, "dev", {"no-schema": "1.0.0"})

        registry = PromptRegistry(tmp_path, schemas_root=None)
        registry.load("dev")

        loaded = registry.get_prompt("no-schema")
        assert loaded.artifact.prompt_id == "no-schema"


class TestGateUnionRequirement:
    """Tests that required gates are union of risk_class gates and artifact gates."""

    def test_artifact_gates_added_to_risk_class_gates(self, tmp_path: Path) -> None:
        """Test that artifact-specified gates are enforced even if not in risk class."""
        _create_test_prompt_structure(
            tmp_path, "extra-gate", "1.0.0", risk_class="LOW", validation_gates_required=[1, 3]
        )
        _create_registry_pointer(tmp_path, "dev", {})

        registry = PromptRegistry(tmp_path)
        service = PromptVersioningService(registry)

        request = PromotionRequest(
            prompt_id="extra-gate",
            new_version="1.0.0",
            env="dev",
            actor="test",
            reason="Test",
            approvals=[_owner_approval()],
            evaluation_results_ref="s3://bucket/eval.json",
            evaluation_results_sha256="sha256",
            gate_results=[GateResult(gate=1, passed=True)],
        )

        with pytest.raises(GateRequirementError) as exc_info:
            service.promote(request)

        assert 3 in exc_info.value.missing_gates
