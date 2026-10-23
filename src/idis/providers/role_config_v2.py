"""Verifier for the inactive GL-05 candidate role document.

``load_candidate_document`` reads ``contracts/llm_role_configs_v2.json`` and
returns an immutable view. It is not the active role loader. Admission stays
on :func:`idis.providers.role_config.resolve_role_config`.

Each output ceiling plus :data:`CANDIDATE_INPUT_TOKEN_ALLOWANCE` must fit the
candidate price row's context window. The allowance is 50,000 input tokens:
the 50 KB planning payload used with these candidate rates, counted as one
token per byte.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, NoReturn

from idis.models.anthropic_price_catalog_v2 import (
    AnthropicCandidatePriceError,
    resolve_candidate_price,
)
from idis.providers.anthropic_capabilities import (
    QualificationStatus,
    ThinkingPolicy,
    resolve_model_capability,
)
from idis.providers.budget_policy import EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
from idis.providers.role_config import (
    ADMITTED_BUDGET_COMPONENT_ID,
    ADMITTED_FALLBACK_POLICY,
    ADMITTED_PROVIDER,
    LlmRole,
    RoleConfigError,
    _canonical_sha256,
    admitted_config_path,
    verify_admitted_document,
)

LLM_ROLE_CANDIDATE_DOCUMENT_INVALID: Final = "LLM_ROLE_CANDIDATE_DOCUMENT_INVALID"
CANDIDATE_DOCUMENT_VERSION: Final = "llm-role-configs-v2"
CANDIDATE_GENERATOR: Final = "scripts/generate_llm_role_configs_v2.py"
CANDIDATE_THINKING_MODE: Final = "omitted"
CANDIDATE_SAMPLING_POLICY: Final = "provider-default-no-sampling-fields-v1"
CANDIDATE_INPUT_TOKEN_ALLOWANCE: Final = 50_000

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DOCUMENT_FIELDS = frozenset(
    {
        "document_version",
        "generator",
        "qualification_status",
        "activation",
        "roles",
        "unbound_plan_roles",
        "document_sha256",
    }
)
_DOCUMENT_HASH_FIELDS = (
    "document_version",
    "generator",
    "qualification_status",
    "activation",
    "roles",
    "unbound_plan_roles",
)
_ROW_IDENTITY_FIELDS = (
    "role",
    "provider",
    "model",
    "request_shape_id",
    "effort",
    "thinking_mode",
    "sampling_policy",
    "max_output_tokens",
    "price_row_sha256",
    "prompt_id",
    "prompt_version",
    "prompt_path",
    "prompt_content_sha256",
    "budget_component_id",
    "fallback_policy",
    "qualification_status",
)
_ROW_FIELDS = frozenset(_ROW_IDENTITY_FIELDS) | {"config_sha256"}
_NO_PLAN_FIELDS = frozenset({"role", "reason"})
_NOT_RUNTIME_FIELDS = frozenset({"plan_role", "proposed_model", "reason"})
_PROMPT_PIN_FIELDS = (
    "prompt_id",
    "prompt_version",
    "prompt_path",
    "prompt_content_sha256",
)


class CandidateQualification(StrEnum):
    """Closed qualification vocabulary for this inactive document."""

    CANDIDATE_NOT_QUALIFIED = "CANDIDATE_NOT_QUALIFIED"


class CandidateActivation(StrEnum):
    """Closed activation vocabulary. This packet has no active state."""

    INACTIVE = "INACTIVE"


class UnboundReason(StrEnum):
    """Why a plan or runtime role is not a v2 role row."""

    NO_PLAN_ROW = "NO_PLAN_ROW"
    NOT_A_RUNTIME_ROLE = "NOT_A_RUNTIME_ROLE"


class CandidateDocumentError(ValueError):
    """Safe candidate-document failure. The message is the code alone."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CandidateRoleConfig:
    """One verified candidate role row."""

    role: LlmRole
    provider: str
    model: str
    request_shape_id: str
    effort: str
    thinking_mode: str
    sampling_policy: str
    max_output_tokens: int
    price_row_sha256: str
    prompt_id: str | None
    prompt_version: str
    prompt_path: str
    prompt_content_sha256: str
    budget_component_id: str
    fallback_policy: str
    qualification_status: str
    config_sha256: str


@dataclass(frozen=True, slots=True)
class UnboundPlanRole:
    """A plan entry or runtime role that is not a candidate role row."""

    reason: str
    role: str | None = None
    plan_role: str | None = None
    proposed_model: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateDocument:
    """Verified inactive document. Roles and unbound rows are tuples."""

    document_version: str
    generator: str
    qualification_status: str
    activation: str
    roles: tuple[CandidateRoleConfig, ...]
    unbound_plan_roles: tuple[UnboundPlanRole, ...]
    document_sha256: str


def _invalid(exc: BaseException | None = None) -> NoReturn:
    error = CandidateDocumentError(LLM_ROLE_CANDIDATE_DOCUMENT_INVALID)
    if exc is None:
        raise error
    raise error from exc


