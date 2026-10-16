"""Validation shared by production writers for optional Task-7 native roots."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping

_COVERAGE_RECORD_ID_RE = re.compile(r"mc_[0-9a-f]{24}\Z", re.ASCII)
_INVALID = "TASK7_NATIVE_ROOT_BINDING_INVALID"


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(_INVALID)
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(_INVALID) from exc
    if str(parsed) != value:
        raise ValueError(_INVALID)
    return value


def validate_run_output_object_ids(
    *,
    stable_native_ids: Iterable[str],
    run_output_object_ids: Mapping[str, str] | None,
    native_ids_are_uuids: bool,
) -> dict[str, str] | None:
    """Return an exact canonical binding map, or preserve the legacy NULL path."""

    if run_output_object_ids is None:
        return None
    if not isinstance(run_output_object_ids, Mapping):
        raise ValueError(_INVALID)

    native_ids = tuple(stable_native_ids)
    if not native_ids or len(native_ids) != len(set(native_ids)):
        raise ValueError(_INVALID)
    if native_ids_are_uuids:
        for native_id in native_ids:
            _canonical_uuid(native_id)
    elif any(
        not isinstance(native_id, str) or _COVERAGE_RECORD_ID_RE.fullmatch(native_id) is None
        for native_id in native_ids
    ):
        raise ValueError(_INVALID)

    if any(not isinstance(key, str) for key in run_output_object_ids):
        raise ValueError(_INVALID)
    if set(run_output_object_ids) != set(native_ids):
        raise ValueError(_INVALID)

    validated = {
        native_id: _canonical_uuid(run_output_object_ids[native_id]) for native_id in native_ids
    }
    if len(validated) != len(set(validated.values())):
        raise ValueError(_INVALID)
    return validated


def validate_durable_run_output_object_ids(
    *,
    rows: Iterable[object],
    stable_native_id_column: str,
    expected_run_output_object_ids: Mapping[str, str],
) -> None:
    """Require an exact durable replay of an already-validated root binding map."""

    observed: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(_INVALID)
        try:
            native_id = str(row[stable_native_id_column])
            raw_root_id = row["run_output_object_id"]
        except KeyError as exc:
            raise ValueError(_INVALID) from exc
        if raw_root_id is None or native_id in observed:
            raise ValueError(_INVALID)
        observed[native_id] = _canonical_uuid(str(raw_root_id))
    if observed != dict(expected_run_output_object_ids):
        raise ValueError(_INVALID)


__all__ = [
    "validate_durable_run_output_object_ids",
    "validate_run_output_object_ids",
]
