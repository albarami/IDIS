"""Tests for IDIS API idempotency middleware.

Tests cover:
A) Replay returns identical 2xx response
B) Collision returns 409
C) Tenant isolation - no cross-tenant replay
D) Fail closed when store is unusable (only when header is present)
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import (
    SqliteIdempotencyStore,
)


@pytest.fixture
def tenant_a_id() -> str:
    """Generate tenant A UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def tenant_b_id() -> str:
    """Generate tenant B UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def api_key_a() -> str:
    """Generate API key for tenant A."""
    return f"key-a-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def api_key_b() -> str:
    """Generate API key for tenant B."""
    return f"key-b-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def actor_a_id() -> str:
    """Generate actor A UUID."""
    return f"actor-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def actor_b_id() -> str:
    """Generate actor B UUID."""
    return f"actor-b-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def api_keys_config_single(
    tenant_a_id: str, actor_a_id: str, api_key_a: str
) -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration with single tenant."""
    return {
        api_key_a: {
            "tenant_id": tenant_a_id,
            "actor_id": actor_a_id,
            "name": "Tenant A",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        }
    }


@pytest.fixture
def api_keys_config_multi(
    tenant_a_id: str,
    tenant_b_id: str,
    actor_a_id: str,
    actor_b_id: str,
    api_key_a: str,
    api_key_b: str,
) -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration with two tenants."""
    return {
        api_key_a: {
            "tenant_id": tenant_a_id,
            "actor_id": actor_a_id,
            "name": "Tenant A",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        api_key_b: {
            "tenant_id": tenant_b_id,
            "actor_id": actor_b_id,
            "name": "Tenant B",
            "timezone": "America/New_York",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }


@pytest.fixture
def client_with_idempotency(
    api_keys_config_single: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with idempotency store configured (in-memory, disk-safe)."""
    clear_deals_store()

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

    # Use in-memory stores for disk-safe testing
    audit_sink = InMemoryAuditSink()
    idem_store = SqliteIdempotencyStore(in_memory=True)

    app = create_app(
        audit_sink=audit_sink, idempotency_store=idem_store, service_region="me-south-1"
    )
    return TestClient(app)


@pytest.fixture
def client_multi_tenant(
    api_keys_config_multi: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with two tenants configured (in-memory, disk-safe)."""
    clear_deals_store()

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_multi))

    # Use in-memory stores for disk-safe testing
    audit_sink = InMemoryAuditSink()
    idem_store = SqliteIdempotencyStore(in_memory=True)

    app = create_app(
        audit_sink=audit_sink, idempotency_store=idem_store, service_region="me-south-1"
    )
    return TestClient(app)


class BrokenIdempotencyStore:
    """Test-only store that fails on all operations (simulates unavailable store)."""

    def get(self, scope_key: object) -> None:
        """Fail on get."""
        from idis.idempotency.store import IdempotencyStoreError

        raise IdempotencyStoreError("Simulated store unavailable")

    def put(self, scope_key: object, record: object) -> None:
        """Fail on put."""
        from idis.idempotency.store import IdempotencyStoreError

        raise IdempotencyStoreError("Simulated store unavailable")


@pytest.fixture
def client_with_broken_store(
    api_keys_config_single: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with broken idempotency store (fails on all operations)."""
    clear_deals_store()

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

    # Use in-memory audit sink and broken store for disk-safe testing
    audit_sink = InMemoryAuditSink()
    broken_store = BrokenIdempotencyStore()

    app = create_app(
        audit_sink=audit_sink, idempotency_store=broken_store, service_region="me-south-1"
    )
    return TestClient(app)


class TestIdempotencyReplay:
    """Test A: Replay returns identical 2xx response."""

    def test_first_request_creates_deal(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """First POST with Idempotency-Key creates deal and returns 201."""
        response = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": "idem_12345678",
            },
            json={"name": "Deal A", "company_name": "Acme"},
        )

        assert response.status_code == 201
        body = response.json()
        assert "deal_id" in body
        assert body["name"] == "Deal A"
        assert body["company_name"] == "Acme"

    def test_replay_returns_same_response(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """Second POST with same Idempotency-Key + payload returns stored response."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal A", "company_name": "Acme"}

        response1 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response1.status_code == 201
        deal_id_1 = response1.json()["deal_id"]

        response2 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response2.status_code == 201
        deal_id_2 = response2.json()["deal_id"]

        assert deal_id_1 == deal_id_2

    def test_replay_has_idempotency_header(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """Replayed response has X-IDIS-Idempotency-Replay: true header."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal B", "company_name": "Beta Corp"}

        response1 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response1.status_code == 201
        assert response1.headers.get("X-IDIS-Idempotency-Replay") is None

        response2 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response2.status_code == 201
        assert response2.headers.get("X-IDIS-Idempotency-Replay") == "true"

    def test_without_idempotency_key_creates_new_deal(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """POST without Idempotency-Key creates new deal each time."""
        payload = {"name": "Deal C", "company_name": "Gamma Inc"}

        response1 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        response2 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        assert response1.status_code == 201
        assert response2.status_code == 201
        assert response1.json()["deal_id"] != response2.json()["deal_id"]


class TestIdempotencyCollision:
    """Test B: Collision returns 409."""

    def test_different_payload_returns_409(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """Same Idempotency-Key with different payload returns 409."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"

        response1 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"name": "Deal A", "company_name": "Acme"},
        )

        assert response1.status_code == 201

        response2 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"name": "Deal A", "company_name": "Different"},
        )

        assert response2.status_code == 409

    def test_collision_error_has_correct_code(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """409 response has code IDEMPOTENCY_KEY_CONFLICT."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"

        client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"name": "Deal X", "company_name": "X Corp"},
        )

        response = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"name": "Deal Y", "company_name": "Y Corp"},
        )

        body = response.json()
        assert body["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    def test_collision_error_has_request_id(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """409 response includes request_id."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"

        client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"name": "Deal P", "company_name": "P Corp"},
        )

        response = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"name": "Deal Q", "company_name": "Q Corp"},
        )

        body = response.json()
        assert "request_id" in body
        assert body["request_id"] is not None


