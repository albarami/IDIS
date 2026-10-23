"""GL-02 obligation register command line.

The script prints identifiers, codes and counts. Source text is printed only
for ``runs`` and ``quote`` when ``--show-text`` is set, and it is never written
to a file. ``status`` prints per-shard counts and never prints source text.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from idis.methodology.obligations.audit_input import command_audit_input
from idis.methodology.obligations.commands import (
    command_extract,
    command_manifest,
    command_quote,
    command_runs,
    command_status,
    command_validate,
    command_views,
)
from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.ledger import command_seal
from idis.methodology.obligations.review_pack import command_review_pack
from idis.methodology.obligations.vocab import Code


def main(argv: list[str] | None = None) -> int:
    """Run one obligation-register command."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 2 if code not in {0, None} else 0
    try:
        return _dispatch(args)
    except Gl02Error as exc:
        sys.stderr.write(f"{exc.code}\n")
        return 2
    except OSError:
        sys.stderr.write(f"{Code.GL02_IO.value}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__} {Code.GL02_IO.value}\n")
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "extract":
        sys.stdout.write(
            command_extract(
                Path(args.workbook),
                Path(args.private_root),
                template=None if args.template is None else Path(args.template),
                category_map=None if args.category_map is None else Path(args.category_map),
                policy=None if args.policy is None else Path(args.policy),
            )
        )
        return 0
    if args.command == "validate":
        code, text = command_validate(
            Path(args.private_root),
            workbook=None if args.workbook is None else Path(args.workbook),
            policy=None if args.policy is None else Path(args.policy),
            shards=args.shard,
            view=args.view,
            output_format=args.format,
        )
        sys.stdout.write(text)
        return code
    if args.command == "runs":
        code, text = command_runs(
            Path(args.private_root),
            args.parent,
            field=args.field,
            show_text=args.show_text,
            output_format=args.format,
        )
        sys.stdout.write(text)
        return code
    if args.command == "status":
        code, text = command_status(
            Path(args.private_root),
            shards=args.shard,
            output_format=args.format,
        )
        sys.stdout.write(text)
        return code
    if args.command == "quote":
        code, text = command_quote(
            Path(args.private_root),
            args.parent,
            args.text,
            field=args.field,
            show_text=args.show_text,
        )
        sys.stdout.write(text)
        return code
    if args.command == "seal":
        code, text = command_seal(
            Path(args.private_root),
            workbook=None if args.workbook is None else Path(args.workbook),
            policy=None if args.policy is None else Path(args.policy),
            shards=args.shard,
            snapshot_root=None if args.snapshot_root is None else Path(args.snapshot_root),
            now=args.now or _utc_now(),
        )
        sys.stdout.write(text)
        return code
    if args.command == "views":
        sys.stdout.write(command_views(Path(args.private_root), output_format=args.format))
        return 0
    if args.command == "review-pack":
        sys.stdout.write(
            command_review_pack(
                Path(args.private_root),
                out_dir=None if args.out_dir is None else Path(args.out_dir),
                shards=args.shard,
            )
        )
        return 0
    if args.command == "audit-input":
        sys.stdout.write(
            command_audit_input(
                Path(args.private_root),
                stage=args.stage,
                shard=args.shard,
                out_file=None if args.out_file is None else Path(args.out_file),
            )
        )
        return 0
    if args.command == "manifest":
        sys.stdout.write(
            command_manifest(
                Path(args.private_root),
                private_out=None if args.private_out is None else Path(args.private_out),
                public_out=None if args.public_out is None else Path(args.public_out),
            )
        )
        return 0
    sys.stderr.write(f"{Code.GL02_USAGE.value}\n")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gl02_register")
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract = subparsers.add_parser("extract")
    extract.add_argument("--workbook", required=True)
    extract.add_argument("--private-root", required=True)
    extract.add_argument("--template")
    extract.add_argument("--category-map")
    extract.add_argument("--policy")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--private-root", required=True)
    validate.add_argument("--workbook")
    validate.add_argument("--policy")
    validate.add_argument("--shard", action="append")
    validate.add_argument("--view")
    validate.add_argument("--format", choices=("text", "json"), default="text")
    runs = subparsers.add_parser("runs")
    runs.add_argument("--private-root", required=True)
    runs.add_argument("--parent", required=True)
    runs.add_argument("--field", default="question")
    runs.add_argument("--show-text", action="store_true")
    runs.add_argument("--format", choices=("text", "json"), default="text")
    status = subparsers.add_parser("status")
    status.add_argument("--private-root", required=True)
    status.add_argument("--shard", action="append")
    status.add_argument("--format", choices=("text", "json"), default="text")
    quote = subparsers.add_parser("quote")
    quote.add_argument("--private-root", required=True)
    quote.add_argument("--parent", required=True)
    quote.add_argument("--text", required=True)
    quote.add_argument("--field", default="question")
    quote.add_argument("--show-text", action="store_true")
    seal = subparsers.add_parser("seal")
    seal.add_argument("--private-root", required=True)
    seal.add_argument("--workbook")
    seal.add_argument("--policy")
    seal.add_argument("--shard", action="append")
    seal.add_argument("--snapshot-root")
    seal.add_argument("--now")
    views = subparsers.add_parser("views")
    views.add_argument("--private-root", required=True)
    views.add_argument("--format", choices=("text", "json"), default="text")
    review = subparsers.add_parser("review-pack")
    review.add_argument("--private-root", required=True)
    review.add_argument("--out-dir")
    review.add_argument("--shard", action="append")
    audit = subparsers.add_parser("audit-input")
    audit.add_argument("--private-root", required=True)
    audit.add_argument("--stage", required=True, choices=("blind", "compare"))
    audit.add_argument("--shard", required=True)
    audit.add_argument("--out-file")
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--private-root", required=True)
    manifest.add_argument("--private-out")
    manifest.add_argument("--public-out")
    return parser


def _utc_now() -> str:
    """UTC timestamp supplied by the script. The library does not read the clock."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
