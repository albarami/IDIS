"""S6 role, client, and provenance helpers moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from idis.providers.role_config import (
    LLM_ROLE_CONFIG_UNADMITTED,
    LlmRole,
    RoleConfigError,
    RoleModelConfig,
    resolve_role_config,
)
from idis.services.runs.step_runtime.common import _find_project_root

_ANALYSIS_ROLES = (
    LlmRole.FINANCIAL_AGENT,
    LlmRole.HISTORIAN_AGENT,
    LlmRole.MARKET_AGENT,
    LlmRole.RISK_OFFICER_AGENT,
    LlmRole.SECTOR_SPECIALIST_AGENT,
    LlmRole.TEAM_AGENT,
    LlmRole.TECHNICAL_AGENT,
    LlmRole.TERMS_AGENT,
)
_DEBATE_DEFAULT_ROLES = (
    LlmRole.ADVOCATE,
    LlmRole.SANAD_BREAKER,
    LlmRole.CONTRADICTION_FINDER,
    LlmRole.RISK_OFFICER,
)


def _shared_role_config(roles: tuple[LlmRole, ...], *, backend: str) -> RoleModelConfig:
    """Return the shared model and token limit for roles that still use one client."""
    configs = [resolve_role_config(role, backend=backend) for role in roles]
    head = configs[0]
    for config in configs[1:]:
        if config.model != head.model or config.max_output_tokens != head.max_output_tokens:
            raise RoleConfigError(LLM_ROLE_CONFIG_UNADMITTED)
    return head


@dataclass(frozen=True)
class AnalysisClientSelection:
    """Safe selection context for an injected analysis-client factory (no API key)."""

    backend: str
    model: str | None
    max_tokens: int


@dataclass(frozen=True)
class ScoringClientSelection:
    """Safe selection context for an injected scoring-client factory (no API key)."""

    backend: str
    model: str | None
    max_tokens: int


@dataclass(frozen=True)
class DebateRoleRunnerSelection:
    """Safe selection context for an injected debate role-runners factory (no API key)."""

    backend: str
    default_model: str | None
    arbiter_model: str | None
    max_tokens: int


AnalysisClientFactory = Callable[[AnalysisClientSelection], Any]
ScoringClientFactory = Callable[[ScoringClientSelection], Any]
DebateRoleRunnersFactory = Callable[[DebateRoleRunnerSelection], Any]


def _resolve_analysis_selection() -> AnalysisClientSelection:
    """Resolve the analysis backend, model, and token limit from the admitted rows."""
    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")
    config = _shared_role_config(_ANALYSIS_ROLES, backend=backend)
    return AnalysisClientSelection(
        backend=backend,
        model=config.model,
        max_tokens=config.max_output_tokens,
    )


def _resolve_scoring_selection() -> ScoringClientSelection:
    """Resolve the scoring backend, model, and token limit from the admitted row."""
    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")
    config = resolve_role_config(LlmRole.SCORING, backend=backend)
    return ScoringClientSelection(
        backend=backend,
        model=config.model,
        max_tokens=config.max_output_tokens,
    )


def _resolve_debate_selection() -> DebateRoleRunnerSelection:
    """Resolve the debate backend and models from the admitted rows."""
    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")
    shared = _shared_role_config(_DEBATE_DEFAULT_ROLES, backend=backend)
    arbiter = resolve_role_config(LlmRole.ARBITER, backend=backend)
    if arbiter.max_output_tokens != shared.max_output_tokens:
        raise RoleConfigError(LLM_ROLE_CONFIG_UNADMITTED)
    return DebateRoleRunnerSelection(
        backend=backend,
        default_model=shared.model,
        arbiter_model=arbiter.model,
        max_tokens=shared.max_output_tokens,
    )


# --- Slice84: execution-time strict-live enforcement for analysis / debate L1 / scoring ---
# A single shared flag ``strict_live_debate_backend_required`` (threaded only from the strict
# FULL execution path) forbids the deterministic analysis client / debate RoleRunners / scoring
# client. Surfaced only as fixed, role-specific codes — never with the API key, prompt,
# response, provider payload, or a raw underlying exception message.
STRICT_LIVE_ANALYSIS_REQUIRED = "STRICT_LIVE_ANALYSIS_REQUIRED"
STRICT_LIVE_ANALYSIS_PROVIDER_FAILED = "STRICT_LIVE_ANALYSIS_PROVIDER_FAILED"
STRICT_LIVE_DEBATE_REQUIRED = "STRICT_LIVE_DEBATE_REQUIRED"
STRICT_LIVE_DEBATE_PROVIDER_FAILED = "STRICT_LIVE_DEBATE_PROVIDER_FAILED"
STRICT_LIVE_SCORING_REQUIRED = "STRICT_LIVE_SCORING_REQUIRED"
STRICT_LIVE_SCORING_PROVIDER_FAILED = "STRICT_LIVE_SCORING_PROVIDER_FAILED"


class StrictLiveRoleError(Exception):
    """A strict FULL analysis/debate/scoring role could not use an approved live backend.

    Carries only a safe, fixed ``code`` + ``message`` — never the API key, prompt, response,
    provider payload, or a raw underlying exception message. The ``code`` is role-specific (one
    of the ``STRICT_LIVE_{ANALYSIS,DEBATE,SCORING}_{REQUIRED,PROVIDER_FAILED}`` constants).
    """

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _new_budgeted_llm_client(*, model: str | None, max_tokens: int, tenant_id: str | None) -> Any:
    """Construct the live Anthropic client and wrap it in the per-tenant/provider budget gate.

    Slice96 DEC-C: the single construction point for every live LLM seam, so no live provider call
    can bypass the hard cap. ``wrap_with_provider_budget`` is a passthrough when no budget is
    configured (the default), leaving non-strict/dev runs unchanged; when a cap is set the returned
    client raises the safe provider-budget denial before any provider request once the tenant's
    budget for the provider is exhausted.
    """
    from idis.providers.budget import wrap_with_provider_budget
    from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

    client = AnthropicLLMClient(model=model, max_tokens=max_tokens)
    return wrap_with_provider_budget(client, tenant_id=tenant_id or "", provider="anthropic")


def _build_strict_anthropic_role_client(
    selection: Any,
    factory: Callable[[Any], Any] | None,
    *,
    role: str,
    provider_failed_code: str,
    tenant_id: str | None = None,
) -> Any:
    """Build the live (Anthropic) analysis/scoring client for strict FULL, failing closed safely.

    Any construction/call failure is surfaced as the role's ``..._PROVIDER_FAILED`` code with a
    fixed message — never the raw exception message, key, prompt, or provider payload.
    """
    try:
        if factory is not None:
            return factory(selection)
        return _new_budgeted_llm_client(
            model=selection.model, max_tokens=selection.max_tokens, tenant_id=tenant_id
        )
    except StrictLiveRoleError:
        raise
    except Exception as exc:
        raise StrictLiveRoleError(
            code=provider_failed_code,
            message=f"Strict live {role} provider construction or call failed.",
        ) from exc


def _build_scoring_llm_client(
    *,
    scoring_client_factory: ScoringClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
    tenant_id: str | None = None,
) -> Any:
    """Build the LLM client for scoring based on env configuration.

    Reads IDIS_DEBATE_BACKEND (default: deterministic). When ``scoring_client_factory`` is
    supplied it is called with the resolved ``ScoringClientSelection`` and its return value is
    used (injection seam for tests / strict wiring). When ``strict_live_debate_backend_required``
    is True (threaded only from the strict FULL execution path) a non-anthropic backend fails
    closed with ``STRICT_LIVE_SCORING_REQUIRED`` (deterministic scoring is forbidden) and an
    anthropic provider failure surfaces ``STRICT_LIVE_SCORING_PROVIDER_FAILED``. The non-strict
    default path is unchanged.

    Returns:
        An LLMClient implementation instance.
    """
    selection = _resolve_scoring_selection()

    if selection.backend == "anthropic":
        if strict_live_debate_backend_required:
            return _build_strict_anthropic_role_client(
                selection,
                scoring_client_factory,
                role="scoring",
                provider_failed_code=STRICT_LIVE_SCORING_PROVIDER_FAILED,
                tenant_id=tenant_id,
            )
        if scoring_client_factory is not None:
            return scoring_client_factory(selection)
        return _new_budgeted_llm_client(
            model=selection.model, max_tokens=selection.max_tokens, tenant_id=tenant_id
        )

    if strict_live_debate_backend_required:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_SCORING_REQUIRED,
            message=(
                "Strict live scoring requires the anthropic backend; "
                "deterministic scoring is forbidden."
            ),
        )
    if scoring_client_factory is not None:
        return scoring_client_factory(selection)

    from idis.services.extraction.extractors.llm_client import DeterministicScoringLLMClient

    return DeterministicScoringLLMClient()


def _build_analysis_llm_client(
    *,
    analysis_client_factory: AnalysisClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
    tenant_id: str | None = None,
) -> Any:
    """Build the LLM client for analysis agents based on env configuration.

    Reads IDIS_DEBATE_BACKEND (default: deterministic). When ``analysis_client_factory`` is
    supplied it is called with the resolved ``AnalysisClientSelection`` and its return value is
    used (injection seam for tests / strict wiring). When ``strict_live_debate_backend_required``
    is True (threaded only from the strict FULL execution path) a non-anthropic backend fails
    closed with ``STRICT_LIVE_ANALYSIS_REQUIRED`` (deterministic analysis is forbidden) and an
    anthropic provider failure surfaces ``STRICT_LIVE_ANALYSIS_PROVIDER_FAILED``. The non-strict
    default path is unchanged.

    Returns:
        An LLMClient implementation instance.
    """
    selection = _resolve_analysis_selection()

    if selection.backend == "anthropic":
        if strict_live_debate_backend_required:
            return _build_strict_anthropic_role_client(
                selection,
                analysis_client_factory,
                role="analysis",
                provider_failed_code=STRICT_LIVE_ANALYSIS_PROVIDER_FAILED,
                tenant_id=tenant_id,
            )
        if analysis_client_factory is not None:
            return analysis_client_factory(selection)
        return _new_budgeted_llm_client(
            model=selection.model, max_tokens=selection.max_tokens, tenant_id=tenant_id
        )

    if strict_live_debate_backend_required:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_ANALYSIS_REQUIRED,
            message=(
                "Strict live analysis requires the anthropic backend; "
                "deterministic analysis is forbidden."
            ),
        )
    if analysis_client_factory is not None:
        return analysis_client_factory(selection)

    from idis.services.extraction.extractors.llm_client import DeterministicAnalysisLLMClient

    return DeterministicAnalysisLLMClient()


# Safe, fixed strict-live-extraction outcome codes (value == name, mirroring
# STRICT_FULL_LIVE_BLOCKED). Surfaced only as codes — never with secrets or raw messages.
STRICT_LIVE_EXTRACTION_REQUIRED = "STRICT_LIVE_EXTRACTION_REQUIRED"
STRICT_LIVE_EXTRACTION_PROVIDER_FAILED = "STRICT_LIVE_EXTRACTION_PROVIDER_FAILED"


class StrictLiveExtractionError(Exception):
    """Strict FULL extraction could not use an approved live extractor backend.

    Carries only a safe, fixed ``code`` + ``message`` — never the API key, prompt, response,
    provider payload, or a raw underlying exception message.
    """

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ExtractorClientSelection:
    """Safe selection context handed to an injected extractor-client factory.

    Carries only the resolved backend + model + max_tokens — never the API key or any other
    secret. The factory (test seam / future strict wiring) returns the extraction client.
    """

    backend: str
    model: str | None
    max_tokens: int


ExtractorClientFactory = Callable[[ExtractorClientSelection], Any]

_EXTRACTION_PROMPT_ID = "EXTRACT_CLAIMS_V1"


def _resolve_extraction_selection() -> ExtractorClientSelection:
    """Resolve the extraction backend, model, and token limit from the admitted row."""
    backend = os.environ.get("IDIS_EXTRACT_BACKEND", "deterministic")
    config = resolve_role_config(LlmRole.EXTRACTION, backend=backend)
    return ExtractorClientSelection(
        backend=backend,
        model=config.model,
        max_tokens=config.max_output_tokens,
    )


def _build_strict_anthropic_extractor(
    selection: ExtractorClientSelection,
    extractor_client_factory: ExtractorClientFactory | None,
    *,
    tenant_id: str | None = None,
) -> Any:
    """Build the live (Anthropic) extractor for strict FULL, failing closed safely.

    Any construction/call failure is surfaced as ``STRICT_LIVE_EXTRACTION_PROVIDER_FAILED``
    with a fixed message — never the raw exception message, key, prompt, or provider payload.
    """
    try:
        if extractor_client_factory is not None:
            return extractor_client_factory(selection)
        return _new_budgeted_llm_client(
            model=selection.model, max_tokens=selection.max_tokens, tenant_id=tenant_id
        )
    except StrictLiveExtractionError:
        raise
    except Exception as exc:
        raise StrictLiveExtractionError(
            code=STRICT_LIVE_EXTRACTION_PROVIDER_FAILED,
            message="Strict live extraction provider construction or call failed.",
        ) from exc


def _build_extraction_llm_client(
    *,
    extractor_client_factory: ExtractorClientFactory | None = None,
    strict_live_extraction_required: bool = False,
    tenant_id: str | None = None,
) -> Any:
    """Build the LLM client for extraction based on env configuration.

    Reads IDIS_EXTRACT_BACKEND (default: deterministic).
    Fail-closed: raises ValueError if anthropic backend selected but key missing.

    When ``extractor_client_factory`` is supplied it is called with the resolved
    ``ExtractorClientSelection`` and its return value is used as the extraction client
    (injection seam for tests / strict wiring). When ``strict_live_extraction_required`` is
    True (threaded only from the strict FULL execution path) a non-anthropic backend fails
    closed with ``STRICT_LIVE_EXTRACTION_REQUIRED`` (deterministic extraction is forbidden)
    and an anthropic provider failure surfaces ``STRICT_LIVE_EXTRACTION_PROVIDER_FAILED``.
    The non-strict default path is unchanged.

    Returns:
        An LLMClient implementation instance.

    Raises:
        ValueError: If IDIS_EXTRACT_BACKEND=anthropic but ANTHROPIC_API_KEY is unset
            (non-strict default path only; an injected factory builds its own client).
        StrictLiveExtractionError: If strict-live extraction is required but a live approved
            backend is not available/usable.
    """
    selection = _resolve_extraction_selection()

    if selection.backend == "anthropic":
        if strict_live_extraction_required:
            return _build_strict_anthropic_extractor(
                selection, extractor_client_factory, tenant_id=tenant_id
            )
        if extractor_client_factory is not None:
            return extractor_client_factory(selection)
        return _new_budgeted_llm_client(
            model=selection.model, max_tokens=selection.max_tokens, tenant_id=tenant_id
        )

    # Non-anthropic backend (deterministic / unset / other).
    if strict_live_extraction_required:
        raise StrictLiveExtractionError(
            code=STRICT_LIVE_EXTRACTION_REQUIRED,
            message=(
                "Strict live extraction requires the anthropic backend; "
                "deterministic extraction is forbidden."
            ),
        )
    if extractor_client_factory is not None:
        return extractor_client_factory(selection)

    from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

    return DeterministicLLMClient()


def _safe_client_request_id(client: Any) -> str | None:
    """Return a safe provider request id if the client exposes one, else None."""
    value = getattr(client, "provider_request_id", None)
    return value if isinstance(value, str) and value.strip() else None


def _extraction_prompt_version() -> str | None:
    """Return the extraction prompt's registry version from prompts/registry.yaml (safe)."""
    return _prompt_registry_version(_EXTRACTION_PROMPT_ID)


