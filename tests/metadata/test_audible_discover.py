"""Tests for AudibleProvider discover fetchers (best sellers / new releases)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.metadata_providers.audible import AudibleProvider

TODAY = datetime.now(UTC).date()
PAST = (TODAY - timedelta(days=10)).isoformat()
FUTURE = (TODAY + timedelta(days=10)).isoformat()


def _product(asin: str, issue_date: str = PAST, **overrides) -> dict:
    product = {
        "asin": asin,
        "title": f"Title {asin}",
        "issue_date": issue_date,
        "is_listenable": True,
        "content_delivery_type": "SinglePartBook",
    }
    product.update(overrides)
    return product


def _response(products: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"products": products}
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def provider() -> AudibleProvider:
    return AudibleProvider()


class TestDiscoverBestSellers:
    def test_returns_parsed_books_with_browse_params(self, provider):
        products = [_product(f"B00000000{i}") for i in range(3)]
        with patch.object(provider.session, "get", return_value=_response(products)) as mock_get:
            result = provider.discover_best_sellers(limit=3)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000000", "B000000001", "B000000002"]
        params = mock_get.call_args.kwargs["params"]
        assert params["products_sort_by"] == "BestSellers"
        assert "keywords" not in params
        assert "title" not in params

    def test_filters_non_listenable_and_podcasts(self, provider):
        products = [
            _product("B000000000"),
            _product("B000000001", is_listenable=False),
            _product("B000000002", content_delivery_type="PodcastParent"),
        ]
        with patch.object(provider.session, "get", return_value=_response(products)):
            result = provider.discover_best_sellers(limit=10)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000000"]

    def test_tops_up_from_page_1_when_filtered_short(self, provider):
        page0 = [_product(f"B0000000{i:02d}", is_listenable=False) for i in range(50)]
        page1 = [_product("B000000090"), _product("B000000091")]
        with patch.object(
            provider.session, "get", side_effect=[_response(page0), _response(page1)]
        ) as mock_get:
            result = provider.discover_best_sellers(limit=2)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000090", "B000000091"]
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[1].kwargs["params"]["page"] == 1

    def test_full_first_page_makes_single_request(self, provider):
        products = [_product(f"B0000000{i:02d}") for i in range(50)]
        with patch.object(provider.session, "get", return_value=_response(products)) as mock_get:
            result = provider.discover_best_sellers(limit=20)
        assert result is not None
        assert len(result) == 20
        assert mock_get.call_count == 1

    def test_request_error_returns_none(self, provider):
        with patch.object(provider.session, "get", side_effect=requests.ConnectionError("down")):
            assert provider.discover_best_sellers() is None

    def test_malformed_payload_returns_none(self, provider):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"unexpected": True}
        with patch.object(provider.session, "get", return_value=response):
            assert provider.discover_best_sellers() is None

    def test_no_qualifying_products_returns_empty(self, provider):
        with patch.object(provider.session, "get", return_value=_response([])):
            assert provider.discover_best_sellers() == []


class TestDiscoverNewReleases:
    def test_drops_preorders(self, provider):
        products = [
            _product("B000000000", issue_date=FUTURE),
            _product("B000000001", issue_date=PAST),
        ]
        with patch.object(provider.session, "get", return_value=_response(products)) as mock_get:
            result = provider.discover_new_releases(limit=5)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000001"]
        assert mock_get.call_args_list[0].kwargs["params"]["products_sort_by"] == "-ReleaseDate"

    def test_tops_up_from_page_1_when_short(self, provider):
        page0 = [_product(f"B0000000{i:02d}", issue_date=FUTURE) for i in range(50)]
        page1 = [_product("B000000090"), _product("B000000091")]
        with patch.object(
            provider.session, "get", side_effect=[_response(page0), _response(page1)]
        ) as mock_get:
            result = provider.discover_new_releases(limit=2)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000090", "B000000091"]
        assert mock_get.call_count == 2

    def test_stops_at_two_pages(self, provider):
        page = [_product("B000000000", issue_date=FUTURE)] * 50
        with patch.object(
            provider.session, "get", side_effect=[_response(page), _response(page)]
        ) as mock_get:
            result = provider.discover_new_releases(limit=20)
        assert result == []
        assert mock_get.call_count == 2

    def test_drops_missing_issue_date(self, provider):
        products = [_product("B000000000", issue_date=""), _product("B000000001")]
        with patch.object(provider.session, "get", return_value=_response(products)):
            result = provider.discover_new_releases(limit=5)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000001"]

    def test_any_page_failure_returns_none(self, provider):
        # Page 0 succeeds but is short; page-1 failure fails the whole fetch —
        # partial rows must never be cached as fresh/last-good (spec contract).
        page0 = [_product("B000000000")]
        with patch.object(
            provider.session,
            "get",
            side_effect=[_response(page0), requests.ConnectionError("down")],
        ):
            assert provider.discover_new_releases(limit=5) is None
