"""Tests for IDIS OpenAPI specification loader."""

import os
from pathlib import Path

import pytest

from idis.api.openapi_loader import (
    OPENAPI_PATH_ENV_VAR,
    OpenAPILoadError,
    get_route_inventory,
    load_openapi_spec,
)


class TestLoadOpenAPISpec:
    """Tests for load_openapi_spec function."""

    def test_loads_default_spec_successfully(self) -> None:
        """load_openapi_spec() successfully loads IDIS_OpenAPI_v6_3.yaml from default location."""
        if OPENAPI_PATH_ENV_VAR in os.environ:
            del os.environ[OPENAPI_PATH_ENV_VAR]

        spec = load_openapi_spec()

        assert isinstance(spec, dict)
        assert "openapi" in spec
        assert "info" in spec
        assert "paths" in spec
        assert spec["info"]["version"] == "6.3"

    def test_loads_spec_from_env_var(self, tmp_path: Path) -> None:
        """load_openapi_spec() uses IDIS_OPENAPI_PATH env var when set."""
        test_spec = tmp_path / "test_spec.yaml"
        test_spec.write_text("openapi: 3.0.3\ninfo:\n  title: Test\n  version: '1.0'\npaths: {}")

        original_env = os.environ.get(OPENAPI_PATH_ENV_VAR)
        try:
            os.environ[OPENAPI_PATH_ENV_VAR] = str(test_spec)
            spec = load_openapi_spec()

            assert spec["info"]["version"] == "1.0"
            assert spec["info"]["title"] == "Test"
        finally:
            if original_env is not None:
                os.environ[OPENAPI_PATH_ENV_VAR] = original_env
            elif OPENAPI_PATH_ENV_VAR in os.environ:
                del os.environ[OPENAPI_PATH_ENV_VAR]

    def test_raises_error_for_missing_file(self, tmp_path: Path) -> None:
        """load_openapi_spec() raises OpenAPILoadError for missing file."""
        nonexistent_path = tmp_path / "nonexistent" / "spec.yaml"
        original_env = os.environ.get(OPENAPI_PATH_ENV_VAR)
        try:
            os.environ[OPENAPI_PATH_ENV_VAR] = str(nonexistent_path)

            with pytest.raises(OpenAPILoadError) as exc_info:
                load_openapi_spec()

            assert "not found" in exc_info.value.message.lower()
            assert exc_info.value.path == str(nonexistent_path)
        finally:
            if original_env is not None:
                os.environ[OPENAPI_PATH_ENV_VAR] = original_env
            elif OPENAPI_PATH_ENV_VAR in os.environ:
                del os.environ[OPENAPI_PATH_ENV_VAR]

    def test_raises_error_for_invalid_yaml(self, tmp_path: Path) -> None:
        """load_openapi_spec() raises OpenAPILoadError for invalid YAML."""
        invalid_yaml = tmp_path / "invalid.yaml"
        invalid_yaml.write_text("{{{{invalid yaml content:::::")

        original_env = os.environ.get(OPENAPI_PATH_ENV_VAR)
        try:
            os.environ[OPENAPI_PATH_ENV_VAR] = str(invalid_yaml)

            with pytest.raises(OpenAPILoadError) as exc_info:
                load_openapi_spec()

            assert "invalid yaml" in exc_info.value.message.lower()
        finally:
            if original_env is not None:
                os.environ[OPENAPI_PATH_ENV_VAR] = original_env
            elif OPENAPI_PATH_ENV_VAR in os.environ:
                del os.environ[OPENAPI_PATH_ENV_VAR]

    def test_raises_error_for_empty_file(self, tmp_path: Path) -> None:
        """load_openapi_spec() raises OpenAPILoadError for empty file."""
        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text("")

        original_env = os.environ.get(OPENAPI_PATH_ENV_VAR)
        try:
            os.environ[OPENAPI_PATH_ENV_VAR] = str(empty_file)

            with pytest.raises(OpenAPILoadError) as exc_info:
                load_openapi_spec()

            assert "empty" in exc_info.value.message.lower()
        finally:
            if original_env is not None:
                os.environ[OPENAPI_PATH_ENV_VAR] = original_env
            elif OPENAPI_PATH_ENV_VAR in os.environ:
                del os.environ[OPENAPI_PATH_ENV_VAR]

    def test_raises_error_for_non_dict_spec(self, tmp_path: Path) -> None:
        """load_openapi_spec() raises OpenAPILoadError when spec is not a dict."""
        list_file = tmp_path / "list.yaml"
        list_file.write_text("- item1\n- item2")

        original_env = os.environ.get(OPENAPI_PATH_ENV_VAR)
        try:
            os.environ[OPENAPI_PATH_ENV_VAR] = str(list_file)

            with pytest.raises(OpenAPILoadError) as exc_info:
                load_openapi_spec()

            assert "mapping" in exc_info.value.message.lower()
        finally:
            if original_env is not None:
                os.environ[OPENAPI_PATH_ENV_VAR] = original_env
            elif OPENAPI_PATH_ENV_VAR in os.environ:
                del os.environ[OPENAPI_PATH_ENV_VAR]

    def test_raises_error_for_directory_path(self, tmp_path: Path) -> None:
        """load_openapi_spec() raises OpenAPILoadError when path is a directory."""
        original_env = os.environ.get(OPENAPI_PATH_ENV_VAR)
        try:
            os.environ[OPENAPI_PATH_ENV_VAR] = str(tmp_path)

            with pytest.raises(OpenAPILoadError) as exc_info:
                load_openapi_spec()

            assert "not a file" in exc_info.value.message.lower()
        finally:
            if original_env is not None:
                os.environ[OPENAPI_PATH_ENV_VAR] = original_env
            elif OPENAPI_PATH_ENV_VAR in os.environ:
                del os.environ[OPENAPI_PATH_ENV_VAR]


