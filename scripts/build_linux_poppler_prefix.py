"""Build the governed Linux Poppler 25.12.0 prefix.

Reused from the reviewed H2a drivers
``C:/Projects/IDIS/.local_reports/cir04h2a-20261018/builder/build_poppler.py``
and ``assemble_runtime.py``: streaming SHA-256, local ``.deb`` install without
refreshing apt indexes, the recipe configure/build/install argv, the B1 feature
checks, and the version-probe regex. Changed: snapshot fetch of the pinned
bodies, a closed-set identity manifest, this CLI, and the pure-Python
``PT_DYNAMIC`` reader copied from ``src/idis/parsers/poppler_linux_runtime.py``.
The H2a driver self-hash and GnuPG index check are not this script.

poppler-25.12.0.tar.xz detached signature is unverified. The product pins are
the SHA-256 and the byte size from
https://poppler.freedesktop.org/poppler-25.12.0.tar.xz.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

RECIPE_SHA256 = "e0ff366be570c7e30c5d354a51af83f76b6c2d05ee2bce73a43e68db8f9ae7fd"
RECIPE_SIZE_BYTES = 187826
SOURCE_SHA256 = "c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48"
SOURCE_SIZE_BYTES = 1992456
SOURCE_URL = "https://poppler.freedesktop.org/poppler-25.12.0.tar.xz"
POPPLER_VERSION = "25.12.0"
SCHEMA_VERSION = "poppler-linux-prefix-identity-v1"
MANIFEST_NAME = "poppler-linux-prefix-identity-v1.json"
MANIFEST_RELATIVE = f"share/idis/{MANIFEST_NAME}"
_INTERPRETER = "/lib64/ld-linux-x86-64.so.2"
_WORK_ROOT = Path("/tmp/idis-poppler-work")
_DEB_ROOT = _WORK_ROOT / "debs"
_HASH_CHUNK = 1024 * 1024
_HELD_BASE_PACKAGES = ("libc6", "liblzma5", "libpcre2-8-0")
_RUNTIME_CLOSURE_COUNT = 27
SNAPSHOT_ROOTS = {
    "bookworm": "https://snapshot.debian.org/archive/debian/20260711T101637Z/",
    "bookworm-updates": "https://snapshot.debian.org/archive/debian/20261017T021138Z/",
    "bookworm-security": "https://snapshot.debian.org/archive/debian-security/20261016T210220Z/",
}
# Builder bodies whose SHA-256 is only in the bookworm-security index.
_SECURITY_ONLY_SHA256 = frozenset(
    {
        "10f13e000ee757f5f2d2d3569f9e30546214a0c850acd78695feae373bfa3e53",
        "159c10b6a650cac30de79f93189e7bfdcbc42088ec1e7049a08477e4e93be223",
        "229112e7be58431e38e9c536a646bdeccc470d705b71e3286a97e567182e2643",
        "3d8c29c8c21ec6007635e1b2591edf98b771c9572a085968f47452b33040f384",
        "420595d97c946994ea72f38ac1103d8bcc1b10385f3d2a2fe2627c52d2beed6e",
        "45419b8c7646c1af12783ae31906ce2da4c465c1a948b01f8f44152d8269f403",
        "466b6ef0a7788ac6a754e727ec43e3e1c45602baa0ecf103af6ba8a2665b7c4f",
        "4facf334e0e0830a87013852f8c3a1cfee11ad702f72f240adf9ad5b9a334e7c",
        "54592f2ac58497ed96dfdac341a05473fcd29296320b0910dc21a8c73f5947bd",
        "69f41cfa3f4ba17284e538f843069d7529c53c207d2ae17a5b02785944870e33",
        "74ed207919d0022cc081aa5b2cb982229b345e0d81a41049a1d944b903b3f90b",
        "8fba549e7a6819bb5758fc9b7d601a82963e6cdc222043d06a989312604bbc55",
        "940b91d40d51c825de7f7aa133aecff1ffa25a91f12cb6aa6926c9ad55cd640c",
        "b5f855ab922dfcd5d1a3223b2118c3dae13ffc7751b0cd5dc24740386323a33a",
        "d2f7edfcc7689b9e0761c2742cc824ac86a20768bc5e8057818dc6875291fe76",
        "ebcd139c31438e19f161bcf277a38708402ddd0e3acbe208cbbb27bff1c52bb9",
        "ed010cc41577d75ab01cccc6afa93496d9a99f1e16bd469caf58e1b81fddae80",
        "fff72a194e493f88e100a2567e22472bb4ab828d429c2956965c6f2f134f1b3a",
    }
)
ELF_PREFIX_FILES = frozenset(
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
    }
)
CLOSED_PREFIX_FILES = ELF_PREFIX_FILES | frozenset(
    {
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
CLOSED_PREFIX_SYMLINKS = {
    "lib/libpoppler.so": "libpoppler.so.155",
    "lib/libpoppler.so.155": "libpoppler.so.155.0.0",
}
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3
_DT_NULL = 0
_DT_STRTAB = 5
_DT_STRSZ = 10
_DT_RPATH = 15
_DT_RUNPATH = 29


@dataclass(frozen=True, slots=True)
class DebianBody:
    """One hash-pinned Debian body and its single snapshot URL."""

    package: str
    version: str
    sha256: str
    size: int
    filename: str
    suite: str

    @property
    def url(self) -> str:
        """Return the one snapshot URL for this body."""

        return SNAPSHOT_ROOTS[self.suite] + self.filename


def _deb(
    package: str,
    version: str,
    sha256: str,
    size: int,
    filename: str,
    suite: str,
) -> DebianBody:
    return DebianBody(package, version, sha256, size, filename, suite)


RUNTIME_DT_NEEDED_BODIES = (
    _deb(
        "libbrotli1",
        "1.0.9-2+b6",
        "563b4caec1aa5e876bd3355b36e7a38e1484baf5a293b48d1e8bd22db786e4d7",
        275248,
        "pool/main/b/brotli/libbrotli1_1.0.9-2+b6_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libbsd0",
        "0.11.7-2",
        "bb31cc8b40f962a85b2cec970f7f79cc704a1ae4bad24257a822055404b2c60b",
        117416,
        "pool/main/libb/libbsd/libbsd0_0.11.7-2_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libcairo2",
        "1.16.0-7",
        "927fa059f8b5791d0cd744d4e3a8c57fa872ef849ce14434dc2222ac8a076a69",
        574676,
        "pool/main/c/cairo/libcairo2_1.16.0-7_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libdeflate0",
        "1.14-1",
        "3d4b39f94317b64a860db8a7a8b581b555124cd461fe07ec0d347edbdb9f6683",
        61416,
        "pool/main/libd/libdeflate/libdeflate0_1.14-1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libfontconfig1",
        "2.14.1-4",
        "16ee38d374e064f534116dc442b086ef26f9831f1c0af7e5fb4fe4512e700649",
        385664,
        "pool/main/f/fontconfig/libfontconfig1_2.14.1-4_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libfreetype6",
        "2.12.1+dfsg-5+deb12u4",
        "8043e479f73f29992d652e3f9dfe8b17f9780c7ea6330afe379ec5f9f188ac44",
        397736,
        "pool/main/f/freetype/libfreetype6_2.12.1+dfsg-5+deb12u4_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libjbig0",
        "2.1-6.1",
        "6b07c77b700a615642888a82ba92a7e7c429d04b9c8669c62b2263f15c4c4059",
        31704,
        "pool/main/j/jbigkit/libjbig0_2.1-6.1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libjpeg62-turbo",
        "1:2.1.5-2",
        "95ec30140789a342add8f8371ed018924de51b539056522b66f207b25cba9cad",
        166332,
        "pool/main/libj/libjpeg-turbo/libjpeg62-turbo_2.1.5-2_amd64.deb",
        "bookworm",
    ),
    _deb(
        "liblcms2-2",
        "2.14-2+deb12u1",
        "333efe13d165b8a98194d95e72fd10b502ebbee4dc2eb43fc2ab83e2b53e5f17",
        153540,
        "pool/main/l/lcms2/liblcms2-2_2.14-2+deb12u1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "liblerc4",
        "4.0.0+ds-2",
        "771f5c47ca69f24ca61e4be0c98c5912b182ce442f921697d17a472f3ded5c9c",
        170368,
        "pool/main/l/lerc/liblerc4_4.0.0+ds-2_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libopenjp2-7",
        "2.5.0-2+deb12u3",
        "a127e2f1741d66bdddb6393a718e3cbec327d53f88c89b859b1f8d73f64cf635",
        189312,
        "pool/main/o/openjpeg2/libopenjp2-7_2.5.0-2+deb12u3_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libpixman-1-0",
        "0.42.2-1",
        "9ed75ef81b6b1eba9e1357f8b0ce01d31112b0e07b2b685bee2cf627a8932892",
        545588,
        "pool/main/p/pixman/libpixman-1-0_0.42.2-1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libpng16-16",
        "1.6.39-2+deb12u6",
        "940b91d40d51c825de7f7aa133aecff1ffa25a91f12cb6aa6926c9ad55cd640c",
        277048,
        "pool/updates/main/libp/libpng1.6/libpng16-16_1.6.39-2+deb12u6_amd64.deb",
        "bookworm-security",
    ),
    _deb(
        "libtiff6",
        "4.5.0-6+deb12u4",
        "255cd1d9f204355c366638ee21756234a8d43c03a3de18a5af6cdd94765b5053",
        316172,
        "pool/main/t/tiff/libtiff6_4.5.0-6+deb12u4_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libwebp7",
        "1.2.4-0.2+deb12u1",
        "7259b7ce46444694ce536360ad53acb68eb3b47a7ff81d7b1b8a3939b2ac9918",
        285512,
        "pool/main/libw/libwebp/libwebp7_1.2.4-0.2+deb12u1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libx11-6",
        "2:1.8.4-2+deb12u2",
        "d88c973e79fd9b65838d77624142952757e47a6eb1a58602acf0911cf35989f4",
        759544,
        "pool/main/libx/libx11/libx11-6_1.8.4-2+deb12u2_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxau6",
        "1:1.0.9-1",
        "679db1c4579ec7c61079adeaae8528adeb2e4bf5465baa6c56233b995d714750",
        19720,
        "pool/main/libx/libxau/libxau6_1.0.9-1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxcb-render0",
        "1.15-1",
        "29c32cc76be9393f409378be79a0f534b9f07dea06ae52fa80a7f27e298de239",
        115012,
        "pool/main/libx/libxcb/libxcb-render0_1.15-1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxcb-shm0",
        "1.15-1",
        "c1afcef29dc78b95c475159b181b28b1dedaf1d5aa06efd2fa6d90c73bfbe0e5",
        105472,
        "pool/main/libx/libxcb/libxcb-shm0_1.15-1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxcb1",
        "1.15-1",
        "fdc61332a3892168f3cc9cfa1fe9cf11a91dc3e0acacbc47cbc50ebaa234cc71",
        144052,
        "pool/main/libx/libxcb/libxcb1_1.15-1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxdmcp6",
        "1:1.1.2-3",
        "ecb8536f5fb34543b55bb9dc5f5b14c9dbb4150a7bddb3f2287b7cab6e9d25ef",
        26344,
        "pool/main/libx/libxdmcp/libxdmcp6_1.1.2-3_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxext6",
        "2:1.3.4-1+b1",
        "504b7be9d7df4f6f4519e8dd4d6f9d03a9fb911a78530fa23a692fba3058cba6",
        52932,
        "pool/main/libx/libxext/libxext6_1.3.4-1+b1_amd64.deb",
        "bookworm",
    ),
    _deb(
        "libxrender1",
        "1:0.9.10-1.1",
        "b2a6d160578f359220101947c8d67059b44704a251e688de1d0b744fefedfd1a",
        33228,
        "pool/main/libx/libxrender/libxrender1_0.9.10-1.1_amd64.deb",
        "bookworm",
    ),
)
RUNTIME_DEPENDS_BODIES = (
    _deb(
        "fontconfig-config",
        "2.14.1-4",
        "281c66e46b95f045a0282a6c7a03b33de0e9a08d016897a759aaf4a04adfddbe",
        314864,
        "pool/main/f/fontconfig/fontconfig-config_2.14.1-4_amd64.deb",
        "bookworm",
    ),
    _deb(
        "fonts-dejavu-core",
        "2.37-6",
        "8892669e51aab4dc56682c8e39d8ddb7d70fad83c369344e1e240bf3ca22bb76",
        1067728,
        "pool/main/f/fonts-dejavu/fonts-dejavu-core_2.37-6_all.deb",
        "bookworm",
    ),
    _deb(
        "libexpat1",
        "2.5.0-1+deb12u4",
        "ed010cc41577d75ab01cccc6afa93496d9a99f1e16bd469caf58e1b81fddae80",
        105852,
        "pool/updates/main/e/expat/libexpat1_2.5.0-1+deb12u4_amd64.deb",
        "bookworm-security",
    ),
    _deb(
        "libx11-data",
        "2:1.8.4-2+deb12u2",
        "987a848aeb1c358e4186368871b0526f10bb14c6b53214ab3bf8b69abb830191",
        291884,
        "pool/main/libx/libx11/libx11-data_1.8.4-2+deb12u2_all.deb",
        "bookworm",
    ),
)


class BuildFailure(Exception):
    """Fail-closed build stop. ``code`` is one of the §C identity codes."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class _ElfDynamic:
    rpath: str | None
    runpath_present: bool
    interpreter: str | None


