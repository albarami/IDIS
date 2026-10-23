"""Emit the admitted v7 producer-selector bytes as a Python source module."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import zlib
from pathlib import Path
from types import ModuleType
from typing import Any


class SelectorGenerationError(RuntimeError):
    """Stable fail-closed selector source-generation error."""


def _load_v7_generator() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v7_contract_fixture.py")
    spec = importlib.util.spec_from_file_location(
        "_task7_v7_selector_source_generator",
        path,
    )
    if spec is None or spec.loader is None:
        raise SelectorGenerationError("TASK7_V7_SELECTOR_GENERATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V7 = _load_v7_generator()


def _load_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SelectorGenerationError("TASK7_V7_SELECTOR_FIXTURE_INVALID")
    return value


def _source(
    *,
    projection: dict[str, Any],
    artifact_sha256: str,
    contract_hash: str,
) -> bytes:
    canonical_rows = _V7._canonical_bytes(projection["rows"])
    compressed_hex = zlib.compress(canonical_rows, level=9).hex()
    chunks = [compressed_hex[index : index + 96] for index in range(0, len(compressed_hex), 96)]
    literal = "\n".join(f'    "{chunk}"' for chunk in chunks)
    rendered = f'''"""Generated full-strict-v7 producer-selector authority; do not edit."""

from __future__ import annotations

from typing import Final

PIPELINE_CONTRACT_VERSION: Final = "full-strict-v7"
PIPELINE_CONTRACT_HASH: Final = "{contract_hash}"
SOURCE_ARTIFACT_SHA256: Final = "{artifact_sha256}"
PRODUCER_SELECTOR_REGISTRY_SHA256: Final = "{projection["expected_sha256"]}"
PRODUCER_SELECTOR_ROW_COUNT: Final = {projection["row_count"]}
PRODUCER_SELECTOR_CANONICAL_JSON_SHA256: Final = "{hashlib.sha256(canonical_rows).hexdigest()}"
PRODUCER_SELECTOR_CANONICAL_JSON_ZLIB: Final = bytes.fromhex(
{literal}
)
'''
    return rendered.encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise SelectorGenerationError("TASK7_V7_SELECTOR_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise SelectorGenerationError("TASK7_V7_SELECTOR_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        artifact_raw = args.artifact.read_bytes()
        projection = _V7.derive_producer_selector_authority(artifact_raw)
        fixture = _load_fixture(args.fixture)
        artifact_sha256 = hashlib.sha256(artifact_raw).hexdigest()
        if (
            fixture.get("fixture_schema") != "full-strict-v7-contract-hash-fixture-v1"
            or fixture.get("artifact_sha256") != artifact_sha256
            or fixture.get("component_hashes", {}).get("producer_selector_registry_hash")
            != projection["expected_sha256"]
        ):
            raise SelectorGenerationError("TASK7_V7_SELECTOR_FIXTURE_MISMATCH")
        contract_hash = fixture.get("FULL_STRICT_V7_CONTRACT_HASH")
        if not isinstance(contract_hash, str) or len(contract_hash) != 64:
            raise SelectorGenerationError("TASK7_V7_SELECTOR_FIXTURE_MISMATCH")
        _write_exclusive(
            args.output,
            _source(
                projection=projection,
                artifact_sha256=artifact_sha256,
                contract_hash=contract_hash,
            ),
        )
    except (
        SelectorGenerationError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "TASK7_V7_PRODUCER_SELECTOR_MODULE_GENERATED:"
        f"rows={projection['row_count']}:"
        f"registry_sha256={projection['expected_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
