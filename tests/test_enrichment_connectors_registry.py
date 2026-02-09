"""Tests for enrichment connector registry completeness.

Verifies all expected connectors are registered in the default registry,
and that ESCWA ISPAR is explicitly NOT registered (no documented REST API).
"""

from __future__ import annotations

from idis.services.enrichment.service import _build_default_registry

EXPECTED_PROVIDER_IDS = frozenset(
    {
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
    }
)


class TestDefaultRegistryCompleteness:
    """Verify the default registry contains exactly the expected providers."""

    def test_all_expected_providers_registered(self) -> None:
        registry = _build_default_registry()
        assert registry.provider_ids == EXPECTED_PROVIDER_IDS

    def test_registry_has_14_providers(self) -> None:
        registry = _build_default_registry()
        assert len(registry.list_providers()) == 14

    def test_each_provider_id_resolves(self) -> None:
        registry = _build_default_registry()
        for pid in EXPECTED_PROVIDER_IDS:
            descriptor = registry.get(pid)
            assert descriptor.provider_id == pid


class TestIsparNotRegistered:
    """ESCWA ISPAR is not registered: ispar.unescwa.org is an HTML/ASPX site
    with no documented REST/JSON API. This exclusion is deterministic and explicit.
    """

    def test_ispar_not_in_registry(self) -> None:
        registry = _build_default_registry()
        assert "escwa_ispar" not in registry.provider_ids, (
            "ESCWA ISPAR must not be registered: ispar.unescwa.org has no "
            "documented REST/JSON API (HTML/ASPX web application only)"
        )

    def test_ispar_not_in_expected_set(self) -> None:
        assert "escwa_ispar" not in EXPECTED_PROVIDER_IDS


class TestRedProvidersMarkedCorrectly:
    """RED providers must be Finnhub and FMP only."""

    def test_finnhub_is_red(self) -> None:
        registry = _build_default_registry()
        assert registry.get("finnhub").rights_class.value == "RED"

    def test_fmp_is_red(self) -> None:
        registry = _build_default_registry()
        assert registry.get("fmp").rights_class.value == "RED"

    def test_red_providers_require_byol(self) -> None:
        registry = _build_default_registry()
        assert registry.get("finnhub").requires_byol is True
        assert registry.get("fmp").requires_byol is True


class TestYellowProvidersMarkedCorrectly:
    """YELLOW providers must be Wayback and Google News RSS only."""

    def test_wayback_is_yellow(self) -> None:
        registry = _build_default_registry()
        assert registry.get("wayback").rights_class.value == "YELLOW"

    def test_google_news_rss_is_yellow(self) -> None:
        registry = _build_default_registry()
        assert registry.get("google_news_rss").rights_class.value == "YELLOW"


class TestByolProviders:
    """BYOL providers: Companies House, GitHub, FRED, Finnhub, FMP."""

    def test_byol_providers(self) -> None:
        registry = _build_default_registry()
        byol_ids = {"companies_house", "github", "fred", "finnhub", "fmp"}
        for pid in byol_ids:
            assert registry.get(pid).requires_byol is True, f"{pid} should require BYOL"

    def test_non_byol_providers(self) -> None:
        registry = _build_default_registry()
        no_byol_ids = {
            "sec_edgar",
            "world_bank",
            "escwa_catalog",
            "qatar_open_data",
            "hackernews",
            "gdelt",
            "patentsview",
            "wayback",
            "google_news_rss",
        }
        for pid in no_byol_ids:
            assert registry.get(pid).requires_byol is False, f"{pid} should not require BYOL"
