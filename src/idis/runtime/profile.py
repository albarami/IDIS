"""Secret-safe strict runtime profile loading.

This module is deliberately limited to the Python standard library.  Loading and
validating the operator profile must happen before any IDIS runtime component or
third-party service client is imported.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from collections import Counter
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from types import MappingProxyType

from idis.runtime.secure_files import SecureFileError, secure_read_integrity_file

STRICT_PROFILE_LOAD_FAILED = "STRICT_PROFILE_LOAD_FAILED"
STRICT_PROFILE_MISMATCH = "STRICT_PROFILE_MISMATCH"
STRICT_PROFILE_GENERATION_INVALID = "STRICT_PROFILE_GENERATION_INVALID"
STRICT_PROCESS_ROLE_INVALID = "STRICT_PROCESS_ROLE_INVALID"
STRICT_FULL_LIVE_BLOCKED = "STRICT_FULL_LIVE_BLOCKED"
ERASURE_EXECUTOR_CONFIG_BLOCKED = "ERASURE_EXECUTOR_CONFIG_BLOCKED"

_COMMANDS = frozenset({"migrate", "governance", "gate", "api", "worker"})
_RUNTIME_ROLES = frozenset({"api", "worker"})
_GENERATIONS = frozenset({"bootstrap-v1", "acceptance-v1"})
_DOTENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_REVIEWED_PROFILE_MARKER = object()
_MIN_REAL_EXAMPLE_MEDIA_DURATION_SECONDS = 128.0


def _generation_pair_is_allowed(*, command: str, generation: str, promoted: str) -> bool:
    """Admit only the two reviewed generation/promotion pairs.

    ``bootstrap-v1`` is unpromoted by definition.  It may compose the API only as
    the route-restricted Task-4 ingestion process; a worker is always a final
    acceptance role.  The API composition boundary derives that restricted scope
    from this already HMAC-bound pair, so there is no ambient feature flag.
    """

    if (generation, promoted) not in {
        ("bootstrap-v1", "0"),
        ("acceptance-v1", "1"),
    }:
        return False
    return command != "worker" or generation == "acceptance-v1"


# Task 2's strict dependency profile is intentionally complete.  Provider-specific
# credentials may also be present in the dotenv and are included in the HMAC, but
# the durable foundation below must always exist before any runtime import.
_REQUIRED_NAMES = frozenset(
    {
        "IDIS_REQUIRE_FULL_LIVE",
        "IDIS_RUNTIME_PROFILE_HMAC_KEY",
        "IDIS_RUNTIME_PROFILE_GENERATION",
        "IDIS_REQUIRE_PROMOTED_PROMPTS",
        "IDIS_FDD_METHODOLOGY_PATH",
        "IDIS_INGEST_MAX_BYTES",
        "IDIS_OCR_ENABLED",
        "IDIS_OCR_ADAPTER",
        "IDIS_OCR_LANGUAGE",
        "IDIS_OCR_MAX_PAGES",
        "IDIS_OCR_TIMEOUT_SECONDS",
        "IDIS_OCR_DPI",
        "IDIS_MEDIA_ADAPTER",
        "IDIS_MEDIA_STT_MODEL_PATH",
        "IDIS_MEDIA_STT_ALLOW_DOWNLOAD",
        "IDIS_MEDIA_TIMEOUT_SECONDS",
        "IDIS_MEDIA_LANGUAGE",
        "IDIS_MEDIA_COMPUTE_TYPE",
        "IDIS_MEDIA_MAX_DURATION_SECONDS",
        "IDIS_DATABASE_URL",
        "IDIS_IDEMPOTENCY_TTL_DAYS",
        "IDIS_OBJECT_STORE_BACKEND",
        "IDIS_OBJECT_STORE_BASE_DIR",
        "IDIS_REDIS_URL",
        "IDIS_RATE_LIMIT_USER_RPM",
        "IDIS_RATE_LIMIT_INTEGRATION_RPM",
        "IDIS_RATE_LIMIT_BURST_MULTIPLIER",
        "IDIS_API_KEYS_JSON",
        "IDIS_REAL_EXAMPLE_API_KEY",
        "IDIS_ISOLATION_CANARY_API_KEY",
        "IDIS_GOVERNANCE_ADMIN_API_KEY",
        "IDIS_ACCEPTANCE_TENANT_ID",
        "IDIS_ISOLATION_CANARY_TENANT_ID",
        "IDIS_SERVICE_REGION",
        "IDIS_UI_DEV_AUTH_BYPASS",
        "IDIS_WORKER_TENANT_IDS",
        "IDIS_WORKER_LEASE_SECONDS",
        "IDIS_WORKER_HEARTBEAT_SECONDS",
        "IDIS_PROVIDER_BUDGET_MAX_CALLS",
        "IDIS_PROVIDER_BUDGET_POLICY_PATH",
        "IDIS_PROVIDER_BUDGET_CURRENCY",
        "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH",
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID",
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
        "IDIS_ACCEPTANCE_API_BASE_URL",
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
    }
)
_COMMAND_REQUIRED_NAMES = {
    "migrate": frozenset({"IDIS_DATABASE_ADMIN_URL", "IDIS_ERASURE_DATABASE_URL"}),
    "api": frozenset({"IDIS_ERASURE_DATABASE_URL"}),
    "gate": frozenset({"IDIS_ERASURE_DATABASE_URL"}),
}

_HMAC_EXCLUDED_NAMES = frozenset(
    {
        "IDIS_STRICT_DOTENV_PATH",
        "IDIS_RUNTIME_PROFILE_HMAC_KEY",
        "IDIS_PROCESS_ROLE",
    }
)


class StrictProfileError(RuntimeError):
    """Fail-closed profile error carrying a stable, secret-safe code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class StrictRuntimeProfile:
    """Immutable effective strict profile.

    ``values`` is retained for constructor injection, but neither ``repr`` nor the
    safe projection exposes values, paths, registry keys, or connection strings.
    """

    values: Mapping[str, str]
    command: str
    generation: str
    process_role: str | None
    base_profile_hmac: str
    governance_public_key_sha256: str
    _sources: Mapping[str, str]
    _review_marker: object

    def source_for(self, name: str) -> str:
        """Return the safe source class for one effective variable."""
        return self._sources.get(name, "missing")

    def safe_summary(self) -> dict[str, object]:
        """Return a persistable projection containing no configuration values."""
        source_counts = Counter(self._sources.values())
        return {
            "base_profile_hmac": self.base_profile_hmac,
            "command": self.command,
            "generation": self.generation,
            "governance_public_key_sha256": self.governance_public_key_sha256,
            "process_role": self.process_role,
            "variable_count": len(self.values),
            "source_counts": dict(sorted(source_counts.items())),
        }

    def __repr__(self) -> str:
        return (
            "StrictRuntimeProfile("
            f"command={self.command!r}, generation={self.generation!r}, "
            f"process_role={self.process_role!r}, "
            f"base_profile_hmac={self.base_profile_hmac!r})"
        )


