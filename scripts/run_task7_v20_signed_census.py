"""Run and sign the approved v20 census through the pinned v19 wrapper.

This preserves the reviewed strict-profile, exclusive-write, migrate-profile,
and Ed25519 mechanics while retargeting only the pinned runner, candidate,
source-manifest digest, version labels, and output name.  It cannot freeze or
publish the contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Final

_V19_SIGNED_CLI_PATH: Final = Path(__file__).with_name("run_task7_v19_signed_census.py")
_V19_SIGNED_CLI_SHA256: Final = "454e2dddf3e7d94007d8d2ffda8f90a3530230444626318fff7dda62be3fc510"
_V19_RUNNER_SHA256: Final = "503ed962bf14e12e72b2a398ea8fc0cc65a9d92e034fcfab7aaba2eb288e01be"
_V20_RUNNER_SHA256: Final = "823230359182e1a0b38483c96f268577bf6fd9f9a5a23d8b2dba1752ca94d217"
_V20_CANDIDATE_SHA256: Final = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_V19_SOURCE_MANIFEST_SHA256: Final = (
    "dd6baf62d9f648d2b880b34677d05de7f64501c70ad0efc2ed117449d8b3c1e1"
)
_V20_SOURCE_MANIFEST_SHA256: Final = (
    "9375b1cbbafafca1c3d4263c43d0f7a4fa32b2df66acc42bf3d00447600404f2"
)


def _adapt_pinned_v19_cli() -> str:
    raw = _V19_SIGNED_CLI_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V19_SIGNED_CLI_SHA256:
        raise RuntimeError("TASK7_V20_V19_SIGNED_CENSUS_CLI_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V20_V19_SIGNED_CENSUS_CLI_INVALID") from exc
    source = source.replace("V19", "V20").replace("v19", "v20")
    candidate_match = re.search(
        r'^_V20_CANDIDATE_SHA256: Final = "([0-9a-f]{64})"$',
        source,
        flags=re.MULTILINE,
    )
    if candidate_match is None:
        raise RuntimeError("TASK7_V20_V19_SIGNED_CENSUS_CLI_INVALID")
    predecessor_candidate_sha256 = candidate_match.group(1)
    for old, new in (
        (_V19_RUNNER_SHA256, _V20_RUNNER_SHA256),
        (predecessor_candidate_sha256, _V20_CANDIDATE_SHA256),
        (_V19_SOURCE_MANIFEST_SHA256, _V20_SOURCE_MANIFEST_SHA256),
    ):
        if source.count(old) != 1:
            raise RuntimeError("TASK7_V20_V19_SIGNED_CENSUS_CLI_INVALID")
        source = source.replace(old, new)
    inherited_return = '    return source.replace("V17", "V20").replace("v17", "v20")\n'
    guarded_return = """    adapted = source.replace("V17", "V20").replace("v17", "v20")
    nested_main_guard = (
        '\\n\\nif __name__ == "__main__":\\n    raise SystemExit(main())\\n'
    )
    if adapted.count(nested_main_guard) != 1:
        raise RuntimeError("TASK7_V20_V17_SIGNED_CENSUS_CLI_INVALID")
    return adapted.replace(nested_main_guard, "", 1)
"""
    if source.count(inherited_return) != 1:
        raise RuntimeError("TASK7_V20_V19_SIGNED_CENSUS_CLI_INVALID")
    source = source.replace(inherited_return, guarded_return)
    return source


exec(  # noqa: S102 - exact hash-pinned predecessor wrapper adaptation.
    compile(
        _adapt_pinned_v19_cli(),
        str(_V19_SIGNED_CLI_PATH),
        "exec",
    ),
    globals(),
)

_SIGNED_CENSUS_CLI_ERROR = globals()["SignedCensusCliError"]
_GENERIC_EXECUTION_FAILURE: Final = "TASK7_V20_SIGNED_CENSUS_EXECUTION_FAILED"
_SAFE_CAUSAL_REASON_CODE = re.compile(r"TASK7_[A-Z0-9_]{1,128}")
_SAFE_SQLSTATE = re.compile(r"[0-9A-Z]{5}")


def _safe_causal_reason_code(error: BaseException) -> str:
    """Return only a closed-shape Task-7 code from the explicit cause chain."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and len(seen) < 16:
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        candidate = (
            current.args[0] if len(current.args) == 1 and isinstance(current.args[0], str) else ""
        )
        if (
            candidate != _GENERIC_EXECUTION_FAILURE
            and _SAFE_CAUSAL_REASON_CODE.fullmatch(candidate) is not None
        ):
            return candidate
        sqlstate = getattr(current, "sqlstate", None)
        if isinstance(sqlstate, str) and _SAFE_SQLSTATE.fullmatch(sqlstate) is not None:
            return f"TASK7_V20_POSTGRES_SQLSTATE_{sqlstate}"
        current = current.__cause__
    return _GENERIC_EXECUTION_FAILURE


def _phase_failure_code(error: BaseException, fallback: str) -> str:
    reason_code = _safe_causal_reason_code(error)
    return fallback if reason_code == _GENERIC_EXECUTION_FAILURE else reason_code


def _execute(output_argument: Path) -> dict[str, Any]:
    """Run each proof stage behind a distinct, secret-safe failure boundary."""

    output = globals()["_validate_output"](output_argument)
    runner = globals()["_load_approved_runner"]()
    artifact_raw, artifact = globals()["_load_candidate"](runner)
    material = globals()["_load_operator_material"]()
    try:
        result = runner.run_pre_fixture_census(
            artifact_path=globals()["_ARTIFACT_PATH"],
            base_schema_revision=runner.BASE_SCHEMA_REVISION,
            harness_identity=runner.HARNESS_IDENTITY,
            ordered_authority_target_tuple=runner.artifact_targets(artifact),
            ordered_trigger_contract_coordinate_tuple=runner.artifact_trigger_contract(artifact),
            production_constructor_spec_tuple=runner._PRODUCTION_CONSTRUCTOR_SPECS,
        )
    except Exception as exc:
        raise _SIGNED_CENSUS_CLI_ERROR(
            _phase_failure_code(exc, "TASK7_V20_CENSUS_EXECUTION_FAILED")
        ) from exc
    try:
        receipt = runner.build_and_verify_signed_receipt(
            signer_id=material.signer_id,
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=result,
            private_key=material.private_key,
            trusted_public_key_raw=material.public_key_raw,
        )
        rendered = (
            json.dumps(
                receipt,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    except Exception as exc:
        raise _SIGNED_CENSUS_CLI_ERROR(
            _phase_failure_code(exc, "TASK7_V20_RECEIPT_BUILD_FAILED")
        ) from exc
    try:
        runner.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=material.public_key_raw,
        )
    except Exception as exc:
        raise _SIGNED_CENSUS_CLI_ERROR(
            _phase_failure_code(exc, "TASK7_V20_RECEIPT_VERIFY_FAILED")
        ) from exc
    globals()["_write_exclusive"](output, rendered)
    return {
        "schema_version": globals()["_EVENT_SCHEMA"],
        "result": "PASS",
        "receipt_sha256": hashlib.sha256(rendered).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
