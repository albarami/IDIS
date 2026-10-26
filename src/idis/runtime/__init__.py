"""Strict runtime bootstrap and dependency-admission primitives.

The package initializer intentionally imports no runtime service.  Strict launchers
must load and apply the approved profile before importing API, worker, migration, or
gate components.
"""

__all__: tuple[str, ...] = ()