class TestGetRouteInventory:
    """Tests for get_route_inventory function."""

    def test_extracts_routes_from_default_spec(self) -> None:
        """get_route_inventory() extracts routes from IDIS OpenAPI spec."""
        if OPENAPI_PATH_ENV_VAR in os.environ:
            del os.environ[OPENAPI_PATH_ENV_VAR]

        inventory = get_route_inventory()

        assert len(inventory) > 0
        assert ("GET", "/health") in inventory

    def test_extracts_routes_from_provided_spec(self) -> None:
        """get_route_inventory() extracts routes from provided spec dict."""
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/test": {"get": {}, "post": {}},
                "/other": {"delete": {}},
            },
        }

        inventory = get_route_inventory(spec)

        assert ("GET", "/test") in inventory
        assert ("POST", "/test") in inventory
        assert ("DELETE", "/other") in inventory
        assert len(inventory) == 3

    def test_returns_empty_for_no_paths(self) -> None:
        """get_route_inventory() returns empty list when no paths defined."""
        spec = {"openapi": "3.0.3", "info": {"title": "Test"}}

        inventory = get_route_inventory(spec)

        assert inventory == []

    def test_returns_empty_for_empty_paths(self) -> None:
        """get_route_inventory() returns empty list when paths is empty."""
        spec = {"openapi": "3.0.3", "paths": {}}

        inventory = get_route_inventory(spec)

        assert inventory == []

    def test_handles_all_http_methods(self) -> None:
        """get_route_inventory() handles all standard HTTP methods."""
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/all-methods": {
                    "get": {},
                    "post": {},
                    "put": {},
                    "patch": {},
                    "delete": {},
                    "head": {},
                    "options": {},
                    "trace": {},
                },
            },
        }

        inventory = get_route_inventory(spec)

        methods = {method for method, _ in inventory}
        assert methods == {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}


class TestRunObservabilityOpenAPI:
    """OpenAPI contract tests for public run observability."""

    def test_run_lifecycle_schemas_include_cancelled_status(self) -> None:
        spec = load_openapi_spec()
        schemas = spec["components"]["schemas"]

        run_ref_status_enum = schemas["RunRef"]["properties"]["status"]["enum"]
        run_status_status_enum = schemas["RunStatus"]["properties"]["status"]["enum"]

        assert "CANCELLED" in run_ref_status_enum
        assert "CANCELLED" in run_status_status_enum

    def test_run_status_schema_exposes_safe_observability_contract(self) -> None:
        spec = load_openapi_spec()
        schemas = spec["components"]["schemas"]
        run_status = schemas["RunStatus"]
        run_step = schemas["RunStepResponse"]

        assert {"run_id", "status", "mode", "started_at"}.issubset(set(run_status["required"]))
        assert "source" in run_status["properties"]
        assert run_status["properties"]["source"]["$ref"] == "#/components/schemas/RunSource"
        assert run_status["properties"]["source"]["nullable"] is True
        assert "summary" in run_step["properties"]
        assert "enum" not in run_step["properties"]["step_name"]
        run_ref = schemas["RunRef"]
        assert (
            run_ref["properties"]["steps"]["items"]["$ref"]
            == "#/components/schemas/RunRefStepResponse"
        )
        assert "summary" not in schemas["RunRefStepResponse"]["properties"]

    def test_postgres_integration_ci_runs_slice_23_observability_tests(self) -> None:
        workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

        assert 'IDIS_REQUIRE_POSTGRES: "1"' in workflow
        assert "tests/test_api_run_observability_postgres.py" in workflow
