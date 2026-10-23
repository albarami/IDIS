"""Fail-closed atomic publisher scaffold for full-strict-v20.

The crash-safe v18 publication engine is immutable and hash-pinned.  This
module retargets that engine to the separately pinned v20 authorizer and adds
the two database-proof fields introduced after v18.  Publication is admitted
only through the finalized v20 census runner, source-manifest, and exact
signed-receipt pins.

Importing this module never publishes anything.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Never, cast

_V18_PUBLISHER_PATH: Final = Path(__file__).with_name("publish_task7_v18_contract.py")
_V18_PUBLISHER_SHA256: Final = "e7667d6aa652d34fd78677acd36e306bc409c5f2ba7e4fe42929f4c6b8508bc4"
_V18_AUTHORIZER_SHA256: Final = "ae2937339c663cb46012307993938e90e7260910840ff9edeedaebc5a9ae8a6c"
_V20_AUTHORIZER_SHA256: Final = "631f873b533931ebb30c42d509b51536c2ddd1077d944fb48a73e536592be207"


def _adapt_pinned_v18_publisher() -> str:
    raw = _V18_PUBLISHER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V18_PUBLISHER_SHA256:
        raise RuntimeError("TASK7_V20_V18_ATOMIC_PUBLISHER_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V20_V18_ATOMIC_PUBLISHER_INVALID") from exc
    if source.count(_V18_AUTHORIZER_SHA256) != 1:
        raise RuntimeError("TASK7_V20_V18_ATOMIC_PUBLISHER_INVALID")
    source = source.replace(_V18_AUTHORIZER_SHA256, _V20_AUTHORIZER_SHA256)
    adapted = source.replace("V18", "V20").replace("v18", "v20")
    nested_main_guard = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
    if adapted.count(nested_main_guard) != 1:
        raise RuntimeError("TASK7_V20_V18_ATOMIC_PUBLISHER_INVALID")
    return adapted.replace(nested_main_guard, "", 1)


exec(  # noqa: S102
    compile(
        _adapt_pinned_v18_publisher(),
        str(_V18_PUBLISHER_PATH),
        "exec",
    ),
    globals(),
)

# V20 retains the exact v18 publication mechanics and the v19 database-proof
# extension. The independently verified runner/source/receipt pins are closed
# in the hash-pinned v20 authorizer before this switch is enabled.
_BASE_DATABASE_EXECUTION_FIELDS = globals()["_DATABASE_EXECUTION_FIELDS"]
_DATABASE_EXECUTION_FIELDS = (
    *_BASE_DATABASE_EXECUTION_FIELDS,
    "downgrade_retained_definition_execution_proof_sha256",
    "erasure_authority_execution_proof_sha256",
)
_PUBLICATION_PINS_FINALIZED: Final = True


def _require_publication_pins() -> None:
    """Reject before any publication input or filesystem path is consulted."""

    if _PUBLICATION_PINS_FINALIZED is not True:
        error = cast(Callable[[str, str], Never], globals()["_error"])
        error(
            "TASK7_V20_PUBLICATION_PINS_PENDING",
            "final v20 census publication pins are not admitted",
        )


_publish_candidate_atomically_after_pin_admission = cast(
    Callable[..., dict[str, Any]],
    globals()["publish_candidate_atomically"],
)


def publish_candidate_atomically(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, object]:
    """Publish only after explicit finalization of all v20 pins."""

    _require_publication_pins()
    return cast(
        dict[str, object],
        _publish_candidate_atomically_after_pin_admission(
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
