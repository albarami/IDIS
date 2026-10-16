"""Execute the unfrozen full-strict-v16 guard census as the application role.

Administrator connections are setup-only: revision 0037 migration, principal
provisioning, artifact/RLS/ACL installation, committed trigger toggles, and
between-case cleanup.  Constructor writes and every legal, illegal, and direct
SQL guard operation execute through the generated NOSUPERUSER/NOBYPASSRLS
application engine.
"""

from __future__ import annotations

import _io
import atexit
import base64
import builtins
import copy
import ctypes
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import json
import math
import os
import re
import secrets
import stat
import sys
import unicodedata
import uuid
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError


class CensusError(RuntimeError):
    """Secret-safe v16 application-role census failure."""


_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
_EXPECTED_ARTIFACT_SHA256: Final = (
    "dd7b2585ff01909765342549bf60b4ba32d18cec398c8b002a77c55d0f92ce74"
)
_EXPECTED_ARTIFACT_SHA512: Final = (
    "f002c51ed7ecc9728a98e5f6f23825e235368823fc19a9e41564e90fa2f96925"
    "d9c02f347cc506378da90ee4d4a7ff6bda48184c0a209256d86ea073e408ae2d"
)
_EXPECTED_PROSPECTIVE_FROZEN_SHA256: Final = (
    "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
)
_HARNESS_MANIFEST_DOMAIN: Final = b"task7-v16-harness-source-manifest-v1\x00"

# Literal reviewed bytes for every repository-local harness source executed by
# the v16 authority.  Product modules under src/idis and migration modules are
# deliberately code under test, not harness authorities.
HARNESS_SOURCE_MANIFEST: Final = (
    (
        "scripts/_task7_v11_census_scaffold.py",
        3922,
        "1e1b40c3b1515aac23dae130e36a533bd846a64a1bc99a49ffef17e1a576811f",
        "raw-scaffold-execution",
        (
            "scripts._task7_v11_census_scaffold",
            "_task7_v11_census_scaffold_core_for_v16",
        ),
    ),
    (
        "scripts/_task7_v16_census_scaffold.py",
        12035,
        "07da0191eca16bab01dbe15ad6fc9f6ad883fdc3106fc989eb7d233a4a6de7bb",
        "canonical-scaffold",
        ("scripts._task7_v16_census_scaffold",),
    ),
    (
        "scripts/_task7_v9_census_scaffold.py",
        29812,
        "7ed00e544d3bc92803bd933c1e083829a92d62f6eddad82c594a10fe6a8f645c",
        "raw-scaffold-execution",
        ("_task7_v9_census_scaffold_core_for_v11",),
    ),
    (
        "scripts/generate_task7_v10_contract_fixture.py",
        32398,
        "b26908179dd7c3f4979b4b490a4ddbb925a248e4dde1845bad59ea10670aa13d",
        "fixture-derivation-authority",
        ("_task7_v10_fixture_helpers_for_v11",),
    ),
    (
        "scripts/generate_task7_v11_contract_fixture.py",
        30571,
        "210fa1195b5340c3cbd89c72c1566c628e34512c3c3dc3bdbd2af733397fe800",
        "fixture-derivation-authority",
        (
            "_task7_v11_census_receipt_generator",
            "_task7_v11_census_generator",
        ),
    ),
    (
        "scripts/generate_task7_v4_contract_fixture.py",
        179805,
        "71e46640022bed45b18e77a68c8924451891ba8331907bf8998b8ebbde7490c6",
        "fixture-derivation-authority",
        ("_task7_v4_fixture_helpers",),
    ),
    (
        "scripts/generate_task7_v5_contract_fixture.py",
        20117,
        "a71bd136500a3ab47a59fd4d14a41a0f3d58c713060f518ea8508f01c0dd8945",
        "fixture-derivation-authority",
        ("_task7_v5_fixture_helpers",),
    ),
    (
        "scripts/generate_task7_v6_contract_fixture.py",
        12164,
        "f73ea3108fc99c90231c289ea79ec6a66e0e51cc8a3f1569e445ab62e875dca8",
        "fixture-derivation-authority",
        ("_task7_v6_fixture_helpers",),
    ),
    (
        "scripts/generate_task7_v7_contract_fixture.py",
        21136,
        "bb41444cb68bbe2ecdec307a30cb93c26ab319a7951cb22cae3c6ab90004de3d",
        "fixture-derivation-authority",
        ("_task7_v7_fixture_helpers_for_v9",),
    ),
    (
        "scripts/generate_task7_v9_contract_fixture.py",
        33420,
        "010695b5ea336ab0a8b8f34932ba245b16822a877ba0a4f7cc360dda3314fae5",
        "fixture-derivation-authority",
        ("_task7_v9_fixture_helpers_for_v10",),
    ),
    (
        "scripts/materialize_full_strict_v10_preimage.py",
        12499,
        "7f0b90f100fc1197e2a12b6bfdc891bf5c4cfa7671b39f9aaec5d7e677a50b69",
        "fixture-derivation-authority",
        ("_task7_v10_materializer_helpers",),
    ),
    (
        "scripts/materialize_full_strict_v11_preimage.py",
        29067,
        "240c1d116775719c03a658c4663b13b03821d366ffd6d6a6803cf2f2a5dbbb75",
        "fixture-derivation-authority",
        ("_task7_v11_materializer_helpers",),
    ),
    (
        "scripts/run_task7_v11_current_guard_census.py",
        99659,
        "7c3ced9c8a61923ea70aeee1a30cb71584d152fc7d56f4ac95dffc24d56d307e",
        "raw-adapted-execution",
        ("_task7_v11_current_guard_engine_for_v16",),
    ),
    (
        "scripts/run_task7_v9_current_guard_census.py",
        66554,
        "8954f62c37d31089aab50c6506d2c1273ae938c3982c49fd64b3b8cbdd30869d",
        "raw-adapted-execution",
        ("_task7_v11_current_guard_census_engine",),
    ),
    (
        "tests/__init__.py",
        24,
        "b4a8ffb0cdf0100e67cb8cda8f37e244948ecaa5ea8c4f5d579855f1def3d9da",
        "package-initializer",
        ("tests",),
    ),
    (
        "tests/slice100_task2b/__init__.py",
        52,
        "9b66f201a8709633597c01cd07bddaa862de0667e083d5701469550a94c117f0",
        "postgres-composition-support",
        ("tests.slice100_task2b",),
    ),
    (
        "tests/slice100_task2b/_support.py",
        16766,
        "fd0e634cb807e0128e1f808e9a0ace7fe5595153c87ccc03741a48b1d1d4536c",
        "postgres-composition-support",
        ("tests.slice100_task2b._support",),
    ),
    (
        "tests/slice100_task7/_void_contract_hash_evidence.py",
        5430,
        "c121b4fe0d20933e377c3c98c4643dce000a181af70b1931028036cee7419306",
        "void-evidence-authority",
        ("tests.slice100_task7._void_contract_hash_evidence",),
    ),
    (
        "tests/slice100_task7/test_full_strict_v9_native_schema_red.py",
        27511,
        "4d229c5da823488e90b145010dcaba5695d2be434edf5a419603f5ebada298d9",
        "constructor-payload-authority",
        ("tests.slice100_task7.test_full_strict_v9_native_schema_red",),
    ),
    (
        "tests/test_run_methodology_deterministic_calculation_service.py",
        19274,
        "047e6e1a994e92e2475053ec25253d5d1495a1c6cdf4caf5250c3112d446c507",
        "constructor-payload-authority",
        ("tests.test_run_methodology_deterministic_calculation_service",),
    ),
    (
        "tests/test_run_methodology_validated_evidence_package_service.py",
        10774,
        "29ea02e66e5390868859dc474de9f966c9324b002327cf17a034f894db49d559",
        "constructor-payload-authority",
        ("tests.test_run_methodology_validated_evidence_package_service",),
    ),
)

# Ordered multiset: repeated coordinates are intentional executions, not
# duplicate-authority noise.  In particular, the two V11 generator aliases
# rebuild the same pinned descendant chain independently.
HARNESS_SOURCE_EXECUTION_COORDINATES: Final = (
    (
        "scripts/_task7_v11_census_scaffold.py",
        "_task7_v11_census_scaffold_core_for_v16",
        "raw-exact",
        "1e1b40c3b1515aac23dae130e36a533bd846a64a1bc99a49ffef17e1a576811f",
    ),
    (
        "scripts/_task7_v11_census_scaffold.py",
        "scripts._task7_v11_census_scaffold",
        "manifest-exact",
        "1e1b40c3b1515aac23dae130e36a533bd846a64a1bc99a49ffef17e1a576811f",
    ),
    (
        "scripts/_task7_v16_census_scaffold.py",
        "scripts._task7_v16_census_scaffold",
        "manifest-exact",
        "07da0191eca16bab01dbe15ad6fc9f6ad883fdc3106fc989eb7d233a4a6de7bb",
    ),
    (
        "scripts/_task7_v9_census_scaffold.py",
        "_task7_v9_census_scaffold_core_for_v11",
        "raw-exact",
        "7ed00e544d3bc92803bd933c1e083829a92d62f6eddad82c594a10fe6a8f645c",
    ),
    (
        "scripts/_task7_v9_census_scaffold.py",
        "_task7_v9_census_scaffold_core_for_v11",
        "raw-exact",
        "7ed00e544d3bc92803bd933c1e083829a92d62f6eddad82c594a10fe6a8f645c",
    ),
    *(
        (
            path,
            alias,
            "manifest-exact",
            sha256,
        )
        for path, alias, sha256 in (
            (
                "scripts/generate_task7_v10_contract_fixture.py",
                "_task7_v10_fixture_helpers_for_v11",
                "b26908179dd7c3f4979b4b490a4ddbb925a248e4dde1845bad59ea10670aa13d",
            ),
            (
                "scripts/generate_task7_v10_contract_fixture.py",
                "_task7_v10_fixture_helpers_for_v11",
                "b26908179dd7c3f4979b4b490a4ddbb925a248e4dde1845bad59ea10670aa13d",
            ),
            (
                "scripts/generate_task7_v11_contract_fixture.py",
                "_task7_v11_census_generator",
                "210fa1195b5340c3cbd89c72c1566c628e34512c3c3dc3bdbd2af733397fe800",
            ),
            (
                "scripts/generate_task7_v11_contract_fixture.py",
                "_task7_v11_census_receipt_generator",
                "210fa1195b5340c3cbd89c72c1566c628e34512c3c3dc3bdbd2af733397fe800",
            ),
            (
                "scripts/generate_task7_v4_contract_fixture.py",
                "_task7_v4_fixture_helpers",
                "71e46640022bed45b18e77a68c8924451891ba8331907bf8998b8ebbde7490c6",
            ),
            (
                "scripts/generate_task7_v4_contract_fixture.py",
                "_task7_v4_fixture_helpers",
                "71e46640022bed45b18e77a68c8924451891ba8331907bf8998b8ebbde7490c6",
            ),
            (
                "scripts/generate_task7_v5_contract_fixture.py",
                "_task7_v5_fixture_helpers",
                "a71bd136500a3ab47a59fd4d14a41a0f3d58c713060f518ea8508f01c0dd8945",
            ),
            (
                "scripts/generate_task7_v5_contract_fixture.py",
                "_task7_v5_fixture_helpers",
                "a71bd136500a3ab47a59fd4d14a41a0f3d58c713060f518ea8508f01c0dd8945",
            ),
            (
                "scripts/generate_task7_v6_contract_fixture.py",
                "_task7_v6_fixture_helpers",
                "f73ea3108fc99c90231c289ea79ec6a66e0e51cc8a3f1569e445ab62e875dca8",
            ),
            (
                "scripts/generate_task7_v6_contract_fixture.py",
                "_task7_v6_fixture_helpers",
                "f73ea3108fc99c90231c289ea79ec6a66e0e51cc8a3f1569e445ab62e875dca8",
            ),
            (
                "scripts/generate_task7_v7_contract_fixture.py",
                "_task7_v7_fixture_helpers_for_v9",
                "bb41444cb68bbe2ecdec307a30cb93c26ab319a7951cb22cae3c6ab90004de3d",
            ),
            (
                "scripts/generate_task7_v7_contract_fixture.py",
                "_task7_v7_fixture_helpers_for_v9",
                "bb41444cb68bbe2ecdec307a30cb93c26ab319a7951cb22cae3c6ab90004de3d",
            ),
            (
                "scripts/generate_task7_v9_contract_fixture.py",
                "_task7_v9_fixture_helpers_for_v10",
                "010695b5ea336ab0a8b8f34932ba245b16822a877ba0a4f7cc360dda3314fae5",
            ),
            (
                "scripts/generate_task7_v9_contract_fixture.py",
                "_task7_v9_fixture_helpers_for_v10",
                "010695b5ea336ab0a8b8f34932ba245b16822a877ba0a4f7cc360dda3314fae5",
            ),
            (
                "scripts/materialize_full_strict_v10_preimage.py",
                "_task7_v10_materializer_helpers",
                "7f0b90f100fc1197e2a12b6bfdc891bf5c4cfa7671b39f9aaec5d7e677a50b69",
            ),
            (
                "scripts/materialize_full_strict_v10_preimage.py",
                "_task7_v10_materializer_helpers",
                "7f0b90f100fc1197e2a12b6bfdc891bf5c4cfa7671b39f9aaec5d7e677a50b69",
            ),
            (
                "scripts/materialize_full_strict_v11_preimage.py",
                "_task7_v11_materializer_helpers",
                "240c1d116775719c03a658c4663b13b03821d366ffd6d6a6803cf2f2a5dbbb75",
            ),
            (
                "scripts/materialize_full_strict_v11_preimage.py",
                "_task7_v11_materializer_helpers",
                "240c1d116775719c03a658c4663b13b03821d366ffd6d6a6803cf2f2a5dbbb75",
            ),
        )
    ),
    (
        "scripts/run_task7_v11_current_guard_census.py",
        "_task7_v11_current_guard_engine_for_v16",
        "adapted-v16",
        "879c0976c233837a9ddd85b90812e632f0a9a6a936b656a5bc3fdd9ad7dbfa1e",
    ),
    (
        "scripts/run_task7_v9_current_guard_census.py",
        "_task7_v11_current_guard_census_engine",
        "adapted-v11-v16",
        "a7a8464e53b024a681b6616cc5660098519c3173e393ba3105e9dea77bf0519f",
    ),
    *(
        (path, alias, "manifest-exact", sha256)
        for path, alias, sha256 in (
            (
                "tests/__init__.py",
                "tests",
                "b4a8ffb0cdf0100e67cb8cda8f37e244948ecaa5ea8c4f5d579855f1def3d9da",
            ),
            (
                "tests/slice100_task2b/__init__.py",
                "tests.slice100_task2b",
                "9b66f201a8709633597c01cd07bddaa862de0667e083d5701469550a94c117f0",
            ),
            (
                "tests/slice100_task2b/_support.py",
                "tests.slice100_task2b._support",
                "fd0e634cb807e0128e1f808e9a0ace7fe5595153c87ccc03741a48b1d1d4536c",
            ),
            (
                "tests/slice100_task7/_void_contract_hash_evidence.py",
                "tests.slice100_task7._void_contract_hash_evidence",
                "c121b4fe0d20933e377c3c98c4643dce000a181af70b1931028036cee7419306",
            ),
            (
                "tests/slice100_task7/test_full_strict_v9_native_schema_red.py",
                "tests.slice100_task7.test_full_strict_v9_native_schema_red",
                "4d229c5da823488e90b145010dcaba5695d2be434edf5a419603f5ebada298d9",
            ),
            (
                "tests/test_run_methodology_deterministic_calculation_service.py",
                "tests.test_run_methodology_deterministic_calculation_service",
                "047e6e1a994e92e2475053ec25253d5d1495a1c6cdf4caf5250c3112d446c507",
            ),
            (
                "tests/test_run_methodology_validated_evidence_package_service.py",
                "tests.test_run_methodology_validated_evidence_package_service",
                "29ea02e66e5390868859dc474de9f966c9324b002327cf17a034f894db49d559",
            ),
        )
    ),
)


