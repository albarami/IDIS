"""StepProvenance — safe, typed strict-run provenance for a single run step.

Captures the five strict observability dimensions using closed enums and
safe-token strings only. By construction it cannot carry raw env values, DSNs,
filesystem paths, object keys, or provider payloads.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

# Health/probe statuses are controlled lowercase-snake tokens (e.g. "contract_only",
# "missing_config"). This pattern rejects DSNs, paths, object keys, API keys, and raw
# env values (which contain ':', '/', '=', '-', uppercase, or whitespace).
_SAFE_HEALTH_STATUS_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ComponentMode(StrEnum):
    """How a strict component is wired/used (mirrors StrictComponentStatus)."""

    LIVE_WIRED_AND_USED = "live-wired-and-used"
    CODE_EXISTS_BUT_NOT_WIRED = "code-exists-but-not-wired"
    CONFIGURED_BUT_FAILED_HEALTH_CHECK = "configured-but-failed-health-check"
    MISSING_CREDENTIALS = "missing-credentials"
    MISSING_INFRASTRUCTURE = "missing-infrastructure"
    NOT_IMPLEMENTED = "not-implemented"
    UNKNOWN = "unknown"


class EnvSourceClass(StrEnum):
    """Source class for a component's configuration — never the value itself."""

    PROCESS_ENV = "process_env"
    DOTENV = "dotenv"
    MISSING = "missing"
    UNKNOWN = "unknown"


class RuntimeUseStatus(StrEnum):
    """Whether the component was actually used at runtime."""

    USED = "used"
    NOT_USED = "not_used"
    UNKNOWN = "unknown"


class OutputVisibilityStatus(StrEnum):
    """Whether the component's output is visible in the run."""

    VISIBLE = "visible"
    NOT_VISIBLE = "not_visible"
    UNKNOWN = "unknown"


class StepProvenance(BaseModel):
    """Safe strict-run provenance for one run step.

    Attributes:
        component_name: Safe component identifier token.
        component_mode: Wiring/use mode class.
        env_source_class: Configuration source class (not the value).
        health_status: Safe health/probe status token.
        runtime_use_status: Whether the component ran at runtime.
        output_visibility_status: Whether the component output is visible.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    component_name: str
    component_mode: ComponentMode
    env_source_class: EnvSourceClass
    health_status: str
    runtime_use_status: RuntimeUseStatus
    output_visibility_status: OutputVisibilityStatus

    @field_validator("health_status")
    @classmethod
    def _reject_unsafe_health_status(cls, value: str) -> str:
        """Reject secret-like values (DSNs, paths, object keys, API keys, raw env values)."""
        if not _SAFE_HEALTH_STATUS_PATTERN.match(value):
            raise ValueError(
                "health_status must be a safe lowercase token "
                "(no paths, URIs, secrets, or raw values)"
            )
        return value
