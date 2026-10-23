"""TTY-only operator lookup for one safe real-example source alias.

The private path is admitted only after the complete source contract and sealed
preparation ledger agree.  It is written once to a verified Windows console
handle and is never returned, serialized, logged, or written to stdio.
"""

from __future__ import annotations

import argparse
import ctypes
import re
import stat
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from idis.evaluation.real_example_source_package import (
    DEFAULT_SOURCE_ROOT,
    SourcePackageContractError,
    TwoCompanySourceContract,
    build_two_company_source_contract,
    write_reconciled_source_alias,
)

SOURCE_LOOKUP_TTY_REQUIRED = "SOURCE_LOOKUP_TTY_REQUIRED"
SOURCE_LOOKUP_ARGUMENT_INVALID = "SOURCE_LOOKUP_ARGUMENT_INVALID"
SOURCE_LOOKUP_FAILED = "SOURCE_LOOKUP_FAILED"
SOURCE_LOOKUP_REPORT_ROOT = DEFAULT_SOURCE_ROOT.parent / ".local_reports" / "slice100"
SOURCE_LOOKUP_LEDGER_NAME = "prepare-ledger.jsonl"

_ACCEPTANCE_NAMESPACE_RE = re.compile(r"^acceptance-[0-9]{3}$")
_GENERATED_UPLOAD_NAME_RE = re.compile(r"^company-00[12]-document-[0-9]{5}\.[a-z0-9]{1,12}$")
_SAFE_FAILURE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_REQUIRED_OPTIONS = (
    "--acceptance-namespace",
    "--generated-upload-name",
    "--acknowledge-private-terminal-output",
)
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = int(ctypes.c_void_p(-1).value or -1)

TerminalWriter = Callable[[Path], None]
TerminalFactory = Callable[[], AbstractContextManager[TerminalWriter]]
ContractBuilder = Callable[[Path], TwoCompanySourceContract]


