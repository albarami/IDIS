"""Candidate Anthropic price rows, kept apart from the v1 methodology catalog.

The rates are planning figures. They are not evidence of a negotiated rate,
an account entitlement, or an official price schedule. Cache, long-context,
priority, and residency terms are absent until an owner supplies them.
The model ids are the capability table's constants.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final

from idis.providers.anthropic_capabilities import CANDIDATE_OPUS_ID, CANDIDATE_SONNET_ID

ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION: Final = "anthropic-candidate-price-catalog-2026-10-20-v2"
ANTHROPIC_CANDIDATE_PRICE_MODE: Final = "standard_non_batch_non_cached_under_200k"

LLM_CANDIDATE_PRICE_UNAVAILABLE: Final = "LLM_CANDIDATE_PRICE_UNAVAILABLE"
LLM_CANDIDATE_PRICE_MALFORMED: Final = "LLM_CANDIDATE_PRICE_MALFORMED"
LLM_CANDIDATE_PRICE_CATALOG_INVALID: Final = "LLM_CANDIDATE_PRICE_CATALOG_INVALID"

_CONTEXT_WINDOW_LIMIT: Final = 200_000
_RATE_BASIS_PLANNING: Final = "PLANNING"
_RATE_BASIS_OFFICIAL: Final = "OFFICIAL"
_WINDOW_BASIS_PRICE_TIER_BOUND: Final = "PRICE_TIER_BOUND"
_ADMITTED_RATE_BASES: Final = frozenset({_RATE_BASIS_PLANNING, _RATE_BASIS_OFFICIAL})
_ADMITTED_WINDOW_BASES: Final = frozenset({_WINDOW_BASIS_PRICE_TIER_BOUND})
_ROW_KEYS: Final = frozenset(
    {
        "input_usd_per_million_tokens",
        "output_usd_per_million_tokens",
        "context_window_limit",
        "rate_basis",
        "window_basis",
    }
)
_SONNET_INPUT_USD_PER_MILLION: Final = "2.00"
_SONNET_OUTPUT_USD_PER_MILLION: Final = "10.00"
_OPUS_INPUT_USD_PER_MILLION: Final = "4.00"
_OPUS_OUTPUT_USD_PER_MILLION: Final = "20.00"


def _row(
    *,
    input_usd_per_million_tokens: str,
    output_usd_per_million_tokens: str,
) -> MappingProxyType[str, Any]:
    return MappingProxyType(
        {
            "input_usd_per_million_tokens": input_usd_per_million_tokens,
            "output_usd_per_million_tokens": output_usd_per_million_tokens,
            "context_window_limit": _CONTEXT_WINDOW_LIMIT,
            "rate_basis": _RATE_BASIS_PLANNING,
            "window_basis": _WINDOW_BASIS_PRICE_TIER_BOUND,
        }
    )


ANTHROPIC_CANDIDATE_PRICE_CATALOG: Mapping[str, Any] = MappingProxyType(
    {
        "version": ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION,
        "mode": ANTHROPIC_CANDIDATE_PRICE_MODE,
        "models": MappingProxyType(
            {
                CANDIDATE_SONNET_ID: _row(
                    input_usd_per_million_tokens=_SONNET_INPUT_USD_PER_MILLION,
                    output_usd_per_million_tokens=_SONNET_OUTPUT_USD_PER_MILLION,
                ),
                CANDIDATE_OPUS_ID: _row(
                    input_usd_per_million_tokens=_OPUS_INPUT_USD_PER_MILLION,
                    output_usd_per_million_tokens=_OPUS_OUTPUT_USD_PER_MILLION,
                ),
            }
        ),
    }
)


class AnthropicCandidatePriceError(ValueError):
    """A candidate price row cannot be resolved. The message is the code."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AnthropicCandidatePrice:
    """One resolved candidate row and the hash of its identity."""

    catalog_version: str
    mode: str
    model_id: str
    input_usd_per_million_tokens: str
    output_usd_per_million_tokens: str
    context_window_limit: int
    rate_basis: str
    window_basis: str
    row_sha256: str


