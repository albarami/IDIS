from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Literal, cast

import pytest
from scripts import forbidden_scan as scanner

from tests.slice100_task8 import conftest as task8_conftest
from tests.slice100_task8 import test_task8_historical_candidate_collection_boundary as boundary
from tests.slice100_task8._task8_void_contract_hash_evidence import (
    VOID_V26_FROZEN_FILE_SHA256_BY_PATH,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVED_PATHS = (
    "tests/slice100_task8/test_task8_fixture_derivation_red.py",
    "tests/slice100_task8/test_v24_sql_authority_postgres_census_red.py",
)
BANNED_WORD = (
    dict(scanner.BANNED_TOKEN_PATTERNS_TESTS)["BANNED_PLACEHOLDER"]
    .pattern.removeprefix(r"\b")
    .removesuffix(r"\b")
)
SYNTHETIC_PATH = "tests/archived_negative.py"


def _write(root: Path, relative_path: str, raw: bytes) -> Path:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return target


def _acknowledgement(
    raw: bytes,
    *,
    path: str = SYNTHETIC_PATH,
    line: int = 1,
) -> scanner.FrozenNegativeAcknowledgement:
    return scanner.FrozenNegativeAcknowledgement(
        path, hashlib.sha256(raw).hexdigest(), line, "BANNED_PLACEHOLDER"
    )


def _codes(matches: list[scanner.Match]) -> set[str]:
    return {match.pattern_name for match in matches}


def test_owned_archived_negative_assertions_are_acknowledged(tmp_path: Path) -> None:
    for relative_path in ARCHIVED_PATHS:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative_path).read_bytes())
    assert scanner.scan_repository(tmp_path) == []


def test_literal_acknowledgements_match_exact_archived_owner_pins() -> None:
    v26_pins = dict(VOID_V26_FROZEN_FILE_SHA256_BY_PATH)
    v24_pins = {
        f"tests/slice100_task8/{name}": digest
        for name, digest in boundary._EXPECTED_ARCHIVED_V24_MODULE_SHA256.items()
    }
    expected = (
        (ARCHIVED_PATHS[0], v26_pins[ARCHIVED_PATHS[0]], 305, "BANNED_PLACEHOLDER"),
        (ARCHIVED_PATHS[1], v24_pins[ARCHIVED_PATHS[1]], 960, "BANNED_PLACEHOLDER"),
        (ARCHIVED_PATHS[1], v24_pins[ARCHIVED_PATHS[1]], 961, "BANNED_PLACEHOLDER"),
    )
    assert expected == scanner.FROZEN_NEGATIVE_ACKNOWLEDGEMENTS
    archived = task8_conftest.ARCHIVED_V24_MODULE_PATHS | task8_conftest.ARCHIVED_V26_MODULE_PATHS
    for acknowledgement in scanner.FROZEN_NEGATIVE_ACKNOWLEDGEMENTS:
        assert acknowledgement.file_path in archived
        raw = (REPO_ROOT / acknowledgement.file_path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == acknowledgement.raw_sha256
        line = raw.decode("utf-8").splitlines()[acknowledgement.line_number - 1]
        assert dict(scanner.BANNED_TOKEN_PATTERNS_TESTS)["BANNED_PLACEHOLDER"].search(line)
    assert {"forbidden_scan.py", "poetry.lock", "package-lock.json"} == scanner.EXCLUDED_FILES


@pytest.mark.parametrize("newline", ("\n", "\r\n"))
def test_raw_hash_acknowledgement_accepts_only_its_pattern(newline: str, tmp_path: Path) -> None:
    raw = (BANNED_WORD + newline).encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    assert scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),)) == []