def _decode_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] not in {"'", '"'}:
        return value
    quote = value[0]
    if len(value) < 2 or value[-1] != quote:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    body = value[1:-1]
    if quote == "'":
        return body
    try:
        # JSON string decoding gives the familiar dotenv escapes without invoking
        # a shell or expanding another environment variable.
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED) from None
    if not isinstance(decoded, str):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    return decoded


def _parse_dotenv(path_value: str) -> dict[str, str]:
    try:
        lines = (
            secure_read_integrity_file(
                path_value,
                max_bytes=1024 * 1024,
                require_readonly=True,
                require_secure_parent=True,
                require_owner_only=True,
            )
            .decode("utf-8")
            .splitlines()
        )
    except (SecureFileError, UnicodeError, ValueError):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED) from None

    parsed: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if not _DOTENV_NAME.fullmatch(name) or name in parsed:
            raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
        parsed[name] = _decode_dotenv_value(raw_value)
    if not parsed:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    return parsed


def _validate_profile(*, command: str, values: Mapping[str, str]) -> tuple[str, str | None]:
    if command not in _COMMANDS:
        raise StrictProfileError(STRICT_PROCESS_ROLE_INVALID)
    if (
        command in {"migrate", "api", "gate"}
        and not values.get("IDIS_ERASURE_DATABASE_URL", "").strip()
    ):
        raise StrictProfileError(ERASURE_EXECUTOR_CONFIG_BLOCKED)
    required_names = _REQUIRED_NAMES | _COMMAND_REQUIRED_NAMES.get(command, frozenset())
    if any(not values.get(name, "").strip() for name in required_names):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_REQUIRE_FULL_LIVE"] != "1":
        raise StrictProfileError(STRICT_FULL_LIVE_BLOCKED)
    if values["IDIS_INGEST_MAX_BYTES"] != "536870912":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_OCR_MAX_PAGES"] != "72":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_OCR_TIMEOUT_SECONDS"] != "120":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_OCR_DPI"] != "200":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_OCR_ENABLED"].strip().lower() not in {"1", "true", "yes", "on"}:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_OCR_ADAPTER"].strip().lower() != "tesseract":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_MEDIA_ADAPTER"].strip().lower() != "faster-whisper":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_MEDIA_STT_ALLOW_DOWNLOAD"].strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if values["IDIS_MEDIA_LANGUAGE"].strip().lower() != "en":
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    try:
        positive_integers = (
            _strict_positive_integer(values["IDIS_OCR_MAX_PAGES"]),
            _strict_positive_integer(values["IDIS_OCR_DPI"]),
        )
        positive_numbers = (
            float(values["IDIS_OCR_TIMEOUT_SECONDS"]),
            float(values["IDIS_MEDIA_TIMEOUT_SECONDS"]),
            float(values["IDIS_MEDIA_MAX_DURATION_SECONDS"]),
        )
    except ValueError:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED) from None
    if (
        any(
            not math.isfinite(value) or value <= 0
            for value in (*positive_integers, *positive_numbers)
        )
        or positive_numbers[2] < _MIN_REAL_EXAMPLE_MEDIA_DURATION_SECONDS
    ):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)

    generation = values["IDIS_RUNTIME_PROFILE_GENERATION"]
    promoted = values["IDIS_REQUIRE_PROMOTED_PROMPTS"]
    if (
        generation not in _GENERATIONS
        or promoted not in {"0", "1"}
        or not _generation_pair_is_allowed(
            command=command,
            generation=generation,
            promoted=promoted,
        )
    ):
        raise StrictProfileError(STRICT_PROFILE_GENERATION_INVALID)

    hmac_key = values["IDIS_RUNTIME_PROFILE_HMAC_KEY"]
    if len(hmac_key.encode("utf-8")) < 32:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    process_role = command if command in _RUNTIME_ROLES else None
    return generation, process_role