def _validate_scalar_json(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_scalar_json(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        normalized_keys: set[str] = set()
        for key, item in value.items():
            if not isinstance(key, str) or unicodedata.normalize("NFC", key) != key:
                raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
            if key in normalized_keys:
                raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
            normalized_keys.add(key)
            _validate_scalar_json(item, f"{path}.{key}")
        return
    raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")


def _canonical_scalar_json_bytes(value: Any) -> bytes:
    """Render the integer-only NFC JSON domain used by all census digests."""

    _validate_scalar_json(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_integer_only_json(raw: bytes) -> dict[str, Any]:
    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
            result[key] = value
        return result

    def reject_number(_value: str) -> Any:
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")

    if raw.startswith(b"\xef\xbb\xbf"):
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_float=reject_number,
            parse_constant=reject_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED") from exc
    if not isinstance(value, dict):
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    _validate_scalar_json(value)
    return value


def _decode_exact_candidate(raw: bytes) -> dict[str, Any]:
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), _EXPECTED_ARTIFACT_SHA256):
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    if not secrets.compare_digest(hashlib.sha512(raw).hexdigest(), _EXPECTED_ARTIFACT_SHA512):
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    artifact = _decode_integer_only_json(raw)
    try:
        metadata = artifact["metadata"]
        valid = (
            isinstance(metadata, Mapping)
            and metadata["artifact_schema"] == "full-strict-v16-reviewed-preimage-v1"
            and metadata["pipeline_contract_version"] == "full-strict-v16"
            and metadata["append_frozen"] is False
        )
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise CensusError("TASK7_V16_CENSUS_ARTIFACT_VERSION_MISMATCH")
    return artifact


def _render_artifact(artifact: Mapping[str, Any]) -> bytes:
    _validate_scalar_json(artifact)
    return (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    artifact = _decode_exact_candidate(candidate_raw)
    frozen = copy.deepcopy(artifact)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    if not secrets.compare_digest(
        hashlib.sha256(frozen_raw).hexdigest(),
        _EXPECTED_PROSPECTIVE_FROZEN_SHA256,
    ):
        raise CensusError("TASK7_V16_CENSUS_PROSPECTIVE_BYTES_INVALID")
    return frozen_raw


def _manifest_preimage() -> list[list[Any]]:
    return [
        [path, byte_length, sha256, role, list(aliases)]
        for path, byte_length, sha256, role, aliases in HARNESS_SOURCE_MANIFEST
    ]


HARNESS_SOURCE_MANIFEST_SHA256: Final = hashlib.sha256(
    _HARNESS_MANIFEST_DOMAIN + _canonical_scalar_json_bytes(_manifest_preimage())
).hexdigest()
HARNESS_SOURCE_EXECUTION_SHA256: Final = hashlib.sha256(
    b"task7-v16-harness-source-execution-coordinates-v1\x00"
    + _canonical_scalar_json_bytes([list(row) for row in HARNESS_SOURCE_EXECUTION_COORDINATES])
).hexdigest()
HARNESS_SOURCE_CLOSURE_SHA256: Final = hashlib.sha256(
    b"task7-v16-harness-source-closure-v1\x00"
    + _canonical_scalar_json_bytes(
        [
            HARNESS_SOURCE_MANIFEST_SHA256,
            HARNESS_SOURCE_EXECUTION_SHA256,
        ]
    )
).hexdigest()


@dataclass(frozen=True, slots=True)
class _HarnessSource:
    relative_path: str
    byte_length: int
    sha256: str
    role: str
    aliases: tuple[str, ...]


@dataclass(slots=True)
class _HeldSource:
    authority: _HarnessSource
    path: Path
    raw: bytes
    handle: Any
    file_identity: tuple[int, int]


class _ManifestLoader(importlib.abc.Loader):
    def __init__(self, store: _HarnessSourceStore, source: _HeldSource, alias: str) -> None:
        self._store = store
        self._source = source
        self._alias = alias

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        source = self._source
        module.__file__ = str(source.path)
        module.__loader__ = self
        module.__package__ = self._alias.rpartition(".")[0]
        if source.path.name == "__init__.py":
            module.__package__ = self._alias
            module.__path__ = [str(source.path.parent)]  # type: ignore[attr-defined]
        code = self._store.compile_held(source.path, source.raw, self._alias)
        exec(code, module.__dict__)  # noqa: S102 - manifest-held reviewed bytes
        self._store._trusted_modules[self._alias] = module


class _ManifestFinder(importlib.abc.MetaPathFinder):
    def __init__(self, store: _HarnessSourceStore) -> None:
        self._store = store

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        held = self._store._by_alias.get(fullname)
        if held is None:
            if self._store._is_protected_module(fullname):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
            return None
        loader = _ManifestLoader(self._store, held, fullname)
        return importlib.util.spec_from_loader(
            fullname,
            loader,
            origin=str(held.path),
            is_package=held.path.name == "__init__.py",
        )


class _HarnessSourceStore:
    """Acquire and execute the complete reviewed harness source authority."""

    def __init__(
        self,
        repo_root: Path,
        manifest: Sequence[Sequence[Any]],
        protected_prefixes: Sequence[str],
        expected_execution_coordinates: Sequence[Sequence[str]] | None = None,
        synthetic_namespaces: Mapping[str, str] | None = None,
        restore_preexisting: bool = True,
    ) -> None:
        self._root = Path(repo_root).absolute()
        self._case_insensitive_paths = os.name == "nt"
        self._protected_prefixes = tuple(
            (
                str(value).replace("\\", "/").casefold()
                if self._case_insensitive_paths
                else str(value).replace("\\", "/")
            )
            for value in protected_prefixes
        )
        self._manifest = self._validate_manifest(manifest)
        self._relative_authority_by_key = {
            (
                row.relative_path.casefold() if self._case_insensitive_paths else row.relative_path
            ): row.relative_path
            for row in self._manifest
        }
        default_coordinates = tuple(
            (
                row.relative_path,
                alias,
                "manifest-exact",
                row.sha256,
            )
            for row in self._manifest
            for alias in row.aliases
        )
        coordinate_source = (
            default_coordinates
            if expected_execution_coordinates is None
            else tuple(tuple(str(value) for value in row) for row in expected_execution_coordinates)
        )
        if any(len(row) != 4 for row in coordinate_source):
            raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
        authorities = {row.relative_path: row for row in self._manifest}
        for relative, alias, kind, digest in cast(
            Sequence[tuple[str, str, str, str]],
            coordinate_source,
        ):
            authority = authorities.get(relative)
            if (
                authority is None
                or alias not in authority.aliases
                or kind
                not in {
                    "manifest-exact",
                    "raw-exact",
                    "adapted-v16",
                    "adapted-v11-v16",
                }
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or kind in {"manifest-exact", "raw-exact"}
                and digest != authority.sha256
            ):
                raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
        if {row[0] for row in coordinate_source} != set(authorities):
            raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
        self._expected_execution = Counter(
            cast(Sequence[tuple[str, str, str, str]], coordinate_source)
        )
        self._expected_reads = Counter(
            row[0] for row in cast(Sequence[tuple[str, str, str, str]], coordinate_source)
        )
        self._synthetic_namespaces = dict(synthetic_namespaces or {})
        self._restore_preexisting = restore_preexisting
        self._protected_module_roots = {
            alias.split(".", 1)[0].casefold()
            for row in self._manifest
            for alias in row.aliases
            if "." in alias or row.relative_path.endswith("/__init__.py")
        } | {name.split(".", 1)[0].casefold() for name in self._synthetic_namespaces}
        self._held: dict[str, _HeldSource] = {}
        self._by_alias: dict[str, _HeldSource] = {}
        self._observed_reads: Counter[str] = Counter()
        self._observed_execution: Counter[tuple[str, str, str, str]] = Counter()
        self._active_depth = 0
        self._finder = _ManifestFinder(self)
        self._saved_modules: dict[str, Any] = {}
        self._saved_parent_attributes: dict[tuple[str, str], tuple[bool, Any]] = {}
        self._trusted_modules: dict[str, ModuleType] = {}
        self._original_spec_from_file_location = importlib.util.spec_from_file_location
        self._original_path_read_bytes = Path.read_bytes
        self._original_compile = builtins.compile
        self._original_builtin_open = builtins.open
        self._original_io_open = io.open
        self._original_os_open = os.open
        self._original_io_open_code = io.open_code
        self._original_raw_open_code = _io.open_code
        self._original_raw_file_io = _io.FileIO
        self._original_io_file_io = io.FileIO
        self._original_sourceless_get_code = importlib.machinery.SourcelessFileLoader.get_code
        self._original_sourceless_get_data = importlib.machinery.SourcelessFileLoader.get_data

        def sourceless_get_code(
            loader: importlib.machinery.SourcelessFileLoader,
            fullname: str,
        ) -> Any:
            return self._guarded_sourceless_get_code(loader, fullname)

        def sourceless_get_data(
            loader: importlib.machinery.SourcelessFileLoader,
            path: str,
        ) -> bytes:
            return self._guarded_sourceless_get_data(loader, path)

        def guarded_file_io(
            file: Any,
            mode: str = "r",
            closefd: bool = True,
            opener: Any = None,
        ) -> Any:
            return self._guarded_file_io(file, mode, closefd, opener)

        self._sourceless_get_code_wrapper = sourceless_get_code
        self._sourceless_get_data_wrapper = sourceless_get_data
        self._file_io_wrapper = guarded_file_io

        def path_read_bytes(path: Path) -> bytes:
            return self.bytes_for_path(path)

        self._path_read_bytes_wrapper = path_read_bytes

    @staticmethod
    def _validate_manifest(
        manifest: Sequence[Sequence[Any]],
    ) -> tuple[_HarnessSource, ...]:
        rows: list[_HarnessSource] = []
        path_keys: set[str] = set()
        alias_keys: set[str] = set()
        previous = ""
        for raw_row in manifest:
            if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes)):
                raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
            if len(raw_row) != 5:
                raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
            raw_path, byte_length, sha256, role, raw_aliases = raw_row
            if (
                not isinstance(raw_path, str)
                or not raw_path
                or "\\" in raw_path
                or raw_path.startswith("/")
                or any(part in {"", ".", ".."} for part in raw_path.split("/"))
                or not isinstance(byte_length, int)
                or isinstance(byte_length, bool)
                or byte_length <= 0
                or not isinstance(sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                or not isinstance(role, str)
                or not role
                or not isinstance(raw_aliases, tuple)
                or not raw_aliases
                or any(not isinstance(alias, str) or not alias for alias in raw_aliases)
            ):
                raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
            key = raw_path.casefold()
            if raw_path <= previous or key in path_keys:
                raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
            previous = raw_path
            path_keys.add(key)
            alias_tuple = cast(tuple[str, ...], raw_aliases)
            if len({alias.casefold() for alias in alias_tuple}) != len(alias_tuple) or any(
                alias.casefold() in alias_keys for alias in alias_tuple
            ):
                raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
            alias_keys.update(alias.casefold() for alias in alias_tuple)
            rows.append(
                _HarnessSource(
                    relative_path=raw_path,
                    byte_length=byte_length,
                    sha256=sha256,
                    role=role,
                    aliases=alias_tuple,
                )
            )
        if not rows:
            raise CensusError("TASK7_V16_HARNESS_MANIFEST_INVALID")
        return tuple(rows)

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID") from exc
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)

    def _validated_path(self, authority: _HarnessSource) -> Path:
        root = self._root
        try:
            if self._is_reparse(root) or root.resolve(strict=True) != root:
                raise CensusError("TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID")
            current = root
            for part in authority.relative_path.split("/"):
                current = current / part
                if self._is_reparse(current):
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID")
                if current.resolve(strict=True) != current:
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID")
        except CensusError:
            raise
        except (OSError, RuntimeError) as exc:
            raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID") from exc
        return current

    @staticmethod
    def _read_windows_locked(path: Path) -> tuple[bytes, int, tuple[int, int]]:
        from ctypes import wintypes

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x80000000,  # GENERIC_READ
            0x00000001,  # FILE_SHARE_READ only
            None,
            3,  # OPEN_EXISTING
            0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (None, invalid_handle):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID")
        info = ByHandleFileInformation()
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation))
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        if not get_info(handle, ctypes.byref(info)):
            close_handle(handle)
            raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID")
        if info.dwFileAttributes & (0x10 | 0x400):
            close_handle(handle)
            raise CensusError("TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID")
        size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
        chunks: list[bytes] = []
        remaining = size
        read_file = kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        read_file.restype = wintypes.BOOL
        while remaining:
            count = min(remaining, 1024 * 1024)
            buffer = ctypes.create_string_buffer(count)
            read_count = wintypes.DWORD()
            if not read_file(handle, buffer, count, ctypes.byref(read_count), None):
                close_handle(handle)
                raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID")
            if read_count.value == 0:
                close_handle(handle)
                raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID")
            chunks.append(buffer.raw[: read_count.value])
            remaining -= read_count.value
        identity = (
            int(info.dwVolumeSerialNumber),
            (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        )
        return b"".join(chunks), int(handle), identity

    @staticmethod
    def _read_posix_locked(path: Path) -> tuple[bytes, int, tuple[int, int]]:
        import fcntl

        flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            before = os.lstat(path)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
                opened.st_dev,
                opened.st_ino,
            ):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.lstat(path)
            if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID")
            return b"".join(chunks), descriptor, (int(opened.st_dev), int(opened.st_ino))
        except CensusError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise CensusError("TASK7_V16_HARNESS_SOURCE_OPEN_INVALID") from exc

    def acquire(self) -> None:
        if self._held:
            return
        identities: set[tuple[int, int]] = set()
        opened: list[_HeldSource] = []
        try:
            for authority in self._manifest:
                path = self._validated_path(authority)
                if os.name == "nt":
                    raw, handle, identity = self._read_windows_locked(path)
                else:
                    raw, handle, identity = self._read_posix_locked(path)
                held = _HeldSource(authority, path, raw, handle, identity)
                opened.append(held)
                if identity in identities:
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID")
                identities.add(identity)
                if len(raw) != authority.byte_length:
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_HASH_INVALID")
                if not secrets.compare_digest(
                    hashlib.sha256(raw).hexdigest(),
                    authority.sha256,
                ):
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_HASH_INVALID")
                self._held[authority.relative_path] = held
                for alias in authority.aliases:
                    self._by_alias[alias] = held
        except Exception:
            for held in opened:
                self._close_handle(held.handle)
            self._held.clear()
            self._by_alias.clear()
            raise

    @staticmethod
    def _close_handle(handle: Any) -> None:
        if handle is None:
            return
        if os.name == "nt":
            from ctypes import wintypes

            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(handle))
        else:
            os.close(int(handle))

    def close(self) -> None:
        for alias, module in sorted(
            self._trusted_modules.items(),
            key=lambda item: item[0].count("."),
            reverse=True,
        ):
            if sys.modules.get(alias) is module:
                self._remove_parent_attribute(alias)
                sys.modules.pop(alias, None)
        self._trusted_modules.clear()
        for held in tuple(self._held.values()):
            self._close_handle(held.handle)
        self._held.clear()
        self._by_alias.clear()

    @staticmethod
    def _reread_windows_handle(held: _HeldSource) -> bytes:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_pointer = kernel32.SetFilePointerEx
        set_pointer.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        )
        set_pointer.restype = wintypes.BOOL
        handle = wintypes.HANDLE(held.handle)
        previous = ctypes.c_longlong()
        if not set_pointer(handle, 0, ctypes.byref(previous), 1):  # FILE_CURRENT
            raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")
        if not set_pointer(handle, 0, None, 0):  # FILE_BEGIN
            raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")
        try:
            chunks: list[bytes] = []
            remaining = held.authority.byte_length
            read_file = kernel32.ReadFile
            read_file.argtypes = (
                wintypes.HANDLE,
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                wintypes.LPVOID,
            )
            read_file.restype = wintypes.BOOL
            while remaining:
                count = min(remaining, 1024 * 1024)
                buffer = ctypes.create_string_buffer(count)
                read_count = wintypes.DWORD()
                if (
                    not read_file(
                        handle,
                        buffer,
                        count,
                        ctypes.byref(read_count),
                        None,
                    )
                    or read_count.value == 0
                ):
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")
                chunks.append(buffer.raw[: read_count.value])
                remaining -= read_count.value
            return b"".join(chunks)
        finally:
            if not set_pointer(handle, previous.value, None, 0):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")

    @staticmethod
    def _reread_posix_handle(held: _HeldSource) -> bytes:
        descriptor = int(held.handle)
        chunks: list[bytes] = []
        offset = 0
        read_ceiling = held.authority.byte_length + 1
        while offset < read_ceiling:
            chunk = os.pread(
                descriptor,
                min(1024 * 1024, read_ceiling - offset),
                offset,
            )
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        return b"".join(chunks)

    def _verify_held_sources(self) -> None:
        for relative, held in self._held.items():
            try:
                path = self._validated_path(held.authority)
                metadata = os.lstat(path)
                if os.name == "nt":
                    raw = self._reread_windows_handle(held)
                else:
                    opened_before = os.fstat(int(held.handle))
                    if (
                        not stat.S_ISREG(opened_before.st_mode)
                        or not stat.S_ISREG(metadata.st_mode)
                        or (int(opened_before.st_dev), int(opened_before.st_ino))
                        != held.file_identity
                        or (int(metadata.st_dev), int(metadata.st_ino)) != held.file_identity
                        or int(opened_before.st_size) != held.authority.byte_length
                        or int(metadata.st_size) != held.authority.byte_length
                    ):
                        raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")
                    raw = self._reread_posix_handle(held)
                    opened_after = os.fstat(int(held.handle))
                    metadata_after = os.lstat(path)
                    if (
                        not stat.S_ISREG(opened_after.st_mode)
                        or not stat.S_ISREG(metadata_after.st_mode)
                        or (int(opened_after.st_dev), int(opened_after.st_ino))
                        != held.file_identity
                        or (int(metadata_after.st_dev), int(metadata_after.st_ino))
                        != held.file_identity
                        or int(opened_after.st_size) != held.authority.byte_length
                        or int(metadata_after.st_size) != held.authority.byte_length
                    ):
                        raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")
            except CensusError:
                raise
            except OSError as exc:
                raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID") from exc
            if (
                path != held.path
                or len(raw) != held.authority.byte_length
                or not secrets.compare_digest(
                    hashlib.sha256(raw).hexdigest(),
                    held.authority.sha256,
                )
                or raw != held.raw
                or relative != held.authority.relative_path
            ):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID")

    def _normalize_input_path(self, path: Path) -> Path:
        rendered = os.fsdecode(os.fspath(path))
        if not self._case_insensitive_paths:
            return Path(rendered)
        windows_rendered = rendered.replace("/", "\\")
        if windows_rendered.startswith("\\\\?\\"):
            local_path = windows_rendered[4:]
            if re.match(r"^[A-Za-z]:\\", local_path) is None:
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
            return Path(local_path)
        if windows_rendered.startswith(("\\\\.\\", "\\??\\", "\\\\??\\")):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        if windows_rendered.startswith("\\\\"):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        return Path(rendered)

    def _relative_to_root(self, candidate: Path, root: Path) -> str | None:
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            if not self._case_insensitive_paths:
                return None
            candidate_parts = candidate.parts
            root_parts = root.parts
            if len(candidate_parts) < len(root_parts) or tuple(
                value.casefold() for value in candidate_parts[: len(root_parts)]
            ) != tuple(value.casefold() for value in root_parts):
                return None
            remaining = candidate_parts[len(root_parts) :]
            return Path(*remaining).as_posix() if remaining else ""

    def _canonical_relative(self, relative: str) -> str:
        key = relative.casefold() if self._case_insensitive_paths else relative
        return self._relative_authority_by_key.get(key, key)

    def _relative_for_path(self, path: Path) -> str | None:
        candidate = self._normalize_input_path(Path(path))
        if not candidate.is_absolute():
            candidate = self._root / candidate
        lexical = Path(os.path.abspath(candidate))
        lexical_relative = self._relative_to_root(lexical, self._root)
        try:
            resolved = lexical.resolve(strict=True)
            resolved_root = self._root.resolve(strict=True)
        except (OSError, RuntimeError):
            return (
                self._canonical_relative(lexical_relative) if lexical_relative is not None else None
            )
        resolved_relative = self._relative_to_root(resolved, resolved_root)
        if lexical_relative is not None and resolved_relative is None:
            raise CensusError("TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID")
        if resolved_relative is not None:
            return self._canonical_relative(resolved_relative)
        for held in self._held.values():
            try:
                if os.path.samefile(lexical, held.path):
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID")
            except CensusError:
                raise
            except OSError:
                continue
        return None

    def _is_protected(self, relative: str) -> bool:
        key = relative.casefold() if self._case_insensitive_paths else relative
        return any(
            prefix == "" or key == prefix or key.startswith(prefix.rstrip("/") + "/")
            for prefix in self._protected_prefixes
        )

    def _is_protected_module(self, fullname: str) -> bool:
        root = fullname.split(".", 1)[0].casefold()
        return root in self._protected_module_roots

    def bytes_for_path(self, path: Path) -> bytes:
        relative = self._relative_for_path(path)
        if relative is None or relative not in self._held:
            if relative is not None and self._is_protected(relative):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
            return self._original_path_read_bytes(Path(path))
        held = self._held[relative]
        self._observed_reads[relative] += 1
        return held.raw

    def compile_held(self, path: Path, source: bytes | str, alias: str) -> Any:
        relative = self._relative_for_path(path)
        if relative is None or relative not in self._held:
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        held = self._held[relative]
        expected = held.raw.decode("utf-8")
        text_source = source.decode("utf-8") if isinstance(source, bytes) else source
        if text_source != expected or alias not in held.authority.aliases:
            raise CensusError("TASK7_V16_HARNESS_SOURCE_EXECUTION_INVALID")
        coordinate = (relative, alias, "manifest-exact", held.authority.sha256)
        self._observed_reads[relative] += 1
        self._observed_execution[coordinate] += 1
        return self._original_compile(source, str(held.path), "exec")

    def mark_adapted_execution(self, path: Path, source: str) -> None:
        source_raw = source.encode("utf-8")
        digest = hashlib.sha256(source_raw).hexdigest()
        coordinate_by_digest = {
            "879c0976c233837a9ddd85b90812e632f0a9a6a936b656a5bc3fdd9ad7dbfa1e": (
                "scripts/run_task7_v11_current_guard_census.py",
                "_task7_v11_current_guard_engine_for_v16",
                "adapted-v16",
            ),
            "a7a8464e53b024a681b6616cc5660098519c3173e393ba3105e9dea77bf0519f": (
                "scripts/run_task7_v9_current_guard_census.py",
                "_task7_v11_current_guard_census_engine",
                "adapted-v11-v16",
            ),
        }
        expected = coordinate_by_digest.get(digest)
        if expected is None:
            raise CensusError("TASK7_V16_HARNESS_SOURCE_EXECUTION_INVALID")
        relative, alias, kind = expected
        held = self._held.get(relative)
        supplied_relative = self._relative_for_path(path)
        # The adapted V9 engine historically reports the V11 filename while
        # compiling.  Its distinct pinned digest is the attribution authority.
        allowed_filename = (
            relative == "scripts/run_task7_v9_current_guard_census.py"
            and supplied_relative == "scripts/run_task7_v11_current_guard_census.py"
        )
        if (
            held is None
            or alias not in held.authority.aliases
            or supplied_relative != relative
            and not allowed_filename
        ):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_EXECUTION_INVALID")
        coordinate = (relative, alias, kind, digest)
        self._observed_execution[coordinate] += 1

    def _spec_from_file_location(
        self,
        name: str,
        location: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: Any,
        **kwargs: Any,
    ) -> importlib.machinery.ModuleSpec | None:
        path = Path(os.fsdecode(location))
        relative = self._relative_for_path(path)
        if relative in self._held:
            held = self._held[cast(str, relative)]
            if name not in held.authority.aliases:
                raise CensusError("TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID")
            loader = _ManifestLoader(self, held, name)
            return importlib.util.spec_from_loader(
                name,
                loader,
                origin=str(held.path),
                is_package=held.path.name == "__init__.py",
            )
        if relative is not None and self._is_protected(relative):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        return self._original_spec_from_file_location(name, location, *args, **kwargs)

    def _path_read_bytes(self, path: Path) -> bytes:
        return self.bytes_for_path(path)

    def _compile(
        self,
        source: Any,
        filename: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: str,
        flags: int = 0,
        dont_inherit: bool = False,
        optimize: int = -1,
        **kwargs: Any,
    ) -> Any:
        relative = self._relative_for_path(Path(os.fsdecode(filename)))
        if relative in self._held and mode == "exec":
            held = self._held[cast(str, relative)]
            text_source = source.decode("utf-8") if isinstance(source, bytes) else source
            if (
                text_source == held.raw.decode("utf-8")
                and held.authority.role == "raw-scaffold-execution"
            ):
                alias_by_path = {
                    "scripts/_task7_v11_census_scaffold.py": (
                        "_task7_v11_census_scaffold_core_for_v16"
                    ),
                    "scripts/_task7_v9_census_scaffold.py": (
                        "_task7_v9_census_scaffold_core_for_v11"
                    ),
                }
                alias = alias_by_path.get(cast(str, relative))
                if alias is None:
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_EXECUTION_INVALID")
                coordinate = (
                    cast(str, relative),
                    alias,
                    "raw-exact",
                    held.authority.sha256,
                )
                self._observed_execution[coordinate] += 1
            elif relative in {
                "scripts/run_task7_v11_current_guard_census.py",
                "scripts/run_task7_v9_current_guard_census.py",
            }:
                self.mark_adapted_execution(held.path, cast(str, text_source))
            else:
                raise CensusError("TASK7_V16_HARNESS_SOURCE_EXECUTION_INVALID")
        elif relative is not None and self._is_protected(relative):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        return self._original_compile(
            source,
            filename,
            mode,
            flags,
            dont_inherit,
            optimize,
            **kwargs,
        )

    def _guarded_open(
        self,
        file: Any,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener: Any = None,
    ) -> Any:
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file))
            relative = self._relative_for_path(path)
            protected_source = (
                relative is not None
                and self._is_protected(relative)
                and path.suffix.casefold() in {".py", ".pyc"}
            )
            if protected_source:
                if (
                    relative not in self._held
                    or path.suffix.casefold() != ".py"
                    or any(token in mode for token in "wax+")
                ):
                    raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
                held = self._held[relative]
                self._observed_reads[relative] += 1
                if "b" in mode:
                    return io.BytesIO(held.raw)
                return io.StringIO(
                    held.raw.decode(encoding or "utf-8", errors or "strict"),
                    newline=newline,
                )
        return self._original_builtin_open(
            file,
            mode,
            buffering,
            encoding,
            errors,
            newline,
            closefd,
            opener,
        )

    def _guarded_os_open(
        self,
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if isinstance(path, (str, bytes, os.PathLike)):
            candidate = Path(os.fsdecode(path))
            if (
                dir_fd is not None
                and not candidate.is_absolute()
                and candidate.suffix.casefold() in {".py", ".pyc"}
            ):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
            relative = self._relative_for_path(candidate)
            if (
                relative is not None
                and self._is_protected(relative)
                and candidate.suffix.casefold() in {".py", ".pyc"}
            ):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        if dir_fd is None:
            return self._original_os_open(path, flags, mode)
        return self._original_os_open(path, flags, mode, dir_fd=dir_fd)

    def _guarded_open_code(self, path: str) -> Any:
        candidate = Path(path)
        relative = self._relative_for_path(candidate)
        if (
            relative is not None
            and self._is_protected(relative)
            and candidate.suffix.casefold() in {".py", ".pyc"}
        ):
            if relative not in self._held or candidate.suffix.casefold() != ".py":
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
            held = self._held[relative]
            self._observed_reads[relative] += 1
            return io.BytesIO(held.raw)
        return self._original_raw_open_code(path)

    def _guarded_file_io(
        self,
        file: Any,
        mode: str = "r",
        closefd: bool = True,
        opener: Any = None,
    ) -> Any:
        if isinstance(file, (str, bytes, os.PathLike)):
            candidate = Path(os.fsdecode(file))
            relative = self._relative_for_path(candidate)
            if (
                relative is not None
                and self._is_protected(relative)
                and candidate.suffix.casefold() in {".py", ".pyc"}
            ):
                raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        return self._original_raw_file_io(file, mode, closefd, opener)

    def _guarded_sourceless_get_code(
        self,
        loader: importlib.machinery.SourcelessFileLoader,
        fullname: str,
    ) -> Any:
        candidate = Path(loader.path)
        relative = self._relative_for_path(candidate)
        if (
            relative is not None
            and self._is_protected(relative)
            and candidate.suffix.casefold() == ".pyc"
        ):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        return self._original_sourceless_get_code(loader, fullname)

    def _guarded_sourceless_get_data(
        self,
        loader: importlib.machinery.SourcelessFileLoader,
        path: str,
    ) -> bytes:
        candidate = Path(path)
        relative = self._relative_for_path(candidate)
        if (
            relative is not None
            and self._is_protected(relative)
            and candidate.suffix.casefold() == ".pyc"
        ):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_UNKNOWN")
        return self._original_sourceless_get_data(loader, path)

    @staticmethod
    def _parent_coordinate(alias: str) -> tuple[str, str] | None:
        parent, separator, child = alias.rpartition(".")
        return (parent, child) if separator else None

    def _remove_parent_attribute(self, alias: str) -> None:
        coordinate = self._parent_coordinate(alias)
        if coordinate is None:
            return
        parent_name, child_name = coordinate
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, child_name):
            delattr(parent, child_name)

    def _install_synthetic_namespace(self, name: str, relative_path: str) -> ModuleType:
        parent_coordinate = self._parent_coordinate(name)
        if parent_coordinate is not None:
            parent_name, child_name = parent_coordinate
            parent = importlib.import_module(parent_name)
        else:
            parent = None
            child_name = ""
        module = ModuleType(name)
        module.__file__ = None
        module.__package__ = name
        module.__path__ = [str(self._root / relative_path)]  # type: ignore[attr-defined]
        module.__spec__ = importlib.machinery.ModuleSpec(
            name,
            loader=None,
            is_package=True,
        )
        module.__spec__.submodule_search_locations = module.__path__
        sys.modules[name] = module
        self._trusted_modules[name] = module
        if parent is not None:
            setattr(parent, child_name, module)
        return module

    @contextmanager
    def activation(self) -> Iterator[None]:
        if not self._held:
            self.acquire()
        if self._active_depth:
            self._active_depth += 1
            try:
                yield
            finally:
                self._active_depth -= 1
            return
        self._active_depth = 1
        controlled_aliases = tuple(
            dict.fromkeys(
                (
                    *self._by_alias,
                    *self._synthetic_namespaces,
                )
            )
        )
        preexisting_protected = tuple(
            name
            for name in sys.modules
            if isinstance(name, str) and self._is_protected_module(name)
        )
        aliases = tuple(dict.fromkeys((*controlled_aliases, *preexisting_protected)))
        saved: dict[str, Any] = {}
        saved_attributes: dict[tuple[str, str], tuple[bool, Any]] = {}
        for alias in aliases:
            coordinate = self._parent_coordinate(alias)
            if coordinate is not None and coordinate not in saved_attributes:
                parent_name, child_name = coordinate
                parent = sys.modules.get(parent_name)
                saved_attributes[coordinate] = (
                    parent is not None and hasattr(parent, child_name),
                    getattr(parent, child_name, None) if parent is not None else None,
                )
        for alias in sorted(
            aliases,
            key=lambda value: value.count("."),
            reverse=True,
        ):
            if alias in sys.modules:
                current = sys.modules[alias]
                trusted = self._trusted_modules.get(alias)
                if alias in self._trusted_modules and current is trusted:
                    continue
                saved[alias] = current
                self._remove_parent_attribute(alias)
                sys.modules.pop(alias, None)
        self._saved_modules = saved
        self._saved_parent_attributes = saved_attributes
        importlib.util.spec_from_file_location = self._spec_from_file_location
        Path.read_bytes = self._path_read_bytes_wrapper  # type: ignore[method-assign]
        builtins.compile = self._compile
        builtins.open = self._guarded_open
        io.open = self._guarded_open
        os.open = self._guarded_os_open
        io.open_code = self._guarded_open_code
        _io.open_code = self._guarded_open_code
        io.FileIO = self._file_io_wrapper
        _io.FileIO = self._file_io_wrapper
        importlib.machinery.SourcelessFileLoader.get_code = self._sourceless_get_code_wrapper
        importlib.machinery.SourcelessFileLoader.get_data = self._sourceless_get_data_wrapper
        sys.meta_path.insert(0, self._finder)
        try:
            for alias, trusted in sorted(
                self._trusted_modules.items(),
                key=lambda item: item[0].count("."),
            ):
                if alias in self._synthetic_namespaces or "." in alias:
                    continue
                sys.modules[alias] = trusted
            for name, relative in sorted(
                self._synthetic_namespaces.items(),
                key=lambda item: item[0].count("."),
            ):
                trusted = self._trusted_modules.get(name)
                if trusted is not None:
                    sys.modules[name] = trusted
                    coordinate = self._parent_coordinate(name)
                    if coordinate is not None:
                        parent = importlib.import_module(coordinate[0])
                        setattr(parent, coordinate[1], trusted)
                else:
                    self._install_synthetic_namespace(name, relative)
            for alias, trusted in sorted(
                self._trusted_modules.items(),
                key=lambda item: item[0].count("."),
            ):
                if alias in self._synthetic_namespaces or "." not in alias:
                    continue
                sys.modules[alias] = trusted
                coordinate = self._parent_coordinate(alias)
                if coordinate is not None:
                    parent = importlib.import_module(coordinate[0])
                    setattr(parent, coordinate[1], trusted)
            yield
        finally:
            if self._finder in sys.meta_path:
                sys.meta_path.remove(self._finder)
            importlib.machinery.SourcelessFileLoader.get_data = self._original_sourceless_get_data
            importlib.machinery.SourcelessFileLoader.get_code = self._original_sourceless_get_code
            _io.FileIO = self._original_raw_file_io
            io.FileIO = self._original_io_file_io
            _io.open_code = self._original_raw_open_code
            io.open_code = self._original_io_open_code
            os.open = self._original_os_open
            io.open = self._original_io_open
            builtins.open = self._original_builtin_open
            builtins.compile = self._original_compile
            Path.read_bytes = self._original_path_read_bytes  # type: ignore[method-assign]
            importlib.util.spec_from_file_location = self._original_spec_from_file_location
            if self._restore_preexisting:
                for alias in sorted(
                    aliases,
                    key=lambda value: value.count("."),
                    reverse=True,
                ):
                    self._remove_parent_attribute(alias)
                    sys.modules.pop(alias, None)
                for alias, module in sorted(
                    saved.items(),
                    key=lambda item: item[0].count("."),
                ):
                    sys.modules[alias] = module
                for (parent_name, child_name), (existed, value) in saved_attributes.items():
                    parent = sys.modules.get(parent_name)
                    if parent is None:
                        continue
                    if existed:
                        setattr(parent, child_name, value)
                    elif hasattr(parent, child_name):
                        delattr(parent, child_name)
            self._saved_modules = {}
            self._saved_parent_attributes = {}
            self._active_depth = 0

    def assert_exact_closure(self) -> None:
        self._verify_held_sources()
        if (
            self._observed_reads != self._expected_reads
            or self._observed_execution != self._expected_execution
        ):
            raise CensusError("TASK7_V16_HARNESS_SOURCE_CLOSURE_INCOMPLETE")


