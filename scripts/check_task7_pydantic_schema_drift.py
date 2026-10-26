"""Compare installed Pydantic schema generation with frozen Task-7 snapshots."""

from __future__ import annotations

import importlib
import json
import struct
from pathlib import Path
from typing import Any

import pydantic


class PydanticSchemaDriftError(ValueError):
    """A schema generator differs from the reviewed full-strict-v4 bytes."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"TASK7_PYDANTIC_SCHEMA_DRIFT: {detail}")
        self.code = "TASK7_PYDANTIC_SCHEMA_DRIFT"


def _normalize_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_schema(item)
            for key, item in sorted(value.items())
            if key not in {"title", "description"}
        }
    if isinstance(value, list):
        return [_normalize_schema(item) for item in value]
    if isinstance(value, float):
        if not (-float("inf") < value < float("inf")):
            raise PydanticSchemaDriftError("non-finite schema scalar")
        normalized = 0.0 if value == 0.0 else value
        return {"float8_be": struct.pack(">d", normalized).hex()}
    return value


def _snapshot(import_targets: list[str]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for target in import_targets:
        try:
            module_name, class_name = target.split(":", 1)
            model = getattr(importlib.import_module(module_name), class_name)
            schema = model.model_json_schema()
        except (AttributeError, ImportError, ValueError) as exc:
            raise PydanticSchemaDriftError(f"cannot regenerate {target}") from exc
        rows.append([target, _normalize_schema(schema)])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "targets": rows,
    }


def check_pydantic_schema_snapshots(artifact_path: Path) -> dict[str, Any]:
    if pydantic.__version__ != "2.12.5":
        raise PydanticSchemaDriftError(
            f"installed Pydantic is {pydantic.__version__}, expected 2.12.5"
        )
    artifact = json.loads(artifact_path.read_text("utf-8"))
    if artifact["metadata"]["pydantic_version"] != "2.12.5":
        raise PydanticSchemaDriftError("artifact Pydantic version is not 2.12.5")

    checked = 0
    for row in artifact["schema_registry"]["rows"]:
        origin = row["origin"]
        if origin["kind"] != "pydantic":
            continue
        if origin["pydantic_version"] != "2.12.5":
            raise PydanticSchemaDriftError(f"{row['schema_id']} origin version mismatch")
        regenerated = _snapshot(origin["import_targets"])
        if regenerated != row["normalized_schema_snapshot"]:
            raise PydanticSchemaDriftError(f"{row['schema_id']} normalized schema bytes changed")
        checked += 1
    if checked == 0:
        raise PydanticSchemaDriftError("artifact contains no Pydantic-backed schemas")
    return {
        "ok": True,
        "pydantic_version": pydantic.__version__,
        "checked_schema_count": checked,
    }
