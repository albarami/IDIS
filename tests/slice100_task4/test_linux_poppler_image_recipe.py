"""Pins for the governed Linux Poppler image recipe (CIR-04H3-3).

The 23 runtime rows are ``closure-design-v2.json`` ``runtime.selected``, which
match design §D. ``libpng16-16`` and ``libexpat1`` are in the bookworm-security
index only; the design's bookworm-updates URL for libpng returns 404, so the
single URL is the security snapshot dated from that index.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "Dockerfile"
RECIPE = REPO / "native" / "poppler" / "recipe-v2.json"
SCRIPT = REPO / "scripts" / "build_linux_poppler_prefix.py"
LINUX_RUNTIME = REPO / "src" / "idis" / "parsers" / "poppler_linux_runtime.py"
PINNED_DIGEST = "sha256:04cd27899595a99dfe77709d96f08876bf2ee99139ee2f0fe9ac948005034e5b"
RECIPE_SHA256 = "e0ff366be570c7e30c5d354a51af83f76b6c2d05ee2bce73a43e68db8f9ae7fd"
RECIPE_SIZE_BYTES = 187826
SOURCE_SHA256 = "c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48"
SOURCE_SIZE_BYTES = 1992456
SOURCE_URL = "https://poppler.freedesktop.org/poppler-25.12.0.tar.xz"
BOOKWORM_SNAPSHOT = "https://snapshot.debian.org/archive/debian/20260711T101637Z/"
UPDATES_SNAPSHOT = "https://snapshot.debian.org/archive/debian/20261017T021138Z/"
SECURITY_SNAPSHOT = "https://snapshot.debian.org/archive/debian-security/20261016T210220Z/"
PREFIX_PATH = "/opt/idis-native/poppler-25.12.0"
_INTERPRETER = "/lib64/ld-linux-x86-64.so.2"
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3
_DT_NULL = 0
_DT_STRTAB = 5
_DT_STRSZ = 10
_DT_RPATH = 15
_DT_RUNPATH = 29

# package, version, sha256, size, filename. Cited from runtime.selected.
_DT_NEEDED_ROWS = (
    (
        "libbrotli1",
        "1.0.9-2+b6",
        "563b4caec1aa5e876bd3355b36e7a38e1484baf5a293b48d1e8bd22db786e4d7",
        275248,
        "pool/main/b/brotli/libbrotli1_1.0.9-2+b6_amd64.deb",
    ),
    (
        "libbsd0",
        "0.11.7-2",
        "bb31cc8b40f962a85b2cec970f7f79cc704a1ae4bad24257a822055404b2c60b",
        117416,
        "pool/main/libb/libbsd/libbsd0_0.11.7-2_amd64.deb",
    ),
    (
        "libcairo2",
        "1.16.0-7",
        "927fa059f8b5791d0cd744d4e3a8c57fa872ef849ce14434dc2222ac8a076a69",
        574676,
        "pool/main/c/cairo/libcairo2_1.16.0-7_amd64.deb",
    ),
    (
        "libdeflate0",
        "1.14-1",
        "3d4b39f94317b64a860db8a7a8b581b555124cd461fe07ec0d347edbdb9f6683",
        61416,
        "pool/main/libd/libdeflate/libdeflate0_1.14-1_amd64.deb",
    ),
    (
        "libfontconfig1",
        "2.14.1-4",
        "16ee38d374e064f534116dc442b086ef26f9831f1c0af7e5fb4fe4512e700649",
        385664,
        "pool/main/f/fontconfig/libfontconfig1_2.14.1-4_amd64.deb",
    ),
    (
        "libfreetype6",
        "2.12.1+dfsg-5+deb12u4",
        "8043e479f73f29992d652e3f9dfe8b17f9780c7ea6330afe379ec5f9f188ac44",
        397736,
        "pool/main/f/freetype/libfreetype6_2.12.1+dfsg-5+deb12u4_amd64.deb",
    ),
    (
        "libjbig0",
        "2.1-6.1",
        "6b07c77b700a615642888a82ba92a7e7c429d04b9c8669c62b2263f15c4c4059",
        31704,
        "pool/main/j/jbigkit/libjbig0_2.1-6.1_amd64.deb",
    ),
    (
        "libjpeg62-turbo",
        "1:2.1.5-2",
        "95ec30140789a342add8f8371ed018924de51b539056522b66f207b25cba9cad",
        166332,
        "pool/main/libj/libjpeg-turbo/libjpeg62-turbo_2.1.5-2_amd64.deb",
    ),
    (
        "liblcms2-2",
        "2.14-2+deb12u1",
        "333efe13d165b8a98194d95e72fd10b502ebbee4dc2eb43fc2ab83e2b53e5f17",
        153540,
        "pool/main/l/lcms2/liblcms2-2_2.14-2+deb12u1_amd64.deb",
    ),
    (
        "liblerc4",
        "4.0.0+ds-2",
        "771f5c47ca69f24ca61e4be0c98c5912b182ce442f921697d17a472f3ded5c9c",
        170368,
        "pool/main/l/lerc/liblerc4_4.0.0+ds-2_amd64.deb",
    ),
    (
        "libopenjp2-7",
        "2.5.0-2+deb12u3",
        "a127e2f1741d66bdddb6393a718e3cbec327d53f88c89b859b1f8d73f64cf635",
        189312,
        "pool/main/o/openjpeg2/libopenjp2-7_2.5.0-2+deb12u3_amd64.deb",
    ),
    (
        "libpixman-1-0",
        "0.42.2-1",
        "9ed75ef81b6b1eba9e1357f8b0ce01d31112b0e07b2b685bee2cf627a8932892",
        545588,
        "pool/main/p/pixman/libpixman-1-0_0.42.2-1_amd64.deb",
    ),
    (
        "libpng16-16",
        "1.6.39-2+deb12u6",
        "940b91d40d51c825de7f7aa133aecff1ffa25a91f12cb6aa6926c9ad55cd640c",
        277048,
        "pool/updates/main/libp/libpng1.6/libpng16-16_1.6.39-2+deb12u6_amd64.deb",
    ),
    (
        "libtiff6",
        "4.5.0-6+deb12u4",
        "255cd1d9f204355c366638ee21756234a8d43c03a3de18a5af6cdd94765b5053",
        316172,
        "pool/main/t/tiff/libtiff6_4.5.0-6+deb12u4_amd64.deb",
    ),
    (
        "libwebp7",
        "1.2.4-0.2+deb12u1",
        "7259b7ce46444694ce536360ad53acb68eb3b47a7ff81d7b1b8a3939b2ac9918",
        285512,
        "pool/main/libw/libwebp/libwebp7_1.2.4-0.2+deb12u1_amd64.deb",
    ),
    (
        "libx11-6",
        "2:1.8.4-2+deb12u2",
        "d88c973e79fd9b65838d77624142952757e47a6eb1a58602acf0911cf35989f4",
        759544,
        "pool/main/libx/libx11/libx11-6_1.8.4-2+deb12u2_amd64.deb",
    ),
    (
        "libxau6",
        "1:1.0.9-1",
        "679db1c4579ec7c61079adeaae8528adeb2e4bf5465baa6c56233b995d714750",
        19720,
        "pool/main/libx/libxau/libxau6_1.0.9-1_amd64.deb",
    ),
    (
        "libxcb-render0",
        "1.15-1",
        "29c32cc76be9393f409378be79a0f534b9f07dea06ae52fa80a7f27e298de239",
        115012,
        "pool/main/libx/libxcb/libxcb-render0_1.15-1_amd64.deb",
    ),
    (
        "libxcb-shm0",
        "1.15-1",
        "c1afcef29dc78b95c475159b181b28b1dedaf1d5aa06efd2fa6d90c73bfbe0e5",
        105472,
        "pool/main/libx/libxcb/libxcb-shm0_1.15-1_amd64.deb",
    ),
    (
        "libxcb1",
        "1.15-1",
        "fdc61332a3892168f3cc9cfa1fe9cf11a91dc3e0acacbc47cbc50ebaa234cc71",
        144052,
        "pool/main/libx/libxcb/libxcb1_1.15-1_amd64.deb",
    ),
    (
        "libxdmcp6",
        "1:1.1.2-3",
        "ecb8536f5fb34543b55bb9dc5f5b14c9dbb4150a7bddb3f2287b7cab6e9d25ef",
        26344,
        "pool/main/libx/libxdmcp/libxdmcp6_1.1.2-3_amd64.deb",
    ),
    (
        "libxext6",
        "2:1.3.4-1+b1",
        "504b7be9d7df4f6f4519e8dd4d6f9d03a9fb911a78530fa23a692fba3058cba6",
        52932,
        "pool/main/libx/libxext/libxext6_1.3.4-1+b1_amd64.deb",
    ),
    (
        "libxrender1",
        "1:0.9.10-1.1",
        "b2a6d160578f359220101947c8d67059b44704a251e688de1d0b744fefedfd1a",
        33228,
        "pool/main/libx/libxrender/libxrender1_0.9.10-1.1_amd64.deb",
    ),
)
_SECURITY_PACKAGES = frozenset({"libpng16-16", "libexpat1"})
_DEPENDS_ROWS = (
    (
        "fontconfig-config",
        "2.14.1-4",
        "281c66e46b95f045a0282a6c7a03b33de0e9a08d016897a759aaf4a04adfddbe",
        314864,
        "pool/main/f/fontconfig/fontconfig-config_2.14.1-4_amd64.deb",
    ),
    (
        "fonts-dejavu-core",
        "2.37-6",
        "8892669e51aab4dc56682c8e39d8ddb7d70fad83c369344e1e240bf3ca22bb76",
        1067728,
        "pool/main/f/fonts-dejavu/fonts-dejavu-core_2.37-6_all.deb",
    ),
    (
        "libexpat1",
        "2.5.0-1+deb12u4",
        "ed010cc41577d75ab01cccc6afa93496d9a99f1e16bd469caf58e1b81fddae80",
        105852,
        "pool/updates/main/e/expat/libexpat1_2.5.0-1+deb12u4_amd64.deb",
    ),
    (
        "libx11-data",
        "2:1.8.4-2+deb12u2",
        "987a848aeb1c358e4186368871b0526f10bb14c6b53214ab3bf8b69abb830191",
        291884,
        "pool/main/libx/libx11/libx11-data_1.8.4-2+deb12u2_all.deb",
    ),
)
_CLOSED_FILES = frozenset(
    {
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
        "lib/libpoppler.so.155.0.0",
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
    }
)
_CLOSED_LINKS = {
    "lib/libpoppler.so": "libpoppler.so.155",
    "lib/libpoppler.so.155": "libpoppler.so.155.0.0",
}
_ELF_NAMES = (
    "_read_elf_dynamic",
    "_vaddr_to_offset",
    "_cstring",
)
_CLOSURE_PACKAGES = tuple(row[0] for row in (*_DT_NEEDED_ROWS, *_DEPENDS_ROWS))
_DISTRO_RUNTIME_PACKAGES = ("libpq5", "curl", "ffmpeg", "tesseract-ocr", "tesseract-ocr-eng")
_HELD_BASE_PACKAGES = ("libc6", "liblzma5", "libpcre2-8-0")
_DPKG_VERSION_FORMAT = "${Package} ${Version}"


def _stages(text: str) -> dict[str, str]:
    stages: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM "):
            current = stripped.rsplit(" AS ", 1)[1].strip() if " AS " in stripped else stripped
            stages[current] = [line]
        elif current:
            stages[current].append(line)
    return {name: "\n".join(lines) for name, lines in stages.items()}


def _apt_packages(stage: str) -> list[str]:
    packages: list[str] = []
    collecting = False
    for line in stage.splitlines():
        if "apt-get install" in line:
            collecting = True
            continue
        if not collecting:
            continue
        stripped = line.strip()
        if stripped.startswith("&&") or stripped.startswith("RUN "):
            break
        token = stripped.rstrip("\\").strip().split()[0] if stripped.rstrip("\\").strip() else ""
        if token and not token.startswith("-"):
            packages.append(token)
    return packages


def _dockerfile_commands(stage: str) -> list[str]:
    """Join escaped newlines, then split ``&&`` chains into shell commands."""

    flattened = stage.replace("\\\n", " ")
    commands: list[str] = []
    for line in flattened.splitlines():
        for part in line.split("&&"):
            command = " ".join(part.split())
            if command:
                commands.append(command)
    return commands


def _command_tokens(command: str) -> list[str]:
    return command.replace(">", " ").split()


def _names_cover(command: str, names: tuple[str, ...]) -> bool:
    return set(names) <= set(_command_tokens(command))


def _redirect_target(command: str) -> str:
    if ">" not in command:
        raise AssertionError(command)
    target = command.split(">", 1)[1].strip().split()[0]
    if not target.startswith("/"):
        raise AssertionError(command)
    return target


def _load_script() -> ModuleType:
    assert SCRIPT.is_file()
    spec = importlib.util.spec_from_file_location("build_linux_poppler_prefix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _function_dump(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.dump(node, include_attributes=False)
    raise AssertionError(name)


def _call_order(path: Path, function_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    names: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            self.generic_visit(node)

    _Visitor().visit(function)
    return names


def test_apt_lines_omit_poppler_utils_and_keep_media_packages() -> None:
    """Design §D removes poppler-utils from both apt lines."""

    stages = _stages(DOCKERFILE.read_text(encoding="utf-8"))
    runtime = _apt_packages(stages["runtime"])
    builder = _apt_packages(stages["builder"])
    assert "poppler-utils" not in runtime
    assert "poppler-utils" not in builder
    for name in ("ffmpeg", "tesseract-ocr", "tesseract-ocr-eng"):
        assert name in runtime
        assert name in builder
    assert "libpq5" in runtime and "curl" in runtime
    for held in ("libc6", "liblzma5", "libpcre2-8-0"):
        assert held not in runtime
        assert held not in builder


def test_pinned_runtime_bodies_are_held_before_distro_apt_and_asserted_after() -> None:
    """Install and hold the 27 pinned bodies before the live distro apt line.

    After that line, the held-base ``dpkg-query``/``cmp`` pattern checks each
    body's version, and ``--assert-runtime-closure`` checks the pinned version.
    Hold records an installed package as ``hi``; the install step still sees ``ii``.
    """

    assert len(_CLOSURE_PACKAGES) == 27
    assert len(set(_CLOSURE_PACKAGES)) == 27
    runtime = _stages(DOCKERFILE.read_text(encoding="utf-8"))["runtime"]
    commands = _dockerfile_commands(runtime)
    distro = [
        index for index, command in enumerate(commands) if command.startswith("apt-get install ")
    ]
    assert len(distro) == 1
    distro_at = distro[0]
    distro_tokens = set(_command_tokens(commands[distro_at]))
    assert distro_tokens.issuperset(_DISTRO_RUNTIME_PACKAGES)
    assert distro_tokens.isdisjoint(_CLOSURE_PACKAGES)

    closure = [
        index
        for index, command in enumerate(commands)
        if "--install-runtime-closure" in command.split()
    ]
    assert len(closure) == 1
    closure_at = closure[0]
    assert closure_at < distro_at

    holds = [
        index
        for index, command in enumerate(commands)
        if command.startswith("apt-mark hold ") and _names_cover(command, _CLOSURE_PACKAGES)
    ]
    assert len(holds) == 1
    assert closure_at < holds[0] < distro_at
    base_holds = [
        index
        for index, command in enumerate(commands)
        if command.startswith("apt-mark hold ") and _names_cover(command, _HELD_BASE_PACKAGES)
    ]
    assert base_holds
    assert base_holds[0] < closure_at

    def _closure_query(command: str) -> bool:
        return (
            command.startswith("dpkg-query ")
            and _DPKG_VERSION_FORMAT in command
            and _names_cover(command, _CLOSURE_PACKAGES)
            and ">" in command
        )

    before_queries = [
        (index, command)
        for index, command in enumerate(commands)
        if index < distro_at and _closure_query(command)
    ]
    after_queries = [
        (index, command)
        for index, command in enumerate(commands)
        if index > distro_at and _closure_query(command)
    ]
    assert len(before_queries) == 1
    assert len(after_queries) == 1
    before_at, before_command = before_queries[0]
    after_at, after_command = after_queries[0]
    assert closure_at < before_at < distro_at
    before_file = _redirect_target(before_command)
    after_file = _redirect_target(after_command)
    assert before_file != after_file
    cmps = [
        index
        for index, command in enumerate(commands)
        if command.split() == ["cmp", before_file, after_file]
    ]
    assert len(cmps) == 1
    assert after_at < cmps[0]
    assertions = [
        index
        for index, command in enumerate(commands)
        if "--assert-runtime-closure" in command.split()
        and "--install-runtime-closure" not in command.split()
    ]
    assert len(assertions) == 1
    assert cmps[0] < assertions[0]
    base_after = [
        command
        for index, command in enumerate(commands)
        if index > distro_at
        and command.startswith("dpkg-query ")
        and _DPKG_VERSION_FORMAT in command
        and _names_cover(command, _HELD_BASE_PACKAGES)
    ]
    assert base_after


def test_poppler_builder_stage_copies_only_the_prefix() -> None:
    stages = _stages(DOCKERFILE.read_text(encoding="utf-8"))
    poppler = stages["poppler-builder"]
    runtime = stages["runtime"]
    assert f"FROM python:3.11-slim-bookworm@{PINNED_DIGEST} AS poppler-builder" in poppler
    assert f"FROM python:3.11-slim-bookworm@{PINNED_DIGEST} AS builder" in stages["builder"]
    assert "COPY native/poppler/recipe-v2.json" in poppler
    assert "scripts/build_linux_poppler_prefix.py" in poppler
    assert f"--checksum=sha256:{SOURCE_SHA256}" in poppler
    assert SOURCE_URL in poppler
    assert "apt-get update" not in poppler
    copies = [line.strip() for line in runtime.splitlines() if "--from=poppler-builder" in line]
    assert copies == [f"COPY --from=poppler-builder {PREFIX_PATH} {PREFIX_PATH}"]
    assert "USER idis" in runtime
    assert "HEALTHCHECK " in runtime
    assert 'CMD ["python", "-m", "idis.runtime.strict_launcher", "api"' in runtime
    path_lines = [
        line.strip() for line in runtime.splitlines() if line.strip().startswith("ENV PATH=")
    ]
    assert path_lines == ['ENV PATH="/opt/venv/bin:$PATH"']


def test_recipe_v2_is_the_h2a_byte_copy() -> None:
    assert RECIPE.is_file()
    data = RECIPE.read_bytes()
    assert len(data) == RECIPE_SIZE_BYTES
    assert hashlib.sha256(data).hexdigest() == RECIPE_SHA256


def test_script_checks_recipe_and_source_before_extraction() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert SOURCE_SHA256 in text
    assert str(SOURCE_SIZE_BYTES) in text
    assert SOURCE_URL in text
    assert "detached signature" in text.lower()
    assert "unverified" in text.lower()
    order = _call_order(SCRIPT, "main")
    recipe_at = order.index("verify_recipe_file")
    source_at = order.index("verify_source_archive")
    extract_at = order.index("extract_source_archive")
    assert recipe_at < source_at < extract_at


def test_runtime_bodies_have_one_snapshot_url_each() -> None:
    module = _load_script()
    assert module.SNAPSHOT_ROOTS == {
        "bookworm": BOOKWORM_SNAPSHOT,
        "bookworm-updates": UPDATES_SNAPSHOT,
        "bookworm-security": SECURITY_SNAPSHOT,
    }
    needed = {body.package: body for body in module.RUNTIME_DT_NEEDED_BODIES}
    assert set(needed) == {row[0] for row in _DT_NEEDED_ROWS}
    for package, version, digest, size, filename in _DT_NEEDED_ROWS:
        body = needed[package]
        assert body.version == version
        assert body.sha256 == digest
        assert body.size == size
        assert body.filename == filename
        assert body.url.count("https://") == 1
        if package in _SECURITY_PACKAGES:
            assert body.url == SECURITY_SNAPSHOT + filename
        else:
            assert body.url == BOOKWORM_SNAPSHOT + filename
        assert "bookworm-updates" not in body.url
    depends = {body.package: body for body in module.RUNTIME_DEPENDS_BODIES}
    assert set(depends) == {row[0] for row in _DEPENDS_ROWS}
    for package, version, digest, size, filename in _DEPENDS_ROWS:
        body = depends[package]
        assert (body.version, body.sha256, body.size, body.filename) == (
            version,
            digest,
            size,
            filename,
        )
        assert body.url.count("https://") == 1
    text = SCRIPT.read_text(encoding="utf-8")
    assert "deb.debian.org" not in text
    assert "ftp.debian.org" not in text
    assert "apt-get update" not in text
    assert "|| true" not in text
    assert "poppler-data" not in {body.package for body in module.RUNTIME_DT_NEEDED_BODIES}
    assert "poppler-data" not in depends


def test_closed_set_names_match_the_h2a_prefix_manifest() -> None:
    module = _load_script()
    assert set(module.CLOSED_PREFIX_FILES) == _CLOSED_FILES
    assert dict(module.CLOSED_PREFIX_SYMLINKS) == _CLOSED_LINKS
    assert len(_CLOSED_FILES) == 26
    assert len(_CLOSED_LINKS) == 2


def test_elf_reader_matches_the_admission_module() -> None:
    assert SCRIPT.is_file()
    for name in _ELF_NAMES:
        assert _function_dump(SCRIPT, name) == _function_dump(LINUX_RUNTIME, name)


def test_success_stdout_is_one_manifest_line(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_script()
    digest = "ab" * 32
    module.print_manifest_sha256(digest)
    captured = capsys.readouterr()
    assert captured.out == f"POPPLER_LINUX_MANIFEST_SHA256={digest}\n"
    assert captured.err == ""


def _run_script(tmp_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.is_file()
    env = os.environ.copy()
    env.pop("IDIS_STRICT_DOTENV_PATH", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_recipe_hash_mismatch_exits_before_any_prefix_write(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.json"
    source = tmp_path / "poppler.tar.xz"
    prefix = tmp_path / "prefix"
    recipe.write_bytes(b"not-the-recipe")
    source.write_bytes(b"not-the-source")
    result = _run_script(
        tmp_path,
        [
            "--prefix",
            str(prefix),
            "--recipe",
            str(recipe),
            "--source",
            str(source),
            "--manifest-out",
            str(prefix / "share" / "idis" / "poppler-linux-prefix-identity-v1.json"),
        ],
    )
    assert result.returncode == 1
    assert "POPPLER_RUNTIME_RECIPE_HASH_MISMATCH" in result.stderr
    assert "Traceback" not in result.stderr
    assert "POPPLER_LINUX_MANIFEST_SHA256=" not in result.stdout
    assert not prefix.exists()


def test_source_hash_mismatch_exits_before_extraction(tmp_path: Path) -> None:
    assert RECIPE.is_file()
    source = tmp_path / "poppler-25.12.0.tar.xz"
    prefix = tmp_path / "prefix"
    source.write_bytes(b"wrong-bytes")
    result = _run_script(
        tmp_path,
        [
            "--prefix",
            str(prefix),
            "--recipe",
            str(RECIPE),
            "--source",
            str(source),
            "--manifest-out",
            str(prefix / "share" / "idis" / "poppler-linux-prefix-identity-v1.json"),
        ],
    )
    assert result.returncode == 1
    assert "POPPLER_RUNTIME_SOURCE_HASH_MISMATCH" in result.stderr
    assert "Traceback" not in result.stderr
    assert not prefix.exists()
    assert not (tmp_path / "poppler-25.12.0").exists()


def test_help_accepts_the_four_build_arguments() -> None:
    result = _run_script(Path.cwd(), ["--help"])
    assert result.returncode == 0
    for flag in ("--prefix", "--recipe", "--source", "--manifest-out"):
        assert flag in result.stdout


def _synthetic_elf(*, rpath: str, interpreter: str | None) -> bytes:
    header_size = 64
    program_count = 3 if interpreter is not None else 2
    cursor = header_size + (56 * program_count)
    interp_bytes = b""
    interp_offset = cursor
    if interpreter is not None:
        interp_bytes = interpreter.encode("ascii") + b"\x00"
        cursor += len(interp_bytes)
    strtab_offset = cursor
    strtab = b"\x00" + rpath.encode("ascii") + b"\x00"
    cursor += len(strtab)
    dynamic_offset = cursor
    entries = [(_DT_STRTAB, strtab_offset), (_DT_STRSZ, len(strtab)), (_DT_RPATH, 1), (_DT_NULL, 0)]
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    file_size = dynamic_offset + len(dynamic)
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
    load = struct.pack("<IIQQQQQQ", _PT_LOAD, 5, 0, 0, 0, file_size, file_size, 8)
    programs = load
    if interpreter is not None:
        programs += struct.pack(
            "<IIQQQQQQ",
            _PT_INTERP,
            4,
            interp_offset,
            interp_offset,
            interp_offset,
            len(interp_bytes),
            len(interp_bytes),
            8,
        )
    programs += struct.pack(
        "<IIQQQQQQ",
        _PT_DYNAMIC,
        4,
        dynamic_offset,
        dynamic_offset,
        dynamic_offset,
        len(dynamic),
        len(dynamic),
        8,
    )
    return header + programs + interp_bytes + strtab + dynamic


def _write_closed_tree(root: Path, module: ModuleType) -> Path:
    prefix = root / "poppler-prefix"
    prefix.mkdir(parents=True)
    resolved = prefix.resolve()
    rpath = str(resolved / "lib")
    elf_paths = set(module.ELF_PREFIX_FILES)
    for relative in module.CLOSED_PREFIX_FILES:
        path = prefix / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative in elf_paths:
            interpreter = None if relative.startswith("lib/") else _INTERPRETER
            path.write_bytes(_synthetic_elf(rpath=rpath, interpreter=interpreter))
        else:
            path.write_bytes(b"synthetic\n")
    for relative, target in module.CLOSED_PREFIX_SYMLINKS.items():
        link = prefix / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link)
    return prefix


def test_written_manifest_is_admitted_and_an_extra_file_is_rejected(tmp_path: Path) -> None:
    from idis.parsers.poppler_linux_runtime import admit_linux_poppler_prefix
    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    module = _load_script()
    prefix = _write_closed_tree(tmp_path / "ok", module)
    manifest_out = prefix / "share" / "idis" / "poppler-linux-prefix-identity-v1.json"
    digest = module.write_prefix_identity_manifest(prefix, manifest_out)
    assert manifest_out.is_file()
    assert hashlib.sha256(manifest_out.read_bytes()).hexdigest() == digest

    def _probe(_path: Path) -> str:
        return "25.12.0"

    binding = admit_linux_poppler_prefix(prefix, version_probe=_probe)
    assert type(binding).__name__ == "PinnedLinuxPopplerRuntime"
    extra = _write_closed_tree(tmp_path / "extra", module)
    (extra / "lib" / "libjpeg.so.62").write_bytes(b"planted")
    with pytest.raises(module.BuildFailure) as writer_error:
        module.write_prefix_identity_manifest(
            extra,
            extra / "share" / "idis" / "poppler-linux-prefix-identity-v1.json",
        )
    assert writer_error.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"
    planted = _write_closed_tree(tmp_path / "admitted-extra", module)
    module.write_prefix_identity_manifest(
        planted,
        planted / "share" / "idis" / "poppler-linux-prefix-identity-v1.json",
    )
    (planted / "lib" / "extra-file").write_bytes(b"extra")
    with pytest.raises(PopplerRuntimeBindingError) as admitted:
        admit_linux_poppler_prefix(planted, version_probe=_probe)
    assert admitted.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"


def test_runtime_stage_has_no_apt_get_update_before_closure_install() -> None:
    """The runtime stage must not refresh apt lists before the pinned closure."""

    runtime = _stages(DOCKERFILE.read_text(encoding="utf-8"))["runtime"]
    commands = _dockerfile_commands(runtime)
    closure = [
        index
        for index, command in enumerate(commands)
        if "--install-runtime-closure" in command.split()
    ]
    assert len(closure) == 1
    earlier = commands[: closure[0]]
    assert all("apt-get update" not in command for command in earlier)


def test_assert_runtime_closure_accepts_exact_ii_and_hi_only() -> None:
    """Canned dpkg-query: ii/hi pass; a wrong version, rc, un, and iiR fail."""

    module = _load_script()
    bodies = module._runtime_closure_bodies()
    assert len(bodies) == 27
    assert len({body.package for body in bodies}) == 27

    def _stdout(status_for: object, version_for: object | None = None) -> bytes:
        lines: list[str] = []
        for body in bodies:
            version = body.version if version_for is None else version_for(body.package)
            lines.append(f"{body.package}\t{version}\t{status_for(body.package)}")
        return ("\n".join(lines) + "\n").encode()

    def _install(stdout: bytes) -> None:
        def fake_run(
            name: str,
            argv: list[str],
            env: object,
            cwd: object,
            timeout: int,
        ) -> subprocess.CompletedProcess[bytes]:
            del name, env, cwd, timeout
            assert argv[:2] == ["dpkg-query", "-W"]
            return subprocess.CompletedProcess(argv, 0, stdout, b"")

        module._run = fake_run  # type: ignore[method-assign]
        module.assert_runtime_closure()

    _install(_stdout(lambda _package: "ii "))
    _install(_stdout(lambda _package: "hi "))

    def _reject(stdout: bytes) -> None:
        def fake_run(
            name: str,
            argv: list[str],
            env: object,
            cwd: object,
            timeout: int,
        ) -> subprocess.CompletedProcess[bytes]:
            del name, env, cwd, timeout
            return subprocess.CompletedProcess(argv, 0, stdout, b"")

        module._run = fake_run  # type: ignore[method-assign]
        with pytest.raises(module.BuildFailure) as caught:
            module.assert_runtime_closure()
        assert caught.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"

    def _one(package: str, status: str, version: str | None = None) -> bytes:
        def status_for(name: str) -> str:
            return status if name == package else "ii "

        def version_for(name: str) -> str:
            if version is not None and name == package:
                return version
            return next(body.version for body in bodies if body.package == name)

        return _stdout(status_for, version_for)

    first = bodies[0].package
    _reject(_one(first, "ii ", "not-the-pin"))
    _reject(_one(first, "rc"))
    _reject(_one(first, "un"))
    _reject(_one(first, "iiR"))
    module._RUNTIME_CLOSURE_COUNT = 26
    _reject(_stdout(lambda _package: "ii "))