def candidate_row_sha256(identity: Mapping[str, Any]) -> str:
    """Return the row hash over the v2 identity fields.

    The digest is the v1 canonical JSON rule: sorted keys, compact
    separators. ``config_sha256`` is not part of the identity.

    Args:
        identity: Role fields. A stored ``config_sha256`` is ignored.

    Returns:
        Lowercase SHA-256 hex digest.

    Raises:
        CandidateDocumentError: An identity field is missing or cannot be
            encoded. The message is the code alone.
    """
    try:
        payload = {field: identity[field] for field in _ROW_IDENTITY_FIELDS}
        return _canonical_sha256(payload)
    except (KeyError, TypeError, ValueError) as exc:
        _invalid(exc)


def candidate_document_sha256(document: Mapping[str, Any]) -> str:
    """Return the document hash over the v2 identity fields.

    The digest uses the same canonical JSON rule as v1. ``document_sha256``
    is excluded. Qualification, activation, roles, and unbound rows are
    included.

    Args:
        document: Parsed document. ``document_sha256`` may be present and is
            ignored.

    Returns:
        Lowercase SHA-256 hex digest.

    Raises:
        CandidateDocumentError: An identity field is missing or cannot be
            encoded. The message is the code alone.
    """
    try:
        payload = {field: document[field] for field in _DOCUMENT_HASH_FIELDS}
        return _canonical_sha256(payload)
    except (KeyError, TypeError, ValueError) as exc:
        _invalid(exc)


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _invalid()
    return value


