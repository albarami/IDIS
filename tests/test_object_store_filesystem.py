"""Tests for IDIS Filesystem Object Storage (STOR-001).

Per v6.3 requirements:
- Roundtrip: put then get returns identical bytes; sha256 matches
- Tenant isolation: Put for tenant A cannot be fetched using tenant B
- Versioning: Different payloads produce distinct version_ids; default get() returns latest
- Path traversal prevention: Keys like "../x", "..\\x", "/abs" are rejected
- OTel spans: With tracing enabled, verify spans are captured with safe attributes
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def temp_storage_dir() -> Any:
    """Create a temporary directory for storage tests."""
    with tempfile.TemporaryDirectory(prefix="idis_test_storage_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def store(temp_storage_dir: Path) -> Any:
    """Create a FilesystemObjectStore with a temp directory."""
    from idis.storage.filesystem_store import FilesystemObjectStore

    return FilesystemObjectStore(base_dir=temp_storage_dir)


@pytest.fixture
def tenant_a() -> str:
    """Return a valid tenant UUID for tenant A."""
    return "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def tenant_b() -> str:
    """Return a valid tenant UUID for tenant B."""
    return "22222222-2222-2222-2222-222222222222"


class TestRoundtrip:
    """Tests for basic put/get roundtrip functionality."""

    def test_put_then_get_returns_identical_bytes(self, store: Any, tenant_a: str) -> None:
        """Put then get should return identical bytes."""
        key = "test/document.pdf"
        data = b"Hello, World! This is test content."

        store.put(tenant_a, key, data, content_type="application/pdf")

        result = store.get(tenant_a, key)

        assert result.body == data
        assert result.metadata.tenant_id == tenant_a
        assert result.metadata.key == key
        assert result.metadata.content_type == "application/pdf"

    def test_sha256_matches_content(self, store: Any, tenant_a: str) -> None:
        """SHA256 in metadata should match actual content hash."""
        key = "test/hash_test.bin"
        data = b"Content for hash verification test"

        expected_sha256 = hashlib.sha256(data).hexdigest()

        metadata = store.put(tenant_a, key, data)

        assert metadata.sha256 == expected_sha256

        result = store.get(tenant_a, key)
        assert result.metadata.sha256 == expected_sha256

        actual_sha256 = hashlib.sha256(result.body).hexdigest()
        assert actual_sha256 == expected_sha256

    def test_size_bytes_matches_content_length(self, store: Any, tenant_a: str) -> None:
        """Size in metadata should match actual content length."""
        key = "test/size_test.bin"
        data = b"X" * 1024

        metadata = store.put(tenant_a, key, data)

        assert metadata.size_bytes == len(data)
        assert metadata.size_bytes == 1024

    def test_empty_content(self, store: Any, tenant_a: str) -> None:
        """Should handle empty content correctly."""
        key = "test/empty.bin"
        data = b""

        metadata = store.put(tenant_a, key, data)

        assert metadata.size_bytes == 0
        assert metadata.sha256 == hashlib.sha256(b"").hexdigest()

        result = store.get(tenant_a, key)
        assert result.body == b""

    def test_large_content(self, store: Any, tenant_a: str) -> None:
        """Should handle larger content (64KB - reduced for disk-safe testing)."""
        key = "test/large.bin"
        # Use 64KB instead of 1MB for disk-safe testing
        data = os.urandom(64 * 1024)

        metadata = store.put(tenant_a, key, data)

        assert metadata.size_bytes == len(data)

        result = store.get(tenant_a, key)
        assert result.body == data

    def test_binary_content(self, store: Any, tenant_a: str) -> None:
        """Should handle binary content with all byte values."""
        key = "test/binary.bin"
        data = bytes(range(256))

        store.put(tenant_a, key, data)

        result = store.get(tenant_a, key)
        assert result.body == data


class TestTenantIsolation:
    """Tests for tenant isolation."""

    def test_tenant_a_cannot_access_tenant_b_object(
        self, store: Any, tenant_a: str, tenant_b: str
    ) -> None:
        """Object stored by tenant A should not be accessible by tenant B."""
        from idis.storage.errors import ObjectNotFoundError

        key = "shared/document.txt"
        data = b"Secret data for tenant A"

        store.put(tenant_a, key, data)

        with pytest.raises(ObjectNotFoundError):
            store.get(tenant_b, key)

    def test_same_key_different_tenants_independent(
        self, store: Any, tenant_a: str, tenant_b: str
    ) -> None:
        """Same key for different tenants should store independent data."""
        key = "config/settings.json"
        data_a = b'{"tenant": "A", "value": 1}'
        data_b = b'{"tenant": "B", "value": 2}'

        store.put(tenant_a, key, data_a)
        store.put(tenant_b, key, data_b)

        result_a = store.get(tenant_a, key)
        result_b = store.get(tenant_b, key)

        assert result_a.body == data_a
        assert result_b.body == data_b
        assert result_a.metadata.tenant_id == tenant_a
        assert result_b.metadata.tenant_id == tenant_b

    def test_delete_does_not_affect_other_tenant(
        self, store: Any, tenant_a: str, tenant_b: str
    ) -> None:
        """Deleting object for tenant A should not affect tenant B's object."""
        from idis.storage.errors import ObjectNotFoundError

        key = "shared/to_delete.txt"
        data_a = b"Tenant A data"
        data_b = b"Tenant B data"

        store.put(tenant_a, key, data_a)
        store.put(tenant_b, key, data_b)

        store.delete(tenant_a, key)

        with pytest.raises(ObjectNotFoundError):
            store.get(tenant_a, key)

        result_b = store.get(tenant_b, key)
        assert result_b.body == data_b

    def test_head_respects_tenant_isolation(self, store: Any, tenant_a: str, tenant_b: str) -> None:
        """Head operation should respect tenant isolation."""
        from idis.storage.errors import ObjectNotFoundError

        key = "test/head_isolation.txt"
        store.put(tenant_a, key, b"data")

        metadata = store.head(tenant_a, key)
        assert metadata.tenant_id == tenant_a

        with pytest.raises(ObjectNotFoundError):
            store.head(tenant_b, key)

    def test_list_versions_respects_tenant_isolation(
        self, store: Any, tenant_a: str, tenant_b: str
    ) -> None:
        """List versions should only show versions for the specified tenant."""
        key = "test/versions_isolation.txt"

        store.put(tenant_a, key, b"v1")
        store.put(tenant_a, key, b"v2")
        store.put(tenant_b, key, b"other_v1")

        versions_a = store.list_versions(tenant_a, key)
        versions_b = store.list_versions(tenant_b, key)

        assert len(versions_a) == 2
        assert len(versions_b) == 1
        assert all(v.tenant_id == tenant_a for v in versions_a)
        assert all(v.tenant_id == tenant_b for v in versions_b)


