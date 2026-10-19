"""Environment values for the v19 erasure API PostgreSQL proof."""

from __future__ import annotations

from pathlib import Path

from idis.api.auth import IDIS_API_KEYS_ENV


def _module_environment(
    *,
    admin_url: str,
    app_url: str,
    erasure_url: str,
    object_store_base: str,
    api_keys_json: str,
) -> dict[str, str]:
    """Return every value the module fixture sets, including its worker lease."""

    return {
        "IDIS_DATABASE_ADMIN_URL": admin_url,
        "IDIS_DATABASE_URL": app_url,
        "IDIS_ERASURE_DATABASE_URL": erasure_url,
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": object_store_base,
        "IDIS_AUDIT_LOG_PATH": str(Path(object_store_base) / "fallback-audit-never-selected.jsonl"),
        "IDIS_REQUIRE_FULL_LIVE": "0",
        IDIS_API_KEYS_ENV: api_keys_json,
        "IDIS_WORKER_LEASE_SECONDS": "60",
        "IDIS_WORKER_HEARTBEAT_SECONDS": "20",
    }