def _build_extraction_provenance(
    *,
    selection: ExtractorClientSelection,
    strict_live_extraction_required: bool,
    client: Any,
) -> dict[str, Any]:
    """Build a safe, additive extraction-provenance block for the step summary.

    Records only provider/backend + safe model name + prompt id/version + the strict flag +
    a sanitized provider request id (only if the client safely exposes one) — never the API
    key, prompt body, response text, raw provider payload, exception message, or a path.
    """
    from idis.services.llm_model_health import _sanitize_request_id

    provider = "anthropic" if selection.backend == "anthropic" else "deterministic"
    return {
        "provider": provider,
        "backend": selection.backend,
        "model": selection.model,
        "prompt_id": _EXTRACTION_PROMPT_ID,
        "prompt_version": _extraction_prompt_version(),
        "strict_live_extraction_required": bool(strict_live_extraction_required),
        "provider_request_id": _sanitize_request_id(_safe_client_request_id(client)),
    }


# --- Slice84 Task 4: safe, additive provenance + debate observability ---
_SCORING_PROMPT_ID = "scoring_agent"  # on-disk prompt family: prompts/scoring_agent/1.0.0/
_SCORING_PROMPT_VERSION = "1.0.0"
_DEBATE_PROMPT_IDS = (
    "DEBATE_ADVOCATE_V1",
    "DEBATE_SANAD_BREAKER_V1",
    "DEBATE_CONTRADICTION_FINDER_V1",
    "DEBATE_RISK_OFFICER_V1",
    "DEBATE_ARBITER_V1",
)
_DEBATE_PROMPT_VERSION_ID = "DEBATE_ARBITER_V1"