class TestVersioning:
    """Tests for versioning functionality."""

    def test_different_payloads_produce_distinct_version_ids(
        self, store: Any, tenant_a: str
    ) -> None:
        """Two different payloads for same key should produce distinct version_ids."""
        key = "test/versioned.txt"
        data_v1 = b"Version 1 content"
        data_v2 = b"Version 2 content"

        metadata_v1 = store.put(tenant_a, key, data_v1)
        metadata_v2 = store.put(tenant_a, key, data_v2)

        assert metadata_v1.version_id != metadata_v2.version_id

    def test_default_get_returns_latest(self, store: Any, tenant_a: str) -> None:
        """Get without version_id should return latest version."""
        key = "test/latest.txt"

        store.put(tenant_a, key, b"First version")
        store.put(tenant_a, key, b"Second version")
        metadata_v3 = store.put(tenant_a, key, b"Third version")

        result = store.get(tenant_a, key)

        assert result.body == b"Third version"
        assert result.metadata.version_id == metadata_v3.version_id

    def test_get_specific_version(self, store: Any, tenant_a: str) -> None:
        """Get with specific version_id should return that version."""
        key = "test/specific_version.txt"

        metadata_v1 = store.put(tenant_a, key, b"Version 1")
        metadata_v2 = store.put(tenant_a, key, b"Version 2")
        store.put(tenant_a, key, b"Version 3")

        result_v1 = store.get(tenant_a, key, version_id=metadata_v1.version_id)
        result_v2 = store.get(tenant_a, key, version_id=metadata_v2.version_id)

        assert result_v1.body == b"Version 1"
        assert result_v2.body == b"Version 2"

    def test_list_versions_returns_all_versions(self, store: Any, tenant_a: str) -> None:
        """List versions should return all versions, newest first."""
        key = "test/list_versions.txt"

        metadata_v1 = store.put(tenant_a, key, b"Version 1")
        metadata_v2 = store.put(tenant_a, key, b"Version 2")
        metadata_v3 = store.put(tenant_a, key, b"Version 3")

        versions = store.list_versions(tenant_a, key)

        assert len(versions) == 3
        version_ids = [v.version_id for v in versions]
        assert metadata_v3.version_id == version_ids[0]
        assert metadata_v1.version_id in version_ids
        assert metadata_v2.version_id in version_ids

    def test_list_versions_empty_for_nonexistent_key(self, store: Any, tenant_a: str) -> None:
        """List versions should return empty list for nonexistent key."""
        versions = store.list_versions(tenant_a, "nonexistent/key.txt")
        assert versions == []

    def test_head_default_returns_latest(self, store: Any, tenant_a: str) -> None:
        """Head without version_id should return metadata for latest version."""
        key = "test/head_latest.txt"

        store.put(tenant_a, key, b"First")
        metadata_v2 = store.put(tenant_a, key, b"Second")

        head_result = store.head(tenant_a, key)

        assert head_result.version_id == metadata_v2.version_id

    def test_head_specific_version(self, store: Any, tenant_a: str) -> None:
        """Head with specific version_id should return that version's metadata."""
        key = "test/head_specific.txt"

        metadata_v1 = store.put(tenant_a, key, b"First")
        store.put(tenant_a, key, b"Second")

        head_result = store.head(tenant_a, key, version_id=metadata_v1.version_id)

        assert head_result.version_id == metadata_v1.version_id
        assert head_result.sha256 == metadata_v1.sha256

    def test_delete_specific_version(self, store: Any, tenant_a: str) -> None:
        """Delete with version_id should only delete that version."""
        from idis.storage.errors import ObjectNotFoundError

        key = "test/delete_version.txt"

        metadata_v1 = store.put(tenant_a, key, b"Version 1")
        metadata_v2 = store.put(tenant_a, key, b"Version 2")

        store.delete(tenant_a, key, version_id=metadata_v1.version_id)

        with pytest.raises(ObjectNotFoundError):
            store.get(tenant_a, key, version_id=metadata_v1.version_id)

        result_v2 = store.get(tenant_a, key, version_id=metadata_v2.version_id)
        assert result_v2.body == b"Version 2"

    def test_delete_all_versions(self, store: Any, tenant_a: str) -> None:
        """Delete without version_id should delete all versions."""
        from idis.storage.errors import ObjectNotFoundError

        key = "test/delete_all.txt"

        store.put(tenant_a, key, b"Version 1")
        store.put(tenant_a, key, b"Version 2")

        store.delete(tenant_a, key)

        with pytest.raises(ObjectNotFoundError):
            store.get(tenant_a, key)

        versions = store.list_versions(tenant_a, key)
        assert versions == []

    def test_identical_content_different_versions(self, store: Any, tenant_a: str) -> None:
        """Identical content uploaded twice should still create different versions."""
        key = "test/identical.txt"
        data = b"Identical content"

        metadata_v1 = store.put(tenant_a, key, data)
        metadata_v2 = store.put(tenant_a, key, data)

        assert metadata_v1.version_id != metadata_v2.version_id
        assert metadata_v1.sha256 == metadata_v2.sha256


