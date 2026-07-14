"""Slice99 Task 1 - prompt governance integrity + promotion audit-core repair (RED-first).

Pins four contracts:

1. Every ``prompt.version.{promoted,rolledback,retired}`` audit event follows the compliance
   core-audit convention: schema-valid against BOTH the Python validator and
   ``schemas/audit_event.schema.json`` (method POST, an ``/internal/prompts/...`` path with a
   status_code, a ``{safe, hashes, refs}`` payload, a UUID tenant), and the emitter calls
   ``validate_audit_event()`` BEFORE ``audit_sink.emit`` - fail-closed, with the registry pointer
   rolled back when the event cannot be validated or emitted.
2. ``prompt`` resource_type parity: the JSON schema enum and the Python validator accept the same
   resource types (closes the pre-Slice99 gap where ``prompt`` existed only in the validator).
3. ``python -m idis prompts validate`` walks ``prompts/registry.yaml`` + on-disk artifacts and
   fails closed on invalid/partial/unregistered artifacts, while DECLARED-only entries (nothing on
   disk yet) and missing evaluation evidence surface as WARNINGS, not fabricated passes.
4. The validate command is wired into the CI ``check`` job (parsed from the actual workflow).

PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from idis.audit.sink import AuditSink, AuditSinkError, InMemoryAuditSink
from idis.cli import main as cli_main
from idis.services.prompts.registry import PromptRegistry
from idis.services.prompts.versioning import (
    Approval,
    ApprovalRole,
    GateResult,
    PromotionRequest,
    PromptVersioningError,
    PromptVersioningService,
    RetireRequest,
    RollbackRequest,
)
from idis.validators.audit_event_validator import (
    VALID_RESOURCE_TYPES,
    validate_audit_event,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers: build a governed prompt artifact tree (PromptRegistry <id>/<version> layout)
# ---------------------------------------------------------------------------


def _metadata(
    prompt_id: str,
    version: str,
    *,
    status: str = "DRAFT",
    risk_class: str = "LOW",
    gates: list[int] | None = None,
    eval_ref: str = "evals/example/results.json",
) -> dict[str, Any]:
    return {
        "prompt_id": prompt_id,
        "name": prompt_id,
        "version": version,
        "status": status,
        "owner": "governance/test",
        "created_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T00:00:00Z",
        "risk_class": risk_class,
        "validation_gates_required": gates if gates is not None else [1],
        "evaluation_results_ref": eval_ref,
    }


def _write_artifact(
    prompts_root: Path,
    prompt_id: str,
    version: str,
    **meta_overrides: Any,
) -> None:
    d = prompts_root / prompt_id / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.md").write_text(f"# {prompt_id} {version}\nBody.\n", encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps(_metadata(prompt_id, version, **meta_overrides), indent=2),
        encoding="utf-8",
    )


def _service(
    prompts_root: Path,
    sink: AuditSink,
    tenant_id: str | None = None,
) -> PromptVersioningService:
    registry = PromptRegistry(prompts_root)
    if tenant_id is None:
        return PromptVersioningService(registry, audit_sink=sink)
    return PromptVersioningService(registry, audit_sink=sink, tenant_id=tenant_id)


def _promotion_request(prompt_id: str, version: str) -> PromotionRequest:
    return PromotionRequest(
        prompt_id=prompt_id,
        new_version=version,
        env="dev",
        actor="governance-actor",
        reason="scheduled promotion",
        approvals=[Approval(approver_id="owner-1", role=ApprovalRole.OWNER)],
        evaluation_results_ref="evals/example/results.json",
        evaluation_results_sha256="a" * 64,
        gate_results=[GateResult(gate=1, passed=True)],
    )


def _assert_core_audit_convention(event: dict[str, Any]) -> None:
    """The Slice98 compliance core-audit convention, applied to prompt governance events."""
    result = validate_audit_event(event)
    assert result.passed, f"event must be schema-valid, got errors: {result.errors}"
    assert event["request"]["method"] == "POST"
    assert str(event["request"]["path"]).startswith("/internal/prompts/")
    assert isinstance(event["request"].get("status_code"), int)
    payload = event["payload"]
    assert set(payload.keys()) <= {"safe", "hashes", "refs"}, (
        f"payload must be {{safe,hashes,refs}} only, got {sorted(payload.keys())}"
    )
    assert event["resource"]["resource_type"] == "prompt"


# ---------------------------------------------------------------------------
# 1. prompt.version.* events are schema-valid (core-audit convention)
# ---------------------------------------------------------------------------


def test_promote_emits_schema_valid_core_audit_event(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "extract_claims_test", "1.0.0")
    sink = InMemoryAuditSink()
    svc = _service(tmp_path, sink)

    svc.promote(_promotion_request("extract_claims_test", "1.0.0"))

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_type"] == "prompt.version.promoted"
    _assert_core_audit_convention(event)
    safe = event["payload"]["safe"]
    assert safe["prompt_id"] == "extract_claims_test"
    assert safe["new_version"] == "1.0.0"
    assert safe["env"] == "dev"
    # evaluation evidence is carried as hash + ref, not as free-form payload keys
    assert any(h.endswith("a" * 64) for h in event["payload"]["hashes"])
    assert any("evals/example/results.json" in r for r in event["payload"]["refs"])


def test_rollback_and_retire_emit_schema_valid_core_audit_events(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "p1", "1.0.0")
    _write_artifact(tmp_path, "p1", "1.1.0")
    sink = InMemoryAuditSink()
    svc = _service(tmp_path, sink)

    svc.promote(_promotion_request("p1", "1.1.0"))
    svc.rollback(
        RollbackRequest(
            prompt_id="p1",
            rollback_target_version="1.0.0",
            env="dev",
            actor="governance-actor",
            reason="regression observed",
            incident_ticket_id="INC-123",
        )
    )
    svc.retire(
        RetireRequest(
            prompt_id="p1",
            version="1.1.0",
            actor="governance-actor",
            reason="superseded",
        )
    )

    types = [e["event_type"] for e in sink.events]
    assert types == [
        "prompt.version.promoted",
        "prompt.version.rolledback",
        "prompt.version.retired",
    ]
    for event in sink.events:
        _assert_core_audit_convention(event)


# ---------------------------------------------------------------------------
# 2. validate-before-emit is fail-closed (invalid event => no emit, pointer rolled back)
# ---------------------------------------------------------------------------


def test_promote_fails_closed_when_event_cannot_validate(tmp_path: Path) -> None:
    """A non-UUID tenant makes the event schema-invalid: promote must fail BEFORE any emit,
    and the registry pointer must be rolled back (fail-closed governance)."""
    _write_artifact(tmp_path, "p2", "1.0.0")
    sink = InMemoryAuditSink()
    svc = _service(tmp_path, sink, tenant_id="not-a-uuid")

    with pytest.raises(PromptVersioningError):
        svc.promote(_promotion_request("p2", "1.0.0"))

    assert sink.events == [], "an invalid event must never reach the sink"
    pointer = tmp_path / "registry.dev.json"
    if pointer.exists():
        data = json.loads(pointer.read_text(encoding="utf-8"))
        assert "p2" not in data.get("prompts", {}), "pointer must be rolled back"


class _ExplodingSink(AuditSink):
    def emit(self, event: dict[str, Any]) -> None:
        raise AuditSinkError("sink unavailable")


def test_promote_rolls_back_pointer_when_emit_fails(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "p3", "1.0.0")
    svc = _service(tmp_path, _ExplodingSink())

    with pytest.raises(PromptVersioningError):
        svc.promote(_promotion_request("p3", "1.0.0"))

    pointer = tmp_path / "registry.dev.json"
    if pointer.exists():
        data = json.loads(pointer.read_text(encoding="utf-8"))
        assert "p3" not in data.get("prompts", {}), "pointer must be rolled back on emit failure"


# ---------------------------------------------------------------------------
# 3. resource_type parity: JSON schema enum == Python validator set
# ---------------------------------------------------------------------------


def test_prompt_resource_type_schema_parity() -> None:
    schema = json.loads(
        (_REPO_ROOT / "schemas" / "audit_event.schema.json").read_text(encoding="utf-8")
    )
    enum = set(schema["properties"]["resource"]["properties"]["resource_type"]["enum"])
    assert "prompt" in enum, "'prompt' must be registered in the JSON schema enum"
    assert enum == set(VALID_RESOURCE_TYPES), (
        "JSON schema resource_type enum and Python VALID_RESOURCE_TYPES must be identical; "
        f"schema-only={sorted(enum - set(VALID_RESOURCE_TYPES))}, "
        f"validator-only={sorted(set(VALID_RESOURCE_TYPES) - enum)}"
    )


# ---------------------------------------------------------------------------
# 4. python -m idis prompts validate (registry.yaml + artifact tree governance)
# ---------------------------------------------------------------------------


def _yaml_entry(
    prompt_id: str,
    version: str,
    *,
    status: str = "DRAFT",
    risk_class: str = "LOW",
    file_path: str | None = None,
    metadata_path: str | None = None,
) -> str:
    lines = [
        f"  {prompt_id}:",
        f'    id: "{prompt_id}"',
        f'    name: "{prompt_id}"',
        f'    version: "{version}"',
        f"    status: {status}",
        f"    risk_class: {risk_class}",
    ]
    if file_path is not None:
        lines.append(f'    file_path: "{file_path}"')
    if metadata_path is not None:
        lines.append(f'    metadata_path: "{metadata_path}"')
    return "\n".join(lines) + "\n"


def _write_tree(
    repo_root: Path,
    entries: list[str],
) -> Path:
    prompts_root = repo_root / "prompts"
    prompts_root.mkdir(parents=True, exist_ok=True)
    (prompts_root / "registry.yaml").write_text(
        'registry:\n  version: "1.0.0"\n  updated_at: "2026-07-14"\nprompts:\n' + "".join(entries),
        encoding="utf-8",
    )
    return prompts_root


def _materialize(
    repo_root: Path, family: str, version: str, **meta_overrides: Any
) -> tuple[str, str]:
    d = repo_root / "prompts" / family / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.md").write_text(f"# {family}\nBody.\n", encoding="utf-8")
    meta = _metadata(family, version, **meta_overrides)
    (d / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    rel = f"prompts/{family}/{version}"
    return f"{rel}/prompt.md", f"{rel}/metadata.json"


def _run_validate(repo_root: Path, out: Path) -> tuple[int, dict[str, Any]]:
    code = cli_main(
        [
            "prompts",
            "validate",
            "--prompts-root",
            str(repo_root / "prompts"),
            "--repo-root",
            str(repo_root),
            "--out",
            str(out),
        ]
    )
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return code, report


def test_prompts_validate_passes_on_valid_tree(tmp_path: Path) -> None:
    fp, mp = _materialize(tmp_path, "alpha", "1.0.0")
    _write_tree(tmp_path, [_yaml_entry("alpha", "1.0.0", file_path=fp, metadata_path=mp)])

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code == 0, report
    assert report["ok"] is True
    assert report["errors"] == []


def test_prompts_validate_fails_on_bad_semver(tmp_path: Path) -> None:
    fp, mp = _materialize(tmp_path, "alpha", "1.0.0")
    _write_tree(tmp_path, [_yaml_entry("alpha", "v1", file_path=fp, metadata_path=mp)])

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code != 0
    assert any(
        "SEMVER" in e["code"].upper() or "semver" in e["detail"].lower() for e in report["errors"]
    )


def test_prompts_validate_fails_on_yaml_metadata_version_mismatch(tmp_path: Path) -> None:
    fp, mp = _materialize(tmp_path, "alpha", "1.0.0")
    _write_tree(tmp_path, [_yaml_entry("alpha", "2.0.0", file_path=fp, metadata_path=mp)])

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code != 0
    assert any(e["code"] == "CONSISTENCY_MISMATCH" for e in report["errors"])


def test_prompts_validate_fails_on_dangling_schema_ref(tmp_path: Path) -> None:
    fp, mp = _materialize(
        tmp_path,
        "alpha",
        "1.0.0",
    )
    meta_path = tmp_path / mp
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["input_schema_ref"] = "schemas/does/not/exist.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _write_tree(tmp_path, [_yaml_entry("alpha", "1.0.0", file_path=fp, metadata_path=mp)])

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code != 0
    assert any(e["code"] == "SCHEMA_REF_MISSING" for e in report["errors"])


def test_prompts_validate_fails_on_partially_materialized_artifact(tmp_path: Path) -> None:
    """prompt.md exists but metadata.json missing => hard fail (not a warning)."""
    d = tmp_path / "prompts" / "alpha" / "1.0.0"
    d.mkdir(parents=True)
    (d / "prompt.md").write_text("# alpha\n", encoding="utf-8")
    _write_tree(
        tmp_path,
        [
            _yaml_entry(
                "alpha",
                "1.0.0",
                file_path="prompts/alpha/1.0.0/prompt.md",
                metadata_path="prompts/alpha/1.0.0/metadata.json",
            )
        ],
    )

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code != 0
    assert any(e["code"] == "ARTIFACT_MISSING" for e in report["errors"])


def test_prompts_validate_warns_on_declared_not_materialized(tmp_path: Path) -> None:
    """A yaml entry with nothing on disk is a declared intention: WARN, exit 0."""
    fp, mp = _materialize(tmp_path, "alpha", "1.0.0")
    _write_tree(
        tmp_path,
        [
            _yaml_entry("alpha", "1.0.0", file_path=fp, metadata_path=mp),
            _yaml_entry(
                "declared_only",
                "1.0.0",
                file_path="prompts/declared_only/1.0.0/prompt.md",
                metadata_path="prompts/declared_only/1.0.0/metadata.json",
            ),
        ],
    )

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code == 0, report
    assert any(w["code"] == "DECLARED_NOT_MATERIALIZED" for w in report["warnings"])


def test_prompts_validate_warns_on_missing_eval_evidence(tmp_path: Path) -> None:
    """evaluation_results_ref pointing at a nonexistent file is honest-but-unproven: WARN."""
    fp, mp = _materialize(tmp_path, "alpha", "1.0.0", eval_ref="evals/alpha/1.0.0/results.json")
    _write_tree(tmp_path, [_yaml_entry("alpha", "1.0.0", file_path=fp, metadata_path=mp)])

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code == 0, report
    assert any(w["code"] == "EVAL_EVIDENCE_MISSING" for w in report["warnings"])


def test_prompts_validate_fails_on_unregistered_artifact_dir(tmp_path: Path) -> None:
    """An on-disk prompt family with no registry entry is ungoverned: hard fail."""
    fp, mp = _materialize(tmp_path, "alpha", "1.0.0")
    _materialize(tmp_path, "rogue_prompt", "1.0.0")
    _write_tree(tmp_path, [_yaml_entry("alpha", "1.0.0", file_path=fp, metadata_path=mp)])

    code, report = _run_validate(tmp_path, tmp_path / "report.json")

    assert code != 0
    assert any(
        e["code"] == "UNREGISTERED_ARTIFACT" and "rogue_prompt" in e["detail"]
        for e in report["errors"]
    )


def test_prompts_validate_real_repo_tree_is_governed(tmp_path: Path) -> None:
    """THE wire-and-prove case: the repo's actual prompts/ tree must validate.

    Declared-only entries and missing eval evidence may WARN; nothing may ERROR."""
    code, report = _run_validate(_REPO_ROOT, tmp_path / "report.json")

    assert code == 0, f"repo prompt tree must be governed; errors: {report.get('errors')}"
    assert report["ok"] is True


def test_runtime_referenced_prompts_are_registered_and_valid(tmp_path: Path) -> None:
    """Every prompt id the runtime actually stamps into provenance must be governed:
    registered in prompts/registry.yaml AND materialized AND valid."""
    import yaml as _yaml

    from idis.api.routes import runs as runs_module

    runtime_ids = {
        runs_module._EXTRACTION_PROMPT_ID,
        runs_module._SCORING_PROMPT_ID,
        *runs_module._DEBATE_PROMPT_IDS,
    }

    registry = _yaml.safe_load(
        (_REPO_ROOT / "prompts" / "registry.yaml").read_text(encoding="utf-8")
    )
    registered = set((registry.get("prompts") or {}).keys())

    missing = sorted(runtime_ids - registered)
    assert not missing, f"runtime-referenced prompts missing from registry.yaml: {missing}"

    code, report = _run_validate(_REPO_ROOT, tmp_path / "report.json")
    assert code == 0
    failed = {e.get("prompt_id") for e in report.get("errors", [])}
    assert not (runtime_ids & failed), (
        f"runtime-referenced prompts failed validation: {sorted(runtime_ids & failed)}"
    )


# ---------------------------------------------------------------------------
# 5. CI wiring: the validate command actually runs in the check job
# ---------------------------------------------------------------------------


def test_ci_check_job_runs_prompts_validate() -> None:
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m idis prompts validate" in ci, (
        "the CI check job must run the prompt governance validation command"
    )
