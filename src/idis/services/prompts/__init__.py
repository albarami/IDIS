"""IDIS Prompt Registry Service.

Provides versioned prompt artifact management with audited promotion/rollback.

Key components:
- PromptRegistry: Fail-closed loader and validator for prompt artifacts
- PromptVersioningService: Atomic promotion/rollback with gate enforcement and audit
"""

from idis.services.prompts.registry import (
    InvalidPromptVersionError,
    MissingRequiredFieldError,
    PromptArtifact,
    PromptNotFoundError,
    PromptRegistry,
    PromptRegistryError,
    SchemaRefBypassError,
    SchemaRefNotFoundError,
)
from idis.services.prompts.versioning import (
    Approval,
    ApprovalRole,
    AuditEmissionError,
    GateRequirementError,
    MissingApprovalError,
    MissingEvidenceError,
    PromptVersioningError,
    PromptVersioningService,
)

__all__ = [
    "PromptArtifact",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptNotFoundError",
    "InvalidPromptVersionError",
    "SchemaRefNotFoundError",
    "SchemaRefBypassError",
    "MissingRequiredFieldError",
    "PromptVersioningService",
    "PromptVersioningError",
    "GateRequirementError",
    "AuditEmissionError",
    "Approval",
    "ApprovalRole",
    "MissingApprovalError",
    "MissingEvidenceError",
]
