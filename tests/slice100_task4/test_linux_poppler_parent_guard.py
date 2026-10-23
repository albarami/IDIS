"""F1 parent-mutation guards for the Linux Poppler child environment.

The parent process must stay byte-identical across a public in-process OCR
call. Applying the child environment is legal only inside a governed worker
child, and ``apply_to_process`` has no default.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest

from idis.parsers import poppler_linux_runtime
from idis.parsers.poppler_linux_runtime import (
    PINNED_POPPLER_LINUX_LIBRARY_NAME,
    PinnedLinuxPopplerRuntime,
    _identity_sha256,
    install_poppler_child_environment,
)
from idis.parsers.poppler_runtime import PINNED_POPPLER_VERSION, PopplerRuntimeBindingError
from idis.services import ocr_health


def _environ_snapshot() -> tuple[tuple[str, str], ...]:
    return tuple((key, os.environ[key]) for key in sorted(os.environ))


def _restore_process(cwd: str, environ: tuple[tuple[str, str], ...]) -> None:
    os.chdir(cwd)
    current = set(os.environ)
    wanted = dict(environ)
    for key in current - set(wanted):
        os.environ.pop(key, None)
    for key, value in wanted.items():
        os.environ[key] = value


def _stub_linux_binding(tmp_path: Path) -> PinnedLinuxPopplerRuntime:
    prefix = (tmp_path / "prefix").resolve()
    source_sha256 = "a" * 64
    recipe_sha256 = "b" * 64
    pdftoppm_sha256 = "c" * 64
    pdfinfo_sha256 = "d" * 64
    library_sha256 = "e" * 64
    rpath = str(prefix / "lib")
    digest = _identity_sha256(
        version=PINNED_POPPLER_VERSION,
        source_sha256=source_sha256,
        recipe_sha256=recipe_sha256,
        prefix=str(prefix),
        pdftoppm_sha256=pdftoppm_sha256,
        pdfinfo_sha256=pdfinfo_sha256,
        library_sha256=library_sha256,
        rpath=rpath,
    )
    return PinnedLinuxPopplerRuntime(
        version=PINNED_POPPLER_VERSION,
        runtime_identity_sha256=digest,
        pdftoppm_sha256=pdftoppm_sha256,
        pdfinfo_sha256=pdfinfo_sha256,
        poppler_libpoppler_sha256=library_sha256,
        poppler_source_sha256=source_sha256,
        poppler_builder_recipe_sha256=recipe_sha256,
        prefix=str(prefix),
        rpath=rpath,
        _pdftoppm_path=prefix / "bin" / "pdftoppm",
        _pdfinfo_path=prefix / "bin" / "pdfinfo",
        _poppler_library_path=prefix / "lib" / PINNED_POPPLER_LINUX_LIBRARY_NAME,
    )


class _InlineProcess:
    """Run the worker target in this process so a parent mutation is visible."""

    def __init__(self, *, target: object, args: tuple[object, ...]) -> None:
        self._target = target
        self._args = args
        self.pid = 0
        self.exitcode = 0

    def start(self) -> None:
        callable_target = self._target
        assert callable(callable_target)
        callable_target(*self._args)

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def kill(self) -> None:
        return None

    def close(self) -> None:
        return None


def _install_fake_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf2image = ModuleType("pdf2image")

    def convert_from_path(*_args: object, **_kwargs: object) -> list[object]:
        return []

    pdf2image.convert_from_path = convert_from_path  # type: ignore[attr-defined]
    exceptions = ModuleType("pdf2image.exceptions")
    exceptions.PDFInfoNotInstalledError = type("PDFInfoNotInstalledError", (Exception,), {})  # type: ignore[attr-defined]
    exceptions.PDFPopplerTimeoutError = type("PDFPopplerTimeoutError", (Exception,), {})  # type: ignore[attr-defined]
    pytesseract = ModuleType("pytesseract")
    pytesseract.Output = type("Output", (), {"DICT": "dict"})  # type: ignore[attr-defined]
    pytesseract.TesseractError = type("TesseractError", (Exception,), {})  # type: ignore[attr-defined]
    pytesseract.TesseractNotFoundError = type("TesseractNotFoundError", (Exception,), {})  # type: ignore[attr-defined]
    pytesseract.image_to_data = lambda *_args, **_kwargs: {}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdf2image", pdf2image)
    monkeypatch.setitem(sys.modules, "pdf2image.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)


def inject_inline_governed_child_seam(
    monkeypatch: pytest.MonkeyPatch,
    ocr_module: object,
) -> None:
    """Let an inline worker body run without mutating this process.

    The production guard refuses ``apply_to_process=True`` unless this process
    has a multiprocessing parent, the worker-entry flag, and a recorded pid
    equal to this process. These tests watch threads and image closes
    in-process, so they cannot move into a real child. The seam sets those
    halves through monkeypatch, then replaces the installer so the mapping
    is not applied here.
    """

    import multiprocessing

    class _InlineBinding:
        binary_dir = Path("/opt/idis-native/poppler-25.12.0/bin")

    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    monkeypatch.setattr(poppler_linux_runtime, "_governed_worker_child", True)
    monkeypatch.setattr(poppler_linux_runtime, "_governed_worker_child_pid", os.getpid())

    def resolve(**_kwargs: object) -> _InlineBinding:
        return _InlineBinding()

    def verify(_binding: object, **_kwargs: object) -> None:
        return None

    def install(_binding: object, *, apply_to_process: bool) -> None:
        assert apply_to_process is True
        assert poppler_linux_runtime._in_governed_worker_child() is True

    monkeypatch.setattr(ocr_module, "resolve_pinned_poppler_runtime", resolve)
    monkeypatch.setattr(ocr_module, "verify_governed_poppler_runtime", verify)
    monkeypatch.setattr(ocr_module, "verify_pinned_poppler_runtime", verify)
    monkeypatch.setattr(ocr_module, "install_poppler_child_environment", install)


@pytest.fixture(autouse=True)
def _restore_governed_worker_marker() -> Iterator[None]:
    """Restore the governed-worker marker after each test in this module."""

    yield
    poppler_linux_runtime._governed_worker_child = False
    poppler_linux_runtime._governed_worker_child_pid = None


def test_public_in_process_ocr_call_does_not_mutate_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public OCR entry forced in-process must not apply the child environment."""

    from idis.parsers import ocr

    binding = _stub_linux_binding(tmp_path)
    source = tmp_path / "page.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(ocr, "verify_governed_poppler_runtime", lambda *_a, **_k: None)
    monkeypatch.setattr(ocr.mp, "Process", _InlineProcess)
    _install_fake_renderer(monkeypatch)
    cwd = os.getcwd()
    environ = _environ_snapshot()
    adapter = ocr.TesseractOcrAdapter(poppler_runtime=binding)
    with contextlib.suppress(Exception):
        adapter.extract_pdf_file(source, max_pages=1, timeout_seconds=2)
    mutated = os.getcwd() != cwd or _environ_snapshot() != environ
    _restore_process(cwd, environ)
    assert mutated is False


