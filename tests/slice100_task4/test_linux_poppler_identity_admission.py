"""Linux Poppler identity, worker-environment, and canonical-rejection tests.

The loader-level negative (a correct-class planted library is not loaded by a
real ``ldd``) is not in this module. It lives in
``test_linux_poppler_loader_negative.py`` and is collected only on a Linux host
that has the governed prefix.
"""

from __future__ import annotations

import ast
import hashlib
import os
import struct
from pathlib import Path
from typing import Any

import pytest

from idis.parsers.base import ParseResult
from idis.parsers.ocr import (
    MAX_OCR_IMAGE_PIXELS,
    OCR_DPI_LADDER,
    OCR_DPI_LADDER_VERSION,
    OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    PINNED_POPPLER_VERSION,
    POPPLER_EXACT_COMPARISON_MODE,
    POPPLER_EXPECTED_DIMENSION_POLICY_ID,
    POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
    TesseractOcrAdapter,
)
from idis.runtime.governance_envelopes import canonicalize_json

_SOURCE_SHA256 = "c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48"
_RECIPE_SHA256 = "e0ff366be570c7e30c5d354a51af83f76b6c2d05ee2bce73a43e68db8f9ae7fd"
_SOURCE_SIZE = 1992456
_SCHEMA = "poppler-linux-prefix-identity-v1"
_MANIFEST_RELATIVE = "share/idis/poppler-linux-prefix-identity-v1.json"
_INTERPRETER = "/lib64/ld-linux-x86-64.so.2"
_WINDOWS_IDENTITY = "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"
_GOLDEN_TRIPLE_SHA256 = "ec864d990f483f8339161f4ee9964d4c8dcf889d5ca0aad69061c24e964bc376"
_ELF_BINARIES = (
    "bin/pdfattach",
    "bin/pdfdetach",
    "bin/pdffonts",
    "bin/pdfimages",
    "bin/pdfinfo",
    "bin/pdfseparate",
    "bin/pdftocairo",
    "bin/pdftohtml",
    "bin/pdftoppm",
    "bin/pdftops",
    "bin/pdftotext",
    "bin/pdfunite",
)
_LIBRARY = "lib/libpoppler.so.155.0.0"
_CLOSED_FILES = (
    *_ELF_BINARIES,
    _LIBRARY,
    "lib/pkgconfig/poppler.pc",
    "share/man/man1/pdfattach.1",
    "share/man/man1/pdfdetach.1",
    "share/man/man1/pdffonts.1",
    "share/man/man1/pdfimages.1",
    "share/man/man1/pdfinfo.1",
    "share/man/man1/pdfseparate.1",
    "share/man/man1/pdftocairo.1",
    "share/man/man1/pdftohtml.1",
    "share/man/man1/pdftoppm.1",
    "share/man/man1/pdftops.1",
    "share/man/man1/pdftotext.1",
    "share/man/man1/pdfunite.1",
)
_CLOSED_SYMLINKS = (
    ("lib/libpoppler.so", "libpoppler.so.155"),
    ("lib/libpoppler.so.155", "libpoppler.so.155.0.0"),
)
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3
_DT_NULL = 0
_DT_STRTAB = 5
_DT_STRSZ = 10
_DT_RPATH = 15
_DT_RUNPATH = 29


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic_elf(
    *,
    rpath: str,
    runpath: str | None = None,
    interpreter: str | None = _INTERPRETER,
    origin: bool = False,
) -> bytes:
    """Build a minimal ELF64 whose PT_DYNAMIC carries the requested RPATH."""

    rpath_text = f"{rpath}/$ORIGIN" if origin else rpath
    header_size = 64
    program_count = 3 if interpreter is not None else 2
    cursor = header_size + (56 * program_count)
    interp_offset = cursor
    interp_bytes = b""
    if interpreter is not None:
        interp_bytes = interpreter.encode("ascii") + b"\x00"
        cursor += len(interp_bytes)
    strtab_offset = cursor
    strtab = b"\x00" + rpath_text.encode("ascii") + b"\x00"
    rpath_index = 1
    runpath_index = 0
    if runpath is not None:
        runpath_index = len(strtab)
        strtab += runpath.encode("ascii") + b"\x00"
    cursor += len(strtab)
    dynamic_offset = cursor
    entries: list[tuple[int, int]] = [
        (_DT_STRTAB, strtab_offset),
        (_DT_STRSZ, len(strtab)),
        (_DT_RPATH, rpath_index),
    ]
    if runpath is not None:
        entries.append((_DT_RUNPATH, runpath_index))
    entries.append((_DT_NULL, 0))
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    file_size = dynamic_offset + len(dynamic)
    return _pack_elf(
        file_size=file_size,
        program_count=program_count,
        interp_offset=interp_offset,
        interp_size=len(interp_bytes),
        dynamic_offset=dynamic_offset,
        dynamic_size=len(dynamic),
        body=interp_bytes + strtab + dynamic,
    )


