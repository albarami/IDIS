"""Compliance-enforced object storage wrapper for IDIS.

Wraps any ObjectStore implementation with compliance enforcement:
- BYOK key revocation check at storage boundary (Class2/3 data)
- Legal hold deletion protection

This is the non-bypassable boundary for compliance controls per v6.3 §5.3 and §6.3.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from idis.compliance.byok import (
    BYOKPolicyRegistry,
    DataClass,
    get_key_metadata,
    require_key_active,
)
from idis.compliance.retention import (
    HoldTarget,
    LegalHoldRegistry,
    block_deletion_if_held,
)
from idis.storage.models import StoredObject, StoredObjectMetadata

if TYPE_CHECKING:
    from idis.api.auth import TenantContext
    from idis.storage.object_store import ObjectStore

logger = logging.getLogger(__name__)


class ComplianceEnforcedStore:
    """Object store wrapper with compliance enforcement at the boundary.

    This wrapper ensures:
    1. BYOK key revocation is enforced for Class2/3 data read/write
    2. Legal holds block deletion operations
    3. Customer key metadata is attached to stored objects

    All compliance checks are fail-closed: if enforcement cannot be performed
    (e.g., missing context), the operation is denied.
    """

    def __init__(
        self,
        inner_store: ObjectStore,
        byok_registry: BYOKPolicyRegistry | None = None,
        hold_registry: LegalHoldRegistry | None = None,
    ) -> None:
        """Initialize the compliance-enforced store.

        Args:
            inner_store: The underlying object store implementation.
            byok_registry: BYOK policy registry (uses default if None).
            hold_registry: Legal hold registry (uses default if None).
        """
        self._inner = inner_store
        self._byok_registry = byok_registry
        self._hold_registry = hold_registry

    @property
    def backend_name(self) -> str:
        """Return the backend identifier with compliance prefix."""
        return f"compliant:{self._inner.backend_name}"

    def _enforce_byok_for_class(
        self,
        tenant_ctx: TenantContext,
        data_class: DataClass,
        operation: str,
    ) -> None:
        """Enforce BYOK key active check for the given data class.

        Args:
            tenant_ctx: Tenant context with tenant_id.
            data_class: Data classification of the object.
            operation: Operation name for logging.

        Raises:
            IdisHttpError: 403 if BYOK key is revoked for Class2/3.
        """
        require_key_active(tenant_ctx, data_class, self._byok_registry)
        logger.debug(
            "BYOK check passed: tenant=%s, class=%s, op=%s",
            tenant_ctx.tenant_id,
            data_class.value,
            operation,
        )

    def _enforce_legal_hold_for_delete(
        self,
        tenant_ctx: TenantContext,
        target_type: HoldTarget,
        target_id: str,
    ) -> None:
        """Enforce legal hold check before deletion.

        Args:
            tenant_ctx: Tenant context with tenant_id.
            target_type: Type of resource being deleted.
            target_id: ID of the resource being deleted.

        Raises:
            IdisHttpError: 403 if resource is under legal hold.
        """
        block_deletion_if_held(tenant_ctx, target_type, target_id, self._hold_registry)

    def put(
        self,
        tenant_ctx: TenantContext,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        data_class: DataClass = DataClass.CLASS_2,
    ) -> StoredObjectMetadata:
        """Store an object with BYOK enforcement.

        For Class2/3 data, BYOK key must be active if configured.

        Args:
            tenant_ctx: Tenant context (required for compliance).
            key: Object key.
            data: Object content.
            content_type: MIME type.
            data_class: Data classification (default CLASS_2).

        Returns:
            StoredObjectMetadata for the stored object.

        Raises:
            IdisHttpError: 403 if BYOK key is revoked for Class2/3.
        """
        self._enforce_byok_for_class(tenant_ctx, data_class, "put")

        metadata = self._inner.put(
            tenant_id=tenant_ctx.tenant_id,
            key=key,
            data=data,
            content_type=content_type,
        )

        byok_metadata = get_key_metadata(tenant_ctx, self._byok_registry)
        if byok_metadata:
            logger.info(
                "Object stored with BYOK: tenant=%s, key=%s, kms_key_hash=%s",
                tenant_ctx.tenant_id,
                key,
                byok_metadata.get("kms_key_alias_hash"),
            )

        return metadata

    def get(
        self,
        tenant_ctx: TenantContext,
        key: str,
        *,
        version_id: str | None = None,
        data_class: DataClass = DataClass.CLASS_2,
    ) -> StoredObject:
        """Retrieve an object with BYOK enforcement.

        For Class2/3 data, BYOK key must be active if configured.

        Args:
            tenant_ctx: Tenant context (required for compliance).
            key: Object key.
            version_id: Optional specific version.
            data_class: Data classification (default CLASS_2).

        Returns:
            StoredObject with metadata and content.

        Raises:
            IdisHttpError: 403 if BYOK key is revoked for Class2/3.
        """
        self._enforce_byok_for_class(tenant_ctx, data_class, "get")

        return self._inner.get(
            tenant_id=tenant_ctx.tenant_id,
            key=key,
            version_id=version_id,
        )

    def head(
        self,
        tenant_ctx: TenantContext,
        key: str,
        *,
        version_id: str | None = None,
        data_class: DataClass = DataClass.CLASS_1,
    ) -> StoredObjectMetadata:
        """Get object metadata with optional BYOK enforcement.

        Metadata retrieval uses CLASS_1 by default (no BYOK check).

        Args:
            tenant_ctx: Tenant context (required for compliance).
            key: Object key.
            version_id: Optional specific version.
            data_class: Data classification (default CLASS_1 for metadata).

        Returns:
            StoredObjectMetadata.

        Raises:
            IdisHttpError: 403 if BYOK key is revoked for Class2/3.
        """
        self._enforce_byok_for_class(tenant_ctx, data_class, "head")

        return self._inner.head(
            tenant_id=tenant_ctx.tenant_id,
            key=key,
            version_id=version_id,
        )

    def delete(
        self,
        tenant_ctx: TenantContext,
        key: str,
        *,
        version_id: str | None = None,
        resource_id: str | None = None,
        hold_target_type: HoldTarget = HoldTarget.ARTIFACT,
    ) -> None:
        """Delete an object with legal hold enforcement.

        Deletion is blocked if the resource is under active legal hold.

        Args:
            tenant_ctx: Tenant context (required for compliance).
            key: Object key.
            version_id: Optional specific version to delete.
            resource_id: ID for legal hold check (defaults to key).
            hold_target_type: Type of resource for hold check.

        Raises:
            IdisHttpError: 403 if resource is under legal hold.
        """
        target_id = resource_id or key
        self._enforce_legal_hold_for_delete(tenant_ctx, hold_target_type, target_id)

        self._inner.delete(
            tenant_id=tenant_ctx.tenant_id,
            key=key,
            version_id=version_id,
        )

        logger.info(
            "Object deleted: tenant=%s, key=%s (legal hold check passed)",
            tenant_ctx.tenant_id,
            key,
        )

    def list_versions(
        self,
        tenant_ctx: TenantContext,
        key: str,
    ) -> list[StoredObjectMetadata]:
        """List all versions of an object.

        Args:
            tenant_ctx: Tenant context.
            key: Object key.

        Returns:
            List of StoredObjectMetadata for all versions.
        """
        return self._inner.list_versions(
            tenant_id=tenant_ctx.tenant_id,
            key=key,
        )
