"""Tests for NoFreeFactsValidator - proves fail-closed behavior.

These tests verify:
1. Per-section validation (refs elsewhere don't save a section)
2. Fail-closed on unreferenced factual assertions (errors, not warnings)
3. Subjective escape hatch works correctly
4. Structured input with is_factual/is_subjective fields
"""

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


class TestNoFreeFactsStructuredInput:
    """Tests for canonical structured deliverable format with is_factual/is_subjective."""

    def test_factual_section_with_local_refs_passes(self) -> None:
        """PASS: Factual section with per-section refs passes."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue grew 20% YoY.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_factual_section_with_calc_refs_passes(self) -> None:
        """PASS: Factual section with calc_ids passes."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "IRR projected at 25%.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_plain_text_factual_assertion_no_refs_fails(self) -> None:
        """FAIL: Plain-text factual assertion with is_factual=true and no refs fails.

        This is a core regression test - a section marked is_factual=true
        without any referenced_claim_ids or referenced_calc_ids MUST fail.
        """
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "This is a fact about the market.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, "Expected FAIL but validator passed"
        assert any(e.code == "NO_FREE_FACTS_UNREFERENCED_FACT" for e in result.errors), (
            f"Expected NO_FREE_FACTS_UNREFERENCED_FACT error, got: {result.errors}"
        )

    def test_refs_elsewhere_do_not_save_factual_section(self) -> None:
        """FAIL: References in another section do NOT satisfy a factual section.

        This is a critical regression test - per-section validation means
        refs elsewhere in the document cannot satisfy a factual assertion.
        """
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The market is growing rapidly.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": [],  # NO LOCAL REFS - should fail
                    "referenced_calc_ids": [],
                },
                {
                    "text": "Supporting data section.",
                    "is_factual": False,
                    "is_subjective": False,
                    "referenced_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "referenced_calc_ids": [],
                },
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, "Expected FAIL - refs in section B should not satisfy section A"
        assert any(e.code == "NO_FREE_FACTS_UNREFERENCED_FACT" for e in result.errors)
        # Verify the error points to the correct section
        assert any("sections[0]" in e.path for e in result.errors)

    def test_subjective_escape_hatch_works(self) -> None:
        """PASS: Subjective section without refs passes (escape hatch).

        When is_subjective=true, the No-Free-Facts rule does not apply,
        even if the text contains factual-looking content.
        """
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "We believe the market could reach $50B in 5 years.",
                    "is_factual": False,
                    "is_subjective": True,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass (subjective) but got: {result.errors}"

    def test_non_factual_section_without_refs_passes(self) -> None:
        """PASS: Non-factual section (is_factual=false) without refs passes."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Introduction to the company.",
                    "is_factual": False,
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_multiple_factual_sections_all_need_refs(self) -> None:
        """FAIL: Multiple factual sections - ALL must have local refs."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue is $10M ARR.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "referenced_calc_ids": [],
                },
                {
                    "text": "Gross margin is 75%.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": [],  # MISSING - should fail
                    "referenced_calc_ids": [],
                },
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, "Expected FAIL - second section has no refs"
        assert any("sections[1]" in e.path for e in result.errors)


