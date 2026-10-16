"""Execute and sign the full-strict-v7 PostgreSQL authority census.

This operator-backed tool is intentionally separate from the hermetic contract
hash generator. It creates one disposable PostgreSQL database per authority
group, executes every hash-bound authority body, records only safe aggregate
execution facts, and signs the artifact-bound receipt with Ed25519.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from tests.slice100_task2b._support import disposable_postgres  # noqa: E402
from tests.slice100_task7 import (  # noqa: E402
    test_sql_authority_guards_postgres_red as guards,
)
from tests.slice100_task7._postgres_support import (  # noqa: E402
    provision_exact_erasure_principals,
)

_CENSUS_ENVELOPE_SCHEMA = "task7-sql-authority-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-sql-authority-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-sql-authority-postgres-census-signable-v1"


class CensusError(RuntimeError):
    """Secret-safe operator census failure."""


def _load_generator() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v7_contract_fixture.py")
    spec = importlib.util.spec_from_file_location(
        "_task7_v7_census_generator",
        path,
    )
    if spec is None or spec.loader is None:
        raise CensusError("TASK7_V7_CENSUS_GENERATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_GENERATOR = _load_generator()


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _GENERATOR._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_authority_inventory(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Expose the generator-owned closed inventory to the operator runner."""

    return cast(
        dict[str, Any],
        _GENERATOR.derive_authority_inventory(artifact),
    )


def _schema_by_authority() -> dict[str, tuple[str, ...] | None]:
    return {
        "dashboard-root-equation-v1": guards._DASHBOARD_SCHEMA,
        "graph-rag-namespace-v1": guards._NAMESPACE_SCHEMA,
        "rag-query-parent-coordinate-v1": guards._RAG_PARENT_SCHEMA,
        "layer2-readiness-v1": guards._READINESS_SCHEMA,
        "extract-rag-context-coordinate-v1": guards._EXTRACT_SCHEMA,
        "debate-muhasabah-exact-one-v1": guards._muhasabah_schema(
            "debate_role_outputs",
            "debate_role_output_id",
            "DEBATE",
            "DEBATE_MUHASABAH",
        ),
        "analysis-muhasabah-exact-one-v1": guards._muhasabah_schema(
            "analysis_agent_outputs",
            "analysis_output_id",
            "ANALYSIS",
            "ANALYSIS_MUHASABAH",
        ),
        "layer2-muhasabah-exact-one-v1": guards._muhasabah_schema(
            "layer2_role_outputs",
            "layer2_role_output_id",
            "LAYER2_IC_CHALLENGE",
            "LAYER2_MUHASABAH",
        ),
        "grading-dispatch-and-consistency-v1": guards._GRADING_SCHEMA,
        "scoring-binary64-finite-v1": (guards._NEGATIVE_ZERO_BYPASS_SCHEMA),
        "scoring-binary64-negative-zero-v1": (guards._NEGATIVE_ZERO_BYPASS_SCHEMA),
        "approved-erasure-append-only-v1": None,
        "native-root-exact-one-v1": guards._NATIVE_ROOT_SCHEMA,
        "identity-identifier-projection-v1": (guards._IDENTITY_PROJECTION_SCHEMA),
        "scoring-dimension-projection-v1": (guards._SCORING_PROJECTION_SCHEMA),
        "generic-claim-projection-v1": guards._GENERIC_SCHEMA,
        "generic-evidence-projection-v1": guards._GENERIC_SCHEMA,
    }


def _load_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    artifact = _GENERATOR._decode_artifact(raw)
    _GENERATOR._validate_shape(raw, artifact)
    return raw, cast(dict[str, Any], artifact)


def _ensure_admin_url() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip():
        return
    dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not dotenv_path:
        raise CensusError("TASK7_V7_POSTGRES_ADMIN_URL_REQUIRED")
    try:
        from idis.runtime.profile import _parse_dotenv

        values = _parse_dotenv(dotenv_path)
        admin_url = values["IDIS_DATABASE_ADMIN_URL"]
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise CensusError("TASK7_V7_POSTGRES_ADMIN_URL_REQUIRED") from exc
    if not admin_url.strip():
        raise CensusError("TASK7_V7_POSTGRES_ADMIN_URL_REQUIRED")
    os.environ["IDIS_DATABASE_ADMIN_URL"] = admin_url


def _sqlstate(error: DBAPIError) -> str:
    original = error.orig
    value = getattr(original, "sqlstate", None) or getattr(
        original,
        "pgcode",
        None,
    )
    return str(value or "UNKNOWN")


