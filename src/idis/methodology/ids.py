"""Stable methodology identifier helpers."""

from __future__ import annotations

import hashlib
import re

from idis.methodology.models import MethodologyType


def normalize_text(value: str) -> str:
    """Normalize methodology text for deterministic IDs and comparison."""
    return re.sub(r"\s+", " ", value.strip().lower())


def generate_methodology_question_id(
    *,
    methodology_type: MethodologyType,
    section: str,
    sheet_or_section: str,
    row_number: int | None,
    line_item: str,
    question_text: str,
) -> str:
    """Generate a stable methodology question ID from source trace fields."""
    seed = "|".join(
        [
            methodology_type.value,
            normalize_text(section),
            normalize_text(sheet_or_section),
            "" if row_number is None else str(row_number),
            normalize_text(line_item),
            normalize_text(question_text),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"mq_{methodology_type.value}_{digest}"
