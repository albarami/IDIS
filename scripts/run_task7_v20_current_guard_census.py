"""Execute the unfrozen full-strict-v20 PostgreSQL census.

The v19 runner is the immutable reviewed composition authority, including the
external helper-owner installation that precedes the erasure runner.  V20
retargets that exact source to the v20 artifact and its two-column SELECT
closure amendment.  No operator profile is read at import time.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

_V19_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v19_current_guard_census.py")
_V19_RUNNER_SHA256: Final = "503ed962bf14e12e72b2a398ea8fc0cc65a9d92e034fcfab7aaba2eb288e01be"

_V20_CANDIDATE_SHA256: Final = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_V19_SCAFFOLD_SHA256: Final = "a7460f38928b52ed5f31b2838e28c0fbdab5d8134d38b67845da0a695e808b8f"
_V20_SCAFFOLD_SHA256: Final = "52c9d21718005f591b4de897864145a7a6322330b10d7ef45c290a993096b866"
_V19_MATERIALIZER_SHA256: Final = "a94fe22a271498ac2276f4435271fb7db6d4160f2cabfd886cb5a7d6558620ae"
_V20_MATERIALIZER_SHA256: Final = "f49b9f4e427505909715c7a84f6ecb88b1a1fb2082f5c652f15da5cffbdee509"
_V19_GENERATOR_SHA256: Final = "5f1217e5327ac04c11d1fe30be973a36ec76235e146c9bf869483f08a7fa2206"
_V20_GENERATOR_SHA256: Final = "3ac5826bfe9909bbedaee6a3984fe9201edd2043dab7bf547c4d81887cd16c9d"
_V19_ADAPTED_V11_SHA256: Final = "58e3bbbda00e6653bd2e4eeecd2ca7859d2340c3e40ed9d62e69038f903a64cc"
_V20_ADAPTED_V11_SHA256: Final = "5358279cd88ddc2dc48a74725f89e16f9c36c863c2c49e6e55b44aa3820a1f4b"
_V19_ADAPTED_V9_SHA256: Final = "b3e5fa9474be385b794cfeb0514a344040b001063302b156f2e081dbe5e08c94"
_V20_ADAPTED_V9_SHA256: Final = "24225a4c284cc8f6c43df1bf75ff7e7c2e9aa7e99d7530051347a8cf46885eab"
_ZERO_DIMENSIONAL_COLUMN_ACL_EXPANSION: Final = (
    "pg_catalog.aclexplode(\n"
    "                  COALESCE(attribute.attacl,'{}'::aclitem[])\n"
    "                )"
)
_NULL_COLUMN_ACL_EXPANSION: Final = (
    "pg_catalog.aclexplode(\n                  attribute.attacl\n                )"
)
_FORBIDDEN_COLUMN_ACL_FALLBACK = re.compile(
    r"pg_catalog\.aclexplode\(\s*COALESCE\(\s*attribute\.attacl\b",
    flags=re.IGNORECASE,
)
_RAW_BASE_DATABASE_EXECUTION_PROJECTION: Final = (
    "projection = {field: result.get(field) for field in _BASE_DATABASE_EXECUTION_FIELDS}"
)
_RAW_BASE_DATABASE_EXECUTION_EXPECTED: Final = (
    "expected = {field: result.get(field) for field in _BASE_DATABASE_EXECUTION_FIELDS}"
)
_DERIVED_BASE_DATABASE_EXECUTION_PROJECTION: Final = (
    "projection = dict(_BASE._database_execution(result, artifact))"
)
_DERIVED_BASE_DATABASE_EXECUTION_EXPECTED: Final = (
    "expected = dict(_BASE._database_execution(result, artifact))"
)


def _normalize_column_acl_null_semantics(source: str) -> str:
    """Use NULL-as-no-rows; empty ACL arrays are zero-dimensional in PostgreSQL."""

    bad_count = source.count(_ZERO_DIMENSIONAL_COLUMN_ACL_EXPANSION)
    direct_count = source.count(_NULL_COLUMN_ACL_EXPANSION)
    if (bad_count, direct_count) == (2, 0):
        source = source.replace(
            _ZERO_DIMENSIONAL_COLUMN_ACL_EXPANSION,
            _NULL_COLUMN_ACL_EXPANSION,
        )
    elif (bad_count, direct_count) != (0, 2):
        raise RuntimeError("TASK7_V20_COLUMN_ACL_NULL_SEMANTICS_INVALID")
    if _FORBIDDEN_COLUMN_ACL_FALLBACK.search(source) is not None:
        raise RuntimeError("TASK7_V20_COLUMN_ACL_NULL_SEMANTICS_INVALID")
    return source


def _normalize_database_execution_projection_semantics(source: str) -> str:
    """Preserve inherited proof derivation instead of copying absent raw fields."""

    raw_pair = (
        source.count(_RAW_BASE_DATABASE_EXECUTION_PROJECTION),
        source.count(_RAW_BASE_DATABASE_EXECUTION_EXPECTED),
    )
    derived_pair = (
        source.count(_DERIVED_BASE_DATABASE_EXECUTION_PROJECTION),
        source.count(_DERIVED_BASE_DATABASE_EXECUTION_EXPECTED),
    )
    if (raw_pair, derived_pair) == ((1, 1), (0, 0)):
        source = source.replace(
            _RAW_BASE_DATABASE_EXECUTION_PROJECTION,
            _DERIVED_BASE_DATABASE_EXECUTION_PROJECTION,
        ).replace(
            _RAW_BASE_DATABASE_EXECUTION_EXPECTED,
            _DERIVED_BASE_DATABASE_EXECUTION_EXPECTED,
        )
    elif (raw_pair, derived_pair) != ((0, 0), (1, 1)):
        raise RuntimeError("TASK7_V20_DATABASE_EXECUTION_PROJECTION_INVALID")
    return source


def _adapt_pinned_v19_runner() -> str:
    raw = _V19_RUNNER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V19_RUNNER_SHA256:
        raise RuntimeError("TASK7_V20_V19_CENSUS_RUNNER_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V20_V19_CENSUS_RUNNER_INVALID") from exc

    source = source.replace("V19", "V20").replace("v19", "v20")
    candidate_match = re.search(
        r'^_V20_CANDIDATE_SHA256: Final = "([0-9a-f]{64})"$',
        source,
        flags=re.MULTILINE,
    )
    if candidate_match is None:
        raise RuntimeError("TASK7_V20_V19_CENSUS_RUNNER_INVALID")
    predecessor_candidate_sha256 = candidate_match.group(1)
    replacements = (
        (predecessor_candidate_sha256, _V20_CANDIDATE_SHA256, 1),
        (_V19_SCAFFOLD_SHA256, _V20_SCAFFOLD_SHA256, 2),
        (_V19_MATERIALIZER_SHA256, _V20_MATERIALIZER_SHA256, 1),
        (_V19_GENERATOR_SHA256, _V20_GENERATOR_SHA256, 1),
        (_V19_ADAPTED_V11_SHA256, _V20_ADAPTED_V11_SHA256, 1),
        (_V19_ADAPTED_V9_SHA256, _V20_ADAPTED_V9_SHA256, 1),
        ("6_513_111", "6_514_100", 1),
        ("8_076", "5_159", 1),
        ("53_969", "22_157", 1),
        ("17_928", "7_131", 1),
        ("!= 114", "!= 116", 1),
    )
    for old, new, count in replacements:
        if source.count(old) != count:
            raise RuntimeError("TASK7_V20_V19_CENSUS_RUNNER_INVALID")
        source = source.replace(old, new)
    source = _normalize_column_acl_null_semantics(source)
    return _normalize_database_execution_projection_semantics(source)


exec(  # noqa: S102 - exact hash-pinned predecessor source adaptation.
    compile(
        _adapt_pinned_v19_runner(),
        str(_V19_RUNNER_PATH),
        "exec",
    ),
    globals(),
)

_PREDECESSOR_VALIDATE_V20_CANDIDATE = globals()["_validate_v20_candidate"]
_V20_CENSUS_ERROR = globals()["CensusError"]


def erasure_acl_census_counts(artifact: Mapping[str, Any]) -> dict[str, int]:
    """Derive the current/future ACL census directly from candidate bytes."""

    _PREDECESSOR_VALIDATE_V20_CANDIDATE(artifact)
    try:
        row = artifact["erasure_execution_registry"]["rows"][0]
    except (KeyError, TypeError, IndexError) as exc:
        raise _V20_CENSUS_ERROR("TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID") from exc
    select_pattern = re.compile(
        r"^GRANT SELECT \((?P<columns>[a-z0-9_,]+)\) "
        r"ON TABLE public\.(?P<table>[a-z][a-z0-9_]*) "
        r"TO idis_erasure_function_owner$"
    )
    result: dict[str, int] = {}
    for scope, expected in (
        ("current", (208, 51, 116)),
        ("future", (360, 89, 192)),
    ):
        try:
            statements = tuple(
                bytes.fromhex(str(value)).decode("utf-8")
                for value in row[f"{scope}_acl_sql_utf8_hex_tuple"]
            )
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise _V20_CENSUS_ERROR("TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID") from exc
        matches = tuple(
            match
            for statement in statements
            if (match := select_pattern.fullmatch(statement)) is not None
        )
        observed = (
            len(statements),
            len({match.group("table") for match in matches}),
            sum(len(match.group("columns").split(",")) for match in matches),
        )
        if observed != expected:
            raise _V20_CENSUS_ERROR("TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID")
        result[f"{scope}_statement_count"] = observed[0]
        result[f"{scope}_select_relation_count"] = observed[1]
        result[f"{scope}_select_column_count"] = observed[2]
    return result


def _validate_v20_candidate(artifact: Mapping[str, Any]) -> None:
    """Admit only the exact v20 candidate and its closed ACL census."""

    erasure_acl_census_counts(artifact)