def _pack_elf(
    *,
    file_size: int,
    program_count: int,
    interp_offset: int,
    interp_size: int,
    dynamic_offset: int,
    dynamic_size: int,
    body: bytes,
) -> bytes:
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF\x02\x01\x01" + b"\x00" * 9,
        3,
        62,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        program_count,
        0,
        0,
        0,
    )
    load = _phdr(_PT_LOAD, 5, 0, 0, file_size)
    dynamic = _phdr(_PT_DYNAMIC, 4, dynamic_offset, dynamic_offset, dynamic_size)
    programs = load
    if interp_size:
        programs += _phdr(_PT_INTERP, 4, interp_offset, interp_offset, interp_size)
    programs += dynamic
    blob = header + programs + body
    if len(blob) != file_size:
        raise RuntimeError("synthetic ELF size drifted")
    return blob


def _phdr(kind: int, flags: int, offset: int, vaddr: int, size: int) -> bytes:
    return struct.pack("<IIQQQQQQ", kind, flags, offset, vaddr, vaddr, size, size, 8)


def write_linux_prefix(
    root: Path,
    *,
    source_sha256: str = _SOURCE_SHA256,
    recipe_sha256: str = _RECIPE_SHA256,
    rpath: str | None = None,
    runpath: str | None = None,
    origin: bool = False,
    interpreter: str | None = _INTERPRETER,
) -> Path:
    """Write a closed synthetic prefix whose manifest matches its bytes."""

    prefix = root / "poppler-prefix"
    prefix.mkdir(parents=True)
    resolved = prefix.resolve()
    lib_dir = str(resolved / "lib")
    rpath_text = lib_dir if rpath is None else rpath
    payloads: dict[str, bytes] = {}
    for relative in _CLOSED_FILES:
        if relative in _ELF_BINARIES or relative == _LIBRARY:
            payloads[relative] = synthetic_elf(
                rpath=rpath_text,
                runpath=runpath,
                interpreter=None if relative == _LIBRARY else interpreter,
                origin=origin,
            )
        else:
            payloads[relative] = f"synthetic:{relative}".encode("ascii")
    for relative, payload in payloads.items():
        path = prefix / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    for relative, target in _CLOSED_SYMLINKS:
        link = prefix / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link)
    files = [
        {
            "elf": relative in _ELF_BINARIES or relative == _LIBRARY,
            "path": relative,
            "sha256": _sha256(payloads[relative]),
        }
        for relative in _CLOSED_FILES
    ]
    manifest = {
        "files": files,
        "poppler_builder_recipe_sha256": recipe_sha256,
        "poppler_source_sha256": source_sha256,
        "poppler_source_size_bytes": _SOURCE_SIZE,
        "poppler_version": "25.12.0",
        "prefix": str(resolved),
        "rpath": rpath_text,
        "schema_version": _SCHEMA,
        "symlinks": [{"path": path, "target": target} for path, target in _CLOSED_SYMLINKS],
    }
    manifest_path = prefix / _MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return prefix


def _version_probe(_path: Path) -> str:
    return "25.12.0"


def _resolve_linux(prefix: Path) -> Any:
    from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime

    return resolve_pinned_poppler_runtime(
        platform_name="Linux",
        linux_prefix=prefix,
        version_probe=_version_probe,
    )


def test_extra_prefix_file_and_missing_entry_are_identity_invalid(tmp_path: Path) -> None:
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    extra = write_linux_prefix(tmp_path / "extra")
    planted = extra / "lib" / "libjpeg.so.62"
    planted.write_bytes(b"planted-libjpeg")
    with pytest.raises(PopplerRuntimeBindingError) as extra_error:
        _resolve_linux(extra)
    assert extra_error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"

    missing = write_linux_prefix(tmp_path / "missing")
    (missing / "share" / "man" / "man1" / "pdfattach.1").unlink()
    with pytest.raises(PopplerRuntimeBindingError) as missing_error:
        _resolve_linux(missing)
    assert missing_error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"


