"""Methodology registry validation helpers."""

from __future__ import annotations

from idis.methodology.models import MethodologyRegistry


def validate_methodology_registry(registry: MethodologyRegistry) -> MethodologyRegistry:
    """Return a validated registry, raising on invalid structure."""
    return MethodologyRegistry.model_validate(registry.model_dump(mode="json"))