def _read_elf_dynamic(path: Path) -> _ElfDynamic | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    try:
        _type, machine, _version, _entry, phoff, _shoff, _flags, _ehsize, phentsize, phnum = (
            struct.unpack_from("<HHIQQQIHHH", data, 16)
        )
    except struct.error:
        return None
    if machine != 62 or phoff <= 0 or phentsize < 56 or phnum <= 0:
        return None
    loads: list[tuple[int, int, int]] = []
    dynamic_offset: int | None = None
    dynamic_size = 0
    interpreter: str | None = None
    for index in range(phnum):
        start = phoff + index * phentsize
        try:
            kind, _flags, offset, vaddr, _paddr, filesz, _memsz, _align = struct.unpack_from(
                "<IIQQQQQQ",
                data,
                start,
            )
        except struct.error:
            return None
        if kind == _PT_LOAD:
            loads.append((offset, vaddr, filesz))
        elif kind == _PT_DYNAMIC:
            dynamic_offset = offset
            dynamic_size = filesz
        elif kind == _PT_INTERP:
            interpreter = _cstring(data, offset)
    if dynamic_offset is None:
        return None
    strtab: int | None = None
    rpath_indexes: list[int] = []
    runpath_present = False
    cursor = dynamic_offset
    end = dynamic_offset + dynamic_size
    while cursor + 16 <= end and cursor + 16 <= len(data):
        try:
            tag, value = struct.unpack_from("<qQ", data, cursor)
        except struct.error:
            return None
        cursor += 16
        if tag == _DT_NULL:
            break
        if tag == _DT_STRTAB:
            strtab = value
        elif tag == _DT_STRSZ:
            continue
        elif tag == _DT_RPATH:
            rpath_indexes.append(value)
        elif tag == _DT_RUNPATH:
            runpath_present = True
    if strtab is None or len(rpath_indexes) != 1:
        return _ElfDynamic(rpath=None, runpath_present=runpath_present, interpreter=interpreter)
    file_offset = _vaddr_to_offset(loads, strtab)
    if file_offset is None:
        return None
    rpath = _cstring(data, file_offset + rpath_indexes[0])
    return _ElfDynamic(rpath=rpath, runpath_present=runpath_present, interpreter=interpreter)


