"""Command functions used by the obligation-register CLI."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.extract import extract_register
from idis.methodology.obligations.jsonl import canonical_json, write_json
from idis.methodology.obligations.manifest import (
    assemble_public_manifest,
    build_private_manifest,
    public_manifest_bytes,
)
from idis.methodology.obligations.models_authored import ViewRecord
from idis.methodology.obligations.models_records import ValidationReport
from idis.methodology.obligations.quote import locate_phrase
from idis.methodology.obligations.register_io import Register, load_register
from idis.methodology.obligations.runs import FieldAccounting, account_parent, field_text
from idis.methodology.obligations.segments import escape_text
from idis.methodology.obligations.status import (
    ledger_status,
    render_status_text,
    selected_shards,
    shard_counts,
    status_payload,
)
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import (
    QUOTE_FIELDS,
    SKIPPED_POLICY_ABSENT,
    Code,
    FindingSeverity,
)


def command_extract(
    workbook: Path,
    private_root: Path,
    *,
    template: Path | None = None,
    category_map: Path | None = None,
    policy: Path | None = None,
) -> str:
    """Extract a workbook and return a count summary."""
    report = extract_register(
        workbook,
        private_root,
        template_path=template,
        category_map_path=category_map,
        policy_path=policy,
    )
    return "\n".join(
        (
            f"workbook_sha256={report.workbook.sha256}",
            f"workbook_byte_count={report.workbook.byte_count}",
            f"parents={sum(item.count for item in report.data_row_counts)}",
            f"merged_ranges={sum(item.count for item in report.merged_range_counts)}",
            f"finding_codes={len(report.finding_codes)}",
            "",
        )
    )


def command_validate(
    private_root: Path,
    *,
    workbook: Path | None = None,
    policy: Path | None = None,
    shards: list[str] | None = None,
    view: str | None = None,
    output_format: str = "text",
) -> tuple[int, str]:
    """Validate a private root. Text from private files is not included."""
    report = validate_register(
        private_root,
        workbook_path=workbook,
        policy_path=policy,
        shards=shards,
        view=view,
    )
    rendered = (
        _render_validation(report)
        if output_format == "text"
        else canonical_json(_validation_payload(report)) + "\n"
    )
    return _exit_code(report), rendered


def command_runs(
    private_root: Path,
    parent: str,
    *,
    field: str = "question",
    show_text: bool = False,
    output_format: str = "text",
) -> tuple[int, str]:
    """List segments, runs and markers for one parent field."""
    if field not in QUOTE_FIELDS or output_format not in {"text", "json"}:
        raise Gl02Error(Code.GL02_USAGE.value)
    register = load_register(private_root)
    located = register.parent(parent) or register.parents_by_coordinate.get(parent)
    if located is None:
        raise Gl02Error(Code.GL02_USAGE.value)
    children = tuple(child for _shard, child in register.children)
    dispositions = tuple(item for _shard, item in register.dispositions)
    accounted = account_parent(located, children, dispositions)
    chosen = next(item for item in accounted if item.field == field)
    source = field_text(located, field) or ""
    disposition_keys = {
        (item.start, item.end, item.text_sha256)
        for item in dispositions
        if item.parent_id == located.parent_id and item.field == field
    }
    quote_spans = [(item.start, item.end, item.derivation.value) for item in chosen.quotes]
    payload = _runs_payload(
        located.parent_id,
        located.public_coordinate,
        field,
        chosen,
        source,
        disposition_keys,
        quote_spans,
        show_text=show_text,
    )
    if output_format == "json":
        return 0, canonical_json(payload) + "\n"
    return 0, _render_runs(payload)


def command_status(
    private_root: Path,
    *,
    shards: list[str] | None = None,
    output_format: str = "text",
) -> tuple[int, str]:
    """Print per-shard counts. Exit 0 unless the caller turns an I/O error into 2."""
    if output_format not in {"text", "json"}:
        raise Gl02Error(Code.GL02_USAGE.value)
    selected = selected_shards(shards)
    register = load_register(private_root)
    report = validate_register(private_root)
    rows = shard_counts(register, report, selected)
    ledger = ledger_status(register)
    if output_format == "json":
        return 0, canonical_json(status_payload(rows, ledger)) + "\n"
    return 0, render_status_text(rows, ledger)


def command_views(private_root: Path, *, output_format: str = "text") -> str:
    """List views. ``agreed_by`` is reported only as present or absent."""
    if output_format not in {"text", "json"}:
        raise Gl02Error(Code.GL02_USAGE.value)
    register = load_register(private_root)
    from idis.methodology.obligations.checks_links import check_views

    _findings, closures = check_views(register) if register.views_present else ([], {})
    rows = [
        _view_row(register, view, closures.get(view.view_id, frozenset()))
        for _shard, view in register.views
    ]
    rows.sort(key=lambda item: str(item["view_id"]))
    if output_format == "json":
        return canonical_json({"views": rows}) + "\n"
    lines = [_view_line(row) for row in rows]
    lines.append("")
    return "\n".join(lines)


def _view_row(
    register: Register,
    view: ViewRecord,
    closure: frozenset[str],
) -> dict[str, object]:
    coordinates = sorted(
        register.parents_by_id[parent_id].public_coordinate
        for parent_id in closure
        if parent_id in register.parents_by_id
    )
    agreed = bool(view.agreed_by and view.agreed_by.strip())
    return {
        "agreed_by_present": agreed,
        "closure": view.closure.value,
        "closure_coordinates": coordinates,
        "closure_parent_count": len(closure),
        "primary_parent_count": len(view.parent_ids),
        "status": view.status.value,
        "view_id": view.view_id,
    }


def _view_line(row: dict[str, object]) -> str:
    coordinates = row["closure_coordinates"]
    shown = ",".join(str(item) for item in coordinates) if isinstance(coordinates, list) else ""
    agreed = 1 if row["agreed_by_present"] is True else 0
    return (
        f"view_id={row['view_id']} status={row['status']} closure={row['closure']} "
        f"agreed_by_present={agreed} primary_parents={row['primary_parent_count']} "
        f"closure_parents={row['closure_parent_count']} coordinates={shown}"
    )


def command_quote(
    private_root: Path,
    parent: str,
    text: str,
    *,
    field: str = "question",
    show_text: bool = False,
) -> tuple[int, str]:
    """Locate a phrase in a parent field."""
    if field not in QUOTE_FIELDS:
        raise Gl02Error(Code.GL02_USAGE.value)
    register = load_register(private_root)
    located = register.parent(parent) or register.parents_by_coordinate.get(parent)
    if located is None:
        raise Gl02Error(Code.GL02_USAGE.value)
    source = field_text(located, field)
    if source is None:
        return 1, f"{Code.GL02_QUOTE_NOT_FOUND.value} count=0\n"
    found = locate_phrase(source, text)
    if isinstance(found, str):
        return 1, f"{found} count=0\n"
    lines: list[str] = []
    for hit in found:
        length = hit.end - hit.start
        line = f"occurrence={hit.occurrence} start={hit.start} end={hit.end} length={length}"
        if show_text:
            line += f" text={escape_text(source[hit.start : hit.end])}"
        lines.append(line)
    lines.append("")
    return 0, "\n".join(lines)


def command_manifest(
    private_root: Path,
    *,
    private_out: Path | None = None,
    public_out: Path | None = None,
) -> str:
    """Write manifests. The text returned to the CLI is counts only."""
    register = load_register(private_root)
    private_path = private_out or (private_root / "manifest" / "register_manifest.private.json")
    public_path = public_out or (private_root / "manifest" / "register_manifest.public.json")
    private_manifest = build_private_manifest(register)
    report = validate_register(private_root)
    public_manifest = assemble_public_manifest(register, report)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(private_path, private_manifest)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(public_manifest_bytes(public_manifest))
    counts = public_manifest["counts"]
    financial = counts["parents_financial"] if isinstance(counts, dict) else 0
    return (
        f"parents_financial={financial}\n"
        f"errors={sum(item.severity is FindingSeverity.ERROR for item in report.findings)}\n"
    )


def _runs_payload(
    parent_id: str,
    coordinate: str,
    field: str,
    chosen: FieldAccounting,
    source: str,
    disposition_keys: set[tuple[int, int, str]],
    quote_spans: list[tuple[int, int, str]],
    *,
    show_text: bool,
) -> dict[str, object]:
    segments: list[dict[str, object]] = []
    for segment in chosen.segments:
        row: dict[str, object] = {"end": segment.end, "start": segment.start}
        if show_text:
            row["text"] = escape_text(segment.text)
        segments.append(row)
    runs: list[dict[str, object]] = []
    for run in chosen.runs:
        status = _run_status(
            run.material, (run.start, run.end, run.text_sha256) in disposition_keys
        )
        row = {
            "content_tokens": run.content_tokens,
            "end": run.end,
            "material": run.material,
            "qualifier_count": run.qualifier_count,
            "start": run.start,
            "status": status,
        }
        if show_text:
            row["text"] = escape_text(source[run.start : run.end])
        runs.append(row)
    markers: list[dict[str, object]] = []
    for marker in chosen.markers:
        accounted = (marker.start, marker.end, marker.text_sha256) in disposition_keys or _quoted(
            marker.start, marker.end, marker.tier, quote_spans
        )
        row = {
            "end": marker.end,
            "start": marker.start,
            "status": "accounted" if accounted else "unaccounted",
            "tier": marker.tier,
        }
        if show_text:
            row["text"] = escape_text(marker.text)
        markers.append(row)
    return {
        "field": field,
        "markers": markers,
        "parent_id": parent_id,
        "public_coordinate": coordinate,
        "runs": runs,
        "segments": segments,
    }


def _run_status(material: bool, dispositioned: bool) -> str:
    if dispositioned:
        return "dispositioned"
    if material:
        return "undispositioned"
    return "counted"


def _render_runs(payload: dict[str, object]) -> str:
    field = payload["field"]
    lines = [
        f"parent_id={payload['parent_id']}",
        f"public_coordinate={payload['public_coordinate']}",
    ]
    lines.extend(_segment_lines(field, payload["segments"]))
    lines.extend(_run_lines(field, payload["runs"]))
    lines.extend(_marker_lines(field, payload["markers"]))
    lines.append("")
    return "\n".join(lines)


def _segment_lines(field: object, rows: object) -> list[str]:
    lines: list[str] = []
    for row in _rows(rows):
        line = f"segment field={field} start={row['start']} end={row['end']}"
        if "text" in row:
            line += f" text={row['text']}"
        lines.append(line)
    return lines


def _run_lines(field: object, rows: object) -> list[str]:
    lines: list[str] = []
    for row in _rows(rows):
        material = 1 if row["material"] else 0
        line = (
            "run "
            f"field={field} start={row['start']} end={row['end']} material={material} "
            f"content_tokens={row['content_tokens']} "
            f"qualifier_count={row['qualifier_count']} status={row['status']}"
        )
        if "text" in row:
            line += f" text={row['text']}"
        lines.append(line)
    return lines


def _marker_lines(field: object, rows: object) -> list[str]:
    lines: list[str] = []
    for row in _rows(rows):
        line = (
            "marker "
            f"field={field} start={row['start']} end={row['end']} tier={row['tier']} "
            f"status={row['status']}"
        )
        if "text" in row:
            line += f" text={row['text']}"
        lines.append(line)
    return lines


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise Gl02Error(Code.GL02_USAGE.value)
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise Gl02Error(Code.GL02_USAGE.value)
        rows.append(item)
    return rows


def _quoted(start: int, end: int, tier: str, quotes: list[tuple[int, int, str]]) -> bool:
    for quote_start, quote_end, derivation in quotes:
        if start < quote_start or end > quote_end:
            continue
        if tier == "reference" and derivation != "cross_reference_applied":
            continue
        return True
    return False


def _render_validation(report: ValidationReport) -> str:
    lines = [
        f"register_complete={int(report.register_complete)}",
        f"errors={sum(item.severity is FindingSeverity.ERROR for item in report.findings)}",
        f"warnings={sum(item.severity is FindingSeverity.WARNING for item in report.findings)}",
        f"info={sum(item.severity is FindingSeverity.INFO for item in report.findings)}",
    ]
    for finding in report.findings:
        lines.append(finding.message)
    lines.extend(_skip_lines(report))
    lines.append("")
    return "\n".join(lines)


def _skip_lines(report: ValidationReport) -> list[str]:
    """Render skipped checks. A policy-less derived comparison says why it was skipped."""
    policy_compare = any(
        invariant_id == "I-14" and code == SKIPPED_POLICY_ABSENT
        for invariant_id, code in report.skipped_invariants
    )
    lines: list[str] = []
    for skip in report.skipped_checks:
        if skip == SKIPPED_POLICY_ABSENT and policy_compare:
            lines.append(
                "skipped=SKIPPED_POLICY_ABSENT scope=derived count=1 ids=policy "
                "message=derived comparison skipped because the policy file was absent"
            )
            continue
        lines.append(f"skipped={skip}")
    return lines


def _validation_payload(report: ValidationReport) -> dict[str, object]:
    return {
        "findings": [finding.model_dump(mode="json") for finding in report.findings],
        "register_complete": report.register_complete,
        "skipped_checks": list(report.skipped_checks),
        "skipped_invariants": [
            {"code": code, "invariant": invariant_id}
            for invariant_id, code in report.skipped_invariants
        ],
    }


def _exit_code(report: ValidationReport) -> int:
    if any(item.severity is FindingSeverity.ERROR for item in report.findings):
        return 1
    return 0
