"""Run and sign the approved v19 census through the pinned v17 CLI.

This wrapper preserves the reviewed safe-profile, exclusive-write, and
Ed25519 mechanics.  It retargets only the immutable runner, unfrozen
candidate, source-manifest digest, version labels, and output name.  It cannot
freeze or publish the contract.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

_V17_SIGNED_CLI_PATH: Final = Path(__file__).with_name("run_task7_v17_signed_census.py")
_V17_SIGNED_CLI_SHA256: Final = "d5de3948a555d557da57a0936ce03941b1548e489b82712543233f20e439bada"
_V17_RUNNER_SHA256: Final = "9f3585fb6b10a35cf1c73bd3d7f08c6c3aacb60c83d5b4d8e9e32ee9c0ed539c"
_V19_RUNNER_SHA256: Final = "503ed962bf14e12e72b2a398ea8fc0cc65a9d92e034fcfab7aaba2eb288e01be"
_V17_CANDIDATE_SHA256: Final = "ae6d42df18d236fad3730be9829fe66b19549e7902c06ea9c62ad7c6d250a5e7"
_V19_CANDIDATE_SHA256: Final = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_V17_SOURCE_MANIFEST_SHA256: Final = (
    "3f8da0bdc0c3bae230ca057388b1a45a30154dacffe85f60c47d07496763e5ca"
)
_V19_SOURCE_MANIFEST_SHA256: Final = (
    "dd6baf62d9f648d2b880b34677d05de7f64501c70ad0efc2ed117449d8b3c1e1"
)
_MIGRATION_DATABASE_PROFILE_KEYS: Final = (
    "IDIS_DATABASE_ADMIN_URL",
    "IDIS_DATABASE_URL",
    "IDIS_ERASURE_DATABASE_URL",
)


def _adapt_pinned_v17_cli() -> str:
    raw = _V17_SIGNED_CLI_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V17_SIGNED_CLI_SHA256:
        raise RuntimeError("TASK7_V19_V17_SIGNED_CENSUS_CLI_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V19_V17_SIGNED_CENSUS_CLI_INVALID") from exc
    replacements = (
        (_V17_RUNNER_SHA256, _V19_RUNNER_SHA256),
        (_V17_CANDIDATE_SHA256, _V19_CANDIDATE_SHA256),
        (_V17_SOURCE_MANIFEST_SHA256, _V19_SOURCE_MANIFEST_SHA256),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError("TASK7_V19_V17_SIGNED_CENSUS_CLI_INVALID")
        source = source.replace(old, new)
    if source.count('command="governance"') != 1:
        raise RuntimeError("TASK7_V19_V17_SIGNED_CENSUS_CLI_INVALID")
    source = source.replace('command="governance"', 'command="migrate"')
    function_marker = "def _load_operator_material() -> _OperatorMaterial:\n"
    binding_function = """
def _bind_migration_profile_database_urls(profile: Any) -> None:
    for key in _MIGRATION_DATABASE_PROFILE_KEYS:
        value = str(profile.values.get(key, "")).strip()
        if not value:
            raise SignedCensusCliError(
                "TASK7_V17_SIGNED_CENSUS_DATABASE_PROFILE_INVALID"
            )
        os.environ[key] = value


"""
    if source.count(function_marker) != 1:
        raise RuntimeError("TASK7_V19_V17_SIGNED_CENSUS_CLI_INVALID")
    source = source.replace(function_marker, binding_function + function_marker)
    signer_marker = (
        '    signer_id = str(profile.values.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", ""))\n'
    )
    if source.count(signer_marker) != 1:
        raise RuntimeError("TASK7_V19_V17_SIGNED_CENSUS_CLI_INVALID")
    source = source.replace(
        signer_marker,
        "    _bind_migration_profile_database_urls(profile)\n" + signer_marker,
    )
    return source.replace("V17", "V19").replace("v17", "v19")


exec(  # noqa: S102
    compile(
        _adapt_pinned_v17_cli(),
        str(_V17_SIGNED_CLI_PATH),
        "exec",
    ),
    globals(),
)