def _strict_positive_integer(value: str) -> int:
    normalized = value.strip()
    if not normalized.isascii() or not normalized.isdecimal():
        raise ValueError("not a decimal integer")
    return int(normalized)


def _governance_public_key_digest(values: Mapping[str, str]) -> str:
    try:
        key_bytes = secure_read_integrity_file(
            values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"],
            max_bytes=64 * 1024,
            require_readonly=True,
            require_secure_parent=True,
        )
    except (KeyError, SecureFileError, ValueError):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED) from None
    return hashlib.sha256(key_bytes).hexdigest()


def _profile_hmac(
    values: Mapping[str, str],
    *,
    governance_public_key_sha256: str,
) -> str:
    key = values["IDIS_RUNTIME_PROFILE_HMAC_KEY"].encode("utf-8")
    projection = {name: value for name, value in values.items() if name not in _HMAC_EXCLUDED_NAMES}
    projection["<derived:governance_public_key_sha256>"] = governance_public_key_sha256
    payload = json.dumps(
        projection,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def load_strict_runtime_profile(
    *,
    command: str,
    process_env: MutableMapping[str, str] | None = None,
) -> StrictRuntimeProfile:
    """Load, validate, atomically apply, and freeze the strict profile.

    Values already present in ``process_env`` override values from the dotenv.  Only
    dotenv names and the explicitly required names participate, so unrelated ambient
    variables cannot perturb the profile HMAC.
    """
    target_env = os.environ if process_env is None else process_env
    dotenv_path = target_env.get("IDIS_STRICT_DOTENV_PATH", "")
    if not dotenv_path:
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)

    dotenv_values = _parse_dotenv(dotenv_path)
    if "IDIS_PROCESS_ROLE" in dotenv_values or "IDIS_PROCESS_ROLE" in target_env:
        raise StrictProfileError(STRICT_PROCESS_ROLE_INVALID)

    candidate_names = (
        set(dotenv_values)
        | set(_REQUIRED_NAMES)
        | set(_COMMAND_REQUIRED_NAMES.get(command, frozenset()))
    )
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name in candidate_names:
        if name in target_env:
            values[name] = target_env[name]
            sources[name] = "process"
        elif name in dotenv_values:
            values[name] = dotenv_values[name]
            sources[name] = "dotenv"

    generation, process_role = _validate_profile(command=command, values=values)
    governance_public_key_sha256 = _governance_public_key_digest(values)
    digest = _profile_hmac(
        values,
        governance_public_key_sha256=governance_public_key_sha256,
    )

    # Privileged DSNs participate in the base-profile identity when supplied, but
    # each is retained only by its reviewed process set.  This prevents either the
    # admin or dedicated erasure credential from drifting into worker/governance.
    effective_values = dict(values)
    effective_sources = dict(sources)
    if command != "migrate":
        effective_values.pop("IDIS_DATABASE_ADMIN_URL", None)
        effective_sources.pop("IDIS_DATABASE_ADMIN_URL", None)
        target_env.pop("IDIS_DATABASE_ADMIN_URL", None)
    if command not in {"migrate", "api", "gate"}:
        effective_values.pop("IDIS_ERASURE_DATABASE_URL", None)
        effective_sources.pop("IDIS_ERASURE_DATABASE_URL", None)
        target_env.pop("IDIS_ERASURE_DATABASE_URL", None)

    # Apply only after the whole candidate has passed validation.  This keeps a
    # malformed or incomplete profile from partially contaminating process state.
    target_env.update(effective_values)
    if process_role is not None:
        target_env["IDIS_PROCESS_ROLE"] = process_role
        effective_values["IDIS_PROCESS_ROLE"] = process_role
        effective_sources["IDIS_PROCESS_ROLE"] = "launcher"
    else:
        target_env.pop("IDIS_PROCESS_ROLE", None)

    return StrictRuntimeProfile(
        values=MappingProxyType(effective_values),
        command=command,
        generation=generation,
        process_role=process_role,
        base_profile_hmac=digest,
        governance_public_key_sha256=governance_public_key_sha256,
        _sources=MappingProxyType(effective_sources),
        _review_marker=_REVIEWED_PROFILE_MARKER,
    )


