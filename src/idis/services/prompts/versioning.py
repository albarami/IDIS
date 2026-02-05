"""Prompt Versioning Service - atomic promotion/rollback/retire with audit.

Implements promotion pipeline from IDIS_Prompt_Registry_and_Model_Policy_v6_3.md §8:
- Required gates by risk class (LOW/MEDIUM/HIGH)
- Atomic pointer updates (temp file + os.replace)
- Deterministic JSON serialization (sorted keys)

Audit events per Go-Live checklist (IDIS_Master_Execution_Plan_v6_3.md §4.4):
- prompt.version.promoted
- prompt.version.rolledback
- prompt.version.retired

Design requirements:
- Fail-closed: missing gates, invalid versions, audit failure => hard fail
- Atomic updates: write temp → os.replace
- Deterministic: stable JSON ordering
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from idis.audit.sink import AuditSink, AuditSinkError, InMemoryAuditSink
from idis.services.prompts.registry import (
    PromptArtifact,
    PromptArtifactError,
    PromptRegistry,
    RegistryPointer,
    RiskClass,
    validate_semver,
)

logger = logging.getLogger(__name__)


class PromptVersioningError(Exception):
    """Base exception for prompt versioning errors."""

    pass


class GateRequirementError(PromptVersioningError):
    """Raised when required gate results are missing or failed."""

    def __init__(self, risk_class: str, missing_gates: list[int], failed_gates: list[int]) -> None:
        self.risk_class = risk_class
        self.missing_gates = missing_gates
        self.failed_gates = failed_gates
        parts = []
        if missing_gates:
            parts.append(f"missing gates: {missing_gates}")
        if failed_gates:
            parts.append(f"failed gates: {failed_gates}")
        super().__init__(
            f"Gate requirements not met for risk class {risk_class}: {'; '.join(parts)}"
        )


class AuditEmissionError(PromptVersioningError):
    """Raised when audit event emission fails (fatal - operation must fail)."""

    def __init__(self, event_type: str, reason: str) -> None:
        self.event_type = event_type
        self.reason = reason
        super().__init__(f"Audit emission failed for '{event_type}': {reason}")


class RollbackTargetError(PromptVersioningError):
    """Raised when rollback target version doesn't exist."""

    def __init__(self, prompt_id: str, target_version: str) -> None:
        self.prompt_id = prompt_id
        self.target_version = target_version
        super().__init__(
            f"Rollback target version '{target_version}' does not exist for prompt '{prompt_id}'"
        )


class MissingApprovalError(PromptVersioningError):
    """Raised when required approvals are missing."""

    def __init__(self, missing_roles: list[str]) -> None:
        self.missing_roles = missing_roles
        super().__init__(f"Missing required approval roles: {missing_roles}")


class MissingEvidenceError(PromptVersioningError):
    """Raised when required evidence fields are missing for promotion."""

    def __init__(self, missing_fields: list[str]) -> None:
        self.missing_fields = missing_fields
        super().__init__(f"Missing required evidence fields for promotion: {missing_fields}")