def provision_exact_erasure_principals(connection: Connection) -> None:
    """Provision the two reviewed erasure principals without mutable test imports."""

    connection.exec_driver_sql(
        """
        DO $task7$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'idis_erasure_function_owner'
          ) THEN
            CREATE ROLE idis_erasure_function_owner
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'idis_erasure_executor'
          ) THEN
            CREATE ROLE idis_erasure_executor
              LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END
        $task7$;
        """
    )
    posture = {
        str(row.rolname): tuple(row)[1:]
        for row in connection.execute(
            text(
                "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                "rolinherit,rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname IN "
                "('idis_erasure_function_owner','idis_erasure_executor') "
                "ORDER BY rolname"
            )
        )
    }
    if posture != {
        "idis_erasure_executor": (True, False, False, False, False, False),
        "idis_erasure_function_owner": (False, False, False, False, False, False),
    }:
        raise CensusError("TASK7_ERASURE_ROLE_POSTURE_INVALID")
    memberships = connection.execute(
        text(
            "SELECT count(*) FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
            "WHERE member.rolname IN "
            "('idis_erasure_function_owner','idis_erasure_executor')"
        )
    ).scalar_one()
    if int(memberships) != 0:
        raise CensusError("TASK7_ERASURE_ROLE_MEMBERSHIP_FORBIDDEN")


_HARNESS_SOURCE_STORE = _HarnessSourceStore(
    _REPO_ROOT,
    HARNESS_SOURCE_MANIFEST,
    (
        "scripts",
        "tests",
    ),
    HARNESS_SOURCE_EXECUTION_COORDINATES,
    {
        "scripts": "scripts",
        "tests.slice100_task7": "tests/slice100_task7",
    },
)
_HARNESS_SOURCE_STORE.acquire()
atexit.register(_HARNESS_SOURCE_STORE.close)

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = (
    "task7-v16-current-guard-app-role-pre-freeze-artifact-sql-on-0037-v2:"
    + HARNESS_SOURCE_CLOSURE_SHA256
)
INSTALLATION_MODE: Final = "ARTIFACT_SQL_APP_ROLE_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v16-current-guard-app-role-census-result-v1"

TASK6_WRITE_CONTEXT_GUCS: Final = (
    "idis.worker_lease_tenant_id",
    "idis.worker_lease_deal_id",
    "idis.worker_lease_run_id",
    "idis.worker_lease_owner_id",
    "idis.worker_lease_fencing_token",
)
_TASK6_OUTPUT_ROOT_UPDATE_TARGETS: Final = (
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
)
_CASE_KINDS: Final = ("LEGAL", "ILLEGAL", "DIRECT_SQL")
_EVENT_ORDER: Final = ("INSERT", "UPDATE", "DELETE")
_PRODUCTION_CONSTRUCTOR_SPECS: Final = (
    (
        "validated-evidence-package-native",
        "task7-validated-evidence-package-native-v1",
        "idis.models.layer1_durability.ValidatedEvidencePackageRow.from_record",
        "validated_evidence_packages",
    ),
    (
        "muhasabah-native",
        "task7-muhasabah-native-v1",
        "idis.models.layer1_durability.MuhasabahRecordRow.from_debate_record",
        "muhasabah_records",
    ),
    (
        "layer2-challenge-native",
        "task7-layer2-challenge-native-v1",
        "idis.models.layer2_durability.Layer2ChallengeRow.from_record",
        "layer2_ic_challenges",
    ),
    (
        "debate-session-native",
        "task7-debate-session-native-v1",
        "idis.debate.orchestrator.DebateOrchestrator.run",
        "debate_sessions",
    ),
)
_HISTORICAL_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v11_current_guard_census.py")
_HISTORICAL_RUNNER_SHA256: Final = (
    "7c3ced9c8a61923ea70aeee1a30cb71584d152fc7d56f4ac95dffc24d56d307e"
)
_CENSUS_ENVELOPE_SCHEMA: Final = "task7-v16-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v16-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN: Final = "task7-v16-postgres-census-signable-v1"
_IDENTIFIER_RE: Final = re.compile(r"[a-z][a-z0-9_]*")
_HISTORICAL_CONTRACT_VERSION_RE: Final = re.compile(r"full-strict-v[0-9]+")
_HISTORICAL_CONTRACT_VERSION_LITERAL_COUNT: Final = 13
_FORBIDDEN_SERIALIZED_PROVENANCE_RE: Final = re.compile(
    r"(?<![a-z0-9])v(?:11|14|15)(?![a-z0-9])",
    re.IGNORECASE,
)
_EXTERNAL_ERASURE_HELPER_SIGNATURE: Final = (
    "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
)
_FIXED_CLUSTER_ROLE_POSTURE: Final = {
    "idis_erasure_executor": (True, False, False, False, False, False, False),
    "idis_erasure_function_owner": (False, False, False, False, False, False, False),
    "idis_task7_guard_function_owner": (False, False, False, False, False, False, False),
}

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
TriggerEvent = Literal["INSERT", "UPDATE", "DELETE"]


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise CensusError("TASK7_V16_CENSUS_IDENTIFIER_INVALID")
    return value


def _adapt_historical_execution_source(raw: bytes) -> str:
    """Retarget only the hash-pinned engine's enumerated contract placeholders."""

    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CensusError("TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID") from exc
    source_versions = set(_HISTORICAL_CONTRACT_VERSION_RE.findall(source))
    old_literal = '"full-strict-v11"'
    if (
        source_versions != {"full-strict-v9", "full-strict-v11"}
        or source.count(old_literal) != _HISTORICAL_CONTRACT_VERSION_LITERAL_COUNT
    ):
        raise CensusError("TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID")
    adapted = source.replace(old_literal, '"full-strict-v16"')
    if adapted.count('"full-strict-v16"') != _HISTORICAL_CONTRACT_VERSION_LITERAL_COUNT or any(
        literal in adapted
        for literal in (
            '"full-strict-v11"',
            '"full-strict-v14"',
            '"full-strict-v15"',
        )
    ):
        raise CensusError("TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID")
    return adapted


def _load_historical_execution_engine() -> ModuleType:
    raw = _HISTORICAL_RUNNER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _HISTORICAL_RUNNER_SHA256:
        raise CensusError("TASK7_V16_HISTORICAL_CENSUS_ENGINE_DRIFT")
    adapted_source = _adapt_historical_execution_source(raw)
    spec = importlib.util.spec_from_loader(
        "_task7_v11_current_guard_engine_for_v16",
        loader=None,
        origin=str(_HISTORICAL_RUNNER_PATH),
    )
    if spec is None:
        raise CensusError("TASK7_V16_HISTORICAL_CENSUS_ENGINE_IDENTITY_MISMATCH")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(_HISTORICAL_RUNNER_PATH)
    sys.modules[spec.name] = module
    exec(  # noqa: S102 - exact hash-pinned historical engine bytes.
        compile(adapted_source, str(_HISTORICAL_RUNNER_PATH), "exec"),
        module.__dict__,
    )
    if module.BASE_SCHEMA_REVISION != BASE_SCHEMA_REVISION:
        raise CensusError("TASK7_V16_HISTORICAL_CENSUS_ENGINE_IDENTITY_MISMATCH")
    return module


with _HARNESS_SOURCE_STORE.activation():
    _SCAFFOLD = importlib.import_module("scripts._task7_v16_census_scaffold")
    GUARD_OWNER_ROLE = _SCAFFOLD.GUARD_OWNER_ROLE
    catalog_projection = _SCAFFOLD.catalog_projection
    current_function_rows = _SCAFFOLD.current_function_rows
    disable_all_current_user_triggers = _SCAFFOLD.disable_all_current_user_triggers
    disable_target_trigger = _SCAFFOLD.disable_target_trigger
    enable_target_trigger = _SCAFFOLD.enable_target_trigger
    install_artifact_sql_scaffold = _SCAFFOLD.install_artifact_sql_scaffold

    _POSTGRES_SUPPORT = importlib.import_module("tests.slice100_task2b._support")
    DisposablePostgres = _POSTGRES_SUPPORT.DisposablePostgres
    disposable_postgres = _POSTGRES_SUPPORT.disposable_postgres
    grant_application_access = _POSTGRES_SUPPORT.grant_application_access
    migrate = _POSTGRES_SUPPORT.migrate

    _V11 = _load_historical_execution_engine()

_HISTORICAL_NATIVE_SOURCE_VALUES: Final = _V11._native_source_values
_HISTORICAL_UNBOUND_SOURCE_VALUES: Final = _V11._unbound_source_values
_HISTORICAL_NATIVE_OUTPUT_VALUES: Final = _V11._native_output_values


