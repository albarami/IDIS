"""RED-first runtime authority for the frozen producer-selector projection."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from pathlib import Path
from uuid import UUID

import pytest

from idis.pipeline import task7_edges

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
DEAL_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
_VERSION_RE = re.compile(r"full_strict_v(\d+)_reviewed_preimage_v1\.json$")


def _latest_artifact() -> dict[str, object]:
    contracts = Path(__file__).parents[2] / "contracts"
    candidates = [
        (int(match.group(1)), path)
        for path in contracts.glob("full_strict_v*_reviewed_preimage_v1.json")
        if (match := _VERSION_RE.fullmatch(path.name)) is not None
    ]
    assert candidates
    return json.loads(max(candidates)[1].read_text(encoding="utf-8"))


def _freeze(value: object) -> object:
    """Compare the immutable runtime projection to equivalent artifact bytes."""
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    return value


def _artifact_instances() -> tuple[tuple[object, ...], ...]:
    section = _latest_artifact()["producer_selector_registry"]
    assert isinstance(section, dict)
    rows = section["rows"]
    assert isinstance(rows, list)
    return tuple(
        (
            row["rule_ordinal"],
            row["rule_id"],
            row["version"],
            instance["ordinal"],
            _freeze(instance["parameter_tuple"]),
            _freeze(instance["selector_ast"]),
            instance["expected_sha256"],
        )
        for row in rows
        for instance in row["instances"]
    )


def test_embedded_selector_authority_matches_latest_admitted_bytes() -> None:
    actual = tuple(
        (
            spec.rule_ordinal,
            spec.rule_id,
            spec.rule_version,
            spec.selector_ordinal,
            spec.parameter_tuple,
            spec.selector_ast,
            spec.selector_sha256,
        )
        for spec in task7_edges.PRODUCER_SELECTOR_REGISTRY
    )

    assert len(actual) == 235
    assert len({row[0:4] for row in actual}) == 235
    assert actual == _artifact_instances()
    section = _latest_artifact()["producer_selector_registry"]
    assert isinstance(section, dict)
    assert section["expected_sha256"] == task7_edges.PRODUCER_SELECTOR_REGISTRY_SHA256


def test_runtime_selector_module_never_reads_the_artifact() -> None:
    source = inspect.getsource(task7_edges)
    assert "full_strict_v" not in source
    assert "read_text(" not in source
    assert "Path(" not in source
    assert "open(" not in source


def test_selector_plus_route_coordinate_is_unambiguous_for_all_1756_routes() -> None:
    conditions = {
        (spec.rule_id, spec.producer_condition) for spec in task7_edges.PRODUCER_SELECTOR_REGISTRY
    }
    coordinates = tuple(
        (
            route.rule_id,
            route.producer_condition,
            route.consumer_step_name,
            route.consumer_subrole,
            route.usage_kind,
        )
        for route in task7_edges.USAGE_ROUTE_REGISTRY
    )

    assert len(coordinates) == 1_756
    assert len(set(coordinates)) == 1_756
    assert all((rule_id, condition) in conditions for rule_id, condition, *_ in coordinates)


def test_executor_returns_rule_bound_facts_from_joined_authority_rows_only() -> None:
    first = task7_edges.PRODUCER_SELECTOR_REGISTRY[0]
    calls: list[object] = []

    def load(spec: object, **scope: object) -> tuple[dict[str, object], ...]:
        calls.append((spec, scope))
        if spec is not first:
            return ()
        return (
            {
                first.producer_id_column: UUID("44444444-4444-4444-8444-444444444444"),
                first.producer_identity_hash_column: "a" * 64,
                first.producer_value_hash_column: "b" * 64,
            },
        )

    facts = task7_edges.derive_rule_bound_producers(
        task7_edges.ProducerSelectionRequest(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        ),
        row_loader=load,
    )

    assert len(calls) == 235
    assert len(facts) == 1
    assert facts[0].rule_id == first.rule_id
    assert facts[0].producer_condition == first.producer_condition
    assert facts[0].producer.producer_type == first.producer_type


@pytest.mark.parametrize(
    "bad_row",
    (
        {},
        {
            "binding_id": UUID("44444444-4444-4444-8444-444444444444"),
            "selected_identity_sha256": "not-a-hash",
            "binding_sha256": "b" * 64,
        },
    ),
)
def test_selector_executor_rejects_missing_or_malformed_projection(
    bad_row: dict[str, object],
) -> None:
    first = task7_edges.PRODUCER_SELECTOR_REGISTRY[0]

    with pytest.raises(task7_edges.EdgeDerivationError):
        task7_edges.derive_rule_bound_producers(
            task7_edges.ProducerSelectionRequest(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
            ),
            row_loader=lambda spec, **_scope: (bad_row,) if spec is first else (),
        )


def test_selector_payload_corruption_changes_its_section_digest() -> None:
    rows = _artifact_instances()
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    changed = bytearray(raw)
    changed[len(changed) // 2] ^= 1

    assert hashlib.sha256(bytes(changed)).digest() != hashlib.sha256(raw).digest()
