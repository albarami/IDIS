"""Tests for NoFreeFactsValidator - proves fail-closed behavior."""

from __future__ import annotations

from idis.validators import NoFreeFactsValidator


class TestNoFreeFactsFailClosed:
    """Tests proving fail-closed behavior."""

    def test_none_data_fails_closed(self) -> None:
        """Validator rejects None data."""
        validator = NoFreeFactsValidator()
        result = validator.validate(None)

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_fails_closed(self) -> None:
        """Validator rejects non-dict data."""
        validator = NoFreeFactsValidator()
        result = validator.validate("string data")

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"


class TestNoFreeFactsPositive:
    """Positive tests - compliant deliverables pass."""

    def test_deliverable_with_claim_refs_passes(self) -> None:
        """Deliverable with proper claim references passes."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
            "sections": [
                {
                    "type": "FINANCIAL",
                    "claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "content": "Revenue is $10M ARR based on verified sources.",
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_subjective_section_without_refs_passes(self) -> None:
        """Subjective sections can have factual-looking content without refs."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "sections": [
                {
                    "type": "SUBJECTIVE",
                    "is_subjective": True,
                    "content": "The company could potentially reach $100M in revenue.",
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_deliverable_with_calc_refs_passes(self) -> None:
        """Deliverable with calc references passes."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "supported_calc_ids": ["550e8400-e29b-41d4-a716-446655440000"],
            "sections": [
                {
                    "type": "FINANCIAL",
                    "calc_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "content": "IRR projected at 25% based on model.",
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed


class TestNoFreeFactsNegative:
    """Negative tests - non-compliant deliverables fail."""

    def test_factual_content_without_any_refs_fails(self) -> None:
        """Deliverable with factual assertions but no refs fails."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "content": "The company has $10M ARR and grew 150% YoY.",
        }

        result = validator.validate(deliverable)
        assert not result.passed
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)

    def test_financial_section_without_refs_fails(self) -> None:
        """FINANCIAL section without claim/calc refs fails."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "sections": [
                {
                    "type": "FINANCIAL",
                    "content": "Revenue metrics look strong.",
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed
        assert any(e.code == "MISSING_REFERENCES" for e in result.errors)

    def test_market_section_without_refs_fails(self) -> None:
        """MARKET section without refs fails."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "sections": [
                {
                    "type": "MARKET",
                    "content": "TAM is $50B according to research.",
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed


class TestNoFreeFactsPatternDetection:
    """Test factual assertion pattern detection."""

    def test_detects_currency_amounts(self) -> None:
        """Detects currency amounts as factual assertions."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "content": "The company raised $5M in seed funding.",
        }

        result = validator.validate(deliverable)
        assert not result.passed
        assert any("$5M" in e.message for e in result.errors)

    def test_detects_percentages(self) -> None:
        """Detects percentages as factual assertions."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "content": "Gross margin is 75% which is industry-leading.",
        }

        result = validator.validate(deliverable)
        assert not result.passed
        assert any("75%" in e.message for e in result.errors)

    def test_detects_user_metrics(self) -> None:
        """Detects user/customer metrics as factual assertions."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "content": "Platform now has 50000 users and growing fast.",
        }

        result = validator.validate(deliverable)
        assert not result.passed
        assert any("50000 users" in e.message for e in result.errors)
