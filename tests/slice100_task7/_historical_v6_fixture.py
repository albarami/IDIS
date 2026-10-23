"""Derive void-v6 fixture evidence transiently without checking it in."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_generator(path: Path) -> ModuleType:
    name = "_task7_historical_v6_fixture_generator"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"historical v6 generator is absent: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def historical_v6_fixture_bytes(repo_root: Path) -> bytes:
    """Return the canonical historical fixture solely as transient test bytes."""

    artifact = repo_root / "contracts" / "full_strict_v6_reviewed_preimage_v1.json"
    generator_path = repo_root / "scripts" / "generate_task7_v6_contract_fixture.py"
    generator = _load_generator(generator_path)
    derived = generator.derive_contract_bytes(artifact.read_bytes(), validate=True)
    return (
        json.dumps(
            derived,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def historical_v6_fixture(repo_root: Path) -> dict[str, Any]:
    value = json.loads(historical_v6_fixture_bytes(repo_root))
    assert isinstance(value, dict)
    return value
