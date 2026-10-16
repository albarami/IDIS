"""RED contract for the approved Task 5 strict-profile workbook binding."""

from __future__ import annotations

from idis.runtime import profile


def test_fdd_methodology_path_is_a_required_hmac_bound_profile_value() -> None:
    """Strict processes must not compose without the server-selected FDD source."""
    assert "IDIS_FDD_METHODOLOGY_PATH" in profile._REQUIRED_NAMES
    assert "IDIS_FDD_METHODOLOGY_PATH" not in profile._HMAC_EXCLUDED_NAMES