class TestPathTraversalPrevention:
    """Tests for path traversal attack prevention."""

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "../escape",
            "foo/../bar",
            "..\\escape",
            "foo\\..\\bar",
            "/absolute/path",
            "C:\\windows\\path",
            "D:/drive/path",
            "~/home/escape",
            "foo/../../etc/passwd",
            "normal/../../../escape",
            "key\x00null",
        ],
    )
    def test_put_rejects_traversal_keys(self, store: Any, tenant_a: str, invalid_key: str) -> None:
        """Put should reject keys with path traversal sequences."""
        from idis.storage.errors import PathTraversalError

        with pytest.raises(PathTraversalError):
            store.put(tenant_a, invalid_key, b"data")

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "../escape",
            "foo/../bar",
            "..\\escape",
            "/absolute/path",
            "C:\\windows\\path",
        ],
    )
    def test_get_rejects_traversal_keys(self, store: Any, tenant_a: str, invalid_key: str) -> None:
        """Get should reject keys with path traversal sequences."""
        from idis.storage.errors import PathTraversalError

        with pytest.raises(PathTraversalError):
            store.get(tenant_a, invalid_key)

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "../escape",
            "/absolute/path",
        ],
    )
    def test_head_rejects_traversal_keys(self, store: Any, tenant_a: str, invalid_key: str) -> None:
        """Head should reject keys with path traversal sequences."""
        from idis.storage.errors import PathTraversalError

        with pytest.raises(PathTraversalError):
            store.head(tenant_a, invalid_key)

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "../escape",
            "/absolute/path",
        ],
    )
    def test_delete_rejects_traversal_keys(
        self, store: Any, tenant_a: str, invalid_key: str
    ) -> None:
        """Delete should reject keys with path traversal sequences."""
        from idis.storage.errors import PathTraversalError

        with pytest.raises(PathTraversalError):
            store.delete(tenant_a, invalid_key)

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "../escape",
            "/absolute/path",
        ],
    )
    def test_list_versions_rejects_traversal_keys(
        self, store: Any, tenant_a: str, invalid_key: str
    ) -> None:
        """List versions should reject keys with path traversal sequences."""
        from idis.storage.errors import PathTraversalError

        with pytest.raises(PathTraversalError):
            store.list_versions(tenant_a, invalid_key)

    @pytest.mark.parametrize(
        "valid_key",
        [
            "simple",
            "path/to/file.txt",
            "deep/nested/path/to/file.pdf",
            "file-with-dashes.txt",
            "file_with_underscores.txt",
            "file.multiple.dots.txt",
            "123/numeric/456",
        ],
    )
    def test_valid_keys_accepted(self, store: Any, tenant_a: str, valid_key: str) -> None:
        """Valid keys should be accepted."""
        metadata = store.put(tenant_a, valid_key, b"test data")
        assert metadata.key == valid_key

        result = store.get(tenant_a, valid_key)
        assert result.body == b"test data"