def test_apply_to_process_true_in_parent_is_forbidden(tmp_path: Path) -> None:
    binding = _stub_linux_binding(tmp_path)
    cwd = os.getcwd()
    environ = _environ_snapshot()
    try:
        with pytest.raises(PopplerRuntimeBindingError) as error:
            install_poppler_child_environment(binding, apply_to_process=True)
        assert error.value.code == "POPPLER_RUNTIME_PARENT_MUTATION_FORBIDDEN"
        assert os.getcwd() == cwd
        assert _environ_snapshot() == environ
    finally:
        _restore_process(cwd, environ)


def test_apply_to_process_has_no_default() -> None:
    signature = inspect.signature(install_poppler_child_environment)
    parameter = signature.parameters["apply_to_process"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_linux_health_stub_leaves_parent_process_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _stub_linux_binding(tmp_path)
    monkeypatch.setattr(ocr_health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ocr_health,
        "resolve_pinned_poppler_runtime",
        lambda **_kwargs: binding,
    )
    cwd = os.getcwd()
    environ = _environ_snapshot()
    try:
        ocr_health.check_ocr_health(
            env={"IDIS_OCR_ENABLED": "1"},
            binary_resolver=lambda _name: "/opt/bin/tesseract",
            module_probe=lambda _name: True,
            language_probe=lambda _language: True,
            runtime_probe=lambda: None,
            poppler_runtime_probe=None,
        )
    finally:
        mutated = os.getcwd() != cwd or _environ_snapshot() != environ
        _restore_process(cwd, environ)
    assert mutated is False


_SPAWN_CHILD_TIMEOUT_SECONDS = 60.0


class _MarkerReportQueue(Protocol):
    def put(self, item: bool) -> None:
        """Accept one governed-child report."""


def _spawned_child_marks_and_reports(queue: _MarkerReportQueue) -> None:
    """Mark this process and report whether the governed-child guard holds."""

    from idis.parsers.poppler_linux_runtime import (
        _in_governed_worker_child,
        mark_governed_worker_child,
    )

    mark_governed_worker_child()
    queue.put(_in_governed_worker_child())


def test_inline_seam_engages_governed_child_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The inline seam engages the guard only while that test is running."""

    from idis.parsers import ocr
    from idis.parsers.poppler_linux_runtime import _in_governed_worker_child

    inject_inline_governed_child_seam(monkeypatch, ocr)
    assert _in_governed_worker_child() is True


def test_governed_worker_marker_does_not_leak_after_inline_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later test must not inherit the inline seam's worker marker."""

    import multiprocessing

    from idis.parsers.poppler_linux_runtime import _in_governed_worker_child

    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    assert _in_governed_worker_child() is False


def test_mark_without_multiprocessing_parent_records_nothing() -> None:
    """Calling mark in a top-level process must record nothing."""

    import multiprocessing

    from idis.parsers import poppler_linux_runtime

    assert multiprocessing.parent_process() is None
    poppler_linux_runtime.mark_governed_worker_child()
    assert poppler_linux_runtime._in_governed_worker_child() is False
    assert poppler_linux_runtime._governed_worker_child is False
    assert poppler_linux_runtime._governed_worker_child_pid is None


def test_spawn_child_mark_is_visible_only_in_that_child() -> None:
    """A spawn child that marks itself is governed; this process stays unmarked."""

    import multiprocessing

    from idis.parsers import poppler_linux_runtime

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_spawned_child_marks_and_reports, args=(queue,))
    process.start()
    try:
        child_marked = queue.get(timeout=_SPAWN_CHILD_TIMEOUT_SECONDS)
    finally:
        process.join(timeout=_SPAWN_CHILD_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(timeout=_SPAWN_CHILD_TIMEOUT_SECONDS)
    exitcode = process.exitcode
    process.close()
    assert exitcode == 0
    assert child_marked is True
    assert poppler_linux_runtime._in_governed_worker_child() is False
    assert poppler_linux_runtime._governed_worker_child is False
    assert poppler_linux_runtime._governed_worker_child_pid is None