def _prompt_registry_version(prompt_id: str) -> str | None:
    """Return a prompt's registry version from prompts/registry.yaml (safe), else None."""
    import yaml

    try:
        registry_path = _find_project_root() / "prompts" / "registry.yaml"
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    prompt = (data.get("prompts") or {}).get(prompt_id) or {}
    version = prompt.get("version")
    return version.strip() if isinstance(version, str) and version.strip() else None


def _provider_label(backend: str) -> str:
    """Map a resolved backend to a safe provider label."""
    return "anthropic" if backend == "anthropic" else "deterministic"


def _build_role_client_provenance(
    *,
    selection: Any,
    prompt_id: str | None,
    prompt_version: str | None,
    strict_live_debate_backend_required: bool,
    client: Any,
) -> dict[str, Any]:
    """Build a safe, additive provenance block for a single-client role (analysis/scoring).

    Records only provider/backend + safe model name + prompt id/version + the strict flag + a
    sanitized provider request id (only if the client safely exposes one) — never the API key,
    prompt body, response text, raw provider payload, exception message, or a path.
    """
    from idis.services.llm_model_health import _sanitize_request_id

    return {
        "provider": _provider_label(selection.backend),
        "backend": selection.backend,
        "model": selection.model,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "strict_live_debate_backend_required": bool(strict_live_debate_backend_required),
        "provider_request_id": _sanitize_request_id(_safe_client_request_id(client)),
    }