class TestIdempotencyConflictOnPayloadMismatch:
    """Roadmap Task 2.7: test_idempotency_conflict_on_payload_mismatch.

    Acceptance criteria from 12_IDIS_End_to_End_Implementation_Roadmap_v6_3.md:
    1) First request with key K + payload P1 returns 2xx and stores record.
    2) Second request with same key K and different payload P2 returns 409.
    3) Third request with original payload P1 still returns stored response (no overwrite).
    4) Same key K but different actor_id does NOT conflict with original record.
    """

    def test_idempotency_conflict_on_payload_mismatch(
        self, client_multi_tenant: TestClient, api_key_a: str, api_key_b: str
    ) -> None:
        """Comprehensive test per roadmap Task 2.7 acceptance criteria."""
        idempotency_key = f"idem_conflict_{uuid.uuid4().hex[:8]}"
        payload_p1 = {"name": "Deal P1", "company_name": "P1 Corp"}
        payload_p2 = {"name": "Deal P2", "company_name": "P2 Corp"}

        # Assertion 1: First request with key K + payload P1 returns 2xx and stores record
        response1 = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload_p1,
        )
        assert response1.status_code == 201, "First request must return 201"
        original_deal_id = response1.json()["deal_id"]
        assert original_deal_id is not None, "First request must create deal"
        assert response1.headers.get("X-IDIS-Idempotency-Replay") is None, (
            "First request must not be a replay"
        )

        # Assertion 2: Second request with same key K and different payload P2 returns 409
        response2 = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload_p2,
        )
        assert response2.status_code == 409, "Payload mismatch must return 409"
        body2 = response2.json()
        assert body2["code"] == "IDEMPOTENCY_KEY_CONFLICT", (
            "409 must have IDEMPOTENCY_KEY_CONFLICT code"
        )
        assert "request_id" in body2, "409 must include request_id"

        # Assertion 3: Third request with original payload P1 still returns stored response
        response3 = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload_p1,
        )
        assert response3.status_code == 201, "Replay with original payload must return 201"
        assert response3.json()["deal_id"] == original_deal_id, (
            "Replay must return original deal_id (proves no overwrite on conflict)"
        )
        assert response3.headers.get("X-IDIS-Idempotency-Replay") == "true", (
            "Replay must have X-IDIS-Idempotency-Replay header"
        )

        # Assertion 4: Same key K but different actor_id does NOT conflict
        response4 = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_b,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload_p1,
        )
        assert response4.status_code == 201, (
            "Different actor must NOT conflict (creates new record)"
        )
        different_actor_deal_id = response4.json()["deal_id"]
        assert different_actor_deal_id != original_deal_id, (
            "Different actor must create separate deal"
        )
        assert response4.headers.get("X-IDIS-Idempotency-Replay") is None, (
            "Different actor request must not be a replay"
        )


