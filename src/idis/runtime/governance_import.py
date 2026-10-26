"""Authenticated import of the two signed Slice100 governance documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never

from idis.runtime.governance_envelopes import (
    BUDGET_SCHEMA_VERSION,
    GOVERNANCE_SCHEMA_INVALID,
    GOVERNANCE_SIGNATURE_INVALID,
    RIGHTS_SCHEMA_VERSION,
    GovernanceEnvelopeError,
    verify_envelope,
)
from idis.runtime.secure_files import SecureFileError, secure_read_integrity_file
from idis.runtime.strict_auth import validate_strict_auth_binding


class GovernanceImportError(RuntimeError):
    """Safe typed failure from governance import admission."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GovernanceImportReport:
    """Secret-free durable import result."""

    may_proceed: bool
    tenant_id: str
    rights_rows: int
    budget_rows: int
    rights_payload_sha256: str
    budget_payload_sha256: str
    public_key_sha256: str
    rights_audit_event_id: str
    budget_audit_event_id: str
    rights_disposition: str
    budget_disposition: str
    operation_audit_event_id: str


class _DuplicateJsonMember(ValueError):
    pass


_MAX_PUBLIC_KEY_BYTES = 16 * 1024
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_LAUNCH_IDENTITY_FIELDS = frozenset({"base_profile_hmac", "command", "generation", "process_role"})


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonMember
        result[key] = value
    return result


def _load_json_object(path_value: str) -> Mapping[str, object]:
    path = Path(path_value)
    if not path.is_absolute() or not path.is_file():
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(_DuplicateJsonMember()),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateJsonMember) as exc:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID) from exc
    if not isinstance(parsed, Mapping):
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    return parsed


def _read_public_key(path_value: str) -> bytes:
    try:
        return secure_read_integrity_file(
            path_value,
            max_bytes=_MAX_PUBLIC_KEY_BYTES,
            require_readonly=True,
            require_secure_parent=True,
        )
    except SecureFileError as exc:
        raise GovernanceImportError(GOVERNANCE_SIGNATURE_INVALID) from exc


def _auth_or_raise(env: Mapping[str, str]) -> None:
    report = validate_strict_auth_binding(env)
    if report.may_proceed:
        return
    if "GOVERNANCE_ADMIN_UNAUTHORIZED" in report.reason_codes:
        raise GovernanceImportError("GOVERNANCE_ADMIN_UNAUTHORIZED")
    raise GovernanceImportError(
        report.reason_codes[0] if report.reason_codes else "AUTH_CONFIG_BLOCKED"
    )


def _require_governance_launch_identity(
    value: Mapping[str, object] | None,
) -> dict[str, object]:
    """Validate the secret-free identity of the reviewed governance launch."""
    if not isinstance(value, Mapping) or frozenset(value) != _LAUNCH_IDENTITY_FIELDS:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    base_profile_hmac = value.get("base_profile_hmac")
    if (
        not isinstance(base_profile_hmac, str)
        or _SHA256_HEX_RE.fullmatch(base_profile_hmac) is None
        or value.get("command") != "governance"
        or value.get("generation") not in {"bootstrap-v1", "acceptance-v1"}
        or value.get("process_role") is not None
    ):
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    return dict(value)


def _require_unexpired_rights_at_import(
    payload: Mapping[str, object],
    *,
    imported_at: datetime,
) -> None:
    """Reject rights whose signed effective interval is not current at import."""
    approvals = payload.get("approvals")
    if not isinstance(approvals, list):
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    for approval in approvals:
        if not isinstance(approval, Mapping):
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        raw_approved_at = approval.get("approved_at")
        if not isinstance(raw_approved_at, str) or not raw_approved_at.endswith("Z"):
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        try:
            approved_at = datetime.fromisoformat(raw_approved_at.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID) from exc
        if approved_at > imported_at:
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        if approval.get("status") == "REVOKED":
            raw_revoked_at = approval.get("revoked_at")
            if not isinstance(raw_revoked_at, str) or not raw_revoked_at.endswith("Z"):
                raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
            try:
                revoked_at = datetime.fromisoformat(raw_revoked_at.removesuffix("Z") + "+00:00")
            except ValueError as exc:
                raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID) from exc
            if revoked_at > imported_at:
                raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
            continue
        if approval.get("status") != "APPROVED":
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        raw_expires_at = approval.get("expires_at")
        if not isinstance(raw_expires_at, str) or not raw_expires_at.endswith("Z"):
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        try:
            expires_at = datetime.fromisoformat(raw_expires_at.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID) from exc
        if expires_at <= imported_at:
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)


