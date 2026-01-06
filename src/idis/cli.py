"""IDIS CLI - Command-line interface for validation and utilities.

Usage:
    python -m idis validate schema <schema_name> <path_to_json>
    python -m idis validate no-free-facts <path_to_deliverable_json>
    python -m idis validate muhasabah <path_to_muhasabah_json>
    python -m idis validate sanad <path_to_sanad_json>
    python -m idis validate audit-event <path_to_audit_event_json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from idis.validators import (
    AuditEventValidator,
    MuhasabahValidator,
    NoFreeFactsValidator,
    SanadIntegrityValidator,
    SchemaValidator,
    ValidationResult,
)


def print_result(result: ValidationResult, verbose: bool = False) -> int:
    """Print validation result and return exit code."""
    if result.passed:
        print("✓ PASSED")
        if result.warnings and verbose:
            print(f"\nWarnings ({len(result.warnings)}):")
            for w in result.warnings:
                print(f"  [{w.code}] {w.path}: {w.message}")
        return 0
    else:
        print("✗ FAILED")
        print(f"\nErrors ({len(result.errors)}):")
        for e in result.errors:
            print(f"  [{e.code}] {e.path}: {e.message}")
        return 1


def load_json_file(path: str) -> dict[str, Any] | list[Any] | None:
    """Load JSON from file path."""
    try:
        file_path = Path(path)
        if not file_path.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            return None

        with file_path.open("r", encoding="utf-8") as f:
            result: dict[str, Any] | list[Any] = json.load(f)
            return result
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {path}: {e}", file=sys.stderr)
        return None
    except OSError as e:
        print(f"Error: Cannot read file {path}: {e}", file=sys.stderr)
        return None


def cmd_validate_schema(args: argparse.Namespace) -> int:
    """Validate JSON against a schema."""
    validator = SchemaValidator()

    # List available schemas if requested
    if args.schema_name == "list":
        schemas = validator.list_available_schemas()
        if schemas:
            print("Available schemas:")
            for s in sorted(schemas):
                print(f"  - {s}")
        else:
            print("No schemas found in schemas/ directory")
        return 0

    result = validator.validate_json_file(args.schema_name, args.json_path)
    return print_result(result, args.verbose)


def cmd_validate_no_free_facts(args: argparse.Namespace) -> int:
    """Validate deliverable for No-Free-Facts compliance."""
    data = load_json_file(args.json_path)
    if data is None:
        return 1

    validator = NoFreeFactsValidator()
    result = validator.validate(data)
    return print_result(result, args.verbose)


def cmd_validate_muhasabah(args: argparse.Namespace) -> int:
    """Validate Muḥāsabah record."""
    data = load_json_file(args.json_path)
    if data is None:
        return 1

    validator = MuhasabahValidator()
    result = validator.validate(data)
    return print_result(result, args.verbose)


def cmd_validate_sanad(args: argparse.Namespace) -> int:
    """Validate Sanad record."""
    data = load_json_file(args.json_path)
    if data is None:
        return 1

    validator = SanadIntegrityValidator()
    result = validator.validate_sanad(data)
    return print_result(result, args.verbose)


def cmd_validate_audit_event(args: argparse.Namespace) -> int:
    """Validate audit event."""
    data = load_json_file(args.json_path)
    if data is None:
        return 1

    validator = AuditEventValidator()
    result = validator.validate(data)
    return print_result(result, args.verbose)


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="idis",
        description="IDIS - Institutional Deal Intelligence System CLI",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show verbose output including warnings",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate JSON data against schemas or rules",
    )
    validate_subparsers = validate_parser.add_subparsers(
        dest="validator",
        help="Validator type",
    )

    # validate schema
    schema_parser = validate_subparsers.add_parser(
        "schema",
        help="Validate against JSON schema",
    )
    schema_parser.add_argument(
        "schema_name",
        help="Schema name (e.g., claim, sanad, defect) or 'list' to show available",
    )
    schema_parser.add_argument(
        "json_path",
        nargs="?",
        default="",
        help="Path to JSON file to validate",
    )

    # validate no-free-facts
    nff_parser = validate_subparsers.add_parser(
        "no-free-facts",
        help="Validate deliverable for No-Free-Facts compliance",
    )
    nff_parser.add_argument(
        "json_path",
        help="Path to deliverable JSON file",
    )

    # validate muhasabah
    muh_parser = validate_subparsers.add_parser(
        "muhasabah",
        help="Validate Muḥāsabah record",
    )
    muh_parser.add_argument(
        "json_path",
        help="Path to Muḥāsabah JSON file",
    )

    # validate sanad
    sanad_parser = validate_subparsers.add_parser(
        "sanad",
        help="Validate Sanad record",
    )
    sanad_parser.add_argument(
        "json_path",
        help="Path to Sanad JSON file",
    )

    # validate audit-event
    audit_parser = validate_subparsers.add_parser(
        "audit-event",
        help="Validate audit event",
    )
    audit_parser.add_argument(
        "json_path",
        help="Path to audit event JSON file",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "validate":
        if args.validator is None:
            parser.parse_args(["validate", "--help"])
            return 0

        if args.validator == "schema":
            if args.schema_name != "list" and not args.json_path:
                print("Error: json_path required for schema validation", file=sys.stderr)
                return 1
            return cmd_validate_schema(args)
        elif args.validator == "no-free-facts":
            return cmd_validate_no_free_facts(args)
        elif args.validator == "muhasabah":
            return cmd_validate_muhasabah(args)
        elif args.validator == "sanad":
            return cmd_validate_sanad(args)
        elif args.validator == "audit-event":
            return cmd_validate_audit_event(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