class MissingFieldError(PromptVersioningError):
    """Raised when required fields are missing."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"Missing required field: {field}")


class ApprovalRole(StrEnum):
    """Approval roles per spec §8.1."""

    OWNER = "OWNER"
    SECURITY_COMPLIANCE = "SECURITY_COMPLIANCE"


class Approval(BaseModel):
    """Approval record for promotion/rollback.

    Per spec §8.1: prompt owner approval required for all promotions.
    Per spec §8.2: security/compliance reviewer required for HIGH risk.
    """

    approver_id: str = Field(..., description="ID of the approver")
    role: ApprovalRole = Field(..., description="Role of the approver")
    approved_at: str | None = Field(default=None, description="ISO-8601 approval timestamp")


class GateResult(BaseModel):
    """Result of a single gate evaluation."""

    gate: int = Field(..., description="Gate number (1, 2, 3, 4)")
    passed: bool = Field(..., description="Whether the gate passed")
    details: str = Field(default="", description="Details about the result")


class PromotionRequest(BaseModel):
    """Request to promote a prompt version.

    Per Go-Live checklist §4.4:
    - evaluation_results_ref and evaluation_results_sha256 are REQUIRED
    - approvals must include at least OWNER role
    - HIGH risk requires SECURITY_COMPLIANCE approval
    """

    prompt_id: str
    new_version: str
    env: Literal["dev", "staging", "prod"]
    actor: str
    reason: str
    approvals: list[Approval] = Field(..., description="List of approvals (required)")
    evaluation_results_ref: str = Field(
        ..., description="Reference to evaluation results (required)"
    )
    evaluation_results_sha256: str = Field(
        ..., description="SHA256 of evaluation results (required)"
    )
    gate_results: list[GateResult] = Field(..., description="Gate results (required)")


class RollbackRequest(BaseModel):
    """Request to rollback a prompt version."""

    prompt_id: str
    rollback_target_version: str
    env: Literal["dev", "staging", "prod"]
    actor: str
    reason: str
    incident_ticket_id: str | None = Field(default=None, description="Incident ticket for rollback")
    approvals: list[Approval] = Field(default_factory=list, description="List of approvals")


class RetireRequest(BaseModel):
    """Request to retire a prompt version."""

    prompt_id: str
    version: str
    actor: str
    reason: str


REQUIRED_GATES_BY_RISK_CLASS: dict[RiskClass, list[int]] = {
    RiskClass.LOW: [1],
    RiskClass.MEDIUM: [1, 2],
    RiskClass.HIGH: [1, 2, 3, 4],
}


def _now_iso8601() -> str:
    """Get current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class PromptVersioningService:
    """Service for prompt version management with atomic updates and audit.

    All mutating operations:
    1. Validate preconditions (fail-closed)
    2. Enforce gate requirements by risk class
    3. Perform atomic file update (temp + replace)
    4. Emit audit event (failure is fatal)

    Audit events follow Go-Live checklist:
    - prompt.version.promoted
    - prompt.version.rolledback
    - prompt.version.retired
    """

    def __init__(
        self,
        registry: PromptRegistry,
        audit_sink: AuditSink | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Initialize the versioning service.

        Args:
            registry: PromptRegistry instance for loading/validating prompts.
            audit_sink: Audit sink for event emission. Default InMemoryAuditSink.
            tenant_id: Tenant context for audit events.
        """
        self._registry = registry
        self._audit_sink = audit_sink or InMemoryAuditSink()
        self._tenant_id = tenant_id or "system"

    def _emit_audit_event(
        self,
        event_type: str,
        prompt_id: str,
        version: str,
        details: dict[str, Any],
        actor: str,
    ) -> None:
        """Emit an audit event for prompt operations.

        FAIL-CLOSED: If audit emission fails, raise AuditEmissionError.
        The calling operation MUST NOT proceed if audit fails.

        Args:
            event_type: One of prompt.version.{promoted,rolledback,retired}
            prompt_id: Prompt identifier
            version: Version being operated on
            details: Additional details for the audit payload
            actor: Actor performing the operation

        Raises:
            AuditEmissionError: If audit emission fails
        """
        event_id = str(uuid.uuid4())
        occurred_at = _now_iso8601()

        event: dict[str, Any] = {
            "event_id": event_id,
            "occurred_at": occurred_at,
            "tenant_id": self._tenant_id,
            "event_type": event_type,
            "severity": "HIGH",
            "summary": f"Prompt {event_type.split('.')[-1]}: {prompt_id}@{version}",
            "actor": {
                "actor_type": "HUMAN",
                "actor_id": actor,
                "roles": [],
            },
            "request": {
                "request_id": f"prompt-{event_id[:8]}",
                "method": "POST",
                "path": f"/internal/prompts/{prompt_id}/versions/{version}",
            },
            "resource": {
                "resource_type": "prompt",
                "resource_id": prompt_id,
            },
            "payload": {
                "prompt_id": prompt_id,
                "version": version,
                **details,
            },
        }

        try:
            self._audit_sink.emit(event)
        except AuditSinkError as e:
            raise AuditEmissionError(event_type, str(e)) from e
        except Exception as e:
            raise AuditEmissionError(event_type, f"Unexpected error: {e}") from e

    def _load_registry_pointer(self, env: str) -> RegistryPointer:
        """Load the registry pointer for an environment.

        Args:
            env: Environment (dev, staging, prod)

        Returns:
            RegistryPointer

        Raises:
            PromptVersioningError: If registry cannot be loaded
        """
        registry_path = self._registry.prompts_root / f"registry.{env}.json"

        if not registry_path.exists():
            return RegistryPointer(
                env=env,  # type: ignore[arg-type]
                updated_at=_now_iso8601(),
                prompts={},
            )

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)
            return RegistryPointer.model_validate(data)
        except Exception as e:
            raise PromptVersioningError(f"Cannot load registry for {env}: {e}") from e

    def _write_registry_pointer_atomic(self, env: str, pointer: RegistryPointer) -> None:
        """Write registry pointer atomically (temp + replace).

        Deterministic JSON: sorted keys, 2-space indent.

        Args:
            env: Environment
            pointer: RegistryPointer to write

        Raises:
            PromptVersioningError: If write fails
        """
        registry_path = self._registry.prompts_root / f"registry.{env}.json"

        pointer.updated_at = _now_iso8601()

        data = pointer.model_dump()
        json_content = json.dumps(data, sort_keys=True, indent=2) + "\n"

        registry_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            fd, temp_path = tempfile.mkstemp(
                dir=str(registry_path.parent),
                prefix=".registry_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json_content)
                os.replace(temp_path, registry_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise
        except Exception as e:
            raise PromptVersioningError(f"Failed to write registry: {e}") from e

    def _validate_gate_requirements(
        self,
        risk_class: RiskClass,
        artifact_required_gates: list[int],
        gate_results: list[GateResult],
    ) -> None:
        """Validate that required gates are present and passing.

        Gate requirements by risk class (per spec §8.2):
        - LOW: Gate 1
        - MEDIUM: Gate 1 + Gate 2
        - HIGH: Gate 1 + Gate 2 + Gate 3 + Gate 4

        Required gates = union(risk_class_required, artifact.validation_gates_required)

        Args:
            risk_class: Prompt risk classification
            artifact_required_gates: Gates required by artifact.validation_gates_required
            gate_results: List of gate results

        Raises:
            GateRequirementError: If required gates are missing or failed
        """
        risk_class_gates = set(REQUIRED_GATES_BY_RISK_CLASS[risk_class])
        artifact_gates = set(artifact_required_gates)
        required_gates = sorted(risk_class_gates | artifact_gates)

        provided_gates = {gr.gate: gr for gr in gate_results}

        missing_gates = [g for g in required_gates if g not in provided_gates]
        failed_gates = [
            g for g in required_gates if g in provided_gates and not provided_gates[g].passed
        ]

        if missing_gates or failed_gates:
            raise GateRequirementError(risk_class.value, missing_gates, failed_gates)

    def _validate_approvals(
        self,
        risk_class: RiskClass,
        approvals: list[Approval],
    ) -> None:
        """Validate that required approvals are present.

        Per spec §8.1:
        - All promotions require OWNER approval
        - HIGH risk requires SECURITY_COMPLIANCE approval in addition

        Args:
            risk_class: Prompt risk classification
            approvals: List of approvals

        Raises:
            MissingApprovalError: If required approvals are missing
        """
        approval_roles = {a.role for a in approvals}
        missing_roles: list[str] = []

        if ApprovalRole.OWNER not in approval_roles:
            missing_roles.append(ApprovalRole.OWNER.value)

        if risk_class == RiskClass.HIGH and ApprovalRole.SECURITY_COMPLIANCE not in approval_roles:
            missing_roles.append(ApprovalRole.SECURITY_COMPLIANCE.value)

        if missing_roles:
            raise MissingApprovalError(missing_roles)

    def _validate_prompt_exists(self, prompt_id: str, version: str) -> PromptArtifact:
        """Validate that a prompt artifact exists.

        Args:
            prompt_id: Prompt identifier
            version: Version string

        Returns:
            PromptArtifact

        Raises:
            PromptVersioningError: If prompt doesn't exist or is invalid
        """
        if not validate_semver(version):
            raise PromptVersioningError(f"Invalid version '{version}': must be MAJOR.MINOR.PATCH")

        prompt_dir = self._registry.prompts_root / prompt_id / version
        if not prompt_dir.exists():
            raise PromptVersioningError(f"Prompt artifact '{prompt_id}@{version}' does not exist")

        try:
            artifact, _ = self._registry._load_prompt_artifact(prompt_id, version)
            return artifact
        except PromptArtifactError as e:
            raise PromptVersioningError(str(e)) from e

    def promote(self, request: PromotionRequest) -> dict[str, Any]:
        """Promote a prompt version to an environment.

        Process (per spec §8.1-8.2 and Go-Live §4.4):
        1. Validate prompt artifact exists
        2. Validate approval requirements (OWNER for all, +SECURITY_COMPLIANCE for HIGH)
        3. Validate gate requirements (union of risk_class + artifact gates)
        4. Update registry pointer atomically
        5. Emit prompt.version.promoted audit event (fail-closed)

        Args:
            request: PromotionRequest with all required fields

        Returns:
            Dict with promotion details including new registry state

        Raises:
            PromptVersioningError: On validation failure
            MissingApprovalError: On missing required approvals
            GateRequirementError: On missing/failed gates
            AuditEmissionError: On audit failure (operation rolled back)
        """
        artifact = self._validate_prompt_exists(request.prompt_id, request.new_version)

        self._validate_approvals(artifact.risk_class, request.approvals)

        self._validate_gate_requirements(
            artifact.risk_class,
            artifact.validation_gates_required,
            request.gate_results,
        )

        pointer = self._load_registry_pointer(request.env)

        old_version = pointer.prompts.get(request.prompt_id)

        pointer.prompts[request.prompt_id] = request.new_version

        self._write_registry_pointer_atomic(request.env, pointer)

        owner_approval = next((a for a in request.approvals if a.role == ApprovalRole.OWNER), None)
        approver_id = owner_approval.approver_id if owner_approval else request.actor

        audit_details: dict[str, Any] = {
            "env": request.env,
            "old_version": old_version,
            "new_version": request.new_version,
            "risk_class": artifact.risk_class.value,
            "approver": approver_id,
            "approvals": [a.model_dump() for a in request.approvals],
            "reason": request.reason,
            "gate_results": [gr.model_dump() for gr in request.gate_results],
            "evaluation_results_ref": request.evaluation_results_ref,
            "evaluation_results_sha256": request.evaluation_results_sha256,
        }

        try:
            self._emit_audit_event(
                event_type="prompt.version.promoted",
                prompt_id=request.prompt_id,
                version=request.new_version,
                details=audit_details,
                actor=request.actor,
            )
        except AuditEmissionError:
            if old_version is not None:
                pointer.prompts[request.prompt_id] = old_version
            else:
                del pointer.prompts[request.prompt_id]
            self._write_registry_pointer_atomic(request.env, pointer)
            raise

        return {
            "prompt_id": request.prompt_id,
            "env": request.env,
            "old_version": old_version,
            "new_version": request.new_version,
            "risk_class": artifact.risk_class.value,
            "promoted_at": pointer.updated_at,
        }

    def rollback(self, request: RollbackRequest) -> dict[str, Any]:
        """Rollback a prompt to a previous version.

        Per normative spec: atomic pointer flip back to target version.
        Does NOT require incident_ticket_id but it should be provided for SEV-1/2.

        Process:
        1. Validate target version exists
        2. Update registry pointer atomically
        3. Emit prompt.version.rolledback audit event (fail-closed)

        Args:
            request: RollbackRequest with required fields

        Returns:
            Dict with rollback details

        Raises:
            RollbackTargetError: If target version doesn't exist
            MissingFieldError: If required fields are missing
            AuditEmissionError: On audit failure
        """
        if not request.reason:
            raise MissingFieldError("reason")

        self._validate_prompt_exists(request.prompt_id, request.rollback_target_version)

        pointer = self._load_registry_pointer(request.env)

        old_version = pointer.prompts.get(request.prompt_id)

        pointer.prompts[request.prompt_id] = request.rollback_target_version

        self._write_registry_pointer_atomic(request.env, pointer)

        audit_details: dict[str, Any] = {
            "env": request.env,
            "old_version": old_version,
            "rollback_target": request.rollback_target_version,
            "reason": request.reason,
            "approvals": [a.model_dump() for a in request.approvals],
        }

        if request.incident_ticket_id:
            audit_details["incident_ticket_id"] = request.incident_ticket_id

        try:
            self._emit_audit_event(
                event_type="prompt.version.rolledback",
                prompt_id=request.prompt_id,
                version=request.rollback_target_version,
                details=audit_details,
                actor=request.actor,
            )
        except AuditEmissionError:
            if old_version is not None:
                pointer.prompts[request.prompt_id] = old_version
            else:
                del pointer.prompts[request.prompt_id]
            self._write_registry_pointer_atomic(request.env, pointer)
            raise

        return {
            "prompt_id": request.prompt_id,
            "env": request.env,
            "old_version": old_version,
            "rollback_target": request.rollback_target_version,
            "rolledback_at": pointer.updated_at,
        }

    def retire(self, request: RetireRequest) -> dict[str, Any]:
        """Retire a prompt version (mark as deprecated).

        Per normative spec: does NOT delete the prompt content
        (prompts used in past deliverables must remain available).

        Emits prompt.version.retired audit event.

        Args:
            request: RetireRequest

        Returns:
            Dict with retire details

        Raises:
            PromptVersioningError: If prompt doesn't exist
            AuditEmissionError: On audit failure
        """
        self._validate_prompt_exists(request.prompt_id, request.version)

        audit_details: dict[str, Any] = {
            "version": request.version,
            "reason": request.reason,
        }

        self._emit_audit_event(
            event_type="prompt.version.retired",
            prompt_id=request.prompt_id,
            version=request.version,
            details=audit_details,
            actor=request.actor,
        )

        return {
            "prompt_id": request.prompt_id,
            "version": request.version,
            "retired_at": _now_iso8601(),
            "reason": request.reason,
        }
