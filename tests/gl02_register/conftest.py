"""Shared fixtures for fictional GL-02 registers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.gl02_register.support import build_authored_register


@pytest.fixture(scope="module")
def authored_register(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Private root and workbook path shared by the fictional register tests."""
    return build_authored_register(tmp_path_factory.mktemp("gl02-register"))