def _canonical_bytes(value: Any) -> bytes:
    return _canonical_scalar_json_bytes(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CensusError(f"TASK7_V16_CENSUS_NON_JSON_EVIDENCE:{type(value).__name__}")


def _validate_serialized_provenance(
    value: Any,
    *,
    error_code: str,
) -> None:
    """Reject internal historical identities anywhere in serialized evidence."""

    active: set[int] = set()

    def visit(current: Any) -> None:
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                raise CensusError(error_code)
            active.add(identity)
            try:
                for key, child in current.items():
                    if not isinstance(key, str):
                        raise CensusError(error_code)
                    visit(key)
                    visit(child)
            finally:
                active.remove(identity)
            return
        if isinstance(current, list):
            identity = id(current)
            if identity in active:
                raise CensusError(error_code)
            active.add(identity)
            try:
                for child in current:
                    visit(child)
            finally:
                active.remove(identity)
            return
        if isinstance(current, str):
            if _FORBIDDEN_SERIALIZED_PROVENANCE_RE.search(current):
                raise CensusError(error_code)
            return
        if current is None or isinstance(current, (bool, int)):
            return
        if isinstance(current, float) and math.isfinite(current):
            return
        raise CensusError(error_code)

    visit(value)


def validate_historical_fixture_values(
    table_name: str,
    values: Mapping[str, Any],
) -> None:
    """Reject stale, unknown, or misplaced contract versions before SQL."""

    _identifier(table_name)

    def visit(value: Any, *, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, path=(*path, str(key)))
            return
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            for index, child in enumerate(value):
                visit(child, path=(*path, str(index)))
            return
        if not isinstance(value, str):
            return
        if path and path[-1] == "pipeline_contract_version":
            if len(path) != 1 or value != "full-strict-v16":
                raise CensusError("TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID")
            return
        if _HISTORICAL_CONTRACT_VERSION_RE.fullmatch(value.casefold()):
            raise CensusError("TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID")

    visit(values, path=())


def _validated_historical_insert(
    *,
    branch: str,
    original_insert: Any,
    connection: Any,
    column_map: Mapping[str, Any],
    table_name: str,
    values: Mapping[str, Any],
    returning: Sequence[str] = (),
) -> Mapping[str, Any] | None:
    if branch not in {"ORDINARY", "TASK5_DEFERRED"}:
        raise CensusError("TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID")
    validate_historical_fixture_values(table_name, values)
    return cast(
        Mapping[str, Any] | None,
        original_insert(
            connection,
            column_map,
            table_name,
            values,
            returning=returning,
        ),
    )


def _decode_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    return raw, _decode_exact_candidate(raw)


def _ensure_admin_url() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip():
        return
    dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not dotenv_path:
        raise CensusError("TASK7_V16_POSTGRES_ADMIN_URL_REQUIRED")
    try:
        from idis.runtime.profile import _parse_dotenv

        values = _parse_dotenv(dotenv_path)
        admin_url = values["IDIS_DATABASE_ADMIN_URL"].strip()
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise CensusError("TASK7_V16_POSTGRES_ADMIN_URL_REQUIRED") from exc
    if not admin_url:
        raise CensusError("TASK7_V16_POSTGRES_ADMIN_URL_REQUIRED")
    os.environ["IDIS_DATABASE_ADMIN_URL"] = admin_url


def _capture_cluster_safety_snapshot() -> dict[str, Any]:
    """Require approved fixed principals and snapshot disposable identities."""

    _ensure_admin_url()
    configured = os.environ["IDIS_DATABASE_ADMIN_URL"]
    maintenance = create_engine(
        make_url(configured).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with maintenance.connect() as connection:
            role_rows = connection.execute(
                text(
                    "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                    "rolinherit,rolbypassrls,rolreplication "
                    "FROM pg_catalog.pg_roles "
                    "WHERE rolname = ANY(:role_names) ORDER BY rolname"
                ),
                {"role_names": list(_FIXED_CLUSTER_ROLE_POSTURE)},
            )
            role_posture = {str(row.rolname): tuple(row)[1:] for row in role_rows}
            if role_posture != _FIXED_CLUSTER_ROLE_POSTURE:
                raise CensusError("TASK7_V16_FIXED_ROLE_POSTURE_INVALID")
            membership_count = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_auth_members m "
                        "JOIN pg_catalog.pg_roles member_role "
                        "ON member_role.oid=m.member "
                        "JOIN pg_catalog.pg_roles granted_role "
                        "ON granted_role.oid=m.roleid "
                        "WHERE member_role.rolname = ANY(:role_names) "
                        "OR granted_role.rolname = ANY(:role_names)"
                    ),
                    {"role_names": list(_FIXED_CLUSTER_ROLE_POSTURE)},
                ).scalar_one()
            )
            if membership_count:
                raise CensusError("TASK7_V16_FIXED_ROLE_MEMBERSHIP_FORBIDDEN")
            generated_databases = tuple(
                str(row.datname)
                for row in connection.execute(
                    text(
                        "SELECT datname FROM pg_catalog.pg_database "
                        "WHERE datname LIKE 'idis_t2b_db_%' ORDER BY datname"
                    )
                )
            )
            generated_app_roles = tuple(
                str(row.rolname)
                for row in connection.execute(
                    text(
                        "SELECT rolname FROM pg_catalog.pg_roles "
                        "WHERE rolname LIKE 'idis_t2b_app_%' ORDER BY rolname"
                    )
                )
            )
    finally:
        maintenance.dispose()
    return {
        "role_posture_tuple": tuple(sorted(role_posture.items())),
        "membership_edge_count": membership_count,
        "generated_database_tuple": generated_databases,
        "generated_app_role_tuple": generated_app_roles,
    }


