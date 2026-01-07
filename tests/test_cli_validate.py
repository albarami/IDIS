"""Tests for the CLI validate command.

Tests cover:
1. PASS: valid muhasabah record via temp file
2. FAIL: invalid JSON file (exit code 2, INVALID_JSON)
3. FAIL: unknown validator (exit code 2, INVALID_VALIDATOR)
4. FAIL: muhasabah invalid record (exit code 2, validator error codes)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from idis.cli import main


class TestCliValidatePass:
    """Test cases for successful validation."""

    def test_validate_muhasabah_valid_record(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PASS: validate --validator muhasabah with a known-valid record."""
        valid_record = {
            "agent_id": "12345678-1234-1234-1234-123456789abc",
            "output_id": "87654321-4321-4321-4321-cba987654321",
            "supported_claim_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
            "confidence": 0.75,
            "timestamp": "2026-01-07T10:00:00Z",
            "uncertainties": [],
            "falsifiability_tests": [],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(valid_record, f)
            temp_path = f.name

        try:
            exit_code = main(["validate", "--validator", "muhasabah", "--input", temp_path])
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 0
            assert output["pass"] is True
            assert output["errors"] == []
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_validate_no_free_facts_subjective_only(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PASS: validate no_free_facts with subjective-only content."""
        valid_deliverable = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "This is a subjective opinion about the market.",
                    "is_subjective": True,
                    "is_factual": False,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(valid_deliverable, f)
            temp_path = f.name

        try:
            exit_code = main(["validate", "--validator", "no_free_facts", "--input", temp_path])
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 0
            assert output["pass"] is True
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestCliValidateInvalidJson:
    """Test cases for invalid JSON handling."""

    def test_validate_invalid_json_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FAIL: invalid JSON file returns exit code 2 and INVALID_JSON error."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("{ this is not valid json }")
            temp_path = f.name

        try:
            exit_code = main(["validate", "--validator", "muhasabah", "--input", temp_path])
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 2
            assert output["pass"] is False
            assert len(output["errors"]) == 1
            assert output["errors"][0]["code"] == "INVALID_JSON"
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_validate_empty_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FAIL: empty file returns exit code 2 and INVALID_JSON error."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            temp_path = f.name

        try:
            exit_code = main(["validate", "--validator", "muhasabah", "--input", temp_path])
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 2
            assert output["pass"] is False
            assert output["errors"][0]["code"] == "INVALID_JSON"
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_validate_file_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FAIL: missing file returns exit code 2 and INVALID_JSON error."""
        exit_code = main(
            ["validate", "--validator", "muhasabah", "--input", "/nonexistent/path.json"]
        )
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert exit_code == 2
        assert output["pass"] is False
        assert output["errors"][0]["code"] == "INVALID_JSON"
        assert "File not found" in output["errors"][0]["message"]


class TestCliValidateInvalidValidator:
    """Test cases for unknown validator handling."""

    def test_validate_unknown_validator_via_direct_call(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """FAIL: calling cmd_validate with unknown validator returns INVALID_VALIDATOR.

        Note: argparse normally blocks invalid --validator choices, but we test
        the internal logic by creating a mock args object.
        """
        import argparse

        from idis.cli import cmd_validate

        args = argparse.Namespace(validator="unknown_validator", input=None)

        # We need to provide stdin, so use a temp file instead
        valid_json = {"test": "data"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(valid_json, f)
            temp_path = f.name

        try:
            args.input = temp_path
            exit_code = cmd_validate(args)
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 2
            assert output["pass"] is False
            assert output["errors"][0]["code"] == "INVALID_VALIDATOR"
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestCliValidateMuhasabahErrors:
    """Test cases for muhasabah validation failures."""

    def test_muhasabah_missing_required_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FAIL: muhasabah record missing required fields."""
        invalid_record = {
            "confidence": 0.5,
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(invalid_record, f)
            temp_path = f.name

        try:
            exit_code = main(["validate", "--validator", "muhasabah", "--input", temp_path])
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 2
            assert output["pass"] is False
            assert len(output["errors"]) > 0

            error_codes = {e["code"] for e in output["errors"]}
            assert "MISSING_AGENT_ID" in error_codes or "MISSING_OUTPUT_ID" in error_codes
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_muhasabah_high_confidence_no_uncertainties(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """FAIL: high confidence (>0.80) without uncertainties."""
        invalid_record = {
            "agent_id": "12345678-1234-1234-1234-123456789abc",
            "output_id": "87654321-4321-4321-4321-cba987654321",
            "supported_claim_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
            "confidence": 0.95,
            "timestamp": "2026-01-07T10:00:00Z",
            "uncertainties": [],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(invalid_record, f)
            temp_path = f.name

        try:
            exit_code = main(["validate", "--validator", "muhasabah", "--input", temp_path])
            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert exit_code == 2
            assert output["pass"] is False

            error_codes = {e["code"] for e in output["errors"]}
            assert "HIGH_CONFIDENCE_NO_UNCERTAINTIES" in error_codes
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestCliValidateOutputDeterminism:
    """Test that CLI output is deterministic (sorted keys)."""

    def test_output_has_sorted_keys(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify JSON output has sorted keys for deterministic diffs."""
        valid_record = {
            "agent_id": "12345678-1234-1234-1234-123456789abc",
            "output_id": "87654321-4321-4321-4321-cba987654321",
            "supported_claim_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
            "confidence": 0.5,
            "timestamp": "2026-01-07T10:00:00Z",
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(valid_record, f)
            temp_path = f.name

        try:
            main(["validate", "--validator", "muhasabah", "--input", temp_path])
            captured = capsys.readouterr()

            # Parse and re-serialize with sort_keys to verify
            output = json.loads(captured.out)
            expected_serialization = json.dumps(output, sort_keys=True, indent=2)

            assert captured.out.strip() == expected_serialization.strip()
        finally:
            Path(temp_path).unlink(missing_ok=True)