def test_child_environment_mapping_and_worker_calls(tmp_path: Path) -> None:
    from idis.parsers.poppler_linux_runtime import install_poppler_child_environment

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    before_cwd = Path.cwd()
    before_library_path = os.environ.get("LD_LIBRARY_PATH")
    mapping = install_poppler_child_environment(binding, apply_to_process=False)
    assert mapping is not None
    assert mapping["LD_LIBRARY_PATH"] == str(binding.poppler_library_path.parent)
    assert mapping["LD_LIBRARY_PATH"].split(os.pathsep) == [mapping["LD_LIBRARY_PATH"]]
    assert "LD_PRELOAD" not in mapping
    assert "LD_AUDIT" not in mapping
    assert mapping["PATH"] == "/usr/bin:/bin"
    assert mapping["LC_ALL"] == "C.UTF-8"
    assert mapping["HOME"] == mapping["XDG_CACHE_HOME"]
    assert Path(mapping["HOME"]).name == "idis-poppler-fontconfig"
    assert Path(mapping["HOME"]).is_dir()
    assert Path.cwd() == before_cwd
    assert os.environ.get("LD_LIBRARY_PATH") == before_library_path
    _assert_workers_call_helper()


def _assert_workers_call_helper() -> None:
    root = Path(__file__).resolve().parents[2]
    expected = {
        root / "src" / "idis" / "parsers" / "pdf.py": {
            "_watermark_page_pre_ocr_worker",
            "_watermark_page_runtime_worker",
        },
        root / "src" / "idis" / "parsers" / "ocr.py": {
            "_ocr_pdf_file_render_admission_batches",
            "_ocr_pdf_file_batches",
            "_tesseract_ocr_worker",
        },
    }
    for path, names in expected.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in names:
                calls = [
                    call.func.id
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                ]
                if "install_poppler_child_environment" in calls:
                    found.add(node.name)
        assert found == names


def test_windows_type_on_linux_is_identity_invalid(tmp_path: Path) -> None:
    from idis.parsers.poppler_runtime import (
        PopplerRuntimeBindingError,
        resolve_pinned_poppler_runtime,
        verify_governed_poppler_runtime,
    )
    from tests.slice100_task4.test_poppler_runtime_binding import _binding

    windows_binding = _binding(tmp_path)
    with pytest.raises(PopplerRuntimeBindingError) as error:
        verify_governed_poppler_runtime(windows_binding, platform_name="Linux")
    assert error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"
    with pytest.raises(PopplerRuntimeBindingError) as resolved:
        resolve_pinned_poppler_runtime(
            platform_name="Linux",
            linux_prefix=tmp_path / "absent",
            binary_resolver=lambda _name: str(windows_binding.pdftoppm_path),
            version_probe=_version_probe,
        )
    assert resolved.value.code == "POPPLER_RUNTIME_LAYOUT_UNLISTED"


def test_linux_binding_crosses_pickle_boundary(tmp_path: Path) -> None:
    import pickle

    from idis.parsers.poppler_linux_runtime import PinnedLinuxPopplerRuntime

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    restored = pickle.loads(pickle.dumps(binding))
    assert type(restored) is PinnedLinuxPopplerRuntime
    assert restored.runtime_identity_sha256 == binding.runtime_identity_sha256


def test_tesseract_adapter_accepts_linux_binding(tmp_path: Path) -> None:
    binding = _resolve_linux(write_linux_prefix(tmp_path))
    adapter = TesseractOcrAdapter(poppler_runtime=binding)
    assert type(adapter.poppler_runtime) is type(binding)
    assert adapter.poppler_runtime is binding


def test_canonical_admissions_reject_linux_binding_passed_in_and_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.parsers.ocr import OcrError
    from idis.parsers.pdf import build_pdf_ocr_render_admissions

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    with pytest.raises(OcrError, match="OCR Poppler runtime binding is invalid"):
        build_pdf_ocr_render_admissions(
            data=b"%PDF-1.4",
            page_numbers=(1,),
            poppler_runtime=binding,
        )
    monkeypatch.setattr(
        "idis.parsers.pdf.resolve_pinned_poppler_runtime",
        lambda **_kwargs: binding,
    )
    with pytest.raises(OcrError, match="OCR Poppler runtime binding is invalid"):
        build_pdf_ocr_render_admissions(
            data=b"%PDF-1.4",
            page_numbers=(1,),
            poppler_runtime=None,
        )


