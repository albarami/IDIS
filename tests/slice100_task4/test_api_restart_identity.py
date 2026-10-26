"""Task 4 API restart-identity contract tests."""

from uuid import UUID

from fastapi.testclient import TestClient

from idis.api.main import create_app


def test_health_boot_id_is_stable_for_one_app_instance() -> None:
    """Repeated health reads identify the same application boot."""
    app = create_app()
    client = TestClient(app)

    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    first_boot_id = first.json()["boot_id"]
    second_boot_id = second.json()["boot_id"]
    assert UUID(first_boot_id).version == 4
    assert second_boot_id == first_boot_id


def test_health_boot_id_differs_between_app_instances() -> None:
    """A newly created application has a distinct boot identity."""
    first_client = TestClient(create_app())
    second_client = TestClient(create_app())

    first_boot_id = first_client.get("/health").json()["boot_id"]
    second_boot_id = second_client.get("/health").json()["boot_id"]

    assert UUID(first_boot_id).version == 4
    assert UUID(second_boot_id).version == 4
    assert first_boot_id != second_boot_id
