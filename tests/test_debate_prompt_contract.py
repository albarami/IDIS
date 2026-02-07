"""CI guard for Layer-1 debate prompt contracts.

Ensures every debate prompt file contains the required tokens for
Muhasabah validation and claim-ref traceability. If any prompt file
is missing or lacks required tokens, the test fails immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DEBATE_PROMPTS: list[tuple[str, str]] = [
    ("advocate", "prompts/debate_advocate/1.0.0/prompt.md"),
    ("sanad_breaker", "prompts/debate_sanad_breaker/1.0.0/prompt.md"),
    ("contradiction_finder", "prompts/debate_contradiction_finder/1.0.0/prompt.md"),
    ("risk_officer", "prompts/debate_risk_officer/1.0.0/prompt.md"),
    ("arbiter", "prompts/debate_arbiter/1.0.0/prompt.md"),
]

REQUIRED_TOKENS: list[str] = [
    "muhasabah",
    "supported_claim_ids",
    "confidence",
    "uncertainties",
]

SCHEMA_MARKERS: list[str] = [
    "```json",
    "Output Schema",
]


@pytest.mark.parametrize(
    ("role", "rel_path"),
    DEBATE_PROMPTS,
    ids=[p[0] for p in DEBATE_PROMPTS],
)
class TestDebatePromptContract:
    """Validates that each debate prompt contains required contract tokens."""

    def test_prompt_file_exists(self, role: str, rel_path: str) -> None:
        """Prompt file must exist on disk."""
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            pytest.fail(f"Missing debate prompt file: {rel_path}")

    def test_required_tokens_present(self, role: str, rel_path: str) -> None:
        """All required tokens must appear in the prompt text."""
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            pytest.fail(f"Missing debate prompt file: {rel_path}")

        text = full_path.read_text(encoding="utf-8")
        text_lower = text.lower()

        missing = [t for t in REQUIRED_TOKENS if t.lower() not in text_lower]
        if missing:
            pytest.fail(
                f"Prompt '{role}' ({rel_path}) missing required token(s): {', '.join(missing)}"
            )

    def test_schema_marker_present(self, role: str, rel_path: str) -> None:
        """At least one schema marker must appear in the prompt text."""
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            pytest.fail(f"Missing debate prompt file: {rel_path}")

        text = full_path.read_text(encoding="utf-8")

        has_marker = any(marker in text for marker in SCHEMA_MARKERS)
        if not has_marker:
            pytest.fail(
                f"Prompt '{role}' ({rel_path}) missing schema marker. "
                f"Expected one of: {SCHEMA_MARKERS}"
            )