class TestNoFreeFactsFallbackHeuristic:
    """Tests for fallback heuristic when is_factual/is_subjective not provided."""

    def test_fallback_detects_numeric_assertions(self) -> None:
        """FAIL (fallback): Deliverable without is_factual but with numeric facts fails."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The company raised $5M in seed funding.",
                    # No is_factual field - fallback heuristic applies
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, "Expected FAIL - heuristic should detect $5M"
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)

    def test_fallback_detects_fact_keyword(self) -> None:
        """FAIL (fallback): Text containing 'the fact is' without refs fails."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The fact is that market conditions are favorable.",
                    # No is_factual field - fallback uses pattern detection
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, "Expected FAIL - 'the fact is' should trigger"
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)

    def test_fallback_with_local_refs_passes(self) -> None:
        """PASS (fallback): Section with local refs passes even with heuristic match."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue grew 150% YoY.",
                    "referenced_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    # No is_factual - but has local refs
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass but got: {result.errors}"


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

    def test_sections_with_numeric_content_no_refs_fails(self) -> None:
        """Section with numeric factual content but no refs fails."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "ic_bound": True,
            "sections": [
                {
                    "text": "TAM is $50B according to research.",
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)


class TestNoFreeFactsPatternDetection:
    """Test factual assertion pattern detection (heuristic fallback)."""

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


class TestNoFreeFactsErrorStructure:
    """Tests that errors have proper structure (code, message, path)."""

    def test_error_has_code_message_path(self) -> None:
        """Error objects have required fields: code, message, path."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The market is worth $100B.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed

        for error in result.errors:
            assert error.code, "Error must have a code"
            assert error.message, "Error must have a message"
            assert error.path, "Error must have a path"
            # Path should reference the section
            assert "sections[0]" in error.path


class TestNoFreeFactsBypassPrevention:
    """Regression tests for bypass prevention.

    These tests verify that the validator cannot be bypassed via:
    1. Mixed structured/unstructured sections
    2. Mislabeling is_factual=false on factual content
    """

    def test_mixed_structured_unstructured_bypass_blocked(self) -> None:
        """FAIL: Mixed structured/unstructured - unstructured factual section must fail.

        Scenario:
        - Section A: structured factual with refs (passes)
        - Section B: unstructured (no is_factual) with "This is a fact..." and no refs

        Expected: Overall FAIL because Section B contains factual content without refs.
        The presence of structured Section A must NOT disable heuristics for Section B.
        """
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue is $10M ARR.",
                    "is_factual": True,
                    "is_subjective": False,
                    "referenced_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "referenced_calc_ids": [],
                },
                {
                    "text": "The fact is that market conditions are favorable.",
                    # NO is_factual field - heuristics must still run!
                    # NO refs - should trigger failure
                },
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, (
            "Expected FAIL - Section B has factual content ('the fact is') without refs. "
            "Structured Section A must NOT disable heuristics for unstructured Section B."
        )
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)
        assert any("sections[1]" in e.path for e in result.errors)

    def test_mixed_structured_unstructured_with_currency_bypass_blocked(self) -> None:
        """FAIL: Unstructured section with $5M must fail even if other sections are structured."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Company overview.",
                    "is_factual": False,
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                },
                {
                    "text": "They raised $5M in their Series A.",
                    # NO is_factual - but contains $5M which is factual
                },
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, (
            "Expected FAIL - Section B has '$5M' without refs. "
            "Heuristics must run on unstructured sections."
        )
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)

    def test_mislabel_is_factual_false_bypass_blocked(self) -> None:
        """FAIL: Mislabeling is_factual=false on factual content must still fail.

        Scenario:
        - Section with is_factual=false, is_subjective=false
        - Text contains "Revenue is $5M." which is clearly factual
        - No refs provided

        Expected: FAIL because heuristics detect the factual content.
        Setting is_factual=false does NOT disable heuristic checking.
        """
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue is $5M.",
                    "is_factual": False,  # MISLABELED - text is clearly factual
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, (
            "Expected FAIL - is_factual=false but text contains '$5M'. "
            "Heuristics must catch mislabeled factual content."
        )
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)
        assert any("$5M" in e.message for e in result.errors)

    def test_mislabel_with_percentage_bypass_blocked(self) -> None:
        """FAIL: Mislabeling on percentage content must fail."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Gross margin is 75% which is strong.",
                    "is_factual": False,  # MISLABELED
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert not result.passed, "Expected FAIL - '75%' is factual despite is_factual=false"
        assert any(e.code == "NO_FREE_FACTS_VIOLATION" for e in result.errors)

    def test_mislabel_with_refs_passes(self) -> None:
        """PASS: Mislabeled section with refs should still pass."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue is $5M.",
                    "is_factual": False,  # Mislabeled but has refs
                    "is_subjective": False,
                    "referenced_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass - section has refs: {result.errors}"

    def test_truly_non_factual_content_passes_without_refs(self) -> None:
        """PASS: Truly non-factual content (no heuristic matches) passes without refs."""
        validator = NoFreeFactsValidator()

        deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The team seems motivated and experienced.",
                    "is_factual": False,
                    "is_subjective": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validator.validate(deliverable)
        assert result.passed, f"Expected pass - text has no factual markers: {result.errors}"