def _assert_cluster_residue_free(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    for field in ("generated_database_tuple", "generated_app_role_tuple"):
        if after.get(field) != before.get(field):
            raise CensusError(f"TASK7_V16_CLUSTER_RESIDUE_DETECTED:{field}")


@contextmanager
def _cluster_safety_boundary() -> Iterator[None]:
    """Prove the disposable database/role helpers leave no cluster residue."""

    before = _capture_cluster_safety_snapshot()
    try:
        yield
    finally:
        after = _capture_cluster_safety_snapshot()
        _assert_cluster_residue_free(before, after)


def validate_fixture_values(
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    table_name: str,
    values: Mapping[str, Any],
) -> tuple[tuple[str, Any, str], ...]:
    """Delegate the strict seam while projecting only the v16 error domain."""

    try:
        return cast(
            tuple[tuple[str, Any, str], ...],
            _V11.validate_fixture_values(columns, table_name, values),
        )
    except _V11.CensusError as exc:
        raise CensusError(str(exc).replace("TASK7_V11_", "TASK7_V16_")) from exc


def corrected_native_source_values(
    *,
    table_name: str,
    tenant_id: str,
    deal_id: str,
    root_id: str,
    corpus_id: str,
) -> dict[str, Any]:
    """Correct the historical package seed without weakening strict inserts."""

    values = dict(
        _HISTORICAL_NATIVE_SOURCE_VALUES(
            table_name=table_name,
            tenant_id=tenant_id,
            deal_id=deal_id,
            root_id=root_id,
            corpus_id=corpus_id,
        )
    )
    if table_name == "data_room_packages":
        if values.pop("source_corpus_receipt_id", None) != corpus_id:
            raise CensusError("TASK7_V16_PACKAGE_NATIVE_SEED_INVALID")
        values["package_id"] = corpus_id
        values["status"] = "SEALED"
    elif table_name == "data_room_package_files":
        if values.pop("source_corpus_receipt_id", None) != corpus_id:
            raise CensusError("TASK7_V16_PACKAGE_FILE_NATIVE_SEED_INVALID")
        values.update(
            {
                "file_entry_id": str(uuid.uuid4()),
                "package_id": corpus_id,
                "sequence": 1,
                "path_hash": "4" * 64,
                "file_status": "PENDING",
                "support_status": "SUPPORTED",
                "triage_status": "READY",
                "doc_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
            }
        )
    return values


def corrected_unbound_source_values(*, table_name: str) -> dict[str, Any]:
    """Let PostgreSQL derive the package corpus root from package_id."""

    values = dict(_HISTORICAL_UNBOUND_SOURCE_VALUES(table_name=table_name))
    if table_name == "data_room_packages":
        corpus_id = values.pop("source_corpus_receipt_id", None)
        if not isinstance(corpus_id, str) or "package_id" not in values:
            raise CensusError("TASK7_V16_PACKAGE_UNBOUND_SEED_INVALID")
    elif table_name == "data_room_package_files":
        corpus_id = values.pop("source_corpus_receipt_id", None)
        if not isinstance(corpus_id, str):
            raise CensusError("TASK7_V16_PACKAGE_FILE_UNBOUND_SEED_INVALID")
        values.update(
            {
                "file_entry_id": str(uuid.uuid4()),
                "package_id": corpus_id,
                "sequence": 1,
                "path_hash": "4" * 64,
                "file_status": "PENDING",
                "support_status": "SUPPORTED",
                "triage_status": "READY",
                "doc_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
            }
        )
    return values


def artifact_native_output_discriminators(
    artifact: Mapping[str, Any],
) -> dict[str, str]:
    patterns = {
        "deterministic_calculations": re.compile(
            r"producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' "
            r"AND run_output_object_kind='([^']+)'"
        ),
        "muhasabah_records": re.compile(
            r"source_step='METHODOLOGY_EVIDENCE_TRUST_COURT' AND "
            r"run_output_object_kind='([^']+)'"
        ),
        "vector_embeddings": re.compile(
            r"producer_step_name='EXTRACT' AND embedding_namespace="
            r"'internal_evidence' AND run_output_object_kind='([^']+)'"
        ),
    }
    rows = {
        str(row["table_name"]): row
        for row in artifact["current_ddl_registry"]["rows"]
        if str(row["table_name"]) in patterns
    }
    if set(rows) != set(patterns):
        raise CensusError("TASK7_V16_NATIVE_DISCRIMINATOR_TARGET_DRIFT")
    result: dict[str, str] = {}
    for table_name, pattern in patterns.items():
        reviewed_checks = " ".join(str(value) for value in rows[table_name]["check_tuple"])
        match = pattern.search(reviewed_checks)
        if match is None or not match.group(1):
            raise CensusError(f"TASK7_V16_NATIVE_DISCRIMINATOR_MISSING:{table_name}")
        result[table_name] = match.group(1)
    return result


def corrected_native_output_values(
    *,
    table_name: str,
    graph: Mapping[str, Any],
    discriminator_by_table: Mapping[str, str],
) -> dict[str, Any]:
    """Populate the three reviewed discriminator columns that are not generated."""

    values = dict(
        _HISTORICAL_NATIVE_OUTPUT_VALUES(
            table_name=table_name,
            graph=graph,
        )
    )
    if set(discriminator_by_table) != {
        "deterministic_calculations",
        "muhasabah_records",
        "vector_embeddings",
    }:
        raise CensusError("TASK7_V16_NATIVE_DISCRIMINATOR_TARGET_DRIFT")
    discriminator = discriminator_by_table.get(table_name)
    if discriminator is not None:
        values["run_output_object_kind"] = discriminator
    return values


def _fixture_rejection_proofs(
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> tuple[tuple[str, str, str, str], ...]:
    cases = (
        (
            "UNKNOWN_FIELD",
            "runs",
            "unknown_fixture_field",
            {"unknown_fixture_field": "rejected"},
        ),
        (
            "OTHER_TARGET_FIELD",
            "runs",
            "step_id",
            {"step_id": str(uuid.uuid4())},
        ),
        (
            "GENERATED_FIELD",
            "methodology_extraction_results",
            "run_output_object_kind",
            {"run_output_object_kind": "rejected"},
        ),
    )
    result: list[tuple[str, str, str, str]] = []
    for kind, table_name, field, values in cases:
        try:
            validate_fixture_values(columns, table_name, values)
        except CensusError as exc:
            result.append((kind, table_name, field, str(exc)))
        else:
            raise CensusError(f"TASK7_V16_CENSUS_FIXTURE_REJECTION_MISSING:{table_name}.{field}")
    return tuple(result)


def _artifact_trigger_contract(
    artifact: Mapping[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    result: list[tuple[Any, ...]] = []
    for function in current_function_rows(artifact):
        signature = str(function["function_signature"])
        function_name = signature.partition("(")[0].removeprefix("public.")
        for coordinate in function["target_event_coordinate_tuple"]:
            (
                table_name,
                trigger_name,
                timing,
                event,
                level,
                enabled,
                constraint,
                deferrable,
                initially_deferred,
            ) = coordinate
            result.append(
                (
                    str(function["authority_id"]),
                    str(table_name),
                    str(trigger_name),
                    str(timing),
                    str(event),
                    str(level),
                    str(enabled),
                    bool(constraint),
                    bool(deferrable),
                    bool(initially_deferred),
                    "public",
                    function_name,
                )
            )
    contract = tuple(result)
    if len(contract) != 192 or len(set(contract)) != 192:
        raise CensusError("TASK7_V16_TRIGGER_CONTRACT_INVENTORY_INVALID")
    return contract


def _artifact_targets(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        dict.fromkeys(
            (str(row[0]), str(row[1]), str(row[2])) for row in _artifact_trigger_contract(artifact)
        )
    )


def _expected_trigger_projection(
    contract: Sequence[Sequence[Any]],
    targets: Sequence[tuple[str, str, str]],
) -> tuple[tuple[Any, ...], ...]:
    result: list[tuple[Any, ...]] = []
    for authority_id, table_name, trigger_name in targets:
        rows = tuple(
            tuple(row)
            for row in contract
            if tuple(row[:3]) == (authority_id, table_name, trigger_name)
        )
        if not rows:
            raise CensusError("TASK7_V16_TRIGGER_CONTRACT_TARGET_MISSING")
        first = rows[0]
        result.append(
            (
                authority_id,
                table_name,
                trigger_name,
                first[3],
                [row[4] for row in rows],
                first[5],
                first[6],
                "CONSTRAINT" if first[7] else "PLAIN",
                first[8],
                first[9],
                first[10],
                first[11],
            )
        )
    return tuple(result)


def _validate_request(
    *,
    artifact: Mapping[str, Any],
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> tuple[
    tuple[tuple[str, str, str], ...],
    tuple[tuple[Any, ...], ...],
]:
    if base_schema_revision != BASE_SCHEMA_REVISION or harness_identity != HARNESS_IDENTITY:
        raise CensusError("TASK7_V16_CENSUS_HARNESS_BINDING_MISMATCH")
    targets = tuple(tuple(map(str, row)) for row in ordered_authority_target_tuple)
    contract = tuple(tuple(row) for row in ordered_trigger_contract_coordinate_tuple)
    if targets != _artifact_targets(artifact) or len(targets) != 73:
        raise CensusError("TASK7_V16_CENSUS_TARGET_INVENTORY_MISMATCH")
    if contract != _artifact_trigger_contract(artifact):
        raise CensusError("TASK7_V16_CENSUS_TRIGGER_CONTRACT_MISMATCH")
    if len(production_constructor_spec_tuple) != 4:
        raise CensusError("TASK7_V16_CENSUS_CONSTRUCTOR_INVENTORY_MISMATCH")
    return targets, contract


def _live_trigger_projection(
    connection: Connection,
    contract: Sequence[Sequence[Any]],
    targets: Sequence[tuple[str, str, str]],
) -> tuple[tuple[Any, ...], ...]:
    records = {
        (str(row.table_name), str(row.trigger_name)): row
        for row in connection.execute(
            text(
                """
                SELECT c.relname AS table_name,t.tgname AS trigger_name,
                       t.tgtype,t.tgenabled,
                       (t.tgconstraint<>0) AS is_constraint,
                       t.tgdeferrable,t.tginitdeferred,
                       pn.nspname AS function_schema,
                       p.proname AS function_name
                FROM pg_catalog.pg_trigger AS t
                JOIN pg_catalog.pg_class AS c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_proc AS p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace AS pn ON pn.oid=p.pronamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal
                ORDER BY c.relname,t.tgname
                """
            )
        )
    }
    projection: list[tuple[Any, ...]] = []
    for authority_id, table_name, trigger_name in targets:
        record = records.get((table_name, trigger_name))
        if record is None:
            raise CensusError(f"TASK7_V16_CENSUS_TRIGGER_MISSING:{table_name}.{trigger_name}")
        rows = tuple(
            tuple(row)
            for row in contract
            if tuple(row[:3]) == (authority_id, table_name, trigger_name)
        )
        tgtype = int(record.tgtype)
        actual_events = tuple(
            event for event, bit in (("INSERT", 4), ("UPDATE", 16), ("DELETE", 8)) if tgtype & bit
        )
        expected_events = tuple(str(row[4]) for row in rows)
        if actual_events != expected_events:
            raise CensusError(f"TASK7_V16_CENSUS_TRIGGER_EVENT_DRIFT:{table_name}.{trigger_name}")
        actual_common = (
            "BEFORE" if tgtype & 2 else "AFTER",
            "ROW" if tgtype & 1 else "STATEMENT",
            str(record.tgenabled),
            bool(record.is_constraint),
            bool(record.tgdeferrable),
            bool(record.tginitdeferred),
            str(record.function_schema),
            str(record.function_name),
        )
        expected_common = (
            str(rows[0][3]),
            str(rows[0][5]),
            str(rows[0][6]),
            bool(rows[0][7]),
            bool(rows[0][8]),
            bool(rows[0][9]),
            str(rows[0][10]),
            str(rows[0][11]),
        )
        if actual_common != expected_common or any(
            (
                str(row[3]),
                str(row[5]),
                str(row[6]),
                bool(row[7]),
                bool(row[8]),
                bool(row[9]),
                str(row[10]),
                str(row[11]),
            )
            != expected_common
            for row in rows
        ):
            raise CensusError(
                f"TASK7_V16_CENSUS_TRIGGER_PROJECTION_DRIFT:{table_name}.{trigger_name}"
            )
        projection.append(
            (
                authority_id,
                table_name,
                trigger_name,
                actual_common[0],
                list(actual_events),
                actual_common[1],
                actual_common[2],
                "CONSTRAINT" if actual_common[3] else "PLAIN",
                actual_common[4],
                actual_common[5],
                actual_common[6],
                actual_common[7],
            )
        )
    if len(projection) != 73:
        raise CensusError("TASK7_V16_CENSUS_TRIGGER_PROJECTION_INCOMPLETE")
    return tuple(projection)


def _normalize_locked_search_path(
    proconfig: Any,
    *,
    expected: Sequence[str],
    error_code: str,
) -> tuple[str, ...]:
    """Parse exactly one PostgreSQL search_path setting without coercion."""

    if isinstance(proconfig, (str, bytes, bytearray)) or not isinstance(
        proconfig,
        Sequence,
    ):
        raise CensusError(error_code)
    settings: dict[str, str] = {}
    for raw_setting in proconfig:
        if not isinstance(raw_setting, str):
            raise CensusError(error_code)
        name, separator, raw_value = raw_setting.partition("=")
        if not separator or not _IDENTIFIER_RE.fullmatch(name) or name in settings or not raw_value:
            raise CensusError(error_code)
        settings[name] = raw_value
    if set(settings) != {"search_path"}:
        raise CensusError(error_code)
    actual = tuple(value.strip() for value in settings["search_path"].split(","))
    if not actual or any(not value for value in actual) or actual != tuple(expected):
        raise CensusError(error_code)
    return actual


def _current_routine_catalog(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for expected in current_function_rows(artifact):
        signature = str(expected["function_signature"])
        row = connection.execute(
            text(
                """
                SELECT p.oid,n.nspname,p.proname,owner.rolname AS owner,
                       p.prosecdef,p.proconfig
                FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
                WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                """
            ),
            {"signature": signature},
        ).one_or_none()
        if row is None:
            raise CensusError(f"TASK7_V16_CURRENT_ROUTINE_MISSING:{signature}")
        search_path_tuple = _normalize_locked_search_path(
            row.proconfig,
            expected=tuple(map(str, expected["search_path_tuple"])),
            error_code=f"TASK7_V16_CURRENT_ROUTINE_ACL_DRIFT:{signature}",
        )
        search_path = ", ".join(search_path_tuple)
        app_execute = bool(
            connection.execute(
                text(
                    "SELECT pg_catalog.has_function_privilege("
                    ":role_name,pg_catalog.to_regprocedure(:signature),'EXECUTE')"
                ),
                {"role_name": application_role, "signature": signature},
            ).scalar_one()
        )
        public_execute = bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                      FROM pg_catalog.pg_proc p
                      CROSS JOIN LATERAL pg_catalog.aclexplode(
                        COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                      ) acl
                      WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                        AND acl.grantee=0 AND acl.privilege_type='EXECUTE'
                    )
                    """
                ),
                {"signature": signature},
            ).scalar_one()
        )
        raw_execute_acl = tuple(
            {
                "grantee": str(acl.grantee),
                "grantor": str(acl.grantor),
                "privilege_type": str(acl.privilege_type),
                "is_grantable": bool(acl.is_grantable),
            }
            for acl in connection.execute(
                text(
                    """
                    SELECT grantee.rolname AS grantee,
                           grantor.rolname AS grantor,
                           acl.privilege_type,acl.is_grantable
                    FROM pg_catalog.pg_proc p
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                      COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                    ) acl
                    JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                    JOIN pg_catalog.pg_roles grantor ON grantor.oid=acl.grantor
                    WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                    ORDER BY grantee.rolname,acl.privilege_type,grantor.rolname
                    """
                ),
                {"signature": signature},
            )
        )
        projected = {
            "function_signature": signature,
            "function_kind": str(expected["function_kind"]),
            "owner": str(row.owner),
            "security_definer": bool(row.prosecdef),
            "search_path": search_path,
            "application_direct_execute": app_execute,
            "public_direct_execute": public_execute,
            "raw_execute_acl_tuple": list(raw_execute_acl),
        }
        if (
            projected["owner"] != GUARD_OWNER_ROLE
            or projected["security_definer"] != (expected["security_mode"] == "DEFINER")
            or projected["search_path"] != "pg_catalog, pg_temp"
            or app_execute
            or public_execute
            or len(raw_execute_acl) != 1
            or {
                key: raw_execute_acl[0][key]
                for key in ("grantee", "grantor", "privilege_type", "is_grantable")
            }
            != {
                "grantee": GUARD_OWNER_ROLE,
                "grantor": GUARD_OWNER_ROLE,
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            }
        ):
            raise CensusError(f"TASK7_V16_CURRENT_ROUTINE_ACL_DRIFT:{signature}")
        result.append(projected)
    if len(result) != 19:
        raise CensusError("TASK7_V16_CURRENT_ROUTINE_CATALOG_INCOMPLETE")
    return tuple(result)


def _guard_owner_raw_attribute_acl(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    role = artifact["sql_authority_registry"]["guard_function_acl_registry"]["guard_owner_role"]
    expected = {
        (str(relation), str(column))
        for relation, columns in role["select_relation_column_tuple"]
        for column in columns
    }
    rows = tuple(
        {
            "relation": str(row.relation),
            "column": str(row.column_name),
            "privilege_type": str(row.privilege_type),
            "is_grantable": bool(row.is_grantable),
            "raw_attacl": str(row.raw_attacl),
        }
        for row in connection.execute(
            text(
                """
                SELECT c.relname AS relation,a.attname AS column_name,
                       acl.privilege_type,acl.is_grantable,
                       a.attacl::text AS raw_attacl
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
                  AND grantee.rolname=:role_name
                ORDER BY c.relname,a.attnum,acl.privilege_type
                """
            ),
            {"role_name": GUARD_OWNER_ROLE},
        )
    )
    actual = {
        (row["relation"], row["column"])
        for row in rows
        if row["privilege_type"] == "SELECT" and not row["is_grantable"]
    }
    if actual != expected or len(rows) != 366:
        raise CensusError("TASK7_V16_GUARD_OWNER_COLUMN_ACL_DRIFT")
    if any(row["privilege_type"] != "SELECT" or row["is_grantable"] for row in rows):
        raise CensusError("TASK7_V16_GUARD_OWNER_NONSELECT_ACL_FORBIDDEN")
    return rows


def _guard_owner_raw_schema_acl(connection: Connection) -> tuple[dict[str, Any], ...]:
    rows = tuple(
        {
            "schema_name": str(row.schema_name),
            "grantee": str(row.grantee),
            "grantor": str(row.grantor),
            "privilege_type": str(row.privilege_type),
            "is_grantable": bool(row.is_grantable),
        }
        for row in connection.execute(
            text(
                """
                SELECT n.nspname AS schema_name,grantee.rolname AS grantee,
                       grantor.rolname AS grantor,
                       acl.privilege_type,acl.is_grantable
                FROM pg_catalog.pg_namespace n
                CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles grantor ON grantor.oid=acl.grantor
                WHERE n.nspname='public' AND grantee.rolname=:role_name
                ORDER BY acl.privilege_type,grantor.rolname
                """
            ),
            {"role_name": GUARD_OWNER_ROLE},
        )
    )
    if len(rows) != 1 or {
        key: rows[0][key] for key in ("schema_name", "grantee", "privilege_type", "is_grantable")
    } != {
        "schema_name": "public",
        "grantee": GUARD_OWNER_ROLE,
        "privilege_type": "USAGE",
        "is_grantable": False,
    }:
        raise CensusError("TASK7_V16_GUARD_OWNER_SCHEMA_ACL_DRIFT")
    return rows


def _guard_owner_posture(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    record = connection.execute(
        text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=:role_name"
        ),
        {"role_name": GUARD_OWNER_ROLE},
    ).one()
    membership_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_auth_members m "
                "JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member "
                "JOIN pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid "
                "WHERE member_role.rolname=:role_name "
                "OR granted_role.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    owned_relation_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_roles owner ON owner.oid=c.relowner "
                "WHERE owner.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    owned_routine_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner "
                "WHERE owner.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    schema_usage, schema_create = connection.execute(
        text(
            "SELECT pg_catalog.has_schema_privilege(:role_name,'public','USAGE'),"
            "pg_catalog.has_schema_privilege(:role_name,'public','CREATE')"
        ),
        {"role_name": GUARD_OWNER_ROLE},
    ).one()
    privilege_counts = tuple(
        int(value)
        for value in connection.execute(
            text(
                """
                SELECT
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'SELECT'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'INSERT'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'UPDATE'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'DELETE'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'TRUNCATE'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'REFERENCES'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'TRIGGER'
                    )
                  )
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p')
                """
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).one()
    )
    relation_names = [
        str(row[0])
        for row in artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "guard_owner_role"
        ]["select_relation_column_tuple"]
    ]
    force_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=ANY(:relations) "
                "AND c.relrowsecurity AND c.relforcerowsecurity"
            ),
            {"relations": relation_names},
        ).scalar_one()
    )
    if force_count != 36:
        raise CensusError("TASK7_V16_GUARD_PARENT_FORCE_RLS_INCOMPLETE")
    posture = {
        "role_name": GUARD_OWNER_ROLE,
        "can_login": bool(record.rolcanlogin),
        "is_superuser": bool(record.rolsuper),
        "can_create_db": bool(record.rolcreatedb),
        "can_create_role": bool(record.rolcreaterole),
        "inherits": bool(record.rolinherit),
        "bypass_rls": bool(record.rolbypassrls),
        "membership_count": membership_count,
        "owned_relation_count": owned_relation_count,
        "owned_routine_count": owned_routine_count,
        "schema_usage": bool(schema_usage),
        "schema_create": bool(schema_create),
        "table_select_privilege_count": privilege_counts[0],
        "table_insert_privilege_count": privilege_counts[1],
        "table_update_privilege_count": privilege_counts[2],
        "table_delete_privilege_count": privilege_counts[3],
        "table_truncate_privilege_count": privilege_counts[4],
        "table_references_privilege_count": privilege_counts[5],
        "table_trigger_privilege_count": privilege_counts[6],
    }
    expected = {
        "role_name": GUARD_OWNER_ROLE,
        "can_login": False,
        "is_superuser": False,
        "can_create_db": False,
        "can_create_role": False,
        "inherits": False,
        "bypass_rls": False,
        "membership_count": 0,
        "owned_relation_count": 0,
        "owned_routine_count": 19,
        "schema_usage": True,
        "schema_create": False,
        "table_select_privilege_count": 0,
        "table_insert_privilege_count": 0,
        "table_update_privilege_count": 0,
        "table_delete_privilege_count": 0,
        "table_truncate_privilege_count": 0,
        "table_references_privilege_count": 0,
        "table_trigger_privilege_count": 0,
    }
    if posture != expected:
        raise CensusError("TASK7_V16_GUARD_OWNER_POSTURE_INVALID")
    return posture, force_count


def _sqlstate(error: DBAPIError) -> str:
    value = getattr(error.orig, "sqlstate", None) or getattr(
        error.orig,
        "pgcode",
        None,
    )
    rendered = str(value or "")
    if not re.fullmatch(r"[0-9A-Z]{5}", rendered):
        raise CensusError("TASK7_V16_CENSUS_SQLSTATE_MISSING") from error
    return rendered


def _rejection_marker(error: DBAPIError, authority_id: str) -> str:
    try:
        return str(_V11._rejection_marker(error, authority_id))
    except _V11.CensusError as exc:
        raise CensusError(str(exc).replace("TASK7_V11_", "TASK7_V16_")) from exc


def _set_tenant_context(connection: Connection, tenant_id: str) -> None:
    connection.execute(
        text("SELECT pg_catalog.set_config('idis.tenant_id',:tenant_id,true)"),
        {"tenant_id": tenant_id},
    )


class _CommittedTriggerToggle:
    """Translate the historical replica seam into committed admin DDL phases."""

    def __init__(
        self,
        *,
        app_connection: Connection,
        admin_engine: Any,
        table_name: str,
        trigger_name: str,
    ) -> None:
        self.app_connection = app_connection
        self.admin_engine = admin_engine
        self.table_name = table_name
        self.trigger_name = trigger_name
        self.tenant_id: str | None = None
        self.fixture_helper_projections: list[dict[str, Any]] = []

    def observe_tenant(self, tenant_id: Any) -> None:
        rendered = str(tenant_id)
        if self.tenant_id is not None and self.tenant_id != rendered:
            raise CensusError("TASK7_V16_CASE_TENANT_DRIFT")
        self.tenant_id = rendered
        _set_tenant_context(self.app_connection, rendered)

    def set_replica(self, _connection: Connection, enabled: bool) -> None:
        if self.app_connection.in_transaction():
            self.app_connection.commit()
        with self.admin_engine.begin() as admin:
            if enabled:
                disable_target_trigger(
                    admin,
                    self.table_name,
                    self.trigger_name,
                )
            else:
                enable_target_trigger(
                    admin,
                    self.table_name,
                    self.trigger_name,
                )
        if not enabled and self.tenant_id is not None:
            _set_tenant_context(self.app_connection, self.tenant_id)

    def restore_tenant_context(self) -> None:
        if self.tenant_id is None:
            raise CensusError("TASK7_V16_CASE_TENANT_CONTEXT_MISSING")
        _set_tenant_context(self.app_connection, self.tenant_id)


def _observe_case_actor(connection: Connection, *, expected_role: str) -> dict[str, Any]:
    row = connection.execute(
        text(
            "SELECT session_user,current_user,r.rolsuper,r.rolbypassrls "
            "FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"
        )
    ).one()
    observed = {
        "session_user": str(row.session_user),
        "current_user": str(row.current_user),
        "is_superuser": bool(row.rolsuper),
        "bypass_rls": bool(row.rolbypassrls),
    }
    if observed != {
        "session_user": expected_role,
        "current_user": expected_role,
        "is_superuser": False,
        "bypass_rls": False,
    }:
        raise CensusError("TASK7_V16_CASE_APPLICATION_ROLE_POSTURE_INVALID")
    return observed


def _v16_output_root_values(
    graph: Mapping[str, Any],
    *,
    output_kind: str,
    identity_sha256: str,
    output_sha256: str,
) -> dict[str, Any]:
    values = dict(
        _V11._output_root_values(
            graph,
            output_kind=output_kind,
            identity_sha256=identity_sha256,
            output_sha256=output_sha256,
        )
    )
    values["output_schema_version"] = "task7-v16-census-v1"
    values["pipeline_contract_version"] = "full-strict-v16"
    return values


def _fixture_native_hashes_as_admin(
    *,
    database: DisposablePostgres,
    toggle: _CommittedTriggerToggle,
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
    graph: Mapping[str, Any],
    output_kind: str,
) -> tuple[str, str]:
    signature = "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
    with database.admin_engine.begin() as admin:
        _set_tenant_context(admin, str(graph["tenant_id"]))
        actor = admin.execute(text("SELECT current_user")).scalar_one()
        rows = (
            admin.execute(
                text(
                    """
                SELECT output_kind,expected_native_identity_sha256,
                       expected_native_output_sha256
                FROM public.idis_task7_current_native_hashes_v1(
                    CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                    CAST(:run_id AS uuid),CAST(:output_object_id AS uuid)
                )
                """
                ),
                dict(graph),
            )
            .mappings()
            .all()
        )
    if len(rows) != 1 or str(rows[0]["output_kind"]) != output_kind:
        raise CensusError("TASK7_V16_FIXTURE_NATIVE_HASH_DISPATCH_INCOMPLETE")
    identity_sha256 = str(rows[0]["expected_native_identity_sha256"])
    output_sha256 = str(rows[0]["expected_native_output_sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", identity_sha256) or not re.fullmatch(
        r"[0-9a-f]{64}",
        output_sha256,
    ):
        raise CensusError("TASK7_V16_FIXTURE_NATIVE_HASH_INVALID")
    toggle.fixture_helper_projections.append(
        {
            "authority_id": authority_id,
            "table_name": table_name,
            "guard_name": guard_name,
            "case_kind": case_kind,
            "phase": "COMMITTED_FIXTURE_SETUP",
            "function_signature": signature,
            "executed_as_role": str(actor),
            "output_kind": output_kind,
            "returned_pair_sha256": _digest(
                [
                    "task7-v16-fixture-native-hash-pair-v1",
                    identity_sha256,
                    output_sha256,
                ]
            ),
        }
    )
    return identity_sha256, output_sha256


def _commit_fixture_setup(connection: Connection, toggle: _CommittedTriggerToggle) -> None:
    if connection.in_transaction():
        connection.commit()
    toggle.restore_tenant_context()


def _output_exact_one_case_as_app(
    *,
    database: DisposablePostgres,
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    toggle: _CommittedTriggerToggle,
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, int]]:
    """Exercise exact-one DML as app while admin only derives fixture hashes."""

    native_table = (
        "methodology_coverage_records" if table_name == "run_output_objects" else table_name
    )
    graph = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "output_object_id": str(uuid.uuid4()),
    }
    toggle.set_replica(connection, True)

    if case_kind == "ILLEGAL":
        if table_name == "run_output_objects":
            toggle.observe_tenant(graph["tenant_id"])
            _V11._insert_strict(
                connection,
                columns,
                table_name,
                _v16_output_root_values(
                    graph,
                    output_kind="METHODOLOGY_COVERAGE_IDENTITY",
                    identity_sha256="0" * 64,
                    output_sha256="0" * 64,
                ),
            )
        else:
            _V11._insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
        _commit_fixture_setup(connection, toggle)
        toggle.set_replica(connection, False)
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
        _V11._flush_deferred(connection)
        return "OUTPUT_EXACT_ONE_EVENT", {}

    output_kind = _V11._insert_native_output(
        connection,
        columns,
        table_name=native_table,
        graph=graph,
    )
    _V11._insert_strict(
        connection,
        columns,
        "run_output_objects",
        _v16_output_root_values(
            graph,
            output_kind=output_kind,
            identity_sha256="0" * 64,
            output_sha256="0" * 64,
        ),
    )
    _commit_fixture_setup(connection, toggle)
    identity_sha256, output_sha256 = _fixture_native_hashes_as_admin(
        database=database,
        toggle=toggle,
        authority_id=authority_id,
        table_name=table_name,
        guard_name=guard_name,
        case_kind=case_kind,
        graph=graph,
        output_kind=output_kind,
    )
    connection.execute(
        text(
            """
            UPDATE public.run_output_objects
            SET native_identity_sha256=:identity_sha256,
                native_output_sha256=:output_sha256
            WHERE output_object_id=CAST(:output_object_id AS uuid)
            """
        ),
        {
            "identity_sha256": identity_sha256,
            "output_sha256": output_sha256,
            "output_object_id": graph["output_object_id"],
        },
    )
    connection.commit()
    toggle.restore_tenant_context()

    if case_kind == "LEGAL":
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
        connection.commit()
        toggle.set_replica(connection, False)
        if table_name == "run_output_objects":
            _V11._insert_strict(
                connection,
                columns,
                table_name,
                _v16_output_root_values(
                    graph,
                    output_kind=output_kind,
                    identity_sha256=identity_sha256,
                    output_sha256=output_sha256,
                ),
            )
        else:
            _V11._insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
        _V11._flush_deferred(connection)
        return "OUTPUT_EXACT_ONE_EVENT", {}

    toggle.set_replica(connection, False)
    connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    _V11._flush_deferred(connection)
    return "OUTPUT_EXACT_ONE_EVENT", {}


def _truncate_case_data(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    disable_all_current_user_triggers(connection, artifact)
    tables = tuple(
        str(value)
        for value in connection.execute(
            text(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname='public' AND tablename<>'alembic_version' "
                "ORDER BY tablename"
            )
        ).scalars()
    )
    if tables:
        rendered = ",".join(f"public.{_identifier(name)}" for name in tables)
        connection.exec_driver_sql(f"TRUNCATE TABLE {rendered} CASCADE")


def _event_for_case(
    *,
    case_kind: CaseKind,
    authority_id: str,
    table_name: str,
    events: Sequence[TriggerEvent],
) -> TriggerEvent:
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS
    ):
        return cast(
            TriggerEvent,
            {"LEGAL": "UPDATE", "ILLEGAL": "INSERT", "DIRECT_SQL": "DELETE"}[case_kind],
        )
    if case_kind == "LEGAL":
        return "INSERT" if "INSERT" in events else "UPDATE"
    if case_kind == "ILLEGAL":
        return "UPDATE" if "UPDATE" in events else events[0]
    return "DELETE" if "DELETE" in events else "UPDATE"


def _expected_rejection(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> tuple[str, str]:
    if case_kind == "LEGAL":
        raise CensusError("TASK7_V16_CENSUS_REJECTION_CASE_INVALID")
    if authority_id == "current-source-exact-one-v1":
        return "23514", "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    if authority_id == "current-output-exact-one-v1":
        return "23514", "STEP_OUTPUT_MISSING"
    if authority_id == "current-receipt-equality-v1":
        return "23514", "CONSUMER_RECEIPT_INCOMPLETE"
    if authority_id in {
        "current-input-binding-admission-v1",
        "current-append-only-lease-v1",
        "current-output-bound-v1",
    }:
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id == "current-source-bound-v1":
        return "40001", "TASK7_SOURCE_FREEZE_FENCED"
    if authority_id in {
        "current-runs-contract-immutable-v1",
        "current-run-steps-contract-immutable-v1",
    }:
        return "23514", "PIPELINE_CONTRACT_HASH_MISMATCH"
    if authority_id == "current-task5-coverage-replacement-v1":
        return "23514", "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE"
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS
        and case_kind == "ILLEGAL"
    ):
        return "40001", "WORKER_LEASE_FENCED"
    return "23514", "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE"


def _scenario_id(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> str:
    return str(
        _V11._scenario_id(
            authority_id=authority_id,
            table_name=table_name,
            case_kind=case_kind,
        )
    )


def _validated_rejection_metrics(
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve evidence collected before an expected authority rejection."""

    projection = metrics.get("admin_fixture_helper_projection_tuple", ())
    if not isinstance(projection, Sequence) or isinstance(
        projection,
        (str, bytes, bytearray),
    ):
        raise CensusError("TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID")
    return dict(metrics)


def _run_one_case_as_app(
    *,
    database: DisposablePostgres,
    artifact: Mapping[str, Any],
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_rejection = case_kind != "LEGAL"
    expected_scenario = _scenario_id(
        authority_id=authority_id,
        table_name=table_name,
        case_kind=case_kind,
    )
    with database.admin_engine.begin() as admin:
        enable_target_trigger(admin, table_name, guard_name)

    error: DBAPIError | None = None
    metrics: dict[str, Any] = {}
    original_insert = _V11._insert_strict
    original_replica = _V11._set_replica
    original_native_source_values = _V11._native_source_values
    original_unbound_source_values = _V11._unbound_source_values
    original_native_output_values = _V11._native_output_values
    native_output_discriminators = artifact_native_output_discriminators(artifact)
    task5_frozen_versions: list[dict[str, Any]] = []
    task5_deferred_selection: dict[str, Any] | None = None
    with database.app_engine.connect() as app:
        case_actor = _observe_case_actor(
            app,
            expected_role=database.role_name,
        )
        toggle = _CommittedTriggerToggle(
            app_connection=app,
            admin_engine=database.admin_engine,
            table_name=table_name,
            trigger_name=guard_name,
        )

        def insert_with_tenant_context(
            connection: Connection,
            column_map: Mapping[str, Mapping[str, tuple[str, bool]]],
            target_table: str,
            values: Mapping[str, Any],
            *,
            returning: Sequence[str] = (),
        ) -> Mapping[str, Any] | None:
            nonlocal task5_deferred_selection
            if values.get("tenant_id") is not None:
                toggle.observe_tenant(values["tenant_id"])
            rendered_values = dict(values)
            if authority_id == "current-task5-coverage-replacement-v1":
                if target_table == "methodology_versions":
                    if rendered_values.get("status") != "FROZEN":
                        raise CensusError("TASK7_V16_TASK5_VERSION_SEED_STATE_INVALID")
                    task5_frozen_versions.append(dict(rendered_values))
                    rendered_values.update(
                        {
                            "status": "IMPORTED",
                            "frozen_by_actor_id": None,
                            "freeze_audit_event_id": None,
                            "frozen_at": None,
                        }
                    )
                elif target_table == "run_methodology_selections":
                    if task5_deferred_selection is not None:
                        raise CensusError("TASK7_V16_TASK5_SELECTION_SEED_DUPLICATE")
                    task5_deferred_selection = rendered_values
                    return None
                elif (
                    target_table == "methodology_coverage_records"
                    and task5_deferred_selection is not None
                ):
                    if len(task5_frozen_versions) != 2:
                        raise CensusError("TASK7_V16_TASK5_VERSION_SEED_COUNT_INVALID")
                    for frozen in task5_frozen_versions:
                        connection.execute(
                            text(
                                "UPDATE public.methodology_versions "
                                "SET status='FROZEN',"
                                "frozen_by_actor_id=:frozen_by_actor_id,"
                                "freeze_audit_event_id="
                                "CAST(:freeze_audit_event_id AS uuid),"
                                "frozen_at=:frozen_at "
                                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                                "AND methodology_version_record_id="
                                "CAST(:methodology_version_record_id AS uuid)"
                            ),
                            {
                                "frozen_by_actor_id": frozen["frozen_by_actor_id"],
                                "freeze_audit_event_id": frozen["freeze_audit_event_id"],
                                "frozen_at": frozen["frozen_at"],
                                "tenant_id": frozen["tenant_id"],
                                "methodology_version_record_id": frozen[
                                    "methodology_version_record_id"
                                ],
                            },
                        )
                    _validated_historical_insert(
                        branch="TASK5_DEFERRED",
                        original_insert=original_insert,
                        connection=connection,
                        column_map=column_map,
                        table_name="run_methodology_selections",
                        values=task5_deferred_selection,
                    )
                    task5_deferred_selection = None
            return _validated_historical_insert(
                branch="ORDINARY",
                original_insert=original_insert,
                connection=connection,
                column_map=column_map,
                table_name=target_table,
                values=rendered_values,
                returning=returning,
            )

        _V11._insert_strict = insert_with_tenant_context
        _V11._set_replica = toggle.set_replica
        _V11._native_source_values = corrected_native_source_values
        _V11._unbound_source_values = corrected_unbound_source_values
        _V11._native_output_values = lambda *, table_name, graph: corrected_native_output_values(
            table_name=table_name,
            graph=graph,
            discriminator_by_table=native_output_discriminators,
        )
        try:
            try:
                if authority_id == "current-output-exact-one-v1":
                    scenario, metrics = _output_exact_one_case_as_app(
                        database=database,
                        connection=app,
                        columns=columns,
                        toggle=toggle,
                        authority_id=authority_id,
                        table_name=table_name,
                        guard_name=guard_name,
                        case_kind=case_kind,
                        trigger_event=trigger_event,
                    )
                else:
                    scenario, metrics = _V11._execute_case_operation(
                        app,
                        columns,
                        authority_id=authority_id,
                        table_name=table_name,
                        case_kind=case_kind,
                        trigger_event=trigger_event,
                    )
                if scenario != expected_scenario:
                    raise CensusError(f"TASK7_V16_CENSUS_SCENARIO_DRIFT:{expected_scenario}")
                if (
                    authority_id == "current-task6-immutable-replacement-v1"
                    and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS
                    and case_kind == "LEGAL"
                ):
                    generated = app.execute(
                        text(
                            "SELECT run_output_object_id IS NOT NULL AS output_attached,"
                            "run_output_object_class,run_output_object_kind "
                            f"FROM public.{_identifier(table_name)}"
                        )
                    ).one()
                    metrics["task6_generated_value_projection_tuple"] = (
                        (
                            table_name,
                            bool(generated.output_attached),
                            str(generated.run_output_object_class),
                            str(generated.run_output_object_kind),
                        ),
                    )
            except DBAPIError as exc:
                error = exc
        finally:
            _V11._insert_strict = original_insert
            _V11._set_replica = original_replica
            _V11._native_source_values = original_native_source_values
            _V11._unbound_source_values = original_unbound_source_values
            _V11._native_output_values = original_native_output_values
            if app.in_transaction():
                app.rollback()
        metrics["admin_fixture_helper_projection_tuple"] = tuple(toggle.fixture_helper_projections)

    with database.admin_engine.begin() as admin:
        _truncate_case_data(admin, artifact)

    timing = (
        "AFTER"
        if authority_id
        in {
            "current-source-exact-one-v1",
            "current-output-exact-one-v1",
            "current-receipt-equality-v1",
        }
        else "BEFORE"
    )
    base = {
        "case_kind": case_kind,
        "authority_id": authority_id,
        "table_name": table_name,
        "guard_name": guard_name,
        "trigger_event": trigger_event,
        "trigger_timing": timing,
        "constraint_flush_attempted": timing == "AFTER",
        "scenario_id": expected_scenario,
        "execution_path": ("DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION"),
        "executed_as_role": case_actor["current_user"],
        "observed_session_user": case_actor["session_user"],
        "observed_is_superuser": case_actor["is_superuser"],
        "observed_bypass_rls": case_actor["bypass_rls"],
    }
    if expected_rejection:
        if error is None:
            raise CensusError(
                "TASK7_V16_CENSUS_ILLEGAL_CASE_ACCEPTED:"
                f"{authority_id}:{table_name}:{guard_name}:{case_kind}"
            )
        sqlstate = _sqlstate(error)
        if sqlstate == "42501":
            raise CensusError(
                "TASK7_V16_CENSUS_PRIVILEGE_MASKING_FORBIDDEN:"
                f"{authority_id}:{table_name}:{guard_name}:{case_kind}"
            ) from error
        return (
            {
                **base,
                "outcome": "REJECTED",
                "sqlstate": sqlstate,
                "authority_rejection_marker": _rejection_marker(
                    error,
                    authority_id,
                ),
                "rejection_provenance": "HASH_BOUND_AUTHORITY",
            },
            _validated_rejection_metrics(metrics),
        )
    if error is not None:
        sqlstate = _sqlstate(error)
        raise CensusError(
            "TASK7_V16_CENSUS_LEGAL_CASE_REJECTED:"
            f"{authority_id}:{table_name}:{guard_name}:SQLSTATE={sqlstate}"
        ) from error
    return (
        {
            **base,
            "outcome": "ACCEPTED",
            "sqlstate": None,
            "authority_rejection_marker": None,
            "rejection_provenance": None,
        },
        metrics,
    )


def _run_constructors_as_app(
    database: DisposablePostgres,
    artifact: Mapping[str, Any],
    specs: Sequence[Sequence[str]],
) -> tuple[dict[str, Any], ...]:
    from tests.slice100_task7 import (
        test_full_strict_v9_native_schema_red as native,
    )

    tenant_id = str(native._vep_payload()["tenant_id"])
    with database.app_engine.connect() as app:
        transaction = app.begin()
        try:
            _set_tenant_context(app, tenant_id)
            result = tuple(
                _V11._ENGINE._run_constructor_roundtrips(
                    app,
                    artifact,
                    specs,
                )
            )
        finally:
            transaction.rollback()
    return cast(tuple[dict[str, Any], ...], result)


def _rls_context_proof(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> dict[str, Any]:
    graph = _V11._seed_run(connection, columns, status="RUNNING")
    tenant_id = str(graph["tenant_id"])
    cross_tenant = str(uuid.uuid4())
    connection.exec_driver_sql(f"SET LOCAL ROLE {GUARD_OWNER_ROLE}")
    try:
        _set_tenant_context(connection, tenant_id)
        same = int(
            connection.execute(
                text("SELECT count(run_id) FROM public.runs WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": graph["run_id"]},
            ).scalar_one()
        )
        _set_tenant_context(connection, cross_tenant)
        cross = int(
            connection.execute(
                text("SELECT count(run_id) FROM public.runs WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": graph["run_id"]},
            ).scalar_one()
        )
        connection.execute(text("SELECT pg_catalog.set_config('idis.tenant_id','',true)"))
        unset = int(
            connection.execute(
                text("SELECT count(run_id) FROM public.runs WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": graph["run_id"]},
            ).scalar_one()
        )
    finally:
        connection.exec_driver_sql("RESET ROLE")
    if (same, cross, unset) != (1, 0, 0):
        raise CensusError("TASK7_V16_GUARD_OWNER_RLS_CONTEXT_INVALID")
    return {
        "proof_scope": "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS",
        "executed_as_role": GUARD_OWNER_ROLE,
        "same_tenant_guard_acceptance_count": same,
        "cross_tenant_guard_denial_count": 1 if cross == 0 else 0,
        "unset_tenant_guard_denial_count": 1 if unset == 0 else 0,
    }


def _external_erasure_helper_authority_row(
    artifact: Mapping[str, Any],
) -> Mapping[str, Any]:
    rows = artifact["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    matches = [
        row for row in rows if row.get("function_signature") == _EXTERNAL_ERASURE_HELPER_SIGNATURE
    ]
    if len(matches) != 1:
        raise CensusError("TASK7_V16_EXTERNAL_ERASURE_HELPER_AUTHORITY_INVALID")
    return cast(Mapping[str, Any], matches[0])


def _external_erasure_helper_contract(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the external helper expectation solely from reviewed v16 bytes."""

    row = _external_erasure_helper_authority_row(artifact)
    owner = _identifier(str(row["owner_role"]))
    explicit_grantees = tuple(
        _identifier(str(value)) for value in row["explicit_execute_grantee_tuple"]
    )
    contract = {
        "function_signature": _EXTERNAL_ERASURE_HELPER_SIGNATURE,
        "function_owner": owner,
        "security_definer": row["security_mode"] == "DEFINER",
        "search_path_tuple": tuple(map(str, row["search_path_tuple"])),
        "execute_grantee_tuple": (owner, *explicit_grantees),
        "application_role_execute": bool(row["application_role_execute"]),
        "public_execute": bool(row["public_execute"]),
    }
    if (
        row["function_kind"] != "HELPER"
        or row["definition_origin"] != "EXTERNAL"
        or row["install_scope"] != "EXTERNAL_DEPENDENCY"
        or contract["security_definer"] is not True
        or contract["search_path_tuple"] != ("pg_catalog", "pg_temp")
        or contract["execute_grantee_tuple"] != ("idis_erasure_function_owner", GUARD_OWNER_ROLE)
        or contract["application_role_execute"] is not False
        or contract["public_execute"] is not False
    ):
        raise CensusError("TASK7_V16_EXTERNAL_ERASURE_HELPER_AUTHORITY_INVALID")
    return contract


def _install_external_erasure_helper_acl(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Install only the exact hash-bound external helper owner and ACL bytes."""

    row = _external_erasure_helper_authority_row(artifact)
    encoded = (row["owner_sql_utf8_hex"], *row["acl_sql_utf8_hex_tuple"])
    try:
        statements = tuple(bytes.fromhex(str(value)).decode("utf-8") for value in encoded)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CensusError("TASK7_V16_EXTERNAL_ERASURE_HELPER_AUTHORITY_INVALID") from exc
    contract = _external_erasure_helper_contract(artifact)
    signature = contract["function_signature"]
    owner = contract["function_owner"]
    guard_grantee = contract["execute_grantee_tuple"][1]
    if statements != (
        f"ALTER FUNCTION {signature} OWNER TO {owner}",
        f"ALTER FUNCTION {signature} SECURITY DEFINER",
        f"ALTER FUNCTION {signature} SET search_path TO pg_catalog, pg_temp",
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION {signature} TO {guard_grantee}",
    ):
        raise CensusError("TASK7_V16_EXTERNAL_ERASURE_HELPER_AUTHORITY_INVALID")
    for statement in statements:
        connection.exec_driver_sql(statement)


def _application_direct_erasure_helper_denial(
    application_engine: Engine,
    *,
    application_role: str,
    signature: str,
) -> bool:
    """Prove denial through the generated app credential, never SET ROLE."""

    expected_role = _identifier(application_role)
    with application_engine.connect() as application:
        actor = application.execute(
            text(
                "SELECT session_user,current_user,r.rolsuper,r.rolbypassrls "
                "FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"
            )
        ).one()
        if tuple(actor) != (expected_role, expected_role, False, False):
            raise CensusError("TASK7_V16_EXTERNAL_ERASURE_HELPER_APPLICATION_ACTOR_INVALID")
        try:
            application.execute(
                text(
                    "SELECT public.idis_task7_approved_erasure_context_v1("
                    "CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid))"
                ),
                {
                    "tenant_id": str(uuid.uuid4()),
                    "deal_id": str(uuid.uuid4()),
                },
            ).scalar_one()
        except DBAPIError as exc:
            if _sqlstate(exc) != "42501":
                raise CensusError("TASK7_V16_EXTERNAL_ERASURE_HELPER_EXECUTION_INVALID") from exc
            return True
    return False


def _validate_external_erasure_helper_proof(
    proof: Mapping[str, Any],
    *,
    artifact: Mapping[str, Any],
    error_code: str,
) -> None:
    """Validate the exact external-helper proof and every nested ACL row."""

    try:
        contract = _external_erasure_helper_contract(artifact)
        expected_fields = {
            "function_signature",
            "guard_owner_execute",
            "application_direct_execute",
            "public_direct_execute",
            "application_direct_call_denied",
            "ordinary_guard_call_result",
            "function_owner",
            "security_definer",
            "search_path_tuple",
            "raw_execute_acl_tuple",
        }
        raw_acl = proof["raw_execute_acl_tuple"]
        search_path = proof["search_path_tuple"]
        if (
            set(proof) != expected_fields
            or not isinstance(raw_acl, Sequence)
            or isinstance(raw_acl, (str, bytes, bytearray))
            or not isinstance(search_path, Sequence)
            or isinstance(search_path, (str, bytes, bytearray))
            or tuple(search_path) != contract["search_path_tuple"]
        ):
            raise CensusError(error_code)
        acl_fields = {
            "grantee",
            "grantor",
            "privilege_type",
            "is_grantable",
        }
        if any(not isinstance(row, Mapping) or set(row) != acl_fields for row in raw_acl):
            raise CensusError(error_code)
        expected_acl = {
            (
                grantee,
                contract["function_owner"],
                "EXECUTE",
                False,
            )
            for grantee in contract["execute_grantee_tuple"]
        }
        actual_without_acl = {
            key: proof[key]
            for key in proof
            if key not in {"raw_execute_acl_tuple", "search_path_tuple"}
        }
        if (
            actual_without_acl
            != {
                "function_signature": contract["function_signature"],
                "guard_owner_execute": True,
                "application_direct_execute": contract["application_role_execute"],
                "public_direct_execute": contract["public_execute"],
                "application_direct_call_denied": True,
                "ordinary_guard_call_result": False,
                "function_owner": contract["function_owner"],
                "security_definer": contract["security_definer"],
            }
            or len(raw_acl) != len(expected_acl)
            or {
                (
                    row["grantee"],
                    row["grantor"],
                    row["privilege_type"],
                    row["is_grantable"],
                )
                for row in raw_acl
            }
            != expected_acl
        ):
            raise CensusError(error_code)
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError(error_code) from exc


def _external_erasure_helper_proof(
    connection: Connection,
    *,
    artifact: Mapping[str, Any],
    application_engine: Engine,
    application_role: str,
) -> dict[str, Any]:
    contract = _external_erasure_helper_contract(artifact)
    signature = str(contract["function_signature"])
    guard_execute = bool(
        connection.execute(
            text(
                "SELECT pg_catalog.has_function_privilege("
                ":role_name,pg_catalog.to_regprocedure(:signature),'EXECUTE')"
            ),
            {"role_name": GUARD_OWNER_ROLE, "signature": signature},
        ).scalar_one()
    )
    app_execute = bool(
        connection.execute(
            text(
                "SELECT pg_catalog.has_function_privilege("
                ":role_name,pg_catalog.to_regprocedure(:signature),'EXECUTE')"
            ),
            {"role_name": application_role, "signature": signature},
        ).scalar_one()
    )
    public_execute = bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_proc p
                  CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                  ) acl
                  WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                    AND acl.grantee=0 AND acl.privilege_type='EXECUTE'
                )
                """
            ),
            {"signature": signature},
        ).scalar_one()
    )
    raw_execute_acl = tuple(
        {
            "grantee": str(row.grantee),
            "grantor": str(row.grantor),
            "privilege_type": str(row.privilege_type),
            "is_grantable": bool(row.is_grantable),
        }
        for row in connection.execute(
            text(
                """
                SELECT grantee.rolname AS grantee,
                       grantor.rolname AS grantor,
                       acl.privilege_type,acl.is_grantable
                FROM pg_catalog.pg_proc p
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                ) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles grantor ON grantor.oid=acl.grantor
                WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                ORDER BY grantee.rolname,acl.privilege_type,grantor.rolname
                """
            ),
            {"signature": signature},
        )
    )
    catalog_row = connection.execute(
        text(
            "SELECT owner.rolname AS function_owner,p.prosecdef,p.proconfig "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner "
            "WHERE p.oid=pg_catalog.to_regprocedure(:signature)"
        ),
        {"signature": signature},
    ).one()
    function_owner = str(catalog_row.function_owner)
    search_path_tuple = _normalize_locked_search_path(
        catalog_row.proconfig,
        expected=cast(Sequence[str], contract["search_path_tuple"]),
        error_code="TASK7_V16_EXTERNAL_ERASURE_HELPER_ACL_INVALID",
    )

    application_direct_call_denied = _application_direct_erasure_helper_denial(
        application_engine,
        application_role=application_role,
        signature=signature,
    )

    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    connection.exec_driver_sql(f"SET LOCAL ROLE {GUARD_OWNER_ROLE}")
    try:
        _set_tenant_context(connection, tenant_id)
        ordinary = bool(
            connection.execute(
                text(
                    "SELECT public.idis_task7_approved_erasure_context_v1("
                    "CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid))"
                ),
                {"tenant_id": tenant_id, "deal_id": deal_id},
            ).scalar_one()
        )
    finally:
        connection.exec_driver_sql("RESET ROLE")
    proof = {
        "function_signature": signature,
        "guard_owner_execute": guard_execute,
        "application_direct_execute": app_execute,
        "public_direct_execute": public_execute,
        "application_direct_call_denied": application_direct_call_denied,
        "ordinary_guard_call_result": ordinary,
        "function_owner": function_owner,
        "security_definer": bool(catalog_row.prosecdef),
        "search_path_tuple": search_path_tuple,
        "raw_execute_acl_tuple": list(raw_execute_acl),
    }
    _validate_external_erasure_helper_proof(
        proof,
        artifact=artifact,
        error_code="TASK7_V16_EXTERNAL_ERASURE_HELPER_ACL_INVALID",
    )
    return proof


def _grant_external_erasure_helper_read_closure(connection: Connection) -> None:
    """Install only the existing erasure helper's reviewed 0037 dependency."""

    connection.exec_driver_sql(
        "REVOKE ALL PRIVILEGES ON TABLE public.erasure_requests FROM idis_erasure_function_owner"
    )
    connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner")
    connection.exec_driver_sql(
        "GRANT SELECT (request_id,tenant_id,deal_id,status) "
        "ON TABLE public.erasure_requests TO idis_erasure_function_owner"
    )


def case_scaffold_relation_names(
    artifact: Mapping[str, Any],
    ordered_targets: Sequence[Sequence[str]],
) -> tuple[str, ...]:
    """Return the complete relation closure whose unrelated constraints are isolated."""

    target_relations: list[str] = []
    for target in ordered_targets:
        if len(target) != 3:
            raise CensusError("TASK7_V16_CENSUS_TARGET_SHAPE_INVALID")
        target_relations.append(_identifier(str(target[1])))
    return tuple(
        dict.fromkeys(
            [
                *(
                    _identifier(str(row["table_name"]))
                    for row in artifact["current_ddl_registry"]["rows"]
                ),
                *target_relations,
                "run_methodology_selections",
                "methodology_questions",
            ]
        )
    )


def _run_pre_fixture_census_impl(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Run 73×3 app-role guard cases plus four app-role constructors."""

    _ensure_admin_url()
    artifact_raw, artifact = _decode_artifact(Path(artifact_path))
    targets, trigger_contract = _validate_request(
        artifact=artifact,
        base_schema_revision=base_schema_revision,
        harness_identity=harness_identity,
        ordered_authority_target_tuple=ordered_authority_target_tuple,
        ordered_trigger_contract_coordinate_tuple=(ordered_trigger_contract_coordinate_tuple),
        production_constructor_spec_tuple=production_constructor_spec_tuple,
    )
    prospective_raw = _derive_prospective_frozen_bytes(artifact_raw)
    outcomes: list[dict[str, Any]] = []
    aggregate_metrics: dict[str, int] = {}
    admin_fixture_helper_projections: list[dict[str, Any]] = []

    with _cluster_safety_boundary(), disposable_postgres() as database:
        migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        grant_application_access(database)
        constructors = _run_constructors_as_app(
            database,
            artifact,
            production_constructor_spec_tuple,
        )
        with database.admin_engine.begin() as admin:
            provision_exact_erasure_principals(admin)
            revision_before = str(
                admin.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
            server_version_num = int(
                admin.execute(
                    text("SELECT current_setting('server_version_num')::integer")
                ).scalar_one()
            )
            install_artifact_sql_scaffold(
                admin,
                artifact,
                application_role=database.role_name,
            )
            _install_external_erasure_helper_acl(admin, artifact)
            _grant_external_erasure_helper_read_closure(admin)
            revision_after_install = str(
                admin.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
            projection = catalog_projection(admin, artifact)
            trigger_projection = _live_trigger_projection(
                admin,
                trigger_contract,
                targets,
            )
            routine_catalog = _current_routine_catalog(
                admin,
                artifact,
                application_role=database.role_name,
            )
            attribute_acl = _guard_owner_raw_attribute_acl(admin, artifact)
            schema_acl = _guard_owner_raw_schema_acl(admin)
            guard_posture, force_rls_count = _guard_owner_posture(
                admin,
                artifact,
            )
            disable_all_current_user_triggers(admin, artifact)
            relation_names = case_scaffold_relation_names(
                artifact,
                targets,
            )
            _V11._ENGINE._drop_non_guard_constraints(
                admin,
                relation_names=relation_names,
            )
            _V11._ENGINE._disable_target_relations(admin, targets)
            columns = cast(
                dict[str, dict[str, tuple[str, bool]]],
                _V11._ENGINE._column_inventory(admin),
            )
            fixture_rejections = _fixture_rejection_proofs(columns)

        with database.admin_engine.begin() as admin:
            external_helper = _external_erasure_helper_proof(
                admin,
                artifact=artifact,
                application_engine=database.app_engine,
                application_role=database.role_name,
            )

        event_map: dict[
            tuple[str, str, str],
            tuple[TriggerEvent, ...],
        ] = {}
        for authority, table_name, guard_name, _timing, event, *_tail in trigger_contract:
            key = (str(authority), str(table_name), str(guard_name))
            event_map.setdefault(key, ())
            event_map[key] = (
                *event_map[key],
                cast(TriggerEvent, str(event)),
            )

        for case_kind_raw in _CASE_KINDS:
            case_kind = cast(CaseKind, case_kind_raw)
            for authority_id, table_name, guard_name in targets:
                trigger_event = _event_for_case(
                    case_kind=case_kind,
                    authority_id=authority_id,
                    table_name=table_name,
                    events=event_map[(authority_id, table_name, guard_name)],
                )
                outcome, metrics = _run_one_case_as_app(
                    database=database,
                    artifact=artifact,
                    columns=columns,
                    authority_id=authority_id,
                    table_name=table_name,
                    guard_name=guard_name,
                    case_kind=case_kind,
                    trigger_event=trigger_event,
                )
                outcomes.append(outcome)
                for key, value in metrics.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        aggregate_metrics[key] = aggregate_metrics.get(key, 0) + value
                    elif key == "admin_fixture_helper_projection_tuple":
                        admin_fixture_helper_projections.extend(
                            cast(Sequence[dict[str, Any]], value)
                        )

        with database.admin_engine.begin() as admin:
            _truncate_case_data(admin, artifact)
            rls_context = _rls_context_proof(admin, columns)
            revision_after_cases = str(
                admin.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )

    legal_count = sum(
        row["case_kind"] == "LEGAL" and row["outcome"] == "ACCEPTED" for row in outcomes
    )
    illegal_count = sum(
        row["case_kind"] == "ILLEGAL" and row["outcome"] == "REJECTED" for row in outcomes
    )
    direct_count = sum(
        row["case_kind"] == "DIRECT_SQL" and row["outcome"] == "REJECTED" for row in outcomes
    )
    unexpected = len(outcomes) - legal_count - illegal_count - direct_count
    observed_roles = {
        (
            str(row["observed_session_user"]),
            str(row["executed_as_role"]),
            bool(row["observed_is_superuser"]),
            bool(row["observed_bypass_rls"]),
        )
        for row in outcomes
    }
    if observed_roles != {(database.role_name, database.role_name, False, False)}:
        raise CensusError("TASK7_V16_CASE_APPLICATION_ROLE_PROJECTION_INVALID")
    application_case_count = sum(
        row["observed_session_user"] == database.role_name
        and row["executed_as_role"] == database.role_name
        for row in outcomes
    )
    admin_case_count = len(outcomes) - application_case_count
    if (application_case_count, admin_case_count) != (219, 0):
        raise CensusError("TASK7_V16_CASE_ACTOR_COUNT_INVALID")
    actor = {
        "role_name": database.role_name,
        "session_user": database.role_name,
        "is_superuser": False,
        "bypass_rls": False,
        "case_execution_count": application_case_count,
    }
    observed_event_keys = {
        (
            str(row["authority_id"]),
            str(row["table_name"]),
            str(row["guard_name"]),
            str(row["trigger_event"]),
        )
        for row in outcomes
    }
    observed_trigger_contract = tuple(
        row
        for row in trigger_contract
        if (str(row[0]), str(row[1]), str(row[2]), str(row[4])) in observed_event_keys
    )
    if len(observed_trigger_contract) != 192 or set(observed_trigger_contract) != set(
        trigger_contract
    ):
        raise CensusError("TASK7_V16_CENSUS_TRIGGER_EVENT_COVERAGE_INCOMPLETE")
    case_target_projection = tuple(
        dict.fromkeys(
            (
                str(row["authority_id"]),
                str(row["table_name"]),
                str(row["guard_name"]),
            )
            for row in outcomes
        )
    )
    live_target_projection = tuple(tuple(map(str, row[:3])) for row in trigger_projection)
    artifact_target_projection = _artifact_targets(artifact)
    manifest_parity_projection = {
        "artifact_authority_target_tuple": [list(row) for row in artifact_target_projection],
        "requested_authority_target_tuple": [list(row) for row in targets],
        "live_catalog_authority_target_tuple": [list(row) for row in live_target_projection],
        "measured_case_authority_target_tuple": [list(row) for row in case_target_projection],
    }
    if not (
        artifact_target_projection == targets == live_target_projection == case_target_projection
    ):
        raise CensusError("TASK7_V16_CENSUS_MANIFEST_PARITY_INVALID")
    if (revision_before, revision_after_install, revision_after_cases) != (
        BASE_SCHEMA_REVISION,
    ) * 3 or (legal_count, illegal_count, direct_count, unexpected) != (73, 73, 73, 0):
        raise CensusError("TASK7_V16_CENSUS_EXECUTION_INCOMPLETE")

    catalog_projection_json = _json_safe(projection)
    trigger_projection_json = _json_safe(trigger_projection)
    outcomes_json = _json_safe(outcomes)
    constructors_json = _json_safe(constructors)
    routine_catalog_json = _json_safe(routine_catalog)
    attribute_acl_json = _json_safe(attribute_acl)
    schema_acl_json = _json_safe(schema_acl)
    external_helper_json = _json_safe(external_helper)
    rls_context_json = _json_safe(rls_context)
    helper_projection_json = _json_safe(admin_fixture_helper_projections)
    trigger_event_coverage_json = [
        list(row)
        for row in sorted(
            {
                (
                    row["authority_id"],
                    row["table_name"],
                    row["guard_name"],
                    row["trigger_event"],
                )
                for row in outcomes
            }
        )
    ]

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "harness_identity": HARNESS_IDENTITY,
        "base_schema_revision": BASE_SCHEMA_REVISION,
        "alembic_version_before_install": revision_before,
        "alembic_version_after_install": revision_after_install,
        "alembic_version_after_cases": revision_after_cases,
        "installation_mode": INSTALLATION_MODE,
        "database_product": "PostgreSQL",
        "server_version_num": server_version_num,
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v16",
        "candidate_append_frozen": False,
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(prospective_raw).hexdigest(),
        "ordered_authority_target_tuple": [list(row) for row in targets],
        "trigger_contract_coordinate_tuple": [list(row) for row in trigger_contract],
        "observed_trigger_contract_coordinate_tuple": [
            list(row) for row in observed_trigger_contract
        ],
        "catalog_projection_tuple": catalog_projection_json,
        "pg_trigger_projection_tuple": trigger_projection_json,
        "case_outcome_tuple": outcomes_json,
        "constructor_proof_tuple": constructors_json,
        "fixture_rejection_proof_tuple": [list(row) for row in fixture_rejections],
        "application_role_name": actor["role_name"],
        "database_actor": actor,
        "guard_owner_posture": guard_posture,
        "current_routine_catalog_tuple": routine_catalog_json,
        "guard_owner_raw_attribute_acl_tuple": attribute_acl_json,
        "guard_owner_raw_schema_acl_tuple": schema_acl_json,
        "external_erasure_helper_proof": external_helper_json,
        "admin_fixture_helper_projection_tuple": helper_projection_json,
        "security_definer_guard_owner_rls_context_proof": rls_context_json,
        "manifest_parity_projection": manifest_parity_projection,
        "current_guard_target_count": len(targets),
        "current_guard_target_event_count": len(trigger_contract),
        "case_outcome_count": len(outcomes),
        "legal_execution_count": legal_count,
        "illegal_rejection_count": illegal_count,
        "direct_sql_rejection_count": direct_count,
        "application_case_execution_count": application_case_count,
        "admin_case_execution_count": admin_case_count,
        "admin_fixture_helper_call_count": len(admin_fixture_helper_projections),
        "constructor_schema_proof_count": len(constructors),
        "unexpected_outcome_count": unexpected,
        "current_routine_catalog_count": len(routine_catalog),
        "guard_owner_column_acl_relation_count": len(
            {str(row["relation"]) for row in attribute_acl}
        ),
        "guard_owner_column_acl_count": len(attribute_acl),
        "application_direct_helper_execute_count": sum(
            bool(row["application_direct_execute"]) for row in routine_catalog
        )
        + int(external_helper["application_direct_execute"]),
        "public_direct_helper_execute_count": sum(
            bool(row["public_direct_execute"]) for row in routine_catalog
        )
        + int(external_helper["public_direct_execute"]),
        "force_rls_parent_relation_count": force_rls_count,
        "task6_output_root_update_acceptance_count": aggregate_metrics.get(
            "task6_output_root_update_acceptance_count",
            0,
        ),
        "strict_input_binding_queued_admission_count": aggregate_metrics.get(
            "strict_input_binding_queued_admission_count",
            0,
        ),
        "expected_edge_queued_admission_count": aggregate_metrics.get(
            "expected_edge_queued_admission_count",
            0,
        ),
        "package_open_to_sealed_binding_count": aggregate_metrics.get(
            "package_open_to_sealed_binding_count",
            0,
        ),
        "methodology_commercial_question_count": aggregate_metrics.get(
            "methodology_commercial_question_count",
            0,
        ),
        "methodology_financial_question_count": aggregate_metrics.get(
            "methodology_financial_question_count",
            0,
        ),
        "methodology_coverage_count": aggregate_metrics.get(
            "methodology_coverage_count",
            0,
        ),
        "catalog_projection_sha256": _digest(
            ["task7-v16-census-catalog-projection-v1", catalog_projection_json]
        ),
        "case_result_sha256": _digest(["task7-v16-current-guard-case-results-v1", outcomes_json]),
        "constructor_roundtrip_sha256": _digest(
            ["task7-v16-constructor-roundtrips-v1", constructors_json]
        ),
        "admin_fixture_helper_projection_sha256": _digest(
            [
                "task7-v16-admin-fixture-helper-projection-v1",
                helper_projection_json,
            ]
        ),
        "manifest_parity_sha256": _digest(
            [
                "task7-v16-manifest-parity-v1",
                manifest_parity_projection,
            ]
        ),
        "pg_trigger_inventory_sha256": _digest(
            ["task7-v16-pg-trigger-projection-v1", trigger_projection_json]
        ),
        "trigger_event_coverage_sha256": _digest(
            [
                "task7-v16-trigger-event-coverage-v1",
                trigger_event_coverage_json,
            ]
        ),
        "guard_acl_projection_sha256": _digest(
            [
                "task7-v16-guard-acl-projection-v1",
                routine_catalog_json,
                attribute_acl_json,
                schema_acl_json,
                guard_posture,
                external_helper_json,
            ]
        ),
        "rls_context_proof_sha256": _digest(
            [
                "task7-v16-security-definer-guard-owner-rls-context-proof-v1",
                rls_context_json,
            ]
        ),
    }
    result["complete_execution_sha256"] = _digest(["task7-v16-complete-execution-v1", result])
    _validate_serialized_provenance(
        result,
        error_code="TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID",
    )
    return result


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Execute once under the held-byte harness and prove exact source closure."""

    with _HARNESS_SOURCE_STORE.activation():
        result = _run_pre_fixture_census_impl(
            artifact_path=artifact_path,
            base_schema_revision=base_schema_revision,
            harness_identity=harness_identity,
            ordered_authority_target_tuple=ordered_authority_target_tuple,
            ordered_trigger_contract_coordinate_tuple=(ordered_trigger_contract_coordinate_tuple),
            production_constructor_spec_tuple=production_constructor_spec_tuple,
        )
        _HARNESS_SOURCE_STORE.assert_exact_closure()
        return result


def _database_execution(result: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "application_case_execution_count",
        "admin_case_execution_count",
        "admin_fixture_helper_call_count",
        "current_guard_target_count",
        "current_guard_target_event_count",
        "case_outcome_count",
        "legal_execution_count",
        "illegal_rejection_count",
        "direct_sql_rejection_count",
        "constructor_schema_proof_count",
        "current_routine_catalog_count",
        "guard_owner_column_acl_relation_count",
        "guard_owner_column_acl_count",
        "application_direct_helper_execute_count",
        "public_direct_helper_execute_count",
        "unexpected_outcome_count",
        "catalog_projection_sha256",
        "case_result_sha256",
        "constructor_roundtrip_sha256",
        "admin_fixture_helper_projection_sha256",
        "manifest_parity_sha256",
        "pg_trigger_inventory_sha256",
        "trigger_event_coverage_sha256",
        "guard_acl_projection_sha256",
        "rls_context_proof_sha256",
        "complete_execution_sha256",
    )
    return {field: result[field] for field in fields}


def _validate_database_execution(execution: Mapping[str, Any]) -> None:
    required = {
        "application_case_execution_count": 219,
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 192,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "constructor_schema_proof_count": 4,
        "current_routine_catalog_count": 19,
        "guard_owner_column_acl_relation_count": 36,
        "guard_owner_column_acl_count": 366,
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "unexpected_outcome_count": 0,
    }
    if any(execution.get(key) != value for key, value in required.items()):
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
    for key in execution:
        if key.endswith("_sha256") and (
            not isinstance(execution[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", execution[key]) is None
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")


def _validate_complete_execution_result(result: Mapping[str, Any]) -> None:
    supplied = result.get("complete_execution_sha256")
    if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None:
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
    expected = _digest(
        [
            "task7-v16-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    if supplied != expected:
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")


_CLOSED_RESULT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "harness_identity",
        "base_schema_revision",
        "alembic_version_before_install",
        "alembic_version_after_install",
        "alembic_version_after_cases",
        "installation_mode",
        "database_product",
        "server_version_num",
        "artifact_schema",
        "pipeline_contract_version",
        "candidate_append_frozen",
        "candidate_artifact_sha256",
        "prospective_frozen_artifact_sha256",
        "ordered_authority_target_tuple",
        "trigger_contract_coordinate_tuple",
        "observed_trigger_contract_coordinate_tuple",
        "catalog_projection_tuple",
        "pg_trigger_projection_tuple",
        "case_outcome_tuple",
        "constructor_proof_tuple",
        "fixture_rejection_proof_tuple",
        "application_role_name",
        "database_actor",
        "guard_owner_posture",
        "current_routine_catalog_tuple",
        "guard_owner_raw_attribute_acl_tuple",
        "guard_owner_raw_schema_acl_tuple",
        "external_erasure_helper_proof",
        "admin_fixture_helper_projection_tuple",
        "security_definer_guard_owner_rls_context_proof",
        "manifest_parity_projection",
        "current_guard_target_count",
        "current_guard_target_event_count",
        "case_outcome_count",
        "legal_execution_count",
        "illegal_rejection_count",
        "direct_sql_rejection_count",
        "application_case_execution_count",
        "admin_case_execution_count",
        "admin_fixture_helper_call_count",
        "constructor_schema_proof_count",
        "unexpected_outcome_count",
        "current_routine_catalog_count",
        "guard_owner_column_acl_relation_count",
        "guard_owner_column_acl_count",
        "application_direct_helper_execute_count",
        "public_direct_helper_execute_count",
        "force_rls_parent_relation_count",
        "task6_output_root_update_acceptance_count",
        "strict_input_binding_queued_admission_count",
        "expected_edge_queued_admission_count",
        "package_open_to_sealed_binding_count",
        "methodology_commercial_question_count",
        "methodology_financial_question_count",
        "methodology_coverage_count",
        "catalog_projection_sha256",
        "case_result_sha256",
        "constructor_roundtrip_sha256",
        "admin_fixture_helper_projection_sha256",
        "manifest_parity_sha256",
        "pg_trigger_inventory_sha256",
        "trigger_event_coverage_sha256",
        "guard_acl_projection_sha256",
        "rls_context_proof_sha256",
        "complete_execution_sha256",
    }
)


def _validate_complete_census_result(
    result: Mapping[str, Any],
    *,
    artifact_raw: bytes,
) -> None:
    """Re-derive the closed evidence graph before any signature is accepted."""

    try:
        _validate_serialized_provenance(
            result,
            error_code="TASK7_V16_CENSUS_RECEIPT_INVALID",
        )
        if frozenset(result) != _CLOSED_RESULT_FIELDS:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if (
            result["schema_version"] != RESULT_SCHEMA
            or result["harness_identity"] != HARNESS_IDENTITY
            or result["base_schema_revision"] != BASE_SCHEMA_REVISION
            or result["alembic_version_before_install"] != BASE_SCHEMA_REVISION
            or result["alembic_version_after_install"] != BASE_SCHEMA_REVISION
            or result["alembic_version_after_cases"] != BASE_SCHEMA_REVISION
            or result["installation_mode"] != INSTALLATION_MODE
            or result["database_product"] != "PostgreSQL"
            or not isinstance(result["server_version_num"], int)
            or int(result["server_version_num"]) < 160000
            or result["artifact_schema"] != "full-strict-v16-reviewed-preimage-v1"
            or result["pipeline_contract_version"] != "full-strict-v16"
            or result["candidate_append_frozen"] is not False
            or result["candidate_artifact_sha256"] != hashlib.sha256(artifact_raw).hexdigest()
            or hashlib.sha512(artifact_raw).hexdigest() != _EXPECTED_ARTIFACT_SHA512
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        artifact = _decode_exact_candidate(artifact_raw)
        expected_trigger_contract = _artifact_trigger_contract(artifact)
        expected_targets = _artifact_targets(artifact)
        prospective = _derive_prospective_frozen_bytes(artifact_raw)
        if result["prospective_frozen_artifact_sha256"] != hashlib.sha256(prospective).hexdigest():
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        outcomes = cast(Sequence[Mapping[str, Any]], result["case_outcome_tuple"])
        constructors = cast(Sequence[Mapping[str, Any]], result["constructor_proof_tuple"])
        helper_projection = cast(
            Sequence[Mapping[str, Any]],
            result["admin_fixture_helper_projection_tuple"],
        )
        routine_catalog = cast(
            Sequence[Mapping[str, Any]],
            result["current_routine_catalog_tuple"],
        )
        attribute_acl = cast(
            Sequence[Mapping[str, Any]],
            result["guard_owner_raw_attribute_acl_tuple"],
        )
        schema_acl = cast(
            Sequence[Mapping[str, Any]],
            result["guard_owner_raw_schema_acl_tuple"],
        )
        trigger_contract = tuple(
            tuple(row)
            for row in cast(Sequence[Sequence[Any]], result["trigger_contract_coordinate_tuple"])
        )
        observed_contract = tuple(
            tuple(row)
            for row in cast(
                Sequence[Sequence[Any]],
                result["observed_trigger_contract_coordinate_tuple"],
            )
        )
        targets = tuple(
            tuple(map(str, row))
            for row in cast(
                Sequence[Sequence[Any]],
                result["ordered_authority_target_tuple"],
            )
        )
        live_trigger_projection = tuple(
            tuple(row)
            for row in cast(
                Sequence[Sequence[Any]],
                result["pg_trigger_projection_tuple"],
            )
        )
        expected_live_trigger_projection = _expected_trigger_projection(
            expected_trigger_contract,
            expected_targets,
        )
        if (
            len(outcomes) != 219
            or len(constructors) != 4
            or len(helper_projection) != 36
            or len(routine_catalog) != 19
            or len(attribute_acl) != 366
            or len(schema_acl) != 1
            or len(trigger_contract) != 192
            or len(set(trigger_contract)) != 192
            or set(observed_contract) != set(trigger_contract)
            or len(observed_contract) != 192
            or len(targets) != 73
            or trigger_contract != expected_trigger_contract
            or targets != expected_targets
            or live_trigger_projection != expected_live_trigger_projection
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        role_name = str(result["application_role_name"])
        event_map: dict[tuple[str, str, str], tuple[TriggerEvent, ...]] = {}
        for authority, table_name, guard_name, _timing, event, *_tail in expected_trigger_contract:
            key = (str(authority), str(table_name), str(guard_name))
            event_map.setdefault(key, ())
            event_map[key] = (*event_map[key], cast(TriggerEvent, str(event)))
        expected_case_coordinates = tuple(
            (
                case_kind,
                authority_id,
                table_name,
                guard_name,
                _event_for_case(
                    case_kind=case_kind,
                    authority_id=authority_id,
                    table_name=table_name,
                    events=event_map[(authority_id, table_name, guard_name)],
                ),
            )
            for case_kind in cast(tuple[CaseKind, ...], _CASE_KINDS)
            for authority_id, table_name, guard_name in expected_targets
        )
        actual_case_coordinates = tuple(
            (
                str(row["case_kind"]),
                str(row["authority_id"]),
                str(row["table_name"]),
                str(row["guard_name"]),
                str(row["trigger_event"]),
            )
            for row in outcomes
        )
        if actual_case_coordinates != expected_case_coordinates:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        expected_outcome_fields = {
            "case_kind",
            "authority_id",
            "table_name",
            "guard_name",
            "trigger_event",
            "trigger_timing",
            "constraint_flush_attempted",
            "scenario_id",
            "execution_path",
            "executed_as_role",
            "observed_session_user",
            "observed_is_superuser",
            "observed_bypass_rls",
            "outcome",
            "sqlstate",
            "authority_rejection_marker",
            "rejection_provenance",
        }
        for row in outcomes:
            if set(row) != expected_outcome_fields:
                raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
            case_kind = cast(CaseKind, str(row["case_kind"]))
            authority_id = str(row["authority_id"])
            table_name = str(row["table_name"])
            guard_name = str(row["guard_name"])
            trigger_event = str(row["trigger_event"])
            coordinate = tuple(
                value
                for value in expected_trigger_contract
                if (
                    str(value[0]),
                    str(value[1]),
                    str(value[2]),
                    str(value[4]),
                )
                == (authority_id, table_name, guard_name, trigger_event)
            )
            if len(coordinate) != 1:
                raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
            timing = str(coordinate[0][3])
            if (
                row["trigger_timing"] != timing
                or row["constraint_flush_attempted"] is not (timing == "AFTER")
                or row["scenario_id"]
                != _scenario_id(
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                )
                or row["execution_path"]
                != ("DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION")
            ):
                raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
            if case_kind == "LEGAL":
                if (
                    row["outcome"] != "ACCEPTED"
                    or row["sqlstate"] is not None
                    or row["authority_rejection_marker"] is not None
                    or row["rejection_provenance"] is not None
                ):
                    raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
            else:
                expected_sqlstate, expected_marker = _expected_rejection(
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                )
                if (
                    row["outcome"] != "REJECTED"
                    or row["sqlstate"] != expected_sqlstate
                    or row["sqlstate"] == "42501"
                    or row["authority_rejection_marker"] != expected_marker
                    or row["rejection_provenance"] != "HASH_BOUND_AUTHORITY"
                ):
                    raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if any(
            row.get("executed_as_role") != role_name
            or row.get("observed_session_user") != role_name
            or row.get("observed_is_superuser") is not False
            or row.get("observed_bypass_rls") is not False
            for row in outcomes
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if result["database_actor"] != {
            "role_name": role_name,
            "session_user": role_name,
            "is_superuser": False,
            "bypass_rls": False,
            "case_execution_count": len(outcomes),
        }:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        expected_functions = current_function_rows(artifact)
        if tuple(str(row["function_signature"]) for row in routine_catalog) != tuple(
            str(row["function_signature"]) for row in expected_functions
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        for actual, expected in zip(routine_catalog, expected_functions, strict=True):
            raw_acl = cast(Sequence[Mapping[str, Any]], actual["raw_execute_acl_tuple"])
            if (
                set(actual)
                != {
                    "function_signature",
                    "function_kind",
                    "owner",
                    "security_definer",
                    "search_path",
                    "application_direct_execute",
                    "public_direct_execute",
                    "raw_execute_acl_tuple",
                }
                or actual["function_kind"] != expected["function_kind"]
                or actual["owner"] != GUARD_OWNER_ROLE
                or actual["security_definer"] != (expected["security_mode"] == "DEFINER")
                or actual["search_path"] != "pg_catalog, pg_temp"
                or actual["application_direct_execute"] is not False
                or actual["public_direct_execute"] is not False
                or len(raw_acl) != 1
                or raw_acl[0]
                != {
                    "grantee": GUARD_OWNER_ROLE,
                    "grantor": GUARD_OWNER_ROLE,
                    "privilege_type": "EXECUTE",
                    "is_grantable": False,
                }
            ):
                raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        expected_column_acl = {
            (str(relation), str(column))
            for relation, columns in artifact["sql_authority_registry"][
                "guard_function_acl_registry"
            ]["guard_owner_role"]["select_relation_column_tuple"]
            for column in columns
        }
        actual_column_acl = {(str(row["relation"]), str(row["column"])) for row in attribute_acl}
        if (
            actual_column_acl != expected_column_acl
            or len(actual_column_acl) != len(attribute_acl)
            or any(
                set(row)
                != {
                    "relation",
                    "column",
                    "privilege_type",
                    "is_grantable",
                    "raw_attacl",
                }
                or row["privilege_type"] != "SELECT"
                or row["is_grantable"] is not False
                or not isinstance(row["raw_attacl"], str)
                or not row["raw_attacl"]
                for row in attribute_acl
            )
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if (
            len(schema_acl) != 1
            or {
                key: schema_acl[0][key]
                for key in ("schema_name", "grantee", "privilege_type", "is_grantable")
            }
            != {
                "schema_name": "public",
                "grantee": GUARD_OWNER_ROLE,
                "privilege_type": "USAGE",
                "is_grantable": False,
            }
            or set(schema_acl[0])
            != {
                "schema_name",
                "grantee",
                "grantor",
                "privilege_type",
                "is_grantable",
            }
            or not isinstance(schema_acl[0]["grantor"], str)
            or not schema_acl[0]["grantor"]
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        expected_posture = {
            "role_name": GUARD_OWNER_ROLE,
            "can_login": False,
            "is_superuser": False,
            "can_create_db": False,
            "can_create_role": False,
            "inherits": False,
            "bypass_rls": False,
            "membership_count": 0,
            "owned_relation_count": 0,
            "owned_routine_count": len(expected_functions),
            "schema_usage": True,
            "schema_create": False,
            "table_select_privilege_count": 0,
            "table_insert_privilege_count": 0,
            "table_update_privilege_count": 0,
            "table_delete_privilege_count": 0,
            "table_truncate_privilege_count": 0,
            "table_references_privilege_count": 0,
            "table_trigger_privilege_count": 0,
        }
        if result["guard_owner_posture"] != expected_posture:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        _validate_external_erasure_helper_proof(
            cast(Mapping[str, Any], result["external_erasure_helper_proof"]),
            artifact=artifact,
            error_code="TASK7_V16_CENSUS_RECEIPT_INVALID",
        )
        if result["security_definer_guard_owner_rls_context_proof"] != {
            "proof_scope": "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS",
            "executed_as_role": GUARD_OWNER_ROLE,
            "same_tenant_guard_acceptance_count": 1,
            "cross_tenant_guard_denial_count": 1,
            "unset_tenant_guard_denial_count": 1,
        }:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        constructor_coordinates = tuple(
            (
                proof["schema_id"],
                proof["schema_version"],
                proof["constructor_qualname"],
                proof["durable_table"],
            )
            for proof in constructors
        )
        if constructor_coordinates != _PRODUCTION_CONSTRUCTOR_SPECS or any(
            set(proof)
            != {
                "schema_id",
                "schema_version",
                "constructor_qualname",
                "durable_table",
                "outcome",
                "original_payload_sha256",
                "reloaded_payload_sha256",
            }
            or proof["outcome"] != "ROUNDTRIP_MATCH"
            or proof["original_payload_sha256"] != proof["reloaded_payload_sha256"]
            or re.fullmatch(r"[0-9a-f]{64}", str(proof["original_payload_sha256"])) is None
            for proof in constructors
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if result["fixture_rejection_proof_tuple"] != [
            [
                "UNKNOWN_FIELD",
                "runs",
                "unknown_fixture_field",
                "TASK7_V16_CENSUS_FIXTURE_UNKNOWN_FIELD:runs.unknown_fixture_field",
            ],
            [
                "OTHER_TARGET_FIELD",
                "runs",
                "step_id",
                "TASK7_V16_CENSUS_FIXTURE_OTHER_TARGET_FIELD:runs.step_id",
            ],
            [
                "GENERATED_FIELD",
                "methodology_extraction_results",
                "run_output_object_kind",
                "TASK7_V16_CENSUS_FIXTURE_GENERATED_FIELD:"
                "methodology_extraction_results.run_output_object_kind",
            ],
        ]:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        expected_helper_coordinates = tuple(
            (authority_id, table_name, guard_name, case_kind)
            for case_kind in ("LEGAL", "DIRECT_SQL")
            for authority_id, table_name, guard_name in expected_targets
            if authority_id == "current-output-exact-one-v1"
        )
        actual_helper_coordinates = tuple(
            (
                str(row["authority_id"]),
                str(row["table_name"]),
                str(row["guard_name"]),
                str(row["case_kind"]),
            )
            for row in helper_projection
        )
        if actual_helper_coordinates != expected_helper_coordinates or any(
            set(row)
            != {
                "authority_id",
                "table_name",
                "guard_name",
                "case_kind",
                "phase",
                "function_signature",
                "executed_as_role",
                "output_kind",
                "returned_pair_sha256",
            }
            or row["phase"] != "COMMITTED_FIXTURE_SETUP"
            or row["function_signature"]
            != "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
            or not isinstance(row["executed_as_role"], str)
            or not row["executed_as_role"]
            or row["executed_as_role"] == role_name
            or not isinstance(row["output_kind"], str)
            or not row["output_kind"]
            or re.fullmatch(r"[0-9a-f]{64}", str(row["returned_pair_sha256"])) is None
            for row in helper_projection
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        catalog_projection = cast(
            Sequence[Sequence[Any]],
            result["catalog_projection_tuple"],
        )
        if not catalog_projection or any(
            not isinstance(row, list) or not row or any(not isinstance(value, str) for value in row)
            for row in catalog_projection
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        catalog_trigger_targets = {
            (str(row[1]), str(row[2]))
            for row in catalog_projection
            if len(row) >= 3 and row[0] == "TRIGGER"
        }
        if catalog_trigger_targets != {
            (table_name, guard_name) for _authority_id, table_name, guard_name in expected_targets
        }:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        legal_count = sum(
            row.get("case_kind") == "LEGAL" and row.get("outcome") == "ACCEPTED" for row in outcomes
        )
        illegal_count = sum(
            row.get("case_kind") == "ILLEGAL" and row.get("outcome") == "REJECTED"
            for row in outcomes
        )
        direct_count = sum(
            row.get("case_kind") == "DIRECT_SQL" and row.get("outcome") == "REJECTED"
            for row in outcomes
        )
        application_case_count = sum(
            row.get("observed_session_user") == role_name
            and row.get("executed_as_role") == role_name
            for row in outcomes
        )
        admin_case_count = len(outcomes) - application_case_count
        derived_counts = {
            "current_guard_target_count": len(targets),
            "current_guard_target_event_count": len(trigger_contract),
            "case_outcome_count": len(outcomes),
            "legal_execution_count": legal_count,
            "illegal_rejection_count": illegal_count,
            "direct_sql_rejection_count": direct_count,
            "application_case_execution_count": application_case_count,
            "admin_case_execution_count": admin_case_count,
            "admin_fixture_helper_call_count": len(helper_projection),
            "constructor_schema_proof_count": len(constructors),
            "unexpected_outcome_count": len(outcomes) - legal_count - illegal_count - direct_count,
            "current_routine_catalog_count": len(routine_catalog),
            "guard_owner_column_acl_relation_count": len(
                {str(row["relation"]) for row in attribute_acl}
            ),
            "guard_owner_column_acl_count": len(attribute_acl),
            "application_direct_helper_execute_count": sum(
                bool(row["application_direct_execute"]) for row in routine_catalog
            )
            + int(result["external_erasure_helper_proof"]["application_direct_execute"]),
            "public_direct_helper_execute_count": sum(
                bool(row["public_direct_execute"]) for row in routine_catalog
            )
            + int(result["external_erasure_helper_proof"]["public_direct_execute"]),
            "force_rls_parent_relation_count": len(
                {relation for relation, _column in expected_column_acl}
            ),
        }
        expected_metrics = {
            "task6_output_root_update_acceptance_count": 2,
            "strict_input_binding_queued_admission_count": 1,
            "expected_edge_queued_admission_count": 1,
            "package_open_to_sealed_binding_count": 1,
            "methodology_commercial_question_count": 21,
            "methodology_financial_question_count": 79,
            "methodology_coverage_count": 100,
        }
        if any(result[key] != value for key, value in derived_counts.items()):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if any(result[key] != value for key, value in expected_metrics.items()):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")

        manifest = result["manifest_parity_projection"]
        if set(manifest) != {
            "artifact_authority_target_tuple",
            "requested_authority_target_tuple",
            "live_catalog_authority_target_tuple",
            "measured_case_authority_target_tuple",
        }:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        manifest_values = tuple(
            tuple(tuple(row) for row in manifest[key])
            for key in (
                "artifact_authority_target_tuple",
                "requested_authority_target_tuple",
                "live_catalog_authority_target_tuple",
                "measured_case_authority_target_tuple",
            )
        )
        if len(set(manifest_values)) != 1 or manifest_values[0] != targets:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        digest_expectations = {
            "catalog_projection_sha256": _digest(
                [
                    "task7-v16-census-catalog-projection-v1",
                    result["catalog_projection_tuple"],
                ]
            ),
            "case_result_sha256": _digest(["task7-v16-current-guard-case-results-v1", outcomes]),
            "constructor_roundtrip_sha256": _digest(
                ["task7-v16-constructor-roundtrips-v1", constructors]
            ),
            "admin_fixture_helper_projection_sha256": _digest(
                ["task7-v16-admin-fixture-helper-projection-v1", helper_projection]
            ),
            "manifest_parity_sha256": _digest(["task7-v16-manifest-parity-v1", manifest]),
            "pg_trigger_inventory_sha256": _digest(
                [
                    "task7-v16-pg-trigger-projection-v1",
                    result["pg_trigger_projection_tuple"],
                ]
            ),
            "trigger_event_coverage_sha256": _digest(
                [
                    "task7-v16-trigger-event-coverage-v1",
                    [
                        list(value)
                        for value in sorted(
                            {
                                (
                                    row["authority_id"],
                                    row["table_name"],
                                    row["guard_name"],
                                    row["trigger_event"],
                                )
                                for row in outcomes
                            }
                        )
                    ],
                ]
            ),
            "guard_acl_projection_sha256": _digest(
                [
                    "task7-v16-guard-acl-projection-v1",
                    routine_catalog,
                    attribute_acl,
                    schema_acl,
                    result["guard_owner_posture"],
                    result["external_erasure_helper_proof"],
                ]
            ),
            "rls_context_proof_sha256": _digest(
                [
                    "task7-v16-security-definer-guard-owner-rls-context-proof-v1",
                    result["security_definer_guard_owner_rls_context_proof"],
                ]
            ),
        }
        if any(result[key] != value for key, value in digest_expectations.items()):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        _validate_complete_execution_result(result)
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID") from exc


def build_and_verify_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Issue and immediately verify an artifact-bound v16 census receipt."""

    _HARNESS_SOURCE_STORE.assert_exact_closure()
    signer_id = _validate_signer_id(signer_id)
    _validate_serialized_provenance(
        result,
        error_code="TASK7_V16_CENSUS_RECEIPT_INVALID",
    )
    _validate_complete_census_result(result, artifact_raw=artifact_raw)
    execution = _database_execution(result)
    _validate_database_execution(execution)
    payload = {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v16",
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": result["prospective_frozen_artifact_sha256"],
        "database_execution": execution,
        "census_result": dict(result),
    }
    payload_sha256 = _digest(payload)
    public_key_sha256 = hashlib.sha256(trusted_public_key_raw).hexdigest()
    signature = private_key.sign(
        _canonical_bytes(
            [
                _CENSUS_SIGNING_DOMAIN,
                signer_id,
                payload_sha256,
                public_key_sha256,
                payload,
            ]
        )
    )
    receipt = {
        "schema_version": _CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    rendered = (json.dumps(receipt, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    verify_signed_receipt(
        receipt_raw=rendered,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    return receipt


def _decode_receipt(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (CensusError, UnicodeError, ValueError) as exc:
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID") from exc
    if not isinstance(value, dict):
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
    return value


def _validate_signer_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
    return value


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify signature, artifact binding, shape, and semantic census counts."""

    try:
        receipt = _decode_receipt(receipt_raw)
        _validate_serialized_provenance(
            receipt,
            error_code="TASK7_V16_CENSUS_RECEIPT_INVALID",
        )
        if tuple(receipt) != (
            "schema_version",
            "signer_id",
            "payload",
            "payload_sha256",
            "public_key_sha256",
            "signature_base64",
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA:
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        signer_id = _validate_signer_id(receipt["signer_id"])
        payload = receipt["payload"]
        if not isinstance(payload, Mapping) or tuple(payload) != (
            "schema_version",
            "result",
            "artifact_schema",
            "pipeline_contract_version",
            "candidate_artifact_sha256",
            "prospective_frozen_artifact_sha256",
            "database_execution",
            "census_result",
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        if (
            payload["schema_version"] != _CENSUS_PAYLOAD_SCHEMA
            or payload["result"] != "PASS"
            or payload["artifact_schema"] != "full-strict-v16-reviewed-preimage-v1"
            or payload["pipeline_contract_version"] != "full-strict-v16"
            or payload["candidate_artifact_sha256"] != hashlib.sha256(artifact_raw).hexdigest()
            or receipt["payload_sha256"] != _digest(payload)
            or receipt["public_key_sha256"] != hashlib.sha256(trusted_public_key_raw).hexdigest()
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        execution = payload["database_execution"]
        census_result = payload["census_result"]
        if not isinstance(execution, Mapping) or not isinstance(census_result, Mapping):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        _validate_complete_census_result(
            census_result,
            artifact_raw=artifact_raw,
        )
        if (
            dict(execution) != _database_execution(census_result)
            or census_result.get("candidate_artifact_sha256")
            != payload["candidate_artifact_sha256"]
            or census_result.get("prospective_frozen_artifact_sha256")
            != payload["prospective_frozen_artifact_sha256"]
        ):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        _validate_database_execution(execution)
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID")
        signature = base64.b64decode(
            str(receipt["signature_base64"]),
            validate=True,
        )
        public_key.verify(
            signature,
            _canonical_bytes(
                [
                    _CENSUS_SIGNING_DOMAIN,
                    signer_id,
                    receipt["payload_sha256"],
                    receipt["public_key_sha256"],
                    payload,
                ]
            ),
        )
    except (
        CensusError,
        InvalidSignature,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        if isinstance(exc, CensusError) and str(exc) == ("TASK7_V16_CENSUS_RECEIPT_INVALID"):
            raise
        raise CensusError("TASK7_V16_CENSUS_RECEIPT_INVALID") from exc
    return dict(payload)


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CensusError",
    "GUARD_OWNER_ROLE",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "RESULT_SCHEMA",
    "TASK6_WRITE_CONTEXT_GUCS",
    "_CENSUS_SIGNING_DOMAIN",
    "_canonical_bytes",
    "_digest",
    "build_and_verify_signed_receipt",
    "run_pre_fixture_census",
    "validate_fixture_values",
    "verify_signed_receipt",
]
