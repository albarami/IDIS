"""Write the admitted GL-05 slice-1 role-config file.

The output reproduces today's per-role model, token limit, binding variable, and
prompt pin. Run from the repository root:

    python scripts/generate_llm_role_configs_v1.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from idis.providers.role_config import (  # noqa: E402
    ADMITTED_BUDGET_COMPONENT_ID,
    ADMITTED_DOCUMENT_VERSION,
    ADMITTED_FALLBACK_POLICY,
    ADMITTED_GENERATOR,
    ADMITTED_PROVIDER,
    ADMITTED_TEMPERATURE,
    LlmRole,
    RoleConfigError,
    admitted_document_sha256,
    role_row_sha256,
    verify_admitted_document,
)
from idis.services.prompts.registry import compute_content_hash  # noqa: E402

_SONNET_4 = "claude-sonnet-4-20250514"
_OPUS_4 = "claude-opus-4-20250514"
_EXTRACT_ENV = "IDIS_ANTHROPIC_MODEL_EXTRACT"
_DEFAULT_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"
_ARBITER_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER"
_PROMPT_VERSION = "1.0.0"
_EXTRACTION_MAX_TOKENS = 4096
_ANALYSIS_MAX_TOKENS = 8192
_SCORING_MAX_TOKENS = 16384
_DEBATE_MAX_TOKENS = 8192
_IC_MAX_TOKENS = 8192

# role, model, binding env, max tokens, registry id or None, prompt directory
_ROLE_SPECS: tuple[tuple[LlmRole, str, str, int, str | None, str], ...] = (
    (
        LlmRole.EXTRACTION,
        _SONNET_4,
        _EXTRACT_ENV,
        _EXTRACTION_MAX_TOKENS,
        "EXTRACT_CLAIMS_V1",
        "extract_claims",
    ),
    (
        LlmRole.METHODOLOGY_EXTRACTION,
        _SONNET_4,
        _EXTRACT_ENV,
        _EXTRACTION_MAX_TOKENS,
        "METHODOLOGY_QUESTION_EXTRACTION_V1",
        "methodology_question_extraction",
    ),
    (
        LlmRole.FINANCIAL_AGENT,
        _SONNET_4,
        _DEFAULT_ENV,
        _ANALYSIS_MAX_TOKENS,
        None,
        "financial_agent",
    ),
    (
        LlmRole.HISTORIAN_AGENT,
        _SONNET_4,
        _DEFAULT_ENV,
        _ANALYSIS_MAX_TOKENS,
        None,
        "historian_agent",
    ),
    (LlmRole.MARKET_AGENT, _SONNET_4, _DEFAULT_ENV, _ANALYSIS_MAX_TOKENS, None, "market_agent"),
    (
        LlmRole.RISK_OFFICER_AGENT,
        _SONNET_4,
        _DEFAULT_ENV,
        _ANALYSIS_MAX_TOKENS,
        None,
        "risk_officer_agent",
    ),
    (
        LlmRole.SECTOR_SPECIALIST_AGENT,
        _SONNET_4,
        _DEFAULT_ENV,
        _ANALYSIS_MAX_TOKENS,
        None,
        "sector_specialist_agent",
    ),
    (LlmRole.TEAM_AGENT, _SONNET_4, _DEFAULT_ENV, _ANALYSIS_MAX_TOKENS, None, "team_agent"),
    (
        LlmRole.TECHNICAL_AGENT,
        _SONNET_4,
        _DEFAULT_ENV,
        _ANALYSIS_MAX_TOKENS,
        None,
        "technical_agent",
    ),
    (LlmRole.TERMS_AGENT, _SONNET_4, _DEFAULT_ENV, _ANALYSIS_MAX_TOKENS, None, "terms_agent"),
    (
        LlmRole.SCORING,
        _SONNET_4,
        _DEFAULT_ENV,
        _SCORING_MAX_TOKENS,
        "scoring_agent",
        "scoring_agent",
    ),
    (
        LlmRole.ADVOCATE,
        _SONNET_4,
        _DEFAULT_ENV,
        _DEBATE_MAX_TOKENS,
        "DEBATE_ADVOCATE_V1",
        "debate_advocate",
    ),
    (
        LlmRole.SANAD_BREAKER,
        _SONNET_4,
        _DEFAULT_ENV,
        _DEBATE_MAX_TOKENS,
        "DEBATE_SANAD_BREAKER_V1",
        "debate_sanad_breaker",
    ),
    (
        LlmRole.CONTRADICTION_FINDER,
        _SONNET_4,
        _DEFAULT_ENV,
        _DEBATE_MAX_TOKENS,
        "DEBATE_CONTRADICTION_FINDER_V1",
        "debate_contradiction_finder",
    ),
    (
        LlmRole.RISK_OFFICER,
        _SONNET_4,
        _DEFAULT_ENV,
        _DEBATE_MAX_TOKENS,
        "DEBATE_RISK_OFFICER_V1",
        "debate_risk_officer",
    ),
    (
        LlmRole.ARBITER,
        _OPUS_4,
        _ARBITER_ENV,
        _DEBATE_MAX_TOKENS,
        "DEBATE_ARBITER_V1",
        "debate_arbiter",
    ),
    (LlmRole.IC_CHALLENGER, _SONNET_4, _DEFAULT_ENV, _IC_MAX_TOKENS, None, "layer2_ic_challenger"),
    (LlmRole.IC_ARBITER, _OPUS_4, _ARBITER_ENV, _IC_MAX_TOKENS, None, "layer2_ic_arbiter"),
)


def _hash_prompt(relative: str) -> str:
    path = _ROOT / relative
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RoleConfigError("LLM_ROLE_CONFIG_UNADMITTED") from exc
    return compute_content_hash(text)


def _role_row(
    role: LlmRole,
    *,
    model: str,
    model_binding_env: str,
    max_output_tokens: int,
    prompt_id: str | None,
    prompt_dir: str,
) -> dict[str, object]:
    prompt_path = f"prompts/{prompt_dir}/{_PROMPT_VERSION}/prompt.md"
    identity: dict[str, object] = {
        "role": role.value,
        "provider": ADMITTED_PROVIDER,
        "model": model,
        "model_binding_env": model_binding_env,
        "effort": None,
        "temperature": ADMITTED_TEMPERATURE,
        "max_output_tokens": max_output_tokens,
        "prompt_id": prompt_id,
        "prompt_version": _PROMPT_VERSION,
        "prompt_path": prompt_path,
        "prompt_content_sha256": _hash_prompt(prompt_path),
        "budget_component_id": ADMITTED_BUDGET_COMPONENT_ID,
        "fallback_policy": ADMITTED_FALLBACK_POLICY,
    }
    identity["config_sha256"] = role_row_sha256(identity)
    return identity


def build_document() -> dict[str, object]:
    """Build the admitted document, including both row and document hashes."""
    if tuple(spec[0] for spec in _ROLE_SPECS) != tuple(LlmRole):
        raise RoleConfigError("LLM_ROLE_CONFIG_UNADMITTED")
    roles = [
        _role_row(
            role,
            model=model,
            model_binding_env=model_binding_env,
            max_output_tokens=max_output_tokens,
            prompt_id=prompt_id,
            prompt_dir=prompt_dir,
        )
        for role, model, model_binding_env, max_output_tokens, prompt_id, prompt_dir in _ROLE_SPECS
    ]
    document: dict[str, object] = {
        "document_version": ADMITTED_DOCUMENT_VERSION,
        "generator": ADMITTED_GENERATOR,
        "roles": roles,
    }
    document["document_sha256"] = admitted_document_sha256(document)
    verify_admitted_document(document)
    return document


def render_document() -> str:
    """Return the canonical JSON text, with a single trailing newline."""
    return json.dumps(build_document(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the admitted file. Returns 0 on success."""
    parser = argparse.ArgumentParser(description="Write contracts/llm_role_configs_v1.json")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    destination = args.output or (_ROOT / "contracts" / "llm_role_configs_v1.json")
    try:
        text = render_document()
    except RoleConfigError as exc:
        sys.stderr.write(f"{exc.code}\n")
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
