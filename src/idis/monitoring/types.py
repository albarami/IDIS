"""Typed spec models for IDIS monitoring dashboards and alerts.

Provides Pydantic models with fail-closed validation for:
- Dashboard specifications (Grafana-compatible)
- Alert rule specifications (Prometheus-compatible)

All validators raise MonitoringValidationError on invalid input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["SEV-1", "SEV-2", "SEV-3"]

RUNBOOK_PATH_PATTERN = re.compile(r"^docs/runbooks/RB-\d{2}_[a-z0-9_]+\.md$")


class MonitoringValidationError(Exception):
    """Raised when monitoring spec validation fails (fail-closed behavior)."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.message = message
        self.errors = errors or []
        super().__init__(message)


class DashboardPanelSpec(BaseModel):
    """Specification for a single dashboard panel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(..., ge=1, description="Unique panel ID within dashboard")
    title: str = Field(..., min_length=1, max_length=200)
    panel_type: Literal["graph", "stat", "gauge", "table", "timeseries"] = Field(
        default="timeseries"
    )
    expr: str = Field(..., min_length=1, description="PromQL or metric expression")
    description: str = Field(default="", max_length=500)

    @field_validator("title", "expr")
    @classmethod
    def no_empty_strings(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty or whitespace-only")
        return v


class DashboardSpec(BaseModel):
    """Specification for a Grafana dashboard with tenant isolation.

    Enforces:
    - Unique uid
    - Required tenant_id variable for tenant filtering
    - Non-empty panels list
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str = Field(..., min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    tags: tuple[str, ...] = Field(default=())
    panels: tuple[DashboardPanelSpec, ...] = Field(..., min_length=1)
    required_variables: tuple[str, ...] = Field(
        default=("tenant_id",),
        description="Required template variables; tenant_id is mandatory",
    )

    @field_validator("title")
    @classmethod
    def no_empty_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Title cannot be empty or whitespace-only")
        return v

    @field_validator("required_variables")
    @classmethod
    def must_include_tenant_id(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if "tenant_id" not in v:
            raise ValueError("required_variables must include 'tenant_id' for tenant isolation")
        return v

    @model_validator(mode="after")
    def validate_unique_panel_ids(self) -> DashboardSpec:
        panel_ids = [p.id for p in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("Panel IDs must be unique within a dashboard")
        return self


class AlertRuleSpec(BaseModel):
    """Specification for a Prometheus alert rule.

    Enforces:
    - Valid severity label (SEV-1, SEV-2, SEV-3)
    - Runbook annotation with valid path format
    - Summary and description annotations
    - Tenant-safe expressions (must include tenant_id filter where applicable)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_]+$")
    severity: Severity = Field(...)
    expr: str = Field(..., min_length=1, description="PromQL expression")
    for_duration: str = Field(..., pattern=r"^\d+[smh]$", description="Duration (e.g., '5m')")
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)

    @field_validator("expr")
    @classmethod
    def no_empty_expr(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Expression cannot be empty or whitespace-only")
        return v

    @model_validator(mode="after")
    def validate_required_annotations(self) -> AlertRuleSpec:
        errors: list[str] = []
        if "summary" not in self.annotations:
            errors.append("Missing required annotation: 'summary'")
        if "description" not in self.annotations:
            errors.append("Missing required annotation: 'description'")
        if "runbook" not in self.annotations:
            errors.append("Missing required annotation: 'runbook'")
        else:
            runbook = self.annotations["runbook"]
            if not RUNBOOK_PATH_PATTERN.match(runbook):
                errors.append(
                    f"Invalid runbook path format: '{runbook}'. "
                    "Must match 'docs/runbooks/RB-XX_name.md'"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self


def validate_dashboard_specs(dashboards: tuple[DashboardSpec, ...]) -> None:
    """Validate a collection of dashboard specs for uniqueness and completeness.

    Raises:
        MonitoringValidationError: If validation fails (fail-closed).
    """
    if not dashboards:
        raise MonitoringValidationError("Dashboard collection cannot be empty")

    errors: list[str] = []
    uids = [d.uid for d in dashboards]
    titles = [d.title for d in dashboards]

    # Check for duplicate UIDs
    seen_uids: set[str] = set()
    for uid in uids:
        if uid in seen_uids:
            errors.append(f"Duplicate dashboard UID: '{uid}'")
        seen_uids.add(uid)

    # Check for duplicate titles
    seen_titles: set[str] = set()
    for title in titles:
        if title in seen_titles:
            errors.append(f"Duplicate dashboard title: '{title}'")
        seen_titles.add(title)

    # Verify tenant_id variable in all dashboards
    for d in dashboards:
        if "tenant_id" not in d.required_variables:
            errors.append(f"Dashboard '{d.uid}' missing required tenant_id variable")

    if errors:
        raise MonitoringValidationError(
            f"Dashboard validation failed with {len(errors)} error(s)", errors=errors
        )


# Global audit alert names that are expected to have tenant_scope="global"
GLOBAL_AUDIT_ALERT_NAMES = frozenset({"IDISAuditIngestionLag", "IDISMissingAuditEvents"})


def _get_repo_root() -> Path:
    """Resolve repository root from this file's location.

    Resolution: src/idis/monitoring/types.py -> repo root is 3 levels up.
    """
    return Path(__file__).resolve().parents[3]


def validate_runbook_file_exists(runbook_path: str, alert_name: str, errors: list[str]) -> None:
    """Validate that a runbook file exists on disk (fail-closed).

    Args:
        runbook_path: Repo-root relative path to runbook (e.g., docs/runbooks/RB-01_api_outage.md)
        alert_name: Name of the alert referencing the runbook (for error messages)
        errors: List to append validation errors to
    """
    repo_root = _get_repo_root()
    full_path = repo_root / runbook_path
    if not full_path.is_file():
        errors.append(
            f"Alert '{alert_name}' references non-existent runbook: '{runbook_path}' "
            f"(resolved to '{full_path}')"
        )


def validate_alert_specs(
    alerts: tuple[AlertRuleSpec, ...], *, check_runbook_files: bool = True
) -> None:
    """Validate a collection of alert specs for uniqueness and completeness.

    Args:
        alerts: Tuple of alert specs to validate.
        check_runbook_files: If True, verify runbook files exist on disk (fail-closed).

    Raises:
        MonitoringValidationError: If validation fails (fail-closed).
    """
    if not alerts:
        raise MonitoringValidationError("Alert collection cannot be empty")

    errors: list[str] = []
    names = [a.name for a in alerts]

    # Check for duplicate names
    seen_names: set[str] = set()
    for name in names:
        if name in seen_names:
            errors.append(f"Duplicate alert name: '{name}'")
        seen_names.add(name)

    # Verify all SEV-1 alerts have runbook references
    for alert in alerts:
        if alert.severity == "SEV-1" and "runbook" not in alert.annotations:
            errors.append(f"SEV-1 alert '{alert.name}' missing runbook annotation")

    # Verify runbook paths are valid format
    for alert in alerts:
        runbook = alert.annotations.get("runbook", "")
        if runbook and not RUNBOOK_PATH_PATTERN.match(runbook):
            errors.append(f"Alert '{alert.name}' has invalid runbook path: '{runbook}'")

    # Verify runbook files exist on disk (fail-closed)
    if check_runbook_files:
        for alert in alerts:
            runbook = alert.annotations.get("runbook", "")
            if runbook:
                validate_runbook_file_exists(runbook, alert.name, errors)

    # Verify global audit alerts have tenant_scope annotation
    for alert in alerts:
        if alert.name in GLOBAL_AUDIT_ALERT_NAMES:
            tenant_scope = alert.annotations.get("tenant_scope")
            if tenant_scope != "global":
                errors.append(
                    f"Global audit alert '{alert.name}' must have "
                    f"annotations['tenant_scope']='global', got '{tenant_scope}'"
                )

    if errors:
        raise MonitoringValidationError(
            f"Alert validation failed with {len(errors)} error(s)", errors=errors
        )