class TestTenantIsolation:
    """Test C: Tenant isolation - no cross-tenant replay."""

    def test_same_key_different_tenants_creates_separate_deals(
        self, client_multi_tenant: TestClient, api_key_a: str, api_key_b: str
    ) -> None:
        """Same Idempotency-Key + payload for different tenants creates separate deals."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal Shared", "company_name": "Shared Corp"}

        response_a = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response_b = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_b,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_a.status_code == 201
        assert response_b.status_code == 201

        deal_id_a = response_a.json()["deal_id"]
        deal_id_b = response_b.json()["deal_id"]

        assert deal_id_a != deal_id_b

    def test_tenant_b_does_not_get_replay_header(
        self, client_multi_tenant: TestClient, api_key_a: str, api_key_b: str
    ) -> None:
        """Tenant B request should not have replay header (new deal, not replayed)."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal Isolated", "company_name": "Isolated Corp"}

        response_a = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response_b = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_b,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_a.headers.get("X-IDIS-Idempotency-Replay") is None
        assert response_b.headers.get("X-IDIS-Idempotency-Replay") is None

    def test_tenant_a_replay_after_tenant_b_creates(
        self, client_multi_tenant: TestClient, api_key_a: str, api_key_b: str
    ) -> None:
        """Tenant A replay works correctly even after Tenant B uses same key."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal Multi", "company_name": "Multi Corp"}

        response_a1 = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )
        deal_id_a = response_a1.json()["deal_id"]

        client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_b,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response_a2 = client_multi_tenant.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_a2.status_code == 201
        assert response_a2.json()["deal_id"] == deal_id_a
        assert response_a2.headers.get("X-IDIS-Idempotency-Replay") == "true"


class TestFailClosed:
    """Test D: Fail closed when store is unusable (only when header is present)."""

    def test_store_failure_returns_500(
        self, client_with_broken_store: TestClient, api_key_a: str
    ) -> None:
        """POST with Idempotency-Key when store is broken returns 500."""
        response = client_with_broken_store.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": "idem_broken_test",
            },
            json={"name": "Deal Broken", "company_name": "Broken Corp"},
        )

        assert response.status_code == 500

    def test_store_failure_has_correct_error_code(
        self, client_with_broken_store: TestClient, api_key_a: str
    ) -> None:
        """500 response has code IDEMPOTENCY_STORE_FAILED."""
        response = client_with_broken_store.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": "idem_broken_code",
            },
            json={"name": "Deal Error", "company_name": "Error Corp"},
        )

        body = response.json()
        assert body["code"] == "IDEMPOTENCY_STORE_FAILED"

    def test_no_stack_trace_in_error(
        self, client_with_broken_store: TestClient, api_key_a: str
    ) -> None:
        """500 response does not leak stack trace."""
        response = client_with_broken_store.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": "idem_broken_trace",
            },
            json={"name": "Deal Stack", "company_name": "Stack Corp"},
        )

        body = response.json()
        assert "traceback" not in str(body).lower()
        assert "exception" not in str(body).lower()
        assert "sqlite" not in str(body).lower()

    def test_without_idempotency_key_succeeds_despite_broken_store(
        self, client_with_broken_store: TestClient, api_key_a: str
    ) -> None:
        """POST without Idempotency-Key succeeds even when store is broken."""
        response = client_with_broken_store.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"name": "Deal OK", "company_name": "OK Corp"},
        )

        assert response.status_code == 201
        assert "deal_id" in response.json()


class TestIdempotencyEdgeCases:
    """Additional edge case tests for idempotency."""

    def test_different_idempotency_keys_create_different_deals(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """Different Idempotency-Keys with same payload create different deals."""
        payload = {"name": "Deal Same", "company_name": "Same Corp"}

        response1 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": "key_1_abc",
            },
            json=payload,
        )

        response2 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": "key_2_xyz",
            },
            json=payload,
        )

        assert response1.status_code == 201
        assert response2.status_code == 201
        assert response1.json()["deal_id"] != response2.json()["deal_id"]

    def test_replay_preserves_status_code(
        self, client_with_idempotency: TestClient, api_key_a: str
    ) -> None:
        """Replayed response preserves original status code (201)."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal Status", "company_name": "Status Corp"}

        response1 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response2 = client_with_idempotency.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response1.status_code == 201
        assert response2.status_code == 201


