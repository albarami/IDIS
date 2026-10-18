"""Prepare explicitly selected, authority-free CI inputs; never start services.

This is an orchestration boundary, NOT a production profile mode or admission
shortcut. Runtime probes, launch audit, native/STT qualification, provider-attempt
observations, routes and health remain NOT_EXECUTED after preparation. No signing,
provider, SQL, Docker, subprocess or network operation is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from idis.runtime import profile as profile_module
from idis.runtime.secure_files import (
    SecureFileError,
    protect_path_for_current_user,
    secure_read_integrity_file,
)
from idis.runtime.strict_auth import validate_strict_auth_binding

AUTHORITY = "AUTH-CI-HEALTH-20261017"
PURPOSE = "container health with synthetic fixtures"
FACTORY_SHA256 = "812ba3043931b40dfda534ac813ce5f7f4eeac2f8e996f49d17630817f439abf"
_CANONICAL_KEY_SHA256 = "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
DEFAULT_API_COMMAND = (
    "python",
    "-m",
    "idis.runtime.strict_launcher",
    "api",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--pid-file",
    "/tmp/idis-api.pid",
)
_ARTIFACT_PATHS = {
    "profile": "api/synthetic-bootstrap.env",
    "public_key": "api/disposable-public.pem",
    "workbook": "api/fictional-79-row.xlsx",
    "rights": "api/unsigned-deny-rights.json",
    "budget": "api/unsigned-deny-budget.json",
}
_IMAGE_CONTROL_NAMES = frozenset(
    {
        "PATH",
        "LANG",
        "PYTHON_VERSION",
        "PYTHON_SHA256",
        "PYTHON_PIP_VERSION",
        "PYTHON_SETUPTOOLS_VERSION",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        # Derived from the pinned base image's Config.Env plus the Dockerfile
        # runtime-stage ENV, not inspected from a built image.
        "GPG_KEY",
        "IDIS_VERSION",
    }
)
_RUNTIME_PHASES = (
    "postgres",
    "audit",
    "idempotency",
    "redis",
    "object_store",
    "ingestion",
    "auth",
    "launch_audit",
    "transport_positive_controls",
    "transport_denial_controls",
    "api_connectivity",
    "thirteen_routes",
    "native_qualification",
    "stt_qualification",
    "tesseract_provenance",
    "health",
)


class PreparationError(ValueError):
    """Only a fixed, secret-safe failure code crosses this boundary."""


@dataclass(frozen=True, slots=True)
class PreparedInputs:
    """Owned locations and a pinned manifest digest; contains no secret values."""

    root: Path
    manifest_sha256: str

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def profile_path(self) -> Path:
        return self.root / _ARTIFACT_PATHS["profile"]

    @property
    def public_key_path(self) -> Path:
        return self.root / _ARTIFACT_PATHS["public_key"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _is_link(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _ordinary_absolute_path(path: Path) -> bool:
    if not path.is_absolute() or ".." in path.parts:
        return False
    return not any(_is_link(parent) for parent in (path, *path.parents) if parent.exists())


def _write_sealed(path: Path, data: bytes) -> None:
    # Exclusive creation and the existing OS-specific custody protection are reused.
    with path.open("xb") as stream:
        stream.write(data)
    protect_path_for_current_user(path)
    path.chmod(stat.S_IRUSR)


def _required_names() -> frozenset[str]:
    return profile_module._REQUIRED_NAMES | profile_module._COMMAND_REQUIRED_NAMES["api"]


def _model_input_is_present(path: Path) -> bool:
    """Presence only. Complete hashes/revision/inference are separately H2-owned."""
    if not _ordinary_absolute_path(path) or not path.is_dir():
        return False
    names = ["model.bin", "config.json", "tokenizer.json"]
    names.append("vocabulary.json" if (path / "vocabulary.json").exists() else "vocabulary.txt")
    return all(
        (path / name).is_file() and not _is_link(path / name) and (path / name).stat().st_size > 0
        for name in names
    )


def _fictional_workbook(source_root: Path, run_id: str) -> bytes:
    """Execute ONLY the raw-pinned public factory, without ambient tests imports."""
    path = source_root / "tests/slice100_task5/_workbook_factory.py"
    source = path.read_bytes()
    if _sha256(source) != FACTORY_SHA256:
        raise PreparationError("CI_HEALTH_FACTORY_CHANGED")
    namespace: dict[str, object] = {"__name__": "ci_health_public_fictional_factory"}
    exec(compile(source, str(path), "exec"), namespace)
    factory = namespace.get("production_fdd_workbook_bytes")
    if not callable(factory):
        raise PreparationError("CI_HEALTH_FACTORY_CHANGED")
    workbook: object = factory(source_marker=f"fictional-ci-health-{run_id}")
    if not isinstance(workbook, bytes):
        raise PreparationError("CI_HEALTH_FACTORY_CHANGED")
    return workbook


def _profile_values(root: Path, model_path: Path, run_id: str) -> tuple[dict[str, str], bytes]:
    """Fresh disposable credentials; maintenance data is never an API file field."""
    tenant, canary = str(uuid4()), str(uuid4())
    api_key, canary_key, admin_key = (secrets.token_urlsafe(32) for _ in range(3))
    pg_password, erase_password, pg_admin_password, redis_password, neo4j_password = (
        secrets.token_urlsafe(32) for _ in range(5)
    )
    registry = {}
    for key, tenant_id, actor_id, name, roles in (
        (
            api_key,
            tenant,
            "slice100-acceptance-service",
            "Slice100 Acceptance Service",
            ["INTEGRATION_SERVICE"],
        ),
        (
            canary_key,
            canary,
            "slice100-isolation-canary",
            "Slice100 Isolation Canary",
            ["INTEGRATION_SERVICE"],
        ),
        (admin_key, tenant, "slice100-governance-admin", "Slice100 Governance Admin", ["ADMIN"]),
    ):
        registry[key] = {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "name": name,
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": roles,
        }
    values = {
        "IDIS_REQUIRE_FULL_LIVE": "1",
        "IDIS_RUNTIME_PROFILE_HMAC_KEY": secrets.token_urlsafe(48),
        "IDIS_RUNTIME_PROFILE_GENERATION": "bootstrap-v1",
        "IDIS_REQUIRE_PROMOTED_PROMPTS": "0",
        "IDIS_FDD_METHODOLOGY_PATH": str(root / _ARTIFACT_PATHS["workbook"]),
        "IDIS_INGEST_MAX_BYTES": "536870912",
        "IDIS_OCR_ENABLED": "1",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_LANGUAGE": "eng",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
        "IDIS_OCR_DPI": "200",
        "IDIS_MEDIA_ADAPTER": "faster-whisper",
        "IDIS_MEDIA_STT_MODEL_PATH": str(model_path),
        "IDIS_MEDIA_STT_ALLOW_DOWNLOAD": "0",
        "IDIS_MEDIA_TIMEOUT_SECONDS": "900",
        "IDIS_MEDIA_LANGUAGE": "en",
        "IDIS_MEDIA_COMPUTE_TYPE": "int8",
        "IDIS_MEDIA_MAX_DURATION_SECONDS": "300",
        "IDIS_DATABASE_URL": f"postgresql+psycopg2://idis_app:{pg_password}@127.0.0.1:5432/idis_ci_health",
        "IDIS_ERASURE_DATABASE_URL": f"postgresql+psycopg2://idis_erasure_executor:{erase_password}@127.0.0.1:5432/idis_ci_health",
        "IDIS_IDEMPOTENCY_TTL_DAYS": "7",
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": f"/var/lib/idis-ci-health/{run_id}/objects",
        "IDIS_REDIS_URL": f"redis://:{redis_password}@127.0.0.1:6379/0",
        "IDIS_RATE_LIMIT_USER_RPM": "600",
        "IDIS_RATE_LIMIT_INTEGRATION_RPM": "1200",
        "IDIS_RATE_LIMIT_BURST_MULTIPLIER": "2",
        "IDIS_API_KEYS_JSON": json.dumps(registry),
        "IDIS_REAL_EXAMPLE_API_KEY": api_key,
        "IDIS_ISOLATION_CANARY_API_KEY": canary_key,
        "IDIS_GOVERNANCE_ADMIN_API_KEY": admin_key,
        "IDIS_ACCEPTANCE_TENANT_ID": tenant,
        "IDIS_ISOLATION_CANARY_TENANT_ID": canary,
        "IDIS_SERVICE_REGION": "me-south-1",
        "IDIS_UI_DEV_AUTH_BYPASS": "false",
        "IDIS_WORKER_TENANT_IDS": tenant,
        "IDIS_WORKER_LEASE_SECONDS": "60",
        "IDIS_WORKER_HEARTBEAT_SECONDS": "15",
        "IDIS_PROVIDER_BUDGET_MAX_CALLS": "0",
        "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(root / _ARTIFACT_PATHS["budget"]),
        "IDIS_PROVIDER_BUDGET_CURRENCY": "USD",
        "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH": str(root / _ARTIFACT_PATHS["rights"]),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": "ci-no-authority",
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(
            root / _ARTIFACT_PATHS["public_key"]
        ),
        "IDIS_ACCEPTANCE_API_BASE_URL": "http://127.0.0.1:8000",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": neo4j_password,
        "NEO4J_DATABASE": "neo4j",
    }
    if frozenset(values) != _required_names():
        raise PreparationError("CI_HEALTH_PROFILE_INVENTORY_CHANGED")
    maintenance = _json_bytes(
        {
            "authority": AUTHORITY,
            "run_id": run_id,
            "IDIS_DATABASE_ADMIN_URL": f"postgresql+psycopg2://postgres:{pg_admin_password}@127.0.0.1:5432/idis_ci_health",
            "application_password": pg_password,
            "erasure_password": erase_password,
            "redis_password": redis_password,
            "neo4j_password": neo4j_password,
        }
    )
    return values, maintenance


def prepare_inputs(
    *,
    output_root: Path,
    model_path: Path,
    synthetic_ci_health: bool,
    authority: str,
    owner_uid: int | None = None,
) -> PreparedInputs:
    """Create one fresh owned scope, optionally sealing API custody to Linux UID1000.

    Run in an owned maintenance volume at its eventual absolute mount location;
    paths are HMAC-bound and must not be relocated. The private maintenance child
    is NOT mounted into the API. No service existence/asset qualification is claimed.
    """
    if synthetic_ci_health is not True or authority != AUTHORITY:
        raise PreparationError("CI_HEALTH_SELECTION_REQUIRED")
    root, model = Path(output_root), Path(model_path)
    source_root = Path(__file__).resolve().parents[2]
    if not _ordinary_absolute_path(root) or root.exists() or root.is_relative_to(source_root):
        raise PreparationError("CI_HEALTH_FRESH_ROOT_REQUIRED")
    if not _model_input_is_present(model):
        raise PreparationError("CI_HEALTH_MODEL_INPUT_REQUIRED")
    if owner_uid is not None and (os.name == "nt" or owner_uid != 1000 or os.geteuid() != 0):
        raise PreparationError("CI_HEALTH_CUSTODY_REJECTED")
    run_id = str(uuid4())
    workbook = _fictional_workbook(source_root, run_id)
    # No private serialization/signing: the ephemeral private object is discarded.
    public_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key_sha256 = _sha256(public_key)
    if public_key_sha256 == _CANONICAL_KEY_SHA256:
        raise PreparationError("CI_HEALTH_KEY_IDENTITY_REJECTED")
    values, maintenance = _profile_values(root, model, run_id)
    declaration = {
        "authority": AUTHORITY,
        "purpose": PURPOSE,
        "run_id": run_id,
        "public_key_sha256": public_key_sha256,
        "provider_execution": "DENY",
        "enrichment_execution": "DENY",
        "max_calls": 0,
        "spend_authorized": False,
        "human_approval_claimed": False,
        "governance_import": "FORBIDDEN",
        "signed_governance_envelope": False,
    }
    payloads = {
        "public_key": public_key,
        "workbook": workbook,
        "rights": _json_bytes({**declaration, "kind": "unsigned-deny-rights"}),
        "budget": _json_bytes({**declaration, "kind": "unsigned-deny-budget"}),
        "profile": "".join(
            f"{key}={json.dumps(value)}\n" for key, value in sorted(values.items())
        ).encode(),
    }
    root.mkdir(mode=0o700)  # parents=False: never create or take over unrelated ancestors.
    protect_path_for_current_user(root, directory=True)
    for child in (root / "api", root / "maintenance"):
        child.mkdir(mode=0o700)
        protect_path_for_current_user(child, directory=True)
    for label, data in payloads.items():
        _write_sealed(root / _ARTIFACT_PATHS[label], data)
    _write_sealed(root / "maintenance/bootstrap.json", maintenance)
    manifest = {
        "schema": "ci-synthetic-health-inputs-v1",
        "authority": AUTHORITY,
        "purpose": PURPOSE,
        "run_id": run_id,
        "public_key_sha256": public_key_sha256,
        "factory_sha256": FACTORY_SHA256,
        "artifacts": {
            label: {"path": _ARTIFACT_PATHS[label], "sha256": _sha256(data)}
            for label, data in payloads.items()
        },
        "maintenance_sha256": _sha256(maintenance),
        "maintenance_custody": "SEPARATE_API_MOUNT_EXCLUSION_REQUIRED_UNVERIFIED",
        "runtime_phases": dict.fromkeys(_RUNTIME_PHASES, "NOT_EXECUTED"),
        "provider_invocation_attempts": "NOT_EXECUTED",
    }
    manifest_bytes = _json_bytes(manifest)
    _write_sealed(root / "manifest.json", manifest_bytes)
    if owner_uid is not None:
        # Root maintenance's secrets remain root-owned. Only sealed API inputs,
        # safe manifest and their parents are transferred; never host-wide chown.
        for path in (
            root,
            root / "api",
            root / "manifest.json",
            *(root / p for p in _ARTIFACT_PATHS.values()),
        ):
            os.chown(path, owner_uid, owner_uid)
    return PreparedInputs(root=root, manifest_sha256=_sha256(manifest_bytes))


def _validate_startup_environment(
    prepared: PreparedInputs,
    startup_env: Mapping[str, str],
    image_defaults: Mapping[str, str],
) -> None:
    # image_defaults MUST be the hosted image Config.Env, limited to
    # _IMAGE_CONTROL_NAMES. GPG_KEY and IDIS_VERSION were derived from the
    # pinned base image's Config.Env plus the Dockerfile runtime-stage ENV,
    # not inspected from a built image.
    if (
        not set(image_defaults) <= _IMAGE_CONTROL_NAMES
        or set(startup_env) != set(image_defaults) | {"IDIS_STRICT_DOTENV_PATH"}
        or any(startup_env.get(name) != value for name, value in image_defaults.items())
        or startup_env.get("IDIS_STRICT_DOTENV_PATH") != str(prepared.profile_path)
    ):
        raise PreparationError("CI_HEALTH_STARTUP_ENV_REJECTED")


def qualify_inputs(
    prepared: PreparedInputs,
    *,
    startup_env: Mapping[str, str],
    image_defaults: Mapping[str, str],
) -> dict[str, object]:
    """Actual production loader preflight; not the later API's in-memory object.

    Uses a private mapping, never contaminates this process's os.environ. Safe
    receipt binds bytes/key/HMAC-derived identity; it contains no config values.
    """
    _validate_startup_environment(prepared, startup_env, image_defaults)
    try:
        if not _ordinary_absolute_path(prepared.root):
            raise ValueError
        manifest_bytes = secure_read_integrity_file(
            str(prepared.manifest_path),
            max_bytes=64 * 1024,
            require_readonly=True,
            require_secure_parent=True,
        )
        if _sha256(manifest_bytes) != prepared.manifest_sha256:
            raise ValueError
        manifest = json.loads(manifest_bytes)
        if (
            manifest["schema"] != "ci-synthetic-health-inputs-v1"
            or manifest["authority"] != AUTHORITY
            or manifest["purpose"] != PURPOSE
            or manifest["factory_sha256"] != FACTORY_SHA256
            or set(manifest["artifacts"]) != set(_ARTIFACT_PATHS)
        ):
            raise ValueError
        for label, relative_path in _ARTIFACT_PATHS.items():
            row = manifest["artifacts"][label]
            if row["path"] != relative_path:
                raise ValueError
            content = secure_read_integrity_file(
                str(prepared.root / relative_path),
                max_bytes=4 * 1024 * 1024,
                require_readonly=True,
                require_secure_parent=True,
            )
            if _sha256(content) != row["sha256"]:
                raise ValueError
        loaded = profile_module.load_strict_runtime_profile(
            command="api", process_env=dict(startup_env)
        )
        if (
            set(loaded.values) != _required_names() | {"IDIS_PROCESS_ROLE"}
            or any(loaded.source_for(name) != "dotenv" for name in _required_names())
            or loaded.source_for("IDIS_PROCESS_ROLE") != "launcher"
            or loaded.safe_summary()["source_counts"]
            != {"dotenv": len(_required_names()), "launcher": 1}
            or loaded.governance_public_key_sha256 != manifest["public_key_sha256"]
            or loaded.governance_public_key_sha256 == _CANONICAL_KEY_SHA256
            or not validate_strict_auth_binding(loaded.values).may_proceed
        ):
            raise ValueError
    except (
        OSError,
        SecureFileError,
        profile_module.StrictProfileError,
        ValueError,
        KeyError,
        TypeError,
    ):
        raise PreparationError("CI_HEALTH_INPUT_INTEGRITY_REJECTED") from None
    return {
        "authority": AUTHORITY,
        "purpose": PURPOSE,
        "run_id": manifest["run_id"],
        "manifest_sha256": prepared.manifest_sha256,
        "profile_sha256": manifest["artifacts"]["profile"]["sha256"],
        "public_key_sha256": loaded.governance_public_key_sha256,
        "profile_identity_sha256": _sha256(loaded.base_profile_hmac.encode("ascii")),
        "source_counts": loaded.safe_summary()["source_counts"],
        "qualification_phase": "SEPARATE_PROFILE_PREFLIGHT_ONLY",
        "runtime_phases": dict.fromkeys(_RUNTIME_PHASES, "NOT_EXECUTED"),
        "provider_invocation_attempts": "NOT_EXECUTED",
    }


def dispatch_api(
    prepared: PreparedInputs,
    *,
    command: Sequence[str],
    startup_env: Mapping[str, str],
    image_defaults: Mapping[str, str],
    runner: Callable[[tuple[str, ...], dict[str, str]], object],
) -> object:
    """Validate BEFORE caller-owned child creation; never inherit host environment.

    No default subprocess runner exists. The later reserved composition must wire
    its inspected, pinned image and real isolation controls to this boundary.
    """
    if tuple(command) != DEFAULT_API_COMMAND:
        raise PreparationError("CI_HEALTH_CONSUMER_REJECTED")
    qualify_inputs(prepared, startup_env=startup_env, image_defaults=image_defaults)
    return runner(DEFAULT_API_COMMAND, dict(startup_env))


def main(argv: Sequence[str] | None = None) -> int:
    """Preparation only. Values never appear in command arguments or output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-ci-health", action="store_true", required=True)
    parser.add_argument("--authority", required=True, choices=[AUTHORITY])
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--owner-uid", type=int, choices=[1000])
    args = parser.parse_args(argv)
    try:
        prepared = prepare_inputs(
            output_root=args.output_root,
            model_path=args.model_path,
            synthetic_ci_health=args.synthetic_ci_health,
            authority=args.authority,
            owner_uid=args.owner_uid,
        )
    except (PreparationError, OSError) as error:
        print(
            str(error) if isinstance(error, PreparationError) else "CI_HEALTH_PREPARATION_IO_FAILED"
        )
        return 1
    print(
        json.dumps(
            {
                "authority": AUTHORITY,
                "purpose": PURPOSE,
                "manifest_sha256": prepared.manifest_sha256,
                "runtime": "NOT_EXECUTED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
