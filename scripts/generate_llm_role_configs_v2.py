"""Write the inactive GL-05 candidate role-config file.

The rows are the master-plan proposals. They are not qualified and the file
is not activated. Run from the repository root:

    python scripts/generate_llm_role_configs_v2.py

An existing different file is left unchanged unless ``--force`` is set.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, NoReturn, cast

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from idis.models.anthropic_price_catalog_v2 import (  # noqa: E402
    AnthropicCandidatePriceError,
    resolve_candidate_price,
)
from idis.providers.anthropic_capabilities import (  # noqa: E402
    CANDIDATE_OPUS_ID,
    CANDIDATE_SONNET_ID,
    resolve_model_capability,
)
from idis.providers.role_config import LlmRole  # noqa: E402
from idis.providers.role_config_v2 import (  # noqa: E402
    CANDIDATE_DOCUMENT_VERSION,
    CANDIDATE_GENERATOR,
    CANDIDATE_SAMPLING_POLICY,
    CANDIDATE_THINKING_MODE,
    LLM_ROLE_CANDIDATE_DOCUMENT_INVALID,
    CandidateActivation,
    CandidateDocumentError,
    CandidateQualification,
    UnboundReason,
    candidate_document_sha256,
    candidate_row_sha256,
    verify_candidate_document,
)

_PERPLEXITY_SONNET_ID = "anthropic/" + CANDIDATE_SONNET_ID
_OVERWRITE_REFUSED = "LLM_ROLE_CANDIDATE_OVERWRITE_REFUSED"
_PROMPT_PIN_FIELDS = (
    "prompt_id",
    "prompt_version",
    "prompt_path",
    "prompt_content_sha256",
)

# role, model, effort, output ceiling
_ROLE_SPECS: tuple[tuple[LlmRole, str, str, int], ...] = (
    (LlmRole.METHODOLOGY_EXTRACTION, CANDIDATE_SONNET_ID, "medium", 16384),
    (LlmRole.MARKET_AGENT, CANDIDATE_SONNET_ID, "high", 16384),
    (LlmRole.TECHNICAL_AGENT, CANDIDATE_SONNET_ID, "high", 16384),
    (LlmRole.SECTOR_SPECIALIST_AGENT, CANDIDATE_SONNET_ID, "high", 16384),
    (LlmRole.TEAM_AGENT, CANDIDATE_SONNET_ID, "medium", 12288),
    (LlmRole.HISTORIAN_AGENT, CANDIDATE_SONNET_ID, "medium", 12288),
    (LlmRole.FINANCIAL_AGENT, CANDIDATE_OPUS_ID, "high", 32768),
    (LlmRole.TERMS_AGENT, CANDIDATE_OPUS_ID, "high", 16384),
    (LlmRole.RISK_OFFICER_AGENT, CANDIDATE_OPUS_ID, "high", 24576),
    (LlmRole.ADVOCATE, CANDIDATE_SONNET_ID, "medium", 16384),
    (LlmRole.SANAD_BREAKER, CANDIDATE_SONNET_ID, "high", 16384),
    (LlmRole.CONTRADICTION_FINDER, CANDIDATE_SONNET_ID, "high", 16384),
    (LlmRole.RISK_OFFICER, CANDIDATE_OPUS_ID, "high", 24576),
    (LlmRole.ARBITER, CANDIDATE_OPUS_ID, "high", 32768),
    (LlmRole.SCORING, CANDIDATE_SONNET_ID, "medium", 16384),
    (LlmRole.IC_CHALLENGER, CANDIDATE_OPUS_ID, "high", 24576),
    (LlmRole.IC_ARBITER, CANDIDATE_OPUS_ID, "high", 32768),
)

# plan label, proposed model id
_UNBOUND_PLAN_ROLES: tuple[tuple[str, str], ...] = (
    ("IC advocate", CANDIDATE_SONNET_ID),
    ("assertion-support check", CANDIDATE_SONNET_ID),
    ("Narrative / IC memo drafting", CANDIDATE_SONNET_ID),
    ("External research through Perplexity Agent API", _PERPLEXITY_SONNET_ID),
    ("Same-evidence comparator, later qualified fallback", "gpt-6.1-sol"),
    ("Later cheap classification candidate", "claude-haiku-5-5"),
    ("Embeddings", "text-embedding-3-small"),
)


def _fail(exc: BaseException | None = None) -> NoReturn:
    raise CandidateDocumentError(LLM_ROLE_CANDIDATE_DOCUMENT_INVALID) from exc


def _write_code(code: str) -> None:
    cast(BinaryIO, sys.stderr.buffer).write(f"{code}\n".encode())


def _v1_prompt_pins() -> dict[str, dict[str, Any]]:
    path = _ROOT / "contracts" / "llm_role_configs_v1.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(exc)
    if not isinstance(loaded, dict) or not isinstance(loaded.get("roles"), list):
        _fail()
    pins: dict[str, dict[str, Any]] = {}
    for row in loaded["roles"]:
        if not isinstance(row, dict) or not isinstance(row.get("role"), str):
            _fail()
        try:
            pins[row["role"]] = {field: row[field] for field in _PROMPT_PIN_FIELDS}
        except KeyError as exc:
            _fail(exc)
    return pins


def _price_hash(model_id: str) -> str:
    try:
        return resolve_candidate_price(model_id).row_sha256
    except AnthropicCandidatePriceError as exc:
        _fail(exc)


def _role_row(
    role: LlmRole,
    *,
    model: str,
    effort: str,
    max_output_tokens: int,
    pins: dict[str, Any],
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "role": role.value,
        "provider": "anthropic",
        "model": model,
        "request_shape_id": resolve_model_capability(model).shape_id,
        "effort": effort,
        "thinking_mode": CANDIDATE_THINKING_MODE,
        "sampling_policy": CANDIDATE_SAMPLING_POLICY,
        "max_output_tokens": max_output_tokens,
        "price_row_sha256": _price_hash(model),
        "prompt_id": pins["prompt_id"],
        "prompt_version": pins["prompt_version"],
        "prompt_path": pins["prompt_path"],
        "prompt_content_sha256": pins["prompt_content_sha256"],
        "budget_component_id": "anthropic",
        "fallback_policy": "none",
        "qualification_status": CandidateQualification.CANDIDATE_NOT_QUALIFIED.value,
    }
    identity["config_sha256"] = candidate_row_sha256(identity)
    return identity


def build_document() -> dict[str, Any]:
    """Build the inactive document, including row and document hashes."""
    if len({spec[0] for spec in _ROLE_SPECS}) != len(_ROLE_SPECS):
        _fail()
    if LlmRole.EXTRACTION in {spec[0] for spec in _ROLE_SPECS}:
        _fail()
    if len({spec[0] for spec in _UNBOUND_PLAN_ROLES}) != len(_UNBOUND_PLAN_ROLES):
        _fail()
    pins = _v1_prompt_pins()
    roles: list[dict[str, Any]] = []
    for role, model, effort, max_output_tokens in _ROLE_SPECS:
        role_pins = pins.get(role.value)
        if role_pins is None:
            _fail()
        roles.append(
            _role_row(
                role,
                model=model,
                effort=effort,
                max_output_tokens=max_output_tokens,
                pins=role_pins,
            )
        )
    unbound: list[dict[str, str]] = [
        {"role": LlmRole.EXTRACTION.value, "reason": UnboundReason.NO_PLAN_ROW.value}
    ]
    for plan_role, proposed_model in _UNBOUND_PLAN_ROLES:
        unbound.append(
            {
                "plan_role": plan_role,
                "proposed_model": proposed_model,
                "reason": UnboundReason.NOT_A_RUNTIME_ROLE.value,
            }
        )
    document: dict[str, Any] = {
        "document_version": CANDIDATE_DOCUMENT_VERSION,
        "generator": CANDIDATE_GENERATOR,
        "qualification_status": CandidateQualification.CANDIDATE_NOT_QUALIFIED.value,
        "activation": CandidateActivation.INACTIVE.value,
        "roles": roles,
        "unbound_plan_roles": unbound,
    }
    document["document_sha256"] = candidate_document_sha256(document)
    verify_candidate_document(document)
    return document


def render_document() -> str:
    """Return the JSON text, with a single trailing newline."""
    return json.dumps(build_document(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the candidate file. Returns 0 on success."""
    parser = argparse.ArgumentParser(description="Write contracts/llm_role_configs_v2.json")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing file whose bytes differ",
    )
    args = parser.parse_args(argv)
    destination = args.output or (_ROOT / "contracts" / "llm_role_configs_v2.json")
    try:
        encoded = render_document().encode("utf-8")
    except CandidateDocumentError as exc:
        _write_code(exc.code)
        return 1
    if destination.is_file() and not args.force:
        try:
            current = destination.read_bytes()
        except OSError:
            _write_code(LLM_ROLE_CANDIDATE_DOCUMENT_INVALID)
            return 1
        if current != encoded:
            _write_code(_OVERWRITE_REFUSED)
            return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
