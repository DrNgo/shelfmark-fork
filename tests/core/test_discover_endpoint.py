"""Tests for GET /api/discover: auth, parameter validation, toggle gating."""

from unittest.mock import patch

import pytest

import shelfmark.main as main_module
from shelfmark.core.discover import DiscoverRow
from shelfmark.metadata_providers import BookMetadata


@pytest.fixture
def client():
    main_module.app.config["TESTING"] = True
    with main_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def no_auth():
    with patch.object(main_module, "get_auth_mode", return_value="none"):
        yield


def _row() -> DiscoverRow:
    return DiscoverRow(
        key="trending",
        label="Trending",
        provider="hardcover",
        books=[BookMetadata(provider="hardcover", provider_id="1", title="Book")],
        stale=False,
    )


class TestDiscoverEndpointAuth:
    def test_unauthenticated_returns_401(self, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        assert resp.status_code == 401


class TestDiscoverEndpoint:
    def test_toggle_off_returns_404(self, client, no_auth):
        with patch.object(main_module, "app_config") as cfg:
            cfg.get.side_effect = lambda key, default=None, **kw: (
                False if key == "SHOW_DISCOVER_ROWS" else default
            )
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        assert resp.status_code == 404

    def test_invalid_content_type_returns_400(self, client, no_auth):
        resp = client.get("/api/discover?content_type=magazine&row=trending")
        assert resp.status_code == 400

    def test_unknown_row_returns_400(self, client, no_auth):
        resp = client.get("/api/discover?content_type=ebook&row=bogus")
        assert resp.status_code == 400

    def test_unavailable_provider_returns_empty_row(self, client, no_auth):
        with patch.object(main_module, "get_discover_row_service", return_value=None):
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        assert resp.status_code == 200
        assert resp.get_json() == {"row": "trending", "books": []}

    def test_success_serializes_books(self, client, no_auth):
        with patch.object(main_module, "get_discover_row_service", return_value=_row()):
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["row"] == "trending"
        assert data["label"] == "Trending"
        assert data["provider"] == "hardcover"
        assert data["stale"] is False
        assert data["books"][0]["title"] == "Book"