def test_reconciliation_rejects_linux_domain_digest() -> None:
    from idis.evaluation.real_example_terminal_reconciliation import (
        TerminalIngestionReconciliationError,
        _validate_v2_render_contract,
    )

    width = 10
    contract = {
        "schema_version": OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        "ladder_version": OCR_DPI_LADDER_VERSION,
        "configured_dpi": OCR_DPI_LADDER[0],
        "chosen_dpi": OCR_DPI_LADDER[0],
        "projected_width": width,
        "projected_height": width,
        "projected_pixel_count": width * width,
        "expected_render_width": width,
        "expected_render_height": width,
        "expected_render_pixel_count": width * width,
        "max_pixel_count": MAX_OCR_IMAGE_PIXELS,
        "poppler_version": PINNED_POPPLER_VERSION,
        "poppler_runtime_identity_sha256": "ab" * 32,
        "expected_dimension_policy_id": POPPLER_EXPECTED_DIMENSION_POLICY_ID,
        "expected_dimension_policy_sha256": POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
        "comparison_mode": POPPLER_EXACT_COMPARISON_MODE,
        "tolerance_pixels": 0,
        "tolerance_used": False,
    }
    with pytest.raises(TerminalIngestionReconciliationError, match="governed ladder"):
        _validate_v2_render_contract(contract, observed=False)


def test_ocr_health_on_linux_uses_prefix_binaries_not_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    import idis.services.ocr_health as health
    from idis.services.ocr_health import OcrHealthStatus, check_ocr_health

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    observed: dict[str, object] = {}

    def resolve(**kwargs: object) -> object:
        observed["kwargs"] = kwargs
        return binding

    monkeypatch.setattr(health, "resolve_pinned_poppler_runtime", resolve)
    which_calls: list[str] = []

    def which(name: str) -> str | None:
        which_calls.append(name)
        return f"/usr/bin/{name}"

    runs: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        runs.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1", "IDIS_OCR_ADAPTER": "tesseract"},
        binary_resolver=which,
        module_probe=lambda _name: True,
        language_probe=lambda _language: True,
        runtime_probe=lambda: None,
    )
    assert result.status == OcrHealthStatus.HEALTHY
    assert "binary_resolver" not in observed["kwargs"]
    assert "pdfinfo" not in which_calls
    assert "pdftoppm" not in which_calls
    assert "pdfinfo.exe" not in which_calls
    assert "pdftoppm.exe" not in which_calls
    commands = [tuple(args) for args, _kwargs in runs]
    assert commands == [
        (str(binding.pdfinfo_path), "-v"),
        (str(binding.pdftoppm_path), "-v"),
    ]
    for _args, kwargs in runs:
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["LD_LIBRARY_PATH"] == str(binding.poppler_library_path.parent)
        assert "LD_PRELOAD" not in env
        assert "LD_AUDIT" not in env
        assert kwargs["cwd"] == env["HOME"]


def test_golden_windows_parser_identity_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.parsers.poppler_runtime import (
        POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION,
        PinnedPopplerRuntime,
    )
    from idis.services.ingestion.service import IngestionService

    triple = [
        "poppler-runtime-identity-v1",
        "25.12.0",
        _WINDOWS_IDENTITY,
    ]
    encoded = canonicalize_json(triple)
    assert len(encoded) == 108
    assert hashlib.sha256(encoded).hexdigest() == _GOLDEN_TRIPLE_SHA256
    windows = PinnedPopplerRuntime(
        version="25.12.0",
        pdftoppm_sha256="0955bd692f0bcf4ad59d94bbd2cf2d745eceac451d1566ea2e102316a714ae56",
        poppler_dll_sha256="a94e2d23c1b6d67417b8fc7dc8585b33d0d2ab9538417452f796a857c8f21af5",
        runtime_identity_sha256=_WINDOWS_IDENTITY,
        _pdftoppm_path=(tmp_path / "pdftoppm.exe").resolve(),
        _pdfinfo_path=(tmp_path / "pdfinfo.exe").resolve(),
        _poppler_library_path=(tmp_path / "poppler.dll").resolve(),
        _platform_name="Windows",
    )
    captured: list[object] = []
    real_canonicalize = canonicalize_json

    def capture(value: object) -> bytes:
        captured.append(value)
        return real_canonicalize(value)

    from idis.parsers import poppler_runtime as poppler_runtime_module

    original_resolve = poppler_runtime_module.resolve_pinned_poppler_runtime
    monkeypatch.setattr("idis.services.ingestion.service.canonicalize_json", capture)
    monkeypatch.setattr(
        poppler_runtime_module,
        "resolve_pinned_poppler_runtime",
        lambda **_kwargs: windows,
    )
    parse_result = ParseResult(
        doc_type="PDF",
        success=True,
        metadata={
            "watermark_page_classifications": [
                {
                    "classification_phase": "pre_ocr",
                    "full_resolution_ocr_attempted": False,
                }
            ]
        },
    )
    identity = IngestionService._watermark_pre_ocr_parser_identity(object(), parse_result)  # type: ignore[arg-type]
    assert identity is not None
    assert captured
    poppler_triple = captured[0][4]  # type: ignore[index]
    assert poppler_triple[0] == POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION
    assert canonicalize_json(poppler_triple) == encoded

    monkeypatch.setattr(
        poppler_runtime_module,
        "resolve_pinned_poppler_runtime",
        original_resolve,
    )
    linux = _resolve_linux(write_linux_prefix(tmp_path / "linux"))
    assert linux.runtime_identity_sha256 != _WINDOWS_IDENTITY
    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        lambda **_kwargs: linux,
    )
    captured.clear()
    linux_identity = IngestionService._watermark_pre_ocr_parser_identity(object(), parse_result)  # type: ignore[arg-type]
    assert linux_identity is not None
    linux_triple = captured[0][4]  # type: ignore[index]
    assert linux_triple[0] == _SCHEMA
    assert linux_triple[2] != _WINDOWS_IDENTITY
    assert canonicalize_json(linux_triple) != encoded