def _build_analysis_provenance(
    *, selection: Any, strict_live_debate_backend_required: bool, client: Any
) -> dict[str, Any]:
    """Safe analysis provenance.

    Analysis runs 8 embedded specialist prompts (no single registry prompt id), so
    ``prompt_id``/``prompt_version`` are null by design; the rest mirrors the shared block.
    """
    return _build_role_client_provenance(
        selection=selection,
        prompt_id=None,
        prompt_version=None,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        client=client,
    )


def _build_scoring_provenance(
    *, selection: Any, strict_live_debate_backend_required: bool, client: Any
) -> dict[str, Any]:
    """Safe scoring provenance (on-disk prompt family scoring_agent/1.0.0)."""
    return _build_role_client_provenance(
        selection=selection,
        prompt_id=_SCORING_PROMPT_ID,
        prompt_version=_SCORING_PROMPT_VERSION,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        client=client,
    )


def _safe_runner_request_id(runner: Any) -> str | None:
    """Return a safe provider request id from a role runner's client, if any."""
    client = getattr(runner, "llm_client", None)
    return _safe_client_request_id(client) if client is not None else None


def _build_debate_provenance(
    *, selection: Any, strict_live_debate_backend_required: bool, role_runners: Any
) -> dict[str, Any]:
    """Build safe, additive debate provenance.

    Records default + arbiter model names, the 5 registry debate prompt ids + their version, the
    strict flag, and sanitized default/arbiter provider request ids (only if safely exposed) —
    never the API key, prompt body, response, raw payload, exception message, or a path.
    """
    from idis.services.llm_model_health import _sanitize_request_id

    return {
        "provider": _provider_label(selection.backend),
        "backend": selection.backend,
        "default_model": selection.default_model,
        "arbiter_model": selection.arbiter_model,
        "prompt_ids": list(_DEBATE_PROMPT_IDS),
        "prompt_version": _prompt_registry_version(_DEBATE_PROMPT_VERSION_ID),
        "strict_live_debate_backend_required": bool(strict_live_debate_backend_required),
        "default_provider_request_id": _sanitize_request_id(
            _safe_runner_request_id(getattr(role_runners, "advocate", None))
        ),
        "arbiter_provider_request_id": _sanitize_request_id(
            _safe_runner_request_id(getattr(role_runners, "arbiter", None))
        ),
    }