@pytest.mark.parametrize("drift", ("hash", "line", "unused", "missing", "newline"))
def test_stale_acknowledgement_fails_closed(drift: str, tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    acknowledgement = _acknowledgement(raw)
    if drift == "hash":
        raw += b" "
    elif drift == "line":
        acknowledgement = acknowledgement._replace(line_number=2)
    elif drift == "unused":
        raw = b"clean\n"
        acknowledgement = _acknowledgement(raw)
    elif drift == "newline":
        raw = raw.replace(b"\n", b"\r\n")
    if drift != "missing":
        _write(tmp_path, SYNTHETIC_PATH, raw)
    codes = _codes(scanner.scan_repository(tmp_path, acknowledgements=(acknowledgement,)))
    expected = {
        "hash": "FROZEN_ACK_HASH_MISMATCH",
        "line": "FROZEN_ACK_UNUSED",
        "unused": "FROZEN_ACK_UNUSED",
        "missing": "FROZEN_ACK_FILE_MISSING",
        "newline": "FROZEN_ACK_HASH_MISMATCH",
    }
    assert expected[drift] in codes
    if drift in {"hash", "line", "newline"}:
        assert "BANNED_PLACEHOLDER" in codes


@pytest.mark.parametrize(
    "relative_path",
    ("tests/moved_negative.py", "src/active.py", "tests/nested/archived_negative.py"),
)
def test_identical_bytes_at_another_path_are_not_acknowledged(
    relative_path: str, tmp_path: Path
) -> None:
    raw = (BANNED_WORD + "\n").encode()
    _write(tmp_path, relative_path, raw)
    codes = _codes(scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),)))
    assert codes == {"BANNED_PLACEHOLDER", "FROZEN_ACK_FILE_MISSING"}


@pytest.mark.parametrize("line", (0, -1))
def test_nonpositive_acknowledged_line_is_invalid(line: int, tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    codes = _codes(
        scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw, line=line),))
    )
    assert codes == {"BANNED_PLACEHOLDER", "FROZEN_ACK_INVALID"}


@pytest.mark.parametrize(
    "path",
    (
        "../tests/archived_negative.py",
        "/tests/archived_negative.py",
        "tests/../tests/archived_negative.py",
        "tests\\archived_negative.py",
        "C:/tests/archived_negative.py",
        "src/active.py",
    ),
)
def test_unexpected_acknowledgement_coordinate_is_invalid(path: str, tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    codes = _codes(
        scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw, path=path),))
    )
    assert codes == {"BANNED_PLACEHOLDER", "FROZEN_ACK_INVALID"}


def test_duplicate_acknowledgement_fails_closed(tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    acknowledgement = _acknowledgement(raw)
    assert "FROZEN_ACK_INVALID" in _codes(
        scanner.scan_repository(tmp_path, acknowledgements=(acknowledgement, acknowledgement))
    )


def test_excluded_file_cannot_silently_consume_acknowledgement(tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    path = "tests/forbidden_scan.py"
    _write(tmp_path, path, raw)
    assert _codes(
        scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw, path=path),))
    ) == {"FROZEN_ACK_UNUSED"}


def test_another_banned_line_is_still_reported(tmp_path: Path) -> None:
    raw = f"{BANNED_WORD}\n{BANNED_WORD}\n".encode()
    target = _write(tmp_path, SYNTHETIC_PATH, raw)
    assert scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),)) == [
        scanner.Match(str(target), 2, "BANNED_PLACEHOLDER")
    ]


@pytest.mark.parametrize("relative_path", ("src/active.py", "tests/active.py"))
def test_unacknowledged_banned_content_fails(relative_path: str, tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    target = _write(tmp_path, relative_path, raw)
    assert scanner.scan_repository(tmp_path, acknowledgements=()) == [
        scanner.Match(str(target), 1, "BANNED_PLACEHOLDER")
    ]


@pytest.mark.parametrize("sensitive", ("OPENAI_API_KEY", "UNSAFE_JSONB_CAST"))
def test_secret_and_sql_patterns_survive_on_acknowledged_line(
    sensitive: str, tmp_path: Path
) -> None:
    sample = f"sk-{'A' * 20}" if sensitive == "OPENAI_API_KEY" else ":{}::{}".format("p", "jsonb")
    raw = f"{BANNED_WORD} {sample}\n".encode()
    target = _write(tmp_path, SYNTHETIC_PATH, raw)
    assert scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),)) == [
        scanner.Match(str(target), 1, sensitive)
    ]


