"""Dependency-neutral, code-owned pricing for Task-6 methodology extraction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION = "anthropic-standard-price-catalog-2026-07-20-v1"
ANTHROPIC_STANDARD_PRICE_MODE = "standard_non_batch_non_cached_under_200k"
ANTHROPIC_METHODOLOGY_MODEL = "claude-sonnet-4-20250514"

_SONNET_4_ROW = MappingProxyType(
    {
        "input_usd_per_million_tokens": "3.00000000",
        "output_usd_per_million_tokens": "15.00000000",
        "context_window_limit": 200_000,
    }
)
ANTHROPIC_STANDARD_PRICE_CATALOG: Mapping[str, Any] = MappingProxyType(
    {
        "version": ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION,
        "mode": ANTHROPIC_STANDARD_PRICE_MODE,
        "models": MappingProxyType({ANTHROPIC_METHODOLOGY_MODEL: _SONNET_4_ROW}),
    }
)


class MethodologyPriceCatalogError(ValueError):
    """Raised when a caller cannot prove the exact admitted price row."""


@dataclass(frozen=True, slots=True)
class AnthropicStandardPrice:
    catalog_version: str
    mode: str
    model_id: str
    input_usd_per_million_tokens: str
    output_usd_per_million_tokens: str
    context_window_limit: int
    row_sha256: str


def _fixed_positive_decimal(value: object) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MethodologyPriceCatalogError("price is not a decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MethodologyPriceCatalogError("price must be finite and positive")
    return f"{parsed:.8f}"


def resolve_anthropic_standard_price(
    *, model_id: str, catalog: Mapping[str, Any] = ANTHROPIC_STANDARD_PRICE_CATALOG
) -> AnthropicStandardPrice:
    """Resolve and content-bind the sole admitted standard Anthropic price row."""

    if (
        catalog.get("version") != ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION
        or catalog.get("mode") != ANTHROPIC_STANDARD_PRICE_MODE
    ):
        raise MethodologyPriceCatalogError("catalog identity or admitted mode drifted")
    models = catalog.get("models")
    row = models.get(model_id) if isinstance(models, Mapping) else None
    if not isinstance(row, Mapping):
        raise MethodologyPriceCatalogError("configured model is absent from the price catalog")
    try:
        input_price = _fixed_positive_decimal(row["input_usd_per_million_tokens"])
        output_price = _fixed_positive_decimal(row["output_usd_per_million_tokens"])
        context_window_limit = int(row["context_window_limit"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MethodologyPriceCatalogError("configured model price row is malformed") from exc
    if context_window_limit <= 0:
        raise MethodologyPriceCatalogError("context window limit must be positive")
    identity = {
        "catalog_version": ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION,
        "mode": ANTHROPIC_STANDARD_PRICE_MODE,
        "model_id": model_id,
        "input_usd_per_million_tokens": input_price,
        "output_usd_per_million_tokens": output_price,
        "context_window_limit": context_window_limit,
    }
    row_sha256 = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return AnthropicStandardPrice(
        catalog_version=ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION,
        mode=ANTHROPIC_STANDARD_PRICE_MODE,
        model_id=model_id,
        input_usd_per_million_tokens=input_price,
        output_usd_per_million_tokens=output_price,
        context_window_limit=context_window_limit,
        row_sha256=row_sha256,
    )


def compute_anthropic_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_usd_per_million_tokens: str,
    output_usd_per_million_tokens: str,
) -> str:
    """Return the exact observed/reserved cost rounded upward to eight decimals."""

    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    input_price = Decimal(input_usd_per_million_tokens)
    output_price = Decimal(output_usd_per_million_tokens)
    exact = (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price) / Decimal(
        1_000_000
    )
    return f"{exact.quantize(Decimal('0.00000001'), rounding=ROUND_CEILING):.8f}"