def _vaddr_to_offset(loads: list[tuple[int, int, int]], vaddr: int) -> int | None:
    for offset, base, size in loads:
        if base <= vaddr < base + size:
            return offset + (vaddr - base)
    return None


def _cstring(data: bytes, offset: int) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset)
    if end < 0:
        return None
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError:
        return None


def print_manifest_sha256(digest: str) -> None:
    """Print the one success line. Callers leave stdout empty until then."""

    sys.stdout.write(f"POPPLER_LINUX_MANIFEST_SHA256={digest}\n")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_recipe_file(path: Path) -> dict[str, Any]:
    """Reject a recipe whose bytes are not the H2a pin, before any extract."""

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise BuildFailure("POPPLER_RUNTIME_RECIPE_HASH_MISMATCH", str(exc)) from None
    if len(data) != RECIPE_SIZE_BYTES or hashlib.sha256(data).hexdigest() != RECIPE_SHA256:
        raise BuildFailure("POPPLER_RUNTIME_RECIPE_HASH_MISMATCH")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise BuildFailure("POPPLER_RUNTIME_RECIPE_HASH_MISMATCH", str(exc)) from None
    if type(payload) is not dict:
        raise BuildFailure("POPPLER_RUNTIME_RECIPE_HASH_MISMATCH")
    return cast(dict[str, Any], payload)