def resolve_candidate_price(
    model_id: str,
    catalog: Mapping[str, Any] = ANTHROPIC_CANDIDATE_PRICE_CATALOG,
) -> AnthropicCandidatePrice:
    """Resolve one candidate row and bind its identity hash.

    Args:
        model_id: Exact provider model id.
        catalog: Candidate catalog. The default is the planning catalog.

    Returns:
        The frozen row, including ``rate_basis`` and ``window_basis``.

    Raises:
        AnthropicCandidatePriceError: The catalog, the id, or the row is not
            usable. The message is the stable code.
    """
    if (
        catalog.get("version") != ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION
        or catalog.get("mode") != ANTHROPIC_CANDIDATE_PRICE_MODE
    ):
        raise AnthropicCandidatePriceError(LLM_CANDIDATE_PRICE_CATALOG_INVALID)
    models = catalog.get("models")
    if not isinstance(models, Mapping):
        raise AnthropicCandidatePriceError(LLM_CANDIDATE_PRICE_CATALOG_INVALID)
    row = models.get(model_id)
    if not isinstance(row, Mapping):
        raise AnthropicCandidatePriceError(LLM_CANDIDATE_PRICE_UNAVAILABLE)
    return _resolved_row(model_id, row)


def _resolved_row(model_id: str, row: Mapping[str, Any]) -> AnthropicCandidatePrice:
    if set(row) != _ROW_KEYS:
        raise AnthropicCandidatePriceError(LLM_CANDIDATE_PRICE_MALFORMED)
    try:
        input_price = _fixed_positive_decimal(row["input_usd_per_million_tokens"])
        output_price = _fixed_positive_decimal(row["output_usd_per_million_tokens"])
        context_window_limit = _positive_window(row["context_window_limit"])
        rate_basis = _basis(row["rate_basis"], _ADMITTED_RATE_BASES)
        window_basis = _basis(row["window_basis"], _ADMITTED_WINDOW_BASES)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise AnthropicCandidatePriceError(LLM_CANDIDATE_PRICE_MALFORMED) from exc
    row_sha256 = _identity_sha256(
        catalog_version=ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION,
        mode=ANTHROPIC_CANDIDATE_PRICE_MODE,
        model_id=model_id,
        input_usd_per_million_tokens=input_price,
        output_usd_per_million_tokens=output_price,
        context_window_limit=context_window_limit,
        rate_basis=rate_basis,
        window_basis=window_basis,
    )
    return AnthropicCandidatePrice(
        catalog_version=ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION,
        mode=ANTHROPIC_CANDIDATE_PRICE_MODE,
        model_id=model_id,
        input_usd_per_million_tokens=input_price,
        output_usd_per_million_tokens=output_price,
        context_window_limit=context_window_limit,
        rate_basis=rate_basis,
        window_basis=window_basis,
        row_sha256=row_sha256,
    )


def candidate_price_row_sha256(row: AnthropicCandidatePrice) -> str:
    """Return the SHA-256 of the row identity, excluding ``row_sha256``.

    Args:
        row: A resolved candidate price. A copied row is hashed as it stands.

    Returns:
        The hex digest the resolver stores when those identity fields match.
    """
    return _identity_sha256(
        catalog_version=row.catalog_version,
        mode=row.mode,
        model_id=row.model_id,
        input_usd_per_million_tokens=row.input_usd_per_million_tokens,
        output_usd_per_million_tokens=row.output_usd_per_million_tokens,
        context_window_limit=row.context_window_limit,
        rate_basis=row.rate_basis,
        window_basis=row.window_basis,
    )


def _identity_sha256(
    *,
    catalog_version: str,
    mode: str,
    model_id: str,
    input_usd_per_million_tokens: str,
    output_usd_per_million_tokens: str,
    context_window_limit: int,
    rate_basis: str,
    window_basis: str,
) -> str:
    identity = {
        "catalog_version": catalog_version,
        "mode": mode,
        "model_id": model_id,
        "input_usd_per_million_tokens": input_usd_per_million_tokens,
        "output_usd_per_million_tokens": output_usd_per_million_tokens,
        "context_window_limit": context_window_limit,
        "rate_basis": rate_basis,
        "window_basis": window_basis,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fixed_positive_decimal(value: object) -> str:
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("price must be finite and positive")
    return f"{parsed:.8f}"


def _positive_window(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("context window limit must be a positive integer")
    return value


def _basis(value: object, admitted: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in admitted:
        raise ValueError("price basis is not admitted on a candidate row")
    return value
