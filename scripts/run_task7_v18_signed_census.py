"""Run and sign the approved v18 census through a pinned v17 CLI adapter.

This script does not freeze or publish the candidate.  It preserves the
reviewed v17 safe-profile, exclusive-write, and Ed25519 receipt mechanics while
pinning the v18 runner, unfrozen candidate, and complete source-manifest digest.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

_V17_SIGNED_CLI_PATH: Final = Path(__file__).with_name("run_task7_v17_signed_census.py")
_V17_SIGNED_CLI_SHA256: Final = "d5de3948a555d557da57a0936ce03941b1548e489b82712543233f20e439bada"
_V17_RUNNER_SHA256: Final = "9f3585fb6b10a35cf1c73bd3d7f08c6c3aacb60c83d5b4d8e9e32ee9c0ed539c"
_V18_RUNNER_SHA256: Final = "10988bb9cbbc368b496c7d9e55177b5d7390219d8d38dc70e40e10583a6cf006"
_V17_CANDIDATE_SHA256: Final = "ae6d42df18d236fad3730be9829fe66b19549e7902c06ea9c62ad7c6d250a5e7"
_V18_CANDIDATE_SHA256: Final = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_V17_SOURCE_MANIFEST_SHA256: Final = (
    "3f8da0bdc0c3bae230ca057388b1a45a30154dacffe85f60c47d07496763e5ca"
)
_V18_SOURCE_MANIFEST_SHA256: Final = (
    "6c610392b89b0d6d8a3bea72a45cd17dcdadbf6da3dd66a46064c8759fa560d2"
)


def _adapt_pinned_v17_cli() -> str:
    raw = _V17_SIGNED_CLI_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V17_SIGNED_CLI_SHA256:
        raise RuntimeError("TASK7_V18_V17_SIGNED_CENSUS_CLI_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V18_V17_SIGNED_CENSUS_CLI_INVALID") from exc
    replacements = (
        (_V17_RUNNER_SHA256, _V18_RUNNER_SHA256),
        (_V17_CANDIDATE_SHA256, _V18_CANDIDATE_SHA256),
        (_V17_SOURCE_MANIFEST_SHA256, _V18_SOURCE_MANIFEST_SHA256),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError("TASK7_V18_V17_SIGNED_CENSUS_CLI_INVALID")
        source = source.replace(old, new)
    return source.replace("V17", "V18").replace("v17", "v18")


exec(  # noqa: S102
    compile(
        _adapt_pinned_v17_cli(),
        str(_V17_SIGNED_CLI_PATH),
        "exec",
    ),
    globals(),
)
