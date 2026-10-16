"""Fail-closed atomic publisher for full-strict-v21.

The finalized v20 atomic engine is immutable and hash-pinned.  This module
retargets only that engine's exact authorizer pin and version labels to the
separately verified v21 publication authority.  Importing this module never
publishes, freezes, or writes anything.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

_V20_PUBLISHER_PATH: Final = Path(__file__).with_name("publish_task7_v20_contract.py")
_V20_PUBLISHER_SHA256: Final = "fefce85cf072451f0212bc60f5999a7b15f35d665b303f85d5048dfb6374d791"
_V20_AUTHORIZER_SHA256: Final = "631f873b533931ebb30c42d509b51536c2ddd1077d944fb48a73e536592be207"
_V21_AUTHORIZER_SHA256: Final = "ba557810ade52ae9c8f63ccd44249e61af9866eca4971f08b9e60569ad0d6c36"


def _adapt_pinned_v20_publisher() -> str:
    raw = _V20_PUBLISHER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V20_PUBLISHER_SHA256:
        raise RuntimeError("TASK7_V21_V20_ATOMIC_PUBLISHER_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V21_V20_ATOMIC_PUBLISHER_INVALID") from exc
    if source.count(_V20_AUTHORIZER_SHA256) != 1:
        raise RuntimeError("TASK7_V21_V20_ATOMIC_PUBLISHER_INVALID")
    source = source.replace(_V20_AUTHORIZER_SHA256, _V21_AUTHORIZER_SHA256)
    adapted = source.replace("V20", "V21").replace("v20", "v21")
    nested_main_guard = (
        '\n\nif __name__ == "__main__":\n    raise SystemExit(globals()["main"]())\n'
    )
    if adapted.count(nested_main_guard) != 1:
        raise RuntimeError("TASK7_V21_V20_ATOMIC_PUBLISHER_INVALID")
    return adapted.replace(nested_main_guard, "", 1)


exec(  # noqa: S102 - exact hash-pinned predecessor publication adaptation.
    compile(
        _adapt_pinned_v20_publisher(),
        str(_V20_PUBLISHER_PATH),
        "exec",
    ),
    globals(),
)


if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