def test_acceptance_v1_full_worker_rejects_linux_binding_before_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.pipeline.worker import WorkerLeaseConfig, build_postgres_claimed_run_runtime
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile
    from tests.slice100_task2.test_worker_strict_preflight_admission import _claimed_run

    binding = _resolve_linux(write_linux_prefix(tmp_path / "prefix"))
    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        lambda **_kwargs: binding,
    )
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    rendered: list[str] = []
    monkeypatch.setattr(
        "pdf2image.pdf2image.convert_from_path",
        lambda *_args, **_kwargs: rendered.append("render"),
        raising=False,
    )
    profile = _reviewed_profile(tmp_path, "worker")
    opened: list[str] = []

    def connection_factory() -> object:
        opened.append("open")
        raise AssertionError("defence-in-depth rejection must precede the claim connection")

    with pytest.raises(RuntimeError, match="POPPLER_RUNTIME_NONCANONICAL"):
        build_postgres_claimed_run_runtime(
            tenant_id="11111111-1111-4111-8111-111111111111",
            run_data=_claimed_run(),
            lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
            audit_sink=object(),
            connection_factory=connection_factory,
            strict_profile=profile,
        )
    assert opened == []
    assert rendered == []


def test_no_production_call_site_passes_linux_prefix() -> None:
    root = Path(__file__).resolve().parents[2] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "linux_prefix":
                        offenders.append(f"{path}:{node.lineno}")
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = [arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)]
                if "linux_prefix" in names and node.name != "resolve_pinned_poppler_runtime":
                    offenders.append(f"{path}:{node.lineno}:{node.name}")
    assert offenders == []


def test_acceptance_launcher_rejects_linux_binding_before_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.runtime.strict_launcher as launcher

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        lambda **_kwargs: binding,
    )
    admitted: list[str] = []
    monkeypatch.setattr(
        launcher,
        "run_admitted_runtime_role",
        lambda **_kwargs: admitted.append("admitted") or 0,
    )
    with pytest.raises(launcher.StrictLauncherError, match="POPPLER_RUNTIME_NONCANONICAL"):
        launcher._run_worker(
            object(),
            type("Args", (), {"poll_seconds": 1.0, "watch": False, "pid_file": tmp_path / "pid"})(),
            type("Profile", (), {"generation": "acceptance-v1"})(),
        )
    assert admitted == []


def test_task4_gate_rejects_linux_binding_before_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.evaluation.real_example_gate as gate

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        lambda **_kwargs: binding,
    )
    health_calls: list[str] = []
    monkeypatch.setattr(gate, "check_ocr_health", lambda **_kwargs: health_calls.append("health"))
    with pytest.raises(gate.DurableIngestionGateError, match="POPPLER_RUNTIME_NONCANONICAL"):
        gate._require_task4_local_runtime_readiness(env={})
    assert health_calls == []


