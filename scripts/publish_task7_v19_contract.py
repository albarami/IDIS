"""Crash-safe atomic publisher for an authorized full-strict-v19 contract.

The v18 atomic publication engine is immutable and hash-pinned.  This module
retargets that engine to the separately pinned v19 authorizer and extends the
closed database projection with the retained-definition and erasure-authority
proof digests.  Importing this module never publishes anything.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

_V18_PUBLISHER_PATH: Final = Path(__file__).with_name("publish_task7_v18_contract.py")
_V18_PUBLISHER_SHA256: Final = "e7667d6aa652d34fd78677acd36e306bc409c5f2ba7e4fe42929f4c6b8508bc4"
_V18_AUTHORIZER_SHA256: Final = "ae2937339c663cb46012307993938e90e7260910840ff9edeedaebc5a9ae8a6c"
_V19_AUTHORIZER_SHA256: Final = "2da18978f28ba13a0d83c509f39434098838c36b4a7eab12033a752b0ad79387"


def _adapt_pinned_v18_publisher() -> str:
    raw = _V18_PUBLISHER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V18_PUBLISHER_SHA256:
        raise RuntimeError("TASK7_V19_V18_ATOMIC_PUBLISHER_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V19_V18_ATOMIC_PUBLISHER_INVALID") from exc
    if source.count(_V18_AUTHORIZER_SHA256) != 1:
        raise RuntimeError("TASK7_V19_V18_ATOMIC_PUBLISHER_INVALID")
    source = source.replace(_V18_AUTHORIZER_SHA256, _V19_AUTHORIZER_SHA256)
    return source.replace("V18", "V19").replace("v18", "v19")


exec(  # noqa: S102
    compile(
        _adapt_pinned_v18_publisher(),
        str(_V18_PUBLISHER_PATH),
        "exec",
    ),
    globals(),
)

# The adapted v18 engine intentionally retains its exact publication
# mechanics.  V19 adds only two hash fields to the closed PostgreSQL evidence
# projection; both must be present and well-formed before any filesystem stage
# is created.
_BASE_DATABASE_EXECUTION_FIELDS = globals()["_DATABASE_EXECUTION_FIELDS"]
_DATABASE_EXECUTION_FIELDS = (
    *_BASE_DATABASE_EXECUTION_FIELDS,
    "downgrade_retained_definition_execution_proof_sha256",
    "erasure_authority_execution_proof_sha256",
)
