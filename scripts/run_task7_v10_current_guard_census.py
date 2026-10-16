"""Execute and sign the full-strict-v10 pre-freeze PostgreSQL census.

The incomplete historical v9 execution engine is reused only as a hash-pinned
mechanical source and replayed through an authenticated, finite v10 projection.
This keeps the historical runner immutable while
changing every evidence-domain/version/error literal and both imported v10
composition seams.  The sole retained historical import supplies production-
constructed payload fixtures; its module name never enters evidence.

This harness starts from revision 0037, installs artifact SQL only, and never
imports or claims migration 0038.  Its public API does not freeze the artifact,
write the repository fixture, or write an operator receipt.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import struct
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v10-current-guard-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v10-current-guard-census-result-v1"

_HISTORICAL_RUNNER_SHA256: Final = (
    "8954f62c37d31089aab50c6506d2c1273ae938c3982c49fd64b3b8cbdd30869d"
)
_V10_GENERATOR_PATH = Path(__file__).with_name("generate_task7_v10_contract_fixture.py")
_HISTORICAL_RUNNER_PATH = Path(__file__).with_name("run_task7_v9_current_guard_census.py")


class CensusError(RuntimeError):
    """Secret-safe v10 census admission failure."""


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CensusError(f"TASK7_V10_CENSUS_MODULE_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_v10_execution_engine() -> ModuleType:
    raw = _HISTORICAL_RUNNER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _HISTORICAL_RUNNER_SHA256:
        raise CensusError("TASK7_V10_HISTORICAL_CENSUS_ENGINE_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CensusError("TASK7_V10_HISTORICAL_CENSUS_ENGINE_INVALID") from exc

    constructor_run_seed_v9 = (
        '"INSERT INTO runs (run_id,tenant_id,deal_id,mode,status) "\n'
        "            \"VALUES (:run_id,:tenant_id,:deal_id,'FULL','RUNNING')\""
    )
    constructor_run_seed_v10 = (
        '"INSERT INTO runs "\n'
        '            "(run_id,tenant_id,deal_id,mode,status,lease_owner_id,"\n'
        '            "lease_fencing_token,lease_expires_at,last_heartbeat_at) "\n'
        "            \"VALUES (:run_id,:tenant_id,:deal_id,'FULL','RUNNING',\"\n"
        "            \"'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,1,\"\n"
        "            \"now()+interval '1 hour',now())\""
    )
    unconstrained_constraint_scan_v9 = (
        "                    WHERE n.nspname='public' AND t.relname=:table_name\n"
        "                    ORDER BY c.conname"
    )
    non_guard_constraint_scan_v10 = (
        "                    WHERE n.nspname='public' AND t.relname=:table_name\n"
        "                      AND NOT EXISTS (\n"
        "                        SELECT 1 FROM pg_catalog.pg_trigger AS guard_trigger\n"
        "                        WHERE guard_trigger.tgconstraint=c.oid\n"
        "                          AND NOT guard_trigger.tgisinternal\n"
        "                      )\n"
        "                    ORDER BY c.conname"
    )
    native_root_seed_v9 = (
        "                **graph,\n"
        '                "run_output_object_id": graph["output_object_id"],\n'
        "            },"
    )
    native_root_seed_v10 = (
        "                **graph,\n"
        '                "run_output_object_id": graph["output_object_id"],\n'
        "                **_v10_native_seed_values(native_table),\n"
        "            },"
    )
    replacements = (
        (constructor_run_seed_v9, constructor_run_seed_v10),
        (unconstrained_constraint_scan_v9, non_guard_constraint_scan_v10),
        (native_root_seed_v9, native_root_seed_v10),
        (
            "from scripts._task7_v9_census_scaffold import",
            "from scripts._task7_v10_census_scaffold import",
        ),
        (
            'Path(__file__).with_name("generate_task7_v9_contract_fixture.py")',
            'Path(__file__).with_name("generate_task7_v10_contract_fixture.py")',
        ),
        ("_task7_v9_census_generator", "_task7_v10_census_generator"),
        ("full-strict-v9", "full-strict-v10"),
        ("task7-v9", "task7-v10"),
        ("TASK7_V9", "TASK7_V10"),
        ("Task 7 v9", "Task 7 v10"),
    )
    for old, new in replacements:
        if old not in source:
            raise CensusError(f"TASK7_V10_HISTORICAL_PROJECTION_TOKEN_MISSING:{old}")
        source = source.replace(old, new)

    # The fixture module supplies values produced by real product constructors.
    # Its historical filename is intentionally retained and cannot enter the
    # result/receipt.  Every other lower-case v9 label must be gone.
    allowed_fixture_import = "test_full_strict_v9_native_schema_red"
    scrubbed = source.replace(allowed_fixture_import, "")
    if "v9" in scrubbed.lower():
        raise CensusError("TASK7_V10_HISTORICAL_LABEL_PROJECTION_INCOMPLETE")

    spec = importlib.util.spec_from_loader(
        "_task7_v10_current_guard_census_engine",
        loader=None,
        origin=str(Path(__file__).resolve()),
    )
    if spec is None:
        raise CensusError("TASK7_V10_CENSUS_ENGINE_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(Path(__file__).resolve())
    module.__package__ = ""
    sys.modules[spec.name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    if (
        module.HARNESS_IDENTITY != HARNESS_IDENTITY
        or module.BASE_SCHEMA_REVISION != BASE_SCHEMA_REVISION
        or module.RESULT_SCHEMA != RESULT_SCHEMA
    ):
        raise CensusError("TASK7_V10_CENSUS_ENGINE_IDENTITY_MISMATCH")
    return module


_ENGINE = _load_v10_execution_engine()
_GENERATOR = _load_module(
    _V10_GENERATOR_PATH,
    "_task7_v10_census_receipt_generator",
)


def _normalize_census_digest_value(value: Any) -> Any:
    """Project runtime values into the artifact's scalar-only hash domain."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CensusError("TASK7_V10_CENSUS_NONFINITE_BINARY64")
        normalized = 0.0 if value == 0.0 else value
        return ["binary64", struct.pack("!d", normalized).hex()]
    if isinstance(value, Mapping):
        return {str(key): _normalize_census_digest_value(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_normalize_census_digest_value(child) for child in value]
    raise CensusError(f"TASK7_V10_CENSUS_DIGEST_VALUE_INVALID:{type(value).__name__}")


def _engine_canonical_bytes(value: Any) -> bytes:
    canonical = _GENERATOR._canonical_bytes
    return cast(bytes, canonical(_normalize_census_digest_value(value)))


# The historical runner predates the scalar-only artifact canonicalizer and
# hashes runtime tuples/binary64 values.  Bind it to the reviewed projection
# explicitly; receipt payloads contain no floats, so signing stays byte-exact
# with the v10 verifier.
_ENGINE._canonical_bytes = _engine_canonical_bytes


def _v10_native_seed_values(native_table: str) -> dict[str, Any]:
    """Supply discriminator columns that are intentionally not generated."""

    if native_table == "deterministic_calculations":
        return {
            "producer_step_name": "METHODOLOGY_DETERMINISTIC_CALCULATION",
            "run_output_object_kind": "METHODOLOGY_CALCULATION",
            "coverage_record_id": "task7-v10-census-coverage",
            "methodology_question_record_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }
    if native_table == "vector_embeddings":
        return {
            "producer_step_name": "EXTRACT",
            "embedding_namespace": "internal_evidence",
            "run_output_object_kind": "EXTRACT_RAG_EMBEDDING",
        }
    if native_table == "muhasabah_records":
        return {
            "source_step": "METHODOLOGY_EVIDENCE_TRUST_COURT",
            "run_output_object_kind": "COURT_MUHASABAH",
        }
    return {}


_ENGINE._v10_native_seed_values = _v10_native_seed_values


def _require_isolated_admin_url() -> None:
    if not os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip():
        raise CensusError("TASK7_V10_POSTGRES_ADMIN_URL_REQUIRED")


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Run 73×3 current-guard cases and four constructors over 0037."""

    _require_isolated_admin_url()
    result = _ENGINE.run_pre_fixture_census(
        artifact_path=artifact_path,
        base_schema_revision=base_schema_revision,
        harness_identity=harness_identity,
        ordered_authority_target_tuple=ordered_authority_target_tuple,
        production_constructor_spec_tuple=production_constructor_spec_tuple,
    )
    rendered = json.dumps(result, allow_nan=False, sort_keys=True)
    if "v9" in rendered.lower() or "migration_revision" in result:
        raise CensusError("TASK7_V10_CENSUS_RESULT_PROVENANCE_INVALID")
    return dict(result)


def build_and_verify_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Issue and immediately verify an artifact-bound v10 census receipt."""

    receipt = _ENGINE._build_signed_receipt(
        signer_id=signer_id,
        artifact_raw=artifact_raw,
        artifact=artifact,
        result=result,
        private_key=private_key,
        public_key_raw=trusted_public_key_raw,
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
    verify_signed_receipt(
        receipt_raw=rendered,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    if "v9" in rendered.decode("utf-8").lower():
        raise CensusError("TASK7_V10_CENSUS_RECEIPT_PROVENANCE_INVALID")
    return dict(receipt)


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify a signed v10 receipt against the trusted Ed25519 key."""

    return dict(
        _GENERATOR.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Delegate the operator CLI to the projected v10 execution engine."""

    _require_isolated_admin_url()
    return int(_ENGINE.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CensusError",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "RESULT_SCHEMA",
    "build_and_verify_signed_receipt",
    "main",
    "run_pre_fixture_census",
    "verify_signed_receipt",
]