class _PayloadQueue:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def put(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


def test_strict_pdf_parse_accepts_linux_adapter_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Linux adapter binding must pass the strict PDF parse type check."""

    from idis.parsers import pdf as pdf_module
    from idis.parsers.ocr import OcrConfig, OcrError

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    seen: list[object] = []

    def build(**kwargs: object) -> tuple[()]:
        seen.append(kwargs["poppler_runtime"])
        raise OcrError("stop-after-type-check")

    monkeypatch.setattr(pdf_module, "_build_pdf_ocr_render_admissions", build)

    class _Adapter:
        dpi = 200

        def __init__(self, runtime: object) -> None:
            self.poppler_runtime = runtime

        def extract_pdf_file_with_render_admissions(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> list[object]:
            return []

    pdf_module._parse_pdf_with_ocr(
        data=b"%PDF-1.4",
        source_path=None,
        page_count=1,
        ocr_config=OcrConfig(
            enabled=True,
            adapter=_Adapter(binding),  # type: ignore[arg-type]
            max_pages=1,
            timeout_seconds=5,
        ),
        required_page_numbers={1},
        native_spans=[],
        native_text_length=0,
        native_text_page_count=0,
        blank_page_count=0,
    )
    assert seen == [binding]


def test_strict_pdf_parse_rejects_non_exact_poppler_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.parsers import pdf as pdf_module
    from idis.parsers.ocr import OcrConfig

    seen: list[object] = []

    def build(**kwargs: object) -> tuple[()]:
        seen.append(kwargs["poppler_runtime"])
        return ()

    monkeypatch.setattr(pdf_module, "_build_pdf_ocr_render_admissions", build)

    class _Adapter:
        dpi = 200
        poppler_runtime = object()

        def extract_pdf_file_with_render_admissions(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> list[object]:
            return []

    result = pdf_module._parse_pdf_with_ocr(
        data=b"%PDF-1.4",
        source_path=None,
        page_count=1,
        ocr_config=OcrConfig(
            enabled=True,
            adapter=_Adapter(),  # type: ignore[arg-type]
            max_pages=1,
            timeout_seconds=5,
        ),
        required_page_numbers={1},
        native_spans=[],
        native_text_length=0,
        native_text_page_count=0,
        blank_page_count=0,
    )
    assert seen == []
    assert result.success is False


def test_strict_pdf_parse_rejects_pinned_runtime_subclasses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subclass of either pinned runtime is not an exact admitted type."""

    from idis.parsers import pdf as pdf_module
    from idis.parsers.ocr import OcrConfig
    from idis.parsers.poppler_linux_runtime import PinnedLinuxPopplerRuntime
    from idis.parsers.poppler_runtime import PinnedPopplerRuntime

    class _WindowsSubclass(PinnedPopplerRuntime):
        __slots__ = ()

    class _LinuxSubclass(PinnedLinuxPopplerRuntime):
        __slots__ = ()

    windows_binding = object.__new__(_WindowsSubclass)
    linux_binding = object.__new__(_LinuxSubclass)
    assert isinstance(windows_binding, PinnedPopplerRuntime)
    assert type(windows_binding) is not PinnedPopplerRuntime
    assert isinstance(linux_binding, PinnedLinuxPopplerRuntime)
    assert type(linux_binding) is not PinnedLinuxPopplerRuntime
    seen: list[object] = []

    def build(**kwargs: object) -> tuple[()]:
        seen.append(kwargs["poppler_runtime"])
        return ()

    monkeypatch.setattr(pdf_module, "_build_pdf_ocr_render_admissions", build)
    observed: list[str] = []
    original_error = pdf_module.OcrError

    class _ObservedOcrError(original_error):
        def __init__(self, message: str = "", *args: object, **kwargs: object) -> None:
            super().__init__(message, *args, **kwargs)
            observed.append(message)

    monkeypatch.setattr(pdf_module, "OcrError", _ObservedOcrError)

    class _Adapter:
        dpi = 200

        def __init__(self, runtime: object) -> None:
            self.poppler_runtime = runtime

        def extract_pdf_file_with_render_admissions(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> list[object]:
            return []

    for binding in (windows_binding, linux_binding):
        seen.clear()
        observed.clear()
        result = pdf_module._parse_pdf_with_ocr(
            data=b"%PDF-1.4",
            source_path=None,
            page_count=1,
            ocr_config=OcrConfig(
                enabled=True,
                adapter=_Adapter(binding),  # type: ignore[arg-type]
                max_pages=1,
                timeout_seconds=5,
            ),
            required_page_numbers={1},
            native_spans=[],
            native_text_length=0,
            native_text_page_count=0,
            blank_page_count=0,
        )
        assert seen == []
        assert observed == ["OCR Poppler runtime binding is invalid"]
        assert result.success is False


def _render_with_none_binding(
    worker_name: str,
    *,
    source: str,
    deadline: float,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[object], _PayloadQueue]:
    import pdf2image

    from idis.parsers import pdf as pdf_module

    renders: list[object] = []

    def convert(*_args: object, **kwargs: object) -> list[object]:
        renders.append(kwargs.get("poppler_path"))
        raise RuntimeError("stop-after-poppler-path")

    monkeypatch.setattr(pdf2image, "convert_from_path", convert)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", convert)
    queue = _PayloadQueue()
    worker = getattr(pdf_module, worker_name)
    runtime_config = type("Config", (), {"policy": type("Policy", (), {"render_dpi": 72})()})()
    if worker_name == "_watermark_page_pre_ocr_worker":
        worker(None, source, "ab" * 32, (1,), runtime_config, None, deadline, queue)
    else:
        worker(
            None,
            source,
            "ab" * 32,
            ((1, 0, 0, 0, "no_consensus", "cd" * 32),),
            {},
            runtime_config,
            None,
            deadline,
            queue,
        )
    return renders, queue


def test_linux_none_binding_resolves_before_watermark_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux, None must not render with apt Poppler from PATH."""

    import platform

    from idis.parsers import pdf as pdf_module

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    resolved: list[str] = []
    installed: list[object] = []
    monkeypatch.setattr(
        pdf_module,
        "resolve_pinned_poppler_runtime",
        lambda **_kwargs: resolved.append("resolve") or binding,
    )
    monkeypatch.setattr(pdf_module, "verify_governed_poppler_runtime", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pdf_module,
        "install_poppler_child_environment",
        lambda runtime, **_kwargs: installed.append(runtime),
    )
    deadline = __import__("time").monotonic() + 30
    source = str(tmp_path / "page.pdf")
    for worker_name in (
        "_watermark_page_pre_ocr_worker",
        "_watermark_page_runtime_worker",
    ):
        renders, _queue = _render_with_none_binding(
            worker_name,
            source=source,
            deadline=deadline,
            monkeypatch=monkeypatch,
        )
        assert renders == [str(binding.binary_dir)]
    assert resolved
    assert installed == [binding, binding]


def test_linux_resolve_failure_does_not_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import platform

    from idis.parsers import pdf as pdf_module
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    def fail(**_kwargs: object) -> object:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")

    monkeypatch.setattr(pdf_module, "resolve_pinned_poppler_runtime", fail)
    deadline = __import__("time").monotonic() + 30
    source = str(tmp_path / "page.pdf")
    for worker_name in (
        "_watermark_page_pre_ocr_worker",
        "_watermark_page_runtime_worker",
    ):
        renders, queue = _render_with_none_binding(
            worker_name,
            source=source,
            deadline=deadline,
            monkeypatch=monkeypatch,
        )
        assert renders == []
        assert queue.payloads
        assert queue.payloads[0]["status"] in {"unavailable", "failed"}


def _run_none_binding_ocr_file_worker(
    *,
    source: str,
    deadline: float,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[object], _PayloadQueue]:
    import pdf2image

    from idis.parsers import ocr as ocr_module

    renders: list[object] = []

    def convert(*_args: object, **kwargs: object) -> list[object]:
        renders.append(kwargs.get("poppler_path", "MISSING"))
        raise RuntimeError("stop-after-poppler-path")

    monkeypatch.setattr(pdf2image, "convert_from_path", convert)
    queue = _PayloadQueue()
    ocr_module._tesseract_pdf_file_ocr_worker(
        source,
        1,
        72,
        "eng",
        deadline,
        queue,
        poppler_runtime=None,
    )
    return renders, queue


def test_linux_none_ocr_worker_resolves_verifies_and_installs_before_convert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux, a None OCR binding must not render with apt Poppler."""

    import platform

    from idis.parsers import ocr as ocr_module

    binding = _resolve_linux(write_linux_prefix(tmp_path))
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    order: list[str] = []

    def resolve(**_kwargs: object) -> object:
        order.append("resolve")
        return binding

    def verify(runtime: object, **_kwargs: object) -> None:
        order.append("verify")
        assert runtime is binding

    def install(runtime: object, **_kwargs: object) -> None:
        order.append("install")
        assert runtime is binding

    monkeypatch.setattr(ocr_module, "resolve_pinned_poppler_runtime", resolve)
    monkeypatch.setattr(ocr_module, "verify_governed_poppler_runtime", verify)
    monkeypatch.setattr(ocr_module, "install_poppler_child_environment", install)
    deadline = __import__("time").monotonic() + 30
    renders, _queue = _run_none_binding_ocr_file_worker(
        source=str(tmp_path / "page.pdf"),
        deadline=deadline,
        monkeypatch=monkeypatch,
    )
    assert order == ["resolve", "verify", "install"]
    assert renders == [str(binding.binary_dir)]


def test_linux_ocr_worker_resolve_failure_does_not_convert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Linux resolve records OcrUnavailableError and never converts."""

    import platform

    from idis.parsers import ocr as ocr_module
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    messages: list[str] = []
    unavailable = ocr_module.OcrUnavailableError

    class _ObservedUnavailable(unavailable):
        def __init__(self, message: str = "") -> None:
            super().__init__(message)
            messages.append(message)

    monkeypatch.setattr(ocr_module, "OcrUnavailableError", _ObservedUnavailable)

    def fail(**_kwargs: object) -> object:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")

    monkeypatch.setattr(ocr_module, "resolve_pinned_poppler_runtime", fail)
    deadline = __import__("time").monotonic() + 30
    renders, queue = _run_none_binding_ocr_file_worker(
        source=str(tmp_path / "page.pdf"),
        deadline=deadline,
        monkeypatch=monkeypatch,
    )
    assert renders == []
    assert messages == ["OCR Poppler runtime binding failed verification"]
    assert queue.payloads
    assert queue.payloads[0]["status"] == "failed"


def test_windows_none_ocr_worker_omits_poppler_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows None still converts without a poppler_path keyword."""

    import platform

    from idis.parsers import ocr as ocr_module

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    resolved: list[str] = []
    monkeypatch.setattr(
        ocr_module,
        "resolve_pinned_poppler_runtime",
        lambda **_kwargs: resolved.append("resolve"),
    )
    deadline = __import__("time").monotonic() + 30
    renders, _queue = _run_none_binding_ocr_file_worker(
        source=str(tmp_path / "page.pdf"),
        deadline=deadline,
        monkeypatch=monkeypatch,
    )
    assert resolved == []
    assert renders == ["MISSING"]


def test_fontconfig_directory_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.parsers.poppler_linux_runtime import _fontconfig_directory
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "idis-poppler-fontconfig"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PopplerRuntimeBindingError) as error:
        _fontconfig_directory()
    assert error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"


def test_fontconfig_directory_rejects_wrong_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import stat
    from types import SimpleNamespace

    from idis.parsers.poppler_linux_runtime import _fontconfig_directory
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    directory = tmp_path / "idis-poppler-fontconfig"
    directory.mkdir()
    real_lstat = os.lstat

    def fake_lstat(path: object, *args: object, **kwargs: object) -> object:
        info = real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(str(path)).name == "idis-poppler-fontconfig":
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=12345)
        return info

    monkeypatch.setattr(os, "lstat", fake_lstat)
    monkeypatch.setattr(os, "geteuid", lambda: 999, raising=False)
    with pytest.raises(PopplerRuntimeBindingError) as error:
        _fontconfig_directory()
    assert error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"


