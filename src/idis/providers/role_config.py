"""Admitted per-role LLM configuration for GL-05 slice 1.

The module reads ``contracts/llm_role_configs_v1.json`` and the prompt files it
pins. It does not import the Anthropic SDK, open a socket, or spend. Slice 1
leaves ``effort`` null and does not check an environment-bound model id against
the admitted model: the explicit binding wins, which keeps the existing
``claude-sonnet-fake`` / ``claude-opus-fake`` fixtures working.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn

import yaml

from idis.providers.budget_policy import EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
from idis.services.prompts.registry import compute_content_hash

LLM_ROLE_UNKNOWN = "LLM_ROLE_UNKNOWN"
LLM_ROLE_CONFIG_UNADMITTED = "LLM_ROLE_CONFIG_UNADMITTED"

ADMITTED_DOCUMENT_VERSION = "llm-role-configs-v1"
ADMITTED_GENERATOR = "scripts/generate_llm_role_configs_v1.py"
ADMITTED_PROVIDER = "anthropic"
ADMITTED_TEMPERATURE = "0"
ADMITTED_FALLBACK_POLICY = "none"
ADMITTED_BUDGET_COMPONENT_ID = "anthropic"

_ANTHROPIC_BACKEND = "anthropic"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DOCUMENT_FIELDS = frozenset({"document_version", "generator", "roles", "document_sha256"})

ROW_IDENTITY_FIELDS: tuple[str, ...] = (
    "role",
    "provider",
    "model",
    "model_binding_env",
    "effort",
    "temperature",
    "max_output_tokens",
    "prompt_id",
    "prompt_version",
    "prompt_path",
    "prompt_content_sha256",
    "budget_component_id",
    "fallback_policy",
)
_ROW_FIELDS = frozenset(ROW_IDENTITY_FIELDS) | {"config_sha256"}


class RoleConfigError(ValueError):
    """Safe typed role-config failure. The message is the code alone."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LlmRole(StrEnum):
    """Roles admitted by the slice-1 role-config file.

    Analysis values are the specialist agent-type strings. Debate values are
    the ``DebateRole`` values. IC values are the Layer-2 runner role strings.
    """

    EXTRACTION = "extraction"
    METHODOLOGY_EXTRACTION = "methodology_extraction"
    FINANCIAL_AGENT = "financial_agent"
    HISTORIAN_AGENT = "historian_agent"
    MARKET_AGENT = "market_agent"
    RISK_OFFICER_AGENT = "risk_officer_agent"
    SECTOR_SPECIALIST_AGENT = "sector_specialist_agent"
    TEAM_AGENT = "team_agent"
    TECHNICAL_AGENT = "technical_agent"
    TERMS_AGENT = "terms_agent"
    SCORING = "scoring"
    ADVOCATE = "advocate"
    SANAD_BREAKER = "sanad_breaker"
    CONTRADICTION_FINDER = "contradiction_finder"
    RISK_OFFICER = "risk_officer"
    ARBITER = "arbiter"
    IC_CHALLENGER = "ic_challenger"
    IC_ARBITER = "ic_arbiter"


@dataclass(frozen=True, slots=True)
class RoleModelConfig:
    """One admitted role row after binding resolution.

    ``prompt_path`` stays in the admitted file and is not a field here: analysis
    and IC rows pin a path because the runtime records no registry id for them.
    """

    role: LlmRole
    provider: str
    model: str | None
    model_binding_env: str
    effort: str | None
    temperature: str
    max_output_tokens: int
    prompt_id: str | None
    prompt_version: str | None
    prompt_content_sha256: str
    budget_component_id: str
    fallback_policy: str
    config_sha256: str


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    path: str
    roles: Mapping[LlmRole, RoleModelConfig]


_CACHE: _CacheEntry | None = None


def _unadmitted(exc: BaseException | None = None) -> NoReturn:
    error = RoleConfigError(LLM_ROLE_CONFIG_UNADMITTED)
    if exc is None:
        raise error
    raise error from exc


def _repo_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").is_file() and (current / "src").is_dir():
            return current
        current = current.parent
    _unadmitted()


