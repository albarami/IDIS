"""IDIS CLI - Deterministic command-line interface for trust validators.

Usage:
    python -m idis validate --validator <name> [--input PATH]
    python -m idis schemas check

Validators: no_free_facts, muhasabah, sanad_integrity, audit_event

Exit codes:
    0: Validation passed
    2: Validation failed
    1: Internal error (unexpected)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from idis.validators import (
    ValidationResult,
    validate_audit_event,
    validate_muhasabah,
    validate_no_free_facts,
    validate_sanad_integrity,
)

# Valid validator names (underscore format as specified)
VALID_VALIDATORS = frozenset(
    {
        "no_free_facts",
        "muhasabah",
        "sanad_integrity",
        "audit_event",
    }
)

# Validator dispatch map
VALIDATOR_DISPATCH: dict[str, Any] = {
    "no_free_facts": validate_no_free_facts,
    "muhasabah": validate_muhasabah,
    "sanad_integrity": validate_sanad_integrity,
    "audit_event": validate_audit_event,
}


def _result_to_dict(result: ValidationResult) -> dict[str, Any]:
    """Convert ValidationResult to a deterministic dict for JSON output."""
    errors_list = []
    for e in result.errors:
        errors_list.append(
            {
                "code": e.code,
                "message": e.message,
                "path": e.path,
            }
        )
    warnings_list = []
    for w in result.warnings:
        warnings_list.append(
            {
                "code": w.code,
                "message": w.message,
                "path": w.path,
            }
        )
    return {
        "errors": errors_list,
        "pass": result.passed,
        "warnings": warnings_list,
    }


def _output_json(data: dict[str, Any]) -> None:
    """Output JSON to stdout with deterministic ordering."""
    print(json.dumps(data, sort_keys=True, indent=2))


def _make_error_result(code: str, message: str) -> dict[str, Any]:
    """Create a failed ValidationResult dict with a single error."""
    return {
        "errors": [{"code": code, "message": message, "path": "$"}],
        "pass": False,
        "warnings": [],
    }


def _load_json_input(input_path: str | None) -> tuple[Any, str | None]:
    """Load JSON from file or stdin.

    Returns:
        Tuple of (parsed_data, error_message). If error_message is not None,
        parsed_data should be ignored.
    """
    try:
        if input_path:
            with open(input_path, encoding="utf-8") as f:
                content = f.read()
        else:
            content = sys.stdin.read()

        if not content.strip():
            return None, "Empty input"

        return json.loads(content), None
    except FileNotFoundError:
        return None, f"File not found: {input_path}"
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    except OSError as e:
        return None, f"Cannot read input: {e}"


def cmd_validate(args: argparse.Namespace) -> int:
    """Execute validate command with deterministic JSON output.

    Exit codes:
        0: pass=True
        2: pass=False (validation failed or invalid input)
    """
    validator_name = args.validator
    input_path = args.input

    # Check for valid validator name (fail-closed)
    if validator_name not in VALID_VALIDATORS:
        result = _make_error_result(
            "INVALID_VALIDATOR",
            f"Unknown validator: '{validator_name}'. Valid options: {sorted(VALID_VALIDATORS)}",
        )
        _output_json(result)
        return 2

    # Load JSON input
    data, error_msg = _load_json_input(input_path)
    if error_msg is not None:
        result = _make_error_result("INVALID_JSON", error_msg)
        _output_json(result)
        return 2

    # Dispatch to validator
    validator_fn = VALIDATOR_DISPATCH[validator_name]
    validation_result = validator_fn(data)

    # Output deterministic JSON
    result_dict = _result_to_dict(validation_result)
    _output_json(result_dict)

    return 0 if validation_result.passed else 2


def cmd_schemas_check(args: argparse.Namespace) -> int:
    """Execute schemas check command.

    Exit codes:
        0: pass=True (all required schemas present and valid)
        2: pass=False (missing or invalid schemas)
    """
    from idis.schemas.registry import SchemaRegistry

    registry = SchemaRegistry()
    result = registry.check_completeness()
    _output_json(result)
    return 0 if result["pass"] else 2


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="idis",
        description="IDIS - Institutional Deal Intelligence System CLI",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate JSON data using trust validators",
    )
    validate_parser.add_argument(
        "--validator",
        required=True,
        help="Validator to use (no_free_facts, muhasabah, sanad_integrity, audit_event)",
    )
    validate_parser.add_argument(
        "--input",
        required=False,
        default=None,
        metavar="PATH",
        help="Path to JSON file (reads from stdin if omitted)",
    )

    # schemas command with check subcommand
    schemas_parser = subparsers.add_parser(
        "schemas",
        help="Schema registry operations",
    )
    schemas_subparsers = schemas_parser.add_subparsers(
        dest="schemas_command",
        help="Schema subcommands",
    )
    schemas_subparsers.add_parser(
        "check",
        help="Check schema registry completeness and loadability",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point.

    Exit codes:
        0: Success / validation passed
        2: Validation failed / schema check failed
        1: Internal error (unexpected)
    """
    try:
        parser = create_parser()
        args = parser.parse_args(argv)

        if args.command is None:
            parser.print_help()
            return 0

        if args.command == "validate":
            return cmd_validate(args)

        if args.command == "schemas":
            if getattr(args, "schemas_command", None) == "check":
                return cmd_schemas_check(args)
            else:
                parser.parse_args(["schemas", "--help"])
                return 0

        return 0

    except Exception as e:
        # Fail-closed: unexpected errors return exit code 1
        error_result = _make_error_result("INTERNAL_ERROR", str(e))
        _output_json(error_result)
        return 1


if __name__ == "__main__":
    sys.exit(main())
