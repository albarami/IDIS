"""OpenAPI/schema/client contract lock (Slice99 Task 5).

A committed lock (``contracts/contract_lock.json``) pins:
- the sha256 of ``openapi/IDIS_OpenAPI_v6_3.yaml`` and EVERY ``schemas/**/*.json``; and
- a snapshot of the OpenAPI contract surface (paths -> methods -> operation id, response
  codes, top-level required request fields).

``verify_contract_lock`` fails closed on: a missing/invalid lock, hash drift of any locked
file, lockable files the lock does not cover, deleted locked files, BREAKING changes vs the
locked surface (removed paths/operations/response codes, newly-required request fields), and
stale ``ui/src/lib/openapi.ts`` references (paths/operations absent from the locked spec).

Drift is resolved only by an INTENTIONAL regeneration (``write_lock_document`` /
``python scripts/contract_lock.py regen``), which forces the change through review. There is
no client codegen here - the generated UI client is only cross-checked, never produced.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

CONTRACT_LOCK_RELATIVE_PATH = Path("contracts") / "contract_lock.json"
OPENAPI_RELATIVE_PATH = Path("openapi") / "IDIS_OpenAPI_v6_3.yaml"
SCHEMAS_RELATIVE_DIR = Path("schemas")
UI_CLIENT_RELATIVE_PATH = Path("ui") / "src" / "lib" / "openapi.ts"

LOCK_VERSION = 1

_HTTP_METHODS = ("delete", "get", "head", "options", "patch", "post", "put", "trace")

_UI_PATH_PATTERN = re.compile(r'^\s*"(/[^"]*)":\s*\{', re.MULTILINE)
_UI_OPERATION_PATTERN = re.compile(r'operations\["([A-Za-z0-9_]+)"\]')


def _finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _sha256_file(path: Path) -> str:
    """CANONICAL content hash - not a raw working-tree byte hash.

    All locked contracts are text (YAML/JSON), and git autocrlf gives the SAME committed blob
    CRLF working copies on Windows and LF working copies on Linux CI. Hashing raw bytes made
    the lock platform-dependent (every file 'drifted' on CI); normalizing CRLF/CR to LF first
    makes the hash a property of the contract CONTENT, identical on every checkout style.
    """
    raw = path.read_bytes()
    canonical = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def _lockable_files(repo_root: Path) -> list[str]:
    """Repo-relative paths of every contract file the lock must cover."""
    files = [OPENAPI_RELATIVE_PATH.as_posix()]
    schemas_dir = repo_root / SCHEMAS_RELATIVE_DIR
    if schemas_dir.is_dir():
        files.extend(path.relative_to(repo_root).as_posix() for path in schemas_dir.rglob("*.json"))
    return sorted(set(files))


def _required_request_fields(spec: dict[str, Any], operation: dict[str, Any]) -> list[str]:
    """Top-level required fields of the JSON request body (one-level local $ref resolution)."""
    schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        component = ref.rsplit("/", 1)[-1]
        schema = spec.get("components", {}).get("schemas", {}).get(component, {})
    required = schema.get("required", []) if isinstance(schema, dict) else []
    return sorted(str(field) for field in required) if isinstance(required, list) else []


def extract_contract_surface(spec: dict[str, Any]) -> dict[str, Any]:
    """Deterministic snapshot of the spec's contract surface."""
    surface: dict[str, Any] = {}
    for path in sorted(spec.get("paths", {}) or {}):
        path_item = spec["paths"][path] or {}
        methods: dict[str, Any] = {}
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {}) or {}
            methods[method] = {
                "operation_id": operation.get("operationId"),
                "responses": sorted(str(code) for code in responses),
                "required_request_fields": _required_request_fields(spec, operation),
            }
        if methods:
            surface[path] = methods
    return surface


def _load_repo_spec(repo_root: Path) -> dict[str, Any]:
    spec_path = repo_root / OPENAPI_RELATIVE_PATH
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    return spec if isinstance(spec, dict) else {}


def build_lock_document(repo_root: str | Path) -> dict[str, Any]:
    """Build the pinnable lock document from the repo's committed contracts."""
    root = Path(repo_root)
    files = {relative: _sha256_file(root / relative) for relative in _lockable_files(root)}
    spec = _load_repo_spec(root)
    return {
        "lock_version": LOCK_VERSION,
        "files": dict(sorted(files.items())),
        "openapi_surface": extract_contract_surface(spec),
    }