def require_runtime_profile(
    profile: StrictRuntimeProfile | None,
    *,
    command: str,
) -> StrictRuntimeProfile:
    """Validate one launcher-produced profile at a strict composition boundary."""
    if profile is None or not isinstance(profile, StrictRuntimeProfile):
        raise StrictProfileError(STRICT_PROFILE_LOAD_FAILED)
    if command not in {"migrate", "governance", "gate", "api", "worker"} or (
        profile.command != command
    ):
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    expected_role = command if command in _RUNTIME_ROLES else None
    if profile.process_role != expected_role:
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    if profile.values.get("IDIS_REQUIRE_FULL_LIVE") != "1":
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    if profile.values.get("IDIS_RUNTIME_PROFILE_GENERATION") != profile.generation:
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    if len(profile.governance_public_key_sha256) != 64:
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    promoted = profile.values.get("IDIS_REQUIRE_PROMOTED_PROMPTS")
    if not isinstance(promoted, str) or not _generation_pair_is_allowed(
        command=command,
        generation=profile.generation,
        promoted=promoted,
    ):
        raise StrictProfileError(STRICT_PROFILE_GENERATION_INVALID)
    if profile.values.get("IDIS_PROCESS_ROLE") != expected_role:
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    if profile._review_marker is not _REVIEWED_PROFILE_MARKER:
        raise StrictProfileError(STRICT_PROFILE_MISMATCH)
    return profile


__all__ = [
    "ERASURE_EXECUTOR_CONFIG_BLOCKED",
    "STRICT_FULL_LIVE_BLOCKED",
    "STRICT_PROCESS_ROLE_INVALID",
    "STRICT_PROFILE_GENERATION_INVALID",
    "STRICT_PROFILE_LOAD_FAILED",
    "STRICT_PROFILE_MISMATCH",
    "StrictProfileError",
    "StrictRuntimeProfile",
    "load_strict_runtime_profile",
    "require_runtime_profile",
]