def verify_source_archive(path: Path) -> None:
    """Reject a tarball whose size or SHA-256 is not the pin, before extract."""

    try:
        digest, size = _sha256_file(path)
    except OSError as exc:
        raise BuildFailure("POPPLER_RUNTIME_SOURCE_HASH_MISMATCH", str(exc)) from None
    if size != SOURCE_SIZE_BYTES or digest != SOURCE_SHA256:
        raise BuildFailure("POPPLER_RUNTIME_SOURCE_HASH_MISMATCH")


def extract_source_archive(archive: Path, destination: Path) -> Path:
    """Extract the already verified tarball and return its top-level source."""

    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:xz") as tar:
            for member in tar.getmembers():
                if member.name.startswith(("/", "\\")) or ".." in Path(member.name).parts:
                    raise BuildFailure("POPPLER_RUNTIME_IDENTITY_INVALID", member.name)
            tar.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise BuildFailure("POPPLER_RUNTIME_IDENTITY_INVALID", str(exc)) from None
    source = destination / "poppler-25.12.0"
    if not source.is_dir():
        raise BuildFailure("POPPLER_RUNTIME_IDENTITY_INVALID", "tarball root missing")
    return source


def _note(text: str) -> None:
    print(text, file=sys.stderr)


def _fail(code: str, detail: str) -> NoReturn:
    raise BuildFailure(code, detail)