def write_lock_document(repo_root: str | Path, lock_path: str | Path | None = None) -> Path:
    """Regenerate the committed lock (the INTENTIONAL drift-resolution step)."""
    root = Path(repo_root)
    target = Path(lock_path) if lock_path is not None else root / CONTRACT_LOCK_RELATIVE_PATH
    document = build_lock_document(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return target


def _load_lock(lock_path: Path) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    if not lock_path.is_file():
        return None, _finding(
            "CONTRACT_LOCK_MISSING",
            f"{CONTRACT_LOCK_RELATIVE_PATH.as_posix()} not found: contracts are unlocked",
        )
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, _finding("CONTRACT_LOCK_INVALID", "contract lock is not valid JSON")
    if (
        not isinstance(document, dict)
        or document.get("lock_version") != LOCK_VERSION
        or not isinstance(document.get("files"), dict)
        or not isinstance(document.get("openapi_surface"), dict)
    ):
        return None, _finding("CONTRACT_LOCK_INVALID", "contract lock has an invalid structure")
    return document, None


def breaking_changes(
    locked_surface: dict[str, Any], current_surface: dict[str, Any]
) -> list[dict[str, str]]:
    """Breaking changes of the CURRENT spec relative to the LOCKED surface."""
    findings: list[dict[str, str]] = []
    for path in sorted(locked_surface):
        locked_methods = locked_surface[path]
        current_methods = current_surface.get(path)
        if current_methods is None:
            findings.append(_finding("BREAKING_PATH_REMOVED", f"path removed: {path}"))
            continue
        for method in sorted(locked_methods):
            locked_operation = locked_methods[method]
            current_operation = current_methods.get(method)
            if current_operation is None:
                findings.append(
                    _finding(
                        "BREAKING_OPERATION_REMOVED",
                        f"operation removed: {method.upper()} {path}",
                    )
                )
                continue
            for code in locked_operation.get("responses", []):
                if code not in current_operation.get("responses", []):
                    findings.append(
                        _finding(
                            "BREAKING_RESPONSE_REMOVED",
                            f"response {code} removed: {method.upper()} {path}",
                        )
                    )
            locked_required = set(locked_operation.get("required_request_fields", []))
            current_required = set(current_operation.get("required_request_fields", []))
            for field in sorted(current_required - locked_required):
                findings.append(
                    _finding(
                        "BREAKING_REQUIRED_FIELD_ADDED",
                        f"request field newly required: '{field}' on {method.upper()} {path}",
                    )
                )
    return findings


def ui_reference_findings(repo_root: Path, locked_surface: dict[str, Any]) -> list[dict[str, str]]:
    """Stale ui/src/lib/openapi.ts references vs the locked spec surface."""
    ui_path = repo_root / UI_CLIENT_RELATIVE_PATH
    if not ui_path.is_file():
        return [
            _finding(
                "UI_CLIENT_MISSING",
                f"{UI_CLIENT_RELATIVE_PATH.as_posix()} not found: UI sync cannot be verified",
            )
        ]
    text = ui_path.read_text(encoding="utf-8")

    locked_paths = set(locked_surface)
    locked_operations = {
        methods[method].get("operation_id")
        for methods in locked_surface.values()
        for method in methods
        if methods[method].get("operation_id")
    }

    findings: list[dict[str, str]] = []
    for path in sorted(set(_UI_PATH_PATTERN.findall(text))):
        if path not in locked_paths:
            findings.append(_finding("UI_STALE_REFERENCE", f"UI references unknown path: {path}"))
    for operation_id in sorted(set(_UI_OPERATION_PATTERN.findall(text))):
        if operation_id not in locked_operations:
            findings.append(
                _finding(
                    "UI_STALE_REFERENCE",
                    f"UI references unknown operation: {operation_id}",
                )
            )
    return findings


def verify_contract_lock(
    repo_root: str | Path, lock_path: str | Path | None = None
) -> dict[str, Any]:
    """Verify the committed contract lock. Fail-closed; deterministic findings."""
    root = Path(repo_root)
    target = Path(lock_path) if lock_path is not None else root / CONTRACT_LOCK_RELATIVE_PATH

    document, error = _load_lock(target)
    if error is not None:
        return {"ok": False, "findings": [error]}
    assert document is not None

    findings: list[dict[str, str]] = []
    locked_files: dict[str, str] = document["files"]

    for relative in sorted(locked_files):
        path = root / relative
        if not path.is_file():
            findings.append(_finding("CONTRACT_FILE_MISSING", f"locked file deleted: {relative}"))
            continue
        if _sha256_file(path) != locked_files[relative]:
            findings.append(
                _finding(
                    "CONTRACT_HASH_MISMATCH",
                    f"contract drifted without lock regeneration: {relative}",
                )
            )

    for relative in _lockable_files(root):
        if relative not in locked_files:
            findings.append(
                _finding("CONTRACT_FILE_UNLOCKED", f"contract file not in lock: {relative}")
            )

    locked_surface: dict[str, Any] = document["openapi_surface"]
    spec_path = root / OPENAPI_RELATIVE_PATH
    if spec_path.is_file():
        try:
            current_surface = extract_contract_surface(_load_repo_spec(root))
        except yaml.YAMLError:
            findings.append(_finding("CONTRACT_SPEC_INVALID", "openapi spec is not parseable YAML"))
        else:
            findings.extend(breaking_changes(locked_surface, current_surface))

    findings.extend(ui_reference_findings(root, locked_surface))

    findings.sort(key=lambda f: (f["code"], f["detail"]))
    return {"ok": not findings, "findings": findings}
