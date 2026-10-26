"""Finding constructor. Messages carry codes, scopes, counts and ids only."""

from __future__ import annotations

from idis.methodology.obligations.models_records import Finding
from idis.methodology.obligations.vocab import FindingSeverity


def span_token(field: str, start: int, end: int) -> str:
    """Return the ``field:start:end`` identifier for a half-open span."""
    return f"{field}:{start}:{end}"


def make_finding(
    code: str,
    *,
    severity: FindingSeverity = FindingSeverity.ERROR,
    scope: str = "global",
    ids: tuple[str, ...] = (),
    count: int | None = None,
) -> Finding:
    """Build one finding whose message is a fixed template."""
    ordered = tuple(sorted(set(ids)))
    total = len(ordered) if count is None else count
    message = f"{code} scope={scope} count={total} ids={','.join(ordered)}"
    return Finding(
        code=code,
        severity=severity,
        scope=scope,
        ids=ordered,
        count=total,
        message=message,
    )
