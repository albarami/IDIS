"""Live (Anthropic) model configuration and health checks — Slice82.

Mirrors `rag/embedding_health.py` / `ocr_health.py` / `media_health.py`: an env-driven
check returning a sanitized Pydantic result that never exposes the API key, prompt text,
response text, raw exception messages, paths, or provider payloads — only fixed safe
identifiers, safe model names, the provider name, and a sanitized request id.

The DEFAULT path is **no-network**: it validates backend/credential/model config only and
never instantiates a provider client; ``runtime_call_proven`` is False. The only path that
touches a client is the **opt-in** runtime probe (``run_probe=True`` + an injectable
``client_factory``) — tests always inject a fake, so no real Anthropic call occurs. This
module performs NO wiring into strict readiness / provisioning truth (that is Task 3).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

IDIS_EXTRACT_BACKEND_ENV = "IDIS_EXTRACT_BACKEND"
IDIS_DEBATE_BACKEND_ENV = "IDIS_DEBATE_BACKEND"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
IDIS_ANTHROPIC_MODEL_EXTRACT_ENV = "IDIS_ANTHROPIC_MODEL_EXTRACT"
IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"
IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER"

ANTHROPIC_BACKEND = "anthropic"
DETERMINISTIC_BACKEND = "deterministic"
PROVIDER_NAME = "anthropic"

# Minimal, content-free probe used only by the opt-in runtime check (no private data).
LLM_HEALTH_PROBE_INPUT = "health"
LLM_HEALTH_PROBE_MAX_TOKENS = 1

_MAX_ERROR_LENGTH = 240
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<!\w)/[^/\s]+(?:/[^/\s]*)+")


class LlmClientFactory(Protocol):
    """Factory for injectable Anthropic-compatible clients (opt-in runtime probe only)."""

    def __call__(self, api_key: str) -> Any:
        """Return a client exposing ``messages.create(...)`` for the health probe."""


class LlmModelRole(StrEnum):
    """Strict-readiness live-model roles.

    EXTRACTION/DEBATE/ANALYSIS/SCORING map 1:1 to the Anthropic inventory components.
    DEBATE_ARBITER is an internal per-model proof role (the debate arbiter model) used so
    Layer-2 readiness can require BOTH the challenger and arbiter models to be runtime-proven;
    it is not a separate inventory component.
    """

    EXTRACTION = "extraction"
    DEBATE = "debate"
    ANALYSIS = "analysis"
    SCORING = "scoring"
    DEBATE_ARBITER = "debate_arbiter"


# role -> (backend env var, ((model env var, safe identifier), ...))
_ROLE_SPECS: dict[LlmModelRole, tuple[str, tuple[tuple[str, str], ...]]] = {
    LlmModelRole.EXTRACTION: (
        IDIS_EXTRACT_BACKEND_ENV,
        ((IDIS_ANTHROPIC_MODEL_EXTRACT_ENV, "extract_model"),),
    ),
    LlmModelRole.DEBATE: (
        IDIS_DEBATE_BACKEND_ENV,
        (
            (IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT_ENV, "debate_model"),
            (IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER_ENV, "debate_arbiter_model"),
        ),
    ),
    LlmModelRole.ANALYSIS: (
        IDIS_DEBATE_BACKEND_ENV,
        ((IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT_ENV, "analysis_model"),),
    ),
    LlmModelRole.SCORING: (
        IDIS_DEBATE_BACKEND_ENV,
        ((IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT_ENV, "scoring_model"),),
    ),
    LlmModelRole.DEBATE_ARBITER: (
        IDIS_DEBATE_BACKEND_ENV,
        ((IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER_ENV, "debate_arbiter_model"),),
    ),
}


class LlmModelHealthStatus(StrEnum):
    """Safe live-model health status values."""

    HEALTHY = "healthy"
    DISABLED = "disabled"
    MISSING_CREDENTIALS = "missing_credentials"
    FAILED = "failed"


class LlmModelHealthCheck(BaseModel):
    """Sanitized live (Anthropic) model health-check result."""

    model_config = ConfigDict(extra="forbid")

    status: LlmModelHealthStatus
    role: str
    configured: bool
    backend: str | None = None
    provider: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    missing_dependencies: list[str] = Field(default_factory=list)
    runtime_call_proven: bool = False
    provider_request_id: str | None = None
    error: str | None = None

    @classmethod
    def disabled(cls, role: LlmModelRole) -> LlmModelHealthCheck:
        """Return the expected off result (live backend not selected for this role)."""
        return cls(status=LlmModelHealthStatus.DISABLED, role=role.value, configured=False)

    @classmethod
    def healthy(
        cls,
        role: LlmModelRole,
        *,
        backend: str,
        provider: str,
        models: dict[str, str],
        runtime_call_proven: bool = False,
        provider_request_id: str | None = None,
    ) -> LlmModelHealthCheck:
        """Return a configured-healthy result (no-network unless a probe proved a call)."""
        return cls(
            status=LlmModelHealthStatus.HEALTHY,
            role=role.value,
            configured=True,
            backend=backend,
            provider=provider,
            models=dict(models),
            runtime_call_proven=runtime_call_proven,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def missing(cls, role: LlmModelRole, *, missing_dependencies: list[str]) -> LlmModelHealthCheck:
        """Return a missing/incomplete-credentials result with safe identifiers only."""
        return cls(
            status=LlmModelHealthStatus.MISSING_CREDENTIALS,
            role=role.value,
            configured=False,
            missing_dependencies=sorted(set(missing_dependencies)),
        )

    @classmethod
    def failed(
        cls, role: LlmModelRole, *, error: str | None = None, configured: bool = True
    ) -> LlmModelHealthCheck:
        """Return a sanitized failed health result."""
        return cls(
            status=LlmModelHealthStatus.FAILED,
            role=role.value,
            configured=configured,
            error=_sanitize_error(error) or "Live model health check failed.",
        )


def _sanitize_error(error: str | None) -> str | None:
    if error is None:
        return None
    sanitized = _SECRET_PATTERN.sub("[redacted]", error)
    sanitized = _WINDOWS_PATH_PATTERN.sub("[redacted]", sanitized)
    sanitized = _UNIX_PATH_PATTERN.sub("[redacted]", sanitized)
    return sanitized[:_MAX_ERROR_LENGTH]


def _sanitize_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    return _sanitize_error(str(value))


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _default_anthropic_client_factory(api_key: str) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def check_llm_model_health(
    *,
    env: Mapping[str, str] | None = None,
    role: LlmModelRole = LlmModelRole.EXTRACTION,
    client_factory: Callable[[str], Any] | None = None,
    run_probe: bool = False,
) -> LlmModelHealthCheck:
    """Check live (Anthropic) model readiness for ``role`` without exposing secrets.

    Args:
        env: Environment mapping to inspect. Defaults to ``os.environ``.
        role: Which live-model role to check (extraction/debate/analysis/scoring).
        client_factory: Injectable client constructor, used ONLY by the opt-in runtime
            probe (defaults to a lazily-imported real Anthropic client). Tests inject a fake.
        run_probe: When True, make a single minimal safe provider call to prove a runtime
            call (sets ``runtime_call_proven``). Defaults False — the no-network path that
            never constructs a client.

    Returns:
        Sanitized live-model health-check result.
    """
    values = os.environ if env is None else env
    backend_env, model_specs = _ROLE_SPECS[role]
    backend = str(values.get(backend_env, "")).strip().lower()

    if backend in {"", DETERMINISTIC_BACKEND}:
        return LlmModelHealthCheck.disabled(role)
    if backend != ANTHROPIC_BACKEND:
        return LlmModelHealthCheck.failed(
            role,
            configured=False,
            error=f"Unsupported live model backend. Allowed backend: {ANTHROPIC_BACKEND}.",
        )

    missing: list[str] = []
    if not _has_value(values, ANTHROPIC_API_KEY_ENV):
        missing.append("anthropic_api_key")
    for model_env, safe_id in model_specs:
        if not _has_value(values, model_env):
            missing.append(safe_id)
    if missing:
        return LlmModelHealthCheck.missing(role, missing_dependencies=missing)

    models = {safe_id: str(values[model_env]).strip() for model_env, safe_id in model_specs}
    if not run_probe:
        # No-network default: configured + health-checked, but not runtime-call-proven.
        return LlmModelHealthCheck.healthy(
            role, backend=ANTHROPIC_BACKEND, provider=PROVIDER_NAME, models=models
        )

    make_client = client_factory or _default_anthropic_client_factory
    api_key = str(values[ANTHROPIC_API_KEY_ENV]).strip()
    probe_model = next(iter(models.values()))
    try:
        client = make_client(api_key)
        response = client.messages.create(
            model=probe_model,
            max_tokens=LLM_HEALTH_PROBE_MAX_TOKENS,
            messages=[{"role": "user", "content": LLM_HEALTH_PROBE_INPUT}],
        )
        request_id = _sanitize_request_id(getattr(response, "id", None))
    except Exception as exc:
        # Only the exception class name is surfaced — never the message body (which could
        # contain the key, prompt, response, or provider payload).
        return LlmModelHealthCheck.failed(role, configured=True, error=type(exc).__name__)

    return LlmModelHealthCheck.healthy(
        role,
        backend=ANTHROPIC_BACKEND,
        provider=PROVIDER_NAME,
        models=models,
        runtime_call_proven=True,
        provider_request_id=request_id,
    )


# ---------------------------------------------------------------------------
# Thin prompt-registry model linkage (Slice82 Task 4)
#
# Surfaces the OpenAI-vs-Anthropic model-name mismatch (the prompt registry's model classes
# default to OpenAI model names while the live runtime backend defaults to Anthropic) as a
# SAFE, label-only diagnostic. It reads ``prompts/registry.yaml`` read-only and never mutates
# it; the result contains only prompt IDs, model-class labels, and model-name strings already
# present in the registry, plus the runtime provider family. No prompt bodies, env values,
# API keys, paths, or private text.
# ---------------------------------------------------------------------------

PROMPT_REGISTRY_FILENAME = "registry.yaml"

# Safe, label-only model-name -> provider-family classification (prefix match).
_PROVIDER_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("gemini", "google"),
)


class PromptRegistryModelLinkage(BaseModel):
    """Safe, label-only prompt-registry -> model-provider linkage diagnostic."""

    model_config = ConfigDict(extra="forbid")

    prompt_count: int
    prompt_model_classes: dict[str, str] = Field(default_factory=dict)
    registry_model_names: list[str] = Field(default_factory=list)
    registry_provider_families: list[str] = Field(default_factory=list)
    runtime_provider_family: str
    provider_mismatch: bool
    summary: str


def _model_provider_family(model_name: str) -> str:
    name = model_name.strip().lower()
    for prefix, family in _PROVIDER_FAMILY_PREFIXES:
        if name.startswith(prefix):
            return family
    return "unknown"


def _default_registry_path() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / PROMPT_REGISTRY_FILENAME


def _registry_linkage_summary(
    *, provider_mismatch: bool, registry_families: list[str], runtime_family: str
) -> str:
    if not provider_mismatch:
        return (
            "Prompt registry model classes resolve to provider families "
            f"{registry_families or ['none']}; runtime provider family "
            f"'{runtime_family}' is represented (no mismatch)."
        )
    return (
        "Prompt registry model classes resolve to provider families "
        f"{registry_families} (e.g. OpenAI model names), but the live runtime backend uses "
        f"provider family '{runtime_family}'. Surfaced as a diagnostic only; registry unchanged."
    )


def summarize_prompt_registry_model_linkage(
    *,
    registry_path: Path | str | None = None,
    runtime_provider_family: str = PROVIDER_NAME,
) -> PromptRegistryModelLinkage:
    """Return a safe diagnostic linking registry model classes to provider families.

    Reads ``prompts/registry.yaml`` read-only. Surfaces the OpenAI-vs-Anthropic mismatch
    (registry model classes default to OpenAI names; the live runtime backend is Anthropic)
    without mutating the registry. Exposes only prompt IDs / model-class labels / model-name
    strings already in the registry, plus the runtime provider family.
    """
    path = Path(registry_path) if registry_path is not None else _default_registry_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    prompts = data.get("prompts") or {}
    model_classes = data.get("model_classes") or {}

    prompt_model_classes: dict[str, str] = {}
    model_names: set[str] = set()
    for prompt_id, prompt in prompts.items():
        spec = prompt or {}
        requirements = spec.get("model_requirements") or {}
        model_class = str(requirements.get("model_class", "")).strip()
        if model_class:
            prompt_model_classes[str(prompt_id)] = model_class
        fallback_policy = spec.get("fallback_policy") or {}
        if isinstance(fallback_policy, dict):
            for value in fallback_policy.values():
                if isinstance(value, str) and value.strip():
                    model_names.add(value.strip())
    for class_spec in model_classes.values():
        spec = class_spec or {}
        for key in ("default_model", "fallback_model"):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                model_names.add(value.strip())

    registry_model_names = sorted(model_names)
    registry_provider_families = sorted(
        {_model_provider_family(name) for name in registry_model_names}
    )
    runtime_family = runtime_provider_family.strip().lower()
    provider_mismatch = (
        bool(registry_model_names) and runtime_family not in registry_provider_families
    )

    return PromptRegistryModelLinkage(
        prompt_count=len(prompts),
        prompt_model_classes=dict(sorted(prompt_model_classes.items())),
        registry_model_names=registry_model_names,
        registry_provider_families=registry_provider_families,
        runtime_provider_family=runtime_family,
        provider_mismatch=provider_mismatch,
        summary=_registry_linkage_summary(
            provider_mismatch=provider_mismatch,
            registry_families=registry_provider_families,
            runtime_family=runtime_family,
        ),
    )
