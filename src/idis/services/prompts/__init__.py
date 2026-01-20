"""IDIS Prompt Registry Service.

Provides versioned prompt artifact management with audited promotion/rollback.

Key components:
- PromptRegistry: Fail-closed loader and validator for prompt artifacts
- PromptVersioningService: Atomic promotion/rollback with gate enforcement and audit
"""

from idis.services.prompts.registry import (
    InvalidPromptVersionError,
    PromptArtifact,
    PromptNotFoundError,
    PromptRegistry,
    PromptRegistryError,
    SchemaRefNotFoundError,
)
from idis.services.prompts.versioning import (
    AuditEmissionError,
    GateRequirementError,
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
    "PromptVersioningService",
    "PromptVersioningError",
    "GateRequirementError",
    "AuditEmissionError",
]
