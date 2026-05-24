"""Default object-store wiring for configured product paths."""

from __future__ import annotations

import os
from collections.abc import Mapping

from idis.storage.filesystem_store import FilesystemObjectStore
from idis.storage.object_store import ObjectStore

IDIS_OBJECT_STORE_BACKEND_ENV = "IDIS_OBJECT_STORE_BACKEND"
IDIS_OBJECT_STORE_BASE_DIR_ENV = "IDIS_OBJECT_STORE_BASE_DIR"
FILESYSTEM_OBJECT_STORE_BACKEND = "filesystem"


def build_configured_product_export_object_store(
    env: Mapping[str, str] | None = None,
) -> ObjectStore | None:
    """Build the explicitly configured filesystem object store for product export."""
    values = os.environ if env is None else env
    backend = str(values.get(IDIS_OBJECT_STORE_BACKEND_ENV, "")).strip().lower()
    base_dir = str(values.get(IDIS_OBJECT_STORE_BASE_DIR_ENV, "")).strip()
    if not backend and not base_dir:
        return None
    if backend != FILESYSTEM_OBJECT_STORE_BACKEND:
        return None
    if not base_dir:
        return None
    return FilesystemObjectStore(base_dir=base_dir)


def product_export_object_store_configured(env: Mapping[str, str]) -> bool:
    """Return whether product export has explicit filesystem object-store config."""
    return str(
        env.get(IDIS_OBJECT_STORE_BACKEND_ENV, "")
    ).strip().lower() == FILESYSTEM_OBJECT_STORE_BACKEND and bool(
        str(env.get(IDIS_OBJECT_STORE_BASE_DIR_ENV, "")).strip()
    )
