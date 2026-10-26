"""Exact-set guards shared by durable methodology canonical materializers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

CANONICAL_MATERIALIZATION_INCOMPLETE = "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE"


class CanonicalMethodologyMaterializationError(ValueError):
    """Stable fail-closed error for incomplete or ambiguous canonical lineage."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = CANONICAL_MATERIALIZATION_INCOMPLETE


class DurableCanonicalMethodologyMaterializationService:
    """Coordinate fenced canonical writes and reload only durable snapshots."""

    def __init__(self, *, boundary: Any) -> None:
        if boundary is None:
            raise CanonicalMethodologyMaterializationError(
                "canonical materialization requires a durable boundary"
            )
        self._boundary = boundary

    def materialize_claims(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        **values: Any,
    ) -> tuple[Any, tuple[Any, ...], Any]:
        scope = _scope(tenant_id, deal_id, run_id, run_step_id)
        prepared = self._boundary.prepare_claim_materialization(**scope, **values)
        try:
            run_result, claims, staged_write = prepared
        except (TypeError, ValueError) as exc:
            raise CanonicalMethodologyMaterializationError(
                "claim materialization did not stage its fenced write"
            ) from exc
        return run_result, tuple(claims), staged_write

    def materialize_evidence(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        **values: Any,
    ) -> tuple[Any, tuple[Any, ...], Any]:
        scope = _scope(tenant_id, deal_id, run_id, run_step_id)
        prepared = self._boundary.prepare_evidence_materialization(**scope, **values)
        try:
            run_result, evidence_items, staged_write = prepared
        except (TypeError, ValueError) as exc:
            raise CanonicalMethodologyMaterializationError(
                "evidence materialization did not stage its fenced write"
            ) from exc
        return run_result, tuple(evidence_items), staged_write

    def materialize_sanads(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        **values: Any,
    ) -> tuple[
        Any,
        tuple[Any, ...],
        tuple[Any, ...],
        tuple[Any, ...],
        tuple[Any, ...],
        Any,
    ]:
        scope = _scope(tenant_id, deal_id, run_id, run_step_id)
        prepared = self._boundary.prepare_sanad_materialization(**scope, **values)
        try:
            run_result, sanads, links, grades, defects, staged_write = prepared
        except (TypeError, ValueError) as exc:
            raise CanonicalMethodologyMaterializationError(
                "Sanad materialization did not stage its fenced write"
            ) from exc
        return (
            run_result,
            tuple(sanads),
            tuple(links),
            tuple(grades),
            tuple(defects),
            staged_write,
        )

    def rehydrate_claims(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        result_summary: Mapping[str, Any] | None = None,
    ) -> tuple[Any, ...]:
        _ = result_summary
        snapshot = self._snapshot(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
        _validate_stage_set(snapshot, "claim_link_output_ids")
        return tuple(snapshot.get("claims", ()))

    def rehydrate_evidence(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        result_summary: Mapping[str, Any] | None = None,
    ) -> tuple[Any, ...]:
        _ = result_summary
        snapshot = self._snapshot(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
        _validate_stage_set(snapshot, "evidence_link_output_ids")
        return tuple(snapshot.get("evidence_items", ()))

    def rehydrate_sanads(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        result_summary: Mapping[str, Any] | None = None,
    ) -> tuple[
        tuple[Any, ...],
        tuple[Any, ...],
        tuple[Any, ...],
        tuple[Any, ...],
    ]:
        _ = result_summary
        snapshot = self._snapshot(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
        validate_canonical_materialization_completion(
            factual_output_ids=tuple(snapshot.get("factual_output_ids", ())),
            persisted_factual_output_ids=tuple(snapshot.get("persisted_factual_output_ids", ())),
            claim_link_output_ids=tuple(snapshot.get("claim_link_output_ids", ())),
            evidence_link_output_ids=tuple(snapshot.get("evidence_link_output_ids", ())),
            sanad_link_output_ids=tuple(snapshot.get("sanad_link_output_ids", ())),
            accepted_source_locator_pairs_by_output=dict(
                snapshot.get("accepted_source_locator_pairs_by_output", {})
            ),
            evidence_link_source_locator_pairs_by_output=dict(
                snapshot.get("evidence_link_source_locator_pairs_by_output", {})
            ),
            expected_defect_ids_by_output=dict(snapshot.get("expected_defect_ids_by_output", {})),
            defect_link_pairs=tuple(snapshot.get("defect_link_pairs", ())),
            rejected_count=int(snapshot.get("rejected_count", 0)),
        )
        return _sanad_values(snapshot)

    def _snapshot(self, *, tenant_id: str, deal_id: str, run_id: str) -> Mapping[str, Any]:
        snapshot = self._boundary.load_canonical_materialization_snapshot(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
        if not isinstance(snapshot, Mapping):
            raise CanonicalMethodologyMaterializationError(
                "durable canonical snapshot is malformed"
            )
        return snapshot


def empty_links_for_evidence_not_found(
    response: Any,
) -> tuple[
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[Any, ...],
]:
    """Return the only valid canonical-link set for a governed evidence gap."""
    if str(getattr(response, "outcome", "")) != "EVIDENCE_NOT_FOUND":
        raise CanonicalMethodologyMaterializationError(
            "canonical empty links are valid only for EVIDENCE_NOT_FOUND"
        )
    if tuple(getattr(response, "factual_outputs", ())):
        raise CanonicalMethodologyMaterializationError(
            "EVIDENCE_NOT_FOUND cannot carry factual outputs"
        )
    return (), (), (), (), ()


def merge_main_evidence_claim_ids(
    *,
    methodology_claim_ids: Iterable[str],
    generic_new_claim_ids: Iterable[str],
    reused_claim_ids: Iterable[str],
) -> tuple[str, ...]:
    """Build the one deterministic claim set consumed by downstream steps."""
    merged = {
        _required_identifier(value)
        for values in (
            methodology_claim_ids,
            generic_new_claim_ids,
            reused_claim_ids,
        )
        for value in values
    }
    return tuple(sorted(merged))


def validate_canonical_materialization_completion(
    *,
    factual_output_ids: Sequence[str],
    persisted_factual_output_ids: Sequence[str],
    claim_link_output_ids: Sequence[str],
    evidence_link_output_ids: Sequence[str],
    sanad_link_output_ids: Sequence[str],
    accepted_source_locator_pairs_by_output: Mapping[str, Sequence[tuple[str, str]]],
    evidence_link_source_locator_pairs_by_output: Mapping[str, Sequence[tuple[str, str]]],
    expected_defect_ids_by_output: Mapping[str, Sequence[str]],
    defect_link_pairs: Sequence[tuple[str, str]],
    rejected_count: int,
) -> tuple[str, ...]:
    """Prove exact output/link/locator/defect equality before completing Step 10."""
    if rejected_count != 0:
        raise CanonicalMethodologyMaterializationError(
            "rejected canonical output prevents completion"
        )
    expected = _unique_identifier_set(factual_output_ids, "factual outputs")
    if not expected:
        raise CanonicalMethodologyMaterializationError(
            "factual canonical materialization requires an output"
        )
    for label, values in (
        ("persisted factual outputs", persisted_factual_output_ids),
        ("claim links", claim_link_output_ids),
        ("EvidenceItem links", evidence_link_output_ids),
        ("Sanad links", sanad_link_output_ids),
    ):
        if _unique_identifier_set(values, label) != expected:
            raise CanonicalMethodologyMaterializationError(
                f"{label} do not exactly cover factual outputs"
            )

    if set(accepted_source_locator_pairs_by_output) != expected:
        raise CanonicalMethodologyMaterializationError(
            "accepted source/locator mapping does not exactly cover factual outputs"
        )
    if set(evidence_link_source_locator_pairs_by_output) != expected:
        raise CanonicalMethodologyMaterializationError(
            "EvidenceItem source/locator mapping does not exactly cover factual outputs"
        )
    for output_id in expected:
        accepted = _unique_source_locator_pairs(
            accepted_source_locator_pairs_by_output[output_id],
            "accepted source/locator pairs",
        )
        linked = _unique_source_locator_pairs(
            evidence_link_source_locator_pairs_by_output[output_id],
            "EvidenceItem source/locator pairs",
        )
        if not accepted or linked != accepted:
            raise CanonicalMethodologyMaterializationError(
                "EvidenceItem source/locator pairs differ from the accepted source pairs"
            )

    if set(expected_defect_ids_by_output) != expected:
        raise CanonicalMethodologyMaterializationError(
            "expected defect mapping does not exactly cover factual outputs"
        )
    expected_defects_by_output: dict[str, set[str]] = {}
    expected_defect_owners: dict[str, str] = {}
    for output_id in expected:
        expected_defects = _unique_identifier_set(
            expected_defect_ids_by_output[output_id], "expected defects"
        )
        for defect_id in expected_defects:
            if defect_id in expected_defect_owners:
                raise CanonicalMethodologyMaterializationError(
                    "one governed defect cannot belong to multiple factual outputs"
                )
            expected_defect_owners[defect_id] = output_id
        expected_defects_by_output[output_id] = expected_defects
    actual_defects: dict[str, set[str]] = {output_id: set() for output_id in expected}
    seen_pairs: set[tuple[str, str]] = set()
    seen_defect_ids: set[str] = set()
    for output_id, defect_id in defect_link_pairs:
        pair = (_required_identifier(output_id), _required_identifier(defect_id))
        if pair in seen_pairs or pair[1] in seen_defect_ids or pair[0] not in expected:
            raise CanonicalMethodologyMaterializationError(
                "defect links contain a duplicate or unlisted factual output"
            )
        seen_pairs.add(pair)
        seen_defect_ids.add(pair[1])
        actual_defects[pair[0]].add(pair[1])
    for output_id in expected:
        if actual_defects[output_id] != expected_defects_by_output[output_id]:
            raise CanonicalMethodologyMaterializationError(
                "defect links do not exactly equal governed defects"
            )

    return tuple(sorted(expected))


def _unique_identifier_set(values: Iterable[str], label: str) -> set[str]:
    cleaned = [_required_identifier(value) for value in values]
    if len(cleaned) != len(set(cleaned)):
        raise CanonicalMethodologyMaterializationError(f"{label} contain duplicates")
    return set(cleaned)


def _unique_source_locator_pairs(
    values: Iterable[tuple[str, str]], label: str
) -> set[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise CanonicalMethodologyMaterializationError(f"{label} are malformed")
        span_id = _required_identifier(value[0])
        locator_sha256 = _required_identifier(value[1])
        if len(locator_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in locator_sha256
        ):
            raise CanonicalMethodologyMaterializationError(
                f"{label} contain a malformed locator hash"
            )
        cleaned.append((span_id, locator_sha256))
    if len(cleaned) != len(set(cleaned)):
        raise CanonicalMethodologyMaterializationError(f"{label} contain duplicates")
    span_ids = [span_id for span_id, _locator_sha256 in cleaned]
    if len(span_ids) != len(set(span_ids)):
        raise CanonicalMethodologyMaterializationError(
            f"{label} bind one span to multiple locators"
        )
    return set(cleaned)


def _scope(
    tenant_id: str,
    deal_id: str,
    run_id: str,
    run_step_id: str,
) -> dict[str, str]:
    values = {
        "tenant_id": str(tenant_id).strip(),
        "deal_id": str(deal_id).strip(),
        "run_id": str(run_id).strip(),
        "run_step_id": str(run_step_id).strip(),
    }
    if any(not value for value in values.values()):
        raise CanonicalMethodologyMaterializationError(
            "canonical materialization scope is incomplete"
        )
    return values


def _validate_stage_set(snapshot: Mapping[str, Any], link_field: str) -> None:
    expected = _unique_identifier_set(snapshot.get("factual_output_ids", ()), "factual outputs")
    persisted = _unique_identifier_set(
        snapshot.get("persisted_factual_output_ids", ()), "persisted factual outputs"
    )
    linked = _unique_identifier_set(snapshot.get(link_field, ()), link_field)
    if expected != persisted or expected != linked:
        raise CanonicalMethodologyMaterializationError(
            f"{link_field} do not exactly cover durable factual outputs"
        )


def _sanad_values(
    snapshot: Mapping[str, Any],
) -> tuple[
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[Any, ...],
]:
    return (
        tuple(snapshot.get("sanads", ())),
        tuple(snapshot.get("sanad_links", ())),
        tuple(snapshot.get("sanad_grades", ())),
        tuple(snapshot.get("sanad_defects", ())),
    )


def _required_identifier(value: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise CanonicalMethodologyMaterializationError("canonical identifier must not be blank")
    return cleaned