def _tool_env(recipe: Mapping[str, Any]) -> dict[str, str]:
    raw = recipe["builder"]["environment"]
    if type(raw) is not dict:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "builder environment missing")
    return {str(key): str(value) for key, value in raw.items()}


def _run(
    name: str,
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    logs = _WORK_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildFailure("POPPLER_RUNTIME_IDENTITY_INVALID", f"{name} timed out") from exc
    except OSError as exc:
        raise BuildFailure("POPPLER_RUNTIME_IDENTITY_INVALID", f"{name}: {exc}") from None
    (logs / f"{name}.stdout").write_bytes(completed.stdout)
    (logs / f"{name}.stderr").write_bytes(completed.stderr)
    _note(f"{name}: exit={completed.returncode}")
    return completed


def _fetch_verified(url: str, destination: Path, sha256: str, size: int) -> None:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "snapshot.debian.org"
        or url.count("https://") != 1
    ):
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "body URL is not the one snapshot URL")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        digest, measured = _sha256_file(destination)
        if digest == sha256 and measured == size:
            return
        destination.unlink()
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.hostname != "snapshot.debian.org":
                _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "snapshot redirect left the host")
            hasher = hashlib.sha256()
            measured = 0
            with destination.open("wb") as handle:
                while chunk := response.read(_HASH_CHUNK):
                    hasher.update(chunk)
                    measured += len(chunk)
                    handle.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise BuildFailure("POPPLER_RUNTIME_IDENTITY_INVALID", f"{url} {exc}") from None
    if measured != size or hasher.hexdigest() != sha256:
        destination.unlink(missing_ok=True)
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"body mismatch {destination.name}")


def _apt_install(paths: Sequence[Path], env: Mapping[str, str]) -> None:
    # Same argv as the reviewed H2a driver. Acquire::Retries=0 and the empty
    # base-image package lists are what stop a fetch; --no-download made apt
    # exit 100 before it would install the local bodies.
    argv = [
        "apt-get",
        "-y",
        "--no-install-recommends",
        "-o",
        "Dpkg::Use-Pty=0",
        "-o",
        "Acquire::Retries=0",
        "install",
        *[str(path) for path in paths],
    ]
    completed = _run("apt-install", argv, env, Path("/"), 1800)
    if completed.returncode != 0:
        tail = " | ".join(completed.stderr.decode("utf-8", "replace").splitlines()[-12:])
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"local deb install failed: {tail}")


def _dpkg_versions(env: Mapping[str, str], *, include_hold: bool = False) -> dict[str, str]:
    completed = _run(
        "dpkg-query",
        ["dpkg-query", "-W", "-f", "${binary:Package}\t${Version}\t${db:Status-Abbrev}\n"],
        env,
        Path("/"),
        60,
    )
    if completed.returncode != 0:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "dpkg-query failed")
    found: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        package, version, status = (line.split("\t") + ["", ""])[:3]
        # ``ii `` is installed. ``apt-mark hold`` keeps that package installed
        # and changes the abbrev to ``hi ``. The third character is the error
        # flag, so a reinstall-required row (``iiR``, ``hiR``) is not installed.
        installed = status == "ii " or (include_hold and status == "hi ")
        if installed:
            found[package.split(":", 1)[0]] = version
    return found