def _audit_event(
    *,
    event_id: str,
    tenant_id: str,
    occurred_at: datetime,
    rights_hash: str,
    budget_hash: str,
    public_key_hash: str,
    rights_rows: int,
    budget_rows: int,
    rights_disposition: str,
    budget_disposition: str,
    rights_lineage_event_id: str,
    budget_lineage_event_id: str,
    rights_payload: Mapping[str, object],
    launch_identity: Mapping[str, object],
    previous_rights_payload: Mapping[str, object] | None = None,
    previous_rights_event_id: str | None = None,
) -> dict[str, object]:
    replay = rights_disposition == budget_disposition == "replayed"
    safe: dict[str, object] = {
        **launch_identity,
        "rights_schema_version": RIGHTS_SCHEMA_VERSION,
        "budget_schema_version": BUDGET_SCHEMA_VERSION,
        "rights_rows": rights_rows,
        "budget_rows": budget_rows,
        "rights_disposition": rights_disposition,
        "budget_disposition": budget_disposition,
        "rights_audit_event_id": rights_lineage_event_id,
        "budget_audit_event_id": budget_lineage_event_id,
        "rights_payload": dict(rights_payload),
    }
    hashes = [
        f"sha256:{rights_hash}",
        f"sha256:{budget_hash}",
        f"sha256:{public_key_hash}",
    ]
    refs = [
        f"rights_audit_event_id:{rights_lineage_event_id}",
        f"budget_audit_event_id:{budget_lineage_event_id}",
    ]
    if previous_rights_payload is not None:
        if previous_rights_event_id is None:
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        from idis.runtime.governance_envelopes import canonicalize_json

        prior_payload = dict(previous_rights_payload)
        prior_hash = hashlib.sha256(canonicalize_json(prior_payload)).hexdigest()
        safe["previous_rights_payload"] = prior_payload
        hashes.append(f"sha256:{prior_hash}")
        refs.append(f"rights_previous_import_audit_event_id:{previous_rights_event_id}")
    event: dict[str, object] = {
        "event_id": event_id,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "tenant_id": tenant_id,
        "event_type": (
            "integration.governance.replayed" if replay else "integration.governance.imported"
        ),
        "severity": "HIGH",
        "summary": (
            "Signed rights and provider budget governance replay verified"
            if replay
            else "Signed rights and provider budget governance imported"
        ),
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": "slice100-governance-admin",
            "roles": ["ADMIN"],
        },
        "request": {
            "request_id": f"governance-{event_id[:8]}",
            "method": "POST",
            "path": "/internal/governance/import",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "integration",
            "resource_id": event_id,
        },
        "payload": {
            "safe": safe,
            "hashes": hashes,
            "refs": refs,
        },
    }
    from idis.validators.audit_event_validator import validate_audit_event

    validation = validate_audit_event(event)
    if not validation.passed:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    return event


