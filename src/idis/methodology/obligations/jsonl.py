"""Deterministic JSONL and tree hashing.

Every writer uses ``json.dumps(..., sort_keys=True, ensure_ascii=False,
separators=(",", ":"))`` plus a single ``\\n``. Files are UTF-8 without a BOM
and are opened with ``newline="\\n"``. Values are integers, booleans, strings,
nulls, arrays and objects. Floats are rejected.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.vocab import Code

T = TypeVar("T", bound=BaseModel)

_BOM = b"\xef\xbb\xbf"


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(value).hexdigest()


def reject_floats(value: object) -> None:
    """Raise when a JSON-ready object contains a float."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        raise Gl02Error(Code.GL02_JSONL_FLOAT.value)
    if isinstance(value, dict):
        for item in value.values():
            reject_floats(item)
        return
    if isinstance(value, list):
        for item in value:
            reject_floats(item)
        return
    raise Gl02Error(Code.GL02_JSONL_FLOAT.value)


def canonical_json(payload: object) -> str:
    """Serialize one object with sorted keys and compact separators."""
    reject_floats(payload)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_json(path: Path, payload: object) -> None:
    """Write one JSON object, UTF-8, LF, no BOM."""
    text = canonical_json(payload) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_json(path: Path) -> object:
    """Read one JSON object, rejecting a BOM, bad UTF-8 and duplicate keys."""
    raw = _read_utf8(path)
    if raw and not raw.endswith(b"\n"):
        raise Gl02Error(Code.GL02_JSONL_MISSING_NEWLINE.value)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Gl02Error(Code.GL02_JSONL_INVALID_RECORD.value) from exc


def dump_model(record: BaseModel) -> dict[str, object]:
    """Dump a model to a JSON-ready object with nulls retained."""
    payload = record.model_dump(mode="json")
    reject_floats(payload)
    return payload


def write_jsonl(path: Path, records: Sequence[T], *, key: Callable[[T], object]) -> None:
    """Write records sorted by ``key``, one canonical object per line."""
    ordered = sorted(records, key=key)  # type: ignore[arg-type]
    lines = [canonical_json(dump_model(record)) for record in ordered]
    text = "" if not lines else "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_jsonl(path: Path, model: type[T]) -> tuple[T, ...]:
    """Read JSONL. Reject duplicate keys, bad UTF-8, carriage returns and bad records."""
    raw = _read_utf8(path)
    if raw == b"":
        return ()
    if not raw.endswith(b"\n"):
        raise Gl02Error(Code.GL02_JSONL_MISSING_NEWLINE.value)
    text = raw.decode("utf-8")
    records: list[T] = []
    for line in text.split("\n")[:-1]:
        if line == "" or "\r" in line:
            raise Gl02Error(Code.GL02_JSONL_INVALID_RECORD.value)
        try:
            payload = json.loads(line, object_pairs_hook=_unique_object)
            records.append(model.model_validate(payload))
        except (json.JSONDecodeError, ValidationError, TypeError, Gl02Error) as exc:
            if isinstance(exc, Gl02Error):
                raise
            raise Gl02Error(Code.GL02_JSONL_INVALID_RECORD.value) from exc
    return tuple(records)


def tree_hash(root: Path) -> str:
    """Hash a directory as sorted relative paths paired with file hashes.

    The digest is SHA-256 over ``relative/path<space>hex`` lines joined by
    ``\\n`` and terminated by a final ``\\n``. Paths use forward slashes.
    """
    lines: list[str] = []
    if root.is_dir():
        files = [
            path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts
        ]
        for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            lines.append(f"{relative} {sha256_bytes(path.read_bytes())}")
    body = "\n".join(lines) + ("\n" if lines else "")
    return sha256_text(body)


def _read_utf8(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Gl02Error(Code.GL02_IO.value) from exc
    if raw.startswith(_BOM):
        raise Gl02Error(Code.GL02_JSONL_INVALID_UTF8.value)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Gl02Error(Code.GL02_JSONL_INVALID_UTF8.value) from exc
    return raw


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Gl02Error(Code.GL02_JSONL_DUPLICATE_KEY.value)
        result[key] = value
    return result
