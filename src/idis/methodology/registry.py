"""Registry loading helpers for methodology data."""

from __future__ import annotations

import json
from pathlib import Path

from idis.methodology.models import MethodologyRegistry


def load_registry_from_json_file(path: Path) -> MethodologyRegistry:
    """Load and validate a MethodologyRegistry from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return MethodologyRegistry.model_validate(data)