def _execute_census(
    *,
    artifact_path: Path,
    artifact: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    schemas = _schema_by_authority()
    authority_rows = artifact["sql_authority_registry"]["rows"]
    authority_ids = tuple(row["authority_id"] for row in authority_rows)
    if tuple(schemas) != authority_ids:
        raise CensusError("TASK7_V7_CENSUS_SCHEMA_COVERAGE_MISMATCH")

    guards._ARTIFACT = artifact_path
    server_versions: set[int] = set()
    executed_groups = 0
    executed_statements = 0
    for row in authority_rows:
        authority_id = row["authority_id"]
        try:
            with disposable_postgres() as database:
                with database.admin_engine.begin() as connection:
                    server_versions.add(
                        int(
                            connection.execute(
                                text("SELECT current_setting('server_version_num')::integer")
                            ).scalar_one()
                        )
                    )
                    if authority_id == ("approved-erasure-append-only-v1"):
                        provision_exact_erasure_principals(connection)
                schema = schemas[authority_id]
                if schema is None:
                    guards._install_erasure_append_only_guard(database)
                else:
                    guards._install(
                        database,
                        authority_id,
                        schema,
                    )
                executed_groups += 1
                executed_statements += len(row["body_sql_utf8_hex_tuple"])
        except DBAPIError as exc:
            raise CensusError(
                "TASK7_V7_POSTGRES_AUTHORITY_REJECTED:"
                f"{row['authority_ordinal']}:{authority_id}:"
                f"SQLSTATE={_sqlstate(exc)}"
            ) from exc
    if (
        executed_groups != inventory["authority_row_count"]
        or executed_statements != inventory["body_statement_count"]
        or len(server_versions) != 1
    ):
        raise CensusError("TASK7_V7_POSTGRES_CENSUS_INCOMPLETE")
    return {
        "database_product": "PostgreSQL",
        "server_version_num": server_versions.pop(),
        "disposable_database_count": executed_groups,
        "executed_authority_group_count": executed_groups,
        "executed_statement_count": executed_statements,
        "database_rejection_count": 0,
    }


def _load_keypair(
    *,
    private_key_path: Path,
    public_key_path: Path,
) -> tuple[Ed25519PrivateKey, bytes]:
    private_raw = private_key_path.read_bytes()
    public_raw = public_key_path.read_bytes()
    private_key = serialization.load_pem_private_key(
        private_raw,
        password=None,
    )
    public_key = serialization.load_pem_public_key(public_raw)
    if not isinstance(private_key, Ed25519PrivateKey) or not isinstance(
        public_key,
        Ed25519PublicKey,
    ):
        raise CensusError("TASK7_V7_CENSUS_SIGNING_KEY_INVALID")
    derived_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    trusted_public = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if derived_public != trusted_public:
        raise CensusError("TASK7_V7_CENSUS_SIGNING_KEY_MISMATCH")
    return private_key, public_raw


def _build_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    database_execution: dict[str, Any],
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
) -> dict[str, Any]:
    payload = {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": (artifact["metadata"]["pipeline_contract_version"]),
        "artifact_sha256": _sha256(artifact_raw),
        "authority_inventory": inventory,
        "database_execution": database_execution,
    }
    payload_sha256 = _sha256(_canonical_bytes(payload))
    public_key_sha256 = _sha256(public_key_raw)
    signable = [
        _CENSUS_SIGNING_DOMAIN,
        signer_id,
        payload_sha256,
        public_key_sha256,
        payload,
    ]
    signature = private_key.sign(_canonical_bytes(signable))
    return {
        "schema_version": _CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _render(receipt: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            receipt,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise CensusError("TASK7_V7_CENSUS_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise CensusError("TASK7_V7_CENSUS_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--signing-private-key", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    parser.add_argument("--signer-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if not args.signer_id.strip():
            raise CensusError("TASK7_V7_CENSUS_SIGNER_ID_INVALID")
        _ensure_admin_url()
        artifact_raw, artifact = _load_artifact(args.artifact)
        inventory = derive_authority_inventory(artifact)
        execution = _execute_census(
            artifact_path=args.artifact.resolve(),
            artifact=artifact,
            inventory=inventory,
        )
        private_key, public_key_raw = _load_keypair(
            private_key_path=args.signing_private_key,
            public_key_path=args.trusted_public_key,
        )
        receipt = _build_signed_receipt(
            signer_id=args.signer_id,
            artifact_raw=artifact_raw,
            artifact=artifact,
            inventory=inventory,
            database_execution=execution,
            private_key=private_key,
            public_key_raw=public_key_raw,
        )
        rendered = _render(receipt)
        _GENERATOR.verify_postgres_census_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )
        _write_exclusive(args.output, rendered)
    except (
        CensusError,
        DBAPIError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "TASK7_V7_POSTGRES_CENSUS_PASSED:"
        f"authorities={execution['executed_authority_group_count']}:"
        f"statements={execution['executed_statement_count']}:"
        f"receipt_sha256={_sha256(rendered)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