def test_admit_linux_prefix_uses_file_hasher(tmp_path: Path) -> None:
    from idis.parsers.poppler_linux_runtime import admit_linux_poppler_prefix
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    prefix = write_linux_prefix(tmp_path)

    def hasher(_path: Path) -> str:
        return "0" * 64

    with pytest.raises(PopplerRuntimeBindingError) as error:
        admit_linux_poppler_prefix(
            prefix,
            version_probe=_version_probe,
            file_hasher=hasher,
        )
    assert error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"


def test_runtime_probe_docstring_is_tesseract_only() -> None:
    from idis.services.ocr_health import _build_default_runtime_probe, check_ocr_health

    document = check_ocr_health.__doc__ or ""
    runtime_section = document.split("runtime_probe:", 1)[1].split("command_runner:", 1)[0]
    assert "pdfinfo -v" not in runtime_section
    assert "tesseract --version" in runtime_section
    seen: list[tuple[str, ...]] = []
    probe = _build_default_runtime_probe(lambda args: seen.append(tuple(args)))
    probe()
    assert seen == [("tesseract", "--version")]


def test_loader_negative_required_fails_and_lenient_records_property(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Required mode fails; lenient mode records the non-execution property."""

    import tests.slice100_task4.test_linux_poppler_loader_negative as loader

    reason = "cannot execute: simulated host"
    monkeypatch.setattr(loader, "_inapplicable_reason", lambda: reason)
    monkeypatch.setenv("IDIS_REQUIRE_LOADER_NEGATIVE", "1")
    with pytest.raises(pytest.fail.Exception, match=reason):
        loader.test_planted_libjpeg_is_not_loaded_under_helper_environment(
            tmp_path,
            lambda *_args: None,
        )
    failed_output = capsys.readouterr()
    assert failed_output.out == ""

    monkeypatch.delenv("IDIS_REQUIRE_LOADER_NEGATIVE", raising=False)
    recorded: list[tuple[str, object]] = []

    def record(name: str, value: object) -> None:
        recorded.append((name, value))

    loader.test_planted_libjpeg_is_not_loaded_under_helper_environment(tmp_path, record)
    assert recorded == [("loader_negative", f"NOT_EXECUTED: {reason}")]
    captured = capsys.readouterr()
    assert captured.out == f"LOADER_NEGATIVE NOT_EXECUTED: {reason}\n"
    assert captured.err == ""
