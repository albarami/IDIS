"""JSONL reader rejections and tree hashing."""

from __future__ import annotations

from pathlib import Path

import pytest

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.jsonl import (
    read_jsonl,
    reject_floats,
    sha256_text,
    tree_hash,
    write_jsonl,
)
from idis.methodology.obligations.models_records import SheetCount
from idis.methodology.obligations.vocab import Code


def test_jsonl_round_trip_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    records = (SheetCount(sheet="P&L", count=29), SheetCount(sheet="Assets", count=16))
    write_jsonl(path, records, key=lambda item: item.sheet)
    assert read_jsonl(path, SheetCount) == tuple(sorted(records, key=lambda item: item.sheet))
    assert path.read_bytes().endswith(b"\n")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_jsonl_rejects_duplicate_keys_bad_utf8_and_a_missing_newline(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_bytes(b'{"sheet":"P&L","sheet":"Assets","count":1}\n')
    with pytest.raises(Gl02Error) as duplicate_error:
        read_jsonl(duplicate, SheetCount)
    assert duplicate_error.value.code == Code.GL02_JSONL_DUPLICATE_KEY.value
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_bytes(b"\xff\n")
    with pytest.raises(Gl02Error) as utf8_error:
        read_jsonl(invalid, SheetCount)
    assert utf8_error.value.code == Code.GL02_JSONL_INVALID_UTF8.value
    missing = tmp_path / "missing.jsonl"
    missing.write_bytes(b'{"sheet":"P&L","count":1}')
    with pytest.raises(Gl02Error) as newline_error:
        read_jsonl(missing, SheetCount)
    assert newline_error.value.code == Code.GL02_JSONL_MISSING_NEWLINE.value


def test_jsonl_rejects_a_carriage_return(tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.jsonl"
    crlf.write_bytes(b'{"count":1,"sheet":"P&L"}\r\n')
    with pytest.raises(Gl02Error) as error:
        read_jsonl(crlf, SheetCount)
    assert error.value.code == Code.GL02_JSONL_INVALID_RECORD.value
    embedded = tmp_path / "embedded.jsonl"
    embedded.write_bytes(b'{"count":1,"sheet":"P&L\r"}\n')
    with pytest.raises(Gl02Error) as embedded_error:
        read_jsonl(embedded, SheetCount)
    assert embedded_error.value.code == Code.GL02_JSONL_INVALID_RECORD.value


def test_floats_are_rejected() -> None:
    with pytest.raises(Gl02Error) as error:
        reject_floats({"count": 1.5})
    assert error.value.code == Code.GL02_JSONL_FLOAT.value


def test_tree_hash_is_stable(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    for root in (first, second):
        (root / "authored").mkdir(parents=True)
        (root / "authored" / "note.txt").write_text("Fictional\n", encoding="utf-8", newline="\n")
    assert tree_hash(first) == tree_hash(second)
    assert tree_hash(first) == sha256_text("authored/note.txt " + sha256_text("Fictional\n") + "\n")