class TestErrorHandling:
    """Tests for error handling."""

    def test_get_nonexistent_object_raises_not_found(self, store: Any, tenant_a: str) -> None:
        """Get on nonexistent object should raise ObjectNotFoundError."""
        from idis.storage.errors import ObjectNotFoundError

        with pytest.raises(ObjectNotFoundError) as exc_info:
            store.get(tenant_a, "nonexistent/key.txt")

        assert exc_info.value.tenant_id == tenant_a
        assert exc_info.value.key == "nonexistent/key.txt"

    def test_get_nonexistent_version_raises_not_found(self, store: Any, tenant_a: str) -> None:
        """Get on nonexistent version should raise ObjectNotFoundError."""
        from idis.storage.errors import ObjectNotFoundError

        key = "test/exists.txt"
        store.put(tenant_a, key, b"data")

        with pytest.raises(ObjectNotFoundError) as exc_info:
            store.get(tenant_a, key, version_id="nonexistent-version-id")

        assert exc_info.value.version_id == "nonexistent-version-id"

    def test_head_nonexistent_object_raises_not_found(self, store: Any, tenant_a: str) -> None:
        """Head on nonexistent object should raise ObjectNotFoundError."""
        from idis.storage.errors import ObjectNotFoundError

        with pytest.raises(ObjectNotFoundError):
            store.head(tenant_a, "nonexistent/key.txt")

    def test_delete_nonexistent_object_raises_not_found(self, store: Any, tenant_a: str) -> None:
        """Delete on nonexistent object should raise ObjectNotFoundError."""
        from idis.storage.errors import ObjectNotFoundError

        with pytest.raises(ObjectNotFoundError):
            store.delete(tenant_a, "nonexistent/key.txt")

    def test_invalid_tenant_id_format(self, store: Any) -> None:
        """Invalid tenant_id format should raise StorageBackendError."""
        from idis.storage.errors import StorageBackendError

        with pytest.raises(StorageBackendError):
            store.put("not-a-uuid", "key.txt", b"data")


class TestBackendProperties:
    """Tests for backend-specific properties."""

    def test_backend_name(self, store: Any) -> None:
        """Backend name should be 'filesystem'."""
        assert store.backend_name == "filesystem"

    def test_base_dir_property(self, store: Any, temp_storage_dir: Path) -> None:
        """Base dir property should return configured directory."""
        assert store.base_dir == temp_storage_dir

    def test_env_var_base_dir(self, temp_storage_dir: Path) -> None:
        """Should use IDIS_OBJECT_STORE_BASE_DIR env var when set."""
        from idis.storage.filesystem_store import FilesystemObjectStore

        custom_dir = temp_storage_dir / "custom"
        custom_dir.mkdir()

        original = os.environ.get("IDIS_OBJECT_STORE_BASE_DIR")
        try:
            os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = str(custom_dir)
            store = FilesystemObjectStore()
            assert store.base_dir == custom_dir
        finally:
            if original is not None:
                os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = original
            elif "IDIS_OBJECT_STORE_BASE_DIR" in os.environ:
                del os.environ["IDIS_OBJECT_STORE_BASE_DIR"]