class SourceLookupError(RuntimeError):
    """Path-free lookup failure carrying only a stable safe code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _default_contract_builder(root: Path) -> TwoCompanySourceContract:
    return build_two_company_source_contract(root=root)


def resolve_and_write_source_alias(
    *,
    source_root: str | Path,
    ledger_path: str | Path,
    generated_upload_name: str,
    terminal_writer: TerminalWriter,
    contract_builder: ContractBuilder = _default_contract_builder,
) -> None:
    """Resolve one alias and pass its path only to the admitted terminal writer."""
    try:
        canonical_source_root = Path(source_root)
        if (
            not canonical_source_root.is_absolute()
            or _has_reparse_component(canonical_source_root)
            or canonical_source_root.resolve(strict=True) != canonical_source_root
        ):
            raise SourceLookupError(SOURCE_LOOKUP_FAILED)
        contract = contract_builder(canonical_source_root)
        if not isinstance(contract, TwoCompanySourceContract):
            raise TypeError
        write_reconciled_source_alias(
            contract=contract,
            ledger_path=ledger_path,
            generated_upload_name=generated_upload_name,
            private_path_writer=terminal_writer,
        )
    except SourcePackageContractError as exc:
        code = exc.code if _is_safe_failure_code(exc.code) else SOURCE_LOOKUP_FAILED
        raise SourceLookupError(code) from None
    except SourceLookupError:
        raise
    except Exception:
        raise SourceLookupError(SOURCE_LOOKUP_FAILED) from None


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    if any(not isinstance(token, str) for token in argv):
        raise SourceLookupError(SOURCE_LOOKUP_ARGUMENT_INVALID)
    if any(argv.count(option) != 1 for option in _REQUIRED_OPTIONS):
        raise SourceLookupError(SOURCE_LOOKUP_ARGUMENT_INVALID)
    parser = argparse.ArgumentParser(
        add_help=False,
        allow_abbrev=False,
        exit_on_error=False,
    )
    parser.add_argument("--acceptance-namespace")
    parser.add_argument("--generated-upload-name")
    parser.add_argument("--acknowledge-private-terminal-output", action="store_true")
    try:
        args, unknown = parser.parse_known_args(argv)
    except argparse.ArgumentError:
        raise SourceLookupError(SOURCE_LOOKUP_ARGUMENT_INVALID) from None
    if (
        unknown
        or args.acknowledge_private_terminal_output is not True
        or not isinstance(args.acceptance_namespace, str)
        or not _ACCEPTANCE_NAMESPACE_RE.fullmatch(args.acceptance_namespace)
        or not isinstance(args.generated_upload_name, str)
        or not _GENERATED_UPLOAD_NAME_RE.fullmatch(args.generated_upload_name)
    ):
        raise SourceLookupError(SOURCE_LOOKUP_ARGUMENT_INVALID)
    return args


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise SourceLookupError(SOURCE_LOOKUP_FAILED) from None
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _has_reparse_component(path: Path) -> bool:
    return any(_is_link_or_reparse(candidate) for candidate in (path, *path.parents))


def _canonical_ledger_path(acceptance_namespace: str) -> Path:
    report_root = Path(SOURCE_LOOKUP_REPORT_ROOT)
    namespace_dir = report_root / acceptance_namespace
    ledger_path = namespace_dir / SOURCE_LOOKUP_LEDGER_NAME
    if (
        not report_root.is_absolute()
        or report_root.name != "slice100"
        or report_root.parent.name != ".local_reports"
        or namespace_dir.parent != report_root
        or not _ACCEPTANCE_NAMESPACE_RE.fullmatch(namespace_dir.name)
        or ledger_path.parent != namespace_dir
        or ledger_path.name != SOURCE_LOOKUP_LEDGER_NAME
        or _has_reparse_component(ledger_path)
    ):
        raise SourceLookupError(SOURCE_LOOKUP_FAILED)
    try:
        if ledger_path.resolve(strict=False) != ledger_path:
            raise SourceLookupError(SOURCE_LOOKUP_FAILED)
    except OSError:
        raise SourceLookupError(SOURCE_LOOKUP_FAILED) from None
    return ledger_path


def _stream_is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except OSError:
        return False


def _windows_kernel32() -> Any:
    if sys.platform != "win32":
        raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED) from None
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED) from None


@contextmanager
def _controlling_console_terminal() -> Iterator[TerminalWriter]:
    if sys.platform != "win32" or not all(
        _stream_is_tty(stream) for stream in (sys.stdin, sys.stdout, sys.stderr)
    ):
        raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED)

    kernel32 = _windows_kernel32()
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetConsoleMode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetConsoleMode.restype = ctypes.c_int
    kernel32.WriteConsoleW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    kernel32.WriteConsoleW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.CreateFileW(
        "CONOUT$",
        _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    handle_value = int(handle) if handle is not None else _INVALID_HANDLE_VALUE
    if handle_value == _INVALID_HANDLE_VALUE:
        raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED)
    try:
        console_mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(
            ctypes.c_void_p(handle_value),
            ctypes.byref(console_mode),
        ):
            raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED)
        wrote_path = False

        def write_private_path(path: Path) -> None:
            nonlocal wrote_path
            if wrote_path:
                raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED)
            path_text = str(path)
            if not path_text or any(character in path_text for character in ("\x00", "\r", "\n")):
                raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED)
            payload = f"{path_text}\r\n"
            payload_units = len(payload.encode("utf-16-le")) // 2
            written = ctypes.c_uint32()
            buffer = ctypes.create_unicode_buffer(payload)
            if (
                not kernel32.WriteConsoleW(
                    ctypes.c_void_p(handle_value),
                    buffer,
                    payload_units,
                    ctypes.byref(written),
                    None,
                )
                or written.value != payload_units
            ):
                raise SourceLookupError(SOURCE_LOOKUP_TTY_REQUIRED)
            wrote_path = True

        yield write_private_path
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle_value))


def _is_safe_failure_code(code: object) -> bool:
    return isinstance(code, str) and _SAFE_FAILURE_CODE_RE.fullmatch(code) is not None


def _emit_safe_failure(code: str) -> int:
    safe_code = code if _is_safe_failure_code(code) else SOURCE_LOOKUP_FAILED
    sys.stderr.write(f"{safe_code}\n")
    return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    terminal_factory: TerminalFactory = _controlling_console_terminal,
) -> int:
    """Run the path-free CLI, emitting a private locator only through ``CONOUT$``."""
    try:
        args = _parse_arguments(tuple(sys.argv[1:] if argv is None else argv))
        ledger_path = _canonical_ledger_path(args.acceptance_namespace)
        with terminal_factory() as terminal_writer:
            resolve_and_write_source_alias(
                source_root=DEFAULT_SOURCE_ROOT,
                ledger_path=ledger_path,
                generated_upload_name=args.generated_upload_name,
                terminal_writer=terminal_writer,
            )
    except SourceLookupError as exc:
        return _emit_safe_failure(exc.code)
    except OSError:
        return _emit_safe_failure(SOURCE_LOOKUP_TTY_REQUIRED)
    except Exception:
        return _emit_safe_failure(SOURCE_LOOKUP_FAILED)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through ``main``.
    raise SystemExit(main())