def import_governance_documents(
    *,
    env: Mapping[str, str],
    connection: Any,
    rights_repository: Any | None = None,
    budget_repository: Any | None = None,
    audit_sink: Any | None = None,
    now: datetime | None = None,
    expected_public_key_sha256: str | None = None,
    launch_identity: Mapping[str, object] | None = None,
) -> GovernanceImportReport:
    """Verify and import both documents in one caller-owned PostgreSQL transaction."""
    reviewed_launch_identity = _require_governance_launch_identity(launch_identity)
    _auth_or_raise(env)
    tenant_id = str(env.get("IDIS_ACCEPTANCE_TENANT_ID", ""))
    trusted_signer_id = str(env.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", ""))
    public_pem = _read_public_key(
        str(env.get("IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH", ""))
    )
    opened_public_key_sha256 = hashlib.sha256(public_pem).hexdigest()
    if expected_public_key_sha256 is not None and (
        len(expected_public_key_sha256) != 64
        or not secrets.compare_digest(opened_public_key_sha256, expected_public_key_sha256)
    ):
        raise GovernanceImportError(GOVERNANCE_SIGNATURE_INVALID)
    rights_document = _load_json_object(str(env.get("IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH", "")))
    budget_document = _load_json_object(str(env.get("IDIS_PROVIDER_BUDGET_POLICY_PATH", "")))
    rights_verified = verify_envelope(
        envelope=rights_document,
        trusted_signer_id=trusted_signer_id,
        trusted_public_key_pem=public_pem,
    )
    budget_verified = verify_envelope(
        envelope=budget_document,
        trusted_signer_id=trusted_signer_id,
        trusted_public_key_pem=public_pem,
    )
    if (
        rights_verified.schema_version != RIGHTS_SCHEMA_VERSION
        or budget_verified.schema_version != BUDGET_SCHEMA_VERSION
        or rights_verified.payload.get("tenant_id") != tenant_id
        or budget_verified.payload.get("tenant_id") != tenant_id
    ):
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)

    import_time = now or datetime.now(UTC)
    if import_time.tzinfo is None:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    imported_at = import_time.astimezone(UTC)
    _require_unexpired_rights_at_import(rights_verified.payload, imported_at=imported_at)

    if rights_repository is None:
        from idis.persistence.repositories.enrichment_rights_approvals import (
            PostgresEnrichmentRightsApprovalsRepository,
        )

        rights_repository = PostgresEnrichmentRightsApprovalsRepository(connection, tenant_id)
    if budget_repository is None:
        from idis.persistence.repositories.provider_budget_policies import (
            PostgresProviderBudgetPoliciesRepository,
        )

        budget_repository = PostgresProviderBudgetPoliciesRepository(connection, tenant_id)
    if audit_sink is None:
        from idis.audit.postgres_sink import PostgresAuditSink

        audit_sink = PostgresAuditSink()
    if (
        getattr(rights_repository, "is_durable", False) is not True
        or getattr(budget_repository, "is_durable", False) is not True
        or not callable(getattr(audit_sink, "emit_in_tx", None))
    ):
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)

    event_id = str(uuid.uuid4())
    rights_rows = rights_repository.import_verified(
        verified_envelope=rights_verified,
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=event_id,
        imported_at=imported_at,
    )
    budget_rows = budget_repository.import_verified(
        verified_envelope=budget_verified,
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=event_id,
        imported_at=imported_at,
    )
    if rights_rows != 4 or budget_rows != 17:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    rights_was_new = bool(getattr(rights_repository, "last_import_was_new", True))
    budget_was_new = bool(getattr(budget_repository, "last_import_was_new", True))
    rights_lineage = str(getattr(rights_repository, "last_import_audit_event_id", None) or event_id)
    budget_lineage = str(getattr(budget_repository, "last_import_audit_event_id", None) or event_id)
    rights_disposition = str(
        getattr(
            rights_repository,
            "last_import_disposition",
            "created" if rights_was_new else "replayed",
        )
    )
    budget_disposition = str(
        getattr(
            budget_repository,
            "last_import_disposition",
            "created" if budget_was_new else "replayed",
        )
    )
    allowed_dispositions = {"created", "transitioned", "replayed"}
    if (
        rights_disposition not in allowed_dispositions
        or budget_disposition not in allowed_dispositions
        or (rights_disposition != "replayed" and rights_lineage != event_id)
        or (budget_disposition != "replayed" and budget_lineage != event_id)
    ):
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    previous_rights_payload = getattr(rights_repository, "last_previous_payload", None)
    previous_rights_event_id = getattr(
        rights_repository, "last_previous_import_audit_event_id", None
    )
    if rights_disposition == "transitioned":
        if not isinstance(previous_rights_payload, Mapping) or not isinstance(
            previous_rights_event_id, str
        ):
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    elif previous_rights_payload is not None or previous_rights_event_id is not None:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
    event = _audit_event(
        event_id=event_id,
        tenant_id=tenant_id,
        occurred_at=imported_at,
        rights_hash=rights_verified.payload_sha256,
        budget_hash=budget_verified.payload_sha256,
        public_key_hash=rights_verified.public_key_sha256,
        rights_rows=rights_rows,
        budget_rows=budget_rows,
        rights_disposition=rights_disposition,
        budget_disposition=budget_disposition,
        rights_lineage_event_id=rights_lineage,
        budget_lineage_event_id=budget_lineage,
        rights_payload=rights_verified.payload,
        launch_identity=reviewed_launch_identity,
        previous_rights_payload=previous_rights_payload,
        previous_rights_event_id=previous_rights_event_id,
    )
    audit_sink.emit_in_tx(connection, event)
    return GovernanceImportReport(
        may_proceed=True,
        tenant_id=tenant_id,
        rights_rows=rights_rows,
        budget_rows=budget_rows,
        rights_payload_sha256=rights_verified.payload_sha256,
        budget_payload_sha256=budget_verified.payload_sha256,
        public_key_sha256=rights_verified.public_key_sha256,
        rights_audit_event_id=rights_lineage,
        budget_audit_event_id=budget_lineage,
        rights_disposition=rights_disposition,
        budget_disposition=budget_disposition,
        operation_audit_event_id=event_id,
    )


