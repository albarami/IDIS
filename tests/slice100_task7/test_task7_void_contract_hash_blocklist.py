from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from tests.slice100_task7._void_contract_hash_evidence import (
    VOID_HASHES,
)
from tests.slice100_task7._void_contract_hash_evidence_postfreeze import (
    FILE_SHA256,
    POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX,
    POSTFREEZE_VOID_HASHES,
    RAW_LITERAL,
    VOID_HASH_EVIDENCE_MATRIX,
    VOID_V19_CANDIDATE_ARTIFACT_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALL_VOID_HASHES = VOID_HASHES + POSTFREEZE_VOID_HASHES


def _repository_files() -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        _REPO_ROOT / Path(raw_path.decode("utf-8"))
        for raw_path in completed.stdout.split(b"\0")
        if raw_path
    ]


def _decoded_json_hex_payloads(value: Any) -> list[bytes]:
    payloads: list[bytes] = []
    if isinstance(value, dict):
        for child in value.values():
            payloads.extend(_decoded_json_hex_payloads(child))
    elif isinstance(value, list):
        for child in value:
            payloads.extend(_decoded_json_hex_payloads(child))
    elif isinstance(value, str) and len(value) >= 64 and len(value) % 2 == 0:
        with contextlib.suppress(ValueError):
            payloads.append(bytes.fromhex(value))
    return payloads


def _scan_void_hash_occurrences(
    paths: tuple[Path, ...] | list[Path],
    *,
    repo_root: Path,
    void_hashes: tuple[bytes, ...],
) -> Counter[tuple[str, str, str]]:
    occurrences: Counter[tuple[str, str, str]] = Counter()
    for path in paths:
        if not path.is_file():
            continue
        content = path.read_bytes()
        relative_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
        searchable_payloads = [content]
        if path.suffix.lower() == ".json":
            with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
                searchable_payloads.extend(
                    _decoded_json_hex_payloads(json.loads(content.decode("utf-8")))
                )

        file_sha256 = hashlib.sha256(content).hexdigest()
        for value in void_hashes:
            hash_value = value.decode("ascii")
            raw_count = sum(payload.count(value) for payload in searchable_payloads)
            if raw_count:
                occurrences[(hash_value, relative_path, RAW_LITERAL)] += raw_count
            if file_sha256 == hash_value:
                occurrences[(hash_value, relative_path, FILE_SHA256)] += 1
    return occurrences


def _is_active_admission_path(relative_path: str) -> bool:
    path = Path(relative_path)
    posix_path = path.as_posix()
    if posix_path.startswith("src/idis/"):
        return True
    if posix_path == "contracts/contract_lock.json":
        return True
    if "fixtures" in {part.lower() for part in path.parts}:
        return True
    return "v20" in path.name.lower() and path.parts[0] in {"contracts", "scripts", "tests"}


def test_void_hash_hidden_in_json_hex_payload_is_decoded_for_blocklist_scan() -> None:
    hidden = {"reviewed_design_utf8_hex": VOID_HASHES[0].hex()}

    assert VOID_HASHES[0] in _decoded_json_hex_payloads(hidden)


def test_void_hash_scanner_records_raw_literals_and_file_digests(tmp_path: Path) -> None:
    raw_hash = "a" * 64
    raw_path = tmp_path / "raw.py"
    raw_path.write_text(raw_hash, encoding="utf-8")
    digest_path = tmp_path / "artifact.json"
    digest_path.write_bytes(b"historical artifact bytes")
    artifact_hash = hashlib.sha256(digest_path.read_bytes()).hexdigest()

    observed = _scan_void_hash_occurrences(
        (raw_path, digest_path),
        repo_root=tmp_path,
        void_hashes=(raw_hash.encode("ascii"), artifact_hash.encode("ascii")),
    )

    assert observed == Counter(
        {
            (raw_hash, "raw.py", RAW_LITERAL): 1,
            (artifact_hash, "artifact.json", FILE_SHA256): 1,
        }
    )


def test_postfreeze_blocklist_extends_without_mutating_signed_census_evidence() -> None:
    assert len(POSTFREEZE_VOID_HASHES) == 6
    assert set(POSTFREEZE_VOID_HASHES).isdisjoint(VOID_HASHES)


def test_postfreeze_evidence_matrix_is_exact_and_covers_v19_file_digest() -> None:
    keys = [
        (hash_value, path, representation)
        for hash_value, path, representation, _ in POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX
    ]

    assert len(keys) == len(set(keys))
    assert {representation for _, _, representation, _ in POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX} == {
        RAW_LITERAL,
        FILE_SHA256,
    }
    assert all(expected_count > 0 for *_, expected_count in POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX)
    assert (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "contracts/full_strict_v19_reviewed_preimage_v1.json",
        FILE_SHA256,
        1,
    ) in POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX


def test_void_hash_evidence_matrix_matches_every_repository_occurrence_exactly() -> None:
    keys = [
        (hash_value, path, representation)
        for hash_value, path, representation, _ in VOID_HASH_EVIDENCE_MATRIX
    ]
    assert len(keys) == len(set(keys))
    assert all(expected_count > 0 for *_, expected_count in VOID_HASH_EVIDENCE_MATRIX)
    assert {representation for _, _, representation, _ in VOID_HASH_EVIDENCE_MATRIX} == {
        RAW_LITERAL,
        FILE_SHA256,
    }
    assert {hash_value for hash_value, *_ in VOID_HASH_EVIDENCE_MATRIX} == {
        value.decode("ascii") for value in _ALL_VOID_HASHES
    }

    expected = Counter(
        {
            (hash_value, path, representation): expected_count
            for hash_value, path, representation, expected_count in VOID_HASH_EVIDENCE_MATRIX
        }
    )
    observed = _scan_void_hash_occurrences(
        _repository_files(),
        repo_root=_REPO_ROOT,
        void_hashes=_ALL_VOID_HASHES,
    )

    assert observed == expected


def test_void_hashes_are_absent_from_all_active_admission_surfaces() -> None:
    observed = _scan_void_hash_occurrences(
        _repository_files(),
        repo_root=_REPO_ROOT,
        void_hashes=_ALL_VOID_HASHES,
    )
    active_offenders = {
        key: count for key, count in observed.items() if _is_active_admission_path(key[1])
    }

    assert active_offenders == {}
