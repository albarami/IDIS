"""RED proof that usage extractor consumers equal their literal route rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
)


def _artifact() -> dict[str, Any]:
    assert _ARTIFACT_PATH.is_file(), f"required v7 authority is absent: {_ARTIFACT_PATH}"
    value = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_every_accepted_consumer_is_the_same_ordinal_route_projection() -> None:
    """AcceptedConsumerTupleV2 cannot contradict UsageRouteV2."""

    artifact = _artifact()
    routes = {row[0]: (*row[:5], *row[6:]) for row in artifact["usage_route_registry"]["rows"]}
    mismatches: list[tuple[str, int]] = []

    for extractor in artifact["usage_extractor_registry"]["rows"]:
        usage_kind = extractor["usage_kind"]
        for accepted_consumer in extractor["accepted_consumers"]:
            ordinal = accepted_consumer[0]
            if (
                tuple(accepted_consumer) != routes.get(ordinal)
                or artifact["usage_route_registry"]["rows"][ordinal][5] != usage_kind
            ):
                mismatches.append((usage_kind, ordinal))

    assert not mismatches, (
        f"accepted-consumer rows contradict their same-ordinal route authority: {mismatches}"
    )