_LAYER2_CHALLENGER_PROMPT_ID = "layer2_ic_challenger"
_LAYER2_ARBITER_PROMPT_ID = "layer2_ic_arbiter"
_LAYER2_PROMPT_IDS = (_LAYER2_CHALLENGER_PROMPT_ID, _LAYER2_ARBITER_PROMPT_ID)
_LAYER2_PROMPT_VERSION = "1.0.0"


def _safe_runner_executed(runner: Any) -> bool:
    """True when a Layer-2 role runner reports a completed live model call."""
    return bool(getattr(runner, "executed", False))


def _build_layer2_provenance(
    *,
    strict_full_live: bool,
    backend: str,
    challenger_model: str | None,
    arbiter_model: str | None,
    challenger_runner: Any,
    arbiter_runner: Any,
) -> dict[str, Any]:
    """Build safe, additive Layer-2 IC challenge provenance (Slice93 Task 5, DEC-F).

    Records provider/backend + challenger/arbiter model names + the two registry prompt ids
    and version + the strict flag + sanitized challenger/arbiter provider request ids (only if
    a client safely exposes one) + booleans proving each live runner executed — never the API
    key, prompt body, model output, raw rationale, or a path. ``live_calls_executed`` is the
    per-run runtime-execution signal (both challenger and arbiter calls completed).
    """
    from idis.services.llm_model_health import _sanitize_request_id

    challenger_executed = _safe_runner_executed(challenger_runner)
    arbiter_executed = _safe_runner_executed(arbiter_runner)
    return {
        "provider": _provider_label(backend),
        "backend": backend,
        "challenger_model": challenger_model,
        "arbiter_model": arbiter_model,
        "prompt_ids": list(_LAYER2_PROMPT_IDS),
        "prompt_version": _LAYER2_PROMPT_VERSION,
        "strict_full_live": bool(strict_full_live),
        "challenger_provider_request_id": _sanitize_request_id(
            _safe_runner_request_id(challenger_runner)
        ),
        "arbiter_provider_request_id": _sanitize_request_id(
            _safe_runner_request_id(arbiter_runner)
        ),
        "challenger_executed": challenger_executed,
        "arbiter_executed": arbiter_executed,
        "live_calls_executed": bool(challenger_executed and arbiter_executed),
    }


