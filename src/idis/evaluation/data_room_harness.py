"""Local Slice 17 harness for arbitrary data-room FULL-run handoff."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import FULL_STEPS, StepName
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.runs.data_room_inventory_package import (
    InMemoryRunDataRoomInventoryPackageService,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from idis.services.runs.steps import build_run_context

DEFERRED_LEGACY_STEP_REASONS: dict[str, str] = {
    "ENRICHMENT": "external_enrichment_execution_deferred",
    "DEBATE": "layer2_execution_deferred",
    "ANALYSIS": "layer2_analysis_deferred",
    "SCORING": "recommendation_scoring_deferred",
    "DELIVERABLES": "deliverables_deferred",
}


def run_data_room_harness(
    *,
    data_room_root: str | Path,
    mode: str = "FULL",
    tenant_id: str,
    deal_id: str,
    company_name: str,
    output_path: str | Path | None = None,
    parse_supported_files: bool = True,
) -> dict[str, Any]:
    """Run the local data-room harness and return a safe summary.

    Args:
        data_room_root: Arbitrary local data-room folder.
        mode: Run mode. Slice 17 supports ``FULL`` only.
        tenant_id: Tenant scope for the in-memory run.
        deal_id: Deal scope for the in-memory run.
        company_name: Explicit deal metadata consumed by the identity boundary.
        output_path: Optional JSON path for the safe summary.
        parse_supported_files: Whether parser-compatible files should be parsed.

    Returns:
        Safe run summary with no raw text, excerpts, or file contents.

    Raises:
        ValueError: If mode is not ``FULL``.
    """
    if mode != "FULL":
        raise ValueError("Slice 17 data-room harness supports FULL mode only")

    run_id = str(uuid.uuid4())
    root = Path(data_room_root)
    audit_sink = InMemoryAuditSink()
    run_steps_repo = InMemoryRunStepsRepository(tenant_id)
    orchestrator = RunOrchestrator(
        audit_sink=audit_sink,
        run_steps_repo=run_steps_repo,
    )
    ctx = _build_context(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        mode=mode,
        data_room_root=root,
        company_name=company_name,
        audit_sink=audit_sink,
        parse_supported_files=parse_supported_files,
    )

    result = orchestrator.execute(ctx)
    steps = run_steps_repo.get_by_run_id(run_id)
    summary = _safe_summary(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        mode=mode,
        data_room_root=root,
        run_status=result.status,
        block_reason=result.block_reason,
        error_code=result.error_code,
        steps=steps,
    )
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(summary, sort_keys=True, indent=2),
            encoding="utf-8",
        )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for the local data-room harness."""
    parser = argparse.ArgumentParser(
        description="Run the local IDIS data-room FULL harness.",
    )
    parser.add_argument("--data-room-root", required=True)
    parser.add_argument("--mode", default="FULL", choices=["FULL"])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--deal-id", required=True)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--output", dest="output_path")
    args = parser.parse_args(argv)

    summary = run_data_room_harness(
        data_room_root=args.data_room_root,
        mode=args.mode,
        tenant_id=args.tenant_id,
        deal_id=args.deal_id,
        company_name=args.company_name,
        output_path=args.output_path,
    )
    if args.output_path is None:
        print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


def _build_context(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    mode: str,
    data_room_root: Path,
    company_name: str,
    audit_sink: InMemoryAuditSink,
    parse_supported_files: bool,
) -> RunContext:
    ctx = build_run_context(
        db_conn=None,
        tenant_id=tenant_id,
        run_id=run_id,
        deal_id=deal_id,
        mode=mode,
        documents=[],
        deal_metadata={
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "company_name": company_name,
        },
        data_room_root_path=str(data_room_root),
        preflight_corpus=[],
        audit_sink=audit_sink,
    )
    ctx.data_room_inventory_fn = _inventory_fn(parse_supported_files=parse_supported_files)
    ctx.enrich_fn = _deferred_step_fn("ENRICHMENT")
    ctx.debate_fn = _deferred_step_fn("DEBATE")
    ctx.analysis_fn = _deferred_step_fn("ANALYSIS")
    ctx.scoring_fn = _deferred_step_fn("SCORING")
    ctx.deliverables_fn = _deferred_step_fn("DELIVERABLES")
    return ctx


