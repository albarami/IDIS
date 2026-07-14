"""Slice99 Task 5 - OpenAPI/schema/client contract lock (RED-first).

Pins the contract-lock contract:

1. A committed lock (``contracts/contract_lock.json``) pins the sha256 of
   ``openapi/IDIS_OpenAPI_v6_3.yaml`` and EVERY ``schemas/**/*.json``, plus a snapshot of the
   OpenAPI contract surface (paths -> methods -> operation ids, response codes,
   required request fields).
2. Verification FAILS CLOSED when the lock is missing/invalid, when any locked file's hash
   drifts, or when a lockable file appears that the lock does not cover - drift requires an
   INTENTIONAL regeneration.
3. A breaking-change guard against the LOCKED surface fails on removed paths/operations/
   response codes and on newly-required request fields.
4. A UI sync check proves ``ui/src/lib/openapi.ts`` references only paths/operations that
   exist in the locked spec.
5. The regenerate/verify path succeeds for the current committed contracts (the committed
   lock is correct and regeneration is deterministic/idempotent).

No OpenAPI client codegen anywhere. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from idis.contracts import (
    CONTRACT_LOCK_RELATIVE_PATH,
    build_lock_document,
    verify_contract_lock,
    write_lock_document,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC_RELATIVE = "openapi/IDIS_OpenAPI_v6_3.yaml"


# ---------------------------------------------------------------------------
# helpers: a tmp repo with the REAL spec + schemas + a minimal generated UI client
# ---------------------------------------------------------------------------


def _tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "openapi").mkdir(parents=True)
    shutil.copyfile(_REPO_ROOT / _SPEC_RELATIVE, repo / _SPEC_RELATIVE)
    shutil.copytree(_REPO_ROOT / "schemas", repo / "schemas")
    ui_dir = repo / "ui" / "src" / "lib"
    ui_dir.mkdir(parents=True)
    (ui_dir / "openapi.ts").write_text(
        "export interface paths {\n"
        '    "/v1/deals": {\n'
        '        get: operations["listDeals"];\n'
        '        post: operations["createDeal"];\n'
        "    };\n"
        "}\n",
        encoding="utf-8",
    )
    return repo


def _load_tmp_spec(repo: Path) -> dict[str, Any]:
    return yaml.safe_load((repo / _SPEC_RELATIVE).read_text(encoding="utf-8"))


def _save_tmp_spec(repo: Path, spec: dict[str, Any]) -> None:
    (repo / _SPEC_RELATIVE).write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


def _codes(result: dict[str, Any]) -> set[str]:
    return {f["code"] for f in result["findings"]}


# ---------------------------------------------------------------------------
# 1. missing/invalid lock fails closed
# ---------------------------------------------------------------------------


def test_missing_lock_fails_closed(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "CONTRACT_LOCK_MISSING" in _codes(result)


def test_malformed_lock_fails_closed(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    lock_path = repo / CONTRACT_LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{ not json", encoding="utf-8")

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "CONTRACT_LOCK_INVALID" in _codes(result)


# ---------------------------------------------------------------------------
# 2. hash drift fails unless the lock is intentionally regenerated
# ---------------------------------------------------------------------------


def test_schema_hash_drift_fails(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    target = repo / "schemas" / "audit_event.schema.json"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    mismatches = [f for f in result["findings"] if f["code"] == "CONTRACT_HASH_MISMATCH"]
    assert any("schemas/audit_event.schema.json" in f["detail"] for f in mismatches)


def test_openapi_hash_drift_fails_and_regeneration_recovers(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    spec = _load_tmp_spec(repo)
    spec["info"]["description"] = "mutated for contract-lock test"
    _save_tmp_spec(repo, spec)

    drifted = verify_contract_lock(repo)
    assert drifted["ok"] is False
    assert "CONTRACT_HASH_MISMATCH" in _codes(drifted)

    write_lock_document(repo)  # the INTENTIONAL regeneration
    assert verify_contract_lock(repo)["ok"] is True


def test_new_unlocked_schema_file_fails(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    (repo / "schemas" / "brand_new.schema.json").write_text("{}", encoding="utf-8")

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "CONTRACT_FILE_UNLOCKED" in _codes(result)


def test_deleted_locked_file_fails(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    (repo / "schemas" / "claim.schema.json").unlink()

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "CONTRACT_FILE_MISSING" in _codes(result)


# ---------------------------------------------------------------------------
# 3. breaking-change guard against the locked surface
# ---------------------------------------------------------------------------


def test_removed_path_is_breaking(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    spec = _load_tmp_spec(repo)
    del spec["paths"]["/v1/deals"]
    _save_tmp_spec(repo, spec)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "BREAKING_PATH_REMOVED" in _codes(result)


def test_removed_operation_is_breaking(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    spec = _load_tmp_spec(repo)
    del spec["paths"]["/v1/deals"]["post"]
    _save_tmp_spec(repo, spec)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "BREAKING_OPERATION_REMOVED" in _codes(result)


def test_removed_response_code_is_breaking(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    spec = _load_tmp_spec(repo)
    responses = spec["paths"]["/v1/deals"]["get"]["responses"]
    removed = next(iter(sorted(responses)))
    del responses[removed]
    _save_tmp_spec(repo, spec)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "BREAKING_RESPONSE_REMOVED" in _codes(result)


def test_newly_required_request_field_is_breaking(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    write_lock_document(repo)
    spec = _load_tmp_spec(repo)

    request_schema = spec["paths"]["/v1/deals"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    if "$ref" in request_schema:
        component = request_schema["$ref"].rsplit("/", 1)[-1]
        target = spec["components"]["schemas"][component]
    else:
        target = request_schema
    target.setdefault("required", []).append("slice99_newly_required_field")
    _save_tmp_spec(repo, spec)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "BREAKING_REQUIRED_FIELD_ADDED" in _codes(result)


# ---------------------------------------------------------------------------
# 4. UI sync check against the locked spec
# ---------------------------------------------------------------------------


def test_stale_ui_path_and_operation_fail(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    ui = repo / "ui" / "src" / "lib" / "openapi.ts"
    ui.write_text(
        "export interface paths {\n"
        '    "/v1/deals": {\n'
        '        get: operations["listDeals"];\n'
        "    };\n"
        '    "/v1/ghost-endpoint": {\n'
        '        get: operations["ghostOperation"];\n'
        "    };\n"
        "}\n",
        encoding="utf-8",
    )
    write_lock_document(repo)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    stale = [f for f in result["findings"] if f["code"] == "UI_STALE_REFERENCE"]
    details = " ".join(f["detail"] for f in stale)
    assert "/v1/ghost-endpoint" in details
    assert "ghostOperation" in details


def test_missing_ui_client_is_reported_not_silently_passed(tmp_path: Path) -> None:
    repo = _tmp_repo(tmp_path)
    (repo / "ui" / "src" / "lib" / "openapi.ts").unlink()
    write_lock_document(repo)

    result = verify_contract_lock(repo)

    assert result["ok"] is False
    assert "UI_CLIENT_MISSING" in _codes(result)


# ---------------------------------------------------------------------------
# 5. the committed lock is correct, deterministic, and idempotent
# ---------------------------------------------------------------------------


def test_committed_lock_exists_and_verifies() -> None:
    lock_path = _REPO_ROOT / CONTRACT_LOCK_RELATIVE_PATH
    assert lock_path.is_file(), "contracts/contract_lock.json must be committed"

    result = verify_contract_lock(_REPO_ROOT)

    assert result["ok"] is True, f"committed contracts must verify: {result['findings']}"


def test_lock_regeneration_is_deterministic_and_idempotent() -> None:
    committed = json.loads((_REPO_ROOT / CONTRACT_LOCK_RELATIVE_PATH).read_text(encoding="utf-8"))

    rebuilt_once = build_lock_document(_REPO_ROOT)
    rebuilt_twice = build_lock_document(_REPO_ROOT)

    assert rebuilt_once == rebuilt_twice
    assert rebuilt_once == committed, (
        "regenerating the lock from the committed contracts must reproduce the committed lock"
    )


def test_lock_covers_every_schema_file_recursively() -> None:
    committed = json.loads((_REPO_ROOT / CONTRACT_LOCK_RELATIVE_PATH).read_text(encoding="utf-8"))
    locked_files = set(committed["files"])

    on_disk = {
        path.relative_to(_REPO_ROOT).as_posix() for path in (_REPO_ROOT / "schemas").rglob("*.json")
    }
    on_disk.add(_SPEC_RELATIVE)

    assert locked_files == on_disk
