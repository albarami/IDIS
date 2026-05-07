"""Tests for the structured Commercial DD methodology template."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.models import MethodologyRegistry, MethodologyType
from idis.methodology.registry import load_registry_from_json_file

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "idis"
    / "methodology"
    / "templates"
    / "commercial_dd_v1.json"
)

REQUIRED_SECTIONS = {
    "Market",
    "Customers",
    "Sales and GTM",
    "Competition",
    "Product",
    "Revenue and Unit Economics",
    "Management",
    "Commercial Risks",
    "Business Plan",
}


def test_commercial_template_loads_successfully() -> None:
    """CDD methodology is structured registry data, not prompt-only text."""
    registry = load_registry_from_json_file(TEMPLATE_PATH)

    assert isinstance(registry, MethodologyRegistry)
    assert registry.methodology_id == "commercial_dd"
    assert registry.methodology_type == MethodologyType.COMMERCIAL_DD
    assert registry.current_version.methodology_version_id == "commercial_dd:v1"


def test_commercial_template_required_sections_exist() -> None:
    """Template covers the core commercial due diligence sections."""
    registry = load_registry_from_json_file(TEMPLATE_PATH)
    sections = {question.section for question in registry.current_version.questions}

    assert REQUIRED_SECTIONS.issubset(sections)


def test_every_commercial_question_has_evidence_agents_and_report_mapping() -> None:
    """Every CDD question is actionable by future extraction/agent/report layers."""
    registry = load_registry_from_json_file(TEMPLATE_PATH)

    for question in registry.current_version.questions:
        assert question.required_evidence
        assert question.target_document_categories
        assert question.assigned_agents
        assert question.report_mapping.report_section
        assert question.validation_requirements


def test_template_validates_through_same_registry_schema() -> None:
    """Commercial DD template uses the exact same MethodologyRegistry schema."""
    registry = load_registry_from_json_file(TEMPLATE_PATH)
    round_tripped = MethodologyRegistry.model_validate_json(
        registry.to_deterministic_json()
    )

    assert round_tripped.registry_hash == registry.registry_hash
    assert all(
        question.methodology_type == MethodologyType.COMMERCIAL_DD
        for question in round_tripped.current_version.questions
    )