def _inventory_fn(
    *, parse_supported_files: bool
) -> Callable[..., tuple[Any, list[Any], list[Any]]]:
    def run_inventory(**kwargs: Any) -> tuple[Any, list[Any], list[Any]]:
        return InMemoryRunDataRoomInventoryPackageService().run(
            parse_supported_files=parse_supported_files,
            **kwargs,
        )

    return run_inventory


def _deferred_step_fn(step_name: str) -> Callable[..., dict[str, Any]]:
    def run_deferred_step(**_: Any) -> dict[str, Any]:
        reason = DEFERRED_LEGACY_STEP_REASONS[step_name]
        return {
            "status": "DEFERRED",
            "reason_codes": [reason],
            "deferred": True,
            "boundary": "local data-room harness deferral",
        }

    return run_deferred_step


def _safe_summary(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    mode: str,
    data_room_root: Path,
    run_status: str,
    block_reason: str | None,
    error_code: str | None,
    steps: list[Any],
) -> dict[str, Any]:
    steps_by_name = {step.step_name: step for step in steps}
    step_summaries = [
        _step_summary(step_name=step_name, step=steps_by_name.get(step_name))
        for step_name in FULL_STEPS
    ]
    inventory = _inventory_summary(steps_by_name.get(StepName.DATA_ROOM_INVENTORY_PACKAGE))
    preflight = _preflight_summary(steps_by_name.get(StepName.DOCUMENT_PREFLIGHT))
    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "mode": mode,
        "data_room_root_hash": _hash_text(str(data_room_root.resolve())),
        "run_status": run_status,
        "block_reason": block_reason,
        "error_code": error_code,
        "steps": step_summaries,
        "block_reasons": _block_reasons(step_summaries),
        "deferred_reasons": _deferred_reasons(step_summaries),
        "deferred_step_names": _deferred_step_names(step_summaries),
        "inventory": inventory,
        "preflight": preflight,
        "methodology_packages": _methodology_packages(step_summaries),
    }


def _step_summary(*, step_name: StepName, step: Any | None) -> dict[str, Any]:
    if step is None:
        return {
            "step_name": step_name.value,
            "status": "NOT_STARTED",
            "error_code": None,
            "reason_codes": [],
        }
    result_summary = step.result_summary if isinstance(step.result_summary, dict) else {}
    return {
        "step_name": step.step_name.value,
        "status": step.status.value,
        "error_code": step.error_code,
        "result_status": _optional_string(result_summary.get("status")),
        "construction_status": _optional_string(result_summary.get("construction_status")),
        "readiness_status": _optional_string(result_summary.get("readiness_status")),
        "identity_status": _optional_string(result_summary.get("identity_status")),
        "reason_codes": _string_list(result_summary.get("reason_codes")),
        "package_ids": _package_ids(result_summary),
    }


def _inventory_summary(step: Any | None) -> dict[str, Any]:
    if step is None or not isinstance(step.result_summary, dict):
        return _empty_inventory_summary()
    result_summary = step.result_summary
    raw_summary = result_summary.get("summary")
    counts = raw_summary if isinstance(raw_summary, dict) else {}
    files = [
        _safe_file_summary(file)
        for file in result_summary.get("files", [])
        if isinstance(file, dict)
    ]
    return {
        "file_count": int(counts.get("file_count") or len(files)),
        "supported_file_count": int(counts.get("supported_file_count") or 0),
        "deferred_file_count": int(counts.get("deferred_file_count") or 0),
        "blocked_file_count": int(counts.get("blocked_file_count") or 0),
        "supported_document_count": int(counts.get("supported_document_count") or 0),
        "supported_document_ids": _string_list(result_summary.get("supported_document_ids")),
        "deferred_file_ids": _string_list(result_summary.get("deferred_file_ids")),
        "blocked_file_ids": _string_list(result_summary.get("blocked_file_ids")),
        "by_extension": _string_int_dict(counts.get("by_extension")),
        "by_file_status": _string_int_dict(counts.get("by_file_status")),
        "by_reason": _string_int_dict(counts.get("by_reason")),
        "files": files,
    }


