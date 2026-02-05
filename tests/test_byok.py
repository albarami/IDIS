"""Tests for BYOK (Bring Your Own Key) policy enforcement (v6.3 Task 7.5).

Requirements (per Traceability Matrix SEC-001):
- Valid key alias accepted; invalid alias rejected fail-closed
- Revoked key denies Class2/3 access
- Audit failure during revoke/configure fails closed (no state change)

Test strategy:
- Unit tests for byok.py primitives
- Audit emission failure tests
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.compliance.byok import (
    KEY_ALIAS_MAX_LENGTH,
    BYOKKeyState,
    BYOKPolicyRegistry,
    DataClass,
    configure_key,
    get_key_metadata,
    require_key_active,
    revoke_key,
    rotate_key,
)


class MockAuditSink:
    """Mock audit sink for testing."""

    def __init__(self, should_fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.should_fail = should_fail

    def emit(self, event: dict[str, Any]) -> None:
        if self.should_fail:
            raise RuntimeError("Audit sink failure")
        self.events.append(event)


def make_tenant_ctx(tenant_id: str = "tenant-123") -> TenantContext:
    """Create a TenantContext for testing."""
    return TenantContext(
        tenant_id=tenant_id,
        actor_id="actor-1",
        name="Test Tenant",
        timezone="UTC",
        data_region="me-south-1",
    )


class TestKeyAliasValidation:
    """Tests for key alias validation."""

    def test_accepts_valid_alias(self) -> None:
        """Valid alphanumeric alias is accepted."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        policy = configure_key(ctx, "my-key-alias_123", sink, registry)
        assert policy.key_alias == "my-key-alias_123"

    def test_accepts_single_char_alias(self) -> None:
        """Single character alias is accepted (min length)."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        policy = configure_key(ctx, "x", sink, registry)
        assert policy.key_alias == "x"

    def test_accepts_max_length_alias(self) -> None:
        """Max length alias is accepted."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        alias = "a" * KEY_ALIAS_MAX_LENGTH
        policy = configure_key(ctx, alias, sink, registry)
        assert len(policy.key_alias) == KEY_ALIAS_MAX_LENGTH

    def test_rejects_empty_alias(self) -> None:
        """Empty string alias is rejected."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            configure_key(ctx, "", sink, registry)

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "BYOK_INVALID_KEY_ALIAS"

    def test_rejects_too_long_alias(self) -> None:
        """Alias exceeding max length is rejected."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        alias = "a" * (KEY_ALIAS_MAX_LENGTH + 1)
        with pytest.raises(IdisHttpError) as exc_info:
            configure_key(ctx, alias, sink, registry)

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "BYOK_INVALID_KEY_ALIAS"

    def test_rejects_special_characters(self) -> None:
        """Alias with invalid special characters is rejected."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        invalid_aliases = ["key@alias", "key/alias", "key alias", "key.alias"]
        for alias in invalid_aliases:
            with pytest.raises(IdisHttpError) as exc_info:
                configure_key(ctx, alias, sink, registry)
            assert exc_info.value.code == "BYOK_INVALID_KEY_ALIAS", f"Failed for: {alias}"


class TestConfigureKey:
    """Tests for configure_key()."""

    def test_creates_active_key(self) -> None:
        """Configured key starts in ACTIVE state."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        policy = configure_key(ctx, "my-key", sink, registry)

        assert policy.key_state == BYOKKeyState.ACTIVE
        assert policy.tenant_id == ctx.tenant_id
        assert policy.key_alias == "my-key"
        assert policy.created_at is not None
        assert policy.revoked_at is None

    def test_emits_audit_event(self) -> None:
        """Configuration emits audit event."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event["event_type"] == "byok.key.configured"
        assert event["severity"] == "HIGH"
        assert event["tenant_id"] == ctx.tenant_id

    def test_audit_failure_blocks_configuration(self) -> None:
        """Configuration fails if audit emission fails."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink(should_fail=True)
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            configure_key(ctx, "my-key", sink, registry)

        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "BYOK_AUDIT_FAILED"
        assert registry.get(ctx.tenant_id) is None

    def test_no_key_material_in_audit(self) -> None:
        """Audit event contains hash, not actual key alias."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "super-secret-key", sink, registry)

        event = sink.events[0]
        payload = event["payload"]
        assert "super-secret-key" not in str(event)
        assert "key_alias_hash" in payload
        assert "key_alias_length" in payload
        assert payload["key_alias_length"] == len("super-secret-key")


class TestRotateKey:
    """Tests for rotate_key()."""

    def test_rotates_existing_key(self) -> None:
        """Rotates key to new alias."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "old-key", sink, registry)
        policy = rotate_key(ctx, "new-key", sink, registry)

        assert policy.key_alias == "new-key"
        assert policy.key_state == BYOKKeyState.ACTIVE
        assert policy.rotated_at is not None

    def test_rotate_emits_audit_event(self) -> None:
        """Rotation emits audit event."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "old-key", sink, registry)
        sink.events.clear()
        rotate_key(ctx, "new-key", sink, registry)

        assert len(sink.events) == 1
        assert sink.events[0]["event_type"] == "byok.key.rotated"

    def test_rotate_fails_without_existing_key(self) -> None:
        """Rotation fails if no existing key configured."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            rotate_key(ctx, "new-key", sink, registry)

        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "BYOK_KEY_NOT_FOUND"

    def test_rotate_audit_failure_blocks_change(self) -> None:
        """Rotation fails if audit emission fails (no state change)."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "old-key", sink, registry)

        sink.should_fail = True
        with pytest.raises(IdisHttpError) as exc_info:
            rotate_key(ctx, "new-key", sink, registry)

        assert exc_info.value.code == "BYOK_AUDIT_FAILED"
        assert registry.get(ctx.tenant_id).key_alias == "old-key"


class TestRevokeKey:
    """Tests for revoke_key()."""

    def test_revokes_existing_key(self) -> None:
        """Revokes key and sets state to REVOKED."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        policy = revoke_key(ctx, sink, registry)

        assert policy.key_state == BYOKKeyState.REVOKED
        assert policy.revoked_at is not None

    def test_revoke_emits_audit_event(self) -> None:
        """Revocation emits audit event."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        sink.events.clear()
        revoke_key(ctx, sink, registry)

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event["event_type"] == "byok.key.revoked"
        assert event["payload"]["key_state"] == "REVOKED"

    def test_revoke_fails_without_existing_key(self) -> None:
        """Revocation fails if no existing key configured."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            revoke_key(ctx, sink, registry)

        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "BYOK_KEY_NOT_FOUND"

    def test_revoke_audit_failure_blocks_revocation(self) -> None:
        """Revocation fails if audit emission fails (no state change)."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)

        sink.should_fail = True
        with pytest.raises(IdisHttpError) as exc_info:
            revoke_key(ctx, sink, registry)

        assert exc_info.value.code == "BYOK_AUDIT_FAILED"
        assert registry.get(ctx.tenant_id).key_state == BYOKKeyState.ACTIVE


class TestRequireKeyActive:
    """Tests for require_key_active()."""

    def test_allows_class0_without_byok(self) -> None:
        """Class0 access allowed without BYOK config."""
        registry = BYOKPolicyRegistry()
        ctx = make_tenant_ctx()

        require_key_active(ctx, DataClass.CLASS_0, registry)

    def test_allows_class1_without_byok(self) -> None:
        """Class1 access allowed without BYOK config."""
        registry = BYOKPolicyRegistry()
        ctx = make_tenant_ctx()

        require_key_active(ctx, DataClass.CLASS_1, registry)

    def test_allows_class2_without_byok(self) -> None:
        """Class2 access allowed when no BYOK configured (BYOK is optional)."""
        registry = BYOKPolicyRegistry()
        ctx = make_tenant_ctx()

        require_key_active(ctx, DataClass.CLASS_2, registry)

    def test_allows_class2_with_active_key(self) -> None:
        """Class2 access allowed when BYOK key is ACTIVE."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        require_key_active(ctx, DataClass.CLASS_2, registry)

    def test_allows_class3_with_active_key(self) -> None:
        """Class3 access allowed when BYOK key is ACTIVE."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        require_key_active(ctx, DataClass.CLASS_3, registry)

    def test_denies_class2_with_revoked_key(self) -> None:
        """Class2 access denied when BYOK key is REVOKED."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        revoke_key(ctx, sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            require_key_active(ctx, DataClass.CLASS_2, registry)

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "BYOK_KEY_REVOKED"
        assert exc_info.value.message == "Access denied."

    def test_denies_class3_with_revoked_key(self) -> None:
        """Class3 access denied when BYOK key is REVOKED."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        revoke_key(ctx, sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            require_key_active(ctx, DataClass.CLASS_3, registry)

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "BYOK_KEY_REVOKED"

    def test_allows_class0_class1_with_revoked_key(self) -> None:
        """Class0/1 access still allowed even with revoked BYOK key."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        revoke_key(ctx, sink, registry)

        require_key_active(ctx, DataClass.CLASS_0, registry)
        require_key_active(ctx, DataClass.CLASS_1, registry)