class TestActorIsolation:
    """Test E: Actor isolation - same tenant, different actor does not replay."""

    @pytest.fixture
    def api_key_a2(self) -> str:
        """Generate second API key for tenant A with different actor."""
        return f"key-a2-{uuid.uuid4().hex[:16]}"

    @pytest.fixture
    def actor_a2_id(self) -> str:
        """Generate second actor ID for tenant A."""
        return f"actor-a2-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def api_keys_config_same_tenant_different_actors(
        self,
        tenant_a_id: str,
        actor_a_id: str,
        actor_a2_id: str,
        api_key_a: str,
        api_key_a2: str,
    ) -> dict[str, dict[str, str | list[str]]]:
        """Create API keys config with same tenant but different actors."""
        return {
            api_key_a: {
                "tenant_id": tenant_a_id,
                "actor_id": actor_a_id,
                "name": "Actor A1",
                "timezone": "Asia/Qatar",
                "data_region": "me-south-1",
                "roles": ["ANALYST"],
            },
            api_key_a2: {
                "tenant_id": tenant_a_id,
                "actor_id": actor_a2_id,
                "name": "Actor A2",
                "timezone": "Asia/Qatar",
                "data_region": "me-south-1",
                "roles": ["ANALYST"],
            },
        }

    @pytest.fixture
    def client_same_tenant_different_actors(
        self,
        api_keys_config_same_tenant_different_actors: dict[str, dict[str, str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> TestClient:
        """Create test client with same tenant but different actors (in-memory)."""
        clear_deals_store()

        monkeypatch.setenv(
            IDIS_API_KEYS_ENV, json.dumps(api_keys_config_same_tenant_different_actors)
        )

        # Use in-memory stores for disk-safe testing
        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        app = create_app(
            audit_sink=audit_sink, idempotency_store=idem_store, service_region="me-south-1"
        )
        return TestClient(app)

    def test_same_tenant_different_actor_creates_separate_deals(
        self,
        client_same_tenant_different_actors: TestClient,
        api_key_a: str,
        api_key_a2: str,
    ) -> None:
        """Same tenant + different actor_id + same Idempotency-Key creates separate deals."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal Actor Test", "company_name": "Actor Corp"}

        response_actor1 = client_same_tenant_different_actors.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response_actor2 = client_same_tenant_different_actors.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a2,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_actor1.status_code == 201
        assert response_actor2.status_code == 201

        deal_id_1 = response_actor1.json()["deal_id"]
        deal_id_2 = response_actor2.json()["deal_id"]

        assert deal_id_1 != deal_id_2, "Different actors must create separate deals"

    def test_same_tenant_different_actor_no_replay_header(
        self,
        client_same_tenant_different_actors: TestClient,
        api_key_a: str,
        api_key_a2: str,
    ) -> None:
        """Actor 2 request should not have replay header (new deal, not replayed)."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"name": "Deal No Replay", "company_name": "NoReplay Corp"}

        response_actor1 = client_same_tenant_different_actors.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response_actor2 = client_same_tenant_different_actors.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a2,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_actor1.headers.get("X-IDIS-Idempotency-Replay") is None
        assert response_actor2.headers.get("X-IDIS-Idempotency-Replay") is None


class FailingIdempotencyStore:
    """Test-only store that fails on put operations."""

    def __init__(self) -> None:
        self._records: dict[tuple, object] = {}

    def get(self, scope_key: object) -> None:
        """Return None (no record found)."""
        return None

    def put(self, scope_key: object, record: object) -> None:
        """Always fail on put."""
        from idis.idempotency.store import IdempotencyStoreError

        raise IdempotencyStoreError("Simulated store put failure")


class TestStorePutFailure:
    """Test F: store.put failure returns 500 IDEMPOTENCY_STORE_FAILED."""

    @pytest.fixture
    def client_with_readonly_store(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> TestClient:
        """Create test client with store that will fail on put (in-memory)."""
        clear_deals_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        # Use in-memory audit sink and a failing store for put operations
        audit_sink = InMemoryAuditSink()
        failing_store = FailingIdempotencyStore()

        app = create_app(
            audit_sink=audit_sink, idempotency_store=failing_store, service_region="me-south-1"
        )

        return TestClient(app)

    def test_store_put_failure_returns_500(
        self, client_with_readonly_store: TestClient, api_key_a: str
    ) -> None:
        """store.put failure returns 500 IDEMPOTENCY_STORE_FAILED."""
        response = client_with_readonly_store.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": f"idem_{uuid.uuid4().hex[:8]}",
            },
            json={"name": "Deal Put Fail", "company_name": "PutFail Corp"},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "IDEMPOTENCY_STORE_FAILED"