def _builder_suite(sha256: str) -> str:
    if sha256 in _SECURITY_ONLY_SHA256:
        return "bookworm-security"
    return "bookworm"


def _selected_rows(recipe: Mapping[str, Any], section: str) -> list[dict[str, Any]]:
    closure = recipe[section]["closure"]["selected"]
    if type(closure) is not list or not closure:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"{section} closure is empty")
    rows: list[dict[str, Any]] = []
    for row in closure:
        if type(row) is not dict:
            _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"{section} row is not an object")
        rows.append(cast(dict[str, Any], row))
    return rows


def install_builder_closure(recipe: Mapping[str, Any]) -> None:
    """Install the recipe's builder bodies from their one snapshot URL each."""

    env = _tool_env(recipe)
    before = _dpkg_versions(env)
    expected = recipe["expected_base_inventory"]
    if type(expected) is not dict:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "expected base inventory missing")
    differences = [
        name for name in set(expected) | set(before) if expected.get(name) != before.get(name)
    ]
    if differences:
        shown = ",".join(sorted(differences)[:40])
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"base inventory differs: {shown}")
    paths: list[Path] = []
    for row in _selected_rows(recipe, "builder"):
        sha256 = str(row["SHA256"])
        size = int(row["Size"])
        filename = str(row["Filename"])
        url = SNAPSHOT_ROOTS[_builder_suite(sha256)] + filename
        destination = _DEB_ROOT / f"{sha256}.deb"
        _note(f"fetch {row['Package']}")
        _fetch_verified(url, destination, sha256, size)
        paths.append(destination)
    _apt_install(paths, env)
    after = _dpkg_versions(env)
    # H2a B3: new packages must land on the selected version, and the only
    # packages that were already in the base image and changed are base_changes.
    selected = _selected_rows(recipe, "builder")
    expected_delta = {str(row["Package"]): str(row["Version"]) for row in selected}
    changed_names = {
        name for name in set(before) | set(after) if before.get(name) != after.get(name)
    }
    unexpected = sorted(
        name for name in changed_names if expected_delta.get(name) != after.get(name)
    )
    missing = sorted(name for name, version in expected_delta.items() if after.get(name) != version)
    base_changed = sorted(name for name in changed_names if name in before)
    base_changes = recipe["builder"]["closure"]["base_changes"]
    if type(base_changes) is not list:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "base_changes missing")
    designed = {str(item["package"]) for item in base_changes}
    if unexpected or missing or set(base_changed) != designed:
        _fail(
            "POPPLER_RUNTIME_IDENTITY_INVALID",
            "builder base-package delta: "
            f"unexpected={unexpected[:12]} missing={missing[:12]} "
            f"base_changed={base_changed}",
        )


def _runtime_tool_env() -> dict[str, str]:
    return {
        "DEBIAN_FRONTEND": "noninteractive",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }


def _runtime_closure_bodies() -> tuple[DebianBody, ...]:
    bodies = (*RUNTIME_DT_NEEDED_BODIES, *RUNTIME_DEPENDS_BODIES)
    names = {body.package for body in bodies}
    if len(bodies) != _RUNTIME_CLOSURE_COUNT or len(names) != len(bodies):
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "runtime closure is not the 27 pinned bodies")
    return bodies


def _require_pinned_runtime_closure(installed: Mapping[str, str]) -> None:
    """Fail unless every closure package is installed (ii) at its pinned version."""

    missing = [
        body.package
        for body in _runtime_closure_bodies()
        if installed.get(body.package) != body.version
    ]
    if missing:
        shown = ",".join(missing[:12])
        _fail(
            "POPPLER_RUNTIME_IDENTITY_INVALID",
            f"runtime closure is not installed at the pinned version: {shown}",
        )


def install_runtime_closure() -> None:
    """Install the 23 DT_NEEDED bodies plus the computed Depends cut."""

    env = _runtime_tool_env()
    before = _dpkg_versions(env)
    paths: list[Path] = []
    for body in _runtime_closure_bodies():
        destination = _DEB_ROOT / f"{body.sha256}.deb"
        _note(f"fetch {body.package}")
        _fetch_verified(body.url, destination, body.sha256, body.size)
        paths.append(destination)
    _apt_install(paths, env)
    after = _dpkg_versions(env)
    for name in _HELD_BASE_PACKAGES:
        if before.get(name) != after.get(name):
            _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"{name} was upgraded")
    _require_pinned_runtime_closure(after)


