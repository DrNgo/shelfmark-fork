"""Tests for Audible taxonomy and category discovery."""

from unittest.mock import MagicMock, patch

import pytest

from shelfmark.metadata_providers.audible import AudibleProvider


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _product(asin: str) -> dict:
    return {
        "asin": asin,
        "title": f"Title {asin}",
        "is_listenable": True,
        "content_delivery_type": "SinglePartBook",
    }


@pytest.fixture
def provider() -> AudibleProvider:
    return AudibleProvider(region="us")


def test_fetch_topic_tree_uses_genres_root(provider):
    response = _response({"categories": [{"id": "10", "name": "Romance"}]})
    with patch.object(provider.session, "get", return_value=response) as mock_get:
        nodes = provider.fetch_topic_tree()
    assert nodes is not None and nodes[0].path == ("Romance",)
    assert mock_get.call_args.kwargs["params"] == {
        "root": "Genres",
        "categories_num_levels": 8,
        "response_groups": "category_metadata",
    }


def test_discover_topic_passes_category_and_best_sellers(provider):
    products = [_product("B000000000")]
    with patch.object(
        provider.session, "get", return_value=_response({"products": products})
    ) as mock_get:
        books = provider.discover_topic("18580607011", limit=1)
    assert books is not None and books[0].provider_id == "B000000000"
    params = mock_get.call_args.kwargs["params"]
    assert params["category_id"] == "18580607011"
    assert params["products_sort_by"] == "BestSellers"


def test_discover_topic_rejects_non_numeric_internal_id(provider):
    assert provider.discover_topic("not-an-id") == []