class TestGetKeyMetadata:
    """Tests for get_key_metadata()."""

    def test_returns_none_without_byok(self) -> None:
        """Returns None when no BYOK configured."""
        registry = BYOKPolicyRegistry()
        ctx = make_tenant_ctx()

        metadata = get_key_metadata(ctx, registry)
        assert metadata is None

    def test_returns_metadata_with_byok(self) -> None:
        """Returns metadata when BYOK configured."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        metadata = get_key_metadata(ctx, registry)

        assert metadata is not None
        assert "kms_key_alias_hash" in metadata
        assert "kms_key_state" in metadata
        assert metadata["kms_key_state"] == "ACTIVE"

    def test_metadata_contains_hash_not_alias(self) -> None:
        """Metadata contains hash of alias, not raw alias."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "secret-alias", sink, registry)
        metadata = get_key_metadata(ctx, registry)

        assert "secret-alias" not in str(metadata)
        assert len(metadata["kms_key_alias_hash"]) == 16


class TestBYOKPolicyRegistry:
    """Tests for BYOKPolicyRegistry."""

    def test_tenant_isolation(self) -> None:
        """Policies are isolated per tenant."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()

        ctx1 = make_tenant_ctx("tenant-1")
        ctx2 = make_tenant_ctx("tenant-2")

        configure_key(ctx1, "key-1", sink, registry)
        configure_key(ctx2, "key-2", sink, registry)

        assert registry.get("tenant-1").key_alias == "key-1"
        assert registry.get("tenant-2").key_alias == "key-2"

    def test_clear(self) -> None:
        """Clear removes all policies."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)
        registry.clear()

        assert registry.get(ctx.tenant_id) is None