class _GovernanceArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)


def _parser() -> argparse.ArgumentParser:
    parser = _GovernanceArgumentParser(add_help=False)
    parser.add_argument("--admin-api-key-env", required=True)
    parser.add_argument("--trusted-signer-id-env", required=True)
    parser.add_argument("--trusted-ed25519-key-env", required=True)
    parser.add_argument("--import-rights-approvals-env", required=True)
    parser.add_argument("--import-budget-policy-env", required=True)
    parser.add_argument("--no-secret-output", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    strict_profile: Any | None = None,
) -> int:
    """Run the profile-loaded governance command with safe output only."""
    try:
        args = _parser().parse_args(argv)
        required_bindings = {
            args.admin_api_key_env: "IDIS_GOVERNANCE_ADMIN_API_KEY",
            args.trusted_signer_id_env: "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID",
            args.trusted_ed25519_key_env: "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
            args.import_rights_approvals_env: "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH",
            args.import_budget_policy_env: "IDIS_PROVIDER_BUDGET_POLICY_PATH",
        }
        if any(actual != expected for actual, expected in required_bindings.items()):
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)
        if not args.no_secret_output:
            raise GovernanceImportError(GOVERNANCE_SCHEMA_INVALID)

        from idis.persistence.db import begin_app_conn
        from idis.runtime.profile import StrictProfileError, require_runtime_profile

        try:
            reviewed_profile = require_runtime_profile(strict_profile, command="governance")
        except StrictProfileError as exc:
            raise GovernanceImportError(exc.code) from exc

        with begin_app_conn() as connection:
            report = import_governance_documents(
                env=reviewed_profile.values,
                connection=connection,
                expected_public_key_sha256=reviewed_profile.governance_public_key_sha256,
                launch_identity={
                    "base_profile_hmac": reviewed_profile.base_profile_hmac,
                    "command": reviewed_profile.command,
                    "generation": reviewed_profile.generation,
                    "process_role": reviewed_profile.process_role,
                },
            )
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, (GovernanceImportError, GovernanceEnvelopeError))
            else GOVERNANCE_SCHEMA_INVALID
        )
        print(
            json.dumps({"may_proceed": False, "code": code}, separators=(",", ":")),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "GovernanceImportError",
    "GovernanceImportReport",
    "import_governance_documents",
    "main",
]