def _empty_inventory_summary() -> dict[str, Any]:
    return {
        "file_count": 0,
        "supported_file_count": 0,
        "deferred_file_count": 0,
        "blocked_file_count": 0,
        "supported_document_count": 0,
        "supported_document_ids": [],
        "deferred_file_ids": [],
        "blocked_file_ids": [],
        "by_extension": {},
        "by_file_status": {},
        "by_reason": {},
        "files": [],
    }


def _safe_file_summary(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": _optional_string(file.get("file_id")),
        "relative_path": _optional_string(file.get("relative_path")),
        "path_hash": _optional_string(file.get("path_hash")),
        "extension": _optional_string(file.get("extension")),
        "size_bytes": int(file.get("size_bytes") or 0),
        "sha256": _optional_string(file.get("sha256")),
        "file_status": _optional_string(file.get("file_status")),
        "support_status": _optional_string(file.get("support_status")),
        "triage_status": _optional_string(file.get("triage_status")),
        "reason_codes": _string_list(file.get("reason_codes")),
        "artifact_id": _optional_string(file.get("artifact_id")),
        "document_id": _optional_string(file.get("document_id")),
    }


def _preflight_summary(step: Any | None) -> dict[str, Any]:
    if step is None or not isinstance(step.result_summary, dict):
        return {
            "status": None,
            "eligible_document_ids": [],
            "blocked_document_ids": [],
            "by_reason": {},
        }
    result_summary = step.result_summary
    raw_summary = result_summary.get("summary")
    counts = raw_summary if isinstance(raw_summary, dict) else {}
    return {
        "status": _optional_string(result_summary.get("status")),
        "eligible_document_ids": _string_list(result_summary.get("eligible_document_ids")),
        "blocked_document_ids": _string_list(result_summary.get("blocked_document_ids")),
        "by_reason": _string_int_dict(counts.get("by_reason")),
    }


def _methodology_packages(step_summaries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for step in step_summaries:
        step_name = str(step["step_name"])
        if not step_name.startswith("METHODOLOGY_"):
            continue
        packages[step_name] = {
            "status": step["status"],
            "result_status": step.get("result_status"),
            "construction_status": step.get("construction_status"),
            "readiness_status": step.get("readiness_status"),
            "identity_status": step.get("identity_status"),
            "package_ids": step.get("package_ids", []),
            "reason_codes": step.get("reason_codes", []),
            "error_code": step.get("error_code"),
        }
    return packages


def _block_reasons(step_summaries: list[dict[str, Any]]) -> list[str]:
    reasons = [
        str(step["error_code"])
        for step in step_summaries
        if step.get("error_code") is not None and str(step.get("error_code")).strip()
    ]
    return sorted(set(reasons))


def _deferred_reasons(step_summaries: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for step in step_summaries:
        reasons.extend(_string_list(step.get("reason_codes")))
    return sorted({reason for reason in reasons if "defer" in reason or "required" in reason})


def _deferred_step_names(step_summaries: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in step_summaries:
        status_values = {
            str(step.get("result_status") or "").lower(),
            str(step.get("readiness_status") or "").lower(),
        }
        if "deferred" in status_values:
            names.append(str(step["step_name"]))
    return sorted(names)


def _package_ids(result_summary: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key, value in result_summary.items():
        if key.endswith("_ids") or key.endswith("_id"):
            ids.extend(_string_list(value))
    return sorted(set(ids))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list | tuple | set):
        return sorted({str(item) for item in value if str(item).strip()})
    return []


def _string_int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(value[key]) for key in sorted(value)}


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