class TestBYOKAuditRequired:
    """Tests for BYOK audit requirement (fail-closed)."""

    def test_configure_fails_without_audit_sink(self) -> None:
        """Configure fails if audit sink is None (fail-closed)."""
        registry = BYOKPolicyRegistry()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            configure_key(ctx, "my-key", None, registry)

        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "BYOK_AUDIT_REQUIRED"
        assert registry.get(ctx.tenant_id) is None

    def test_rotate_fails_without_audit_sink(self) -> None:
        """Rotate fails if audit sink is None (fail-closed)."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "old-key", sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            rotate_key(ctx, "new-key", None, registry)

        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "BYOK_AUDIT_REQUIRED"
        assert registry.get(ctx.tenant_id).key_alias == "old-key"

    def test_revoke_fails_without_audit_sink(self) -> None:
        """Revoke fails if audit sink is None (fail-closed)."""
        registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        configure_key(ctx, "my-key", sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            revoke_key(ctx, None, registry)

        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "BYOK_AUDIT_REQUIRED"
        assert registry.get(ctx.tenant_id).key_state == BYOKKeyState.ACTIVE


class TestCustomerKeyUsed:
    """Traceability test: verify customer key is actually used at storage boundary.

    Per Traceability Matrix SEC-001: BYOK key must be used for Class2/3 storage.
    This test verifies that the storage boundary actually uses the customer key.
    """

    def test_customer_key_used(self) -> None:
        """Verify BYOK-configured tenant's storage path uses customer key metadata.

        This is the traceability test required by SEC-001.
        It verifies that:
        1. When BYOK is configured, storage operations include key metadata
        2. The key alias hash is deterministic and included in stored object metadata
        3. BYOK evidence is persisted as sidecar metadata at the storage boundary
        """
        import hashlib
        import tempfile
        from pathlib import Path

        from idis.compliance.byok import BYOKPolicyRegistry, configure_key, get_key_metadata
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        byok_registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx("00000000-0000-0000-0000-000000000001")

        customer_key_alias = "customer-key-alias-me-south-1-123456789"
        configure_key(ctx, customer_key_alias, sink, registry=byok_registry)

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                byok_registry=byok_registry,
            )

            test_data = b"sensitive Class2 document content"
            object_key = "documents/secret.pdf"
            metadata = compliant_store.put(
                tenant_ctx=ctx,
                key=object_key,
                data=test_data,
                content_type="application/pdf",
            )

            assert metadata is not None
            assert metadata.tenant_id == ctx.tenant_id

            byok_meta = get_key_metadata(ctx, byok_registry)
            assert byok_meta is not None
            assert "kms_key_alias_hash" in byok_meta
            assert byok_meta["kms_key_state"] == "ACTIVE"

            expected_hash = hashlib.sha256(customer_key_alias.encode()).hexdigest()[:16]
            assert byok_meta["kms_key_alias_hash"] == expected_hash

            evidence = compliant_store.get_byok_evidence(ctx, object_key)
            assert evidence is not None, "BYOK evidence sidecar must be persisted"
            assert evidence["kms_key_alias_hash"] == expected_hash
            assert evidence["kms_key_state"] == "ACTIVE"
            assert evidence["data_class"] == "CLASS_2"
            assert evidence["tenant_id"] == ctx.tenant_id
            assert "written_at" in evidence
            assert evidence["evidence_version"] == "1.0"

    def test_revoked_key_denies_storage_access(self) -> None:
        """Verify revoked BYOK key denies Class2/3 storage access at real boundary.

        Integration test: BYOK revoke must block read/write at storage boundary.
        """
        import tempfile
        from pathlib import Path

        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            DataClass,
            configure_key,
            revoke_key,
        )
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        byok_registry = BYOKPolicyRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx("00000000-0000-0000-0000-000000000002")

        configure_key(ctx, "customer-key", sink, registry=byok_registry)

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                byok_registry=byok_registry,
            )

            compliant_store.put(
                tenant_ctx=ctx,
                key="test-doc",
                data=b"test content",
                data_class=DataClass.CLASS_2,
            )

            revoke_key(ctx, sink, registry=byok_registry)

            with pytest.raises(IdisHttpError) as exc_info:
                compliant_store.get(
                    tenant_ctx=ctx,
                    key="test-doc",
                    data_class=DataClass.CLASS_2,
                )

            assert exc_info.value.status_code == 403
            assert exc_info.value.code == "BYOK_KEY_REVOKED"

            with pytest.raises(IdisHttpError) as exc_info:
                compliant_store.put(
                    tenant_ctx=ctx,
                    key="new-doc",
                    data=b"new content",
                    data_class=DataClass.CLASS_2,
                )

            assert exc_info.value.status_code == 403
            assert exc_info.value.code == "BYOK_KEY_REVOKED"


class TestLegalHoldBlocksDelete:
    """Integration test: legal hold must block deletion at real storage boundary."""

    def test_legal_hold_blocks_delete_at_storage_boundary(self) -> None:
        """Verify legal hold blocks deletion via compliant store."""
        import tempfile
        from pathlib import Path

        from idis.compliance.retention import (
            HoldTarget,
            LegalHoldRegistry,
            apply_hold,
        )
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        hold_registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx("00000000-0000-0000-0000-000000000003")

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                hold_registry=hold_registry,
            )

            compliant_store.put(
                tenant_ctx=ctx,
                key="held-document",
                data=b"important evidence",
            )

            apply_hold(
                tenant_ctx=ctx,
                target_type=HoldTarget.ARTIFACT,
                target_id="held-document",
                reason="Legal investigation #12345",
                audit_sink=sink,
                registry=hold_registry,
            )

            with pytest.raises(IdisHttpError) as exc_info:
                compliant_store.delete(
                    tenant_ctx=ctx,
                    key="held-document",
                    resource_id="held-document",
                    hold_target_type=HoldTarget.ARTIFACT,
                )

            assert exc_info.value.status_code == 403
            assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"

            obj = compliant_store.get(tenant_ctx=ctx, key="held-document")
            assert obj.body == b"important evidence"