def admitted_config_path() -> Path:
    """Return the admitted role-config document path."""
    return _repo_root() / "contracts" / "llm_role_configs_v1.json"


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def role_row_sha256(identity: Mapping[str, Any]) -> str:
    """Return the row hash over the identity fields, excluding ``config_sha256``."""
    try:
        payload = {field: identity[field] for field in ROW_IDENTITY_FIELDS}
    except KeyError as exc:
        _unadmitted(exc)
    return _canonical_sha256(payload)


def admitted_document_sha256(document: Mapping[str, Any]) -> str:
    """Return the document hash over version, generator, and roles."""
    try:
        payload = {
            "document_version": document["document_version"],
            "generator": document["generator"],
            "roles": document["roles"],
        }
    except KeyError as exc:
        _unadmitted(exc)
    return _canonical_sha256(payload)


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _unadmitted()
    return value


def _require_token(value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        _unadmitted()
    return value


def _require_env_name(value: object) -> str:
    if not isinstance(value, str) or _ENV_RE.fullmatch(value) is None:
        _unadmitted()
    return value


def _require_positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        _unadmitted()
    return value


def _require_prompt_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _unadmitted()
    return value


def _require_prompt_version(value: object) -> str:
    if not isinstance(value, str) or not value:
        _unadmitted()
    return value


def _prompt_file(relative: object) -> Path:
    if (
        not isinstance(relative, str)
        or not relative.startswith("prompts/")
        or not relative.endswith("/prompt.md")
        or "\\" in relative
    ):
        _unadmitted()
    if ".." in Path(relative).parts:
        _unadmitted()
    root = _repo_root().resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        _unadmitted()
    return path


def _hash_prompt_file(relative: object) -> str:
    path = _prompt_file(relative)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _unadmitted(exc)
    return compute_content_hash(text)


def _registry_prompts() -> Mapping[str, Any]:
    path = _repo_root() / "prompts" / "registry.yaml"
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        _unadmitted(exc)
    if not isinstance(loaded, dict):
        _unadmitted()
    prompts = loaded.get("prompts")
    if not isinstance(prompts, dict):
        _unadmitted()
    return prompts


def _require_registry_prompt(
    prompts: Mapping[str, Any],
    *,
    prompt_id: str,
    prompt_version: str,
    prompt_path: str,
) -> None:
    entry = prompts.get(prompt_id)
    if not isinstance(entry, dict):
        _unadmitted()
    if entry.get("version") != prompt_version or entry.get("file_path") != prompt_path:
        _unadmitted()


def _parse_row(row: object, prompts: Mapping[str, Any]) -> RoleModelConfig:
    if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
        _unadmitted()
    config_sha256 = _require_sha256(row.get("config_sha256"))
    if config_sha256 != role_row_sha256(row):
        _unadmitted()
    role_text = row.get("role")
    if not isinstance(role_text, str):
        _unadmitted()
    try:
        role = LlmRole(role_text)
    except ValueError:
        _unadmitted()
    if row.get("provider") != ADMITTED_PROVIDER:
        _unadmitted()
    if row.get("effort") is not None:
        _unadmitted()
    if row.get("temperature") != ADMITTED_TEMPERATURE:
        _unadmitted()
    if row.get("fallback_policy") != ADMITTED_FALLBACK_POLICY:
        _unadmitted()
    component = row.get("budget_component_id")
    if (
        component != ADMITTED_BUDGET_COMPONENT_ID
        or component not in EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
    ):
        _unadmitted()
    prompt_id = _require_prompt_id(row.get("prompt_id"))
    prompt_version = _require_prompt_version(row.get("prompt_version"))
    prompt_path = row.get("prompt_path")
    if not isinstance(prompt_path, str):
        _unadmitted()
    pinned_hash = _require_sha256(row.get("prompt_content_sha256"))
    if _hash_prompt_file(prompt_path) != pinned_hash:
        _unadmitted()
    if prompt_id is not None:
        _require_registry_prompt(
            prompts,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            prompt_path=prompt_path,
        )
    return RoleModelConfig(
        role=role,
        provider=ADMITTED_PROVIDER,
        model=_require_token(row.get("model")),
        model_binding_env=_require_env_name(row.get("model_binding_env")),
        effort=None,
        temperature=ADMITTED_TEMPERATURE,
        max_output_tokens=_require_positive_int(row.get("max_output_tokens")),
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        prompt_content_sha256=pinned_hash,
        budget_component_id=ADMITTED_BUDGET_COMPONENT_ID,
        fallback_policy=ADMITTED_FALLBACK_POLICY,
        config_sha256=config_sha256,
    )


def verify_admitted_document(document: Mapping[str, Any]) -> Mapping[LlmRole, RoleModelConfig]:
    """Verify one admitted document and return its rows keyed by role.

    Args:
        document: Parsed JSON object, including ``document_sha256``.

    Returns:
        The verified rows. The admitted model is the file model.

    Raises:
        RoleConfigError: ``LLM_ROLE_CONFIG_UNADMITTED`` when the document, a row,
            a hash, or a pinned prompt file does not match.
    """
    if set(document) != _DOCUMENT_FIELDS:
        _unadmitted()
    if document.get("document_version") != ADMITTED_DOCUMENT_VERSION:
        _unadmitted()
    if document.get("generator") != ADMITTED_GENERATOR:
        _unadmitted()
    document_sha256 = _require_sha256(document.get("document_sha256"))
    if document_sha256 != admitted_document_sha256(document):
        _unadmitted()
    roles = document.get("roles")
    if not isinstance(roles, list):
        _unadmitted()
    prompts = _registry_prompts()
    parsed: dict[LlmRole, RoleModelConfig] = {}
    for row in roles:
        config = _parse_row(row, prompts)
        if config.role in parsed:
            _unadmitted()
        parsed[config.role] = config
    if set(parsed) != set(LlmRole):
        _unadmitted()
    return parsed


def _load_path(path: Path) -> Mapping[LlmRole, RoleModelConfig]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _unadmitted(exc)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _unadmitted(exc)
    if not isinstance(payload, dict):
        _unadmitted()
    return verify_admitted_document(payload)


def _cached_roles() -> Mapping[LlmRole, RoleModelConfig]:
    global _CACHE
    path = admitted_config_path().resolve()
    key = str(path)
    if _CACHE is not None and _CACHE.path == key:
        return _CACHE.roles
    roles = _load_path(path)
    _CACHE = _CacheEntry(path=key, roles=roles)
    return roles


def _coerce_role(role: LlmRole | str) -> LlmRole:
    if isinstance(role, LlmRole):
        return role
    if isinstance(role, str):
        try:
            return LlmRole(role)
        except ValueError:
            raise RoleConfigError(LLM_ROLE_UNKNOWN) from None
    raise RoleConfigError(LLM_ROLE_UNKNOWN)


def resolve_role_config(
    role: LlmRole | str,
    *,
    backend: str,
    environ: Mapping[str, str] | None = None,
) -> RoleModelConfig:
    """Resolve one admitted role row for a backend.

    Args:
        role: Role enum or its string value.
        backend: Active backend. Any value other than ``anthropic`` returns
            the row with ``model=None``.
        environ: Model-binding environment. When omitted, ``os.environ`` is read.
            A present binding variable replaces the admitted model. Slice 1 does
            not check that replacement against the admitted model.

    Returns:
        A value copy of the admitted row with the backend and binding applied.

    Raises:
        RoleConfigError: ``LLM_ROLE_UNKNOWN`` for an unknown role, or
            ``LLM_ROLE_CONFIG_UNADMITTED`` when the admitted file or a pinned
            prompt cannot be verified.
    """
    role_key = _coerce_role(role)
    roles = _cached_roles()
    try:
        admitted = roles[role_key]
    except KeyError as exc:
        _unadmitted(exc)
    source = os.environ if environ is None else environ
    if backend == _ANTHROPIC_BACKEND and admitted.model_binding_env in source:
        model: str | None = source[admitted.model_binding_env]
    elif backend == _ANTHROPIC_BACKEND:
        model = admitted.model
    else:
        model = None
    return replace(admitted, model=model)