def assert_runtime_closure() -> None:
    """Fail the build unless the 27 pinned bodies are still at their versions.

    ``apt-mark hold`` leaves an installed package in status ``hi`` rather than
    ``ii``. The post-distro check accepts both and still requires the pin.
    """

    installed = _dpkg_versions(_runtime_tool_env(), include_hold=True)
    _require_pinned_runtime_closure(installed)


def _rewrite(argv: Sequence[str], source_dir: Path, build_dir: Path, prefix: Path) -> list[str]:
    replacements = {
        "/inputs/poppler-25.12.0": str(source_dir),
        "/build/poppler": str(build_dir),
        "/opt/idis-native/poppler-25.12.0": str(prefix),
    }
    rewritten: list[str] = []
    for item in argv:
        text = str(item)
        for old, new in replacements.items():
            text = text.replace(old, new)
        rewritten.append(text)
    return rewritten


def _defined_one(text: str, macro: str) -> bool:
    return re.search(rf"^#define {macro} 1\s*$", text, re.M) is not None


def _undefined(text: str, macro: str) -> bool:
    return re.search(rf"^#define {macro}\b", text, re.M) is None


def _check_features(
    recipe: Mapping[str, Any],
    build_dir: Path,
    stdout: bytes,
    stderr: bytes,
) -> None:
    checks = recipe["builder"]["B1"]
    config_h = (build_dir / "config.h").read_text(encoding="utf-8")
    poppler_config = (build_dir / "poppler" / "poppler-config.h").read_text(encoding="utf-8")
    summary = stdout.decode("utf-8", "replace") + "\n" + stderr.decode("utf-8", "replace")
    failures: list[str] = []
    for macro in checks["config_h_defined_1"]:
        if not _defined_one(config_h, str(macro)):
            failures.append(str(macro))
    for macro in checks["config_h_undefined"]:
        if not _undefined(config_h, str(macro)):
            failures.append(f"!{macro}")
    for macro in checks["poppler_config_h_defined_1"]:
        if not _defined_one(poppler_config, str(macro)):
            failures.append(str(macro))
    for macro in checks["poppler_config_h_undefined"]:
        if not _undefined(poppler_config, str(macro)):
            failures.append(f"!{macro}")
    for label, expected in checks["summary_lines"].items():
        match = re.search(rf"^(?:--)?\s*{re.escape(str(label))}\s*:\s*(\S+)", summary, re.M)
        if match is None or match.group(1) != expected:
            failures.append(str(label))
    cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    rpath_match = re.search(r"^CMAKE_INSTALL_RPATH:[A-Z]+=(.*)$", cache, re.M)
    expected_rpath = str(recipe["builder"]["B2"]["install_rpath"])
    if rpath_match is None or rpath_match.group(1) != expected_rpath:
        failures.append("CMAKE_INSTALL_RPATH")
    if failures:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "B1 " + ",".join(failures))


def _configure_build_install(recipe: Mapping[str, Any], source_dir: Path, prefix: Path) -> None:
    env = _tool_env(recipe)
    build_dir = _WORK_ROOT / "build"
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    configure_argv = _rewrite(
        list(recipe["builder"]["configure_argv"]), source_dir, build_dir, prefix
    )
    configured = _run("configure", configure_argv, env, _WORK_ROOT, 1800)
    if configured.returncode != 0:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "configure failed")
    _check_features(recipe, build_dir, configured.stdout, configured.stderr)
    built = _run(
        "build",
        _rewrite(list(recipe["builder"]["build_argv"]), source_dir, build_dir, prefix),
        env,
        _WORK_ROOT,
        4 * 3600,
    )
    if built.returncode != 0:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "build failed")
    installed = _run(
        "install",
        _rewrite(list(recipe["builder"]["install_argv"]), source_dir, build_dir, prefix),
        env,
        _WORK_ROOT,
        600,
    )
    if installed.returncode != 0:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "install failed")


def _probe_prefix(prefix: Path, recipe: Mapping[str, Any]) -> None:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    version = str(recipe["poppler_version"])
    for tool in recipe["builder"]["probe_tools"]:
        name = str(tool)
        completed = _run(f"probe-{name}", [str(prefix / "bin" / name), "-v"], env, Path("/"), 60)
        text = (completed.stdout + completed.stderr).decode("utf-8", "replace")
        match = re.search(rf"{re.escape(name)} version (\d+\.\d+\.\d+)", text)
        if completed.returncode != 0 or match is None or match.group(1) != version:
            _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"probe {name}")


