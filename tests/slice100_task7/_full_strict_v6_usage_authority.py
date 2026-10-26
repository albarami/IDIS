"""Independent v6 accepted-consumer projection authority for tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

V5_ACCEPTED_CONSUMER_SHA256 = "25865007d798eda4c040112a983149ba6f233f62e0e1d3ae1937f18280b9e8b5"
V6_ACCEPTED_CONSUMER_SHA256 = "8555cadbd073a939f11c7a5992777eb3446b99b5cafca6105ea691cd272ac964"
V5_ENRICHMENT_EXTRACTOR_SHA256 = "77d6d0a2d9923830ce663bbc238d2e45a98a08b7bfebb2a5896a4de925cbc9bf"
V6_ENRICHMENT_EXTRACTOR_SHA256 = "cf14c89ea95ec619d1565f7127db7e829baa091fd74d69b0ed3a3c9e0dc75c51"
V5_EXTRACTOR_REGISTRY_SHA256 = "108156cce956b7882082684bd396a72616db0e6f77170d9be224126b38a17d4b"
V6_EXTRACTOR_REGISTRY_SHA256 = "e6542bb692bb4819712bc95bfdd23c2f66f7fd21fa15e0b7881057ff81eeeccc"
V5_CONTRACT_SHA256 = "5c290038c4ab409dd08e0cb8f54e9eb34267250fb0fe676816e5dd77ca1d0801"
ROUTE_REGISTRY_SHA256 = "96cbe37825fca73179b8077d7a81fb235aaf057aff405342bac18b7cf365c616"
EDGE_MANIFEST_SHA256 = "bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014"
CORRECTED_ROUTE_ORDINALS = (
    437,
    438,
    452,
    453,
    467,
    468,
    482,
    483,
    497,
    498,
    512,
    513,
    527,
    528,
    542,
    543,
    557,
    558,
    572,
    573,
    587,
    588,
    602,
    603,
    617,
    618,
    632,
    633,
    646,
    647,
    661,
    662,
    676,
    677,
    690,
    691,
    705,
    706,
    720,
    721,
    734,
    735,
    749,
    750,
    764,
    765,
    778,
    779,
    792,
    793,
)


def canonical_bytes(value: object) -> bytes:
    """Render the reviewed canonical JSON form used by Task 7."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_hash(value: object) -> str:
    """Hash one canonical Task-7 preimage."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def accepted_consumer_projection(route: list[Any]) -> list[Any]:
    """Drop only UsageRouteV2's usage_kind field."""

    assert len(route) == 12
    return [*route[:5], *route[6:]]


def accepted_consumers_for_kind(
    routes: Iterable[list[Any]],
    usage_kind: str,
) -> list[list[Any]]:
    """Project same-kind routes in their existing literal order."""

    return [accepted_consumer_projection(route) for route in routes if route[5] == usage_kind]


def expected_v6_reviewed_design(v5_design: list[Any]) -> list[Any]:
    """Return the only reviewed-design delta authorized for v6."""

    result = json.loads(json.dumps(v5_design))
    result[0] = "full-strict-v6-reviewed-design-literal-v1"
    result[1] = ["pipeline_contract_version", "full-strict-v6"]
    result.append(
        [
            "usage_extractor_route_projection_closure",
            [
                ["projection", "same_ordinal_route_drop_usage_kind_only"],
                ["usage_extractor_count", 19],
                ["usage_route_count", 1756],
                ["corrected_enrichment_tuple_count", 50],
            ],
        ]
    )
    return result
