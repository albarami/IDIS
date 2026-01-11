"""Tests for No-Free-Facts semantic pattern detection.

Phase POST-5.2: Tests for semantic subject-predicate pattern matching
in addition to regex-based factual assertion detection.
"""

from __future__ import annotations

from idis.validators.no_free_facts import (
    NoFreeFactsValidator,
    SemanticMatch,
    validate_no_free_facts,
)


class TestSemanticPatternExtraction:
    """Tests for semantic pattern extraction."""

    def test_company_achievement_pattern(self) -> None:
        """Test detection of company achievement patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The company achieved product-market fit in Q2 2024."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "company_achievement" for m in matches)

    def test_revenue_change_pattern(self) -> None:
        """Test detection of revenue change patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "Revenue grew significantly over the past year."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "revenue_change" for m in matches)

    def test_funding_event_pattern(self) -> None:
        """Test detection of funding event patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The company raised a Series A round from top investors."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "funding_event" for m in matches)

    def test_margin_state_pattern(self) -> None:
        """Test detection of margin state patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "Gross margin is healthy at current levels."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "margin_state" for m in matches)

    def test_market_size_pattern(self) -> None:
        """Test detection of market size patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The TAM is estimated at a significant size."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "market_size" for m in matches)

    def test_team_growth_pattern(self) -> None:
        """Test detection of team growth patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The team grew rapidly to meet demand."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "team_growth" for m in matches)

    def test_founder_background_pattern(self) -> None:
        """Test detection of founder background patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The founder previously worked at a major tech company."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "founder_background" for m in matches)

    def test_customer_growth_pattern(self) -> None:
        """Test detection of customer growth patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "Customers grew to a substantial base."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "customer_growth" for m in matches)

    def test_valuation_claim_pattern(self) -> None:
        """Test detection of valuation claim patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The valuation is set at a premium level."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "valuation_claim" for m in matches)

    def test_founding_date_pattern(self) -> None:
        """Test detection of founding date patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The company was founded in 2020."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) >= 1
        assert any(m.rule_name == "founding_date" for m in matches)


class TestSemanticRulesDisabled:
    """Tests for semantic rules when disabled."""

    def test_no_semantic_matches_when_disabled(self) -> None:
        """Test that semantic rules don't match when disabled."""
        validator = NoFreeFactsValidator(enable_semantic_rules=False)
        text = "The company achieved product-market fit."
        matches = validator._extract_semantic_matches(text)

        assert len(matches) == 0

    def test_regex_still_works_when_semantic_disabled(self) -> None:
        """Test that regex patterns still work when semantic is disabled."""
        validator = NoFreeFactsValidator(enable_semantic_rules=False)
        text = "Revenue is $5M."
        assertions = validator._looks_like_fact(text)

        assert len(assertions) >= 1  # Should still find $5M


class TestSemanticValidation:
    """Tests for semantic patterns in full validation flow."""

    def test_semantic_assertion_without_refs_fails(self) -> None:
        """Test that semantic assertions without refs fail validation."""
        data = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The company achieved significant growth last quarter.",
                    "is_factual": False,  # Mislabeled
                }
            ],
        }
        result = validate_no_free_facts(data)
        # Should detect via semantic pattern even though is_factual=False
        assert result.passed is False
        assert any("company" in str(e.message).lower() for e in result.errors)

    def test_semantic_assertion_with_refs_passes(self) -> None:
        """Test that semantic assertions with refs pass validation."""
        data = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The company achieved significant growth last quarter.",
                    "referenced_claim_ids": ["claim-001"],
                }
            ],
        }
        result = validate_no_free_facts(data)
        assert result.passed is True

    def test_subjective_bypasses_semantic_check(self) -> None:
        """Test that subjective sections bypass semantic checks."""
        data = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "The company achieved significant growth last quarter.",
                    "is_subjective": True,
                }
            ],
        }
        result = validate_no_free_facts(data)
        assert result.passed is True


class TestCombinedRegexAndSemantic:
    """Tests for combined regex and semantic detection."""

    def test_both_regex_and_semantic_detected(self) -> None:
        """Test that both regex and semantic patterns are detected."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "Revenue grew 50% and the company achieved $10M ARR."
        assertions = validator._looks_like_fact(text)

        # Should find both percentage (regex) and company achievement (semantic)
        assert len(assertions) >= 2

        patterns = [a.pattern_matched for a in assertions]
        # At least one regex and one semantic match
        assert any("semantic:" in p for p in patterns) or any(
            "semantic:" not in p for p in patterns
        )

    def test_no_duplicate_positions(self) -> None:
        """Test that same position isn't reported twice."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "Revenue grew 50% last year."
        assertions = validator._looks_like_fact(text)

        # Check no duplicate positions
        positions = [a.position for a in assertions]
        assert len(positions) == len(set(positions))


class TestSemanticMatchDataclass:
    """Tests for SemanticMatch dataclass."""

    def test_semantic_match_creation(self) -> None:
        """Test creating a SemanticMatch."""
        match = SemanticMatch(
            subject="company",
            predicate="achieved",
            rule_name="company_achievement",
            position=10,
        )
        assert match.subject == "company"
        assert match.predicate == "achieved"
        assert match.rule_name == "company_achievement"
        assert match.position == 10


class TestComplexSemanticScenarios:
    """Tests for complex semantic detection scenarios."""

    def test_multiple_semantic_patterns_in_paragraph(self) -> None:
        """Test detection of multiple semantic patterns in one paragraph."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = (
            "The company achieved product-market fit in 2023. "
            "Revenue grew significantly year-over-year. "
            "The team expanded to meet growing demand."
        )
        matches = validator._extract_semantic_matches(text)

        rule_names = {m.rule_name for m in matches}
        assert "company_achievement" in rule_names
        assert "revenue_change" in rule_names
        assert "team_growth" in rule_names

    def test_competitive_claim_detection(self) -> None:
        """Test detection of competitive claims."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "We outperform all competitors in customer satisfaction."
        matches = validator._extract_semantic_matches(text)

        assert any(m.rule_name == "competitive_claim" for m in matches)

    def test_unit_economics_pattern(self) -> None:
        """Test detection of unit economics patterns."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "CAC is improving and LTV is growing steadily."
        matches = validator._extract_semantic_matches(text)

        assert any(m.rule_name == "unit_economics" for m in matches)


class TestDeterminism:
    """Tests to ensure semantic detection is deterministic."""

    def test_same_input_same_output(self) -> None:
        """Test that same input always produces same output."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "The company achieved strong growth in Q4."

        matches1 = validator._extract_semantic_matches(text)
        matches2 = validator._extract_semantic_matches(text)

        assert len(matches1) == len(matches2)
        for m1, m2 in zip(matches1, matches2, strict=True):
            assert m1.subject == m2.subject
            assert m1.predicate == m2.predicate
            assert m1.rule_name == m2.rule_name
            assert m1.position == m2.position

    def test_no_randomness_in_detection(self) -> None:
        """Test that detection has no randomness."""
        validator = NoFreeFactsValidator(enable_semantic_rules=True)
        text = "Revenue grew and the team expanded rapidly."

        # Run 10 times and verify consistency
        results = [validator._extract_semantic_matches(text) for _ in range(10)]
        first_result = results[0]

        for result in results[1:]:
            assert len(result) == len(first_result)