def _inventory(prefix: Path) -> tuple[dict[str, Path], dict[str, str]]:
    files: dict[str, Path] = {}
    links: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(prefix, followlinks=False):
        current = Path(dirpath)
        for name in dirnames:
            if (current / name).is_symlink():
                _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"directory symlink {name}")
        for name in filenames:
            child = current / name
            relative = child.relative_to(prefix).as_posix()
            if child.is_symlink():
                links[relative] = os.readlink(child)
            elif child.is_file() and not child.is_symlink():
                files[relative] = child
            else:
                _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"not a regular file {relative}")
    return files, links


def _require_elf(path: Path, relative: str, expected_rpath: str) -> None:
    notes = _read_elf_dynamic(path)
    if notes is None or notes.runpath_present or notes.rpath != expected_rpath:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"RPATH {relative}")
    if notes.rpath is None or "$ORIGIN" in notes.rpath:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"RPATH {relative}")
    if relative.startswith("bin/") and notes.interpreter != _INTERPRETER:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", f"interpreter {relative}")


def write_prefix_identity_manifest(prefix: Path, manifest_out: Path) -> str:
    """Write the closed-set manifest and return the SHA-256 of that file."""

    resolved = prefix.expanduser().resolve()
    files, links = _inventory(resolved)
    if set(files) != set(CLOSED_PREFIX_FILES) or links != CLOSED_PREFIX_SYMLINKS:
        _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "prefix closed set mismatch")
    expected_rpath = str(resolved / "lib")
    rows: list[dict[str, Any]] = []
    for relative in sorted(files):
        _sha, _size = _sha256_file(files[relative])
        elf = relative in ELF_PREFIX_FILES
        if elf:
            _require_elf(files[relative], relative, expected_rpath)
        rows.append({"elf": elf, "path": relative, "sha256": _sha})
    manifest = {
        "files": rows,
        "poppler_builder_recipe_sha256": RECIPE_SHA256,
        "poppler_source_sha256": SOURCE_SHA256,
        "poppler_source_size_bytes": SOURCE_SIZE_BYTES,
        "poppler_version": POPPLER_VERSION,
        "prefix": str(resolved),
        "rpath": expected_rpath,
        "schema_version": SCHEMA_VERSION,
        "symlinks": [
            {"path": path, "target": target}
            for path, target in sorted(CLOSED_PREFIX_SYMLINKS.items())
        ],
    }
    encoded = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    encoded += b"\n"
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_bytes(encoded)
    digest, _size = _sha256_file(manifest_out)
    return digest


def _parse_args(argv: Sequence[str]) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description="Build or install the governed Linux Poppler prefix."
    )
    parser.add_argument("--prefix", help="Absolute prefix directory to install into.")
    parser.add_argument("--recipe", help="Path to native/poppler/recipe-v2.json.")
    parser.add_argument("--source", help="Path to the verified poppler-25.12.0.tar.xz.")
    parser.add_argument("--manifest-out", help="Path of the closed-set manifest inside the prefix.")
    parser.add_argument(
        "--install-runtime-closure",
        action="store_true",
        help="Install the runtime DT_NEEDED bodies and their selected Depends.",
    )
    parser.add_argument(
        "--assert-runtime-closure",
        action="store_true",
        help="Assert each runtime closure package is ii at its pinned version.",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Verify the pins, build the prefix or install the runtime closure."""

    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.install_runtime_closure or args.assert_runtime_closure:
            if args.install_runtime_closure:
                install_runtime_closure()
            if args.assert_runtime_closure:
                assert_runtime_closure()
            return 0
        if not all((args.prefix, args.recipe, args.source, args.manifest_out)):
            _fail("POPPLER_RUNTIME_IDENTITY_INVALID", "prefix build requires four arguments")
        recipe = verify_recipe_file(Path(args.recipe))
        verify_source_archive(Path(args.source))
        source_dir = extract_source_archive(Path(args.source), _WORK_ROOT / "src")
        install_builder_closure(recipe)
        _configure_build_install(recipe, source_dir, Path(args.prefix))
        _probe_prefix(Path(args.prefix), recipe)
        digest = write_prefix_identity_manifest(Path(args.prefix), Path(args.manifest_out))
        print_manifest_sha256(digest)
    except BuildFailure as failure:
        print(failure.code, file=sys.stderr)
        if failure.detail:
            print(failure.detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