class TestOtelSpans:
    """Tests for OpenTelemetry span emission."""

    @pytest.fixture(autouse=True)
    def reset_tracing_env(self) -> Any:
        """Reset tracing environment before each test."""
        env_vars = [
            "IDIS_OTEL_ENABLED",
            "IDIS_OTEL_TEST_CAPTURE",
        ]
        original_env = {k: os.environ.get(k) for k in env_vars}

        for k in env_vars:
            if k in os.environ:
                del os.environ[k]

        from idis.observability.tracing import reset_tracing

        reset_tracing()

        yield

        for k in env_vars:
            if k in os.environ:
                del os.environ[k]

        for k, v in original_env.items():
            if v is not None:
                os.environ[k] = v

        reset_tracing()

    def test_put_emits_span_with_safe_attributes(self, store: Any, tenant_a: str) -> None:
        """Put operation should emit span with safe attributes (no absolute paths, no raw keys)."""
        import hashlib

        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()
        clear_test_spans()

        key = "test/otel_put.txt"
        expected_key_sha256 = hashlib.sha256(key.encode("utf-8")).hexdigest()
        store.put(tenant_a, key, b"test data", content_type="text/plain")

        spans = get_test_spans()
        put_spans = [s for s in spans if "object_store.put" in s.name]

        assert len(put_spans) >= 1, f"Expected put span. All spans: {[s.name for s in spans]}"

        span = put_spans[0]
        attrs = dict(span.attributes) if span.attributes else {}

        assert attrs.get("idis.tenant_id") == tenant_a
        # SECURITY: raw key must NOT be in spans - only sha256 hash
        assert "idis.object_key" not in attrs, "Raw key must not be in span attributes"
        assert attrs.get("idis.object_key_sha256") == expected_key_sha256
        assert attrs.get("storage.backend") == "filesystem"
        assert "idis.object_sha256" in attrs
        assert "idis.object_version_id" in attrs

        for attr_key, attr_value in attrs.items():
            attr_str = str(attr_value).lower()
            assert ":\\" not in attr_str, f"Attribute {attr_key} contains absolute path"
            assert ":/" not in attr_str or attr_str.startswith("http"), (
                f"Attribute {attr_key} may contain absolute path"
            )
            if os.name == "nt":
                assert "\\users\\" not in attr_str, f"Attribute {attr_key} contains user path"
            else:
                assert "/home/" not in attr_str, f"Attribute {attr_key} contains home path"
                assert "/tmp/" not in attr_str, f"Attribute {attr_key} contains temp path"

    def test_get_emits_span(self, store: Any, tenant_a: str) -> None:
        """Get operation should emit span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()

        key = "test/otel_get.txt"
        store.put(tenant_a, key, b"test data")

        clear_test_spans()

        store.get(tenant_a, key)

        spans = get_test_spans()
        get_spans = [s for s in spans if "object_store.get" in s.name]

        assert len(get_spans) >= 1

    def test_head_emits_span(self, store: Any, tenant_a: str) -> None:
        """Head operation should emit span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()

        key = "test/otel_head.txt"
        store.put(tenant_a, key, b"test data")

        clear_test_spans()

        store.head(tenant_a, key)

        spans = get_test_spans()
        head_spans = [s for s in spans if "object_store.head" in s.name]

        assert len(head_spans) >= 1

    def test_delete_emits_span(self, store: Any, tenant_a: str) -> None:
        """Delete operation should emit span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()

        key = "test/otel_delete.txt"
        store.put(tenant_a, key, b"test data")

        clear_test_spans()

        store.delete(tenant_a, key)

        spans = get_test_spans()
        delete_spans = [s for s in spans if "object_store.delete" in s.name]

        assert len(delete_spans) >= 1

    def test_list_versions_emits_span(self, store: Any, tenant_a: str) -> None:
        """List versions operation should emit span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()

        key = "test/otel_list.txt"
        store.put(tenant_a, key, b"v1")
        store.put(tenant_a, key, b"v2")

        clear_test_spans()

        store.list_versions(tenant_a, key)

        spans = get_test_spans()
        list_spans = [s for s in spans if "object_store.list_versions" in s.name]

        assert len(list_spans) >= 1

        span = list_spans[0]
        attrs = dict(span.attributes) if span.attributes else {}
        assert attrs.get("idis.object_version_count") == 2

    def test_no_spans_when_otel_disabled(self, store: Any, tenant_a: str) -> None:
        """No spans should be emitted when OTEL is disabled."""
        from idis.observability.tracing import get_test_spans

        store.put(tenant_a, "test/no_otel.txt", b"data")

        spans = get_test_spans()
        storage_spans = [s for s in spans if "object_store" in s.name]

        assert len(storage_spans) == 0

    def test_span_attributes_do_not_contain_credentials(self, store: Any, tenant_a: str) -> None:
        """Span attributes should never contain credentials or secrets in ANY attribute."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()
        clear_test_spans()

        key = "secrets/api_key.txt"
        data = b"secret_api_key_12345"

        store.put(tenant_a, key, data, content_type="text/plain")

        spans = get_test_spans()

        for span in spans:
            attrs = dict(span.attributes) if span.attributes else {}
            for attr_key, attr_value in attrs.items():
                attr_str = str(attr_value).lower()
                # NO exceptions - secrets must never appear in ANY attribute
                assert "secret" not in attr_str, (
                    f"Attribute {attr_key} contains 'secret': {attr_value}"
                )
                assert "password" not in attr_str, (
                    f"Attribute {attr_key} contains 'password': {attr_value}"
                )
                assert "api_key" not in attr_str, (
                    f"Attribute {attr_key} contains 'api_key': {attr_value}"
                )

    def test_secret_in_key_never_appears_in_spans(self, store: Any, tenant_a: str) -> None:
        """Regression test: secrets embedded in object keys must NEVER appear in spans.

        This test uses a key containing obvious secret patterns and verifies that
        the raw key content does not appear anywhere in any span attribute.
        """
        import hashlib

        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()
        clear_test_spans()

        # Key containing obvious secret patterns (using only allowed chars: a-zA-Z0-9_-./)
        secret_key = "uploads/password-hunter2/token-abcd1234/secret.txt"
        expected_key_sha256 = hashlib.sha256(secret_key.encode("utf-8")).hexdigest()

        store.put(tenant_a, secret_key, b"sensitive content")

        spans = get_test_spans()
        assert len(spans) >= 1, "Expected at least one span"

        for span in spans:
            # Check span name does not contain raw key
            assert "password" not in span.name.lower(), (
                f"Span name contains 'password': {span.name}"
            )
            assert "hunter2" not in span.name.lower(), f"Span name contains 'hunter2': {span.name}"

            attrs = dict(span.attributes) if span.attributes else {}

            # Check NO attribute key contains secret patterns
            for attr_key in attrs:
                attr_key_lower = attr_key.lower()
                assert "password" not in attr_key_lower, (
                    f"Attribute key contains 'password': {attr_key}"
                )

            # Check NO attribute value contains raw key or secret substrings
            for attr_key, attr_value in attrs.items():
                attr_str = str(attr_value)
                attr_str_lower = attr_str.lower()

                # Raw key must never appear
                assert secret_key not in attr_str, (
                    f"Attribute {attr_key} contains raw key: {attr_value}"
                )

                # Secret substrings must never appear
                assert "password" not in attr_str_lower, (
                    f"Attribute {attr_key} contains 'password': {attr_value}"
                )
                assert "hunter2" not in attr_str_lower, (
                    f"Attribute {attr_key} contains 'hunter2': {attr_value}"
                )
                assert "abcd1234" not in attr_str_lower, (
                    f"Attribute {attr_key} contains 'abcd1234': {attr_value}"
                )

            # Verify idis.object_key_sha256 exists and equals expected hash
            assert "idis.object_key_sha256" in attrs, (
                "Expected idis.object_key_sha256 attribute in span"
            )
            assert attrs["idis.object_key_sha256"] == expected_key_sha256, (
                f"Expected key SHA256 {expected_key_sha256}, got {attrs['idis.object_key_sha256']}"
            )

            # Verify raw idis.object_key does NOT exist
            assert "idis.object_key" not in attrs, (
                "idis.object_key should not exist - use idis.object_key_sha256 instead"
            )
