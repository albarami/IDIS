"""Run and sign the approved v21 census through the pinned v20 wrapper.

The reviewed strict-profile, exclusive-write, disposable-database, and
Ed25519 mechanics are preserved.  This wrapper retargets only the pinned
runner, candidate, source-manifest digest, version labels, and output name.
It cannot freeze or publish the contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Final

_V20_SIGNED_CLI_PATH: Final = Path(__file__).with_name("run_task7_v20_signed_census.py")
_V20_SIGNED_CLI_SHA256: Final = "b12c429c7539fac88e981f05d72fe76087bf183a55ba91fb2075f7b59e695c96"
_V20_RUNNER_SHA256: Final = "823230359182e1a0b38483c96f268577bf6fd9f9a5a23d8b2dba1752ca94d217"
_V21_RUNNER_SHA256: Final = "d772e5a7341b36463ef130cd237b24744de481b1d2f0ec43d860143297228412"
_V21_CANDIDATE_SHA256: Final = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"
_V20_SOURCE_MANIFEST_SHA256: Final = (
    "9375b1cbbafafca1c3d4263c43d0f7a4fa32b2df66acc42bf3d00447600404f2"
)
_V21_SOURCE_MANIFEST_SHA256: Final = (
    "2d6edf590a4b4ac439be408be69731f64e79c25af764d9c879e1f14fde6c371c"
)


def _adapt_pinned_v20_cli() -> str:
    raw = _V20_SIGNED_CLI_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V20_SIGNED_CLI_SHA256:
        raise RuntimeError("TASK7_V21_V20_SIGNED_CENSUS_CLI_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V21_V20_SIGNED_CENSUS_CLI_INVALID") from exc
    source = source.replace("V20", "V21").replace("v20", "v21")
    candidate_match = re.search(
        r'^_V21_CANDIDATE_SHA256: Final = "([0-9a-f]{64})"$',
        source,
        flags=re.MULTILINE,
    )
    if candidate_match is None:
        raise RuntimeError("TASK7_V21_V20_SIGNED_CENSUS_CLI_INVALID")
    predecessor_candidate_sha256 = candidate_match.group(1)
    for old, new in (
        (_V20_RUNNER_SHA256, _V21_RUNNER_SHA256),
        (predecessor_candidate_sha256, _V21_CANDIDATE_SHA256),
        (_V20_SOURCE_MANIFEST_SHA256, _V21_SOURCE_MANIFEST_SHA256),
    ):
        if source.count(old) != 1:
            raise RuntimeError("TASK7_V21_V20_SIGNED_CENSUS_CLI_INVALID")
        source = source.replace(old, new)
    return source


exec(  # noqa: S102 - exact hash-pinned predecessor wrapper adaptation.
    compile(
        _adapt_pinned_v20_cli(),
        str(_V20_SIGNED_CLI_PATH),
        "exec",
    ),
    globals(),
)

_SIGNED_CENSUS_CLI_ERROR = globals()["SignedCensusCliError"]
_GENERIC_EXECUTION_FAILURE: Final = "TASK7_V21_SIGNED_CENSUS_EXECUTION_FAILED"
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
            return f"TASK7_V21_POSTGRES_SQLSTATE_{sqlstate}"
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
            _phase_failure_code(exc, "TASK7_V21_CENSUS_EXECUTION_FAILED")
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
            _phase_failure_code(exc, "TASK7_V21_RECEIPT_BUILD_FAILED")
        ) from exc
    try:
        runner.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=material.public_key_raw,
        )
    except Exception as exc:
        raise _SIGNED_CENSUS_CLI_ERROR(
            _phase_failure_code(exc, "TASK7_V21_RECEIPT_VERIFY_FAILED")
        ) from exc
    globals()["_write_exclusive"](output, rendered)
    return {
        "schema_version": globals()["_EVENT_SCHEMA"],
        "result": "PASS",
        "receipt_sha256": hashlib.sha256(rendered).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
