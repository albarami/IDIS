"""OpenAPI specification loader for IDIS API.

Provides deterministic loading of the OpenAPI spec with fail-closed behavior.
"""

import os
from pathlib import Path
from typing import Any

import yaml

OPENAPI_PATH_ENV_VAR = "IDIS_OPENAPI_PATH"
DEFAULT_OPENAPI_FILENAME = "IDIS_OpenAPI_v6_3.yaml"


class OpenAPILoadError(Exception):
    """Raised when OpenAPI spec cannot be loaded or parsed.

    This is a fail-closed error type that callers must handle explicitly.
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        self.message = message
        self.path = path
        super().__init__(message)


def _resolve_openapi_path() -> Path:
    """Resolve the OpenAPI spec file path deterministically.

    Resolution order:
    1. IDIS_OPENAPI_PATH environment variable (if set and non-empty)
    2. Default: repo-root/openapi/IDIS_OpenAPI_v6_3.yaml

    Returns:
        Path to the OpenAPI spec file.
    """
    env_path = os.environ.get(OPENAPI_PATH_ENV_VAR)
    if env_path and env_path.strip():
        return Path(env_path.strip())

    repo_root = Path(__file__).parent.parent.parent.parent
    return repo_root / "openapi" / DEFAULT_OPENAPI_FILENAME


def load_openapi_spec() -> dict[str, Any]:
    """Load and parse the OpenAPI specification.

    Resolution:
    - Uses IDIS_OPENAPI_PATH env var if set, else repo-root openapi/IDIS_OpenAPI_v6_3.yaml.

    Returns:
        Parsed OpenAPI spec as a dictionary.

    Raises:
        OpenAPILoadError: If file is missing, unreadable, or contains invalid YAML.
    """
    spec_path = _resolve_openapi_path()
    path_str = str(spec_path)

    if not spec_path.exists():
        raise OpenAPILoadError(f"OpenAPI spec file not found: {path_str}", path=path_str)

    if not spec_path.is_file():
        raise OpenAPILoadError(f"OpenAPI spec path is not a file: {path_str}", path=path_str)

    try:
        with spec_path.open("r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise OpenAPILoadError(f"Failed to read OpenAPI spec: {e}", path=path_str) from e

    try:
        spec = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise OpenAPILoadError(f"Invalid YAML in OpenAPI spec: {e}", path=path_str) from e

    if spec is None:
        raise OpenAPILoadError("OpenAPI spec file is empty", path=path_str)

    if not isinstance(spec, dict):
        raise OpenAPILoadError(
            f"OpenAPI spec must be a mapping, got {type(spec).__name__}", path=path_str
        )

    return spec


def get_route_inventory(spec: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    """Extract (method, path) inventory from OpenAPI spec.

    This helper is provided for Phase 2.1 routing validation.

    Args:
        spec: Parsed OpenAPI spec dict. If None, loads spec from default location.

    Returns:
        List of (HTTP method uppercase, path) tuples.

    Raises:
        OpenAPILoadError: If spec cannot be loaded or is invalid.
    """
    if spec is None:
        spec = load_openapi_spec()

    paths = spec.get("paths")
    if paths is None:
        return []

    if not isinstance(paths, dict):
        raise OpenAPILoadError(
            f"OpenAPI spec 'paths' must be a mapping, got {type(paths).__name__}"
        )

    inventory: list[tuple[str, str]] = []
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in path_item:
            if method.lower() in http_methods:
                inventory.append((method.upper(), path))

    return inventory
