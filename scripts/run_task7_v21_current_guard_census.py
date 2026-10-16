"""Execute the unfrozen full-strict-v21 PostgreSQL census.

The v20 runner remains immutable and hash-pinned.  This adapter retargets its
219 application-role cases and dedicated erasure supplement to the v21
candidate, admitting the one additional direct executor schema-USAGE grant
only when the materializer's executable-call ACL closure is exact.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final

_V20_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v20_current_guard_census.py")
_V20_RUNNER_SHA256: Final = "823230359182e1a0b38483c96f268577bf6fd9f9a5a23d8b2dba1752ca94d217"

_V21_CANDIDATE_SHA256: Final = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"
_V20_SCAFFOLD_SHA256: Final = "52c9d21718005f591b4de897864145a7a6322330b10d7ef45c290a993096b866"
_V21_SCAFFOLD_SHA256: Final = "d7825de8a1a1681ab4ebb717c018f33a74ca25f94c0969fbed0d03767b3694a9"
_V20_MATERIALIZER_SHA256: Final = "f49b9f4e427505909715c7a84f6ecb88b1a1fb2082f5c652f15da5cffbdee509"
_V21_MATERIALIZER_SHA256: Final = "f038230702e846d1ee79025abdaa6b08f416702645f6d90f9d72f7ae64996946"
_V20_GENERATOR_SHA256: Final = "3ac5826bfe9909bbedaee6a3984fe9201edd2043dab7bf547c4d81887cd16c9d"
_V21_GENERATOR_SHA256: Final = "fc7865f40942a6ea0e7eb2c5cd577acf1e33b6250d30bec7396859a4b18b93c1"
_V20_ADAPTED_V11_SHA256: Final = "5358279cd88ddc2dc48a74725f89e16f9c36c863c2c49e6e55b44aa3820a1f4b"
_V21_ADAPTED_V11_SHA256: Final = "9ec57982aea17fc0296debca02a4843d27c18d25d20da1003d287efbc2f6b5b1"
_V20_ADAPTED_V9_SHA256: Final = "24225a4c284cc8f6c43df1bf75ff7e7c2e9aa7e99d7530051347a8cf46885eab"
_V21_ADAPTED_V9_SHA256: Final = "745c4024a072a4e90ad09ce3435ab419d866315e41d24a5a47a42f690603f6c1"
_V21_MATERIALIZER_PATH: Final = Path(__file__).with_name(
    "materialize_full_strict_v21_preimage.py"
)


def _adapt_pinned_v20_runner() -> str:
    raw = _V20_RUNNER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V20_RUNNER_SHA256:
        raise RuntimeError("TASK7_V21_V20_CENSUS_RUNNER_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V21_V20_CENSUS_RUNNER_INVALID") from exc
    source = source.replace("V20", "V21").replace("v20", "v21")
    candidate_match = re.search(
        r'^_V21_CANDIDATE_SHA256: Final = "([0-9a-f]{64})"$',
        source,
        flags=re.MULTILINE,
    )
    if candidate_match is None:
        raise RuntimeError("TASK7_V21_V20_CENSUS_RUNNER_INVALID")
    predecessor_candidate_sha256 = candidate_match.group(1)
    replacements = (
        (predecessor_candidate_sha256, _V21_CANDIDATE_SHA256, 1),
        (_V20_SCAFFOLD_SHA256, _V21_SCAFFOLD_SHA256, 1),
        (_V20_MATERIALIZER_SHA256, _V21_MATERIALIZER_SHA256, 1),
        (_V20_GENERATOR_SHA256, _V21_GENERATOR_SHA256, 1),
        (_V20_ADAPTED_V11_SHA256, _V21_ADAPTED_V11_SHA256, 1),
        (_V20_ADAPTED_V9_SHA256, _V21_ADAPTED_V9_SHA256, 1),
        ("6_514_100", "6_515_556", 1),
        ("5_159", "7_034", 1),
        ("22_157", "20_898", 1),
        ("7_131", "7_117", 1),
        (
            '        ("!= 114", "!= 116", 1),',
            '        ("!= 114", "!= 116", 1),\n'
            '        ("len(acl) != 208", "len(acl) != 209", 1),',
            1,
        ),
        ('("current", (208, 51, 116))', '("current", (209, 51, 116))', 1),
        ('("future", (360, 89, 192))', '("future", (361, 89, 192))', 1),
    )
    for old, new, count in replacements:
        if source.count(old) != count:
            raise RuntimeError("TASK7_V21_V20_CENSUS_RUNNER_INVALID")
        source = source.replace(old, new)

    inherited_return = "    return _normalize_database_execution_projection_semantics(source)\n"
    guarded_return = """    source = _normalize_database_execution_projection_semantics(source)
    old_direct_acl = 'and acl["executor_direct_schema_acl_count"] == 0'
    new_direct_acl = 'and acl["executor_direct_schema_acl_count"] == 1'
    if source.count(old_direct_acl) != 1 or new_direct_acl in source:
        raise RuntimeError("TASK7_V21_EXECUTOR_SCHEMA_ACL_PROOF_INVALID")
    return source.replace(old_direct_acl, new_direct_acl)
"""
    if source.count(inherited_return) != 1:
        raise RuntimeError("TASK7_V21_V20_CENSUS_RUNNER_INVALID")
    return source.replace(inherited_return, guarded_return)


exec(  # noqa: S102 - exact hash-pinned predecessor source adaptation.
    compile(
        _adapt_pinned_v20_runner(),
        str(_V20_RUNNER_PATH),
        "exec",
    ),
    globals(),
)

_V21_COUNT_VALIDATE = globals()["_validate_v21_candidate"]
_V21_CENSUS_ERROR = globals()["CensusError"]


def _load_v21_materializer() -> ModuleType:
    raw = _V21_MATERIALIZER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V21_MATERIALIZER_SHA256:
        raise _V21_CENSUS_ERROR("TASK7_V21_MATERIALIZATION_SOURCE_DRIFT")
    name = "_task7_v21_census_materializer"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V21_MATERIALIZER_PATH),
    )
    if spec is None:
        raise _V21_CENSUS_ERROR("TASK7_V21_MATERIALIZATION_SOURCE_DRIFT")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(_V21_MATERIALIZER_PATH)
    sys.modules[name] = module
    exec(compile(raw, str(_V21_MATERIALIZER_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


_V21_MATERIALIZER = _load_v21_materializer()


def _validate_v21_candidate(artifact: Mapping[str, Any]) -> None:
    """Admit exact ACL counts plus executable-call/schema-usage closure."""

    _V21_COUNT_VALIDATE(artifact)
    try:
        _V21_MATERIALIZER.validate_executable_call_acl_closure(artifact)
    except Exception as exc:
        raise _V21_CENSUS_ERROR("TASK7_V21_EXECUTABLE_CALL_ACL_CLOSURE_INVALID") from exc


__all__ = [
    *globals().get("__all__", ()),
    "_V21_ADAPTED_V11_SHA256",
    "_V21_ADAPTED_V9_SHA256",
    "_V21_CANDIDATE_SHA256",
    "_validate_v21_candidate",
]