def _require_token(value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        _invalid()
    return value


def _require_positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        _invalid()
    return value


def _repo_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").is_file() and (current / "src").is_dir():
            return current
        current = current.parent
    _invalid()


def _candidate_config_path() -> Path:
    return _repo_root() / "contracts" / "llm_role_configs_v2.json"


def _v1_prompt_pins() -> Mapping[str, Mapping[str, Any]]:
    path = admitted_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _invalid(exc)
    if not isinstance(payload, dict):
        _invalid()
    try:
        verify_admitted_document(payload)
    except RoleConfigError as exc:
        _invalid(exc)
    roles = payload.get("roles")
    if not isinstance(roles, list):
        _invalid()
    pins: dict[str, Mapping[str, Any]] = {}
    for row in roles:
        if not isinstance(row, dict) or not isinstance(row.get("role"), str):
            _invalid()
        pins[row["role"]] = {field: row.get(field) for field in _PROMPT_PIN_FIELDS}
    return pins


def _parse_role(row: object, pins: Mapping[str, Mapping[str, Any]]) -> CandidateRoleConfig:
    if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
        _invalid()
    config_sha256 = _require_sha256(row.get("config_sha256"))
    if config_sha256 != candidate_row_sha256(row):
        _invalid()
    role_text = row.get("role")
    if not isinstance(role_text, str):
        _invalid()
    try:
        role = LlmRole(role_text)
    except ValueError:
        _invalid()
    if row.get("provider") != ADMITTED_PROVIDER:
        _invalid()
    if row.get("budget_component_id") != ADMITTED_BUDGET_COMPONENT_ID:
        _invalid()
    if ADMITTED_BUDGET_COMPONENT_ID not in EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS:
        _invalid()
    if row.get("fallback_policy") != ADMITTED_FALLBACK_POLICY:
        _invalid()
    if row.get("qualification_status") != CandidateQualification.CANDIDATE_NOT_QUALIFIED:
        _invalid()
    if row.get("thinking_mode") != CANDIDATE_THINKING_MODE:
        _invalid()
    if row.get("sampling_policy") != CANDIDATE_SAMPLING_POLICY:
        _invalid()
    model = _require_token(row.get("model"))
    effort = _require_token(row.get("effort"))
    request_shape_id = _require_token(row.get("request_shape_id"))
    capability = resolve_model_capability(model)
    if capability.qualification_status is not QualificationStatus.CANDIDATE_NOT_QUALIFIED:
        _invalid()
    if effort not in capability.supported_efforts:
        _invalid()
    if request_shape_id != capability.shape_id:
        _invalid()
    if capability.thinking_policy is not ThinkingPolicy.OMIT:
        _invalid()
    if capability.supports_sampling_controls:
        _invalid()
    max_output_tokens = _require_positive_int(row.get("max_output_tokens"))
    price_row_sha256 = _require_sha256(row.get("price_row_sha256"))
    try:
        price = resolve_candidate_price(model)
    except AnthropicCandidatePriceError as exc:
        _invalid(exc)
    if price_row_sha256 != price.row_sha256:
        _invalid()
    if max_output_tokens + CANDIDATE_INPUT_TOKEN_ALLOWANCE > price.context_window_limit:
        _invalid()
    expected = pins.get(role.value)
    if expected is None:
        _invalid()
    for field in _PROMPT_PIN_FIELDS:
        if row.get(field) != expected[field]:
            _invalid()
    prompt_id = row.get("prompt_id")
    if prompt_id is not None and not isinstance(prompt_id, str):
        _invalid()
    prompt_version = row.get("prompt_version")
    prompt_path = row.get("prompt_path")
    if not isinstance(prompt_version, str) or not isinstance(prompt_path, str):
        _invalid()
    return CandidateRoleConfig(
        role=role,
        provider=ADMITTED_PROVIDER,
        model=model,
        request_shape_id=request_shape_id,
        effort=effort,
        thinking_mode=CANDIDATE_THINKING_MODE,
        sampling_policy=CANDIDATE_SAMPLING_POLICY,
        max_output_tokens=max_output_tokens,
        price_row_sha256=price_row_sha256,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        prompt_path=prompt_path,
        prompt_content_sha256=_require_sha256(row.get("prompt_content_sha256")),
        budget_component_id=ADMITTED_BUDGET_COMPONENT_ID,
        fallback_policy=ADMITTED_FALLBACK_POLICY,
        qualification_status=CandidateQualification.CANDIDATE_NOT_QUALIFIED.value,
        config_sha256=config_sha256,
    )


def _parse_unbound(rows: object, admitted_roles: set[LlmRole]) -> tuple[UnboundPlanRole, ...]:
    if not isinstance(rows, list):
        _invalid()
    admitted_names = {role.value for role in admitted_roles}
    runtime_names = {role.value for role in LlmRole}
    seen_roles: set[str] = set()
    seen_plan_roles: set[str] = set()
    parsed: list[UnboundPlanRole] = []
    for item in rows:
        if not isinstance(item, dict):
            _invalid()
        reason = item.get("reason")
        if reason == UnboundReason.NO_PLAN_ROW and set(item) == _NO_PLAN_FIELDS:
            role_text = item.get("role")
            if not isinstance(role_text, str):
                _invalid()
            try:
                role = LlmRole(role_text)
            except ValueError:
                _invalid()
            if role_text in seen_roles or role in admitted_roles:
                _invalid()
            seen_roles.add(role_text)
            parsed.append(UnboundPlanRole(reason=UnboundReason.NO_PLAN_ROW.value, role=role.value))
            continue
        if reason == UnboundReason.NOT_A_RUNTIME_ROLE and set(item) == _NOT_RUNTIME_FIELDS:
            plan_role = _require_token(item.get("plan_role"))
            proposed_model = _require_token(item.get("proposed_model"))
            if plan_role in seen_plan_roles or plan_role in runtime_names:
                _invalid()
            if plan_role in admitted_names:
                _invalid()
            seen_plan_roles.add(plan_role)
            parsed.append(
                UnboundPlanRole(
                    reason=UnboundReason.NOT_A_RUNTIME_ROLE.value,
                    plan_role=plan_role,
                    proposed_model=proposed_model,
                )
            )
            continue
        _invalid()
    return tuple(parsed)


def verify_candidate_document(document: Mapping[str, Any]) -> CandidateDocument:
    """Verify one inactive candidate document.

    Args:
        document: Parsed JSON object, including ``document_sha256``.

    Returns:
        The immutable view. Hashes, capability rows, price rows, v1 prompt
        pins, and the closed vocabularies have been checked.

    Raises:
        CandidateDocumentError: The document is not the inactive candidate
            file. The message is the code alone.
    """
    if set(document) != _DOCUMENT_FIELDS:
        _invalid()
    if document.get("document_version") != CANDIDATE_DOCUMENT_VERSION:
        _invalid()
    if document.get("generator") != CANDIDATE_GENERATOR:
        _invalid()
    if document.get("qualification_status") != CandidateQualification.CANDIDATE_NOT_QUALIFIED:
        _invalid()
    if document.get("activation") != CandidateActivation.INACTIVE:
        _invalid()
    document_sha256 = _require_sha256(document.get("document_sha256"))
    if document_sha256 != candidate_document_sha256(document):
        _invalid()
    roles = document.get("roles")
    if not isinstance(roles, list):
        _invalid()
    pins = _v1_prompt_pins()
    parsed_roles: list[CandidateRoleConfig] = []
    seen: set[LlmRole] = set()
    for row in roles:
        config = _parse_role(row, pins)
        if config.role in seen:
            _invalid()
        seen.add(config.role)
        parsed_roles.append(config)
    unbound = _parse_unbound(document.get("unbound_plan_roles"), seen)
    return CandidateDocument(
        document_version=CANDIDATE_DOCUMENT_VERSION,
        generator=CANDIDATE_GENERATOR,
        qualification_status=CandidateQualification.CANDIDATE_NOT_QUALIFIED.value,
        activation=CandidateActivation.INACTIVE.value,
        roles=tuple(parsed_roles),
        unbound_plan_roles=unbound,
        document_sha256=document_sha256,
    )


def load_candidate_document() -> CandidateDocument:
    """Load and verify the inactive candidate document.

    Returns:
        The immutable view of ``contracts/llm_role_configs_v2.json``.

    Raises:
        CandidateDocumentError: The file is missing, unreadable, or not a
            valid inactive candidate document. The message is the code alone.
    """
    path = _candidate_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _invalid(exc)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _invalid(exc)
    if not isinstance(payload, dict):
        _invalid()
    return verify_candidate_document(payload)
