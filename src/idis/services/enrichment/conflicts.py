"""Narrow enrichment conflict checks (Slice86, decision D-G).

Compares the structured identifiers a request asked for against the identifiers a provider
reports having used (``EnrichmentProvenance.identifiers_used``), field by shared field name,
case- and whitespace-insensitively. A mismatch yields a SAFE flag carrying only a fixed code
and the identifier field name — never the compared values, provider payload, or deal data.
Conflicts are observational (recorded in the enrichment ledger), never fatal.
"""

from __future__ import annotations

from collections.abc import Mapping

IDENTIFIER_MISMATCH = "identifier_mismatch"


def _normalize(value: str) -> str:
    return value.strip().casefold()


def identifier_conflicts(
    query_identifiers: Mapping[str, str | None],
    used_identifiers: Mapping[str, str],
) -> list[dict[str, str]]:
    """Return safe mismatch flags for identifier fields present on BOTH sides.

    Args:
        query_identifiers: Identifier fields the request asked for (None values skipped).
        used_identifiers: Identifier fields the provider reports having used.

    Returns:
        One ``{"code": "identifier_mismatch", "field": <name>}`` per shared field whose
        normalized values differ, in deterministic field order. Values are never included.
    """
    conflicts: list[dict[str, str]] = []
    for field in sorted(set(query_identifiers) & set(used_identifiers)):
        requested = query_identifiers[field]
        used = used_identifiers[field]
        if requested is None or used is None:
            continue
        if _normalize(str(requested)) != _normalize(str(used)):
            conflicts.append({"code": IDENTIFIER_MISMATCH, "field": field})
    return conflicts
