"""S1 common helpers moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from idis.audit.sink import AuditSink
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
)

# Characters of raw stage text included in a STAGE_UNKNOWN step message.
_STAGE_UNKNOWN_MESSAGE_LIMIT = 120


def _resolve_run_audit_sink(audit_sink: AuditSink | None) -> AuditSink:
    """Resolve hermetic fallback only when strict FULL mode is not active."""
    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    if is_strict_full_live_required(dotenv_path=strict_dotenv_path):
        from idis.audit.postgres_sink import PostgresAuditSink

        if not isinstance(audit_sink, PostgresAuditSink):
            raise RuntimeError("AUDIT_FAILURE: strict run helper requires PostgresAuditSink")
        return audit_sink

    if audit_sink is not None:
        return audit_sink
    from idis.audit.sink import InMemoryAuditSink

    return InMemoryAuditSink()


def _claim_source_summary(claim_class: str, source_span_id: Any) -> str:
    """Build a concise claim source summary for evidence appendices."""
    span_summary = f"span {source_span_id}" if source_span_id else None
    parts = [part for part in (claim_class, span_summary) if part]
    return ", ".join(parts) if parts else "Extracted claim"


def _calc_output_summary(output: Any) -> str:
    """Render a calculation output to a short deterministic summary."""
    if isinstance(output, dict):
        value = output.get("primary_value") or output.get("value") or output.get("amount")
        unit = output.get("unit")
        currency = output.get("currency")
        parts = [str(item) for item in (value, currency, unit) if item not in (None, "")]
        return " ".join(parts) if parts else str(output)
    return str(output or "")


def _calc_output_payload(output: Any) -> dict[str, Any]:
    """Return the safe structured output fields used by product exports."""
    if not isinstance(output, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in ("primary_value", "value", "amount", "unit", "currency"):
        if key in output:
            payload[key] = output[key]
    secondary_values = output.get("secondary_values")
    if isinstance(secondary_values, dict):
        payload["secondary_values"] = secondary_values
    return payload


def _calc_assumptions(inputs: Any) -> dict[str, Any]:
    """Expose calculation inputs/metadata as deterministic assumptions."""
    if not isinstance(inputs, dict):
        return {}
    assumptions: dict[str, Any] = {}
    values = inputs.get("values")
    metadata = inputs.get("metadata")
    if isinstance(values, dict):
        assumptions["inputs"] = {str(key): value for key, value in sorted(values.items())}
    if isinstance(metadata, dict) and metadata:
        assumptions["metadata"] = {
            str(key): value for key, value in sorted(metadata.items()) if value not in (None, "")
        }
    return assumptions


def _calc_input_claim_ids(inputs: Any) -> list[str]:
    """Extract input claim IDs from calculation repository payloads."""
    if not isinstance(inputs, dict):
        return []
    raw_claim_ids = inputs.get("input_claim_ids") or inputs.get("claim_ids") or []
    if isinstance(raw_claim_ids, list):
        return sorted(str(item) for item in raw_claim_ids if str(item).strip())
    return []


def _analysis_context_stage_text(analysis_context: Any) -> str | None:
    """Return the analysis context stage text, or None when it is not text."""
    raw = getattr(analysis_context, "stage", None)
    if isinstance(raw, str):
        return raw
    return None


def _deal_metadata_stage_text(deal_metadata: Mapping[str, Any] | None) -> str | None:
    """Return the deal snapshot's stage text, or None when it is not text.

    Same rule as ``_analysis_context_stage_text``: only a string is visible.
    ``resolve_stage`` collapses whitespace for the lookup.
    """
    raw = (deal_metadata or {}).get("stage")
    if isinstance(raw, str):
        return raw
    return None


def _find_project_root() -> Path:
    """Walk up from this file to find the directory containing pyproject.toml.

    Returns:
        Path to the project root.

    Raises:
        FileNotFoundError: If pyproject.toml cannot be found.
    """
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    msg = "Cannot locate project root (no pyproject.toml found above %s)"
    raise FileNotFoundError(msg % Path(__file__).resolve())