@pytest.mark.parametrize(
    "pattern_name", [name for name, _pattern in scanner.SECRET_PATTERNS + scanner.SQL_PATTERNS]
)
def test_every_security_pattern_remains_active_on_acknowledged_evidence(
    pattern_name: str, tmp_path: Path
) -> None:
    samples = {
        "PRIVATE_KEY_HEADER": "{}BEGIN RSA PRIVATE KEY".format("-" * 5),
        "PRIVATE_KEY_GENERIC": "BEGIN {} KEY".format("PRIVATE"),
        "OPENAI_API_KEY": f"sk-{'A' * 20}",
        "AWS_SECRET_KEY": "{}={}".format("AWS_SECRET_ACCESS_KEY", "A" * 30),
        "AWS_ACCESS_KEY_ID": f"AKIA{'A' * 16}",
        "SLACK_BOT_TOKEN": "xoxb-{}-{}-{}".format("1" * 10, "2" * 10, "A" * 24),
        "SLACK_USER_TOKEN": "xoxp-{}-{}-{}".format("1" * 10, "2" * 10, "A" * 24),
        "HARDCODED_PASSWORD": "{}='{}'".format("password", "A" * 8),
        "PGPASSWORD_ENV": "{}={}".format("PGPASSWORD", "A" * 8),
        "GITHUB_TOKEN": f"ghp_{'A' * 36}",
        "GITHUB_OAUTH": f"gho_{'A' * 36}",
        "GENERIC_API_KEY": "{}='{}'".format("api_key", "A" * 20),
        "BEARER_TOKEN": "Bearer {}".format("A" * 40),
        "UNSAFE_JSONB_CAST": ":{}::{}".format("p", "jsonb"),
    }
    raw = f"{BANNED_WORD} {samples[pattern_name]}\n".encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    codes = _codes(scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),)))
    assert pattern_name in codes
    assert "BANNED_PLACEHOLDER" not in codes


def test_nonacknowledged_patterns_are_all_evaluated_on_one_line(tmp_path: Path) -> None:
    sample = f"sk-{'A' * 20} " + ":{}::{}".format("p", "jsonb")
    raw = f"{BANNED_WORD} {sample}\n".encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    assert _codes(scanner.scan_repository(tmp_path, acknowledgements=())) == {
        "BANNED_PLACEHOLDER",
        "OPENAI_API_KEY",
        "UNSAFE_JSONB_CAST",
    }


@pytest.mark.parametrize("bypass", ("REDACTED", "# example"))
def test_acknowledged_lines_never_bypass_secret_or_sql_checks(bypass: str, tmp_path: Path) -> None:
    sample = f"sk-{'A' * 20} " + ":{}::{}".format("p", "jsonb")
    raw = f"{bypass} {BANNED_WORD} {sample}\n".encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    assert _codes(scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),))) == {
        "OPENAI_API_KEY",
        "UNSAFE_JSONB_CAST",
    }


@pytest.mark.parametrize("field", ("raw_sha256", "pattern_name"))
def test_invalid_digest_or_pattern_cannot_suppress_a_match(field: str, tmp_path: Path) -> None:
    raw = (BANNED_WORD + "\n").encode()
    _write(tmp_path, SYNTHETIC_PATH, raw)
    acknowledgement = _acknowledgement(raw)
    acknowledgement = (
        acknowledgement._replace(raw_sha256="invalid")
        if field == "raw_sha256"
        else acknowledgement._replace(pattern_name=cast(Literal["BANNED_PLACEHOLDER"], "invalid"))
    )
    assert _codes(scanner.scan_repository(tmp_path, acknowledgements=(acknowledgement,))) == {
        "BANNED_PLACEHOLDER",
        "FROZEN_ACK_INVALID",
    }


def test_unreadable_acknowledged_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = (BANNED_WORD + "\n").encode()
    target = _write(tmp_path, SYNTHETIC_PATH, raw)
    original = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == target:
            raise PermissionError("synthetic read failure")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert _codes(scanner.scan_repository(tmp_path, acknowledgements=(_acknowledgement(raw),))) == {
        "FROZEN_ACK_FILE_UNREADABLE"
    }


def test_cli_failure_is_nonzero_and_never_echoes_matching_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = f"sk-{'A' * 20}"
    _write(tmp_path, "src/active.py", sample.encode())
    monkeypatch.setattr(sys, "argv", ["forbidden_scan", "--repo-root", str(tmp_path)])
    assert scanner.main() == 1
    output = capsys.readouterr()
    assert "OPENAI_API_KEY" in output.out
    assert sample not in output.out + output.err