def _build_debate_observability(final_state: Any) -> dict[str, Any]:
    """Build safe, additive debate observability for the step summary.

    Surfaces only bounded counts/booleans + safe source-reference ids + a FIXED arbiter rationale
    summary — never the raw arbiter rationale (which may contain private model text), agent output
    content, the API key, response, raw payload, or a path.
    """
    decisions = list(getattr(final_state, "arbiter_decisions", []) or [])
    return {
        "round_number": final_state.round_number,
        "stop_reason": (final_state.stop_reason.value if final_state.stop_reason else None),
        "agent_output_count": len(final_state.agent_outputs),
        "arbiter_decision_count": len(decisions),
        "dissent_preserved": any(bool(d.dissent_preserved) for d in decisions),
        "challenges_validated_count": sum(len(d.challenges_validated) for d in decisions),
        "arbiter_rationale_summary": (
            "arbiter_decision_recorded" if decisions else "no_arbiter_decision"
        ),
        "source_reference_ids": [
            final_state.claim_registry_ref,
            final_state.sanad_graph_ref,
        ],
    }


def _build_debate_role_runners(
    context: Any = None,
    *,
    debate_role_runners_factory: DebateRoleRunnersFactory | None = None,
    strict_live_debate_backend_required: bool = False,
    tenant_id: str | None = None,
) -> Any:
    """Build role runners for debate based on env configuration.

    Reads IDIS_DEBATE_BACKEND (default: deterministic).
    Fail-closed: raises ValueError if anthropic backend selected but key missing.

    When ``debate_role_runners_factory`` is supplied it is called with the resolved
    ``DebateRoleRunnerSelection`` and its return value (a complete ``RoleRunners``) is used
    (injection seam for tests / strict wiring). When ``strict_live_debate_backend_required`` is
    True (threaded only from the strict FULL execution path) a non-anthropic backend fails closed
    with ``STRICT_LIVE_DEBATE_REQUIRED`` (deterministic debate is forbidden) and an anthropic
    provider failure surfaces ``STRICT_LIVE_DEBATE_PROVIDER_FAILED``. The non-strict default path
    is unchanged.

    Args:
        context: Optional DebateContext with rich pipeline data for LLM agents.

    Returns:
        RoleRunners instance (deterministic or LLM-backed).

    Raises:
        ValueError: If IDIS_DEBATE_BACKEND=anthropic but ANTHROPIC_API_KEY is unset
            (default path only; an injected factory builds its own runners).
        StrictLiveRoleError: If strict-live debate is required but a live approved backend is
            not available/usable.
    """
    from idis.debate.orchestrator import RoleRunners

    selection = _resolve_debate_selection()

    if selection.backend == "anthropic":
        if strict_live_debate_backend_required:
            return _build_strict_debate_role_runners(
                selection, context, debate_role_runners_factory, tenant_id=tenant_id
            )
        if debate_role_runners_factory is not None:
            return debate_role_runners_factory(selection)
        return _build_live_debate_role_runners(selection, context, tenant_id=tenant_id)

    if strict_live_debate_backend_required:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_DEBATE_REQUIRED,
            message=(
                "Strict live debate requires the anthropic backend; "
                "deterministic debate is forbidden."
            ),
        )
    if debate_role_runners_factory is not None:
        return debate_role_runners_factory(selection)
    return RoleRunners()


