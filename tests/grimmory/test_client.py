"""Tests for the Grimmory API client."""

import pytest
import requests

from shelfmark.grimmory.client import BookloreConfig, BookloreError, list_books

CONFIG = BookloreConfig(
    base_url="http://grimmory:6060",
    username="shelfmark",
    password="secret",
    library_id=0,
    path_id=0,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


class TestListBooks:
    def test_returns_the_page_content_and_total_pages(self, monkeypatch):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _Response({"content": [{"id": 1}], "totalPages": 3})

        monkeypatch.setattr(requests, "get", fake_get)

        books, total_pages = list_books(CONFIG, "token", page=0, size=500)

        assert books == [{"id": 1}]
        assert total_pages == 3
        assert captured["url"] == "http://grimmory:6060/api/v1/books/page"
        assert captured["params"] == {"page": 0, "size": 500}

    def test_treats_a_missing_total_pages_as_one(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response({"content": []}))

        assert list_books(CONFIG, "token", page=0, size=500) == ([], 1)

    def test_raises_a_booklore_error_on_transport_failure(self, monkeypatch):
        def boom(url, **kwargs):
            raise requests.exceptions.ConnectionError

        monkeypatch.setattr(requests, "get", boom)

        with pytest.raises(BookloreError, match="Grimmory"):
            list_books(CONFIG, "token", page=0, size=500)

    def test_raises_on_an_unexpected_payload_shape(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(["not", "an", "object"]))

        with pytest.raises(BookloreError):
            list_books(CONFIG, "token", page=0, size=500)


class TestConnectionMessage:
    def test_reports_both_library_and_book_counts(self, monkeypatch):
        from shelfmark.config import booklore_settings

        monkeypatch.setattr(
            booklore_settings,
            "_get_booklore_select_options",
            lambda *a, **kw: ([{"value": "1", "label": "Ebooks"}], []),
        )
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "list_books", lambda *a, **kw: ([{"id": 1}], 3))

        result = booklore_settings.check_booklore_connection(
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            }
        )

        assert result["success"] is True
        assert "1 libraries" in result["message"]
        assert "books" in result["message"]
