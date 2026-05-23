"""Strict full-live readiness report models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictComponentStatus(StrEnum):
    """Allowed strict full-live readiness states."""

    LIVE_WIRED_AND_USED = "live-wired-and-used"
    CODE_EXISTS_BUT_NOT_WIRED = "code-exists-but-not-wired"
    CONFIGURED_BUT_FAILED_HEALTH_CHECK = "configured-but-failed-health-check"
    MISSING_CREDENTIALS = "missing-credentials"
    MISSING_INFRASTRUCTURE = "missing-infrastructure"
    NOT_IMPLEMENTED = "not-implemented"


class StrictComponentReadiness(BaseModel):
    """Readiness result for one required full-live component."""

    model_config = ConfigDict(extra="forbid")

    component_name: str
    status: StrictComponentStatus
    blocker_message: str
    required_env_vars: list[str] = Field(default_factory=list)
    required_services: list[str] = Field(default_factory=list)
    evidence: str
    may_proceed: bool
    mode: str
    provenance: dict[str, str] = Field(default_factory=dict)


class StrictFullLiveReadinessReport(BaseModel):
    """Strict full-live preflight report."""

    model_config = ConfigDict(extra="forbid")

    required: bool
    may_proceed: bool
    blocker_count: int
    blocking_components: list[str]
    components: list[StrictComponentReadiness]
    env_config_inventory: list[StrictEnvVarInventory] = Field(default_factory=list)

    def component(self, component_name: str) -> StrictComponentReadiness:
        """Return a named component readiness result."""
        for component in self.components:
            if component.component_name == component_name:
                return component
        msg = f"Unknown strict full-live component: {component_name}"
        raise KeyError(msg)


class StrictEnvVarInventory(BaseModel):
    """Secret-free inventory of strict config propagation state."""

    model_config = ConfigDict(extra="forbid")

    env_var: str
    present_in_dotenv: bool
    loaded_in_process: bool
    read_by_code: bool
    wired_into_full: bool
    health_checked_live: bool
    note: str