def _build_strict_debate_role_runners(
    selection: DebateRoleRunnerSelection,
    context: Any,
    debate_role_runners_factory: DebateRoleRunnersFactory | None,
    *,
    tenant_id: str | None = None,
) -> Any:
    """Build the live debate RoleRunners for strict FULL, failing closed safely.

    Any construction/call failure is surfaced as ``STRICT_LIVE_DEBATE_PROVIDER_FAILED`` with a
    fixed message — never the raw exception message, key, prompt, or provider payload.
    """
    try:
        if debate_role_runners_factory is not None:
            return debate_role_runners_factory(selection)
        return _build_live_debate_role_runners(selection, context, tenant_id=tenant_id)
    except StrictLiveRoleError:
        raise
    except Exception as exc:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_DEBATE_PROVIDER_FAILED,
            message="Strict live debate provider construction or call failed.",
        ) from exc


def _build_live_debate_role_runners(
    selection: DebateRoleRunnerSelection, context: Any, *, tenant_id: str | None = None
) -> Any:
    """Construct the live (Anthropic) debate RoleRunners (5 LLM role runners)."""
    from idis.debate.orchestrator import RoleRunners
    from idis.debate.roles.llm_role_runner import LLMRoleRunner
    from idis.models.debate import DebateRole

    prompts = _load_debate_prompts()

    default_client = _new_budgeted_llm_client(
        model=selection.default_model, max_tokens=selection.max_tokens, tenant_id=tenant_id
    )
    arbiter_client = _new_budgeted_llm_client(
        model=selection.arbiter_model, max_tokens=selection.max_tokens, tenant_id=tenant_id
    )

    return RoleRunners(
        advocate=LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=default_client,
            system_prompt=prompts["advocate"],
            context=context,
        ),
        sanad_breaker=LLMRoleRunner(
            role=DebateRole.SANAD_BREAKER,
            llm_client=default_client,
            system_prompt=prompts["sanad_breaker"],
            context=context,
        ),
        contradiction_finder=LLMRoleRunner(
            role=DebateRole.CONTRADICTION_FINDER,
            llm_client=default_client,
            system_prompt=prompts["contradiction_finder"],
            context=context,
        ),
        risk_officer=LLMRoleRunner(
            role=DebateRole.RISK_OFFICER,
            llm_client=default_client,
            system_prompt=prompts["risk_officer"],
            context=context,
        ),
        arbiter=LLMRoleRunner(
            role=DebateRole.ARBITER,
            llm_client=arbiter_client,
            system_prompt=prompts["arbiter"],
            context=context,
        ),
    )


def _load_debate_prompts() -> dict[str, str]:
    """Load debate role prompt texts from disk.

    Reads from prompts/<role>/1.0.0/prompt.md for each role.
    Fail-closed: raises FileNotFoundError if any prompt is missing.

    Returns:
        Dict mapping role name to prompt text.
    """
    root = _find_project_root()
    role_dirs = {
        "advocate": "debate_advocate",
        "sanad_breaker": "debate_sanad_breaker",
        "contradiction_finder": "debate_contradiction_finder",
        "risk_officer": "debate_risk_officer",
        "arbiter": "debate_arbiter",
    }

    prompts: dict[str, str] = {}
    for role_key, dir_name in role_dirs.items():
        prompt_path = root / "prompts" / dir_name / "1.0.0" / "prompt.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Debate prompt file not found: {prompt_path}")
        prompts[role_key] = prompt_path.read_text(encoding="utf-8")

    return prompts
