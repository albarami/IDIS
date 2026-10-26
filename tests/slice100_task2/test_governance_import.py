"""RED contracts for the authenticated, durable governance import command."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import stat
import subprocess
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.slice100_task2._support import (
    ACCEPTANCE_TENANT_ID,
    BUDGET_SCHEMA_VERSION,
    EXPECTED_BUDGET_COMPONENT_COUNT,
    RIGHTS_SCHEMA_VERSION,
    SIGNER_ID,
    budget_payload,
    error_code,
    rights_payload,
    safe_render,
    strict_auth_env,
)

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
LAUNCH_IDENTITY: dict[str, object] = {
    "base_profile_hmac": "d" * 64,
    "command": "governance",
    "generation": "bootstrap-v1",
    "process_role": None,
}


class _DurableRepository:
    is_durable = True

    def __init__(
        self,
        expected_schema: str,
        imported_count: int,
        *,
        replay_import_audit_event_id: str | None = None,
        disposition: str = "created",
        previous_rights_payload: dict[str, object] | None = None,
        previous_import_audit_event_id: str | None = None,
    ) -> None:
        self.expected_schema = expected_schema
        self.imported_count = imported_count
        self.calls: list[dict[str, Any]] = []
        if replay_import_audit_event_id is not None:
            disposition = "replayed"
            previous_import_audit_event_id = replay_import_audit_event_id
        self.disposition = disposition
        self.configured_lineage = previous_import_audit_event_id
        self.previous_rights_payload = previous_rights_payload

    def import_verified(
        self,
        *,
        verified_envelope: object,
        imported_by_actor_id: str,
        import_audit_event_id: str,
        imported_at: datetime,
    ) -> int:
        schema = verified_envelope.schema_version  # type: ignore[attr-defined]
        assert schema == self.expected_schema
        self.calls.append(
            {
                "verified_envelope": verified_envelope,
                "imported_by_actor_id": imported_by_actor_id,
                "import_audit_event_id": import_audit_event_id,
                "imported_at": imported_at,
            }
        )
        self.last_import_disposition = self.disposition
        if self.disposition == "replayed":
            assert self.configured_lineage is not None
            self.last_import_audit_event_id = self.configured_lineage
            self.last_import_was_new = False
        else:
            self.last_import_audit_event_id = import_audit_event_id
            self.last_import_was_new = True
        if self.disposition == "transitioned":
            assert self.configured_lineage is not None
            assert self.previous_rights_payload is not None
            self.last_previous_import_audit_event_id = self.configured_lineage
            self.last_previous_payload = self.previous_rights_payload
        return self.imported_count


class _NonDurableRepository(_DurableRepository):
    is_durable = False


class _AuditSink:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def emit_in_tx(self, connection: object, event: dict[str, object]) -> None:
        self.calls.append((connection, copy.deepcopy(event)))


def _signed_inputs(tmp_path: Path) -> tuple[dict[str, str], bytes, bytes]:
    envelope_module = importlib.import_module("idis.runtime.governance_envelopes")
    signing_module = importlib.import_module("idis.tools.governance_signing")
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "trusted-public.pem"
    signing_module.generate_ed25519_keypair(
        private_key_path=private_path,
        public_key_path=public_path,
    )
    private_pem = private_path.read_bytes()
    public_pem = public_path.read_bytes()
    rights_envelope = envelope_module.sign_envelope(
        schema_version=RIGHTS_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    budget_envelope = envelope_module.sign_envelope(
        schema_version=BUDGET_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=budget_payload(),
        private_key_pem=private_pem,
    )
    rights_path = tmp_path / "rights-envelope.json"
    budget_path = tmp_path / "budget-envelope.json"
    rights_path.write_text(json.dumps(rights_envelope), encoding="utf-8")
    budget_path.write_text(json.dumps(budget_envelope), encoding="utf-8")
    env = strict_auth_env()
    env.update(
        {
            "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": SIGNER_ID,
            "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_path),
            "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH": str(rights_path),
            "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(budget_path),
            "IDIS_PROVIDER_BUDGET_CURRENCY": "USD",
        }
    )
    return env, private_pem, public_pem


def _import(
    *,
    env: dict[str, str],
    rights_repository: object,
    budget_repository: object,
    audit_sink: object,
    connection: object,
    expected_public_key_sha256: str | None = None,
) -> object:
    module = importlib.import_module("idis.runtime.governance_import")
    return module.import_governance_documents(
        env=env,
        connection=connection,
        rights_repository=rights_repository,
        budget_repository=budget_repository,
        audit_sink=audit_sink,
        now=NOW,
        expected_public_key_sha256=expected_public_key_sha256,
        launch_identity=LAUNCH_IDENTITY,
    )


def test_governance_import_verifies_both_documents_and_commits_one_audited_unit(
    tmp_path: Path,
) -> None:
    env, private_pem, public_pem = _signed_inputs(tmp_path)
    rights_repository = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget_repository = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit_sink = _AuditSink()
    connection = object()

    report = _import(
        env=env,
        rights_repository=rights_repository,
        budget_repository=budget_repository,
        audit_sink=audit_sink,
        connection=connection,
    )

    assert report.may_proceed is True
    assert report.rights_rows == 4
    assert report.budget_rows == EXPECTED_BUDGET_COMPONENT_COUNT
    assert report.tenant_id == ACCEPTANCE_TENANT_ID
    assert len(rights_repository.calls) == len(budget_repository.calls) == 1
    assert len(audit_sink.calls) == 1
    assert audit_sink.calls[0][0] is connection
    event = audit_sink.calls[0][1]
    from idis.validators.audit_event_validator import validate_audit_event

    validation = validate_audit_event(event)
    assert validation.passed, [error.code for error in validation.errors]
    assert event["event_type"] == "integration.governance.imported"
    assert event["tenant_id"] == ACCEPTANCE_TENANT_ID
    safe = event["payload"]["safe"]
    assert {name: safe[name] for name in LAUNCH_IDENTITY} == LAUNCH_IDENTITY
    assert rights_repository.calls[0]["import_audit_event_id"] == event["event_id"]
    assert budget_repository.calls[0]["import_audit_event_id"] == event["event_id"]
    rendered = safe_render(report) + json.dumps(event, sort_keys=True)
    for secret in (
        private_pem.decode("ascii"),
        public_pem.decode("ascii"),
        env["IDIS_API_KEYS_JSON"],
        env["IDIS_REAL_EXAMPLE_API_KEY"],
        env["IDIS_GOVERNANCE_ADMIN_API_KEY"],
        env["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"],
        env["IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH"],
        env["IDIS_PROVIDER_BUDGET_POLICY_PATH"],
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    "launch_identity",
    [
        {},
        {**LAUNCH_IDENTITY, "base_profile_hmac": "not-a-digest"},
        {**LAUNCH_IDENTITY, "command": "gate"},
        {**LAUNCH_IDENTITY, "generation": "unreviewed-v1"},
        {**LAUNCH_IDENTITY, "process_role": "api"},
        {**LAUNCH_IDENTITY, "unreviewed": "field"},
    ],
)
def test_governance_import_rejects_invalid_launch_identity_before_write(
    tmp_path: Path,
    launch_identity: dict[str, object],
) -> None:
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    rights = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit = _AuditSink()
    module = importlib.import_module("idis.runtime.governance_import")

    with pytest.raises(Exception) as exc_info:
        module.import_governance_documents(
            env=env,
            connection=object(),
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=audit,
            now=NOW,
            launch_identity=launch_identity,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert rights.calls == []
    assert budget.calls == []
    assert audit.calls == []


def test_idempotent_replay_preserves_row_lineage_and_audits_the_noop(
    tmp_path: Path,
) -> None:
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    original_event_id = "30000000-0000-4000-8000-000000000099"
    rights = _DurableRepository(
        RIGHTS_SCHEMA_VERSION,
        4,
        replay_import_audit_event_id=original_event_id,
    )
    budget = _DurableRepository(
        BUDGET_SCHEMA_VERSION,
        EXPECTED_BUDGET_COMPONENT_COUNT,
        replay_import_audit_event_id=original_event_id,
    )
    audit = _AuditSink()

    report = _import(
        env=env,
        rights_repository=rights,
        budget_repository=budget,
        audit_sink=audit,
        connection=object(),
    )

    assert report.rights_audit_event_id == original_event_id
    assert report.budget_audit_event_id == original_event_id
    assert report.rights_disposition == "replayed"
    assert report.budget_disposition == "replayed"
    assert report.operation_audit_event_id != original_event_id
    event = audit.calls[0][1]
    assert event["event_id"] == report.operation_audit_event_id
    assert event["event_type"] == "integration.governance.replayed"
    assert event["resource"]["resource_id"] == report.operation_audit_event_id  # type: ignore[index]
    refs = event["payload"]["refs"]  # type: ignore[index]
    assert f"rights_audit_event_id:{original_event_id}" in refs
    assert f"budget_audit_event_id:{original_event_id}" in refs


def test_budget_replacement_succeeds_when_rights_document_is_a_replay(
    tmp_path: Path,
) -> None:
    """The two independently signed documents have independent lifecycle lineages."""
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    rights_event_id = "30000000-0000-4000-8000-000000000071"
    rights = _DurableRepository(
        RIGHTS_SCHEMA_VERSION,
        4,
        replay_import_audit_event_id=rights_event_id,
    )
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit = _AuditSink()

    report = _import(
        env=env,
        rights_repository=rights,
        budget_repository=budget,
        audit_sink=audit,
        connection=object(),
    )

    assert report.rights_audit_event_id == rights_event_id
    assert report.budget_audit_event_id == report.operation_audit_event_id
    assert report.rights_disposition == "replayed"
    assert report.budget_disposition == "created"
    event = audit.calls[0][1]
    safe = event["payload"]["safe"]  # type: ignore[index]
    assert safe["rights_disposition"] == "replayed"
    assert safe["budget_disposition"] == "created"
    assert safe["rights_payload"] == rights_payload()
    from idis.validators.audit_event_validator import validate_audit_event

    assert validate_audit_event(event).passed is True  # type: ignore[arg-type]


def test_rights_transition_succeeds_when_budget_document_is_a_replay(
    tmp_path: Path,
) -> None:
    """A rights renewal/revocation does not force a synthetic budget replacement."""
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    previous_rights_event_id = "30000000-0000-4000-8000-000000000072"
    budget_event_id = "30000000-0000-4000-8000-000000000073"
    previous_payload = rights_payload()
    rights = _DurableRepository(
        RIGHTS_SCHEMA_VERSION,
        4,
        disposition="transitioned",
        previous_rights_payload=previous_payload,
        previous_import_audit_event_id=previous_rights_event_id,
    )
    budget = _DurableRepository(
        BUDGET_SCHEMA_VERSION,
        EXPECTED_BUDGET_COMPONENT_COUNT,
        replay_import_audit_event_id=budget_event_id,
    )
    audit = _AuditSink()

    report = _import(
        env=env,
        rights_repository=rights,
        budget_repository=budget,
        audit_sink=audit,
        connection=object(),
    )

    assert report.rights_audit_event_id == report.operation_audit_event_id
    assert report.budget_audit_event_id == budget_event_id
    assert report.rights_disposition == "transitioned"
    assert report.budget_disposition == "replayed"
    event = audit.calls[0][1]
    safe = event["payload"]["safe"]  # type: ignore[index]
    assert safe["rights_payload"] == rights_payload()
    assert safe["previous_rights_payload"] == previous_payload
    assert (
        f"rights_previous_import_audit_event_id:{previous_rights_event_id}"
        in event["payload"]["refs"]  # type: ignore[index]
    )


@pytest.mark.parametrize("repository_name", ["rights", "budget"])
def test_governance_import_rejects_any_non_durable_repository_before_write(
    tmp_path: Path,
    repository_name: str,
) -> None:
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    rights: _DurableRepository = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget: _DurableRepository = _DurableRepository(
        BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT
    )
    if repository_name == "rights":
        rights = _NonDurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    else:
        budget = _NonDurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit_sink = _AuditSink()

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=audit_sink,
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert rights.calls == []
    assert budget.calls == []
    assert audit_sink.calls == []


def test_governance_import_rejects_payload_tenant_mismatch_before_repository_write(
    tmp_path: Path,
) -> None:
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    budget_path = Path(env["IDIS_PROVIDER_BUDGET_POLICY_PATH"])
    document = json.loads(budget_path.read_text(encoding="utf-8"))
    document["payload"]["tenant_id"] = "10000000-0000-4000-8000-000000000099"
    budget_path.write_text(json.dumps(document), encoding="utf-8")
    rights = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=_AuditSink(),
            connection=object(),
        )

    assert error_code(exc_info.value) in {
        "GOVERNANCE_SIGNATURE_INVALID",
        "GOVERNANCE_SCHEMA_INVALID",
    }
    assert rights.calls == []
    assert budget.calls == []


def test_governance_import_requires_the_exact_admin_binding(tmp_path: Path) -> None:
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    registry = json.loads(env["IDIS_API_KEYS_JSON"])
    registry[env["IDIS_GOVERNANCE_ADMIN_API_KEY"]]["roles"] = ["INTEGRATION_SERVICE"]
    env["IDIS_API_KEYS_JSON"] = json.dumps(registry)

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=_DurableRepository(RIGHTS_SCHEMA_VERSION, 4),
            budget_repository=_DurableRepository(
                BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT
            ),
            audit_sink=_AuditSink(),
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_ADMIN_UNAUTHORIZED"


def test_governance_import_rejects_writable_trusted_public_pem_before_write(
    tmp_path: Path,
) -> None:
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    public_path = Path(env["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"])
    public_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    rights = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit = _AuditSink()

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=audit,
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SIGNATURE_INVALID"
    assert rights.calls == []
    assert budget.calls == []
    assert audit.calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")
def test_governance_import_rejects_read_only_public_pem_with_other_principal_write_acl(
    tmp_path: Path,
) -> None:
    """The read-only DOS attribute is not a trust boundary when another SID can replace it."""
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    public_path = Path(env["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"])
    subprocess.run(
        ["icacls.exe", str(public_path), "/grant", "*S-1-1-0:(M)"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert public_path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=_DurableRepository(RIGHTS_SCHEMA_VERSION, 4),
            budget_repository=_DurableRepository(
                BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT
            ),
            audit_sink=_AuditSink(),
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SIGNATURE_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="Windows parent DACL contract")
def test_governance_import_rejects_protected_public_pem_in_replaceable_parent(
    tmp_path: Path,
) -> None:
    """A safe file DACL is insufficient when another SID can replace it via its parent."""
    env, _private_pem, _public_pem = _signed_inputs(tmp_path)
    public_path = Path(env["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"])
    subprocess.run(
        ["icacls.exe", str(public_path.parent), "/grant", "*S-1-1-0:(OI)(CI)(M)"],
        check=True,
        capture_output=True,
        text=True,
    )

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=_DurableRepository(RIGHTS_SCHEMA_VERSION, 4),
            budget_repository=_DurableRepository(
                BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT
            ),
            audit_sink=_AuditSink(),
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SIGNATURE_INVALID"


def test_governance_import_rejects_valid_key_swap_after_profile_digest_admission(
    tmp_path: Path,
) -> None:
    """The reviewed key digest prevents a same-path trust-root replacement."""
    env, _private_pem, original_public_pem = _signed_inputs(tmp_path)
    expected_digest = hashlib.sha256(original_public_pem).hexdigest()
    alternate_dir = tmp_path / "alternate-operator-keys"
    alternate_dir.mkdir()
    signing_module = importlib.import_module("idis.tools.governance_signing")
    signing_module.generate_ed25519_keypair(
        private_key_path=alternate_dir / "private.pem",
        public_key_path=alternate_dir / "public.pem",
    )
    public_path = Path(env["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"])
    public_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    public_path.write_bytes((alternate_dir / "public.pem").read_bytes())
    secure_files = importlib.import_module("idis.runtime.secure_files")
    secure_files.protect_path_for_current_user(public_path)
    public_path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=_DurableRepository(RIGHTS_SCHEMA_VERSION, 4),
            budget_repository=_DurableRepository(
                BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT
            ),
            audit_sink=_AuditSink(),
            connection=object(),
            expected_public_key_sha256=expected_digest,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SIGNATURE_INVALID"


def test_governance_cli_failure_emits_only_stable_code_without_chained_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = importlib.import_module("idis.runtime.governance_import")
    profile_module = importlib.import_module("idis.runtime.profile")
    db_module = importlib.import_module("idis.persistence.db")
    reviewed = SimpleNamespace(
        values={},
        governance_public_key_sha256="a" * 64,
        **LAUNCH_IDENTITY,
    )
    raw_path = r"C:\operator-secrets\rights-envelope.json"

    monkeypatch.setattr(
        profile_module,
        "require_runtime_profile",
        lambda _profile, *, command: reviewed,
    )
    monkeypatch.setattr(db_module, "begin_app_conn", lambda: nullcontext(object()))

    def fail_import(**_kwargs: object) -> None:
        try:
            raise OSError(raw_path)
        except OSError as exc:
            raise module.GovernanceImportError("GOVERNANCE_SCHEMA_INVALID") from exc

    monkeypatch.setattr(module, "import_governance_documents", fail_import)
    result = module.main(
        [
            "--admin-api-key-env",
            "IDIS_GOVERNANCE_ADMIN_API_KEY",
            "--trusted-signer-id-env",
            "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID",
            "--trusted-ed25519-key-env",
            "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
            "--import-rights-approvals-env",
            "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH",
            "--import-budget-policy-env",
            "IDIS_PROVIDER_BUDGET_POLICY_PATH",
            "--no-secret-output",
        ],
        strict_profile=object(),
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "GOVERNANCE_SCHEMA_INVALID" in captured.err
    assert raw_path not in captured.out + captured.err


def test_direct_governance_cli_parse_failure_never_echoes_argument_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = importlib.import_module("idis.runtime.governance_import")
    sensitive_path = r"C:\operator-secrets\budget-envelope.json"

    result = module.main(["--unexpected", sensitive_path], strict_profile=object())

    captured = capsys.readouterr()
    assert result == 1
    assert json.loads(captured.err) == {
        "may_proceed": False,
        "code": "GOVERNANCE_SCHEMA_INVALID",
    }
    assert captured.out == ""
    assert sensitive_path not in captured.err


@pytest.mark.parametrize(
    "code",
    ["GOVERNANCE_SIGNATURE_INVALID", "GOVERNANCE_SIGNER_UNTRUSTED"],
)
def test_governance_cli_preserves_safe_envelope_failure_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: str,
) -> None:
    module = importlib.import_module("idis.runtime.governance_import")
    envelope_module = importlib.import_module("idis.runtime.governance_envelopes")
    profile_module = importlib.import_module("idis.runtime.profile")
    db_module = importlib.import_module("idis.persistence.db")
    reviewed = SimpleNamespace(
        values={},
        governance_public_key_sha256="a" * 64,
        **LAUNCH_IDENTITY,
    )
    monkeypatch.setattr(
        profile_module,
        "require_runtime_profile",
        lambda _profile, *, command: reviewed,
    )
    monkeypatch.setattr(db_module, "begin_app_conn", lambda: nullcontext(object()))

    def fail_import(**_kwargs: object) -> None:
        raise envelope_module.GovernanceEnvelopeError(code)

    monkeypatch.setattr(module, "import_governance_documents", fail_import)
    result = module.main(
        [
            "--admin-api-key-env",
            "IDIS_GOVERNANCE_ADMIN_API_KEY",
            "--trusted-signer-id-env",
            "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID",
            "--trusted-ed25519-key-env",
            "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
            "--import-rights-approvals-env",
            "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH",
            "--import-budget-policy-env",
            "IDIS_PROVIDER_BUDGET_POLICY_PATH",
            "--no-secret-output",
        ],
        strict_profile=object(),
    )

    captured = capsys.readouterr()
    assert result == 1
    assert json.loads(captured.err) == {"may_proceed": False, "code": code}
    assert captured.out == ""


@pytest.mark.parametrize(
    "expires_at",
    ["2026-07-15T12:00:00Z", "2026-07-15T11:59:59Z"],
    ids=["expires-at-import-time", "expired-before-import"],
)
def test_governance_import_rejects_when_any_rights_approval_is_expired(
    tmp_path: Path,
    expires_at: str,
) -> None:
    """Signed but inactive rights cannot be persisted or produce a success audit."""
    env, private_pem, _public_pem = _signed_inputs(tmp_path)
    envelope_module = importlib.import_module("idis.runtime.governance_envelopes")
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    first_approval = approvals[0]
    assert isinstance(first_approval, dict)
    first_approval["expires_at"] = expires_at
    envelope = envelope_module.sign_envelope(
        schema_version=RIGHTS_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_pem,
    )
    Path(env["IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH"]).write_text(
        json.dumps(envelope), encoding="utf-8"
    )
    rights = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit = _AuditSink()

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=audit,
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert rights.calls == []
    assert budget.calls == []
    assert audit.calls == []


def test_governance_import_rejects_future_dated_approval_before_any_write(
    tmp_path: Path,
) -> None:
    env, private_pem, _public_pem = _signed_inputs(tmp_path)
    envelope_module = importlib.import_module("idis.runtime.governance_envelopes")
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    future_approval = approvals[0]
    assert isinstance(future_approval, dict)
    future_approval["approved_at"] = "2026-07-15T12:00:01Z"
    future_approval["expires_at"] = "2026-07-16T12:00:01Z"
    envelope = envelope_module.sign_envelope(
        schema_version=RIGHTS_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_pem,
    )
    Path(env["IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH"]).write_text(
        json.dumps(envelope), encoding="utf-8"
    )
    rights = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit = _AuditSink()

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=audit,
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert rights.calls == []
    assert budget.calls == []
    assert audit.calls == []


def test_governance_import_allows_expired_revoked_snapshot_row(
    tmp_path: Path,
) -> None:
    """Import-time expiry gates active approvals only; revocation history remains importable."""
    env, private_pem, _public_pem = _signed_inputs(tmp_path)
    envelope_module = importlib.import_module("idis.runtime.governance_envelopes")
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["expires_at"] = "2026-07-15T11:59:59Z"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"
    envelope = envelope_module.sign_envelope(
        schema_version=RIGHTS_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_pem,
    )
    Path(env["IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH"]).write_text(
        json.dumps(envelope), encoding="utf-8"
    )

    report = _import(
        env=env,
        rights_repository=_DurableRepository(RIGHTS_SCHEMA_VERSION, 4),
        budget_repository=_DurableRepository(
            BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT
        ),
        audit_sink=_AuditSink(),
        connection=object(),
    )

    assert report.may_proceed is True


def test_governance_import_rejects_future_dated_revocation(
    tmp_path: Path,
) -> None:
    """A revocation is not current until its signed effective timestamp has arrived."""
    env, private_pem, _public_pem = _signed_inputs(tmp_path)
    envelope_module = importlib.import_module("idis.runtime.governance_envelopes")
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:01Z"
    envelope = envelope_module.sign_envelope(
        schema_version=RIGHTS_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_pem,
    )
    Path(env["IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH"]).write_text(
        json.dumps(envelope), encoding="utf-8"
    )
    rights = _DurableRepository(RIGHTS_SCHEMA_VERSION, 4)
    budget = _DurableRepository(BUDGET_SCHEMA_VERSION, EXPECTED_BUDGET_COMPONENT_COUNT)
    audit = _AuditSink()

    with pytest.raises(Exception) as exc_info:
        _import(
            env=env,
            rights_repository=rights,
            budget_repository=budget,
            audit_sink=audit,
            connection=object(),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert rights.calls == []
    assert budget.calls == []
    assert audit.calls == []
